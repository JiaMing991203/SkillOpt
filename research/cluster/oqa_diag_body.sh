#!/bin/bash
# OfficeQA diagnostic read of the finished three-arm campaign. Spends NOTHING.
#
#   sbatch --wrap="bash <bundle>/oqa_diag_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle> \
#          --cpus-per-task=2 --mem=8G --time=00:30:00 --no-requeue
#
# WHY IT IS A JOB AND NOT A LOGIN-NODE COMMAND. It reads most of a 252 MB tree of JSON (every selection
# rollout's results and every saved conversation), which is real work and belongs on a compute node;
# CLAUDE.md keeps that off the login nodes.
#
# WHAT MAKES IT SAFE TO RE-RUN. It opens nothing but the campaign directory and writes only into
# $OQA_CAMPAIGN_ROOT/diagnostic/. Every API key is dropped before Python starts, so no paid request is
# possible even if a tool were wrong about what it does. It needs no ledger claim for the same reason.
#
# Reads <bundle>/settings.env (private, stays on the cluster): OQA_PY and OQA_CAMPAIGN_ROOT.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}"
WORK="/tmp/oqa_diag_${SLURM_JOB_ID:-$$}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
log "start job=${SLURM_JOB_ID:-none} host=$(hostname -s) restart=${SLURM_RESTART_COUNT:-0}"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?}" ; : "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
[ -d "$CAMP" ] || { log "ERROR: no campaign at $CAMP"; exit 4; }

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
log "manifest ok; commit $(cat "$B/COMMIT")"

tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || { log "ERROR: extract failed"; exit 3; }
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

# No request of any kind is part of this job, so make one impossible.
unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY \
      DEEPSEEK_API_KEY VLLM_API_KEY OPENAI_API_KEY HF_TOKEN AZURE_OPENAI_API_KEY
log "every API key dropped: this job cannot send a paid request"

D="$CAMP/diagnostic"
mkdir -p "$D" || exit 3
cd "$WORK" || exit 1
status=0
run() {
    local name="$1"; shift
    log "--- $name"
    "$OQA_PY" -I research/officeqa/oqa_diagnose.py "$@" > "$OUTDIR/${name}.txt" 2>&1 || status=1
    tail -40 "$OUTDIR/${name}.txt" | tee -a "$OUTDIR/job.log" > /dev/null
    cat "$OUTDIR/${name}.txt"
}
run gates     gates     --campaign "$CAMP" --out "$D/DIAG_GATES.json"
run questions questions --campaign "$CAMP" --out "$D/DIAG_QUESTIONS.json"
run contracts contracts --campaign "$CAMP" --out "$D/DIAG_CONTRACTS.json"
run chains    chains    --campaign "$CAMP" \
    --case B.2.step --case C.3.slow --case A.4.step --case B.4.step --case C.4.step \
    --case A.2.step --case A.3.step --case B.3.step --case C.2.step --case B.2.slow \
    --out "$D/DIAG_CHAINS.json"

log "wrote $(ls -1 "$D" | tr '\n' ' ')"
ls -la "$D" | tee -a "$OUTDIR/job.log"
log "done status=$status"
exit $status
