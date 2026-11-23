#!/bin/bash
# Submit the OfficeQA CONTRACT-ARM campaign as ONE dependency chain. Rule OQA-FW-1.
# Run this ON THE CLUSTER LOGIN NODE from the bundle directory; it only calls sbatch.
#
#   OQA_BUNDLE=<bundle> [OQA_DEP=<jobid[:jobid...]>] bash oqa_fw_submit.sh [--dry-run] [--from <stage>]
#
# OQA_DEP is the Lean compile and the test suite: the instruction asks for those to be AUTOMATIC JOB
# DEPENDENCIES rather than another approval milestone, so they are submitted separately (through
# research/submit.sh, which uses the deployed snapshot) and their ids are passed in here. Every link in
# this chain is `afterok`, so a failed check stops everything downstream before a cent is spent.
#
# THE CHAIN, and why it has this exact shape:
#
#   preflight -> learn.e1 -> arm.e1 -> arm.e2 -> learn.e3 -> arm.e3 -> learn.e4 -> arm.e4 -> heldout
#
# WHICH MODEL EACH EPOCH REPORTS UNDER. Every report must rest on a model frozen BEFORE the traces that
# report describes, and at the start of epoch 2 the only traces in existence are epoch 1's - which are
# the ones M0 was learned on. So M0 serves epochs 1 and 2, and the first evolution is the one that
# follows epoch 2:
#
#   epoch 1   models/FW_MODEL_e1.json   learned from the shared epoch-1 rollout   IN-SAMPLE, and says so
#   epoch 2   models/FW_MODEL_e1.json   the same M0                              out-of-sample
#   epoch 3   models/FW_MODEL_e3.json   learned from the arm's own epoch-2 traces out-of-sample
#   epoch 4   models/FW_MODEL_e4.json   learned from the arm's own epoch-3 traces out-of-sample
#
# WHY THE LEARNER IS ITS OWN STAGE. It is an optimizer-side call and needs no actor key, so a learner
# failure - a transport error, an unparseable reply, a refused proposal - stops the chain before any
# actor spend. It also gives the campaign a checkpoint between every model and the epoch that consumes
# it, and the stage exits non-zero when the learner was never actually heard from, which is the
# difference the instruction asks us not to blur between "proposed nothing" and "never ran".
#
# WHY A STRICT CHAIN RATHER THAN CONCURRENT STAGES. The actor endpoint's rate limit is ACCOUNT-WIDE and
# the ALFWorld session is using the same account right now. One job with workers=4 already offers more
# requests per minute than the quota allows, so concurrency cannot raise throughput - it only divides
# the same quota and multiplies the CPU job-hours spent waiting for it.
#
# WALLTIME, from the baselines' MEASURED elapsed times rather than from their requests: epoch 1 took
# 3 minutes, epochs 2-4 took 19, 19 and 18, each held-out endpoint 22-32. The requests below are about
# 4x those, which is the margin for sharing the provider with the ALFWorld campaign; the earlier
# "1.5 hours for the arm" estimate is not a guaranteed runtime while that campaign is also running.
set -euo pipefail
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
DRY=0
FROM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --from) FROM="${2:?--from needs a stage name, e.g. arm.3}"; shift ;;
        --from=*) FROM="${1#--from=}" ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
set -a; . "$B/settings.env"; set +a
: "${OQA_BASE_ROOT:?settings.env must define OQA_BASE_ROOT}"
FW_ROOT="${OQA_FW_ROOT:-$(dirname "$OQA_BASE_ROOT")/20261123-officeqa-fw-campaign}"
ARM_OUT="$FW_ROOT/contract_arm"
MODELS="$FW_ROOT/models"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_fw_body.sh"
[ -f "$BODY" ] || { echo "no body script at $BODY" >&2; exit 2; }
_accepts="$(grep -oE '^case "\$STAGE" in [a-z0-9|]+' "$BODY" | sed 's/.* in //')"
for s in preflight learn arm heldout; do
    printf '%s' "|$_accepts|" | grep -q "|$s|" \
        || { echo "refusing: the deployed body does not support stage $s (it accepts: $_accepts)" >&2
             echo "  build a NEW bundle from a revision that has it." >&2; exit 2; }
done
FAKE=2000

submit() {  # submit <name> <minutes> <cpus> <mem> <dependency-or-empty> <extra-exports...>
    local name="$1" minutes="$2" cpus="$3" mem="$4" dep="$5"; shift 5
    local exports="ALL,OQA_BUNDLE=$B,OQA_FW_ROOT=$FW_ROOT,OQA_WORKERS=${OQA_WORKERS:-4}"
    local kv
    for kv in "$@"; do exports="$exports,$kv"; done
    local depflag=()
    [ -n "$dep" ] && depflag=(--dependency="afterok:$dep" --kill-on-invalid-dep=yes)
    if [ "$DRY" = "1" ]; then
        FAKE=$((FAKE + 1))
        printf '  [dry-run] %-16s %3s min  cpus=%s mem=%-4s dep=%-14s %s\n' \
            "$name" "$minutes" "$cpus" "$mem" "${dep:-none}" "$(printf '%s ' "$@")" >&2
        printf '%s' "$FAKE"
        return 0
    fi
    sbatch --parsable --job-name="$name" --account="$ACCOUNT" --partition="$PARTITION" \
        --cpus-per-task="$cpus" --mem="$mem" --time="00:$minutes:00" --no-requeue \
        --output="$B/${name}-%j.out" --chdir="$B" "${depflag[@]}" \
        --export="$exports" --wrap="bash $BODY"
}

echo "fw root:   $FW_ROOT"
echo "bundle:    $B  (commit $(cat "$B/COMMIT"))"
echo "baselines: $OQA_BASE_ROOT  (READ ONLY)"
echo "gate jobs: ${OQA_DEP:-none passed; the Lean compile and the test suite are NOT wired in}"
echo "actor workers: ${OQA_WORKERS:-4}  (~$(( ${OQA_WORKERS:-4} * 115 / 10 )) req/min at peak; the"
echo "  EmpirioLabs limit is ACCOUNT-WIDE at ~50/min and the ALFWorld session shares it)"
echo

SKIP=1; [ -z "$FROM" ] && SKIP=0
runs() {  # runs <stage-name>: 1 when this stage should be submitted
    if [ "$SKIP" = "1" ]; then
        if [ "$1" = "$FROM" ]; then SKIP=0; else printf '%-16s SKIPPED (--from %s)\n' "$1" "$FROM"; return 1; fi
    fi
    return 0
}

PREV="${OQA_DEP:-}"

if runs preflight; then
    J=$(submit oqa-fw-pre 20 2 8G "$PREV" "STAGE=preflight")
    printf '%-16s %s   after %s\n' "preflight" "$J" "${PREV:-none}"; PREV="$J"
fi

# learn.e1: the seed, over the reused shared rollout. Optimizer only.
if runs learn.1; then
    J=$(submit oqa-fw-learn-e1 20 2 8G "$PREV" "STAGE=learn" "EPOCH=1" "OQA_TRACE_EPOCH=1" \
        "OQA_JOB_CAP_OPT_CALLS=8" "OQA_JOB_CAP_OPT_USD=0.50" \
        "OQA_JOB_CAP_ACTOR_ATTEMPTS=0" "OQA_JOB_CAP_ACTOR_USD=0.00")
    printf '%-16s %s   after %s   -> models/FW_MODEL_e1.json\n' "learn.e1" "$J" "$PREV"; PREV="$J"
fi

for E in 1 2 3 4; do
    # Epochs 3 and 4 each need a model learned from the PREVIOUS epoch's own traces first.
    if [ "$E" = "3" ] || [ "$E" = "4" ]; then
        TE=$((E - 1))
        PM="$MODELS/FW_MODEL_e$([ "$E" = "3" ] && echo 1 || echo 3).json"
        if runs "learn.$E"; then
            J=$(submit "oqa-fw-learn-e$E" 20 2 8G "$PREV" "STAGE=learn" "EPOCH=$E" \
                "OQA_TRACE_EPOCH=$TE" "OQA_PREV_MODEL=$PM" \
                "OQA_JOB_CAP_OPT_CALLS=8" "OQA_JOB_CAP_OPT_USD=0.50" \
                "OQA_JOB_CAP_ACTOR_ATTEMPTS=0" "OQA_JOB_CAP_ACTOR_USD=0.00")
            printf '%-16s %s   after %s   traces e%s, prev %s\n' \
                "learn.e$E" "$J" "$PREV" "$TE" "$(basename "$PM")"; PREV="$J"
        fi
    fi
    case "$E" in
        1) MODEL="$MODELS/FW_MODEL_e1.json"; MIN=40;  CAPA=400; CAPAU=0.50; CAPO=12; CAPOU=0.60 ;;
        2) MODEL="$MODELS/FW_MODEL_e1.json"; MIN=90;  CAPA=900; CAPAU=1.00; CAPO=20; CAPOU=1.00 ;;
        3) MODEL="$MODELS/FW_MODEL_e3.json"; MIN=90;  CAPA=900; CAPAU=1.00; CAPO=20; CAPOU=1.00 ;;
        4) MODEL="$MODELS/FW_MODEL_e4.json"; MIN=90;  CAPA=900; CAPAU=1.00; CAPO=20; CAPOU=1.00 ;;
    esac
    if runs "arm.$E"; then
        J=$(submit "oqa-fw-A-e$E" "$MIN" 4 24G "$PREV" "STAGE=arm" "EPOCH=$E" "OQA_MODEL=$MODEL" \
            "OQA_JOB_CAP_ACTOR_ATTEMPTS=$CAPA" "OQA_JOB_CAP_ACTOR_USD=$CAPAU" \
            "OQA_JOB_CAP_OPT_CALLS=$CAPO" "OQA_JOB_CAP_OPT_USD=$CAPOU" "OQA_CALL_BUDGET=$CAPO")
        printf '%-16s %s   after %s   model %s\n' "arm.e$E" "$J" "$PREV" "$(basename "$MODEL")"; PREV="$J"
    fi
done

# The held-out evaluation of the ONE new endpoint. It skips itself when the selected skill is
# byte-identical to an endpoint the baselines already scored, because that measurement exists.
if runs heldout; then
    J=$(submit oqa-fw-ho 120 4 16G "$PREV" "STAGE=heldout" "OQA_SKILL=$ARM_OUT/best_skill.md" \
        "OQA_LABEL=contract_arm" \
        "OQA_JOB_CAP_ACTOR_ATTEMPTS=1000" "OQA_JOB_CAP_ACTOR_USD=1.00" \
        "OQA_JOB_CAP_OPT_CALLS=0" "OQA_JOB_CAP_OPT_USD=0.00")
    printf '%-16s %s   after %s   endpoint %s\n' "heldout" "$J" "$PREV" "$ARM_OUT/best_skill.md"; PREV="$J"
fi

echo
echo "last job in the chain: $PREV"
echo "requested walltime, a full chain: 20 + 3x20 + 40 + 3x90 + 120 = 510 min = 8.50 job-hours"
