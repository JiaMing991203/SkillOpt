#!/usr/bin/env python3
"""Freeze, check prefix-only, repair on a counterexample, recheck, and compare against what already existed.

Amendment B-1, the demonstration half. Order, enforced by the code rather than by care:

  1. `freeze`   compile the validated proposal into a model and freeze it under its digest.
  2. `check`    process the CHECKING records in order. For each, the prediction and the stage
                requirement are written out BEFORE any later step is read (`oqa_cc_check.Revealer`
                raises if that order is broken).
  3. `repair`   if a checking record produces a real counterexample - the conditions were met and the
                conclusion is contradicted - narrow the condition, record the reason, keep the old
                version, and RECHECK the new version against the LEARNING records as well, so a repair
                cannot silently break what the hypothesis was induced from.
  4. `compare`  put the new report beside what the campaign already had for the same attempt: the
                ordinary SkillOpt analysis (the step's own `trajectory_digest.json` failure patterns)
                and the historical contract report (`arm_C/stage_reports/<item>.<key>.md`).

The comparison verdicts are deliberately narrow, because the question is what a reader can conclude:
    adds_a_valid_diagnosis / earlier_or_more_precise / repeats_what_was_available /
    false_alarm / remains_unknown
and the excerpts are printed so the judgement can be checked rather than taken.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_check as CHK  # noqa: E402
import oqa_cc_ir as IR  # noqa: E402
import oqa_trace as TR  # noqa: E402

RULE = "OQA-CC-1"


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _packet(pred_dir: str, item: str, epoch: int):
    pk = TR.build_packet(pred_dir, item)
    pk["trace_key"] = f"{item}/e{epoch}"
    pk["trace_sha256"] = TR.trace_digest(pred_dir, item)
    pk["collection"] = os.path.abspath(pred_dir)
    return pk


def cmd_freeze(proposal_path: str, records_path: str, out_dir: str) -> dict:
    prop = json.load(open(proposal_path, encoding="utf-8"))
    rec = json.load(open(records_path, encoding="utf-8"))
    comp = prop.get("compiled") or {}
    if comp.get("problems"):
        raise RuntimeError(f"the proposal did not validate: {comp['problems']}")
    c = comp["contract"]
    m = IR.model([c], epoch=2, scope="arm C epoch-2 training traces of 4 items (frozen split)",
                 learned_from=[{"trace_key": f"{r['item']}/e{r['epoch']}",
                                "trace_sha256": r["trace_sha256"]} for r in rec["learning"]])
    m["records_sha256"] = rec["records_sha256"]
    m["proposal_sha256"] = IR.sha(IR.canon(prop.get("proposal", {}).get("parsed") or {}))
    path = os.path.join(out_dir, "CC_MODEL_v1.json")
    digest = IR.freeze(m, path)
    # Recomputed HERE with the evaluator in force now, not echoed from the proposal file. The score
    # stored with the proposal was taken before `nearer_scale_between` had an evaluator, so echoing it
    # would report all-UNKNOWN for a contract that now decides.
    recomputed = {"HOLDS": 0, "UNKNOWN": 0, "CONFLICT": 0, "VIOLATED": 0, "cells": 0,
                  "cells_whose_own_table_could_not_be_rebound": 0}
    for r in rec["learning"]:
        pk = _packet(r["predictions"], r["item"], r["epoch"])
        steps, supplied = pk.get("steps") or [], pk.get("oracle_pages") or []
        tabs = CHK.tables_in(steps, supplied)
        for cell in CHK.cells_in(steps, supplied, ("flat_header_authoritative",))[:60]:
            # B-2: the cell's OWN table, by (source, table index). B-1 took any table sharing the
            # cell's scale, which is not the same claim.
            table = CHK.table_of(cell, tabs)
            if table is None:
                recomputed["cells_whose_own_table_could_not_be_rebound"] += 1
                continue
            recomputed["cells"] += 1
            recomputed[CHK.evaluate_binding(c, table, cell)["truth"]] += 1
    return {"rule": RULE, "utc": _utc(), "model": path, "sha256": digest,
            "contract": IR.render(c), "score_on_learning": recomputed,
            "score_at_proposal_time": comp.get("score"),
            "score_note": ("recomputed with the evaluator in force at freeze time; the proposal-time "
                           "score was taken before `nearer_scale_between` had one"),
            "withdrawal_reason": comp.get("withdrawal_reason")}


def cmd_check(model_path: str, records_path: str, which: str, out_dir: str) -> dict:
    m = IR.load_frozen(model_path)
    rec = json.load(open(records_path, encoding="utf-8"))
    results = []
    for r in rec[which]:
        pk = _packet(r["predictions"], r["item"], r["epoch"])
        res = CHK.check_attempt(pk, m)
        res["epoch"] = r["epoch"]
        results.append(res)
    doc = {"rule": RULE, "utc": _utc(), "model_sha256": m.get("sha256"), "which": which,
           "results": results, "score": CHK.score(results),
           "lean_evidence": CHK.export_for_lean(results)}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"CC_CHECK_{which}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def find_counterexample(check_doc: dict) -> dict | None:
    """A REAL counterexample: the conditions were met and the observation contradicts the conclusion.

    A cell the conditions do not cover is not one - the contract is silent about it. That distinction is
    the whole reason for having a condition, and it is what the restricted form could not draw.
    """
    for r in check_doc.get("results") or []:
        for p in r.get("predictions") or []:
            b = p.get("binding") or {}
            if b.get("truth") == IR.CONFLICT and b.get("conflict"):
                return {"item": r.get("item"), "trace_key": r.get("trace_key"),
                        "operand": (p.get("operand") or {}).get("raw"),
                        "cell": p.get("cell"), "conflict": b["conflict"],
                        "why": ("the table states one scale and the cell's own column header states "
                                "another, so the table-level binding cannot be asserted for this cell")}
    return None


def cmd_repair(model_path: str, check_path: str, records_path: str, out_dir: str) -> dict:
    m = IR.load_frozen(model_path)
    chk = json.load(open(check_path, encoding="utf-8"))
    ce = find_counterexample(chk)
    if ce is None:
        return {"rule": RULE, "utc": _utc(), "repaired": False,
                "why": ("no counterexample in the permitted checking records: no checked cell had its "
                        "conditions met and its conclusion contradicted"),
                "score": chk.get("score")}
    c = IR.active(m)[0]
    extra = IR.clause("column_header_states_unit", ["header", "scale"], value=False)
    old, new = IR.repair(c, extra, reason=(
        f"counterexample {ce['trace_key']} operand {ce['operand']}: {ce['why']}"), counterexamples=[ce])
    m2 = IR.model([old, new], epoch=3,
                  scope=m.get("evidence_scope", "") + " + one counterexample from the checking records",
                  learned_from=m.get("evidence_attempts") or [])
    m2["records_sha256"] = m.get("records_sha256")
    path = os.path.join(out_dir, "CC_MODEL_v2.json")
    digest = IR.freeze(m2, path, parent_sha256=m.get("sha256"))
    rec = json.load(open(records_path, encoding="utf-8"))
    relearn = []
    for r in rec["learning"]:
        pk = _packet(r["predictions"], r["item"], r["epoch"])
        relearn.append(CHK.check_attempt(pk, m2))
    return {"rule": RULE, "utc": _utc(), "repaired": True, "counterexample": ce,
            "model": path, "sha256": digest, "parent_sha256": m.get("sha256"),
            "v1": IR.render(old), "v2": IR.render(new),
            "recheck_on_learning_records": CHK.score(relearn),
            "recheck_note": ("the repaired version is re-run over the LEARNING records, so a narrowed "
                             "condition that broke what the hypothesis was induced from would show here")}


def cmd_compare(campaign: str, check_path: str, out_dir: str, arm: str = "C",
                records_path: str | None = None) -> dict:
    chk = json.load(open(check_path, encoding="utf-8"))
    # WHICH ARM each checking record came from. The v2 split spans arms B and C, and comparing a B
    # record against arm C's reports would put the prototype beside the wrong historical report.
    arm_of = {}
    if records_path and os.path.isfile(records_path):
        rec = json.load(open(records_path, encoding="utf-8"))
        for r in (rec.get("checking") or []) + (rec.get("learning") or []):
            arm_of[str(r.get("item"))] = str(r.get("arm") or arm)
    out = []
    for r in chk.get("results") or []:
        item = r.get("item")
        arm = arm_of.get(str(item), arm)
        epoch = r.get("epoch") or 3
        # 1. the ordinary SkillOpt analysis for that epoch, as the campaign recorded it
        td_path = os.path.join(campaign, f"arm_{arm}", "steps", f"step_{epoch:04d}",
                               "trajectory_digest.json")
        ordinary = []
        if os.path.isfile(td_path):
            for pat in json.load(open(td_path, encoding="utf-8")).get("failure_patterns") or []:
                if item in (pat.get("task_ids") or []):
                    ordinary.append(pat.get("pattern"))
        # 2. the historical contract report for the same attempt
        hist = {"found": False}
        gen = os.path.join(campaign, f"arm_{arm}", "stage_reports", "GENERATED.jsonl")
        if os.path.isfile(gen):
            for line in open(gen, encoding="utf-8"):
                if not line.strip():
                    continue
                g = json.loads(line)
                if g.get("id") == item and f"step_{epoch:04d}" in str(g.get("prediction_dir", "")):
                    hist = {"found": True, "obligations": g.get("obligations"),
                            "n_distinct_diagnoses": g.get("n_distinct_diagnoses"),
                            "report_sha256": g.get("report_sha256")}
                    for f in glob.glob(os.path.join(campaign, f"arm_{arm}", "stage_reports",
                                                    f"{item}.*.md")):
                        with open(f, "rb") as fh:
                            raw = fh.read()
                        if hashlib.sha256(raw).hexdigest() == g.get("report_sha256"):
                            text = raw.decode("utf-8", "replace")
                            hist["unit_match_lines"] = [ln for ln in text.splitlines()
                                                        if "unit_match" in ln][:6]
                            break
                    break
        # 3. the prototype's own finding, WITH the witnesses a reader needs to judge it
        new = []
        for p in r.get("predictions") or []:
            res = p.get("resolution") or {}
            op = p.get("operand") or {}
            cell = p.get("cell") or {}
            new.append({"operand": op.get("raw"),
                        "operand_verdict": op.get("verdict"), "operand_reason": op.get("reason"),
                        "match_status": p.get("match_status"),
                        "cell": {k: cell.get(k) for k in ("raw", "row_label", "column", "scale",
                                                          "col_index", "header_known")},
                        "table_key": p.get("table_key"), "table_scale": p.get("table_scale"),
                        "binding": (p.get("binding") or {}).get("truth"),
                        "conversion": (p.get("conversion") or {}).get("verdict"),
                        "state": res.get("state"), "outcome": res.get("outcome"),
                        "why": res.get("why"), "required_exp": res.get("required_exp"),
                        "used_exp": res.get("used_exp"),
                        "extraction_error": p.get("outcome") if res == {} else None,
                        "committed_at_step": p.get("committed_at_step")})
        verdict, basis = _verdict(new, ordinary, hist)
        out.append({"item": item, "arm": arm, "trace_key": r.get("trace_key"), "epoch": epoch,
                    "ordinary_skillopt_analysis": ordinary,
                    "historical_contract_report": hist,
                    "prototype_report": new, "verdict": verdict, "verdict_basis": basis,
                    "extraction": r.get("extraction"),
                    "problems": r.get("problems")})
    doc = {"rule": RULE, "utc": _utc(), "comparisons": out,
           "verdict_counts": {v: sum(1 for o in out if o["verdict"] == v) for v in
                              {o["verdict"] for o in out}}}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "CC_COMPARE.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def _well_bound(n: dict) -> bool:
    """Does this finding rest on a binding a reader would accept?

    B-2. The instruction: an automated "more precise" label requires inspection of the actual operand
    and evidence binding. This is that inspection, mechanised: the operand must be a MONETARY one (not
    a year, a date or an unsettled four-digit number), the cell match must be unambiguous, the cell must
    be a data cell with a bound header, and the binding must hold. A finding that fails any of these is
    not more precise than anything - it is a false alarm waiting to be printed.
    """
    cell = n.get("cell") or {}
    return (n.get("operand_verdict") == "MONETARY"
            and n.get("match_status") in ("unique_cell_match", "repeated_identical_cell_match")
            and int(cell.get("col_index") or 0) > 0
            and bool(cell.get("header_known"))
            and n.get("binding") == "HOLDS")


def _verdict(new: list, ordinary: list, hist: dict) -> tuple:
    """The verdict and the basis for it. Lexical overlap is NOT used to decide a finding was acted on."""
    if not new:
        return "remains_unknown", {"why": "no prediction was committed for this attempt"}
    findings = [n for n in new if n.get("state") in ("VIOLATED", "OUTSTANDING")]
    wb = [n for n in findings if _well_bound(n)]
    weak = [n for n in findings if not _well_bound(n)]
    hist_lines = hist.get("unit_match_lines") or []
    basis = {"n_predictions": len(new), "n_findings": len(findings),
             "n_well_bound": len(wb), "n_weakly_bound": len(weak),
             "weak_reasons": sorted({f"{n.get('operand_verdict')}/{n.get('match_status')}/"
                                     f"header_known={(n.get('cell') or {}).get('header_known')}/"
                                     f"binding={n.get('binding')}" for n in weak}),
             "historical_unit_match_lines": hist_lines[:4],
             "requires_inspection": True}
    if not findings:
        return "remains_unknown", {**basis, "why": "every resolution is UNKNOWN, REOPENED or DISCHARGED"}
    if not wb:
        return "false_alarm", {**basis,
                               "why": ("the only findings rest on an operand or a cell binding that "
                                       "does not survive inspection")}
    violated = [n for n in wb if n.get("state") == "VIOLATED"]
    if violated:
        if any("VIOLATED" in str(ln) for ln in hist_lines):
            return "repeats_what_was_available", {**basis,
                                                  "why": "the historical report already has a VIOLATED "
                                                         "unit_match line for this attempt"}
        return "adds_a_valid_diagnosis", {**basis,
                                          "why": "a well-bound VIOLATED obligation with no equivalent "
                                                 "line in the historical report"}
    if any("OUTSTANDING" in str(ln) for ln in hist_lines):
        return "repeats_what_was_available", {**basis,
                                              "why": "the historical report already has an OUTSTANDING "
                                                     "unit_match line for this attempt"}
    return "earlier_or_more_precise", {**basis,
                                       "why": ("a well-bound OUTSTANDING obligation, committed before "
                                               "the later steps were read, with no equivalent "
                                               "historical line")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--proposal", required=True)
    f.add_argument("--records", required=True)
    f.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--model", required=True)
    c.add_argument("--records", required=True)
    c.add_argument("--which", default="checking", choices=("learning", "checking"))
    c.add_argument("--out", required=True)
    r = sub.add_parser("repair")
    r.add_argument("--model", required=True)
    r.add_argument("--check", required=True)
    r.add_argument("--records", required=True)
    r.add_argument("--out", required=True)
    p = sub.add_parser("compare")
    p.add_argument("--campaign", required=True)
    p.add_argument("--check", required=True)
    p.add_argument("--arm", default="C")
    p.add_argument("--records")
    p.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.cmd == "freeze":
        doc = cmd_freeze(args.proposal, args.records, args.out)
        print(f"rule {RULE}  frozen {doc['sha256'][:16]}  -> {doc['model']}")
        print(doc["contract"])
        print(f"  score on the learning records: {doc['score_on_learning']}")
    elif args.cmd == "check":
        doc = cmd_check(args.model, args.records, args.which, args.out)
        print(f"rule {RULE}  {args.which}: {len(doc['results'])} attempt(s), outcomes {doc['score']}")
        for r in doc["results"]:
            print(f"  {r['item']} ({r.get('trace_key')}): {len(r.get('predictions') or [])} "
                  f"prediction(s) {r.get('problems') or ''}")
            for p in (r.get("predictions") or [])[:3]:
                res = p.get("resolution") or {}
                print(f"     operand {(p.get('operand') or {}).get('raw')} binding "
                      f"{(p.get('binding') or {}).get('truth')} -> {res.get('state')} "
                      f"({res.get('outcome')}) required 10^{res.get('required_exp')} "
                      f"used 10^{res.get('used_exp')}")
        print(f"wrote {doc['written_to']}")
    elif args.cmd == "repair":
        doc = cmd_repair(args.model, args.check, args.records, args.out)
        print(f"rule {RULE}  repaired={doc['repaired']}")
        if doc["repaired"]:
            print(f"  counterexample: {doc['counterexample']['trace_key']} "
                  f"operand {doc['counterexample']['operand']}")
            print(f"  v2 frozen {doc['sha256'][:16]} parent {str(doc['parent_sha256'])[:16]}")
            print(doc["v2"])
            print(f"  recheck on the learning records: {doc['recheck_on_learning_records']}")
        else:
            print(f"  {doc['why']}")
        with open(os.path.join(args.out, "CC_REPAIR.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, default=str)
    else:
        doc = cmd_compare(args.campaign, args.check, args.out, args.arm, args.records)
        print(f"rule {RULE}  verdicts {doc['verdict_counts']}")
        for o in doc["comparisons"]:
            b = o.get("verdict_basis") or {}
            print(f"  arm {o.get('arm')} {o['item']}: {o['verdict']}  "
                  f"({b.get('n_findings', 0)} finding(s), {b.get('n_well_bound', 0)} well bound)")
            print(f"      {b.get('why', '')}")
            for n in o["prototype_report"][:3]:
                print(f"      operand {n.get('operand')!r} [{n.get('operand_verdict')}] cell "
                      f"{(n.get('cell') or {}).get('raw')!r} col={(n.get('cell') or {}).get('column')!r} "
                      f"-> {n.get('state')} req 10^{n.get('required_exp')} used 10^{n.get('used_exp')}")
        print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
