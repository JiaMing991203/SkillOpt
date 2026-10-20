#!/bin/bash
# Focused CPU test run from a deployed snapshot. No model or API calls.
#   research/submit.sh run_tests                        (default test set)
#   TESTS="tests/test_gate.py" research/submit.sh run_tests
#SBATCH --job-name=lso-tests
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run run_tests
require_clean_snapshot
exec > >(tee -a "$RUN_DIR/job.log") 2>&1
write_manifest stage=run_tests

TESTS="${TESTS:-tests/test_research_configs.py tests/test_research_summarize_run.py tests/test_gate.py tests/test_gate_no_regression.py tests/test_alfworld_paths.py tests/test_openai_compatible_backend.py tests/test_openai_compatible_config.py tests/test_qwen_backend.py}"
# shellcheck disable=SC2086
status=0
PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" -m pytest -q $TESTS --junitxml="$RUN_DIR/pytest.xml" || status=$?
assert_run_dir_clean
exit "$status"
