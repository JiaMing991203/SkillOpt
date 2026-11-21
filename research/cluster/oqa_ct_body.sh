#!/bin/bash
# THE CONTRACT ARM, developed alongside the running baselines. Rule OQA-OCC-1.
#
#   sbatch --wrap="bash <bundle>/oqa_ct_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>
#
#   PHASE=bundle7   FREE. Correct the mixed-version bundle, then measure occurrence-level source
#                   binding over the saved TRAINING records. No key is loaded, so this phase cannot
#                   spend a cent and cannot reach the actor or the optimizer.
#
# WHY THIS IS A SEPARATE BODY AND A SEPARATE BUNDLE. The baseline jobs (66552421-425, 66552508) are
# running from oqa_base_20261121b and oqa_base_20261121c. bash reads a script body lazily, so touching
# a live bundle corrupts its running job; and the instruction is to develop the contract arm in an
# isolated checkout or snapshot so contract changes cannot affect a running baseline. This body lives
# in its own bundle and writes only to its own output directory.
#
# THE MIXED-VERSION PROBLEM THIS PHASE CORRECTS, stated exactly.
#   Job 66414726 froze the semantic bundle as label v6 = 36c37a397eddaa65 and wrote its analyses into
#   collect_20261118/v2_bind/. Job 66414832 then ran after two semantic-function sources had changed;
#   the bundle guard CORRECTLY refused to let label v6 mean a second thing (68b8d640f9bc3667) and
#   exited 1, but the later steps still rewrote the analysis artifacts in that directory. So
#   v2_bind/CC_BUNDLE_v6.json is run 1's bundle sitting beside run 2's analyses, and the honest label
#   for run 2's semantic state is v7.
#
#   The correction is to freeze the CURRENT state as v7 in a NEW versioned directory and PRESERVE
#   v2_bind byte-for-byte. Nothing is overwritten and nothing is relabelled in place: the digests of
#   the preserved directory are logged before and after so the preservation is checkable rather than
#   promised.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:-bundle7}"
case "$PHASE" in bundle7) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}" >&2; exit 6
fi

WORK="/tmp/oqa_ct_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?}" ; : "${OQA_CAMPAIGN_ROOT:?}" ; : "${OQA_CT_ROOT:?}"

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"

# NO KEY REACHES PYTHON. This phase cannot spend and cannot call the actor or the optimizer.
unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY \
      EMPIRIOLABS_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY \
      HF_TOKEN HUGGING_FACE_HUB_TOKEN
log "every API key dropped: this phase cannot spend"

status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"

CAMP="$OQA_CAMPAIGN_ROOT"
COL="$CAMP/collect_20261118"
V2="$COL/v2_bind"                      # PRESERVED, never written
CC18="$CAMP/followup_20261118/connected_contract"
OUT="$OQA_CT_ROOT/v3_occ"              # everything this phase writes
mkdir -p "$OUT" || exit 3

log "1/4 recording the PRESERVED baseline and the mixed-version evidence"
{
  echo "# the directory that must not change, with its digests BEFORE this job"
  for f in "$V2"/*.json; do [ -f "$f" ] && sha256sum "$f"; done
  echo "# the bundle label actually stored in v2_bind, and the semantic digest it carries"
  "$OQA_PY" -I -c "
import json, sys
try:
    d = json.load(open('$V2/CC_BUNDLE_v6.json'))
    print('label         ', d.get('label'))
    print('semantic_digest', d.get('digest') or d.get('semantic_digest'))
except Exception as exc:
    print('could not read the v6 bundle:', exc)
"
} > "$OUTDIR/PRESERVED_BEFORE.txt" 2>&1
cat "$OUTDIR/PRESERVED_BEFORE.txt" >> "$OUTDIR/job.log"

log "2/4 freezing the CURRENT semantic state as v7, in a NEW directory"
"$OQA_PY" -I research/officeqa/oqa_cc_bundle.py freeze \
    --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
    --prompt "$CC18/CC_PROMPT.txt" --out "$OUT" --label v7 2>&1 \
    | tee "$OUTDIR/bundle_v7.txt" || status=$?
log "v7 freeze exit $status"

log "3/4 measuring occurrence-level source binding over the saved TRAINING records"
"$OQA_PY" -I research/officeqa/oqa_occ.py \
    --predictions "$COL/collect/predictions/*/conversation.json" \
    --out "$OUT/OCC_TRAIN24.json" 2>&1 | tee "$OUTDIR/occ_train24.txt" || status=$?
# And over the NEW s0 trajectories if stage s0 has finished, which is richer evidence: four passes
# rather than one attempt per question.
for p in 1 2 3 4; do
    PRED="$OQA_BASE_ROOT/s0/train_p$p/predictions"
    if [ -d "$PRED" ]; then
        "$OQA_PY" -I research/officeqa/oqa_occ.py --predictions "$PRED/*/conversation.json" \
            --out "$OUT/OCC_S0_p$p.json" >> "$OUTDIR/occ_s0.txt" 2>&1 \
            && log "measured s0 pass $p" || log "note: s0 pass $p not measurable yet"
    fi
done

log "4/4 the test suite"
"$OQA_PY" -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -4
PYSTATUS=${PIPESTATUS[0]}
[ "$PYSTATUS" = "0" ] || { log "tests exited $PYSTATUS"; status=$PYSTATUS; }

log "the preserved directory is UNCHANGED:"
{
  for f in "$V2"/*.json; do [ -f "$f" ] && sha256sum "$f"; done
} > "$OUTDIR/PRESERVED_AFTER.txt" 2>&1
if diff <(grep -E '^[0-9a-f]{64} ' "$OUTDIR/PRESERVED_BEFORE.txt") \
        <(grep -E '^[0-9a-f]{64} ' "$OUTDIR/PRESERVED_AFTER.txt") > "$OUTDIR/PRESERVED_DIFF.txt" 2>&1; then
    log "  VERIFIED: every digest in $V2 is identical before and after"
else
    log "  ERROR: $V2 CHANGED. See PRESERVED_DIFF.txt"
    cat "$OUTDIR/PRESERVED_DIFF.txt" >> "$OUTDIR/job.log"
    status=1
fi
log "this phase wrote:"
for f in "$OUT"/*.json; do [ -f "$f" ] && log "  $(stat -c%s "$f") $f"; done

rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR and $OUT"
exit "$status"
