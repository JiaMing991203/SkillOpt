#!/bin/bash
# Submit the recovery of the arm-epochs a budget refusal silently turned into a skip. Run this ON THE CLUSTER
# LOGIN NODE from the bundle directory; it only calls sbatch.
#
#   OQA_BUNDLE=<bundle> bash oqa_recovery_submit.sh [--dry-run]
#
# WHAT IS BEING RECOVERED. The spend ledger capped CLAIMED money and never released what a process did not
# spend, so the optimizer's claims reached the $4.00 cap with $2.19 actually paid. From that point every
# optimizer request was refused, and the trainer recorded the consequences as ordinary quiet epochs:
# `skip_no_patches` for the ordinary update, `no_content` for the slow update and the meta skill. Four
# arm-epochs are affected - arm C epoch 3, and epoch 4 in all three arms - which
# `research/officeqa/oqa_recover.py audit` establishes from the ledger's own refusal records rather than from
# the shape of the artifacts.
#
# THE ORDER, and why it is this one:
#
#     A.e4 -> B.e4 -> C.e3 -> C.e4
#
# Arms A and B lost only their last epoch, so one job each. Arm C lost epochs 3 AND 4, and epoch 4 must follow
# epoch 3 because it begins from the skill epoch 3 leaves in force. The chain is `afterok` throughout, so a
# failure stops what follows instead of spending the authorization on a broken chain.
#
# WHAT IS REUSED, AND WHAT SETTLES IT. Every starved arm-epoch still holds its paid 24-question training
# rollout and both 10-question slow-update rollouts; only the refused optimizer calls and the gate passes are
# missing. `run_batch` resumes by id from results.jsonl, so a saved rollout left in place is returned
# unchanged and costs nothing - the same mechanism that gave the three arms byte-identical epoch-1 traces.
#
# That mechanism ignores the skill for a row it already has, which is exactly the hazard here: if a recovered
# gate ACCEPTS, the skill changes and some of those saved rollouts were produced by a skill that is no longer
# in force. Which ones cannot be known before the jobs run. So every recovery job sets
#
#     SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1
#
# and the question is settled per row at run time: each stored row carries the system prompt it ran under, the
# guard compares it against the prompt the current skill and settings would build, and re-rolls only what
# genuinely changed. On a reject nothing is re-rolled and the recovery costs two gate passes; on an accept the
# dependent rollouts are re-rolled and paid for, and the superseded results.jsonl is preserved beside the new
# one. Either way nothing is reflected on under the wrong skill, and nothing is paid for twice.
#
# BEFORE THIS SCRIPT, in order: `oqa_recover.py audit`, then `plan`, then `prepare --apply` (which archives the
# originals, rewinds each arm's runtime_state.json to its last sound step, and removes the `no_content` result
# files the trainer would otherwise take for finished work). This script refuses to submit if `prepare` has not
# run, because without the rewind every job would resume as "already done" and charge nothing while fixing
# nothing.
set -euo pipefail
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
FAKE=2000

set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?settings.env must define OQA_CAMPAIGN_ROOT}"
CAMP="$OQA_CAMPAIGN_ROOT"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_campaign_body.sh"
[ -f "$BODY" ] || { echo "no body script at $BODY" >&2; exit 2; }

RECORD="$CAMP/recovery/RECOVERY_RECORD.json"
[ -f "$RECORD" ] || {
    echo "no recovery record at $RECORD." >&2
    echo "Run oqa_recover.py audit, then plan, then prepare --apply, before submitting." >&2
    exit 2
}
# The rewind is the thing that makes these jobs do work rather than resume as finished. Check it happened.
for ARM in A B C; do
    STATE="$CAMP/arm_$ARM/runtime_state.json"
    [ -f "$STATE" ] || { echo "no runtime_state.json for arm $ARM" >&2; exit 2; }
done
echo "recovery record: $RECORD"
echo "campaign root:   $CAMP"
echo "bundle:          $B  (commit $(cat "$B/COMMIT"))"
echo

submit() {  # submit <name> <minutes> <dependency-or-empty> <extra-exports...>
    local name="$1" minutes="$2" dep="$3"; shift 3
    local exports="ALL,OQA_BUNDLE=$B"
    local kv
    for kv in "$@"; do exports="$exports,$kv"; done
    local depflag=()
    [ -n "$dep" ] && depflag=(--dependency="afterok:$dep" --kill-on-invalid-dep=yes)
    if [ "$DRY" = "1" ]; then
        FAKE=$((FAKE + 1))
        printf '  [dry-run] %s  %s min  dep=%s  %s\n' "$name" "$minutes" "${dep:-none}" \
            "$(printf '%s ' "$@" | sed 's/[^ ]*\///g')" >&2
        printf '%s' "$FAKE"
        return 0
    fi
    sbatch --parsable --job-name="$name" --account="$ACCOUNT" --partition="$PARTITION" \
        --cpus-per-task=2 --mem=8G --time="00:${minutes}:00" --no-requeue \
        --output="$B/${name}-%j.out" --chdir="$B" "${depflag[@]}" \
        --export="$exports" --wrap="bash $BODY"
}

[ "$DRY" = "1" ] && echo "DRY RUN: shape only. Job ids exist once sbatch accepts a job, so every dep= is a
placeholder."

PREV=""
# 90 minutes each. Measured: a later-epoch arm job that rolls all 92 questions took 49-82 minutes; a recovery
# job that reuses its training and paired rollouts has only the two 24-question gate passes to run, about 32
# minutes of actor time, and the worst case - arm C epoch 4 after an accepted epoch 3 - re-rolls 44 more
# questions on top, which is about 60. 90 leaves room without being a wall.
for KEY in A.4 B.4 C.3 C.4; do
    ARM="${KEY%%.*}"
    EPOCH="${KEY##*.}"
    EXPORTS=("STAGE=arm" "ARM=$ARM" "EPOCH=$EPOCH"
             "OQA_SHARED_DIR=$CAMP/shared" "OQA_ARM_OUT=$CAMP/arm_$ARM"
             # The provenance guard: reuse a saved rollout only while the skill that produced it is still the
             # one in force. This is what makes the conditional dependency safe.
             "SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1"
             # Per-job ceilings, unchanged in kind from the first run: the worst recovery job rolls 92
             # questions (about 950 attempts, $0.62) and spends $0.36 of optimizer over 9 calls.
             "OQA_JOB_CAP_ACTOR_ATTEMPTS=1600" "OQA_JOB_CAP_ACTOR_USD=1.20"
             "OQA_JOB_CAP_OPT_CALLS=16" "OQA_JOB_CAP_OPT_USD=0.70")
    if [ "$ARM" != "A" ]; then
        case "$EPOCH" in
            1|2) MODEL="$CAMP/contracts/model_M0.json" ;;
            3)   MODEL="$CAMP/contracts/arm_$ARM/model_arm${ARM}_epoch02.json" ;;
            4)   MODEL="$CAMP/contracts/arm_$ARM/model_arm${ARM}_epoch03.json" ;;
        esac
        [ -f "$MODEL" ] || { echo "arm $ARM epoch $EPOCH needs $MODEL, which does not exist" >&2; exit 2; }
        EXPORTS+=("OFFICEQA_LC_MODEL=$MODEL")
    fi
    JOB="$(submit "oqa-rec-$ARM-e$EPOCH" 90 "$PREV" "${EXPORTS[@]}")"
    printf 'recover arm %s epoch %s    %s   after %s%s\n' "$ARM" "$EPOCH" "$JOB" "${PREV:-none}" \
        "$([ "$ARM" != "A" ] && echo "   model $(basename "$MODEL")")"
    PREV="$JOB"
done
echo
echo "last job in the chain: $PREV"
echo
echo "AFTERWARDS, in this order:"
echo "  1. oqa_recover.py verify --campaign $CAMP --record $RECORD"
echo "  2. oqa_ledger_read.py $CAMP/ledger --questions N        (the caps, from outside the job)"
echo "  3. oqa_heldout.py --plan ...                            (the distinct skills to evaluate)"
echo "  4. oqa_heldout_submit.sh                                (PRIMARY first; last skills EXPLORATORY)"
