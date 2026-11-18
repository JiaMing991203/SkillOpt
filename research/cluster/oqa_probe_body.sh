#!/bin/bash
# OfficeQA HTTP response probe (rule OQA-HTTP-1). Sends AT MOST the attempts the caller allows.
#
#   sbatch --wrap="bash <bundle>/oqa_probe_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,OQA_PROBE_PHASE=select|send,OQA_PROBE_SETTING=...,\
#OQA_PROBE_REPS=3,OQA_PROBE_MAX_SENDS=9 --cpus-per-task=2 --mem=8G --time=00:20:00 --no-requeue
#
# REFUSES ON A REQUEUE, for the same reason the slow-update probe does: the send cap lives in the
# Python process, so a Slurm restart would begin with a fresh counter and respend the allowance.
#
# The OPTIMIZER key is dropped: no DeepSeek request is part of this probe.
set -uo pipefail
B="${OQA_BUNDLE:?}"
PHASE="${OQA_PROBE_PHASE:?select or send}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}"
WORK="/tmp/oqa_probe_${SLURM_JOB_ID:-$$}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
log "start job=${SLURM_JOB_ID:-none} host=$(hostname -s) phase=$PHASE restart=${SLURM_RESTART_COUNT:-0}"

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    log "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}. The send cap is per-process."
    exit 6
fi

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?}" ; : "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
PR="$(dirname "$(dirname "$CAMP")")"
FU="$CAMP/followup_20261118"
mkdir -p "$FU/http_probe" || exit 3

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; exit 3; }
log "manifest ok; commit $(cat "$B/COMMIT")"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || exit 3

if [ "$PHASE" = "rederive" ]; then
    unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY \
          DEEPSEEK_API_KEY OPENAI_API_KEY
    log "every API key dropped: re-deriving saved outcomes sends nothing"
    cd "$WORK" && "$OQA_PY" -I research/officeqa/oqa_http_probe.py rederive \
        --out "$FU/http_probe" 2>&1 | tee "$OUTDIR/rederive.txt"
    exit ${PIPESTATUS[0]}
fi

if [ "$PHASE" = "select" ]; then
    unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY \
          DEEPSEEK_API_KEY OPENAI_API_KEY
    log "every API key dropped: the selection phase cannot send anything"
    LOGS=()
    for d in "$PR"/oqa_campaign_20261117 "$PR"/oqa_campaign_20261117b "$PR"/oqa_campaign_20261117c; do
        [ -d "$d" ] && LOGS+=(--logs "$d")
    done
    cd "$WORK" && "$OQA_PY" -I research/officeqa/oqa_http_probe.py select \
        "${LOGS[@]}" --campaign "$CAMP" --out "$FU/http_probe" 2>&1 | tee "$OUTDIR/select.txt"
    exit ${PIPESTATUS[0]}
fi

# ── send ────────────────────────────────────────────────────────────────────────
[ -f "$FU/http_probe/SELECTION.json" ] || { log "ERROR: no frozen selection"; exit 4; }
# The endpoint comes from the CAMPAIGN'S OWN recorded config, not from a new setting, so the probe
# cannot address an endpoint the campaign did not use.
BASE_URL="$("$OQA_PY" -I -c "
import json,sys
d=json.load(open(sys.argv[1]))
print(d.get('target_openai_compatible_base_url') or '')
" "$CAMP/arm_B/config.json")"
[ -n "$BASE_URL" ] || { log "ERROR: no target_openai_compatible_base_url in the campaign config"; exit 4; }
log "endpoint from the campaign config: $BASE_URL"
: "${ACTOR_ENV_FILE:?settings.env must define ACTOR_ENV_FILE}"
set -a; . "$ACTOR_ENV_FILE"; set +a
[ -n "${EMPIRIOLABS_API_KEY:-}" ] || { log "ERROR: EMPIRIOLABS_API_KEY not set by ACTOR_ENV_FILE"; exit 4; }
export TARGET_OPENAI_COMPATIBLE_BASE_URL="$BASE_URL"
export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
KEYLEN=${#EMPIRIOLABS_API_KEY}
unset EMPIRIOLABS_API_KEY
unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY
log "actor key loaded (length $KEYLEN); optimizer keys dropped"

# The spend ledger, so the follow-up allowance is enforced by the same machinery as the campaign.
export SKILLOPT_SPEND_LEDGER_DIR="$CAMP/ledger"
export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_FU_MAX_ATTEMPTS:-30024}"
export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_FU_MAX_USD:-17.00}"
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=4
export SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.05
export SKILLOPT_LEDGER_RESERVE_USD_TARGET=0.02
export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET="${OQA_PROBE_MAX_SENDS:-9}"
export SKILLOPT_LEDGER_JOB_MAX_USD_TARGET=0.50
export TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/probe_calls.jsonl"
# OQA-HTTP-1's correction, checked live when the caller asks for it. Off by default, as in the library.
if [ -n "${OQA_PROBE_CONTENT_FALLBACK:-}" ]; then
    export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK="$OQA_PROBE_CONTENT_FALLBACK"
    log "content fallback ENABLED for this job: $OQA_PROBE_CONTENT_FALLBACK"
fi

cd "$WORK" && PYTHONPATH="$WORK" "$OQA_PY" research/officeqa/oqa_http_probe.py send \
    --selection "$FU/http_probe/SELECTION.json" --out "$FU/http_probe" \
    --setting "${OQA_PROBE_SETTING:-historical}" --reps "${OQA_PROBE_REPS:-3}" \
    --max-sends "${OQA_PROBE_MAX_SENDS:-9}" ${OQA_PROBE_LABEL:+--label "$OQA_PROBE_LABEL"} \
    2>&1 | tee "$OUTDIR/send.txt"
status=${PIPESTATUS[0]}
log "done status=$status"
exit $status
