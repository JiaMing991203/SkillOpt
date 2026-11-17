#!/bin/bash
# Submit the three-arm OfficeQA campaign as ONE dependency chain. Run this ON THE CLUSTER LOGIN NODE from the
# bundle directory; it only calls sbatch.
#
#   OQA_BUNDLE=<bundle> bash oqa_campaign_submit.sh [--dry-run]
#
# WHY A STRICT CHAIN RATHER THAN CONCURRENT ARMS.
#
# The EmpirioLabs rate limit is 50 requests per minute ACCOUNT-WIDE. One rollout job with workers=4 at the
# measured 3.41 s median latency already offers about 70 RPM, so a single job is rate-limited on its own:
# running three arms at once cannot raise throughput, it only divides the same quota three ways. The wall time
# is therefore the same either way - 12,763 requests / 50 RPM = 255 minutes - while the CPU JOB-HOURS would
# roughly triple, because three jobs would each sit waiting for the same quota. A chain is the cheaper shape for
# an identical result, and it gives a natural checkpoint between every job.
#
# THE ORDER the user asked for: the shared epoch-1 rollout first, then Original SkillOpt, then the two contract
# arms, epoch by epoch.
#
#     shared -> A.e1 -> B.e1 -> C.e1 -> A.e2 -> B.e2 -> C.e2 -> A.e3 -> ... -> C.e4
#
# Every link is `afterok`, so one failure stops everything downstream instead of spending the authorization on a
# chain whose earlier step is broken. The held-out evaluation is NOT submitted here: it runs once per DISTINCT
# selected skill, which is not known until the arms have finished (research/officeqa/oqa_heldout.py --plan).
#
# WHICH CONTRACT MODEL EACH EPOCH RUNS UNDER. Epochs 1 and 2 both use M0, the one model learned on the shared
# rollout, so the first evolution is the one that follows epoch 2. The arm job itself evolves the model at the
# END of each epoch from 2 on, which is why these paths can be fixed in advance.
#
#     epoch 1   contracts/model_M0.json                        reports are IN-SAMPLE (M0 was learned on them)
#     epoch 2   contracts/model_M0.json                        out-of-sample
#     epoch 3   contracts/arm_<X>/model_arm<X>_epoch02.json     out-of-sample
#     epoch 4   contracts/arm_<X>/model_arm<X>_epoch03.json     out-of-sample
set -euo pipefail
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
FAKE=1000

set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?settings.env must define OQA_CAMPAIGN_ROOT}"
CAMP="$OQA_CAMPAIGN_ROOT"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_campaign_body.sh"
[ -f "$BODY" ] || { echo "no body script at $BODY" >&2; exit 2; }

submit() {  # submit <name> <minutes> <dependency-or-empty> <extra-exports...>
    local name="$1" minutes="$2" dep="$3"; shift 3
    local exports="ALL,OQA_BUNDLE=$B"
    local kv
    for kv in "$@"; do exports="$exports,$kv"; done
    local depflag=()
    [ -n "$dep" ] && depflag=(--dependency="afterok:$dep" --kill-on-invalid-dep=yes)
    if [ "$DRY" = "1" ]; then
        # Print what would be submitted, compactly, and hand back a placeholder id so the chain still reads.
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

[ "$DRY" = "1" ] && echo "DRY RUN: this shows the SHAPE of the chain - names, order, limits, caps and
which contract model each arm-epoch runs under. It cannot show job ids, which exist only once sbatch has
accepted a job, so every dep= below is a placeholder. Check the real ids in the submission output."
echo "campaign root: $CAMP"
echo "bundle:        $B  (commit $(cat "$B/COMMIT"))"
echo

# ── the shared epoch-1 work: 24 training questions + the 24-question gate baseline, actor only ──────────────
JOB_SHARED="$(submit oqa-shared 60 "" "STAGE=shared" \
    "OQA_JOB_CAP_ACTOR_ATTEMPTS=900" "OQA_JOB_CAP_ACTOR_USD=0.80")"
echo "shared            $JOB_SHARED"

PREV="$JOB_SHARED"
declare -A LAST_OF_ARM
for EPOCH in 1 2 3 4; do
    for ARM in A B C; do
        # Per-epoch caps: epoch 1 only runs the 24-question gate (the training rollout is injected), while a
        # later epoch runs 24 train + 24 gate + 2x10 slow-update + 24 slow-update gate = 92 question-rollouts.
        if [ "$EPOCH" = "1" ]; then CAP_ACT=700; CAP_USD=0.60; CAP_OPT=12; CAP_OPT_USD=0.50; MIN=60
        else CAP_ACT=1600; CAP_USD=1.20; CAP_OPT=16; CAP_OPT_USD=0.70; MIN=60
        fi
        EXPORTS=("STAGE=arm" "ARM=$ARM" "EPOCH=$EPOCH"
                 "OQA_SHARED_DIR=$CAMP/shared" "OQA_ARM_OUT=$CAMP/arm_$ARM"
                 "OQA_JOB_CAP_ACTOR_ATTEMPTS=$CAP_ACT" "OQA_JOB_CAP_ACTOR_USD=$CAP_USD"
                 "OQA_JOB_CAP_OPT_CALLS=$CAP_OPT" "OQA_JOB_CAP_OPT_USD=$CAP_OPT_USD")
        if [ "$ARM" != "A" ]; then
            case "$EPOCH" in
                1|2) MODEL="$CAMP/contracts/model_M0.json" ;;
                3)   MODEL="$CAMP/contracts/arm_$ARM/model_arm${ARM}_epoch02.json" ;;
                4)   MODEL="$CAMP/contracts/arm_$ARM/model_arm${ARM}_epoch03.json" ;;
            esac
            EXPORTS+=("OFFICEQA_LC_MODEL=$MODEL")
        fi
        JOB="$(submit "oqa-$ARM-e$EPOCH" "$MIN" "$PREV" "${EXPORTS[@]}")"
        printf 'arm %s epoch %s    %s   after %s%s\n' "$ARM" "$EPOCH" "$JOB" "$PREV" \
            "$([ "$ARM" != "A" ] && echo "   model $(basename "$MODEL")")"
        PREV="$JOB"
        LAST_OF_ARM[$ARM]="$JOB"
    done
done
echo
echo "last job in the chain: $PREV"
echo "the held-out evaluation is NOT submitted here; run research/officeqa/oqa_heldout.py --plan afterwards."
