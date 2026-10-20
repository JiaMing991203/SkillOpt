#!/bin/bash
# Compute-node connectivity check (CPU only, ~1 minute, 1-2 tiny API calls).
# Verifies outbound HTTPS from a compute node to DeepSeek and Hugging Face,
# lists the DeepSeek model ids, and (if OPTIMIZER_MODEL is set) makes one
# small chat call to record the resolved model id.
#SBATCH --job-name=lso-check
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run check_compute
require_clean_snapshot
exec > >(tee -a "$RUN_DIR/job.log") 2>&1
load_secrets
write_manifest stage=check_compute

log "outbound HTTPS from $(hostname)"
for url in "$DEEPSEEK_BASE_URL/models" "https://huggingface.co/api/models/$TARGET_MODEL_ID"; do
    curl -s -o /dev/null -w "  $url -> HTTP %{http_code} (%{time_total}s)\n" --max-time 20 "$url" || echo "  $url -> FAILED"
done

PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" research/preflight_endpoints.py \
    --optimizer --out "$RUN_DIR/preflight.json"
