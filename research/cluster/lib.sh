# Shared helpers for lean_skillopt Slurm jobs. Source from an sbatch script
# whose working directory is a deployed snapshot (submit.sh guarantees this).
#
#   source research/cluster/lib.sh
#   load_site                 # experiment.env (tracked) + private site.env
#   init_run <name>           # creates $RUN_DIR, refuses to reuse one
#   require_clean_snapshot    # records commit; aborts on a dirty tree
#   write_manifest            # $RUN_DIR/manifest.json + copies of both env files
#   start_vllm / stop_vllm    # job-scoped server, loopback only, random key
#   scrape_vllm_metrics <tag> # Prometheus counters for throughput accounting

set -euo pipefail

SNAPSHOT_DIR="${SNAPSHOT_DIR:-$PWD}"
LSO_SITE_ENV="${LSO_SITE_ENV:-$HOME/.config/lean_skillopt/site.env}"

log() { printf '[%(%Y-%m-%dT%H:%M:%S)T] %s\n' -1 "$*"; }
die() { log "ERROR: $*"; exit 1; }

load_site() {
    [ -f "$SNAPSHOT_DIR/research/cluster/experiment.env" ] \
        || die "run from a snapshot root (missing research/cluster/experiment.env in $SNAPSHOT_DIR)"
    [ -f "$LSO_SITE_ENV" ] || die "private site settings not found: $LSO_SITE_ENV (deploy.sh uploads it)"
    set -a
    # shellcheck disable=SC1091
    source "$SNAPSHOT_DIR/research/cluster/experiment.env"
    # shellcheck disable=SC1090
    source "$LSO_SITE_ENV"
    set +a
    export HF_HOME ALFWORLD_DATA
    # Keep pip/vLLM caches off the home quota.
    export PIP_CACHE_DIR="$PROJECT_ROOT/cache/pip"
    export XDG_CACHE_HOME="$PROJECT_ROOT/cache/xdg"
    export VLLM_CACHE_ROOT="$PROJECT_ROOT/cache/vllm"
    export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
    # Requests to the job-local server must never go through a proxy.
    export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}" no_proxy="127.0.0.1,localhost${no_proxy:+,$no_proxy}"
    mkdir -p "$PIP_CACHE_DIR" "$XDG_CACHE_HOME" "$VLLM_CACHE_ROOT"
}

# Load cluster-only secrets without echoing them. Exports the key under the
# name SkillOpt's openai_compatible backend reads for the optimizer role.
load_secrets() {
    [ -f "$SECRETS_FILE" ] || die "secrets file not found: $SECRETS_FILE"
    local perms
    perms=$(stat -c %a "$SECRETS_FILE")
    [ "$perms" = "600" ] || die "$SECRETS_FILE must be chmod 600 (is $perms)"
    set +x
    set -a
    # shellcheck disable=SC1090
    source "$SECRETS_FILE"
    set +a
    [ -n "${DEEPSEEK_API_KEY:-}" ] || die "DEEPSEEK_API_KEY not set in $SECRETS_FILE"
    export OPTIMIZER_OPENAI_COMPATIBLE_API_KEY="$DEEPSEEK_API_KEY"
}

init_run() {
    local name="$1"
    local jobid="${SLURM_JOB_ID:-nojob}"
    RUN_DIR="$RUN_ROOT/$(date +%Y%m%d-%H%M%S)_${name}_${jobid}"
    [ -e "$RUN_DIR" ] && die "run dir already exists: $RUN_DIR"
    mkdir -p "$RUN_DIR"
    export RUN_DIR RUN_NAME="$name"
    log "run dir: $RUN_DIR"
}

require_clean_snapshot() {
    git -C "$SNAPSHOT_DIR" rev-parse --git-dir >/dev/null 2>&1 || die "$SNAPSHOT_DIR is not a git worktree"
    GIT_COMMIT=$(git -C "$SNAPSHOT_DIR" rev-parse HEAD)
    local dirty
    dirty=$(git -C "$SNAPSHOT_DIR" status --porcelain --untracked-files=no)
    [ -z "$dirty" ] || die "snapshot has modified tracked files; refusing to run: $dirty"
    export GIT_COMMIT
    log "commit: $GIT_COMMIT"
}

# Records what this run is: code identity, Slurm allocation, hardware, models.
# Extra key=value pairs are stored under "extra".
write_manifest() {
    local out="$RUN_DIR/manifest.json"
    cp "$SNAPSHOT_DIR/research/cluster/experiment.env" "$RUN_DIR/experiment.env"
    cp "$LSO_SITE_ENV" "$RUN_DIR/site.env"
    nvidia-smi --query-gpu=name,driver_version,memory.total,uuid --format=csv,noheader \
        > "$RUN_DIR/gpus.txt" 2>/dev/null || echo "none" > "$RUN_DIR/gpus.txt"
    python3 - "$out" "$@" <<'PY'
import json, os, socket, sys, datetime
out, extras = sys.argv[1], sys.argv[2:]
env = os.environ
keys = ["SLURM_JOB_ID", "SLURM_JOB_NAME", "SLURM_JOB_ACCOUNT", "SLURM_JOB_PARTITION",
        "SLURM_JOB_NODELIST", "SLURM_CPUS_PER_TASK", "SLURM_MEM_PER_NODE",
        "SLURM_GPUS_ON_NODE", "SLURM_JOB_GPUS", "CUDA_VISIBLE_DEVICES", "SLURM_TIMELIMIT"]
manifest = {
    "written_at": datetime.datetime.now().astimezone().isoformat(),
    "host": socket.gethostname(),
    "run_name": env.get("RUN_NAME"),
    "run_dir": env.get("RUN_DIR"),
    "git_commit": env.get("GIT_COMMIT"),
    "snapshot_dir": env.get("SNAPSHOT_DIR"),
    "slurm": {k: env.get(k) for k in keys if env.get(k) is not None},
    "gpus": open(os.path.join(env["RUN_DIR"], "gpus.txt")).read().strip().splitlines(),
    "target": {
        "model_id": env.get("TARGET_MODEL_ID"),
        "revision": env.get("TARGET_MODEL_REVISION"),
        "thinking_mode": env.get("TARGET_QWEN_CHAT_THINKING_MODE"),
        "temperature": env.get("TARGET_QWEN_CHAT_TEMPERATURE"),
        "max_tokens": env.get("TARGET_QWEN_CHAT_MAX_TOKENS"),
        "vllm_version_pinned": env.get("VLLM_VERSION"),
        "vllm_args": env.get("VLLM_ARGS_RECORD"),
    },
    "optimizer": {
        "base_url": env.get("DEEPSEEK_BASE_URL"),
        "model_requested": env.get("OPTIMIZER_MODEL"),
    },
    "extra": dict(e.split("=", 1) for e in extras if "=" in e),
}
with open(out, "w") as f:
    json.dump(manifest, f, indent=2)
PY
    log "manifest: $out"
}

freeze_env() {
    local prefix="$1" name="$2"
    "$prefix/bin/python" -m pip freeze > "$RUN_DIR/pip_freeze_${name}.txt" 2>/dev/null || true
}

_free_port() {
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

# Fails the job if any TCP listener in vLLM's process group is bound to
# anything other than loopback. Records all of them in vllm_listeners.txt.
check_vllm_listeners() {
    local pids
    pids=$(pgrep -s "$VLLM_PID" | tr '\n' ' ')   # setsid: session id == VLLM_PID
    : > "$RUN_DIR/vllm_listeners.txt"
    for pid in $pids; do
        ss -ltnpH 2>/dev/null | grep "pid=$pid," >> "$RUN_DIR/vllm_listeners.txt" || true
    done
    log "vLLM TCP listeners:"; sed 's/^/    /' "$RUN_DIR/vllm_listeners.txt"
    [ -s "$RUN_DIR/vllm_listeners.txt" ] || die "found no vLLM listeners; cannot verify binding"
    if awk '{print $4}' "$RUN_DIR/vllm_listeners.txt" | grep -vqE '^(127\.0\.0\.1|\[::1\]):[0-9]+$'; then
        die "vLLM has a non-loopback TCP listener (see vllm_listeners.txt)"
    fi
}

# Starts a vLLM OpenAI-compatible server for this job only.
#  - HTTP server bound to 127.0.0.1 (--host)
#  - VLLM_HOST_IP=127.0.0.1 so the torch.distributed rendezvous of the
#    single-GPU executor also uses loopback instead of the node's network IP
#    (vllm/v1/executor/uniproc_executor.py uses get_ip(); engine<->API
#    sockets are local IPC files when single-node)
#  - per-job random API key via env (not argv): compute nodes can be shared
#    with other users' jobs, so loopback alone is not private
#  - own process group (setsid) so cleanup reaches the engine subprocess
start_vllm() {
    local timeout_s="${1:-900}"
    VLLM_PORT=$(_free_port)
    VLLM_API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
    export VLLM_PORT VLLM_API_KEY
    local args=(
        serve "$TARGET_MODEL_ID"
        --revision "$TARGET_MODEL_REVISION"
        --served-model-name "$TARGET_MODEL_ID"
        --host 127.0.0.1 --port "$VLLM_PORT"
        --dtype bfloat16
        --max-model-len "$VLLM_MAX_MODEL_LEN"
        --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL"
        --limit-mm-per-prompt '{"image":0,"video":0}'
        --seed 0
    )
    export VLLM_ARGS_RECORD="VLLM_HOST_IP=127.0.0.1 vllm ${args[*]}"
    log "starting: $VLLM_ARGS_RECORD"
    VLLM_API_KEY="$VLLM_API_KEY" VLLM_HOST_IP=127.0.0.1 HF_HUB_OFFLINE=1 \
        setsid "$VLLM_ENV/bin/vllm" "${args[@]}" > "$RUN_DIR/vllm.log" 2>&1 &
    VLLM_PID=$!
    export VLLM_PID
    trap stop_vllm EXIT

    local waited=0
    until curl -sf -H "Authorization: Bearer $VLLM_API_KEY" \
            "http://127.0.0.1:$VLLM_PORT/v1/models" > "$RUN_DIR/vllm_models.json"; do
        kill -0 "$VLLM_PID" 2>/dev/null || { tail -50 "$RUN_DIR/vllm.log"; die "vLLM exited during startup"; }
        [ "$waited" -ge "$timeout_s" ] && { tail -50 "$RUN_DIR/vllm.log"; die "vLLM not ready after ${timeout_s}s"; }
        sleep 10; waited=$((waited + 10))
    done
    echo "$waited" > "$RUN_DIR/vllm_startup_seconds.txt"
    log "vLLM ready on 127.0.0.1:$VLLM_PORT after ~${waited}s"
    check_vllm_listeners

    # Point SkillOpt's qwen_chat target role at the job-local server.
    export TARGET_QWEN_CHAT_BASE_URL="http://127.0.0.1:$VLLM_PORT/v1"
    export TARGET_QWEN_CHAT_API_KEY="$VLLM_API_KEY"
}

scrape_vllm_metrics() {
    curl -sf -H "Authorization: Bearer $VLLM_API_KEY" "http://127.0.0.1:$VLLM_PORT/metrics" \
        > "$RUN_DIR/vllm_metrics_$1.prom" || log "warning: could not scrape vLLM /metrics ($1)"
    date +%s.%N > "$RUN_DIR/vllm_metrics_$1.time"
}

stop_vllm() {
    if [ -n "${VLLM_PID:-}" ] && kill -0 "$VLLM_PID" 2>/dev/null; then
        log "stopping vLLM (process group $VLLM_PID)"
        kill -- "-$VLLM_PID" 2>/dev/null || kill "$VLLM_PID" 2>/dev/null || true
        for _ in $(seq 1 30); do kill -0 "$VLLM_PID" 2>/dev/null || break; sleep 1; done
        kill -9 -- "-$VLLM_PID" 2>/dev/null || true
    fi
}
