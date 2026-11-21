"""The two-baseline OfficeQA campaign: the selection rule, the outbound assertion, the preflight.

WHY EACH TEST IS HERE. Every one of these pins a property the campaign's validity rests on, and several pin
a mistake this project has actually made:

  * the split written to disk must hold the ids the manifest froze - a CSV column read with the wrong
    separator silently produces the wrong set, which is the bug commit 455e267 fixed;
  * "transmitted" and "effective" are different claims, so the verifier must fail a log whose requests are
    right but whose responses show the provider ignored the switch;
  * the workload arithmetic must be computed, because a campaign that cannot fit its allowance must be
    refused before it starts rather than discovered half-way through.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")


def _mod(name):
    for p in (REPO, RESEARCH):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"{name}_base_test", os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SEL = _mod("oqa_sel4")
VER = _mod("oqa_base_verify")
PRE = _mod("oqa_base_preflight")

FROZEN_PATH = os.path.join(REPO, "docs", "research", "OFFICEQA_FROZEN_SETS_V4.json")


def _frozen():
    with open(FROZEN_PATH, encoding="utf-8") as f:
        return json.load(f)


# ───────────────────────── the selection rule ─────────────────────────

def test_the_frozen_record_is_the_design_the_instruction_asked_for():
    d = _frozen()
    assert d["rule"] == "OQA-SEL-4"
    assert [len(d["sets"][n]["ids"]) for n in ("train", "val", "heldout")] == [12, 12, 30]
    assert d["disjoint"] is True
    assert d["problems"] == []


def test_the_three_sets_are_pairwise_disjoint_recomputed_from_the_ids():
    d = _frozen()
    ids = {n: set(d["sets"][n]["ids"]) for n in ("train", "val", "heldout")}
    assert not ids["train"] & ids["val"]
    assert not ids["train"] & ids["heldout"]
    assert not ids["val"] & ids["heldout"]


def test_train_and_val_are_balanced_and_heldout_is_as_even_as_four_strata_allow():
    d = _frozen()
    assert d["sets"]["train"]["per_stratum"] == {s: 3 for s in SEL.STRATA}
    assert d["sets"]["val"]["per_stratum"] == {s: 3 for s in SEL.STRATA}
    assert d["sets"]["heldout"]["per_stratum"] == SEL.HELDOUT_QUOTA
    assert sum(SEL.HELDOUT_QUOTA.values()) == 30


def test_every_item_comes_from_its_own_released_split():
    d = _frozen()
    for name, want in (("train", "train"), ("val", "val"), ("heldout", "test")):
        assert d["sets"][name]["released_split"] == want
        pool = {str(i["id"]) for i in SEL._split_items(want)}
        assert set(d["sets"][name]["ids"]) <= pool, name


def test_each_set_is_a_subset_of_the_freeze_it_claims_to_subset():
    d = _frozen()
    for name, rec in d["subset_of_previous_freeze"].items():
        assert rec["is_subset"] is True, (name, rec["items_outside"])


def test_no_item_changed_the_role_an_earlier_freeze_gave_it():
    """A question that was training and is now held-out would be contaminated by construction."""
    d = _frozen()
    for name, per_item in d["prior_research_exposure"].items():
        for uid, roles in per_item.items():
            assert all(r["role"] == name for r in roles), (name, uid, roles)


def test_source_documents_split_on_newlines_not_only_commas():
    """The CSV separates multiple source files with a NEWLINE; splitting on commas alone reads one filename."""
    item = {"source_files": "a_1942_07.txt\nb_1942_08.txt", "source_docs": ""}
    assert SEL._docs_of(item) == ["a_1942_07.txt", "b_1942_08.txt"]
    assert SEL._multi(item) is True
    assert SEL._docs_of({"source_files": "x.txt, y.txt"}) == ["x.txt", "y.txt"]
    assert SEL._multi({"source_files": "only.txt"}) is False


def test_the_draw_order_depends_on_the_rule_name_and_the_id_and_nothing_else():
    a = SEL._order_key("UID0123")
    assert a == SEL._order_key("UID0123")
    assert a != SEL._order_key("UID0124")


def test_document_overlap_between_sets_is_counted_and_not_assumed_empty():
    """A shared document is a real leakage channel, so it must be reported rather than silently tolerated."""
    d = _frozen()
    pairs = d["source_document_overlap"]["pairs"]
    assert set(pairs) == {"train|val", "train|heldout", "val|heldout"}
    for key, rec in pairs.items():
        assert rec["n_shared_documents"] == len(rec["shared_documents"])
        if rec["n_shared_documents"]:
            # whenever a document is shared, the items resting on it are named on BOTH sides
            assert set(rec["items_touching_a_shared_document"]) == set(key.split("|"))


def test_the_excluded_item_is_carried_forward_rather_than_forgotten():
    d = _frozen()
    assert "UID0046" in d["excluded"]
    assert "UID0046" not in d["sets"]["train"]["ids"]


def test_build_set_refuses_to_silently_drop_a_retained_item():
    """`previous_items_dropped` is the guard: a retained id squeezed out of its quota must be reported."""
    pool = [{"id": f"UID{i:04d}", "category": "easy", "source_files": "one.txt"} for i in range(1, 40)]
    # quota for easy/single is 8, so retaining 9 ids of that stratum must surface one as dropped
    retain = [f"UID{i:04d}" for i in range(1, 10)]
    built = SEL.build_set("heldout", pool, retain=retain, allowed={p["id"] for p in pool})
    assert len(built["previous_items_dropped"]) == 1


# ───────────────────────── the outbound assertion ─────────────────────────

def _log(tmp_path, rows):
    p = tmp_path / "calls.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(p)


def _good_row(**over):
    row = {"request": {"model": "qwen3-5-4b", "max_tokens": 8000, "temperature": 0.7,
                       "extra_body": {"enable_thinking": False}},
           "response": {"choices": [{"message": {"content": "<answer>1</answer>"}}]},
           "outcome": "ok", "recovery": None}
    row.update(over)
    return row


def test_the_frozen_condition_passes_on_well_formed_requests(tmp_path):
    doc = VER.verify(_log(tmp_path, [_good_row(), _good_row()]))
    assert doc["verdict"] == "FROZEN_CONDITION_HELD"
    assert doc["n_fail"] == 0 and doc["n_calls"] == 2


def test_a_wrong_output_cap_fails_the_job(tmp_path):
    bad = _good_row()
    bad["request"] = dict(bad["request"], max_tokens=16384)
    doc = VER.verify(_log(tmp_path, [_good_row(), bad]))
    assert doc["verdict"] == "CONDITION_VIOLATED"
    assert "transmitted/max_tokens" in [c["check"] for c in doc["checks"] if c["verdict"] == "FAIL"]


def test_a_single_wrong_temperature_among_many_is_caught(tmp_path):
    rows = [_good_row() for _ in range(9)]
    rows.append(_good_row(request={"model": "qwen3-5-4b", "max_tokens": 8000, "temperature": 0.0,
                                   "extra_body": {"enable_thinking": False}}))
    doc = VER.verify(_log(tmp_path, rows))
    assert doc["n_fail"] >= 1
    assert "transmitted/temperature" in [c["check"] for c in doc["checks"] if c["verdict"] == "FAIL"]


def test_transmitted_is_not_effective_a_provider_that_ignored_the_switch_fails(tmp_path):
    """THE distinction this file exists for. The request is perfect; the response proves it was ignored."""
    ignored = _good_row()
    ignored["response"] = {"choices": [{"message": {"content": "", "reasoning": "x" * 400}}]}
    doc = VER.verify(_log(tmp_path, [_good_row(), ignored]))
    assert doc["verdict"] == "CONDITION_VIOLATED"
    failed = [c["check"] for c in doc["checks"] if c["verdict"] == "FAIL"]
    assert "effective/thinking_actually_off" in failed
    # and every TRANSMISSION check still passed, which is what makes the two claims distinct
    assert not [c for c in failed if c.startswith("transmitted/")]
    assert doc["alternate_reasoning"] == {"calls": 1, "chars": 400}


def test_the_alternate_field_is_searched_under_every_known_provider_name(tmp_path):
    for field in ("reasoning", "reasoning_content", "thinking", "thought"):
        row = _good_row()
        row["response"] = {"choices": [{"message": {"content": "", field: "zz"}}]}
        doc = VER.verify(_log(tmp_path, [row]))
        assert doc["alternate_reasoning"]["chars"] == 2, field


def test_an_empty_or_malformed_log_cannot_pass(tmp_path):
    assert VER.verify(_log(tmp_path, []))["verdict"] == "CONDITION_VIOLATED"
    p = tmp_path / "bad.jsonl"
    p.write_text('{"request": {}}\nnot json at all\n', encoding="utf-8")
    doc = VER.verify(str(p))
    assert doc["verdict"] == "CONDITION_VIOLATED"
    assert "call_log/no_malformed_lines" in [c["check"] for c in doc["checks"] if c["verdict"] == "FAIL"]


def test_a_recovery_is_reported_and_does_not_fail_the_job(tmp_path):
    """The narrow committed-answer policy is ALLOWED to fire; it is counted, not treated as a violation."""
    doc = VER.verify(_log(tmp_path, [_good_row(recovery="committed_answer"), _good_row()]))
    assert doc["verdict"] == "FROZEN_CONDITION_HELD"
    assert doc["recoveries"].get("committed_answer") == 1


# ───────────────────────── the preflight ─────────────────────────

def test_the_split_directory_is_compared_id_by_id_not_by_count(tmp_path):
    """Right count, wrong ids: the check that a count-only comparison would pass."""
    frozen = {"sets": {"train": {"ids": ["UID0001", "UID0002"]},
                       "val": {"ids": []}, "heldout": {"ids": []}}}
    for role, sub in PRE.SPLIT_ROLE_DIR.items():
        os.makedirs(tmp_path / sub, exist_ok=True)
        (tmp_path / sub / f"officeqa_{sub}.csv").write_text("uid,question\n", encoding="utf-8")
    (tmp_path / "train" / "officeqa_train.csv").write_text(
        "uid,question\nUID0001,a\nUID9999,b\n", encoding="utf-8")
    results: list = []
    saved = dict(PRE.DESIGN)
    PRE.DESIGN.update({"train": 2, "val": 0, "heldout": 0})
    try:
        PRE.check_splits(results, frozen, str(tmp_path))
    finally:
        PRE.DESIGN.clear(); PRE.DESIGN.update(saved)
    by = {r["check"]: r for r in results}
    assert by["split/train/count"]["verdict"] == "PASS"          # the count is right
    assert by["split/train/ids_match_frozen"]["verdict"] == "FAIL"  # the ids are not
    assert by["split/train/ids_match_frozen"]["detail"]["only_in_split_dir"] == ["UID9999"]


def test_the_split_directory_layout_is_train_val_test():
    """valid_seen / valid_unseen are the trainer's ALIASES; the directories on disk are train/val/test."""
    assert PRE.SPLIT_ROLE_DIR == {"train": "train", "val": "val", "heldout": "test"}


def test_the_workload_arithmetic_is_computed_and_compared_with_the_ceilings():
    results: list = []
    caps = {"actor_attempts": 20000, "actor_usd": 15.0, "opt_calls": 400, "opt_usd": 20.0}
    w = PRE.check_workload(results, caps)
    e = w["episodes"]
    # the design, spelled out: 4 passes of 12, one validation baseline, the gate pass of epoch 1,
    # three later epochs of (12 training + 2x6 slow-update sides + 2x12 gate), and 3 endpoints x 2 x 30
    assert e["s0_train_4_passes"] == 48
    assert e["s0_val_baseline"] == 12
    assert e["original_skillopt_epoch1_gate"] == 12
    assert e["original_skillopt_epochs_2_to_4"] == 3 * (12 + 12 + 24)
    assert e["trace2skill_rollouts"] == 0, "Trace2Skill reuses the s0 trajectories; it rolls out nothing"
    assert e["heldout_3_endpoints_2_passes"] == 180
    assert w["total_episodes"] == sum(e.values())
    assert all(r["verdict"] == "PASS" for r in results), [r for r in results if r["verdict"] != "PASS"]
    # The optimizer-call projection must count Trace2Skill's MAP phase, which the first version omitted:
    # one analysis call AND one map call per trajectory, 6 batch merges, 1 final merge, apply and verify.
    oc = w["optimizer_calls"]
    assert oc["trace2skill_analysis"] == 48
    assert oc["trace2skill_map"] == 48
    assert oc["trace2skill_reduce"] == 7
    assert oc["trace2skill_apply_and_verify"] == 2
    assert oc["original_skillopt"] == 12
    assert w["projected_optimizer_calls"] == 117


def test_a_workload_that_cannot_fit_is_refused():
    results: list = []
    PRE.check_workload(results, {"actor_attempts": 10, "actor_usd": 0.01, "opt_calls": 1, "opt_usd": 1.0})
    failed = [r["check"] for r in results if r["verdict"] == "FAIL"]
    assert "workload/actor_attempts_fit" in failed
    assert "workload/actor_usd_fit" in failed
    assert "workload/optimizer_calls_fit" in failed


def test_the_required_outbound_settings_are_the_frozen_ones():
    assert PRE.REQUIRED_OUTBOUND["max_tokens"] == 8000
    assert PRE.REQUIRED_OUTBOUND["temperature"] == 0.7
    assert PRE.REQUIRED_OUTBOUND["model"] == "qwen3-5-4b"
    assert PRE.REQUIRED_ENV["SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK"] == "committed_answer"
    assert json.loads(PRE.REQUIRED_ENV["TARGET_OPENAI_COMPATIBLE_EXTRA_BODY"]) == {"enable_thinking": False}
    assert VER.REQUIRED["max_tokens"] == PRE.REQUIRED_OUTBOUND["max_tokens"]


def test_a_prior_collection_with_a_different_skill_is_not_reusable(tmp_path):
    """Reuse is an identity question. Different skill bytes means a different policy, so it is refused."""
    (tmp_path / "COLLECT_FREEZE.json").write_text(json.dumps({
        "freeze_sha256_12": "aaaaaaaaaaaa", "ids": ["UID0014"], "attempts_per_question": 1,
        "answer_recovery_policy": "committed_answer", "skill": {"sha256_12": "deadbeefdead"},
        "limits": {"max_tool_turns": 24},
        "model_and_generation": {"condition": {"extra_body": {"enable_thinking": False}}},
    }), encoding="utf-8")
    skill = tmp_path / "initial.md"
    skill.write_text("a different starting skill", encoding="utf-8")
    results: list = []
    out = PRE.check_prior_collection(results, str(tmp_path), str(skill))
    assert out["verdict"] == "NOT_REUSABLE_recorded_skill_differs"
    # it is a WARN, not a FAIL: losing reuse costs money, it does not invalidate the campaign
    assert all(r["verdict"] != "FAIL" for r in results)


def test_the_backend_fallback_is_recognised_as_such():
    """Job 66552338's finding: an unconfigured backend reports gpt-4o-mini on OpenAI's endpoint, and a
    preflight that reads it without applying the config calls those the campaign's settings."""
    assert PRE.is_backend_fallback("gpt-4o-mini", "https://api.empiriolabs.ai/v1") is True
    assert PRE.is_backend_fallback("qwen3-5-4b", "https://api.openai.com/v1") is True
    assert PRE.is_backend_fallback("gpt-4o-mini", "https://api.openai.com/v1") is True
    # the configured endpoint is not a fallback
    assert PRE.is_backend_fallback("qwen3-5-4b", "https://api.empiriolabs.ai/v1") is False
    assert PRE.REQUIRED_OUTBOUND["base_url"] == "https://api.empiriolabs.ai/v1"


def test_the_preflight_applies_the_config_before_asserting_the_outbound_settings():
    """The config does not reach the backend by itself; the trainer calls configure_openai_compatible."""
    src = open(os.path.join(RESEARCH, "oqa_base_preflight.py"), encoding="utf-8").read()
    body = src[src.index("def check_outbound"):src.index("def check_prior_collection")]
    assert "configure_openai_compatible" in body
    assert "_config_for" in body, "the LIVE config must be read, not _initial_config"
    assert "_initial_config" not in body.split('"""')[2] if body.count('"""') > 2 else True


# ───────────────────── the spend ledger must be LIVE, not merely configured ─────────────────────

CLUSTER = os.path.join(REPO, "research", "cluster")


def _body():
    return open(os.path.join(CLUSTER, "oqa_base_body.sh"), encoding="utf-8").read()


def test_the_body_points_the_ledger_at_its_directory():
    """spend_ledger.py is INERT BY DEFAULT: "With SKILLOPT_SPEND_LEDGER_DIR unset this module does
    nothing at all." This body set every cap and never set that variable, so the ledger stayed empty -
    0 claims, 0 spend records - and the caps were unenforced for the whole campaign. The prep stage's
    own LEDGER_BEFORE.json said {"active": false}."""
    body = _body()
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert 'export SKILLOPT_SPEND_LEDGER_DIR="$OQA_LEDGER_DIR"' in code
    # and the directory must be created, or the module is inert for a different reason
    assert '"$OQA_LEDGER_DIR/claims"' in code and '"$OQA_LEDGER_DIR/spend"' in code


def test_every_paid_stage_refuses_to_load_a_key_with_an_inert_ledger():
    """The caps are what the authorization rests on, so a paid stage must fail closed without them."""
    body = _body()
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "require_live_ledger()" in code, "the check must exist"
    # every key load is preceded by the check, within its stage
    for key_load in ("load_actor_key\n", "load_optimizer_key\n"):
        idx = 0
        while True:
            idx = code.find("    " + key_load, idx)
            if idx == -1:
                break
            before = code[max(0, idx - 400):idx]
            assert "require_live_ledger" in before, (
                f"a {key_load.strip()} call is not preceded by require_live_ledger")
            idx += 1


def test_the_check_reads_the_module_rather_than_trusting_the_variable():
    """An unwritable directory or a missing cap also leaves the module inert, so the env var alone is
    not proof. The check asks the module."""
    body = _body()
    fn = body[body.index("require_live_ledger()"):body.index("load_actor_key()")]
    assert "spend_ledger" in fn and "report()" in fn
    assert "active" in fn
    assert "Refusing to load a key" in fn
