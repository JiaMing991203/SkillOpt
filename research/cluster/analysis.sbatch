#!/bin/bash
# Offline CPU analysis of an existing run directory (no model, no API calls).
#   SOURCE_RUN=<run dir name under RUN_ROOT> research/submit.sh analysis
# Currently runs research/demo_reasoning_parser.py on SOURCE_RUN/skillopt;
# outputs go to this job's own run dir (the source run is never modified).
#SBATCH --job-name=lso-analysis
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out

SOURCE_RUN="${SOURCE_RUN:?set SOURCE_RUN to a run directory name}"
source research/cluster/lib.sh
load_site
init_run analysis
require_clean_snapshot
start_job_log
write_manifest stage=analysis "source_run=$SOURCE_RUN"
SRC="$RUN_ROOT/$SOURCE_RUN/skillopt"
[ -f "$SRC/results.jsonl" ] || die "no results.jsonl under \$RUN_ROOT/$SOURCE_RUN/skillopt"

status=0
PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" research/demo_reasoning_parser.py "$SRC" --out-dir "$RUN_DIR" || status=$?
assert_run_dir_clean
exit "$status"
