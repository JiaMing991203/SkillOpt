#!/bin/bash
# Offline CPU analysis of an existing run directory (no model, no API calls).
#   SOURCE_RUN=<run dir name under RUN_ROOT> ANALYSIS=<name> research/submit.sh analysis
#     ANALYSIS=reasoning_demo   research/demo_reasoning_parser.py (default)
#     ANALYSIS=rollout          research/analyze_rollout.py (needs trace.json)
# Outputs go to this job's own run dir; the source run is never modified.
#SBATCH --job-name=lso-analysis
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out

SOURCE_RUN="${SOURCE_RUN:?set SOURCE_RUN to a run directory name}"
ANALYSIS="${ANALYSIS:-reasoning_demo}"
source research/cluster/lib.sh
load_site
init_run "analysis_${ANALYSIS}"
require_clean_snapshot
start_job_log
write_manifest stage=analysis "source_run=$SOURCE_RUN" "analysis=$ANALYSIS"
SRC="$RUN_ROOT/$SOURCE_RUN/skillopt"
[ -f "$SRC/results.jsonl" ] || die "no results.jsonl under \$RUN_ROOT/$SOURCE_RUN/skillopt"

status=0
case "$ANALYSIS" in
    reasoning_demo)
        PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" research/demo_reasoning_parser.py "$SRC" --out-dir "$RUN_DIR" || status=$? ;;
    rollout)
        PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" research/analyze_rollout.py "$SRC" \
            --out "$RUN_DIR/rollout_analysis.json" || status=$? ;;
    *) die "unknown ANALYSIS=$ANALYSIS" ;;
esac
assert_run_dir_clean
exit "$status"
