#!/usr/bin/env bash
# Submit a Slurm job from a deployed snapshot on the cluster.
#
#   research/submit.sh <job> [<commit-ish>] [-- extra sbatch args]
#     <job>  setup_env | setup_vllm_env | check_compute | run_tests | analysis | measure_envs | smoke
#   e.g.  STAGE=vllm research/submit.sh smoke
#         STAGE=rollout ROLLOUT_EPISODES=1 research/submit.sh smoke
#         STAGE=train research/submit.sh smoke HEAD -- --time=02:00:00
#
# The snapshot must already exist (research/deploy.sh). Account and partition
# come from the private site.env (smoke -> GPU_PARTITION, others ->
# CPU_PARTITION). Forwards STAGE, ROLLOUT_EPISODES, TESTS, OPTIMIZER_MODEL
# when set. Appends one line per submission to docs/research/jobs.tsv.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
# shellcheck disable=SC1091
source "$HERE/cluster/site.env"

JOB="${1:?usage: submit.sh <setup_env|setup_vllm_env|check_compute|run_tests|analysis|measure_envs|smoke> [commit] [-- sbatch args]}"; shift
REV="HEAD"
if [ $# -gt 0 ] && [ "$1" != "--" ]; then REV="$1"; shift; fi
[ "${1:-}" = "--" ] && shift
EXTRA=("$@")

SHA=$(git -C "$REPO_ROOT" rev-parse --verify "$REV^{commit}")
SHORT=${SHA:0:12}
SNAP="$SNAPSHOT_ROOT/$SHORT"
SCRIPT="research/cluster/${JOB}.sbatch"
[ -f "$REPO_ROOT/$SCRIPT" ] || { echo "no such job script: $SCRIPT" >&2; exit 1; }
case "$JOB" in smoke) PARTITION="$GPU_PARTITION" ;; *) PARTITION="$CPU_PARTITION" ;; esac

EXPORTS="ALL"
for v in STAGE ROLLOUT_EPISODES ROLLOUT_SPLIT ROLLOUT_CFG_OPTIONS ROLLOUT_TASKS_FILE TASKS_FILE SOURCE_RUN ANALYSIS TRAIN_CONFIG TESTS OPTIMIZER_MODEL; do
    [ -n "${!v:-}" ] && EXPORTS="$EXPORTS,$v=${!v}"
done

OUT=$(ssh "$SSH_HOST" "set -e; test -d '$SNAP' || { echo 'snapshot missing: $SNAP (run research/deploy.sh)'; exit 1; }; \
    mkdir -p '$PROJECT_ROOT/slurm_logs'; cd '$SNAP'; \
    sbatch --parsable --account='$SLURM_ACCOUNT' --partition='$PARTITION' --chdir='$SNAP' --export='$EXPORTS' \
      --output='$PROJECT_ROOT/slurm_logs/%x-%j.out' ${EXTRA[*]:-} '$SCRIPT'")
JOBID="${OUT##*$'\n'}"
echo "submitted job $JOBID ($JOB, stage=${STAGE:--}, partition=$PARTITION) from snapshot $SHORT"

LOG="$REPO_ROOT/docs/research/jobs.tsv"
[ -f "$LOG" ] || printf 'submitted_at\tjob_id\tjob\tstage\tcommit\textra_sbatch\n' > "$LOG"
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$JOBID" "$JOB" "${STAGE:--}" "$SHA" "${EXTRA[*]:-}" >> "$LOG"
