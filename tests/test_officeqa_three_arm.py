"""Stage A software checks for the THREE-ARM OfficeQA comparison (constructed inputs only).

These establish what the code does on inputs whose intended answer is known in advance. They are NOT evidence
about the actor, the benchmark or the research hypothesis - design v0.1 section 7, Stage A.

Four are regression tests for defects found while building the three arms, and each says which:
  * `test_two_attempts_of_one_question_do_not_share_the_atom_cache`
  * `test_a_counterexample_in_accumulated_evidence_refutes_an_active_contract`
  * `test_a_retired_contract_is_shown_as_withdrawn`
  * `test_the_action_report_refuses_to_carry_stage_material`

Run: pytest tests/test_officeqa_three_arm.py   (pure Python; no network, no model, no Lean, no SkillOpt pipeline)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")
sys.path.insert(0, RESEARCH)

import oqa_lc_arm as ARM  # noqa: E402
import oqa_lc_ir as IR  # noqa: E402
import oqa_lc_learn as LEARN  # noqa: E402
import oqa_lc_replay as REPLAY  # noqa: E402
import oqa_lc_report as REPORT  # noqa: E402
import oqa_lc_vocab as VOCAB  # noqa: E402
import oqa_sel3 as SEL3  # noqa: E402

PY = sys.executable


# ── rule OQA-SEL-3: the three question sets ─────────────────────────────────────

@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    out = tmp_path_factory.mktemp("sel3") / "sets.json"
    rc = subprocess.run([PY, "-I", os.path.join(RESEARCH, "oqa_sel3.py"), "--out", str(out)],
                        cwd=REPO, capture_output=True, text=True).returncode
    assert rc == 0, "oqa_sel3.py reported a problem with the sets it built"
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def test_the_three_sets_have_the_sizes_the_comparison_asks_for(frozen):
    assert [len(frozen["sets"][k]["ids"]) for k in ("train", "val", "heldout")] == [24, 24, 60]


def test_val_is_the_whole_released_split_and_is_not_stratified(frozen):
    val = frozen["sets"]["val"]
    assert val["released_split_size"] == len(val["ids"]) == 24
    assert val["balanced"] is False


def test_the_three_sets_are_disjoint(frozen):
    ids = {k: set(frozen["sets"][k]["ids"]) for k in ("train", "val", "heldout")}
    assert not ids["train"] & ids["val"]
    assert not ids["train"] & ids["heldout"]
    assert not ids["val"] & ids["heldout"]
    assert frozen["disjoint"] is True


def test_every_previously_frozen_set_is_a_subset_so_saved_traces_stay_usable(frozen):
    # The development traces of the A40 run are traces of OQA-SEL-2's items. If those items were not carried
    # forward, every saved trace would describe a question no longer in the comparison.
    for name, rec in frozen["previous_sets_are_subsets"].items():
        assert rec["is_superset"], f"{name} dropped {rec['previous_items_not_carried']}"


def test_the_train_pool_still_excludes_the_item_with_no_retrievable_evidence(frozen):
    assert "UID0046" in SEL3.EXCLUDED
    assert "UID0046" not in frozen["sets"]["train"]["ids"]
    assert frozen["sets"]["train"]["excluded"] == ["UID0046"]


def test_the_draw_is_deterministic(tmp_path, frozen):
    out = tmp_path / "again.json"
    subprocess.run([PY, "-I", os.path.join(RESEARCH, "oqa_sel3.py"), "--out", str(out)],
                   cwd=REPO, capture_output=True, text=True, check=True)
    with open(out, encoding="utf-8") as f:
        again = json.load(f)
    for name in ("train", "val", "heldout"):
        assert again["sets"][name]["ids"] == frozen["sets"][name]["ids"]


def test_items_without_an_availability_verdict_are_counted_not_hidden(frozen):
    # The documents live on the cluster, so availability cannot be tested here. What must not happen is for an
    # untested item to be reported as available.
    for name in ("train", "val", "heldout"):
        s = frozen["sets"][name]
        assert s["n_untested"] + s["n_available"] <= len(s["ids"])
        assert s["n_untested"] == sum(1 for v in s["verdicts"].values() if v == "UNTESTED")


# ── the two contract arms' reports ──────────────────────────────────────────────

def _model_with_one_contract(reason: str = "it held of three observations with no counterexample"):
    m = IR.empty_model()
    m["assumptions"] = ["flat_header_authoritative"]
    m["contracts"] = [{"id": "read.effect.1", "version": 1, "status": "active", "verb": "read", "kind": "effect",
                       "condition": [], "effects": [{"atom": "width_uniform", "args": [], "value": True}],
                       "context": {}, "reason": reason, "epoch": 1, "source": "test",
                       "evidence": {"n_support_ok": 3, "n_counter": 0, "undetermined": 0,
                                    "items_supporting": ["UID0001", "UID0002"]}}]
    m["version"] = 1
    m["sha256"] = IR.model_sha(m)
    return m


def _replay_of_one_step(model, atom_observed: bool):
    """A replay record of a single `read` step whose observation settles `width_uniform`."""
    return {"item": "UID0001", "attempts": [{
        "aid": "UID0001@1", "item": "UID0001", "t": 0, "step": 1, "tool": "read", "args": {},
        "outcome": "ok", "class": "no_claim_ok", "witnesses": [],
        "forecast_checks": [{"contract": "read.effect.1", "version": 1, "atom": "width_uniform",
                             "predicted": True, "observed": atom_observed,
                             "class": REPLAY.FORECAST_CONFIRMED if atom_observed
                             else REPLAY.FORECAST_VIOLATED}],
    }]}


def test_the_action_report_names_the_contract_version_that_made_the_claim():
    m = _model_with_one_contract()
    text = REPORT.render_action(_replay_of_one_step(m, True), m)
    assert "RULE(read.effect.1@v1)" in text


def test_the_action_report_carries_no_stage_material():
    m = _model_with_one_contract()
    for observed in (True, False):
        text = REPORT.render_action(_replay_of_one_step(m, observed), m)
        assert REPORT.audit_action_only(text)["verdict"] == "PASS"


def test_the_action_report_refuses_to_carry_stage_material():
    # REGRESSION. The audit must look at the RENDERED text, so that stage material arriving through a field the
    # learner filled in - here a contract's own reason - is caught rather than trusted.
    m = _model_with_one_contract(reason="this rule discharges the unit_match obligation for the answer")
    text = REPORT.render_action(_replay_of_one_step(m, True), m)
    audit = REPORT.audit_action_only(text)
    assert audit["verdict"] == "FAIL"
    assert "unit_match" in audit["stage_words_found"]


def test_a_generic_word_from_a_document_does_not_abort_the_arm():
    # A column heading or row label quoted out of a bulletin may contain "stage" or "conversion". That is an
    # advisory, not a failure: one word in a scanned table must not end an arm of the comparison.
    m = _model_with_one_contract(reason="it held at the stage of every read in the sample")
    audit = REPORT.audit_action_only(REPORT.render_action(_replay_of_one_step(m, True), m))
    assert audit["verdict"] == "PASS"
    assert "stage" in audit["advisory"]


def test_a_retired_contract_is_shown_as_withdrawn():
    # REGRESSION. `mech_repair` RETIRES a contract it cannot revise; the first version of the renderer listed only
    # the refuted ones, so the commonest outcome of the repair schedule was invisible to the optimizer.
    m = _model_with_one_contract()
    m["contracts"][0].update(status="retired", retire_reason="counterexample(s) UID0009/e2@42")
    text = REPORT.render_action({"item": "UID0001", "attempts": []}, m)
    assert "Retracted" in text
    assert "retired: counterexample(s) UID0009/e2@42" in text


def test_the_in_sample_warning_appears_only_for_an_item_the_model_learned_from():
    m = _model_with_one_contract()
    m["evidence_items"] = ["UID0001"]
    m["evidence_scope"] = "a test collection"
    assert "in-sample" in REPORT.render_action(_replay_of_one_step(m, True), m)
    m["evidence_items"] = ["UID0999"]
    assert "in-sample" not in REPORT.render_action(_replay_of_one_step(m, True), m)


def test_the_headings_differ_and_arm_b_s_does_not_say_stage():
    import oqa_reports as RP
    assert RP.LC_HEADINGS[RP.LC_ACTION] == "Action Contract Report"
    assert "stage" not in RP.LC_HEADINGS[RP.LC_ACTION].lower()
    assert "Stage" in RP.LC_HEADINGS[RP.LC_STAGE]
    assert RP.LC_ACTION in RP.LC_CONDITIONS and RP.LC_STAGE in RP.LC_CONDITIONS


def test_an_unknown_report_condition_is_refused(tmp_path):
    import oqa_reports as RP
    with pytest.raises(ValueError, match="unknown learned-contract condition"):
        RP.build_one_lc(str(tmp_path), "UID0001", "q?", str(tmp_path / "results.jsonl"),
                        str(tmp_path / "model.json"), condition="NOT_A_CONDITION")


def test_containment_fails_when_the_full_report_drops_an_action_line():
    a = "# head\n- a line\n- another line\n"
    assert REPORT.audit_containment(a, "# head\n- a line\n- another line\n## stages\n")["verdict"] == "PASS"
    bad = REPORT.audit_containment(a, "# head\n- a line\n## stages\n")
    assert bad["verdict"] == "FAIL" and bad["n_missing"] == 1


# ── accumulated evidence across epochs ─────────────────────────────────────────

def _attempt(item_id: str, trace_key: str, width_uniform_obs: str):
    """One recorded `read` step. The observation's table width decides `width_uniform`."""
    return {"id": item_id, "trace_key": trace_key, "question": "What was the total in 1944?",
            "answer_kind": "number", "oracle_pages": [], "final_answer": "",
            "steps": [{"i": 1, "kind": "tool", "tool": "read", "args": {}, "cmd": "read x",
                       "obs": width_uniform_obs}]}


UNIFORM = "| Year | Total |\n|---|---|\n| 1944 | 1,234 |\n| 1945 | 2,345 |\n"
RAGGED = "| Year | Total |\n|---|---|\n| 1944 | 1,234 | 9 |\n| 1945 | 2,345 |\n"


def test_two_attempts_of_one_question_do_not_share_the_atom_cache():
    # REGRESSION. `Evidence` keyed its atom cache on the ITEM id, so the second epoch's attempt of a training
    # question silently reused the first epoch's atom values. With evidence accumulating across four epochs that
    # corrupts every recheck; on the real traces it turned one genuine counterexample into five phantom ones.
    a1 = _attempt("UID0001", "UID0001/e1", UNIFORM)
    a2 = _attempt("UID0001", "UID0001/e2", RAGGED)
    ev = REPLAY.Evidence([a1, a2])
    v1 = ev.atoms(a1, a1["steps"][0], {"flat_header_authoritative"})
    v2 = ev.atoms(a2, a2["steps"][0], {"flat_header_authoritative"})
    assert REPLAY.trace_key(a1) != REPLAY.trace_key(a2)
    assert v1.get("width_uniform") is not v2.get("width_uniform") or v1 != v2


def test_a_counterexample_is_attributed_to_the_attempt_it_was_found_in():
    a1 = _attempt("UID0001", "UID0001/e1", UNIFORM)
    a2 = _attempt("UID0001", "UID0001/e2", RAGGED)
    ev = REPLAY.Evidence([a1, a2])
    m = _model_with_one_contract()
    sc = REPLAY.score_contract(m["contracts"][0], ev, m["assumptions"])
    aids = set(sc["counter"]) | set(sc["support_ok"])
    assert any("/e" in a for a in aids), f"no attempt-scoped id among {aids}"


def test_a_counterexample_in_accumulated_evidence_refutes_an_active_contract():
    # REGRESSION. The refutation of a surviving contract happens inside `apply_proposal`. When the learner
    # proposed nothing new - which is the normal case from epoch 3 on - `apply_proposal` was never called, so a
    # counterexample found by the epoch's replay left the contract active and the repair had nothing to target.
    # `_learn_over` now applies an empty proposal whenever the evidence contradicts an active contract.
    ev = REPLAY.Evidence([_attempt("UID0001", "UID0001/e1", RAGGED)])
    m = _model_with_one_contract()
    before = LEARN.recheck(m, ev)
    assert before["read.effect.1@v1"]["n_counter"] >= 1, "the fixture does not contradict the contract"
    assert before["read.effect.1@v1"]["status"] == "active"
    after_model, events = ARM._learn_over(m, ev, epoch=3, seed_assumptions=False)
    assert any(e["phase"] == "recheck" for e in events), f"no recheck ran; phases {[e['phase'] for e in events]}"
    assert "read.effect.1" not in [c["id"] for c in IR.active(after_model)]


def test_a_contract_kept_with_a_counterexample_is_reported_as_an_error_not_a_retention():
    # REGRESSION. `_retained` printed "no counterexample" beside a non-zero count.
    ev = REPLAY.Evidence([_attempt("UID0001", "UID0001/e1", RAGGED)])
    m = _model_with_one_contract()
    rows = ARM._retained(m, ev)
    row = next(r for r in rows if r["id"] == "read.effect.1@v1")
    assert row["decision"] == "retained_in_error"
    assert "RETAINED IN ERROR" in row["why"]


def test_load_attempts_tags_each_collection(tmp_path):
    # Without a tag the two collections collide; `load_attempts` is where the tag is attached.
    pred = tmp_path / "predictions" / "UID0001"
    pred.mkdir(parents=True)
    (pred / "conversation.json").write_text(json.dumps([{"role": "user", "content": "## Question\nq?"}]))
    (pred / "target_user_prompt.txt").write_text("## Question\nWhat was the total in 1944?\n")
    got = ARM.load_attempts(str(tmp_path / "predictions"), ["UID0001"], tag="e2")
    assert got and got[0]["trace_key"] == "UID0001/e2"
    assert REPLAY.trace_key(got[0]) == "UID0001/e2"


# ── the held-out plan ───────────────────────────────────────────────────────────

def test_the_heldout_plan_scores_each_distinct_skill_once(tmp_path):
    import oqa_heldout as HO
    init = tmp_path / "initial.md"
    init.write_text("S0 body\n")
    arms = {}
    for arm, best, last in (("A", "S0 body\n", "S0 body\n"),        # both coincide with S0
                            ("B", "B best\n", "B last\n")):          # two further distinct skills
        root = tmp_path / f"arm_{arm}"
        (root / "skills").mkdir(parents=True)
        (root / "best_skill.md").write_text(best)
        (root / "skills" / "skill_v0003.md").write_text(last)
        (root / "skills" / "skill_v0001.md").write_text("an earlier one\n")
        arms[arm] = str(root)
    got = HO.collect(arms, str(init))
    assert got["n_requested"] == 5           # S0 + 2 per arm
    assert got["n_distinct"] == 3            # S0 (x3 coincident), B best, B last
    assert not got["problems"]


def test_the_heldout_plan_reports_a_missing_skill_instead_of_guessing(tmp_path):
    import oqa_heldout as HO
    init = tmp_path / "initial.md"
    init.write_text("S0\n")
    root = tmp_path / "arm_A"
    root.mkdir()
    got = HO.collect({"A": str(root)}, str(init))
    assert len(got["problems"]) == 2          # no best_skill.md and no skill snapshot
    assert got["n_distinct"] == 1             # only S0 is scoreable


# ── the budget ──────────────────────────────────────────────────────────────────

def test_the_budget_reports_the_slow_update_prompt_against_what_was_actually_proven():
    """Rewritten 2026-11-17 after job 66271290. The assertions it used to make encoded the superseded
    "173,060-token envelope", which was never a limit - only the largest prompt this project had tried."""
    import oqa_budget as B
    four, chosen, upstream = B.budget(4, 7), B.budget(10, 7), B.budget(20, 7)
    proven = four["context_risk"]["largest_prompt_this_project_has_sent"]
    assert proven == 243986, "the proven acceptance is job 66271290's reported prompt_tokens"

    # 4 pairs stay inside the proven point even if every comparison pair shows a trajectory.
    assert four["context_risk"]["verdict"].startswith("INSIDE")
    assert four["context_risk"]["worst_case_needs_trimming"] is False

    # 10 pairs: the plan put the worst case at 345,690 tokens, over the guard's limit. The CAMPAIGN measured
    # itself (job 66273856, arm A epoch 2) on a draw where all ten pairs were in trajectory-showing
    # categories - the worst case - and the prompt came to about 185,332 tokens. So 10 pairs fits even at
    # worst, and the guard did not need to trim. The assertion records the measurement, not the estimate.
    assert chosen["context_risk"]["worst_case_needs_trimming"] is False
    assert chosen["context_risk"]["slow_update_prompt_tokens_worst_case"] < \
        chosen["context_risk"]["pre_send_guard_limit_tokens"]
    assert chosen["context_risk"]["pairs_that_fit_the_guard_in_the_worst_case"] >= 10

    # 20 pairs would still be beyond the guard's limit even at the campaign's smaller per-pair size.
    assert upstream["context_risk"]["worst_case_needs_trimming"] is True
    assert upstream["question_rollouts"]["total"] > chosen["question_rollouts"]["total"] \
        > four["question_rollouts"]["total"]

    # The guard's limit must leave room for the whole completion allowance inside the proven total.
    limit = chosen["context_risk"]["pre_send_guard_limit_tokens"]
    assert limit + 16384 <= 243986 + 7562

    # MEASURED on the campaign itself (job 66272737): 15.7 requests/min, so the account's 50 RPM limit is
    # NOT what binds. The planned conclusion is kept beside the measured one, because the gap is the point.
    assert "NOT the 50 RPM account" in chosen["actor"]["binding_constraint"]
    assert chosen["actor"]["binding_constraint_as_planned"] == "the 50 RPM ACCOUNT limit"
    assert chosen["actor"]["minutes_at_the_MEASURED_rate"] > chosen["actor"]["minutes_at_the_account_rate_limit"]
    # One later-epoch arm-job needs more than the 60 minutes the plan allowed for it.
    assert chosen["actor"]["job_minutes_for_one_later_epoch_arm"] > 55


def test_the_budget_and_the_running_code_agree_on_the_prompt_limit():
    """A constant that disagrees with the module that enforces it is worse than no constant."""
    import oqa_budget as B
    from skillopt.optimizer import prompt_budget as PB
    assert B.measured_constants()["prompt_guard_limit_tokens"]["value"] == 225000
    assert PB.PROVEN["prompt_tokens"] == \
        B.measured_constants()["deepseek_accepted_prompt_tokens"]["value"]


def test_every_budget_constant_says_where_it_came_from():
    import oqa_budget as B
    for name, rec in B.measured_constants().items():
        assert rec["source"].strip(), f"{name} has no source"
