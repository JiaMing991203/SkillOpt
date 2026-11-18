#!/usr/bin/env python3
"""Operand, cell, unit and conversion extraction for the connected contract. Amendment B-2, rule OQA-CC-2.

WHY THIS FILE EXISTS. The B-1 prototype carried its own operand reader and its own cell matcher, and both
were weaker than the ones `oqa_lc_chain` / `oqa_lc_cells` already had. The consequence is on the record:
on arm C's UID0014 - the campaign's actual 1,000x unit error - the prototype bound the YEARS "1942" and
"07" while the historical contract report bound the real money operands (2,410,259 and 3,471,122r
thousands). It got the requirement right and the operand wrong, which is the worst combination, because
the report then looks like a finding.

SO THIS MODULE REUSES THE STRONGER MACHINERY AND AUDITS IT, rather than trusting it:

  * `oqa_lc_cells.tables / cells_of / bind_columns / cell_number / read_period` are reused for table,
    header, period and cell reading. They are NOT modified: the frozen campaign's `oqa_lc_*` behaviour
    must stay byte-identical, so every correction lives here, in the layer above.
  * `oqa_lc_chain.conversions` is reused for conversions the actor WROTE DOWN ("1,004,461 (in millions)
    = $1,004.461 billion"), including its CARRIED-source-scale caveat.

THE AUDIT FINDINGS the reuse required, each with a test:

  1. `cells_of` returns the row-label column as a cell whenever the label is numeric, so a year used as
     a row label ("| 1942 | 2,410,259 | ...") is offered as a data cell. Dropped here by column index.
  2. A number in an actor message is not an operand. Years, dates, month fragments, list numbers,
     footnote markers, percentages and bare prose digits are rejected BY NAME, and a 4-digit number is
     NOT rejected merely for having four digits: with a currency or scale marker it is money, with date
     context it is a date, and with neither it is AMBIGUOUS and reported as such.
  3. An operand that matches several cells which disagree about scale, column or row is an AMBIGUOUS
     match, not the first hit.
  4. A cell is bound to the table it CAME FROM, by (source, table index), not to any table that happens
     to state the same scale.
  5. `nearer_scale_between` must be UNKNOWN, not False, when the scope that would carry the nearer
     annotation was never observed - a cell with no bound column header cannot witness the ABSENCE of a
     column unit.
  6. When the bound scale already equals the asked scale the required factor is 10^0: no conversion is
     needed and an explicit multiplication by one is not required. Reporting that as OUTSTANDING is a
     false alarm.
  7. A conversion is judged on three separate questions: was a factor stated, was it the right one, and
     was the converted value actually used.

Nothing here reads the answer key, the scorer, the skill or any held-out material.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_lc_cells as CELLS  # noqa: E402
import oqa_lc_chain as CHAIN  # noqa: E402

# tri-state for every "is the nearer annotation there?" question
PRESENT, ABSENT, UNKNOWN = "PRESENT", "ABSENT", "UNKNOWN"

MONETARY, AMBIGUOUS, REJECTED = "MONETARY", "AMBIGUOUS", "REJECTED"

SCALE_EXP = {"units": 0, "thousands": 3, "millions": 6, "billions": 9}

_NUM = re.compile(r"(?<![\d.,])-?\$?\s?\d[\d,]*(?:\.\d+)?%?")
_SCALE_WORD = re.compile(r"\b(thousand|million|billion)s?\b", re.I)
_MONTH = "|".join(sorted(CELLS.MONTHS, key=len, reverse=True))
_MONTH_RE = re.compile(rf"\b({_MONTH})\b\.?", re.I)
_DOLLARS = re.compile(r"\b(dollars?|usd)\b", re.I)
_YEAR_WORD = re.compile(r"\b(year|fy|fiscal|calendar|as of|ended|ending|through|month)\b", re.I)
_DATE_SHAPE = re.compile(r"\d{4}[-_/]\d{1,2}|\d{1,2}[-_/]\d{4}|\b\d{4}\s*[-–]\s*\d{1,2}\b")
_LIST_LEAD = re.compile(r"(?:^|\n)\s*(?:[-*]\s*)?\d+[.)]\s")
_FOOTNOTE = re.compile(r"\d+/|\d+(?:\.\d+)?r\b")
_FACTOR = re.compile(r"(?:divide[d]?\s+by|/|\*|multipl(?:y|ied)\s+by|times)\s*"
                     r"(1[,\d]*(?:\.\d+)?|0\.0+1|1e-?\d+)", re.I)


def _value(raw: str):
    t = raw.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


# ───────────────────────── 1. operands the actor wrote ─────────────────────────

def classify_number(text: str, start: int, end: int) -> dict:
    """Is this number in this context a monetary operand? The reason is always recorded.

    A classification is one of MONETARY, AMBIGUOUS or REJECTED(reason). AMBIGUOUS exists because the
    alternative is to guess: "1942" with a dollar sign is money, "1942" after "for the year" is a year,
    and "1942" alone in a sentence is neither established nor excluded. Treating the third case as money
    is what bound years in B-1; treating it as a year would discard a real four-digit amount.
    """
    raw = text[start:end]
    lo, hi = max(0, start - 60), min(len(text), end + 60)
    before, after = text[lo:start], text[end:hi]
    window = before + raw + after
    digits = raw.replace("$", "").replace(",", "").replace("%", "").strip()
    grouped = "," in raw
    has_decimal = "." in digits
    # TWO STRENGTHS OF EVIDENCE, and the distinction is load-bearing. A year-shaped number must not
    # become "money" because the sentence mentions dollars somewhere - the very line that bound YEARS in
    # B-1 reads "From treasury_bulletin_1942_07.txt (in thousands of dollars): 2,410,259 for 1942", so
    # every number on it sits within 60 characters of the word "dollars".
    tight_marker = bool(re.search(r"\$", raw) or re.search(r"\$\s?$", before[-3:])
                        or _SCALE_WORD.search(after[:14]) or _DOLLARS.search(after[:14]))
    loose_marker = bool(_SCALE_WORD.search(window) or _DOLLARS.search(window)
                        or re.search(r"\$", window))
    monetary_marker = tight_marker or loose_marker

    if raw.strip().endswith("%"):
        return {"verdict": REJECTED, "reason": "percentage_not_a_monetary_operand"}
    if _FOOTNOTE.fullmatch(text[start:min(len(text), end + 2)].strip()):
        return {"verdict": REJECTED, "reason": "footnote_marker"}
    for m in _LIST_LEAD.finditer(text):
        if m.start() <= start < m.end():
            return {"verdict": REJECTED, "reason": "list_number"}
    if _DATE_SHAPE.search(text[max(0, start - 5):min(len(text), end + 5)]):
        return {"verdict": REJECTED, "reason": "date_fragment"}
    month_near = _MONTH_RE.search(before[-18:]) or _MONTH_RE.search(after[:18])
    if month_near and not monetary_marker:
        if len(digits) <= 2:
            return {"verdict": REJECTED, "reason": "month_fragment"}
        return {"verdict": REJECTED, "reason": "date_fragment"}
    year_shape = bool(re.fullmatch(r"(?:18|19|20)\d{2}", digits))
    if year_shape and not tight_marker and not grouped and not has_decimal:
        # FOUR DIGITS IS NOT A REASON ON ITS OWN. A Treasury figure of $1,942 million is four digits and
        # is money - but then it carries the marker right beside it. Without that, a year-shaped number
        # is a year when date words are near, and otherwise simply not settled.
        if _YEAR_WORD.search(before[-28:]) or _YEAR_WORD.search(after[:18]):
            return {"verdict": REJECTED, "reason": "year_in_date_context"}
        return {"verdict": AMBIGUOUS, "reason": "four_digit_number_could_be_a_year_or_an_amount"}
    if grouped:
        return {"verdict": MONETARY, "reason": "thousands_grouping"}
    if tight_marker:
        return {"verdict": MONETARY, "reason": "currency_or_scale_marker_adjacent"}
    if loose_marker and (has_decimal or len(digits) >= 4):
        return {"verdict": MONETARY, "reason": "monetary_wording_nearby_and_not_a_date_or_small_digit"}
    if len(digits) <= 2 and not has_decimal:
        return {"verdict": REJECTED, "reason": "bare_prose_digit"}
    return {"verdict": AMBIGUOUS, "reason": "no_monetary_marker_and_no_date_context"}


def operands(step: dict, *, accept_ambiguous: bool = False) -> list:
    """Every number in one actor message, classified. Only MONETARY (and optionally AMBIGUOUS) are operands."""
    text = str(step.get("text") or step.get("content") or "")
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0)
        val = _value(raw)
        if val is None:
            continue
        cls = classify_number(text, m.start(), m.end())
        keep = cls["verdict"] == MONETARY or (accept_ambiguous and cls["verdict"] == AMBIGUOUS)
        lo, hi = max(0, m.start() - 90), min(len(text), m.end() + 90)
        out.append({"raw": raw.strip(), "value": val, "step": step.get("i"),
                    "verdict": cls["verdict"], "reason": cls["reason"],
                    "is_operand": keep, "context": text[lo:hi]})
    return out


def monetary_operands(step: dict, *, accept_ambiguous: bool = False) -> list:
    return [o for o in operands(step, accept_ambiguous=accept_ambiguous) if o["is_operand"]]


# ───────────────────────── 2. tables and data cells ─────────────────────────

def _key(source: dict, table_index) -> str:
    s = source or {}
    return f"{s.get('kind')}:{s.get('ref') or s.get('step')}:{table_index}"


def tables_in(steps: list, supplied: list) -> list:
    """Every table of the prefix, each with a KEY that a cell can name exactly."""
    out = []
    for page in supplied:
        for ti, t in enumerate(CELLS.tables(str(page.get("text") or ""))):
            src = {"kind": "supplied_page", "ref": page.get("ref")}
            t = dict(t, origin=src, table_index=ti, key=_key(src, ti))
            out.append(t)
    for s in steps:
        if s.get("kind") != "tool":
            continue
        for ti, t in enumerate(CELLS.tables(str(s.get("obs") or ""))):
            src = {"kind": "observation", "step": s.get("i")}
            t = dict(t, origin=src, table_index=ti, key=_key(src, ti))
            out.append(t)
    return out


def data_cells(steps: list, supplied: list, assumptions) -> tuple:
    """Data cells of the prefix, with the row-label column removed, plus what was dropped and why.

    AUDIT FINDING 1. `oqa_lc_cells.cells_of` yields every numeric cell of a row INCLUDING index 0, which
    is the row LABEL. In this corpus a label is very often a year, so the B-1 matcher could bind an
    operand to "the cell 1942" - which is a period, not an amount.
    """
    kept, dropped = [], []
    def _collect(text, src):
        for c in CELLS.cells_of(str(text or ""), assumptions, source=src):
            c = dict(c, table_key=_key(src, c.get("table_index")))
            period = CELLS.read_period(str(c.get("row_label") or ""))
            if int(c.get("col_index") or 0) == 0:
                dropped.append({**c, "dropped_because": "row_label_not_a_data_cell",
                                "row_label_is_a_period": bool(period)})
                continue
            c["header_known"] = bool(c.get("column")) and c.get("column_basis") != CELLS.COL_UNKNOWN
            kept.append(c)
    for page in supplied:
        _collect(page.get("text"), {"kind": "supplied_page", "ref": page.get("ref")})
    for s in steps:
        if s.get("kind") == "tool":
            _collect(s.get("obs"), {"kind": "observation", "step": s.get("i")})
    return kept, dropped


def table_of(cell: dict, tabs: list) -> dict | None:
    """The table this cell CAME FROM. AUDIT FINDING 4: identity, not scale equality."""
    want = cell.get("table_key")
    for t in tabs:
        if t.get("key") == want:
            return t
    return None


def match_operand(op: dict, cells: list, *, tol: float = 1e-9) -> dict:
    """Which data cell an operand refers to, with ambiguity reported instead of resolved.

    AUDIT FINDING 3. Several cells can carry the same number - the same figure in two renderings of one
    table, or two different series that coincide. When the candidates DISAGREE about the scale, the
    column or the row, the match is ambiguous and the contract must stay silent.
    """
    hits = [c for c in cells if abs(float(c["value"]) - float(op["value"])) <= tol]
    if not hits:
        return {"status": "no_cell_match", "cell": None, "n_hits": 0, "candidates": []}
    sig = {(str(c.get("scale")), str(c.get("column")), str(c.get("row_label"))) for c in hits}
    if len(sig) > 1:
        return {"status": "ambiguous_cell_match", "cell": None, "n_hits": len(hits),
                "candidates": [{k: c.get(k) for k in ("raw", "scale", "column", "row_label",
                                                      "table_key")} for c in hits[:6]]}
    return {"status": ("unique_cell_match" if len(hits) == 1 else "repeated_identical_cell_match"),
            "cell": hits[0], "n_hits": len(hits),
            "candidates": [{k: hits[0].get(k) for k in ("raw", "scale", "column", "row_label",
                                                        "table_key")}]}


# ───────────────────────── 3. the nearer unit, as a tri-state ─────────────────────────

def scale_word(text) -> str | None:
    m = _SCALE_WORD.search(str(text or ""))
    return CELLS.canon_scale(m.group(1)) if m else None


def nearer_scale(cell: dict, table: dict) -> dict:
    """Does a NEARER scale annotation override the table-wide one? PRESENT / ABSENT / UNKNOWN.

    AUDIT FINDING 5, and it is the one the instruction names. `False` was returned whenever the cell's
    column field was empty - which happens because the header was missing, unreadable or unbound, not
    because the column carries no unit. An unobserved scope cannot witness an absence.
    """
    tscale = CELLS.canon_scale(str(table.get("scale") or "")) or None
    col_text = cell.get("column")
    row_text = cell.get("row_label")
    col_observed = bool(col_text) and cell.get("column_basis") != CELLS.COL_UNKNOWN
    row_observed = bool(str(row_text or "").strip())
    cu = scale_word(col_text) if col_observed else None
    ru = scale_word(row_text) if row_observed else None
    differing = [(w, u) for w, u in (("column", cu), ("row_label", ru))
                 if u is not None and u != tscale]
    scopes = {"column_observed": col_observed, "row_label_observed": row_observed,
              "column_unit": cu, "row_label_unit": ru, "table_scale": tscale,
              "column_basis": cell.get("column_basis")}
    if differing:
        return {"state": PRESENT, "differing": differing, "scopes": scopes,
                "why": "a nearer scope states a money scale other than the table's"}
    if col_observed and row_observed:
        return {"state": ABSENT, "differing": [], "scopes": scopes,
                "why": "both nearer scopes were observed and neither states another money scale"}
    missing = [n for n, ok in (("column header", col_observed), ("row label", row_observed)) if not ok]
    return {"state": UNKNOWN, "differing": [], "scopes": scopes,
            "why": f"not observed for this cell: {', '.join(missing)}; absence cannot be asserted"}


# ───────────────────────── 4. the conversion, as three questions ─────────────────────────

def required_exp(frm: str | None, to: str | None) -> int | None:
    if frm in SCALE_EXP and to in SCALE_EXP:
        return SCALE_EXP[frm] - SCALE_EXP[to]       # MATH: 10^required converts frm -> to
    return None


def stated_factor_exp(text: str) -> int | None:
    """The power of ten the actor SAYS it divided or multiplied by. OBS, not an inference."""
    for m in _FACTOR.finditer(text or ""):
        tok = m.group(1).replace(",", "")
        try:
            v = float(tok)
        except ValueError:
            continue
        if v <= 0:
            continue
        e, x = 0, v
        while x >= 10 and e < 12:
            x /= 10.0
            e += 1
        while x < 1 and e > -12:
            x *= 10.0
            e -= 1
        if abs(x - 1.0) < 1e-9:
            word = (m.group(0) or "").lower()
            return -e if ("divide" in word or "/" in word) else e
    return None


def _appears(value: float, text: str) -> bool:
    """Is this number written in this text, in any of the renderings the corpus uses?"""
    for form in {f"{value:,.0f}", f"{value:.0f}", f"{value:,.3f}".rstrip("0").rstrip("."),
                 f"{value:.3f}".rstrip("0").rstrip("."), f"{value:g}"}:
        if form and form in text:
            return True
    return False


def conversion(op: dict, later_steps: list, req: int | None) -> dict:
    """Was a factor stated, was it right, and was the converted value used? Three answers, not one.

    AUDIT FINDING 6. When `req == 0` the bound scale already equals the asked scale: nothing has to be
    multiplied, and demanding an explicit "x 1" turns a correct episode into an OUTSTANDING obligation.
    AUDIT FINDING 7. A stated factor that is never applied to anything is not a conversion.
    """
    text = "\n".join(str(s.get("text") or s.get("content") or "") for s in later_steps
                     if s.get("kind") == "message")
    written = [c for s in later_steps if s.get("kind") == "message"
               for c in CHAIN.conversions(str(s.get("text") or s.get("content") or ""))]
    mine = [c for c in written if abs(float(c["src"]) - float(op["value"])) <= 1e-6]
    stated = stated_factor_exp(text)
    out = {"required_exp": req, "stated_exp": stated,
           "written_conversions_for_this_operand": mine,
           "converted_value_used": None}
    if req == 0:
        out["verdict"] = "no_conversion_required"
        out["why"] = ("the bound scale already equals the asked scale, so no factor is needed and an "
                      "explicit multiplication by one is not required")
        out["converted_value_used"] = _appears(float(op["value"]), text)
        return out
    if mine:
        c = mine[0]
        ok = (c.get("verdict") == CHAIN.DISCHARGED)
        out["verdict"] = "correct_conversion" if ok else (
            "unknown_conversion" if c.get("verdict") == CHAIN.UNKNOWN else "incorrect_conversion")
        out["why"] = (f"the actor wrote {c['src_raw']} = {c['dst_raw']} {c['dst_scale']} "
                      f"(factor {c['factor_used']:g}, required {c['factor_required']})")
        out["converted_value_used"] = _appears(float(c["dst"]), text)
        out["written_source_scale_basis"] = c.get("src_scale_basis")
        return out
    if stated is not None:
        out["verdict"] = "correct_factor_stated" if stated == req else "incorrect_factor_stated"
        out["why"] = f"the actor states a factor of 10^{stated}; the binding requires 10^{req}"
        return out
    out["verdict"] = "no_factor_stated"
    out["why"] = f"the binding requires a factor of 10^{req} and no later step states one"
    return out
