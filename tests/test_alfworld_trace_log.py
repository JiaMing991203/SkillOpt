"""Opt-in ALFWorld diagnostics must not change actions, outcomes or scores.

A deterministic two-environment stand-in for AlfWorldEnvironmentManager uses
the real vendored projection, so "executed action" means exactly what the
env manager would execute. A scripted chat_target replaces the model.
"""
import json
import os
import re

import pytest

from skillopt.envs.alfworld import rollout
from skillopt.envs.alfworld.vendor.alfworld_projection import alfworld_projection
from skillopt.utils import compute_score

ADMISSIBLE = [["go to desk 1", "take key 1", "look", "inventory"],
              ["go to shelf 1", "look", "inventory"]]

SCRIPT = {
    (0, 0): "The key is probably on the desk.\n<action>go to desk 1</action>",
    (0, 1): "<think>I see key 1 here.</think><action>Take key 1</action>",
    (1, 0): "nothing to see yet\n\n</think>\n\n<action>look</action>",
    (1, 1): RuntimeError("backend down"),
    (1, 2): "I am thinking but forgot the tag",
    (1, 3): "<action>fly to the moon</action>",
}


class FakeEnvs:
    def __init__(self):
        self.prev_admissible_commands = [list(a) for a in ADMISSIBLE]

    @property
    def get_admissible_commands(self):
        return self.prev_admissible_commands


class FakeEnvManager:
    def __init__(self):
        self.envs = FakeEnvs()
        self.t = [0, 0]
        self.executed = [[], []]
        self.won = [False, False]

    def _obs(self):
        text = [f"ENV{i} STEP{self.t[i]} You are in a room. Your task is to: find the key."
                f" Admissible: {ADMISSIBLE[i]}" for i in range(2)]
        anchor = [f"ENV{i} observation at step {self.t[i]}. Your task is to: find the key." for i in range(2)]
        return {"text": text, "anchor": anchor}

    def reset(self, kwargs):
        infos = [{"extra.gamefile": f"json_2.1.1/valid_seen/pick_and_place_simple-Key-{i}/game.tw-pddl"}
                 for i in range(2)]
        return self._obs(), infos

    def step(self, text_actions):
        actions, valids = alfworld_projection(list(text_actions), self.envs.get_admissible_commands)
        rewards, dones, infos = [], [], []
        for i in range(2):
            self.executed[i].append(actions[i])
            if i == 0 and actions[i] == "take key 1":
                self.won[i] = True
            self.t[i] += 1
            rewards.append(10.0 if self.won[i] else 0.0)
            dones.append(self.won[i])
            infos.append({"won": self.won[i], "is_action_valid": valids[i],
                          "extra.gamefile": f"json_2.1.1/valid_seen/pick_and_place_simple-Key-{i}/game.tw-pddl"})
        return self._obs(), rewards, dones, infos


def scripted_chat_target(system, user, **kwargs):
    env, step = map(int, re.search(r"ENV(\d) STEP(\d+)", user).groups())
    reply = SCRIPT.get((env, step), "<action>inventory</action>")
    if isinstance(reply, Exception):
        raise reply
    return reply, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def run(tmp_path, name, **kwargs):
    out = os.path.join(tmp_path, name)
    env = FakeEnvManager()
    results = rollout.run_alfworld_batch(env, "SKILL TEXT", max_steps=5, out_root=out,
                                         max_api_workers=2, result_ids=["t0", "t1"], **kwargs)
    convs = {tid: json.load(open(os.path.join(out, "predictions", tid, "conversation.json")))
             for tid in ("t0", "t1")}
    return out, env, results, convs


@pytest.fixture(autouse=True)
def _scripted_model(monkeypatch):
    monkeypatch.setattr(rollout, "chat_target", scripted_chat_target)


def test_trace_log_changes_nothing_but_adds_trace_files(tmp_path):
    out_a, env_a, res_a, conv_a = run(tmp_path, "plain")
    out_b, env_b, res_b, conv_b = run(tmp_path, "traced", trace_log=True)
    assert res_a == res_b
    assert conv_a == conv_b
    assert env_a.executed == env_b.executed
    assert compute_score(res_a) == compute_score(res_b)
    assert not os.path.exists(os.path.join(out_a, "predictions", "t0", "trace.json"))
    assert os.path.exists(os.path.join(out_b, "predictions", "t0", "trace.json"))


def test_trace_contents_match_what_the_env_executed(tmp_path):
    out, env, results, _ = run(tmp_path, "traced", trace_log=True)
    t0 = json.load(open(os.path.join(out, "predictions", "t0", "trace.json")))
    t1 = json.load(open(os.path.join(out, "predictions", "t1", "trace.json")))
    assert t0["initial_observation"].startswith("ENV0 observation at step 0")
    assert t0["initial_admissible_actions"] == ADMISSIBLE[0]
    assert [s["executed_action"] for s in t0["steps"]] == env.executed[0][:len(t0["steps"])]
    assert [s["executed_action"] for s in t1["steps"]] == env.executed[1][:len(t1["steps"])]
    # case-folding by the projection is visible: parsed "Take key 1" -> executed "take key 1"
    assert t0["steps"][1]["parsed_action"] == "Take key 1"
    assert t0["steps"][1]["executed_action"] == "take key 1"
    assert t0["steps"][1]["executed_action_admissible"] is True
    assert t0["final"]["won"] is True and t0["final"]["hard"] == 1
    assert t0["steps"][0]["observation_before"].startswith("ENV0 observation at step 0")
    assert t0["steps"][1]["observation_before"].startswith("ENV0 observation at step 1")
    # failures that conversation.json cannot explain are explained here
    assert "backend down" in t1["steps"][1]["call"]["error"]
    assert t1["steps"][2]["call"]["unparsed_response"] == "I am thinking but forgot the tag"
    assert t1["steps"][3]["executed_action"] == "fly to the moon"
    assert t1["steps"][3]["executed_action_admissible"] is False
    assert t1["final"]["timed_out"] is True and t1["final"]["hard"] == 0
    assert all("latency_s" in s["call"] for s in t0["steps"] + t1["steps"])


def test_compat_parser_changes_only_reasoning_fields(tmp_path):
    _, env_s, res_s, conv_s = run(tmp_path, "strict")
    _, env_c, res_c, conv_c = run(tmp_path, "compat", reasoning_parser="qwen_compat")
    assert res_s == res_c and env_s.executed == env_c.executed
    assert compute_score(res_s) == compute_score(res_c)
    for tid in ("t0", "t1"):
        for s, c in zip(conv_s[tid], conv_c[tid]):
            assert "reasoning_source" not in s and "reasoning_source" in c
            assert {k: v for k, v in s.items() if k != "reasoning"} == \
                   {k: v for k, v in c.items() if k not in ("reasoning", "reasoning_source")}
    assert conv_s["t0"][0]["reasoning"] is None
    assert conv_c["t0"][0]["reasoning"] == "The key is probably on the desk."
    assert conv_c["t0"][1]["reasoning"] == conv_s["t0"][1]["reasoning"] == "I see key 1 here."


def test_unknown_parser_fails_fast(tmp_path):
    with pytest.raises(ValueError):
        run(tmp_path, "bad", reasoning_parser="lenient")
