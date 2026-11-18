#!/bin/bash
# THE ALFWORLD CONTEXT-CHECKING STUDY: the cluster half. CPU only, ZERO API calls, zero GPUs, zero
# gameplay. It runs the test suite and compiles the Lean file; the replays themselves were computed
# offline on the workstation from saved training traces, which is what the instruction asked for.
#
#   sbatch --wrap="bash <bundle>/awc_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>
#
#   PHASE=tests  the whole test suite, including the 27 constructed AWC regressions
#   PHASE=lean   regenerate AwcContext.lean from the comparison and compile it with Lean 4.20.0
#
# EVERY API KEY IS DROPPED BEFORE PYTHON STARTS. Nothing here can spend.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:?set PHASE=tests|lean}"
case "$PHASE" in tests|lean) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac
if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}" >&2; exit 6
fi

WORK="/tmp/awc_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
PROJ="$(dirname "$(dirname "$OQA_CAMPAIGN_ROOT")")"
AWC="${AWC_OUT:-$PROJ/runs/20261118-alfworld-context}"

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export AWC_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY \
      EMPIRIOLABS_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY \
      HF_TOKEN HUGGING_FACE_HUB_TOKEN
log "every API key dropped: this job cannot spend"

status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"

if [ "$PHASE" = "tests" ]; then
    log "the frozen-module snapshot first: the study's results describe exactly these files"
    "$OQA_PY" -I research/alfworld_context/awc_manifest.py verify 2>&1 \
        | tee "$OUTDIR/snapshot_verify.txt" || log "NOTE: the live sibling copy is unreachable here, which is expected on the cluster"
    "$OQA_PY" -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -5
    status=${PIPESTATUS[0]}
    log "phase tests finished with exit $status"
    rm -rf "$WORK"; exit "$status"
fi

[ -f "$AWC/AWC_COMPARE.json" ] || die "no comparison at $AWC/AWC_COMPARE.json (upload the run directory first)"
LEAN_BIN="${AWC_LEAN_BIN:-/nfs/turbo/umms-csriram/yaojm/lean/elan/toolchains/leanprover--lean4---v4.20.0/bin/lean}"
log "Lean: $("$LEAN_BIN" --version 2>&1 | head -1)"
"$OQA_PY" -I research/alfworld_context/awc_lean.py --compare "$AWC/AWC_COMPARE.json" \
    --out "$AWC" --lean-bin "$LEAN_BIN" 2>&1 | tee "$OUTDIR/awc_lean.txt" || status=$?
cp -f "$AWC/AWC_LEAN.json" "$AWC/AwcContext.lean" "$OUTDIR/" 2>/dev/null || true
rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR and $AWC"
exit "$status"
