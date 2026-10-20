#!/bin/bash
# Create ONLY the vLLM serving env for LSO_VLLM_VERSION at LSO_VLLM_ENV (CPU
# node; installing wheels needs no GPU), then verify the INSTALLED code:
#   - torch / CUDA build / transformers versions
#   - single-GPU executor uses file:// rendezvous (vLLM PR #50999)
#   - Qwen3_5ForConditionalGeneration registered
#   - VLLM_USE_FLASHINFER_SAMPLER and its "disabled" log message exist
#   - every `vllm serve` flag used by lib.sh:start_vllm is accepted
# Other envs (including earlier vLLM envs) are never touched: ensure_env
# refuses to modify any env it did not create.
#SBATCH --job-name=lso-vllm-env
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run "setup_vllm_env_${LSO_VLLM_VERSION}"
require_clean_snapshot
start_job_log
write_manifest "stage=setup_vllm_env" "vllm_version=$LSO_VLLM_VERSION"
source "$CONDA_BASE/etc/profile.d/conda.sh"

ensure_env "$LSO_VLLM_ENV" "$LSO_VLLM_PYTHON_VERSION" "vLLM $LSO_VLLM_VERSION" "vllm==$LSO_VLLM_VERSION"

log "verifying the installed vLLM $LSO_VLLM_VERSION"
"$LSO_VLLM_ENV/bin/python" - "$RUN_DIR/vllm_env_check.json" "$LSO_VLLM_VERSION" <<'PY'
# Reads installed files and package metadata only (no vLLM import on a CPU node).
import importlib.metadata as md, importlib.util, json, os, re, sys
out, want = sys.argv[1], sys.argv[2]
def pkg_dir(name):
    return list(importlib.util.find_spec(name).submodule_search_locations)[0]
V, T = pkg_dir("vllm"), pkg_dir("torch")
def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()
uniproc = read(V, "v1", "executor", "uniproc_executor.py")
m = re.search(r"def _distributed_args\(self\).*?return distributed_init_method", uniproc, re.S)
sampler = read(V, "v1", "sample", "ops", "topk_topp_sampler.py")
netu = read(V, "utils", "network_utils.py")
cuda = re.search(r"cuda: Optional\[str\] = '([^']*)'|cuda = '([^']*)'", read(T, "version.py"))
checks = {
    "vllm_version": md.version("vllm"),
    "vllm_version_matches_pin": md.version("vllm") == want,
    "torch": md.version("torch"),
    "torch_cuda": next((g for g in (cuda.groups() if cuda else ()) if g), None),
    "transformers": md.version("transformers"),
    "torchaudio": md.version("torchaudio"),
    "torchvision": md.version("torchvision"),
    "flashinfer_python": md.version("flashinfer-python"),
    "uniproc_uses_file_store": bool(m) and "get_file_store_init_method()" in m.group(0),
    "file_store_uses_tempdir": "file://{tempfile.gettempdir()}/vllm_dist_" in netu,
    "qwen3_5_registered": '"Qwen3_5ForConditionalGeneration"' in read(V, "model_executor", "models", "registry.py"),
    "native_sampler_message_present": "disabled via " in sampler and "VLLM_USE_FLASHINFER_SAMPLER=0" in sampler,
}
json.dump(checks, open(out, "w"), indent=2)
print(json.dumps(checks, indent=2))
required = ["vllm_version_matches_pin", "uniproc_uses_file_store", "file_store_uses_tempdir",
            "qwen3_5_registered", "native_sampler_message_present"]
missing = [k for k in required if not checks[k]]
if missing:
    print("FAILED checks:", missing)
    sys.exit(1)
PY

log "checking that 'vllm serve' accepts every flag start_vllm uses"
help_rc=0
"$LSO_VLLM_ENV/bin/vllm" serve --help=all > "$RUN_DIR/vllm_serve_help.txt" 2>&1 || help_rc=$?
if [ "$help_rc" != 0 ]; then
    log "WARNING: 'vllm serve --help=all' exited $help_rc on this CPU node; flag check skipped (see vllm_serve_help.txt)"
fi
missing=0
for flag in --revision --served-model-name --host --port --dtype --max-model-len \
            --gpu-memory-utilization --limit-mm-per-prompt --seed --no-enable-prefix-caching; do
    if grep -qE -- "(^|[ ,])${flag}([ ,=]|$)" "$RUN_DIR/vllm_serve_help.txt"; then
        echo "  ok   $flag"
    else
        echo "  MISSING $flag"; missing=1
    fi
done
if [ "$help_rc" = 0 ] && [ "$missing" != 0 ]; then
    die "vllm serve does not list every flag we use (see vllm_serve_help.txt)"
fi

touch "$LSO_VLLM_ENV/$DONE_MARK"
freeze_env "$LSO_VLLM_ENV" vllm
assert_run_dir_clean
log "vLLM $LSO_VLLM_VERSION env ready"
