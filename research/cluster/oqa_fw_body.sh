#!/bin/bash
# THE OfficeQA CONTRACT-ARM CAMPAIGN, 2026-11-23. Rule OQA-FW-1. One body, four stages, CPU only: the
# actor is a hosted API and the document tools read local files, so no GPU is involved anywhere.
#
#   sbatch --wrap="bash <bundle>/oqa_fw_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,STAGE=<stage>[,EPOCH=1..4,...]
#
#   STAGE=preflight   FREE, no keys loaded, cannot spend. Verifies by PROVENANCE that the completed
#                     baselines' epoch-1 rollout, gate baseline, starting skill, question manifest and
#                     actor/tool settings are the ones this arm will reuse; asserts the effective
#                     outbound actor settings; proves the spend ledger is live on this filesystem; runs
#                     the framework test file.
#   STAGE=learn       PAID (optimizer only; NO actor call). One learner pass: DeepSeek proposes,
#                     code validates, scores, gates and repairs, and the model is frozen. EPOCH names
#                     the epoch whose reports will CONSUME the model, and OQA_TRACE_EPOCH the epoch
#                     whose traces it is learned FROM - they differ by one from epoch 3 on, which is
#                     what keeps every report resting on a model frozen before the traces it describes.
#   STAGE=arm         PAID (actor + optimizer). One epoch of SkillOpt under the contract arm: the same
#                     trainer, the same config, the same gate, with OFFICEQA_STAGE_REPORT_CONDITION=FW
#                     and the epoch's frozen model. Needs EPOCH and the model path.
#   STAGE=heldout     PAID (actor only). One endpoint, TWO passes of the 30 held-out questions. Skips
#                     itself when the endpoint's digest is one the baselines already evaluated, because
#                     that measurement exists and is sealed.
#
# WHY A NEW FILE AND NOT AN EDIT OF oqa_base_body.sh. That body is the record of what the completed
# two-baseline campaign executed. Editing it would overwrite that record to serve a different
# campaign. Everything it enforces is carried over here deliberately, and the five enforcements keep
# their reasons:
#   1. --wrap and node-local /tmp (a body submitted from /nfs/turbo was NODE_FAILed at 60 s, 8 nodes).
#   2. SLURM_RESTART_COUNT != 0 is refused (exit 6): a requeue is a fresh process and would re-spend.
#   3. SKILLOPT_REQUIRE_ROUTE is set on every paid stage: chat_optimizer is a ROUTER whose default is
#      Azure, and job 66271190 dispatched a perfect openai-compatible config to Azure and recorded
#      `no_content`.
#   4. The bundle is never rebuilt under a live job; bash reads a body lazily.
#   5. The 8,000-token output cap is ASSERTED, not assumed: two config fields look like it and only
#      `model.target_openai_compatible_max_tokens` is transmitted.
# And one this campaign adds:
#   6. THE LEDGER IS POINTED AT ITS DIRECTORY. The two-baseline campaign configured every cap and
#      enforced none, because SKILLOPT_SPEND_LEDGER_DIR was never set and the module is inert without
#      it. Nothing overran, which is luck rather than protection. Every paid stage here refuses to
#      load a key until the ledger reports itself ACTIVE.
#
# Reads <bundle>/settings.env, which is PRIVATE and stays on the cluster. Required keys:
#   OQA_PY OQA_DOCS_DIR ACTOR_ENV_FILE OQA_OPTIMIZER_ENV_FILE OQA_SPLIT4_DIR OQA_LEDGER_DIR
#   OQA_BASE_ROOT     the completed two-baseline run root, READ ONLY here
# Optional: OQA_FW_ROOT (default: a sibling of OQA_BASE_ROOT), OQA_ACCOUNT, OQA_PARTITION.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
STAGE="${STAGE:?set STAGE}"
case "$STAGE" in preflight|learn|arm|heldout) ;; *) echo "unknown STAGE=$STAGE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}; a requeue would re-spend." >&2; exit 6
fi

WORK="/tmp/oqa_fw_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${STAGE}${EPOCH:+_e$EPOCH}${OQA_LABEL:+_$OQA_LABEL}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} stage=$STAGE epoch=${EPOCH:-} host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_DOCS_DIR:?}" ; : "${OQA_LEDGER_DIR:?}" ; : "${OQA_BASE_ROOT:?}" ; : "${OQA_SPLIT4_DIR:?}"
FW_ROOT="${OQA_FW_ROOT:-$(dirname "$OQA_BASE_ROOT")/20261123-officeqa-fw-campaign}"
ARM_OUT="$FW_ROOT/contract_arm"
MODELS="$FW_ROOT/models"
REPORTS="$FW_ROOT/reports_fw"
mkdir -p "$FW_ROOT" "$MODELS" "$REPORTS" || die "cannot create $FW_ROOT"
log "fw root: $FW_ROOT   (the baselines at $OQA_BASE_ROOT are READ ONLY)"

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

# ── the ledger, pointed at its directory so the caps are actually in force ───────────────────────────
mkdir -p "$OQA_LEDGER_DIR/claims" "$OQA_LEDGER_DIR/spend" "$OQA_LEDGER_DIR/release" \
    || die "cannot create the ledger at $OQA_LEDGER_DIR"
export SKILLOPT_SPEND_LEDGER_DIR="$OQA_LEDGER_DIR"
# The PHASE ceilings of 2026-11-21, unchanged: this campaign is reserved inside them, not beside them.
export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_CAP_ACTOR_ATTEMPTS:-20000}"
export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_CAP_ACTOR_USD:-15.00}"
export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER="${OQA_CAP_OPT_CALLS:-400}"
export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER="${OQA_CAP_OPT_USD:-20.00}"
export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=50
export SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.05
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_OPTIMIZER=4
export SKILLOPT_LEDGER_BLOCK_USD_OPTIMIZER=0.10
export SKILLOPT_LEDGER_RESERVE_USD_TARGET=0.02
export SKILLOPT_LEDGER_RESERVE_USD_OPTIMIZER=0.15
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET="${OQA_JOB_CAP_ACTOR_ATTEMPTS:-2200}"
export SKILLOPT_LEDGER_JOB_MAX_USD_TARGET="${OQA_JOB_CAP_ACTOR_USD:-1.50}"
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER="${OQA_JOB_CAP_OPT_CALLS:-24}"
export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER="${OQA_JOB_CAP_OPT_USD:-1.00}"

export SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT="${OQA_PROMPT_TOKEN_LIMIT:-225000}"
export SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG="$OUTDIR/prompt_budget.jsonl"
export SKILLOPT_ROUTE_GUARD_RECORD="$OUTDIR/route_guard.json"
export SKILLOPT_EPOCH_LABEL="${EPOCH:-}"
export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=committed_answer
export TARGET_OPENAI_COMPATIBLE_EXTRA_BODY='{"enable_thinking": false}'
export TARGET_OPENAI_COMPATIBLE_MAX_TOKENS=8000
export TARGET_OPENAI_COMPATIBLE_TEMPERATURE=0.7

unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY TARGET_OPENAI_COMPATIBLE_API_KEY
unset DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY HF_TOKEN HUGGING_FACE_HUB_TOKEN
require_live_ledger() {
    "$OQA_PY" -I -c "
import json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
from skillopt.model import spend_ledger as L
snap = L.report()
print(json.dumps({'active': snap.get('active'), 'dir': snap.get('dir')}))
sys.exit(0 if snap.get('active') else 9)
" > "$OUTDIR/ledger_live.json" 2>&1 \
        || die "the spend ledger is NOT ACTIVE, so the caps are not enforced. Refusing to load a key. See ledger_live.json"
    log "ledger live: $(tr -d '\n' < "$OUTDIR/ledger_live.json")"
}
load_actor_key() {
    : "${ACTOR_ENV_FILE:?settings.env must define ACTOR_ENV_FILE}"
    set -a; . "$ACTOR_ENV_FILE"; set +a
    [ -n "${EMPIRIOLABS_API_KEY:-}" ] || die "EMPIRIOLABS_API_KEY not set by $ACTOR_ENV_FILE"
    export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
    local n=${#EMPIRIOLABS_API_KEY}
    unset EMPIRIOLABS_API_KEY
    log "actor key loaded (length $n)"
}
load_optimizer_key() {
    : "${OQA_OPTIMIZER_ENV_FILE:?settings.env must define OQA_OPTIMIZER_ENV_FILE}"
    set -a; . "$OQA_OPTIMIZER_ENV_FILE"; set +a
    [ -n "${DEEPSEEK_API_KEY:-}" ] || die "DEEPSEEK_API_KEY not set by $OQA_OPTIMIZER_ENV_FILE"
    export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
    local n=${#DEEPSEEK_API_KEY}
    unset DEEPSEEK_API_KEY
    log "optimizer key loaded (length $n)"
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
OPT_ROUTE="optimizer=openai_compatible|https://api.deepseek.com|deepseek-flash"
CFG="${OQA_CFG:-configs/research/officeqa_base2.yaml}"
FROZEN="docs/research/OFFICEQA_FROZEN_SETS_V4.json"
SKILL_INIT="skillopt/envs/officeqa/skills/initial.md"
S0_DIR="$OQA_BASE_ROOT/s0"
BASE_ARM="$OQA_BASE_ROOT/original_skillopt"
status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"
log "config $CFG ; frozen sets $FROZEN ; arm out $ARM_OUT"

# THE EPOCH-1 REUSE, in one place because three stages need it to be the same bytes.
inject_epoch1() {
    [ -f "$S0_DIR/train_p1/results.jsonl" ] || die "no S0 training rollout at $S0_DIR/train_p1"
    [ -f "$S0_DIR/val_baseline/results.jsonl" ] || die "no S0 gate baseline at $S0_DIR/val_baseline"
    mkdir -p "$ARM_OUT/steps/step_0001"
    [ -d "$ARM_OUT/steps/step_0001/rollout" ] \
        || cp -r "$S0_DIR/train_p1" "$ARM_OUT/steps/step_0001/rollout"
    [ -d "$ARM_OUT/selection_eval_baseline" ] \
        || cp -r "$S0_DIR/val_baseline" "$ARM_OUT/selection_eval_baseline"
    { echo "s0_dir=$S0_DIR"
      sha256sum "$ARM_OUT/steps/step_0001/rollout/results.jsonl" \
                "$ARM_OUT/selection_eval_baseline/results.jsonl"
    } > "$OUTDIR/injected_s0.txt" 2>&1
    log "injected $(wc -l < "$ARM_OUT/steps/step_0001/rollout/results.jsonl") S0 training rollouts"
}
# The reuse check, run wherever the reused bytes are about to be used.
verify_reuse() {
    "$OQA_PY" -I research/officeqa/oqa_fw_reuse.py \
        --rollout "$1" --baseline-rollout "$BASE_ARM/steps/step_0001/rollout" \
        --selection-baseline "$ARM_OUT/selection_eval_baseline" \
        --frozen "$FROZEN" --skill "$SKILL_INIT" --config "$CFG" \
        --out "$OUTDIR/FW_REUSE.json" --require-identical >> "$OUTDIR/job.log" 2>&1 \
        || die "the epoch-1 reuse check FAILED; see FW_REUSE.json. Nothing was spent."
    cp "$OUTDIR/FW_REUSE.json" "$FW_ROOT/" 2>/dev/null || true
    SUMMARY=$("$OQA_PY" -I research/officeqa/oqa_fw_reuse.py --summarize "$OUTDIR/FW_REUSE.json")
    log "reuse verified by provenance: $SUMMARY"
}

case "$STAGE" in
preflight)
    log "FREE stage: no actor key, no optimizer key, no network call is made from here"
    inject_epoch1
    verify_reuse "$ARM_OUT/steps/step_0001/rollout"
    # The EFFECTIVE outbound actor settings, asserted rather than assumed.
    "$OQA_PY" -I research/officeqa/oqa_base_preflight.py --config "$CFG" \
        --split-dir "$OQA_SPLIT4_DIR" --docs-dir "$OQA_DOCS_DIR" --frozen "$FROZEN" \
        --skill "$SKILL_INIT" --out "$OUTDIR/PREFLIGHT.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "the preflight FAILED: the split, the resolved config or the effective actor settings are not what this campaign requires. See PREFLIGHT.json."
    require_live_ledger
    ledger_report > "$OUTDIR/LEDGER_BEFORE.json" 2>> "$OUTDIR/job.log" || true
    "$OQA_PY" -I -c "
import json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
from skillopt.model import spend_ledger as L
snap = L.report()
assert snap.get('active'), 'the ledger reported itself inactive AFTER require_live_ledger'
print('ledger active with caps:', json.dumps(snap.get('caps') or {}, sort_keys=True))
" >> "$OUTDIR/job.log" 2>&1 || die "ledger assertion failed"
    # The framework's own tests, on the deployed snapshot.
    "$OQA_PY" -I -m pytest -q tests/test_officeqa_fw.py > "$OUTDIR/pytest_fw.txt" 2>&1 || status=$?
    tail -5 "$OUTDIR/pytest_fw.txt" | tee -a "$OUTDIR/job.log"
    [ "$status" = 0 ] || die "the framework test file failed on the deployed snapshot (see pytest_fw.txt)"
    # And a dry assertion that the report hook will select the FW condition.
    OFFICEQA_STAGE_REPORT_CONDITION=FW "$OQA_PY" -I -c "
import importlib.util, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
spec = importlib.util.spec_from_file_location('oqa_reports', 'research/officeqa/oqa_reports.py')
m = importlib.util.module_from_spec(spec); sys.path.insert(0, 'research/officeqa'); spec.loader.exec_module(m)
assert 'FW' in m.LC_CONDITIONS, m.LC_CONDITIONS
print('report condition FW is registered; heading:', m.LC_HEADINGS['FW'])
" >> "$OUTDIR/job.log" 2>&1 || die "the FW report condition is not registered in the deployed snapshot"
    log "preflight PASS: reuse verified, settings asserted, ledger live, tests green, FW condition registered"
    ;;
learn)
    EPOCH="${EPOCH:?set EPOCH=1,3,4 - the epoch whose reports consume this model}"
    TRACE_EPOCH="${OQA_TRACE_EPOCH:-$EPOCH}"
    require_live_ledger
    load_optimizer_key                       # the learner is an OPTIMIZER-side call; no actor key here
    export SKILLOPT_REQUIRE_ROUTE="$OPT_ROUTE;target=none"
    # THE CONTENT FALLBACK, AND ONLY IN THIS STAGE. This provider returns its reasoning in
    # `reasoning_content` and its reply in `content`, and a reply cut off by the completion budget can
    # leave `content` empty with a usable JSON block sitting in the reasoning field. `reasoning_text`
    # promotes that field when content is empty; for a learner that is safe, because the only thing
    # read out of it is a JSON object that then faces the whole gate.
    #
    # IT MUST NOT LEAK INTO THE ARM STAGE. There the same variable governs the ACTOR, where promoting
    # reasoning text into an answer would change what the scorer sees and would no longer be the
    # baselines' actor. The arm stage therefore keeps `committed_answer`, the value the baselines ran
    # under, and no actor key is loaded here at all, so this setting cannot reach a rollout.
    export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=reasoning_text
    export OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl"
    MODE="evolve"; PREV_ARG=()
    if [ "$EPOCH" = "1" ]; then
        MODE="seed"
        inject_epoch1
        verify_reuse "$ARM_OUT/steps/step_0001/rollout"
        PRED="$ARM_OUT/steps/step_0001/rollout/predictions"
        ORDER="$ARM_OUT/steps/step_0001/rollout/results.jsonl"
        CARRY=()
    else
        : "${OQA_PREV_MODEL:?set OQA_PREV_MODEL to the model frozen before this one}"
        [ -f "$OQA_PREV_MODEL" ] || die "no previous model at $OQA_PREV_MODEL"
        PREV_ARG=(--prev "$OQA_PREV_MODEL")
        SD=$(printf 'step_%04d' "$TRACE_EPOCH")
        PRED="$ARM_OUT/steps/$SD/rollout/predictions"
        ORDER="$ARM_OUT/steps/$SD/rollout/results.jsonl"
        [ -f "$ORDER" ] || die "no epoch-$TRACE_EPOCH rollout at $ARM_OUT/steps/$SD/rollout"
        CARRY=()
        for e in $(seq 1 $((TRACE_EPOCH - 1))); do
            ED=$(printf 'step_%04d' "$e")
            [ -f "$ARM_OUT/steps/$ED/rollout/results.jsonl" ] || continue
            CARRY+=(--carry "$ARM_OUT/steps/$ED/rollout/predictions:$ARM_OUT/steps/$ED/rollout/results.jsonl:e$e/")
        done
    fi
    log "learner pass: mode=$MODE model-for-epoch=$EPOCH traces-from-epoch=$TRACE_EPOCH proposer=teacher"
    "$OQA_PY" -I research/officeqa/oqa_fw_arm.py "$MODE" \
        --pred "$PRED" --order "$ORDER" --tag "e$TRACE_EPOCH/" \
        --out "$MODELS" --epoch "$EPOCH" --arm FW --proposer teacher \
        "${PREV_ARG[@]}" "${CARRY[@]}" >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "learner exit $status"
    [ -f "$OUTDIR/optimizer_calls.jsonl" ] \
        && log "learner attempts logged: $(wc -l < "$OUTDIR/optimizer_calls.jsonl")"
    # A LEARNER THAT WAS NEVER HEARD FROM IS NOT A LEARNING STEP. The instruction is explicit about
    # this, so it is checked here rather than inferred later from an empty model.
    "$OQA_PY" -I -c "
import json, sys
d = json.load(open('$MODELS/FW_ARM_FW_e$EPOCH.json'))
lc = d.get('learner_calls') or {}
rounds = [e for e in d.get('events') or [] if e.get('phase') == 'teacher']
called = sum(1 for e in rounds if e.get('called'))
parsed = sum(1 for e in rounds if e.get('parsed'))
print(json.dumps({'learner_attempts': lc.get('n'), 'parsed': lc.get('n_parsed'),
                  'teacher_rounds': len(rounds), 'called': called, 'reply_parsed': parsed,
                  'proposer': d.get('proposer'), 'active': d.get('snapshot', {}).get('active')}))
if not called:
    sys.stderr.write('THE LEARNER WAS NEVER CALLED: this is an operational failure, not an abstention\n')
    sys.exit(7)
if not parsed:
    sys.stderr.write('THE LEARNER WAS CALLED BUT NO REPLY PARSED: an empty operational failure, which '
                     'must not be recorded as a learning step\n')
    sys.exit(8)
" >> "$OUTDIR/job.log" 2>&1 || { tail -20 "$OUTDIR/job.log"; die "the learner did not actually run; see job.log"; }
    [ "$status" = 0 ] || die "the learner pass exited $status"
    cp "$MODELS/FW_ARM_FW_e$EPOCH.json" "$OUTDIR/" 2>/dev/null || true
    cp "$MODELS/FW_LEARNER_CALLS_FW_e$EPOCH.jsonl" "$OUTDIR/" 2>/dev/null || true
    log "frozen: $(sha256sum "$MODELS/FW_MODEL_e$EPOCH.json" | cut -c1-16) -> $MODELS/FW_MODEL_e$EPOCH.json"
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    ;;
arm)
    EPOCH="${EPOCH:?set EPOCH=1..4}"
    : "${OQA_MODEL:?set OQA_MODEL to the frozen model that this epoch reports rest on}"
    [ -f "$OQA_MODEL" ] || die "no model at $OQA_MODEL"
    require_live_ledger
    load_actor_key
    load_optimizer_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;$OPT_ROUTE"
    mkdir -p "$ARM_OUT" "$REPORTS/e$EPOCH"
    # THE CONTRACT ARM. Three variables and nothing else distinguishes it from the executed baseline.
    export OFFICEQA_STAGE_REPORT_CONDITION=FW
    export OFFICEQA_STAGE_REPORT_DIR="$REPORTS/e$EPOCH"
    export OFFICEQA_LC_MODEL="$OQA_MODEL"
    log "contract arm epoch $EPOCH: condition FW, model $(sha256sum "$OQA_MODEL" | cut -c1-16), reports $OFFICEQA_STAGE_REPORT_DIR"
    if [ "$EPOCH" = "1" ]; then
        inject_epoch1
        verify_reuse "$ARM_OUT/steps/step_0001/rollout"
    fi
    "$OQA_PY" -I research/officeqa/oqa_optimizer_precheck.py \
        --config "$CFG" --rollout "$ARM_OUT/steps/step_0001/rollout" \
        --budget "${OQA_CALL_BUDGET:-24}" --steps 1 \
        --out "$OUTDIR/optimizer_precheck.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "optimizer precheck failed; no paid call was made (see optimizer_precheck.json)"
    OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl" \
    TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/actor_calls.jsonl" \
    "$OQA_PY" -I scripts/train.py --config "$CFG" \
        --optimizer_model "${OPTIMIZER_MODEL:-deepseek-flash}" --out_root "$ARM_OUT" \
        --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
                      "train.num_epochs=$EPOCH" "evaluation.eval_test=false" \
        >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "trainer exit $status"
    for f in optimizer_calls.jsonl actor_calls.jsonl; do
        [ -f "$OUTDIR/$f" ] && log "$f: $(wc -l < "$OUTDIR/$f") attempts"
    done
    # DID THE REPORT ACTUALLY REACH THE OPTIMIZER? Verified from the artifacts, not assumed.
    "$OQA_PY" -I research/officeqa/oqa_optimizer_input_audit.py "$ARM_OUT" \
        --report-dir "$OFFICEQA_STAGE_REPORT_DIR" --minibatch-size 8 \
        --out "$OUTDIR/FW_UPTAKE_e$EPOCH.json" >> "$OUTDIR/job.log" 2>&1 \
        || log "note: the optimizer-input audit exited non-zero; see job.log"
    cp "$OUTDIR/FW_UPTAKE_e$EPOCH.json" "$FW_ROOT/" 2>/dev/null || true
    NREP=$(ls -1 "$OFFICEQA_STAGE_REPORT_DIR" 2>/dev/null | wc -l)
    log "reports written this epoch: $NREP"
    if [ -f "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG" ]; then
        log "prompt-budget decisions: $(wc -l < "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG")"
    fi
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    [ "$status" = 0 ] || die "the trainer exited $status"
    ;;
heldout)
    : "${OQA_SKILL:?set OQA_SKILL to the skill file to evaluate}"
    [ -f "$OQA_SKILL" ] || die "no such skill file: $OQA_SKILL"
    LABEL="${OQA_LABEL:-contract_arm}"
    # AN ENDPOINT THE BASELINES ALREADY EVALUATED IS NOT RE-EVALUATED. The same skill under the same
    # split, config and turn budget is the same measurement, and it is sealed.
    D=$(sha256sum "$OQA_SKILL" | cut -d' ' -f1)
    log "endpoint $LABEL digest $D"
    for prev in "$OQA_BASE_ROOT"/heldout/*/PROVENANCE.json; do
        [ -f "$prev" ] || continue
        if grep -q "\"skill_sha256\": \"$D\"" "$prev"; then
            PREVLABEL=$(basename "$(dirname "$prev")")
            log "SKIPPING: this endpoint is byte-identical to the already-evaluated $PREVLABEL,"
            log "which the completed baselines scored on the held-out split. That result is reused."
            printf '{"skipped": true, "reason": "identical to an already-evaluated endpoint", "previous": "%s", "skill_sha256": "%s"}\n' \
                "$prev" "$D" > "$OUTDIR/HELDOUT_SKIPPED.json"
            cp "$OUTDIR/HELDOUT_SKIPPED.json" "$FW_ROOT/" 2>/dev/null || true
            exit 0
        fi
    done
    require_live_ledger
    load_actor_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    OUT="$FW_ROOT/heldout/$LABEL"
    mkdir -p "$OUT" "$OUTDIR/skill_under_test"
    cp "$OQA_SKILL" "$OUTDIR/skill_under_test/$LABEL.md"
    sha256sum "$OQA_SKILL" > "$OUTDIR/skill_under_test/$LABEL.sha256"
    export TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/actor_calls.jsonl"
    log "held-out evaluation of $LABEL ($(wc -c < "$OQA_SKILL") bytes), TWO passes of 30 questions"
    for p in 1 2; do
        "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$OQA_SKILL" \
            --split valid_unseen --out_root "$OUT/p$p" \
            --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
            >> "$OUTDIR/job.log" 2>&1 || log "WARNING: held-out pass $p exited $?"
        n=$( [ -f "$OUT/p$p/results.jsonl" ] && wc -l < "$OUT/p$p/results.jsonl" || echo 0 )
        log "pass $p: $n of 30 held-out episodes"
    done
    "$OQA_PY" -I -c "
import hashlib, json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
skill = open('$OQA_SKILL','rb').read(); cfg = open('$CFG','rb').read()
json.dump({'schema':'lso.heldout_provenance.v2','label':'$LABEL','skill_path':'$OQA_SKILL',
           'skill_sha256':hashlib.sha256(skill).hexdigest(),'skill_bytes':len(skill),
           'split':'valid_unseen','n_questions':30,'passes':2,'config':'$CFG',
           'config_sha256':hashlib.sha256(cfg).hexdigest(),'frozen_sets':'$FROZEN',
           'commit':os.environ.get('OQA_COMMIT'),'rule':'OQA-FW-1'},
          open('$OUT/PROVENANCE.json','w'), indent=1, sort_keys=True)
" >> "$OUTDIR/job.log" 2>&1 || log "note: provenance record failed"
    cp "$OUT/PROVENANCE.json" "$OUTDIR/" 2>/dev/null || true
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    ;;
esac
log "stage $STAGE done (status $status)"
exit "$status"
