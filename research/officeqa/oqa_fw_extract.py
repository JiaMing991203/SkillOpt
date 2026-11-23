#!/usr/bin/env python3
"""OfficeQA document entities for the ported framework, version OQA-FW-1.

WHAT IS NEW HERE, and both items are extraction corrections the earlier generations needed:

  1. STABLE OBSERVATION REFERENCES. The LN generation's recorded assumption was
     "an occurrence exists only where an observation line carries a line number and pipe-delimited
     fields; a read without line numbers yields no position". Measured over the 48 frozen attempts,
     EVERY `read` observation is unnumbered, and reads carry 571 of the 769 observed tables (74%). So
     three quarters of the document content could not be cited at all, and a cell that cannot be cited
     cannot witness anything. A reference here is (step index, table index, row index, column index)
     taken from the observation's own structure - `s12:tb1:r4:c3` - plus the row and column LABELS for
     a human to check. It needs no line number and it is stable under re-rendering, because the step
     index comes from the saved conversation and the table index from the same parse that produced the
     cell.

  2. A NUMERIC COMPARISON AT THE WRITTEN PRECISION. `oqa_ln_evidence._near` is
     `abs(x - y) <= max(0.5, abs(y) * 1e-6)`. That absolute floor of 0.5 is wrong for the small
     quantities this corpus is full of: it makes 1.8 equal to 2.0, and it makes `x * 0.6 = x` an
     "identity". `near()` below compares at half a unit in the last place the number was WRITTEN to,
     which is the precision the writer actually claimed, with the same relative term beside it.
     `oqa_ln_evidence` is NOT modified: the frozen D-2..D-5 outputs must stay reproducible, so the
     correction lives in this new version and `near_legacy` is kept beside it for the comparison.

WHAT IS REUSED UNCHANGED. `oqa_lc_cells` for tables, headers, cells, periods and the split/flat
cross-check, and `oqa_cc_extract` for operand classification, the tri-state nearer-scale question and
the conversion arithmetic. Both are frozen campaign code and are not edited; everything new is here.

Nothing in this module reads `ground_truth`, `soft`, `hard`, a score, a skill or any held-out material.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_extract as X  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402

VERSION = "oqa.fw.extract.v1"
PLACEHOLDER_LABELS = ("nan", "none", "unnamed")
_BARE_ORDINAL = re.compile(r"^\(\s*\d+\s*\)$")
_UNNAMED_COL = re.compile(r"^unnamed:\s*\d+", re.I)


# ── the numeric comparison ────────────────────────────────────────────────────
def written_decimals(raw: str) -> int:
    """How many decimal places the number was WRITTEN to. 2,410,259 -> 0; 4.708 -> 3."""
    m = re.search(r"\.(\d+)", str(raw or ""))
    return len(m.group(1)) if m else 0


def near(x, y, raw_x=None, raw_y=None, rel: float = 1e-9) -> bool:
    """Equal at the precision the numbers were written to.

    The tolerance is half a unit in the last written place - the most either writer claimed - plus a
    relative term for the large magnitudes. With no written form in hand it falls back to exact
    integers, never to a half-unit window around an unknown precision.
    """
    if x is None or y is None:
        return False
    d = max(written_decimals(raw_x), written_decimals(raw_y)) if (raw_x or raw_y) else 0
    half = 0.5 * (10.0 ** -d) if (raw_x or raw_y) else 0.0
    return abs(x - y) <= max(half, abs(y) * rel)


def near_legacy(x, y) -> bool:
    """`oqa_ln_evidence._near`, kept so the correction can be MEASURED rather than asserted."""
    if x is None or y is None:
        return False
    return abs(x - y) <= max(0.5, abs(y) * 1e-6)


# ── references ────────────────────────────────────────────────────────────────
def step_ref(step: dict) -> str:
    return f"s{int(step.get('i'))}"


def table_ref(step: dict, k: int) -> str:
    return f"tb:{step_ref(step)}:{k}"


def col_ref(tref: str, j: int) -> str:
    return f"co:{tref[3:]}:{j}"


def cell_ref(tref: str, r: int, j: int) -> str:
    return f"ce:{tref[3:]}:{r}:{j}"


def operand_ref(step: dict, n: int) -> str:
    return f"op:{step_ref(step)}:{n}"


def scale_ref(scale: str) -> str:
    return f"sc:{scale}"


def kind_of(ref: str) -> str | None:
    """The entity type of a reference, for the domain's `type_of`."""
    return {"tb": "table", "co": "column", "ce": "cell", "op": "operand", "sc": "scale"}.get(
        str(ref or "")[:2])


def citation(cell: dict) -> str:
    """A reference a person can check WITHOUT a line number: where it came from, in words."""
    return (f"step {cell['step']} table {cell['table_index'] + 1}, "
            f"row {cell['row_label'] or '(unlabelled)'!r}, "
            f"column {cell['column_label'] or '(unbound)'!r}"
            + (f", {cell['table_scale']}" if cell.get("table_scale") else ""))


# ── labels ────────────────────────────────────────────────────────────────────
def label_present(label) -> bool:
    """Is this merged header label a real column name?

    `nan` is pandas' empty cell printed as text, "Unnamed: 9_level_0" is its empty-header placeholder,
    and "(3)" names a column POSITION rather than a measure. 234 of the 1,465 non-empty child-row
    labels in the frozen records are the literal text `nan`.
    """
    s = str(label or "").strip()
    if not s or s.lower() in PLACEHOLDER_LABELS or _BARE_ORDINAL.match(s) or _UNNAMED_COL.match(s):
        return False
    return True


# ── the entities of one attempt ───────────────────────────────────────────────
def document_entities(steps: list, assumptions=()) -> dict:
    """Tables, columns and cells of the prefix, each with a stable reference and its observed facts.

    `assumptions` is passed through to `oqa_lc_cells` ONLY so its own column binding can be compared
    with the framework's; the framework's `col_bound` comes from the learned model, not from here.
    """
    tables, columns, cells = {}, {}, {}
    flat_by_step = []          # (step index, flat table) for the cross-check
    introduced = {}            # step index -> {type: [refs]}
    for s in steps:
        if s.get("kind") != "tool":
            continue
        i = int(s.get("i"))
        obs = str(s.get("obs") or "")
        own = CELLS.tables(obs)
        intro = introduced.setdefault(i, {"table": [], "column": [], "cell": [], "operand": []})
        for k, t in enumerate(own):
            tref = table_ref(s, k)
            if t["header_form"] == "flat":
                flat_by_step.append((i, t))
            tables[tref] = {
                "ref": tref, "step": i, "table_index": k, "width": t["width"],
                "header_form": t["header_form"], "scale": t.get("scale"),
                "child_row": list(t.get("child_row") or []), "parent_row": list(t.get("parent_row") or []),
                "flat": t.get("flat"), "fingerprints": list(t.get("fingerprints") or []),
                "n_rows": len(t.get("rows") or []), "raw": t,
            }
            intro["table"].append(tref)
            child = t.get("child_row") or []
            parent = t.get("parent_row") or []
            for j in range(t["width"]):
                cref = col_ref(tref, j)
                ch = str(child[j] or "").strip() if j < len(child) else ""
                pa = str(parent[j] or "").strip() if j < len(parent) else ""
                columns[cref] = {"ref": cref, "table": tref, "step": i, "index": j,
                                 "child": ch, "parent": pa,
                                 "merged_label": ch or pa,
                                 "label_present": label_present(ch or pa),
                                 # B-4 again, and it is the condition the learner has to find: the
                                 # SUB-LABEL'S OWN cell. `merged_label` falls back to the spanning
                                 # parent when the child cell is empty, and a spanning parent is
                                 # printed once at its first column, so the fallback shifts.
                                 "child_label_present": label_present(ch),
                                 "agrees_flat": None, "flat_leaf": None, "pair_step": None,
                                 "pair_overlap": None}
                intro["column"].append(cref)
            for r, row in enumerate(t.get("rows") or []):
                for cell in (row.get("cells") or []):
                    j = int(cell.get("index") or 0)
                    if j == 0:
                        continue                      # the row LABEL column is not a data cell (B-3)
                    xref = cell_ref(tref, r, j)
                    cells[xref] = {
                        "ref": xref, "table": tref, "column": col_ref(tref, j), "step": i,
                        "table_index": k, "row_index": r, "col_index": j,
                        "value": cell.get("value"), "raw": cell.get("raw"),
                        "row_label": row.get("label"), "period": row.get("period"),
                        "column_label": (columns[col_ref(tref, j)]["merged_label"]
                                         if col_ref(tref, j) in columns else None),
                        "table_scale": t.get("scale"),
                    }
                    intro["cell"].append(xref)
    _cross_check(tables, columns, flat_by_step)
    return {"version": VERSION, "tables": tables, "columns": columns, "cells": cells,
            "introduced": introduced}


MIN_SHARED_ROWS = 2


def _leaf(fcell) -> str:
    return (fcell or {}).get("child") or (fcell or {}).get("parent") or ""


def _cross_check(tables: dict, columns: dict, flat_by_step: list) -> None:
    """Pair each split rendering with flat renderings of the SAME table and record per-column agreement.

    A flat rendering observed at ANY earlier step is ground truth for a split one, which is the whole
    reason a reference is (step, table) rather than a line number.

    TWO GUARDS, and the first one changes the numbers a great deal. `oqa_lc_cells.pair_tables` accepts a
    pair on ONE shared data row and takes the first match; measured here, that accepts pairings in which
    the two renderings are different tables of equal width, and it turns 8 real disagreements into 68. So
    a pair needs MIN_SHARED_ROWS = 2 independent shared rows. Second, if several candidate flat
    renderings disagree WITH EACH OTHER at an index, the ground truth is itself contradictory and the
    column is left UNKNOWN rather than judged against an arbitrary one of them.
    """
    for tref, t in tables.items():
        if t["header_form"] not in ("split", "single"):
            continue
        cands = [(fi, ft, len(set(t["fingerprints"]) & set(ft["fingerprints"])))
                 for (fi, ft) in flat_by_step if ft["width"] == t["width"]]
        cands = [c for c in cands if c[2] >= MIN_SHARED_ROWS]
        if not cands:
            continue
        fi, ft, overlap = cands[0]
        dis = {d["index"] for d in CELLS.binding_disagreements(t["raw"], ft, "leaf")}
        for j in range(t["width"]):
            c = columns.get(col_ref(tref, j))
            if c is None:
                continue
            labs = {CELLS._norm(_leaf(((f.get("flat") or [{}] * t["width"])[j]
                                      if j < len(f.get("flat") or []) else {})))
                    for (_, f, _) in cands}
            if len(labs) > 1:
                c["agrees_flat"] = None       # the ground truth contradicts itself here
                c["flat_conflict"] = sorted(labs)[:4]
                continue
            fcell = ((ft.get("flat") or [{}] * t["width"])[j] or {}) if j < t["width"] else {}
            c["agrees_flat"] = j not in dis
            c["flat_leaf"] = _leaf(fcell) or None
            c["pair_step"] = fi
            c["pair_overlap"] = overlap
        t["flat_pair_step"] = fi
        t["flat_pair_overlap"] = overlap
        t["flat_pair_candidates"] = len(cands)


_CALC = re.compile(
    r"(?<![\d.,])(-?\d[\d,]*(?:\.\d+)?)\s*([-+*/x\u00d7\u00f7])\s*(-?\d[\d,]*(?:\.\d+)?)"
    r"\s*=\s*(-?\d[\d,]*(?:\.\d+)?)")


def calculation_spans(text: str) -> list:
    """Character spans of the written binary statements `a OP b = c` in one message.

    Needed because an obligation about a CONVERSION applies where a conversion would be used, which is
    in arithmetic - not to every number the actor types while narrating what it sees. The same regular
    expression `oqa_ln_evidence` uses, so "part of a written calculation" means the same thing here as
    in the frozen audit.
    """
    return [(m.start(), m.end()) for m in _CALC.finditer(text or "")]


def operand_entities(steps: list, ents: dict, *, legacy_tolerance: bool = False) -> dict:
    """Written operands of the prefix, matched to data cells at the written precision.

    THREE THINGS THIS RECORDS that the first version of this module did not, each of which was making
    the slice's obligation counts wrong:
      * `raw` - the number AS WRITTEN. `oqa_cc_extract.operands` calls it `raw`, and reading it as
        `text` silently returned None, so `near()` fell back to its integer path and the precision
        correction this module exists for was not in force.
      * `in_calculation` - whether the number is an operand or the result of a written binary
        statement. A number that is merely read aloud carries no conversion obligation.
      * `factor_steps` - every actor message in the prefix, not only this one, that states a factor of
        ten. One conversion written once covers the numbers it is applied to; looking only in the
        operand's own message turned one stated factor into dozens of missing ones.
    """
    ops = {}
    cells = list(ents["cells"].values())
    factors = {}
    for s in steps:
        if s.get("kind") == "message":
            e = X.stated_factor_exp(str(s.get("text") or ""))
            if e is not None:
                factors[int(s.get("i"))] = e
    for s in steps:
        if s.get("kind") != "message":
            continue
        i = int(s.get("i"))
        spans = calculation_spans(str(s.get("text") or ""))
        text_s = str(s.get("text") or "")
        intro = ents["introduced"].setdefault(i, {"table": [], "column": [], "cell": [], "operand": []})
        for n, op in enumerate(X.monetary_operands(s)):
            oref = operand_ref(s, n)
            written = op.get("raw")
            pos = text_s.find(written) if written else -1
            in_calc = any(a <= pos < b for (a, b) in spans) if pos >= 0 else False
            hits = [c for c in cells
                    if c["step"] < i and (near_legacy(op["value"], c["value"]) if legacy_tolerance
                                          else near(op["value"], c["value"], written, c.get("raw")))]
            uniq = {(c["value"], c["table"], c["col_index"]) for c in hits}
            own = factors.get(i)
            earlier = {k: v for k, v in factors.items() if k <= i}
            ops[oref] = {"ref": oref, "step": i, "n": n, "value": op["value"],
                         "text": written, "raw": written, "verdict": op.get("verdict"),
                         "in_calculation": in_calc,
                         "conversion_stated": bool(earlier),
                         "stated_factor_exp": own if own is not None else (
                             earlier[max(earlier)] if earlier else None),
                         "factor_steps": sorted(earlier),
                         "cells": [c["ref"] for c in hits[:8]], "n_cells": len(hits),
                         "unique": len(uniq) == 1 and len(hits) >= 1,
                         "cell": hits[0]["ref"] if (len(uniq) == 1 and hits) else None}
            intro["operand"].append(oref)
    ents["operands"] = ops
    return ents
