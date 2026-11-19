#!/bin/bash
# Submit one phase of the corrected-client training collection. Run on the login node. Rule OQA-COL-1.
#
#   OQA_BUNDLE=<bundle> bash oqa_col_submit.sh <phase> [--dry-run]
#
#     freeze   0:10  free   the collection freeze and the prototype's v5 semantic bundle
#     collect  0:30  PAID   the 24 TRAINING questions, one attempt each (<=800 attempts, <=$0.75).
#                           OQA_COL_TIME overrides the wall; the phase RESUMES, so a second shorter job
#                           finishes what the first did not.
#     analyze  0:10  free   records, the frozen prototype, the bind audit, provenance, the inventory
#     tests    0:10  free   the whole test suite
#
# THE ALLOCATION is 1.5 REQUESTED CPU job-hours, 800 actor attempts and $0.75, zero GPUs and ZERO
# optimizer calls. MEASURED, not estimated: the first collect job ran at about 2 minutes per episode -
# slower than the twelve-episode precedent (8:58 for 12, job 66346256) because that job's episodes were
# shorter, not because anything here is wrong - so 24 episodes need about 48 minutes and the first 0:30
# job stops around episode 14. The plan is therefore 0:10 + 0:30 + 0:25 resume + 0:10 + 0:10 = 1.42 of
# the 1.5 authorized job-hours, and the resume never recollects a completed episode.
#
# ONLY THE ACTOR KEY IS EVER LOADED, and only in `collect`. The body never loads a DeepSeek key.
set -euo pipefail
PHASE="${1:?usage: oqa_col_submit.sh freeze|collect|analyze|tests [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?}"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_col_body.sh"
[ -f "$BODY" ] || { echo "no body at $BODY" >&2; exit 2; }

sub() {                       # sub <name> <time> <extra-exports>
    local name="$1" time="$2" extra="$3"
    if [ "$DRY" = "1" ]; then
        printf '  %-18s %s  %s\n' "$name" "$time" "$extra"
        echo "dry"
        return
    fi
    sbatch --parsable --job-name="$name" --account="$ACCOUNT" --partition="$PARTITION" \
        --cpus-per-task=2 --mem=8G --time="$time" --no-requeue \
        --output="$B/$name-%j.out" --chdir="$B" \
        --export="ALL,OQA_BUNDLE=$B,$extra" --wrap="bash $BODY"
}

case "$PHASE" in
freeze)  J=$(sub oqa-col-freeze  00:10:00 "PHASE=freeze");  echo "freeze  $J" ;;
collect) J=$(sub oqa-col-collect "${OQA_COL_TIME:-00:30:00}" "PHASE=collect"); echo "collect $J  PAID  wall ${OQA_COL_TIME:-00:30:00}" ;;
analyze) J=$(sub oqa-col-analyze 00:10:00 "PHASE=analyze"); echo "analyze $J" ;;
tests)   J=$(sub oqa-col-tests   00:10:00 "PHASE=tests");   echo "tests   $J" ;;
*)       echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
