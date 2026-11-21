"""The OfficeQA value chain and its obligation ledger: source cell -> operand -> conversion -> calculation -> answer.

What this adds over `oqa_check.py`, and why it is the point of the port.

`oqa_check` emits a finding the moment a check's inputs look wrong. It has no object for "a requirement that is
still open", so it cannot distinguish a requirement nobody ever met from one the actor met two steps earlier. The
contract audit (docs/research/OFFICEQA_CONTRACT_AUDIT.md, defects 1 and 5) traced the report's two least reliable
rules to exactly that: `scale_conversion_missing` reads a value's EVIDENCE provenance and never `state["computed"]`,
so a conversion the actor performed is invisible and the obligation is reported as outstanding. Five of a ten-case
sample were demonstrable false positives.

This module makes the requirement a first-class object with a resolution, following amendment A4 section 3.1
(transferred reference `transferred/fact_ledger.py`) and the stage bookkeeping of `transferred/stage_tracker.py`:

    DISCHARGED  a witness in the record satisfies it; the witness is recorded and printed
    VIOLATED    a witness addresses it and is wrong; strictly more informative than OUTSTANDING
    OUTSTANDING nothing in the record addresses it
    UNKNOWN     the record does not determine it (a missing link is never turned into a violation)

and every discharge records WHICH facts it used, so that when a model assumption is dropped during repair, each
obligation discharged through that assumption is REOPENED rather than silently left satisfied (A4 3.1,
"a contradiction ... reopens every stage obligation whose discharge used that fact").

Source labels, kept distinguishable as design v0.1 section 3 requires:
    TASK      read from the question sentence by `oqa_obligations.derive` (never from the skill)
    TOOL      documented tool behaviour
    OBS       stated by a supplied page or a returned observation
    ACTOR     written by the target in its own message (a claim, not evidence)
    RULE(id)  licensed by a named learned contract of the frozen model
    ASSUMED(n) licensed by a named model assumption

Nothing here reads the answer key, `fail_reason`, the evaluator, the skill or held-out data.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_occ as OCC            # amendment C-1: occurrence-level source corroboration
import oqa_check as CHK  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402
import oqa_obligations as OB  # noqa: E402

DISCHARGED, VIOLATED, OUTSTANDING, UNKNOWN = "DISCHARGED", "VIOLATED", "OUTSTANDING", "UNKNOWN"

# ── unit conversions the actor wrote down ───────────────────────────────────────

_N = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+"
_SCALES = r"thousands?|millions?|billions?"
_CONV = re.compile(
    rf"(?P<src>{_N})\s*"
    rf"(?:\(\s*in\s+(?P<s1>{_SCALES})\s*\)|(?P<s2>{_SCALES}))?\s*"
    rf"=\s*\$?\s*(?P<dst>{_N})\s*(?P<s3>{_SCALES})",
    re.I)
_SCALE_HINT = re.compile(rf"\bin\s+(?P<s>{_SCALES})\b", re.I)
_TOL = 1e-6


def _num(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def conversions(text: str) -> list:
    """Unit conversions stated in one message, each judged against the factor its scales require.

    Four written forms occur in the saved traces, all from UID0014's epoch-2 attempt:
        "1,004,461 (in millions) = $1,004.461 billion"   explicit source scale            -> factor 1e-3, correct
        "1,004,461 million = $1,004.461 billion"         scale word after the number      -> correct
        "2,331,274 (in thousands) = $2,331.274 billion"  explicit source scale            -> factor 1e-3, WRONG
                                                         (thousands to billions is 1e-6)
        "2,410,259 = $2,410.259 billion"                 source scale carried from the
                                                         previous line of the same message
    A carried source scale is marked `src_scale_basis = "CARRIED"`, because it is an inference about what the
    actor meant and not something the actor stated on that line. A conversion whose source scale cannot be
    established at all is returned with verdict UNKNOWN and never judged.
    """
    out: list[dict] = []
    carried = None
    for line in str(text or "").splitlines():
        hint = _SCALE_HINT.search(line)
        for m in _CONV.finditer(line):
            src, dst = _num(m.group("src")), _num(m.group("dst"))
            if src is None or dst is None or src == 0:
                continue
            stated = m.group("s1") or m.group("s2")
            src_scale = CELLS.canon_scale(stated) if stated else None
            basis = "STATED"
            if src_scale is None and hint and hint.start() < m.start("src"):
                src_scale, basis = CELLS.canon_scale(hint.group("s")), "STATED"
            if src_scale is None and carried:
                src_scale, basis = carried, "CARRIED"
            dst_scale = CELLS.canon_scale(m.group("s3"))
            used = dst / src
            required = CELLS.scale_factor(src_scale, dst_scale) if src_scale else None
            if required is None:
                verdict = UNKNOWN
            elif abs(used - required) <= max(abs(required), _TOL) * 1e-6:
                verdict = DISCHARGED
            else:
                verdict = VIOLATED
            out.append({"src": src, "src_raw": m.group("src"), "src_scale": src_scale,
                        "src_scale_basis": basis if src_scale else "UNKNOWN",
                        "dst": dst, "dst_raw": m.group("dst"), "dst_scale": dst_scale,
                        "factor_used": used, "factor_required": required, "verdict": verdict,
                        "line": line.strip()[:220]})
            if src_scale and basis == "STATED":
                carried = src_scale
    return out


# ── the chain ───────────────────────────────────────────────────────────────────

def supplied_tables(packet: dict) -> list:
    out = []
    for page in packet.get("oracle_pages") or []:
        for t in CELLS.tables(page.get("text") or ""):
            out.append(dict(t, _source={"kind": "oracle_page", "file": page.get("file"), "page": page.get("page")}))
    return out


def observation_tables(packet: dict) -> list:
    out = []
    for step in packet.get("steps") or []:
        if step.get("kind") != "tool":
            continue
        path = ((step.get("args") or {}).get("path") or "").rsplit("/", 1)[-1]
        for t in CELLS.tables(step.get("obs") or ""):
            out.append(dict(t, _source={"kind": step.get("tool"), "file": path, "step": step["i"]}))
    return out


def cell_facts(packet: dict, model: dict) -> list:
    """Every numeric cell available in this record, bound to a column, a period and a scale.

    The flattened headers in the tool observations are the references that let a supplied page's split-form table
    be bound; which basis each binding gets is decided by `oqa_lc_cells.bind_columns` under the MODEL's assumption
    set, so dropping `index_alignment` during repair turns those bindings into UNKNOWN rather than leaving them
    asserted.
    """
    A = set(model.get("assumptions") or [])
    refs = [t for t in observation_tables(packet) if t["header_form"] == "flat"]
    facts: list[dict] = []
    for page in packet.get("oracle_pages") or []:
        src = {"kind": "oracle_page", "file": page.get("file"), "page": page.get("page")}
        for c in CELLS.cells_of(page.get("text") or "", A, refs, src):
            facts.append({**c, "kind": "OBSERVED", "status": "ACTIVE", "available_from": 0,
                          "supports": ["OBS"] + ([f"ASSUMED({b})" for b in _binding_assumptions(c, A)])})
    for step in packet.get("steps") or []:
        if step.get("kind") != "tool":
            continue
        obs = step.get("obs") or ""
        if obs.startswith("[") and obs.rstrip().endswith("]"):
            continue
        path = ((step.get("args") or {}).get("path") or "").rsplit("/", 1)[-1]
        src = {"kind": step.get("tool"), "file": path, "step": step["i"]}
        for c in CELLS.cells_of(obs, A, refs, src):
            facts.append({**c, "kind": "OBSERVED", "status": "ACTIVE", "available_from": step["i"],
                          "supports": ["OBS"] + ([f"ASSUMED({b})" for b in _binding_assumptions(c, A)])})
    return facts


def _binding_assumptions(cell: dict, A: set) -> list:
    out = []
    if cell.get("column_basis") == CELLS.COL_FLAT and "flat_header_authoritative" in A:
        out.append("flat_header_authoritative")
    if cell.get("column_basis") == CELLS.COL_INDEX_MERGE and "index_alignment" in A:
        out.append("index_alignment")
    p = cell.get("period") or {}
    if p.get("inherited_year") and "year_inheritance" in A:
        out.append("year_inheritance")
    return out


def _digits(x: float) -> str:
    """The significant digits of a number, which a scale conversion preserves: 2,331,274 and 2331.274 both -> "2331274"."""
    t = f"{abs(x):.10f}".rstrip("0").replace(".", "").lstrip("0")
    return t or "0"


def _match_cells(value: float, cells: list, upto: int) -> list:
    """Cells this operand could have come from, available by step `upto`.

    An exact match needs nothing more. A SCALED match (the operand is the cell value times 1e3 / 1e6 / 1e9 or its
    reciprocal) additionally requires the two numbers to have the same significant digits, because that is what a
    scale conversion preserves.

    Without the digit test this function is a false-positive generator, and it was one: the value 1 occurs in
    almost every Treasury table (the "Treasury notes of 1890" column is a column of 1s), so "1 scaled by 1e6"
    matched any operand near a million and the unit check then reported it as converted by the wrong factor by a
    billion. Requiring equal digits removes that whole class and keeps the real case: 2331.274 is 2,331,274
    scaled by 1e-3, digits "2331274" either way.
    """
    hits = []
    want = _digits(value)
    for c in cells:
        if c["available_from"] > upto:
            continue
        for factor in (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9):
            if abs(c["value"] * factor - value) > max(abs(value), 1.0) * 1e-9:
                continue
            if factor != 1.0 and _digits(c["value"]) != want:
                continue
            hits.append({**c, "scale_factor": factor})
            break
    return hits


def build(packet: dict, model: dict) -> dict:
    """The value chain of one recorded attempt, with its obligation ledger.

    Returns {"obligations": <the task's requirements from the question>, "cells", "operands", "conversions",
             "computations", "answer", "ledger": [obligation records]}.
    """
    task = OB.derive(packet.get("question", ""))
    # The operand / computed split is `oqa_check.stage_state`'s, reused unchanged so that the plain report and this
    # one disagree about diagnoses and never about what the actor wrote down.
    base_facts = CHK.build_ledger(packet)
    state = CHK.stage_state(packet, base_facts, task)
    cells = cell_facts(packet, model)

    convs: list[dict] = []
    for step in packet.get("steps") or []:
        if step.get("kind") != "message":
            continue
        for c in conversions(step.get("text") or ""):
            convs.append({**c, "step": step["i"]})

    # Amendment C-1: the occurrence index is computed once from the permitted steps and consulted for
    # every operand, so a tie between equal-valued cells is broken by observed evidence or not at all.
    occurrence_support = OCC.support_index(packet.get("steps") or [])

    operands = []
    for o in state["operands"]:
        hits = _match_cells(o["value"], cells, o["step"])
        cell, basis = select_cell(hits, occurrence_support.get(o["value"]))
        operands.append({**o, "cells": hits, "cell": cell, "selection": basis,
                         "role": "operand"})
    computed = [{**c, "cells": _match_cells(c["value"], cells, c["step"]), "role": "computed"}
                for c in state["computed"]]

    chain = {"id": packet.get("id"), "question": packet.get("question", ""), "task": task,
             "cells": cells, "operands": operands, "computed": computed, "conversions": convs,
             "computations": state["computations"], "answer": state["answer"],
             "answer_value": state["answer_value"], "model": {"version": model.get("version"),
                                                              "sha256": model.get("sha256"),
                                                              "assumptions": list(model.get("assumptions") or [])}}
    chain["ledger"] = obligations(chain)
    return chain


def cell_identity(cell: dict) -> tuple:
    """A cell's identity, independent of the OBSERVATION ROUTE that reached it. Amendment C-2.

    `_match_cells` collects candidates per observation, so one cell reached by the supplied page and by
    two different greps becomes three candidates and C-1 then refuses a tie that does not exist. Of 499
    operands with several candidates, 199 (39.9%) collapse to one cell under this key.

    The route - which step returned it - is deliberately EXCLUDED, because the route is exactly what
    differs when one cell is seen twice. Two candidates agreeing on all five fields are the same
    measurement in the same document, so selecting "one of them" asserts nothing extra.
    """
    return (str((cell.get("source") or {}).get("file") or ""),
            str(cell.get("row_label") or ""),
            cell.get("col_index"),
            cell.get("value"),
            cell.get("scale_factor"))


def dedupe_candidates(hits: list) -> tuple:
    """Collapse candidates that are one cell seen through several observations. Amendment C-2.

    The EARLIEST-available member of each group is kept, so `available_from` still reflects when the
    actor could first have read it. Returns (deduped, n_raw) so the raw count stays reportable: a
    reader should be able to see that nine candidates were really three cells.
    """
    groups: dict = {}
    for hit in hits:
        key = cell_identity(hit)
        current = groups.get(key)
        if current is None or hit.get("available_from", 0) < current.get("available_from", 0):
            groups[key] = hit
    return list(groups.values()), len(hits)


SOLE_CANDIDATE = "SOLE_CANDIDATE"
OCCURRENCE_CORROBORATED = "OCCURRENCE_CORROBORATED"
REFUSED_NO_CORROBORATION = "REFUSED_NO_CORROBORATION"


def select_cell(hits: list, support: dict | None) -> tuple:
    """Which source cell to report, and WHY - or a refusal with the rivals named. Amendment C-1.

    WHAT THIS REPLACED AND WHY. `_best_cell` was handed every cell whose value matched the operand and
    returned one of them, ranked by exact-match, then FLAT column, then earliest step. When several
    cells hold the same number that is a source binding resting on numeric equality and a tie-break,
    and the ledger would then discharge or violate an obligation against a cell the actor may never
    have read. "Numeric equality alone must not establish cell, series or period identity."

    So a tie is now resolved only by evidence INDEPENDENT of the number: an occurrence binding
    (research/officeqa/oqa_occ.py) that is SUPPORTED, meaning the value sits at a unique observed
    (document, line, field) position AND the actor's own words or its own grep pattern corroborate that
    line. Without that, no cell is selected and the rivals are recorded.

    Returns (cell_or_None, basis_record). The basis is always recorded, including on a refusal, so a
    reader can tell "no candidate" from "several candidates and no way to choose".
    """
    if not hits:
        return None, {"basis": None, "n_candidates": 0, "n_raw_candidates": 0,
                      "why": "no cell in the permitted view holds this value"}
    # AMENDMENT C-2: collapse one cell seen through several observations BEFORE declaring a tie.
    cells, n_raw = dedupe_candidates(hits)
    ranked = sorted(cells, key=lambda c: (c["scale_factor"] != 1.0,
                                          c.get("column_basis") != CELLS.COL_FLAT,
                                          c["available_from"]))
    if len(cells) == 1:
        return ranked[0], {
            "basis": SOLE_CANDIDATE, "n_candidates": 1, "n_raw_candidates": n_raw,
            "why": ("exactly one cell in the permitted view holds this value" if n_raw == 1 else
                    f"{n_raw} candidates collapse to ONE cell once the observation route is excluded "
                    f"from the identity, so there is no tie to break")}
    supported = bool(support and support.get("binding") == OCC.SUPPORTED)
    if supported:
        return ranked[0], {
            "basis": OCCURRENCE_CORROBORATED, "n_candidates": len(cells), "n_raw_candidates": n_raw,
            "occurrence": support.get("position"), "corroborated_by": support.get("supporting"),
            "why": "several distinct cells hold this value, and the actor's own observation "
                   "corroborates one position independently of the number"}
    return None, {
        "basis": REFUSED_NO_CORROBORATION, "n_candidates": len(cells), "n_raw_candidates": n_raw,
        "occurrence_match": (support or {}).get("match"),
        "rivals": [{"where": _where(c), "column_basis": c.get("column_basis"),
                    "row_label": c.get("row_label"), "col_index": c.get("col_index"),
                    "scale_factor": c.get("scale_factor")} for c in ranked[:6]],
        "why": "several DISTINCT cells hold this value and no occurrence corroborates one of them; a "
               "unique numeric match is not an established source binding, so no cell is selected"}


# ── obligations ─────────────────────────────────────────────────────────────────

def _ob(kind: str, subject: str, requires: str, state: str, why: str, supports=(), witness=None, **extra) -> dict:
    return {"kind": kind, "subject": subject, "requires": requires, "state": state, "why": why,
            "supports": list(supports), "witness": witness, **extra}


def obligations(chain: dict) -> list:
    """The requirement records of one attempt, each with its resolution and its witness."""
    task = chain["task"]
    out: list[dict] = []
    asked_scale = task["scale"]["scale"]
    asked_periods = task["periods"]
    asked_years = {p["year"] for p in asked_periods}

    arith_steps = {c["step"] for c in chain["computations"]}
    for o in chain["operands"]:
        sub = f"operand {o['raw']} (step {o['step']})"

        # 1. cell_bound
        cell = o["cell"]
        sel = o.get("selection") or {}
        if cell is None and sel.get("basis") == REFUSED_NO_CORROBORATION:
            # AMENDMENT C-2. A REFUSAL IS NOT AN ABSENCE, and the branch below says it is: it reports
            # "no available numeric cell has this value", which is FALSE here - several cells hold it
            # and the record cannot say which the actor read. OUTSTANDING means "nothing in the record
            # addresses this requirement"; several rival cells plainly do. So the honest state is
            # UNKNOWN, with the rivals named.
            out.append(_ob("cell_bound", sub, "the operand must come from a cell of a supplied page or a "
                                              "returned observation", UNKNOWN,
                           f"the value occurs at {sel.get('n_candidates')} distinct cells "
                           f"({sel.get('n_raw_candidates')} candidate observations) and no observation "
                           f"corroborates one of them; a unique numeric match is not an established "
                           f"source binding, so the source is undetermined rather than absent",
                           ["OBS"], None, refused_rivals=sel.get("rivals"),
                           unresolvable_without_a_source=True))
            for kind, requires in (
                    ("period_match", "the cell's period must be one the question asks about"),
                    ("unit_match", "the operand's unit must be the one the question asks for")):
                out.append(_ob(kind, sub, requires, UNKNOWN,
                               f"the value occurs at {sel.get('n_candidates')} distinct cells and no "
                               f"observation corroborates one of them, so no source cell is "
                               f"established and this requirement cannot be resolved",
                               ["OBS"], None, refused_rivals=sel.get("rivals"),
                               unresolvable_without_a_source=True))
            continue
        if cell is None:
            # A value written in a message that also does arithmetic may be an intermediate the record does not
            # spell out - a regression residual, a partial sum - and calling that "provenance missing" is the
            # arithmetic-intermediate false positive of the contract audit's defect 4. `oqa_check._number_role`
            # catches the "a - b = c" and last-mention forms; a value buried in a long list of derived numbers it
            # cannot. So the honest state here is UNKNOWN, not OUTSTANDING.
            if o["step"] in arith_steps:
                out.append(_ob("cell_bound", sub, "the operand must come from a cell of a supplied page or a "
                                                  "returned observation", UNKNOWN,
                               "no available numeric cell has this value, and the message it appears in also does "
                               "arithmetic, so it may be an intermediate result rather than a figure read from a "
                               "table; the record does not say which", ["OBS", "ACTOR"]))
            else:
                out.append(_ob("cell_bound", sub, "the operand must come from a cell of a supplied page or a "
                                                  "returned observation", OUTSTANDING,
                               "no available numeric cell has this value, and the message it appears in states no "
                               "arithmetic it could be a result of", ["OBS"]))
            # AMENDMENT C-2, DEFECT A. This used to `continue`, so the two obligations BELOW were never
            # generated for an operand without a selected cell: 464 refusals lost exactly two records
            # each, which is the whole 1,839 -> 911 ledger gap C-1's replay showed. A requirement that
            # DISAPPEARS when its source becomes unidentifiable is indistinguishable from one nobody
            # ever had, and this module exists because `oqa_check` had no object for an open
            # requirement. So both are emitted as UNKNOWN, naming WHY the source is unavailable.
            for kind, requires in (
                    ("period_match", "the cell's period must be one the question asks about"),
                    ("unit_match", "the operand's unit must be the one the question asks for")):
                out.append(_ob(kind, sub, requires, UNKNOWN,
                               "no source cell is established for this operand, so this requirement "
                               "cannot be resolved", ["OBS"], None,
                               unresolvable_without_a_source=True))
            continue
        if cell.get("column") is None:
            out.append(_ob("cell_bound", sub, "the operand's cell must be bound to a column", UNKNOWN,
                           f"the value occurs in {_where(cell)} but its column is not determined "
                           f"({cell.get('column_basis')}): the supplied page renders the header in split form and "
                           f"no flattened header for that table was returned",
                           ["OBS"], _witness(cell)))
        else:
            out.append(_ob("cell_bound", sub, "the operand's cell must be bound to a column", DISCHARGED,
                           f"{_where(cell)}, row {cell['row_label']!r}, column {cell['column']!r}",
                           ["OBS"] + [f"ASSUMED({a})" for a in _cell_assumptions(cell, chain)], _witness(cell)))

        # 2. period_match
        if not asked_periods:
            out.append(_ob("period_match", sub, "the cell's period must be one the question asks about", UNKNOWN,
                           "the question states no period, so the required period has to be inferred and this "
                           "check cannot fire", ["TASK"]))
        elif cell.get("period") is None:
            out.append(_ob("period_match", sub, "the cell's period must be one the question asks about", UNKNOWN,
                           f"the row label {cell.get('row_label')!r} yields no period", ["OBS"], _witness(cell)))
        else:
            cp = cell["period"]
            asked_text = ", ".join(p["text"] for p in asked_periods)
            if cp.get("year") is None:
                # AMENDMENT C-3. This used to fall through to VIOLATED, because the span guard below
                # read `(cp.get("year") or -1)` and -1 lies outside any span of real years. A SENTINEL
                # STANDING IN FOR "UNKNOWN", USED IN AN INEQUALITY, IS HOW AN UNKNOWN BECOMES AN
                # ACCUSATION: 55 of 69 accusations in the C-2 replay were rows like `Aug.` reported as
                # "August (year unknown) is not among the asked June 1970, 1960, 1969".
                #
                # The month alone cannot settle it either. UID0028's asked periods include two BARE
                # YEARS, so an August row could be August 1960 or August 1969 and might be exactly what
                # the question asks about. `oqa_obligations._periods` is known to read "fiscal years
                # 1929-1942" as two bare years, so with this representation an unknown year is
                # genuinely undecidable and UNKNOWN is the honest state, not a cautious one.
                out.append(_ob("period_match", sub,
                               "the cell's period must be one the question asks about", UNKNOWN,
                               f"the row label {cell.get('row_label')!r} yields the period "
                               f"{cell.get('period_text')} with NO YEAR, so it cannot be compared "
                               f"with the asked {asked_text}; an undetermined year is not a wrong one",
                               ["TASK", "OBS"], _witness(cell), year_undetermined=True))
            elif cp.get("year") in asked_years:
                out.append(_ob("period_match", sub, "the cell's period must be one the question asks about",
                               DISCHARGED, f"row period {cell['period_text']} matches the asked {asked_text}",
                               ["TASK", "OBS"], _witness(cell)))
            elif len(asked_years) >= 2 and min(asked_years) < cp["year"] < max(asked_years):
                # A year INSIDE the span of the asked years is not reported as wrong. `oqa_obligations._periods`
                # reads "fiscal years 1929-1942" as the two bare years 1929 and 1942, so every intermediate year
                # of a regression over that span would otherwise be flagged - twelve loud false positives on
                # UID0014 alone. Whether the question names two points or a range cannot be decided from the cue
                # (`operation_varies` records the same ambiguity), so the honest value is UNKNOWN.
                out.append(_ob("period_match", sub, "the cell's period must be one the question asks about",
                               UNKNOWN,
                               f"row period {cell['period_text']} lies inside the span of the asked {asked_text}; "
                               f"whether the question names those years or the range between them cannot be "
                               f"decided from its wording, so this is not reported as a wrong period",
                               ["TASK", "OBS"], _witness(cell)))
            else:
                out.append(_ob("period_match", sub, "the cell's period must be one the question asks about",
                               VIOLATED, f"row period {cell['period_text']} is not among the asked {asked_text} "
                                         f"and is not inside their span",
                               ["TASK", "OBS"], _witness(cell)))

        # 3. unit_match - the regression case
        out.append(_unit_obligation(o, cell, asked_scale, chain))

    # 4. arith_recorded
    op = task["operation"]
    if op["basis"] != "STATED" or op["arity"] < 2:
        out.append(_ob("arith_recorded", "the answer", "a stated arithmetic step", UNKNOWN,
                       "the question states no arithmetic cue, so no computation is required", ["TASK"]))
    elif chain["computations"]:
        c = chain["computations"][0]
        out.append(_ob("arith_recorded", "the answer", f"a stated {op['op']}", DISCHARGED,
                       f"step {c['step']} states the arithmetic", ["TASK", "ACTOR"],
                       {"step": c["step"], "text": c["text"][:160]}))
    else:
        out.append(_ob("arith_recorded", "the answer", f"a stated {op['op']}", OUTSTANDING,
                       f"the question asks for a {op['op']} and no step states it", ["TASK"]))

    # 5. answer_traced
    ans = chain["answer_value"]
    if not (chain["answer"] or "").strip():
        out.append(_ob("answer_traced", "the answer", "an answer must be produced", OUTSTANDING,
                       "the attempt ended with no <answer> block", ["TASK"]))
    elif ans is None:
        out.append(_ob("answer_traced", "the answer", "the answer value must trace to a cell or a computation",
                       UNKNOWN, f"the answer {chain['answer'].strip()[:60]!r} contains no number", ["TASK"]))
    else:
        hits = _match_cells(ans, chain["cells"], 10 ** 9)
        derived = [c for c in chain["computed"]
                   if abs(c["value"] - ans) <= max(abs(ans), 1.0) * 1e-6]
        if hits:
            out.append(_ob("answer_traced", "the answer",
                           "the answer value must trace to a cell or a computation", DISCHARGED,
                           f"the answer value occurs in {_where(hits[0])}", ["OBS"], _witness(hits[0])))
        elif derived:
            out.append(_ob("answer_traced", "the answer",
                           "the answer value must trace to a cell or a computation", DISCHARGED,
                           f"the answer value is the result the actor derived at step {derived[0]['step']} "
                           f"({derived[0].get('rule')})", ["ACTOR"], {"step": derived[0]["step"]}))
        else:
            out.append(_ob("answer_traced", "the answer",
                           "the answer value must trace to a cell or a computation", OUTSTANDING,
                           f"the answer value {ans:g} occurs in no available cell and is not a recorded result",
                           ["OBS"]))
    return out


def _unit_obligation(o: dict, cell: dict | None, asked_scale: str | None, chain: dict) -> dict:
    """Resolution of the asked-unit requirement for one operand.

    This is the check whose old form produced the known false warning. Its resolution consults the actor's own
    conversions, which `oqa_check` collects and never reads, and it separates four outcomes the old rule
    conflated into one:
        DISCHARGED  the cell is already in the asked scale; or the operand IS the cell's value scaled by exactly
                    the factor the two scales require; or a conversion of this value with the correct factor is
                    recorded in a message
        VIOLATED    the operand is a converted value and the factor is wrong, or a recorded conversion of it uses
                    the wrong factor (the informative case)
        OUTSTANDING the cell is in another scale, the operand is the raw figure, and no conversion of it is
                    recorded anywhere
        UNKNOWN     no scale is asked, or no scale is stated above the cell's table

    The factor of the operand's own binding is what makes this work. `_match_cells` matches a value to a cell up
    to 1e3 / 1e6 / 1e9, and that factor is exactly the conversion the actor applied: "2331.274" binds to the cell
    "2,331,274" with factor 1e-3, so it is a CONVERTED value and the question is whether 1e-3 was the right factor
    (it was not; thousands to billions needs 1e-6). Asking such a value to be converted again - which the old rule
    effectively did - is the arithmetic-intermediate false positive of the contract audit's defect 4.
    """
    sub = f"operand {o['raw']} (step {o['step']})"
    req = f"the value used must be in the asked scale ({asked_scale})"
    if not asked_scale or asked_scale not in ("thousands", "millions", "billions"):
        return _ob("unit_match", sub, "the value used must be in the asked scale", UNKNOWN,
                   "the question states no money scale, so any scale in the evidence is allowed", ["TASK"])
    cell_scale = (cell or {}).get("scale")
    if cell_scale is None:
        return _ob("unit_match", sub, req, UNKNOWN,
                   "no scale is stated above the cell's table, so the operand's own scale is undetermined",
                   ["OBS"], _witness(cell) if cell else None)
    if cell_scale == asked_scale:
        return _ob("unit_match", sub, req, DISCHARGED,
                   f"the cell's table states {cell_scale!r}, which is the asked scale", ["TASK", "OBS"],
                   _witness(cell))
    required = CELLS.scale_factor(cell_scale, asked_scale)
    applied = cell.get("scale_factor", 1.0)
    if applied != 1.0 and required is not None:
        if abs(applied - required) <= max(abs(required), _TOL) * 1e-6:
            return _ob("unit_match", sub, req, DISCHARGED,
                       f"the operand is the cell value {cell['raw']} ({cell_scale}) scaled by {applied:g}, which is "
                       f"the factor {cell_scale} to {asked_scale} requires", ["TASK", "OBS", "ACTOR"],
                       _witness(cell), applied_exp=_exp(applied))
        return _ob("unit_match", sub, req, VIOLATED,
                   f"the operand is the cell value {cell['raw']} ({cell_scale}) scaled by {applied:g}, but "
                   f"{cell_scale} to {asked_scale} requires {required:g}; the value is wrong by "
                   f"{applied / required:,.0f}x", ["TASK", "OBS", "ACTOR"], _witness(cell),
                   applied_exp=_exp(applied))
    mine = [c for c in chain["conversions"]
            if abs(c["src"] - o["value"]) <= max(abs(o["value"]), 1.0) * 1e-9 and c["dst_scale"] == asked_scale]
    if not mine:
        how = required
        return _ob("unit_match", sub, req, OUTSTANDING,
                   f"the operand is the raw figure from a table that states {cell_scale!r}, the question asks for "
                   f"{asked_scale!r}, and no conversion of this value to {asked_scale} is recorded in any message "
                   f"({'multiply by ' + format(how, 'g') if how and how >= 1 else 'divide by ' + format(1 / how, ',.0f') if how else 'convert'})",
                   ["TASK", "OBS", "ACTOR"], _witness(cell), applied_exp=0)
    bad = [c for c in mine if c["verdict"] == VIOLATED]
    if bad:
        c = bad[0]
        return _ob("unit_match", sub, req, VIOLATED,
                   f"step {c['step']} converts it as {c['line']!r}; {c['src_scale']} to {c['dst_scale']} requires a "
                   f"factor of {c['factor_required']:g} and the factor used is {c['factor_used']:g}, so the "
                   f"converted value is wrong by {c['factor_used'] / c['factor_required']:,.0f}x"
                   + ("  (the source scale on that line was carried from an earlier line, not restated)"
                      if c["src_scale_basis"] == "CARRIED" else ""),
                   ["TASK", "OBS", "ACTOR"], {"step": c["step"], "line": c["line"]},
                   applied_exp=_exp(c["factor_used"]))
    ok = [c for c in mine if c["verdict"] == DISCHARGED]
    if ok:
        c = ok[0]
        return _ob("unit_match", sub, req, DISCHARGED,
                   f"step {c['step']} converts it with the correct factor: {c['line']!r}",
                   ["TASK", "OBS", "ACTOR"], {"step": c["step"], "line": c["line"]},
                   applied_exp=_exp(c["factor_used"]))
    return _ob("unit_match", sub, req, UNKNOWN,
               f"step {mine[0]['step']} converts it but its source scale could not be established",
               ["ACTOR"], {"step": mine[0]["step"], "line": mine[0]["line"]})


def _exp(factor: float) -> int:
    """log10 of a scale factor, as the exponent the Lean operand model uses. 0 when the factor is not a power of 10."""
    import math
    try:
        e = round(math.log10(abs(factor)))
    except (ValueError, OverflowError):
        return 0
    return int(e) if abs(10.0 ** e - abs(factor)) <= abs(factor) * 1e-9 else 0


def _cell_assumptions(cell: dict, chain: dict) -> list:
    A = set(chain["model"].get("assumptions") or [])
    return _binding_assumptions(cell, A)


def _where(cell: dict) -> str:
    s = cell.get("source") or {}
    if s.get("kind") == "oracle_page":
        return f"supplied page {s.get('file')} p.{s.get('page')}"
    return f"the {s.get('kind')} observation at step {s.get('step')} of {s.get('file')}"


def _witness(cell: dict | None) -> dict | None:
    if cell is None:
        return None
    return {"value": cell.get("value"), "raw": cell.get("raw"), "row_label": cell.get("row_label"),
            "period": cell.get("period_text"), "column": cell.get("column"),
            "column_basis": cell.get("column_basis"), "scale": cell.get("scale"),
            "source": cell.get("source"), "doc_line": cell.get("doc_line")}


# ── reopening on repair ─────────────────────────────────────────────────────────

def reopen(ledger: list, dropped_assumption: str) -> list:
    """Obligations whose discharge used `dropped_assumption`, re-marked UNKNOWN.

    Amendment A4 3.1 requires this: a discharge that rested on an assumption the records have since contradicted
    must not stay satisfied. Returning a new list keeps the original ledger intact for the before/after comparison.
    """
    tag = f"ASSUMED({dropped_assumption})"
    out = []
    for item in ledger:
        if item["state"] == DISCHARGED and tag in item["supports"]:
            out.append({**item, "state": UNKNOWN,
                        "why": f"REOPENED: this was discharged under the assumption {dropped_assumption}, which a "
                               f"counterexample has since refuted. Original reason: {item['why']}",
                        "supports": [s for s in item["supports"] if s != tag],
                        "reopened_from": DISCHARGED})
        else:
            out.append(item)
    return out


def summary(ledger: list) -> dict:
    out: dict = {}
    for item in ledger:
        out[item["state"]] = out.get(item["state"], 0) + 1
    return dict(sorted(out.items()))
