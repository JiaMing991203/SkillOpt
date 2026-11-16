"""Generate the stage-and-action reports that arm B adds to the optimizer's prompt.

Arm A (original SkillOpt) and arm B (SkillOpt + stage and action feedback) see the SAME traces. The only
difference is that arm B's analyst prompt carries one of these reports per failed item, appended by the opt-in
hook in skillopt/gradient/reflect.py (`OFFICEQA_STAGE_REPORT_DIR`).

The reports are produced here, outside SkillOpt, so that:
  * they are deterministic - no model call, so the comparison is not confounded by a second sampled model;
  * they are auditable - the file handed to the optimizer is on disk and can be read;
  * they cannot see the answer key - the packet is built from the recorded user prompt and conversation only, and
    `oqa_trace.audit_no_gold` is run on every one of them.

Writes <out>/<task id>.md for each failed item, plus a manifest with the audit verdicts and the digest of each
report, so the exact bytes the optimizer saw can be reconstructed later.

usage: python3 oqa_reports.py <rollout out_root> --out <dir> [--condition C] [--all-items]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_check as CHK  # noqa: E402
import oqa_trace as TR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_root", help="rollout directory containing results.jsonl and predictions/")
    ap.add_argument("--out", required=True, help="directory to write <id>.md reports into")
    ap.add_argument("--condition", default="C", choices=["P", "S", "C"],
                    help="C = stage handoffs + action contracts (arm B); S = stages only; P = plain (control)")
    ap.add_argument("--all-items", action="store_true",
                    help="also report on items the scorer accepted (default: failures only, which is what the "
                         "analyst minibatch receives)")
    args = ap.parse_args()

    results_path = os.path.join(args.out_root, "results.jsonl")
    rows = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    os.makedirs(args.out, exist_ok=True)
    pred_root = os.path.join(args.out_root, "predictions")
    manifest = {"out_root": os.path.abspath(args.out_root), "condition": args.condition,
                "report_dir": os.path.abspath(args.out), "items": [], "problems": []}

    for row in rows:
        item_id = str(row.get("id"))
        failed = not float(row.get("hard") or 0)
        if not failed and not args.all_items:
            continue
        try:
            packet = TR.build_packet(pred_root, item_id, question=row.get("question", ""))
            audit = TR.audit_no_gold(packet, results_path)
            result = CHK.report(packet, args.condition)
            # Both arms must rest on the same trace: the containment invariant is checked so a report can never
            # quietly drop what the plain view already said.
            base = CHK.report(packet, "P")
            if args.condition in ("S", "C"):
                CHK.structural_check(base, result, f"{item_id}: {args.condition} over P")
            text = CHK.render(result, packet)
            path = os.path.join(args.out, f"{item_id}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            manifest["items"].append({
                "id": item_id,
                "scored_correct": not failed,
                "report_file": os.path.basename(path),
                "report_chars": len(text),
                "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "boundary_audit": audit["verdict"],
                "boundary_problems": audit.get("problems", []),
                "findings": [{"cat": f["cat"], "kind": f["kind"], "layer": f["layer"], "problem": f["problem"],
                              "first_step": f["first"]} for f in result["findings"]],
                "n_advice": sum(1 for f in result["findings"] if f["kind"] == "advice"),
                "n_warning": sum(1 for f in result["findings"] if f["kind"] == "warning"),
            })
        except Exception as exc:  # noqa: BLE001
            manifest["problems"].append({"id": item_id, "error": f"{type(exc).__name__}: {exc}"})

    manifest["totals"] = {
        "reports": len(manifest["items"]),
        "boundary_audit_pass": sum(1 for i in manifest["items"] if i["boundary_audit"] == "PASS"),
        "boundary_audit_not_pass": sum(1 for i in manifest["items"] if i["boundary_audit"] != "PASS"),
        "items_with_advice": sum(1 for i in manifest["items"] if i["n_advice"]),
        "total_report_chars": sum(i["report_chars"] for i in manifest["items"]),
    }
    with open(os.path.join(args.out, "REPORT_MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps(manifest["totals"], indent=2))
    if manifest["problems"]:
        print(f"{len(manifest['problems'])} item(s) failed; see REPORT_MANIFEST.json", file=sys.stderr)
        return 1
    # A boundary failure must stop the pipeline: the whole point is that arm B adds reasoning, not the answer key.
    if manifest["totals"]["boundary_audit_not_pass"]:
        print("REFUSED: at least one report's packet failed the answer-key audit", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
