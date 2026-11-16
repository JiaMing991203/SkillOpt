"""Build the ACTUAL optimizer input for both arms and audit it for answer-key leakage.

What the user asked for: "Keep expected answers inside the scorer and verify the actual optimizer inputs in both
arms." This does not inspect the code and reason about it - it calls SkillOpt's own
`reflect.fmt_minibatch_trajectories` on the real rollout records, once with the arm-B hook off and once with it on,
and then audits the two prompts that come out. Those are the bytes the optimizer would receive.

Three questions, answered separately:

  1. LEAKAGE. Does the prompt contain the answer key as evaluator-supplied information? The gold VALUE usually
     appears somewhere in the supplied document pages - that is the evidence the question is asked about, and it
     carries no information about which answer is correct. What must be absent is the evaluator's own statement of
     the target: the old `fail_reason` form "predicted 'X' but expected 'GOLD'", or any evaluator-only field name.
  2. ARM DIFFERENCE. Is arm A's prompt byte-identical to upstream's, and is arm B's prompt exactly arm A's plus
     the stage-and-action reports? If arm B differed in any other way, the comparison would not isolate the report.
  3. COST. How large is each prompt, in characters and in an estimated token count? This is what decides whether
     the optimizer half of the comparison is affordable, and it is the number the budget is built from.

usage: python3 oqa_optimizer_input_audit.py <rollout out_root> --report-dir <dir> --out audit.json
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

EVALUATOR_ONLY_FIELDS = ("ground_truth", "gold_answer", "answers", "correct_label", "em", "f1")
OLD_LEAK_PATTERN = re.compile(r"but expected\b", re.I)

# The one finding that mattered on the first real traces: after `grep` returns its 20-line cap, an "it is not in
# the document" conclusion is unsound. The question the user asked is whether the WRITTEN WARNING reaches arm A
# or only arm B, which is a different question from whether the underlying evidence reaches both.
GREP_CAP_WARNING = "which is the tool's cap"
GREP_CAP_LINE_LIMIT = 20


def _load_rows(out_root: str) -> list[dict]:
    rows = []
    with open(os.path.join(out_root, "results.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _render(items: list[dict], prediction_dir: str, report_dir: str | None) -> str:
    """Call SkillOpt's own formatter, with the arm-B hook set or unset."""
    if report_dir:
        os.environ["OFFICEQA_STAGE_REPORT_DIR"] = report_dir
    else:
        os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
    reflect = importlib.import_module("skillopt.gradient.reflect")
    importlib.reload(reflect)  # the hook is read per call, but reload keeps this robust to import caching
    return reflect.fmt_minibatch_trajectories(items, prediction_dir)


def _estimate_tokens(text: str) -> int:
    """A deliberately rough upper-ish estimate: ~3.6 characters per token for English prose with tables.

    Stated as an estimate because the real tokenizer is the optimizer provider's, which we do not run here. The
    budget built from it should be treated as an order-of-magnitude figure, not a quote.
    """
    return int(len(text) / 3.6)


def audit_text(label: str, text: str, golds: dict) -> dict:
    """Leakage audit of one rendered prompt."""
    problems, notes = [], []
    if OLD_LEAK_PATTERN.search(text):
        excerpt = text[max(0, OLD_LEAK_PATTERN.search(text).start() - 60):][:200]
        problems.append(f"the old leaking fail_reason form is present: ...{excerpt!r}")
    for field in EVALUATOR_ONLY_FIELDS:
        if f'"{field}"' in text or f"'{field}'" in text:
            problems.append(f"evaluator-only field name {field!r} appears in the prompt")

    # Where does each gold value appear? Inside the supplied pages is expected; anywhere else is suspicious.
    page_section = "\n".join(re.findall(r"(?s)#### Target User Prompt\n(.*?)(?=\n#### |\n### |\Z)", text))
    for item_id, gold in golds.items():
        gold = str(gold or "").strip()
        if not gold:
            continue
        total = text.count(gold)
        in_pages = page_section.count(gold)
        if total and total == in_pages:
            notes.append(f"{item_id}: gold value occurs {total}x, all inside the supplied document pages "
                         f"(expected - that is the evidence)")
        elif total:
            problems.append(f"{item_id}: gold value occurs {total}x, of which {total - in_pages} OUTSIDE the "
                            f"supplied pages - inspect those occurrences")
    return {"label": label, "verdict": "FAIL" if problems else "PASS", "problems": problems, "notes": notes,
            "chars": len(text), "estimated_tokens": _estimate_tokens(text),
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


def grep_cap_audit(arm_a: str, arm_b: str, minibatch: list[dict], prediction_dir: str) -> dict:
    """Where does the grep-cap warning appear, and where does the evidence behind it appear?

    Three separate counts, because they answer three different questions:
      traces_at_the_cap   how many of these trajectories actually ran a `grep` that returned its 20-line cap.
                          Computed from the recorded trace, not from a report.
      warning_in_arm_*    how often the written warning sentence appears in each arm's rendered prompt.
      evidence_in_arm_*   how often the capped observation's own lines appear in each arm's prompt. Both arms
                          read the same trace, so this is expected to be equal: the difference between the arms
                          is the interpretation, not the information.
    """
    import oqa_trace as TR

    at_cap = []
    for row in minibatch:
        item_id = str(row.get("id"))
        try:
            packet = TR.build_packet(prediction_dir, item_id, question=row.get("question", ""))
        except Exception:  # noqa: BLE001
            continue
        for step in packet["steps"]:
            if step.get("kind") != "tool" or step.get("tool") != "grep":
                continue
            lines = [ln for ln in (step.get("obs") or "").splitlines() if ln.strip()]
            if len(lines) >= GREP_CAP_LINE_LIMIT:
                at_cap.append({"id": item_id, "step": step["i"], "pattern": (step.get("args") or {}).get("pattern"),
                               "returned_lines": len(lines), "first_line": lines[0][:120]})
    evidence_a = sum(1 for e in at_cap if e["first_line"][:60] in arm_a)
    evidence_b = sum(1 for e in at_cap if e["first_line"][:60] in arm_b)
    return {
        "warning_sentence": GREP_CAP_WARNING,
        "traces_at_the_cap": len(at_cap),
        "capped_calls": at_cap,
        "warning_in_arm_a": arm_a.count(GREP_CAP_WARNING),
        "warning_in_arm_b": arm_b.count(GREP_CAP_WARNING),
        "evidence_in_arm_a": evidence_a,
        "evidence_in_arm_b": evidence_b,
        "reading": ("the written warning appears only in arm B, while the capped grep output it is derived from "
                    "appears in both arms"
                    if arm_b.count(GREP_CAP_WARNING) and not arm_a.count(GREP_CAP_WARNING) and evidence_a
                    else "see the counts"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_root")
    ap.add_argument("--report-dir", required=True, help="arm B's stage-report directory (from oqa_reports.py)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--minibatch-size", type=int, default=8,
                    help="failures per analyst call, to size one real prompt (config gradient.minibatch_size)")
    args = ap.parse_args()

    rows = _load_rows(args.out_root)
    failures = [r for r in rows if not float(r.get("hard") or 0)]
    golds = {str(r.get("id")): r.get("ground_truth") for r in rows}
    prediction_dir = os.path.join(args.out_root, "predictions")

    minibatch = failures[:args.minibatch_size]
    arm_a = _render(minibatch, prediction_dir, None)
    arm_b = _render(minibatch, prediction_dir, args.report_dir)

    audit_a = audit_text("arm A (original SkillOpt)", arm_a, golds)
    audit_b = audit_text("arm B (+ stage and action report)", arm_b, golds)

    # Arm B must be arm A plus the reports and nothing else. The injected block is removed by its EXACT bytes,
    # read back from the report files, rather than by a regex over the surrounding text: the block is followed by
    # the trajectory lines, not by another heading, so a lookahead-based strip silently eats real content and
    # would make this check meaningless.
    added_blocks = re.findall(r"\n#### Stage and Action Report\n", arm_b)
    stripped_b = arm_b
    for row in minibatch:
        report_path = os.path.join(args.report_dir, f"{row.get('id')}.md")
        if not os.path.isfile(report_path):
            continue
        with open(report_path, encoding="utf-8") as f:
            injected = f.read().strip()
        if injected:
            stripped_b = stripped_b.replace(f"\n#### Stage and Action Report\n{injected}\n", "", 1)
    identical_after_strip = stripped_b == arm_a
    diff_sample = []
    if not identical_after_strip:
        diff = list(difflib.unified_diff(arm_a.splitlines(), stripped_b.splitlines(), lineterm="", n=1))
        diff_sample = diff[:40]

    report = {
        "out_root": os.path.abspath(args.out_root),
        "grep_cap": grep_cap_audit(arm_a, arm_b, minibatch, prediction_dir),
        "rollout_items": len(rows),
        "failures": len(failures),
        "minibatch_items": [str(r.get("id")) for r in minibatch],
        "arm_a": audit_a,
        "arm_b": audit_b,
        "arm_difference": {
            "stage_reports_injected": len(added_blocks),
            "arm_b_is_arm_a_plus_reports_only": identical_after_strip,
            "added_chars": audit_b["chars"] - audit_a["chars"],
            "added_estimated_tokens": audit_b["estimated_tokens"] - audit_a["estimated_tokens"],
            "unexpected_diff_sample": diff_sample,
        },
        "verdict": "PASS" if (audit_a["verdict"] == "PASS" and audit_b["verdict"] == "PASS"
                              and identical_after_strip) else "FAIL",
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    printable = {k: v for k, v in report.items() if k not in ("arm_difference", "grep_cap")}
    printable["arm_difference"] = {k: v for k, v in report["arm_difference"].items()
                                   if k != "unexpected_diff_sample"}
    printable["grep_cap"] = {k: v for k, v in report["grep_cap"].items() if k != "capped_calls"}
    print(json.dumps(printable, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
