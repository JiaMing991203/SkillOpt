#!/bin/bash
# Submit one phase of the OfficeQA status-and-evidence packet. Run on the login node.
#
#   OQA_BUNDLE=<bundle> bash oqa_st_submit.sh <phase> [--dry-run]
#
#     status  0:15  free   the trace inventory, the six statements, the UID0014 feedback chain
#     tests   0:12  free   the whole test suite
#
# THE ALLOWANCE FOR THIS PACKET is 1 REQUESTED CPU job-hour, at most 2 CPUs per job, zero API calls and
# zero GPUs. A requested hour is spent whether or not the job uses it, so both limits are sized from
# measured precedents: the audit's tools ran in seconds over the same artifacts (job 66356437 took 7s),
# and the whole test suite took about one minute (job 66356411). Two phases at these limits are 0.45 of
# the hour, which leaves room for one repeat if something needs fixing.
#
# NO KEY IS PASSED AND NO LEDGER VARIABLE IS SET. The body drops every credential before Python starts.
set -euo pipefail
PHASE="${1:?usage: oqa_st_submit.sh status|tests [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?}"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_st_body.sh"
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
status)
    J=$(sub oqa-st-status 00:15:00 "PHASE=status")
    echo "status  $J"
    ;;
tests)
    J=$(sub oqa-st-tests 00:12:00 "PHASE=tests")
    echo "tests  $J"
    ;;
*)
    echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
