"""Deterministic inventory of what an OfficeQA rollout actually recorded, per question.

This is the readout the three-question baseline check is for. For every item it reports, from the run directory
alone:
  * the tool requests the target made, by tool, with their arguments;
  * how much evidence each request returned, and whether it returned an error or no matches;
  * what the harness supplied before the target acted (parsed pages, candidate files, source hints) - which is what
    decides how much of the retrieval problem is left to the actor;
  * the intermediate values the target wrote down, and whether each is grounded in evidence that was available at
    that point;
  * the native score (`hard` = exact match after the adapter's normalization, `soft` = token F1), copied verbatim;
  * the size of the first user message and of the trajectory, which is what an optimizer prompt would cost;
  * the information-boundary audit of the contract input (oqa_trace.audit_no_gold).

It makes no judgement about the research question. Reports are produced by oqa_check.py.

usage: python3 oqa_inventory.py <rollout out_root> --out inventory.json [--render-reports DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_check as CHK  # noqa: E402
import oqa_trace as TR  # noqa: E402


def _read_results(out_root: str) -> list[dict]:
    path = os.path.join(out_root, "results.jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def inventory_one(out_root: str, row: dict, render_dir: str = "") -> dict:
    item_id = str(row.get("id"))
    pred_root = os.path.join(out_root, "predictions")
    packet = TR.build_packet(pred_root, item_id, question=row.get("question", ""))
    facts = CHK.build_ledger(packet)
    obligations = CHK.report(packet, "P")["obligations"]
    state = CHK.stage_state(packet, facts, obligations)

    tool_steps = [s for s in packet["steps"] if s["kind"] == "tool"]
    by_tool: dict[str, int] = {}
    for step in tool_steps:
        by_tool[step["tool"]] = by_tool.get(step["tool"], 0) + 1

    user_prompt_path = os.path.join(pred_root, item_id, "target_user_prompt.txt")
    entry = {
        "id": item_id,
        "category": row.get("task_type", ""),
        "question_chars": len(row.get("question", "")),
        # what the harness supplied before the actor acted
        "supplied": {
            "parsed_pages": [{"file": p["file"], "page": p["page"], "chars": len(p["text"])}
                             for p in packet["oracle_pages"]],
            "parsed_page_chars_total": sum(len(p["text"]) for p in packet["oracle_pages"]),
            "candidate_files": packet["candidate_files"],
            "source_hints": len(packet["source_hints"]),
            "oracle_pages_included_per_record": row.get("oracle_parsed_pages_included"),
            "oracle_page_chars_per_record": row.get("oracle_parsed_pages_chars"),
        },
        # what the actor did
        "tool_requests": [
            {"step": s["i"], "tool": s["tool"], "args": s["args"],
             "obs_chars": len(s["obs"] or ""),
             "obs_lines": len([ln for ln in (s["obs"] or "").splitlines() if ln.strip()]),
             "obs_is_error": (s["obs"] or "").startswith("[read error") or (s["obs"] or "").startswith("[grep error"),
             "obs_no_matches": (s["obs"] or "").startswith("[no matches]")}
            for s in tool_steps
        ],
        "tool_request_counts": by_tool,
        "messages": len([s for s in packet["steps"] if s["kind"] == "message"]),
        # intermediate values the actor wrote down
        "stated_operands": [{"raw": o["raw"], "step": o["step"], "grounded": bool(o["provenance"]),
                             "sources": sorted({p["source"].get("file") or p["source"]["kind"]
                                                for p in o["provenance"]})}
                            for o in state["operands"]],
        "computed_values": [{"raw": c["raw"], "step": c["step"], "rule": c["rule"]} for c in state["computed"]],
        "arithmetic_steps": [c["step"] for c in state["computations"]],
        "final_answer": packet["final_answer"],
        # obligations read from the question
        "obligations": {
            "periods": [p["text"] for p in obligations["periods"]],
            "scale": obligations["scale"]["scale"] or "UNKNOWN",
            "operation": obligations["operation"]["op"],
            "operation_basis": obligations["operation"]["basis"],
        },
        # native scoring, copied verbatim
        "native_score": {"hard": row.get("hard"), "soft": row.get("soft"),
                         "predicted_answer": row.get("predicted_answer"),
                         "agent_ok": row.get("agent_ok"), "n_turns": row.get("n_turns"),
                         "last_finish_reason": row.get("last_finish_reason")},
        # prompt sizes: what an optimizer prompt would cost for this item
        "sizes": {
            "user_prompt_chars": os.path.getsize(user_prompt_path) if os.path.isfile(user_prompt_path) else None,
            "evidence_returned_chars": sum(len(s["obs"] or "") for s in tool_steps),
            "numeric_facts_available": len(facts),
        },
        "boundary_audit": TR.audit_no_gold(packet, os.path.join(out_root, "results.jsonl")),
    }
    # fail_reason is the one record field that contains the answer key, so it is reported as a flag, never copied.
    fail_reason = str(row.get("fail_reason") or "")
    entry["fail_reason_present"] = bool(fail_reason)
    entry["fail_reason_contains_answer_key"] = "but expected" in fail_reason

    if render_dir:
        os.makedirs(render_dir, exist_ok=True)
        for condition in ("P", "S", "C"):
            result = CHK.report(packet, condition)
            with open(os.path.join(render_dir, f"{item_id}_{condition}.md"), "w", encoding="utf-8") as f:
                f.write(CHK.render(result, packet))
        base = CHK.report(packet, "P")
        mid = CHK.report(packet, "S")
        top = CHK.report(packet, "C")
        CHK.structural_check(base, mid, f"{item_id}: S over P")
        CHK.structural_check(mid, top, f"{item_id}: C over S")
        entry["report_comparison"] = {"P_to_S": CHK.compare(base, mid), "S_to_C": CHK.compare(mid, top)}
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--render-reports", default="", help="also write per-item P/S/C reports into this directory")
    args = ap.parse_args()

    rows = _read_results(args.out_root)
    items = []
    problems = []
    for row in rows:
        try:
            items.append(inventory_one(args.out_root, row, args.render_reports))
        except Exception as exc:  # noqa: BLE001
            problems.append({"id": str(row.get("id")), "error": f"{type(exc).__name__}: {exc}"})
    report = {
        "out_root": os.path.abspath(args.out_root),
        "items": items,
        "problems": problems,
        "totals": {
            "items": len(items),
            "hard_sum": sum(float(i["native_score"]["hard"] or 0) for i in items),
            "tool_requests": sum(len(i["tool_requests"]) for i in items),
            "items_with_no_tool_request": sum(1 for i in items if not i["tool_requests"]),
            "items_with_supplied_pages": sum(1 for i in items if i["supplied"]["parsed_pages"]),
            "boundary_audit_pass": sum(1 for i in items if i["boundary_audit"]["verdict"] == "PASS"),
            "fail_reasons_containing_answer_key": sum(1 for i in items
                                                      if i["fail_reason_contains_answer_key"]),
        },
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report["totals"], indent=2))
    if problems:
        print(f"{len(problems)} item(s) could not be inventoried; see {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
