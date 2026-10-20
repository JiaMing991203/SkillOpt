#!/bin/bash
# Baseline smoke tests on one A40. The controller and the vLLM server share
# this single-node allocation, so SkillOpt talks to vLLM over loopback.
#
#   STAGE=vllm     start vLLM, send one ALFWorld-format prompt through the
#                  qwen_chat backend, check for an <action> tag (no DeepSeek)
#   STAGE=rollout  ROLLOUT_EPISODES (default 1) real ALFWorld episodes via
#                  scripts/eval_only.py on the selection split; no optimizer,
#                  no DeepSeek calls; measures target throughput
#   STAGE=train    + DeepSeek preflight, then one small original SkillOpt
#                  iteration using configs/research/alfworld_smoke.yaml
#
# Every stage samples GPU memory/utilization every 5 s into gpu_usage.csv.
#
# Submit via research/submit.sh, e.g.  STAGE=vllm research/submit.sh smoke
# (the partition comes from GPU_PARTITION in the private site.env).
#SBATCH --job-name=lso-smoke
#SBATCH --gpus-per-node=a40:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out

STAGE="${STAGE:?set STAGE=vllm|rollout|train}"
ROLLOUT_EPISODES="${ROLLOUT_EPISODES:-1}"
ROLLOUT_SPLIT="${ROLLOUT_SPLIT:-valid_seen}"          # train | valid_seen (never valid_unseen here)
ROLLOUT_CFG_OPTIONS="${ROLLOUT_CFG_OPTIONS:-}"        # e.g. "env.trace_log=true"
case "$ROLLOUT_SPLIT" in train|valid_seen) ;; *) echo "ROLLOUT_SPLIT must be train or valid_seen" >&2; exit 2 ;; esac

source research/cluster/lib.sh
load_site
init_run "smoke_${STAGE}"
require_clean_snapshot
start_job_log

# experiment.env sets TARGET_QWEN_CHAT_{THINKING_MODE,TEMPERATURE,MAX_TOKENS};
# the qwen_chat backend reads them at import time (and they match the YAML).
export TARGET_QWEN_CHAT_MODEL="$TARGET_MODEL_ID"
export PYTHONPATH="$SNAPSHOT_DIR"
export PYTHONUNBUFFERED=1

# Only the training stage talks to the optimizer.
if [ "$STAGE" = "train" ]; then
    load_secrets
    [ -n "${OPTIMIZER_MODEL:-}" ] || die "OPTIMIZER_MODEL is empty; run check_compute first and pin the DeepSeek model id"
fi

nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu \
    --format=csv,noheader,nounits -l 5 > "$RUN_DIR/gpu_usage.csv" 2>/dev/null &
LSO_GPU_MON_PID=$!
# Manifest and env freezes first, so a failed vLLM start still leaves them
# (job 64329002 lost both); rewritten after start to add the vLLM command.
write_manifest "stage=$STAGE" "rollout_episodes=$ROLLOUT_EPISODES" "rollout_split=$ROLLOUT_SPLIT" "rollout_cfg_options=$ROLLOUT_CFG_OPTIONS"
freeze_env "$CTL_ENV" ctl
freeze_env "$LSO_VLLM_ENV" vllm
start_vllm 900
write_manifest "stage=$STAGE" "rollout_episodes=$ROLLOUT_EPISODES" "rollout_split=$ROLLOUT_SPLIT" "rollout_cfg_options=$ROLLOUT_CFG_OPTIONS"

PREFLIGHT=(--target)
[ "$STAGE" = "train" ] && PREFLIGHT+=(--optimizer)
"$CTL_ENV/bin/python" research/preflight_endpoints.py "${PREFLIGHT[@]}" --out "$RUN_DIR/preflight.json" \
    || die "preflight failed; see $RUN_DIR/preflight.json"

# STAGE=vllm keeps the server up briefly so its sockets can be probed from a
# login node (research/probe_listeners.sh). Agent stages run only when every
# vLLM listener is loopback (LSO_NETWORK_STATUS=pass); "unresolved" (the
# torch rendezvous TCPStore on a wildcard address) is not auto-allowed.
[ "$STAGE" = "vllm" ] && open_probe_window
if [ "$LSO_NETWORK_STATUS" != "pass" ]; then
    log "network not cleared (status=$LSO_NETWORK_STATUS); no agent stage will run"
    assert_run_dir_clean
    exit 3
fi

OUT="$RUN_DIR/skillopt"
status=0
scrape_vllm_metrics before
case "$STAGE" in
    vllm)
        ;;
    rollout)
        "$CTL_ENV/bin/python" scripts/eval_only.py \
            --config configs/research/alfworld_qwen35_deepseek.yaml \
            --skill skillopt/envs/alfworld/skills/initial.md \
            --split "$ROLLOUT_SPLIT" --test_env_num "$ROLLOUT_EPISODES" \
            --out_root "$OUT" ${ROLLOUT_CFG_OPTIONS:+--cfg-options $ROLLOUT_CFG_OPTIONS} || status=$?
        ;;
    train)
        "$CTL_ENV/bin/python" scripts/train.py \
            --config configs/research/alfworld_smoke.yaml \
            --optimizer_model "$OPTIMIZER_MODEL" \
            --out_root "$OUT" || status=$?
        ;;
    *) die "unknown STAGE=$STAGE" ;;
esac
scrape_vllm_metrics after

"$CTL_ENV/bin/python" research/summarize_run.py "$OUT" --run-dir "$RUN_DIR" \
    --out "$RUN_DIR/run_summary.json" || log "warning: summarize_run failed"
# What SkillOpt's reflection step would show the optimizer for this rollout
# (upstream fmt_minibatch_trajectories; no API call).
if [ "$STAGE" = "rollout" ] && [ -f "$OUT/results.jsonl" ]; then
    "$CTL_ENV/bin/python" research/render_reflection_input.py "$OUT" \
        --out "$RUN_DIR/reflection_input_preview.txt" || log "warning: render_reflection_input failed"
fi
assert_run_dir_clean
log "stage $STAGE finished with status $status"
exit "$status"
