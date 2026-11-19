"""Amendment B-4: the column LEAF is recoverable from a split header, the column PATH is not.

WHY EACH TEST IS HERE. The instruction behind B-4 names six checks, and each is a test below, plus the
two boundaries the amendment promises not to cross:

  * supported split-header alignment - a child row with real sub-labels yields a leaf;
  * ambiguous alignment - a bare ordinal "(3)" names a position, not a measure, so nothing is recorded;
  * conflicting / repeated headers - a leaf is read from the cell's OWN table, never from another table
    that merely shares a width, and two tables with the same width and different child rows must not
    bleed into each other;
  * duplicate values - the same number in several cells gets each cell's own leaf, and a leaf never
    turns a multi-cell match into an identity;
  * previously working flat headers - a cell that already has a bound header is untouched;
  * preservation of unknowns and source pointers - `column`, `column_basis`, `header_known`, `table_key`
    and `source` are all unchanged, so no binding and no unit question changes state.

And the two boundaries: `oqa_lc_cells` is not modified (B-2's promise), and a leaf must NOT be allowed to
satisfy `_well_bound`, because a leaf is column/series identity and not unit scope.

The fixtures are constructed except where a docstring says the text is quoted from a saved record.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")


def _mod(name):
    for p in (REPO, RESEARCH):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"{name}_b4_test", os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


X = _mod("oqa_cc_extract")
CELLS = _mod("oqa_lc_cells")

ASSUMPTIONS = ("flat_header_authoritative", "grep_cap_not_reached", "table_scale_applies_to_rows")

# Quoted VERBATIM from the supplied oracle page of the saved record UID0019, which is why the leaf
# matters: the parent row's labels are shifted because a two-column span was rendered as one cell.
UID0019_YEN = """[In billions of Japanese yen. Source: Treasury Foreign Currency Reporting]

| Report date | Spot, forward and future contracts | Net options positions | Exchange rate (Japanese yen per U.S. dollar) |  |
|  | Purchased (1) | Sold (2) | (3) | (4) |
| 03/16/16 | 528979 | 533325 | -74 | 114.0 |
"""


def _cells(text, src=None):
    kept, dropped = X.data_cells([], [{"text": text, "ref": (src or "p")}], ASSUMPTIONS)
    return kept


# ───────────────────────── supported split-header alignment ─────────────────────────

def test_a_real_sub_label_yields_a_leaf_on_the_saved_witness():
    cells = _cells(UID0019_YEN)
    by_index = {c["col_index"]: c for c in cells}
    assert by_index[1]["column_leaf"] == "Purchased (1)"
    assert by_index[2]["column_leaf"] == "Sold (2)"
    assert by_index[1]["column_leaf_basis"] == X.LEAF_ONLY
    # and this is the point of the witness: the operand columns are the spot/forward ones
    assert by_index[1]["value"] == 528979.0
    assert by_index[2]["value"] == 533325.0


def test_the_asked_measure_column_is_a_bare_ordinal_so_no_leaf_is_claimed():
    """`(3)` names a column position, not a measure, so nothing is recorded for it."""
    cells = _cells(UID0019_YEN)
    by_index = {c["col_index"]: c for c in cells}
    assert by_index[3]["value"] == -74.0
    assert by_index[3]["column_leaf"] is None
    assert by_index[3]["column_leaf_basis"] is None


# ───────────────────────── ambiguous alignment ─────────────────────────

def test_a_bare_ordinal_child_label_records_nothing():
    t = {"header_form": "split", "child_row": ["", "(1)", "( 2 )", "Real label"]}
    assert X.leaf_of(t, 1) is None
    assert X.leaf_of(t, 2) is None
    assert X.leaf_of(t, 3) == "Real label"


def test_an_empty_child_label_records_nothing():
    t = {"header_form": "split", "child_row": ["", "", "Total"]}
    assert X.leaf_of(t, 1) is None
    assert X.leaf_of(t, 2) == "Total"


def test_a_header_form_with_no_child_row_records_nothing():
    for form in ("flat", "none"):
        assert X.leaf_of({"header_form": form, "child_row": ["a", "b"]}, 1) is None
    assert X.leaf_of(None, 1) is None
    assert X.leaf_of({"header_form": "split", "child_row": ["a"]}, 7) is None


# ───────────────────────── conflicting / repeated headers ─────────────────────────

def test_two_tables_of_the_same_width_do_not_bleed_into_each_other():
    """A leaf is read from the cell's OWN table, re-bound by its own table index."""
    text = """| A | B |
|  | Alpha |
| r1 | 11 |

| A | B |
|  | Beta |
| r2 | 22 |
"""
    cells = _cells(text)
    got = {(c["table_index"], c["value"]): c["column_leaf"] for c in cells}
    assert got[(0, 11.0)] == "Alpha"
    assert got[(1, 22.0)] == "Beta"


def test_a_repeated_leaf_label_is_recorded_per_cell_and_claims_no_identity():
    """The same sub-label on two columns gives both cells that leaf; it does not make them one column."""
    text = """| Year | Total | Total |
|  | Receipts | Outlays |
| 1961 | 500 | 600 |
"""
    cells = _cells(text)
    leaves = sorted((c["col_index"], c["column_leaf"]) for c in cells)
    assert leaves == [(1, "Receipts"), (2, "Outlays")]
    # the PATH is still not claimed for either
    assert all(c["column"] is None or c["column_basis"] == CELLS.COL_UNKNOWN for c in cells)


# ───────────────────────── duplicate values ─────────────────────────

def test_the_same_number_in_two_columns_keeps_each_columns_own_leaf():
    text = """| Year | Group |  |
|  | Purchased | Sold |
| 1961 | 13 | 13 |
"""
    cells = [c for c in _cells(text) if c["value"] == 13.0]
    assert len(cells) == 2
    assert sorted(c["column_leaf"] for c in cells) == ["Purchased", "Sold"]
    # a leaf must never be read as resolving which of the two a written "13" came from
    assert len({c["col_index"] for c in cells}) == 2


# ───────────────────────── previously working flat headers ─────────────────────────

def test_a_cell_that_already_has_a_bound_header_is_untouched():
    flat = """| Year | Receipts > Total | Receipts > Net |
| 1961 | 500 | 400 |
"""
    cells = _cells(flat)
    assert cells, "the flat table should still yield cells"
    for c in cells:
        assert c["header_known"] is True
        assert c["column_basis"] == CELLS.COL_FLAT
        # no leaf is recorded where the path is already known
        assert c["column_leaf"] is None


# ───────────────────────── unknowns and source pointers preserved ─────────────────────────

def test_the_leaf_changes_no_binding_field_and_keeps_the_source_pointer():
    cells = _cells(UID0019_YEN, src="page-54")
    c = next(c for c in cells if c["col_index"] == 1)
    assert c["header_known"] is False                      # unit scope is still unknown
    assert c["column_basis"] == CELLS.COL_UNKNOWN          # the path is still unknown
    assert c["column"] is None
    assert c["source"]["kind"] == "supplied_page"          # provenance preserved
    assert c["source"]["ref"] == "page-54"
    assert c["table_key"]                                   # table identity preserved
    # The table states "[In billions of Japanese yen]", which is NOT a dollar scale: `_SCALE` requires
    # "of dollars". So `table_states_scale` is correctly undetermined here, which is a THIRD independent
    # reason the frozen unit hypothesis is silent on this record, beside the unknown path and the
    # unknown unit scope. The leaf does not change that.
    assert c["scale"] is None


def test_every_cell_carries_the_two_new_fields_even_when_empty():
    """Absence is recorded as None, so a reader can tell 'no leaf' from 'field missing'."""
    for text in (UID0019_YEN, "| Year | Receipts > Total |\n| 1961 | 500 |\n"):
        for c in _cells(text):
            assert "column_leaf" in c and "column_leaf_basis" in c


# ───────────────────────── the two boundaries the amendment promises ─────────────────────────

def test_the_frozen_cell_module_is_not_modified():
    """B-2's promise: every correction lives ABOVE oqa_lc_cells, which stays byte-identical."""
    src = open(os.path.join(RESEARCH, "oqa_lc_cells.py"), encoding="utf-8").read()
    assert "LEAF_ONLY" not in src
    assert "column_leaf" not in src
    # and the frozen module's own bases are unchanged
    assert (CELLS.COL_FLAT, CELLS.COL_INDEX_MERGE, CELLS.COL_UNKNOWN) == ("FLAT", "INDEX_MERGE", "UNKNOWN")


def test_a_leaf_can_never_satisfy_the_well_bound_rule():
    """A leaf is column/series identity, not unit scope, so it must not make a finding well bound."""
    run = _mod("oqa_cc_run")
    finding = {"operand_verdict": "MONETARY", "match_status": "unique_cell_match",
               "binding": "HOLDS",
               "cell": {"col_index": 2, "header_known": False,
                        "column_leaf": "Sold (2)", "column_leaf_basis": X.LEAF_ONLY}}
    assert run._well_bound(finding) is False
    finding["cell"]["header_known"] = True
    assert run._well_bound(finding) is True


def test_the_index_alignment_assumption_is_still_not_in_force():
    """The span reading stays refused: it was wrong at 1 of 10 columns where it could be tested."""
    t = {"header_form": "split", "width": 3,
         "parent_row": ["Year", "Group", ""], "child_row": ["", "Purchased", "Sold"], "flat": None}
    cols = CELLS.bind_columns(t, ASSUMPTIONS)
    assert all(c["basis"] == CELLS.COL_UNKNOWN for c in cols)
    with_assumption = CELLS.bind_columns(t, tuple(ASSUMPTIONS) + ("index_alignment",))
    assert all(c["basis"] == CELLS.COL_INDEX_MERGE for c in with_assumption)


# ───────────────────────── the leaf must be RECORDED, not merely computed ─────────────────────────

def test_the_leaf_is_carried_into_both_witness_projections():
    """A repair whose output appears in no artifact is not evidence.

    The first replay measured `operands_whose_cell_has_a_leaf: 0` not because no leaf was recovered but
    because both downstream projections copy a fixed key list. Both now carry the two B-4 fields, and
    both still decide the unit question on `header_known` alone.
    """
    for name in ("oqa_cc_check", "oqa_audit_bind"):
        src = open(os.path.join(RESEARCH, f"{name}.py"), encoding="utf-8").read()
        assert '"column_leaf"' in src, name
        assert '"column_leaf_basis"' in src, name


def test_carrying_the_leaf_does_not_add_it_to_any_decision():
    """It is copied into the witness and read by no condition, cause or verdict."""
    import re as _re
    for name in ("oqa_cc_check", "oqa_audit_bind", "oqa_cc_run"):
        src = open(os.path.join(RESEARCH, f"{name}.py"), encoding="utf-8").read()
        for m in _re.finditer(r"[^\n]*column_leaf[^\n]*", src):
            line = m.group(0)
            # the only permitted appearances are the projection key lists and comments
            assert ('for k in (' in line or line.strip().startswith("#")
                    or '"column_leaf"' in line or '"column_leaf_basis"' in line), (name, line)
            assert "if " not in line and "==" not in line, (name, line)
