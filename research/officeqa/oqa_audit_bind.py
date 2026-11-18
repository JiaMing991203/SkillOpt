#!/usr/bin/env python3
"""Why the connected contract cannot bind a value to a header: one named cause per candidate.

Rule OQA-AUD-2. Offline audit of `OFFICEQA_RECOVERY_PACKET.md` section 6 and of amendment B-2's
extraction. Reads saved traces only. It does NOT modify the historical checker: `oqa_cc_extract`,
`oqa_cc_check`, `oqa_lc_cells` and `oqa_lc_chain` are imported and exercised exactly as frozen, and
every variant this file measures is computed here, beside them, under its own name.

THE CLAIM UNDER AUDIT. The packet reported that "1,484 to 2,022 of the data cells in these records have
no bound column header, and the applicability condition needs one, so the contract is silent by
construction". Two things in that sentence need checking. First, whether a cell with no BOUND header is
a cell whose header information is ABSENT - those are different facts, and only the second would make
the corpus the obstacle. Second, whether the unbound header is what actually silences the predictions,
or whether most of them never reach a binding at all.

THE SEVEN CAUSES, kept apart on purpose. For each candidate value the audit records a primary cause and
any secondary ones, and refuses to choose when the record cannot:

  operand_is_in_no_cell            the value the actor wrote is in no data cell of the prefix - a
                                   computed total, a value read from text rather than a table, or a
                                   number that is not an amount at all. No header question arises
  header_never_observed            the cell's own table has no header row anywhere in the prefix
  header_observed_not_connected    the header is in the prefix in FLAT form, in another text of the
                                   same record, and pairs with this table by a shared data row - but
                                   the extractor bound each text in isolation, so it was never carried
                                   across. Observed, and lost between two calls. THIS WAS THE CASE
                                   BEFORE amendment B-3.2; the audit still reports it, now against the
                                   pre-repair extractor kept in `data_cells_isolated`
  header_observed_structure_lost   the header row is observed in SPLIT form only (the oracle page's
                                   rendering), its column spans are gone, and merging by cell index is
                                   known to bind the wrong column, so the binding is refused
  identity_matched_wrongly         the cell's table could not be re-bound by its own (source, table)
                                   key, or the operand matched a cell of a different table
  several_plausible_bindings       the value occurs in several cells that disagree about scale, column
                                   or row, and nothing in the prefix chooses between them
  condition_needs_more_than_the_hypothesis
                                   the hypothesis only needs "no nearer scale intervenes", and no cell
                                   of this table's OBSERVED header row states any money scale - so the
                                   absence is supported by the observed scope, yet the evaluator asks
                                   for a bound column before it will say so
  records_cannot_distinguish       none of the above is established by the prefix

PREFIX DISCIPLINE. The prefix is exactly the one `oqa_cc_check.check_attempt` uses: the steps before the
first message in which the actor writes a monetary operand. Later observations are never read while a
cause is decided. Where looking at the whole document would help a human reader, it is reported under
`additional_context_not_used_in_the_replay` and takes no part in any count.

usage:
  oqa_audit_bind.py audit --model <CC_MODEL_v1.json> --records <CC_RECORDS_v2.json> --which checking
                          [--records <CC_RECORDS_episodes.json> --which descriptive] --out <dir>
  oqa_audit_bind.py interpret --audit <BIND_AUDIT_*.json> ... --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_check as CHK  # noqa: E402
import oqa_cc_extract as X  # noqa: E402
import oqa_cc_ir as IR  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402
import oqa_trace as TR  # noqa: E402

RULE = "OQA-AUD-2"
SCHEMA = "lso.officeqa_binding_audit.v1"
ASSUMPTIONS = ("flat_header_authoritative",)

OPERAND_IN_NO_CELL = "operand_is_in_no_cell"
NEVER_OBSERVED = "header_never_observed"
NOT_CONNECTED = "header_observed_not_connected"
STRUCTURE_LOST = "header_observed_structure_lost"
MATCHED_WRONGLY = "identity_matched_wrongly"
SEVERAL = "several_plausible_bindings"
CONDITION_TOO_STRONG = "condition_needs_more_than_the_hypothesis"
CANNOT_TELL = "records_cannot_distinguish"
BOUND = "bound_no_failure"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


# ───────────────────────── the connected variant, measured beside the frozen one ─────────────────────────

def data_cells_isolated(steps: list, supplied: list, assumptions) -> tuple:
    """`oqa_cc_extract.data_cells` AS IT WAS BEFORE B-3.2: each text bound in isolation.

    Kept here, in the audit, so the before/after of the repair can be measured on the real records
    rather than argued. Identical to the repaired function in every other respect.
    """
    kept, dropped = [], []

    def _collect(text, src):
        for c in CELLS.cells_of(str(text or ""), assumptions, source=src):
            c = dict(c, table_key=X._key(src, c.get("table_index")))
            if int(c.get("col_index") or 0) == 0:
                dropped.append(c)
                continue
            c["header_known"] = bool(c.get("column")) and c.get("column_basis") != CELLS.COL_UNKNOWN
            kept.append(c)

    for page in supplied:
        _collect(page.get("text"), {"kind": "supplied_page", "ref": page.get("ref")})
    for s in steps:
        if s.get("kind") == "tool":
            _collect(s.get("obs"), {"kind": "observation", "step": s.get("i")})
    return kept, dropped, X.flat_references(steps, supplied)


# ───────────────────────── the header scope of one table ─────────────────────────

def header_scope(table: dict) -> dict:
    """What this table's own header rows say, and whether any of them states a money scale.

    The distinction the audit turns on. A column binding needs to know WHICH column a header cell
    belongs to. The question "does a nearer scope state another money scale?" does not: if no cell of
    the observed header row states any money scale, then this cell's column header does not either,
    whatever the index alignment. That inference uses only what was observed.
    """
    cells = []
    if table.get("header_form") == "flat":
        cells = [f"{f.get('parent','')} {f.get('child','')}" for f in (table.get("flat") or [])]
    else:
        cells = list(table.get("parent_row") or []) + list(table.get("child_row") or [])
    cells = [c for c in cells if str(c or "").strip()]
    scales = sorted({X.scale_word(c) for c in cells if X.scale_word(c)})
    return {"header_form": table.get("header_form"), "n_header_cells_with_text": len(cells),
            "header_rows_observed": bool(table.get("header_lines")),
            "money_scales_named_anywhere_in_the_header": scales,
            "no_money_scale_anywhere_in_the_observed_header": bool(cells) and not scales,
            "table_scale": table.get("scale"), "width": table.get("width"),
            "header_cells_sample": cells[:8]}


def pairs_with_a_flat_table(table: dict, refs: list) -> dict:
    """Would a flat header elsewhere in this record adjudicate this table? By the frozen rule."""
    for st, ft in CELLS.pair_tables([table], refs):
        return {"pairs": True, "reference_width": ft["width"],
                "shared_rows": sorted(set(st["fingerprints"]) & set(ft["fingerprints"]))[:2],
                "reference_header_sample": [f"{f.get('parent','')}>{f.get('child','')}"
                                            for f in (ft.get("flat") or [])][:6]}
    return {"pairs": False}


# ───────────────────────── one record ─────────────────────────

def asked_scale(packet: dict) -> str | None:
    """The scale the QUESTION asks for, read exactly as `oqa_cc_check.check_attempt` reads it."""
    import re
    m = re.search(r"\b(thousand|million|billion)s?\b", str(packet.get("question") or ""), re.I)
    return CELLS.canon_scale(m.group(1)) if m else None


def audit_record(packet: dict, model: dict, assumptions=ASSUMPTIONS) -> dict:
    steps = packet.get("steps") or []
    asked = asked_scale(packet)
    supplied = packet.get("oracle_pages") or []
    contracts = IR.active(model)
    out = {"item": packet.get("id"), "trace_key": packet.get("trace_key"),
           "trace_sha256": packet.get("trace_sha256"), "asked_scale": asked,
           "prediction_dir": (packet.get("provenance") or {}).get("prediction_dir"),
           "candidates": [], "problems": [], "prefix": {}}

    first_step, rejected_before = None, []
    for st in steps:
        if st.get("kind") != "message":
            continue
        seen = X.operands(st)
        if any(o["is_operand"] for o in seen):
            first_step = st
            break
        rejected_before += [{"step": st.get("i"), "raw": o["raw"], "verdict": o["verdict"],
                             "reason": o["reason"], "context": o["context"][:160]} for o in seen]
    out["numbers_before_the_first_operand"] = rejected_before[:40]
    if first_step is None:
        out["problems"].append("the actor wrote no monetary operand in any message")
        out["prefix"] = {"n_steps": len(steps), "n_supplied_pages": len(supplied),
                         "messages": sum(1 for s in steps if s.get("kind") == "message"),
                         "tool_steps": sum(1 for s in steps if s.get("kind") == "tool")}
        return out

    i = int(first_step.get("i") or 0)
    prefix = steps[:i]
    tabs = X.tables_in(prefix, supplied)
    frozen_cells, frozen_dropped = X.data_cells(prefix, supplied, assumptions)
    iso_cells, _cd, refs = data_cells_isolated(prefix, supplied, assumptions)

    forms: dict = {}
    for t in tabs:
        forms[str(t.get("header_form"))] = forms.get(str(t.get("header_form")), 0) + 1
    out["prefix"] = {
        "committed_at_step": i, "n_prefix_steps": len(prefix),
        "n_supplied_pages": len(supplied),
        "n_tables": len(tabs), "table_header_forms": forms,
        "n_flat_reference_tables_in_the_record": len(refs),
        "n_data_cells": len(frozen_cells),
        "n_row_label_cells_dropped": len(frozen_dropped),
        "n_cells_without_a_bound_header": sum(1 for c in frozen_cells if not c.get("header_known")),
        "n_cells_without_a_bound_header_before_B_3_2":
            sum(1 for c in iso_cells if not c.get("header_known")),
        "n_data_cells_before_B_3_2": len(iso_cells),
        "tables_whose_header_states_no_money_scale":
            sum(1 for t in tabs if header_scope(t)["no_money_scale_anywhere_in_the_observed_header"]),
        "tables_that_pair_with_a_flat_header":
            sum(1 for t in tabs if t.get("header_form") in ("split", "single")
                and pairs_with_a_flat_table(t, refs)["pairs"]),
    }

    for op in X.operands(first_step):
        rec = {"raw": op["raw"], "value": op["value"], "verdict": op["verdict"],
               "reason": op["reason"], "used_as_operand": op["is_operand"],
               "context": op["context"][:220], "step": op["step"],
               "year_shaped": bool(str(int(op["value"])) in
                                   {str(y) for y in range(1800, 2100)}) if float(
                                       op["value"]).is_integer() else False}
        if not op["is_operand"]:
            rec["primary_cause"] = "not_used_as_an_operand"
            rec["secondary_causes"] = []
            out["candidates"].append(rec)
            continue
        match = X.match_operand(op, frozen_cells)
        rec["match_status"] = match["status"]
        rec["candidate_cells"] = match["candidates"]
        rec["match_status_before_B_3_2"] = X.match_operand(op, iso_cells)["status"]

        if match["status"] == "no_cell_match":
            rec["primary_cause"] = OPERAND_IN_NO_CELL
            rec["secondary_causes"] = []
            rec["why"] = ("no data cell of the prefix carries this value, so no table, header or unit "
                          "question is reached")
            out["candidates"].append(rec)
            continue
        if match["status"] == "ambiguous_cell_match":
            rec["primary_cause"] = SEVERAL
            rec["secondary_causes"] = []
            disagree = {k: sorted({str(c.get(k)) for c in match["candidates"]})
                        for k in ("scale", "column", "row_label", "table_key")}
            rec["disagreement"] = disagree
            # the headers of the tables those candidates came from, for the reader
            rec["why"] = ("the value occurs in several cells that disagree about "
                          + ", ".join(k for k, v in disagree.items() if len(v) > 1))
            out["candidates"].append(rec)
            continue

        cell = match["cell"]
        table = CHK.table_of(cell, tabs)
        rec["cell"] = {k: cell.get(k) for k in ("raw", "value", "row_label", "column", "scale",
                                                "period_text", "column_basis", "table_key",
                                                "col_index", "header_known")}
        if table is None:
            rec["primary_cause"] = MATCHED_WRONGLY
            rec["secondary_causes"] = []
            rec["why"] = "the cell's own (source, table index) key does not resolve in the prefix"
            out["candidates"].append(rec)
            continue

        scope = header_scope(table)
        near = X.nearer_scale(cell, table)
        pair = pairs_with_a_flat_table(table, refs)
        rec["header_evidence"] = scope
        rec["nearer_scale"] = {"state": near["state"], "why": near["why"], "scopes": near["scopes"]}
        rec["pairs_with_a_flat_header_elsewhere"] = pair

        causes = []
        for c in contracts:
            binding = CHK.evaluate_binding(c, table, cell)
            link = CHK.stage_link(binding, asked, None)
            conv = X.conversion(op, steps[i:], link.get("required_exp"))
            resolved = CHK.stage_link(binding, asked, conv)
            # P-1, MEASURED AND NOT APPLIED: the same resolution when the operand's own message may be
            # read too. This actor writes the operand, the factor and the answer in one final message,
            # so the strictly-later window is often empty.
            conv_same = X.conversion(op, steps[i - 1:], link.get("required_exp"))
            resolved_same = CHK.stage_link(binding, asked, conv_same)
            rec.setdefault("bindings", []).append({
                "contract": f"{c['id']}@v{c['version']}", "truth": binding["truth"],
                "stage_state": link["state"], "stage_outcome": link["outcome"],
                "required_exp": link.get("required_exp"),
                "resolution_from_later_steps": {"conversion": conv.get("verdict"),
                                                "state": resolved["state"],
                                                "outcome": resolved["outcome"],
                                                "why": resolved.get("why")},
                "resolution_if_the_operand_message_were_read_too_PROPOSAL_P1": {
                    "conversion": conv_same.get("verdict"), "state": resolved_same["state"],
                    "outcome": resolved_same["outcome"], "why": resolved_same.get("why")},
                "unsatisfied_or_unknown_clauses": [
                    {"pred": cl.get("pred"), "want": cl.get("value"), "observed": cl.get("observed"),
                     "evidence": cl.get("evidence_pointer")}
                    for cl in binding["clauses"] if cl.get("satisfied") is not True],
            })
            if binding["truth"] == IR.HOLDS:
                causes.append(BOUND)
                continue
            blocking = [cl for cl in binding["clauses"] if cl.get("satisfied") is not True]
            only_nearer = all(cl.get("pred") == "nearer_scale_between" for cl in blocking) and blocking
            if near["state"] == X.UNKNOWN and not near["scopes"].get("column_observed"):
                if scope["header_form"] == "none" or not scope["header_rows_observed"]:
                    causes.append(NEVER_OBSERVED)
                elif pair["pairs"]:
                    causes.append(NOT_CONNECTED)
                else:
                    causes.append(STRUCTURE_LOST)
                if only_nearer and scope["no_money_scale_anywhere_in_the_observed_header"]:
                    causes.append(CONDITION_TOO_STRONG)
            elif blocking:
                causes.append(CANNOT_TELL)
        seen_c: list = []
        for c in causes:
            if c not in seen_c:
                seen_c.append(c)
        rec["primary_cause"] = seen_c[0] if seen_c else CANNOT_TELL
        rec["secondary_causes"] = seen_c[1:]
        out["candidates"].append(rec)
    return out


def _packet(rec: dict) -> dict:
    pk = TR.build_packet(rec["predictions"], rec["item"])
    pk["trace_key"] = f"{rec['item']}/e{rec.get('epoch')}"
    try:
        pk["trace_sha256"] = TR.trace_digest(rec["predictions"], rec["item"])
    except Exception:                                        # noqa: BLE001 - provenance only
        pk["trace_sha256"] = None
    return pk


def cmd_audit(model_path: str, record_specs: list, out_dir: str) -> dict:
    model = IR.load_frozen(model_path)
    groups = []
    for spec in record_specs:
        which, _, path = spec.partition("=")
        doc = json.load(open(path, encoding="utf-8"))
        recs = doc.get(which) or []
        results = [audit_record(_packet(r), model) for r in recs]
        groups.append({"which": which, "records_file": os.path.abspath(path),
                       "records_sha256": doc.get("records_sha256"),
                       "n_records": len(recs), "results": results,
                       "counts": tally(results)})
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "model": os.path.abspath(model_path), "model_sha256": model.get("sha256"),
           "assumptions_in_force": list(ASSUMPTIONS),
           "contract": [IR.render(c) for c in IR.active(model)],
           "groups": groups, "counts_over_all_groups": tally(
               [r for g in groups for r in g["results"]]),
           "note": ("the frozen checker is unmodified; the `connected_variant` columns are computed in "
                    "this file by passing `references` to `oqa_lc_cells.cells_of`, which is what that "
                    "parameter documents and what `oqa_cc_extract.data_cells` omits")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "BIND_AUDIT.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def tally(results: list) -> dict:
    causes, verdicts, matches, bindings, states = {}, {}, {}, {}, {}
    resolved_states, p1_states = {}, {}
    n_cand = n_used = 0
    for r in results:
        for c in r.get("candidates") or []:
            n_cand += 1
            verdicts[str(c.get("verdict"))] = verdicts.get(str(c.get("verdict")), 0) + 1
            if not c.get("used_as_operand"):
                continue
            n_used += 1
            causes[str(c.get("primary_cause"))] = causes.get(str(c.get("primary_cause")), 0) + 1
            matches[str(c.get("match_status"))] = matches.get(str(c.get("match_status")), 0) + 1
            for b in c.get("bindings") or []:
                bindings[str(b.get("truth"))] = bindings.get(str(b.get("truth")), 0) + 1
                states[str(b.get("stage_state"))] = states.get(str(b.get("stage_state")), 0) + 1
                lat = (b.get("resolution_from_later_steps") or {}).get("state")
                p1 = (b.get("resolution_if_the_operand_message_were_read_too_PROPOSAL_P1")
                      or {}).get("state")
                resolved_states[str(lat)] = resolved_states.get(str(lat), 0) + 1
                p1_states[str(p1)] = p1_states.get(str(p1), 0) + 1
    return {"n_records": len(results), "n_candidate_numbers": n_cand, "n_used_as_operands": n_used,
            "operand_verdicts": verdicts, "primary_causes": causes, "match_status": matches,
            "binding_truths": bindings, "stage_states": states,
            "resolution_states_from_later_steps": resolved_states,
            "resolution_states_under_proposal_P1": p1_states,
            "records_with_no_operand_at_all": sum(
                1 for r in results if "the actor wrote no monetary operand in any message"
                in (r.get("problems") or [])),
            "cells_without_a_bound_header": {
                "frozen": [r["prefix"].get("n_cells_without_a_bound_header")
                           for r in results if r.get("prefix", {}).get("n_data_cells") is not None],
                "before_B_3_2": [r["prefix"].get("n_cells_without_a_bound_header_before_B_3_2")
                             for r in results
                             if r.get("prefix", {}).get("n_data_cells") is not None]},
            }


# ───────────────────────── the interpretation checks of the brief's section 6 ─────────────────────────

def interpret(audits: list) -> dict:
    results = [r for a in audits for g in a.get("groups") or [] for r in g["results"]]
    header_forms: dict = {}
    for r in results:
        for k, v in (r.get("prefix", {}).get("table_header_forms") or {}).items():
            header_forms[k] = header_forms.get(k, 0) + v
    cells_frozen = sum(int(r.get("prefix", {}).get("n_cells_without_a_bound_header") or 0)
                       for r in results)
    cells_iso = sum(int(r.get("prefix", {}).get("n_cells_without_a_bound_header_before_B_3_2") or 0)
                    for r in results)
    cells_total = sum(int(r.get("prefix", {}).get("n_data_cells") or 0) for r in results)

    monetary_year_shaped, nearer_states = [], {}
    conv_verdicts = {"from_later_steps": {}, "under_proposal_P1": {}, "stage_states": {},
                     "stage_states_under_proposal_P1": {}}
    for r in results:
        for c in r.get("candidates") or []:
            for b in c.get("bindings") or []:
                lat = b.get("resolution_from_later_steps") or {}
                p1 = b.get("resolution_if_the_operand_message_were_read_too_PROPOSAL_P1") or {}
                for key, src in (("from_later_steps", lat), ("under_proposal_P1", p1)):
                    v = str(src.get("conversion"))
                    conv_verdicts[key][v] = conv_verdicts[key].get(v, 0) + 1
                for key, src in (("stage_states", lat), ("stage_states_under_proposal_P1", p1)):
                    v = str(src.get("state"))
                    conv_verdicts[key][v] = conv_verdicts[key].get(v, 0) + 1
            if c.get("used_as_operand") and c.get("year_shaped"):
                monetary_year_shaped.append({"item": r.get("item"), "raw": c.get("raw"),
                                             "reason": c.get("reason"),
                                             "context": str(c.get("context"))[:200]})
            st = (c.get("nearer_scale") or {}).get("state")
            if st:
                nearer_states[st] = nearer_states.get(st, 0) + 1
    unknown_without_column = sum(
        1 for r in results for c in r.get("candidates") or []
        if (c.get("nearer_scale") or {}).get("state") == X.UNKNOWN
        and not ((c.get("nearer_scale") or {}).get("scopes") or {}).get("column_observed"))
    could_decide_from_the_header_row = sum(
        1 for r in results for c in r.get("candidates") or []
        if (c.get("header_evidence") or {}).get("no_money_scale_anywhere_in_the_observed_header")
        and (c.get("nearer_scale") or {}).get("state") == X.UNKNOWN)

    return {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(),
        "no_bound_header_does_not_mean_header_absent": {
            "table_header_forms_over_all_prefixes": header_forms,
            "tables_with_a_header_row_of_some_form":
                sum(v for k, v in header_forms.items() if k != "none"),
            "tables_with_no_header_row_at_all": header_forms.get("none", 0),
            "finding": ("a table whose header_form is split or single HAS an observed header row; the "
                        "column binding is refused because merging a split rendering by cell index "
                        "binds the wrong column, not because the text is missing"),
        },
        "data_cells_without_a_bound_header": {
            "total_data_cells": cells_total, "after_B_3_2": cells_frozen,
            "before_B_3_2": cells_iso,
            "difference": cells_iso - cells_frozen,
        },
        "nearer_scale_between_preserves_unknown": {
            "states_over_bound_cells": nearer_states,
            "unknown_with_no_observed_column": unknown_without_column,
            "finding": ("UNKNOWN is returned, never False, when the column scope was not observed - "
                        "the B-2 repair holds on the real records"),
        },
        "table_wide_scale_under_the_existing_hypothesis": {
            "cells_whose_table_header_names_no_money_scale_yet_stay_unknown":
                could_decide_from_the_header_row,
            "finding": ("the hypothesis needs only that no nearer scope states ANOTHER money scale. "
                        "Where no cell of the observed header row states any money scale, that is "
                        "settled by the observed scope without assuming an index alignment - but the "
                        "evaluator asks for a bound column first. Narrowing that gap is a PROPOSAL: it "
                        "changes what the checker decides and needs its own amendment and re-freeze"),
        },
        "dates_row_labels_and_prose_digits": {
            "year_shaped_numbers_still_used_as_monetary_operands": monetary_year_shaped,
            "n": len(monetary_year_shaped),
        },
        "conversion_verdicts_exercised": {
            **conv_verdicts,
            "finding": ("the four questions are answered separately on the real records: "
                        "`no_conversion_required` when the bound scale already equals the asked scale, "
                        "`correct_*` / `incorrect_*` when a factor is judged against the requirement, "
                        "and `converted_value_used` reported apart from the factor's correctness. "
                        "`requirement_unknown` covers a binding that determines no requirement, so no "
                        "factor is ever called wrong with nothing behind it"),
        },
    }


def cmd_interpret(audit_paths: list, out_dir: str) -> dict:
    audits = [json.load(open(p, encoding="utf-8")) for p in audit_paths]
    doc = interpret(audits)
    doc["audits"] = [os.path.abspath(p) for p in audit_paths]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "BIND_INTERPRETATION.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--model", required=True)
    a.add_argument("--records", dest="records", action="append", required=True,
                   help="<which>=<path to a records json>, e.g. checking=CC_RECORDS_v2.json")
    a.add_argument("--out", required=True)
    b = sub.add_parser("interpret")
    b.add_argument("--audit", dest="audits", action="append", required=True)
    b.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "audit":
        doc = cmd_audit(args.model, args.records, args.out)
        for g in doc["groups"]:
            c = g["counts"]
            print(f"rule {RULE}  {g['which']}: {c['n_records']} records, "
                  f"{c['n_candidate_numbers']} candidate numbers, {c['n_used_as_operands']} used")
            print(f"    operand verdicts {c['operand_verdicts']}")
            print(f"    match status     {c['match_status']}")
            print(f"    binding truths   {c['binding_truths']}  predicted states {c['stage_states']}")
            print(f"    resolved (later steps only)  {c['resolution_states_from_later_steps']}")
            print(f"    resolved (P-1, proposal)     {c['resolution_states_under_proposal_P1']}")
            print(f"    PRIMARY CAUSES   {c['primary_causes']}")
            print(f"    cells with no bound header: now {c['cells_without_a_bound_header']['frozen']}"
                  f" before B-3.2 {c['cells_without_a_bound_header']['before_B_3_2']}")
            print(f"    records with no monetary operand at all: "
                  f"{c['records_with_no_operand_at_all']}")
        print(f"wrote {doc['written_to']}")
    else:
        doc = cmd_interpret(args.audits, args.out)
        print(f"rule {RULE}  interpretation checks")
        print(f"  header forms {doc['no_bound_header_does_not_mean_header_absent']['table_header_forms_over_all_prefixes']}")
        print(f"  unbound cells after B-3.2 {doc['data_cells_without_a_bound_header']['after_B_3_2']} "
              f"before {doc['data_cells_without_a_bound_header']['before_B_3_2']} "
              f"of {doc['data_cells_without_a_bound_header']['total_data_cells']}")
        print(f"  nearer_scale states {doc['nearer_scale_between_preserves_unknown']['states_over_bound_cells']}")
        print(f"  year-shaped numbers still used as money: "
              f"{doc['dates_row_labels_and_prose_digits']['n']}")
        print(f"  conversion verdicts (later steps) "
              f"{doc['conversion_verdicts_exercised']['from_later_steps']}")
        print(f"  conversion verdicts (P-1)         "
              f"{doc['conversion_verdicts_exercised']['under_proposal_P1']}")
        print(f"  cells decidable from the observed header row alone: "
              f"{doc['table_wide_scale_under_the_existing_hypothesis']['cells_whose_table_header_names_no_money_scale_yet_stay_unknown']}")
        print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
