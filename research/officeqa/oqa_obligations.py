"""Independent task obligations for an OfficeQA question (design v0.1 section 3: "Information boundary").

What this is. A deterministic, conservative reading of the QUESTION TEXT into the obligations an answer must
discharge: which period(s) are asked about, which scale/unit is requested, which arithmetic operation is requested,
and what kind of answer is expected. Nothing here reads the skill, the answer key, the evaluator, or the trace.

Why it matters. The contract feedback must say what the TASK required, not what the current skill advises
(CLAUDE.md section 5, "Independent task contracts"). For ALFWorld the trusted source was the PDDL `(:goal ...)`.
OfficeQA ships no machine-readable goal, so the question sentence is the only trusted task statement available.

How it works. Explicit cues only. Every obligation carries a `basis`:
  STATED   a literal cue was matched in the question (the matched text is kept)
  UNKNOWN  no cue was matched - the obligation is undetermined and NEVER guessed

Limitation. This is a regex reading of English, so it is itself fallible: a missed cue yields UNKNOWN (silent, safe)
and a mis-parsed cue yields a wrong obligation (loud, unsafe). Every finding that depends on a STATED obligation
must therefore quote the matched question text so a reader can reject it. Treat the obligation set as an extraction
hypothesis, not as a verified specification.
"""
from __future__ import annotations

import re

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_SCALE_CUES = [
    (r"\bin\s+(?:millions?|\$\s*millions?)\b", "millions"),
    (r"\bin\s+(?:billions?|\$\s*billions?)\b", "billions"),
    (r"\bin\s+(?:thousands?)\b", "thousands"),
    (r"\bmillions?\s+of\s+dollars\b", "millions"),
    (r"\bbillions?\s+of\s+dollars\b", "billions"),
    (r"\bthousands?\s+of\s+dollars\b", "thousands"),
    (r"\bas\s+a\s+percent(?:age)?\b|\bwhat\s+percent(?:age)?\b|\bpercent(?:age)?\s+of\b", "percent"),
]

# Order matters: the first matching cue wins, so the more specific patterns come first.
_OP_CUES = [
    (r"\bpercent(?:age)?\s+(?:change|increase|decrease|growth|decline)\b", "percent_change", 2),
    (r"\b(?:change|increase|decrease|growth|decline|difference)\b.*\b(?:from|between)\b", "difference", 2),
    (r"\bhow\s+much\s+(?:more|less|greater|higher|lower)\b", "difference", 2),
    (r"\b(?:difference|gap)\s+between\b", "difference", 2),
    (r"\b(?:ratio|share|proportion|percent(?:age)?)\s+of\b", "ratio", 2),
    (r"\b(?:combined|total|sum|together|aggregate)\b", "sum", 0),
    (r"\b(?:average|mean)\b", "average", 0),
]

_KIND_CUES = [
    (r"\b(?:what\s+(?:month|year|date)|in\s+which\s+(?:month|year))\b", "date"),
    (r"\b(?:which|what)\s+(?:category|security|class|fund|item|type|agency)\b", "name"),
    (r"\b(?:how\s+many|how\s+much|what\s+was\s+the\s+(?:amount|value|total|level)|what\s+percent)\b", "number"),
]

_STOP = {
    "the", "a", "an", "of", "in", "on", "at", "for", "to", "and", "or", "by", "was", "were", "is", "are",
    "what", "which", "how", "much", "many", "between", "from", "total", "value", "amount", "did", "do",
    "does", "that", "this", "with", "as", "it", "its", "be", "been", "during", "than", "then", "there",
}


def _periods(text: str) -> list[dict]:
    """Month-year, fiscal-year and bare-year mentions, in the order they appear, de-duplicated."""
    found: list[dict] = []
    seen: set[tuple] = set()

    def add(p):
        key = (p.get("year"), p.get("month"), p.get("fiscal"))
        if key not in seen:
            seen.add(key)
            found.append(p)

    for m in re.finditer(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b", text):
        month = MONTHS.get(m.group(1).lower())
        if month:
            add({"text": m.group(0), "year": int(m.group(2)), "month": month, "fiscal": False, "basis": "STATED"})
    for m in re.finditer(r"\bfiscal\s+year\s+(\d{4})\b", text, re.I):
        add({"text": m.group(0), "year": int(m.group(1)), "month": None, "fiscal": True, "basis": "STATED"})
    for m in re.finditer(r"\b(?:19|20)\d{2}\b", text):
        year = int(m.group(0))
        if not any(p["year"] == year for p in found):
            add({"text": m.group(0), "year": year, "month": None, "fiscal": False, "basis": "STATED"})
    return found


def _scale(text: str) -> dict:
    for pattern, name in _SCALE_CUES:
        m = re.search(pattern, text, re.I)
        if m:
            return {"scale": name, "text": m.group(0), "basis": "STATED"}
    return {"scale": None, "text": "", "basis": "UNKNOWN"}


def _operation(text: str) -> dict:
    for pattern, op, arity in _OP_CUES:
        m = re.search(pattern, text, re.I)
        if m:
            return {"op": op, "arity": arity, "text": m.group(0), "basis": "STATED"}
    return {"op": "none", "arity": 1, "text": "", "basis": "UNKNOWN"}


def _answer_kind(text: str) -> dict:
    for pattern, kind in _KIND_CUES:
        m = re.search(pattern, text, re.I)
        if m:
            return {"kind": kind, "text": m.group(0), "basis": "STATED"}
    return {"kind": "unknown", "text": "", "basis": "UNKNOWN"}


def measure_terms(text: str) -> list[str]:
    """Content words of the question, used only to describe a finding - never as a correctness criterion."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text.lower()) if w not in _STOP]
    out: list[str] = []
    for w in words:
        if w not in out:
            out.append(w)
    return out[:12]


def derive(question: str) -> dict:
    """Question text -> obligation set. No other input is read."""
    q = str(question or "")
    periods = _periods(q)
    op = _operation(q)
    # A stated 2-ary operation needs two distinct asked periods OR two distinct asked categories. Which of the two
    # it is cannot be decided from the cue alone, so record the ambiguity instead of resolving it.
    varies = "unknown"
    if op["arity"] == 2:
        varies = "period" if len(periods) >= 2 else ("category" if len(periods) == 1 else "unknown")
    return {
        "question": q,
        "periods": periods,
        "scale": _scale(q),
        "operation": op,
        "operation_varies": varies,
        "answer_kind": _answer_kind(q),
        "measure_terms": measure_terms(q),
    }


def describe(obligations: dict) -> list[str]:
    """Readable obligation lines for a report. Each line states its basis so a reader can reject it."""
    lines = []
    periods = obligations["periods"]
    if periods:
        lines.append("period(s) asked: " + ", ".join(p["text"] for p in periods) + "  [STATED]")
    else:
        lines.append("period(s) asked: UNKNOWN (no month-year, fiscal-year or year cue in the question)")
    scale = obligations["scale"]
    lines.append(f"requested scale: {scale['scale']}  [STATED: {scale['text']!r}]" if scale["basis"] == "STATED"
                 else "requested scale: UNKNOWN (the question states no scale; any scale in the evidence is allowed)")
    op = obligations["operation"]
    if op["basis"] == "STATED":
        lines.append(f"operation: {op['op']} (arity {op['arity']}, varies by {obligations['operation_varies']})"
                     f"  [STATED: {op['text']!r}]")
    else:
        lines.append("operation: UNKNOWN (no arithmetic cue; a single cited value may be the answer)")
    kind = obligations["answer_kind"]
    lines.append(f"answer kind: {kind['kind']}  [{kind['basis']}" + (f": {kind['text']!r}]" if kind["text"] else "]"))
    return lines
