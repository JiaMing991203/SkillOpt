#!/bin/bash
# OfficeQA slow-update size probe: ONE guarded paid DeepSeek attempt per stage, on the cluster where the key is.
#
#   sbatch --wrap="bash <bundle>/oqa_slow_probe_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle> \
#          --cpus-per-task=1 --mem=4G --time=00:20:00
#
# Same submission shape as oqa_api_body.sh and for the same reasons: invoke via --wrap, work in node-local /tmp.
# See that file's header for the NODE_FAIL history.
#
# THREE THINGS THIS JOB DOES THAT A ROLLOUT JOB DOES NOT:
#   1. It REFUSES TO RUN ON A REQUEUE. The HTTP attempt cap lives in the Python process, so a Slurm restart
#      would begin with a fresh counter and respend the authorized attempts. SLURM_RESTART_COUNT > 0 exits 6
#      without sending anything. This is the safeguard the earlier runs only documented.
#   2. It drops the ACTOR key, so no rollout request is possible even by accident.
#   3. It turns on the backend's call log, so the exact request parameters, finish reason, usage and any provider
#      error are on disk for the report. The log never alters a request (the backend swallows its own write
#      errors) and never records the API key.
#
# Reads <bundle>/settings.env, which is PRIVATE and stays on the cluster. Required keys:
#   OQA_PY                  interpreter with SkillOpt's dependencies
#   SECRETS_FILE            file defining DEEPSEEK_API_KEY (mode 600)
#   OQA_SLOW_PREV           earlier-epoch training rollout dir (results.jsonl + predictions/)
#   OQA_SLOW_CURR           later-epoch training rollout dir
#   OQA_SLOW_CURR2          a second later-epoch rollout dir (optional; heavier attempt per item wins)
#   OQA_SLOW_PREV_SKILL     previous epoch's skill file
#   OQA_SLOW_CURR_SKILL     current epoch's skill file
#   OQA_OPT_BASE_URL        the campaign's optimizer endpoint
#   OQA_OPT_MODEL           the campaign's resolved optimizer model id
#   OQA_SLOW_PAIRS          pairs per slow update to test
#   OQA_SLOW_MAX_ATTEMPTS   HARD cap on actual HTTP sends
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}"
WORK="/tmp/oqa_slow_${SLURM_JOB_ID:-$$}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
log "start job=${SLURM_JOB_ID:-none} host=$(hostname -s) restart=${SLURM_RESTART_COUNT:-0} work=$WORK"

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    log "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}. The HTTP attempt cap is per-process, so a requeue"
    log "would respend the authorized attempts. Nothing was sent. Resubmit deliberately if that is wanted."
    exit 6
fi

set -a; . "$B/settings.env"; set +a
( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
log "manifest ok; commit $(cat "$B/COMMIT")"

tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || { log "ERROR: extract failed"; exit 3; }
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

set -a; . "$SECRETS_FILE"; set +a
[ -n "${DEEPSEEK_API_KEY:-}" ] || { log "ERROR: DEEPSEEK_API_KEY not set in $SECRETS_FILE"; exit 4; }
export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
KEY_COPY="$DEEPSEEK_API_KEY"
unset DEEPSEEK_API_KEY
# No actor request is part of this probe, so make one impossible.
unset TARGET_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY VLLM_API_KEY OPENAI_API_KEY HF_TOKEN
log "optimizer key loaded (length ${#KEY_COPY}); actor keys dropped so no rollout request is possible"

export OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$WORK/optimizer_calls.jsonl"

cd "$WORK" || exit 1
EXTRA=()
[ -n "${OQA_SLOW_CURR2:-}" ] && EXTRA+=(--curr2 "$OQA_SLOW_CURR2")
status=0
PYTHONPATH="$WORK" "$OQA_PY" -I research/officeqa/oqa_slow_probe.py \
    --mode send --batch largest \
    --prev "$OQA_SLOW_PREV" --curr "$OQA_SLOW_CURR" "${EXTRA[@]}" \
    --prev-skill "$OQA_SLOW_PREV_SKILL" --curr-skill "$OQA_SLOW_CURR_SKILL" \
    --pairs "${OQA_SLOW_PAIRS:-10}" --max-attempts "${OQA_SLOW_MAX_ATTEMPTS:-2}" \
    --base-url "$OQA_OPT_BASE_URL" --model "$OQA_OPT_MODEL" \
    --out "$WORK/probe" >> "$OUTDIR/job.log" 2>&1 || status=$?
log "driver exit $status"

cp -r "$WORK/probe" "$OUTDIR/" 2>/dev/null
cp "$WORK/optimizer_calls.jsonl" "$OUTDIR/" 2>/dev/null
if grep -rIl -e "$KEY_COPY" "$OUTDIR" 2>/dev/null | grep -q .; then
    log "ERROR: the key appears in a run artifact; removing those files"
    grep -rIl -e "$KEY_COPY" "$OUTDIR" 2>/dev/null | xargs -r rm -f
    status=5
fi
rm -rf "$WORK"
log "report: $OUTDIR/probe/SEND.json (exit $status)"
exit "$status"
