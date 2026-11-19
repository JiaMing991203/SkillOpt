#!/bin/bash
# Submit the source-binding investigation. One job, because the allocation is 0.5 requested CPU
# job-hours and every step is free and takes seconds. Rule OQA-BIND-1.
#
#   OQA_BUNDLE=<bundle> bash oqa_bind_submit.sh [--dry-run]
#
#     all  0:20  free   tests, bundle v6, replay of all 24 records, bind audit, baseline comparison
#
# 0:20 is 0.333 of the 0.5 authorized requested job-hours, leaving room for one retry. No key is
# loaded in any step; the body drops every credential before Python starts.
set -euo pipefail
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?}"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_bind_body.sh"
[ -f "$BODY" ] || { echo "no body at $BODY" >&2; exit 2; }
if [ "$DRY" = "1" ]; then printf '  %-18s %s  %s\n' oqa-bind-all 00:20:00 "PHASE=all"; exit 0; fi
sbatch --parsable --job-name=oqa-bind-all --account="$ACCOUNT" --partition="$PARTITION" \
    --cpus-per-task=2 --mem=8G --time=00:20:00 --no-requeue \
    --output="$B/oqa-bind-all-%j.out" --chdir="$B" \
    --export="ALL,OQA_BUNDLE=$B,PHASE=all" --wrap="bash $BODY"
