#!/bin/bash
# OfficeQA forensics: why attempts ended without an answer, and why the contract reports said so little.
# Spends NOTHING. Rule OQA-FOR-1.
#
#   sbatch --wrap="bash <bundle>/oqa_forensics_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle> \
#          --cpus-per-task=2 --mem=16G --time=00:40:00 --no-requeue
#
# It reads ~100 MB of raw provider call logs and ~250 MB of campaign records, which is real work and belongs
# on a compute node. Every API key is dropped before Python starts, so no paid request is possible.
#
# THE HELD-OUT SPLIT IS EXCLUDED BY PATH, twice: the log roots below name only the campaign bundles, and
# `oqa_forensics.py::call_log_files` refuses any path containing "heldout" and reports what it skipped.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}"
WORK="/tmp/oqa_for_${SLURM_JOB_ID:-$$}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
log "start job=${SLURM_JOB_ID:-none} host=$(hostname -s) restart=${SLURM_RESTART_COUNT:-0}"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?}" ; : "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
PR="$(dirname "$(dirname "$CAMP")")"
[ -d "$CAMP" ] || { log "ERROR: no campaign at $CAMP"; exit 4; }

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
log "manifest ok; commit $(cat "$B/COMMIT")"

tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || { log "ERROR: extract failed"; exit 3; }
log "extracted $(find "$WORK" -type f | wc -l) files"

unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY \
      DEEPSEEK_API_KEY VLLM_API_KEY OPENAI_API_KEY HF_TOKEN AZURE_OPENAI_API_KEY
log "every API key dropped: this job cannot send a paid request"

LOGS=()
for d in "$PR"/oqa_campaign_20261117 "$PR"/oqa_campaign_20261117b "$PR"/oqa_campaign_20261117c; do
    [ -d "$d" ] && LOGS+=(--logs "$d")
done
log "call-log roots: ${LOGS[*]}"

D="$CAMP/diagnostic"
mkdir -p "$D" || exit 3
cd "$WORK" || exit 1
status=0
run() {
    local name="$1"; shift
    log "--- $name"
    "$OQA_PY" -I research/officeqa/oqa_forensics.py "$@" > "$OUTDIR/${name}.txt" 2>&1 || status=1
    cat "$OUTDIR/${name}.txt"
}
run noanswer  noanswer  --campaign "$CAMP" "${LOGS[@]}" --out "$D/FOR_NOANSWER.json"
run contracts contracts --campaign "$CAMP" --out "$D/FOR_CONTRACTS.json"

log "wrote $(ls -1 "$D" | tr '\n' ' ')"
log "done status=$status"
exit $status
