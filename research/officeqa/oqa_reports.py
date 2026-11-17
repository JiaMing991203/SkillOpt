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


class AnswerKeyLeak(RuntimeError):
    """The packet a report would be built from carries evaluator-only information. Never continue past this."""


LC_CONDITION = "LC"


def lc_model_digest(model_path: str) -> str:
    """The frozen learned-contract model's own digest, verified against its content before it is used.

    `oqa_lc_ir.load_frozen` refuses a file whose content does not match the digest it carries, so a report keyed
    by this value cannot be attributed to a model version that was edited after freezing.
    """
    _, IR, _, _ = _lc_modules()
    return str(IR.load_frozen(model_path).get("sha256") or "")


def _lc_modules():
    """Import the learned-contract modules by path (research instrumentation, not a package)."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    mods = []
    for name in ("oqa_lc_ir", "oqa_lc_chain", "oqa_lc_report"):
        spec = importlib.util.spec_from_file_location(name, os.path.join(here, f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(name, mod)
        spec.loader.exec_module(mod)
        mods.append(mod)
    IR, CHAIN, REPORT = mods
    return None, IR, CHAIN, REPORT


def build_one_lc(pred_root: str, item_id: str, question: str, results_path: str,
                 model_path: str) -> tuple[str, dict]:
    """The LEARNED-CONTRACT report for one item: obligations with a resolution, under a frozen model.

    This is the treatment arm's report for the API comparison. It differs from `build_one` in what it says, not in
    what it may read: the same packet, the same answer-key audit, no skill and no held-out data. Two things are
    recorded that the old report had no notion of, and both are what makes a report attributable:
      * `trace_sha256`  the conversation and user prompt the report was derived from;
      * `model_sha256`  the frozen model whose assumptions licensed its bindings.
    A report is valid only for that (attempt, model version) pair, and `verify_report_provenance` checks it.
    """
    _, IR, CHAIN, REPORT = _lc_modules()
    packet = TR.build_packet(pred_root, item_id, question=question)
    if not packet.get("steps"):
        raise ValueError(f"{item_id}: no conversation events were recorded at {pred_root}")
    audit = TR.audit_no_gold(packet, results_path)
    if audit["verdict"] != "PASS":
        raise AnswerKeyLeak(f"{item_id}: {audit.get('problems')}")
    model = IR.load_frozen(model_path)
    chain = CHAIN.build(packet, model)
    text = REPORT.render(chain, model)
    ledger = CHAIN.summary(chain["ledger"])
    record = {
        "id": item_id,
        "condition": LC_CONDITION,
        "report_chars": len(text),
        "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "trace_sha256": trace_digest(pred_root, item_id),
        "model_sha256": str(model.get("sha256") or ""),
        "model_version": model.get("version"),
        "model_assumptions": list(model.get("assumptions") or []),
        "boundary_audit": audit["verdict"],
        "boundary_problems": audit.get("problems", []),
        "obligations": ledger,
        "n_distinct_diagnoses": len(REPORT.group(chain["ledger"])),
    }
    return text, record


def trace_digest(pred_root: str, item_id: str) -> str:
    """Digest of the exact attempt a report must describe: its conversation plus the prompt the actor was given."""
    h = hashlib.sha256()
    for name in ("conversation.json", "target_user_prompt.txt"):
        h.update(b"\x00" + name.encode() + b"\x00")
        try:
            with open(os.path.join(pred_root, item_id, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def verify_report_provenance(records: list, pred_root: str, model_path: str) -> dict:
    """Every report must come from the attempt it claims and from the frozen model version in force.

    Returns {"verdict", "checked", "problems"}. This is the check the stale-report failure of the A40 run would
    have caught: there, eight epoch-2 failures were handed reports built from their epoch-1 trajectories.
    """
    _, IR, _, _ = _lc_modules()
    want_model = str(IR.load_frozen(model_path).get("sha256") or "")
    problems = []
    for rec in records:
        tid = str(rec.get("id"))
        actual = trace_digest(pred_root, tid)
        if rec.get("trace_sha256") != actual:
            problems.append(f"{tid}: report was built from trace {str(rec.get('trace_sha256'))[:12]} but the "
                            f"attempt on disk is {actual[:12]}")
        if rec.get("model_sha256") != want_model:
            problems.append(f"{tid}: report was built under model {str(rec.get('model_sha256'))[:12]} but the "
                            f"frozen model in force is {want_model[:12]}")
    return {"verdict": "FAIL" if problems else "PASS", "checked": len(records), "problems": problems,
            "model_sha256": want_model}


def build_one(pred_root: str, item_id: str, question: str, results_path: str,
              condition: str = "C") -> tuple[str, dict]:
    """Build one item's report text plus its record. Raises AnswerKeyLeak if the packet fails the audit.

    Shared by this script's batch mode and by the opt-in hook in skillopt/gradient/reflect.py, so arm B's report
    is produced by exactly one code path whether it is written ahead of a run or generated during one.
    """
    packet = TR.build_packet(pred_root, item_id, question=question)
    if not packet.get("steps"):
        # A packet with no conversation events cannot support a report. Raising here means the caller records the
        # item as a problem instead of handing the optimizer an empty block and leaving no trace of why.
        raise ValueError(f"{item_id}: no conversation events were recorded at {pred_root}")
    audit = TR.audit_no_gold(packet, results_path)
    if audit["verdict"] != "PASS":
        raise AnswerKeyLeak(f"{item_id}: {audit.get('problems')}")
    result = CHK.report(packet, condition)
    base = CHK.report(packet, "P")
    if condition in ("S", "C"):
        # The containment invariant: a richer condition may never drop what the plain view already said.
        CHK.structural_check(base, result, f"{item_id}: {condition} over P")
    text = CHK.render(result, packet)
    record = {
        "id": item_id,
        "report_chars": len(text),
        "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "boundary_audit": audit["verdict"],
        "boundary_problems": audit.get("problems", []),
        "findings": [{"cat": f["cat"], "kind": f["kind"], "layer": f["layer"], "problem": f["problem"],
                      "first_step": f["first"]} for f in result["findings"]],
        "n_advice": sum(1 for f in result["findings"] if f["kind"] == "advice"),
        "n_warning": sum(1 for f in result["findings"] if f["kind"] == "warning"),
    }
    return text, record


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
            text, record = build_one(pred_root, item_id, row.get("question", ""), results_path, args.condition)
            path = os.path.join(args.out, f"{item_id}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            record["scored_correct"] = not failed
            record["report_file"] = os.path.basename(path)
            manifest["items"].append(record)
        except AnswerKeyLeak as exc:
            manifest["items"].append({"id": item_id, "boundary_audit": "FAIL", "boundary_problems": [str(exc)],
                                      "report_chars": 0, "n_advice": 0, "n_warning": 0, "findings": []})
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
