#!/usr/bin/env python3
"""Extract the entities, evaluate a connected contract on a prefix, and link it to a stage obligation.

Amendment B-1. Everything here is DETERMINISTIC: the learner proposes, this validates and scores.

PREFIX-ONLY, AND THE PREDICTION IS COMMITTED FIRST. The checked relationship is a claim about a value
the actor wrote down and about what it must then do with it, so the order is:

  1. walk the attempt's steps in order;
  2. at the step where the actor first writes an operand, bind {table, header, cell, value} from the
     observations revealed SO FAR and evaluate the contract. That yields a binding and, through the
     stage link, a REQUIREMENT on the conversion factor the actor must use;
  3. COMMIT that prediction - write it out - before any later step is read;
  4. only then read the later steps, find the factor the actor actually used, and resolve.

A `Revealer` enforces step 2/3: it raises if a step beyond the prefix is touched while a prediction is
still open, so prefix-only checking is a property of the code and not of the author's care.

WHAT THE FOUR OUTCOMES MEAN, and they are deliberately different things:
    actor_error         the binding holds, the requirement is determinate, the actor used another factor
    refuted_hypothesis  the binding's own conditions were met and the observation contradicts the
                        CONCLUSION - the contract is wrong, not the actor
    extraction_error    the entities could not be bound from the record (no table, no matching cell)
    unknown             the records do not settle it; the obligation's witness is withdrawn, never
                        turned into a violation
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402
import oqa_obligations as OB  # noqa: E402
import oqa_trace as TR  # noqa: E402

ACTOR_ERROR, REFUTED, EXTRACTION_ERROR, UNKNOWN_OUT = ("actor_error", "refuted_hypothesis",
                                                       "extraction_error", "unknown")
# Predicates `evaluate_binding` can actually decide. A condition outside this set makes a contract
# UNFALSIFIABLE: every cell comes out UNKNOWN and the hypothesis is never tested. The first proposal
# (job 66341664) did exactly that, and `oqa_cc_ir.validate` passed it, because well-formedness and
# evaluability are different questions. `unevaluable` is the second gate.
EVALUABLE = ("table_states_scale", "cell_in_table", "column_header_states_unit",
             "row_label_states_scale", "nearer_scale_between")


def unevaluable(contract: dict) -> list:
    """The condition predicates this checker has no evaluator for. Non-empty means do not admit it."""
    return sorted({cl.get("pred") for cl in (contract.get("conditions") or [])
                   if cl.get("pred") not in EVALUABLE})
# 10^e for the money scales, as a definition. MATH, never learned.
SCALE_EXP = {"units": 0, "thousands": 3, "millions": 6, "billions": 9}
_SCALE_WORD = re.compile(r"\b(thousand|million|billion)s?\b", re.I)
_FACTOR = re.compile(r"(?:divide[d]?\s+by|/|\*|multipl(?:y|ied)\s+by|times)\s*"
                     r"(1[,\d]*(?:\.\d+)?|0\.0+1|1e-?\d+)", re.I)


class Revealer:
    """The prefix guard. Reading past the prefix while a prediction is open is an error, not a habit."""

    def __init__(self, steps: list):
        self.steps = steps
        self.prefix = 0
        self.open_prediction = False
        self.touched_beyond = []

    def upto(self, i: int) -> list:
        self.prefix = max(self.prefix, i)
        return self.steps[:i]

    def beyond(self, i: int) -> list:
        if self.open_prediction:
            raise RuntimeError(f"a prediction is still open and step {i} is beyond the prefix")
        self.touched_beyond.append(i)
        return self.steps[i:]


# ───────────────────────── entity extraction ─────────────────────────

def observed_text(steps: list) -> str:
    return "\n".join(str(s.get("obs") or "") for s in steps if s.get("kind") == "tool")


def tables_in(steps: list, supplied: list) -> list:
    """Tables from the supplied pages plus every observation in the prefix, each with its stated scale."""
    out = []
    for page in supplied:
        for t in CELLS.tables(str(page.get("text") or "")):
            t["origin"] = {"kind": "supplied_page", "ref": page.get("ref")}
            out.append(t)
    for s in steps:
        if s.get("kind") != "tool":
            continue
        for t in CELLS.tables(str(s.get("obs") or "")):
            t["origin"] = {"kind": "observation", "step": s.get("i")}
            out.append(t)
    return out


def cells_in(steps: list, supplied: list, assumptions) -> list:
    out = []
    for page in supplied:
        out += CELLS.cells_of(str(page.get("text") or ""), assumptions,
                              source={"kind": "supplied_page", "ref": page.get("ref")})
    for s in steps:
        if s.get("kind") != "tool":
            continue
        out += CELLS.cells_of(str(s.get("obs") or ""), assumptions,
                              source={"kind": "observation", "step": s.get("i")})
    return out


def operands_in(step: dict) -> list:
    """Numbers the actor wrote in one message, with the text around them. OBS about the ACTOR."""
    text = str(step.get("text") or step.get("content") or "")
    out = []
    for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", text):
        raw = m.group(0)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        lo, hi = max(0, m.start() - 90), min(len(text), m.end() + 90)
        out.append({"raw": raw, "value": val, "step": step.get("i"), "context": text[lo:hi]})
    return out


def stated_factor_exp(text: str) -> int | None:
    """The power of ten the actor says it divided or multiplied by, if it says one. OBS, not inference."""
    for m in _FACTOR.finditer(text or ""):
        tok = m.group(1).replace(",", "")
        try:
            v = float(tok)
        except ValueError:
            continue
        if v <= 0:
            continue
        e = 0
        x = v
        while x >= 10 and e < 12:
            x /= 10.0
            e += 1
        while x < 1 and e > -12:
            x *= 10.0
            e -= 1
        if abs(x - 1.0) < 1e-9:
            word = (m.group(0) or "").lower()
            return -e if ("divide" in word or "/" in word) else e
    return None


def column_unit(cell: dict) -> str | None:
    """A unit the cell's own COLUMN header states, which is what can withdraw a table-level binding."""
    m = _SCALE_WORD.search(str(cell.get("column") or ""))
    return CELLS.canon_scale(m.group(1)) if m else None


def row_unit(cell: dict) -> str | None:
    m = _SCALE_WORD.search(str(cell.get("row_label") or ""))
    return CELLS.canon_scale(m.group(1)) if m else None


# ───────────────────────── evaluation ─────────────────────────

def evaluate_binding(c: dict, table: dict, cell: dict) -> dict:
    """The contract on one (table, cell), with each clause's value and its evidence pointer."""
    scale = CELLS.canon_scale(table.get("scale") or "")
    clauses = []
    ok = True
    unknown = False
    conflict = None
    for cl in c["conditions"]:
        pred, want = cl["pred"], cl.get("value", True)
        got, ev = None, None
        if pred == "table_states_scale":
            got = scale in SCALE_EXP
            ev = {"table_scale": table.get("scale"), "origin": table.get("origin")}
        elif pred == "cell_in_table":
            got = (cell.get("table_index") is not None
                   and cell.get("source", {}) == table.get("origin", {}) or True)
            ev = {"cell_table_index": cell.get("table_index")}
        elif pred == "column_header_states_unit":
            u = column_unit(cell)
            got = u is not None and u != scale
            ev = {"column": cell.get("column"), "column_unit": u}
            if u is not None and u != scale:
                conflict = {"kind": "column_header_states_another_unit", "table_scale": scale,
                            "column_unit": u, "column": cell.get("column")}
        elif pred == "row_label_states_scale":
            u = row_unit(cell)
            got = u is not None and u != scale
            ev = {"row_label": cell.get("row_label"), "row_unit": u}
        elif pred == "nearer_scale_between":
            # IMPLEMENTED FROM THE PROPOSER'S OWN STATED READING, and that provenance matters. The
            # proposal (job 66341664) gave the withdrawal_reason: "A nearer_scale_between observation,
            # such as a column header or row label with its own scale annotation closer to the cell,
            # withdraws the table-level binding." So: a nearer annotation exists when the cell's own
            # column header or row label states a money scale that differs from the table's. This is a
            # GIVEN implementation choice about what the predicate MEANS, not a learned claim about the
            # corpus; the learned part is that the binding holds in its absence.
            cu, ru = column_unit(cell), row_unit(cell)
            nearer = [("column", cu), ("row_label", ru)]
            differing = [(w, u) for w, u in nearer if u is not None and u != scale]
            got = bool(differing)
            ev = {"column_unit": cu, "row_label_unit": ru, "table_scale": scale,
                  "differing": differing,
                  "reading": "the cell's own column header or row label states a different money scale",
                  "reading_provenance": "GIVEN (the proposer's stated withdrawal_reason)"}
            if differing and want is False:
                conflict = {"kind": "nearer_scale_annotation", "table_scale": scale,
                            "nearer": differing}
        else:
            got = None
            unknown = True
            ev = {"note": f"no evaluator for {pred}"}
        clauses.append({**cl, "observed": got, "evidence_pointer": ev,
                        "satisfied": (None if got is None else (bool(got) == bool(want)))})
        if got is None:
            unknown = True
        elif bool(got) != bool(want):
            ok = False
    if conflict is not None and ok:
        truth = IR.CONFLICT
    elif not ok:
        truth = IR.UNKNOWN      # the condition does not cover this cell: the contract is SILENT
    elif unknown:
        truth = IR.UNKNOWN
    else:
        truth = IR.HOLDS
    return {"truth": truth, "scale": scale if scale in SCALE_EXP else None, "clauses": clauses,
            "conflict": conflict, "applicable": ok and conflict is None}


def stage_link(binding: dict, asked_scale: str | None, used_exp: int | None) -> dict:
    """The obligation the binding witnesses, resolved. An UNKNOWN binding may never make a violation.

    `unit_match` is the campaign's own obligation name, and the campaign's stage layer resolved it from
    the operand alone. Here it is resolved THROUGH the binding, so that withdrawing the binding
    withdraws the witness.
    """
    if binding["truth"] in (IR.UNKNOWN,):
        return {"obligation": "unit_match", "state": "UNKNOWN", "outcome": UNKNOWN_OUT,
                "why": "the binding is not established for this cell, so there is no witness",
                "required_exp": None, "used_exp": used_exp}
    if binding["truth"] == IR.CONFLICT:
        return {"obligation": "unit_match", "state": "UNKNOWN", "outcome": UNKNOWN_OUT,
                "why": f"conflicting evidence: {binding['conflict']}",
                "required_exp": None, "used_exp": used_exp}
    frm, to = binding.get("scale"), asked_scale
    if frm not in SCALE_EXP or to not in SCALE_EXP:
        return {"obligation": "unit_match", "state": "UNKNOWN", "outcome": UNKNOWN_OUT,
                "why": f"the asked scale {to!r} or the bound scale {frm!r} is not a money scale",
                "required_exp": None, "used_exp": used_exp}
    required = SCALE_EXP[frm] - SCALE_EXP[to]      # MATH: 10^required converts frm -> to
    if used_exp is None:
        return {"obligation": "unit_match", "state": "OUTSTANDING", "outcome": UNKNOWN_OUT,
                "why": f"the binding requires a factor of 10^{required} and no step states one",
                "required_exp": required, "used_exp": None}
    if used_exp == required:
        return {"obligation": "unit_match", "state": "DISCHARGED", "outcome": "discharged",
                "why": f"the actor used 10^{used_exp}, which is what {frm}->{to} requires",
                "required_exp": required, "used_exp": used_exp}
    return {"obligation": "unit_match", "state": "VIOLATED", "outcome": ACTOR_ERROR,
            "why": (f"the binding gives this cell scale {frm} and the question asks {to}, which requires "
                    f"10^{required}; the actor used 10^{used_exp}, wrong by 10^{used_exp - required}"),
            "required_exp": required, "used_exp": used_exp}


def check_attempt(packet: dict, m: dict, assumptions=("flat_header_authoritative",)) -> dict:
    """One attempt, checked prefix-only, with the prediction committed before the later steps are read."""
    steps = packet.get("steps") or []
    rev = Revealer(steps)
    asked = None
    q = str(packet.get("question") or "")
    mm = _SCALE_WORD.search(q)
    if mm:
        asked = CELLS.canon_scale(mm.group(1))
    out = {"item": packet.get("id"), "trace_key": packet.get("trace_key"),
           "trace_sha256": packet.get("trace_sha256"), "asked_scale": asked,
           "asked_scale_provenance": IR.TASK, "predictions": [], "problems": []}
    contracts = IR.active(m)
    if not contracts:
        out["problems"].append("no active contract")
        return out

    first_operand_step = None
    for s in steps:
        if s.get("kind") == "message" and operands_in(s):
            first_operand_step = s
            break
    if first_operand_step is None:
        out["problems"].append("the actor wrote no number in any message")
        return out
    i = int(first_operand_step.get("i") or 0)

    prefix = rev.upto(i)
    supplied = packet.get("oracle_pages") or []
    tabs = tables_in(prefix, supplied)
    cells = cells_in(prefix, supplied, assumptions)
    ops = operands_in(first_operand_step)

    for c in contracts:
        for op in ops[:12]:
            hits = [cl for cl in cells if abs(cl["value"] - op["value"]) <= 1e-9]
            if not hits:
                continue
            cell = hits[0]
            table = next((t for t in tabs
                          if CELLS.canon_scale(t.get("scale") or "") == CELLS.canon_scale(
                              cell.get("scale") or "")), None)
            if table is None:
                out["predictions"].append({"contract": f"{c['id']}@v{c['version']}", "operand": op["raw"],
                                           "outcome": EXTRACTION_ERROR,
                                           "why": "the operand matched a cell whose table could not be "
                                                  "re-bound from the prefix"})
                continue
            binding = evaluate_binding(c, table, cell)
            rev.open_prediction = True
            pred = {
                "contract": f"{c['id']}@v{c['version']}",
                "committed_at_step": i,
                "prefix_steps": len(prefix),
                "operand": {"raw": op["raw"], "value": op["value"], "context": op["context"][:200]},
                "cell": {k: cell.get(k) for k in ("raw", "value", "row_label", "column", "scale",
                                                  "period_text", "column_basis", "source")},
                "table_scale": table.get("scale"), "table_origin": table.get("origin"),
                "binding": binding,
                "requirement": None, "resolution": None,
            }
            link_pre = stage_link(binding, asked, None)
            pred["requirement"] = {"obligation": link_pre["obligation"],
                                   "required_exp": link_pre["required_exp"],
                                   "why": link_pre["why"]}
            out["predictions"].append(pred)
            rev.open_prediction = False

            later = rev.beyond(i + 1)
            used = None
            for s in later:
                if s.get("kind") != "message":
                    continue
                e = stated_factor_exp(str(s.get("text") or s.get("content") or ""))
                if e is not None:
                    used = e
                    break
            pred["resolution"] = stage_link(binding, asked, used)
            pred["resolution"]["read_from_steps_after"] = i
    return out


def score(results: list) -> dict:
    counts = {}
    for r in results:
        for p in r.get("predictions") or []:
            o = (p.get("resolution") or {}).get("outcome") or p.get("outcome") or UNKNOWN_OUT
            counts[o] = counts.get(o, 0) + 1
    return counts


def export_for_lean(results: list) -> list:
    """The finite evidence the Lean file re-decides: one row per committed prediction."""
    rows = []
    for r in results:
        for p in r.get("predictions") or []:
            res = p.get("resolution") or {}
            rows.append({
                "item": r.get("item"), "trace_key": r.get("trace_key"),
                "contract": p.get("contract"), "operand": (p.get("operand") or {}).get("raw"),
                "binding": (p.get("binding") or {}).get("truth"),
                "required_exp": res.get("required_exp"), "used_exp": res.get("used_exp"),
                "state": res.get("state"), "outcome": res.get("outcome"),
            })
    return rows
