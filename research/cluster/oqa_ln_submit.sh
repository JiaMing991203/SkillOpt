#!/bin/bash
# Submit a learned-contract phase. Rule OQA-LN-1.
#
#   OQA_BUNDLE=<bundle> bash oqa_ln_submit.sh ledger|freeze|learn|restage|lean|rehearse [--dry-run]
#
#     ledger    0:25  FREE  the ledger race fix, verified on /nfs/turbo, plus the full test suite
#     freeze    0:10  FREE  the evidence set, the v10 vocabulary and the accounting, frozen
#     learn     0:15  PAID  two learner repetitions, at most 2 optimizer calls each
#     restage   0:10  FREE  rebuild the stage reports from the already-frozen models
#     lean      0:15  FREE  generate and compile the Lean file
#     rehearse  0:15  PAID  the paired analyst rehearsal, exactly 2 optimizer calls
#
# The two paid phases take a phase-wide exclusive payer token, so they can never spend concurrently.
set -euo pipefail
WHAT="${1:?usage: oqa_ln_submit.sh ledger|freeze|learn|lean|rehearse [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_ln_body.sh"
[ -f "$BODY" ] || { echo "no body at $BODY" >&2; exit 2; }
# The DEPLOYED body must contain the phase: a bundle is an immutable snapshot, so a phase added after
# it was built does not exist in it (job 66553061 burned a submission on exactly that).
_accepts="$(grep -oE '^case "\$(STAGE|PHASE)" in [a-z0-9|]+' "$BODY" | sed 's/.* in //')"
if [ -n "$_accepts" ] && ! printf '%s' "|$_accepts|" | grep -q "|$WHAT|"; then
    echo "refusing: the body at $BODY does not support $WHAT." >&2
    echo "  it accepts: $_accepts" >&2
    echo "  build a NEW bundle from a revision that has it." >&2
    exit 2
fi
# SIZED TO MEASURED RUNTIME, not to a comfortable margin. The two-baseline campaign requested 17.50
# CPU job-hours across seven Trace2Skill submissions that used 44 minutes between them, because every
# one asked for 2:30. The free phases here have run in 2 and 143 seconds; the paid ones make at most a
# handful of HTTP calls.
case "$WHAT" in
  ledger)   T=00:25:00 ;; freeze) T=00:10:00 ;; learn) T=00:15:00 ;;
  restage)  T=00:10:00 ;; lean) T=00:15:00 ;; rehearse) T=00:15:00 ;;
  *) echo "unknown: $WHAT" >&2; exit 2 ;;
esac
if [ "$DRY" = "1" ]; then printf '  %-18s %s  PHASE=%s\n' "oqa-ln-$WHAT" "$T" "$WHAT" >&2; exit 0; fi
sbatch --parsable --job-name="oqa-ln-$WHAT" --account="$ACCOUNT" --partition="$PARTITION" \
    --cpus-per-task=2 --mem=8G --time="$T" --no-requeue \
    --output="$B/oqa-ln-$WHAT-%j.out" --chdir="$B" \
    --export="ALL,OQA_BUNDLE=$B,PHASE=$WHAT" --wrap="bash $BODY"
