"""Amendment B-1: the connected contract's representation, its guards, and the invariant Lean also proves.

These are the deterministic parts - what `validate` refuses, what a repair does, and the stage link's
behaviour on an unknown binding. The learner's own proposal is checked against these before it is scored,
so a proposal cannot enter the model by being plausible.
"""
from __future__ import annotations

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
    spec = importlib.util.spec_from_file_location(f"{name}_cc_test",
                                                  os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


IR = _mod("oqa_cc_ir")
CHK = _mod("oqa_cc_check")


def _good():
    return IR.contract(
        "scale.binds",
        IR.clause("scale_binds", ["table", "cell", "scale"]),
        [IR.clause("table_states_scale", ["table", "scale"]),
         IR.clause("cell_in_table", ["cell", "table"]),
         IR.clause("column_header_states_unit", ["header", "scale"], value=False)],
        witnesses="unit_match")


def test_a_well_formed_connected_contract_validates():
    c = _good()
    assert IR.validate(c) == []
    assert set(c["binds"]) >= {"table", "cell", "header"}
    assert "IF" in IR.render(c) and "WITNESSES stage obligation unit_match" in IR.render(c)


def test_a_definition_may_not_be_the_conclusion_of_a_learned_contract():
    """The contract audit's complaint in one rule: ordinary unit arithmetic is not a discovery."""
    c = IR.contract("bad", IR.clause("factor_exp", ["scale", "scale", "exp10"]),
                    [IR.clause("table_states_scale", ["table", "scale"])])
    problems = IR.validate(c)
    assert any("cannot be the CONCLUSION" in p for p in problems), problems


def test_a_clause_may_not_be_relabelled_and_a_definition_may_not_be_called_learned():
    c = _good()
    c["conditions"][0]["provenance"] = IR.LEARNED        # table_states_scale is OBS
    assert any("must be labelled OBS" in p for p in IR.validate(c))
    c2 = _good()
    c2["conditions"].append({"pred": "factor_exp", "args": ["scale", "scale", "exp10"],
                             "value": True, "provenance": IR.LEARNED, "evidence": []})
    assert any("cannot be LEARNED" in p for p in IR.validate(c2))


def test_an_unconditional_claim_is_refused_because_that_is_the_restricted_form():
    c = IR.contract("bad", IR.clause("scale_binds", ["table", "cell", "scale"]), [])
    assert any("must state an applicability condition" in p for p in IR.validate(c))


def test_an_unbound_entity_is_refused():
    c = _good()
    c["binds"] = ["table"]
    assert any("unbound entity" in p for p in IR.validate(c))


def test_a_repair_narrows_the_condition_and_keeps_the_old_version():
    """The restricted form's only repair was to RETIRE a rule, which is what happened to read.effect.2 -
    the campaign's single counterexample in six rounds deleted a hypothesis instead of improving one."""
    c = _good()
    extra = IR.clause("row_label_states_scale", ["cell", "scale"], value=False)
    old, new = IR.repair(c, extra, "a counterexample", [{"item": "UID0014"}])
    assert old["status"] == "superseded" and old["version"] == 1
    assert new["status"] == "active" and new["version"] == 2
    assert new["parent"] == "scale.binds@v1"
    assert len(new["conditions"]) == len(c["conditions"]) + 1
    assert new["repair_reason"] == "a counterexample"
    assert IR.validate(new) == []


@pytest.mark.parametrize("truth", [IR.UNKNOWN, IR.CONFLICT])
def test_an_unknown_or_conflicting_binding_never_produces_a_violation(truth):
    """The invariant that makes a repair sound, and the first theorem the Lean file proves: withdrawing a
    binding may withdraw a discharge, it may never manufacture an accusation against the actor."""
    link = CHK.stage_link({"truth": truth, "scale": "thousands",
                           "conflict": {"kind": "x"} if truth == IR.CONFLICT else None},
                          "billions", used_exp=-3)
    assert link["state"] == "UNKNOWN"
    assert link["outcome"] == CHK.UNKNOWN_OUT


def test_the_stage_link_computes_the_conversion_from_the_definition():
    """thousands -> billions is 10^-6. The campaign's arm C UID0014 report says the actor used 10^-3 and
    was 'wrong by 1,000x'; this is where that number comes from, and it is MATH, not a hypothesis."""
    holds = {"truth": IR.HOLDS, "scale": "thousands", "conflict": None}
    bad = CHK.stage_link(holds, "billions", used_exp=-3)
    assert bad["state"] == "VIOLATED" and bad["outcome"] == CHK.ACTOR_ERROR
    assert bad["required_exp"] == -6 and bad["used_exp"] == -3
    assert "wrong by 10^3" in bad["why"]
    good = CHK.stage_link(holds, "billions", used_exp=-6)
    assert good["state"] == "DISCHARGED"
    missing = CHK.stage_link(holds, "billions", used_exp=None)
    assert missing["state"] == "OUTSTANDING" and missing["outcome"] == CHK.UNKNOWN_OUT


def test_the_prefix_guard_raises_rather_than_trusting_the_author():
    """Prefix-only checking has to be a property of the code. Reading past the prefix while a prediction
    is open is an error."""
    rev = CHK.Revealer([{"i": 0}, {"i": 1}, {"i": 2}])
    assert len(rev.upto(2)) == 2
    rev.open_prediction = True
    with pytest.raises(RuntimeError):
        rev.beyond(2)
    rev.open_prediction = False
    assert len(rev.beyond(2)) == 1


def test_a_stated_factor_is_read_from_the_actors_own_words_not_inferred():
    assert CHK.stated_factor_exp("so I divide by 1,000,000 to get billions") == -6
    assert CHK.stated_factor_exp("multiply by 1000") == 3
    assert CHK.stated_factor_exp("divided by 1000") == -3
    assert CHK.stated_factor_exp("the total is 1,234 million") is None
    assert CHK.stated_factor_exp("") is None


def test_the_column_header_is_what_can_withdraw_a_table_level_binding():
    assert CHK.column_unit({"column": "Receipts > Total (in thousands)"}) == "thousands"
    assert CHK.column_unit({"column": "Income and profit taxes > Total"}) is None
    assert CHK.row_unit({"row_label": "1930 (millions)"}) == "millions"


def test_a_cell_the_condition_does_not_cover_is_silence_not_a_counterexample():
    """The whole reason for having a condition. The restricted form had none, which is why both
    polarities of `row_period_bound` were induced and refuted and the atom had to be struck out."""
    c = _good()
    table = {"scale": "thousands", "origin": {"kind": "observation", "step": 3}}
    covered = {"value": 1.0, "raw": "1", "row_label": "1930", "column": "Receipts > Total",
               "scale": "thousands", "table_index": 0, "source": {"kind": "observation", "step": 3}}
    assert CHK.evaluate_binding(c, table, covered)["truth"] == IR.HOLDS
    # a cell whose own column states another unit: NOT a counterexample to the conclusion, a conflict
    other = dict(covered, column="Receipts > Total (in millions)")
    got = CHK.evaluate_binding(c, table, other)
    assert got["truth"] == IR.CONFLICT
    assert got["conflict"]["column_unit"] == "millions"
    assert got["applicable"] is False


def test_the_model_freezes_under_its_digest_and_records_its_parent():
    import tempfile
    c = _good()
    m = IR.model([c], epoch=2, scope="test", learned_from=[{"trace_key": "UID1/e2"}])
    with tempfile.TemporaryDirectory() as root:
        p1 = os.path.join(root, "m1.json")
        d1 = IR.freeze(m, p1)
        back = IR.load_frozen(p1)
        assert back["sha256"] == d1 and back["frozen_at_epoch"] == 2
        m2 = IR.model([c], epoch=3, scope="test2", learned_from=[])
        p2 = os.path.join(root, "m2.json")
        d2 = IR.freeze(m2, p2, parent_sha256=d1)
        assert IR.load_frozen(p2)["parent_sha256"] == d1 and d2 != d1
        os.chmod(p2, 0o644)
        doc = json.load(open(p2, encoding="utf-8"))
        doc["contracts"] = []
        with open(p2, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        with pytest.raises(RuntimeError):
            IR.load_frozen(p2)
