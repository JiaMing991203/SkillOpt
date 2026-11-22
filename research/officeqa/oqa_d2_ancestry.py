#!/usr/bin/env python3
"""Calculation ancestry and what "recovery" actually means here. Rule OQA-LN-1, follow-up section 5.

FOUR CLAIMS THAT MUST NOT ESTABLISH ONE ANOTHER, and this module reports each separately:

  1. AN ORIGIN LINK EXISTS      - some earlier written statement could have produced this value.
  2. THE DERIVATION IS VALID    - that statement's own arithmetic holds, where the parser can read it.
  3. THE SOURCE MEANS THE RIGHT THING - the cell carries the requested measure, period and column.
                                  NEVER CHECKED HERE, and never implied by 1 or 2.
  4. THE FINAL ANSWER IS CORRECT - NEVER READ HERE, in any form.

HOW AN EDGE IS PROPOSED, AND WHY IT STAYS UNCERTAIN. The records carry nothing but the numbers, so a
dependency is proposed by numeric equality alone. That is not enough to identify a dependency, so:

  * several earlier producers of one value stay SEVERAL, reported as ambiguous rather than resolved;
  * an identity such as `86,000 - 0 = 86,000` RESTATES its own operand. Following it establishes no
    origin: it moves the same question one step back, and it is reported as a restatement;
  * a root the records cannot settle stays unresolved and is NOT called an actor error.

AND THE TASK PROCEDURE IS PART OF THE JUDGEMENT. A question that asks for a single cited value needs
no calculation at all, so an attempt with no arithmetic is not thereby defective.

WHAT "RECOVERY" REQUIRES. The shape test selects an attempt where an earlier calculation's result
differs from the committed answer. That is a necessary condition for a replacement and not a
sufficient one: in a multi-step computation EVERY intermediate result differs from the answer while
being consumed by it. A replacement is demonstrated only when at least one earlier calculation is
ABANDONED - its result reaches the supporting calculation by no path at all.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_obligations as OB  # noqa: E402
import oqa_occ as OCC  # noqa: E402

SCHEMA = "lso.officeqa_d2_ancestry.v1"

ABANDONED = "ABANDONED_ALTERNATIVE"
ANCESTOR = "INTERMEDIATE_STILL_USED_DOWNSTREAM"
SOURCED = "INDEPENDENTLY_SOURCED"
GIVEN = "FROM_THE_QUESTION_OR_A_DECLARED_CONSTANT"
SAME_RESULT = "AN_EARLIER_STATEMENT_OF_THE_SAME_RESULT"
UNRESOLVED = "UNRESOLVED"
RESTATEMENT = "RESTATEMENT_ONLY"
AMBIGUOUS = "SEVERAL_POSSIBLE_ORIGINS"


def audit_attempt(freeze_doc: dict, attempt_row: dict, *, numeric_cells_only: bool = True) -> dict:
    """The dependency picture for one attempt, with every uncertainty kept."""
    pdir = os.path.join(EV.source_root(freeze_doc), "train_p%d" % attempt_row["pass"], "predictions")
    att = EV.read_attempt(pdir, attempt_row["item"])
    if att["trace_sha256"] != attempt_row["trace_sha256"]:
        raise SystemExit(f"{attempt_row['attempt_id']}: trace digest does not match the freeze")
    ex = EV.extract(att, numeric_cells_only=numeric_cells_only)
    vd = EV.verdicts(att, ex, numeric_cells_only=numeric_cells_only)
    task = OB.derive(ex["question"])
    q_nums = {EV.num(m.group(0)) for m in EV._NUM_IN.finditer(ex["question"])} - {None}
    shape = EV.recovery_shape(ex)
    deps = EV.dependencies(ex)
    supporting = shape.get("supporting_calculation")

    op = (task.get("operation") or {})
    needs_arithmetic = (op.get("basis") == "STATED" and int(op.get("arity") or 0) >= 2)

    rec: dict = {
        "attempt_id": attempt_row["attempt_id"], "role": attempt_row["role"],
        "item": attempt_row["item"], "trace_sha256": att["trace_sha256"],
        "question": ex["question"],
        "task_requires_arithmetic": needs_arithmetic,
        "task_procedure_note": (
            "the question states an operation, so written work is expected"
            if needs_arithmetic else
            "the question states no arithmetic requirement: a DIRECT LOOKUP is a correct procedure "
            "and the absence of a calculation is not a defect"),
        "committed_answer_raw": ex["committed_answer_raw"],
        "committed_answer_value": ex["committed_answer_value"],
        "committed_answer_unreadable_reason": ex.get("committed_answer_unreadable_reason") or "",
        "n_calculations": len(ex["calculations"]),
        "n_unrepresented_arithmetic": ex.get("n_unrepresented_arithmetic", 0),
        "supporting_calculation": supporting,
    }
    if supporting is None:
        rec["finding"] = ("no written calculation this parser can read yields the committed answer; "
                          "ancestry cannot be built")
        rec["earlier_work"] = []
        rec["roots"] = []
        rec["replacement_demonstrated"] = False
        return rec

    calcs = ex["calculations"]
    consumed = set(EV.consumed_by(ex, supporting)["consumed"])

    # ── earlier work: abandoned, or an intermediate the answer still consumes ──
    earlier = []
    for i, c in enumerate(calcs):
        if i >= supporting:
            continue
        # Three classes, not two. An earlier statement that yields the SAME result as the
        # committed answer is the actor writing the answer twice, not an alternative it abandoned.
        if i in consumed:
            klass = ANCESTOR
        elif EV._near(c["result"], ex["committed_answer_value"]):
            klass = SAME_RESULT
        else:
            klass = ABANDONED
        earlier.append({
            "index": i, "statement": c["statement"], "result": c["result"],
            "class": klass,
            "arithmetic": ("OUT_OF_SCOPE" if c["fragment_of_larger_expression"]
                           else "HOLDS" if c["arithmetic_consistent"] else "DOES_NOT_HOLD"),
            "is_identity_restatement": EV._is_identity(c),
            "why": ("its result could still reach the supporting calculation, so the answer consumes "
                    "it and it was not replaced" if klass == ANCESTOR else
                    "it yields the committed answer itself: the same result written earlier, not an "
                    "alternative that was abandoned" if klass == SAME_RESULT else
                    "no path from its result reaches the supporting calculation, and its result is "
                    "not the committed answer"),
        })

    # ── the roots of the supporting calculation, followed back ───────────────
    roots, seen = [], set()
    frontier = [(supporting, None)]
    while frontier:
        ci, via = frontier.pop()
        for oi, edge in sorted(deps.get(ci, {}).items()):
            v = vd.get(f"{ci}/{oi}", {})
            cands = edge["candidates"]
            idents = set(edge["candidates_are_identities"])
            real = [j for j in cands if j not in idents]
            node = {
                "of_calculation": ci, "operand_index": oi,
                "operand": edge["as_written"], "value": edge["value"],
                "reached_via": via,
                "candidate_producers": cands,
                "candidate_statements": edge["candidate_statements"],
                "edge": edge["edge"],
            }
            if real:
                node["class"] = ANCESTOR
                node["origin_claim"] = ("AN ORIGIN LINK EXISTS" if len(real) == 1
                                        else "SEVERAL ORIGIN LINKS ARE POSSIBLE; the records cannot "
                                             "choose between them")
                node["derivation_arithmetic"] = [
                    {"index": j, "statement": calcs[j]["statement"],
                     "arithmetic": ("OUT_OF_SCOPE" if calcs[j]["fragment_of_larger_expression"]
                                    else "HOLDS" if calcs[j]["arithmetic_consistent"]
                                    else "DOES_NOT_HOLD")}
                    for j in real]
                for j in real:
                    if (j, ci) not in seen:
                        seen.add((j, ci))
                        frontier.append((j, ci))
            elif cands:
                node["class"] = RESTATEMENT
                node["origin_claim"] = ("NO ORIGIN IS ESTABLISHED: the only earlier statement that "
                                        "yields this value restates it unchanged")
            elif v.get("sourced"):
                node["class"] = SOURCED
                node["origin_claim"] = "AN ORIGIN LINK EXISTS: a corroborated observed position"
                node["position"] = v.get("position")
                node["position_field_text"] = v.get("field_text")
                node["supporting_corroborations"] = v.get("supporting") or []
            elif v.get("match") == OCC.SEVERAL_POSITIONS:
                node["class"] = AMBIGUOUS
                node["origin_claim"] = (f"SEVERAL observed positions ({v.get('n_positions')}); a "
                                        f"numeric match cannot choose")
            elif edge["value"] in q_nums:
                node["class"] = GIVEN
                node["origin_claim"] = "the value appears in the question"
            elif _is_power_of_ten(edge["value"]):
                node["class"] = GIVEN
                node["origin_claim"] = "a power of ten, read as a declared conversion factor"
            else:
                node["class"] = UNRESOLVED
                node["origin_claim"] = "no origin the records can settle"
            node["semantic_meaning_checked"] = False
            node["benchmark_correctness_checked"] = False
            roots.append(node)

    abandoned = [e for e in earlier if e["class"] == ABANDONED]
    # A DEFECT FOUND AND NOT FIXED HERE, because fixing it would move `recovery_shape` and make the
    # whole D-2 comparison incomparable. `EV._near` uses `max(0.5, |x| * 1e-6)`, an absolute
    # tolerance meant for rounding in large financial figures. When the committed answer is smaller
    # than 0.5 the tolerance EXCEEDS THE VALUE, so 0.000678402 and 0.0678402 both count as "equal"
    # to 0.068 and the supporting-calculation selection is effectively arbitrary among them.
    cv = ex["committed_answer_value"]
    tol_note = ""
    if cv is not None and abs(cv) <= 0.5:
        tol_note = (f"THE NUMERIC TOLERANCE DOMINATES HERE: the committed answer is {cv}, and the "
                    f"equality tolerance is 0.5, so every result within 0.5 of it - including "
                    f"values two orders of magnitude apart - is treated as the same number. Every "
                    f"dependency and every class on this attempt inherits that weakness.")
    rec.update({
        "earlier_work": earlier,
        "n_abandoned_alternatives": len(abandoned),
        "n_intermediates_still_used": len([e for e in earlier if e["class"] == ANCESTOR]),
        "n_earlier_statements_of_the_same_result": len([e for e in earlier
                                                        if e["class"] == SAME_RESULT]),
        "roots": roots,
        "replacement_demonstrated": bool(abandoned),
        "recovery_claim": (
            "A REPLACEMENT IS VISIBLE: at least one earlier calculation is abandoned and a later one "
            "supports the committed answer. This shows the checker handles replaced work. It does "
            "NOT show the task was successfully recovered: that would need the answer key, which is "
            "not read here"
            if abandoned else
            "NO REPLACEMENT IS DEMONSTRATED: every earlier calculation is an intermediate the "
            "committed answer still consumes. The shape test selected this attempt because earlier "
            "results differ from the answer, which is true of every multi-step computation"),
        "numeric_tolerance_note": tol_note,
        "unsettled_roots": [r["operand"] for r in roots
                            if r["class"] in (UNRESOLVED, RESTATEMENT, AMBIGUOUS)],
    })
    return rec


def _is_power_of_ten(v) -> bool:
    return EV._is_power_of_ten(v)


def run(freeze_doc: dict, attempt_ids: list, *, numeric_cells_only: bool = True) -> dict:
    by_id = {a["attempt_id"]: a for a in freeze_doc["attempts"]}
    out = [audit_attempt(freeze_doc, by_id[aid], numeric_cells_only=numeric_cells_only)
           for aid in attempt_ids if aid in by_id]
    doc = {"schema": SCHEMA, "numeric_cells_only": numeric_cells_only,
           "freeze_sha256": freeze_doc["self_sha256"],
           "attempts": out,
           "what_is_not_checked": [
               "that a cell carries the requested measure, period or column",
               "that the committed answer is correct - no score, answer key or held-out record is read",
           ]}
    doc["self_sha256"] = IR.sha(json.dumps(
        {k: v for k, v in doc.items() if k != "self_sha256"}, sort_keys=True, default=str))
    return doc
