# Shared helpers for lean_skillopt Slurm jobs. Source from an sbatch script
# whose working directory is a deployed snapshot (submit.sh guarantees this).
#
#   source research/cluster/lib.sh
#   load_site                 # experiment.env (tracked) + private site.env
#   init_run <name>           # creates $RUN_DIR, refuses to reuse one
#   require_clean_snapshot    # records commit; aborts on a dirty tree
#   write_manifest            # sanitized $RUN_DIR/manifest.json (+ tracked experiment.env)
#   assert_run_dir_clean      # fail if settings files or key values reached the run dir
#   start_job_log             # tee stdout/stderr to $RUN_DIR/job.log; EXIT trap cleans up
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

# Mirror all output into $RUN_DIR/job.log and install the single EXIT handler:
# stop vLLM and the GPU sampler (if started), then close our end of the pipe
# and wait for tee, so the last log lines are not lost when Slurm reaps the
# job (seen in job 64328962, where the final line went missing).
start_job_log() {
    exec > >(tee -a "$RUN_DIR/job.log") 2>&1
    LSO_TEE_PID=$!
    trap _lso_exit EXIT
}

_lso_exit() {
    local rc=$?
    stop_vllm
    if [ -n "${LSO_GPU_MON_PID:-}" ]; then kill "$LSO_GPU_MON_PID" 2>/dev/null || true; fi
    if [ -n "${LSO_RES_MON_PID:-}" ]; then kill "$LSO_RES_MON_PID" 2>/dev/null || true; fi
    # Only the directory this job created with mktemp (lso_vllm.XXXXXXXX).
    case "${LSO_VLLM_TMPDIR:-}" in
        */lso_vllm.????????) rm -rf -- "$LSO_VLLM_TMPDIR" ;;
    esac
    exec 1>&- 2>&-
    if [ -n "${LSO_TEE_PID:-}" ]; then wait "$LSO_TEE_PID" 2>/dev/null || true; fi
    exit "$rc"
}

# Samples the job cgroup's memory (total / process / page cache) and CPU time
# every 5 s into resource_usage.csv; stopped by the EXIT handler.
start_resource_sampler() {
    "$CTL_ENV/bin/python" "$SNAPSHOT_DIR/research/resource_sampler.py" \
        --out "$RUN_DIR/resource_usage.csv" --interval 5 &
    LSO_RES_MON_PID=$!
    export LSO_RES_MON_PID
}

# Allocated resources and Great Lakes billing for this job, from Slurm itself:
# billing units (TRES "billing"), the partition's TRESBillingWeights, the
# GPU-equivalents billed (billing / GPU weight; one A40 unit = 4 CPUs = 48 GB
# on spgpu) and ARC's my_job_estimate for the requested walltime.
write_billing() {
    local part="${SLURM_JOB_PARTITION:-}" jobline weights est cpus memmb gpus tl
    jobline=$(scontrol show job -o "${SLURM_JOB_ID:-0}" 2>/dev/null || true)
    weights=$(scontrol show partition "$part" 2>/dev/null | grep -oE "TRESBillingWeights=[^ ]+" | cut -d= -f2- || true)
    cpus="${SLURM_CPUS_PER_TASK:-${SLURM_CPUS_ON_NODE:-1}}"
    memmb="${SLURM_MEM_PER_NODE:-0}"
    gpus=$(printf '%s' "$jobline" | grep -oE "gres/gpu=[0-9]+" | head -1 | cut -d= -f2 || true)
    tl=$(printf '%s' "$jobline" | grep -oE "TimeLimit=[^ ]+" | cut -d= -f2 || true)
    est=$(my_job_estimate -p "$part" -n 1 -c "$cpus" -g "${gpus:-0}" -m "${memmb}m" -t "${tl:-01:00:00}" 2>/dev/null || true)
    JOBLINE="$jobline" WEIGHTS="$weights" EST="$est" TL="$tl" python3 - "$RUN_DIR/billing.json" <<'PY'
import json, os, re, sys
line, weights = os.environ.get("JOBLINE", ""), os.environ.get("WEIGHTS", "")
alloc = re.search(r"AllocTRES=(\S+)", line) or re.search(r"\bTRES=(\S+)", line)
tres = dict(kv.split("=", 1) for kv in alloc.group(1).split(",")) if alloc else {}
w = dict(kv.split("=", 1) for kv in weights.split(",")) if weights else {}
gpu_w = float(w.get("GRES/gpu", "0") or 0)
billing = float(tres.get("billing", "0") or 0)
out = {
    "allocated": {k: tres[k] for k in ("cpu", "mem", "gres/gpu", "node") if k in tres},
    "billing_units": billing or None,
    "partition_billing_weights": w or None,
    "billable_gpu_equivalents": round(billing / gpu_w, 3) if billing and gpu_w else None,
    "physical_gpus": int(tres.get("gres/gpu", "0") or 0),
    "time_limit": os.environ.get("TL") or None,
    "my_job_estimate_usd_for_time_limit": (lambda m: float(m.group(1)) if m else None)(
        re.search(r"\(\$([0-9.]+)\)", os.environ.get("EST", ""))),
}
json.dump(out, open(sys.argv[1], "w"), indent=2)
print("billing:", json.dumps(out))
PY
}

init_run() {
    local name="$1"
    local jobid="${SLURM_JOB_ID:-nojob}"
    RUN_DIR="$RUN_ROOT/$(date +%Y%m%d-%H%M%S)_${name}_${jobid}"
    [ -e "$RUN_DIR" ] && die "run dir already exists: $RUN_DIR"
    mkdir -p "$RUN_DIR"
    export RUN_DIR RUN_NAME="$name"
    log "run dir: \$RUN_ROOT/$(basename "$RUN_DIR")"
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

# Records what this run is, in a SANITIZED form: code identity, Slurm
# allocation size, GPU model, model/serving settings. It deliberately omits
# the private site settings (account, node names, absolute paths) and never
# touches the secrets file. The private site.env is represented only by its
# SHA-256 digest, so a change of site settings between runs is detectable
# without revealing them. experiment.env is tracked in git and copied as-is.
# Extra key=value pairs are stored under "extra".
write_manifest() {
    local out="$RUN_DIR/manifest.json"
    cp "$SNAPSHOT_DIR/research/cluster/experiment.env" "$RUN_DIR/experiment.env"
    SITE_ENV_SHA256=$(sha256sum "$LSO_SITE_ENV" | cut -d' ' -f1)
    export SITE_ENV_SHA256
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader \
        > "$RUN_DIR/gpus.txt" 2>/dev/null || echo "none" > "$RUN_DIR/gpus.txt"
    python3 - "$out" "$@" <<'PY'
import json, os, sys, datetime
out, extras = sys.argv[1], sys.argv[2:]
env = os.environ
keys = ["SLURM_JOB_ID", "SLURM_JOB_NAME", "SLURM_JOB_PARTITION", "SLURM_CPUS_PER_TASK",
        "SLURM_MEM_PER_NODE", "SLURM_GPUS_ON_NODE", "SLURM_TIMELIMIT"]
manifest = {
    "written_at": datetime.datetime.now().astimezone().isoformat(),
    "run_name": env.get("RUN_NAME"),
    "run_id": os.path.basename(env["RUN_DIR"]),
    "git_commit": env.get("GIT_COMMIT"),
    "site_env_sha256": env.get("SITE_ENV_SHA256"),
    "slurm": {k: env.get(k) for k in keys if env.get(k) is not None},
    "gpus": open(os.path.join(env["RUN_DIR"], "gpus.txt")).read().strip().splitlines(),
    "target": {
        "model_id": env.get("TARGET_MODEL_ID"),
        "revision": env.get("TARGET_MODEL_REVISION"),
        "thinking_mode": env.get("TARGET_QWEN_CHAT_THINKING_MODE"),
        "temperature": env.get("TARGET_QWEN_CHAT_TEMPERATURE"),
        "max_tokens": env.get("TARGET_QWEN_CHAT_MAX_TOKENS"),
        "vllm_version_pinned": env.get("LSO_VLLM_VERSION"),
        "vllm_env": {
            "VLLM_HOST_IP": "127.0.0.1",
            "GLOO_SOCKET_IFNAME": "lo",
            "TMPDIR": "private 0700 per-job dir" if env.get("LSO_VLLM_TMPDIR") else None,
            "VLLM_USE_FLASHINFER_SAMPLER": env.get("LSO_VLLM_USE_FLASHINFER_SAMPLER"),
        },
        "vllm_enable_prefix_caching": env.get("LSO_VLLM_ENABLE_PREFIX_CACHING"),
        "vllm_args": env.get("LSO_VLLM_ARGS"),
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
    log "manifest written (sanitized)"
}

# Final guard for every job: no private settings or secrets in the artifacts.
# Fails the job if a site.env / secrets.env file is in the run dir, or if the
# DeepSeek key or the per-job vLLM key appears in any run file. Never prints
# the key values.
assert_run_dir_clean() {
    local bad=0 f var
    f=$(find "$RUN_DIR" \( -name 'site.env' -o -name 'secrets.env' \) -print -quit)
    [ -z "$f" ] || { log "LEAK: settings file in run dir: ${f#"$RUN_DIR"/}"; bad=1; }
    for var in DEEPSEEK_API_KEY VLLM_API_KEY; do
        [ -n "${!var:-}" ] || continue
        f=$(grep -rlF -- "${!var}" "$RUN_DIR" 2>/dev/null | head -1 || true)
        [ -z "$f" ] || { log "LEAK: value of $var found in ${f#"$RUN_DIR"/}"; bad=1; }
    done
    [ "$bad" = 0 ] || die "secret/settings leak in run dir (see LEAK lines); do not share this run"
    log "run dir check: no settings files or key values in artifacts"
}

# Envs created by setup_env / setup_vllm_env carry a marker file; only such envs are ever
# resumed after a failed install. An existing env without the marker (i.e.
# not created by setup_env) is never modified.
CREATED_MARK=.lso_created_by_setup_env
DONE_MARK=.lso_setup_complete

ensure_env() {
    local prefix="$1" pyver="$2" label="$3"; shift 3
    if [ -e "$prefix/$DONE_MARK" ]; then
        log "$label env already complete; leaving it unchanged"
        return
    fi
    if [ -e "$prefix" ] && [ ! -e "$prefix/$CREATED_MARK" ]; then
        die "$label env exists but was not created by setup_env; refusing to modify it"
    fi
    if [ -e "$prefix" ]; then
        log "resuming incomplete $label env from an earlier setup_env job ($(cat "$prefix/$CREATED_MARK"))"
    else
        log "creating $label env (python $pyver)"
        conda create -y -p "$prefix" "python=$pyver" pip
        echo "job=${SLURM_JOB_ID:-none} commit=$GIT_COMMIT" > "$prefix/$CREATED_MARK"
    fi
    # Some dependencies build from source and shell out to a bare `python`
    # (fast-downward-textworld runs `python build.py` + cmake), so the env's
    # bin must be first on PATH, as with `conda activate`. Job 64323201
    # failed without this.
    (
        export PATH="$prefix/bin:$PATH"
        python -m pip install --upgrade pip
        python -m pip install "$@"
    )
}

freeze_env() {
    local prefix="$1" name="$2"
    "$prefix/bin/python" -m pip freeze > "$RUN_DIR/pip_freeze_${name}.txt" 2>/dev/null || true
}

_free_port() {
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

# Classifies every TCP listener in vLLM's process session with
# research/classify_listeners.py (raw lines in vllm_listeners.txt, result in
# vllm_listeners.json) and sets LSO_NETWORK_STATUS:
#   pass        all loopback (HTTP API included)
#   unresolved  only torch's rendezvous TCPStore listens beyond loopback;
#               NOT allowed: callers must not run agent stages
#   fail        anything else -> the job stops here
check_vllm_listeners() {
    local pids rc
    pids=$(pgrep -s "$LSO_VLLM_PID" | tr '\n' ' ')   # setsid: session id == LSO_VLLM_PID
    : > "$RUN_DIR/vllm_listeners.txt"
    for pid in $pids; do
        ss -ltnpH 2>/dev/null | grep "pid=$pid," >> "$RUN_DIR/vllm_listeners.txt" || true
    done
    rc=0
    "$CTL_ENV/bin/python" "$SNAPSHOT_DIR/research/classify_listeners.py" \
        --ss-file "$RUN_DIR/vllm_listeners.txt" --http-port "$LSO_VLLM_PORT" \
        --vllm-log "$RUN_DIR/vllm.log" --out "$RUN_DIR/vllm_listeners.json" || rc=$?
    case "$rc" in
        0) LSO_NETWORK_STATUS=pass ;;
        3) LSO_NETWORK_STATUS=unresolved ;;
        *) die "vLLM listener check failed (see vllm_listeners.json)" ;;
    esac
    export LSO_NETWORK_STATUS
    log "network status: $LSO_NETWORK_STATUS"
}

# Keeps the server up for a fixed window so its listeners can be probed from
# OUTSIDE the node (research/probe_listeners.sh, run from a login node). The
# node's addresses and the listener ports are written to probe_window.json.
open_probe_window() {
    local secs="${LSO_PROBE_WINDOW_S:-180}"
    "$CTL_ENV/bin/python" - "$RUN_DIR" "$secs" <<'PY'
import json, os, subprocess, sys, time
run_dir, secs = sys.argv[1], int(sys.argv[2])
ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
lis = json.load(open(os.path.join(run_dir, "vllm_listeners.json")))
json.dump({
    "node_ips": ips,
    "listeners": [{"host": x["host"], "port": x["port"], "class": x["class"], "is_http_api": x["is_http_api"]}
                  for x in lis["listeners"]],
    "opened_at": time.time(),
    "closes_at": time.time() + secs,
}, open(os.path.join(run_dir, "probe_window.json"), "w"), indent=2)
PY
    log "probe window open for ${secs}s (probe_window.json)"
    sleep "$secs"
    log "probe window closed"
}

# Records which torch.distributed rendezvous vLLM used (from vllm.log's
# "distributed_init_method=..."): file (FileStore, no TCP listener) or tcp
# (TCPStore server on all interfaces). For file, also records whether the
# store lives inside the private 0700 temp dir. Informational; the listener
# check is the security gate. Writes vllm_rendezvous.json.
check_vllm_rendezvous() {
    local init method in_private=false mode
    init=$(grep -oE "distributed_init_method=[^ ]+" "$RUN_DIR/vllm.log" | head -1 | cut -d= -f2-)
    case "$init" in
        file://*) method=file ;;
        tcp://*) method=tcp ;;
        *) method=unknown ;;
    esac
    if [ "$method" = file ] && [ "${init#file://"$LSO_VLLM_TMPDIR"/}" != "$init" ]; then in_private=true; fi
    mode=$(stat -c %a "$LSO_VLLM_TMPDIR" 2>/dev/null || echo "?")
    LSO_RENDEZVOUS_METHOD=$method
    export LSO_RENDEZVOUS_METHOD
    printf '{"method": "%s", "store_in_private_tmpdir": %s, "private_tmpdir_mode": "%s"}\n' \
        "$method" "$in_private" "$mode" > "$RUN_DIR/vllm_rendezvous.json"
    log "rendezvous: method=$method store_in_private_tmpdir=$in_private tmpdir_mode=$mode"
}

# Confirms from vllm.log which top-k/top-p sampler vLLM selected, and fails the
# job if it does not match LSO_VLLM_USE_FLASHINFER_SAMPLER. Writes vllm_sampler.txt.
check_vllm_sampler() {
    local native="FlashInfer top-p/top-k sampling disabled via VLLM_USE_FLASHINFER_SAMPLER=0"
    local flashinfer="Using FlashInfer for top-p & top-k sampling"
    if grep -qF "$flashinfer" "$RUN_DIR/vllm.log"; then
        echo "flashinfer" > "$RUN_DIR/vllm_sampler.txt"
    elif grep -qF "$native" "$RUN_DIR/vllm.log"; then
        echo "native" > "$RUN_DIR/vllm_sampler.txt"
    else
        echo "unknown" > "$RUN_DIR/vllm_sampler.txt"
    fi
    log "vLLM top-k/top-p sampler: $(cat "$RUN_DIR/vllm_sampler.txt")"
    if [ "$LSO_VLLM_USE_FLASHINFER_SAMPLER" = "0" ] && [ "$(cat "$RUN_DIR/vllm_sampler.txt")" != "native" ]; then
        die "expected the native sampler (LSO_VLLM_USE_FLASHINFER_SAMPLER=0); see vllm.log"
    fi
}

# Verifies engine settings that must match the pinned baseline, from vllm.log:
#   - enable_prefix_caching matches LSO_VLLM_ENABLE_PREFIX_CACHING
#   - no "Default vLLM sampling parameters have been overridden" warning
#     (same text in 0.25.1 and 0.28.0; absent in the 0.25.1 runs), i.e. the
#     request defaults are not changed by a model generation config
# Records the attention backend line. Writes vllm_engine_config.json.
check_vllm_engine_config() {
    local pc override backend want
    pc=$(grep -oE "enable_prefix_caching=(True|False)" "$RUN_DIR/vllm.log" | head -1 | cut -d= -f2)
    override=false
    grep -qF "Default vLLM sampling parameters have been overridden" "$RUN_DIR/vllm.log" && override=true
    backend=$(grep -oE "Using [A-Z_]+ attention backend" "$RUN_DIR/vllm.log" | head -1 || true)
    printf '{"enable_prefix_caching": "%s", "sampling_defaults_overridden": %s, "attention_backend": "%s"}\n' \
        "${pc:-unknown}" "$override" "$backend" > "$RUN_DIR/vllm_engine_config.json"
    log "engine: enable_prefix_caching=${pc:-unknown} sampling_defaults_overridden=$override ($backend)"
    want=True; [ "$LSO_VLLM_ENABLE_PREFIX_CACHING" = "0" ] && want=False
    [ "$pc" = "$want" ] || die "enable_prefix_caching=${pc:-unknown}, expected $want (LSO_VLLM_ENABLE_PREFIX_CACHING)"
    [ "$override" = false ] || die "vLLM overrode default sampling parameters from a generation config (see vllm.log)"
}

# Starts a vLLM OpenAI-compatible server for this job only.
#  - HTTP server bound to 127.0.0.1 (--host)
#  - VLLM_HOST_IP=127.0.0.1: the rendezvous ADDRESS clients use is loopback
#    (uniproc_executor.py: tcp://get_ip():port). torch's TCPStore server still
#    binds all interfaces (no bind-address option; job 64330104)
#  - GLOO_SOCKET_IFNAME=lo: vLLM's Gloo CPU process groups bind to loopback
#    instead of the hostname's interface (7 node-IP listeners in 64330104)
#  - engine<->API sockets are local IPC files when single-node
#  - per-job random API key via env (not argv): compute nodes can be shared
#    with other users' jobs, so loopback alone is not private
#  - own process group (setsid) so cleanup reaches the engine subprocess
start_vllm() {
    local timeout_s="${1:-900}"
    LSO_VLLM_PORT=$(_free_port)
    VLLM_API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
    export LSO_VLLM_PORT VLLM_API_KEY
    local args=(
        serve "$TARGET_MODEL_ID"
        --revision "$TARGET_MODEL_REVISION"
        --served-model-name "$TARGET_MODEL_ID"
        --host 127.0.0.1 --port "$LSO_VLLM_PORT"
        --dtype bfloat16
        --max-model-len "$LSO_VLLM_MAX_MODEL_LEN"
        --gpu-memory-utilization "$LSO_VLLM_GPU_MEM_UTIL"
        --limit-mm-per-prompt '{"image":0,"video":0}'
        --seed 0
    )
    if [ "$LSO_VLLM_ENABLE_PREFIX_CACHING" = "0" ]; then args+=(--no-enable-prefix-caching); fi
    # Private temp dir for the vLLM process only: vLLM >= 0.28 puts its file://
    # rendezvous store (PR #50999) and engine IPC sockets in tempfile.gettempdir(),
    # and the node's /tmp is shared with other users' jobs.
    LSO_VLLM_TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/lso_vllm.XXXXXXXX")
    chmod 700 "$LSO_VLLM_TMPDIR"
    export LSO_VLLM_TMPDIR
    export LSO_VLLM_ARGS="TMPDIR=<private 0700 dir> VLLM_HOST_IP=127.0.0.1 GLOO_SOCKET_IFNAME=lo VLLM_USE_FLASHINFER_SAMPLER=$LSO_VLLM_USE_FLASHINFER_SAMPLER vllm ${args[*]}"
    log "starting: $LSO_VLLM_ARGS"
    TMPDIR="$LSO_VLLM_TMPDIR" VLLM_API_KEY="$VLLM_API_KEY" VLLM_HOST_IP=127.0.0.1 GLOO_SOCKET_IFNAME=lo HF_HUB_OFFLINE=1 \
        VLLM_USE_FLASHINFER_SAMPLER="$LSO_VLLM_USE_FLASHINFER_SAMPLER" \
        setsid "$LSO_VLLM_ENV/bin/vllm" "${args[@]}" > "$RUN_DIR/vllm.log" 2>&1 &
    LSO_VLLM_PID=$!
    export LSO_VLLM_PID

    local waited=0
    until curl -sf -H "Authorization: Bearer $VLLM_API_KEY" \
            "http://127.0.0.1:$LSO_VLLM_PORT/v1/models" > "$RUN_DIR/vllm_models.json"; do
        kill -0 "$LSO_VLLM_PID" 2>/dev/null || { tail -50 "$RUN_DIR/vllm.log"; die "vLLM exited during startup"; }
        [ "$waited" -ge "$timeout_s" ] && { tail -50 "$RUN_DIR/vllm.log"; die "vLLM not ready after ${timeout_s}s"; }
        sleep 10; waited=$((waited + 10))
    done
    echo "$waited" > "$RUN_DIR/vllm_startup_seconds.txt"
    log "vLLM ready on 127.0.0.1:$LSO_VLLM_PORT after ~${waited}s"
    check_vllm_rendezvous
    check_vllm_listeners
    check_vllm_sampler
    check_vllm_engine_config

    # Point SkillOpt's qwen_chat target role at the job-local server.
    export TARGET_QWEN_CHAT_BASE_URL="http://127.0.0.1:$LSO_VLLM_PORT/v1"
    export TARGET_QWEN_CHAT_API_KEY="$VLLM_API_KEY"
}

scrape_vllm_metrics() {
    curl -sf -H "Authorization: Bearer $VLLM_API_KEY" "http://127.0.0.1:$LSO_VLLM_PORT/metrics" \
        > "$RUN_DIR/vllm_metrics_$1.prom" || log "warning: could not scrape vLLM /metrics ($1)"
    date +%s.%N > "$RUN_DIR/vllm_metrics_$1.time"
}

stop_vllm() {
    if [ -n "${LSO_VLLM_PID:-}" ] && kill -0 "$LSO_VLLM_PID" 2>/dev/null; then
        log "stopping vLLM (process group $LSO_VLLM_PID)"
        kill -- "-$LSO_VLLM_PID" 2>/dev/null || kill "$LSO_VLLM_PID" 2>/dev/null || true
        for _ in $(seq 1 30); do kill -0 "$LSO_VLLM_PID" 2>/dev/null || break; sleep 1; done
        kill -9 -- "-$LSO_VLLM_PID" 2>/dev/null || true
    fi
}
