#!/usr/bin/env python3
"""A hand-checkable audit of a few occurrence bindings, against the permitted observations themselves.

Rule OQA-LN-1. The instruction names four situations and asks why each binding is supported or
unresolved, with the warning that "a unique number, matching row token or grep query alone must not
automatically establish correct semantic source identity", and that the corpus figures - 64 supported
candidates, 14 remaining violations - are NOT validated ground truth.

THE FOUR SITUATIONS, each selected MECHANICALLY from the frozen evidence set and then quoted in full:

  unambiguous_source        one observed position, with a corroboration. The strongest case the
                            resolver can produce, and the audit still asks whether the corroboration
                            is about the right thing.
  competing_matches         the same number at two or more observed positions. A numeric match cannot
                            choose, and the resolver says UNKNOWN rather than picking one.
  derived_value_collision   a number the actor COMPUTED that happens to equal an observed cell. This is
                            the case that makes "equal numbers establish a source" false, and it is the
                            reason `operand_equals_earlier_result` exists as a separate class.
  replaced_calculation      an earlier statement superseded by a later one that yields the committed
                            answer. The binding question is whether the superseded operands are still
                            reported, and whether the answer is accused because of them.

For each one this prints the operand as written, the statement it appears in, every observed position
with its document, line and field, the line itself as the actor saw it, the grep pattern that returned
it, the corroborations found, and the resolver's own reason. A reader can disagree with any of it from
what is printed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_occ as OCC  # noqa: E402

SCHEMA = "lso.officeqa_occurrence_audit.v1"
UNAMBIGUOUS, COMPETING, COLLISION, REPLACED = (
    "unambiguous_source", "competing_matches", "derived_value_collision", "replaced_calculation")


def _line_of(items, document, line_no):
    """The observation line itself, as the actor received it, with the query that returned it."""
    for entry in OCC.normalize(items):
        if entry["kind"] != "tool":
            continue
        import re
        pm = re.search(r"path=['\"]([^'\"]+)['\"]", entry["cmd"])
        if not pm or os.path.basename(pm.group(1)) != document:
            continue
        qm = re.search(r"pattern=['\"]([^'\"]+)['\"]", entry["cmd"])
        for raw in entry["obs"].splitlines():
            m = OCC._LINE.match(raw)
            if m and int(m.group(1)) == line_no:
                return {"line": raw[:400], "pattern": qm.group(1) if qm else None,
                        "cmd": entry["cmd"][:220]}
    return None


def audit_attempt(freeze_doc: dict, row: dict) -> dict:
    pdir = os.path.join(freeze_doc["source_root"], "train_p%d" % row["pass"], "predictions")
    att = EV.read_attempt(pdir, row["item"])
    ex = EV.extract(att)
    vd = EV.verdicts(att, ex)
    shape = EV.recovery_shape(ex)
    cases = []
    earlier_results: list = []
    for ci, calc in enumerate(ex["calculations"]):
        upto = att["items"][: calc["message_item_index"] + 1]
        occs = OCC.observed_occurrences(upto)
        for oi, operand in enumerate(calc["operands"]):
            v = vd[f"{ci}/{oi}"]
            hits = [o for o in occs if o["value"] == operand["value"]]
            positions = sorted({(h["document"], h["line"], h["field"]) for h in hits})
            kind = None
            if v["sourced"] and len(positions) == 1:
                kind = UNAMBIGUOUS
            elif len(positions) > 1:
                kind = COMPETING
            if operand["value"] in earlier_results and positions:
                kind = COLLISION          # a computed value that ALSO sits in an observed cell
            if kind is None:
                continue
            cases.append({
                "kind": kind, "attempt_id": row["attempt_id"], "role": row["role"],
                "calculation_index": ci, "statement": calc["statement"],
                "operand_as_written": operand["as_written"], "operand_value": operand["value"],
                "operand_is_an_earlier_result": operand["value"] in earlier_results,
                "features": operand["features"],
                "n_observed_positions": len(positions),
                "positions": [{"document": d, "line": ln, "field": f,
                               "observation": _line_of(upto, d, ln)}
                              for d, ln, f in positions[:4]],
                "resolver_match": v["match"], "resolver_binding": v["binding"],
                "corroborations": v["corroborations"],
                "supporting_corroborations": v["supporting"],
                "resolver_reason": v["why"],
                "why_this_is_or_is_not_a_source": (
                    "SUPPORTED: one observed position and an independent corroboration "
                    + ", ".join(v["supporting"]) if v["sourced"] else
                    "UNRESOLVED: " + v["why"]),
            })
        earlier_results.append(calc["result"])
    if shape["qualifies"]:
        sup = shape["supporting_calculation"]
        cases.append({
            "kind": REPLACED, "attempt_id": row["attempt_id"], "role": row["role"],
            "committed_answer_value": shape["committed_answer_value"],
            "supporting_calculation": {"index": sup,
                                       "statement": ex["calculations"][sup]["statement"]},
            "superseded_calculations": [
                {"index": i, "statement": ex["calculations"][i]["statement"],
                 "result": ex["calculations"][i]["result"]}
                for i in shape["superseded_calculations"][:6]],
            "n_superseded": len(shape["superseded_calculations"]),
            "why_this_is_or_is_not_a_source": (
                "the superseded statements remain in the record and are reported as replaced; the "
                "obligation is decided from the SUPPORTING calculation's operands alone, so an "
                "earlier attempt cannot accuse a committed answer"),
        })
    return {"attempt_id": row["attempt_id"], "cases": cases}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-kind", type=int, default=2)
    a = ap.parse_args(argv)
    with open(a.freeze, encoding="utf-8") as f:
        freeze_doc = json.load(f)
    chosen: dict = {UNAMBIGUOUS: [], COMPETING: [], COLLISION: [], REPLACED: []}
    scanned = 0
    for row in sorted(freeze_doc["attempts"], key=lambda d: d["attempt_id"]):
        if all(len(v) >= a.per_kind for v in chosen.values()):
            break
        scanned += 1
        for case in audit_attempt(freeze_doc, row)["cases"]:
            bucket = chosen[case["kind"]]
            if len(bucket) < a.per_kind:
                bucket.append(case)
    doc = {
        "schema": SCHEMA, "rule": "OQA-LN-1",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "freeze_sha256": freeze_doc["self_sha256"],
        "selection": f"the first {a.per_kind} case(s) of each kind in attempt-id order; "
                     f"{scanned} attempts scanned",
        "what_this_is_not": ("a validation of the corpus figures. The 64 supported candidates and the "
                             "14 remaining violations reported earlier are measurements of this "
                             "resolver, not ground truth about the documents, and nothing here "
                             "establishes that a corroborated position is the cell the actor meant."),
        "cases": {k: v for k, v in chosen.items()},
        "counts": {k: len(v) for k, v in chosen.items()},
    }
    body = json.dumps(doc, indent=1, sort_keys=True, default=str)
    doc["self_sha256"] = IR.sha(body)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(f"wrote {a.out}  digest {doc['self_sha256'][:16]}  counts {doc['counts']}")
    for kind, cases in chosen.items():
        for c in cases[:1]:
            print(f"\n== {kind} == {c['attempt_id']}")
            if kind == REPLACED:
                print(f"   committed {c['committed_answer_value']} from "
                      f"{c['supporting_calculation']['statement']!r}; "
                      f"{c['n_superseded']} superseded, e.g. "
                      f"{[s['statement'] for s in c['superseded_calculations'][:2]]}")
            else:
                print(f"   {c['statement']!r}  operand {c['operand_as_written']}  "
                      f"{c['resolver_match']} -> {c['resolver_binding']}")
                for p in c["positions"][:2]:
                    obs = (p.get("observation") or {})
                    print(f"     {p['document']} line {p['line']} field {p['field']}")
                    print(f"       {str(obs.get('line'))[:150]}")
                    print(f"       pattern={obs.get('pattern')!r}")
                print(f"   {c['why_this_is_or_is_not_a_source'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
