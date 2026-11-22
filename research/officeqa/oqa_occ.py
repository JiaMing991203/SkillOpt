#!/usr/bin/env python3
"""Occurrence-level source binding from the permitted view. Rule OQA-OCC-1.

WHAT THIS IS FOR. The source-binding investigation (OQA-BIND-1) established that the column's MEANING
cannot be recovered from the pipe rendering the document tools return: the spans are already destroyed,
and the leaf read from a split header was correct on one record and wrong on three. It did NOT test a
different question, and the instruction is explicit that it demonstrated a failing extraction method
rather than the impossibility of every inference from permitted observations.

The different question is OCCURRENCE identity rather than column semantics. A `grep` observation
prefixes each matched line with its LINE NUMBER in the document:

    3507: | Fiscal year or month | Total | Income and profit taxes > Individual | ...

so a cell has an observable POSITION - (document, line, field index) - independent of what its column
means. That is what this module binds to.

THE DISTINCTION THE INSTRUCTION INSISTS ON, AND WHY IT IS NOT GLOSSED HERE.
"A unique numeric match versus an established source binding." Finding that a number the actor wrote
sits at exactly one observed position is still a NUMERIC MATCH. It narrows the candidate to one cell;
it does not establish that the actor read that cell. So a unique position is necessary and NOT
sufficient, and this module reports it as `UNIQUE_POSITION`, not as a binding.

A binding becomes SUPPORTED only with an independent corroboration:

    ROW_LABEL    the actor's own message that wrote the number also names a distinctive token from the
                 line the number sits on. The actor said what it was looking up, and the line says the
                 same thing. This is evidence about the actor's intent, not about the arithmetic.
    QUERY        the line was returned by a grep whose PATTERN the actor chose, and the pattern's words
                 appear on that line. The actor's query is evidence of intent.

A THIRD CANDIDATE WAS REJECTED, and the rejection is recorded because the number it produced was
attractive. `SOLE_IN_ROW` - the value occurs once on its line - looked like a corroboration and lifted
the supported rate to 100 per cent of unique positions. It is very nearly a TAUTOLOGY: if a value
appeared twice on one line it would occupy two field indices, so the position would not be unique.
Measured over the 24 saved training records, 200 of 201 unique positions were sole-in-row, so the
condition carries essentially no information. It is computed and reported for transparency and
`SUPPORTING` excludes it. Honest support is 64 of 201, not 201 of 201.

WHAT IS DELIBERATELY NOT CLAIMED.
  * The column's meaning. A supported occurrence binding says WHICH CELL, not what the cell measures.
    The two witnessed error classes of OQA-BIND-1 are column SELECTION errors, and this module does not
    pretend to settle them; it provides the cell identity a later stage contract would need.
  * That an unbound number is wrong. Most numbers the actor writes are its own intermediate results and
    have no observed occurrence by construction. `NO_OCCURRENCE` is a category, not a fault.
  * That a record with no line-numbered observation is a failure. Four of the 24 records read documents
    without producing numbered lines, so the channel is simply absent there. That is genuinely missing
    information, and it is reported as its own outcome rather than folded into UNKNOWN.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import os
import re

RULE = "OQA-OCC-1"
SCHEMA = "lso.officeqa_occurrence_binding.v1"

_NUM = re.compile(r"(?<![\d.,])-?\$?\s?\d(?:[\d,]*\d)?(?:\.\d+)?%?")
_LINE = re.compile(r"^\s*(\d+):\s?(.*)$")
_WORD = re.compile(r"[A-Za-z]{4,}")
_PATH = re.compile(r"path=['\"]([^'\"]+)['\"]")
_PATTERN = re.compile(r"pattern=['\"]([^'\"]+)['\"]")

# ── amendment D-2: a numeral inside label text is not a numeric data cell ─────
#
# `observed_occurrences` used to emit an occurrence for EVERY numeral anywhere in ANY field, so a
# column heading carrying a year ("Receipts Carriers' Taxing Act of 1937") and a pandas rendering
# artifact ("Unnamed: 14_level_2") occupied a (document, line, field) position exactly as a data cell
# does. Measured over the 48 frozen OQA-LN-1 attempts, 1,546 of 3,548 occurrence-producing matches
# (43.6%) came from fields that are not numeric cells at all.
#
# A field is a NUMERIC CELL when the WHOLE field is one numeral in a form this module's own `_NUM`
# already reads, optionally wrapped in the parentheses `parse_value` already treats as a negative,
# optionally carrying a currency or percent marker, and optionally followed by footnote references of
# the form "6/ 7/". Nothing is added to the set of numeric formats the parser supports; what changes
# is only WHERE a numeral is allowed to sit.
#
# Everything else - a field containing a letter, a date such as 8/21/91, a collapsed multi-value
# rendering such as "2.861 2.807 2.860 2.893", a bare footnote marker such as "5/" - yields no
# occurrence. It is NOT asserted to be wrong; it is left unknown, which is what an ambiguous match
# should be.
#
# WHAT IS NOT FIXED. A year standing alone in its own heading cell - "| 1987 | 1988 |" - is a bare
# numeral and remains indistinguishable from a data cell in this rendering. Telling them apart needs a
# header-row detector, which is a different and much larger change.
#
# LABEL TEXT IS STILL READ. `line_tokens` is taken from the whole line body either way, so the
# ROW_LABEL corroboration and every piece of context are unchanged. The correction removes label
# text's ability to be a POSITION, not its ability to be EVIDENCE.
_CELL = re.compile(r"""
    ^\s*\(?\s*
    (?:-?\$?\s?\d(?:[\d,]*\d)?(?:\.\d+)?%?)   # exactly _NUM's own numeral
    \s*\)?\s*(?:\$|%)?\s*                       # a trailing currency or percent marker
    (?:\d+/\s*)*                                 # footnote references: "6/", "6/ 7/"
    $""", re.X)

LEGACY_ANY_MATCH = "legacy_any_match"
BARE_NUMERAL_CELLS = "bare_numeral_cells"


def is_numeric_cell(field: str) -> bool:
    """Is this whole field one numeral, rather than a number sitting inside label text?"""
    text = str(field or "").strip()
    if not text:
        return False
    return bool(_CELL.match(text))


# Words too common in both a financial table and an agent's prose to corroborate anything.
STOPWORDS = frozenset({
    "total", "year", "month", "fiscal", "calendar", "this", "that", "from", "with", "which",
    "value", "table", "column", "number", "there", "then", "have", "also", "will", "need",
})

# The corroborations that COUNT. SOLE_IN_ROW is computed but excluded: see the module docstring.
SUPPORTING = ("ROW_LABEL", "QUERY")
NON_INFORMATIVE = ("SOLE_IN_ROW",)

UNIQUE_POSITION = "UNIQUE_POSITION"
SEVERAL_POSITIONS = "SEVERAL_POSITIONS"
NO_OCCURRENCE = "NO_OCCURRENCE"
SUPPORTED = "SUPPORTED"
UNKNOWN = "UNKNOWN"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_value(raw: str):
    """A table number, or None. Parenthesised negatives and trailing percent signs are handled."""
    text = str(raw).strip().lstrip("$").replace(",", "").replace("$", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").rstrip("%")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def tokens(text) -> set:
    """Distinctive words, lowercased. Stopwords removed so a shared "total" corroborates nothing."""
    return {w.lower() for w in _WORD.findall(str(text or ""))} - STOPWORDS


def normalize(items: list) -> list:
    """One shape for the two recorded forms, so the SAME measurement serves both.

    A saved trajectory uses {"type": "tool_call", "cmd", "obs"} and {"type": "message", "content"};
    a chain packet uses {"kind": "tool", "cmd", "obs", "args"} and {"kind": "message", "text"}. Both
    become {"kind": "tool"|"message", "cmd", "obs", "args", "text"}. Anything else is dropped, so a
    field added to either form later cannot reach a prompt or a binding silently.
    """
    out: list = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or {"tool_call": "tool", "message": "message"}.get(item.get("type"))
        if kind == "tool":
            out.append({"kind": "tool", "cmd": str(item.get("cmd") or ""),
                        "obs": str(item.get("obs") or ""),
                        "args": item.get("args") if isinstance(item.get("args"), dict) else {}})
        elif kind == "message":
            out.append({"kind": "message",
                        "text": str(item.get("text") if item.get("text") is not None
                                    else (item.get("content") or ""))})
    return out


def observed_occurrences(items: list, *, numeric_cells_only: bool = False) -> list:
    """Every numeric cell the actor actually OBSERVED, with its position and its line's evidence.

    Only line-numbered, pipe-delimited observation lines qualify. An observation without line numbers
    carries no position, so it yields nothing rather than a guess.

    `numeric_cells_only` is amendment D-2. It DEFAULTS TO FALSE, the superseded behaviour, so every
    caller that has not been reviewed against the amendment - including the historical report path,
    whose artifacts are pinned by digest - is byte-identical. The learned-contract pipeline passes
    True. Both sides of the D-2 comparison therefore come from this one code path.
    """
    out: list = []
    for entry in normalize(items):
        if entry["kind"] != "tool":
            continue
        cmd = entry["cmd"]
        args = entry.get("args") or {}
        raw_path = args.get("path") or (_PATH.search(cmd).group(1) if _PATH.search(cmd) else "")
        document = os.path.basename(str(raw_path)) if raw_path else "?"
        raw_pattern = args.get("pattern") or (_PATTERN.search(cmd).group(1)
                                              if _PATTERN.search(cmd) else "")
        pattern_tokens = tokens(raw_pattern) if raw_pattern else set()
        for raw_line in entry["obs"].splitlines():
            line_match = _LINE.match(raw_line)
            if not line_match:
                continue
            line_no, body = int(line_match.group(1)), line_match.group(2)
            if "|" not in body:
                continue
            fields = [f.strip() for f in body.split("|")]

            def _values(field: str) -> list:
                """The values this field contributes, under the mode in force."""
                found = [v for v in (parse_value(n.group(0)) for n in _NUM.finditer(field))
                         if v is not None]
                if not numeric_cells_only:
                    return found
                # A cell carries ONE value - its leading numeral. "1,089 6/ 7/" is 1,089, and the
                # 6 and the 7 are footnote references, not cells.
                return found[:1] if (found and is_numeric_cell(field)) else []

            # `sole_in_row` counts what sits on the line, so under D-2 it counts CELLS. A year inside
            # a heading no longer makes a data value non-sole.
            values_on_line = [v for f in fields for v in _values(f)]
            line_tokens = tokens(body)          # the WHOLE body: label text is still evidence
            for field_index, field in enumerate(fields):
                for value in _values(field):
                    out.append({
                        "document": document, "line": line_no, "field": field_index,
                        "value": value, "line_tokens": sorted(line_tokens),
                        "pattern_tokens": sorted(pattern_tokens),
                        "field_text": field,
                        "sole_in_row": values_on_line.count(value) == 1,
                    })
    return out


def actor_written(items: list) -> dict:
    """Numbers the actor wrote in its OWN messages, each with the words of every message that wrote it.

    The actor's own text is the only place its intent is observable, so the tokens are collected per
    VALUE across messages: a number introduced in one sentence and used in the next is still the same
    claim.
    """
    out: dict = collections.defaultdict(set)
    for entry in normalize(items):
        if entry["kind"] != "message":
            continue
        content = entry["text"]
        message_tokens = tokens(content)
        for num in _NUM.finditer(content):
            value = parse_value(num.group(0))
            if value is not None:
                out[value] |= message_tokens
    return dict(out)


def corroborations(occurrence: dict, message_tokens: set) -> list:
    """Which corroborations hold for one occurrence. SOLE_IN_ROW is included but does not support."""
    found: list = []
    if set(occurrence["line_tokens"]) & message_tokens:
        found.append("ROW_LABEL")
    pattern = set(occurrence["pattern_tokens"])
    if pattern and (pattern & set(occurrence["line_tokens"])):
        found.append("QUERY")
    if occurrence["sole_in_row"]:
        found.append("SOLE_IN_ROW")
    return found


def bind(items: list, *, numeric_cells_only: bool = False) -> dict:
    """Bind every number the actor wrote to an observed occurrence, or say why it cannot be bound."""
    occurrences = observed_occurrences(items, numeric_cells_only=numeric_cells_only)
    written = actor_written(items)
    by_value: dict = collections.defaultdict(list)
    for occurrence in occurrences:
        by_value[occurrence["value"]].append(occurrence)

    bindings: list = []
    for value in sorted(written):
        hits = by_value.get(value, [])
        positions = {(h["document"], h["line"], h["field"]): h for h in hits}
        if not positions:
            bindings.append({"value": value, "match": NO_OCCURRENCE, "binding": UNKNOWN,
                             "why": "no observed occurrence: an actor-derived result, or a value the "
                                    "observations never showed",
                             "corroborations": [], "n_positions": 0})
            continue
        if len(positions) > 1:
            bindings.append({"value": value, "match": SEVERAL_POSITIONS, "binding": UNKNOWN,
                             "why": f"the value occurs at {len(positions)} observed positions; a "
                                    f"numeric match cannot choose between them",
                             "corroborations": [], "n_positions": len(positions),
                             "positions": [list(k) for k in sorted(positions)][:8],
                             "position_field_texts": [positions[k].get("field_text")
                                                      for k in sorted(positions)][:8]})
            continue
        key, occurrence = next(iter(positions.items()))
        found = corroborations(occurrence, written[value])
        supporting = [c for c in found if c in SUPPORTING]
        bindings.append({
            "value": value, "match": UNIQUE_POSITION,
            "binding": SUPPORTED if supporting else UNKNOWN,
            "why": ("a unique observed position, corroborated by " + ", ".join(supporting))
                   if supporting else
                   "a unique observed position but NO independent corroboration: a unique numeric "
                   "match is not an established source binding",
            "corroborations": found, "supporting": supporting,
            "position": {"document": key[0], "line": key[1], "field": key[2]},
            # The field AS IT WAS RENDERED, so a reader can see whether the match sat in a data cell
            # or inside label text without re-reading the trajectory. Amendment D-2.
            "field_text": occurrence.get("field_text"),
            "n_positions": 1,
        })
    return {
        "n_observed_occurrences": len(occurrences),
        "n_line_numbered_observations": len({(o["document"], o["line"]) for o in occurrences}),
        "occurrence_channel_available": bool(occurrences),
        "n_written_distinct": len(written),
        "bindings": bindings,
        "counts": collections.Counter(b["match"] for b in bindings),
        "supported": sum(1 for b in bindings if b["binding"] == SUPPORTED),
    }


def measure(pattern: str) -> dict:
    """The corpus measurement, including the evidence that SOLE_IN_ROW is non-informative."""
    per_record: list = []
    totals = collections.Counter()
    combos = collections.Counter()
    entailment = {"unique_positions": 0, "unique_and_not_sole_in_row": 0}

    for conv_path in sorted(glob.glob(pattern)):
        item = os.path.basename(os.path.dirname(conv_path))
        with open(conv_path, encoding="utf-8") as f:
            conv = json.load(f)
        result = bind(conv)
        for binding in result["bindings"]:
            if binding["match"] == UNIQUE_POSITION:
                entailment["unique_positions"] += 1
                if "SOLE_IN_ROW" not in binding["corroborations"]:
                    entailment["unique_and_not_sole_in_row"] += 1
                combos[",".join(sorted(binding["corroborations"])) or "(none)"] += 1
        per_record.append({
            "item": item,
            "occurrence_channel_available": result["occurrence_channel_available"],
            "n_observed_occurrences": result["n_observed_occurrences"],
            "n_written_distinct": result["n_written_distinct"],
            "unique_position": result["counts"].get(UNIQUE_POSITION, 0),
            "several_positions": result["counts"].get(SEVERAL_POSITIONS, 0),
            "no_occurrence": result["counts"].get(NO_OCCURRENCE, 0),
            "supported": result["supported"],
        })
        totals["records"] += 1
        totals["channel_available"] += int(result["occurrence_channel_available"])
        for key in ("n_observed_occurrences", "n_written_distinct", "supported"):
            totals[key] += result[key]
        for match in (UNIQUE_POSITION, SEVERAL_POSITIONS, NO_OCCURRENCE):
            totals[match] += result["counts"].get(match, 0)

    unique = entailment["unique_positions"] or 1
    return {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(),
        "label": "REUSED DEVELOPMENT EVIDENCE: these are training-split records, re-analysed offline. "
                 "No validation or held-out record takes part.",
        "input_pattern": pattern,
        "totals": dict(totals),
        "supported_rate_of_unique_positions": round(totals["supported"] / unique, 4),
        "corroboration_combinations": dict(combos),
        "sole_in_row_is_non_informative": {
            **entailment,
            "share_entailed": round(1 - entailment["unique_and_not_sole_in_row"] / unique, 4),
            "note": "SOLE_IN_ROW is excluded from SUPPORTING because a unique position almost entails "
                    "it: two occurrences of one value on a single line would occupy two field indices. "
                    "Counting it lifted the supported rate to 100% of unique positions, which is the "
                    "tell. The attractive number was a tautology.",
        },
        "per_record": per_record,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--predictions", required=True,
                    help="glob for conversation.json, e.g. '<dir>/predictions/*/conversation.json'")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    doc = measure(a.predictions)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    t = doc["totals"]
    print(json.dumps({
        "out": a.out, "records": t.get("records"),
        "records_with_the_occurrence_channel": t.get("channel_available"),
        "observed_occurrences": t.get("n_observed_occurrences"),
        "written_distinct": t.get("n_written_distinct"),
        "unique_position": t.get(UNIQUE_POSITION), "several_positions": t.get(SEVERAL_POSITIONS),
        "no_occurrence": t.get(NO_OCCURRENCE),
        "SUPPORTED": t.get("supported"),
        "supported_rate_of_unique_positions": doc["supported_rate_of_unique_positions"],
        "corroboration_combinations": doc["corroboration_combinations"],
        "sole_in_row_entailed_share": doc["sole_in_row_is_non_informative"]["share_entailed"],
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def support_index(items: list, *, numeric_cells_only: bool = False) -> dict:
    """value -> its occurrence binding, for a consumer that must decide whether a source is established.

    This is the interface `oqa_lc_chain` uses under amendment C-1. It deliberately returns the WHOLE
    record rather than a boolean, so the consumer can report WHY a selection was refused and which
    rival positions existed, instead of only that it was refused.
    """
    return {b["value"]: b for b in bind(items, numeric_cells_only=numeric_cells_only)["bindings"]}
