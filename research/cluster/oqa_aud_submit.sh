#!/bin/bash
# Submit one phase of the offline OfficeQA audit. Run on the login node.
#
#   OQA_BUNDLE=<bundle> bash oqa_aud_submit.sh <phase> [--dry-run]
#
#     audit   0:20  free   both workstreams, the v4 re-freeze, the re-check, the comparison and Lean
#     tests   0:12  free   the whole test suite
#
# THE ALLOWANCE FOR THIS AUDIT is 2 REQUESTED CPU job-hours, at most 2 CPUs per job and 2 jobs at once,
# zero API calls and zero GPUs. A requested hour is spent whether or not the job uses it, so both time
# limits are sized from measured precedents: the audit's tools run in seconds over a few megabytes of
# JSON, and the whole test suite took about one minute in job 66352312.
#
# NO KEY IS PASSED AND NO LEDGER VARIABLE IS SET. The body drops every credential before Python starts.
set -euo pipefail
PHASE="${1:?usage: oqa_aud_submit.sh audit|tests [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?}"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_aud_body.sh"
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
audit)
    J=$(sub oqa-aud-audit 00:20:00 "PHASE=audit")
    echo "audit  $J"
    ;;
tests)
    J=$(sub oqa-aud-tests 00:12:00 "PHASE=tests")
    echo "tests  $J"
    ;;
*)
    echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
