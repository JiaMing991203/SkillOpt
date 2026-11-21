#!/bin/bash
# THE TWO-BASELINE OfficeQA CAMPAIGN, 2026-11-21. One body, five stages, CPU only (the actor is a hosted API
# and the document tools read local files, so no GPU is involved anywhere).
#
#   sbatch --wrap="bash <bundle>/oqa_base_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,STAGE=<stage>[,EPOCH=1..4,PASS=...,OQA_SKILL=...]
#
#   STAGE=prep      free. Fetch the parsed documents rule OQA-SEL-4 needs, materialize the 12/12/30 split,
#                   run the document-availability check, prove the ledger holds on this filesystem, and
#                   assert the EFFECTIVE outbound actor settings. NO actor key and NO optimizer key are
#                   loaded, so this stage cannot spend a cent.
#   STAGE=s0        actor only. The S0 reference: FOUR independent passes of the 12 training questions under
#                   the unchanged starting skill (48 trajectories, which is Trace2Skill's input), gated on
#                   the first three of pass 1, plus ONE pass of the 12 validation questions, which is the
#                   gate's baseline and also S0's validation reference. THE OPTIMIZER KEY IS NOT LOADED.
#   STAGE=arm       one epoch of ORIGINAL SkillOpt. Needs EPOCH, OQA_S0_DIR and OQA_ARM_OUT. This is the
#                   upstream trainer with OFFICEQA_STAGE_REPORT_DIR unset, so the analyst prompt is
#                   upstream's. The only stage that makes a SkillOpt optimizer call.
#   STAGE=t2s       Trace2Skill: per-trajectory analysis and patch proposal over the 48 saved S0
#                   trajectories, then hierarchical consolidation. Needs OQA_S0_DIR and OQA_T2S_OUT. Makes
#                   optimizer calls; makes NO actor call, because its trajectories already exist.
#   STAGE=heldout   one endpoint on the 30 held-out questions, TWO passes, actor only. OQA_SKILL names the
#                   skill, OQA_LABEL its output directory. The optimizer key is not loaded.
#
# WHY THIS IS A NEW FILE AND NOT AN EDIT OF oqa_campaign_body.sh. That body is the record of what the
# completed three-arm campaign actually executed, down to the 24/24/60 counts written into its log lines.
# Editing it would overwrite that record to serve a different campaign. The enforcement it earned is carried
# over here deliberately and each piece is attributed below.
#
# FIVE THINGS THIS BODY ENFORCES, every one because of a recorded failure.
#
#   1. THE SUBMISSION SHAPE. Invoke via --wrap and work in node-local /tmp. A script submitted directly as
#      the sbatch batch file from /nfs/turbo was killed by NODE_FAIL at 60 s, four times each, on eight
#      different nodes (jobs 66262999, 66263561), before any Python ran; --no-requeue did not stop it.
#
#   2. NO RESPENDING ON A RESTART. A Slurm requeue starts a fresh process. This body refuses to run when
#      SLURM_RESTART_COUNT is not 0 (exit 6), and the authorization lives in a durable shared ledger on
#      /nfs/turbo, so even a restart that slipped through could not reset the counter.
#
#   3. THE ROUTE IS PINNED AND PROVEN IN-PROCESS. skillopt.model.chat_optimizer is a ROUTER whose default is
#      Azure. Job 66271190 had a perfect openai-compatible config, dispatched to Azure, raised, had the
#      exception swallowed, and recorded `no_content` - a result that reads like a DeepSeek failure and is
#      not one. Every paid stage sets SKILLOPT_REQUIRE_ROUTE.
#
#   4. THE BUNDLE IS NEVER REBUILT UNDER A LIVE JOB. bash reads a script body lazily, so rewriting a bundle
#      while a job is queued or running corrupts the running job. The submit script refuses to rebuild.
#
#   5. THE 8,000-TOKEN OUTPUT CAP IS ASSERTED, NOT ASSUMED. Two different config fields look like the output
#      cap and only one is. `model.target_openai_compatible_max_tokens` (8000) is transmitted;
#      `env.max_completion_tokens` (16384) reaches no prompt and no API call and feeds only the provenance
#      fingerprint. STAGE=prep asserts the transmitted value before any paid stage runs.
#
# Reads <bundle>/settings.env, which is PRIVATE and stays on the cluster. Required keys:
#   OQA_PY OQA_DOCS_DIR ACTOR_ENV_FILE OQA_CSV OQA_HF_ENV_FILE OQA_OPTIMIZER_ENV_FILE
#   OQA_SPLIT4_DIR        the 12/12/30 split directory (written by STAGE=prep)
#   OQA_LEDGER_DIR        the SHARED authorization ledger for THIS phase, on /nfs/turbo
#   OQA_BASE_ROOT         durable run root for this campaign, on /nfs/turbo
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
STAGE="${STAGE:?set STAGE}"
case "$STAGE" in prep|s0|arm|t2s|heldout) ;; *) echo "unknown STAGE=$STAGE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}; a requeue would re-spend." >&2; exit 6
fi

WORK="/tmp/oqa_base_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${STAGE}${EPOCH:+_e$EPOCH}${OQA_LABEL:+_$OQA_LABEL}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} stage=$STAGE epoch=${EPOCH:-} host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_DOCS_DIR:?}" ; : "${OQA_LEDGER_DIR:?}" ; : "${OQA_BASE_ROOT:?}"

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

# ── THE AUTHORIZATION FOR THIS PHASE, 2026-11-21 ────────────────────────────────────────────────────────────
# A FRESH allocation, recorded in its own ledger directory. The user's words: "This replaces the earlier
# draft's allowance for this same new phase; do not count it twice. Historical spending and its unresolved
# accounting remain separate." So this ledger starts at zero and the three-arm campaign's ledger is left
# untouched; the two are never summed into one cap.
#
#     Requested CPU job-hours   50 additional
#     GPUs                      zero
#     Actor API                 20,000 attempts AND $15
#     DeepSeek, all roles       400 attempts AND $20
# ── THE LEDGER MUST BE POINTED AT ITS DIRECTORY, OR IT DOES NOTHING ─────────────────────────────────────────
# skillopt/model/spend_ledger.py is "INERT BY DEFAULT. With SKILLOPT_SPEND_LEDGER_DIR unset this module
# does nothing at all." THIS BODY DID NOT SET IT. Every cap below was configured and NONE was enforced
# for the whole campaign: the ledger directory stayed empty, 0 claims and 0 spend records, and the prep
# stage's own LEDGER_BEFORE.json said {"active": false} - which I read past. Measured afterwards from
# the per-job call logs, nothing overran (actor 1,801 calls / $0.89 of $15; optimizer 148 / $1.28 of
# $20), so the omission cost nothing. It removed the protection all the same.
mkdir -p "$OQA_LEDGER_DIR/claims" "$OQA_LEDGER_DIR/spend" "$OQA_LEDGER_DIR/release" \
    || die "cannot create the ledger at $OQA_LEDGER_DIR"
export SKILLOPT_SPEND_LEDGER_DIR="$OQA_LEDGER_DIR"

export SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET="${OQA_CAP_ACTOR_ATTEMPTS:-20000}"
export SKILLOPT_LEDGER_MAX_USD_TARGET="${OQA_CAP_ACTOR_USD:-15.00}"
export SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER="${OQA_CAP_OPT_CALLS:-400}"
export SKILLOPT_LEDGER_MAX_USD_OPTIMIZER="${OQA_CAP_OPT_USD:-20.00}"
# EmpirioLabs published prices (GET /v1/models/qwen3-5-4b, 2026-11-16): prompt, completion, cached prompt.
export SKILLOPT_LEDGER_PRICE_TARGET="0.04,0.07,0.02"
# DeepSeek at PEAK prices on purpose: the ledger's dollar figure is then an upper bound on the real charge,
# so the cap binds early rather than late. Off-peak is exactly half.
export SKILLOPT_LEDGER_PRICE_OPTIMIZER="0.30,1.20,0.006"
# CLAIM BLOCK SIZES. A claim is taken before the request is sent, which is what makes `spent <= claimed <=
# cap` hold across concurrent processes and restarts; the price is that a process which dies without
# releasing strands up to one block. The $0.25 blocks of the first campaign were 6 per cent of a $4.00 cap
# EACH, and with no release path that is how 45 per cent of an authorization became unreachable and starved
# four arm-epochs. Blocks stay small relative to the cap.
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET=50
export SKILLOPT_LEDGER_BLOCK_USD_TARGET=0.05
export SKILLOPT_LEDGER_BLOCK_ATTEMPTS_OPTIMIZER=4
export SKILLOPT_LEDGER_BLOCK_USD_OPTIMIZER=0.10
export SKILLOPT_LEDGER_RESERVE_USD_TARGET=0.02
export SKILLOPT_LEDGER_RESERVE_USD_OPTIMIZER=0.15
# A second ceiling on THIS job, so one runaway stage cannot consume what the rest of the campaign needs.
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET="${OQA_JOB_CAP_ACTOR_ATTEMPTS:-3000}"
export SKILLOPT_LEDGER_JOB_MAX_USD_TARGET="${OQA_JOB_CAP_ACTOR_USD:-2.50}"
export SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_OPTIMIZER="${OQA_JOB_CAP_OPT_CALLS:-60}"
export SKILLOPT_LEDGER_JOB_MAX_USD_OPTIMIZER="${OQA_JOB_CAP_OPT_USD:-4.00}"

# ── concurrency, by instruction: at most four actor requests and two DeepSeek requests in flight ────────────
export SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT="${OQA_PROMPT_TOKEN_LIMIT:-225000}"
export SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG="$OUTDIR/prompt_budget.jsonl"
export SKILLOPT_ROUTE_GUARD_RECORD="$OUTDIR/route_guard.json"
export SKILLOPT_EPOCH_LABEL="${EPOCH:-}"

# ── the corrected actor client, pinned rather than defaulted ────────────────────────────────────────────────
# The narrow recovery policy and the explicit thinking-off switch are the two settings the answer-recovery
# work established. Both are set here so they appear in the job record instead of relying on a default.
export SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=committed_answer
export TARGET_OPENAI_COMPATIBLE_EXTRA_BODY='{"enable_thinking": false}'
export TARGET_OPENAI_COMPATIBLE_MAX_TOKENS=8000
export TARGET_OPENAI_COMPATIBLE_TEMPERATURE=0.7

# ── keys: load only what this stage is allowed to spend ─────────────────────────────────────────────────────
unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY TARGET_OPENAI_COMPATIBLE_API_KEY
unset DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY HF_TOKEN HUGGING_FACE_HUB_TOKEN
ACTOR_KEY=""
OPT_KEY=""
# A PAID STAGE MUST NOT START WITH AN INERT LEDGER. Checked in-process, because the env var alone
# does not prove the module accepted it - an unwritable directory or a missing cap would also leave it
# inert, and the point of the check is that the caps are live before a key is loaded.
require_live_ledger() {
    "$OQA_PY" -I -c "
import json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
from skillopt.model import spend_ledger as L
snap = L.report()
print(json.dumps({'active': snap.get('active'), 'dir': snap.get('dir')}))
sys.exit(0 if snap.get('active') else 9)
" > "$OUTDIR/ledger_live.json" 2>&1         || die "the spend ledger is NOT ACTIVE, so the caps are not enforced. Refusing to load a key. See ledger_live.json"
    log "ledger live: $(cat "$OUTDIR/ledger_live.json" | tr -d '\n')"
}

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
status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"
export OQA_TREE="$WORK"
log "config $CFG ; frozen sets $FROZEN"

case "$STAGE" in
prep)
    : "${OQA_CSV:?settings.env must define OQA_CSV}"
    : "${OQA_HF_ENV_FILE:?settings.env must define OQA_HF_ENV_FILE}"
    : "${OQA_SPLIT4_DIR:?settings.env must define OQA_SPLIT4_DIR}"
    set -a; . "$OQA_HF_ENV_FILE"; set +a
    [ -n "${HF_TOKEN:-}" ] || die "HF_TOKEN not set by $OQA_HF_ENV_FILE; the dataset is gated"
    HF_KEY="$HF_TOKEN"
    log "HF token loaded (length ${#HF_KEY}); no actor or optimizer key is loaded in this stage"

    log "1/7 fetching the parsed documents rule OQA-SEL-4 needs"
    "$OQA_PY" -I research/officeqa/oqa_fetch_docs.py --frozen "$FROZEN" \
        --docs-root "$OQA_DOCS_DIR" --out "$OUTDIR/FETCH.json" >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "fetch exit $status"

    log "2/7 verifying the corpus against the frozen record"
    "$OQA_PY" -I research/officeqa/oqa_materialize.py --verify-docs "$OQA_DOCS_DIR" --frozen "$FROZEN" \
        > "$OUTDIR/VERIFY_DOCS.json" 2>> "$OUTDIR/job.log" || log "WARNING: corpus verification reported gaps"

    log "3/7 materializing the 12/12/30 split"
    mkdir -p "$OQA_SPLIT4_DIR"
    "$OQA_PY" -I research/officeqa/oqa_materialize.py \
        --frozen "$FROZEN" --csv "$OQA_CSV" --out-dir "$OQA_SPLIT4_DIR" \
        > "$OUTDIR/MATERIALIZE.json" 2>> "$OUTDIR/job.log" || die "materialization failed"
    wc -l "$OQA_SPLIT4_DIR"/*/*.csv >> "$OUTDIR/job.log" 2>&1

    log "4/7 the document-availability check over the 54 selected questions"
    "$OQA_PY" -I research/officeqa/oqa_avail3.py --csv "$OQA_CSV" --docs-root "$OQA_DOCS_DIR" \
        --frozen "$FROZEN" --out "$OUTDIR/AVAILABILITY.json" >> "$OUTDIR/job.log" 2>&1 \
        || log "NOTE: availability reported problems"

    # The caps must be shown to hold ON THIS FILESYSTEM before a paid stage relies on them. The mechanism
    # rests on O_CREAT|O_EXCL atomicity, which is exactly what can differ between a local disk and NFS.
    log "5/7 verifying the attempt and spend caps against concurrency, restarts and an unwritable ledger"
    "$OQA_PY" -I research/officeqa/oqa_ledger_check.py \
        --root "$OQA_LEDGER_DIR/_selftest" --out "$OUTDIR/LEDGER_CHECK.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "the shared ledger does not hold on this filesystem; see LEDGER_CHECK.json."
    rm -rf "$OQA_LEDGER_DIR/_selftest"
    ledger_report > "$OUTDIR/LEDGER_BEFORE.json" 2>> "$OUTDIR/job.log" || log "WARNING: ledger report failed"

    log "6/7 the test suite. Free, and it runs BEFORE any paid stage: a campaign deployed from a tree whose
tests fail is a campaign whose results nobody can interpret."
    "$OQA_PY" -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -4
    PYSTATUS=${PIPESTATUS[0]}
    [ "$PYSTATUS" = "0" ] || { log "tests exited $PYSTATUS; see pytest.txt"; status=$PYSTATUS; }

    log "7/7 the preflight: split integrity, resolved configuration and the EFFECTIVE outbound settings"
    "$OQA_PY" -I research/officeqa/oqa_base_preflight.py --config "$CFG" --frozen "$FROZEN" \
        --split-dir "$OQA_SPLIT4_DIR" --docs-dir "$OQA_DOCS_DIR" \
        --prior-collection "${OQA_PRIOR_COLLECTION:-}" \
        --out "$OUTDIR/PREFLIGHT.json" >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "preflight exit $status"
    unset HF_TOKEN
    ;;
s0)
    : "${OQA_SPLIT4_DIR:?}"
    require_live_ledger
    load_actor_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    S0="$OQA_BASE_ROOT/s0"
    mkdir -p "$S0"
    log "the S0 reference: four passes of the 12 training questions, gated on the first three of pass 1"

    # EVERY outbound actor request of this stage is logged, so "the intended settings were transmitted" is a
    # measurement over real requests and not an inference from the configuration. oqa_collect.py --verify
    # reads this log; the step after the passes asserts against it.
    export TARGET_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/actor_calls.jsonl"

    # PASS 1, GATED. Three questions first: if the actor path is broken, this costs three rollouts, not 48.
    "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$SKILL_INIT" \
        --split train --out_root "$S0/train_p1" \
        --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" "env.limit=3" \
        >> "$OUTDIR/job.log" 2>&1 || status=$?
    if [ "$status" -ne 0 ]; then
        die "the first three questions failed with status=$status; the remaining 45 are NOT run"
    fi
    "$OQA_PY" -I research/officeqa/oqa_rollout_checkpoint.py "$S0/train_p1" \
        --expect 3 --max-tool-turns 24 --out "$OUTDIR/checkpoint_first3.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "CHECKPOINT FAILED on the first three questions; the remaining 45 are NOT run"
    log "checkpoint passed; completing the four passes (run_batch resumes by id within each pass)"

    # Four INDEPENDENT passes. One directory per pass, which is the convention oqa_s0repeat.py reads: the
    # same 12 items at temperature 0.7, resumed by id inside a pass and never across passes.
    for p in 1 2 3 4; do
        "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$SKILL_INIT" \
            --split train --out_root "$S0/train_p$p" \
            --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
            >> "$OUTDIR/job.log" 2>&1 || log "WARNING: training pass $p exited $?"
        n=$( [ -f "$S0/train_p$p/results.jsonl" ] && wc -l < "$S0/train_p$p/results.jsonl" || echo 0 )
        log "pass $p: $n of 12 training episodes"
    done

    log "the gate's baseline and S0's validation reference: S0 on the 12 validation questions"
    "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$SKILL_INIT" \
        --split valid_seen --out_root "$S0/val_baseline" \
        --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
        >> "$OUTDIR/job.log" 2>&1 || log "WARNING: the validation baseline exited $?"

    # THE SETTINGS ASSERTION, against the transmitted requests rather than the config. The gate for the whole
    # campaign: if the cap, the temperature, the model or the thinking switch is not what was frozen, the
    # later stages are measuring something else and must not run.
    log "asserting the EFFECTIVE transmitted settings against $OUTDIR/actor_calls.jsonl"
    "$OQA_PY" -I research/officeqa/oqa_base_verify.py --call-log "$OUTDIR/actor_calls.jsonl" \
        --out "$OUTDIR/OUTBOUND_VERIFY.json" >> "$OUTDIR/job.log" 2>&1 \
        || die "the transmitted actor settings are NOT the frozen ones; see OUTBOUND_VERIFY.json"

    "$OQA_PY" -I research/officeqa/oqa_s0repeat.py \
        --pass "p1=$S0/train_p1" --pass "p2=$S0/train_p2" \
        --pass "p3=$S0/train_p3" --pass "p4=$S0/train_p4" \
        --out "$OUTDIR/S0_PASSES" >> "$OUTDIR/job.log" 2>&1 || log "note: pass summary failed"
    "$OQA_PY" -I research/officeqa/oqa_inventory.py "$S0/train_p1" \
        --out "$OUTDIR/trace_inventory.json" >> "$OUTDIR/job.log" 2>&1 || log "note: inventory failed"
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    ;;
arm)
    EPOCH="${EPOCH:?set EPOCH=1..4}"
    : "${OQA_S0_DIR:?set OQA_S0_DIR to the STAGE=s0 output}"
    : "${OQA_ARM_OUT:?set OQA_ARM_OUT to the durable out_root for Original SkillOpt}"
    : "${OQA_SPLIT4_DIR:?}"
    require_live_ledger
    load_actor_key
    load_optimizer_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;$OPT_ROUTE"
    mkdir -p "$OQA_ARM_OUT"

    # ORIGINAL SkillOpt. Everything upstream reads is left exactly as upstream wrote it.
    unset OFFICEQA_STAGE_REPORT_DIR OFFICEQA_STAGE_REPORT_CONDITION OFFICEQA_LC_MODEL
    log "Original SkillOpt framework baseline: OFFICEQA_STAGE_REPORT_DIR unset, analyst prompt is upstream's"

    if [ "$EPOCH" = "1" ]; then
        [ -f "$OQA_S0_DIR/train_p1/results.jsonl" ] || die "no S0 training rollout to inject"
        [ -f "$OQA_S0_DIR/val_baseline/results.jsonl" ] || die "no S0 gate baseline to inject"
        # Inject pass 1 and the validation baseline. run_batch resumes by id from <dir>/results.jsonl, so the
        # trainer's epoch-1 rollout and its gate baseline consume these exact trajectories and generate
        # nothing new. This is not a shortcut: it is what makes Original SkillOpt and Trace2Skill reason
        # about the SAME S0 evidence instead of two independent samples at temperature 0.7.
        mkdir -p "$OQA_ARM_OUT/steps/step_0001"
        [ -d "$OQA_ARM_OUT/steps/step_0001/rollout" ] \
            || cp -r "$OQA_S0_DIR/train_p1" "$OQA_ARM_OUT/steps/step_0001/rollout"
        [ -d "$OQA_ARM_OUT/selection_eval_baseline" ] \
            || cp -r "$OQA_S0_DIR/val_baseline" "$OQA_ARM_OUT/selection_eval_baseline"
        { echo "s0_dir=$OQA_S0_DIR"
          sha256sum "$OQA_ARM_OUT/steps/step_0001/rollout/results.jsonl" \
                    "$OQA_ARM_OUT/selection_eval_baseline/results.jsonl"
        } > "$OUTDIR/injected_s0.txt" 2>&1
        log "injected $(wc -l < "$OQA_ARM_OUT/steps/step_0001/rollout/results.jsonl") S0 training rollouts"
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
        --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
                      "train.num_epochs=$EPOCH" "evaluation.eval_test=false" \
        >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "trainer exit $status"
    [ -f "$OUTDIR/optimizer_calls.jsonl" ] \
        && log "optimizer attempts logged: $(wc -l < "$OUTDIR/optimizer_calls.jsonl")"
    if [ -f "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG" ]; then
        log "prompt-budget decisions: $(wc -l < "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG")"
        cat "$SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG" >> "$OUTDIR/job.log"
    fi
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    ;;
t2s)
    : "${OQA_S0_DIR:?set OQA_S0_DIR to the STAGE=s0 output}"
    : "${OQA_T2S_OUT:?set OQA_T2S_OUT to the durable out_root for Trace2Skill}"
    require_live_ledger
    load_optimizer_key
    export SKILLOPT_REQUIRE_ROUTE="target=none;$OPT_ROUTE"
    mkdir -p "$OQA_T2S_OUT"
    # NO ACTOR KEY. Trace2Skill does not roll out: its 48 trajectories were collected by STAGE=s0 and this
    # stage only analyses and consolidates them. Leaving the key unloaded proves it cannot roll out.
    log "Trace2Skill over the 48 saved S0 trajectories; the actor key is NOT loaded"

    # A FREE SELF-CHECK BEFORE ANY PAID CALL, which this stage earned. Job 66552508 aborted on the
    # boundary guard after two seconds - correctly, and at no cost, but only because the guard happened
    # to fire on the first call. `--dry-run` builds every prompt and runs every guard WITHOUT sending
    # anything, so a boundary or plumbing problem is found over all 48 trajectories rather than over
    # whichever one happens to be analysed first.
    "$OQA_PY" -I research/officeqa/oqa_t2s.py run \
        --config "$CFG" --s0-dir "$OQA_S0_DIR" --skill "$SKILL_INIT" \
        --split-dir "$OQA_SPLIT4_DIR" --out "$OQA_T2S_OUT/_dryrun" --dry-run \
        --optimizer-max-tokens "${OQA_T2S_OPT_MAX_TOKENS:-32768}" \
        >> "$OUTDIR/t2s_dryrun.log" 2>&1
    DRY=$?
    log "dry run exit $DRY (it makes no request; a non-zero exit here is a plumbing or boundary problem)"
    grep -E 'BOUNDARY|Traceback|Error' "$OUTDIR/t2s_dryrun.log" | head -5 >> "$OUTDIR/job.log" 2>/dev/null
    if grep -q 'BOUNDARY VIOLATION' "$OUTDIR/t2s_dryrun.log" 2>/dev/null; then
        die "the dry run hit the boundary guard; NOTHING was sent. See t2s_dryrun.log"
    fi

    OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG="$OUTDIR/optimizer_calls.jsonl" \
    "$OQA_PY" -I research/officeqa/oqa_t2s.py run \
        --config "$CFG" --s0-dir "$OQA_S0_DIR" --skill "$SKILL_INIT" \
        --split-dir "$OQA_SPLIT4_DIR" \
        --optimizer-model "${OPTIMIZER_MODEL:-deepseek-flash}" \
        --optimizer-max-tokens "${OQA_T2S_OPT_MAX_TOKENS:-32768}" \
        --out "$OQA_T2S_OUT" >> "$OUTDIR/job.log" 2>&1 || status=$?
    log "Trace2Skill exit $status"
    [ -f "$OUTDIR/optimizer_calls.jsonl" ] \
        && log "optimizer attempts logged: $(wc -l < "$OUTDIR/optimizer_calls.jsonl")"
    cp "$OQA_T2S_OUT/T2S_REPORT.json" "$OUTDIR/" 2>/dev/null || true
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    ;;
heldout)
    : "${OQA_SKILL:?set OQA_SKILL to the skill file to evaluate}"
    : "${OQA_SPLIT4_DIR:?}"
    [ -f "$OQA_SKILL" ] || die "no such skill file: $OQA_SKILL"
    require_live_ledger
    load_actor_key
    export SKILLOPT_REQUIRE_ROUTE="$ACTOR_ROUTE;optimizer=none"
    LABEL="${OQA_LABEL:-$(basename "${OQA_SKILL%.md}")}"
    OUT="$OQA_BASE_ROOT/heldout/$LABEL"
    mkdir -p "$OUT" "$OUTDIR/skill_under_test"
    cp "$OQA_SKILL" "$OUTDIR/skill_under_test/$LABEL.md"
    sha256sum "$OQA_SKILL" > "$OUTDIR/skill_under_test/$LABEL.sha256"
    log "held-out evaluation of $LABEL ($(wc -c < "$OQA_SKILL") bytes), TWO passes of 30 questions"
    for p in 1 2; do
        "$OQA_PY" -I scripts/eval_only.py --config "$CFG" --skill "$OQA_SKILL" \
            --split valid_unseen --out_root "$OUT/p$p" \
            --cfg-options "env.split_dir=$OQA_SPLIT4_DIR" "env.data_dirs=$OQA_DOCS_DIR" \
            >> "$OUTDIR/job.log" 2>&1 || log "WARNING: held-out pass $p exited $?"
        n=$( [ -f "$OUT/p$p/results.jsonl" ] && wc -l < "$OUT/p$p/results.jsonl" || echo 0 )
        log "pass $p: $n of 30 held-out episodes"
    done
    # PROVENANCE, so an identical evaluation is never paid for twice: the digest alone is not enough, since
    # the same skill scored under a different split, config or turn budget is a different measurement.
    "$OQA_PY" -I -c "
import hashlib, json, os, sys
sys.path.insert(0, os.environ['OQA_TREE'])
skill = open('$OQA_SKILL','rb').read(); cfg = open('$CFG','rb').read()
json.dump({'schema':'lso.heldout_provenance.v2','label':'$LABEL','skill_path':'$OQA_SKILL',
           'skill_sha256':hashlib.sha256(skill).hexdigest(),'skill_bytes':len(skill),
           'split':'valid_unseen','n_questions':30,'passes':2,'config':'$CFG',
           'config_sha256':hashlib.sha256(cfg).hexdigest(),'frozen_sets':'$FROZEN',
           'commit':os.environ.get('OQA_COMMIT'),'rule':'OQA-SEL-4'},
          open('$OUT/PROVENANCE.json','w'), indent=1, sort_keys=True)
" >> "$OUTDIR/job.log" 2>&1 || log "note: provenance record failed"
    cp "$OUT/PROVENANCE.json" "$OUTDIR/" 2>/dev/null || true
    ledger_report > "$OUTDIR/LEDGER_AFTER.json" 2>> "$OUTDIR/job.log" || true
    ;;
esac

rm -rf "$WORK"
log "stage $STAGE finished with exit $status; artifacts in $OUTDIR"
exit "$status"
