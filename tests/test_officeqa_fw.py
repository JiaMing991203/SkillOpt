#!/usr/bin/env python3
"""Tests for the ported learned action-and-stage framework (rule OQA-FW-1).

Each test asserts a fact the implementation report rests on, so a later reader can check the claims
without re-deriving them, and a later change that breaks one of them fails here.

These run on a workstation: they touch no provider, no cluster and no GPU, and they read only the
snapshot in `research/officeqa/framework/upstream/` plus small fixtures built in-process.
"""
from __future__ import annotations

import ast
import hashlib
import itertools
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RESEARCH = os.path.join(REPO, "research", "officeqa")
FRAMEWORK = os.path.join(RESEARCH, "framework")
UPSTREAM = os.path.join(FRAMEWORK, "upstream")
for p in (RESEARCH, FRAMEWORK):
    if p not in sys.path:
        sys.path.insert(0, p)

FW_MODULES = ["fw_kleene.py", "fw_domain.py", "fw_ir.py", "fw_state.py", "fw_check.py",
              "fw_learn.py", "fw_repair.py"]
OQA_MODULES = ["oqa_fw_extract.py", "oqa_fw_domain.py", "oqa_fw_learn.py", "oqa_fw_stage.py",
               "oqa_fw_run.py", "oqa_fw_repair.py", "oqa_fw_lean.py", "oqa_fw_report.py",
               "oqa_fw_edit.py"]


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


# ── 1. the snapshot ──────────────────────────────────────────────────────────
def test_upstream_snapshot_matches_its_manifest():
    man = json.load(open(os.path.join(UPSTREAM, "MANIFEST.json")))
    assert man["totals"]["missing"] == 0
    assert man["totals"]["files"] == 32
    # every ALFWorld source carried over is UNTRACKED there, so its hash is its only identity
    assert man["totals"]["untracked_in_source"] == 32
    for f in man["files"]:
        local = os.path.join(FRAMEWORK, f["copied_to"].split("upstream/", 1)[-1]) \
            if f["copied_to"].startswith("upstream/") else os.path.join(REPO, f["copied_to"])
        local = os.path.join(UPSTREAM, os.path.relpath(f["copied_to"], "upstream")) \
            if f["copied_to"].startswith("upstream/") else local
        assert os.path.exists(local), f["copied_to"]
        assert _sha(local) == f["sha256"], f"{f['copied_to']} differs from its manifest digest"


def test_nothing_imports_the_sibling_worktree_at_run_time():
    """The port must not depend on a mutable path outside this worktree."""
    for name in FW_MODULES + OQA_MODULES:
        path = os.path.join(FRAMEWORK if name.startswith("fw_") else RESEARCH, name)
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        docs = {ast.get_docstring(n) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for s in strings:
            if s in docs:
                continue        # a docstring may NAME the sibling worktree; it may not be a path used
            assert "SkillOpt-learned-contract" not in s, f"{name} refers to the sibling worktree"


# ── 2. behavioural parity with the snapshot ──────────────────────────────────
def test_kleene_logic_agrees_with_the_alfworld_snapshot():
    sys.path[:0] = [os.path.join(UPSTREAM, d) for d in ("vx1", "v1_core", "v1_vocab")]
    import vx_state as UP
    import fw_kleene as FW
    vals = (True, False, None)
    for n in (1, 2, 3):
        for combo in itertools.product(vals, repeat=n):
            assert FW.k_and(combo) == UP.k_and(combo), combo
            assert FW.k_or(combo) == UP.k_or(combo), combo


def test_merge_agrees_with_the_alfworld_snapshot():
    sys.path[:0] = [os.path.join(UPSTREAM, d) for d in ("vx1", "v1_core", "v1_vocab")]
    import vx_state as UP
    import fw_kleene as FW
    facts = [FW.fact(True, "observed", [1]), FW.fact(None, "stale", [2]),
             FW.fact(False, "inferred", [3], ("a",)), FW.fact(True, "record", [4]),
             FW.fact(True, "predicted", [5])]
    for n in (1, 2, 3):
        for combo in itertools.combinations(facts, n):
            for v in (True, False, None):
                assert FW.merge(v, list(combo)) == UP.merge(v, list(combo))


def test_gate_agrees_with_the_alfworld_snapshot():
    sys.path[:0] = [os.path.join(UPSTREAM, d) for d in ("vx1", "v1_core", "v1_vocab")]
    import vx_check as UP
    import fw_check as FW
    for ns, nc, nk in itertools.product((0, 1, 2), (0, 1), (0, 2)):
        sc = {"counter": [f"c{i}" for i in range(nc)], "support": [f"s{i}" for i in range(ns)],
              "consistent": [f"k{i}" for i in range(nk)]}
        for kind in ("requires", "suffices", "effect", "completes", "persists"):
            assert FW.gate(dict(sc), kind) == UP.gate(dict(sc), kind)


# ── 3. the capabilities the CC/LN generation lacked ──────────────────────────
def test_repair_can_delete_a_condition_and_the_old_generation_cannot():
    import fw_ir as IR
    import oqa_cc_ir as OLD
    c = {"kind": "concludes", "scope": "any", "subject": {"?s": "column"},
         "conditions": [[{"atom": "col_label_present", "args": ["?s"], "value": True}],
                        [{"atom": "col_child_label_present", "args": ["?s"], "value": True}]],
         "consequence": {"atom": "col_label_agrees_flat", "args": ["?s"], "value": True},
         "context": {"task_types": []}}
    out = IR.drop_conjunct(c, 0)
    assert len(out["conditions"]) == 1
    assert out["conditions"][0][0]["atom"] == "col_child_label_present"
    with pytest.raises(ValueError):
        IR.drop_conjunct(out, 0)        # never empty a condition; retire instead
    # The old generation's `repair` can only APPEND. Asserted against the function's own code rather
    # than against the file's prose, which does discuss deletion.
    old_src = open(os.path.join(RESEARCH, "oqa_cc_ir.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(old_src))
              if isinstance(n, ast.FunctionDef) and n.name == "repair")
    body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
    assert "extra_condition" in body
    assert "Delete" not in body and "Del(" not in body, "the old repair must not delete anything"
    assert "'drop'" not in body and '"drop"' not in body
    assert not hasattr(OLD, "drop_conjunct"), "the old IR has no deletion operation"
    assert OLD.STATUSES == ("active", "superseded") if hasattr(OLD, "STATUSES") else True


def test_statuses_include_retired_and_refuted():
    import fw_ir as IR
    assert set(IR.STATUSES) == {"active", "superseded", "retired", "refuted"}


def test_assumption_operations_exist_and_are_gated():
    import fw_learn as LN
    src = open(os.path.join(FRAMEWORK, "fw_learn.py"), encoding="utf-8").read()
    assert "assumption_ops" in src and "direct_contradictions" in src
    assert "would create counterexamples for active contracts" in src   # the regression check
    assert callable(LN.apply_proposal)


# ── 4. the unnamed-entity token ──────────────────────────────────────────────
def test_an_existential_over_an_empty_pool_is_unknown_not_false():
    """The bug this test exists for: with no instance named yet, `evaluate` had nothing to OR and
    returned False, so a rule's condition was FALSE before its entities appeared."""
    import fw_ir as IR
    import oqa_fw_domain as DOM
    dom = DOM.OfficeQADomain(entities={"tables": {}, "columns": {}, "cells": {}, "operands": {},
                                       "introduced": {}})
    est = dom.estimator(None, [], ())
    pool = est.entities_of_type(0, "table")
    assert pool and all(p.startswith("<unnamed:") for p in pool)
    c = {"kind": "concludes", "scope": "any", "subject": {"?s": "column"},
         "exists": {"?t": "table"},
         "conditions": [[{"atom": "header_split", "args": ["?t"], "value": True}]],
         "consequence": {"atom": "col_label_agrees_flat", "args": ["?s"], "value": True},
         "context": {"task_types": []}}
    r = IR.evaluate(c, c["conditions"], est, 0, {"?s": "co:s0:0:0"}, {}, dom)
    assert r["v"] is None, "an unnamed instance must leave the existential UNKNOWN"


def test_subject_pool_excludes_the_unnamed_token():
    import fw_ir as IR
    import oqa_fw_domain as DOM
    dom = DOM.OfficeQADomain()
    est = dom.estimator(None, [], ())
    c = {"kind": "concludes", "subject": {"?s": "column"}, "subject_scope": "all"}
    subs = list(IR.bindings(c, est, 0, {}, {}, "subject"))
    assert subs == [] or all(not str(b.get("?s", "")).startswith("<unnamed:") for b in subs)


# ── 5. the extraction corrections ────────────────────────────────────────────
def test_numeric_comparison_at_the_written_precision():
    import oqa_fw_extract as E
    assert E.near_legacy(1.8, 2.0) is True          # the defect
    assert E.near(1.8, 2.0, "1.8", "2.0") is False  # the correction
    assert E.near(2410259.0, 2410259.0, "2,410,259", "2,410,259") is True
    assert E.near(4.708, 4.653, "4.708", "4.653") is False
    assert E.written_decimals("4.708") == 3 and E.written_decimals("2,410,259") == 0


def test_placeholder_column_labels_are_not_column_names():
    import oqa_fw_extract as E
    for bad in ("nan", "NaN", "", "   ", "(3)", "Unnamed: 9_level_0"):
        assert E.label_present(bad) is False, bad
    for good in ("Total", "Income and profit taxes", "Gross"):
        assert E.label_present(good) is True, good


def test_a_cell_from_an_unnumbered_read_still_has_a_reference():
    """The LN generation recorded 'a read without line numbers yields no position'. A reference here is
    (step, table, row, column) and needs no line number."""
    import oqa_fw_extract as E
    obs = ("(in thousands of dollars)\n"
           "| Classification | Receipts | Outlays |\n"
           "|  | Gross | Total |\n"
           "| 1961 | 1,000 | 800 |\n")
    steps = [{"i": 4, "kind": "tool", "cmd": "read(path='x.txt', offset=0)", "obs": obs, "text": ""}]
    ents = E.document_entities(steps)
    assert "\n1 |" not in obs and ":" not in obs.splitlines()[1]      # no line numbers at all
    cells = list(ents["cells"].values())
    assert cells, "an unnumbered read must still yield data cells"
    for c in cells:
        assert c["ref"].startswith("ce:s4:")
        assert c["step"] == 4 and c["row_index"] is not None and c["col_index"] >= 1
    assert all(E.kind_of(c["ref"]) == "cell" for c in cells)


def test_pairing_needs_two_shared_rows():
    import oqa_fw_extract as E
    assert E.MIN_SHARED_ROWS == 2


# ── 6. the licence is load-bearing and never accuses ─────────────────────────
def _fixture_record():
    import oqa_fw_extract as E
    split = ("(in thousands of dollars)\n"
             "| Classification | Receipts | Receipts | Outlays |\n"
             "|  | Gross | Refunds | Total |\n"
             "| 1961 | 1,000 | 200 | 800 |\n"
             "| 1962 | 2,000 | 400 | 1,600 |\n")
    flat = ("| Period > Period | Receipts > Gross | Receipts > Refunds | Outlays > Total |\n"
            "| 1961 | 1,000 | 200 | 800 |\n"
            "| 1962 | 2,000 | 400 | 1,600 |\n")
    steps = [{"i": 0, "kind": "tool", "cmd": "read(path='a.txt', offset=0)", "obs": split, "text": ""},
             {"i": 1, "kind": "tool", "cmd": "read(path='b.txt', offset=0)", "obs": flat, "text": ""},
             {"i": 2, "kind": "message", "cmd": "", "obs": "",
              "text": "The Gross column gives 1,000 and 2,000. So 1,000 + 2,000 = 3,000."}]
    ents = E.document_entities(steps)
    ents = E.operand_entities(steps, ents)
    return steps, ents


def test_without_a_learned_rule_the_licence_is_unknown_not_false():
    import oqa_fw_domain as DOM
    steps, ents = _fixture_record()
    dom = DOM.OfficeQADomain(entities=ents, asked_scale="millions", model=None)
    est = dom.estimator(None, steps, ("index_alignment", "table_scale_applies_to_rows"))
    n = len(steps)
    assert dom.licence_rules() == []
    for cref in est.entities_of_type(n, "column"):
        if cref.startswith("<unnamed:"):
            continue
        assert est.value(n, "col_bound", [cref])["v"] is None


def test_a_withdrawn_licence_never_produces_an_accusation():
    import oqa_fw_domain as DOM
    import oqa_fw_stage as ST
    steps, ents = _fixture_record()
    dom = DOM.OfficeQADomain(entities=ents, asked_scale="millions", model=None)
    est = dom.estimator(None, steps, ("index_alignment", "table_scale_applies_to_rows"))
    rep = ST.attempt_report(est, len(steps), dom)
    assert rep["accusations"] == 0
    for row in rep["operands"]:
        for name in ST.OBLIGATIONS:
            assert row["obligations"][name]["state"] != ST.OUTSTANDING


def test_a_number_not_in_a_calculation_carries_no_conversion_obligation():
    """The audit this test records: the obligation was raised for every number the actor typed, and 70
    of one attempt's 227 were accused that way. None of the 70 survived."""
    import oqa_fw_extract as E
    import oqa_fw_domain as DOM
    import oqa_fw_stage as ST
    steps, ents = _fixture_record()
    narrated = [o for o in ents["operands"].values() if not o["in_calculation"]]
    assert narrated, "the fixture must contain a number that is only read aloud"
    dom = DOM.OfficeQADomain(entities=ents, asked_scale="millions", model=None)
    est = dom.estimator(None, steps, ())
    for o in narrated:
        ob = ST.operand_obligations(est, len(steps), o["ref"], dom)
        st = ob["obligations"]["conversion_accounted"]["state"]
        assert st in (ST.NOT_REQUIRED, ST.UNKNOWN), st


def test_the_operands_written_text_is_captured():
    """`oqa_cc_extract` calls it `raw`; reading it as `text` returned None and silently disabled the
    precision-aware comparison."""
    _, ents = _fixture_record()
    ops = list(ents["operands"].values())
    assert ops and all(o["text"] for o in ops), "every operand must carry the number as written"


# ── 7. Lean generation ───────────────────────────────────────────────────────
def test_the_lean_file_states_the_soundness_conditions():
    path = os.path.join(RESEARCH, "lean", "OqaFramework.lean")
    if not os.path.exists(path):
        pytest.skip("the Lean file has not been generated into the repository yet")
    src = open(path, encoding="utf-8").read()
    for name in ("licenceWithdrawnNeverAccuses", "unknownNeverOutstanding",
                 "undeterminedWhenConditionUnknown", "undeterminedWhenConsequenceUnknown",
                 "impliesNeverCounterOnFalseCondition", "iffIsStrictlyStronger",
                 "unknownConjunctIsNeverTrue"):
        assert f"theorem {name}" in src, name
    # the model's APPLICATION is checked, not an exported verdict: the rows carry literal values
    assert "def rows : List Row" in src and "def rowClass" in src and "evalCNF" in src
    assert "countCls" in src and "countState" in src
    man = os.path.join(RESEARCH, "lean", "MANIFEST.sha256")
    assert os.path.exists(man)
    digests = dict(reversed(l.split()) for l in open(man).read().split("\n") if l.strip())
    assert digests["OqaFramework.lean"] == _sha(path)


# ── 8. report hygiene and the information boundary ───────────────────────────
def test_no_framework_module_names_a_forbidden_field():
    banned = ("ground_truth", "reasoning_content", '"soft"', "'soft'", '"hard"', "'hard'")
    for name in FW_MODULES + OQA_MODULES:
        path = os.path.join(FRAMEWORK if name.startswith("fw_") else RESEARCH, name)
        tree = ast.parse(open(path, encoding="utf-8").read())
        docs = {ast.get_docstring(n) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docs:
                for b in banned:
                    assert b.strip("\"'") != n.value, f"{name} names {b}"


def test_the_report_carries_each_states_meaning_and_no_score():
    import oqa_fw_domain as DOM
    import oqa_fw_report as RPT
    import oqa_fw_stage as ST
    steps, ents = _fixture_record()
    dom = DOM.OfficeQADomain(entities=ents, asked_scale="millions", model=None)
    est = dom.estimator(None, steps, ())
    rep = ST.attempt_report(est, len(steps), dom)
    doc = {"schema": RPT.SCHEMA, "condition": RPT.CONDITION, "item": "FIXTURE",
           "trace_sha256": "0" * 64, "model_sha256": None, "model_version": 0,
           "vocab_digest": dom.digest(), "assumptions": [], "contracts": [],
           "asked_scale": "millions", "counts": rep["counts"], "n_operands": rep["n_operands"],
           "accusations": rep["accusations"], "learned_ids": [], "assumptions_used": [],
           "operands": rep["operands"], "result_sha256": "1" * 64}
    text = RPT.render(doc)
    for name, (_stage, means, notmeans) in ST.OBLIGATIONS.items():
        assert name in text and means[:40] in text and notmeans[:40] in text
    for banned in ("ground_truth", "answer key is", "score of", "correct answer"):
        assert banned not in text
    assert "UNKNOWN" in text and "does NOT establish" in text


def test_adding_the_FW_condition_left_the_three_LC_conditions_alone():
    import oqa_reports as RP
    assert RP.FW_CONDITION == "FW"
    for c in (RP.LC_CONDITION, RP.LC_ACTION, RP.LC_STAGE):
        assert c in RP.LC_CONDITIONS
    assert RP.LC_HEADINGS[RP.LC_ACTION] == "Action Contract Report"
    assert RP.LC_HEADINGS[RP.LC_STAGE] == "Action Contract and Stage Report"
    assert RP.LC_HEADINGS[RP.LC_CONDITION] == "Stage and Action Report"
    assert "stage" not in RP.LC_HEADINGS[RP.LC_ACTION].lower()
    src = open(os.path.join(RESEARCH, "oqa_reports.py"), encoding="utf-8").read()
    i = src.index("if condition == FW_CONDITION:")
    j = src.index("if condition not in LC_CONDITIONS:")
    assert i < j, "the FW branch must return before the LC path, leaving it untouched"


# ── 9. the per-epoch campaign driver ─────────────────────────────────────────
def test_the_arm_driver_applies_an_empty_proposal_when_a_contract_is_contradicted():
    """`oqa_lc_arm` records this the hard way: `fw_learn._finish` is what refutes a contract the new
    evidence contradicts, and it runs only when `apply_proposal` is called. Across epochs the proposer
    will not always propose, so the recheck must be explicit or a refuted contract stays active."""
    src = open(os.path.join(RESEARCH, "oqa_fw_arm.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "learn_over")
    body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
    # an EMPTY proposal, applied explicitly, with "recheck" as its source
    assert "'recheck'" in body or '"recheck"' in body
    assert "assumption_ops" in body and "contract_ops" in body
    assert body.count("apply_proposal") >= 2, "the seed and the recheck must each be able to apply"
    assert "attempt" in body, "a refuted contract must be offered to the repair machinery"
    # the proposing step applies too, and since 2026-11-23 it lives in a per-proposer helper rather
    # than inline: assert the whole module can still apply from all three places.
    assert ast.dump(tree).count("apply_proposal") >= 4, (
        "seed, teacher, search and recheck must each be able to apply")
    for name in ("_teacher_pass", "_search_pass"):
        assert any(isinstance(n, ast.FunctionDef) and n.name == name for n in ast.walk(tree)), name


def test_the_arm_driver_freezes_one_model_per_epoch_with_its_parent():
    import oqa_fw_arm as ARM
    src = open(os.path.join(RESEARCH, "oqa_fw_arm.py"), encoding="utf-8").read()
    assert "FW_MODEL_e{a.epoch}.json" in src or 'f"FW_MODEL_e{a.epoch}.json"' in src
    assert callable(ARM.freeze_to) and callable(ARM.learn_over)
    assert ARM.SEED_ASSUMPTIONS == ("index_alignment", "table_scale_applies_to_rows")
    # a seed run must declare that epoch 1's reports are in-sample
    assert "in_sample_warning" in src and "IN-SAMPLE" in src


# ── 10. the DeepSeek learner (oqa_fw_teacher) and amendment F-1 ──────────────
def test_the_learner_prompt_is_generated_from_the_domain_not_hard_coded():
    """A hand-written atom list in the prompt is a drift waiting to happen: the learner would be told
    about a vocabulary the code no longer has. Every atom and every assumption must come from the
    Domain object, and the category marks must be the ones the code uses."""
    import oqa_fw_domain as DOM
    import oqa_fw_teacher as TEACH
    dom = DOM.OfficeQADomain()
    block = TEACH.vocab_block(dom)
    for atom, (_kinds, cat, _why) in dom.atoms.items():
        assert atom in block, atom
        assert f"[{cat}]" in block
    for name in dom.assumptions:
        assert name in block, name
    for kind in dom.kinds:
        assert kind in block, kind
    # the prompt must warn about the two ways a gate-accepted rule is still worthless
    sysp = TEACH.system_prompt(dom)
    assert "CIRCULARITY" in sysp and "REDUNDANCY" in sysp
    assert "LEARNED-DEPENDENT" in sysp
    d = TEACH.prompt_digest(dom)
    assert d["system_sha"] and d["vocab_sha"] and d["domain_version"] == dom.version


def test_the_leak_check_refuses_rather_than_filters():
    """The learner may see the question, the tool calls, the observations and the code-computed values.
    It may not see a gold answer, a score, the skill or held-out material. The check RAISES - it does
    not quietly strip, because a silently filtered prompt is a prompt nobody can reconstruct."""
    import oqa_fw_teacher as TEACH
    TEACH.leak_check([{"role": "user", "content": "co:s1:0:0 col_label_present=T"}])
    for bad in ("ground_truth: 123", "the answer is 4,500", "## Skill Knowledge", "valid_unseen",
                "eval_summary", "scored: 0.41"):
        try:
            TEACH.leak_check([{"role": "user", "content": f"records ... {bad} ... more"}])
        except TEACH.LeakError:
            continue
        raise AssertionError(f"leak_check accepted {bad!r}")


def test_circularity_is_refused_before_the_gate_ever_sees_it():
    """A rule that conditions on its own consequence, or on a LEARNED-DEPENDENT atom, would be scored
    against values its own acceptance produced. That is a schema fault, not an evidence fault."""
    import oqa_fw_domain as DOM
    import oqa_fw_teacher as TEACH
    dom = DOM.OfficeQADomain()
    own = {"kind": "concludes", "scope": "any", "subject": {"?s": "column"}, "subject_scope": "once",
           "conditions": [[{"atom": "col_child_label_present", "args": ["?s"], "value": True}]],
           "consequence": {"atom": "col_child_label_present", "args": ["?s"], "value": True},
           "context": {"task_types": []}}
    assert TEACH.circularity(own, dom), "a rule may not condition on its own consequence"
    dep = dict(own, conditions=[[{"atom": "col_bound", "args": ["?s"], "value": True}]],
               consequence={"atom": "col_label_present", "args": ["?s"], "value": True})
    why = TEACH.circularity(dep, dom)
    assert why and "LEARNED-DEPENDENT" in " ".join(why)
    clean = dict(own, conditions=[[{"atom": "col_label_present", "args": ["?s"], "value": True}]])
    assert TEACH.circularity(clean, dom) == []


def test_compile_ops_reports_a_malformed_proposal_instead_of_dropping_it():
    """"The learner proposed nothing" and "the learner proposed something unusable" are different
    findings. A dropped operation would record the first when the second happened."""
    import oqa_fw_domain as DOM
    import oqa_fw_teacher as TEACH
    dom = DOM.OfficeQADomain()
    parsed = {"contract_ops": [
        {"op": "add", "contract": {"kind": "concludes", "scope": "any",
                                   "subject": {"?s": "column"},
                                   "conditions": [[{"atom": "header_split", "args": ["?s"],
                                                    "value": True}]],
                                   "consequence": {"atom": "col_label_present", "args": ["?s"],
                                                   "value": True},
                                   "context": {"task_types": []}}},
        {"op": "add", "contract": {"kind": "concludes"}},
        {"op": "retire"},
        {"op": "nonsense"}],
        "assumption_ops": [{"op": "adopt", "name": "not_an_assumption", "reason": "x"}]}
    out = TEACH.compile_ops(parsed, dom)
    assert out["contract_ops"] == [], "every one of these is unusable"
    assert len(out["refused"]) == 5
    # the TYPED-argument error must be the reason the first one failed, named explicitly
    first = " ".join(out["refused"][0]["why"])
    assert "header_split" in first and "column" in first
    assert out["assumption_ops"] == []


def test_parse_reply_takes_the_last_fenced_block_and_falls_back_to_bare_json():
    import oqa_fw_teacher as TEACH
    d = TEACH.parse_reply('blah ```json {"contract_ops": [], "notes": "a"} ``` tail')
    assert d["notes"] == "a" and d["contract_ops"] == []
    d = TEACH.parse_reply('thinking... {"assumption_ops": [{"op": "adopt", "name": "x"}]} done')
    assert d["assumption_ops"][0]["name"] == "x"
    for bad in ("", "no json here"):
        try:
            TEACH.parse_reply(bad)
        except ValueError:
            continue
        raise AssertionError(f"parse_reply accepted {bad!r}")


def test_amendment_F1_widens_what_licenses_col_bound_without_making_it_true():
    """F-1: the licence set was one atom that section 11b measured to be unevaluable in a campaign
    epoch, so no rule concluding it could ever pass the support gate and the learner could not reach a
    single obligation. The set is now the two atoms that can DETERMINE a column's own label. It must
    still contain no LEARNED-DEPENDENT atom, and an empty model must still license nothing."""
    import fw_ir as IR
    import oqa_fw_domain as DOM
    dom = DOM.OfficeQADomain()
    assert dom.LICENCE_CONSEQUENCES == ("col_label_agrees_flat", "col_child_label_present")
    for atom in dom.LICENCE_CONSEQUENCES:
        assert atom in dom.atoms
        assert dom.atoms[atom][1] != "LEARNED-DEPENDENT", f"{atom} would license itself"
        assert dom.atoms[atom][0] == ("column",)
    assert DOM.OfficeQADomain(model=None).licence_rules() == []
    assert DOM.OfficeQADomain(model=IR.empty_model(dom)).licence_rules() == []
    # a rule concluding EITHER atom licenses; one concluding something else does not
    def rule(atom):
        return {"id": "C1", "version": 1, "status": "active", "kind": "concludes", "scope": "any",
                "subject": {"?s": "column"}, "subject_scope": "once",
                "conditions": [[{"atom": "col_label_present", "args": ["?s"], "value": True}]],
                "consequence": {"atom": atom, "args": ["?s"], "value": True},
                "context": {"task_types": []}}
    for atom in dom.LICENCE_CONSEQUENCES:
        m = dict(IR.empty_model(dom), contracts=[rule(atom)])
        assert len(DOM.OfficeQADomain(model=m).licence_rules()) == 1, atom
    m = dict(IR.empty_model(dom), contracts=[rule("cell_scale_resolved")])
    assert DOM.OfficeQADomain(model=m).licence_rules() == [], (
        "only a label-determining conclusion may license reading a merged sub-label")


def test_the_campaign_learner_is_the_provider_and_the_search_is_a_labelled_diagnostic():
    """The instruction is explicit: the finite enumeration may remain a diagnostic but must not be
    silently substituted for the campaign learner. So the default proposer is the teacher, the
    diagnostic is recorded as NOT APPLIED, and the only function that opens a socket leak-checks first."""
    import inspect
    import oqa_fw_arm as ARM
    import oqa_fw_teacher as TEACH
    sig = inspect.signature(ARM.learn_over)
    assert sig.parameters["proposer"].default == "teacher"
    src = inspect.getsource(ARM._search_diagnostic)
    assert '"applied": False' in src and "NOT applied" in src
    assert "not the campaign learner" in src
    # the one paid entry point: leak_check runs before the import of the provider client
    call_src = inspect.getsource(TEACH.call)
    assert call_src.index("leak_check") < call_src.index("chat_optimizer_messages")
    # and no other function in the module reaches the provider: every mention is inside call()
    mod_src = inspect.getsource(TEACH)
    assert mod_src.count("chat_optimizer") == call_src.count("chat_optimizer") == 2


# ── 11. the Lean export, after the compiler refuted it (oqa.fw.lean.v2) ──────
def test_the_lean_conversion_rule_matches_the_checkers_corrected_one():
    """2026-11-23: the first compile of OqaFramework.lean disproved 24 `decide` goals. The Lean
    definition took three inputs and encoded the conversion rule as it was BEFORE the 2026-11-22
    audit, and the exported tuple did not carry the two fields the corrected rule reads. This test
    compares the two implementations case by case so a compiler is never again the first to find out."""
    import itertools
    import oqa_fw_lean as LN
    import oqa_fw_stage as ST
    for has_cell, asked, in_calc, resolved, req, stated in itertools.product(
            (True, False), (True, False), (True, False), (True, False, None),
            (None, 0, 3, 6), (None, 0, 3, 6)):
        t = {"has_cell": has_cell, "licence": True, "resolved": resolved, "asked": asked,
             "in_calculation": in_calc, "required_exp": req, "stated_exp": stated}
        got = LN._d_states(t)
        assert set(got) == set(ST.OBLIGATIONS)
        if not has_cell:
            assert all(v == ST.UNKNOWN for v in got.values())
            continue
        c = got["conversion_accounted"]
        # the four properties the audit established, asserted directly
        if not asked or not in_calc:
            assert c == ST.NOT_REQUIRED
        elif resolved is not True:
            assert c == ST.UNKNOWN, "an unresolved scale is never a missing conversion"
        elif req is None:
            assert c == ST.UNKNOWN, "no factor defined between the scales leaves it unsettled"
        elif req == 0:
            assert c == ST.NOT_REQUIRED, "a factor of 10^0 demands no written multiplication"
        elif stated is None:
            assert c == ST.OUTSTANDING
        elif stated == req:
            assert c == ST.DISCHARGED
        else:
            assert c == ST.UNKNOWN, "a DIFFERING factor is unsettled, never an accusation"
        assert c != ST.OUTSTANDING or (asked and in_calc and resolved is True
                                       and req not in (None, 0) and stated is None)


def test_the_exported_tuple_determines_the_state_it_is_recorded_beside():
    """The hole that let the bad export through: `self_check` only asserted that the tuples'
    multiplicities summed to the report's totals, never that the tuple's own fields DETERMINE the
    state written beside it. A tuple that does not determine its state cannot be checked by anything."""
    import inspect
    import oqa_fw_lean as LN
    src = inspect.getsource(LN.self_check)
    assert "_d_states" in src, "self_check must re-derive each state from the exported fields"
    assert "state_mismatches" in src and "n_state_mismatches" in src
    # and `clean` must require that check to pass, not only the other two
    assert "not state_bad" in src
    # the export must carry every field the rule reads
    rows = inspect.getsource(LN.obligation_rows)
    for field in ("has_cell", "in_calculation", "asked", "required_exp", "stated_exp"):
        assert field in rows, field
    assert "0 if req is None else" not in rows, (
        "a missing required factor must stay None: mapping it to 0 conflates "
        "'no factor is defined between these scales' with 'the required factor is 10^0'")


def test_the_generated_lean_file_is_core_only_and_carries_the_corrected_rule():
    lean = os.path.join(RESEARCH, "lean", "OqaFramework.lean")
    text = open(lean, encoding="utf-8").read()
    assert "def conversionAccounted (asked inCalc : Bool) (resolved : KV) (req : Option Int)" in text
    assert "hasCell : Bool" in text and "if r.hasCell = false then" in text
    for name in ("outsideACalculationNeverAccuses", "aDifferingFactorIsNeverAnAccusation",
                 "unmatchedOperandNeverAccuses", "licenceWithdrawnNeverAccuses"):
        assert f"theorem {name}" in text, name
    # core Lean only: this file imports nothing, so a Mathlib-only tactic would not compile
    for tactic in ("rcases", "omega", "linarith", "aesop", "norm_num", "field_simp"):
        assert tactic not in text, f"{tactic} is not in the Lean core"
    assert "import " not in text, "the file must stay self-contained"
    # the manifest must cover the file as it now stands
    man = open(os.path.join(RESEARCH, "lean", "MANIFEST.sha256"), encoding="utf-8").read()
    import hashlib
    assert hashlib.sha256(text.encode()).hexdigest() in man
