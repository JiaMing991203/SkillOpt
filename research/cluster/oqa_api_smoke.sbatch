#!/bin/bash
# Three-question OfficeQA development smoke test with the ACTOR ON A HOSTED API. CPU only: zero GPUs.
#   STAGE=preflight research/submit.sh oqa_api_smoke <commit> -- --time=00:10:00     (free; no key, no requests)
#   STAGE=smoke     research/submit.sh oqa_api_smoke <commit> -- --time=00:30:00     (the authorized smoke test)
#
# Caps, enforced by research/officeqa/oqa_api_smoke.py and not by this script:
#   OQA_API_MAX_ATTEMPTS   hard cap on actor HTTP attempts, retries included (default 90 of the authorized 100)
#   OQA_API_USD_CAP        hard cap on actor charges, reserved worst-case before each attempt (default 0.50)
# Zero GPUs are requested, so a GPU cannot be consumed by accident. No DeepSeek call is made: the smoke test
# exercises the actor's tool loop only, and the optimizer key is unset below so a stray call fails closed.
#
# Exit: 0 tool loop PASS | 3 preflight failed (nothing sent) | 4 the actor key is absent | 33 tool loop FAIL.
#SBATCH --job-name=oqa-api-smoke
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run oqa_api_smoke
require_clean_snapshot
start_job_log
write_manifest stage="oqa_api_smoke:${STAGE:-smoke}"

STAGE="${STAGE:-smoke}"
PY="$CTL_ENV/bin/python"
DRIVER="research/officeqa/oqa_api_smoke.py"
OUT="$RUN_DIR/smoke"

log "outbound connectivity FROM THE COMPUTE NODE (login-node reachability proves nothing about here)"
curl -s -o "$RUN_DIR/models_probe.json" -w 'GET /v1/models http=%{http_code} total=%{time_total}s\n' \
    --max-time 25 "https://api.empiriolabs.ai/v1/models/qwen3-5-4b" | tee "$RUN_DIR/connectivity.txt"

if [ "$STAGE" = preflight ]; then
    PYTHONPATH="$SNAPSHOT_DIR" "$PY" -I "$DRIVER" --preflight-only --out "$OUT"
    rc=$?
    assert_run_dir_clean
    exit "$rc"
fi

[ -n "${OFFICEQA_SPLIT_DIR:-}" ] || die "OFFICEQA_SPLIT_DIR is not set"
[ -n "${OFFICEQA_DOCS_DIR:-}" ] || die "OFFICEQA_DOCS_DIR is not set"

# The actor key: from the private secrets file, never echoed, never written to a log.
load_secrets
if [ -z "${EMPIRIOLABS_API_KEY:-}" ]; then
    log "EMPIRIOLABS_API_KEY is absent from the secrets file. The preflight below still runs (it needs no key),"
    log "but no request can be sent. Add the key to the secrets file named by SECRETS_FILE in the private site"
    log "settings, mode 600, then resubmit."
    PYTHONPATH="$SNAPSHOT_DIR" "$PY" -I "$DRIVER" --preflight-only --out "$OUT"
    assert_run_dir_clean
    exit 4
fi
export TARGET_OPENAI_COMPATIBLE_API_KEY="$EMPIRIOLABS_API_KEY"
unset EMPIRIOLABS_API_KEY
# Only the ACTOR's tool loop is under test. The optimizer key is dropped AFTER load_secrets (which exports it),
# so a stray optimizer call fails closed rather than spending a DeepSeek call from a budget this authorization
# does not cover.
unset OPTIMIZER_OPENAI_COMPATIBLE_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY

status=0
PYTHONPATH="$SNAPSHOT_DIR" "$PY" -I "$DRIVER" \
    --out "$OUT" \
    --split-dir "$OFFICEQA_SPLIT_DIR" \
    --docs-dir "$OFFICEQA_DOCS_DIR" \
    --max-attempts "${OQA_API_MAX_ATTEMPTS:-90}" \
    --usd-cap "${OQA_API_USD_CAP:-0.50}" \
    --max-tool-turns "${OQA_API_MAX_TOOL_TURNS:-12}" || status=$?

# The key must not survive into anything the run directory keeps.
if grep -rIl --exclude=job.log -e "$TARGET_OPENAI_COMPATIBLE_API_KEY" "$RUN_DIR" 2>/dev/null | grep -q .; then
    log "ERROR: the key appears in a run artifact; removing the offending files"
    grep -rIl -e "$TARGET_OPENAI_COMPATIBLE_API_KEY" "$RUN_DIR" 2>/dev/null | xargs -r rm -f
    status=5
fi
unset TARGET_OPENAI_COMPATIBLE_API_KEY

log "smoke report: \$RUN_DIR/smoke/SMOKE.json"
assert_run_dir_clean
exit "$status"
