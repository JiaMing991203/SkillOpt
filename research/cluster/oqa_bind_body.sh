#!/bin/bash
# THE SOURCE-BINDING INVESTIGATION: freeze a new bundle, replay all 24 saved records under the B-4
# extractor repair into a NEW versioned directory, and run the test suite. Rule OQA-BIND-1.
#
#   sbatch --wrap="bash <bundle>/oqa_bind_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=all
#
# ONE PHASE, because the allocation is 0.5 REQUESTED CPU job-hours and four separate jobs would request
# four walltimes for about a minute of work. Every step is free: no key is loaded, no request path is
# exercised, and the spend ledger is not touched at all.
#
# WHAT IS PRESERVED, and this is the point of the versioned directory. The baseline stays byte-identical:
#   $COL/COLLECT_FREEZE.json             the original freeze 2083b4587253
#   $COL/collect/                        all 24 episodes, predictions, call log, raw capture
#   $COL/connected_contract/CC_*         the v5 bundle and the BASELINE check and records
#   $COL/BIND_AUDIT.json                 the BASELINE per-operand audit
#   $COL/COLLECT_SUMMARY.json            the BASELINE inventory
# Everything this job writes goes to $COL/v2_bind/ . The `analyze` phase of oqa_col_body.sh would have
# rewritten the baseline in place, which is why it is NOT used here.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:-all}"
case "$PHASE" in all) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}" >&2; exit 6
fi

WORK="/tmp/oqa_bind_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
COL="$CAMP/collect_20261118"
V2="$COL/v2_bind"                    # the versioned output directory; nothing else is written
CC18="$CAMP/followup_20261118/connected_contract"
mkdir -p "$V2" || exit 3

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files"

# NO KEY REACHES PYTHON. This job cannot spend, and the ledger is never written.
unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY \
      EMPIRIOLABS_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY \
      HF_TOKEN HUGGING_FACE_HUB_TOKEN
log "every API key dropped: this job cannot spend"

status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"

[ -f "$COL/collect/results.jsonl" ] || die "no baseline records at $COL/collect"
[ -f "$COL/connected_contract/CC_RECORDS_collect24.json" ] || die "no baseline records file"
BASE_N="$(wc -l < "$COL/collect/results.jsonl")"
log "baseline intact: $BASE_N episode rows; writing only to $V2"

log "1/5 the test suite, including the 14 new B-4 checks"
"$OQA_PY" -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -4
status=${PIPESTATUS[0]}
[ "$status" = "0" ] || log "WARNING: tests exited $status; the replay below is reported with that caveat"

# The new semantic bundle identifies the EXTRACTOR change while carrying the SAME model and checker.
log "2/5 freezing the semantic bundle as v6 (same hypothesis, changed extractor)"
"$OQA_PY" -I research/officeqa/oqa_cc_bundle.py freeze \
    --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
    --prompt "$CC18/CC_PROMPT.txt" --out "$V2" --label v6 2>&1 \
    | tee "$OUTDIR/bundle_v6.txt" || status=$?

log "3/5 replaying all 24 saved records under the repaired extractor, into the versioned directory"
"$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
    --model "$CC18/CC_MODEL_v1.json" \
    --records "$COL/connected_contract/CC_RECORDS_collect24.json" \
    --which descriptive --out "$V2" 2>&1 | tee "$OUTDIR/cc_check.txt" || status=$?

log "4/5 the per-operand causes under the repair, with the same separate P-1 diagnostic column"
"$OQA_PY" -I research/officeqa/oqa_audit_bind.py audit \
    --model "$CC18/CC_MODEL_v1.json" \
    --records "descriptive=$COL/connected_contract/CC_RECORDS_collect24.json" \
    --out "$V2" 2>&1 | tee "$OUTDIR/bind_audit.txt" || status=$?

log "5/5 the baseline-versus-repaired comparison over all 24 records"
"$OQA_PY" -I research/officeqa/oqa_bind_compare.py \
    --baseline-check "$COL/connected_contract/CC_CHECK_descriptive.json" \
    --baseline-audit "$COL/BIND_AUDIT.json" \
    --repaired-check "$V2/CC_CHECK_descriptive.json" \
    --repaired-audit "$V2/BIND_AUDIT.json" \
    --records "$COL/collect/results.jsonl" \
    --out "$V2" 2>&1 | tee "$OUTDIR/compare.txt" || status=$?

log "the baseline is unchanged:"
for f in "$COL/COLLECT_FREEZE.json" "$COL/BIND_AUDIT.json" "$COL/COLLECT_SUMMARY.json" \
         "$COL/connected_contract/CC_CHECK_descriptive.json"; do
    [ -f "$f" ] && log "  $(sha256sum "$f" | cut -c1-16)  $f"
done
log "this job wrote:"
for f in "$V2"/*.json; do [ -f "$f" ] && log "  $(stat -c%s "$f") $f"; done

rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR and $V2"
exit "$status"
