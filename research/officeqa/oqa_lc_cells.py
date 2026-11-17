"""OfficeQA domain adapter: pipe-delimited tables -> cells bound to a column path, a period and a unit.

Why this module exists. The learned-contract architecture needs a state estimate whose facts carry a source, a
validity and a basis (design v0.1 section 4.A). `oqa_check.build_ledger` already records a number's file, page and
line, and the years that happen to appear on the same line, but it records neither the ROW the number sits in nor
the COLUMN it belongs to, so it cannot say which series or which month a figure comes from. The contract audit
(docs/research/OFFICEQA_CONTRACT_AUDIT.md) named that as the reason `operand_period_mismatch` can never fire on a
monthly table. This module supplies the missing binding.

The two header forms, which is the whole difficulty.

  * The TRANSFORMED documents the tools read state one header row with a column PATH per cell:
        | End of fiscal year or month > End of fiscal year or month | Coin > Total coin | Coin > Standard silver ...
    Call this the FLAT form. It is the document's own statement of each column's path, so under the assumption
    `flat_header_authoritative` it is usable as ground truth.

  * The ORACLE PARSED PAGES the harness puts in the actor's first message render the same table as a parent row,
    a separator, and a sub-label row:
        | End of fiscal year or month | Coin | Money in circulation per capita (in dollars) 2/ |  |  |  |
        | --- | --- | --- | --- | --- | --- |
        | Total coin | Standard silver dollars | Subsidiary silver | Minor coin |  |  |
    Call this the SPLIT form. The column spans are gone: "Coin" appears once at index 1 although it covers four
    columns, and the sub-label row has dropped the blank that belongs under the row-label column. Merging the two
    rows by cell index therefore binds the wrong column, which is what `index_alignment` claims and what the
    counterexample in docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md section 4 refutes.

So a column binding carries its own basis, and the honest value when the evidence does not determine it is UNKNOWN
(amendment A4 3.1: "an unknown is never turned into false"):
    FLAT         read from a flattened header cell of this table
    INDEX_MERGE  the split form merged by cell index, licensed only by the `index_alignment` assumption
    UNKNOWN      no flattened header for this table and `index_alignment` is not assumed

Nothing here reads the answer key, the evaluator, the skill or any held-out data. Every function takes text that
the actor itself was given or received.
"""
from __future__ import annotations

import re

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
}
MONTH_NAME = {v: k.capitalize() for k, v in MONTHS.items() if len(k) > 3}

_ROW = re.compile(r"^\s*(?:\d+:\s*)?\|(.*)\|\s*$")          # a grep line may carry a "<line no>: " prefix
_LINE_PREFIX = re.compile(r"^\s*(\d+):\s")
_SEP_CELL = re.compile(r"^:?-{2,}:?$")
_SCALE = re.compile(r"\(?\s*in\s+(millions?|billions?|thousands?)\s+of\s+dollars", re.I)
_UNNAMED = re.compile(r"^Unnamed:\s*\d+_level_\d+$")
_NUMBER = re.compile(r"^[-+]?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)$")
_FOOTNOTE = re.compile(r"\s*\d+/\s*$|\s*[a-z]\s*$")          # "34,782 3/", "181.79r"
_SCALE_FACTOR = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}

COL_FLAT, COL_INDEX_MERGE, COL_UNKNOWN = "FLAT", "INDEX_MERGE", "UNKNOWN"


def canon_scale(word: str) -> str:
    w = (word or "").lower().rstrip("s")
    return {"million": "millions", "billion": "billions", "thousand": "thousands"}.get(w, w)


def scale_factor(frm: str, to: str) -> float | None:
    """How many `to`-units one `frm`-unit is. None when either scale is not a money scale."""
    if frm in _SCALE_FACTOR and to in _SCALE_FACTOR:
        return _SCALE_FACTOR[frm] / _SCALE_FACTOR[to]
    return None


# ── row reading ─────────────────────────────────────────────────────────────────

def split_row(line: str) -> list | None:
    """A pipe row's cells, or None when the line is not a pipe row. Strips a grep "<n>: " prefix."""
    m = _ROW.match(line or "")
    if not m:
        return None
    cells = [c.strip() for c in m.group(1).split("|")]
    return cells if len(cells) >= 2 else None


def line_number_of(line: str) -> int | None:
    m = _LINE_PREFIX.match(line or "")
    return int(m.group(1)) if m else None


def is_separator(cells: list) -> bool:
    return bool(cells) and all(_SEP_CELL.match(c or "") for c in cells)


def cell_number(cell: str) -> float | None:
    """The numeric value of a data cell, with a trailing footnote marker removed. None for "*", "-" and text."""
    t = _FOOTNOTE.sub("", (cell or "").strip())
    t = t.strip()
    if not _NUMBER.match(t):
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def is_data_row(cells: list) -> bool:
    """A data row has a label in cell 0 and at least one numeric cell after it."""
    return bool(cells) and any(cell_number(c) is not None for c in cells[1:])


def read_period(label: str) -> dict | None:
    """A row label -> {"year", "month", "text"}; month-only labels get year None (the caller may inherit it)."""
    t = (label or "").strip()
    if not t:
        return None
    m = re.match(r"^(\d{4})\s*[-–]\s*([A-Za-z]{3,9})\.?$", t)
    if m and MONTHS.get(m.group(2).lower()):
        return {"year": int(m.group(1)), "month": MONTHS[m.group(2).lower()], "text": t}
    m = re.match(r"^([A-Za-z]{3,9})\.?\s*[-–]?\s*(\d{4})$", t)
    if m and MONTHS.get(m.group(1).lower()):
        return {"year": int(m.group(2)), "month": MONTHS[m.group(1).lower()], "text": t}
    if re.fullmatch(r"(?:18|19|20)\d{2}", t):
        return {"year": int(t), "month": None, "text": t}
    if MONTHS.get(t.lower().rstrip(".")):
        return {"year": None, "month": MONTHS[t.lower().rstrip(".")], "text": t}
    return None


def period_text(p: dict | None) -> str:
    if not p:
        return "UNKNOWN"
    if p.get("month") and p.get("year"):
        return f"{MONTH_NAME.get(p['month'], p['month'])} {p['year']}"
    if p.get("year"):
        return str(p["year"])
    return f"{MONTH_NAME.get(p.get('month'), '?')} (year unknown)"


# ── header reading ──────────────────────────────────────────────────────────────

def _flat_cell(cell: str) -> dict:
    """A flattened header cell "Parent > Child" -> {"parent", "child"}; repeats and Unnamed levels collapse."""
    parts = [p.strip() for p in (cell or "").split(">")]
    parts = ["" if _UNNAMED.match(p) else p for p in parts]
    parts = [p for p in parts if p]
    seen: list[str] = []
    for p in parts:
        if not seen or seen[-1] != p:
            seen.append(p)
    if not seen:
        return {"parent": "", "child": ""}
    if len(seen) == 1:
        return {"parent": seen[0], "child": ""}
    return {"parent": " > ".join(seen[:-1]), "child": seen[-1]}


def looks_flat(cells: list) -> bool:
    """A flattened header row states a path in at least two cells."""
    return sum(1 for c in cells if ">" in (c or "")) >= 2


# ── tables ──────────────────────────────────────────────────────────────────────

def tables(text: str) -> list:
    """Every pipe table in `text`, with its header form, columns and data rows.

    A block of consecutive pipe rows is split into TABLES at every non-data row that follows a data row. That split
    is necessary, not cosmetic: the oracle page of UID0026 renders the "Money in Circulation" paper-money table and
    the coin table as 44 consecutive pipe rows with no blank line between them, and treating them as one table
    binds the coin figures (1,858; 223; ...) to the paper-money columns. A wrong binding is worse than none.

    Each table's width is trimmed to its last non-blank cell, because the parser pads every row of a page to the
    widest table on it (the coin rows above carry four trailing empty cells).

    The scale is the last "(in <scale> of dollars)" seen above the block.
    """
    out: list[dict] = []
    lines = (text or "").splitlines()
    scale = None
    i = 0
    while i < len(lines):
        cells = split_row(lines[i])
        if cells is None:
            m = _SCALE.search(lines[i])
            if m:
                scale = canon_scale(m.group(1))
            i += 1
            continue
        block = []
        while i < len(lines):
            cs = split_row(lines[i])
            if cs is None:
                break
            block.append((i, lines[i], cs))
            i += 1
        for segment in _segments(block):
            out.append(_build(segment, scale))
    return [t for t in out if t["rows"] or t["header_lines"]]


def _segments(block: list) -> list:
    """Split one block of pipe rows wherever a header-like row follows a data row."""
    segments: list[list] = []
    current: list = []
    seen_data = False
    for item in block:
        _, _, cs = item
        header_like = not is_data_row(cs) and not is_separator(cs)
        if header_like and seen_data:
            segments.append(current)
            current, seen_data = [], False
        current.append(item)
        seen_data = seen_data or is_data_row(cs)
    if current:
        segments.append(current)
    return segments


def _trim_width(block: list) -> int:
    """1 + the largest index at which any row of this table has a non-blank cell (at least 2)."""
    best = 1
    for _, _, cs in block:
        if is_separator(cs):
            continue
        for k, c in enumerate(cs):
            if (c or "").strip():
                best = max(best, k + 1)
    return max(best, 2)


def _build(block: list, scale: str | None) -> dict:
    width = _trim_width(block)
    flat_row = next(((n, cs) for n, _, cs in block if looks_flat(cs) and not is_data_row(cs)), None)
    header_rows = [(n, cs) for n, _, cs in block
                   if not is_separator(cs) and not is_data_row(cs) and (flat_row is None or n != flat_row[0])]
    data = [(n, ln, cs) for n, ln, cs in block if is_data_row(cs) and not is_separator(cs)]

    form = "none"
    parent: list[str] = [""] * width
    child: list[str] = [""] * width
    flat: list[dict] | None = None
    if flat_row is not None:
        form = "flat"
        flat = ([_flat_cell(c) for c in flat_row[1]] + [{"parent": "", "child": ""}] * width)[:width]
        parent = [f["parent"] for f in flat]
        child = [f["child"] for f in flat]
    elif header_rows:
        form = "split" if len(header_rows) >= 2 else "single"
        parent = (list(header_rows[0][1]) + [""] * width)[:width]
        if len(header_rows) >= 2:
            child = (list(header_rows[1][1]) + [""] * width)[:width]

    rows = []
    carried_year = None
    for n, ln, cs in data:
        cs = cs[:width]
        label = cs[0] if cs else ""
        p = read_period(label)
        if p and p.get("year") is None and p.get("month") and carried_year is not None:
            p = dict(p, year=carried_year, inherited_year=True)
        if p and p.get("year") is not None and not p.get("inherited_year"):
            carried_year = p["year"]
        rows.append({"line_in_block": n, "doc_line": line_number_of(ln), "label": label, "period": p,
                     "cells": [{"index": k, "raw": c, "value": cell_number(c)} for k, c in enumerate(cs)],
                     "width": len(cs)})
    return {
        "width": width, "header_form": form, "scale": scale,
        "parent_row": parent, "child_row": child, "flat": flat,
        "header_lines": [n for n, _ in header_rows] + ([flat_row[0]] if flat_row else []),
        "rows": rows,
        "fingerprints": sorted({_fingerprint(r) for r in rows if _fingerprint(r)}),
    }


def _fingerprint(row: dict) -> str:
    """A data row's identity across renderings: its label plus its numeric values, commas stripped.

    Two tables that share a fingerprint are the same table: the oracle page writes "1,858" where the transformed
    document writes "1858", and the fingerprint is equal either way.
    """
    vals = [c["value"] for c in row["cells"][1:] if c["value"] is not None]
    if not row["label"] or len(vals) < 3:
        return ""
    return row["label"].strip().lower() + "|" + ",".join(f"{v:g}" for v in vals)


def pair_tables(split_tables: list, flat_tables: list) -> list:
    """(split table, flat table) pairs that share a data row, so the flat header is ground truth for the split one."""
    pairs = []
    for st in split_tables:
        if st["header_form"] not in ("split", "single"):
            continue
        for ft in flat_tables:
            if ft["header_form"] != "flat":
                continue
            if ft["width"] != st["width"]:
                continue   # a shared row in a table of another width is not the same table
            if set(st["fingerprints"]) & set(ft["fingerprints"]):
                pairs.append((st, ft))
                break
    return pairs


# ── column binding ──────────────────────────────────────────────────────────────

def bind_columns(table: dict, assumptions, reference: dict | None = None) -> list:
    """Column paths for one table, each with its basis.

    `reference` is a flat-form table paired with this one (pair_tables). When present its header is ground truth,
    which is what the `flat_header_authoritative` assumption licenses.
    """
    A = set(assumptions or [])
    width = table["width"]
    cols: list[dict] = []
    if table["header_form"] == "flat" and "flat_header_authoritative" in A:
        for k in range(width):
            f = (table["flat"] or [{}] * width)[k] if table["flat"] else {}
            cols.append({"index": k, "parent": f.get("parent", ""), "child": f.get("child", ""),
                         "path": _path(f.get("parent", ""), f.get("child", "")), "basis": COL_FLAT})
        return cols
    if reference is not None and "flat_header_authoritative" in A and reference["width"] == width:
        for k in range(width):
            f = (reference["flat"] or [{}] * width)[k]
            cols.append({"index": k, "parent": f.get("parent", ""), "child": f.get("child", ""),
                         "path": _path(f.get("parent", ""), f.get("child", "")), "basis": COL_FLAT})
        return cols
    if table["header_form"] in ("split", "single") and "index_alignment" in A:
        for k in range(width):
            p, c = table["parent_row"][k], table["child_row"][k]
            cols.append({"index": k, "parent": p, "child": c, "path": _path(p, c), "basis": COL_INDEX_MERGE})
        return cols
    for k in range(width):
        cols.append({"index": k, "parent": "", "child": "", "path": None, "basis": COL_UNKNOWN})
    return cols


def _path(parent: str, child: str) -> str:
    parts = [x for x in ((parent or "").strip(), (child or "").strip()) if x]
    return " > ".join(parts)


def binding_disagreements(split_table: dict, flat_table: dict, level: str = "leaf") -> list:
    """Columns where merging the split form by index disagrees with the paired flat header.

    `level` picks which claim is being tested, and the distinction matters because the two failures are different
    mistakes:

      "leaf"  does the split form name the right COLUMN? The leaf is the innermost label ("Gold certificates"),
              which is what identifies the series a figure belongs to. This is the evaluator of the atom
              `col_leaf_sound`.
      "path"  does the split form recover the whole path, parent included ("Paper money 2/ > Gold certificates")?
              The split rendering prints a spanning parent once, at its first column, so columns 3-8 of the
              paper-money table lose "Paper money 2/" even though their leaf is right. This is the evaluator of
              `col_path_sound`, and it is a loss of qualification, not a wrong binding.

    A flattened cell "Unnamed: 9_level_0 > National bank notes" collapses to the single name "National bank
    notes"; at "leaf" level that is compared with the split form's sub-label, and a flat cell with no sub-label
    ("End of fiscal year or month > End of fiscal year or month") has its parent as its leaf.
    """
    out = []
    width = min(split_table["width"], flat_table["width"])
    for k in range(width):
        merged_child = (split_table["child_row"][k] or "").strip()
        merged_parent = (split_table["parent_row"][k] or "").strip()
        fcell = ((flat_table["flat"] or [{}] * width)[k] or {})
        flat_child = (fcell.get("child") or "").strip()
        flat_parent = (fcell.get("parent") or "").strip()
        merged_path, flat_path = _path(merged_parent, merged_child), _path(flat_parent, flat_child)
        if level == "path":
            same = _norm(merged_path) == _norm(flat_path)
        else:
            same = _norm(merged_child or merged_parent) == _norm(flat_child or flat_parent)
        if not same:
            out.append({"index": k, "level": level,
                        "merged_leaf": merged_child or merged_parent, "flat_leaf": flat_child or flat_parent,
                        "flat_path": flat_path, "merged_path": merged_path})
    return out


_LABEL_FOOTNOTE = re.compile(r"\s*\d+/\s*$")


def _norm(s: str) -> str:
    """Compare two header labels up to Treasury's own footnote markers.

    The same statistical column is footnoted differently in different bulletins: the per-capita column of the
    "Money in Circulation" table is "... (in dollars) 2/" in the January 1963 issue and "... (in dollars) 7/" in
    the January 1962 one. A trailing "<digits>/" is therefore dropped before comparing. Parenthesised COLUMN
    NUMBERS are not: "Net income taxes (8)" and "Net income taxes (11)" are different columns of the FFO-2 table,
    so digits are not stripped in general.
    """
    t = _LABEL_FOOTNOTE.sub("", (s or "").strip())
    return re.sub(r"[^a-z0-9]+", "", t.lower())


# ── cells of a whole text ───────────────────────────────────────────────────────

def cells_of(text: str, assumptions, references: list | None = None, source: dict | None = None) -> list:
    """Every numeric cell in `text`, bound to a column path, a period and a scale.

    Returns a flat list of cell facts:
        {"value", "raw", "row_label", "period", "period_text", "column", "column_basis", "scale",
         "table_index", "row_index", "col_index", "doc_line", "source"}
    `references` are flat-form tables from elsewhere in the same record (for example the tool observations) that may
    pair with a split-form table here.
    """
    refs = [t for t in (references or []) if t["header_form"] == "flat"]
    out: list[dict] = []
    for ti, table in enumerate(tables(text)):
        ref = None
        for pair in pair_tables([table], refs):
            ref = pair[1]
            break
        cols = bind_columns(table, assumptions, ref)
        for ri, row in enumerate(table["rows"]):
            for cell in row["cells"]:
                if cell["value"] is None:
                    continue
                col = cols[cell["index"]] if cell["index"] < len(cols) else {"path": None, "basis": COL_UNKNOWN}
                out.append({
                    "value": cell["value"], "raw": cell["raw"], "row_label": row["label"],
                    "period": row["period"], "period_text": period_text(row["period"]),
                    "column": col.get("path"), "column_basis": col.get("basis"),
                    "scale": table["scale"], "table_index": ti, "row_index": ri, "col_index": cell["index"],
                    "doc_line": row.get("doc_line"), "source": dict(source or {}),
                })
    return out


# ── the 0-ary atoms of one tool step ────────────────────────────────────────────

def atom_values(step: dict, assumptions, supplied_tables: list | None = None) -> dict:
    """The value of every oqa_lc_vocab atom on one tool step's observation: True / False / None (unknown).

    None means "this observation does not determine the atom", and no finding may be issued from it.

    Which way round the column check runs. `supplied_tables` are the SPLIT-form tables of the oracle parsed pages
    the harness put in the actor's first message; those are the bindings under test. The flattened headers in THIS
    step's observation are the ground truth that adjudicates them. So `col_leaf_sound` / `col_path_sound` are
    properties of the observation the step returned - whether it confirms or refutes the index merge of a page the
    actor already held - and that is what makes the prediction prefix-only: at PRE the checker has the page and the
    model but not the observation that decides the question.
    """
    obs = (step or {}).get("obs") or ""
    tool = (step or {}).get("tool")
    args = (step or {}).get("args") or {}
    ts = tables(obs)
    vals: dict = {a: None for a in (
        "has_table", "header_flat", "header_split", "width_uniform", "sub_row_aligned",
        "col_leaf_sound", "col_path_sound", "row_period_bound", "scale_stated", "cap_reached",
        "pattern_in_every_line")}

    lines = [ln for ln in obs.splitlines() if ln.strip()]
    if tool == "grep" and not obs.startswith("["):
        vals["cap_reached"] = len(lines) >= 20
        pattern = str(args.get("pattern") or "").lower()
        if pattern and not re.search(r"[|*+?\[\](){}^$\\]", pattern):
            vals["pattern_in_every_line"] = all(pattern in ln.lower() for ln in lines)
    elif tool in ("read", "glob"):
        vals["cap_reached"] = False

    vals["has_table"] = bool(ts)
    if not ts:
        return vals

    vals["header_flat"] = any(t["header_form"] == "flat" for t in ts)
    vals["header_split"] = any(t["header_form"] == "split" for t in ts)
    vals["scale_stated"] = any(t["scale"] for t in ts)
    widths = [(t, r) for t in ts for r in t["rows"]]
    vals["width_uniform"] = all(r["width"] == t["width"] for t, r in widths) if widths else None
    splits = [t for t in ts if t["header_form"] == "split"]
    vals["sub_row_aligned"] = (all(len(t["child_row"]) == t["width"] for t in splits) if splits else None)
    labelled = [r for _, r in widths if r["label"]]
    vals["row_period_bound"] = (all(r["period"] is not None for r in labelled) if labelled else None)

    truth = [t for t in ts if t["header_form"] == "flat"]
    under_test = [t for t in (supplied_tables or []) if t["header_form"] in ("split", "single")] + splits
    pairs = pair_tables(under_test, truth)
    if pairs:
        vals["col_leaf_sound"] = not any(binding_disagreements(st, ft, "leaf") for st, ft in pairs)
        vals["col_path_sound"] = not any(binding_disagreements(st, ft, "path") for st, ft in pairs)
    return vals


def disagreement_witnesses(step: dict, supplied_tables: list | None = None, level: str = "leaf") -> list:
    """The concrete columns that make `col_leaf_sound` / `col_path_sound` false here (a counterexample witness)."""
    ts = tables((step or {}).get("obs") or "")
    truth = [t for t in ts if t["header_form"] == "flat"]
    under_test = [t for t in (supplied_tables or []) if t["header_form"] in ("split", "single")] + \
                 [t for t in ts if t["header_form"] == "split"]
    out = []
    for st, ft in pair_tables(under_test, truth):
        for d in binding_disagreements(st, ft, level):
            out.append(dict(d, flat_rows=len(ft["rows"]), split_rows=len(st["rows"]), width=st["width"]))
    return out
