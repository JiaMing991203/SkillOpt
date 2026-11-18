"""Amendment A-2: the narrow answer-recovery policy, against real provider bodies and constructed cases.

WHAT THESE TESTS DEFEND. The campaign ended 331 of 528 selection observations on a reply whose text the
client never read, because the provider puts it in `choices[0].message.reasoning`. The correction must
recover a COMMITTED answer and nothing else: promoting arbitrary reasoning prose, a quoted example or
one of two competing answers would replace a measurement defect with a fabrication.

The real body below is the shape captured by rule OQA-HTTP-1 in job 66340531 - content null, no tool
calls, `reasoning` holding text that ends in a complete `<answer>`. Nothing here sends a request.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from skillopt.model import common as COMMON  # noqa: E402
from skillopt.envs.officeqa import rollout as RO  # noqa: E402

ENV = "SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK"


def _mod(name):
    if RESEARCH not in sys.path:
        sys.path.insert(0, RESEARCH)
    spec = importlib.util.spec_from_file_location(f"{name}_rec_test",
                                                  os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


REC = _mod("oqa_recovery")

# The text of a real reply, from job 66340531 (arm B epoch 2's recorded request for UID0017 replayed).
_REAL_REASONING = ("Based on my analysis of the Treasury Bulletin August 1982 document, I found:\n\n"
                   "   - Total bids = $10,102 million\n   - Noncash rollover tenders = $478 million\n"
                   "   - Percentage = (478 / 10,102) x 100 = 4.7319...% = 4.73%\n\n"
                   "<answer>[10102000000, 4.73]</answer>")


def _body(message: dict, finish: str = "stop", usage: dict | None = None) -> dict:
    """A provider body with this project's provider's real key set."""
    base = {"role": "assistant", "content": None, "refusal": None, "annotations": None,
            "audio": None, "function_call": None, "tool_calls": None}
    base.update(message)
    return {"id": "chatcmpl-test", "object": "chat.completion", "model": "qwen3-5-4b",
            "created": 0,
            "choices": [{"index": 0, "finish_reason": finish, "logprobs": None, "message": base}],
            "usage": usage or {"prompt_tokens": 9072, "completion_tokens": 401,
                               "total_tokens": 9473, "cost_usd": 0.00039095,
                               "prompt_tokens_details": {"cached_tokens": 0}}}


# ───────────────────────── the policy itself ─────────────────────────

@pytest.mark.parametrize("text,outcome,payload", [
    ("work <answer>[1, 2]</answer>", COMMON.R_RECOVERED, "[1, 2]"),
    ("<answer>42</answer>\n\n ", COMMON.R_RECOVERED, "42"),
    ("<answer>7</answer> ... <answer>7</answer>", COMMON.R_RECOVERED, "7"),
    ("the value is <answer>[101020", COMMON.R_INCOMPLETE, None),
    ("<answer>1</answer> no wait <answer>2</answer>", COMMON.R_AMBIGUOUS, None),
    ("e.g. <answer>[1, 2]</answer>, but let me check the column first",
     COMMON.R_NOT_TERMINAL, None),
    ("I cannot find the table.", COMMON.R_NO_TAG, None),
    ("<answer></answer>", COMMON.R_EMPTY_PAYLOAD, ""),
])
def test_the_policy_accepts_only_an_unambiguous_complete_terminal_block(text, outcome, payload):
    got = COMMON.committed_answer(text)
    assert got["outcome"] == outcome, got
    assert got["payload"] == payload


def test_the_modes_are_named_and_off_is_the_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert COMMON.recovery_mode() == COMMON.MODE_OFF
    assert COMMON.content_fallback_enabled() is False
    for word in ("1", "true", "yes", "on", "answer", "committed_answer"):
        monkeypatch.setenv(ENV, word)
        assert COMMON.recovery_mode() == COMMON.MODE_COMMITTED_ANSWER, word
    # the permissive promotion shipped on 2026-11-18 keeps its own spelling, so the probe's recorded
    # outcomes stay reproducible; no new run asks for it
    monkeypatch.setenv(ENV, "reasoning")
    assert COMMON.recovery_mode() == COMMON.MODE_REASONING_TEXT


# ───────────────────────── through the adapter, on real and constructed bodies ─────────────────────────

def _adapt(body: dict):
    message, resp, choice = REC.from_body(body)
    return COMMON.compat_message_from_chat_message(message, resp, choice)


def test_the_committed_answer_is_recovered_from_the_real_captured_body(monkeypatch):
    monkeypatch.setenv(ENV, "committed_answer")
    out = _adapt(_body({"content": None, "reasoning": _REAL_REASONING}))
    # THE BLOCK ALONE. The reasoning prose is NOT promoted: this is exactly what the saved trace, and
    # therefore the contract learner, receives.
    assert out.content == "<answer>[10102000000, 4.73]</answer>"
    assert "Treasury Bulletin" not in out.content
    rec = out.metadata["answer_recovery"]
    assert rec["recovered"] is True and rec["source_field"] == "reasoning"
    assert rec["outcome"] == COMMON.R_RECOVERED
    assert RO._extract_answer(out.content) == "[10102000000, 4.73]"


def test_the_same_body_under_the_old_client_ends_the_episode(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    out = _adapt(_body({"content": None, "reasoning": _REAL_REASONING}))
    assert out.content == ""
    assert out.metadata["answer_recovery"]["outcome"] == COMMON.R_DISABLED
    assert RO._no_answer_cause(out.content, out.metadata, "") == "empty_reply_content_null"


@pytest.mark.parametrize("message,finish,outcome,recovered", [
    ({"content": "<answer>7</answer>"}, "stop", COMMON.R_CONTENT_PRESENT, False),
    ({"content": None, "reasoning": _REAL_REASONING}, "stop", COMMON.R_RECOVERED, True),
    ({"content": None, "reasoning": "looked at the page but I am not sure"}, "stop",
     COMMON.R_NO_TAG, False),
    ({"content": None, "reasoning": "maybe <answer>1</answer> or <answer>2</answer>"}, "stop",
     COMMON.R_AMBIGUOUS, False),
    ({"content": None}, "stop", COMMON.R_NO_ALTERNATE, False),
    ({"content": None, "reasoning": "the total is <answer>[10102"}, "length",
     COMMON.R_INCOMPLETE, False),
    ({"content": None, "reasoning": "<answer>5</answer>"}, "length",
     COMMON.R_RECOVERED_TRUNCATED, True),
    ({"content": None, "reasoning": "fine <answer>9</answer>",
      "tool_calls": [{"id": "c1", "type": "function",
                      "function": {"name": "grep", "arguments": "{}"}}]},
     "tool_calls", COMMON.R_TOOL_CALL, False),
])
def test_every_reply_shape_gets_its_own_named_outcome(monkeypatch, message, finish, outcome, recovered):
    """"Record other cases explicitly rather than silently treating them as successful recovery.\""""
    monkeypatch.setenv(ENV, "committed_answer")
    out = _adapt(_body(message, finish))
    rec = out.metadata["answer_recovery"]
    assert rec["outcome"] == outcome, rec
    assert bool(rec.get("recovered")) is recovered


def test_a_declined_recovery_is_named_in_the_termination_cause(monkeypatch):
    monkeypatch.setenv(ENV, "committed_answer")
    out = _adapt(_body({"content": None, "reasoning": "<answer>1</answer> or <answer>2</answer>"}))
    cause = RO._no_answer_cause(out.content, out.metadata, "")
    assert cause == "empty_reply_content_null+recovery_ambiguous_competing_answers"


def test_a_tool_calling_turn_keeps_the_tool_branch_even_with_an_answer_in_reasoning(monkeypatch):
    monkeypatch.setenv(ENV, "committed_answer")
    body = _body({"content": None, "reasoning": "I will check, but <answer>9</answer>",
                  "tool_calls": [{"id": "c1", "type": "function",
                                  "function": {"name": "read", "arguments": "{}"}}]}, "tool_calls")
    out = _adapt(body)
    assert out.content == "" and len(out.tool_calls) == 1
    assert REC.loop_decision(out)["episode_ends"] is False


# ───────────────────────── the offline replay tool ─────────────────────────

def test_the_replay_shows_where_the_old_client_differed(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    cap = tmp_path / "raw_capture.jsonl"
    rows = [
        {"schema": "lso.raw_exchange.v1", "stage": "rollout",
         "http": {"status_code": 200, "body": _body({"content": None, "reasoning": _REAL_REASONING})},
         "request": {"first_user_sha256": "a" * 64}},
        {"schema": "lso.raw_exchange.v1", "stage": "rollout",
         "http": {"status_code": 200, "body": _body({"content": "<answer>3</answer>"})},
         "request": {"first_user_sha256": "a" * 64}},
    ]
    cap.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    doc = REC.cmd_replay([str(cap)], str(tmp_path / "out"))
    assert doc["n_exchanges"] == 2
    assert doc["answers_the_old_client_discarded"] == 1
    assert doc["decisions_that_differ"] == 1
    first = doc["exchanges"][0]
    assert first["old_adapter"]["decision"]["branch"].startswith("neither")
    assert first["narrow_policy"]["decision"]["extracted_answer"] == "[10102000000, 4.73]"
    # the environment is left exactly as it was found
    assert ENV not in os.environ


def test_the_replay_does_not_claim_to_rebuild_the_rest_of_the_episode(tmp_path, monkeypatch):
    """The honest limit, asserted so it cannot be dropped: one re-decided exchange is not a counterfactual
    episode. After an episode has already ended, what the later turns would have done is unknown."""
    monkeypatch.delenv(ENV, raising=False)
    cap = tmp_path / "raw_capture.jsonl"
    cap.write_text(json.dumps({"schema": "lso.raw_exchange.v1", "stage": "rollout",
                               "http": {"status_code": 200,
                                        "body": _body({"content": None,
                                                       "reasoning": _REAL_REASONING})},
                               "request": {}}) + "\n")
    doc = REC.cmd_replay([str(cap)], str(tmp_path / "out"))
    assert "does not reconstruct" in doc["exchanges"][0]["caveat"]


# ───────────────────────── generation settings stay separate ─────────────────────────

def test_the_serving_controls_are_a_separate_setting_and_are_validated(monkeypatch):
    """Generation settings and answer-field handling must not be conflated: one changes what the model
    does, the other only what the client reads."""
    from skillopt.model import openai_compatible_backend as B
    monkeypatch.delenv("TARGET_OPENAI_COMPATIBLE_EXTRA_BODY", raising=False)
    assert B._extra_body("target") is None
    monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_EXTRA_BODY", '{"enable_thinking": false}')
    assert B._extra_body("target") == {"enable_thinking": False}
    monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_EXTRA_BODY", "enable_thinking=false")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        B._extra_body("target")
    monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_EXTRA_BODY", '["enable_thinking"]')
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        B._extra_body("target")


def test_the_raw_capture_names_the_episode_and_keeps_no_credential():
    from skillopt.model import openai_compatible_backend as B
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert B._msg_sha(msgs, "user") == __import__("hashlib").sha256(b"U").hexdigest()
    assert B._msg_sha(msgs, "assistant") == ""
    # the allow-list cannot pass an Authorization header through
    assert "authorization" not in [h.lower() for h in B._HEADER_ALLOW]
    assert "api-key" not in [h.lower() for h in B._HEADER_ALLOW]


# ───────────────────────── the row, and the exit that was never initialised ─────────────────────────

def test_the_row_records_where_the_answer_came_from(monkeypatch, tmp_path):
    """`answer_recovered` is about PROVENANCE, not correctness: the scorer's verdict is separate."""
    monkeypatch.setenv(ENV, "committed_answer")
    calls = {"n": 0}

    def fake_chat(**kwargs):
        calls["n"] += 1
        body = _body({"content": None, "reasoning": _REAL_REASONING})
        message, resp, choice = REC.from_body(body)
        return COMMON.compat_message_from_chat_message(message, resp, choice), {}

    monkeypatch.setattr(RO, "chat_target_messages", fake_chat)
    monkeypatch.setattr(RO, "is_target_exec_backend", lambda: False)
    monkeypatch.delenv("OFFICEQA_DOCS_DIR", raising=False)
    item = {"id": "UIDTEST", "question": "What were total bids in dollars?", "ground_truth": "x",
            "source_files": [], "source_docs": []}
    row = RO.process_one(item, str(tmp_path), "skill", max_tool_turns=3, data_dirs=[])
    assert calls["n"] == 1
    assert row["answer_recovered"] is True
    assert row["answer_recovery_source"] == "reasoning"
    assert row["answer_recovery_policy"] == "committed_answer"
    assert row["termination_cause"] == "answer_recovered_from_reasoning"
    assert row["predicted_answer"] == "[10102000000, 4.73]"
    assert row["hard"] == 0, "the scorer is unchanged and this answer is wrong"
    # the trace the learner reads carries the BLOCK, not the reasoning prose
    conv = json.load(open(os.path.join(tmp_path, "predictions", "UIDTEST", "conversation.json"),
                          encoding="utf-8"))
    said = [e.get("content") for e in conv if e.get("type") == "message"]
    assert said == ["<answer>[10102000000, 4.73]</answer>"]


def test_a_rollout_without_local_tools_still_builds_its_row(monkeypatch, tmp_path):
    """Amendment A-1 read `termination` in the row for every branch and assigned it in one, so a
    no-tools, custom-search, azure-search or codex OfficeQA rollout raised NameError when it finished."""
    monkeypatch.delenv("OFFICEQA_DOCS_DIR", raising=False)
    monkeypatch.setattr(
        RO, "_run_offline_no_tools_process",
        lambda *a, **k: ("sys", "usr", "<answer>7</answer>", "7", [], "", {}))
    item = {"id": "UIDNOTOOLS", "question": "q", "ground_truth": "7", "source_files": [],
            "source_docs": []}
    row = RO.process_one(item, str(tmp_path), "skill", use_local_tools=False, data_dirs=[])
    assert row["predicted_answer"] == "7" and row["hard"] == 1
    assert row["termination_cause"] == "unrecorded"
    assert row["answer_recovered"] is False
