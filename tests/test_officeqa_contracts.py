"""Stage A software checks for the OfficeQA contract prototype (constructed inputs only).

These tests establish what the code does on inputs whose intended diagnosis is known in advance. They are not
evidence about the actor, the benchmark or the research hypothesis (research/officeqa/oqa_cases.py says so too).

Run: pytest tests/test_officeqa_contracts.py   (pure Python; no network, no model, no SkillOpt pipeline)
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "research", "officeqa"))

import oqa_cases as CASES  # noqa: E402
import oqa_check as CHK  # noqa: E402
import oqa_obligations as OB  # noqa: E402
import oqa_trace as TR  # noqa: E402


# ── obligations ────────────────────────────────────────────────────────────────

def test_obligations_read_only_explicit_cues():
    o = OB.derive("What was the change in total debt between June 1970 and December 1964, in millions of dollars?")
    assert [(p["year"], p["month"]) for p in o["periods"]] == [(1970, 6), (1964, 12)]
    assert o["scale"]["scale"] == "millions" and o["scale"]["basis"] == "STATED"
    assert o["operation"]["op"] == "difference" and o["operation"]["arity"] == 2
    assert o["operation_varies"] == "period"


def test_obligations_unknown_is_not_guessed():
    o = OB.derive("What does the bulletin report about ownership of public debt?")
    assert o["periods"] == []
    assert o["scale"]["basis"] == "UNKNOWN" and o["scale"]["scale"] is None
    assert o["operation"]["basis"] == "UNKNOWN" and o["operation"]["op"] == "none"


def test_fiscal_year_and_bare_year():
    o = OB.derive("How much was outstanding in fiscal year 1950?")
    assert o["periods"][0]["fiscal"] is True and o["periods"][0]["year"] == 1950


# ── evidence ledger ────────────────────────────────────────────────────────────

def test_ledger_carries_scale_context_and_years():
    page = CASES.PAGE_1970
    facts = CHK.numbers_in(page)
    row = [f for f in facts if f["raw"] == "81,243"]
    assert row, "the figure on the June 1970 row must be extracted"
    assert row[0]["scale_context"] == "millions"
    assert 1970 in row[0]["years"]


def test_ledger_availability_is_prefix_only():
    case = CASES.case_operand_from_wrong_period()
    facts = CHK.build_ledger(case)
    assert all(f["available_from"] == 0 for f in facts if f["source"]["kind"] == "oracle_page")
    tool_facts = [f for f in facts if f["source"]["kind"] != "oracle_page"]
    assert all(f["available_from"] >= 1 for f in tool_facts)
    assert CHK.available(facts, 0) == [f for f in facts if f["available_from"] == 0]


def test_error_observations_contribute_no_facts():
    case = CASES.case_disallowed_path_and_grep_cap()
    facts = CHK.build_ledger(case)
    assert not [f for f in facts if f["source"].get("step") == 1], \
        "a '[read error: ...]' observation must not become numeric evidence"


def test_navigation_references_are_not_operands():
    """Regression from job 66196051: "read around line 976-980" must not become an ungrounded operand."""
    body = "I see the table FO-2 is on pages 860 and 917, but I need the chart on page 21. Let me read around line 976-980 again."
    mentions = CHK.numbers_in(body)
    kept = [n["raw"] for n in mentions if not CHK.looks_like_location_reference(n, body)
            and not CHK.looks_like_year(n)]
    kept = [n["raw"] for n in mentions if CHK.looks_like_figure(n) and not CHK.looks_like_year(n)
            and not CHK.looks_like_location_reference(n, body)]
    for raw in ("860", "917", "976", "980", "21"):
        assert raw not in kept, f"{raw} is a navigation reference, not an operand (kept={kept})"


def test_a_real_figure_after_a_location_word_is_still_kept():
    """The exclusion must not swallow figures: only the number right after the location word is a reference."""
    body = "On line 412 the table gives 646,379 for April 1977."
    mentions = CHK.numbers_in(body)
    kept = [n["raw"] for n in mentions if CHK.looks_like_figure(n) and not CHK.looks_like_year(n)
            and not CHK.looks_like_location_reference(n, body)]
    assert "646,379" in kept and "412" not in kept, kept


def test_file_period_from_name():
    assert CHK.file_period("treasury_bulletin_1944_01.txt") == {"year": 1944, "month": 1}
    assert CHK.file_period("not_a_bulletin.txt") is None


# ── report structure: S contains P, C contains S ───────────────────────────────

@pytest.mark.parametrize("factory", CASES.ALL, ids=[f.__name__ for f in CASES.ALL])
def test_conditions_are_nested(factory):
    case = factory()
    p = CHK.report(case, "P")
    s = CHK.report(case, "S")
    c = CHK.report(case, "C")
    assert CHK.structural_check(p, s, "S over P")["shared"] == len(p["findings"])
    assert CHK.structural_check(s, c, "C over S")["shared"] == len(s["findings"])


@pytest.mark.parametrize("factory", CASES.ALL, ids=[f.__name__ for f in CASES.ALL])
def test_expected_diagnoses(factory):
    case = factory()
    expect = case["expect"]
    p = CHK.report(case, "P")
    s = CHK.report(case, "S")
    c = CHK.report(case, "C")
    p_cats = {f["cat"] for f in p["findings"] if f["kind"] in {"advice", "warning"}}
    assert p_cats == expect["P"], f"P categories {p_cats} != expected {expect['P']}"
    s_cats = {f["cat"] for f in s["findings"] if f["kind"] in {"advice", "warning"}} - p_cats
    assert s_cats == expect["S_adds"], f"S added {s_cats} != expected {expect['S_adds']}"
    if "C_adds" in expect:
        c_cats = {f["cat"] for f in c["findings"] if f["kind"] in {"advice", "warning"}} - p_cats - s_cats
        assert c_cats == expect["C_adds"], f"C added {c_cats} != expected {expect['C_adds']}"


def test_notes_are_not_advice():
    """A3 section 3.2: a NOTE is neutral information and must never be scored as advice."""
    case = CASES.case_scale_not_converted()
    s = CHK.report(case, "S")
    notes = [f for f in s["findings"] if f["cat"] == "select_source_prefilled"]
    assert notes and notes[0]["kind"] == "note"
    cmp_ps = CHK.compare(CHK.report(case, "P"), s)
    assert "select_source" in cmp_ps["NOTE_ONLY"]
    assert "select_source" not in cmp_ps["ADDITIONAL"]


def test_wrong_period_operand_is_an_additional_advice_for_S():
    case = CASES.case_operand_from_wrong_period()
    p, s = CHK.report(case, "P"), CHK.report(case, "S")
    assert not [f for f in p["findings"] if f["kind"] in {"advice", "warning"}], \
        "the plain report has nothing to say when both operands are grounded and a pair reproduces the answer"
    diff = CHK.compare(p, s)
    assert "operand_period_binding" in diff["ADDITIONAL"]
    assert "operand_period_binding" in diff["MORE_ACTIONABLE"]


def test_hallucinated_value_is_caught_by_the_plain_report():
    """The honest negative case: the connected report only localises and anticipates what P already says.

    Because the comparison unit is the PROBLEM, saying something different about `answer_grounding` is credited as
    EARLIER / MORE_SPECIFIC, never as an additional discovery.
    """
    case = CASES.case_value_not_in_evidence()
    p, s = CHK.report(case, "P"), CHK.report(case, "S")
    assert "ungrounded_answer_value" in {f["cat"] for f in p["findings"]}
    diff = CHK.compare(p, s)
    assert "answer_grounding" not in diff["ADDITIONAL"]
    assert "answer_grounding" in diff["EARLIER"]
    specific = [e for e in diff["MORE_SPECIFIC"] if e["problem"] == "answer_grounding"]
    assert specific and "operand_provenance_missing" in specific[0]["categories"]


def test_scale_check_needs_the_named_assumption():
    """C's scale finding depends on contract table.scale.1; S must not make it."""
    case = CASES.case_scale_not_converted()
    s, c = CHK.report(case, "S"), CHK.report(case, "C")
    assert not [f for f in s["findings"] if f["cat"] == "scale_conversion_missing"]
    hit = [f for f in c["findings"] if f["cat"] == "scale_conversion_missing"]
    assert hit and "table_scale_applies_to_rows" in hit[0]["text"]


def test_report_renders_without_findings():
    case = CASES.case_scale_not_converted()
    text = CHK.render(CHK.report(case, "P"), case)
    assert "Task obligations" in text and "none: every check" in text


# ── trace extraction and the information boundary ──────────────────────────────

_USER_PROMPT = """## Question
What was the gross Federal debt outstanding at the end of September 1988?

## Oracle Parsed Pages
The following content is pre-parsed from the oracle OfficeQA source page(s). Treat it as primary document evidence.

### treasury_bulletin_1988_09.txt page 13
Source URL: https://fraser.stlouisfed.org/title/treasury-bulletin-407/september-1988?page=13

TABLE FD-2. - FEDERAL DEBT OUTSTANDING
(In millions of dollars)
September 1988       2,602,338

## Candidate Files
- /data/officeqa/transformed/treasury_bulletin_1988_09.txt

## Source Hints
- https://fraser.stlouisfed.org/title/treasury-bulletin-407/september-1988?page=13
"""

_CONVERSATION = [
    {"role": "user", "content": _USER_PROMPT},
    {"type": "message", "content": "I will search the candidate file."},
    {"type": "tool_call",
     "cmd": "grep(pattern='gross federal debt', path='/data/officeqa/transformed/treasury_bulletin_1988_09.txt')",
     "obs": "88: Gross Federal debt 2,602,338"},
    {"type": "message", "content": "The value is 2,602,338. <answer>2602338</answer>"},
]


@pytest.fixture()
def recorded_run(tmp_path):
    pred = tmp_path / "predictions" / "UID0046"
    pred.mkdir(parents=True)
    (pred / "target_user_prompt.txt").write_text(_USER_PROMPT, encoding="utf-8")
    (pred / "conversation.json").write_text(json.dumps(_CONVERSATION), encoding="utf-8")
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({
        "id": "UID0046", "ground_truth": "2602338", "hard": 0, "soft": 1.0,
        "fail_reason": "predicted '2602338' but expected '2,602,338 million'",
    }) + "\n", encoding="utf-8")
    return str(tmp_path / "predictions"), str(results)


def test_packet_extraction(recorded_run):
    pred_dir, _ = recorded_run
    packet = TR.build_packet(pred_dir, "UID0046")
    assert packet["question"].startswith("What was the gross Federal debt")
    assert packet["candidate_files"] == ["treasury_bulletin_1988_09.txt"]
    assert len(packet["oracle_pages"]) == 1
    page = packet["oracle_pages"][0]
    assert page["file"] == "treasury_bulletin_1988_09.txt" and page["page"] == 13
    assert "2,602,338" in page["text"] and page["url"].endswith("page=13")
    tools = [s for s in packet["steps"] if s["kind"] == "tool"]
    assert len(tools) == 1 and tools[0]["tool"] == "grep"
    assert tools[0]["args"]["pattern"] == "gross federal debt"
    assert packet["final_answer"] == "2602338"


def test_packet_steps_are_ordered_and_runnable(recorded_run):
    pred_dir, _ = recorded_run
    packet = TR.build_packet(pred_dir, "UID0046")
    assert [s["i"] for s in packet["steps"]] == [1, 2, 3]
    for condition in ("P", "S", "C"):
        result = CHK.report(packet, condition)
        assert result["condition"] == condition


def test_information_boundary_passes_on_a_clean_packet(recorded_run):
    pred_dir, results = recorded_run
    packet = TR.build_packet(pred_dir, "UID0046")
    audit = TR.audit_no_gold(packet, results)
    assert audit["verdict"] == "PASS", audit
    assert audit["info"]["gold_present_in_supplied_pages"] is False


def test_information_boundary_catches_an_evaluator_field(recorded_run):
    pred_dir, results = recorded_run
    packet = TR.build_packet(pred_dir, "UID0046")
    packet["fail_reason"] = "predicted '2602338' but expected '2,602,338 million'"
    audit = TR.audit_no_gold(packet, results)
    assert audit["verdict"] == "FAIL"
    assert any("fail_reason" in p for p in audit["problems"])


def test_information_boundary_unknown_when_item_absent(recorded_run):
    pred_dir, results = recorded_run
    packet = TR.build_packet(pred_dir, "UID0046")
    packet["id"] = "UID9999"
    assert TR.audit_no_gold(packet, results)["verdict"] == "UNKNOWN"
