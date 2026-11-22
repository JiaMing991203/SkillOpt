#!/usr/bin/env python3
"""Prefix-only checking, discrepancy classification, the stage connection and the recovery control.

Rule OQA-LN-1, amendment D-1.

THE ORDER MATTERS AND IS ENFORCED BY THE FILE LAYOUT. `predict()` writes a prediction per operand from
the frozen contract and the prefix-only features ALONE; its digest is recorded. Only then does
`resolve()` read the occurrence resolver's verdicts. Two files, two digests, in that order, so "the
prediction was committed before the observation" is checkable rather than promised.

FOUR KINDS OF DISCREPANCY, kept apart because conflating them is how a checker starts blaming an actor
for its own mistakes:

  MODEL_REFUTATION       the contract predicted a source and the resolver found a unique position with
                         no corroboration. The learned rule over-claimed. This is NOT an actor error
                         and must never be reported as one.
  EXTRACTION_AMBIGUITY   the value occurs at several observed positions, so a numeric match cannot
                         choose. The records cannot settle it.
  MISSING_INFORMATION    no line-numbered observation exists in the prefix at all, so the occurrence
                         channel was never available. UNKNOWN, never an accusation.
  ACTOR_EXECUTION_ERROR  reserved for the STAGE layer: a number used in the calculation that supports
                         the committed answer which the actor neither observed nor derived nor read
                         from the question. This one is about the actor.

THE STAGE CONNECTION, from the question requirement to the committed answer:

    TASK requirement (oqa_obligations, from the question sentence)
      -> LOOKUP        a line-numbered observation exists                      [OBS]
      -> SOURCE        every operand of the supporting calculation is sourced,
                       derived from earlier written work, a conversion factor,
                       or taken from the question                              [LEARNED + OBS]
      -> CALC          the supporting calculation's arithmetic holds            [MATH]
      -> ANSWER        the committed answer is the result of written work       [OBS]

A SUPERSEDED calculation stays in the record and stops supporting the answer: its operands are not
required to be sourced, and the later calculation that does support the answer can discharge the
obligation on its own. That is the recovery control, and it is why the earlier attempt is not an
accusation.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_ln_learn as LN  # noqa: E402
import oqa_ln_vocab as V  # noqa: E402
import oqa_obligations as OB  # noqa: E402
import oqa_occ as OCC  # noqa: E402

SCHEMA = "lso.officeqa_learned_source_check.v1"
RULE = "OQA-LN-1"

PREDICTED, DISCHARGED, VIOLATED, OUTSTANDING, REOPENED, UNKNOWN = (
    "PREDICTED", "DISCHARGED", "VIOLATED", "OUTSTANDING", "REOPENED", "UNKNOWN")

MODEL_REFUTATION = "MODEL_REFUTATION"
EXTRACTION_AMBIGUITY = "EXTRACTION_AMBIGUITY"
MISSING_INFORMATION = "MISSING_INFORMATION"
ACTOR_EXECUTION_ERROR = "ACTOR_EXECUTION_ERROR"
CONFIRMATION = "CONFIRMATION"

SOURCED, DERIVED, MATH_FACTOR, FROM_QUESTION, UNRESOLVED = (
    "SOURCED", "DERIVED_FROM_EARLIER_WORK", "MATH_CONVERSION_FACTOR", "TAKEN_FROM_THE_QUESTION",
    "UNRESOLVED")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── step 1: predictions, from the contract and the features only ─────────────

def predict(contract: dict, rows: list) -> dict:
    """One prediction per operand. NOTHING here reads a verdict."""
    preds = []
    for r in rows:
        verdict, why = LN.applies(contract, r["features"])
        preds.append({
            "attempt_id": r["attempt_id"], "item": r["item"], "operand_key": r["operand_key"],
            "as_written": r["as_written"], "value": r["value"],
            "statement": r["statement"],
            "features": r["features"],
            "contract_applies": verdict,
            "prediction": ("operand_sourced" if verdict else
                           "silent" if verdict is False else "not_evaluable"),
            "why_silent": why if verdict is False else [],
        })
    doc = {"schema": SCHEMA + ".predictions", "rule": RULE, "written_utc": _utc(),
           "contract_id": contract["id"], "contract_version": contract["version"],
           "contract_sha256": IR.sha(IR.canon(contract)),
           "n_operands": len(preds),
           "n_predicted_sourced": sum(1 for p in preds if p["contract_applies"] is True),
           "predictions": preds}
    doc["self_sha256"] = IR.sha(json.dumps(doc, sort_keys=True))
    return doc


# ── step 2: the verdicts, and the classification of every discrepancy ────────

def resolve(prediction_doc: dict, rows: list) -> dict:
    """Read the resolver's verdicts and classify each (prediction, verdict) pair."""
    by_key = {(r["attempt_id"], r["operand_key"]): r for r in rows}
    out = []
    counts = collections.Counter()
    for p in prediction_doc["predictions"]:
        r = by_key[(p["attempt_id"], p["operand_key"])]
        v = r["verdict"]
        kind = None
        if p["contract_applies"] is True:
            if v["sourced"]:
                kind = CONFIRMATION
            elif v["match"] == OCC.UNIQUE_POSITION:
                kind = MODEL_REFUTATION
            elif v["match"] == OCC.SEVERAL_POSITIONS:
                kind = EXTRACTION_AMBIGUITY
            else:
                kind = MISSING_INFORMATION
        counts[kind or "silent"] += 1
        out.append({**{k: p[k] for k in ("attempt_id", "item", "operand_key", "as_written",
                                         "statement", "prediction")},
                    "verdict_match": v["match"], "verdict_binding": v["binding"],
                    "sourced": v["sourced"], "corroborations": v["corroborations"],
                    "discrepancy_kind": kind,
                    "why": v["why"]})
    doc = {"schema": SCHEMA + ".verdicts", "rule": RULE, "written_utc": _utc(),
           "predictions_sha256": prediction_doc["self_sha256"],
           "counts": dict(counts), "results": out}
    doc["self_sha256"] = IR.sha(json.dumps(doc, sort_keys=True))
    return doc


# ── the stage connection, per attempt ────────────────────────────────────────

def classify_operand(operand: dict, verdict: dict, earlier_results: list, q_nums: set) -> str:
    if verdict["sourced"]:
        return SOURCED
    if operand["value"] in earlier_results:
        return DERIVED
    if operand["features"]["round_power_of_ten"]:
        return MATH_FACTOR
    if operand["value"] in q_nums:
        return FROM_QUESTION
    return UNRESOLVED


def stage_report(freeze_doc: dict, attempt_row: dict, contract: dict) -> dict:
    """The whole chain for one attempt, with every obligation's state and its reason."""
    pdir = os.path.join(freeze_doc["source_root"], "train_p%d" % attempt_row["pass"], "predictions")
    att = EV.read_attempt(pdir, attempt_row["item"])
    ex = EV.extract(att)
    vd = EV.verdicts(att, ex)
    task = OB.derive(ex["question"])
    q_nums = {EV.num(m.group(0)) for m in EV._NUM_IN.finditer(ex["question"])} - {None}

    shape = EV.recovery_shape(ex)
    cv = ex["committed_answer_value"]
    channel = bool(OCC.observed_occurrences(att["items"]))

    led = []

    def put(name, state, why, provenance, **extra):
        led.append({"obligation": name, "state": state, "why": why, "provenance": provenance, **extra})

    # LOOKUP - was the occurrence channel available at all?
    put("lookup_observed", DISCHARGED if channel else UNKNOWN,
        ("at least one line-numbered, pipe-delimited observation exists, so a position can be read"
         if channel else
         "NO line-numbered observation exists in this attempt, so the occurrence channel was never "
         "available. The obligation is UNKNOWN - an absent observation does not mean nothing changed"),
        IR.OBS)

    if cv is None:
        put("answer_traced", UNKNOWN, "no answer was committed under the narrow committed-answer "
            "policy, so there is nothing to trace", IR.OBS)
        put("source_traced", UNKNOWN, "without a committed answer there is no supporting calculation "
            "whose operands could be required to have sources", IR.OBS)
        put("arith_recorded", UNKNOWN, "no supporting calculation", IR.MATH)
        return _pack(attempt_row, att, ex, task, shape, led, channel, [], contract)

    supporting = shape.get("supporting_calculation")
    if supporting is None:
        put("answer_traced", VIOLATED,
            f"the committed answer {ex['committed_answer_raw']!r} is the result of no written "
            f"calculation in this attempt, and no written calculation yields it",
            IR.OBS, actor_execution_diagnosis=True)
        put("source_traced", UNKNOWN, "there is no supporting calculation to trace", IR.OBS)
        put("arith_recorded", UNKNOWN, "no supporting calculation", IR.MATH)
        return _pack(attempt_row, att, ex, task, shape, led, channel, [], contract)

    calc = ex["calculations"][supporting]
    put("answer_traced", DISCHARGED,
        f"the committed answer {ex['committed_answer_raw']!r} is the result of the written statement "
        f"{calc['statement']!r} (calculation {supporting})", IR.OBS)
    put("arith_recorded", DISCHARGED if calc["arithmetic_consistent"] else VIOLATED,
        (f"{calc['statement']!r} is arithmetically consistent" if calc["arithmetic_consistent"]
         else f"{calc['statement']!r} does not hold as written"),
        IR.MATH, actor_execution_diagnosis=not calc["arithmetic_consistent"])

    # SOURCE - the learned part. Every operand of the SUPPORTING calculation only.
    earlier = [c["result"] for i, c in enumerate(ex["calculations"]) if i < supporting]
    operand_rows, model_refutations = [], []
    for oi, operand in enumerate(calc["operands"]):
        v = vd[f"{supporting}/{oi}"]
        cls = classify_operand(operand, v, earlier, q_nums)
        applies, _why = LN.applies(contract, operand["features"])
        refuted = bool(applies is True and not v["sourced"])
        if refuted:
            model_refutations.append({"operand": operand["as_written"],
                                      "verdict_match": v["match"],
                                      "kind": (MODEL_REFUTATION if v["match"] == OCC.UNIQUE_POSITION
                                               else EXTRACTION_AMBIGUITY
                                               if v["match"] == OCC.SEVERAL_POSITIONS
                                               else MISSING_INFORMATION)})
        operand_rows.append({
            "operand": operand["as_written"], "value": operand["value"], "class": cls,
            "verdict_match": v["match"], "sourced": v["sourced"],
            "corroborations": v["corroborations"], "position": v["position"],
            "contract_predicted_sourced": applies is True,
            "contract_refuted_here": refuted,
            "features": operand["features"],
        })

    unresolved = [o for o in operand_rows if o["class"] == UNRESOLVED]
    if model_refutations:
        put("source_traced", UNKNOWN,
            f"the learned contract predicted a source for "
            f"{len(model_refutations)} operand(s) and the resolver did not confirm it. That is a "
            f"refutation of the MODEL, not an error by the actor, so the obligation is UNKNOWN and no "
            f"accusation is made", V.LEARNED,
            model_refutations=model_refutations, witnessed_by=f"{contract['id']}@v{contract['version']}")
    elif not unresolved:
        put("source_traced", DISCHARGED,
            "every operand of the supporting calculation has an identified origin: "
            + ", ".join(f"{o['operand']} = {o['class']}" for o in operand_rows),
            V.LEARNED, witnessed_by=f"{contract['id']}@v{contract['version']}")
    elif not channel:
        put("source_traced", UNKNOWN,
            f"{len(unresolved)} operand(s) have no identified origin, but no line-numbered observation "
            f"exists in this attempt, so the records cannot settle it", IR.OBS)
    else:
        put("source_traced", OUTSTANDING,
            f"{len(unresolved)} operand(s) of the supporting calculation have no identified origin "
            f"({', '.join(o['operand'] for o in unresolved)}): not a corroborated observed cell, not "
            f"the result of earlier written work, not a conversion factor and not in the question",
            IR.OBS, actor_execution_diagnosis=True)

    return _pack(attempt_row, att, ex, task, shape, led, channel, operand_rows, contract)


def _pack(attempt_row, att, ex, task, shape, led, channel, operand_rows, contract) -> dict:
    kinds = collections.Counter()
    for e in led:
        if e["state"] == DISCHARGED:
            kinds["confirmations"] += 1
        elif e.get("model_refutations"):
            kinds["model_refutations"] += len(e["model_refutations"])
        elif e.get("actor_execution_diagnosis"):
            kinds["execution_diagnoses"] += 1
        elif e["state"] == UNKNOWN:
            kinds["unknowns"] += 1
    return {
        "attempt_id": attempt_row["attempt_id"], "role": attempt_row["role"],
        "item": attempt_row["item"], "pass": attempt_row["pass"],
        "trace_sha256": att["trace_sha256"],
        "question": ex["question"],
        "task_requirements": task,
        "committed_answer_raw": ex["committed_answer_raw"],
        "n_calculations": len(ex["calculations"]),
        "occurrence_channel_available": channel,
        "recovery_shape": shape,
        "superseded_calculations": [
            {"index": i, "statement": ex["calculations"][i]["statement"],
             "result": ex["calculations"][i]["result"],
             "still_recorded": True,
             "supports_the_answer": False}
            for i in shape.get("superseded_calculations", [])],
        "supporting_operands": operand_rows,
        "obligations": led,
        "diagnosis_counts": {
            "confirmations": kinds["confirmations"],
            "model_refutations": kinds["model_refutations"],
            "execution_diagnoses": kinds["execution_diagnoses"],
            "unknowns": kinds["unknowns"],
        },
        "contract": f"{contract['id']}@v{contract['version']}",
    }


# ── the readable report, generated FROM the checked structure ────────────────

HEADING = "Source and Calculation Report"

_QUALIFICATIONS = """\
HOW TO READ THIS, AND WHAT IT DOES NOT ESTABLISH.
- A SOURCE is an observed position (document, line, field) that the value matches uniquely AND an
  independent corroboration. A unique numeric match on its own is NOT a source and is reported as
  unresolved, not as an error.
- UNKNOWN means the records cannot settle it. It is never an accusation.
- A MODEL REFUTATION means this checker's own learned rule was wrong here. It is not a mistake by the
  agent and must not be read as one.
- The learned rule is empirical, fitted to {n_learn} operands of saved training work, and it predicts
  a resolver verdict. It is not a statement about the document corpus.
- Nothing here reads the answer key, the score, or any validation or held-out record.
"""


def render_report(stage: dict, *, n_learn_operands: int, contract_rendered: str,
                  model_sha256: str, lean: dict | None = None) -> str:
    """The report the optimizer actually reads, built from `stage_report`'s own fields.

    It is generated from the checked structure and from nothing else: every line below is a field of
    `stage`, so a claim cannot appear in the report that the checker did not make.
    """
    L = []
    L.append(_QUALIFICATIONS.format(n_learn=n_learn_operands))
    L.append("")
    L.append("TASK REQUIREMENTS, read from the question sentence only [TASK]")
    task = stage.get("task_requirements") or {}
    for line in (OB.describe(task) if task else []):
        L.append(f"  - {line}")
    if not task:
        L.append("  - (the question could not be read into requirements)")
    L.append("")
    L.append("THE CHAIN, from the lookup to the committed answer")
    for o in stage["obligations"]:
        mark = {DISCHARGED: "HOLDS", VIOLATED: "**VIOLATED**", OUTSTANDING: "OUTSTANDING",
                UNKNOWN: "UNKNOWN", REOPENED: "REOPENED"}.get(o["state"], o["state"])
        L.append(f"  [{o['obligation']}] {mark}  ({o['provenance']})")
        L.append(f"      {o['why']}")
        for ref in o.get("model_refutations") or []:
            L.append(f"      REFUTATION of this checker's own rule at operand {ref['operand']}: "
                     f"{ref['verdict_match']} ({ref['kind']})")
    L.append("")
    if stage.get("supporting_operands"):
        L.append("THE OPERANDS OF THE CALCULATION THAT SUPPORTS THE COMMITTED ANSWER")
        for o in stage["supporting_operands"]:
            where = ""
            if o.get("position"):
                p = o["position"]
                where = f"  at {p['document']} line {p['line']} field {p['field']}"
            L.append(f"  {o['operand']:>14}  {o['class']}{where}")
            if o.get("corroborations"):
                L.append(f"                  corroborated by {', '.join(o['corroborations'])}")
            if o.get("contract_refuted_here"):
                L.append("                  the learned rule predicted a source here and the resolver "
                         "did not confirm it")
        L.append("")
    if stage.get("superseded_calculations"):
        L.append("WORK THAT WAS REPLACED. It stays in the record and stops supporting the answer; it is "
                 "not an accusation.")
        for s in stage["superseded_calculations"][:8]:
            L.append(f"  calculation {s['index']}: {s['statement']}  -> replaced")
        if len(stage["superseded_calculations"]) > 8:
            L.append(f"  ... and {len(stage['superseded_calculations']) - 8} more, all still recorded")
        L.append("")
    L.append("THE LEARNED RULE THIS REPORT APPLIED")
    for line in contract_rendered.splitlines():
        L.append(f"  {line}")
    L.append(f"  model {model_sha256[:16]}")
    if lean:
        L.append("")
        L.append("FORMAL CHECK")
        L.append(f"  Lean {lean.get('version', '?')} return code {lean.get('returncode')}, "
                 f"source {str(lean.get('source_sha256', ''))[:16]}, "
                 f"{lean.get('n_examples', 0)} exported examples")
        L.append(f"  WHAT IT CHECKS: {lean.get('scope', 'unstated')}")
        L.append("  WHAT IT DOES NOT: that the extraction found the right cell, that the learned rule "
                 "is true of the corpus, or that any reported state caused the task outcome.")
    counts = stage["diagnosis_counts"]
    L.append("")
    L.append(f"COUNTS  actionable execution diagnoses {counts['execution_diagnoses']}  |  "
             f"refutations of this checker {counts['model_refutations']}  |  "
             f"confirmations {counts['confirmations']}  |  unknowns {counts['unknowns']}")
    return "\n".join(L) + "\n"
