"""The answer key must not reach the optimizer, and arm A's prompt must stay byte-identical to upstream.

These tests run against SkillOpt's own `reflect.fmt_minibatch_trajectories`, not a copy of its logic, so they fail
if upstream changes in a way that reintroduces the leak.

    python3.12 -m pytest tests/test_officeqa_answer_key_isolation.py -q
"""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "research", "officeqa"))

pytest.importorskip("skillopt.gradient.reflect", reason="needs SkillOpt importable (Python >= 3.10)")

GOLD = "646,379"
PREDICTION = "2,602,338"

USER_PROMPT = f"""## Question
What was the interest-bearing debt outstanding at the end of April 1977?

## Oracle Parsed Pages
The following content is pre-parsed from the oracle OfficeQA source page(s).

### treasury_bulletin_1977_04.txt page 9
Source URL: https://fraser.stlouisfed.org/example?page=9

TABLE IBD-1. - INTEREST-BEARING DEBT OUTSTANDING
(In millions of dollars)
Interest-bearing debt, April 1977 .................... {GOLD}

## Candidate Files
- /docs/transformed/treasury_bulletin_1977_04.txt
"""

CONVERSATION = [
    {"role": "user", "content": USER_PROMPT},
    {"type": "tool_call", "cmd": "grep(pattern='interest-bearing debt', path='treasury_bulletin_1977_04.txt')",
     "obs": f"4: Interest-bearing debt, April 1977 .................... {GOLD}"},
    {"type": "message", "content": f"The figure is {PREDICTION}. <answer>{PREDICTION}</answer>"},
]


@pytest.fixture()
def rollout(tmp_path):
    """A rollout directory whose single item FAILED, written the way the adapter writes it."""
    pred = tmp_path / "predictions" / "UID0033"
    pred.mkdir(parents=True)
    (pred / "target_user_prompt.txt").write_text(USER_PROMPT, encoding="utf-8")
    (pred / "target_system_prompt.txt").write_text("You are an expert OfficeQA agent.", encoding="utf-8")
    (pred / "conversation.json").write_text(json.dumps(CONVERSATION), encoding="utf-8")
    row = {
        "id": "UID0033", "question": "What was the interest-bearing debt outstanding at the end of April 1977?",
        "task_description": "What was the interest-bearing debt outstanding at the end of April 1977?",
        "task_type": "easy", "predicted_answer": PREDICTION, "ground_truth": GOLD,
        "hard": 0, "soft": 0.0, "n_turns": 3,
        "fail_reason": f"predicted '{PREDICTION}' but the scorer marked it incorrect",
    }
    (tmp_path / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return {"root": str(tmp_path), "prediction_dir": str(tmp_path / "predictions"), "row": row}


def _render(items, prediction_dir, report_dir=None):
    if report_dir:
        os.environ["OFFICEQA_STAGE_REPORT_DIR"] = report_dir
    else:
        os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
    reflect = importlib.import_module("skillopt.gradient.reflect")
    return reflect.fmt_minibatch_trajectories(items, prediction_dir)


# ── the leak ───────────────────────────────────────────────────────────────────

def test_adapter_no_longer_writes_the_gold_into_fail_reason():
    """The record the analyst reads must not state the expected answer."""
    from skillopt.envs.officeqa.evaluator import evaluate
    # Comments are stripped first: the fix's own comment quotes the old form to explain what changed, and that
    # must not look like a regression. Only executable lines are searched.
    source = open(os.path.join(REPO, "skillopt", "envs", "officeqa", "rollout.py"), encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "but expected" not in code, "the leaking fail_reason form is back in the OfficeQA adapter's code"
    # The scorer still knows the gold - isolation must not mean losing the ability to score.
    assert evaluate("646379", GOLD)["em"] == 1.0
    assert evaluate("2602338", GOLD)["em"] == 0.0


def test_optimizer_prompt_states_the_prediction_but_not_the_target(rollout):
    prompt = _render([rollout["row"]], rollout["prediction_dir"])
    assert "but expected" not in prompt
    assert "the scorer marked it incorrect" in prompt
    assert PREDICTION in prompt, "the analyst still needs to know what was answered"


def test_the_gold_appears_only_inside_the_supplied_evidence(rollout):
    """The gold value legitimately occurs in the document; it must not occur anywhere else."""
    prompt = _render([rollout["row"]], rollout["prediction_dir"])
    # Everything outside the trace/evidence blocks must be free of it.
    header_only = prompt.split("#### Target System Prompt")[0]
    assert GOLD not in header_only, (
        "the gold value appears in the trajectory header, i.e. as evaluator-supplied information rather than as "
        "document evidence"
    )


def test_ground_truth_field_never_reaches_the_prompt(rollout):
    prompt = _render([rollout["row"]], rollout["prediction_dir"])
    for field in ("ground_truth", "gold_answer", '"answers"'):
        assert field not in prompt


# ── arm A vs arm B ─────────────────────────────────────────────────────────────

def test_arm_a_is_unchanged_when_the_hook_is_unset(rollout):
    """With the hook unset the prompt must contain no arm-B section at all."""
    prompt = _render([rollout["row"]], rollout["prediction_dir"])
    assert "Stage and Action Report" not in prompt


def test_arm_b_is_arm_a_plus_the_report(rollout, tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report_text = "# OfficeQA C report\n\n## Findings\n- [ADVICE] operand_period_binding: read the 1964 page."
    (report_dir / "UID0033.md").write_text(report_text, encoding="utf-8")

    arm_a = _render([rollout["row"]], rollout["prediction_dir"])
    arm_b = _render([rollout["row"]], rollout["prediction_dir"], str(report_dir))

    assert "Stage and Action Report" in arm_b
    assert report_text in arm_b
    # Arm B is arm A plus exactly that block: removing it must restore arm A.
    restored = arm_b.replace(f"\n#### Stage and Action Report\n{report_text}\n", "")
    assert restored == arm_a, "arm B differs from arm A by more than the injected report"


def test_a_report_is_generated_when_none_is_cached(rollout, tmp_path):
    """Epoch 2's traces do not exist until the run makes them, so the hook generates and caches the report.

    This replaces an earlier contract ("a missing file is skipped"), which would have left arm B with no report
    on every epoch after the first and so quietly turned it back into arm A.
    """
    empty = tmp_path / "generated_reports"
    empty.mkdir()
    arm_a = _render([rollout["row"]], rollout["prediction_dir"])
    arm_b = _render([rollout["row"]], rollout["prediction_dir"], str(empty))
    assert "Stage and Action Report" in arm_b
    assert (empty / "UID0033.md").is_file(), "the generated report must be cached for the audit to read"
    cached = (empty / "UID0033.md").read_text(encoding="utf-8").strip()
    assert cached and cached in arm_b
    assert arm_b.replace(f"\n#### Stage and Action Report\n{cached}\n", "") == arm_a
    # Deterministic: a second render reuses the cache and produces the same bytes.
    assert _render([rollout["row"]], rollout["prediction_dir"], str(empty)) == arm_b
    assert GOLD not in cached, "the report must not contain the expected answer"


def test_an_item_with_no_trace_file_never_reaches_the_hook(rollout, tmp_path):
    """Upstream already skips an item whose conversation.json is absent, so arm B cannot see one."""
    reports = tmp_path / "reports_absent_trace"
    reports.mkdir()
    absent = dict(rollout["row"], id="UID9999", task_description="no trace was ever written")
    prompt = _render([absent], rollout["prediction_dir"], str(reports))
    assert prompt.strip() == "", "upstream drops items without a recorded conversation"
    assert not (reports / "GENERATION_PROBLEMS.jsonl").exists()


def test_a_trace_with_no_usable_events_is_recorded_not_fatal(rollout, tmp_path):
    """A conversation that exists but yields no steps: the item carries no report and the reason is written down."""
    pred = tmp_path / "predictions" / "UID0077"
    pred.mkdir(parents=True)
    (pred / "target_user_prompt.txt").write_text(USER_PROMPT, encoding="utf-8")
    (pred / "conversation.json").write_text(json.dumps([{"role": "system", "content": "boot"}]), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(json.dumps({"id": "UID0077", "hard": 0}) + "\n", encoding="utf-8")
    reports = tmp_path / "reports_no_steps"
    reports.mkdir()
    row = dict(rollout["row"], id="UID0077")
    prompt = _render([row], str(tmp_path / "predictions"), str(reports))
    assert "Trajectory 1 (id=UID0077)" in prompt, "the trajectory itself is still shown to the analyst"
    assert "Stage and Action Report" not in prompt
    problems = (reports / "GENERATION_PROBLEMS.jsonl").read_text(encoding="utf-8")
    assert "UID0077" in problems and "no conversation events" in problems


def test_a_leaking_packet_stops_the_run(rollout, tmp_path, monkeypatch):
    """An answer-key audit failure is re-raised, not swallowed: arm B adds reasoning, never the target."""
    import oqa_reports

    reports = tmp_path / "reports_leak"
    reports.mkdir()

    def _leak(*_args, **_kwargs):
        raise oqa_reports.AnswerKeyLeak("UID0033: evaluator-only field reached the packet")

    monkeypatch.setattr(oqa_reports, "build_one", _leak)
    reflect = importlib.import_module("skillopt.gradient.reflect")
    monkeypatch.setattr(reflect, "_OFFICEQA_REPORTS_MODULE", oqa_reports, raising=False)
    with pytest.raises(oqa_reports.AnswerKeyLeak):
        _render([rollout["row"]], rollout["prediction_dir"], str(reports))


def test_the_report_itself_is_audited_for_the_key(rollout, tmp_path):
    """oqa_trace.audit_no_gold is what gates report generation; confirm it rejects an evaluator field."""
    import oqa_trace as TR
    packet = TR.build_packet(rollout["prediction_dir"], "UID0033")
    clean = TR.audit_no_gold(packet, os.path.join(rollout["root"], "results.jsonl"))
    assert clean["verdict"] == "PASS", clean
    # The gold does appear in the page text, and that must NOT be treated as a violation.
    assert clean["info"]["gold_present_in_supplied_pages"] is True
    packet["ground_truth"] = GOLD
    assert TR.audit_no_gold(packet, os.path.join(rollout["root"], "results.jsonl"))["verdict"] == "FAIL"


# ── the audit must find a real leak and not invent one ─────────────────────────

def test_the_audit_ignores_a_short_gold_that_occurs_in_document_text(rollout):
    """A gold like "18" occurs by accident hundreds of times in Treasury tables; that is not a leak.

    The first real 24-turn rollout produced four such reports (gold "18" matched 710 times in one prompt),
    all of them substring noise inside the supplied pages and the grep output the actor read.
    """
    import oqa_optimizer_input_audit as AUDIT

    prompt = _render([rollout["row"]], rollout["prediction_dir"])

    # The real gold is in the supplied page AND in the grep output the actor read. Both are legitimate.
    report = AUDIT.audit_text("arm A", prompt, {"UID0033": GOLD})
    assert report["verdict"] == "PASS", report["problems"]
    assert any("none of them in the evaluator-written part" in n for n in report["notes"]), report["notes"]
    assert len(AUDIT.gold_hits(prompt, GOLD)) >= 2

    # A short gold must not match inside a longer number: "338" is not an occurrence of the prediction
    # "2,602,338", and "18" is not one of "1,218".
    assert AUDIT.gold_hits("the figure 2,602,338 appears", "338") == []
    assert AUDIT.gold_hits("the figure 1,218 appears", "18") == []
    assert len(AUDIT.gold_hits("the figure is 646,379.", "646,379")) == 1, "a trailing full stop must not hide it"
    assert len(AUDIT.gold_hits("yield was 11.73% that year", "11.73%")) == 1
    assert AUDIT.audit_text("arm A", prompt, {"UID0033": "338"})["verdict"] == "PASS"


def test_the_audit_catches_a_gold_stated_in_the_failure_reason(rollout):
    """The channel the fix closed: the evaluator's own statement of the target, inside the trajectory header."""
    import oqa_optimizer_input_audit as AUDIT

    leaking = dict(rollout["row"], fail_reason=f"predicted '{PREDICTION}' but expected '{GOLD}'")
    prompt = _render([leaking], rollout["prediction_dir"])
    report = AUDIT.audit_text("arm A", prompt, {"UID0033": GOLD})
    assert report["verdict"] == "FAIL"
    assert any("old leaking fail_reason form" in p for p in report["problems"])
    assert any("the ACTOR was not" in p for p in report["problems"]), report["problems"]


def test_the_audit_catches_a_gold_in_the_hidden_reference(rollout):
    """`reference_text` is evaluator-supplied, so a gold there is a leak even in the fixed fail_reason form."""
    import oqa_optimizer_input_audit as AUDIT

    leaking = dict(rollout["row"], reference_text=f"The correct figure is {GOLD}.")
    prompt = _render([leaking], rollout["prediction_dir"])
    assert "Hidden Reference" in prompt
    report = AUDIT.audit_text("arm A", prompt, {"UID0033": GOLD})
    assert report["verdict"] == "FAIL"
    assert any("the ACTOR was not" in p for p in report["problems"]), report["problems"]


def test_a_gold_in_the_supplied_pages_alone_would_not_hide_a_header_leak(rollout):
    """The old rule ("every occurrence is inside the supplied pages") could not see this case."""
    import oqa_optimizer_input_audit as AUDIT

    leaking = dict(rollout["row"], fail_reason=f"the target value was {GOLD}")
    prompt = _render([leaking], rollout["prediction_dir"])
    assert prompt.count(GOLD) > 1, "the gold is also in the supplied pages, as it normally is"
    report = AUDIT.audit_text("arm A", prompt, {"UID0033": GOLD})
    assert report["verdict"] == "FAIL", "a header leak must be caught even when the pages contain the value too"


def test_a_question_the_actor_was_given_cannot_leak_the_answer(rollout, tmp_path):
    """Two false positives from the real run, both now excluded by construction.

    UID0031's gold was "1990" and the audit "found" it in UID0030's question, "the September 1990 US Treasury
    Monthly Bulletin". One item's question says nothing about another item's answer, and a question the actor
    received verbatim cannot disclose anything to the analyst that the actor did not already have.
    """
    import oqa_optimizer_input_audit as AUDIT

    other = dict(rollout["row"], id="UID0030", task_description="On page 5 of the September 1990 bulletin, how "
                 "many local maxima are there?", fail_reason="predicted '' but the scorer marked it incorrect")
    pred = tmp_path / "predictions" / "UID0030"
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps(CONVERSATION), encoding="utf-8")
    (pred / "target_user_prompt.txt").write_text(USER_PROMPT, encoding="utf-8")
    (pred / "target_system_prompt.txt").write_text("sys", encoding="utf-8")
    for name in ("conversation.json", "target_user_prompt.txt", "target_system_prompt.txt"):
        src = os.path.join(rollout["prediction_dir"], "UID0033", name)
        dst_dir = tmp_path / "predictions" / "UID0033"
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / name).write_text(open(src, encoding="utf-8").read(), encoding="utf-8")

    prompt = _render([dict(rollout["row"]), other], str(tmp_path / "predictions"))
    report = AUDIT.audit_text("arm A", prompt, {"UID0031": "1990", "UID0033": GOLD})
    assert report["verdict"] == "PASS", report["problems"]

    # The gold IS in its own question: still not a leak, because the actor had that question.
    own = dict(rollout["row"], task_description="What happened in 1990?")
    prompt2 = _render([own], rollout["prediction_dir"])
    assert AUDIT.audit_text("arm A", prompt2, {"UID0033": "1990"})["verdict"] == "PASS"

    # But the same value in the failure reason is a leak.
    leaking = dict(rollout["row"], fail_reason="predicted '' but the correct year was 1990")
    prompt3 = _render([leaking], rollout["prediction_dir"])
    assert AUDIT.audit_text("arm A", prompt3, {"UID0033": "1990"})["verdict"] == "FAIL"
