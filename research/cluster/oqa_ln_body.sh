#!/bin/bash
# THE LEARNED SOURCE-TO-CALCULATION CONTRACT. Rule OQA-LN-1, amendment D-1.
#
#   sbatch --wrap="bash <bundle>/oqa_ln_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>
#
#   PHASE=ledger    FREE. Verify the spend ledger ON /nfs/turbo with the race fix in place: the
#                   deterministic over-grant mechanism, the four-process race twelve times, restarts
#                   and fail-closed. Then run the OfficeQA test suite and record EXACTLY which
#                   assertions fail, rather than only that a stage exited non-zero.
#   PHASE=freeze    FREE. Freeze the evidence set, the extended vocabulary as bundle v10, and the
#                   phase accounting. Written before any learner call and quoted by every later result.
#   PHASE=learn     PAID. Two independent learner repetitions, each: propose -> validate -> score on
#                   the LEARNING records -> freeze -> predict PROSPECTIVELY on the checking questions
#                   (predictions written and digested BEFORE any verdict is read) -> classify ->
#                   repair from the counterexamples -> recheck the learning records for regressions.
#                   At most 2 paid calls per repetition.
#   PHASE=lean      FREE. Generate and COMPILE the Lean file over the model applications, the stage
#                   connections and the uncertainty/recovery cases. Records the source digest, the
#                   compiler version, the return code and the output.
#   PHASE=rehearse  PAID. One isolated paired rehearsal through SkillOpt's real analyst entry point:
#                   the ordinary analyst, and the same analyst with the checked contract report added.
#                   Exactly 2 paid calls, matched model, budget, sampling and call count.
#
# THE SINGLE PAYMENT COORDINATOR. The concurrency defect in the shared ledger was reproduced
# deterministically and fixed (amendment in skillopt/model/spend_ledger.py), but a fix verified once is
# not a reason to run four payers at once while the allowance is 24 attempts. So this phase takes an
# exclusive PAYER TOKEN before any key is loaded: `mkdir` on a single directory, which is atomic on
# NFS, holding this job's id. A second paid job finds it and refuses. Limiting each job to one request
# would NOT be enough - several jobs could still spend independently - which is why the token is
# phase-wide and not per-job.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:-freeze}"
case "$PHASE" in ledger|freeze|learn|restage|lean|rehearse|d2) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}; a requeue would re-spend." >&2; exit 6
fi

WORK="/tmp/oqa_ln_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}" ; : "${OQA_LN_ROOT:?}" ; : "${OQA_LEDGER_DIR:?}"
: "${OQA_BASE_ROOT:?}"

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || die "bundle manifest mismatch"
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"
RES="$WORK/research/officeqa"

# The route the guard must see, in its own format: semicolons between roles, pipes inside one. The
# previous value named no base url or model, so the guard could not have pinned the backend at all.
OPT_ROUTE="optimizer=openai_compatible|https://api.deepseek.com|deepseek-flash"
S0="$OQA_BASE_ROOT/s0"
OUT="$OQA_LN_ROOT"
mkdir -p "$OUT" || exit 3

# ── the ledger, identically configured for every paid phase ──────────────────
ledger_env() {
    mkdir -p "$OQA_LEDGER_DIR/claims" "$OQA_LEDGER_DIR/spend" "$OQA_LEDGER_DIR/release" \
        || die "cannot create the ledger at $OQA_LEDGER_DIR"
    export SKILLOPT_SPEND_LEDGER_DIR="$OQA_LEDGER_DIR"
    # The PHASE caps. The follow-up sublimit is 24 optimizer attempts AND $3, inside the phase
    # ceilings of 400 and $20 that the two-baseline campaign already drew 206 and $1.98 against.
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER="${OQA_CAP_OPT_CALLS:-400}"
    export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER="${OQA_CAP_OPT_USD:-20.00}"
    export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_CAP_ACTOR_ATTEMPTS:-20000}"
    export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_CAP_ACTOR_USD:-15.00}"
    export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
    export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
    export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_OPTIMIZER=2
    export SKILLOPT_LEDGER_BLOCK_USD_OPTIMIZER=0.10
    export SKILLOPT_LEDGER_RESERVE_USD_OPTIMIZER=0.15
    # THIS FOLLOW-UP'S OWN SUBLIMIT, as a per-process ceiling on top of the shared cap.
    export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER="${OQA_LN_JOB_CALLS:-8}"
    export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER="${OQA_LN_JOB_USD:-1.50}"
}

# `-I` is isolated mode: it does NOT put the current directory on sys.path and it IGNORES PYTHONPATH,
# so `import skillopt` fails under it however the job is cd-ed. The tree root has to be inserted
# explicitly, exactly as oqa_base_body.sh does. Job 66580714 is what this comment costs: the ledger
# probe raised ModuleNotFoundError, `require_live_ledger` read that as an inactive ledger and refused
# to load the key - which is the CORRECT fail-closed behaviour on a wrong premise, and spent nothing.
ledger_report() {
    "$OQA_PY" -I -c "
import json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
from skillopt.model import spend_ledger as L
print(json.dumps(L.report(), indent=2, sort_keys=True))
"
}

require_live_ledger() {
    "$OQA_PY" -I -c "
import json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
from skillopt.model import spend_ledger as L
snap = L.report()
print(json.dumps({'active': snap.get('active'), 'dir': snap.get('dir'),
                  'verdict': snap.get('verdict'), 'problems': snap.get('problems')}))
sys.exit(0 if snap.get('active') else 9)
" > "$OUTDIR/ledger_live.json" 2>&1 \
        || die "the spend ledger is NOT ACTIVE, so the caps are not enforced. Refusing to load a key. \
See ledger_live.json"
    log "ledger live: $(tr -d '\n' < "$OUTDIR/ledger_live.json")"
}

# ── the phase-wide single payment coordinator ───────────────────────────────
PAYER="$OQA_LEDGER_DIR/payer.single"
take_payer_token() {
    if mkdir "$PAYER" 2>/dev/null; then
        printf '%s\n' "${SLURM_JOB_ID:-$$}" > "$PAYER/job"
        date -u +%Y-%m-%dT%H:%M:%SZ > "$PAYER/since"
        log "payer token TAKEN by job ${SLURM_JOB_ID:-$$}"
        trap 'rm -f "$PAYER/job" "$PAYER/since" 2>/dev/null; rmdir "$PAYER" 2>/dev/null; \
              log "payer token released"' EXIT
        return 0
    fi
    local holder; holder="$(cat "$PAYER/job" 2>/dev/null || echo unknown)"
    if [ "$holder" != "unknown" ] && command -v squeue >/dev/null 2>&1 \
       && ! squeue -h -j "$holder" >/dev/null 2>&1; then
        log "the payer token is held by job $holder, which the scheduler no longer knows. Breaking it."
        rm -f "$PAYER/job" "$PAYER/since" 2>/dev/null; rmdir "$PAYER" 2>/dev/null
        take_payer_token; return $?
    fi
    die "another paid job ($holder) holds the single-payer token at $PAYER. Refusing to spend \
concurrently while this follow-up's allowance is 24 optimizer attempts."
}

load_optimizer_key() {
    # The credential file defines DEEPSEEK_API_KEY, not the backend's own variable name. Job 66580801
    # died on "the optimizer key is empty" because this read the wrong name - zero paid calls, and
    # the same mapping oqa_base_body.sh has always done.
    : "${OQA_OPTIMIZER_ENV_FILE:?settings.env must name the optimizer credential file}"
    [ -f "$OQA_OPTIMIZER_ENV_FILE" ] || die "no optimizer credential file at $OQA_OPTIMIZER_ENV_FILE"
    set -a; . "$OQA_OPTIMIZER_ENV_FILE"; set +a
    [ -n "${DEEPSEEK_API_KEY:-}" ] || die "DEEPSEEK_API_KEY not set by $OQA_OPTIMIZER_ENV_FILE"
    export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
    _klen=${#DEEPSEEK_API_KEY}
    unset DEEPSEEK_API_KEY
    log "optimizer key loaded (length $_klen; the value is never logged)"
}

drop_all_keys() {
    unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY \
          EMPIRIOLABS_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY \
          HF_TOKEN HUGGING_FACE_HUB_TOKEN
    log "every API key dropped: this phase cannot spend"
}

status=0
case "$PHASE" in

ledger)
    drop_all_keys
    log "verifying the spend ledger on the PRODUCTION filesystem, with the race fix in place"
    "$OQA_PY" -I "$RES/oqa_ledger_check.py" --root "$OQA_LEDGER_DIR/_selftest_ln" \
        --out "$OUTDIR/LEDGER_CHECK.json" --repeats 12 >> "$OUTDIR/job.log" 2>&1
    rc=$?
    rm -rf "$OQA_LEDGER_DIR/_selftest_ln"
    log "ledger check exit $rc; verdict $("$OQA_PY" -I -c "
import json;print(json.load(open('$OUTDIR/LEDGER_CHECK.json'))['verdict'])" 2>/dev/null || echo unreadable)"
    [ "$rc" = "0" ] || status=1
    # WHY IS THE STAGE-GROUP COUNT 17 WHERE THE HISTORICAL RECORD READS 14? An amendment claimed C-3
    # had returned it to 14 and called that support for C-3. The test says `assert 17 == 14`, so the
    # claim is wrong; this probe decides whether 17 is CORRECT under the current semantics from the
    # groups themselves, rather than from an argument about them.
    : "${OQA_CAMPAIGN_ROOT:?settings.env must name the frozen campaign root}"
    "$OQA_PY" -I "$RES/oqa_groups_probe.py" --campaign "$OQA_CAMPAIGN_ROOT" \
        --arm C --item UID0014 --epoch 3 --out "$OUTDIR/GROUPS_PROBE.json" \
        >> "$OUTDIR/job.log" 2>&1 || log "WARNING: the group probe failed"
    log "running the OfficeQA test suite, recording every failing assertion"
    export OQA_CAMPAIGN_ROOT
    "$OQA_PY" -I -m pytest -q -p no:cacheprovider \
        --tb=long -rf tests/ > "$OUTDIR/pytest.txt" 2>&1 || true
    tail -40 "$OUTDIR/pytest.txt" >> "$OUTDIR/job.log"
    "$OQA_PY" -I -m pytest -q -p no:cacheprovider --tb=no -rf tests/ 2>&1 \
        | tail -30 > "$OUTDIR/pytest_summary.txt" || true
    ;;

freeze)
    drop_all_keys
    V="$OUT/v1_freeze"; mkdir -p "$V" || exit 3
    "$OQA_PY" -I "$RES/oqa_ln_evidence.py" --s0-root "$S0" --out "$V/LN_FREEZE.json" \
        >> "$OUTDIR/job.log" 2>&1 || die "the evidence freeze failed"
    # The hand-checkable audit of a few occurrence bindings, against the observations themselves.
    "$OQA_PY" -I "$RES/oqa_ln_audit.py" --freeze "$V/LN_FREEZE.json" \
        --out "$V/LN_AUDIT.json" --per-kind 2 >> "$OUTDIR/job.log" 2>&1 \
        || log "WARNING: the occurrence audit failed"
    "$OQA_PY" -I -c "
import json, sys
sys.path.insert(0, '$RES')
import oqa_ln_vocab as V
json.dump({'bundle_label': V.BUNDLE_LABEL, 'digest': V.bundle_digest(),
           'predicates': {p: {'args': list(s['args']), 'provenance': s['provenance']}
                          for p, s in sorted(V.PREDICATES.items())},
           'entities': list(V.ENTITIES), 'stage_obligations': list(V.STAGE_OBLIGATIONS),
           'learner_conditions': list(V.LEARNER_CONDITIONS),
           'initial_model': V.initial_model()},
          open('$V/LN_VOCAB_v10.json', 'w'), indent=1, sort_keys=True)
print('vocabulary bundle', V.BUNDLE_LABEL, V.bundle_digest()[:16])
" >> "$OUTDIR/job.log" 2>&1 || die "the vocabulary freeze failed"
    cp "$V/LN_FREEZE.json" "$V/LN_VOCAB_v10.json" "$V/LN_AUDIT.json" "$OUTDIR/" 2>/dev/null || true
    log "freeze written to $V"
    ;;

learn)
    ledger_env
    take_payer_token
    require_live_ledger
    load_optimizer_key
    export SKILLOPT_REQUIRE_ROUTE="$OPT_ROUTE;target=none"
    V="$OUT/v2_learn"; mkdir -p "$V" || exit 3
    export OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl"
    ledger_report > "$OUTDIR/LEDGER_BEFORE.json" 2>> "$OUTDIR/job.log" || log "WARNING: ledger report failed"
    "$OQA_PY" -I "$RES/oqa_ln_run.py" --freeze "$OUT/v1_freeze/LN_FREEZE.json" --out "$V" \
        --repetitions "${OQA_LN_REPS:-2}" >> "$OUTDIR/job.log" 2>&1 || status=1
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    cp -r "$V"/*.json "$OUTDIR/" 2>/dev/null || true
    ;;

restage)
    drop_all_keys
    # FREE. Rebuild the stage reports from the models already frozen in v2_learn, after the
    # recovery_shape defect the Lean cross-check led to. The superseded reports stay where they are:
    # a corrected result that overwrites the result it corrects leaves nothing to check.
    V="$OUT/v2b_restage"; mkdir -p "$V" || exit 3
    "$OQA_PY" -I "$RES/oqa_ln_restage.py" --learn-root "$OUT/v2_learn" \
        --freeze "$OUT/v1_freeze/LN_FREEZE.json" --out "$V" >> "$OUTDIR/job.log" 2>&1 || status=1
    cp "$V"/*.json "$OUTDIR/" 2>/dev/null || true
    ;;

lean)
    drop_all_keys
    : "${OQA_LEAN_BIN:?settings.env must name the Lean toolchain binary, not the elan shim}"
    V="$OUT/v3_lean"; mkdir -p "$V" || exit 3
    # Read the RESTAGED reports when they exist, so Lean checks the corrected states.
    STAGE_SRC="$OUT/v2_learn"
    [ -d "$OUT/v2b_restage" ] && STAGE_SRC="$OUT/v2b_restage"
    log "lean reads stage reports from $STAGE_SRC"
    "$OQA_PY" -I "$RES/oqa_ln_lean.py" --learn-root "$STAGE_SRC" \
        --freeze "$OUT/v1_freeze/LN_FREEZE.json" --out "$V" --lean-bin "$OQA_LEAN_BIN" \
        >> "$OUTDIR/job.log" 2>&1 || status=1
    cp "$V"/*.json "$V"/*.lean "$OUTDIR/" 2>/dev/null || true
    ;;

d2)
    drop_all_keys
    # FREE, and it CANNOT spend: none of the oqa_d2_* modules imports the model layer, and a test
    # asserts that. Amendments D-2..D-5, replayed over the SAME frozen records with the SAME frozen
    # models - nothing is retrained and nothing is sent.
    : "${OQA_LEAN_BIN:?settings.env must name the Lean toolchain binary, not the elan shim}"
    V="$OUT/v5_d2"; L="$OUT/v5_lean"; mkdir -p "$V" "$L" || exit 3
    d2_driver() {
        "$OQA_PY" -I "$RES/oqa_d2_run.py" \
            --freeze "$OUT/v1_freeze/LN_FREEZE.json" \
            --learn-root "$OUT/v2_learn" \
            --rehearse-root "$OUT/v4_rehearse" \
            --compare-stage-root "$OUT/v2b_restage" \
            --lean "$1" \
            --out "$V" >> "$OUTDIR/job.log" 2>&1
    }
    # Pass 1 writes the corrected stage reports, which Lean then checks. Lean cannot run first.
    d2_driver "$OUT/v3_lean/LN_LEAN.json" || status=1
    "$OQA_PY" -I "$RES/oqa_ln_lean.py" --learn-root "$V" \
        --freeze "$OUT/v1_freeze/LN_FREEZE.json" --out "$L" --lean-bin "$OQA_LEAN_BIN" \
        >> "$OUTDIR/job.log" 2>&1 || status=1
    # Pass 2 is deterministic and free, and makes the regenerated reports quote the NEW receipt.
    d2_driver "$L/LN_LEAN.json" || status=1
    log "running the full test suite against the corrected code"
    export OQA_LN_FREEZE="$OUT/v1_freeze/LN_FREEZE.json"
    export OQA_CAMPAIGN_ROOT
    "$OQA_PY" -I -m pytest -q -p no:cacheprovider --tb=long -rf tests/ \
        > "$OUTDIR/pytest.txt" 2>&1 || true
    tail -40 "$OUTDIR/pytest.txt" >> "$OUTDIR/job.log"
    "$OQA_PY" -I -m pytest -q -p no:cacheprovider --tb=no -rf tests/ 2>&1 \
        | tail -30 > "$OUTDIR/pytest_summary.txt" || true
    cp "$V"/*.json "$OUTDIR/" 2>/dev/null || true
    cp "$L"/*.json "$L"/*.lean "$OUTDIR/" 2>/dev/null || true
    mkdir -p "$OUTDIR/reports_regenerated"
    cp "$V"/reports_regenerated/*.md "$OUTDIR/reports_regenerated/" 2>/dev/null || true
    ;;
rehearse)
    ledger_env
    take_payer_token
    require_live_ledger
    load_optimizer_key
    export SKILLOPT_REQUIRE_ROUTE="$OPT_ROUTE;target=none"
    V="$OUT/v4_rehearse"; mkdir -p "$V" || exit 3
    export OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl"
    ledger_report > "$OUTDIR/LEDGER_BEFORE.json" 2>> "$OUTDIR/job.log" || true
    "$OQA_PY" -I "$RES/oqa_ln_rehearse.py" --learn-root "$OUT/v2_learn" \
        --stage-root "$([ -d "$OUT/v2b_restage" ] && echo "$OUT/v2b_restage" || echo "$OUT/v2_learn")" \
        --lean-root "$OUT/v3_lean" --freeze "$OUT/v1_freeze/LN_FREEZE.json" \
        --skill "$OQA_BASE_ROOT/endpoint_S0.md" --out "$V" >> "$OUTDIR/job.log" 2>&1 || status=1
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    cp "$V"/*.json "$V"/*.md "$OUTDIR/" 2>/dev/null || true
    ;;
esac

log "phase=$PHASE done status=$status"
exit $status
