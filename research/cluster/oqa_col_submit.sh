#!/bin/bash
# Submit one phase of the corrected-client training collection. Run on the login node. Rule OQA-COL-1.
#
#   OQA_BUNDLE=<bundle> bash oqa_col_submit.sh <phase> [--dry-run]
#
#     freeze   0:10  free   the collection freeze and the prototype's v5 semantic bundle
#     collect  0:30  PAID   the 24 TRAINING questions, one attempt each (<=800 attempts, <=$0.75)
#     analyze  0:15  free   records, the frozen prototype, the bind audit, the 24-row inventory
#     tests    0:12  free   the whole test suite
#
# THE ALLOCATION is 1.5 REQUESTED CPU job-hours, 800 actor attempts and $0.75, zero GPUs and ZERO
# optimizer calls. All four phases at these limits request 1.12 job-hours, which leaves room for one
# bounded retry. The walltimes are sized from measured precedents: twelve episodes with thinking ON took
# 8:58 of a 45:00 request (job 66346256), and thinking off cuts completion tokens about fivefold, so
# twenty-four episodes in 30 minutes is a deliberately loose bound rather than a guess.
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
collect) J=$(sub oqa-col-collect 00:30:00 "PHASE=collect"); echo "collect $J  PAID" ;;
analyze) J=$(sub oqa-col-analyze 00:15:00 "PHASE=analyze"); echo "analyze $J" ;;
tests)   J=$(sub oqa-col-tests   00:12:00 "PHASE=tests");   echo "tests   $J" ;;
*)       echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
