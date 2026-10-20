#!/bin/bash
# Baseline smoke tests on one A40. The controller and the vLLM server share
# this single-node allocation, so SkillOpt talks to vLLM over loopback.
#
#   STAGE=vllm     start vLLM, send one ALFWorld-format prompt through the
#                  qwen_chat backend, check for an <action> tag (no DeepSeek)
#   STAGE=rollout  + DeepSeek check, then ROLLOUT_EPISODES (default 1) real
#                  ALFWorld episodes via scripts/eval_only.py on the selection
#                  split, no optimization; measures target throughput
#   STAGE=train    + one small original SkillOpt iteration using
#                  configs/research/alfworld_smoke.yaml (gate included)
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

source research/cluster/lib.sh
load_site
init_run "smoke_${STAGE}"
require_clean_snapshot
exec > >(tee -a "$RUN_DIR/job.log") 2>&1

# experiment.env sets TARGET_QWEN_CHAT_{THINKING_MODE,TEMPERATURE,MAX_TOKENS};
# the qwen_chat backend reads them at import time (and they match the YAML).
export TARGET_QWEN_CHAT_MODEL="$TARGET_MODEL_ID"
export PYTHONPATH="$SNAPSHOT_DIR"
export PYTHONUNBUFFERED=1

if [ "$STAGE" != "vllm" ]; then
    load_secrets
    [ -n "${OPTIMIZER_MODEL:-}" ] || die "OPTIMIZER_MODEL is empty; run check_compute first and pin the DeepSeek model id"
fi

start_vllm 900
write_manifest "stage=$STAGE" "rollout_episodes=$ROLLOUT_EPISODES"
freeze_env "$CTL_ENV" ctl
freeze_env "$VLLM_ENV" vllm

PREFLIGHT=(--target)
[ "$STAGE" != "vllm" ] && PREFLIGHT+=(--optimizer)
"$CTL_ENV/bin/python" research/preflight_endpoints.py "${PREFLIGHT[@]}" --out "$RUN_DIR/preflight.json" \
    || die "preflight failed; see $RUN_DIR/preflight.json"

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
            --split valid_seen --test_env_num "$ROLLOUT_EPISODES" \
            --out_root "$OUT" || status=$?
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
log "stage $STAGE finished with status $status"
exit "$status"
