"""Stage A software checks for the OfficeQA learned-contract port (constructed inputs only).

These tests establish what the code does on inputs whose intended answer is known in advance. They are NOT
evidence about the actor, the benchmark or the research hypothesis - design v0.1 section 7, Stage A: "Constructed
cases establish software behavior and must be identified as such."

Two of them are regression tests for defects found while building the port, and each says which:
  * `test_two_tables_in_one_block_are_not_merged`
  * `test_scaled_match_requires_equal_digits`

Run: pytest tests/test_officeqa_lc.py   (pure Python; no network, no model, no SkillOpt pipeline, no Lean)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "research", "officeqa"))

import oqa_lc_cells as CELLS  # noqa: E402
import oqa_lc_chain as CHAIN  # noqa: E402
import oqa_lc_ir as IR  # noqa: E402
import oqa_lc_lean as LEAN  # noqa: E402
import oqa_lc_learn as LEARN  # noqa: E402
import oqa_lc_replay as REPLAY  # noqa: E402
import oqa_lc_report as REPORT  # noqa: E402
import oqa_lc_vocab as VOCAB  # noqa: E402

# The two header renderings of the SAME table, abridged from the saved traces of UID0026: the oracle page's split
# form and the transformed document's flattened form. The split form's sub-label row has lost the blank that
# belongs under the row-label column, which is what makes the index merge wrong here.
SPLIT_COIN = """(In millions of dollars except per capita figures)

| End of fiscal year or month | Coin | Money in circulation per capita (in dollars) 2/ |  |  |  |
| --- | --- | --- | --- | --- | --- |
| Total coin | Standard silver dollars | Subsidiary silver | Minor coin |  |  |
| 1955 | 1,858 | 223 | 1,202 | 433 | 182.90 |
| 1956 | 1,948 | 237 | 1,259 | 453 | 182.64 |
"""

FLAT_COIN = """| End of fiscal year or month > End of fiscal year or month | Coin > Total coin | Coin > Standard silver dollars | Coin > Subsidiary silver | Coin > Minor coin | Money in circulation per capita (in dollars) 2/ > Money in circulation per capita (in dollars) 2/ |
| 1955 | 1858 | 223 | 1202 | 433 | 182.90 |
| 1956 | 1948 | 237 | 1259 | 453 | 182.64 |
"""

# A split form whose sub-label row DOES keep its leading blanks: index merging gets the leaf right here, so the
# two cases together are what makes `index_alignment` a hypothesis with support on one side and a refutation on
# the other, rather than a rule that is simply wrong.
SPLIT_PAPER = """(In millions of dollars)

| End of fiscal year or month | Total money in circulation 1/ | Paper money 2/ |  |
| --- | --- | --- | --- |
|  |  | Total paper money | Gold certificates |
| 1960-December | 32,869 | 30,442 | 30 |
| 1961-January | 31,776 | 29,385 | 30 |
| February | 31,769 | 29,377 | 30 |
"""

FLAT_PAPER = """| End of fiscal year or month > Unnamed: 0_level_1 | Total money in circulation 1/ > Unnamed: 1_level_1 | Paper money 2/ > Total paper money | Paper money 2/ > Gold certificates |
| 1960-December | 32869 | 30442 | 30 |
| 1961-January | 31776 | 29385 | 30 |
"""

ALL_ASSUMPTIONS = {"flat_header_authoritative", "index_alignment", "year_inheritance",
                   "table_scale_applies_to_rows", "grep_cap_not_reached"}


# ── vocabulary ─────────────────────────────────────────────────────────────────

def test_vocab_digest_is_stable_and_covers_atoms_and_assumptions():
    d1, d2 = VOCAB.vocab_digest(), VOCAB.vocab_digest()
    assert d1 == d2 and len(d1) == 64
    assert set(VOCAB.ATOMS) >= {"col_leaf_sound", "col_path_sound", "row_period_bound", "cap_reached"}
    assert set(VOCAB.DIRECTLY_TESTABLE) <= set(VOCAB.ASSUMPTIONS)


def test_outcome_of_reserves_fail_for_unproductive_observations():
    assert VOCAB.outcome_of({"kind": "tool", "obs": "| 1955 | 1,858 |"}) == "ok"
    assert VOCAB.outcome_of({"kind": "tool", "obs": "[no matches]"}) == "fail"
    assert VOCAB.outcome_of({"kind": "tool", "obs": "[read error: nope]"}) == "fail"
    assert VOCAB.outcome_of({"kind": "tool", "obs": "  "}) == "fail"
    assert VOCAB.outcome_of({"kind": "message", "text": "hello"}) == "unknown"


# ── table and cell adapter ─────────────────────────────────────────────────────

def test_header_forms_are_distinguished():
    (split,) = [t for t in CELLS.tables(SPLIT_COIN)]
    (flat,) = [t for t in CELLS.tables(FLAT_COIN)]
    assert split["header_form"] == "split" and split["width"] == 6 and split["scale"] == "millions"
    assert flat["header_form"] == "flat" and flat["width"] == 6
    assert flat["flat"][1] == {"parent": "Coin", "child": "Total coin"}
    # a repeated name collapses: "End of fiscal year or month > End of fiscal year or month" has no sub-label
    assert flat["flat"][0] == {"parent": "End of fiscal year or month", "child": ""}


def test_two_tables_in_one_block_are_not_merged():
    """REGRESSION. The oracle page of UID0026 puts two tables in 44 consecutive pipe rows with no blank line
    between them. Treating them as one table bound the coin figures to the paper-money columns, and a wrong
    binding is worse than none."""
    glued = (SPLIT_PAPER.rstrip() + "\n"
             + "| End of fiscal year or month | Total coin | Standard silver dollars |  |\n"
             + "| 1960-December | 2,427 | 319 |  |\n")
    ts = CELLS.tables(glued)
    assert len(ts) == 2, [t["parent_row"] for t in ts]
    assert ts[1]["rows"][0]["label"] == "1960-December"
    # the second table's width is trimmed to its last non-blank cell, not padded to the first table's
    assert ts[1]["width"] == 3


def test_period_is_read_from_the_row_label_and_the_year_is_inherited():
    (t,) = CELLS.tables(SPLIT_PAPER)
    labels = {r["label"]: r["period"] for r in t["rows"]}
    assert labels["1960-December"] == {"year": 1960, "month": 12, "text": "1960-December"}
    assert labels["1961-January"]["month"] == 1 and labels["1961-January"]["year"] == 1961
    feb = labels["February"]
    assert feb["month"] == 2 and feb["year"] == 1961 and feb["inherited_year"] is True
    assert CELLS.period_text(labels["1960-December"]) == "December 1960"


def test_index_merge_disagrees_with_the_flat_header_on_the_coin_table():
    (split,) = CELLS.tables(SPLIT_COIN)
    (flat,) = CELLS.tables(FLAT_COIN)
    assert CELLS.pair_tables([split], [flat]) == [(split, flat)]
    leaf = CELLS.binding_disagreements(split, flat, "leaf")
    assert len(leaf) == 6
    assert leaf[1]["merged_leaf"] == "Standard silver dollars" and leaf[1]["flat_leaf"] == "Total coin"


def test_index_merge_agrees_when_the_sub_row_keeps_its_leading_blanks():
    (split,) = CELLS.tables(SPLIT_PAPER)
    (flat,) = CELLS.tables(FLAT_PAPER)
    assert CELLS.binding_disagreements(split, flat, "leaf") == []
    # the PATH still disagrees: the split form prints a spanning parent only at its first column
    assert [d["index"] for d in CELLS.binding_disagreements(split, flat, "path")] == [3]


def test_footnote_markers_do_not_count_as_a_column_disagreement():
    a = CELLS.tables(FLAT_COIN)[0]
    b = CELLS.tables(FLAT_COIN.replace("(in dollars) 2/", "(in dollars) 7/"))[0]
    assert CELLS.binding_disagreements(a, b, "leaf") == []


def test_cells_bind_to_the_flat_header_when_one_is_available():
    cells = CELLS.cells_of(SPLIT_PAPER, ALL_ASSUMPTIONS, CELLS.tables(FLAT_PAPER), {"kind": "oracle_page"})
    by_value = {c["value"]: c for c in cells}
    assert by_value[30442.0]["column"] == "Paper money 2/ > Total paper money"
    assert by_value[30442.0]["column_basis"] == CELLS.COL_FLAT
    assert by_value[30442.0]["period_text"] == "December 1960"
    assert by_value[32869.0]["column"] == "Total money in circulation 1/"
    assert by_value[30442.0]["scale"] == "millions"


def test_without_the_index_alignment_assumption_a_split_column_is_unknown_not_wrong():
    cells = CELLS.cells_of(SPLIT_COIN, {"flat_header_authoritative"}, [], {"kind": "oracle_page"})
    assert cells and all(c["column"] is None and c["column_basis"] == CELLS.COL_UNKNOWN for c in cells)


def test_atoms_are_unknown_when_nothing_adjudicates_them():
    step = {"kind": "tool", "tool": "read", "args": {"path": "x.txt"}, "obs": FLAT_PAPER}
    vals = CELLS.atom_values(step, ALL_ASSUMPTIONS, [])
    assert vals["has_table"] is True and vals["header_flat"] is True
    assert vals["col_leaf_sound"] is None, "no split-form page was supplied, so there is nothing to adjudicate"


def test_atoms_are_adjudicated_by_the_observation_against_the_supplied_page():
    step = {"kind": "tool", "tool": "read", "args": {"path": "x.txt"}, "obs": FLAT_COIN}
    vals = CELLS.atom_values(step, ALL_ASSUMPTIONS, CELLS.tables(SPLIT_COIN))
    assert vals["col_leaf_sound"] is False
    w = CELLS.disagreement_witnesses(step, CELLS.tables(SPLIT_COIN), "leaf")
    assert len(w) == 6 and w[0]["index"] == 0


def test_cell_number_ignores_footnote_markers_and_non_numbers():
    assert CELLS.cell_number("34,782 3/") == 34782.0
    assert CELLS.cell_number("181.79r") == 181.79
    assert CELLS.cell_number("*") is None and CELLS.cell_number("-") is None
    assert CELLS.cell_number("Individual income taxes") is None


# ── unit conversions and the obligation ledger ─────────────────────────────────

def test_conversion_forms_and_their_verdicts():
    text = ("- For 2000: Net income taxes = 1,004,461 (in millions) = $1,004.461 billion\n"
            "- 1929: Individual = 2,331,274 (in thousands) = $2,331.274 billion\n"
            "- 1930: Individual = 2,410,259 = $2,410.259 billion\n")
    cs = CHAIN.conversions(text)
    assert [c["verdict"] for c in cs] == [CHAIN.DISCHARGED, CHAIN.VIOLATED, CHAIN.VIOLATED]
    assert cs[0]["src_scale"] == "millions" and cs[0]["factor_required"] == pytest.approx(1e-3)
    assert cs[1]["src_scale"] == "thousands" and cs[1]["factor_required"] == pytest.approx(1e-6)
    assert cs[1]["factor_used"] == pytest.approx(1e-3)
    # the third line restates no scale: it is carried from the second, and the basis records that
    assert cs[2]["src_scale"] == "thousands" and cs[2]["src_scale_basis"] == "CARRIED"


def test_a_conversion_with_no_establishable_source_scale_is_unknown_not_wrong():
    (c,) = CHAIN.conversions("the total = 1,234 = $1.234 billion")
    assert c["verdict"] == CHAIN.UNKNOWN and c["src_scale"] is None


def test_scaled_match_requires_equal_digits():
    """REGRESSION. `_match_cells` accepted any 1e3/1e6/1e9 multiple, and the value 1 occurs in almost every
    Treasury table, so "1 scaled by 1e6" matched any operand near a million and the unit check then called it a
    conversion wrong by a billion. A scale conversion preserves the significant digits."""
    cells = [{"value": 1.0, "available_from": 0, "scale": "millions", "raw": "1"},
             {"value": 2331274.0, "available_from": 0, "scale": "thousands", "raw": "2,331,274"}]
    hits = CHAIN._match_cells(2331.274, cells, 99)
    assert [h["raw"] for h in hits] == ["2,331,274"]
    assert hits[0]["scale_factor"] == pytest.approx(1e-3)
    assert CHAIN._match_cells(1000000.0, [cells[0]], 99) == []


def _packet(question, pages, steps, answer=""):
    return {"id": "T1", "question": question, "candidate_files": [], "source_hints": [],
            "oracle_pages": pages, "steps": steps, "final_answer": answer, "provenance": {}}


def test_a_completed_conversion_discharges_the_unit_requirement():
    """The known false warning, as a constructed case: the old rule said "No conversion step appears" for a value
    the actor had already converted correctly."""
    pages = [{"file": "b_2001_12.txt", "page": 17, "url": "", "text": SPLIT_PAPER}]
    steps = [{"i": 1, "kind": "message",
              "text": "From the table: 30,442 (in millions) = $30.442 billion for December 1960."}]
    pk = _packet("What was total paper money in December 1960, in billions of dollars?", pages, steps)
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    ch = CHAIN.build(pk, model)
    unit = [o for o in ch["ledger"] if o["kind"] == "unit_match"]
    assert unit and all(o["state"] == CHAIN.DISCHARGED for o in unit), [o["why"] for o in unit]
    assert "correct factor" in unit[0]["why"]


def test_a_wrong_conversion_factor_is_a_violation_not_a_missing_conversion():
    pages = [{"file": "b_1942_07.txt", "page": 76, "url": "",
              "text": "(In thousands of dollars)\n\n| Fiscal year | Individual |\n| --- | --- |\n"
                      "| 1929 | 2,331,274 |\n| 1930 | 2,410,259 |\n"}]
    steps = [{"i": 1, "kind": "message",
              "text": "- 1929: 2,331,274 (in thousands) = $2,331.274 billion"}]
    pk = _packet("Individual income tax receipts for fiscal year 1929 in billions of dollars?", pages, steps)
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    ch = CHAIN.build(pk, model)
    unit = [o for o in ch["ledger"] if o["kind"] == "unit_match"]
    violated = [o for o in unit if o["state"] == CHAIN.VIOLATED]
    assert violated, [(o["state"], o["why"]) for o in unit]
    assert "1e-06" in violated[0]["why"] and "1,000x" in violated[0]["why"]
    assert violated[0]["applied_exp"] == -3


def test_a_year_inside_the_span_of_two_asked_years_is_unknown_not_violated():
    pages = [{"file": "b.txt", "page": 1, "url": "",
              "text": "(In thousands of dollars)\n\n| Fiscal year | Individual |\n| --- | --- |\n"
                      "| 1930 | 2,410,259 |\n"}]
    steps = [{"i": 1, "kind": "message", "text": "For 1930 the figure is 2,410,259."}]
    pk = _packet("Fit a line to receipts for fiscal years 1929-1942 and project 2000.", pages, steps)
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    ch = CHAIN.build(pk, model)
    per = [o for o in ch["ledger"] if o["kind"] == "period_match"]
    assert per and all(o["state"] == CHAIN.UNKNOWN for o in per), [(o["state"], o["why"]) for o in per]
    assert "inside the span" in per[0]["why"]


def test_reopen_turns_a_discharge_into_unknown_and_never_into_a_violation():
    ledger = [
        {"kind": "cell_bound", "subject": "operand 1", "requires": "r", "state": CHAIN.DISCHARGED,
         "why": "bound", "supports": ["OBS", "ASSUMED(index_alignment)"], "witness": None},
        {"kind": "unit_match", "subject": "operand 2", "requires": "r", "state": CHAIN.VIOLATED,
         "why": "wrong factor", "supports": ["OBS"], "witness": None},
    ]
    out = CHAIN.reopen(ledger, "index_alignment")
    assert out[0]["state"] == CHAIN.UNKNOWN and out[0]["reopened_from"] == CHAIN.DISCHARGED
    assert "ASSUMED(index_alignment)" not in out[0]["supports"]
    assert out[1] == ledger[1], "an obligation that did not use the assumption is untouched"


def test_report_groups_one_diagnosis_per_distinct_reason():
    ledger = [{"kind": "unit_match", "subject": f"operand {v}", "requires": "r", "state": CHAIN.VIOLATED,
               "why": f"the operand is the cell value {v} (thousands) scaled by 0.001, but thousands to billions "
                      f"requires 1e-06", "supports": ["OBS"], "witness": None}
              for v in ("2,331,274", "2,410,259", "1,860,040")]
    groups = REPORT.group(ledger)
    assert len(groups) == 1 and groups[0]["n"] == 3
    assert len(groups[0]["subjects"]) == 3


# ── IR, gate and repair ────────────────────────────────────────────────────────

def test_ir_rejects_what_the_vocabulary_cannot_express():
    assert IR.validate_contract({"verb": "read", "kind": "effect",
                                 "effects": [{"atom": "col_leaf_sound", "args": [], "value": True}]}) == []
    errs = IR.validate_contract({"verb": "read", "kind": "effect",
                                 "effects": [{"atom": "nope", "args": [], "value": True}]})
    assert errs and "unknown atom" in errs[0]
    errs = IR.validate_contract({"verb": "read", "kind": "effect",
                                 "effects": [{"atom": "col_leaf_sound", "args": ["O"], "value": True}]})
    assert errs and "0-ary" in errs[0]
    errs = IR.validate_contract({"verb": "read", "kind": "effect",
                                 "effects": [{"atom": "col_leaf_sound", "args": [], "value": True}],
                                 "condition": [[{"atom": "header_flat", "args": [], "value": True}]]})
    assert errs and "no condition" in errs[0]


def test_freeze_refuses_to_overwrite_with_other_content(tmp_path):
    m = dict(IR.empty_model(), assumptions=["index_alignment"])
    path = str(tmp_path / "m.json")
    digest = IR.freeze(m, path)
    assert IR.freeze(m, path) == digest, "freezing the same content twice is idempotent"
    assert IR.load_frozen(path)["sha256"] == digest
    with pytest.raises(RuntimeError):
        IR.freeze(dict(m, assumptions=["year_inheritance"]), path)


def _ev(obs_pairs):
    """Evidence from constructed attempts: each entry is (supplied page text, [observation texts])."""
    attempts = []
    for n, (page, observations) in enumerate(obs_pairs):
        steps = [{"i": 2 * i + 2, "kind": "tool", "tool": "read", "args": {"path": "x.txt"}, "obs": o}
                 for i, o in enumerate(observations)]
        attempts.append({"id": f"C{n}", "question": "q", "oracle_pages":
                         ([{"file": "x.txt", "page": 1, "url": "", "text": page}] if page else []),
                         "steps": steps, "final_answer": "", "candidate_files": [], "source_hints": []})
    return REPLAY.Evidence(attempts)


def test_the_gate_refuses_a_contract_with_a_counterexample():
    ev = _ev([(SPLIT_COIN, [FLAT_COIN])])
    c = {"verb": "read", "kind": "effect", "effects": [{"atom": "col_leaf_sound", "args": [], "value": True}]}
    sc = REPLAY.score_contract(c, ev, ALL_ASSUMPTIONS)
    assert sc["counter"] and LEARN.gate(sc)
    model, dec = LEARN.apply_proposal(IR.empty_model(), {"contract_ops": [{"op": "add", "contract": c}]},
                                      ev, epoch=1, source="test")
    assert dec[0]["decision"] == "rejected" and "counterexample" in dec[0]["why"][0]
    assert IR.active(model) == [] and len(model["rejected"]) == 1


def test_the_gate_refuses_a_contract_supported_by_a_single_item():
    ev = _ev([(SPLIT_PAPER, [FLAT_PAPER])])
    induced = LEARN.mech_induce(ev, ALL_ASSUMPTIONS, model=IR.empty_model())
    assert all(op["contract"]["effects"][0]["atom"] != "col_leaf_sound" for op in induced["contract_ops"]), \
        "one supporting item is not independent task-level confirmation"
    induced2 = LEARN.mech_induce(ev, ALL_ASSUMPTIONS, model=IR.empty_model(), min_items=1)
    assert any(op["contract"]["effects"][0]["atom"] == "col_leaf_sound" for op in induced2["contract_ops"])


def test_an_adopted_assumption_is_retested_against_later_evidence():
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    clean = _ev([(SPLIT_PAPER, [FLAT_PAPER])])
    assert LEARN.assumption_recheck(model, clean)["assumption_ops"] == []
    dirty = _ev([(SPLIT_PAPER, [FLAT_PAPER]), (SPLIT_COIN, [FLAT_COIN])])
    ops = LEARN.assumption_recheck(model, dirty)["assumption_ops"]
    assert [o["name"] for o in ops] == ["index_alignment"]
    assert ops[0]["n_witnesses"] == 6 and "merging the supplied page" in ops[0]["witnesses"][0]
    after, dec = LEARN.apply_proposal(model, {"assumption_ops": ops}, dirty, epoch=2, source="test")
    assert "index_alignment" not in after["assumptions"]
    assert [d["decision"] for d in dec] == ["accepted"]


def test_a_truncated_window_is_not_a_counterexample_to_year_inheritance():
    """REGRESSION. A `read` returns a 100-line window, so a table is usually truncated and the window's first
    labelled row is not the table's first row. Counting those dropped the assumption on artefacts of the window."""
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    truncated = "| February | 31,769 | 29,377 |\n| March | 31,891 | 29,484 |\n"
    assert LEARN.assumption_recheck(model, _ev([(None, [truncated])]))["assumption_ops"] == []
    with_header = ("| Period | A | B |\n| --- | --- | --- |\n|  | x | y |\n"
                   "| February | 31,769 | 29,377 |\n| March | 31,891 | 29,484 |\n")
    ops = LEARN.assumption_recheck(model, _ev([(None, [with_header])]))["assumption_ops"]
    assert [o["name"] for o in ops] == ["year_inheritance"]


def test_a_repair_targets_a_refuted_contract_not_an_active_one():
    """`apply_proposal` auto-refutes every active contract that acquires a counterexample, so a repair proposer
    looking only at active contracts never fires at all."""
    ev = _ev([(SPLIT_PAPER, [FLAT_PAPER]), (SPLIT_COIN, [FLAT_COIN])])
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS),
                 contracts=[{"id": "read.effect.9", "verb": "read", "kind": "effect", "version": 1,
                             "status": "refuted", "context": {"answer_kinds": []}, "assumptions": [],
                             "effects": [{"atom": "col_leaf_sound", "args": [], "value": True}]}])
    repair = LEARN.mech_repair(model, ev)
    assert [o["name"] for o in repair["assumption_ops"]] == ["index_alignment"]


# ── prefix replay ──────────────────────────────────────────────────────────────

def _attempt(page, observations):
    return {"id": "R1", "question": "q", "candidate_files": [], "source_hints": [], "final_answer": "",
            "oracle_pages": [{"file": "x.txt", "page": 1, "url": "", "text": page}],
            "steps": [{"i": i + 1, "kind": "tool", "tool": "read", "args": {"path": "x.txt"}, "obs": o}
                      for i, o in enumerate(observations)]}


def test_revealing_before_committing_raises():
    rev = REPLAY.Revealer(_attempt(SPLIT_COIN, [FLAT_COIN]))
    with pytest.raises(REPLAY.RevealOrderError):
        rev.reveal(0)
    rev.request(0)
    with pytest.raises(REPLAY.RevealOrderError):
        rev.reveal(0)
    rev.commit(0, {"x": 1})
    assert rev.reveal(0)["obs"] == FLAT_COIN
    with pytest.raises(REPLAY.RevealOrderError):
        rev.request(2)


def test_the_commit_log_is_hash_chained_and_prediction_precedes_reveal():
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    seen = []
    res = REPLAY.replay_attempt(_attempt(SPLIT_COIN, [FLAT_COIN]), model, seen.append)
    events = [e["event"] for e in res["reveal_log"]]
    assert events == ["commit", "reveal"]
    assert len(seen) == 1 and "outcome" not in seen[0], "the sink is called before the observation is released"
    chain = ""
    for e in res["reveal_log"]:
        expect = VOCAB.sha(chain + (e["sha256"] if e["event"] == "commit" else f"reveal:{e['t']}"))
        assert e["chain"] == expect
        chain = e["chain"]


def test_an_adopted_assumption_is_itself_a_committed_forecast_that_the_observation_refutes():
    model = dict(IR.empty_model(), assumptions=sorted(ALL_ASSUMPTIONS))
    res = REPLAY.replay_attempt(_attempt(SPLIT_COIN, [FLAT_COIN]), model)
    (rec,) = res["attempts"]
    forecasts = [f for f in rec["prediction"]["forecasts"] if f["contract"] == "ASSUMED(index_alignment)"]
    assert forecasts and forecasts[0]["predicted"] is True
    checks = [f for f in rec["forecast_checks"] if f["contract"] == "ASSUMED(index_alignment)"]
    assert checks[0]["class"] == REPLAY.FORECAST_VIOLATED
    assert rec["witnesses"], "the violation carries the columns that refute it"


def test_without_the_assumption_no_forecast_is_made_and_nothing_is_violated():
    model = dict(IR.empty_model(), assumptions=["flat_header_authoritative"])
    res = REPLAY.replay_attempt(_attempt(SPLIT_COIN, [FLAT_COIN]), model)
    (rec,) = res["attempts"]
    assert rec["prediction"]["forecasts"] == []
    assert REPLAY.score(res["attempts"])["forecasts"] == {"confirmed": 0, "violated": 0, "unresolved": 0}


def test_condition_based_prediction_is_undetermined_at_pre_by_construction():
    """Every OfficeQA atom is a property of the observation the step has not returned yet, so a requires/suffices
    condition cannot be valued from the prefix. The port records that rather than inventing a value."""
    model = dict(IR.empty_model(), assumptions=[], contracts=[
        {"id": "read.requires.1", "verb": "read", "kind": "requires", "version": 1, "status": "active",
         "context": {"answer_kinds": []}, "assumptions": [],
         "condition": [[{"atom": "has_table", "args": [], "value": True}]]}])
    pred = REPLAY.predict(model, {"tool": "read", "args": {}}, None)
    assert pred["label"] == "undetermined" and pred["unknown_by"] == ["read.requires.1"]


# ── Lean generation and its mirror ─────────────────────────────────────────────

def test_the_lean_unit_model_uses_the_cell_minus_asked_exponent():
    """millions (6) to billions (9) is a factor of 10^(6-9); getting the sign the other way round marked every
    correct conversion as a violation."""
    assert LEAN.m_unit_discharged({"cellExp": 6, "askedExp": 9, "appliedExp": -3}) is True
    assert LEAN.m_unit_discharged({"cellExp": 3, "askedExp": 9, "appliedExp": -3}) is False
    assert LEAN.m_unit_discharged({"cellExp": 3, "askedExp": 9, "appliedExp": -6}) is True
    assert LEAN.m_unit_discharged({"cellExp": 3, "askedExp": None, "appliedExp": 0}) is None


def test_the_general_mirror_confirms_the_exhaustions_the_lean_file_states():
    g = LEAN.general_mirror()
    assert g["all_ok"] and g["unit_false_always_false"] and g["unknown_bound_never_true"]


def test_unknown_binding_is_never_acceptable_and_reopening_never_violates():
    op = {"basis": "indexMerge", "periodAsked": True, "cellExp": 6, "askedExp": 9, "appliedExp": -3}
    assert LEAN.m_acceptable(op) is True
    assert LEAN.m_acceptable(op, after_drop=True) is None, "a withdrawn basis reopens to unknown"
    bad = dict(op, appliedExp=-6)
    assert LEAN.m_acceptable(bad) is False and LEAN.m_acceptable(bad, after_drop=True) is False


def test_generated_lean_is_core_only_and_states_one_theorem_per_row(tmp_path):
    rows = [LEAN.operand_row({"aid": "A@1", "asked_scale": "billions", "applied_exp": -3,
                              "period_match": {"state": "DISCHARGED"},
                              "witness": {"scale": "millions", "column_basis": "FLAT"}})]
    atoms = [{"aid": "A@1", "contract": "read.effect.1", "atom": "col_leaf_sound", "predicted": True,
              "observed": False, "class": "violated"}]
    src, mirror = LEAN.generate(rows, atoms)
    assert mirror["mismatches"] == []
    base, _ = LEAN.generate([], [])
    # two statements per operand row (before and after a repair) and one per adjudicated forecast
    assert src.count("by decide") == base.count("by decide") + 2 * len(rows) + len(atoms)
    assert mirror["n_theorems"] == 2 * len(rows) + len(atoms)
    for forbidden in ("sorry", "native_decide", "axiom ", "import Mathlib"):
        assert forbidden not in src
    path = str(tmp_path / "OqaContracts.lean")
    assert LEAN.write(src, path) == mirror["sha256"]
    assert open(path, encoding="utf-8").read() == src


def test_parse_results_fails_on_an_error_or_an_unexpected_axiom():
    assert LEAN.parse_results(0, "", 10)["ok"] is True
    assert LEAN.parse_results(1, "OqaContracts.lean:3:0: error: boom", 10)["ok"] is False
    bad = LEAN.parse_results(0, "'axiom' uses 'sorryAx'", 10)
    assert bad["ok"] is False or bad["unexpected_axioms"]


# ── report provenance: the right attempt AND the right model version ───────────

def _mk_pred(tmp_path, tid, conv, prompt="## Question\nq\n"):
    d = tmp_path / "predictions" / tid
    d.mkdir(parents=True)
    (d / "conversation.json").write_text(__import__("json").dumps(conv), encoding="utf-8")
    (d / "target_user_prompt.txt").write_text(prompt, encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(
        __import__("json").dumps({"id": tid, "ground_truth": "30,442"}) + "\n", encoding="utf-8")
    return str(tmp_path / "predictions")


def _frozen(tmp_path, name, assumptions):
    path = str(tmp_path / name)
    IR.freeze(dict(IR.empty_model(), assumptions=sorted(assumptions)), path)
    return path


def test_report_provenance_binds_a_report_to_its_attempt_and_model_version(tmp_path):
    """The A40 run handed eight epoch-2 failures reports built from their epoch-1 trajectories. A report must
    therefore carry BOTH the trace it came from and the model version that produced it, and a check must reject
    either mismatch."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "oqa_reports", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "research", "officeqa", "oqa_reports.py"))
    RP = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("oqa_reports", RP)
    spec.loader.exec_module(RP)

    conv_a = [{"type": "message", "content": "From the table: 30,442 for December 1960."}]
    conv_b = [{"type": "message", "content": "A different attempt entirely: 29,385."}]
    pred_a = _mk_pred(tmp_path / "a", "UID0026", conv_a)
    pred_b = _mk_pred(tmp_path / "b", "UID0026", conv_b)
    m1 = _frozen(tmp_path, "m1.json", ALL_ASSUMPTIONS)
    m2 = _frozen(tmp_path, "m2.json", ALL_ASSUMPTIONS - {"index_alignment"})

    _, rec_a = RP.build_one_lc(pred_a, "UID0026", "q", str(tmp_path / "a" / "results.jsonl"), m1)
    assert rec_a["model_sha256"] and rec_a["trace_sha256"]
    # against its own attempt and model: PASS
    assert RP.verify_report_provenance([rec_a], pred_a, m1)["verdict"] == "PASS"
    # the same report against a DIFFERENT attempt: FAIL, naming the trace
    bad_trace = RP.verify_report_provenance([rec_a], pred_b, m1)
    assert bad_trace["verdict"] == "FAIL" and "trace" in bad_trace["problems"][0]
    # and against a different MODEL VERSION: FAIL, naming the model
    bad_model = RP.verify_report_provenance([rec_a], pred_a, m2)
    assert bad_model["verdict"] == "FAIL" and "model" in bad_model["problems"][0]


def test_the_frozen_model_digest_is_verified_before_a_report_is_built(tmp_path):
    """A model file edited after freezing must not silently produce reports attributed to its old digest."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "oqa_reports2", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "research", "officeqa", "oqa_reports.py"))
    RP = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RP)
    path = _frozen(tmp_path, "m.json", ALL_ASSUMPTIONS)
    assert RP.lc_model_digest(path)
    import json as _json
    os.chmod(path, 0o644)
    blob = _json.loads(open(path).read())
    blob["assumptions"] = ["year_inheritance"]          # tamper, keep the recorded sha256
    open(path, "w").write(_json.dumps(blob))
    with pytest.raises(RuntimeError, match="does not match its frozen digest"):
        RP.lc_model_digest(path)
