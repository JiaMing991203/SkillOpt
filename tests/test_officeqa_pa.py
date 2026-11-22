"""PA-1 parity-probe tests. Offline; no provider call, no cluster, no ALFWorld file is touched."""
import ast
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
OQA = os.path.join(os.path.dirname(HERE), "research", "officeqa")
sys.path.insert(0, OQA)

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_occ as OCC  # noqa: E402
import oqa_pa_parity as PA  # noqa: E402

RUNS = "/Users/yaojiaming/dev/lean_skillopt/runs"
FREEZE = os.path.join(RUNS, "20261121-officeqa-learner/v1_freeze/LN_FREEZE.json")
MODEL = os.path.join(RUNS, "20261121-officeqa-learner/v2_learn/r1_model_v1.json")
needs_records = pytest.mark.skipif(not os.path.exists(FREEZE), reason="frozen records not present")


def _src(name):
    with open(os.path.join(OQA, name), encoding="utf-8") as f:
        return f.read()


# ── the claim that carries the audit: the classifier cannot see the contract ──
def test_classify_operand_takes_no_contract_argument():
    tree = ast.parse(_src("oqa_ln_check.py"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "classify_operand")
    names = [a.arg for a in fn.args.args]
    assert names == ["operand", "verdict", "dep_row", "q_nums"]
    body = ast.dump(fn)
    for forbidden in ("contract", "applies", "LEARNED"):
        assert forbidden not in body, f"{forbidden} reached the origin classifier"


def test_the_absent_condition_is_the_checkers_own_none_path():
    """Removing the rule must not be a second implementation, or the comparison drifts."""
    assert CK._applies(None, {"n_positions_in_prefix": "1"}) == (None, [])


def test_query_corroboration_is_entailed_by_a_grep_hit():
    """A grep returns a line BECAUSE the pattern is on it, so QUERY is not an empirical finding."""
    occ = {"line_tokens": ["german", "marks", "81774"], "pattern_tokens": ["german", "marks"],
           "sole_in_row": False}
    assert "QUERY" in OCC.corroborations(occ, {"81774"})
    # and QUERY alone is enough to make a unique position SUPPORTED
    assert "QUERY" in OCC.SUPPORTING


def test_the_repair_interface_can_only_append_a_condition():
    """ALFWorld's actual repair DELETED a conjunct. This interface cannot express that."""
    c = IR.contract("x.1", IR.clause("scale_binds", ["table", "cell", "scale"]),
                    [IR.clause("table_states_scale", ["table", "scale"]),
                     IR.clause("cell_in_table", ["cell", "table"])])
    old, new = IR.repair(c, IR.clause("asked_scale", ["scale"]), "narrowed", ["a1"])
    assert old["status"] == "superseded"
    assert len(new["conditions"]) == len(c["conditions"]) + 1
    assert all(cl in new["conditions"] for cl in c["conditions"])  # nothing can be removed
    src = _src("oqa_cc_ir.py")
    for op in ('"retire"', '"revise"', '"drop"', '"widen"'):
        assert op not in src, f"{op} appeared; the repair vocabulary changed"


def test_the_probe_module_reads_no_score_and_no_answer_key():
    """Forbidden fields must not be reached. Docstrings are prose and are excluded."""
    tree = ast.parse(_src("oqa_pa_parity.py"))
    docstrings = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    doctexts = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    banned = ("ground_truth", "reasoning_content", "heldout", "held_out", "val_baseline")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in doctexts:
                continue
            low = node.value.lower()
            for b in banned:
                assert b not in low, f"{b!r} appears in a code string constant"
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""])
            assert not any(str(m).startswith("skillopt") for m in mods)


@needs_records
def test_callpath_finds_the_rule_changes_no_state():
    with open(FREEZE, encoding="utf-8") as f:
        freeze = json.load(f)
    with open(MODEL, encoding="utf-8") as f:
        model = json.load(f)
    os.environ.setdefault("OQA_LN_SOURCE_ROOT",
                          os.path.join(RUNS, "20261121-officeqa-two-baselines/s0"))
    doc = PA.callpath(freeze, model)
    assert doc["all_obligation_states_equal"] is True
    assert doc["all_operand_classes_equal"] is True
    assert doc["totals"].get("classify_saw_contract", 0) == 0
    assert set(doc["fields_that_ever_differ"]) <= {
        "contract", "learned_rule_applied", "obligations", "supporting_operands"}


@needs_records
def test_target_is_entailed_by_the_antecedent():
    with open(FREEZE, encoding="utf-8") as f:
        freeze = json.load(f)
    os.environ.setdefault("OQA_LN_SOURCE_ROOT",
                          os.path.join(RUNS, "20261121-officeqa-two-baselines/s0"))
    doc = PA.target(freeze)
    g = doc["from_a_grep_hit"]
    assert g["n"] > 0 and g["QUERY_fired"] == g["n"] == g["SUPPORTED"]
    assert doc["P_supported_given_QUERY"] == 1.0
