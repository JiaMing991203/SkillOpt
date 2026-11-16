"""Gate the 12-question rollout on the first three questions.

The user's instruction: "Use the first three questions to check document loading, tool execution where requested,
and trace and score recording. If those checks pass, finish all 12."

What each check means, and why it is or is not fatal:

  documents loaded      The harness must have supplied the parsed gold page(s) for the item, and the candidate
                        file list must be non-empty. If this fails the corpus or the split is wrong and the
                        remaining questions would be wasted. FATAL.
  tool execution        Only checked WHERE REQUESTED: if the target asked for a tool, at least one observation
                        must be real output rather than an error. A target that asks for no tool is not a failure
                        - the supplied pages may be enough - so that is recorded, not failed. A target whose every
                        tool call errored is FATAL, because the loop is broken.
  trace recording       conversation.json, target_system_prompt.txt and target_user_prompt.txt must exist and be
                        non-empty, and the conversation must contain at least one event. FATAL.
  score recording       `hard` must be 0 or 1 and `soft` a finite number in [0, 1]. FATAL if absent or malformed -
                        the gate consumes `hard`, so a missing score silently corrupts the comparison.
  answer-key isolation  No rollout record's `fail_reason` may state the expected answer, and every contract-input
                        packet must pass oqa_trace.audit_no_gold. FATAL: this runs before any optimizer call, and
                        a leak would invalidate both arms.

Exit codes: 0 all checks passed; 1 a fatal check failed; 2 the rollout is missing or unreadable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_check as CHK  # noqa: E402
import oqa_trace as TR  # noqa: E402

LEAK_PATTERN = re.compile(r"but expected\b", re.I)


def check_item(out_root: str, row: dict) -> dict:
    item_id = str(row.get("id"))
    pred_dir = os.path.join(out_root, "predictions", item_id)
    entry: dict = {"id": item_id, "fatal": [], "notes": []}

    # --- trace recording ---
    for name in ("conversation.json", "target_system_prompt.txt", "target_user_prompt.txt"):
        path = os.path.join(pred_dir, name)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            entry["fatal"].append(f"missing or empty {name}")
    if entry["fatal"]:
        return entry

    packet = TR.build_packet(os.path.join(out_root, "predictions"), item_id, question=row.get("question", ""))
    entry["steps"] = len(packet["steps"])
    if not packet["steps"]:
        entry["fatal"].append("conversation.json contains no events")

    # --- documents loaded ---
    entry["supplied_pages"] = len(packet["oracle_pages"])
    entry["supplied_page_chars"] = sum(len(p["text"]) for p in packet["oracle_pages"])
    entry["candidate_files"] = len(packet["candidate_files"])
    if not packet["oracle_pages"]:
        entry["fatal"].append("no parsed source page was supplied; the corpus or the split is wrong")
    elif entry["supplied_page_chars"] < 200:
        entry["fatal"].append(f"supplied page text is only {entry['supplied_page_chars']} chars; the parsed JSON "
                              f"for this bulletin is probably missing or empty")
    if not packet["candidate_files"]:
        entry["fatal"].append("the candidate-file list is empty; the document root did not resolve")

    # --- tool execution, where requested ---
    tool_steps = [s for s in packet["steps"] if s["kind"] == "tool"]
    errored = [s for s in tool_steps
               if (s["obs"] or "").startswith("[read error") or (s["obs"] or "").startswith("[grep error")
               or (s["obs"] or "").startswith("[tool error")]
    entry["tool_calls"] = len(tool_steps)
    entry["tool_calls_errored"] = len(errored)
    entry["tool_calls_by_name"] = {}
    for step in tool_steps:
        entry["tool_calls_by_name"][step["tool"]] = entry["tool_calls_by_name"].get(step["tool"], 0) + 1
    if not tool_steps:
        entry["notes"].append("the target requested no tool; the supplied pages may have been sufficient, so this "
                              "is recorded rather than failed")
    elif len(errored) == len(tool_steps):
        entry["fatal"].append(f"all {len(tool_steps)} tool call(s) returned an error: "
                              f"{[(s['cmd'][:60], s['obs'][:60]) for s in errored][:3]}")
    elif errored:
        entry["notes"].append(f"{len(errored)} of {len(tool_steps)} tool calls errored")

    # --- score recording ---
    hard, soft = row.get("hard"), row.get("soft")
    try:
        hard_f, soft_f = float(hard), float(soft)
        if hard_f not in (0.0, 1.0):
            entry["fatal"].append(f"hard is {hard!r}, expected 0 or 1")
        if not (0.0 <= soft_f <= 1.0):
            entry["fatal"].append(f"soft is {soft!r}, expected a value in [0, 1]")
        entry["hard"], entry["soft"] = hard_f, round(soft_f, 4)
    except (TypeError, ValueError):
        entry["fatal"].append(f"score missing or not numeric: hard={hard!r} soft={soft!r}")

    # --- answer-key isolation ---
    fail_reason = str(row.get("fail_reason") or "")
    entry["fail_reason"] = fail_reason[:160]
    if LEAK_PATTERN.search(fail_reason):
        entry["fatal"].append("fail_reason states the expected answer (the upstream leak is back)")
    audit = TR.audit_no_gold(packet, os.path.join(out_root, "results.jsonl"))
    entry["boundary_audit"] = audit["verdict"]
    if audit["verdict"] == "FAIL":
        entry["fatal"].append(f"contract-input packet failed the answer-key audit: {audit['problems']}")

    # --- informational: what the reports would have to work with ---
    facts = CHK.build_ledger(packet)
    obligations = CHK.report(packet, "P")["obligations"]
    state = CHK.stage_state(packet, facts, obligations)
    entry["numeric_facts_available"] = len(facts)
    entry["operands_written_down"] = len(state["operands"])
    entry["arithmetic_steps"] = len(state["computations"])
    entry["final_answer"] = packet["final_answer"][:80]
    entry["obligations"] = {"periods": [p["text"] for p in obligations["periods"]],
                            "scale": obligations["scale"]["scale"] or "UNKNOWN",
                            "operation": obligations["operation"]["op"]}
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_root")
    ap.add_argument("--expect", type=int, default=3, help="how many items must be present")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    results_path = os.path.join(args.out_root, "results.jsonl")
    if not os.path.isfile(results_path):
        print(f"no results.jsonl in {args.out_root}", file=sys.stderr)
        return 2
    rows = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    report = {"out_root": os.path.abspath(args.out_root), "items_found": len(rows), "expected": args.expect,
              "checks": [], "fatal": []}
    if len(rows) < args.expect:
        report["fatal"].append(f"only {len(rows)} item(s) recorded, expected {args.expect}")

    for row in rows[:args.expect]:
        entry = check_item(args.out_root, row)
        report["checks"].append(entry)
        for problem in entry["fatal"]:
            report["fatal"].append(f"{entry['id']}: {problem}")

    report["summary"] = {
        "items_checked": len(report["checks"]),
        "scored_correct": sum(1 for c in report["checks"] if c.get("hard") == 1.0),
        "items_with_supplied_pages": sum(1 for c in report["checks"] if c.get("supplied_pages")),
        "items_requesting_tools": sum(1 for c in report["checks"] if c.get("tool_calls")),
        "total_tool_calls": sum(c.get("tool_calls", 0) for c in report["checks"]),
        "tool_calls_errored": sum(c.get("tool_calls_errored", 0) for c in report["checks"]),
        "boundary_audit_pass": sum(1 for c in report["checks"] if c.get("boundary_audit") == "PASS"),
    }
    report["verdict"] = "FAIL" if report["fatal"] else "PASS"
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({"verdict": report["verdict"], "summary": report["summary"],
                      "fatal": report["fatal"]}, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
