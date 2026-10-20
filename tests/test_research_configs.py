"""Guards for the research baseline configs (configs/research/).

The research configs may only change model wiring and run scale. Everything
that defines the original SkillOpt algorithm on ALFWorld (gate, edit budget,
schedule, update mode, split, episode length) must stay identical to
configs/alfworld/default.yaml.
"""
from skillopt.config import flatten_config, load_config

UPSTREAM = "configs/alfworld/default.yaml"
BASELINE = "configs/research/alfworld_qwen35_deepseek.yaml"
SMOKE = "configs/research/alfworld_smoke.yaml"

# Keys allowed to differ from upstream, per config.
WIRING_KEYS = {
    "optimizer_backend", "target_backend", "optimizer_model", "target_model",
    "optimizer_openai_compatible_base_url", "optimizer_openai_compatible_max_tokens",
    "target_qwen_chat_thinking_mode", "target_qwen_chat_temperature", "target_qwen_chat_max_tokens",
}
SCALE_KEYS = {"num_epochs", "train_size", "batch_size", "analyst_workers", "eval_test"}


def _flat(path):
    return flatten_config(load_config(path))


def _diff(a, b):
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


def test_baseline_changes_only_model_wiring():
    assert _diff(_flat(UPSTREAM), _flat(BASELINE)) <= WIRING_KEYS


def test_smoke_changes_only_scale_on_top_of_baseline():
    assert _diff(_flat(BASELINE), _flat(SMOKE)) <= SCALE_KEYS


def test_baseline_wiring_values():
    cfg = _flat(BASELINE)
    assert cfg["optimizer_backend"] == "openai_compatible"
    assert cfg["target_backend"] == "qwen_chat"
    assert cfg["target_model"] == "Qwen/Qwen3.5-4B"
    assert cfg["target_qwen_chat_thinking_mode"] == "disabled"
    # Blank so the run cannot silently use the backend's gpt-4o-mini default.
    assert cfg["optimizer_model"] == ""


def test_smoke_keeps_original_gate_and_selection_split():
    cfg = _flat(SMOKE)
    assert cfg["use_gate"] is True
    assert cfg["sel_env_num"] == 0  # full selection split
    assert cfg["split_dir"] == "data/alfworld_path_split"
    assert cfg["max_steps"] == 50
    assert cfg["eval_test"] is False  # held-out test split stays unseen
    # upstream epoch-level behaviour is not switched off for the smoke run
    assert cfg["use_slow_update"] is True
    assert cfg["use_meta_skill"] is True
    assert cfg["slow_update_gate_with_selection"] is False


def _experiment_env():
    values = {}
    with open("research/cluster/experiment.env", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip()
    return values


def test_vllm_serving_settings_are_pinned():
    # Shared by every baseline and Lean-guided arm; changing them changes the target.
    env = _experiment_env()
    assert env["TARGET_MODEL_ID"] == "Qwen/Qwen3.5-4B"
    assert env["TARGET_MODEL_REVISION"] == "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
    assert env["LSO_VLLM_VERSION"] == "0.25.1"
    assert env["LSO_VLLM_USE_FLASHINFER_SAMPLER"] == "0"  # native sampler (job 64329002)
    # Must agree with the YAML the trainer reads.
    cfg = _flat(BASELINE)
    assert env["TARGET_QWEN_CHAT_THINKING_MODE"] == cfg["target_qwen_chat_thinking_mode"]
    assert float(env["TARGET_QWEN_CHAT_TEMPERATURE"]) == float(cfg["target_qwen_chat_temperature"])
    assert int(env["TARGET_QWEN_CHAT_MAX_TOKENS"]) == int(cfg["target_qwen_chat_max_tokens"])
