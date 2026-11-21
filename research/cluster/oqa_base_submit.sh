#!/bin/bash
# Submit the two-baseline OfficeQA campaign. Rule OQA-BASE-1.
#
#   OQA_BUNDLE=<bundle> bash oqa_base_submit.sh <what> [--dry-run]
#
#     prep        0:20  free     documents, 12/12/30 split, availability, ledger self-test, preflight
#     chain       -     paid     prep -> s0 -> Original SkillOpt epochs 1..4, as one dependency chain
#     s0          1:30  paid     four passes of the 12 training questions + the validation baseline
#     arm         -     paid     Original SkillOpt epochs 1..4 only (needs an existing s0)
#     t2s         2:30  paid     Trace2Skill over the 48 saved trajectories
#     heldout     1:30  paid     one endpoint, two passes of the 30 held-out questions (needs OQA_SKILL)
#
# THE WALLTIME BUDGET, because requested walltime is what the strict accounting convention charges:
#
#     prep     0:20          s0       1:30          arm e1   1:00          arm e2-e4  1:45 each
#     t2s      2:30          heldout  1:30 per endpoint, three endpoints
#
#     chain = 0:20 + 1:30 + 1:00 + 3x1:45 = 8:05 requested
#     t2s + three held-out endpoints      = 2:30 + 4:30 = 7:00 requested
#     total                               = 15:05 of the 50 authorized, leaving 34:55 of recovery headroom
#
# WHY t2s IS 2:30 AND NOT 1:00. Its call count is 105, not the 56 first projected: one analysis
# call per trajectory AND one MAP call per analysis record, plus seven hierarchical merges and
# apply/verify. At two DeepSeek requests in flight that is ~53 sequential slots, and the analysis
# prompts each carry a whole trajectory. Every reply is cached by prompt digest, so a TIMEOUT is
# resumed for the price of the calls that had not yet happened.
#
# SIZING, from measurement rather than estimate. The 2026-11-18 collection ran 24 episodes in 15:49 with
# workers=4, so one episode costs about 2.7 minutes of single-worker time and four workers clear roughly six
# episodes per 10 minutes. s0 is 60 episodes (48 training + 12 validation) ~ 40 minutes; each of epochs 2-4 is
# 48 episodes (12 training + 2x6 slow-update sides + 2x12 gate passes) ~ 32 minutes plus two or three
# optimizer calls, whose slow-update prompt is large and slow. Every figure above is at least 1.3x the
# estimate.
#
# RESUME. eval_only.py and run_batch resume by id from <dir>/results.jsonl, so a TIMEOUT is recovered by
# resubmitting the SAME stage with the SAME output directory: completed episodes are reused and never
# re-paid. The body refuses to run under SLURM_RESTART_COUNT != 0, so an automatic requeue can never
# re-spend; recovery is always a deliberate resubmission.
#
# THE BUNDLE IS NEVER REBUILT UNDER A LIVE JOB. bash reads a script body lazily, so rewriting a bundle while
# one of its jobs is queued or running corrupts that job. This script refuses to submit if it cannot see the
# bundle, and the operator must build a NEW bundle directory for changed code rather than overwrite one.
set -euo pipefail
WHAT="${1:?usage: oqa_base_submit.sh prep|chain|s0|arm|t2s|heldout [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
: "${OQA_BASE_ROOT:?settings.env must define OQA_BASE_ROOT}"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_base_body.sh"
[ -f "$BODY" ] || { echo "no body at $BODY" >&2; exit 2; }
# THE BUNDLE MUST SUPPORT THE STAGE IT IS ASKED FOR. A bundle is an immutable snapshot, so a stage
# added to the body AFTER a bundle was built does not exist in that bundle, and the body exits 2 with
# "unknown PHASE=...". Job 66553061 did exactly that: `lean` was submitted against a bundle built
# before the lean phase existed. Free, one second, nothing spent, and entirely avoidable - checking
# the DEPLOYED body turns a wasted submission into an error at the keyboard.
#
# The two bodies name their selector differently ($STAGE here, $PHASE in the contract body) and some
# words are submit-level composites that are not body stages at all, so both are handled. The first
# version of this guard matched only $PHASE and would have rejected EVERY valid stage of this script.
_accepts="$(grep -oE '^case "\$(STAGE|PHASE)" in [a-z0-9|]+' "$BODY" | sed 's/.* in //')"
_probe="$WHAT"
case "$WHAT" in chain) _probe="prep" ;; esac      # a composite: its first real stage stands for it
if [ -n "$_accepts" ] && ! printf '%s' "|$_accepts|" | grep -q "|$_probe|"; then
    echo "refusing: the body at $BODY does not support $_probe (asked: $WHAT)." >&2
    echo "  it accepts: $_accepts" >&2
    echo "  build a NEW bundle from a revision that has it." >&2
    exit 2
fi


S0_DIR="$OQA_BASE_ROOT/s0"
ARM_OUT="$OQA_BASE_ROOT/original_skillopt"
T2S_OUT="$OQA_BASE_ROOT/trace2skill"

# One submission. Prints the plan under --dry-run and otherwise returns the job id on stdout.
sub() {
    local name="$1" time="$2" cpus="$3" mem="$4" dep="$5" extra="$6"
    if [ "$DRY" = "1" ]; then
        # The plan goes to STDERR. Callers capture stdout to chain dependencies, so a plan line printed on
        # stdout would be swallowed into the next job's --dependency argument.
        printf '  %-22s %s  cpus=%s mem=%s  dep=%-22s %s\n' \
            "$name" "$time" "$cpus" "$mem" "${dep:-none}" "$extra" >&2
        echo "DRY"
        return 0
    fi
    local args=(--parsable --job-name="$name" --account="$ACCOUNT" --partition="$PARTITION"
                --cpus-per-task="$cpus" --mem="$mem" --time="$time" --no-requeue
                --output="$B/$name-%j.out" --chdir="$B")
    [ -n "$dep" ] && args+=(--dependency="afterok:$dep")
    sbatch "${args[@]}" --export="ALL,OQA_BUNDLE=$B,$extra" --wrap="bash $BODY"
}

case "$WHAT" in
prep)
    sub oqa-base-prep 00:20:00 2 8G "" "STAGE=prep,OQA_PRIOR_COLLECTION=${OQA_PRIOR_COLLECTION:-}"
    ;;
s0)
    sub oqa-base-s0 01:30:00 4 16G "${OQA_DEP:-}" "STAGE=s0"
    ;;
arm)
    DEP="${OQA_DEP:-}"
    for e in 1 2 3 4; do
        # Epoch 1 only pays for the candidate's gate pass: its training rollout and gate baseline are the
        # injected S0 traces. Epochs 2-4 each add a training rollout, the slow update's two sides and a
        # second gate pass, so they are given the longer walltime.
        T=$( [ "$e" = "1" ] && echo 01:00:00 || echo 01:45:00 )
        JID=$(sub "oqa-base-A-e$e" "$T" 4 24G "$DEP" \
              "STAGE=arm,EPOCH=$e,OQA_S0_DIR=$S0_DIR,OQA_ARM_OUT=$ARM_OUT")
        echo "$JID"
        DEP="$JID"
    done
    ;;
chain)
    # prep -> s0 -> epochs 1..4. Each link is afterok, so a failed stage stops the chain instead of paying
    # for a stage whose input does not exist.
    P=$(sub oqa-base-prep 00:20:00 2 8G "" "STAGE=prep,OQA_PRIOR_COLLECTION=${OQA_PRIOR_COLLECTION:-}")
    echo "$P"
    S=$(sub oqa-base-s0 01:30:00 4 16G "$P" "STAGE=s0")
    echo "$S"
    DEP="$S"
    for e in 1 2 3 4; do
        T=$( [ "$e" = "1" ] && echo 01:00:00 || echo 01:45:00 )
        JID=$(sub "oqa-base-A-e$e" "$T" 4 24G "$DEP" \
              "STAGE=arm,EPOCH=$e,OQA_S0_DIR=$S0_DIR,OQA_ARM_OUT=$ARM_OUT")
        echo "$JID"
        DEP="$JID"
    done
    ;;
t2s)
    sub oqa-base-t2s 02:30:00 4 16G "${OQA_DEP:-}" \
        "STAGE=t2s,OQA_S0_DIR=$S0_DIR,OQA_T2S_OUT=$T2S_OUT"
    ;;
heldout)
    : "${OQA_SKILL:?set OQA_SKILL to the skill file to evaluate}"
    : "${OQA_LABEL:?set OQA_LABEL to the endpoint label}"
    sub "oqa-base-ho-$OQA_LABEL" 01:30:00 4 16G "${OQA_DEP:-}" \
        "STAGE=heldout,OQA_SKILL=$OQA_SKILL,OQA_LABEL=$OQA_LABEL"
    ;;
*)
    echo "unknown: $WHAT" >&2; exit 2 ;;
esac
