#!/bin/bash
# THE THREE-ARM OfficeQA CAMPAIGN. One body, four stages, CPU only (the actor is a hosted API and the document
# tools read local files, so no GPU is involved anywhere).
#
#   sbatch --wrap="bash <bundle>/oqa_campaign_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,STAGE=<stage>[,ARM=A|B|C,EPOCH=1..4,...]
#
#   STAGE=prep      free. Fetch the parsed documents rule OQA-SEL-3 needs, materialize the 24/24/60 split, and
#                   run the document-availability check over all 108 questions (UID0019 included). NO actor key
#                   and NO optimizer key are loaded, so this stage cannot spend a cent.
#   STAGE=shared    the SHARED epoch-1 work, actor only: the 24 training questions under the starting skill
#                   (gated on the first three) and then the 24 validation questions under the same skill, which
#                   is the gate's baseline. All three arms are given these exact traces, so the comparison is not
#                   confounded by the actor's sampling at temperature 0.7. The OPTIMIZER KEY IS NOT LOADED.
#   STAGE=arm       one ARM-EPOCH. Needs ARM, EPOCH, OQA_SHARED_DIR, OQA_ARM_OUT and - for B and C -
#                   OFFICEQA_LC_MODEL. This is the only stage that makes an optimizer call.
#   STAGE=heldout   one skill on the 60 held-out questions, actor only. OQA_SKILL names the skill, OQA_LABEL its
#                   output directory. The optimizer key is not loaded.
#
# THREE THINGS THIS BODY ENFORCES, each because of a recorded failure.
#
#   1. THE SUBMISSION SHAPE. Invoke via --wrap and work in node-local /tmp. A script submitted directly as the
#      sbatch batch file from /nfs/turbo was killed by NODE_FAIL at 60 s, four times each, on eight different
#      nodes (jobs 66262999, 66263561), before any Python ran; --no-requeue did not stop it. See
#      research/cluster/oqa_api_body.sh for the one-factor controls.
#
#   2. NO RESPENDING ON A RESTART. A Slurm requeue starts a fresh process. This body refuses to run when
#      SLURM_RESTART_COUNT is not 0 (exit 6), AND the authorization itself now lives in a durable shared ledger
#      (skillopt/model/spend_ledger.py) on /nfs/turbo, so even a restart that slipped through could not reset the
#      counter. Belt and braces, because the ledger is new and the refusal is cheap.
#
#   3. THE ROUTE IS PINNED AND PROVEN IN-PROCESS. skillopt.model.chat_optimizer is a ROUTER whose default is
#      Azure. Job 66271190 had a perfect openai-compatible config, dispatched to Azure, raised, had the exception
#      swallowed, and recorded `no_content` - a result that reads like a DeepSeek failure and is not one. Every
#      paid stage therefore sets SKILLOPT_REQUIRE_ROUTE, and the trainer and eval_only verify the LIVE route of
#      each role, including a zero-cost proof of the dispatch path, before sending anything.
#
# Reads <bundle>/settings.env, which is PRIVATE and stays on the cluster. Required keys:
#   OQA_PY OQA_DOCS_DIR ACTOR_ENV_FILE          as in oqa_api_body.sh
#   OQA_SPLIT3_DIR                              the 24/24/60 split directory (written by STAGE=prep)
#   OQA_CSV                                     the already-downloaded officeqa_full.csv
#   OQA_HF_ENV_FILE                             file defining HF_TOKEN (STAGE=prep only; the dataset is gated)
#   OQA_OPTIMIZER_ENV_FILE                      file defining DEEPSEEK_API_KEY (STAGE=arm only)
#   OQA_LEDGER_DIR                              the SHARED authorization ledger, on /nfs/turbo
#   OQA_CAMPAIGN_ROOT                           durable run root on /nfs/turbo
set -uo pipefail

B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
STAGE="${STAGE:?set STAGE=prep|shared|arm|heldout}"
case "$STAGE" in prep|shared|arm|heldout) ;; *) echo "unknown STAGE=$STAGE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}. A restart must not re-run a stage that spends" >&2
    echo "the authorization. Inspect the shared ledger at \$OQA_LEDGER_DIR and resubmit deliberately." >&2
    exit 6
fi

WORK="/tmp/oqa_campaign_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${STAGE}${ARM:+_$ARM}${EPOCH:+_e$EPOCH}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} stage=$STAGE arm=${ARM:-} epoch=${EPOCH:-} host=$(hostname -s) work=$WORK"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_DOCS_DIR:?settings.env must define OQA_DOCS_DIR}"
: "${OQA_LEDGER_DIR:?settings.env must define OQA_LEDGER_DIR}"

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
log "manifest ok; commit $COMMIT"

tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

# ── the shared authorization ledger, on shared storage so concurrency and restarts cannot reset it ───────────
mkdir -p "$OQA_LEDGER_DIR/claims" "$OQA_LEDGER_DIR/spend" "$OQA_LEDGER_DIR/release" \
    || die "cannot create the ledger at $OQA_LEDGER_DIR"
export SKILLOPT_SPEND_LEDGER_DIR="$OQA_LEDGER_DIR"
# ── THE AUTHORIZATION, AS AMENDED ON 2026-11-17 ─────────────────────────────────────────────────────────────
# The user raised the TOTAL campaign ceilings, "including everything already spent", to:
#
#     EmpirioLabs  $16   and 30,000 HTTP attempts      (was $8  and 15,000)
#     DeepSeek     $8    and    260 HTTP attempts      (was $4  and     130)
#     CPU          40 job-hours                        (was 20)
#     GPUs         zero                                (unchanged)
#
# The caps BELOW ARE SMALLER THAN THOSE NUMBERS, on purpose. The ceiling covers the whole campaign, but this
# ledger only sees what passed through it, and one paid call for this campaign did not: the slow-update size
# probe of job 66271290 spent 1 DeepSeek attempt and $0.0411 before the ledger existed. Subtracting it here
# is what makes the cap the enforcement of the TOTAL rather than of the ledger's own subtotal. There is no
# equivalent subtraction for the actor: the preflight that ran outside the ledger was key-free by
# construction (research/officeqa/oqa_avail3.py) and oqa_ledger_check.py makes no HTTP call at all.
#
#     docs/research/OFFICEQA_AUTHORIZATION.md records the amendment and this arithmetic.
export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_CAP_ACTOR_ATTEMPTS:-30000}"
export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_CAP_ACTOR_USD:-16.00}"
export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER="${OQA_CAP_OPT_CALLS:-259}"
export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER="${OQA_CAP_OPT_USD:-7.95}"
# EmpirioLabs published prices (GET /v1/models/qwen3-5-4b, 2026-11-16): prompt, completion, cached prompt.
export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
# DeepSeek at PEAK prices on purpose: the ledger's dollar figure is then an upper bound on the real charge, so
# the cap binds early rather than late. Off-peak is exactly half.
export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
# CLAIM BLOCK SIZES. A claim is taken before the request is sent, which is what makes `spent <= claimed <= cap`
# hold across concurrent processes and restarts; the price is that a process which dies without releasing
# strands up to one block. The $0.25 blocks used on the first run were 6 per cent of the $4.00 optimizer cap
# EACH, and with no release path at all that is how 45 per cent of the optimizer's authorization became
# unreachable and starved four arm-epochs. Blocks are now $0.05: 0.6 per cent of the amended optimizer cap.
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=50
export SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.05
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_OPTIMIZER=4
export SKILLOPT_LEDGER_BLOCK_USD_OPTIMIZER=0.05
export SKILLOPT_LEDGER_RESERVE_USD_TARGET=0.02
export SKILLOPT_LEDGER_RESERVE_USD_OPTIMIZER=0.15
# A second ceiling on THIS job, so one runaway arm-epoch cannot consume what the other arms still need.
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET="${OQA_JOB_CAP_ACTOR_ATTEMPTS:-2000}"
export SKILLOPT_LEDGER_JOB_MAX_USD_TARGET="${OQA_JOB_CAP_ACTOR_USD:-1.50}"
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER="${OQA_JOB_CAP_OPT_CALLS:-20}"
export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER="${OQA_JOB_CAP_OPT_USD:-0.80}"

# ── the pre-send prompt size check, identical in every arm ───────────────────────────────────────────────────
export SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT="${OQA_PROMPT_TOKEN_LIMIT:-225000}"
export SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG="$OUTDIR/prompt_budget.jsonl"
export SKILLOPT_ARM_LABEL="${ARM:-}"
export SKILLOPT_EPOCH_LABEL="${EPOCH:-}"
export SKILLOPT_ROUTE_GUARD_RECORD="$OUTDIR/route_guard.json"

# ── keys: load only what this stage is allowed to spend, and prove the rest is unreachable ───────────────────
unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY TARGET_OPENAI_COMPATIBLE_API_KEY
unset DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY HF_TOKEN HUGGING_FACE_HUB_TOKEN
ACTOR_KEY=""
OPT_KEY=""

load_actor_key() {
    : "${ACTOR_ENV_FILE:?settings.env must define ACTOR_ENV_FILE}"
    set -a; . "$ACTOR_ENV_FILE"; set +a
    [ -n "${EMPIRIOLABS_API_KEY:-}" ] || die "EMPIRIOLABS_API_KEY not set by $ACTOR_ENV_FILE"
    export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
    ACTOR_KEY="$EMPIRIOLABS_API_KEY"
    unset EMPIRIOLABS_API_KEY
    log "actor key loaded (length ${#ACTOR_KEY})"
}
load_optimizer_key() {
    : "${OQA_OPTIMIZER_ENV_FILE:?settings.env must define OQA_OPTIMIZER_ENV_FILE}"
    set -a; . "$OQA_OPTIMIZER_ENV_FILE"; set +a
    [ -n "${DEEPSEEK_API_KEY:-}" ] || die "DEEPSEEK_API_KEY not set by $OQA_OPTIMIZER_ENV_FILE"
    export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
    OPT_KEY="$DEEPSEEK_API_KEY"
    unset DEEPSEEK_API_KEY
    log "optimizer key loaded (length ${#OPT_KEY})"
}

# What the shared ledger says, as JSON. `python -I` implies -E, so PYTHONPATH is ignored and a
# `-m skillopt.model.spend_ledger` invocation cannot find the extracted tree - which is how the first run of
# this body lost its end-of-job ledger summary (job 66272737: "No module named 'skillopt'"). The tree is
# therefore put on sys.path explicitly. Only this diagnostic was affected: the ledger that enforces the caps is
# imported inside the trainer's own process, and the caps held throughout.
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
CFG="configs/research/officeqa_api3.yaml"
SKILL_INIT="skillopt/envs/officeqa/skills/initial.md"
status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"

case "$STAGE" in
prep)
    # Free: a gated-dataset download plus local analysis. No actor key, no optimizer key, no model request.
    : "${OQA_CSV:?settings.env must define OQA_CSV}"
    : "${OQA_HF_ENV_FILE:?settings.env must define OQA_HF_ENV_FILE}"
    : "${OQA_SPLIT3_DIR:?settings.env must define OQA_SPLIT3_DIR}"
    set -a; . "$OQA_HF_ENV_FILE"; set +a
    [ -n "${HF_TOKEN:-}" ] || die "HF_TOKEN not set by $OQA_HF_ENV_FILE; the dataset is gated"
    HF_KEY="$HF_TOKEN"
    log "HF token loaded (length ${#HF_KEY}); no actor or optimizer key is loaded in this stage"

    log "1/4 fetching the parsed documents rule OQA-SEL-3 needs"
    "$OQA_PY" -I research/officeqa/oqa_fetch_docs.py \
        --frozen docs/research/OFFICEQA_FROZEN_SETS_V3.json \
        --docs-root "$OQA_DOCS_DIR" --out "$OUTDIR/FETCH.json" >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "fetch exit $status"

    log "2/4 verifying the corpus against the frozen record"
    "$OQA_PY" -I research/officeqa/oqa_materialize.py \
        --verify-docs "$OQA_DOCS_DIR" --frozen docs/research/OFFICEQA_FROZEN_SETS_V3.json \
        > "$OUTDIR/VERIFY_DOCS.json" 2>> "$OUTDIR/job.log" || log "WARNING: corpus verification reported gaps"

    log "3/4 materializing the 24/24/60 split"
    mkdir -p "$OQA_SPLIT3_DIR"
    "$OQA_PY" -I research/officeqa/oqa_materialize.py \
        --frozen docs/research/OFFICEQA_FROZEN_SETS_V3.json --csv "$OQA_CSV" --out-dir "$OQA_SPLIT3_DIR" \
        > "$OUTDIR/MATERIALIZE.json" 2>> "$OUTDIR/job.log" || die "materialization failed"
    wc -l "$OQA_SPLIT3_DIR"/*/*.csv >> "$OUTDIR/job.log" 2>&1

    log "4/4 the document-availability check over all 108 questions, UID0019 included"
    "$OQA_PY" -I research/officeqa/oqa_avail3.py \
        --csv "$OQA_CSV" --docs-root "$OQA_DOCS_DIR" \
        --frozen docs/research/OFFICEQA_FROZEN_SETS_V3.json \
        --out "$OUTDIR/AVAILABILITY.json" >> "$OUTDIR/job.log" 2>&1 || log "NOTE: availability reported problems"

    # The caps must be shown to hold ON THIS FILESYSTEM before a paid stage relies on them. The mechanism
    # rests on O_CREAT|O_EXCL atomicity, which is exactly what can differ between a local disk and NFS.
    log "verifying the attempt and spend caps against concurrency, restarts and an unwritable ledger"
    "$OQA_PY" -I research/officeqa/oqa_ledger_check.py \
        --root "$OQA_LEDGER_DIR/_selftest" --out "$OUTDIR/LEDGER_CHECK.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "the shared ledger does not hold on this filesystem; see LEDGER_CHECK.json. No paid stage may run."
    rm -rf "$OQA_LEDGER_DIR/_selftest"
    ledger_report > "$OUTDIR/LEDGER_BEFORE.json" 2>> "$OUTDIR/job.log" \
        || log "WARNING: ledger report failed"
    unset HF_TOKEN
    ;;
shared)
    : "${OQA_SPLIT3_DIR:?}" ; : "${OQA_CAMPAIGN_ROOT:?}"
    load_actor_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    SHARED="$OQA_CAMPAIGN_ROOT/shared"
    mkdir -p "$SHARED"
    log "the shared epoch-1 rollout: 24 training questions under the starting skill, gated on the first three"
    "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$SKILL_INIT" \
        --split train --out_root "$SHARED/epoch1" \
        --cfg-options "env.split_dir=$OQA_SPLIT3_DIR" "env.data_dirs=$OQA_DOCS_DIR" "env.limit=3" \
        >> "$OUTDIR/job.log" 2>&1 || status=$?
    if [ "$status" -ne 0 ]; then
        log "the first three questions failed with status=$status; the remaining 21 are NOT run"
    else
        "$OQA_PY" -I research/officeqa/oqa_rollout_checkpoint.py "$SHARED/epoch1" \
            --expect 3 --max-tool-turns 24 --out "$OUTDIR/checkpoint_first3.json" \
            >> "$OUTDIR/job.log" 2>&1 || status=$?
        if [ "$status" -ne 0 ]; then
            log "CHECKPOINT FAILED on the first three questions; the remaining 21 are NOT run"
        else
            log "checkpoint passed; completing the training rollout (run_batch resumes by id)"
            "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$SKILL_INIT" \
                --split train --out_root "$SHARED/epoch1" \
                --cfg-options "env.split_dir=$OQA_SPLIT3_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
                >> "$OUTDIR/job.log" 2>&1 || status=$?
            "$OQA_PY" -I research/officeqa/oqa_rollout_checkpoint.py "$SHARED/epoch1" \
                --expect 24 --max-tool-turns 24 --out "$OUTDIR/checkpoint_all24.json" \
                >> "$OUTDIR/job.log" 2>&1 || log "WARNING: the all-24 checkpoint flagged problems"
            log "the gate's baseline: the same starting skill on the 24 validation questions"
            "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$SKILL_INIT" \
                --split valid_seen --out_root "$SHARED/val_baseline" \
                --cfg-options "env.split_dir=$OQA_SPLIT3_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
                >> "$OUTDIR/job.log" 2>&1 || log "WARNING: the shared validation baseline failed"
        fi
    fi
    if [ -f "$SHARED/epoch1/results.jsonl" ]; then
        "$OQA_PY" -I research/officeqa/oqa_inventory.py "$SHARED/epoch1" \
            --out "$OUTDIR/trace_inventory.json" >> "$OUTDIR/job.log" 2>&1 || log "note: inventory failed"
        # M0: the ONE contract model both contract arms start from, learned on this shared rollout. Offline,
        # deterministic, free. It records its own evidence items, which is what makes a report on one of them
        # say in its own text that its forecasts are IN-SAMPLE training feedback rather than predictions.
        log "seeding M0 from the shared rollout"
        "$OQA_PY" -I research/officeqa/oqa_lc_arm.py seed \
            --pred "$SHARED/epoch1/predictions" --order "$SHARED/epoch1/results.jsonl" \
            --out "$OQA_CAMPAIGN_ROOT/contracts" >> "$OUTDIR/job.log" 2>&1 || status=$?
        cp "$OQA_CAMPAIGN_ROOT/contracts/SEED.json" "$OUTDIR/SEED.json" 2>/dev/null || true
        [ -f "$OQA_CAMPAIGN_ROOT/contracts/model_M0.json" ] \
            && log "M0 frozen: $(sha256sum "$OQA_CAMPAIGN_ROOT/contracts/model_M0.json" | cut -c1-16)" \
            || log "WARNING: M0 was not written; arms B and C cannot start"
    fi
    ;;
arm)
    ARM="${ARM:?set ARM=A, B or C}"
    EPOCH="${EPOCH:?set EPOCH=1..4}"
    case "$ARM" in A|B|C) ;; *) die "ARM must be A, B or C (got $ARM)" ;; esac
    : "${OQA_SHARED_DIR:?set OQA_SHARED_DIR to the STAGE=shared output}"
    : "${OQA_ARM_OUT:?set OQA_ARM_OUT to the durable out_root for this arm}"
    : "${OQA_SPLIT3_DIR:?}"
    load_actor_key
    load_optimizer_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;$OPT_ROUTE"

    mkdir -p "$OQA_ARM_OUT"
    if [ "$EPOCH" = "1" ]; then
        [ -f "$OQA_SHARED_DIR/epoch1/results.jsonl" ] || die "no shared training rollout"
        [ -f "$OQA_SHARED_DIR/val_baseline/results.jsonl" ] || die "no shared gate baseline"
        # Inject the shared traces. run_batch resumes by id from <dir>/results.jsonl, so the trainer's epoch-1
        # rollout and its gate baseline consume these exact trajectories and generate nothing: the three arms
        # then reflect on byte-identical traces.
        mkdir -p "$OQA_ARM_OUT/steps/step_0001"
        [ -d "$OQA_ARM_OUT/steps/step_0001/rollout" ] \
            || cp -r "$OQA_SHARED_DIR/epoch1" "$OQA_ARM_OUT/steps/step_0001/rollout"
        [ -d "$OQA_ARM_OUT/selection_eval_baseline" ] \
            || cp -r "$OQA_SHARED_DIR/val_baseline" "$OQA_ARM_OUT/selection_eval_baseline"
        { echo "shared_dir=$OQA_SHARED_DIR"
          sha256sum "$OQA_ARM_OUT/steps/step_0001/rollout/results.jsonl" \
                    "$OQA_ARM_OUT/selection_eval_baseline/results.jsonl"
        } > "$OUTDIR/injected_shared.txt" 2>&1
        log "arm $ARM: injected $(wc -l < "$OQA_ARM_OUT/steps/step_0001/rollout/results.jsonl") shared rollouts"
    fi

    if [ "$ARM" = "A" ]; then
        unset OFFICEQA_STAGE_REPORT_DIR OFFICEQA_STAGE_REPORT_CONDITION OFFICEQA_LC_MODEL
        log "arm A: OFFICEQA_STAGE_REPORT_DIR unset, so the analyst prompt is upstream's"
    else
        : "${OFFICEQA_LC_MODEL:?arms B and C need OFFICEQA_LC_MODEL (the frozen contract model for this epoch)}"
        [ -f "$OFFICEQA_LC_MODEL" ] || die "no such contract model: $OFFICEQA_LC_MODEL"
        export OFFICEQA_STAGE_REPORT_DIR="$OQA_ARM_OUT/stage_reports"
        export OFFICEQA_STAGE_REPORT_CONDITION="$([ "$ARM" = "B" ] && echo LC_ACTION || echo LC_STAGE)"
        mkdir -p "$OFFICEQA_STAGE_REPORT_DIR"
        log "arm $ARM: condition $OFFICEQA_STAGE_REPORT_CONDITION under model $(basename "$OFFICEQA_LC_MODEL")"
        sha256sum "$OFFICEQA_LC_MODEL" >> "$OUTDIR/job.log"
    fi

    # Free: base URL, model id and GET /models before any paid call, plus the call budget this epoch implies.
    "$OQA_PY" -I research/officeqa/oqa_optimizer_precheck.py \
        --config "$CFG" --rollout "$OQA_ARM_OUT/steps/step_0001/rollout" \
        --budget "${OQA_CALL_BUDGET:-20}" --steps 1 \
        --out "$OUTDIR/optimizer_precheck.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "optimizer precheck failed; no paid call was made (see optimizer_precheck.json)"

    OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl" \
    TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/actor_calls.jsonl" \
    "$OQA_PY" -I scripts/train.py --config "$CFG" \
        --optimizer_model "${OPTIMIZER_MODEL:-deepseek-flash}" --out_root "$OQA_ARM_OUT" \
        --cfg-options "env.split_dir=$OQA_SPLIT3_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
                      "train.num_epochs=$EPOCH" "evaluation.eval_test=false" \
        >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "trainer exit $status"
    if [ -f "$OUTDIR/optimizer_calls.jsonl" ]; then
        log "optimizer attempts logged: $(wc -l < "$OUTDIR/optimizer_calls.jsonl")"
    fi
    if [ -f "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG" ]; then
        log "prompt-budget decisions: $(wc -l < "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG")"
        cat "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG" >> "$OUTDIR/job.log"
    fi

    # ── END OF EPOCH: evolve this arm's contract model, AFTER the reports of this epoch were built ───────────
    # The order is what keeps a prediction a prediction: rollout -> reports under the model frozen BEFORE this
    # epoch -> analyst -> gate -> END OF EPOCH -> evolve. `evolve` replays the new traces against the model in
    # force first, committing each forecast to a hash chain before the observation is released, and only then
    # admits them as evidence. Offline, deterministic, free. Epochs 1 and 2 both run under M0, so the first
    # evolution is the one that follows epoch 2.
    if [ "$ARM" != "A" ] && [ "$EPOCH" -ge 2 ]; then
        PRED="$OQA_ARM_OUT/steps/step_000$EPOCH/rollout"
        if [ -f "$PRED/results.jsonl" ]; then
            CDIR="$OQA_CAMPAIGN_ROOT/contracts/arm_$ARM"
            mkdir -p "$CDIR"
            log "evolving arm $ARM's contract model from its own epoch-$EPOCH traces"
            "$OQA_PY" -I research/officeqa/oqa_lc_arm.py evolve \
                --prev "$OFFICEQA_LC_MODEL" --pred "$PRED/predictions" --order "$PRED/results.jsonl" \
                --epoch "$EPOCH" --arm "$ARM" --out "$CDIR" --history "$CDIR/history.jsonl" \
                >> "$OUTDIR/job.log" 2>&1 || log "WARNING: the evolution failed; the next epoch has no new model"
            NEXT="$CDIR/model_arm${ARM}_epoch0${EPOCH}.json"
            [ -f "$NEXT" ] && log "next model frozen: $NEXT ($(sha256sum "$NEXT" | cut -c1-16))"
            cp "$CDIR/EVOLVE_arm${ARM}_epoch0${EPOCH}.json" "$OUTDIR/" 2>/dev/null || true
        else
            log "WARNING: no epoch-$EPOCH rollout at $PRED, so nothing was evolved"
        fi
    fi
    ;;
heldout)
    : "${OQA_SKILL:?set OQA_SKILL to the skill file to evaluate}"
    : "${OQA_SPLIT3_DIR:?}" ; : "${OQA_CAMPAIGN_ROOT:?}"
    [ -f "$OQA_SKILL" ] || die "no such skill file: $OQA_SKILL"
    load_actor_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    LABEL="${OQA_LABEL:-$(basename "${OQA_SKILL%.md}")}"
    OUT="$OQA_CAMPAIGN_ROOT/heldout/$LABEL"
    mkdir -p "$OUT" "$OUTDIR/skill_under_test"
    cp "$OQA_SKILL" "$OUTDIR/skill_under_test/$LABEL.md"
    sha256sum "$OQA_SKILL" > "$OUTDIR/skill_under_test/$LABEL.sha256"
    log "held-out evaluation of $LABEL ($(wc -c < "$OQA_SKILL") bytes)"
    "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$OQA_SKILL" \
        --split valid_unseen --out_root "$OUT" \
        --cfg-options "env.split_dir=$OQA_SPLIT3_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
        >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "eval exit $status"
    ;;
esac

# ── what this job actually spent, from the shared ledger ─────────────────────────────────────────────────────
ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || log "WARNING: ledger report failed"
if [ -f "$OUTDIR/LEDGER_AFTER.json" ]; then
    "$OQA_PY" -I -c "
import json,sys
d=json.load(open('$OUTDIR/LEDGER_AFTER.json'))
o=d.get('observed',{}).get('by_role',{})
for role,v in sorted(o.items()):
    print('  ledger %-9s attempts=%-6d usd=%.4f refusals=%d' % (role, v['attempts'], v['usd'], v['refusals']))
print('  ledger verdict:', d.get('verdict'), d.get('problems') or '')
" | tee -a "$OUTDIR/job.log"
fi

# ── never leave a key in an artifact ────────────────────────────────────────────────────────────────────────
for KEY in "$ACTOR_KEY" "$OPT_KEY"; do
    [ -n "$KEY" ] || continue
    if grep -rIl -e "$KEY" "$OUTDIR" 2>/dev/null | grep -q .; then
        log "ERROR: a key appears in a run artifact; removing those files"
        grep -rIl -e "$KEY" "$OUTDIR" 2>/dev/null | xargs -r rm -f
        status=5
    fi
done
rm -rf "$WORK"
log "stage $STAGE finished with exit $status; artifacts in $OUTDIR"
exit "$status"
