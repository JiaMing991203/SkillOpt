#!/bin/bash
# THE SECOND OfficeQA FOLLOW-UP: answer recovery, twelve development episodes, the repaired connected
# contract, and the corrected-client S0 reproducibility measurement. CPU only; GPUs are never requested.
#
#   sbatch --wrap="bash <bundle>/oqa_fu2_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>[,...]
#
#   PHASE=tests      free. The whole test suite. Every API key is dropped before Python starts.
#   PHASE=prep       free. Records the 23 earlier probe sends in the shared ledger (once), freezes the
#                    episode plan, replays the saved HTTP bodies under the old client and the new policy,
#                    and freezes the connected contract's semantic bundle. No key is loaded.
#   PHASE=episodes   PAID, actor only. COND=A|B, REP=1|2. Three frozen TRAINING questions, run to
#                    completion with the narrow answer-recovery policy and the raw capture on.
#   PHASE=cc         free unless OQA_CC_PROPOSE=1. Re-score, prefix-only recheck, compare, Lean.
#   PHASE=s0         PAID, actor only. PASSES=<n> passes of the 24 validation questions under S0.
#
# WHAT THIS BODY ENFORCES, each because of a recorded failure:
#   1. --wrap and node-local /tmp (NODE_FAIL at 60s on eight nodes, jobs 66262999 / 66263561).
#   2. A requeue REFUSES: a restart begins with a fresh in-process counter (exit 6). The durable ledger
#      is the real guard; this is belt and braces.
#   3. The route is PINNED and proved in-process (job 66271190 dispatched to Azure and recorded a
#      result that looks like a provider failure and is not one).
#   4. Only the keys a phase may spend are loaded; the rest are dropped before Python starts.
#
# THE ALLOWANCE, frozen on 2026-11-18 from the ledger's own reading (target 10,158 attempts /
# $6.855447, optimizer 105 / $3.468882) plus the probe backfill (23 / $0.007213) plus the user's
# suballocation of 5,000 attempts / $4.00 actor and 4 attempts / $1.00 optimizer. The caps below are
# ABSOLUTE and are therefore computed once and passed in, never recomputed per job - a cap derived from
# "what has been spent so far" would grant a fresh allowance to every job.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:?set PHASE=tests|prep|episodes|cc|s0}"
case "$PHASE" in tests|prep|episodes|cc|s0) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}; a restart must not respend." >&2
    exit 6
fi

WORK="/tmp/oqa_fu2_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}${COND:+_$COND}${REP:+_r$REP}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE cond=${COND:-} rep=${REP:-} host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_CAMPAIGN_ROOT:?}" ; : "${OQA_DOCS_DIR:?}" ; : "${OQA_SPLIT3_DIR:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
FU="$CAMP/followup_20261119"          # a SEPARATELY LABELLED directory; followup_20261118 is untouched
mkdir -p "$FU" || exit 3

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
ACTOR_KEY=""; OPT_KEY=""
load_actor_key() {
    : "${ACTOR_ENV_FILE:?settings.env must define ACTOR_ENV_FILE}"
    set -a; . "$ACTOR_ENV_FILE"; set +a
    [ -n "${EMPIRIOLABS_API_KEY:-}" ] || die "EMPIRIOLABS_API_KEY not set by $ACTOR_ENV_FILE"
    export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
    ACTOR_KEY="$EMPIRIOLABS_API_KEY"; unset EMPIRIOLABS_API_KEY
    unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY
    log "actor key loaded (length ${#ACTOR_KEY}); every other key dropped"
}
load_optimizer_key() {
    : "${OQA_OPTIMIZER_ENV_FILE:?settings.env must define OQA_OPTIMIZER_ENV_FILE}"
    set -a; . "$OQA_OPTIMIZER_ENV_FILE"; set +a
    [ -n "${DEEPSEEK_API_KEY:-}" ] || die "DEEPSEEK_API_KEY not set"
    export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
    OPT_KEY="$DEEPSEEK_API_KEY"; unset DEEPSEEK_API_KEY
    log "optimizer key loaded (length ${#OPT_KEY})"
}

ledger_env() {
    export SKILLOPT_SPEND_LEDGER_DIR="$CAMP/ledger"
    # ABSOLUTE caps; see the header for the arithmetic.
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_FU2_CAP_ACTOR_ATTEMPTS:-15181}"
    export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_FU2_CAP_ACTOR_USD:-10.8626}"
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER="${OQA_FU2_CAP_OPT_CALLS:-109}"
    export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER="${OQA_FU2_CAP_OPT_USD:-4.4688}"
    export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
    export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
    export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=50
    export SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.05
    export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_OPTIMIZER=2
    export SKILLOPT_LEDGER_BLOCK_USD_OPTIMIZER=0.05
    export SKILLOPT_LEDGER_RESERVE_USD_TARGET=0.02
    export SKILLOPT_LEDGER_RESERVE_USD_OPTIMIZER=0.15
    export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET="${OQA_JOB_CAP_ACTOR_ATTEMPTS:-400}"
    export SKILLOPT_LEDGER_JOB_MAX_USD_TARGET="${OQA_JOB_CAP_ACTOR_USD:-0.40}"
    export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER="${OQA_JOB_CAP_OPT_CALLS:-2}"
    export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER="${OQA_JOB_CAP_OPT_USD:-0.60}"
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
status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"

case "$PHASE" in
tests)
    drop_all_keys
    log "every API key dropped: this phase cannot send a paid request"
    "$OQA_PY" -I -m pytest -q tests/ 2>&1 | tail -40 | tee "$OUTDIR/tests.txt"
    status=${PIPESTATUS[0]}
    ;;
prep)
    drop_all_keys
    log "every API key dropped: this phase cannot send a paid request"
    mkdir -p "$FU/episodes" "$FU/connected_contract" "$FU/recovery"

    log "1/4 recording the 23 earlier probe sends in the shared ledger (idempotent)"
    SKILLOPT_SPEND_LEDGER_DIR="$CAMP/ledger" \
    SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_FU2_CAP_ACTOR_ATTEMPTS:-15181}" \
    SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_FU2_CAP_ACTOR_USD:-10.8626}" \
    SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02" \
    SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=25 SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.02 \
    "$OQA_PY" -I research/officeqa/oqa_ledger_backfill.py \
        --exchanges "$CAMP/followup_20261118/http_probe" --ledger "$CAMP/ledger" \
        --out "$FU/LEDGER_BACKFILL.json" ${OQA_BACKFILL_APPLY:+--apply} \
        2>&1 | tee "$OUTDIR/backfill.txt" || status=$?

    log "2/4 freezing the episode plan: the three questions, their documents and every setting"
    "$OQA_PY" -I research/officeqa/oqa_episodes.py freeze \
        --selection "$CAMP/followup_20261118/http_probe/SELECTION.json" \
        --frozen-sets docs/research/OFFICEQA_FROZEN_SETS_V3.json \
        --split3 "$OQA_SPLIT3_DIR" --docs "$OQA_DOCS_DIR" --skill "$S0" --config "$CFG" \
        --out "$FU/episodes" 2>&1 | tee "$OUTDIR/freeze_episodes.txt" || status=$?

    log "3/4 replaying the saved HTTP bodies: the old client beside the narrow policy"
    "$OQA_PY" -I research/officeqa/oqa_recovery.py replay \
        --exchanges "$CAMP/followup_20261118/http_probe" --out "$FU/recovery" \
        2>&1 | tee "$OUTDIR/replay.txt" || status=$?

    log "4/4 freezing the connected contract's COMPLETE semantic bundle, before any record is re-read"
    CC18="$CAMP/followup_20261118/connected_contract"
    "$OQA_PY" -I research/officeqa/oqa_cc_bundle.py freeze \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
        --prompt "$CC18/CC_PROMPT.txt" --out "$FU/connected_contract" --label v2 \
        2>&1 | tee "$OUTDIR/freeze_bundle.txt" || status=$?
    ;;
episodes)
    # ALL TWELVE EPISODES IN ONE JOB: three questions x two reps x two conditions. One job, because the
    # CPU allowance is counted in REQUESTED job-hours and four jobs would request four times the
    # walltime for the same ~25 minutes of work. Each (condition, rep) gets its OWN output directory,
    # which matters: `run_batch` resumes by question id, so two reps sharing a directory would be one.
    PLAN="$FU/episodes/EPISODE_PLAN.json"
    [ -f "$PLAN" ] || die "no frozen episode plan at $PLAN; run PHASE=prep first"
    [ -f "$S0" ] || die "no S0 skill at $S0"
    SPLIT="$("$OQA_PY" -I -c "import json;print(json.load(open('$PLAN'))['split_dir'])")"
    [ -d "$SPLIT/train" ] || die "the frozen episode split is missing: $SPLIT"
    PLAN_SHA="$("$OQA_PY" -I -c "import json;print(json.load(open('$PLAN'))['plan_sha256'][:16])")"
    log "plan $PLAN_SHA  split $SPLIT  skill $(sha256sum "$S0" | cut -c1-16)"
    load_actor_key
    ledger_env
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    # ANSWER-FIELD HANDLING: the narrow policy, identical in both conditions.
    export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=committed_answer
    for COND in ${OQA_EPI_CONDS:-A B}; do
        for REP in ${OQA_EPI_REPS:-1 2}; do
            # GENERATION SETTINGS: condition B adds exactly one documented serving control; condition A
            # adds none, so the historical payload's omissions stay omitted.
            if [ "$COND" = "B" ]; then
                export TARGET_OPENAI_COMPATIBLE_EXTRA_BODY='{"enable_thinking": false}'
            else
                unset TARGET_OPENAI_COMPATIBLE_EXTRA_BODY
            fi
            OUT="$FU/episodes/${COND}${REP}"
            if [ -f "$OUT/results.jsonl" ] && [ "$(wc -l < "$OUT/results.jsonl")" -ge 3 ]; then
                log "$COND$REP already complete ($(wc -l < "$OUT/results.jsonl") rows); skipping"
                continue
            fi
            mkdir -p "$OUT"
            export TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUT/actor_calls.jsonl"
            export TARGET_OPENAI_COMPATIBLE_RAW_CAPTURE="$OUT/raw_capture.jsonl"
            log "episodes $COND$REP -> $OUT  extra_body=${TARGET_OPENAI_COMPATIBLE_EXTRA_BODY:-<none>}"
            "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$S0" \
                --split train --out_root "$OUT" \
                --cfg-options "env.split_dir=$SPLIT" "env.data_dirs=$OQA_DOCS_DIR" "env.workers=1" \
                >> "$OUTDIR/job.log" 2>&1 || { status=$?; log "$COND$REP exited $status"; }
            [ -f "$OUT/results.jsonl" ] && log "$COND$REP: $(wc -l < "$OUT/results.jsonl") row(s)"
        done
    done
    log "summarising the twelve episodes"
    RUNS=()
    for COND in A B; do for REP in 1 2; do
        [ -d "$FU/episodes/${COND}${REP}" ] && RUNS+=(--run "${COND}${REP}=$FU/episodes/${COND}${REP}")
    done; done
    "$OQA_PY" -I research/officeqa/oqa_episodes.py summarize --plan "$PLAN" "${RUNS[@]}" \
        --out "$FU/episodes" 2>&1 | tee "$OUTDIR/episode_summary.txt" || log "NOTE: summary failed"
    log "re-deciding the captured bodies under the OLD adapter, offline and free"
    "$OQA_PY" -I research/officeqa/oqa_recovery.py replay \
        $(for COND in A B; do for REP in 1 2; do
              [ -f "$FU/episodes/${COND}${REP}/raw_capture.jsonl" ] \
                  && printf -- "--exchanges %s " "$FU/episodes/${COND}${REP}/raw_capture.jsonl"
          done; done) --out "$FU/episodes" 2>&1 | tee "$OUTDIR/episode_replay.txt" \
        || log "NOTE: the replay failed"
    ;;
cc)
    CC18="$CAMP/followup_20261118/connected_contract"
    CC="$FU/connected_contract"
    mkdir -p "$CC"
    [ -f "$CC/CC_BUNDLE_v2.json" ] || die "no frozen semantic bundle; run PHASE=prep first"
    if [ "${OQA_CC_PROPOSE:-0}" = "1" ]; then
        load_optimizer_key
        ledger_env
        export OPTIMIZER_OPENAI_COMPATIBLE_MODEL="${OPTIMIZER_MODEL:-deepseek-flash}"
        export OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl"
        export SKILLOPT_REQUIRE_ROUTE="optimizer=openai_compatible|https://api.deepseek.com|deepseek-flash;target=none"
        log "a new proposal IS authorized for this job: at most 2 DeepSeek attempts"
    else
        drop_all_keys
        log "every API key dropped: the re-score, the recheck, the comparison and Lean send nothing"
    fi
    # THE BUNDLE IN FORCE FOR THIS CHECK. v2 was frozen in PHASE=prep; if the checker's source has
    # changed since, the honest move is to freeze the CURRENT meaning under its own label, run the
    # check under that, and report what moved between the two - which is the whole purpose of a bundle.
    log "1/5 freezing the bundle in force for this check, then naming what moved since v2"
    "$OQA_PY" -I research/officeqa/oqa_cc_bundle.py freeze \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
        --prompt "$CC18/CC_PROMPT.txt" --out "$CC" --label v3 \
        2>&1 | tee "$OUTDIR/bundle_freeze_v3.txt" || status=$?
    "$OQA_PY" -I research/officeqa/oqa_cc_bundle.py verify --bundle "$CC/CC_BUNDLE_v3.json" \
        2>&1 | tee "$OUTDIR/bundle_verify_v3.txt" || log "NOTE: v3 reports changed components"
    "$OQA_PY" -I research/officeqa/oqa_cc_bundle.py verify --bundle "$CC/CC_BUNDLE_v2.json" \
        2>&1 | tee "$OUTDIR/bundle_verify_v2.txt" || log "NOTE: v2 reports changed components"
    log "2/5 re-scoring the EXISTING hypothesis on the repaired learning records"
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
        --which learning --out "$CC" 2>&1 | tee "$OUTDIR/cc_learning.txt" || status=$?
    log "3/5 prefix-only checking of the reserved checking records"
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
        --which checking --out "$CC" 2>&1 | tee "$OUTDIR/cc_checking.txt" || status=$?
    log "4/5 the comparison, against each record's own arm, with the witnesses"
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py compare \
        --campaign "$CAMP" --check "$CC/CC_CHECK_checking.json" \
        --records "$CC18/CC_RECORDS_v2.json" --out "$CC" \
        2>&1 | tee "$OUTDIR/cc_compare.txt" || status=$?
    log "5/5 repair if a counterexample exists, then Lean"
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py repair \
        --model "$CC18/CC_MODEL_v1.json" --check "$CC/CC_CHECK_checking.json" \
        --records "$CC18/CC_RECORDS_v2.json" --out "$CC" \
        2>&1 | tee "$OUTDIR/cc_repair.txt" || status=$?
    # DESCRIPTIVE ONLY, and labelled as such: the twelve new episode traces did not exist when the
    # semantic bundle was frozen, nothing about them was reserved in advance, and three questions test
    # nothing. What a check over them produces is a description.
    if [ -d "$FU/episodes/A1/predictions" ]; then
        log "6/6 a DESCRIPTIVE check over the twelve new episode traces"
        RUNS=()
        for COND in A B; do for REP in 1 2; do
            [ -d "$FU/episodes/${COND}${REP}/predictions" ] \
                && RUNS+=(--run "${COND}${REP}=$FU/episodes/${COND}${REP}")
        done; done
        "$OQA_PY" -I research/officeqa/oqa_episodes.py records "${RUNS[@]}" \
            --out "$CC/CC_RECORDS_episodes.json" 2>&1 | tee "$OUTDIR/cc_episode_records.txt" \
            || log "NOTE: the descriptive records could not be built"
        if [ -f "$CC/CC_RECORDS_episodes.json" ]; then
            "$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
                --model "$CC18/CC_MODEL_v1.json" --records "$CC/CC_RECORDS_episodes.json" \
                --which descriptive --out "$CC" 2>&1 | tee "$OUTDIR/cc_descriptive.txt" \
                || log "NOTE: the descriptive check failed"
        fi
    fi
    LEAN_BIN="${OQA_LEAN_BIN:-/nfs/turbo/umms-csriram/yaojm/lean/elan/toolchains/leanprover--lean4---v4.20.0/bin/lean}"
    "$OQA_PY" -I research/officeqa/oqa_cc_lean.py --check "$CC/CC_CHECK_checking.json" \
        --out "$CC" --lean-bin "$LEAN_BIN" 2>&1 | tee "$OUTDIR/cc_lean.txt" || status=$?
    # The S0 summary runs here because this phase is free and runs after the paid passes. It reads
    # whatever passes exist and says how many; it does not wait for eight.
    if [ -d "$FU/s0_repeat" ]; then
        PASSARGS=()
        for d in "$FU"/s0_repeat/p*; do
            [ -f "$d/results.jsonl" ] && PASSARGS+=(--pass "$(basename "$d")=$d")
        done
        if [ "${#PASSARGS[@]}" -gt 0 ]; then
            log "summarising the corrected-client S0 measurement (${#PASSARGS[@]} pass(es) found)"
            "$OQA_PY" -I research/officeqa/oqa_s0repeat.py "${PASSARGS[@]}" --out "$FU/s0_repeat" \
                2>&1 | tee "$OUTDIR/s0_summary.txt" || log "NOTE: the S0 summary failed"
        fi
    fi
    ;;
s0)
    PASSES="${PASSES:?set PASSES to the number of passes}"
    FIRST="${FIRST:-1}"
    [ -f "$S0" ] || die "no S0 skill at $S0"
    load_actor_key
    ledger_env
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    # CONDITION A IS THE PRIMARY CONFIGURATION: the historical generation payload, with answer-field
    # recovery. Thinking-off is NOT substituted here, whatever the development episodes scored.
    export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=committed_answer
    unset TARGET_OPENAI_COMPATIBLE_EXTRA_BODY
    log "S0 reproducibility: passes $FIRST..$((FIRST + PASSES - 1)) of the 24 validation questions"
    log "primary configuration = condition A (historical payload + answer recovery)"
    for i in $(seq "$FIRST" $((FIRST + PASSES - 1))); do
        OUT="$FU/s0_repeat/p$i"
        if [ -f "$OUT/results.jsonl" ] && \
           [ "$(wc -l < "$OUT/results.jsonl")" -ge 24 ]; then
            log "pass $i already complete ($(wc -l < "$OUT/results.jsonl") rows); skipping"
            continue
        fi
        mkdir -p "$OUT"
        export TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUT/actor_calls.jsonl"
        unset TARGET_OPENAI_COMPATIBLE_RAW_CAPTURE
        log "pass $i -> $OUT"
        "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$S0" \
            --split valid_seen --out_root "$OUT" \
            --cfg-options "env.split_dir=$OQA_SPLIT3_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
            >> "$OUTDIR/job.log" 2>&1 || { status=$?; log "pass $i exited $status"; }
        if [ -f "$OUT/results.jsonl" ]; then
            log "pass $i: $(wc -l < "$OUT/results.jsonl") rows, hard=$("$OQA_PY" -I -c "
import json,sys
print(sum(int(json.loads(l).get('hard') or 0) for l in open('$OUT/results.jsonl') if l.strip()))
")"
        fi
    done
    ;;
esac

if [ "$PHASE" = "episodes" ] || [ "$PHASE" = "s0" ] || [ "${OQA_CC_PROPOSE:-0}" = "1" ]; then
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || log "WARNING: ledger report failed"
    [ -f "$OUTDIR/LEDGER_AFTER.json" ] && "$OQA_PY" -I -c "
import json
d=json.load(open('$OUTDIR/LEDGER_AFTER.json'))
for role,v in sorted(d.get('observed',{}).get('by_role',{}).items()):
    print('  ledger %-9s attempts=%-6d usd=%.4f refusals=%d' % (role, v['attempts'], v['usd'], v['refusals']))
print('  ledger verdict:', d.get('verdict'), d.get('problems') or '')
" | tee -a "$OUTDIR/job.log"
fi

for KEY in "$ACTOR_KEY" "$OPT_KEY"; do
    [ -n "$KEY" ] || continue
    if grep -rIl -e "$KEY" "$OUTDIR" "$FU" 2>/dev/null | grep -q .; then
        log "ERROR: a key appears in an artifact; removing those files"
        grep -rIl -e "$KEY" "$OUTDIR" "$FU" 2>/dev/null | xargs -r rm -f
        status=5
    fi
done
rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR"
exit "$status"
