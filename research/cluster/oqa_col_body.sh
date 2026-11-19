#!/bin/bash
# COLLECT CORRECTED-CLIENT TRAINING EVIDENCE: 24 frozen TRAINING questions, one attempt each, starting
# skill unchanged, `enable_thinking: false`, narrow `committed_answer` recovery. Rule OQA-COL-1.
#
#   sbatch --wrap="bash <bundle>/oqa_col_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>
#
#   PHASE=freeze    free, NO KEY. Freezes the collection (questions, skill, code, settings, tools,
#                   limits, recovery policy, information boundary) AND the connected prototype's
#                   complete semantic bundle as v5 - both BEFORE any request is sent and before any new
#                   record is read.
#   PHASE=collect   PAID, actor only. The 24 questions, one attempt each. RESUMES by question id: an
#                   episode already in results.jsonl is never recollected.
#   PHASE=analyze   free, NO KEY. Builds the descriptive records file, runs the FROZEN prototype over
#                   the new traces, runs the existing per-operand bind audit (which carries its own
#                   P-1 diagnostic column), and writes the 24-row inventory.
#   PHASE=tests     free, NO KEY. The whole test suite.
#
# THE ALLOCATION, and the arithmetic. The user authorized 800 actor attempts and $0.75, zero GPUs and
# ZERO optimizer calls. The live ledger's claims net of releases on 2026-11-18 were target 11,795
# attempts / $7.897264 and optimizer 105 / $3.768882. The caps below are therefore ABSOLUTE and computed
# ONCE from those readings - a cap derived from "what has been spent so far" would hand every job a
# fresh allowance:
#
#     target     12,595 attempts   $8.6473     = 11,795 + 800   and   7.897264 + 0.75
#     optimizer     105 attempts   $3.768882   = the CURRENT level, so no optimizer call can succeed
#
# The DeepSeek key is never loaded in any phase, so the optimizer cap is belt and braces.
#
# WHAT IS PRESERVED. Everything is written under $CAMP/collect_20261118/. followup_20261118/,
# followup_20261119/, audit_20261118/, status_20261118/, heldout/ and every arm_*/ directory are read
# only. The skill is not edited. The deployed checker semantics are NOT changed: neither pending repair
# is applied, and the P-1 column comes from the existing audit tool's own separate diagnostic mode.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:?set PHASE=freeze|collect|analyze|tests}"
case "$PHASE" in freeze|collect|analyze|tests) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}; a restart must not respend." >&2
    exit 6
fi

WORK="/tmp/oqa_col_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_CAMPAIGN_ROOT:?}" ; : "${OQA_DOCS_DIR:?}" ; : "${OQA_SPLIT3_DIR:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
COL="$CAMP/collect_20261118"          # a SEPARATELY LABELLED directory; nothing else is written
RUN="$COL/collect"
mkdir -p "$COL" || exit 3

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

drop_all_keys() {
    unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY \
          EMPIRIOLABS_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY \
          HF_TOKEN HUGGING_FACE_HUB_TOKEN
}
ACTOR_KEY=""
load_actor_key_only() {
    : "${ACTOR_ENV_FILE:?settings.env must define ACTOR_ENV_FILE}"
    set -a; . "$ACTOR_ENV_FILE"; set +a
    [ -n "${EMPIRIOLABS_API_KEY:-}" ] || die "EMPIRIOLABS_API_KEY not set by $ACTOR_ENV_FILE"
    export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
    ACTOR_KEY="$EMPIRIOLABS_API_KEY"; unset EMPIRIOLABS_API_KEY
    # THE OPTIMIZER KEY IS NEVER LOADED IN THIS BODY. Zero DeepSeek calls are authorized.
    unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY
    log "actor key loaded (length ${#ACTOR_KEY}); the optimizer key is NOT loaded in any phase"
}
ledger_env() {
    export SKILLOPT_SPEND_LEDGER_DIR="$CAMP/ledger"
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_COL_CAP_ACTOR_ATTEMPTS:-12595}"
    export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_COL_CAP_ACTOR_USD:-8.6473}"
    # the optimizer cap is set AT the current claimed level, so a call cannot be reserved at all
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER=105
    export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER=3.768882
    export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
    export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
    export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=50
    export SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.05
    export SKILLOPT_LEDGER_RESERVE_USD_TARGET=0.02
    # ONE job may spend the whole allocation, and no more.
    export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET="${OQA_JOB_CAP_ACTOR_ATTEMPTS:-800}"
    export SKILLOPT_LEDGER_JOB_MAX_USD_TARGET="${OQA_JOB_CAP_ACTOR_USD:-0.75}"
    export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER=0
    export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER=0
}
ledger_report() {
    "$OQA_PY" -I -c "
import json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
from skillopt.model import spend_ledger as L
print(json.dumps(L.report(), indent=2, sort_keys=True))
"
}

ACTOR_ROUTE="target=openai_compatible|https://api.empiriolabs.ai/v1|qwen3-5-4b"
CFG="configs/research/officeqa_api3.yaml"
S0="$CAMP/heldout/S0.md"
FREEZE="$COL/COLLECT_FREEZE.json"
CC18="$CAMP/followup_20261118/connected_contract"
CC="$COL/connected_contract"
status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"

case "$PHASE" in
tests)
    drop_all_keys
    log "every API key dropped: this phase cannot send a paid request"
    "$OQA_PY" -I -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -5
    status=${PIPESTATUS[0]}
    ;;

freeze)
    drop_all_keys
    log "every API key dropped: this phase cannot send a paid request"
    [ -f "$S0" ] || die "no S0 skill at $S0"
    mkdir -p "$CC" || exit 3

    log "1/2 freezing the collection BEFORE any request: questions, skill, code, settings, limits, policy"
    "$OQA_PY" -I research/officeqa/oqa_collect.py freeze \
        --frozen-sets docs/research/OFFICEQA_FROZEN_SETS_V3.json \
        --split3 "$OQA_SPLIT3_DIR" --docs "$OQA_DOCS_DIR" \
        --skill "$S0" --config "$CFG" --out "$COL" 2>&1 \
        | tee "$OUTDIR/freeze_collection.txt" || status=$?

    # THE PROTOTYPE'S SEMANTICS ARE FROZEN NOW, before any new record exists to be read. v3 and v4 stay
    # where they are; this is a new label over the same unchanged source.
    log "2/2 freezing the connected prototype's COMPLETE semantic bundle as v5"
    "$OQA_PY" -I research/officeqa/oqa_cc_bundle.py freeze \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
        --prompt "$CC18/CC_PROMPT.txt" --out "$CC" --label v5 2>&1 \
        | tee "$OUTDIR/freeze_bundle_v5.txt" || status=$?
    ;;

collect)
    [ -f "$FREEZE" ] || die "no collection freeze at $FREEZE; run PHASE=freeze first"
    [ -f "$S0" ] || die "no S0 skill at $S0"
    SPLIT="$("$OQA_PY" -I -c "import json;print(json.load(open('$FREEZE'))['split_dir'])")"
    [ -d "$SPLIT/train" ] || die "the frozen collection split is missing: $SPLIT"
    N_WANT="$("$OQA_PY" -I -c "import json;print(json.load(open('$FREEZE'))['n_questions'])")"
    F_SHA="$("$OQA_PY" -I -c "import json;print(json.load(open('$FREEZE'))['freeze_sha256_12'])")"
    log "freeze $F_SHA  split $SPLIT  skill $(sha256sum "$S0" | cut -c1-16)  want $N_WANT episodes"

    # RESUME, NEVER RECOLLECT. `run_batch` resumes by question id inside one out_root, so re-entering
    # this phase after an interruption continues the same directory instead of paying again.
    if [ -f "$RUN/results.jsonl" ]; then
        HAVE="$(wc -l < "$RUN/results.jsonl")"
        log "resuming: $HAVE of $N_WANT episodes already recorded in $RUN"
        if [ "$HAVE" -ge "$N_WANT" ]; then
            log "the collection is already complete; sending nothing"
            "$OQA_PY" -I research/officeqa/oqa_collect.py verify \
                --freeze "$FREEZE" --run "$RUN" --out "$COL" 2>&1 \
                | tee "$OUTDIR/verify.txt" || status=$?
            rm -rf "$WORK"; exit "$status"
        fi
    fi
    mkdir -p "$RUN"

    load_actor_key_only
    ledger_env
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    # THE TWO SETTINGS THIS COLLECTION IS FOR, and nothing else changes.
    export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=committed_answer
    export TARGET_OPENAI_COMPATIBLE_EXTRA_BODY='{"enable_thinking": false}'
    # The complete evidence: the transmitted request and the recovery decision in the call log, the raw
    # HTTP body and the SDK view in the capture. Both are AUDIT-ONLY by the frozen boundary.
    export TARGET_OPENAI_COMPATIBLE_CALL_LOG="$RUN/actor_calls.jsonl"
    export TARGET_OPENAI_COMPATIBLE_RAW_CAPTURE="$RUN/raw_capture.jsonl"
    log "collecting -> $RUN  extra_body=$TARGET_OPENAI_COMPATIBLE_EXTRA_BODY  policy=committed_answer"
    log "caps: cumulative target ${SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET} / \$${SKILLOPT_LEDGER_MAX_USD_TARGET}; this job ${SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET} / \$${SKILLOPT_LEDGER_JOB_MAX_USD_TARGET}; optimizer 0"

    "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$S0" \
        --split train --out_root "$RUN" \
        --cfg-options "env.split_dir=$SPLIT" "env.data_dirs=$OQA_DOCS_DIR" "env.workers=1" \
        >> "$OUTDIR/job.log" 2>&1 || { status=$?; log "eval_only exited $status"; }
    [ -f "$RUN/results.jsonl" ] && log "collected: $(wc -l < "$RUN/results.jsonl") row(s)"

    log "verifying that the intended settings were the settings TRANSMITTED"
    "$OQA_PY" -I research/officeqa/oqa_collect.py verify \
        --freeze "$FREEZE" --run "$RUN" --out "$COL" 2>&1 \
        | tee "$OUTDIR/verify.txt" || log "NOTE: verify reported a mismatch; see COLLECT_VERIFY.json"

    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || log "WARNING: ledger report failed"
    [ -f "$OUTDIR/LEDGER_AFTER.json" ] && "$OQA_PY" -I -c "
import json
d=json.load(open('$OUTDIR/LEDGER_AFTER.json'))
for role,v in sorted(d.get('observed',{}).get('by_role',{}).items()):
    print('  ledger %-9s attempts=%-6d usd=%.4f refusals=%d' % (role, v['attempts'], v['usd'], v['refusals']))
print('  ledger verdict:', d.get('verdict'), d.get('problems') or '')
" | tee -a "$OUTDIR/job.log"
    ;;

analyze)
    drop_all_keys
    log "every API key dropped: this phase cannot send a paid request"
    [ -f "$FREEZE" ] || die "no collection freeze at $FREEZE"
    [ -f "$RUN/results.jsonl" ] || die "no collected records at $RUN/results.jsonl"
    [ -f "$CC/CC_BUNDLE_v5.json" ] || log "WARNING: no v5 bundle; the prototype semantics are unverified here"
    mkdir -p "$CC" || exit 3

    log "0/5 verifying the frozen bundle still describes the deployed prototype"
    "$OQA_PY" -I research/officeqa/oqa_cc_bundle.py verify --bundle "$CC/CC_BUNDLE_v5.json" 2>&1 \
        | tee "$OUTDIR/verify_bundle_v5.txt" || log "NOTE: the bundle reports changed components"

    log "1/5 the descriptive records file over the NEW traces"
    "$OQA_PY" -I research/officeqa/oqa_episodes.py records \
        --run "collect24=$RUN" --out "$CC/CC_RECORDS_collect24.json" 2>&1 \
        | tee "$OUTDIR/records.txt" || status=$?

    log "2/5 the FROZEN prototype over them - descriptive, testing nothing"
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC/CC_RECORDS_collect24.json" \
        --which descriptive --out "$CC" 2>&1 | tee "$OUTDIR/cc_check.txt" || status=$?

    log "3/5 the per-operand causes, with the existing separate P-1 diagnostic column"
    "$OQA_PY" -I research/officeqa/oqa_audit_bind.py audit \
        --model "$CC18/CC_MODEL_v1.json" \
        --records "descriptive=$CC/CC_RECORDS_collect24.json" --out "$COL" 2>&1 \
        | tee "$OUTDIR/bind_audit.txt" || status=$?

    log "4/5 the provenance audit: which optimizer requests were RECORDED, not reconstructed"
    PROJ="$(dirname "$(dirname "$CAMP")")"
    "$OQA_PY" -I research/officeqa/oqa_provenance.py audit \
        --campaign "$CAMP" \
        --bundles "$PROJ/oqa_campaign_*/run_*/optimizer_calls.jsonl" \
        --item UID0014 --out "$COL" 2>&1 | tee "$OUTDIR/provenance.txt" || status=$?

    log "5/5 the 24-row inventory"
    "$OQA_PY" -I research/officeqa/oqa_collect.py summarize \
        --freeze "$FREEZE" --run "$RUN" \
        --cc-check "$CC/CC_CHECK_descriptive.json" --bind-audit "$COL/BIND_AUDIT.json" \
        --out "$COL" 2>&1 | tee "$OUTDIR/summary.txt" || status=$?
    ;;
esac

log "the phase wrote:"
for f in "$COL"/*.json "$CC"/*.json; do [ -f "$f" ] && log "  $(stat -c%s "$f") $f"; done
rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR and $COL"
exit "$status"
