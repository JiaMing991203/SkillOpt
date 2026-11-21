"""Occurrence-level source binding (OQA-OCC-1).

WHY EACH TEST IS HERE. This module exists because OQA-BIND-1 refuted column SEMANTICS from the pipe
rendering, and the instruction distinguishes "a unique numeric match" from "an established source
binding". The tests pin that distinction and the two ways it could be lost:

  * a unique position found by VALUE could be reported as a binding - it must not be, without
    independent corroboration;
  * a condition that is nearly a tautology could be counted as corroboration - SOLE_IN_ROW was, and
    lifted the supported rate to 100%, so a test asserts it is excluded and that the entailment is
    measured rather than asserted in prose.
"""
from __future__ import annotations

import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")


def _mod(name):
    for p in (REPO, RESEARCH):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"{name}_occ_test", os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


O = _mod("oqa_occ")


def _grep(path, pattern, obs):
    return {"type": "tool_call", "cmd": f"grep(pattern='{pattern}', path='{path}')", "obs": obs}


def _msg(text):
    return {"type": "message", "content": text}


# ───────────────────────── value parsing ─────────────────────────

def test_table_numbers_parse_including_parenthesised_negatives():
    assert O.parse_value("1,234") == 1234.0
    assert O.parse_value("$2,331,274") == 2331274.0
    assert O.parse_value("(716)") == -716.0
    assert O.parse_value("-74") == -74.0
    assert O.parse_value("114.0") == 114.0
    assert O.parse_value("12%") == 12.0
    assert O.parse_value("not a number") is None


def test_stopwords_cannot_corroborate_anything():
    """A shared "total" would corroborate every row in a financial table."""
    assert O.tokens("Total receipts for the fiscal year") == {"receipts"}
    for word in ("total", "year", "fiscal", "column", "table"):
        assert word in O.STOPWORDS


# ───────────────────────── the occurrence channel ─────────────────────────

def test_only_line_numbered_pipe_delimited_observations_yield_positions():
    conv = [
        _grep("/docs/b_1942_07.txt", "individual", "3507: | Receipts | 528979 | 533325 |"),
        # no line number -> no position, so it yields nothing rather than a guess
        {"type": "tool_call", "cmd": "read(path='/docs/x.txt')", "obs": "| Receipts | 999 |"},
        # line number but no pipes -> not a table row
        _grep("/docs/y.txt", "p", "42: just prose with 123 in it"),
    ]
    occ = O.observed_occurrences(conv)
    assert {o["value"] for o in occ} == {528979.0, 533325.0}
    assert all(o["line"] == 3507 for o in occ)
    assert {o["document"] for o in occ} == {"b_1942_07.txt"}
    assert sorted(o["field"] for o in occ) == [2, 3]


def test_a_record_with_no_numbered_observation_reports_the_channel_as_absent():
    """Four of the 24 saved records are like this. That is missing information, not an UNKNOWN binding."""
    result = O.bind([{"type": "tool_call", "cmd": "read(path='/d/x.txt')", "obs": "| a | 5 |"},
                     _msg("the answer is 5")])
    assert result["occurrence_channel_available"] is False
    assert result["n_observed_occurrences"] == 0
    assert result["supported"] == 0
    assert result["bindings"][0]["match"] == O.NO_OCCURRENCE


# ───────────────────────── a unique match is NOT a binding ─────────────────────────

def test_a_unique_position_without_corroboration_stays_unknown():
    """THE distinction the instruction insists on: a unique numeric match is not a source binding."""
    conv = [_grep("/d/b.txt", "zzz", "100: | Alpha | 4242 |"),
            _msg("I will report 4242 as the figure.")]     # names nothing on the line
    result = O.bind(conv)
    binding = next(b for b in result["bindings"] if b["value"] == 4242.0)
    assert binding["match"] == O.UNIQUE_POSITION
    assert binding["binding"] == O.UNKNOWN
    assert binding["supporting"] == []
    assert "not an established source binding" in binding["why"]
    assert result["supported"] == 0


def test_a_unique_position_corroborated_by_the_actors_own_words_is_supported():
    conv = [_grep("/d/b.txt", "zzz", "100: | Individual receipts | 4242 |"),
            _msg("The Individual receipts row gives 4242.")]
    result = O.bind(conv)
    binding = next(b for b in result["bindings"] if b["value"] == 4242.0)
    assert binding["binding"] == O.SUPPORTED
    assert "ROW_LABEL" in binding["supporting"]
    assert binding["position"] == {"document": "b.txt", "line": 100, "field": 2}
    assert result["supported"] == 1


def test_the_query_pattern_corroborates_because_it_is_evidence_of_intent():
    conv = [_grep("/d/b.txt", "individual", "100: | Individual | 4242 |"),
            _msg("the figure is 4242")]
    binding = next(b for b in O.bind(conv)["bindings"] if b["value"] == 4242.0)
    assert "QUERY" in binding["supporting"]
    assert binding["binding"] == O.SUPPORTED


def test_the_same_value_at_two_positions_is_unknown_and_names_the_rivals():
    """Numeric equality must not choose between competing cells."""
    conv = [_grep("/d/b.txt", "receipts", "100: | Receipts | 4242 |\n200: | Receipts | 4242 |"),
            _msg("Receipts are 4242.")]
    binding = next(b for b in O.bind(conv)["bindings"] if b["value"] == 4242.0)
    assert binding["match"] == O.SEVERAL_POSITIONS
    assert binding["binding"] == O.UNKNOWN
    assert binding["n_positions"] == 2
    assert len(binding["positions"]) == 2


def test_an_actor_derived_result_has_no_occurrence_and_that_is_not_a_fault():
    conv = [_grep("/d/b.txt", "receipts", "100: | Receipts | 300 | 200 |"),
            _msg("Receipts 300 minus 200 gives 100 as my result.")]
    result = O.bind(conv)
    derived = next(b for b in result["bindings"] if b["value"] == 100.0)
    assert derived["match"] == O.NO_OCCURRENCE
    assert "actor-derived" in derived["why"]


def test_an_intermediate_quantity_may_come_from_a_different_document():
    """A multihop question needs operands from two tables; nothing here forbids that."""
    conv = [_grep("/d/a.txt", "receipts", "10: | Receipts | 500 |"),
            _grep("/d/b.txt", "outlays", "20: | Outlays | 600 |"),
            _msg("Receipts 500 and Outlays 600.")]
    result = O.bind(conv)
    docs = {b["position"]["document"] for b in result["bindings"] if b.get("position")}
    assert docs == {"a.txt", "b.txt"}
    assert result["supported"] == 2


# ───────────────────────── the rejected corroboration ─────────────────────────

def test_sole_in_row_is_excluded_from_support():
    assert "SOLE_IN_ROW" in O.NON_INFORMATIVE
    assert "SOLE_IN_ROW" not in O.SUPPORTING
    assert set(O.SUPPORTING) == {"ROW_LABEL", "QUERY"}


def test_sole_in_row_is_computed_and_reported_but_cannot_support_alone():
    conv = [_grep("/d/b.txt", "zzz", "100: | Alpha | 4242 |"), _msg("report 4242")]
    binding = next(b for b in O.bind(conv)["bindings"] if b["value"] == 4242.0)
    assert "SOLE_IN_ROW" in binding["corroborations"]   # reported, for transparency
    assert binding["binding"] == O.UNKNOWN              # but it supports nothing


def test_a_unique_position_almost_entails_sole_in_row_which_is_why_it_was_rejected():
    """Two occurrences of one value on a single line occupy two field indices, so the position is not
    unique. The condition is therefore nearly free, and counting it made the rate 100%."""
    conv = [_grep("/d/b.txt", "zzz", "100: | Alpha | 4242 | 4242 |"), _msg("Alpha is 4242")]
    binding = next(b for b in O.bind(conv)["bindings"] if b["value"] == 4242.0)
    assert binding["match"] == O.SEVERAL_POSITIONS, \
        "a repeated value on one line must not be a unique position"


def test_the_corpus_measurement_reports_the_entailment_rather_than_asserting_it(tmp_path):
    import json as _json
    d = tmp_path / "predictions" / "UID0001"
    d.mkdir(parents=True)
    (d / "conversation.json").write_text(_json.dumps([
        _grep("/d/b.txt", "zzz", "100: | Alpha | 11 |"), _msg("report 11"),
    ]), encoding="utf-8")
    doc = O.measure(str(tmp_path / "predictions" / "*" / "conversation.json"))
    rec = doc["sole_in_row_is_non_informative"]
    assert rec["unique_positions"] == 1
    assert rec["unique_and_not_sole_in_row"] == 0
    assert rec["share_entailed"] == 1.0
    assert "tautology" in rec["note"]
    assert doc["totals"]["supported"] == 0


def test_the_measurement_is_labelled_reused_development_evidence():
    import json as _json
    doc = O.measure("no-such-path-*/conversation.json")
    assert "REUSED DEVELOPMENT EVIDENCE" in doc["label"]
    assert "No validation or held-out record takes part" in doc["label"]
    _json.dumps(doc, default=str)


# ───────────────────────── amendment C-1: the chain may not pick a cell by value ─────────────────────────

CHAIN = _mod("oqa_lc_chain")


def _hit(value, where, basis=None, step=1, factor=1.0):
    return {"value": value, "available_from": step, "scale_factor": factor,
            "column_basis": basis, "row_label": where, "column": where,
            "source": {"kind": "observation", "ref": where}, "table_key": where}


def test_one_candidate_is_selected_without_needing_corroboration():
    cell, basis = CHAIN.select_cell([_hit(500.0, "a")], None)
    assert cell is not None
    assert basis["basis"] == CHAIN.SOLE_CANDIDATE
    assert basis["n_candidates"] == 1


def test_no_candidate_is_reported_as_such_not_as_a_refusal():
    cell, basis = CHAIN.select_cell([], None)
    assert cell is None
    assert basis["basis"] is None
    assert basis["n_candidates"] == 0


def test_several_candidates_without_corroboration_select_NOTHING():
    """THE defect C-1 repairs: `_best_cell` returned a tie-break winner here."""
    hits = [_hit(500.0, "a", step=1), _hit(500.0, "b", step=2)]
    cell, basis = CHAIN.select_cell(hits, None)
    assert cell is None, "a tie broken by numeric equality is not an established source binding"
    assert basis["basis"] == CHAIN.REFUSED_NO_CORROBORATION
    assert basis["n_candidates"] == 2
    assert len(basis["rivals"]) == 2
    assert "not an established source binding" in basis["why"]


def test_several_candidates_with_an_unknown_occurrence_still_select_nothing():
    hits = [_hit(500.0, "a"), _hit(500.0, "b")]
    support = {"binding": O.UNKNOWN, "match": O.SEVERAL_POSITIONS}
    cell, basis = CHAIN.select_cell(hits, support)
    assert cell is None
    assert basis["basis"] == CHAIN.REFUSED_NO_CORROBORATION
    assert basis["occurrence_match"] == O.SEVERAL_POSITIONS


def test_several_candidates_with_a_supported_occurrence_do_select_and_record_the_position():
    hits = [_hit(500.0, "a"), _hit(500.0, "b")]
    support = {"binding": O.SUPPORTED, "match": O.UNIQUE_POSITION,
               "position": {"document": "b.txt", "line": 100, "field": 2},
               "supporting": ["ROW_LABEL"]}
    cell, basis = CHAIN.select_cell(hits, support)
    assert cell is not None
    assert basis["basis"] == CHAIN.OCCURRENCE_CORROBORATED
    assert basis["occurrence"] == {"document": "b.txt", "line": 100, "field": 2}
    assert basis["corroborated_by"] == ["ROW_LABEL"]


def test_the_refusal_names_the_rivals_so_a_reader_can_judge():
    hits = [_hit(500.0, "alpha", basis="FLAT"), _hit(500.0, "beta"), _hit(500.0, "gamma")]
    _, basis = CHAIN.select_cell(hits, None)
    assert {r["column_basis"] for r in basis["rivals"]} == {"FLAT", None}
    assert len(basis["rivals"]) == 3


def test_the_gate_can_only_withhold_a_selection_never_invent_one():
    """C-1's committed prediction: the change only ever WITHHOLDS. Selections cannot increase."""
    for hits in ([], [_hit(1.0, "a")], [_hit(1.0, "a"), _hit(1.0, "b")]):
        for support in (None, {"binding": O.UNKNOWN}, {"binding": O.SUPPORTED, "supporting": ["QUERY"]}):
            cell, _ = CHAIN.select_cell(hits, support)
            if not hits:
                assert cell is None
            elif len(hits) == 1:
                assert cell is not None      # unchanged from the old behaviour
            else:
                assert (cell is not None) == (support is not None
                                              and support.get("binding") == O.SUPPORTED)


def test_the_packet_step_shape_feeds_the_same_occurrence_measurement():
    """A chain packet uses kind/text/cmd/obs/args; a saved trajectory uses type/content. One measurement."""
    steps = [
        {"i": 1, "kind": "tool", "tool": "grep",
         "args": {"pattern": "individual", "path": "/d/b_1942.txt"},
         "cmd": "grep(pattern='individual', path='/d/b_1942.txt')",
         "obs": "100: | Individual receipts | 4242 |"},
        {"i": 2, "kind": "message", "text": "The Individual receipts row gives 4242."},
    ]
    index = O.support_index(steps)
    assert index[4242.0]["binding"] == O.SUPPORTED
    assert index[4242.0]["position"] == {"document": "b_1942.txt", "line": 100, "field": 2}
    # and the args are preferred over re-parsing the cmd string
    assert O.observed_occurrences(steps)[0]["document"] == "b_1942.txt"


def test_normalize_drops_anything_that_is_not_a_known_step_shape():
    out = O.normalize([{"kind": "tool", "cmd": "c", "obs": "o"},
                       {"type": "message", "content": "m"},
                       {"kind": "message", "text": "t"},
                       {"kind": "provider_debug", "content": "SECRET"},
                       "not a dict"])
    assert [i["kind"] for i in out] == ["tool", "message", "message"]
    assert all("SECRET" not in str(i) for i in out)


# ───────────────────────── amendment C-2: the two repairs to C-1 ─────────────────────────

def _cell(value, file, row, col_index, step, factor=1.0, basis=None):
    return {"value": value, "available_from": step, "scale_factor": factor, "column_basis": basis,
            "row_label": row, "column": None, "col_index": col_index,
            "source": {"kind": "grep", "file": file, "step": step}, "table_key": f"{file}#{step}"}


def test_cell_identity_excludes_the_observation_route_but_keeps_the_document():
    """The route is exactly what differs when ONE cell is seen twice, so it cannot be part of identity."""
    a = _cell(500.0, "b_1942.txt", "Receipts", 2, step=2)
    b = _cell(500.0, "b_1942.txt", "Receipts", 2, step=12)      # same cell, later grep
    c = _cell(500.0, "other.txt", "Receipts", 2, step=2)        # different document
    d = _cell(500.0, "b_1942.txt", "Outlays", 2, step=2)        # different row
    e = _cell(500.0, "b_1942.txt", "Receipts", 3, step=2)       # different column index
    assert CHAIN.cell_identity(a) == CHAIN.cell_identity(b)
    for other in (c, d, e):
        assert CHAIN.cell_identity(a) != CHAIN.cell_identity(other)


def test_dedupe_keeps_the_earliest_available_member_of_each_group():
    hits = [_cell(500.0, "b.txt", "Receipts", 2, step=12),
            _cell(500.0, "b.txt", "Receipts", 2, step=2),
            _cell(500.0, "b.txt", "Receipts", 2, step=7)]
    cells, n_raw = CHAIN.dedupe_candidates(hits)
    assert n_raw == 3 and len(cells) == 1
    assert cells[0]["available_from"] == 2, "available_from must still say when it could first be read"


def test_a_tie_that_is_one_cell_seen_three_times_is_no_tie_at_all():
    """THE defect C-2 repairs. The UID0044 witness: three 'rivals' for -3044.0 were the supplied page
    and two greps of the SAME document."""
    hits = [_cell(-3044.0, "b_1939.txt", "Net", 4, step=0),
            _cell(-3044.0, "b_1939.txt", "Net", 4, step=2),
            _cell(-3044.0, "b_1939.txt", "Net", 4, step=12)]
    cell, basis = CHAIN.select_cell(hits, None)
    assert cell is not None, "a collapsed tie needs no corroboration"
    assert basis["basis"] == CHAIN.SOLE_CANDIDATE
    assert basis["n_candidates"] == 1 and basis["n_raw_candidates"] == 3
    assert "collapse to ONE cell" in basis["why"]


def test_genuinely_distinct_cells_are_still_refused_without_corroboration():
    """C-2 must not undo C-1: different rows holding the same number remain a real tie."""
    hits = [_cell(500.0, "b.txt", "Receipts", 2, step=2),
            _cell(500.0, "b.txt", "Outlays", 2, step=2)]
    cell, basis = CHAIN.select_cell(hits, None)
    assert cell is None
    assert basis["basis"] == CHAIN.REFUSED_NO_CORROBORATION
    assert basis["n_candidates"] == 2 and basis["n_raw_candidates"] == 2
    assert {r["row_label"] for r in basis["rivals"]} == {"Receipts", "Outlays"}


def test_the_raw_candidate_count_stays_reportable():
    """A reader should be able to see that nine candidates were really three cells."""
    hits = ([_cell(1.0, "b.txt", "A", 2, step=s) for s in (1, 2, 3)]
            + [_cell(1.0, "b.txt", "B", 2, step=s) for s in (4, 5, 6)]
            + [_cell(1.0, "b.txt", "C", 2, step=s) for s in (7, 8, 9)])
    _, basis = CHAIN.select_cell(hits, None)
    assert basis["n_raw_candidates"] == 9
    assert basis["n_candidates"] == 3


# ── defect A: a withdrawn binding leaves UNKNOWN requirements, not absent ones ──

OB = _mod("oqa_obligations")


def _chain_with_a_refused_operand(n_candidates=2, n_raw=5):
    """A chain whose one operand was REFUSED by C-1. Built directly, because a synthetic packet does
    not reach the operand classifier and a skipped test pins nothing."""
    sel = {"basis": CHAIN.REFUSED_NO_CORROBORATION, "n_candidates": n_candidates,
           "n_raw_candidates": n_raw,
           "rivals": [{"where": "the grep at step 1", "row_label": "Receipts"},
                      {"where": "the grep at step 1", "row_label": "Outlays"}]}
    task = OB.derive("What were receipts in fiscal year 1961, in millions of dollars?")
    return {"id": "T", "question": "q", "task": task, "cells": [],
            "operands": [{"raw": "500", "value": 500.0, "step": 2, "cells": [{}, {}],
                          "cell": None, "selection": sel, "role": "operand"}],
            "computed": [], "conversions": [], "computations": [],
            "answer": "300", "answer_value": 300.0,
            "model": {"version": "t", "sha256": "x", "assumptions": []}}


def test_a_refused_operand_still_carries_all_three_requirements():
    """It used to `continue` after cell_bound, so period_match and unit_match were never generated:
    464 refusals lost exactly two records each, which is the whole 1,839 -> 911 ledger gap."""
    ledger = CHAIN.obligations(_chain_with_a_refused_operand())
    mine = [r for r in ledger if r["subject"] == "operand 500 (step 2)"]
    assert {r["kind"] for r in mine} == {"cell_bound", "period_match", "unit_match"}
    assert len(mine) == 3
    for r in mine:
        assert r["state"] == CHAIN.UNKNOWN, r["kind"]
        assert r.get("unresolvable_without_a_source") is True
        assert r.get("refused_rivals"), "the rivals must travel with the requirement"


def test_a_refusal_is_reported_as_undetermined_not_as_absent():
    """The pre-existing branch says "no available numeric cell has this value" and marks it
    OUTSTANDING. For a refusal that is FALSE - several cells hold it - and OUTSTANDING means nothing in
    the record addresses the requirement, when several rival cells plainly do."""
    ledger = CHAIN.obligations(_chain_with_a_refused_operand())
    bound = next(r for r in ledger if r["kind"] == "cell_bound")
    assert bound["state"] == CHAIN.UNKNOWN
    assert bound["state"] != "OUTSTANDING"
    assert "no available numeric cell has this value" not in bound["why"]
    assert "undetermined rather than absent" in bound["why"]
    # and both counts are reported, so a reader sees 5 observations collapsed to 2 distinct cells
    assert "2 distinct cells" in bound["why"] and "5 candidate observations" in bound["why"]


def test_the_recovered_requirements_are_never_accusations():
    """C-2's committed prediction 2: withdrawing a binding may withdraw a discharge; it may never
    manufacture an accusation."""
    for n in (2, 3, 9):
        ledger = CHAIN.obligations(_chain_with_a_refused_operand(n_candidates=n))
        assert not [r for r in ledger if r.get("unresolvable_without_a_source")
                    and r["state"] != CHAIN.UNKNOWN]
        assert not [r for r in ledger
                    if r["subject"] == "operand 500 (step 2)" and r["state"] == "VIOLATED"]


def test_an_operand_with_no_candidate_at_all_also_keeps_its_requirements():
    """The principle is the same whichever way the source is unavailable."""
    chain = _chain_with_a_refused_operand()
    chain["operands"][0]["selection"] = {"basis": None, "n_candidates": 0, "n_raw_candidates": 0}
    chain["operands"][0]["cells"] = []
    mine = [r for r in CHAIN.obligations(chain) if r["subject"] == "operand 500 (step 2)"]
    assert {r["kind"] for r in mine} == {"cell_bound", "period_match", "unit_match"}
    downstream = [r for r in mine if r["kind"] in ("period_match", "unit_match")]
    assert all(r["state"] == CHAIN.UNKNOWN for r in downstream)
    assert all(r.get("unresolvable_without_a_source") for r in downstream)


# ───────────────────────── the replay must carry accusations, not just count them ─────────────────────────

REPLAY = _mod("oqa_occ_replay")


def test_the_replay_carries_every_accusation_with_its_witness():
    """C-2's prediction 5: "any increase must be inspected witness by witness before it is believed".
    A count cannot be inspected, so the witnesses have to be in the artifact."""
    task = OB.derive("What were receipts in fiscal year 1961, in millions of dollars?")
    # a cell whose period is plainly not the asked one, which is what produces a period VIOLATED
    cell = {"value": 500.0, "available_from": 1, "scale_factor": 1.0, "column_basis": "FLAT",
            "row_label": "1999", "column": "Receipts", "col_index": 1,
            "source": {"kind": "grep", "file": "b.txt", "step": 1}, "table_key": "b.txt#1",
            "period": {"year": 1999}, "period_text": "1999", "scale": None}
    chain = {"id": "T", "question": "q", "task": task, "cells": [cell],
             "operands": [{"raw": "500", "value": 500.0, "step": 2, "cells": [cell], "cell": cell,
                           "selection": {"basis": CHAIN.SOLE_CANDIDATE, "n_candidates": 1,
                                         "n_raw_candidates": 1}, "role": "operand"}],
             "computed": [], "conversions": [], "computations": [],
             "answer": "500", "answer_value": 500.0,
             "model": {"version": "t", "sha256": "x", "assumptions": []}}
    out = REPLAY._ledger_states(chain, chain["operands"])
    assert "violated" in out
    assert out["n"] == sum(out["states"].values())
    for v in out["violated"]:
        assert {"kind", "subject", "why", "witness", "supports"} <= set(v)
        assert v["why"], "an accusation without a stated reason cannot be inspected"


def test_the_replay_reports_whether_the_ledger_sizes_are_identical():
    """C-2's prediction 1 is the sharp one, so the artifact must answer it directly rather than
    leaving a reader to add up four deltas."""
    src = open(os.path.join(RESEARCH, "oqa_occ_replay.py"), encoding="utf-8").read()
    assert '"ledger_size_identical"' in src
    assert '"accusations_under_the_gate"' in src
    assert '"every_accusation_with_its_witness"' in src
    # and the committed prediction is quoted in the module, so the measurement names what it tests
    assert "committed_prediction" in src
