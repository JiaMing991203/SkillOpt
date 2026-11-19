"""Rule OQA-COL-1: the collection verifies what was TRANSMITTED, and keeps the boundary it declares.

WHY EACH TEST IS HERE. The instruction behind `oqa_collect.py` names three things that must not be
assumed, and each is a test:

  * "Verify that the intended settings are actually transmitted." A freeze is a plan; only the run's own
    call log is evidence. `verify` must FAIL when the transmitted `extra_body` or the recovery policy
    disagrees with the freeze, and must fail when a frozen question has no result row.
  * "Do not infer that a calculation was absent merely because it was not written in the permitted
    trace." So the opportunity field reports `not_written_in_the_permitted_trace`, never `no_calculation`,
    and carries the caveat with it.
  * "Learner/checker input must follow the declared information boundary." The alternate provider field
    is read from the call log for reporting only; the permitted trace is measured from
    `conversation.json` with the harness's own first message and its separators excluded.

These fixtures are constructed, not sampled from a run: they test the reading rules. The empirical
figures live in the collection packet.
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
    spec = importlib.util.spec_from_file_location(f"{name}_collect_test",
                                                  os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


COL = _mod("oqa_collect")


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _freeze_doc(ids=("UID0001",)):
    return {
        "ids": list(ids),
        "n_questions": len(ids),
        "freeze_sha256_12": "deadbeefcafe",
        "answer_recovery_policy": "committed_answer",
        "settings_fingerprint": "d58cf3b59bbc2928",
        "model_and_generation": {"intended_extra_body": {"enable_thinking": False}},
    }


def _call(extra_body={"enable_thinking": False}, policy="committed_answer", outcome="ok"):
    return {"request": {"model": "qwen3-5-4b", "temperature": 0.7, "max_tokens": 8000,
                        "tools": True, "extra_body": extra_body},
            "recovery": {"policy": policy, "recovered": False,
                         "outcome": "ordinary_content_present"},
            "outcome": outcome,
            "response": {"id": "chatcmpl-1"}}


def _row(qid="UID0001", fp="d58cf3b59bbc2928"):
    return {"id": qid, "hard": 1, "soft": 1.0, "predicted_answer": "-75",
            "rollout_settings_fingerprint": fp, "termination_cause": "answer_emitted"}


def _setup(tmp_path, calls, rows, ids=("UID0001",)):
    run = tmp_path / "run"
    _write_jsonl(str(run / "actor_calls.jsonl"), calls)
    _write_jsonl(str(run / "results.jsonl"), rows)
    fr = tmp_path / "COLLECT_FREEZE.json"
    fr.write_text(json.dumps(_freeze_doc(ids)), encoding="utf-8")
    return str(fr), str(run)


# ───────────────────────── verify: the transmitted settings ─────────────────────────

def test_verify_passes_when_every_request_carried_the_frozen_settings(tmp_path):
    fr, run = _setup(tmp_path, [_call(), _call()], [_row()])
    doc = COL.cmd_verify(fr, run, str(tmp_path / "out"))
    assert doc["all_ok"] is True, [c for c in doc["checks"] if not c["ok"]]


def test_verify_fails_when_the_thinking_control_was_not_transmitted(tmp_path):
    """The defect this exists to catch: the plan says thinking off, the wire says nothing."""
    fr, run = _setup(tmp_path, [_call(), _call(extra_body=None)], [_row()])
    doc = COL.cmd_verify(fr, run, str(tmp_path / "out"))
    assert doc["all_ok"] is False
    bad = [c for c in doc["checks"] if not c["ok"]]
    assert any("extra_body" in c["check"] for c in bad)


def test_verify_fails_when_the_recovery_policy_differs_from_the_freeze(tmp_path):
    fr, run = _setup(tmp_path, [_call(policy="reasoning")], [_row()])
    doc = COL.cmd_verify(fr, run, str(tmp_path / "out"))
    assert doc["all_ok"] is False
    assert any("recovery policy" in c["check"] for c in [c for c in doc["checks"] if not c["ok"]])


def test_verify_names_a_frozen_question_that_produced_no_row(tmp_path):
    fr, run = _setup(tmp_path, [_call()], [_row("UID0001")], ids=("UID0001", "UID0002"))
    doc = COL.cmd_verify(fr, run, str(tmp_path / "out"))
    assert doc["all_ok"] is False
    rowcheck = next(c for c in doc["checks"] if "exactly one result row" in c["check"])
    assert rowcheck["missing"] == ["UID0002"]


def test_verify_fails_when_the_settings_fingerprint_moved(tmp_path):
    fr, run = _setup(tmp_path, [_call()], [_row(fp="0000000000000000")])
    doc = COL.cmd_verify(fr, run, str(tmp_path / "out"))
    assert doc["all_ok"] is False
    assert any("settings fingerprint" in c["check"] for c in [c for c in doc["checks"] if not c["ok"]])


def test_verify_counts_a_provider_error_rather_than_ignoring_it(tmp_path):
    fr, run = _setup(tmp_path, [_call(), _call(outcome="http_500")], [_row()])
    doc = COL.cmd_verify(fr, run, str(tmp_path / "out"))
    assert doc["all_ok"] is False
    err = next(c for c in doc["checks"] if "unresolved" in c["check"])
    assert err["observed_non_ok_calls"] == 1


# ───────────────────────── the permitted trace ─────────────────────────

def _conv(tmp_path, item, entries):
    pred = tmp_path / "predictions" / item
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps(entries), encoding="utf-8")
    return str(tmp_path / "predictions")


def test_the_harness_message_and_its_separators_are_not_actor_text(tmp_path):
    root = _conv(tmp_path, "UID0001", [
        {"role": "user", "content": "x" * 9000},
        {"type": "message", "content": ""},
        {"type": "message", "content": "abc"},
        {"type": "message", "content": ""},
        {"type": "message", "content": "de"},
    ])
    vis = COL.permitted_trace(root, "UID0001")
    assert vis["actor_written_chars"] == 5
    assert vis["actor_messages_with_text"] == 2


def test_tool_calls_and_their_observations_are_counted_by_name(tmp_path):
    root = _conv(tmp_path, "UID0002", [
        {"role": "user", "content": "q"},
        {"type": "tool_call", "cmd": "grep(pattern='x', path='/p')", "obs": "y" * 100},
        {"type": "tool_call", "cmd": "read(path='/p', start=1, limit=5)", "obs": "z" * 50},
        {"type": "tool_call", "cmd": "grep(pattern='w', path='/p')", "obs": ""},
        {"type": "message", "content": "<answer>[1]</answer>"},
    ])
    vis = COL.permitted_trace(root, "UID0002")
    assert vis["n_tool_calls"] == 3
    assert vis["tools_by_name"] == {"grep": 2, "read": 1}
    assert vis["observation_chars"] == 150


def test_written_arithmetic_and_a_stated_factor_are_separate_observations(tmp_path):
    root = _conv(tmp_path, "UID0014", [
        {"role": "user", "content": "q"},
        {"type": "message",
         "content": "Convert to billions (divide by 1000):\n- 1930: 2410.259\n3,475 - 3,550 = -75"},
    ])
    vis = COL.permitted_trace(root, "UID0014")
    assert vis["arithmetic_written"] is True
    assert vis["conversion_factor_stated"] is True
    assert vis["an_equals_result_written"] is True
    assert vis["a_money_scale_named"] is True


def test_the_same_message_shape_is_detected_only_when_it_is_the_last_entry(tmp_path):
    root = _conv(tmp_path, "UID0163", [
        {"role": "user", "content": "q"},
        {"type": "tool_call", "cmd": "read(path='/p')", "obs": "o"},
        {"type": "message", "content": "October: 3,550\nNovember: 3,475\n3,475 - 3,550 = -75\n<answer>-75</answer>"},
    ])
    vis = COL.permitted_trace(root, "UID0163")
    assert vis["final_message_is_the_last_entry"] is True
    assert vis["one_final_message_carries_most_of_the_text"] is True


def test_a_trailing_harness_entry_means_the_final_message_is_not_the_last_entry(tmp_path):
    root = _conv(tmp_path, "UID0044", [
        {"role": "user", "content": "q"},
        {"type": "message", "content": "3,475 - 3,550 = -75"},
        {"type": "tool_call", "cmd": "grep(pattern='z', path='/p')", "obs": "o"},
    ])
    vis = COL.permitted_trace(root, "UID0044")
    assert vis["final_message_is_the_last_entry"] is False


# ───────────────────────── absence of writing is not absence of calculation ─────────────────────────

def test_an_unwritten_calculation_is_reported_as_unobservable_not_as_absent():
    row = {"hard": 0, "predicted_answer": "12", "last_finish_reason": "stop"}
    vis = {"arithmetic_written": False, "n_tool_calls": 4}
    opp = COL._opportunity(row, vis, {})
    assert opp["calculation_in_the_permitted_trace"] == "not_written_in_the_permitted_trace"
    assert "NOT evidence that no calculation occurred" in opp["caveat"]
    assert "incorrect_answer" in opp["kinds"]
    assert "written_arithmetic" not in opp["kinds"]


def test_a_failure_with_no_written_arithmetic_still_offers_named_opportunities():
    """A failure need not contain arithmetic to be diagnostically relevant."""
    row = {"hard": 0, "predicted_answer": "", "last_finish_reason": "length"}
    vis = {"arithmetic_written": False, "n_tool_calls": 0}
    opp = COL._opportunity(row, vis, {})
    for k in ("incorrect_answer", "no_answer_emitted", "no_tool_call_at_all",
              "truncated_at_the_completion_cap"):
        assert k in opp["kinds"], k


def test_a_correct_episode_is_kept_as_a_control_with_its_own_opportunities():
    row = {"hard": 1, "predicted_answer": "-75", "last_finish_reason": "stop"}
    vis = {"arithmetic_written": True, "n_tool_calls": 5,
           "one_final_message_carries_most_of_the_text": True, "final_message_is_the_last_entry": True}
    opp = COL._opportunity(row, vis, {"monetary_operands": 5, "operands_with_a_holding_binding": 3})
    assert "incorrect_answer" not in opp["kinds"]
    assert "operand_bound_to_a_cell" in opp["kinds"]
    assert "same_message_operands_and_answer" in opp["kinds"]


# ───────────────────────── the declared boundary ─────────────────────────

def test_the_information_boundary_keeps_the_alternate_field_out_of_the_checker_input():
    b = COL.BOUNDARY
    permitted = " ".join(b["permitted_learner_and_checker_input"])
    audit = " ".join(b["audit_only_never_in_a_checker_input"])
    assert "conversation.json" in permitted
    assert "reasoning" not in permitted.lower().replace("reasoning field", "")
    assert "actor_calls.jsonl" in audit and "raw_capture.jsonl" in audit
    assert any("hard" in x for x in b["evaluator_only"])


def test_the_condition_sets_exactly_one_serving_control():
    assert COL.CONDITION["extra_body"] == {"enable_thinking": False}
    assert COL.RECOVERY_POLICY == "committed_answer"
