#!/bin/bash
# CPU-only measurement of 8 concurrent ALFWorld simulators (no model, no GPU,
# no API). Same 4 CPUs as one spgpu A40 billing unit, so the result tells us
# whether 4 CPUs suffice for the simulators in an 8-episode rollout.
#   research/submit.sh measure_envs
#SBATCH --job-name=lso-measure-envs
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=%x-%j.out

TASKS_FILE="${TASKS_FILE:-research/task_subsets/throughput_train8.txt}"
source research/cluster/lib.sh
load_site
init_run measure_envs
require_clean_snapshot
start_job_log
write_manifest stage=measure_envs "tasks_file=$TASKS_FILE"
write_billing
start_resource_sampler

"$CTL_ENV/bin/python" research/make_task_subset.py --ids-file "$TASKS_FILE" --out "$RUN_DIR/task_split"
status=0
PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" research/measure_alfworld_envs.py \
    --split-dir "$RUN_DIR/task_split" --steps 30 --out "$RUN_DIR/env_measure.json" || status=$?
sleep 6  # one more resource sample after the envs close
"$CTL_ENV/bin/python" research/summarize_run.py "$RUN_DIR/none" --run-dir "$RUN_DIR" \
    --out "$RUN_DIR/run_summary.json" || log "warning: summarize_run failed"
assert_run_dir_clean
exit "$status"
