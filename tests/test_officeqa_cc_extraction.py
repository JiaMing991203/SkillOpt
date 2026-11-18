"""Amendment B-2: the repaired operand, cell, unit and conversion extraction, and the audit that found it.

WHY EACH TEST IS HERE. The B-1 prototype's one apparent gain was an artifact: on arm C's UID0014 - the
campaign's real 1,000x unit error - it bound the YEARS "1942" and "07" as monetary operands, while the
historical contract report bound the real figures (2,410,259 and 3,471,122r thousands). So this file
tests the extraction on the actual text of that trace and on the specific confusions the instruction
names, and it audits the REUSED machinery (`oqa_lc_cells`, `oqa_lc_chain`) rather than assuming it.

The reused modules are NOT modified: the frozen campaign's `oqa_lc_*` behaviour must stay as it was, so
every correction lives in `oqa_cc_extract` above them. Two tests below pin that boundary.
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
    spec = importlib.util.spec_from_file_location(f"{name}_b2_test",
                                                  os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


X = _mod("oqa_cc_extract")
CELLS = _mod("oqa_lc_cells")
CHAIN = _mod("oqa_lc_chain")
IR = _mod("oqa_cc_ir")
CHK = _mod("oqa_cc_check")


def _contract():
    """The frozen hypothesis's own shape: a table-level scale binding withdrawn by a nearer scale."""
    return IR.contract(
        "scale.binds",
        IR.clause("scale_binds", ["table", "cell", "scale"]),
        [IR.clause("table_states_scale", ["table", "scale"]),
         IR.clause("cell_in_table", ["cell", "table"]),
         IR.clause("nearer_scale_between", ["table", "cell"], value=False)],
        witnesses="unit_match")

# The actor's own words from arm C epoch 3, UID0014 - the message the B-1 prototype read "1942" out of.
UID0014_MESSAGE = ("I have the data needed. Let me extract the values:\n"
                   "**From treasury_bulletin_1942_07.txt (in thousands of dollars):**\n"
                   "- Income and profit taxes, Individual column for year 1942: 2,410,259\n"
                   "- The 1929 figure is 3,471,122r\n")

# A two-column table whose COLUMN header carries its own unit, which is what withdraws a table binding.
TABLE_TEXT = ("Receipts (in thousands of dollars)\n"
              "| Period | Income and profit taxes > Total | Customs > Total (in millions) |\n"
              "| --- | --- | --- |\n"
              "| 1942 | 2,410,259 | 388 |\n"
              "| 1929 | 1,096,000 | 602 |\n")


def _step(text, i=1):
    return {"i": i, "kind": "message", "text": text}


# ───────────────────────── operands ─────────────────────────

def test_a_year_in_the_real_uid0014_message_is_not_a_monetary_operand():
    ops = X.operands(_step(UID0014_MESSAGE))
    by_raw = {o["raw"]: o for o in ops}
    assert by_raw["2,410,259"]["verdict"] == X.MONETARY
    assert by_raw["2,410,259"]["is_operand"] is True
    # the two numbers B-1 bound instead
    assert by_raw["1942"]["is_operand"] is False
    assert by_raw["07"]["is_operand"] is False
    assert by_raw["1929"]["reason"] == "year_in_date_context"
    assert [o["raw"] for o in ops if o["is_operand"]] == ["2,410,259", "3,471,122"]


@pytest.mark.parametrize("text,raw,reason", [
    ("July 1942 shows 07 in the filename", "07", "month_fragment"),
    ("the file treasury_bulletin_1942_07.txt", "1942", "date_fragment"),
    ("1. Read the table\n2. Extract the row", "1", "list_number"),
    ("I read 3 rows and 12 columns", "3", "bare_prose_digit"),
    ("the figure 34,782 3/ is footnoted", "3", "footnote_marker"),
    ("that is 39.31% of the total", "39.31%", "percentage_not_a_monetary_operand"),
    ("as of the year ended 1942 the receipts rose", "1942", "year_in_date_context"),
])
def test_each_confusion_is_rejected_by_name(text, raw, reason):
    got = {o["raw"]: o for o in X.operands(_step(text))}
    assert raw in got, got
    assert got[raw]["verdict"] == X.REJECTED
    assert got[raw]["reason"] == reason


def test_a_monetary_amount_that_happens_to_look_like_a_year_is_still_money():
    """"avoid blanket filtering by digit length". Four digits is not a reason on its own."""
    ops = {o["raw"]: o for o in X.operands(_step("the value is $1,942 million"))}
    assert ops["$1,942"]["verdict"] == X.MONETARY and ops["$1,942"]["is_operand"] is True
    ops2 = {o["raw"]: o for o in X.operands(_step("total receipts of 1942 million dollars"))}
    assert ops2["1942"]["verdict"] == X.MONETARY


def test_an_unsettled_four_digit_number_is_ambiguous_and_is_not_used():
    """Neither established nor excluded. Guessing in either direction is what produced a false finding."""
    ops = {o["raw"]: o for o in X.operands(_step("Total receipts were 1942 in the table"))}
    assert ops["1942"]["verdict"] == X.AMBIGUOUS
    assert ops["1942"]["is_operand"] is False
    opt_in = {o["raw"]: o for o in X.operands(_step("Total receipts were 1942 in the table"),
                                              accept_ambiguous=True)}
    assert opt_in["1942"]["is_operand"] is True, "available, but only when asked for"


# ───────────────────────── cells ─────────────────────────

def _supplied():
    return [{"ref": "page1", "text": TABLE_TEXT}]


def test_a_numeric_row_label_is_not_offered_as_a_data_cell():
    """AUDIT FINDING 1 against the reused machinery: `cells_of` yields column 0, which is the label."""
    raw = CELLS.cells_of(TABLE_TEXT, ("flat_header_authoritative",), source={"kind": "supplied_page"})
    assert 1942.0 in [c["value"] for c in raw], "the reused reader does offer the label as a cell"
    kept, dropped = X.data_cells([], _supplied(), ("flat_header_authoritative",))
    assert 1942.0 not in [c["value"] for c in kept]
    assert any(d["value"] == 1942.0 and d["dropped_because"] == "row_label_not_a_data_cell"
               for d in dropped)
    assert any(d["row_label_is_a_period"] for d in dropped)
    assert 2410259.0 in [c["value"] for c in kept]


def test_a_cell_is_bound_to_the_table_it_came_from_not_to_one_with_the_same_scale():
    """AUDIT FINDING 4. Two tables on a page can state the same scale; identity is not scale equality."""
    two_pages = [{"ref": "p1", "text": TABLE_TEXT}, {"ref": "p2", "text": TABLE_TEXT}]
    tabs = X.tables_in([], two_pages)
    kept, _ = X.data_cells([], two_pages, ("flat_header_authoritative",))
    assert len({t["key"] for t in tabs}) == len(tabs) >= 2
    cell = next(c for c in kept if c["value"] == 2410259.0 and c["source"]["ref"] == "p2")
    table = X.table_of(cell, tabs)
    assert table is not None and table["key"] == cell["table_key"]
    assert table["origin"]["ref"] == "p2"


def test_an_operand_matching_several_disagreeing_cells_is_ambiguous_not_the_first_hit():
    """AUDIT FINDING 3."""
    cells = [
        {"value": 388.0, "raw": "388", "scale": "thousands", "column": "A", "row_label": "1942",
         "table_key": "k1"},
        {"value": 388.0, "raw": "388", "scale": "millions", "column": "B", "row_label": "1942",
         "table_key": "k2"},
    ]
    got = X.match_operand({"value": 388.0, "raw": "388"}, cells)
    assert got["status"] == "ambiguous_cell_match" and got["cell"] is None
    same = X.match_operand({"value": 388.0, "raw": "388"}, [cells[0], dict(cells[0])])
    assert same["status"] == "repeated_identical_cell_match" and same["cell"] is not None
    none = X.match_operand({"value": 1.0, "raw": "1"}, cells)
    assert none["status"] == "no_cell_match"


# ───────────────────────── the nearer unit ─────────────────────────

def test_a_nearer_column_unit_overrides_the_table_wide_unit():
    tabs = X.tables_in([], _supplied())
    kept, _ = X.data_cells([], _supplied(), ("flat_header_authoritative",))
    cell = next(c for c in kept if c["value"] == 388.0)
    near = X.nearer_scale(cell, X.table_of(cell, tabs))
    assert near["state"] == X.PRESENT
    assert near["differing"] == [("column", "millions")]


def test_a_nearer_row_unit_also_overrides_it():
    table = {"scale": "thousands", "key": "k"}
    cell = {"value": 1.0, "column": "Receipts > Total", "column_basis": CELLS.COL_FLAT,
            "row_label": "1930 (millions)", "table_key": "k"}
    assert X.nearer_scale(cell, table)["state"] == X.PRESENT


def test_absence_of_a_nearer_unit_is_asserted_only_when_both_scopes_were_observed():
    table = {"scale": "thousands", "key": "k"}
    seen = {"value": 1.0, "column": "Receipts > Total", "column_basis": CELLS.COL_FLAT,
            "row_label": "1930", "table_key": "k"}
    assert X.nearer_scale(seen, table)["state"] == X.ABSENT


@pytest.mark.parametrize("cell", [
    {"value": 1.0, "column": None, "column_basis": CELLS.COL_UNKNOWN, "row_label": "1930"},
    {"value": 1.0, "column": "", "column_basis": CELLS.COL_FLAT, "row_label": "1930"},
    {"value": 1.0, "column": "Receipts > Total", "column_basis": CELLS.COL_UNKNOWN,
     "row_label": "1930"},
    {"value": 1.0, "column": "Receipts > Total", "column_basis": CELLS.COL_FLAT, "row_label": ""},
])
def test_a_missing_or_unbound_scope_makes_the_nearer_unit_unknown_not_false(cell):
    """THE FIX THE INSTRUCTION NAMES: "`nearer_scale_between=False` must not follow merely from a missing
    column-unit field. Represent it as unknown unless the relevant observed scope supports its absence.\""""
    got = X.nearer_scale(dict(cell, table_key="k"), {"scale": "thousands", "key": "k"})
    assert got["state"] == X.UNKNOWN
    assert "absence cannot be asserted" in got["why"]


def test_cells_without_a_bound_header_are_flagged_rather_than_treated_as_headerless():
    headerless = "| 1942 | 2,410,259 |\n| 1929 | 1,096,000 |\n"
    kept, _ = X.data_cells([], [{"ref": "p", "text": headerless}], ("flat_header_authoritative",))
    assert kept, "the rows are still read"
    assert all(c["header_known"] is False for c in kept)


# ───────────────────────── conversions ─────────────────────────

def test_an_already_compatible_unit_requires_no_factor():
    """AUDIT FINDING 6, and the false alarm it produced on UID0044."""
    op = {"value": 1234.0, "raw": "1,234"}
    got = X.conversion(op, [_step("the answer is 1,234 billion")], X.required_exp("billions",
                                                                                 "billions"))
    assert got["required_exp"] == 0
    assert got["verdict"] == "no_conversion_required"
    assert got["converted_value_used"] is True


@pytest.mark.parametrize("text,verdict,used", [
    ("1,004,461 (in millions) = $1,004.461 billion", "correct_conversion", True),
    ("2,331,274 (in thousands) = $2,331.274 billion", "incorrect_conversion", True),
])
def test_a_written_conversion_is_judged_against_the_factor_its_scales_require(text, verdict, used):
    """Reuses `oqa_lc_chain.conversions`, which is the stronger reader, and keeps its verdict."""
    src = float(text.split()[0].replace(",", ""))
    got = X.conversion({"value": src, "raw": text.split()[0]}, [_step(text)],
                       X.required_exp("thousands", "billions"))
    assert got["verdict"] == verdict, got
    assert got["converted_value_used"] is used


def test_a_stated_factor_that_is_never_applied_is_reported_as_such():
    """AUDIT FINDING 7: "whether the converted value was actually used" is a separate question."""
    op = {"value": 2410259.0, "raw": "2,410,259"}
    got = X.conversion(op, [_step("I will divide by 1,000,000 to get billions")],
                       X.required_exp("thousands", "billions"))
    assert got["verdict"] == "correct_factor_stated" and got["stated_exp"] == -6
    assert got["written_conversions_for_this_operand"] == []


def test_no_factor_stated_is_not_a_violation():
    op = {"value": 2410259.0, "raw": "2,410,259"}
    got = X.conversion(op, [_step("the table is clear enough")],
                       X.required_exp("thousands", "billions"))
    assert got["verdict"] == "no_factor_stated"


def test_an_undetermined_requirement_cannot_make_a_factor_wrong():
    """With no required factor - an unknown or withdrawn binding, or a non-money scale - a stated factor
    is neither right nor wrong, and calling it incorrect would be an accusation with nothing behind it."""
    got = X.conversion({"value": 5.0, "raw": "5"}, [_step("I divided by 1000")], None)
    assert got["verdict"] == "requirement_unknown"
    assert got["stated_exp"] == -3


def test_the_required_exponent_is_the_definition_and_nothing_else():
    assert X.required_exp("thousands", "billions") == -6
    assert X.required_exp("millions", "billions") == -3
    assert X.required_exp("billions", "billions") == 0
    assert X.required_exp("thousands", None) is None
    assert X.required_exp("apples", "billions") is None


# ───────────────────────── the boundary with the frozen modules ─────────────────────────

def test_the_frozen_modules_are_reused_and_not_reimplemented():
    """The campaign's `oqa_lc_*` behaviour must not change, so the corrections live above it."""
    import inspect
    src = inspect.getsource(X)
    assert "import oqa_lc_cells as CELLS" in src and "import oqa_lc_chain as CHAIN" in src
    # the readers the prototype used to duplicate are called, not rewritten
    for name in ("CELLS.tables", "CELLS.cells_of", "CELLS.read_period", "CHAIN.conversions"):
        assert name in src, name


def test_the_checker_uses_the_repaired_extraction():
    import inspect
    CHK = _mod("oqa_cc_check")
    src = inspect.getsource(CHK)
    assert "import oqa_cc_extract as X" in src
    assert "X.monetary_operands" in src and "X.data_cells" in src and "X.nearer_scale" in src


# ───────────────────────── the semantic bundle ─────────────────────────

def test_the_bundle_keeps_itself_when_only_the_provenance_moved(tmp_path, monkeypatch):
    """A different digest is not necessarily a different MEANING.

    The digest covers `configuration`, which records the deployed commit and the Slurm job, so
    re-running the same check from a later commit changes it while every semantic component is
    identical. Job 66352245 exited non-zero for exactly that reason. A real change to the meaning must
    still refuse.
    """
    B = _mod("oqa_cc_bundle")
    IR = _mod("oqa_cc_ir")
    c = IR.contract("scale.binds", IR.clause("scale_binds", ["table", "cell", "scale"]),
                    [IR.clause("table_states_scale", ["table", "scale"]),
                     IR.clause("cell_in_table", ["cell", "table"]),
                     IR.clause("nearer_scale_between", ["table", "cell"], value=False)],
                    witnesses="unit_match")
    m = IR.model([c], epoch=2, scope="test", learned_from=[{"trace_key": "UID1/e2"}])
    model_path = str(tmp_path / "CC_MODEL_v1.json")
    IR.freeze(m, model_path)
    records = tmp_path / "CC_RECORDS_v2.json"
    records.write_text(json.dumps({"records_sha256": "abc", "rule": "OQA-CC-1",
                                   "learning": [], "checking": []}))
    monkeypatch.setenv("OQA_COMMIT", "aaaaaaa")
    first = B.cmd_freeze(model_path, str(records), None, str(tmp_path), "vt")
    assert "written_to" in first

    monkeypatch.setenv("OQA_COMMIT", "bbbbbbb")       # a later commit, identical semantics
    again = B.cmd_freeze(model_path, str(records), None, str(tmp_path), "vt")
    assert again["bundle_sha256"] == first["bundle_sha256"], "the frozen file is kept"
    assert again["provenance_only_difference"]["configuration_now"]["commit"] == "bbbbbbb"

    # and `verify` ignores provenance, so it still verifies
    v = B.cmd_verify(str(tmp_path / "CC_BUNDLE_vt.json"))
    assert v["verifies"] is True and v["changed_components"] == []

    # a real change to the meaning still refuses: edit the recorded manifest
    doc = json.loads((tmp_path / "CC_BUNDLE_vt.json").read_text())
    os.chmod(tmp_path / "CC_BUNDLE_vt.json", 0o644)
    doc["learning_manifest"]["records_sha256"] = "something else"
    (tmp_path / "CC_BUNDLE_vt.json").write_text(json.dumps(doc))
    with pytest.raises(RuntimeError, match="DIFFERENT meaning"):
        B.cmd_freeze(model_path, str(records), None, str(tmp_path), "vt")


def test_configuration_is_not_one_of_the_semantic_sections():
    B = _mod("oqa_cc_bundle")
    assert "configuration" not in B.SEMANTIC_SECTIONS
    for name in ("hypothesis", "vocabulary", "code_sha256", "function_sha256", "stage_definitions",
                 "learning_manifest"):
        assert name in B.SEMANTIC_SECTIONS, name


# ───────────────────────── B-3: the three repairs, each on its real witness ─────────────────────────

# The actor's own sentence from arm B epoch 3, UID0079 - the line that made FOUR YEARS into money.
UID0079_SENTENCE = ("I need to get the budget outlay data from the FFO-4 tables in the December "
                    "Treasury Bulletins from odd years (2011, 2013, 2015, 2017, 2019).")

# UID0014's receipts table in the two renderings the corpus actually uses, trimmed to the header rows
# and three data rows. The oracle page the harness supplies gives the SPLIT form, where the spanning
# parent "Income and profit taxes" is printed once and the column spans are gone; the actor's own
# `read` returns the SAME table in FLAT form, which states a path per column.
UID0014_PAGE_SPLIT = (
    "Internal revenue receipts (in thousands of dollars)\n"
    "| Fiscal year or month | Total | Income and profit taxes | Employment taxes | Carriers' Taxing Act |  |  |  |  |  |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| Total | Individual | Corporation, income and excess profits 2/ | Miscellaneous profits taxes 3/ | Total | Social Security taxes 4/ | Federal Unemployment Tax Act |  |  |  |\n"
    "| 1929 | 2,939,054 | 2,331,274 | 1,095,541 | 1,235,733 | - | - | - | - | - |\n"
    "| 1930 | 3,040,146 | 2,410,259 | 1,146,845 | 1,263,414 | - | - | - | - | - |\n"
    "| 1931 | 2,428,229 | 1,860,040 | 833,648 | 1,026,392 | - | - | - | - | - |\n")
UID0014_OBS_FLAT = (
    "Internal revenue receipts (in thousands of dollars)\n"
    "| Fiscal year or month > Fiscal year or month | Total > Total | Income and profit taxes > Total | "
    "Income and profit taxes > Individual | Income and profit taxes > Corporation, income and excess profits 2/ | "
    "Employment taxes > Miscellaneous profits taxes 3/ | Employment taxes > Total | "
    "Employment taxes > Social Security taxes 4/ | Carriers > Total | Carriers > Act |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| 1929 | 2939054 | 2331274 | 1095541 | 1235733 | - | - | - | - | - |\n"
    "| 1930 | 3040146 | 2410259 | 1146845 | 1263414 | - | - | - | - | - |\n"
    "| 1931 | 2428229 | 1860040 | 833648 | 1026392 | - | - | - | - | - |\n")


def test_b31_a_comma_between_list_items_is_not_a_thousands_separator():
    """The real witness: four of UID0079's "operands" were years admitted by a list comma."""
    got = {o["raw"]: o for o in X.operands(_step(UID0079_SENTENCE))}
    assert "2011," not in got, "a number may not end in a comma"
    assert "2011" in got, got
    for year in ("2011", "2013", "2015", "2017", "2019"):
        assert got[year]["is_operand"] is False, (year, got[year])
        assert got[year]["reason"] != "thousands_grouping"
    assert [o["raw"] for o in X.operands(_step(UID0079_SENTENCE)) if o["is_operand"]] == []


def test_b31_a_real_thousands_group_is_still_money():
    got = {o["raw"]: o for o in X.operands(_step("the figure is 2,410,259 in thousands"))}
    assert got["2,410,259"]["verdict"] == X.MONETARY
    assert got["2,410,259"]["reason"] == "thousands_grouping"


def test_b32_a_flat_header_elsewhere_in_the_record_binds_the_split_rendering():
    """The real witness: UID0014's page gives the split form, the actor's own read gives the flat form.

    Before the repair each text was bound in isolation, so the page's 2,410,259 had NO column and the
    observation's 2410259 had one - and the operand "matched several cells that disagree".
    """
    supplied = [{"ref": "page", "text": UID0014_PAGE_SPLIT}]
    steps = [{"i": 1, "kind": "tool", "tool": "read", "obs": UID0014_OBS_FLAT}]
    refs = X.flat_references(steps, supplied)
    assert [t["header_form"] for t in refs] == ["flat"], "the observation's rendering is the reference"

    cells, _dropped = X.data_cells(steps, supplied, ("flat_header_authoritative",))
    page_cells = [c for c in cells if c["value"] == 2410259.0
                  and (c["source"] or {}).get("kind") == "supplied_page"]
    assert page_cells, "the page's own copy of the figure"
    assert page_cells[0]["column"] == "Income and profit taxes > Total"
    assert page_cells[0]["column_basis"] == CELLS.COL_FLAT
    assert page_cells[0]["header_known"] is True

    op = [o for o in X.operands(_step("the 1930 figure is 2,410,259")) if o["is_operand"]][0]
    assert X.match_operand(op, cells)["status"] == "repeated_identical_cell_match"
    assert X.match_operand(op, cells)["cell"]["scale"] == "thousands"


def test_b32_before_the_repair_the_same_record_gave_an_ambiguous_match():
    """The pre-repair behaviour, kept in the audit tool, so the before/after is a test and not a claim."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "oqa_audit_bind", os.path.join(RESEARCH, "oqa_audit_bind.py"))
    AB = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(AB)
    supplied = [{"ref": "page", "text": UID0014_PAGE_SPLIT}]
    steps = [{"i": 1, "kind": "tool", "tool": "read", "obs": UID0014_OBS_FLAT}]
    iso, _d, _r = AB.data_cells_isolated(steps, supplied, ("flat_header_authoritative",))
    op = [o for o in X.operands(_step("the 1930 figure is 2,410,259")) if o["is_operand"]][0]
    assert X.match_operand(op, iso)["status"] == "ambiguous_cell_match"
    candidates = X.match_operand(op, iso)["candidates"]
    assert sorted({str(c["column"]) for c in candidates}) == ["Income and profit taxes > Total", "None"]


def test_b33_the_step_after_the_operand_message_is_read():
    """It was in neither window: the prefix ended at the operand's message and `beyond(i + 1)` began
    one step later."""
    packet = {
        "id": "SYN0001", "question": "report the total in billions",
        "oracle_pages": [{"ref": "page", "text": UID0014_OBS_FLAT}],
        "steps": [
            {"i": 1, "kind": "tool", "tool": "read", "obs": UID0014_OBS_FLAT},
            {"i": 2, "kind": "message", "text": "The 1930 figure is 2,410,259 thousand."},
            {"i": 3, "kind": "message", "text": "Converting: divide by 1000 gives 2410.259 billion."},
        ],
    }
    model = IR.model([_contract()], epoch=1, scope="synthetic", learned_from=[])
    res = CHK.check_attempt(packet, model)
    preds = [p for p in res["predictions"] if p.get("binding")]
    assert preds, res
    conv = preds[0]["conversion"]
    assert conv["stated_exp"] == -3, conv
    assert conv["verdict"] == "incorrect_factor_stated", conv
    assert preds[0]["resolution"]["state"] == "VIOLATED"
    assert preds[0]["resolution"]["outcome"] == "actor_error"
