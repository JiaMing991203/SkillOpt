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

import oqa_cc_extract as X  # noqa: E402
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


# AMENDMENT B-2. The extraction is `oqa_cc_extract`, which REUSES `oqa_lc_cells` and `oqa_lc_chain` and
# corrects seven things above them. The three thin wrappers below are kept so the call sites read the
# same, and the weaker local readers they used to contain are gone:
#
#   operands_in  took ANY number out of a message (it bound the years 1942 and 07 on UID0014)
#   cells_in     offered the row-LABEL column as a data cell (so "1942" was a cell)
#   the table    was any table sharing the cell's scale, not the table the cell came from

def tables_in(steps: list, supplied: list) -> list:
    return X.tables_in(steps, supplied)


def cells_in(steps: list, supplied: list, assumptions) -> list:
    kept, _dropped = X.data_cells(steps, supplied, assumptions)
    return kept


def table_of(cell: dict, tabs: list) -> dict | None:
    """The table the cell CAME FROM, by (source, table index) - not any table with the same scale."""
    return X.table_of(cell, tabs)


def operands_in(step: dict) -> list:
    """Monetary operands only. Years, dates, list numbers, footnotes, percentages and prose digits are
    rejected by name, and an unsettled four-digit number is AMBIGUOUS and not used."""
    return X.monetary_operands(step)


def column_unit(cell: dict) -> str | None:
    """A unit the cell's own COLUMN header states - only when that column was actually observed."""
    if not cell.get("column") or cell.get("column_basis") == CELLS.COL_UNKNOWN:
        return None
    return X.scale_word(cell.get("column"))


def row_unit(cell: dict) -> str | None:
    return X.scale_word(cell.get("row_label"))


def stated_factor_exp(text: str) -> int | None:
    return X.stated_factor_exp(text)


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
            # B-2. This read `(A and B) or True`, which is True for every input: the clause asserted
            # nothing at all. It now compares the cell's own table KEY with the table being evaluated.
            got = bool(cell.get("table_key")) and cell.get("table_key") == table.get("key")
            ev = {"cell_table_key": cell.get("table_key"), "table_key": table.get("key"),
                  "cell_table_index": cell.get("table_index")}
        elif pred == "column_header_states_unit":
            near = X.nearer_scale(cell, table)
            u = near["scopes"].get("column_unit")
            if not near["scopes"].get("column_observed"):
                got = None          # the column was never bound: nothing is asserted either way
            else:
                got = u is not None and u != scale
            ev = {"column": cell.get("column"), "column_unit": u,
                  "column_observed": near["scopes"].get("column_observed"),
                  "column_basis": cell.get("column_basis")}
            if got and u is not None and u != scale:
                conflict = {"kind": "column_header_states_another_unit", "table_scale": scale,
                            "column_unit": u, "column": cell.get("column")}
        elif pred == "row_label_states_scale":
            u = row_unit(cell)
            got = (None if not str(cell.get("row_label") or "").strip()
                   else (u is not None and u != scale))
            ev = {"row_label": cell.get("row_label"), "row_unit": u,
                  "row_label_observed": bool(str(cell.get("row_label") or "").strip())}
        elif pred == "nearer_scale_between":
            # IMPLEMENTED FROM THE PROPOSER'S OWN STATED READING, and that provenance matters. The
            # proposal (job 66341664) gave the withdrawal_reason: "A nearer_scale_between observation,
            # such as a column header or row label with its own scale annotation closer to the cell,
            # withdraws the table-level binding." The reading is therefore GIVEN; only "the binding
            # holds in its absence" is learned.
            #
            # B-2 MAKES IT A TRI-STATE. `False` previously followed from an EMPTY column field, which
            # happens when the header is missing, unreadable or unbound - not because the column carries
            # no unit. An unobserved scope cannot witness an absence, so that case is now UNKNOWN and
            # the contract stays silent about the cell instead of claiming it.
            near = X.nearer_scale(cell, table)
            got = (None if near["state"] == X.UNKNOWN else (near["state"] == X.PRESENT))
            ev = {**near["scopes"], "state": near["state"], "differing": near["differing"],
                  "why": near["why"],
                  "reading": "the cell's own column header or row label states a different money scale",
                  "reading_provenance": "GIVEN (the proposer's stated withdrawal_reason)"}
            if near["state"] == X.PRESENT and want is False:
                conflict = {"kind": "nearer_scale_annotation", "table_scale": scale,
                            "nearer": near["differing"]}
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
    # CONFLICT TAKES PRECEDENCE over "the condition is not met", and the test that caught this is the
    # reason. The same observation that withdraws the binding - the cell's own column header stating
    # another unit - also fails the condition, so ordering "not met" first flattened every withdrawal
    # into silence and `find_counterexample` could never see one. A withdrawal is informative and must
    # be reported as what it is.
    if conflict is not None:
        truth = IR.CONFLICT
    elif not ok:
        truth = IR.UNKNOWN      # the condition does not cover this cell: the contract is SILENT
    elif unknown:
        truth = IR.UNKNOWN
    else:
        truth = IR.HOLDS
    return {"truth": truth, "scale": scale if scale in SCALE_EXP else None, "clauses": clauses,
            "conflict": conflict, "applicable": ok and conflict is None}


def stage_link(binding: dict, asked_scale: str | None, conv: dict | None) -> dict:
    """The obligation the binding witnesses, resolved. An UNKNOWN binding may never make a violation.

    `unit_match` is the campaign's own obligation name, and the campaign's stage layer resolved it from
    the operand alone. Here it is resolved THROUGH the binding, so that withdrawing the binding withdraws
    the witness. `conv` is `oqa_cc_extract.conversion(...)` for this operand, or None before the
    prediction is committed.

    B-2 adds three states the B-1 version could not express:
      * REOPENED        a conflict withdraws the binding, so the obligation is open again - and that is
                        NOT an actor error. The campaign's complaint was the reverse: an outstanding
                        obligation reported as if the agent had done something wrong.
      * no conversion   when the bound scale already equals the asked scale the required factor is 10^0.
                        Demanding an explicit "x 1" was a false alarm.
      * stated but
        unused          a correct factor that never reaches a later value is not a completed conversion.
    """
    used_exp = (conv or {}).get("stated_exp")
    base = {"obligation": "unit_match", "used_exp": used_exp}
    if binding["truth"] == IR.UNKNOWN:
        return {**base, "state": "UNKNOWN", "outcome": UNKNOWN_OUT, "required_exp": None,
                "why": "the binding is not established for this cell, so there is no witness"}
    if binding["truth"] == IR.CONFLICT:
        return {**base, "state": "REOPENED", "outcome": UNKNOWN_OUT, "required_exp": None,
                "withdrawn": True,
                "why": (f"conflicting evidence withdraws the binding ({binding['conflict']}), so the "
                        f"obligation is open again; nothing here says the actor did anything wrong")}
    if binding["truth"] != IR.HOLDS:
        # Only a HOLDING binding may witness an obligation. `evaluate_binding` returns HOLDS, UNKNOWN or
        # CONFLICT, so this is unreachable today - it is spelled out because the Lean encoding has the
        # same clause, and two implementations that agree only on the reachable cases are not checked
        # against each other.
        return {**base, "state": "UNKNOWN", "outcome": UNKNOWN_OUT, "required_exp": None,
                "why": f"the binding is {binding['truth']}, which cannot witness an obligation"}
    frm, to = binding.get("scale"), asked_scale
    required = X.required_exp(frm, to)
    if required is None:
        return {**base, "state": "UNKNOWN", "outcome": UNKNOWN_OUT, "required_exp": None,
                "why": f"the asked scale {to!r} or the bound scale {frm!r} is not a money scale"}
    out = {**base, "required_exp": required}
    if conv is None:                       # before the observation: the REQUIREMENT only
        return {**out, "state": "PREDICTED", "outcome": UNKNOWN_OUT,
                "why": (f"{frm} -> {to} requires a factor of 10^{required}"
                        if required else
                        f"the cell is already in the asked scale ({to}), so no conversion is required")}
    v = conv.get("verdict")
    if required == 0:
        if used_exp in (None, 0):
            return {**out, "state": "DISCHARGED", "outcome": "discharged",
                    "why": (f"the cell is already in the asked scale ({to}); no factor is required and "
                            f"none was applied"),
                    "converted_value_used": conv.get("converted_value_used")}
        return {**out, "state": "VIOLATED", "outcome": ACTOR_ERROR,
                "why": (f"the cell is already in the asked scale ({to}), so the required factor is "
                        f"10^0, and the actor applied 10^{used_exp}")}
    if v in ("correct_conversion", "correct_factor_stated"):
        if conv.get("converted_value_used") is False:
            return {**out, "state": "OUTSTANDING", "outcome": UNKNOWN_OUT,
                    "why": (f"a correct factor of 10^{required} is stated, but the converted value does "
                            f"not appear in any later step, so the conversion was not carried through"),
                    "conversion": v}
        return {**out, "state": "DISCHARGED", "outcome": "discharged",
                "why": conv.get("why") or f"the actor used 10^{required}, which {frm}->{to} requires",
                "conversion": v}
    if v in ("incorrect_conversion", "incorrect_factor_stated"):
        wrong_by = (used_exp - required) if used_exp is not None else None
        return {**out, "state": "VIOLATED", "outcome": ACTOR_ERROR,
                "why": (f"the binding gives this cell scale {frm} and the question asks {to}, which "
                        f"requires 10^{required}; " + str(conv.get("why") or "")
                        + (f" wrong by 10^{wrong_by}" if wrong_by else "")),
                "conversion": v}
    if v == "unknown_conversion":
        return {**out, "state": "UNKNOWN", "outcome": UNKNOWN_OUT, "conversion": v,
                "why": conv.get("why") or "the written conversion's source scale cannot be established"}
    return {**out, "state": "OUTSTANDING", "outcome": UNKNOWN_OUT, "conversion": v,
            "why": conv.get("why") or f"the binding requires 10^{required} and no step states one"}


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
           "asked_scale_provenance": IR.TASK, "predictions": [], "problems": [],
           "extraction": {}}
    contracts = IR.active(m)
    if not contracts:
        out["problems"].append("no active contract")
        return out

    # WHICH MESSAGE FIRST CARRIES A MONETARY OPERAND. B-1 took the first message with ANY number, which
    # on several traces was a planning message full of list numbers, and then bound whatever it found.
    first_operand_step = None
    rejected_before = []
    for st in steps:
        if st.get("kind") != "message":
            continue
        seen = X.operands(st)
        if any(o["is_operand"] for o in seen):
            first_operand_step = st
            break
        rejected_before += [{"step": st.get("i"), "raw": o["raw"], "verdict": o["verdict"],
                             "reason": o["reason"]} for o in seen]
    out["extraction"]["numbers_rejected_before_the_first_operand"] = rejected_before[:40]
    if first_operand_step is None:
        out["problems"].append("the actor wrote no monetary operand in any message")
        return out
    i = int(first_operand_step.get("i") or 0)

    prefix = rev.upto(i)
    supplied = packet.get("oracle_pages") or []
    tabs = tables_in(prefix, supplied)
    cells, dropped = X.data_cells(prefix, supplied, assumptions)
    ops_all = X.operands(first_operand_step)
    ops = [o for o in ops_all if o["is_operand"]]
    out["extraction"].update({
        "n_tables": len(tabs), "n_data_cells": len(cells),
        "n_row_label_cells_dropped": len(dropped),
        "n_cells_without_a_bound_header": sum(1 for c in cells if not c.get("header_known")),
        "operands_considered": [{"raw": o["raw"], "verdict": o["verdict"], "reason": o["reason"],
                                 "is_operand": o["is_operand"]} for o in ops_all[:40]],
    })

    for c in contracts:
        for op in ops[:12]:
            match = X.match_operand(op, cells)
            if match["status"] in ("no_cell_match", "ambiguous_cell_match"):
                out["predictions"].append({
                    "contract": f"{c['id']}@v{c['version']}", "operand": op["raw"],
                    "outcome": EXTRACTION_ERROR, "match_status": match["status"],
                    "candidates": match["candidates"],
                    "why": ("the operand matches no data cell in the prefix"
                            if match["status"] == "no_cell_match" else
                            "the operand matches several data cells that disagree about scale, column "
                            "or row, so the contract stays silent rather than picking one")})
                continue
            cell = match["cell"]
            table = table_of(cell, tabs)
            if table is None:
                out["predictions"].append({"contract": f"{c['id']}@v{c['version']}", "operand": op["raw"],
                                           "outcome": EXTRACTION_ERROR,
                                           "why": "the cell's own table could not be re-bound from the "
                                                  "prefix by its (source, table index) key"})
                continue
            binding = evaluate_binding(c, table, cell)
            rev.open_prediction = True
            link_pre = stage_link(binding, asked, None)
            pred = {
                "contract": f"{c['id']}@v{c['version']}",
                "committed_at_step": i,
                "prefix_steps": len(prefix),
                "operand": {"raw": op["raw"], "value": op["value"], "verdict": op["verdict"],
                            "reason": op["reason"], "context": op["context"][:200]},
                "match_status": match["status"],
                "cell": {k: cell.get(k) for k in ("raw", "value", "row_label", "column", "scale",
                                                  "period_text", "column_basis", "source",
                                                  "table_key", "col_index", "header_known")},
                "table_scale": table.get("scale"), "table_origin": table.get("origin"),
                "table_key": table.get("key"),
                "binding": binding,
                "requirement": {"obligation": link_pre["obligation"],
                                "required_exp": link_pre["required_exp"],
                                "state": link_pre["state"], "why": link_pre["why"]},
                "resolution": None,
            }
            out["predictions"].append(pred)
            rev.open_prediction = False

            later = rev.beyond(i + 1)
            conv = X.conversion(op, later, link_pre.get("required_exp"))
            pred["conversion"] = conv
            pred["resolution"] = stage_link(binding, asked, conv)
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
            if not res:
                continue        # an extraction error has no resolution to re-decide
            rows.append({
                "item": r.get("item"), "trace_key": r.get("trace_key"),
                "contract": p.get("contract"), "operand": (p.get("operand") or {}).get("raw"),
                "binding": (p.get("binding") or {}).get("truth"),
                "required_exp": res.get("required_exp"), "used_exp": res.get("used_exp"),
                "conversion": res.get("conversion"),
                "converted_value_used": res.get("converted_value_used"),
                "withdrawn": bool(res.get("withdrawn")),
                "state": res.get("state"), "outcome": res.get("outcome"),
            })
    return rows
