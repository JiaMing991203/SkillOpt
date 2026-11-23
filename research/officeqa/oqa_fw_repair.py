#!/usr/bin/env python3
"""Repair and earlier-evidence regression checking for the OfficeQA slice, with a labelled fixture.

TWO SEPARATE THINGS, and they are kept apart on purpose.

  1. THE RECORDED PATH (`natural`). What happens on the real 48 attempts when the over-general form of
     the rule meets the records. In this corpus the gate REFUSES it on first contact - both of its
     counterexample columns are in the same attempt as its supporting ones - so the sequence
     "accepted, later refuted, then repaired" does not occur naturally here. That is reported as what
     it is, not dressed up: the narrowing is a response to a REFUSAL rather than to a later refutation.

  2. THE CONSTRUCTED FIXTURE (`fixture`). Two synthetic records, written here and labelled in every
     artifact, that produce the sequence the machinery exists for: a rule is accepted on the first
     record carrying a superfluous conjunct, the second record refutes it, and the repair that passes
     is the DELETION of that conjunct. This is the shape of ALFWorld's one accepted RP-1 repair (both
     attempts dropped the same monotone conjunct), and it is the repair the OfficeQA CC/LN generation
     could not express at all, because its `repair` could only append a condition.

     A FIXTURE RESULT IS NOT AN EMPIRICAL FINDING. Nothing from here is counted as evidence about the
     Treasury corpus. It tests code.

THE REGRESSION CHECK is `fw_repair.regression`: the repaired rule is re-scored over the evidence the
original was accepted on, and a candidate that loses support or gains a counterexample anywhere is
reported as a regression even when the gate would accept it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import fw_check as K  # noqa: E402
import fw_ir as IR  # noqa: E402
import fw_learn as LN  # noqa: E402
import fw_repair as RP  # noqa: E402
import oqa_fw_domain as DOM  # noqa: E402
import oqa_fw_extract as E  # noqa: E402
import oqa_fw_run as RUN  # noqa: E402

FIXTURE_TAG = "CONSTRUCTED FIXTURE - not evidence about the corpus"

# ── the fixture records ───────────────────────────────────────────────────────
# Column 0 is the row-label column and the flat rendering names it differently ("Period" against the
# split form's "Classification"), so index 0 DISAGREES. That is deliberate: it keeps the fixture from
# having a column whose sub-label cell is empty and which nevertheless agrees, which would refute the
# `iff` rule on the first record and so test nothing.
# Two pipe renderings of the same table per record: a SPLIT one (parent row, sub-label row) and a FLAT
# one ("Parent > Child" in a single header row). They share two labelled data rows with three values
# each, which is what `oqa_fw_extract._cross_check` requires before it will treat the flat form as
# ground truth for the split form.
_SPLIT_F1 = """(in thousands of dollars)
| Classification | Receipts | Receipts | Outlays |
|  | Gross | Refunds | Total |
| 1961 | 1,000 | 200 | 800 |
| 1962 | 2,000 | 400 | 1,600 |
"""
_FLAT_F1 = """| Period > Period | Receipts > Gross | Receipts > Refunds | Outlays > Total |
| 1961 | 1,000 | 200 | 800 |
| 1962 | 2,000 | 400 | 1,600 |
"""
# F2 is the SAME SHAPE with NO stated scale. Its columns still agree with its flat rendering, so a rule
# that requires a stated scale has its consequence true where its conditions are false: under
# `direction: "iff"` that is a counterexample, and the only repair that fixes it is DELETING the
# scale conjunct.
_SPLIT_F2 = """| Classification | Holdings | Holdings | Total |
|  | Bills | Notes | Total |
| 1970 | 3,000 | 500 | 3,500 |
| 1971 | 6,000 | 900 | 6,900 |
"""
_FLAT_F2 = """| Period > Period | Holdings > Bills | Holdings > Notes | Total > Total |
| 1970 | 3,000 | 500 | 3,500 |
| 1971 | 6,000 | 900 | 6,900 |
"""


def _fixture_record(eid: str, split: str, flat: str) -> dict:
    steps = [
        {"i": 0, "kind": "tool", "cmd": "read(path='fixture/split.txt', offset=0)", "obs": split,
         "text": ""},
        {"i": 1, "kind": "tool", "cmd": "read(path='fixture/flat.txt', offset=0)", "obs": flat,
         "text": ""},
    ]
    ents = E.document_entities(steps)
    ents = E.operand_entities(steps, ents)
    return {"episode_id": eid, "item": eid, "pass": 0, "role": "fixture", "task": "fixture",
            "task_type": None, "steps": steps, "entities": ents, "asked_scale": "millions",
            "trace_sha256": "fixture", "frozen_trace_sha256": "fixture", "fixture": True}


def _rule(with_scale: bool, direction: str = "iff") -> dict:
    conds = [[{"atom": "col_child_label_present", "args": ["?s"], "value": True}]]
    c = {"kind": "concludes", "scope": "any", "subject": {"?s": "column"}, "subject_scope": "once",
         "conditions": conds, "direction": direction,
         "consequence": {"atom": "col_label_agrees_flat", "args": ["?s"], "value": True},
         "context": {"task_types": []}}
    if with_scale:
        c["conditions"] = [[{"atom": "col_of", "args": ["?s", "?t"], "value": True}],
                           [{"atom": "scale_stated", "args": ["?t"], "value": True}]] + conds
        c["exists"] = {"?t": "table"}
    return c


def run_fixture(out_dir: str) -> dict:
    f1 = _fixture_record("FIXTURE/F1_scaled", _SPLIT_F1, _FLAT_F1)
    f2 = _fixture_record("FIXTURE/F2_unscaled", _SPLIT_F2, _FLAT_F2)
    doc = {"label": FIXTURE_TAG, "phase": "repair_fixture", "records": {}}
    for r in (f1, f2):
        doc["records"][r["episode_id"]] = {
            "tables": {k: {"header_form": v["header_form"], "scale": v["scale"],
                           "width": v["width"], "n_rows": v["n_rows"],
                           "flat_pair_step": v.get("flat_pair_step"),
                           "flat_pair_overlap": v.get("flat_pair_overlap")}
                       for k, v in r["entities"]["tables"].items()},
            "columns": {k: {"child": v["child"], "child_label_present": v["child_label_present"],
                            "agrees_flat": v["agrees_flat"], "flat_leaf": v["flat_leaf"]}
                        for k, v in r["entities"]["columns"].items()}}

    # step 1: accept the rule WITH the superfluous conjunct on F1 alone.
    dom1, ev1 = RUN.evidence_of([f1])
    m = IR.empty_model(dom1)
    m["assumptions"] = ["index_alignment", "table_scale_applies_to_rows"]
    prop = {"contract_ops": [{"op": "add", "contract": _rule(True),
                              "reason": "accepted on the first fixture record"}]}
    m, dec = LN.apply_proposal(m, prop, ev1, 1, "fixture", dom1)
    doc["batch_1"] = {"evidence": [f1["episode_id"]], "decisions": dec,
                      "active": [{"id": c["id"], "rendered": IR.render(c, dom1),
                                  "evidence": c.get("evidence")} for c in IR.active(m)]}
    if not IR.active(m, "concludes"):
        doc["outcome"] = "the fixture rule was not accepted on F1; the fixture needs fixing"
        return doc
    cid = IR.active(m, "concludes")[0]["id"]

    # step 2: add F2. The gate's own re-scoring refutes the rule.
    dom2, ev2 = RUN.evidence_of([f1, f2])
    m2, dec2 = LN.apply_proposal(m, {"contract_ops": [{"op": "keep", "target": cid}]},
                                 ev2, 2, "fixture", dom2)
    tgt = LN._find(m2, cid)
    doc["batch_2"] = {"evidence": [f1["episode_id"], f2["episode_id"]], "decisions": dec2,
                      "status_after": (tgt or {}).get("status"),
                      "refute_reason": (tgt or {}).get("refute_reason"),
                      "evidence_after": (tgt or {}).get("evidence")}

    # step 3: every repair the framework can express, each through the ordinary gate.
    att = RP.attempt(m2, cid, ev2, dom2, 3, "fixture",
                     extra_conjuncts=[[{"atom": "col_label_present", "args": ["?s"], "value": True}]])
    doc["repair"] = {
        "candidates": [{"shape": c["shape"], "why": c["why"], "decision": c["decision"],
                        "gate_why": c["gate_why"], "rendered": c["rendered"],
                        "evidence": c["evidence"], "regression": c.get("regression")}
                       for c in att["candidates"]],
        "accepted_index": att["accepted"],
        "accepted_shape": (att["candidates"][att["accepted"]]["shape"]
                           if att["accepted"] is not None else None)}
    m3 = att["model"]
    doc["model_after_repair"] = [{"id": c["id"], "status": c.get("status"),
                                 "rendered": IR.render(c, dom2), "version": c.get("version"),
                                 "parent": c.get("parent"), "operation": c.get("operation"),
                                 "evidence": c.get("evidence")} for c in m3["contracts"]]
    doc["outcome"] = ("the repair that passed the gate with a clean regression was "
                      f"{doc['repair']['accepted_shape']!r}")
    return doc


# ── the recorded path ─────────────────────────────────────────────────────────
def run_natural(args) -> dict:
    fz, recs, mismatched = RUN.load_all(args.freeze)
    learn = [r for r in recs if r["role"] == "learn" and r["episode_id"] != RUN.RESERVED]
    dom, ev = RUN.evidence_of(learn)
    m = IR.empty_model(dom)
    m["assumptions"] = ["index_alignment", "table_scale_applies_to_rows"]
    general = {"kind": "concludes", "scope": "any", "subject": {"?s": "column"},
               "subject_scope": "once",
               "conditions": [[{"atom": "col_of", "args": ["?s", "?t"], "value": True}],
                              [{"atom": "header_split", "args": ["?t"], "value": True}]],
               "exists": {"?t": "table"},
               "consequence": {"atom": "col_label_agrees_flat", "args": ["?s"], "value": True},
               "context": {"task_types": []}}
    m1, dec = LN.apply_proposal(m, {"contract_ops": [
        {"op": "add", "contract": general,
         "reason": "the most general form: a split header's index merge names its columns"}]},
        ev, 1, "search", dom)
    d = [x for x in dec if x.get("type") == "contract"][-1]
    out = {"phase": "repair_natural", "evidence": sorted(r["episode_id"] for r in learn),
           "general_form": {"rendered": IR.render(IR.normalize(general), dom),
                            "decision": d.get("decision"), "gate_why": d.get("why"),
                            "evidence": d.get("evidence")}}
    # the narrowing, offered as a repair of the refused proposal, through the same gate
    narrowed = IR.narrow(IR.normalize(general),
                         [{"atom": "col_child_label_present", "args": ["?s"], "value": True}])
    sc_g = K.score(IR.normalize(general), ev, m["assumptions"], [], dom)
    sc_n = K.score(narrowed, ev, m["assumptions"], [], dom)
    out["narrowing"] = {
        "rendered": IR.render(narrowed, dom),
        "gate_why": K.gate(sc_n, "concludes"),
        "regression_vs_general": RP.regression(IR.normalize(general), narrowed, ev,
                                               m["assumptions"], [], dom),
        "general_counterexamples": sc_g["counter"],
        "general_counter_witness": {k: sc_g["counter_witness"][k] for k in sc_g["counter"][:4]
                                    if k in sc_g["counter_witness"]}}
    out["note"] = ("In this corpus the over-general form is REFUSED on first contact rather than "
                   "accepted and later refuted: its supporting and its refuting columns are in the "
                   "same attempt. So the narrowing here answers a refusal. The accept-then-refute "
                   "sequence, and the DELETION repair, are exercised on the labelled fixture.")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--freeze", default=RUN.FREEZE)
    a = ap.parse_args(argv)
    doc = {"schema": RUN.SCHEMA, "rule": RUN.RULE, "phase": "repair",
           "natural": run_natural(a), "fixture": run_fixture(a.out)}
    RUN.seal(doc)
    RUN.jdump(doc, os.path.join(a.out, "FW_REPAIR.json"))
    print(f"[repair] {doc['self_sha256'][:16]}  ->  {a.out}")
    print("  natural: general form", doc["natural"]["general_form"]["decision"],
          "| narrowing gate", doc["natural"]["narrowing"]["gate_why"] or "PASSES")
    print("  fixture:", doc["fixture"].get("outcome"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
