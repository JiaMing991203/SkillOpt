"""Rule OQA-ST-1: the status inventory reads the producing configuration, not the directory date.

WHY EACH TEST IS HERE. The instruction behind `oqa_status.py` names three traps, and each one is a test:

  * "Do not infer 'corrected' from a directory date alone; verify the producing configuration." So the
    configuration is read back out of the run's own call log, and a run whose log carries no recovery
    policy must not be reported as corrected.
  * "Keep the analyst/learner-visible record separate from additional provider fields." So the
    actor-visible trace is measured from `conversation.json` with the harness's own first message
    EXCLUDED - including it would credit the actor with the supplied pages - and the size of the
    provider's alternate field is reported from the call log, where it stays.
  * "Missing observations must not count as successful." So a statement of the form "nothing ran" over a
    tree that holds no such records is UNDECIDED, never HOLDS.

The fixtures here are constructed, not sampled from a run: they are labelled tests of the reading rules,
not evidence about any attempt. The empirical figures live in the status packet.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")


def _mod(name):
    for p in (REPO, RESEARCH):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"{name}_status_test",
                                                  os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ST = _mod("oqa_status")


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _call(extra_body=None, policy="committed_answer", recovered=False, source_chars=None):
    rec = {"policy": policy, "recovered": recovered}
    if recovered:
        rec.update({"source_field": "reasoning", "source_chars": source_chars or 8580,
                    "outcome": "recovered_committed_answer"})
    return {"request": {"model": "qwen3-5-4b", "temperature": 0.7, "max_tokens": 8000,
                        "tools": True, "extra_body": extra_body},
            "recovery": rec}


# ───────────────────────── the producing configuration ─────────────────────────

def test_the_transmitted_thinking_control_is_read_from_the_log_not_assumed(tmp_path):
    log = str(tmp_path / "thinking_off" / "actor_calls.jsonl")
    _write(log, [_call(extra_body={"enable_thinking": False}) for _ in range(3)])
    cfg = ST.transmitted_configuration(log)
    assert cfg["present"] is True
    assert cfg["thinking_control"] == "enable_thinking=false"
    assert cfg["model"] == ["qwen3-5-4b"]
    assert cfg["max_tokens"] == ["8000"]


def test_an_omitted_control_is_reported_as_the_serving_default_not_as_thinking_off(tmp_path):
    log = str(tmp_path / "historical" / "actor_calls.jsonl")
    _write(log, [_call(extra_body=None), _call(extra_body=None, recovered=True)])
    cfg = ST.transmitted_configuration(log)
    assert "serving default" in cfg["thinking_control"]
    assert cfg["calls_that_recovered"] == 1
    assert cfg["recovery_source_fields"] == {"reasoning": 1}
    assert cfg["provider_field_chars_max"] == 8580


def test_a_run_with_no_recovery_policy_in_its_log_is_not_called_corrected(tmp_path):
    """The pre-A-2 client wrote no policy at all. That absence is the thing being detected."""
    log = str(tmp_path / "old" / "actor_calls.jsonl")
    _write(log, [{"request": {"model": "qwen3-5-4b", "extra_body": None}}])
    cfg = ST.transmitted_configuration(log)
    assert cfg["recovery_policy_in_the_log"] == []
    assert cfg["calls_that_recovered"] == 0


def test_a_missing_call_log_is_reported_absent_rather_than_defaulted(tmp_path):
    cfg = ST.transmitted_configuration(str(tmp_path / "nothing" / "actor_calls.jsonl"))
    assert cfg["present"] is False


# ───────────────────────── the learner-visible trace ─────────────────────────

def _conversation(entries):
    return entries


def test_the_harness_first_message_is_not_counted_as_actor_text(tmp_path):
    pred = tmp_path / "predictions" / "UID0001"
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps([
        {"role": "user", "content": "x" * 10000},
        {"type": "message", "content": "<answer>[1]</answer>"},
    ]), encoding="utf-8")
    vis = ST.actor_written(str(tmp_path / "predictions"), "UID0001")
    assert vis["actor_written_chars"] == len("<answer>[1]</answer>")
    assert vis["answer_block_in_the_trace"] is True
    assert vis["actor_messages_with_text"] == 1


def test_a_recovered_answer_leaves_a_trace_with_no_operand_and_no_arithmetic(tmp_path):
    """The A-condition shape: the policy recovers the block alone, so the trace is 36 characters."""
    pred = tmp_path / "predictions" / "UID0017"
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps(
        [{"role": "user", "content": "q"}]
        + [{"type": "tool_call", "cmd": "grep(...)", "obs": "..."} for _ in range(11)]
        + [{"type": "message", "content": "<answer>[10102000000, 4.73]</answer>"}]
    ), encoding="utf-8")
    vis = ST.actor_written(str(tmp_path / "predictions"), "UID0017")
    assert vis["actor_written_chars"] == 36
    assert vis["arithmetic_written_in_the_trace"] is False
    assert vis["a_conversion_factor_is_stated"] is False
    assert vis["n_tool_calls"] == 11


def test_one_final_message_carrying_everything_is_detected(tmp_path):
    """The B-condition and UID0014 shape: operands, the arithmetic and the answer in the last message."""
    pred = tmp_path / "predictions" / "UID0163"
    pred.mkdir(parents=True)
    body = ("October 1981: 3,550 (in millions)\nNovember 1981: 3,475 (in millions)\n"
            "Change = 3,475 - 3,550 = -75\n<answer>[-75]</answer>")
    (pred / "conversation.json").write_text(json.dumps(
        [{"role": "user", "content": "q"}]
        + [{"type": "tool_call", "cmd": "read(...)", "obs": "..."} for _ in range(5)]
        + [{"type": "message", "content": body}]
    ), encoding="utf-8")
    vis = ST.actor_written(str(tmp_path / "predictions"), "UID0163")
    assert vis["one_final_message_carries_everything"] is True
    assert vis["arithmetic_written_in_the_trace"] is True
    assert vis["final_message_chars"] == len(body)


def test_the_harness_separators_between_messages_are_not_counted_as_actor_text(tmp_path):
    """The size of the trace is the SUM of the actor's messages, not the length of a joined string.

    This is the measurement that decides the published "20-36 characters with thinking on" figure. Under a
    joined-string reading, every empty entry the harness wrote between two turns contributes a separator,
    which inflated A1/UID0017 from 36 characters to 47. The two readings differ only when there is more
    than one entry, so the fixture has several.
    """
    pred = tmp_path / "predictions" / "UID0063"
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps([
        {"role": "user", "content": "q"},
        {"type": "message", "content": ""},
        {"type": "message", "content": "abc"},
        {"type": "message", "content": ""},
        {"type": "message", "content": "de"},
    ]), encoding="utf-8")
    vis = ST.actor_written(str(tmp_path / "predictions"), "UID0063")
    assert vis["actor_written_chars"] == 5            # "abc" + "de", and nothing for the separators
    assert vis["actor_messages_with_text"] == 2


def test_a_stated_conversion_factor_is_distinguished_from_bare_arithmetic(tmp_path):
    pred = tmp_path / "predictions" / "UID0014"
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps([
        {"role": "user", "content": "q"},
        {"type": "message", "content": "Convert to billions (divide by 1000):\n- 1930: 2410.259"},
    ]), encoding="utf-8")
    vis = ST.actor_written(str(tmp_path / "predictions"), "UID0014")
    assert vis["a_conversion_factor_is_stated"] is True


def test_a_missing_conversation_is_reported_absent(tmp_path):
    vis = ST.actor_written(str(tmp_path / "predictions"), "UID9999")
    assert vis["present"] is False
    assert "actor_written_chars" not in vis


# ───────────────────────── membership and the join key ─────────────────────────

def test_membership_comes_from_the_frozen_record_and_can_be_empty():
    frozen = {"sets": {"train": {"ids": ["UID0017"]}, "val": {"ids": ["UID0001"]},
                       "heldout": {"ids": ["UID0500"]}}}
    assert ST.membership(frozen, "UID0017") == ["train"]
    assert ST.membership(frozen, "UID0001") == ["val"]
    assert ST.membership(frozen, "UID9999") == []


def test_the_bind_audit_join_key_separates_two_runs_of_one_question():
    a1 = ST._run_key("/any/root/episodes/A1/predictions/UID0017", "UID0017")
    a2 = ST._run_key("/other/copy/episodes/A2/predictions/UID0017", "UID0017")
    assert a1 == ("A1", "UID0017")
    assert a2 == ("A2", "UID0017")
    assert a1 != a2
    # the key must survive a copy of the tree to another root, which is the point of not using the path
    assert ST._run_key("/elsewhere/episodes/A1/predictions/UID0017", "UID0017") == a1


# ───────────────────────── missing evidence is not a pass ─────────────────────────

def test_nothing_ran_over_an_empty_tree_is_undecided_not_holds(tmp_path):
    """A tree with no arm and no held-out records cannot establish that nothing ran there."""
    inv = {"summary": {"development_records_on_training_questions": 0,
                       "distinct_training_questions": []},
           "record_sets": []}
    inv_path = tmp_path / "STATUS_INVENTORY.json"
    inv_path.write_text(json.dumps(inv), encoding="utf-8")
    doc = ST.cmd_statements(str(tmp_path / "empty_campaign"), str(inv_path), str(tmp_path / "out"))
    by_text = {c["statement"]: c["verdict"] for c in doc["statements"]}
    assert by_text["No corrected-client three-approach training comparison has run."].startswith("UNDECIDED")
    assert by_text["No corrected held-out comparison has been completed."].startswith("UNDECIDED")
    assert doc["all_hold"] is False
    assert len(doc["undecided"]) == 2


def test_the_two_pending_proposals_are_detected_as_unapplied_in_the_deployed_checker(tmp_path):
    """P-1 and P-2 are proposals exactly while the deployed code still reads the way it reads.

    This test fails the day either is applied, which is the point: the packet's claim that they remain
    proposals is checked against the source rather than restated.
    """
    inv = {"summary": {"development_records_on_training_questions": 0,
                       "distinct_training_questions": []},
           "record_sets": []}
    inv_path = tmp_path / "STATUS_INVENTORY.json"
    inv_path.write_text(json.dumps(inv), encoding="utf-8")
    doc = ST.cmd_statements(str(tmp_path / "empty_campaign"), str(inv_path), str(tmp_path / "out"))
    pending = next(c for c in doc["statements"] if c["statement"].startswith("Same-message"))
    assert pending["verdict"] == "HOLDS"
    assert pending["evidence"]["P1_the_later_window_starts_after_the_operand_message"] is True
    assert pending["evidence"]["P2_absence_still_needs_both_nearer_scopes_observed"] is True
