"""The learned source-to-calculation contract: the boundary, the prefix discipline, and the states.

Rule OQA-LN-1, amendment D-1. These tests are about the properties the instruction names, in the order
it names them, and each one says which property it pins.

The fixtures are constructed except where a docstring says the text is quoted from a saved record.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")


def _mod(name):
    for p in (REPO, RESEARCH):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"{name}_ln_test", os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


V = _mod("oqa_ln_vocab")
EV = _mod("oqa_ln_evidence")
LN = _mod("oqa_ln_learn")
CK = _mod("oqa_ln_check")

# Quoted from the structure of a saved trajectory: a list of {"role"/"type", ...} entries. The numbers
# are constructed so that each feature can be exercised on its own.
TRACE = [
    {"role": "user", "content": "## Question\nIn 1961, how much paper money was added in circulation? "
                                "Report your answer in millions of nominal dollars.\n\n## Oracle Parsed "
                                "Pages\n(omitted)\n"},
    {"type": "tool_call",
     "cmd": "grep(pattern='total paper money', path='/data/treasury_bulletin_1963_01.txt')",
     "obs": "4343: | End of year | Total paper money | Gold certificates |\n"
            "4350: | 1960 | 29726 | 311 |\n"
            "4351: | 1961 | 29943 | 309 |\n"},
    {"type": "message", "content": "The table gives total paper money by year.\n"
                                   "29,943 - 29,726 = 217\n"},
    {"type": "message", "content": "Converting: 217 * 1 = 217\n<answer>217</answer>"},
]


def _attempt(items=None, user=None):
    return {"item": "UID9999", "items": items if items is not None else TRACE,
            "conversation_path": "<constructed>", "trace_sha256": "0" * 64,
            "system_prompt": "", "user_prompt": user if user is not None else TRACE[0]["content"]}


# ── the information boundary ──────────────────────────────────────────────────

FORBIDDEN_FIELDS = ("ground_truth", "soft")


def test_no_learned_source_module_reads_a_forbidden_field():
    """The boundary is structural, not a habit: no module in this family may name the answer key.

    `hard` is triage-only and is not read either, because the recovery control is selected by the SHAPE
    of the trajectory. A grep would pass on a comment; this walks the syntax tree.
    """
    for name in ("oqa_ln_evidence", "oqa_ln_learn", "oqa_ln_check", "oqa_ln_run", "oqa_ln_vocab",
                 "oqa_ln_rehearse", "oqa_ln_lean"):
        src = open(os.path.join(RESEARCH, f"{name}.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        # a docstring may DISCUSS the boundary; a subscript or a .get() may not USE it
        used = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
                if n.slice.value in FORBIDDEN_FIELDS:
                    used.append(n.slice.value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get":
                for arg in n.args[:1]:
                    if isinstance(arg, ast.Constant) and arg.value in FORBIDDEN_FIELDS:
                        used.append(arg.value)
        assert not used, f"{name} reads {sorted(set(used))}"
        assert "hard" not in [s for s in strings if s == "hard"], (
            f"{name} names the triage-only score field; the recovery control is selected by shape")


def test_the_learner_prompt_carries_no_answer_no_score_and_no_checking_record():
    """What the proposer is shown: a contingency table over the LEARNING records and nothing else."""
    rows = [{"attempt_id": "p1/UID0001", "item": "UID0001", "pass": 1, "operand_key": "0/0",
             "as_written": "29,943", "value": 29943.0, "statement": "29,943 - 29,726 = 217",
             "message_item_index": 2, "arithmetic_consistent": True,
             "features": {k: (1 if k == "n_positions_in_prefix" else
                              "grep" if k == "source_tool" else False) for k in LN.FEATURES},
             "verdict": {"sourced": True, "match": "UNIQUE_POSITION", "binding": "SUPPORTED",
                         "corroborations": ["ROW_LABEL"], "supporting": ["ROW_LABEL"],
                         "n_positions": 1, "position": None, "why": ""}}]
    prompt = LN.build_prompt(rows, {"selection": {"check_questions": ["UID0085"]}})
    for banned in ("ROW_LABEL", "QUERY", "SOLE_IN_ROW", "ground_truth", "UID0085", "hard"):
        assert banned not in prompt, f"the learner prompt leaks {banned!r}"
    assert "operand_sourced" in prompt
    # and only the permitted conditions are offered
    for p in V.LEARNER_CONDITIONS:
        assert p in prompt
    for p in ("calculation_supports_answer", "calculation_superseded", "arithmetic_consistent"):
        assert f"  {p}(" not in prompt, f"{p} is offered as a condition and must not be"


# ── the prefix discipline ─────────────────────────────────────────────────────

def test_features_see_only_the_prefix_and_the_verdict_sees_the_writing_message():
    """An observation that arrives AFTER the calculation must not change its features."""
    ex_before = EV.extract(_attempt())
    later = TRACE + [{"type": "tool_call", "cmd": "grep(pattern='217', path='/data/x.txt')",
                      "obs": "9: | row | 217 |\n"}]
    ex_after = EV.extract(_attempt(items=later))
    f_before = ex_before["calculations"][0]["operands"][0]["features"]
    f_after = ex_after["calculations"][0]["operands"][0]["features"]
    assert f_before == f_after, "a later observation changed an earlier calculation's features"


def test_a_prediction_is_digested_before_any_verdict_is_read():
    """`predict` must not touch `verdict`. Asserted structurally AND by digest stability."""
    src = open(os.path.join(RESEARCH, "oqa_ln_check.py"), encoding="utf-8").read()
    body = src[src.index("def predict("):src.index("def resolve(")]
    assert '"verdict"' not in body and "['verdict']" not in body, (
        "predict() reads a verdict; the prediction must be committed before the verdict is read")
    c = V.contract("t", V.clause("operand_sourced", ["operand"]),
                   [V.clause("operand_positions", ["operand", "1"])])
    rows = [{"attempt_id": "a", "item": "i", "operand_key": "0/0", "as_written": "1", "value": 1.0,
             "statement": "s", "features": {k: (1 if k == "n_positions_in_prefix" else
                                                "grep" if k == "source_tool" else False)
                                            for k in LN.FEATURES},
             "verdict": {"sourced": True, "match": "UNIQUE_POSITION"}}]
    d1 = CK.predict(c, rows)["self_sha256"]
    rows[0]["verdict"] = {"sourced": False, "match": "NO_OCCURRENCE"}
    d2 = CK.predict(c, rows)["self_sha256"]
    assert d1 == d2, "the prediction digest changed when only the verdict changed"


# ── the vocabulary ────────────────────────────────────────────────────────────

def test_only_operand_sourced_may_be_the_learned_conclusion():
    for pred in ("arithmetic_consistent", "operand_of", "calculation_supports_answer"):
        bad = V.contract("x", V.clause(pred, ["calculation", "answer"][:len(V.PREDICATES[pred]["args"])]),
                         [V.clause("operand_positions", ["operand", "1"])])
        assert V.validate(bad), f"{pred} was admitted as a learned conclusion"


def test_an_unconditional_claim_is_refused():
    bad = V.contract("x", V.clause("operand_sourced", ["operand"]), [])
    assert any("applicability condition" in p for p in V.validate(bad))


def test_the_extended_vocabulary_does_not_change_the_frozen_one():
    IR = _mod("oqa_cc_ir")
    assert len(IR.PREDICATES) == 11, "oqa.cc.model.v1 gained a predicate; the 2026-11-17 learner's "
    assert IR.ENTITIES == ("table", "header", "cell", "value", "calculation")
    assert IR.STAGE_OBLIGATIONS == ("unit_match", "cell_bound", "period_match", "arith_recorded",
                                    "answer_traced")
    # and the extension is a superset
    assert set(IR.PREDICATES) < set(V.PREDICATES)
    assert "source_traced" in V.STAGE_OBLIGATIONS


def test_the_vocabulary_bundle_label_means_one_digest():
    first = V.bundle_digest()
    assert first == V.bundle_digest()
    assert V.BUNDLE_LABEL == "v10"


def test_the_initial_model_bank_is_declared_empty():
    """'Begin a separate prototype model bank from its declared initial state.'"""
    m = V.initial_model()
    assert m["contracts"] == []
    assert m["schema"] == "oqa.ln.model.v1"
    assert "empty" in m["evidence_scope"]


# ── the five kinds of outcome ────────────────────────────────────────────────

def _row(match, sourced, applies=True):
    return {"attempt_id": "a", "item": "i", "operand_key": "0/0", "as_written": "1", "value": 1.0,
            "statement": "s",
            "features": {k: ((1 if applies else 0) if k == "n_positions_in_prefix" else
                             "grep" if k == "source_tool" else False) for k in LN.FEATURES},
            "verdict": {"sourced": sourced, "match": match, "binding": "x", "corroborations": [],
                        "supporting": [], "n_positions": 1, "position": None, "why": "w"}}


@pytest.mark.parametrize("match,sourced,expected", [
    ("UNIQUE_POSITION", True, CK.CONFIRMATION),
    ("UNIQUE_POSITION", False, CK.MODEL_REFUTATION),
    ("SEVERAL_POSITIONS", False, CK.EXTRACTION_AMBIGUITY),
    ("NO_OCCURRENCE", False, CK.MISSING_INFORMATION),
])
def test_every_discrepancy_is_classified_and_the_four_kinds_are_distinct(match, sourced, expected):
    c = V.contract("t", V.clause("operand_sourced", ["operand"]),
                   [V.clause("operand_positions", ["operand", "1"])])
    rows = [_row(match, sourced)]
    out = CK.resolve(CK.predict(c, rows), rows)
    assert out["results"][0]["discrepancy_kind"] == expected


def test_a_contract_that_does_not_apply_is_silent_not_a_prediction_of_unsourced():
    c = V.contract("t", V.clause("operand_sourced", ["operand"]),
                   [V.clause("operand_positions", ["operand", "1"])])
    rows = [_row("NO_OCCURRENCE", False, applies=False)]
    out = CK.resolve(CK.predict(c, rows), rows)
    assert out["results"][0]["discrepancy_kind"] is None
    assert out["results"][0]["prediction"] == "silent"


# ── the stage connection ─────────────────────────────────────────────────────

def test_a_model_refutation_makes_the_obligation_unknown_and_never_an_accusation():
    """'A model refutation is not misreported as an actor mistake.'"""
    src = open(os.path.join(RESEARCH, "oqa_ln_check.py"), encoding="utf-8").read()
    block = src[src.index("if model_refutations:"):src.index("elif not unresolved:")]
    assert "UNKNOWN" in block
    assert "VIOLATED" not in block and "OUTSTANDING" not in block
    assert "not an error by the actor" in block


def test_an_unavailable_channel_is_unknown_not_discharged_and_not_violated():
    """'Missing action effects do not silently imply that nothing changed.'"""
    no_tools = [TRACE[0], TRACE[2], TRACE[3]]
    ex = EV.extract(_attempt(items=no_tools))
    assert ex["calculations"], "the constructed trace should still carry a calculation"
    # the occurrence channel is unavailable, because no observation was returned at all
    OCC = _mod("oqa_occ")
    assert OCC.observed_occurrences(no_tools) == []


def test_the_report_states_what_unknown_means_and_names_no_score():
    stage = {"task_requirements": {}, "obligations": [
        {"obligation": "source_traced", "state": CK.UNKNOWN, "why": "the records cannot settle it",
         "provenance": "OBS"}],
        "supporting_operands": [], "superseded_calculations": [],
        "diagnosis_counts": {"confirmations": 0, "model_refutations": 0, "execution_diagnoses": 0,
                             "unknowns": 1}}
    text = CK.render_report(stage, n_learn_operands=10, contract_rendered="c@v1", model_sha256="a" * 64)
    assert "UNKNOWN means the records cannot settle it. It is never an accusation." in text
    # The disclaimer legitimately contains the WORD "score" ("Nothing here reads the answer key, the
    # score, ..."), so the test looks for a score being USED, not for the word.
    assert "Nothing here reads the answer key, the score" in text
    for banned in ("ground_truth", "the scorer marked", "correct answer is", "hard=", '"hard"'):
        assert banned not in text, f"the report uses {banned!r}"


# ── the recovery control ─────────────────────────────────────────────────────

def test_the_recovery_control_is_selected_by_shape_and_not_by_any_outcome():
    """Two calculations with different results, the later one equal to the committed answer."""
    items = [TRACE[0], TRACE[1],
             {"type": "message", "content": "First try: 29,943 - 29,385 = 558\n"},
             {"type": "message", "content": "Actually: 29,943 - 29,726 = 217\n<answer>217</answer>"}]
    ex = EV.extract(_attempt(items=items))
    shape = EV.recovery_shape(ex)
    assert shape["qualifies"] is True
    assert shape["committed_answer_value"] == 217.0
    assert shape["superseded_calculations"], "the replaced attempt must be recorded as superseded"
    assert ex["calculations"][shape["supporting_calculation"]]["result"] == 217.0
    # the superseded one is still in the record
    assert any(abs(c["result"] - 558.0) < 1e-9 for c in ex["calculations"])


def test_one_calculation_repeated_is_not_a_recovery():
    items = [TRACE[0], TRACE[1],
             {"type": "message", "content": "29,943 - 29,726 = 217\n"},
             {"type": "message", "content": "again 29,943 - 29,726 = 217\n<answer>217</answer>"}]
    shape = EV.recovery_shape(EV.extract(_attempt(items=items)))
    assert shape["qualifies"] is False


def test_an_inconsistent_statement_is_a_math_judgement_and_is_recorded():
    items = [TRACE[0], TRACE[1],
             {"type": "message", "content": "29,943 - 29,726 = 300\n<answer>300</answer>"}]
    ex = EV.extract(_attempt(items=items))
    assert ex["calculations"][0]["arithmetic_consistent"] is False


# ── the evaluator is one place ───────────────────────────────────────────────

def test_a_condition_the_evaluator_cannot_read_is_not_evaluable_rather_than_true():
    """'Preserve uncertainty.' A condition outside the feature map must not quietly pass."""
    c = {"id": "t", "version": 1, "status": "active", "conditions": [
        {"pred": "calculation_supports_answer", "args": ["calculation", "answer"], "value": True}],
        "conclusion": V.clause("operand_sourced", ["operand"]), "binds": ["operand"]}
    verdict, why = LN.applies(c, {k: False for k in LN.FEATURES})
    assert verdict is None
    assert why and "cannot read" in why[0]


def test_chained_statements_are_counted_and_not_parsed_badly():
    items = [TRACE[0], TRACE[1],
             {"type": "message", "content": "1,000 + 2,000 + 3,000 = 6,000\n"}]
    ex = EV.extract(_attempt(items=items))
    assert ex["chained_statements_out_of_scope"] >= 1


def test_the_dry_run_configures_the_route_too():
    """Job 66552741 raised 48 times after a dry run reported everything healthy."""
    src = open(os.path.join(RESEARCH, "oqa_ln_run.py"), encoding="utf-8").read()
    main = src[src.index("def main("):]
    assert "configure_route(a.config, a.dry_run)" in main
    route = src[src.index("def configure_route("):src.index("def _write(")]
    assert "dry" in route and "configure_models" in route


# ── a CONSTRUCTED fixture for the repair machinery ───────────────────────────
#
# READ THIS BEFORE READING THE TESTS BELOW. These fixtures are INVENTED. They establish that the
# repair path works as software: that a counterexample produces a new version, that the old version is
# kept and marked superseded with its reason, that the parent link is recorded, and that a repair which
# loses ground is reported as a regression rather than as an improvement.
#
# THEY ESTABLISH NOTHING EMPIRICAL. On the natural traces the real learner was asked to repair a real
# contradiction and DECLINED, in both repetitions, because the counterexample shares its entire feature
# vector with operands the resolver did call sourced - so no predicate in the vocabulary can separate
# them. That is the empirical result, it is recorded with the run, and it is not what these fixtures
# are about. Nothing here is evidence that a learner repaired a hypothesis on real data.

def _synthetic_rows(n_right: int, n_wrong: int):
    """Rows where the counterexample IS separable, which the natural ones were not."""
    def row(i, tool, sourced, sep):
        return {"attempt_id": f"synthetic/{i}", "item": "SYNTHETIC", "pass": 1,
                "operand_key": f"{i}/0", "as_written": str(i), "value": float(i),
                "statement": f"{i} + 0 = {i}", "message_item_index": 1,
                "arithmetic_consistent": True,
                "features": {"n_positions_in_prefix": 1, "source_tool": tool,
                             "written_with_separator": sep, "round_power_of_ten": False,
                             "appears_in_question": False, "equals_earlier_result": False,
                             "message_quotes_question_term": True},
                "verdict": {"sourced": sourced, "match": "UNIQUE_POSITION",
                            "binding": "SUPPORTED" if sourced else "UNKNOWN",
                            "corroborations": [], "supporting": [], "n_positions": 1,
                            "position": None, "why": "synthetic"}}
    rows = [row(i, "grep", True, True) for i in range(n_right)]
    rows += [row(100 + i, "grep", False, False) for i in range(n_wrong)]
    return rows


def test_constructed_the_repair_path_versions_narrows_and_keeps_the_old_rule():
    """CONSTRUCTED. Software capability only - see the block comment above."""
    rows = _synthetic_rows(n_right=6, n_wrong=3)
    v1 = V.contract("synthetic.sourced", V.clause("operand_sourced", ["operand"]),
                    [V.clause("operand_source_tool", ["operand", "grep"])],
                    witnesses="source_traced")
    before = LN.score_contract(v1, rows)
    assert before["confirmations"] == 6 and before["counterexamples"] == 3

    counters, pool, provenance = LN.counterexamples_for_repair(v1, [], rows)
    assert len(counters) == 3
    assert provenance == LN.IN_SAMPLE

    extra = V.clause("operand_written_with_separator", ["operand"])
    old, v2 = V.repair(v1, extra, "the three counterexamples are the ones written without a separator",
                       [c["attempt_id"] for c in counters])
    assert V.validate(v2) == []
    assert old["status"] == "superseded", "the old version must be kept, not deleted"
    assert old["repair_reason"]
    assert v2["version"] == 2 and v2["status"] == "active"
    assert v2["parent"] == "synthetic.sourced@v1"
    assert len(v2["conditions"]) == len(v1["conditions"]) + 1

    after = LN.score_contract(v2, rows)
    assert after["counterexamples"] == 0, "the repair must remove the counterexamples it was built from"
    assert after["confirmations"] == 6, "and must not lose a confirmation it already had"
    lost = set(before["confirmation_keys"]) - set(after["confirmation_keys"])
    assert not lost


def test_constructed_a_repair_that_loses_ground_is_reported_as_a_regression():
    """CONSTRUCTED. The guard that stops a narrowing being sold as an improvement."""
    rows = _synthetic_rows(n_right=6, n_wrong=1)
    # six correct ones are written WITH a separator; make two of them without, so the same narrowing
    # now costs real coverage
    rows[0]["features"]["written_with_separator"] = False
    rows[1]["features"]["written_with_separator"] = False
    v1 = V.contract("synthetic.sourced", V.clause("operand_sourced", ["operand"]),
                    [V.clause("operand_source_tool", ["operand", "grep"])])
    before = LN.score_contract(v1, rows)
    _old, v2 = V.repair(v1, V.clause("operand_written_with_separator", ["operand"]), "narrowing", [])
    after = LN.score_contract(v2, rows)
    lost = sorted(set(before["confirmation_keys"]) - set(after["confirmation_keys"]))
    assert len(lost) == 2, lost
    assert after["counterexamples"] == 0
    assert after["confirmations"] < before["confirmations"], (
        "this narrowing removes the counterexample AND two confirmations; a report that showed only "
        "the first number would be selling a retreat as a repair")


def test_the_natural_contradiction_was_not_separable_and_that_is_recorded_not_patched():
    """The empirical result, pinned so it cannot quietly become a success story.

    Both repetitions proposed `operand_positions=1 AND operand_source_tool=grep`, both were given the
    same single counterexample, and both replied that no available predicate separates it. The reason
    is checkable from the evidence and is not a matter of the learner's opinion: the counterexample's
    feature vector is IDENTICAL to that of operands the resolver did call sourced, so no condition over
    those features can exclude one without excluding the others.
    """
    counter = {"n_positions_in_prefix": 1, "source_tool": "grep", "written_with_separator": False,
               "round_power_of_ten": False, "appears_in_question": False,
               "equals_earlier_result": False, "message_quotes_question_term": True}
    twin = dict(counter)          # an operand the resolver DID call sourced, same vector
    c = V.contract("t", V.clause("operand_sourced", ["operand"]),
                   [V.clause("operand_positions", ["operand", "1"]),
                    V.clause("operand_source_tool", ["operand", "grep"])])
    assert LN.applies(c, counter)[0] is True
    assert LN.applies(c, twin)[0] is True
    # every single-condition narrowing over the available features treats them identically
    for pred in V.LEARNER_CONDITIONS:
        if pred in ("operand_of",):
            continue
        for value in (True, False):
            args = ["operand"]
            if pred == "operand_positions":
                args = ["operand", "1"]
            elif pred == "operand_source_tool":
                args = ["operand", "grep"]
            elif pred == "operand_equals_earlier_result":
                args = ["operand", "calculation"]
            narrowed = V.contract("t2", V.clause("operand_sourced", ["operand"]),
                                  list(c["conditions"]) + [V.clause(pred, args, value)])
            a, b = LN.applies(narrowed, counter)[0], LN.applies(narrowed, twin)[0]
            assert a == b, (f"{pred}={value} would separate them, so 'not separable' would be wrong")
