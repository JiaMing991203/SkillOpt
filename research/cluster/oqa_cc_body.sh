#!/bin/bash
# OfficeQA connected-contract prototype (rule OQA-CC-1, amendment B-1).
#
#   sbatch --wrap="bash <bundle>/oqa_cc_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,OQA_CC_PHASE=select|prompt|propose|demo \
#          --cpus-per-task=2 --mem=8G --time=00:20:00 --no-requeue
#
# `propose` is the ONLY phase that spends anything: one DeepSeek call, capped by the ledger at the
# follow-up allowance. Every other phase drops all keys. It REFUSES ON A REQUEUE for the usual reason.
set -uo pipefail
B="${OQA_BUNDLE:?}"
PHASE="${OQA_CC_PHASE:?select|prompt|propose|demo}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}"
WORK="/tmp/oqa_cc_${SLURM_JOB_ID:-$$}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
log "start job=${SLURM_JOB_ID:-none} host=$(hostname -s) phase=$PHASE restart=${SLURM_RESTART_COUNT:-0}"
[ "${SLURM_RESTART_COUNT:-0}" = "0" ] || { log "REFUSING a requeue: the call cap is per-process"; exit 6; }

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?}" ; : "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
FU="$CAMP/followup_20261118/connected_contract"
mkdir -p "$FU" || exit 3
( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) >/dev/null 2>&1 || { log "manifest mismatch"; exit 3; }
log "manifest ok; commit $(cat "$B/COMMIT")"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || exit 3

if [ "$PHASE" != "propose" ]; then
    unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY \
          DEEPSEEK_API_KEY OPENAI_API_KEY
    log "every API key dropped: this phase cannot send a paid request"
fi

cd "$WORK" || exit 1
status=0
case "$PHASE" in
  select)
    "$OQA_PY" -I research/officeqa/oqa_cc_learn.py select --campaign "$CAMP" --arm C --out "$FU" \
        2>&1 | tee "$OUTDIR/select.txt" ; status=${PIPESTATUS[0]} ;;
  prompt)
    "$OQA_PY" -I research/officeqa/oqa_cc_learn.py prompt --records "$FU/CC_RECORDS.json" \
        --out "$FU/CC_PROMPT.txt" 2>&1 | tee "$OUTDIR/prompt.txt" ; status=${PIPESTATUS[0]} ;;
  propose)
    : "${OQA_OPTIMIZER_ENV_FILE:?}"
    set -a; . "$OQA_OPTIMIZER_ENV_FILE"; set +a
    [ -n "${DEEPSEEK_API_KEY:-}" ] || { log "ERROR: DEEPSEEK_API_KEY not set"; exit 4; }
    export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
    KEYLEN=${#DEEPSEEK_API_KEY}; unset DEEPSEEK_API_KEY
    unset TARGET_OPENAI_COMPATIBLE_API_KEY EMPIRIOLABS_API_KEY
    log "optimizer key loaded (length $KEYLEN); actor keys dropped"
    export OPTIMIZER_OPENAI_COMPATIBLE_BASE_URL="$("$OQA_PY" -I -c "
import json,sys; print(json.load(open(sys.argv[1])).get('optimizer_openai_compatible_base_url') or '')
" "$CAMP/arm_B/config.json")"
    export OPTIMIZER_MODEL="${OPTIMIZER_MODEL:-deepseek-flash}"
    export OPENAI_COMPATIBLE_OPTIMIZER_MODEL="$OPTIMIZER_MODEL"
    export MODEL_BACKEND=openai_compatible
    export OPTIMIZER_BACKEND=openai_compatible
    export SKILLOPT_SPEND_LEDGER_DIR="$CAMP/ledger"
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER="${OQA_FU_MAX_OPT_ATTEMPTS:-266}"
    export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER="${OQA_FU_MAX_OPT_USD:-10.00}"
    export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_OPTIMIZER=1
    export SKILLOPT_LEDGER_BLOCK_USD_OPTIMIZER=0.50
    export SKILLOPT_LEDGER_RESERVE_USD_OPTIMIZER=0.30
    export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
    export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER="${OQA_CC_MAX_CALLS:-2}"
    export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER=1.00
    export OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl"
    log "endpoint $OPTIMIZER_OPENAI_COMPATIBLE_BASE_URL model $OPTIMIZER_MODEL; job cap ${OQA_CC_MAX_CALLS:-2} call(s)"
    PYTHONPATH="$WORK" "$OQA_PY" research/officeqa/oqa_cc_learn.py propose \
        --records "$FU/CC_RECORDS.json" --out "$FU" 2>&1 | tee "$OUTDIR/propose.txt"
    status=${PIPESTATUS[0]} ;;
  demo)
    R="research/officeqa/oqa_cc_run.py"
    "$OQA_PY" -I $R freeze --proposal "$FU/PROPOSAL.json" --records "$FU/CC_RECORDS.json" --out "$FU" \
        2>&1 | tee "$OUTDIR/freeze.txt" || status=1
    "$OQA_PY" -I $R check --model "$FU/CC_MODEL_v1.json" --records "$FU/CC_RECORDS.json" \
        --which learning --out "$FU" 2>&1 | tee "$OUTDIR/check_learning.txt" || status=1
    "$OQA_PY" -I $R check --model "$FU/CC_MODEL_v1.json" --records "$FU/CC_RECORDS.json" \
        --which checking --out "$FU" 2>&1 | tee "$OUTDIR/check_checking.txt" || status=1
    "$OQA_PY" -I $R repair --model "$FU/CC_MODEL_v1.json" --check "$FU/CC_CHECK_checking.json" \
        --records "$FU/CC_RECORDS.json" --out "$FU" 2>&1 | tee "$OUTDIR/repair.txt" || status=1
    "$OQA_PY" -I $R compare --campaign "$CAMP" --check "$FU/CC_CHECK_checking.json" --arm C \
        --out "$FU" 2>&1 | tee "$OUTDIR/compare.txt" || status=1
    LEAN="${OQA_LEAN_BIN:-/nfs/turbo/umms-csriram/yaojm/lean/elan/bin/lean}"
    "$OQA_PY" -I research/officeqa/oqa_cc_lean.py --check "$FU/CC_CHECK_checking.json" \
        --out "$FU" --lean-bin "$LEAN" 2>&1 | tee "$OUTDIR/lean.txt" || status=1 ;;
  *) log "unknown phase"; exit 2 ;;
esac
log "done status=$status"
exit $status
