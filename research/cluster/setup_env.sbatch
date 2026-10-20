#!/bin/bash
# One-time environment setup on a CPU node (no GPU needed to install wheels).
#   - creates two NEW conda envs (refuses to modify an existing one)
#   - downloads Qwen3.5-4B at the pinned revision into $HF_HOME
#   - downloads ALFWorld game files into $ALFWORLD_DATA
#   - runs import smoke checks
# Submit through research/submit.sh so it runs from a deployed snapshot.
#SBATCH --job-name=lso-setup
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run setup_env
require_clean_snapshot
exec > >(tee -a "$RUN_DIR/job.log") 2>&1
write_manifest stage=setup_env
source "$CONDA_BASE/etc/profile.d/conda.sh"

# ── Controller env: SkillOpt + ALFWorld (TextWorld only, no THOR) ────────────
if [ -e "$CTL_ENV" ]; then
    log "controller env exists, leaving it unchanged: $CTL_ENV"
else
    log "creating controller env $CTL_ENV (python $CTL_PYTHON_VERSION)"
    conda create -y -p "$CTL_ENV" "python=$CTL_PYTHON_VERSION" pip
    "$CTL_ENV/bin/python" -m pip install --upgrade pip
    # Non-editable install would copy a snapshot into site-packages; instead we
    # install deps only and run SkillOpt from the snapshot via PYTHONPATH.
    "$CTL_ENV/bin/python" -m pip install \
        "openai>=1.30.0" "pyyaml>=6.0" "numpy>=1.24.0" "openpyxl>=3.1.0" \
        "azure-identity>=1.15.0" "azure-core>=1.30.0" "httpx>=0.27.0" \
        "alfworld==$ALFWORLD_VERSION" "gymnasium>=0.29.0" omegaconf "json_repair>=0.61.0" \
        "pytest>=8.0.0" "huggingface_hub"
fi

log "controller import check"
if ! PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" -c \
    "import alfworld.agents.environment, textworld, gymnasium, omegaconf; import skillopt.envs.alfworld.adapter; print('controller imports ok')"; then
    die "controller import check failed (see traceback above); env left for inspection"
fi

# ── vLLM env ─────────────────────────────────────────────────────────────────
if [ -e "$VLLM_ENV" ]; then
    log "vLLM env exists, leaving it unchanged: $VLLM_ENV"
else
    log "creating vLLM env $VLLM_ENV (python $VLLM_PYTHON_VERSION, vllm==$VLLM_VERSION)"
    conda create -y -p "$VLLM_ENV" "python=$VLLM_PYTHON_VERSION" pip
    "$VLLM_ENV/bin/python" -m pip install --upgrade pip
    "$VLLM_ENV/bin/python" -m pip install "vllm==$VLLM_VERSION"
fi
"$VLLM_ENV/bin/python" -c "import vllm, torch, transformers; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda', torch.version.cuda, 'transformers', transformers.__version__)"

# ── Target model weights (pinned revision) ───────────────────────────────────
log "downloading $TARGET_MODEL_ID@$TARGET_MODEL_REVISION into $HF_HOME"
"$CTL_ENV/bin/python" - <<PY
from huggingface_hub import snapshot_download
p = snapshot_download("$TARGET_MODEL_ID", revision="$TARGET_MODEL_REVISION")
print("model path:", p)
PY

# ── ALFWorld game data ───────────────────────────────────────────────────────
if [ -d "$ALFWORLD_DATA/json_2.1.1" ] && [ -d "$ALFWORLD_DATA/logic" ]; then
    log "ALFWorld data present at $ALFWORLD_DATA"
else
    log "running alfworld-download into $ALFWORLD_DATA"
    mkdir -p "$ALFWORLD_DATA"
    ALFWORLD_DATA="$ALFWORLD_DATA" "$CTL_ENV/bin/alfworld-download"
fi

log "checking that every split gamefile exists"
PYTHONPATH="$SNAPSHOT_DIR" "$CTL_ENV/bin/python" - <<'PY'
import json, os
root = os.environ["ALFWORLD_DATA"]
for split in ("train", "val", "test"):
    items = json.load(open(f"data/alfworld_path_split/{split}/items.json"))
    missing = [i["gamefile"] for i in items if not os.path.exists(os.path.join(root, i["gamefile"]))]
    print(f"{split}: {len(items)} items, {len(missing)} missing")
    if missing:
        print("  e.g.", missing[:3])
        raise SystemExit(1)
PY

log "ALFWorld data census (split provenance; see BASELINE_ARCHITECTURE.md §2a)"
"$CTL_ENV/bin/python" - "$RUN_DIR/alfworld_data_census.json" <<'PY'
import glob, json, os, sys
root = os.environ["ALFWORLD_DATA"]
census = {"games": {}, "split_files_found": []}
for d in ("train", "valid_seen", "valid_unseen", "valid_train"):
    games = glob.glob(os.path.join(root, "json_2.1.1", d, "*", "*", "game.tw-pddl"))
    census["games"][d] = len(games)
for name in ("split_train.json", "split_val.json", "split_test.json"):
    census["split_files_found"] += [os.path.relpath(p, root) for p in glob.glob(os.path.join(root, "**", name), recursive=True)]
# Is the shipped 18-item val split some simple prefix/ordering of valid_seen?
val = [i["gamefile"] for i in json.load(open("data/alfworld_path_split/val/items.json"))]
seen = sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, "json_2.1.1", "valid_seen", "*", "*", "game.tw-pddl")))
census["val_items_in_valid_seen"] = sum(v in set(seen) for v in val)
census["val_positions_in_sorted_valid_seen"] = [seen.index(v) if v in seen else None for v in val]
json.dump(census, open(sys.argv[1], "w"), indent=2)
print(json.dumps(census, indent=2))
PY

freeze_env "$CTL_ENV" ctl
freeze_env "$VLLM_ENV" vllm
log "setup complete"
