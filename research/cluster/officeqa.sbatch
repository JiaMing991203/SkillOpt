#!/bin/bash
# OfficeQA feasibility study. Separate job name, separate run directory, separate configs; it touches no ALFWorld
# path, no Experiment 3 path and no API-campaign path.
#
#   STAGE=tools    start vLLM with the tool-call flags and send ONE OfficeQA-shaped request with the adapter's own
#                  function schemas, to establish whether the server returns `tool_calls` at all. No OfficeQA data,
#                  no DeepSeek call. Run this before check3: without it the offline tool loop silently degrades to a
#                  single turn with no tool use (see the note on LSO_VLLM_EXTRA_ARGS in research/cluster/lib.sh).
#   STAGE=check3   the three-question baseline check (selection OQA-SEL-1 / B3) through scripts/eval_only.py.
#                  No optimizer, no DeepSeek call.
#   STAGE=pilot12  the twelve-question development pilot (OQA-SEL-1 / P12). Same, with 12 questions.
#   STAGE=epoch1   the SHARED epoch-1 rollout of the two-arm comparison: the frozen 12 questions with the
#                  released starting skill, gated on the first three. It runs the first 3 questions, checks
#                  document loading / tool execution / trace and score recording / answer-key isolation
#                  (oqa_rollout_checkpoint.py), and only then completes the remaining 9 into the SAME output
#                  directory, which run_batch resumes by skipping ids already in results.jsonl. Afterwards it
#                  generates the arm-B stage reports and audits the real optimizer input for both arms. No
#                  optimizer call is made: this stage is rollout plus offline analysis only.
#
# The P / S / C report comparison is NOT run here: it is offline, deterministic and CPU-only, and runs over the
# fetched run directory (research/officeqa/oqa_check.py).
#
# Required environment:
#   OFFICEQA_SPLIT_DIR   materialized split directory (research/officeqa/oqa_materialize.py), with train/val/test
#   OFFICEQA_DOCS_DIR    parsed-corpus root containing transformed/ and jsons/ (may be sparse)
#
# Submit via research/submit.sh, e.g.  STAGE=tools research/submit.sh officeqa
#SBATCH --job-name=oqa-feas
#SBATCH --gpus-per-node=a40:1
# One spgpu billing unit: 1 A40 = 4 CPUs = 48 GB (billing = max of weighted TRES).
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
# 00:30:00 is the ACKed limit for STAGE=tools (OQA-1), and it is the DEFAULT so that forgetting a flag can only
# under-request, never over-request. check3 and pilot12 need an explicit override once they are ACKed, e.g.
#   STAGE=check3 research/submit.sh officeqa <commit> -- --time=01:00:00
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out

STAGE="${STAGE:?set STAGE=tools|check3|pilot12|epoch1}"
case "$STAGE" in tools|check3|pilot12|epoch1) ;; *) echo "unknown STAGE=$STAGE" >&2; exit 2 ;; esac

# Parser choice, corrected by measurement in job 66190733 (the earlier comment here claimed hermes, wrongly).
# Qwen3.5-4B emits Qwen3-Coder XML, not JSON inside <tool_call>:
#   <tool_call><function=grep><parameter=pattern>...</parameter><parameter=path>...</parameter></function></tool_call>
# hermes expects JSON there, so it extracted nothing and vLLM returned the call as plain content with an empty
# tool_calls list. In this build `qwen3_coder` and `qwen3_xml` both resolve to the same class
# (vllm/tool_parsers/__init__.py -> Qwen3EngineToolParser), so they are one parser under two names. An offline
# replay of that job's exact reply through qwen3_coder recovers the grep call with both arguments intact
# (research/officeqa/oqa_parser_replay.py).
OQA_TOOL_PARSER="${OQA_TOOL_PARSER:-qwen3_coder}"
OQA_CONFIG_CHECK3="configs/research/officeqa_check3.yaml"
OQA_CONFIG_PILOT12="configs/research/officeqa_pilot12.yaml"

if [ "$STAGE" != "tools" ]; then
    : "${OFFICEQA_SPLIT_DIR:?set OFFICEQA_SPLIT_DIR to a materialized split directory}"
    : "${OFFICEQA_DOCS_DIR:?set OFFICEQA_DOCS_DIR to the parsed-corpus root (transformed/ + jsons/)}"
    [ -d "$OFFICEQA_SPLIT_DIR/train" ] || { echo "no train/ in $OFFICEQA_SPLIT_DIR" >&2; exit 2; }
    [ -d "$OFFICEQA_DOCS_DIR/transformed" ] || { echo "no transformed/ in $OFFICEQA_DOCS_DIR" >&2; exit 2; }
    [ -d "$OFFICEQA_DOCS_DIR/jsons" ] || { echo "no jsons/ in $OFFICEQA_DOCS_DIR" >&2; exit 2; }
fi

source research/cluster/lib.sh
load_site

# Fail closed on the allocation itself: STAGE=tools is ACKed for at most 30 minutes, and an A40 is charged for the
# whole requested window once the job starts, so a larger window would overspend even if the stage finished early.
if [ -n "${SLURM_JOB_ID:-}" ]; then
    LIMIT_MIN=$(squeue -h -j "$SLURM_JOB_ID" -o "%l" 2>/dev/null | awk -F: '
        NF==3 {split($1,d,"-"); if (length(d)>1) printf "%d", (d[1]*1440)+(d[2]*60)+$2; else printf "%d", ($1*60)+$2}
        NF==2 {printf "%d", $1}')
    echo "[officeqa] requested time limit: ${LIMIT_MIN:-unknown} min"
    if [ "$STAGE" = "tools" ] && [ -n "${LIMIT_MIN:-}" ] && [ "$LIMIT_MIN" -gt 30 ]; then
        echo "REFUSED: STAGE=tools is ACKed for <= 30 min, but this allocation requests ${LIMIT_MIN} min" >&2
        exit 2
    fi
fi
init_run "officeqa_${STAGE}"
require_clean_snapshot
start_job_log
start_resource_sampler

# ── OfficeQA deviations from the frozen ALFWorld serving settings ────────────
# Both are set AFTER load_site, so research/cluster/experiment.env keeps its ALFWorld values in git and every
# deviation is visible here and in the run manifest.
#
# 1. Tool calling. The OfficeQA offline loop sends function tools with tool_choice="auto". vLLM 0.28.0 only converts
#    the model's output into `tool_calls` when the server was started with --enable-auto-tool-choice and a parser;
#    otherwise the tool syntax comes back as plain content, `message.tool_calls` is empty, and the loop ends after
#    one turn with "Model neither produced a tool request nor a final answer".
export LSO_VLLM_EXTRA_ARGS="--enable-auto-tool-choice --tool-call-parser $OQA_TOOL_PARSER"
# 2. Context length. One OfficeQA user message carries up to 80,000 characters of supplied parsed pages (the
#    adapter's _MAX_ORACLE_CONTEXT_CHARS), roughly 20k tokens, and the tool loop appends every observation to the
#    same conversation. 32,768 is the ALFWorld value and is not enough headroom here.
export LSO_VLLM_MAX_MODEL_LEN=65536

export TARGET_QWEN_CHAT_MODEL="$TARGET_MODEL_ID"
export PYTHONPATH="$SNAPSHOT_DIR"
export PYTHONUNBUFFERED=1
# The adapter also honours this; set so a misconfigured env.data_dirs cannot silently fall back to another corpus.
[ "$STAGE" != "tools" ] && export OFFICEQA_DOCS_DIR

nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu \
    --format=csv,noheader,nounits -l 5 > "$RUN_DIR/gpu_usage.csv" 2>/dev/null &
LSO_GPU_MON_PID=$!

manifest_fields=(
    "stage=$STAGE" "tool_parser=$OQA_TOOL_PARSER" "vllm_extra_args=$LSO_VLLM_EXTRA_ARGS"
    "max_model_len=$LSO_VLLM_MAX_MODEL_LEN" "split_dir=${OFFICEQA_SPLIT_DIR:-}" "docs_dir=${OFFICEQA_DOCS_DIR:-}"
    "selection_rule=OQA-SEL-1"
)
write_manifest "${manifest_fields[@]}"
write_billing
freeze_env "$CTL_ENV" ctl
freeze_env "$LSO_VLLM_ENV" vllm

# Record the parser choices this vLLM build actually accepts, so a wrong name is a documented refusal rather than a
# confusing startup failure.
"$LSO_VLLM_ENV/bin/vllm" serve --help=all 2>/dev/null \
    | grep -A 4 -- "--tool-call-parser" > "$RUN_DIR/tool_parser_help.txt" || true
grep -q -- "$OQA_TOOL_PARSER" "$RUN_DIR/tool_parser_help.txt" \
    || log "WARNING: parser '$OQA_TOOL_PARSER' not found in --help=all output; see tool_parser_help.txt"

start_vllm 900
write_manifest "${manifest_fields[@]}"

"$CTL_ENV/bin/python" research/preflight_endpoints.py --target --out "$RUN_DIR/preflight.json" \
    || die "target preflight failed; see $RUN_DIR/preflight.json"

if [ "$LSO_NETWORK_STATUS" != "pass" ]; then
    log "network not cleared (status=$LSO_NETWORK_STATUS); no agent stage will run"
    assert_run_dir_clean
    exit 3
fi

OUT="$RUN_DIR/skillopt"
status=0
scrape_vllm_metrics before
case "$STAGE" in
    tools)
        # A pass requires more than a structured reply: the returned call must execute through the OfficeQA
        # adapter against a small allowed fixture and come back with the expected matching line.
        "$CTL_ENV/bin/python" research/officeqa/oqa_tool_probe.py \
            --out "$RUN_DIR/tool_probe.json" \
            --fixture-dir "$RUN_DIR/tool_fixture" || status=$?
        ;;
    epoch1)
        # One rollout, shared by both arms. Phase 1: the first three questions only.
        OUT="$RUN_DIR/epoch1"
        "$CTL_ENV/bin/python" scripts/eval_only.py \
            --config "$OQA_CONFIG_PILOT12" \
            --skill skillopt/envs/officeqa/skills/initial.md \
            --split train --out_root "$OUT" \
            --cfg-options "env.split_dir=$OFFICEQA_SPLIT_DIR" "env.data_dirs=[$OFFICEQA_DOCS_DIR]" \
                          "env.limit=3" || status=$?
        if [ "$status" -ne 0 ]; then
            log "phase 1 (first 3 questions) failed with status=$status; not continuing to the remaining 9"
        else
            "$CTL_ENV/bin/python" research/officeqa/oqa_rollout_checkpoint.py "$OUT" \
                --expect 3 --out "$RUN_DIR/checkpoint_first3.json" || status=$?
            if [ "$status" -ne 0 ]; then
                log "CHECKPOINT FAILED on the first 3 questions; the remaining 9 are NOT run (see checkpoint_first3.json)"
            else
                log "checkpoint passed; completing the remaining questions (run_batch resumes by id)"
                "$CTL_ENV/bin/python" scripts/eval_only.py \
                    --config "$OQA_CONFIG_PILOT12" \
                    --skill skillopt/envs/officeqa/skills/initial.md \
                    --split train --out_root "$OUT" \
                    --cfg-options "env.split_dir=$OFFICEQA_SPLIT_DIR" "env.data_dirs=[$OFFICEQA_DOCS_DIR]" \
                    || status=$?
                "$CTL_ENV/bin/python" research/officeqa/oqa_rollout_checkpoint.py "$OUT" \
                    --expect 12 --out "$RUN_DIR/checkpoint_all12.json" || log "WARNING: all-12 checkpoint flagged problems"
            fi
        fi
        # Offline, deterministic, no API call: the arm-B reports, then an audit of the ACTUAL optimizer input for
        # both arms (leakage, arm difference, and the prompt sizes the budget is built from).
        if [ -f "$OUT/results.jsonl" ]; then
            "$CTL_ENV/bin/python" research/officeqa/oqa_inventory.py "$OUT" \
                --out "$RUN_DIR/trace_inventory.json" || log "warning: oqa_inventory failed"
            "$CTL_ENV/bin/python" research/officeqa/oqa_reports.py "$OUT" \
                --out "$RUN_DIR/stage_reports_C" --condition C || log "WARNING: report generation flagged problems"
            "$CTL_ENV/bin/python" research/officeqa/oqa_optimizer_input_audit.py "$OUT" \
                --report-dir "$RUN_DIR/stage_reports_C" \
                --out "$RUN_DIR/optimizer_input_audit.json" || log "WARNING: optimizer-input audit did not pass"
            "$CTL_ENV/bin/python" research/render_reflection_input.py "$OUT" \
                --out "$RUN_DIR/reflection_input_preview.txt" || log "warning: render_reflection_input failed"
        fi
        ;;
    check3|pilot12)
        CFG="$OQA_CONFIG_CHECK3"; [ "$STAGE" = "pilot12" ] && CFG="$OQA_CONFIG_PILOT12"
        "$CTL_ENV/bin/python" scripts/eval_only.py \
            --config "$CFG" \
            --skill skillopt/envs/officeqa/skills/initial.md \
            --split train --out_root "$OUT" \
            --cfg-options "env.split_dir=$OFFICEQA_SPLIT_DIR" "env.data_dirs=[$OFFICEQA_DOCS_DIR]" || status=$?
        # What SkillOpt's reflection step would show the optimizer for this rollout (upstream
        # fmt_minibatch_trajectories; no API call). For OfficeQA this is also the measurement of how large an
        # analyst prompt would be, which decides whether an optimizer comparison is affordable at all.
        if [ -f "$OUT/results.jsonl" ]; then
            "$CTL_ENV/bin/python" research/render_reflection_input.py "$OUT" \
                --out "$RUN_DIR/reflection_input_preview.txt" || log "warning: render_reflection_input failed"
            wc -c "$RUN_DIR/reflection_input_preview.txt" >> "$RUN_DIR/job.log" 2>/dev/null || true
            # Deterministic trace inventory: tool calls, returned evidence, stated operands, native score.
            "$CTL_ENV/bin/python" research/officeqa/oqa_inventory.py "$OUT" \
                --out "$RUN_DIR/trace_inventory.json" || log "warning: oqa_inventory failed"
        fi
        ;;
esac
scrape_vllm_metrics after

"$CTL_ENV/bin/python" research/summarize_run.py "$OUT" --run-dir "$RUN_DIR" \
    --out "$RUN_DIR/run_summary.json" 2>/dev/null || log "note: summarize_run not applicable to this stage"

stop_vllm
kill "$LSO_GPU_MON_PID" 2>/dev/null || true
assert_run_dir_clean
log "stage=$STAGE finished with status=$status"
exit "$status"
