"""Amendments D-2 .. D-5: a numeral in a label is not a cell, and what the report may claim.

Every test here is deterministic and runs from constructed fixtures. The tests that need the frozen
48-trajectory record set are opt-in through `OQA_LN_FREEZE` and skip without it, so the file is
runnable anywhere while the record-based checks still run where the records are.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESEARCH = os.path.join(os.path.dirname(_HERE), "research", "officeqa")
if _RESEARCH not in sys.path:
    sys.path.insert(0, _RESEARCH)

import oqa_d2_ancestry as AN  # noqa: E402
import oqa_d2_rule as D2R  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_occ as OCC  # noqa: E402


# ── D-2: what counts as a numeric cell ───────────────────────────────────────

@pytest.mark.parametrize("field", [
    "81774", "-74", "1,234", "(1,234)", "12.5%", "$1,234", "684 $", "1,089 6/ 7/", "1960 8/", "0",
])
def test_the_numeric_formats_the_parser_already_supports_are_retained(field):
    assert OCC.is_numeric_cell(field), field


@pytest.mark.parametrize("field", [
    "Receipts Carriers' Taxing Act of 1937",       # the audit's strongest supported binding
    "Unnamed: 14_level_2",                         # a rendering artifact
    "Description of security refunded > Date of original issue > Unnamed: 14_level_2",
    "1984-Continued",
    "1962 Dec. 6 13 20 27",
    "8/21/91",                                     # a date, not a value
    "2.861 2.807 2.860 2.893",                     # a collapsed multi-value rendering
    "2 -148,086",
    "5/",                                          # a footnote reference standing alone
    "1962.",
    "nan",
    "",
])
def test_a_numeral_inside_label_text_or_an_ambiguous_field_is_not_a_cell(field):
    assert not OCC.is_numeric_cell(field), field


def _obs(line: str, cmd: str = "grep(pattern='x', path='/d/doc.txt')") -> list:
    return [{"type": "tool_call", "cmd": cmd, "obs": line},
            {"type": "message", "content": "x"}]


def test_the_superseded_behaviour_is_still_the_default_of_oqa_occ():
    """Every caller that has not been reviewed against D-2 must be byte-identical."""
    items = _obs("910: | Fiscal year | Act of 1937 | 1,234 |")
    legacy = OCC.observed_occurrences(items)
    assert sorted(o["value"] for o in legacy) == [1234.0, 1937.0]
    corrected = OCC.observed_occurrences(items, numeric_cells_only=True)
    assert [o["value"] for o in corrected] == [1234.0]


def test_label_text_is_still_evidence_even_though_it_is_no_longer_a_position():
    items = _obs("910: | Polish zlotys | 86000 |")
    corrected = OCC.observed_occurrences(items, numeric_cells_only=True)
    assert len(corrected) == 1
    assert "polish" in corrected[0]["line_tokens"] and "zlotys" in corrected[0]["line_tokens"]


def test_a_cell_yields_its_leading_numeral_only():
    items = _obs("12: | Something | 1,089 6/ 7/ |")
    got = OCC.observed_occurrences(items, numeric_cells_only=True)
    assert [o["value"] for o in got] == [1089.0]


def test_sole_in_row_counts_cells_not_numerals_in_headings():
    """A year inside a heading used to make a data value non-sole on its own line."""
    items = _obs("5: | Act of 1937 | 1937 |")
    legacy = {o["field"]: o["sole_in_row"] for o in OCC.observed_occurrences(items)}
    corrected = {o["field"]: o["sole_in_row"]
                 for o in OCC.observed_occurrences(items, numeric_cells_only=True)}
    assert legacy[2] is False          # two occurrences of 1937 on the line
    assert corrected[2] is True        # only the cell remains


def test_an_ambiguous_match_stays_unknown_rather_than_being_called_wrong():
    items = _obs("5: | Report date | 8/21/91 |")
    assert OCC.observed_occurrences(items, numeric_cells_only=True) == []


# ── D-4: a fragment of a larger expression ───────────────────────────────────

def test_a_binary_match_inside_a_parenthesised_expression_is_a_fragment():
    text = "- California percentage = 472,027,235 / 3,564,583,961 × 100 = 13.24213093"
    hit = [m for m in EV._CALC.finditer(text)][-1]
    assert EV.is_fragment(text, hit.start())


def test_a_markdown_bullet_is_not_a_fragment():
    text = "- 100 - 50 = 50"
    hit = list(EV._CALC.finditer(text))[0]
    assert not EV.is_fragment(text, hit.start())


def test_a_plain_statement_after_an_equals_sign_is_not_a_fragment():
    text = "Change = 13.24213093 - 13.17429074 = 0.06784019"
    hit = list(EV._CALC.finditer(text))[0]
    assert not EV.is_fragment(text, hit.start())


# ── D-5: what the committed answer may be ────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("4226", 4226.0), ("4,226", 4226.0), ("0.068", 0.068), ("$1,234", 1234.0), ("13.2%", 13.2),
])
def test_a_single_numeral_answer_is_read(raw, expected):
    value, why = EV.committed_value(raw)
    assert value == expected and why == ""


@pytest.mark.parametrize("raw", ["[10102000000, 4.73]", "March 3, 1977", "Information not available"])
def test_an_answer_that_is_not_one_number_is_unreadable_rather_than_mangled(raw):
    value, why = EV.committed_value(raw)
    assert value is None
    assert "not a single number" in why


def test_absolute_value_arithmetic_is_detected_as_unrepresented():
    assert EV._ABS_STATEMENT.search("- |2.0% - 0.2%| = 1.8%")
    assert EV._ABS_STATEMENT.search("Absolute difference = |2.0 - 0.2| = 1.8")


# ── D-3: what a state may be read to mean ────────────────────────────────────

def test_every_obligation_carries_what_it_does_and_does_not_establish():
    for name, (does, does_not) in CK.OBLIGATION_MEANING.items():
        assert does and does_not, name
    assert "NOTHING about whether that answer is correct" in \
        CK.OBLIGATION_MEANING["answer_traced"][1]


def test_an_identity_restatement_does_not_settle_an_origin():
    """`86,000 - 0 = 86,000` reproduces its own operand; following it establishes nothing."""
    operand = {"value": 86000.0, "features": {"round_power_of_ten": False,
                                              "appears_in_question": False}}
    verdict = {"sourced": False, "match": OCC.SEVERAL_POSITIONS, "n_positions": 2}
    dep = {"candidates": [0], "candidates_are_identities": [0]}
    got = CK.classify_operand(operand, verdict, dep, set())
    assert got["class"] == CK.RESTATEMENT_ONLY
    assert got["settled"] is False


def test_a_real_earlier_producer_does_settle_an_origin():
    operand = {"value": 81774.0, "features": {"round_power_of_ten": False,
                                              "appears_in_question": False}}
    verdict = {"sourced": False, "match": OCC.NO_OCCURRENCE, "n_positions": 0}
    dep = {"candidates": [1], "candidates_are_identities": []}
    got = CK.classify_operand(operand, verdict, dep, set())
    assert got["class"] == CK.DERIVED and got["settled"] is True


def test_several_candidate_producers_keep_the_ambiguity():
    operand = {"value": 5.0, "features": {"round_power_of_ten": False,
                                          "appears_in_question": False}}
    verdict = {"sourced": False, "match": OCC.NO_OCCURRENCE, "n_positions": 0}
    dep = {"candidates": [1, 4], "candidates_are_identities": []}
    got = CK.classify_operand(operand, verdict, dep, set())
    assert got["ambiguous"] is True and got["producers"] == [1, 4]


def test_several_observed_positions_are_an_ambiguous_origin_not_an_absent_one():
    operand = {"value": 86000.0, "features": {"round_power_of_ten": False,
                                              "appears_in_question": False}}
    verdict = {"sourced": False, "match": OCC.SEVERAL_POSITIONS, "n_positions": 2}
    got = CK.classify_operand(operand, verdict, {"candidates": []}, set())
    assert got["class"] == CK.AMBIGUOUS_ORIGIN and got["settled"] is False


def test_sole_in_row_reaches_no_decision_only_the_display():
    """It is computed and rejected by `oqa_occ`; `binding` reads `supporting`, which excludes it."""
    assert "SOLE_IN_ROW" in OCC.NON_INFORMATIVE
    assert "SOLE_IN_ROW" not in OCC.SUPPORTING
    items = [{"type": "tool_call", "cmd": "grep(pattern='zz', path='/d/doc.txt')",
              "obs": "5: | Polish zlotys | 86000 |"},
             {"type": "message", "content": "I read nothing relevant here."}]
    binding = OCC.bind(items, numeric_cells_only=True)["bindings"]
    assert binding == [] or all(b["binding"] == OCC.UNKNOWN for b in binding)


# ── the dependency structure ─────────────────────────────────────────────────

def _fake_extract(statements, committed):
    calcs = []
    for op_a, op, op_b, result in statements:
        calcs.append({"op": op, "result": result, "statement": f"{op_a} {op} {op_b} = {result}",
                      "arithmetic_consistent": True, "fragment_of_larger_expression": False,
                      "operands": [{"value": op_a, "as_written": str(op_a)},
                                   {"value": op_b, "as_written": str(op_b)}]})
    return {"calculations": calcs, "committed_answer_value": committed}


def test_an_intermediate_the_answer_consumes_is_not_abandoned_work():
    ex = _fake_extract([(5541720.0, "-", 5459946.0, 81774.0),
                        (86000.0, "-", 81774.0, 4226.0)], 4226.0)
    got = EV.classify_earlier(ex, 1, [0])
    assert got[0]["dependency_class"] == "CONSUMED_ANCESTOR"


def test_work_no_path_reaches_is_abandoned():
    ex = _fake_extract([(31336.0, "-", 30442.0, 894.0),
                        (29943.0, "-", 29726.0, 217.0)], 217.0)
    got = EV.classify_earlier(ex, 1, [0])
    assert got[0]["dependency_class"] == "ABANDONED_ALTERNATIVE"


def test_an_identity_is_recognised_as_a_restatement():
    ex = _fake_extract([(86000.0, "-", 0.0, 86000.0)], 86000.0)
    assert EV._is_identity(ex["calculations"][0])


def test_a_replacement_needs_an_abandoned_alternative():
    """Every intermediate of a multi-step computation differs from the answer while feeding it."""
    ex = _fake_extract([(5541720.0, "-", 5459946.0, 81774.0),
                        (86000.0, "-", 81774.0, 4226.0)], 4226.0)
    earlier = EV.classify_earlier(ex, 1, [0])
    assert not [e for e in earlier if e["dependency_class"] == "ABANDONED_ALTERNATIVE"]


# ── section 4: the rule-absent reference ─────────────────────────────────────

def test_the_rule_absent_condition_makes_no_prediction_rather_than_a_false_one():
    verdict, why = CK._applies(None, {"n_positions_in_prefix": 1, "source_tool": "grep"})
    assert verdict is None and why == []


def test_the_four_conditions_are_the_two_by_two():
    names = [c[0] for c in D2R.CONDITIONS]
    assert names == ["superseded_extraction/rule_applied", "superseded_extraction/rule_absent",
                     "D2_extraction/rule_applied", "D2_extraction/rule_absent"]


# ── the information boundary still holds in the new modules ──────────────────

@pytest.mark.parametrize("module", ["oqa_d2_cells.py", "oqa_d2_rule.py", "oqa_d2_ancestry.py",
                                    "oqa_d2_run.py"])
def test_no_new_module_reads_a_score_or_an_answer_key(module):
    import ast
    src = open(os.path.join(_RESEARCH, module), encoding="utf-8").read()
    tree = ast.parse(src)
    forbidden = {"ground_truth", "soft", "hard", "reasoning_content"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in forbidden, f"{module} names {node.value!r}"
        if isinstance(node, ast.Attribute):
            assert node.attr not in forbidden, f"{module} reads .{node.attr}"


def test_no_new_module_can_send_a_paid_request():
    """None of the audit modules imports the model layer, so none can spend."""
    import ast
    for module in ("oqa_d2_cells.py", "oqa_d2_rule.py", "oqa_d2_ancestry.py", "oqa_d2_run.py"):
        src = open(os.path.join(_RESEARCH, module), encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                mod = getattr(node, "module", "") or ""
                assert "skillopt" not in mod and not any("skillopt" in n for n in names), module


# ── over the frozen records, where they are available ────────────────────────

def _freeze():
    path = os.environ.get("OQA_LN_FREEZE", "")
    if not path or not os.path.exists(path):
        pytest.skip("set OQA_LN_FREEZE to the frozen record set to run the record-based checks")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_the_d2_correction_changes_no_operand_and_no_statement():
    """D-2 is about occurrences. The parse, the operand count and the arithmetic must not move."""
    import oqa_d2_cells as CELLS
    fz = _freeze()
    out = CELLS.replay(fz)
    for role, checks in out["out_of_scope_material_unchanged"].items():
        assert all(checks.values()), (role, checks)


def test_source_traced_is_never_an_accusation_on_the_frozen_records():
    fz = _freeze()
    import glob
    models = sorted(glob.glob(os.path.join(os.path.dirname(os.environ["OQA_LN_FREEZE"]),
                                           "..", "v2_learn", "r*_model_v1.json")))
    if not models:
        pytest.skip("no frozen model beside the freeze")
    with open(models[-1], encoding="utf-8") as f:
        model = json.load(f)
    contract = [c for c in model["contracts"] if c["status"] == "active"][-1]
    wanted = {r["attempt_id"] for r in fz["recovery_controls"]}
    rows = [a for a in fz["attempts"] if a["role"] == "check" or a["attempt_id"] in wanted]
    for a in rows:
        rep = CK.stage_report(fz, a, contract)
        for o in rep["obligations"]:
            if o["obligation"] == "source_traced":
                assert o["state"] != CK.VIOLATED
                assert not o.get("actor_execution_diagnosis")
