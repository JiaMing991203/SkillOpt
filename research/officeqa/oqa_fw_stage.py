#!/usr/bin/env python3
"""Action-to-stage connections and the obligations of the first OfficeQA slice.

THE SLICE, as one chain, and every arrow is a separate thing that can be unknown:

  returned window -> table / header / cell bindings -> LEARNED licence -> applicable money scale
                  -> the conversion the question requires -> what the report tells the optimizer

STAGES, and which recorded action belongs to each:
  LOCATE     glob / grep steps that find a document and a page
  EXTRACT    read / grep steps that return a table
  INTERPRET  no action of its own: it is what the returned window LICENSES, which is where a learned
             rule lives and the reason this stage is worth separating
  COMPUTE    the actor's own written arithmetic
  ANSWER     the committed answer

OBLIGATION STATES, and the asymmetry is deliberate:
  DISCHARGED   the records establish it
  OUTSTANDING  the records establish that something required was NOT done (an accusation; it needs the
               requirement to be established first)
  UNKNOWN      the records do not settle it - including every case where the learned licence is absent,
               refuted or retired
  NOT_REQUIRED the question does not ask for it (no asked scale, or a factor of 10^0)

A WITHDRAWN LICENCE MUST NEVER PRODUCE AN ACCUSATION. If the learned rule is absent or refuted, every
obligation downstream of it is UNKNOWN - never OUTSTANDING and never a diagnosis naming the actor. That
is the soundness condition `licenceWithdrawnNeverAccuses` that `oqa_fw_lean.py` checks, and it is what
keeps a gap in the CHECKER from being reported as a mistake by the AGENT.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import oqa_lc_cells as CELLS  # noqa: E402
import oqa_cc_extract as X  # noqa: E402

DISCHARGED, OUTSTANDING, UNKNOWN, NOT_REQUIRED = "DISCHARGED", "OUTSTANDING", "UNKNOWN", "NOT_REQUIRED"
STAGES = ("LOCATE", "EXTRACT", "INTERPRET", "COMPUTE", "ANSWER")

OBLIGATIONS = {
    "column_identified": (
        "INTERPRET",
        "the column of the cell this operand was taken from is identified",
        "it does not say the column carries the measure the question asks about - only that the "
        "document names this column and that something licenses reading that name"),
    "unit_resolved": (
        "INTERPRET",
        "the money scale that applies to this operand's cell is identified",
        "it does not say the operand is the right quantity, and it never reads a score"),
    "conversion_accounted": (
        "COMPUTE",
        "where the resolved scale differs from the scale the question asks for, the actor wrote a "
        "factor of ten",
        "it does not check that the factor was applied to the right number, nor that the final answer "
        "is correct"),
}


def stage_of_step(step: dict, dom) -> str:
    cmd = dom.parse_command(step)
    return {"glob": "LOCATE", "grep": "EXTRACT", "read": "EXTRACT", "answer": "ANSWER"}.get(
        cmd.get("verb") or "", "COMPUTE" if step.get("kind") == "message" else "LOCATE")


def _dep(f: dict) -> dict:
    return {"basis": f.get("basis"), "assumptions": sorted(f.get("assumptions") or []),
            "learned": sorted(f.get("learned") or []), "steps": sorted(f.get("steps") or [])}


def operand_obligations(est, t: int, oref: str, dom) -> dict:
    """The three obligations for one written operand, with what each rests on.

    `t` is the state the judgement is made at - the end of the record - and every value is still read
    through the prefix estimator, so nothing later than `t` is consulted.
    """
    o = (dom.entities.get("operands") or {}).get(oref) or {}
    xref = o.get("cell")
    out = {"operand": oref, "value": o.get("value"), "text": o.get("text"), "step": o.get("step"),
           "cell": xref, "citation": None, "obligations": {}}
    if not xref:
        for name in OBLIGATIONS:
            out["obligations"][name] = {"state": UNKNOWN, "why": "this operand matches no single data "
                                        "cell, so there is no cell whose column or scale to resolve",
                                        "rests_on": {}}
        return out
    cell = (dom.entities.get("cells") or {}).get(xref) or {}
    out["citation"] = _citation(cell)
    cb = est.value(t, "col_bound", [cell.get("column")])
    out["obligations"]["column_identified"] = {
        "state": DISCHARGED if cb["v"] is True else UNKNOWN,
        "why": ("the sub-label row names this column and an active learned rule licenses reading it"
                if cb["v"] is True else
                "nothing licenses reading this column's printed sub-label as its name: no active "
                "learned rule covers it, or the `index_alignment` assumption is not adopted"),
        "column_label": cell.get("column_label"), "rests_on": _dep(cb)}
    res = est.value(t, "cell_scale_resolved", [xref])
    nu = est.value(t, "nearer_unit_stated", [xref])
    out["obligations"]["unit_resolved"] = {
        "state": DISCHARGED if res["v"] is True else (
            UNKNOWN if res["v"] is None else NOT_REQUIRED),
        "why": ({True: f"the table states {cell.get('table_scale')!r} and no nearer scope overrides it",
                 None: "the scale that applies here is not settled by the records: "
                       + ("a nearer scope was not observed" if nu["v"] is None else "no table scale"),
                 False: "the table states no scale, or a nearer scope states another one"}[res["v"]]),
        "table_scale": cell.get("table_scale"), "nearer_unit": nu["v"], "rests_on": _dep(res)}
    out["obligations"]["conversion_accounted"] = _conversion(est, t, o, cell, res, dom)
    return out


def _conversion(est, t, o, cell, res, dom) -> dict:
    asked = dom.asked_scale
    if not asked:
        return {"state": NOT_REQUIRED, "why": "the question does not ask for a money scale",
                "rests_on": {}}
    if not o.get("in_calculation"):
        # AUDIT, 2026-11-22. Without this the obligation was raised for EVERY number the actor typed,
        # including the ones it was only reading aloud ("I can see the Individual column, which shows
        # 1,095,541"). 70 of UID0014's 227 written numbers were accused that way. A conversion
        # obligation belongs where a conversion would be used, which is in written arithmetic.
        return {"state": NOT_REQUIRED,
                "why": "this number is not an operand or result of a written calculation, so no "
                       "conversion of it was required", "rests_on": {}}
    if res["v"] is not True:
        return {"state": UNKNOWN,
                "why": "the applicable scale is unresolved, so whether a conversion is required is "
                       "unknown; an unresolved scale is never reported as a missing conversion",
                "rests_on": _dep(res)}
    frm = CELLS.canon_scale(str(cell.get("table_scale") or ""))
    req = X.required_exp(frm, asked)
    if req is None:
        return {"state": UNKNOWN, "why": f"no factor is defined between {frm!r} and {asked!r}",
                "rests_on": _dep(res)}
    if req == 0:
        return {"state": NOT_REQUIRED,
                "why": f"the cell is already in {asked}: the required factor is 10^0, and demanding an "
                       f"explicit multiplication by one would be a false alarm",
                "required_exp": 0, "rests_on": _dep(res)}
    stated = o.get("stated_factor_exp")
    if stated is None:
        return {"state": OUTSTANDING,
                "why": f"the cell is in {frm}, the question asks for {asked}, so a factor of 10^{req} "
                       f"is required and no factor of ten is written anywhere in the record up to "
                       f"this point",
                "required_exp": req, "stated_exp": None, "factor_steps": [],
                "rests_on": _dep(res)}
    if stated == req:
        return {"state": DISCHARGED,
                "why": f"the actor wrote a factor of 10^{stated}, which is the one required",
                "required_exp": req, "stated_exp": stated,
                "factor_steps": o.get("factor_steps") or [], "rests_on": _dep(res)}
    # A DIFFERING factor is reported as UNKNOWN, not as a violation. The factor was written in some
    # message of the record; which numbers it was applied to is not something this checker reads, so
    # "the actor converted wrongly" is not established. Saying so would be the same mistake the D-2
    # audit found thirteen times.
    return {"state": UNKNOWN,
            "why": (f"a factor of 10^{stated} is written in the record and 10^{req} is the one this "
                    f"operand needs; which numbers that factor was applied to is not recorded, so "
                    f"whether this one was converted is unsettled"),
            "required_exp": req, "stated_exp": stated,
            "factor_steps": o.get("factor_steps") or [], "rests_on": _dep(res)}


def _citation(cell: dict) -> str:
    return (f"step {cell.get('step')}, table {int(cell.get('table_index') or 0) + 1}, row "
            f"{cell.get('row_label') or '(unlabelled)'!r}, column "
            f"{cell.get('column_label') or '(unbound)'!r}"
            + (f" [{cell['table_scale']}]" if cell.get("table_scale") else ""))


def attempt_report(est, t: int, dom, *, max_operands: int = 400) -> dict:
    """Every operand's obligations, plus the counts a report and a Lean export both read."""
    rows = [operand_obligations(est, t, oref, dom)
            for oref in sorted((dom.entities.get("operands") or {}))[:max_operands]]
    counts = {}
    for name in OBLIGATIONS:
        c = {}
        for r in rows:
            st = r["obligations"][name]["state"]
            c[st] = c.get(st, 0) + 1
        counts[name] = c
    learned = sorted({x for r in rows for ob in r["obligations"].values()
                      for x in (ob.get("rests_on") or {}).get("learned") or []})
    assumed = sorted({x for r in rows for ob in r["obligations"].values()
                      for x in (ob.get("rests_on") or {}).get("assumptions") or []})
    return {"operands": rows, "counts": counts, "learned_ids": learned, "assumptions_used": assumed,
            "n_operands": len(rows),
            "accusations": sum(1 for r in rows for ob in r["obligations"].values()
                               if ob["state"] == OUTSTANDING)}
