#!/usr/bin/env bash
# Copy a finished run directory (and its Slurm log + accounting) to the local
# research workspace. Additive only: no --delete, existing local files are kept.
#
#   research/fetch_run.sh <run-dir-name | job-id>
# Destination: $LOCAL_RUNS/<run-dir-name> (from the private site.env),
# outside the git checkout so large artifacts are never committed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/cluster/site.env"

KEY="${1:?usage: fetch_run.sh <run-dir-name|job-id>}"
if [[ "$KEY" =~ ^[0-9]+$ ]]; then
    NAME=$(ssh "$SSH_HOST" "ls -d '$RUN_ROOT'/*_'$KEY' 2>/dev/null | head -1 | xargs -r basename")
    [ -n "$NAME" ] || { echo "no run dir for job $KEY under $RUN_ROOT" >&2; exit 1; }
    JOBID="$KEY"
else
    NAME="$KEY"; JOBID="${KEY##*_}"
fi

DEST="$LOCAL_RUNS/$NAME"
mkdir -p "$DEST"
rsync -a "$SSH_HOST:$RUN_ROOT/$NAME/" "$DEST/"
rsync -a "$SSH_HOST:$PROJECT_ROOT/slurm_logs/*-$JOBID.out" "$DEST/" 2>/dev/null || true
ssh "$SSH_HOST" "sacct -j '$JOBID' -P --format=JobID,JobName,State,ExitCode,Elapsed,AllocTRES%80,MaxRSS,TotalCPU,Start,End" \
    > "$DEST/sacct.txt" 2>/dev/null || true
echo "fetched to $DEST"
