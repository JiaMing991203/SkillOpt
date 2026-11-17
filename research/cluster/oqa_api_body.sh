#!/bin/bash
# OfficeQA hosted-actor job body. THIS IS THE SUBMISSION SHAPE THAT WORKS ON THIS CLUSTER - reuse it for the
# four-epoch comparison rather than rediscovering it.
#
#   sbatch --wrap="bash <bundle>/oqa_api_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle> ...
#
# Two hard-won constraints, both verified in 2026-11-17 jobs:
#   1. INVOKE VIA --wrap. A script submitted directly as the sbatch batch file from /nfs/turbo was killed by
#      NODE_FAIL at 60 s, four times each, on eight different nodes (jobs 66262999 and 66263561), before any
#      Python ran. A control job with the same account, partition and resources COMPLETED, and one-factor
#      controls showed that reading the key from ~/.config, extracting the bundle and curling the endpoint from
#      a compute node are each fine. `--no-requeue` did NOT stop the requeue loop.
#   2. WORK IN NODE-LOCAL /tmp, not on /nfs/turbo. The learned-contract line recorded the same lesson in job
#      65818719 (a TMPDIR on NFS broke tempfile cleanup). Results are copied back at the end.
#
# A job that spends API budget must also not be requeuable in effect: the attempt ledger lives in the Python
# process, so a restart begins with a fresh counter and respends the authorized attempts. Keep every such job
# short and check SLURM_RESTART_COUNT in the log before trusting a ledger total.
#
# Reads <bundle>/settings.env, which is PRIVATE and stays on the cluster (it holds machine paths). Required keys:
#   OQA_PY               interpreter with SkillOpt's dependencies
#   OQA_SPLIT_DIR        frozen split dir containing train/ val/ test/
#   OQA_DOCS_DIR         parsed bulletin root
#   ACTOR_ENV_FILE       file defining EMPIRIOLABS_API_KEY (mode 600)
#   OQA_API_MAX_ATTEMPTS / OQA_API_USD_CAP / OQA_API_MAX_TOOL_TURNS   the authorized caps
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
WORK="/tmp/oqa_smoke_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
log "start job=${SLURM_JOB_ID:-none} host=$(hostname -s) cpus=${SLURM_CPUS_PER_TASK:-?} work=$WORK"

set -a; . "$B/settings.env"; set +a
( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
log "manifest ok; commit $(cat "$B/COMMIT")"

tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || { log "ERROR: extract failed"; exit 3; }
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

set -a; . "$ACTOR_ENV_FILE"; set +a
[ -n "${EMPIRIOLABS_API_KEY:-}" ] || { log "ERROR: EMPIRIOLABS_API_KEY not set"; exit 4; }
export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
KEY_COPY="$EMPIRIOLABS_API_KEY"
unset EMPIRIOLABS_API_KEY
unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY HF_TOKEN
log "actor key loaded (length ${#KEY_COPY}); optimizer keys dropped so no DeepSeek call is possible"

curl -s -o /dev/null -w 'compute-node reachability: http=%{http_code} total=%{time_total}s\n' \
    --max-time 25 https://api.empiriolabs.ai/v1/models/qwen3-5-4b 2>&1 | tee -a "$OUTDIR/job.log"

cd "$WORK" || exit 1
status=0
PYTHONPATH="$WORK" "$OQA_PY" -I research/officeqa/oqa_api_smoke.py \
    --out "$WORK/smoke" --split-dir "$OQA_SPLIT_DIR" --docs-dir "$OQA_DOCS_DIR" \
    --max-attempts "${OQA_API_MAX_ATTEMPTS:-87}" --usd-cap "${OQA_API_USD_CAP:-0.50}" \
    --max-tool-turns "${OQA_API_MAX_TOOL_TURNS:-12}" >> "$OUTDIR/job.log" 2>&1 || status=$?
log "driver exit $status"

cp -r "$WORK/smoke" "$OUTDIR/" 2>/dev/null
if grep -rIl -e "$KEY_COPY" "$OUTDIR" 2>/dev/null | grep -q .; then
    log "ERROR: the key appears in a run artifact; removing those files"
    grep -rIl -e "$KEY_COPY" "$OUTDIR" 2>/dev/null | xargs -r rm -f
    status=5
fi
rm -rf "$WORK"
log "report: $OUTDIR/smoke/SMOKE.json (exit $status)"
exit "$status"
