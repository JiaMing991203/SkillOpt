"""research/summarize_run.py must separate rollout fallbacks from genuine actions."""
import json

from research.summarize_run import FALLBACK_SENTINELS, classify_step, summarize_rollout
from skillopt.envs.alfworld import rollout


def test_sentinels_match_rollout_source():
    # If rollout.py changes its fallback strings, detection silently breaks.
    src = open(rollout.__file__, encoding="utf-8").read()
    for sentinel in FALLBACK_SENTINELS:
        assert f'"{sentinel}"' in src


def test_genuine_look_is_not_a_fallback():
    c = classify_step({"model_response": "<think>scan room</think><action>look</action>", "action": "look"})
    assert c["fallback"] is None and c["genuine_look"] and not c["format_invalid"]


def test_each_sentinel_is_classified():
    kinds = {classify_step({"model_response": s, "action": "look"})["fallback"] for s in FALLBACK_SENTINELS}
    assert kinds == {"empty_response", "missing_action_tag", "call_error"}


def test_near_miss_is_not_a_fallback():
    # Prefix match would misclassify this genuine response.
    c = classify_step({"model_response": "<think>error</think><action>look</action> extra", "action": "look"})
    assert c["fallback"] is None


def test_summarize_rollout_counts(tmp_path):
    steps = [
        {"model_response": "<think>missing action tag</think><action>look</action>", "action": "look",
         "env_feedback": "You arrive."},
        {"model_response": "<think>t</think><action>go to desk 1</action>", "action": "go to desk 1",
         "env_feedback": "Nothing happens."},
        {"model_response": "<action>look</action>", "action": "look", "env_feedback": "ok"},
    ]
    (tmp_path / "predictions" / "val:0000").mkdir(parents=True)
    (tmp_path / "predictions" / "val:0000" / "conversation.json").write_text(json.dumps(steps))
    (tmp_path / "results.jsonl").write_text(json.dumps({"id": "val:0000", "hard": 0, "n_turns": 3}) + "\n")
    r = summarize_rollout(str(tmp_path))
    assert r["fallback_actions"] == 1
    assert r["episodes"][0]["fallback_by_kind"] == {"missing_action_tag": 1}
    assert r["genuine_look"] == 1
    assert r["nothing_happens"] == 1
    assert r["format_invalid"] == 1
