#!/bin/bash
# Submit a contract-arm phase. Rule OQA-OCC-1.
#
#   OQA_BUNDLE=<bundle> bash oqa_ct_submit.sh bundle7|replay|c2|lean [--dry-run]
#
#     bundle7  0:20  FREE  correct the mixed-version bundle as v7 in a new directory, measure
#                          occurrence-level binding over the saved training records, run the tests
#     replay   0:25  FREE  freeze the post-C-1 state as v8, then replay the chain to compare the C-1
#                          selection gate against the tie-break it replaced, against a prediction
#                          committed in the amendment before the run
#
# Free: the body drops every credential before Python starts. 0:20 requested is 0.333 CPU job-hours.
set -euo pipefail
WHAT="${1:?usage: oqa_ct_submit.sh bundle7|replay|c2|lean [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_ct_body.sh"
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

case "$WHAT" in bundle7) T=00:20:00 ;; replay) T=00:25:00 ;; c2) T=00:25:00 ;; lean) T=00:30:00 ;;
  *) echo "unknown: $WHAT" >&2; exit 2 ;; esac
if [ "$DRY" = "1" ]; then printf '  %-18s %s  PHASE=%s\n' "oqa-ct-$WHAT" "$T" "$WHAT"; exit 0; fi
sbatch --parsable --job-name="oqa-ct-$WHAT" --account="$ACCOUNT" --partition="$PARTITION" \
    --cpus-per-task=2 --mem=8G --time="$T" --no-requeue \
    --output="$B/oqa-ct-$WHAT-%j.out" --chdir="$B" \
    --export="ALL,OQA_BUNDLE=$B,PHASE=$WHAT" --wrap="bash $BODY"
