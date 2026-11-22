#!/usr/bin/env python3
"""The D-2 extraction/label correction, replayed over the frozen record set. Rule OQA-LN-1.

WHAT THIS IS, AND WHAT IT IS NOT. Amendment D-2 stops a numeral inside label text from occupying a
`(document, line, field)` position. This module replays the SAME 48 frozen attempts through the SAME
two frozen learned models under both extraction modes and reports every difference.

It is an EXTRACTION/LABEL CORRECTION. If agreement with the resolver improves, or the learner's one
counterexample disappears, that is NOT empirical learner repair and NOT fresh confirmation: the same
model is being scored against a changed measurement, on records it has already seen or whose verdicts
have already been read once.

AND A BARE NUMERAL IS STILL NOT A MEANING. Knowing that a match sits in a data cell rather than in a
heading says nothing about whether that cell carries the requested measure, period or column. That
question is untouched and is reported as untouched.
"""
from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_ln_learn as LN  # noqa: E402
import oqa_occ as OCC  # noqa: E402

SCHEMA = "lso.officeqa_d2_cells.v1"

_VERDICT_FIELDS = ("match", "binding", "sourced", "n_positions", "corroborations", "position")


def occurrence_inventory(freeze_doc: dict) -> dict:
    """Every field that produces an occurrence, classified, with witnesses. Read-only."""
    counts = collections.Counter()
    witnesses: dict = {"numeric_cell": [], "label_text": [], "ambiguous": []}
    per_doc = collections.Counter()
    for a in freeze_doc["attempts"]:
        pdir = os.path.join(EV.source_root(freeze_doc), "train_p%d" % a["pass"], "predictions")
        att = EV.read_attempt(pdir, a["item"])
        if att["trace_sha256"] != a["trace_sha256"]:
            raise SystemExit(f"{a['attempt_id']}: trace digest does not match the freeze")
        for entry in OCC.normalize(att["items"]):
            if entry["kind"] != "tool":
                continue
            for raw in entry["obs"].splitlines():
                m = OCC._LINE.match(raw)
                if not m or "|" not in m.group(2):
                    continue
                for field in (f.strip() for f in m.group(2).split("|")):
                    hits = [n for n in OCC._NUM.finditer(field)
                            if OCC.parse_value(n.group(0)) is not None]
                    if not hits:
                        continue
                    if OCC.is_numeric_cell(field):
                        counts["kept_as_a_numeric_cell"] += 1
                        counts["dropped_inside_a_kept_cell"] += len(hits) - 1
                        bucket = "numeric_cell"
                    elif any(c.isalpha() for c in field):
                        counts["dropped_label_text"] += len(hits)
                        bucket = "label_text"
                    else:
                        counts["dropped_ambiguous_no_letters"] += len(hits)
                        bucket = "ambiguous"
                    per_doc[bucket] += 1
                    if len(witnesses[bucket]) < 12 and field not in [
                            w["field_text"] for w in witnesses[bucket]]:
                        witnesses[bucket].append(
                            {"attempt_id": a["attempt_id"], "line": int(m.group(1)),
                             "field_text": field, "line_as_observed": raw.strip()[:240]})
    total = (counts["kept_as_a_numeric_cell"] + counts["dropped_inside_a_kept_cell"]
             + counts["dropped_label_text"] + counts["dropped_ambiguous_no_letters"])
    return {"counts": dict(counts), "matches_before": total,
            "matches_after": counts["kept_as_a_numeric_cell"],
            "share_dropped": round(1 - counts["kept_as_a_numeric_cell"] / max(total, 1), 4),
            "witnesses": witnesses}


def _verdict_view(v: dict) -> dict:
    return {k: v.get(k) for k in _VERDICT_FIELDS}


def replay(freeze_doc: dict) -> dict:
    """Both extraction modes over both roles, with every changed binding and its witness."""
    out: dict = {"roles": {}, "changed_bindings": [], "by_question": {}}
    unchanged_checks: dict = {}
    for role in ("learn", "check"):
        before = LN.rows_for(freeze_doc, role, numeric_cells_only=False)
        after = LN.rows_for(freeze_doc, role, numeric_cells_only=True)
        if len(before) != len(after):
            raise SystemExit("D-2 changed the number of operands, which it must not")
        changed = []
        feat_changed = 0
        for b, a in zip(before, after):
            assert (b["attempt_id"], b["operand_key"]) == (a["attempt_id"], a["operand_key"])
            if b["features"] != a["features"]:
                feat_changed += 1
            if _verdict_view(b["verdict"]) != _verdict_view(a["verdict"]):
                changed.append({
                    "attempt_id": b["attempt_id"], "item": b["item"],
                    "operand_key": b["operand_key"], "operand": b["as_written"],
                    "value": b["value"], "statement": b["statement"][:120],
                    "before": _verdict_view(b["verdict"]),
                    "after": _verdict_view(a["verdict"]),
                    "before_field_text": b["verdict"].get("field_text"),
                    "after_field_text": a["verdict"].get("field_text"),
                    "feature_n_positions_before": b["features"]["n_positions_in_prefix"],
                    "feature_n_positions_after": a["features"]["n_positions_in_prefix"],
                    "feature_source_tool_before": b["features"]["source_tool"],
                    "feature_source_tool_after": a["features"]["source_tool"],
                    "direction": _direction(b["verdict"], a["verdict"]),
                })
        out["roles"][role] = {
            "n_operands": len(before),
            "sourced_before": sum(1 for r in before if r["verdict"]["sourced"]),
            "sourced_after": sum(1 for r in after if r["verdict"]["sourced"]),
            "n_features_changed": feat_changed,
            "n_verdicts_changed": len(changed),
        }
        out["changed_bindings"].extend(changed)
        # per QUESTION, not pooled
        for rows, tag in ((before, "before"), (after, "after")):
            for r in rows:
                q = out["by_question"].setdefault(r["item"], {})
                q.setdefault("role", role)
                q[f"operands_{tag}"] = q.get(f"operands_{tag}", 0) + 1
                if r["verdict"]["sourced"]:
                    q[f"sourced_{tag}"] = q.get(f"sourced_{tag}", 0) + 1
                if r["verdict"]["match"] == OCC.SEVERAL_POSITIONS:
                    q[f"several_positions_{tag}"] = q.get(f"several_positions_{tag}", 0) + 1
                if r["verdict"]["match"] == OCC.NO_OCCURRENCE:
                    q[f"no_occurrence_{tag}"] = q.get(f"no_occurrence_{tag}", 0) + 1
        unchanged_checks[role] = {
            "operand_count_identical": len(before) == len(after),
            "statements_identical": all(b["statement"] == a["statement"]
                                        for b, a in zip(before, after)),
            "arithmetic_identical": all(b["arithmetic_consistent"] == a["arithmetic_consistent"]
                                        for b, a in zip(before, after)),
        }
    for q in out["by_question"].values():
        for k in ("sourced_before", "sourced_after", "several_positions_before",
                  "several_positions_after", "no_occurrence_before", "no_occurrence_after"):
            q.setdefault(k, 0)
        q["sourced_delta"] = q["sourced_after"] - q["sourced_before"]
    out["out_of_scope_material_unchanged"] = unchanged_checks
    return out


def _direction(before: dict, after: dict) -> str:
    if before["sourced"] and not after["sourced"]:
        return "a supported binding WITHDRAWN"
    if after["sourced"] and not before["sourced"]:
        return "a supported binding GAINED"
    if before["match"] == OCC.SEVERAL_POSITIONS and after["match"] == OCC.UNIQUE_POSITION:
        return "ambiguity resolved to one position, still not supported"
    if after["match"] == OCC.NO_OCCURRENCE and before["match"] != OCC.NO_OCCURRENCE:
        return "the only position was not a cell; the value now has no occurrence"
    return f"{before['match']} -> {after['match']}"


def score_models(freeze_doc: dict, contracts: list) -> dict:
    """Each frozen contract, scored under both extraction modes. NO RETRAINING."""
    out = []
    rows = {(role, mode): LN.rows_for(freeze_doc, role, numeric_cells_only=mode)
            for role in ("learn", "check") for mode in (False, True)}
    for tag, c in contracts:
        rec = {"tag": tag, "contract": f"{c['id']}@v{c['version']}",
               "contract_sha256": IR.sha(IR.canon(c)), "scores": {}}
        for role in ("learn", "check"):
            for mode, name in ((False, "superseded_extraction"), (True, "D2_corrected_extraction")):
                sc = LN.score_contract(c, rows[(role, mode)])
                rec["scores"][f"{role}/{name}"] = {
                    k: v for k, v in sc.items() if not k.endswith("_keys")}
        out.append(rec)
    return {"models": out,
            "note": "the same frozen contracts, scored against a changed measurement. An improvement "
                    "here is an extraction correction, not learner repair and not fresh confirmation."}
