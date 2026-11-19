#!/usr/bin/env python3
"""Baseline against repaired extraction, over the same 24 saved records. Rule OQA-BIND-1.

The comparison the B-4 amendment has to answer: does recovering the column LEAF from a split header let
the frozen contract produce findings? The amendment predicts NO, because the frozen hypothesis
`scale_binds` is about the unit and a leaf is column/series identity. This file measures it rather than
restating the prediction, and reports an increase in bindings as a number to be explained, never as
success on its own.

WHAT IT KEEPS APART, because collapsing any of them would flatter the repair:

    operands / matches          what the extractor found, before any binding question
    supported bindings          bindings whose truth is HOLDS - the only kind that may witness
    remaining unknown causes    the named reason each binding is still undetermined
    findings                    what the checker would actually report
    well bound                  a finding resting on a binding a reader would accept
    the leaf, separately        column/series identity, which does NOT make a finding well bound

usage:
  oqa_bind_compare.py --baseline-check <json> --baseline-audit <json>
                      --repaired-check <json> --repaired-audit <json>
                      --records <results.jsonl> --out <dir>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os

RULE = "OQA-BIND-1"
SCHEMA = "lso.officeqa_bind_compare.v1"


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _rows(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                out[str(r.get("id"))] = r
    return out


def _audit_index(audit: dict) -> dict:
    """item -> the per-operand picture the bind audit computed."""
    index = {}
    for group in (audit or {}).get("groups") or []:
        for r in group.get("results") or []:
            cands = r.get("candidates") or []
            mon = [c for c in cands if c.get("verdict") == "MONETARY"]
            causes, match, states, p1, truths = {}, {}, {}, {}, {}
            leaves = 0
            for c in mon:
                causes[str(c.get("primary_cause"))] = causes.get(str(c.get("primary_cause")), 0) + 1
                match[str(c.get("match_status"))] = match.get(str(c.get("match_status")), 0) + 1
                cell = c.get("cell") or {}
                if cell.get("column_leaf"):
                    leaves += 1
                for b in c.get("bindings") or []:
                    truths[str(b.get("truth"))] = truths.get(str(b.get("truth")), 0) + 1
                    st = (b.get("resolution_from_later_steps") or {}).get("state")
                    if st:
                        states[st] = states.get(st, 0) + 1
                    s1 = (b.get("resolution_if_the_operand_message_were_read_too_PROPOSAL_P1")
                          or {}).get("state")
                    if s1:
                        p1[s1] = p1.get(s1, 0) + 1
            index[str(r.get("item"))] = {
                "candidate_numbers": len(cands),
                "monetary_operands": len(mon),
                "operands_whose_cell_has_a_leaf": leaves,
                "primary_causes": causes,
                "match_status": match,
                "binding_truths": truths,
                "resolution_states": states,
                "resolution_under_P1": p1,
                "n_bindings": sum(truths.values()),
                "supported_bindings": truths.get("HOLDS", 0),
            }
    return index


def _check_index(check: dict) -> dict:
    """item -> what the deployed checker committed and reported."""
    index = {}
    for r in (check or {}).get("results") or []:
        preds = r.get("predictions") or []
        states, convs, errs = {}, {}, {}
        leaves = 0
        for p in preds:
            st = str(p.get("state"))
            states[st] = states.get(st, 0) + 1
            convs[str(p.get("conversion"))] = convs.get(str(p.get("conversion")), 0) + 1
            if p.get("extraction_error"):
                errs[str(p.get("extraction_error"))] = errs.get(str(p.get("extraction_error")), 0) + 1
            if (p.get("cell") or {}).get("column_leaf"):
                leaves += 1
        index[str(r.get("item"))] = {
            "n_predictions": len(preds),
            "states": states,
            "conversions": convs,
            "extraction_errors": errs,
            "predictions_whose_cell_has_a_leaf": leaves,
            "problems": r.get("problems") or [],
        }
    return index


def _delta(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b))
    return {k: (b.get(k, 0) - a.get(k, 0)) for k in keys if b.get(k, 0) != a.get(k, 0)}


def compare(bc: str, ba: str, rc: str, ra: str, records: str) -> dict:
    base_c, base_a = _check_index(_load(bc)), _audit_index(_load(ba))
    rep_c, rep_a = _check_index(_load(rc)), _audit_index(_load(ra))
    rows = _rows(records)

    per_item, classes = [], {}
    for item in sorted(rows):
        b_c, r_c = base_c.get(item, {}), rep_c.get(item, {})
        b_a, r_a = base_a.get(item, {}), rep_a.get(item, {})
        row = rows[item]
        changed = {
            "predictions": r_c.get("n_predictions", 0) - b_c.get("n_predictions", 0),
            "monetary_operands": r_a.get("monetary_operands", 0) - b_a.get("monetary_operands", 0),
            "bindings": r_a.get("n_bindings", 0) - b_a.get("n_bindings", 0),
            "supported_bindings": r_a.get("supported_bindings", 0) - b_a.get("supported_bindings", 0),
            "states": _delta(b_c.get("states", {}), r_c.get("states", {})),
            "primary_causes": _delta(b_a.get("primary_causes", {}), r_a.get("primary_causes", {})),
            "match_status": _delta(b_a.get("match_status", {}), r_a.get("match_status", {})),
        }
        any_change = (any(v for k, v in changed.items() if isinstance(v, int))
                      or changed["states"] or changed["primary_causes"] or changed["match_status"])
        # the classification is deliberately coarse and states what it rests on
        if changed["supported_bindings"] > 0:
            cls = "a binding became SUPPORTED - inspect the witness before calling it correct"
        elif any_change:
            cls = "something changed, but no binding became supported"
        elif r_a.get("operands_whose_cell_has_a_leaf", 0) > 0:
            cls = "a leaf was recovered and NOTHING about the contract changed"
        else:
            cls = "identical, and no leaf recovered"
        classes[cls] = classes.get(cls, 0) + 1
        per_item.append({
            "item": item,
            "hard": row.get("hard"),
            "termination_cause": row.get("termination_cause"),
            "baseline": {"predictions": b_c.get("n_predictions", 0), "states": b_c.get("states", {}),
                         "monetary_operands": b_a.get("monetary_operands", 0),
                         "bindings": b_a.get("n_bindings", 0),
                         "supported_bindings": b_a.get("supported_bindings", 0),
                         "primary_causes": b_a.get("primary_causes", {})},
            "repaired": {"predictions": r_c.get("n_predictions", 0), "states": r_c.get("states", {}),
                          "monetary_operands": r_a.get("monetary_operands", 0),
                          "bindings": r_a.get("n_bindings", 0),
                          "supported_bindings": r_a.get("supported_bindings", 0),
                          "primary_causes": r_a.get("primary_causes", {}),
                          "operands_whose_cell_has_a_leaf": r_a.get("operands_whose_cell_has_a_leaf", 0),
                          "predictions_whose_cell_has_a_leaf": r_c.get("predictions_whose_cell_has_a_leaf", 0)},
            "changed": changed,
            "classification": cls,
        })

    def tot(index, key):
        return sum((v.get(key) or 0) for v in index.values())

    def merge(index, key):
        out = {}
        for v in index.values():
            for k, n in (v.get(key) or {}).items():
                out[k] = out.get(k, 0) + n
        return out

    totals = {
        "records": len(rows),
        "baseline": {
            "predictions": tot(base_c, "n_predictions"),
            "monetary_operands": tot(base_a, "monetary_operands"),
            "bindings": tot(base_a, "n_bindings"),
            "supported_bindings": tot(base_a, "supported_bindings"),
            "states": merge(base_c, "states"),
            "primary_causes": merge(base_a, "primary_causes"),
            "match_status": merge(base_a, "match_status"),
            "resolution_states": merge(base_a, "resolution_states"),
        },
        "repaired": {
            "predictions": tot(rep_c, "n_predictions"),
            "monetary_operands": tot(rep_a, "monetary_operands"),
            "bindings": tot(rep_a, "n_bindings"),
            "supported_bindings": tot(rep_a, "supported_bindings"),
            "operands_whose_cell_has_a_leaf": tot(rep_a, "operands_whose_cell_has_a_leaf"),
            "predictions_whose_cell_has_a_leaf": tot(rep_c, "predictions_whose_cell_has_a_leaf"),
            "states": merge(rep_c, "states"),
            "primary_causes": merge(rep_a, "primary_causes"),
            "match_status": merge(rep_a, "match_status"),
            "resolution_states": merge(rep_a, "resolution_states"),
        },
        "classification": classes,
    }
    totals["verdict"] = (
        "the repair enabled NO supported binding and therefore NO finding; the leaf is recorded as "
        "column/series identity beside the unchanged unit question"
        if totals["repaired"]["supported_bindings"] == totals["baseline"]["supported_bindings"] == 0
        else "a supported binding changed; every witness must be inspected before it is called correct")
    return {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
            "inputs": {"baseline_check": bc, "baseline_audit": ba,
                       "repaired_check": rc, "repaired_audit": ra, "records": records},
            "label": "REUSED DEVELOPMENT EVIDENCE: these are the campaign's own training questions, "
                     "re-attempted and now re-analysed; nothing here is a held-out measurement",
            "totals": totals, "per_item": per_item}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--baseline-check", "--baseline-audit", "--repaired-check", "--repaired-audit",
                 "--records", "--out"):
        ap.add_argument(flag, required=True)
    a = ap.parse_args(argv)
    doc = compare(a.baseline_check, a.baseline_audit, a.repaired_check, a.repaired_audit, a.records)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "BIND_COMPARE.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    t = doc["totals"]
    print(json.dumps({"written_to": path, "verdict": t["verdict"],
                      "baseline": t["baseline"], "repaired": t["repaired"],
                      "classification": t["classification"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
