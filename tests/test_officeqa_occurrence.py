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
