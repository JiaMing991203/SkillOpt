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


def test_a_missing_report_file_is_skipped_not_fatal(rollout, tmp_path):
    empty = tmp_path / "empty_reports"
    empty.mkdir()
    arm_a = _render([rollout["row"]], rollout["prediction_dir"])
    arm_b = _render([rollout["row"]], rollout["prediction_dir"], str(empty))
    assert arm_b == arm_a, "a partially generated report set must not change the prompt for items without one"


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
