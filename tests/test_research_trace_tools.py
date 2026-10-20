"""Research tooling: fixed task subsets, trace checker, resource summary."""
import json
import os

import pytest

from research.check_traces import check_episode
from research.make_task_subset import build_subset, read_ids
from research.summarize_run import resource_usage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_throughput_subset_is_eight_train_tasks_covering_all_types():
    ids = read_ids(os.path.join(REPO, "research/task_subsets/throughput_train8.txt"))
    train = json.load(open(os.path.join(REPO, "data/alfworld_path_split/train/items.json")))
    subset = build_subset(ids, train)
    assert len(subset) == 8 and all(t["id"].startswith("train:") for t in subset)
    assert len({t["task_type"] for t in subset}) == 6
    val_ids = {t["id"] for t in json.load(open(os.path.join(REPO, "data/alfworld_path_split/val/items.json")))}
    test_ids = {t["id"] for t in json.load(open(os.path.join(REPO, "data/alfworld_path_split/test/items.json")))}
    assert not ({t["id"] for t in subset} & (val_ids | test_ids))


def test_subset_rejects_unknown_and_duplicate_ids():
    train = [{"id": "train:0000"}, {"id": "train:0001"}]
    with pytest.raises(ValueError):
        build_subset(["val:0000"], train)
    with pytest.raises(ValueError):
        build_subset(["train:0000", "train:0000"], train)


def _trace(steps, hard=1):
    return {"initial_observation": "room", "initial_admissible_actions": ["look"],
            "steps": steps, "final": {"hard": hard}}


STEP_T = {"raw_response": "x <action>Go to desk 1</action>", "parsed_action": "Go to desk 1",
          "executed_action": "go to desk 1", "projection_valid": False, "env_is_action_valid": 0,
          "env_feedback": "You arrive.", "reward": 0.0, "done": False, "observation_before": "room",
          "admissible_actions": ["go to desk 1", "look"], "executed_action_admissible": True}
STEP_C = {"step": 0, "model_response": "x <action>Go to desk 1</action>", "action": "Go to desk 1",
          "env_feedback": "You arrive.", "reward": 0.0, "done": False}


def test_trace_check_passes_on_consistent_episode():
    r = check_episode({"id": "a", "hard": 1}, [STEP_C], _trace([STEP_T]))
    assert r["ok"], r["problems"]


def test_trace_check_flags_divergence_and_missing_evidence():
    bad = dict(STEP_T, executed_action="look", admissible_actions=[])
    r = check_episode({"id": "a", "hard": 0}, [STEP_C], _trace([bad], hard=1))
    assert not r["ok"]
    text = " ".join(r["problems"])
    assert "executed" in text and "admissible" in text and "final.hard" in text


def test_resource_usage_summary(tmp_path):
    gib = 1024 ** 3
    (tmp_path / "resource_usage.csv").write_text(
        "# cgroup files: []\nunix_time,mem_bytes,mem_peak_bytes,anon_bytes,file_bytes,cpu_seconds\n"
        f"100.0,{2*gib},{2*gib},{1*gib},{1*gib},10.0\n"
        f"110.0,{5*gib},{6*gib},{3*gib},{2*gib},30.0\n")
    (tmp_path / "billing.json").write_text(json.dumps({"allocated": {"cpu": "4"}}))
    r = resource_usage(str(tmp_path))
    assert r["peak_cgroup_mem_gib"] == 5.0 and r["cgroup_recorded_peak_gib"] == 6.0
    assert r["peak_process_mem_gib"] == 3.0 and r["peak_page_cache_gib"] == 2.0
    assert r["mean_cpu_cores_used"] == 2.0 and r["cpu_utilization_of_allocation"] == 0.5
