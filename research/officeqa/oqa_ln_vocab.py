#!/usr/bin/env python3
"""The source-to-calculation vocabulary. Amendment D-1, rule OQA-LN-1.

A SEPARATE NAMESPACE, not an extension of `oqa.cc.model.v1`. The 2026-11-17 learner enumerates
`oqa_cc_ir.PREDICATES` into its own prompt, so extending that dict in place would silently change the
frozen prototype's behaviour. Everything here is additive and the old module keeps its exact vocabulary;
`oqa_cc_ir.validate`, `clause`, `contract` now take an optional vocabulary and default to the old one.

THE ONE LEARNABLE CONCLUSION is `operand_sourced`. Everything else is supplied structure (GIVEN), a
reading of the question (TASK), a reading of the observations or the actor's own message (OBS), or a
definition (MATH). A learner that labels any of those LEARNED is rejected before it is scored.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402

SCHEMA = "oqa.ln.model.v1"
RULE = "OQA-LN-1"
BUNDLE_LABEL = "v10"

TASK, GIVEN, MATH, LEARNED, OBS = IR.TASK, IR.GIVEN, IR.MATH, IR.LEARNED, IR.OBS

ENTITIES = tuple(IR.ENTITIES) + ("operand", "answer")

# Non-entity argument sorts, so a validator can tell an entity from a literal.
SORTS = {"tool": ("grep", "read", "none", "mixed"),
         "count": ("0", "1", "many"),
         "exp10": "an integer power of ten"}

EXTRA_PREDICATES = {
    # ── supplied by the parse: a definition of the object, not a discovery about it ──
    "operand_of":                  {"args": ("operand", "calculation"), "provenance": GIVEN},
    "calculation_result":          {"args": ("calculation", "value"), "provenance": GIVEN},
    # ── a definition ──
    "arithmetic_consistent":       {"args": ("calculation",), "provenance": MATH},
    # ── read off the observations or the actor's own message ──
    "calculation_supports_answer": {"args": ("calculation", "answer"), "provenance": OBS},
    "calculation_superseded":      {"args": ("calculation", "calculation"), "provenance": OBS},
    "operand_positions":           {"args": ("operand", "count"), "provenance": OBS},
    "operand_source_tool":         {"args": ("operand", "tool"), "provenance": OBS},
    "operand_written_with_separator": {"args": ("operand",), "provenance": OBS},
    "operand_round_power_of_ten":  {"args": ("operand",), "provenance": OBS},
    "operand_equals_earlier_result": {"args": ("operand", "calculation"), "provenance": OBS},
    "message_quotes_question_term": {"args": ("operand",), "provenance": OBS},
    # ── read from the question sentence ──
    "operand_in_question":         {"args": ("operand",), "provenance": TASK},
    # ── THE ONLY LEARNABLE CONCLUSION ──
    "operand_sourced":             {"args": ("operand",), "provenance": LEARNED},
}

PREDICATES = {**IR.PREDICATES, **EXTRA_PREDICATES}
NOT_LEARNABLE = tuple(p for p, s in PREDICATES.items() if s["provenance"] in (MATH, GIVEN))

STAGE_OBLIGATIONS = tuple(IR.STAGE_OBLIGATIONS) + ("source_traced",)

# The conditions a learner may actually condition on. Deliberately a SUBSET of PREDICATES: the
# resolver's own two corroborations are not here, and neither is anything that would let a rule restate
# the resolver's definition. See amendment D-1 for why, and for the limit of that claim.
LEARNER_CONDITIONS = (
    "operand_positions", "operand_source_tool", "operand_written_with_separator",
    "operand_round_power_of_ten", "operand_in_question", "operand_equals_earlier_result",
    "message_quotes_question_term", "operand_of",
)


def clause(pred, args, value=True, provenance=None, evidence=None):
    return IR.clause(pred, args, value, provenance, evidence, vocab=PREDICATES)


def contract(cid, conclusion, conditions, **kw):
    return IR.contract(cid, conclusion, conditions, entities=ENTITIES, **kw)


def validate(c: dict) -> list:
    """IR's own logic over THIS vocabulary, plus the two rules that are specific to it."""
    problems = IR.validate(c, vocab=PREDICATES, entities=ENTITIES, obligations=STAGE_OBLIGATIONS)
    conc = (c.get("conclusion") or {}).get("pred")
    if conc and conc not in ("operand_sourced", "scale_binds"):
        problems.append(f"{conc!r} is not a conclusion this vocabulary learns; the only learnable "
                        f"conclusion here is operand_sourced")
    for cl in c.get("conditions") or []:
        p = cl.get("pred")
        if p in EXTRA_PREDICATES and p not in LEARNER_CONDITIONS and p != "operand_sourced":
            problems.append(f"{p!r} may not be a learned condition: it is either the resolver's own "
                            f"input or a consequence rather than an antecedent")
    return sorted(set(problems))


def render(c: dict) -> str:
    return IR.render(c)


def repair(c: dict, extra_condition: dict, reason: str, counterexamples: list):
    return IR.repair(c, extra_condition, reason, counterexamples)


def model(contracts: list, **kw) -> dict:
    m = IR.model(contracts, **kw)
    m["schema"] = SCHEMA
    m["rule"] = RULE
    m["vocabulary_bundle"] = BUNDLE_LABEL
    return m


def bundle_digest() -> str:
    """The semantic digest of THIS vocabulary, so one label can never mean two definitions."""
    return IR.sha(IR.canon({
        "schema": SCHEMA,
        "predicates": {p: {"args": list(v["args"]), "provenance": v["provenance"]}
                       for p, v in sorted(PREDICATES.items())},
        "entities": list(ENTITIES),
        "stage_obligations": list(STAGE_OBLIGATIONS),
        "learner_conditions": list(LEARNER_CONDITIONS),
        "truths": list(IR.TRUTHS),
        "provenance": list(IR.PROVENANCE),
    }))


INITIAL_MODEL_DECLARATION = {
    "what_this_is": "the declared initial state of a SEPARATE prototype model bank. It holds NO "
                    "contracts. Nothing is carried over from the 2026-11-17 unit hypothesis, and no "
                    "existing rule is renamed into it.",
    "contracts": [],
    "learned_from": [],
}


def initial_model() -> dict:
    m = model([], epoch=0, scope="declared initial state: empty", learned_from=[])
    m["declaration"] = INITIAL_MODEL_DECLARATION["what_this_is"]
    return m
