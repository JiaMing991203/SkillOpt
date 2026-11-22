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


_NOT_CONTENT = ("written_utc", "self_sha256")


def _content_sha(doc: dict) -> str:
    """A digest of what the document SAYS, with the clock and the digest field itself left out."""
    return IR.sha(json.dumps({k: v for k, v in doc.items() if k not in _NOT_CONTENT},
                             sort_keys=True, default=str))


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
    # THE DIGEST IS OVER THE CONTENT, NOT THE CLOCK. The first version hashed the whole document
    # including `written_utc`, so two identical prediction sets digested differently and the digest
    # could not witness "the prediction was committed before the verdict was read" at all - it only
    # witnessed when it was written. A test that changed nothing but the verdict caught it.
    doc["self_sha256"] = _content_sha(doc)
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
    doc["self_sha256"] = _content_sha(doc)
    return doc


# ── the stage connection, per attempt ────────────────────────────────────────

RESTATEMENT_ONLY = "DERIVED_ONLY_VIA_A_RESTATEMENT"
AMBIGUOUS_ORIGIN = "SEVERAL_POSSIBLE_ORIGINS"

# D-3c. What each obligation does and does not establish, carried WITH the state so the report cannot
# print a status without its meaning. `answer_traced HOLDS` was read in the paired rehearsal as "the
# answer is correct"; it says only that the answer is the result of a statement the actor wrote.
OBLIGATION_MEANING = {
    "lookup_observed": (
        "a line-numbered observation exists, so a cell has a readable position",
        "it does not say the actor read the right cell, or any cell"),
    "answer_traced": (
        "PROVENANCE ONLY: the committed answer is the result of a statement the actor wrote down",
        "it says NOTHING about whether that answer is correct, matches the answer key, or satisfies "
        "the question - this checker never reads a score"),
    "arith_recorded": (
        "ARITHMETIC ONLY: the written statement is internally consistent",
        "it does not say the operands were the right quantities, or that the statement answers the "
        "question"),
    "source_traced": (
        "PROVENANCE ONLY: every operand of the supporting calculation has an identified origin",
        "an origin is a CELL IDENTITY, never a claim that the cell carries the requested measure, "
        "period or column"),
}


def _applies(contract, features: dict):
    """`LN.applies`, or (None, []) when the learned rule is ABSENT.

    Absent is not false: with no rule there is no prediction, so nothing is refuted and nothing is
    witnessed. Facts the permitted observations establish on their own are untouched.
    """
    if contract is None:
        return None, []
    return LN.applies(contract, features)


def classify_operand(operand: dict, verdict: dict, dep_row: dict, q_nums: set) -> dict:
    """Where this operand came from, with the ambiguity kept rather than resolved.

    D-3e and the follow-up instruction's §5: a dependency is proposed by numeric equality and nothing
    else, so several earlier producers stay several; and an identity such as `86,000 - 0 = 86,000`
    RESTATES its own operand, so following that edge establishes no origin - it moves the same
    question one step back. The milestone's `source_traced DISCHARGED` on p4/UID0063 rested on exactly
    such an edge.
    """
    if verdict["sourced"]:
        return {"class": SOURCED, "settled": True,
                "why": "a unique observed position with an independent corroboration"}
    cands = dep_row.get("candidates") or []
    ident = set(dep_row.get("candidates_are_identities") or [])
    real = [j for j in cands if j not in ident]
    if real:
        return {"class": DERIVED, "settled": True,
                "why": ("the result of earlier written work (calculation "
                        + ", ".join(str(j) for j in real) + ")"),
                "ambiguous": len(real) > 1,
                "producers": real}
    if cands:
        return {"class": RESTATEMENT_ONLY, "settled": False,
                "why": ("the only earlier statement that yields it restates it unchanged "
                        "(calculation " + ", ".join(str(j) for j in sorted(ident)) + "), which "
                        "establishes no origin"),
                "producers": sorted(ident)}
    if operand["features"]["round_power_of_ten"]:
        return {"class": MATH_FACTOR, "settled": True,
                "why": "a power of ten, read as a declared conversion factor"}
    if operand["value"] in q_nums:
        return {"class": FROM_QUESTION, "settled": True, "why": "the value appears in the question"}
    if verdict["match"] == OCC.SEVERAL_POSITIONS:
        return {"class": AMBIGUOUS_ORIGIN, "settled": False,
                "why": f"the value occurs at {verdict['n_positions']} observed positions and a "
                       f"numeric match cannot choose between them"}
    return {"class": UNRESOLVED, "settled": False,
            "why": "no corroborated observed cell, no earlier written work, not a conversion factor "
                   "and not in the question"}


def stage_report(freeze_doc: dict, attempt_row: dict, contract: dict, *,
                 numeric_cells_only: bool = EV.NUMERIC_CELLS_ONLY) -> dict:
    """The whole chain for one attempt, with every obligation's state and its reason."""
    pdir = os.path.join(EV.source_root(freeze_doc), "train_p%d" % attempt_row["pass"], "predictions")
    att = EV.read_attempt(pdir, attempt_row["item"])
    # The freeze is the witness that this record set was fixed before any learner call, so a record
    # read from a MIRROR of the source root must be byte-identical to the one that was frozen.
    if att["trace_sha256"] != attempt_row["trace_sha256"]:
        raise SystemExit(f"{attempt_row['attempt_id']}: the trace digest does not match the freeze")
    ex = EV.extract(att, numeric_cells_only=numeric_cells_only)
    vd = EV.verdicts(att, ex, numeric_cells_only=numeric_cells_only)
    task = OB.derive(ex["question"])
    q_nums = {EV.num(m.group(0)) for m in EV._NUM_IN.finditer(ex["question"])} - {None}

    shape = EV.recovery_shape(ex)
    cv = ex["committed_answer_value"]
    channel = bool(OCC.observed_occurrences(att["items"],
                                            numeric_cells_only=numeric_cells_only))

    deps = EV.dependencies(ex)
    led = []

    def put(name, state, why, provenance, **extra):
        establishes, does_not = OBLIGATION_MEANING.get(name, ("", ""))
        led.append({"obligation": name, "state": state, "why": why, "provenance": provenance,
                    "establishes": establishes, "does_not_establish": does_not, **extra})

    # LOOKUP - was the occurrence channel available at all?
    put("lookup_observed", DISCHARGED if channel else UNKNOWN,
        ("at least one line-numbered, pipe-delimited observation exists, so a position can be read"
         if channel else
         "NO line-numbered observation exists in this attempt, so the occurrence channel was never "
         "available. The obligation is UNKNOWN - an absent observation does not mean nothing changed"),
        IR.OBS)

    if cv is None:
        # D-5. An answer that IS committed but is not one number - a list, a date, a sentence - used
        # to be forced through a numeric strip, and the nonsense value that came out could match no
        # calculation, so the actor was accused. The answer is out of scope, not wrong.
        put("answer_traced", UNKNOWN,
            ex.get("committed_answer_unreadable_reason")
            or "no answer was committed under the narrow committed-answer policy, so there is "
               "nothing to trace",
            IR.OBS,
            committed_answer_out_of_scope=bool(ex.get("committed_answer_raw")))
        put("source_traced", UNKNOWN, "without a committed answer there is no supporting calculation "
            "whose operands could be required to have sources", IR.OBS)
        put("arith_recorded", UNKNOWN, "no supporting calculation", IR.MATH)
        return _pack(attempt_row, att, ex, task, shape, led, channel, [], contract)

    # D-3b. ARITHMETIC IS A TASK REQUIREMENT, NOT AN ASSUMPTION. A question that asks for a single
    # cited value needs no calculation at all, so an answer with no calculation behind it is correct
    # behaviour and must not be accused. The requirement is read from the question sentence alone.
    op = (task or {}).get("operation") or {}
    needs_arithmetic = (op.get("basis") == "STATED" and int(op.get("arity") or 0) >= 2)

    supporting = shape.get("supporting_calculation")
    if supporting is None:
        if needs_arithmetic and ex.get("n_unrepresented_arithmetic"):
            # D-5. The actor DID write arithmetic; this parser cannot represent it - absolute-value
            # bars, a chained expression, or a fragment of one. All four UID0065 attempts write
            # "|2.0% - 0.2%| = 1.8%" and were accused of committing an answer with no work behind it.
            put("answer_traced", UNKNOWN,
                f"no written calculation THIS PARSER CAN READ yields the committed answer "
                f"{ex['committed_answer_raw']!r}, and the attempt contains "
                f"{ex['n_unrepresented_arithmetic']} expression(s) the parser cannot represent "
                f"(absolute-value bars, a chained expression, or a fragment of one). The records "
                f"cannot settle whether the answer was computed, so this is UNKNOWN",
                IR.OBS, unrepresented_arithmetic=ex["n_unrepresented_arithmetic"])
        elif needs_arithmetic:
            put("answer_traced", VIOLATED,
                f"the question states an operation of arity {op.get('arity')} "
                f"({op.get('op')}), so the answer should be the result of written work, and the "
                f"committed answer {ex['committed_answer_raw']!r} is the result of no written "
                f"calculation in this attempt",
                IR.OBS, actor_execution_diagnosis=True)
        else:
            put("answer_traced", UNKNOWN,
                f"the question states no arithmetic requirement, so a DIRECT LOOKUP is a correct "
                f"procedure and the committed answer {ex['committed_answer_raw']!r} needs no "
                f"calculation behind it. Whether the actor read the right cell is not settled by "
                f"these records",
                IR.TASK,
                direct_lookup_permitted=True)
        put("source_traced", UNKNOWN, "there is no supporting calculation to trace", IR.OBS)
        put("arith_recorded", UNKNOWN,
            "no supporting calculation" + ("" if needs_arithmetic else
                                           "; the question requires no arithmetic"), IR.MATH)
        return _pack(attempt_row, att, ex, task, shape, led, channel, [], contract, deps)

    calc = ex["calculations"][supporting]
    put("answer_traced", DISCHARGED,
        f"the committed answer {ex['committed_answer_raw']!r} is the result of the written statement "
        f"{calc['statement']!r} (calculation {supporting})", IR.OBS)
    # D-4. A match that continues a larger expression is not the statement the actor wrote, so its
    # arithmetic is OUT OF SCOPE rather than false. 38 of the 50 inconsistent statements in the
    # frozen set are fragments of this kind.
    if calc.get("fragment_of_larger_expression"):
        put("arith_recorded", UNKNOWN,
            f"{calc['statement']!r} is a fragment of a larger expression the binary parser cannot "
            f"represent, so its arithmetic is OUT OF SCOPE - not false", IR.MATH,
            out_of_scope_expression=True)
    else:
        put("arith_recorded", DISCHARGED if calc["arithmetic_consistent"] else VIOLATED,
            (f"{calc['statement']!r} is arithmetically consistent" if calc["arithmetic_consistent"]
             else f"{calc['statement']!r} does not hold as written"),
            IR.MATH, actor_execution_diagnosis=not calc["arithmetic_consistent"])

    # SOURCE - the learned part. Every operand of the SUPPORTING calculation only.
    operand_rows, model_refutations = [], []
    for oi, operand in enumerate(calc["operands"]):
        v = vd[f"{supporting}/{oi}"]
        cls = classify_operand(operand, v, deps.get(supporting, {}).get(oi, {}), q_nums)
        applies, _why = _applies(contract, operand["features"])
        refuted = bool(applies is True and not v["sourced"])
        if refuted:
            model_refutations.append({"operand": operand["as_written"],
                                      "verdict_match": v["match"],
                                      "kind": (MODEL_REFUTATION if v["match"] == OCC.UNIQUE_POSITION
                                               else EXTRACTION_AMBIGUITY
                                               if v["match"] == OCC.SEVERAL_POSITIONS
                                               else MISSING_INFORMATION)})
        operand_rows.append({
            "operand": operand["as_written"], "value": operand["value"], "class": cls["class"],
            "origin_settled": cls["settled"], "origin_why": cls["why"],
            "origin_producers": cls.get("producers") or [],
            "n_real_producers": len([j for j in (deps.get(supporting, {}).get(oi, {})
                                                 .get("candidates") or [])
                                     if j not in set(deps.get(supporting, {}).get(oi, {})
                                                     .get("candidates_are_identities") or [])]),
            "n_identity_producers": len(deps.get(supporting, {}).get(oi, {})
                                        .get("candidates_are_identities") or []),
            "origin_ambiguous": bool(cls.get("ambiguous")),
            "verdict_match": v["match"], "sourced": v["sourced"],
            # D-3d. `supporting` are the corroborations that DECIDE; `corroborations` also carries
            # SOLE_IN_ROW, which `oqa_occ.bind` excludes from `supporting` and which therefore
            # reaches no decision. Printing them in one list presented a rejected criterion beside
            # two accepted ones, so they are kept apart here and in the report.
            "supporting_corroborations": v.get("supporting") or [],
            "observed_not_counted_as_support": [c for c in (v["corroborations"] or [])
                                                if c not in (v.get("supporting") or [])],
            "corroborations": v["corroborations"],
            "position": v["position"], "position_field_text": v.get("field_text"),
            "contract_predicted_sourced": applies is True,
            "contract_refuted_here": refuted,
            "features": operand["features"],
        })

    unresolved = [o for o in operand_rows if not o["origin_settled"]]
    # D-3a and the follow-up instruction's SS6: an operand the RECORDS cannot settle - several
    # candidate positions, or only a restatement behind it - is an unknown, not an outstanding
    # obligation and certainly not an actor error.
    ambiguous = [o for o in unresolved
                 if o["class"] in (AMBIGUOUS_ORIGIN, RESTATEMENT_ONLY)]
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
            V.LEARNED if contract is not None else IR.OBS,
            witnessed_by=(f"{contract['id']}@v{contract['version']}" if contract is not None
                          else "the supplied resolver alone; no learned rule was applied"))
    elif not channel:
        put("source_traced", UNKNOWN,
            f"{len(unresolved)} operand(s) have no identified origin, but no line-numbered observation "
            f"exists in this attempt, so the records cannot settle it", IR.OBS)
    elif ambiguous:
        put("source_traced", UNKNOWN,
            f"{len(unresolved)} operand(s) of the supporting calculation have no settled origin "
            f"({', '.join(o['operand'] for o in unresolved)}), and at least one of them is "
            f"AMBIGUOUS rather than missing: " + "; ".join(
                f"{o['operand']} - {o['origin_why']}" for o in ambiguous)
            + ". The records cannot settle it, so this is UNKNOWN and no accusation is made",
            IR.OBS, unresolved_provenance=True)
    else:
        put("source_traced", OUTSTANDING,
            f"{len(unresolved)} operand(s) of the supporting calculation have no identified origin "
            f"({', '.join(o['operand'] for o in unresolved)}): not a corroborated observed cell, not "
            f"the result of earlier written work, not a conversion factor and not in the question. "
            f"THIS IS UNRESOLVED PROVENANCE, NOT A DEMONSTRATED ACTOR ERROR - the records may simply "
            f"not show where the number came from",
            IR.OBS, unresolved_provenance=True)

    return _pack(attempt_row, att, ex, task, shape, led, channel, operand_rows, contract, deps)


def _pack(attempt_row, att, ex, task, shape, led, channel, operand_rows, contract, deps=None) -> dict:
    kinds = collections.Counter()
    for e in led:
        if e["state"] == DISCHARGED:
            kinds["confirmations"] += 1
        elif e.get("model_refutations"):
            kinds["model_refutations"] += len(e["model_refutations"])
        elif e.get("actor_execution_diagnosis"):
            kinds["execution_diagnoses"] += 1
        elif e.get("unresolved_provenance"):
            # D-3a. This used to be counted as an ACTIONABLE EXECUTION DIAGNOSIS, and the report the
            # rehearsal delivered said so. Missing provenance is not an actor error.
            kinds["unresolved_provenance"] += 1
        elif e["state"] == UNKNOWN:
            kinds["unknowns"] += 1
    earlier = EV.classify_earlier(ex, shape.get("supporting_calculation"),
                                  shape.get("superseded_calculations") or [])
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
        # D-3e. The shape test calls every earlier calculation with a different result "superseded".
        # That conflates work the actor ABANDONED with an intermediate result the final answer still
        # CONSUMES. Both are kept, each with its class.
        "superseded_calculations": earlier,
        "abandoned_alternatives": [e for e in earlier
                                   if e["dependency_class"] == "ABANDONED_ALTERNATIVE"],
        "consumed_ancestors": [e for e in earlier
                               if e["dependency_class"] == "CONSUMED_ANCESTOR"],
        "replacement_demonstrated": bool([e for e in earlier
                                          if e["dependency_class"] == "ABANDONED_ALTERNATIVE"]),
        "supporting_operands": operand_rows,
        "dependencies": {str(k): {str(oi): v for oi, v in row.items()}
                         for k, row in (deps or {}).items()},
        "obligations": led,
        "diagnosis_counts": {
            "confirmations": kinds["confirmations"],
            "model_refutations": kinds["model_refutations"],
            "execution_diagnoses": kinds["execution_diagnoses"],
            "unresolved_provenance": kinds["unresolved_provenance"],
            "unknowns": kinds["unknowns"],
        },
        "contract": (f"{contract['id']}@v{contract['version']}" if contract is not None
                     else "NONE: the learned rule was absent for this report"),
        "learned_rule_applied": contract is not None,
        # The EXACT inputs the Lean cross-check consumes, named once here rather than re-derived
        # in the generator - so a disagreement is between the two implementations and not between
        # two different readings of the same record.
        "lean_inputs": _lean_inputs(ex, shape, led, channel, operand_rows, earlier),
    }


def _lean_inputs(ex, shape, led, channel, operand_rows, earlier) -> dict:
    sup = shape.get("supporting_calculation")
    calc = ex["calculations"][sup] if sup is not None else None
    unsettled = [o for o in operand_rows if not o["origin_settled"]]
    ambiguous = [o for o in unsettled
                 if o["class"] in (AMBIGUOUS_ORIGIN, RESTATEMENT_ONLY)]
    refuted = any(e.get("model_refutations") for e in led)
    return {
        "has_supporting": sup is not None,
        "channel": bool(channel),
        "refuted": bool(refuted),
        "n_unsettled": len(unsettled),
        "n_ambiguous": len(ambiguous),
        "committed": ex["committed_answer_value"] is not None,
        "needs_arithmetic": _needs(ex),
        "n_unrepresented": int(ex.get("n_unrepresented_arithmetic") or 0),
        "fragment": bool(calc and calc.get("fragment_of_larger_expression")),
        "consistent": bool(calc and calc["arithmetic_consistent"]),
        "n_abandoned": len([e for e in earlier
                            if e["dependency_class"] == "ABANDONED_ALTERNATIVE"]),
    }


def _needs(ex) -> bool:
    op = (OB.derive(ex["question"]).get("operation") or {})
    return op.get("basis") == "STATED" and int(op.get("arity") or 0) >= 2


# ── the readable report, generated FROM the checked structure ────────────────

HEADING = "Source and Calculation Report"

_QUALIFICATIONS = """\
HOW TO READ THIS, AND WHAT IT DOES NOT ESTABLISH.
FOUR DIFFERENT THINGS, kept apart. This report speaks to the first two only.
  1. PROVENANCE TRACED  - where a number came from, as a cell identity or as earlier written work.
  2. ARITHMETIC CHECKED - whether a written statement is internally consistent.
  3. TASK REQUIREMENTS SATISFIED - whether the right measure, period and column were used. NOT CHECKED.
  4. BENCHMARK CORRECTNESS - whether the answer is right. NEVER READ HERE, in any form.
A status of HOLDS is a statement about 1 or 2. It is never a statement about 3 or 4, and in
particular `answer_traced HOLDS` does NOT mean the answer is correct.
- A SOURCE is an observed position (document, line, field) that the value matches uniquely AND an
  independent corroboration. A unique numeric match on its own is NOT a source and is reported as
  unresolved, not as an error.
- UNKNOWN means the records cannot settle it. It is never an accusation.
- OUTSTANDING means an origin was not identified. That is UNRESOLVED PROVENANCE, not a demonstrated
  actor error: the records may simply not show where a number came from.
- A MODEL REFUTATION means this checker's own learned rule was wrong here. It is not a mistake by the
  agent and must not be read as one.
- An expression the binary parser cannot represent is OUT OF SCOPE, not false.
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
        # D-3c. The meaning travels with the state, so a status cannot be read as a stronger claim
        # than the checker made. In the paired rehearsal `answer_traced HOLDS` was read as "the
        # answer is correct"; it is not.
        if o.get("establishes"):
            L.append(f"      MEANS: {o['establishes']}")
            L.append(f"      DOES NOT MEAN: {o['does_not_establish']}")
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
                if o.get("position_field_text") is not None:
                    where += f" (the field reads {o['position_field_text']!r})"
            L.append(f"  {o['operand']:>14}  {o['class']}{where}")
            if o.get("origin_why"):
                L.append(f"                  {o['origin_why']}")
            # D-3d. SOLE_IN_ROW is computed and REJECTED as support by `oqa_occ`; it reached no
            # decision, and printing it in one list with the two that did was misleading.
            if o.get("supporting_corroborations"):
                L.append("                  corroborated by "
                         + ", ".join(o["supporting_corroborations"]))
            if o.get("observed_not_counted_as_support"):
                L.append("                  also observed, NOT counted as support: "
                         + ", ".join(o["observed_not_counted_as_support"]))
            if o.get("contract_refuted_here"):
                L.append("                  the learned rule predicted a source here and the resolver "
                         "did not confirm it")
        L.append("")
    abandoned = stage.get("abandoned_alternatives") or []
    ancestors = stage.get("consumed_ancestors") or []
    if abandoned:
        L.append("WORK THAT WAS REPLACED. It stays in the record and stops supporting the answer; it is "
                 "not an accusation.")
        for sc in abandoned[:8]:
            L.append(f"  calculation {sc['index']}: {sc['statement']}  -> replaced")
        if len(abandoned) > 8:
            L.append(f"  ... and {len(abandoned) - 8} more, all still recorded")
        L.append("")
    if ancestors:
        # D-3e. These used to be printed as replaced work. The final answer still consumes them.
        L.append("EARLIER WORK THE ANSWER STILL CONSUMES. Not replaced - these results feed the "
                 "calculation that supports the committed answer.")
        for sc in ancestors[:8]:
            note = "  (a RESTATEMENT: it reproduces its own operand and establishes no origin)" \
                if sc.get("is_identity_restatement") else ""
            L.append(f"  calculation {sc['index']}: {sc['statement']}{note}")
        L.append("")
    if stage.get("learned_rule_applied"):
        L.append("THE LEARNED RULE THIS REPORT APPLIED")
        for line in contract_rendered.splitlines():
            L.append(f"  {line}")
        L.append(f"  model {model_sha256[:16]}")
    else:
        L.append("NO LEARNED RULE WAS APPLIED. Every state above comes from the supplied resolver and "
                 "the supplied stage logic.")
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
             f"unresolved provenance (NOT an actor error) "
             f"{counts.get('unresolved_provenance', 0)}  |  "
             f"refutations of this checker {counts['model_refutations']}  |  "
             f"confirmations {counts['confirmations']}  |  unknowns {counts['unknowns']}")
    return "\n".join(L) + "\n"
