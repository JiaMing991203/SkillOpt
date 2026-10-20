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
SMOKE_COMPAT = "configs/research/alfworld_smoke_qwen_compat.yaml"
SMOKE_TRACED = "configs/research/alfworld_smoke_strict_traced.yaml"

# Keys allowed to differ from upstream, per config.
WIRING_KEYS = {
    "optimizer_backend", "target_backend", "optimizer_model", "target_model",
    "optimizer_openai_compatible_base_url", "optimizer_openai_compatible_max_tokens",
    "target_qwen_chat_thinking_mode", "target_qwen_chat_temperature", "target_qwen_chat_max_tokens",
}
SCALE_KEYS = {"num_epochs", "train_size", "batch_size", "analyst_workers", "eval_test"}
# Research adapter options recorded at upstream-equivalent values (strict / off).
RESEARCH_OPTION_KEYS = {"reasoning_parser", "trace_log"}


def _flat(path):
    return flatten_config(load_config(path))


def _diff(a, b):
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


def test_baseline_changes_only_model_wiring():
    assert _diff(_flat(UPSTREAM), _flat(BASELINE)) <= WIRING_KEYS | RESEARCH_OPTION_KEYS


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
    # research options at upstream-equivalent values in the baseline
    assert cfg["reasoning_parser"] == "strict"
    assert cfg["trace_log"] is False


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
    assert env["LSO_VLLM_VERSION"] == "0.28.0"  # file:// rendezvous (PR #50999)
    assert env["LSO_VLLM_USE_FLASHINFER_SAMPLER"] == "0"  # native sampler (job 64329002)
    assert env["LSO_VLLM_ENABLE_PREFIX_CACHING"] == "0"  # parity with 0.25.1 (off for hybrid models)
    # Must agree with the YAML the trainer reads.
    cfg = _flat(BASELINE)
    assert env["TARGET_QWEN_CHAT_THINKING_MODE"] == cfg["target_qwen_chat_thinking_mode"]
    assert float(env["TARGET_QWEN_CHAT_TEMPERATURE"]) == float(cfg["target_qwen_chat_temperature"])
    assert int(env["TARGET_QWEN_CHAT_MAX_TOKENS"]) == int(cfg["target_qwen_chat_max_tokens"])


def test_qwen_compat_smoke_differs_only_in_parser_and_trace():
    strict, compat = _flat(SMOKE), _flat(SMOKE_COMPAT)
    assert _diff(strict, compat) == {"reasoning_parser", "trace_log"}
    assert compat["reasoning_parser"] == "qwen_compat" and compat["trace_log"] is True
    # everything that defines the run is unchanged
    for key in ("target_model", "target_qwen_chat_max_tokens", "target_qwen_chat_thinking_mode",
                "target_qwen_chat_temperature", "num_epochs", "batch_size", "train_size", "sel_env_num",
                "use_gate", "edit_budget", "skill_update_mode", "split_dir", "max_steps", "workers",
                "max_api_workers", "eval_test", "use_slow_update", "use_meta_skill"):
        assert strict.get(key) == compat.get(key), key


def test_strict_traced_smoke_differs_only_in_trace_log():
    # Condition A with diagnostics: strict parsing, trace.json on, nothing else.
    strict, traced = _flat(SMOKE), _flat(SMOKE_TRACED)
    assert _diff(strict, traced) == {"trace_log"}
    assert traced["reasoning_parser"] == "strict" and traced["trace_log"] is True
    assert strict["reasoning_parser"] == "strict" and strict["trace_log"] is False  # original unchanged


def test_strict_traced_smoke_reaches_the_adapter_as_strict_plus_trace():
    # The path scripts/train.py uses: flattened config -> adapter kwargs.
    import scripts.train as train_script

    adapter = train_script.get_adapter(_flat(SMOKE_TRACED))
    assert type(adapter).__name__ == "ALFWorldAdapter"
    assert adapter.reasoning_parser == "strict"
    assert adapter.trace_log is True
    base = train_script.get_adapter(_flat(SMOKE))
    assert base.reasoning_parser == "strict" and base.trace_log is False
    for attr in ("max_steps", "workers", "max_api_workers", "analyst_workers", "minibatch_size",
                 "edit_budget", "max_completion_tokens", "failure_only"):
        assert getattr(adapter, attr) == getattr(base, attr), attr
