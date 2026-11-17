"""Frozen OfficeQA vocabulary for the learned-contract port: tool verbs, observation atoms, named assumptions.

What this is. The OfficeQA counterpart of the ALFWorld prototype's `vocab.py` (transferred reference:
`transferred/vocab.py`, sha256 `6b48...` recorded in OFFICEQA_TRANSFER_MANIFEST.json). It fixes the small logical
vocabulary that learned contracts may use, and nothing outside it is expressible. Design v0.1 section 4.B:
"Use a controlled, extensible logical vocabulary ... expand the vocabulary when concrete counterexamples show that
the current abstraction cannot distinguish relevant situations."

How OfficeQA differs from ALFWorld, and why the vocabulary looks different.

  * ALFWorld gives every command an informative OUTCOME: "Nothing happens." is an observed failure, so a
    `requires` contract can be refuted by a success and supported by a failure. The OfficeQA document tools
    (`glob` / `read` / `grep`) almost never fail in that sense - `read` returns a window, `grep` returns matches or
    `[no matches]`, and a refused path returns an error string. Outcome-based refutation is therefore nearly
    unavailable here, and `requires` / `suffices` contracts are close to vacuous.
  * What OfficeQA does give, which ALFWorld does not, is a richly STRUCTURED observation: the parsed bulletins are
    pipe-delimited tables whose header row states each column's path. So the productive contract kind here is
    `effect`: a claim about the observation a successful call returns, checkable against that observation after it
    is revealed.

  Consequence for the atom set: every atom below is 0-ary and is evaluated on ONE tool step's observation (the
  ALFWorld analogue is `holding_any`). An atom is a property of the returned text, never of a hidden world state.

Trust labels. Each atom and assumption carries a `basis`, which the report prints verbatim so a reader can reject
a finding that rests on an unverified hypothesis:
    TOOL      documented tool behaviour, verified against skillopt/envs/officeqa/tool_runtime.py
    FORMAT    a property of the parsed-document format, read off the supplied corpus
    EMPIRICAL an unverified hypothesis about the corpus
    TASK      taken from the question sentence (never from the skill)
"""
from __future__ import annotations

import hashlib
import json
import re

VOCAB_VERSION = "oqa.lc.vocab.v1"

# ── verbs ───────────────────────────────────────────────────────────────────────
# The document tools, as `oqa_trace._parse_cmd` recovers them. `roles` is empty because every atom below is a
# property of the step's own observation, so no entity binding is needed.
VERBS = {
    "glob": {"roles": (), "args": ("pattern",),
             "report": "the file paths matching the pattern, or an error string"},
    "read": {"roles": (), "args": ("path", "start", "limit"),
             "report": "the contiguous line window [start, start+limit) of the file, truncated at 4000 characters"},
    "grep": {"roles": (), "args": ("pattern", "path"),
             "report": "up to 20 matching lines, each prefixed '<line no>: ', or '[no matches]'"},
}

# ── atoms ───────────────────────────────────────────────────────────────────────
# atom -> (arity 0, human reading, basis). The evaluator is `oqa_lc_cells.atom_values`.
ATOMS = {
    "has_table": ("the observation contains at least one pipe-delimited table row", "FORMAT"),
    "header_flat": ("the table's header is a single row that states a column path per cell "
                    "(\"Parent > Child\"), the form the transformed documents use", "FORMAT"),
    "header_split": ("the table's header is a parent row plus a separate sub-label row, the form the oracle "
                     "parsed pages use", "FORMAT"),
    "width_uniform": ("every data row of the table has the header's cell count", "FORMAT"),
    "sub_row_aligned": ("the sub-label row has one cell per column, so merging it with the parent row by cell "
                        "index is well defined", "FORMAT"),
    "col_leaf_sound": ("every data cell that can be cross-checked against a flattened header of the same table is "
                       "bound to the COLUMN that header names (its innermost label, which identifies the series)",
                       "EMPIRICAL"),
    "col_path_sound": ("the same, for the whole column PATH including the spanning parent - a strictly stronger "
                       "claim, because the split rendering prints a spanning parent only at its first column",
                       "EMPIRICAL"),
    "row_period_bound": ("every data row's label yields a period (a year, or a month plus a year)", "FORMAT"),
    "scale_stated": ("a scale (\"in millions of dollars\") is stated above the table", "FORMAT"),
    "cap_reached": ("the observation returned the tool's 20-line grep cap, so it is not a complete listing",
                    "TOOL"),
    "pattern_in_every_line": ("every returned line contains the requested pattern, case-insensitively", "TOOL"),
}
ATOM_NAMES = tuple(sorted(ATOMS))

# ── assumptions ─────────────────────────────────────────────────────────────────
# A contract's clauses are evaluated under the MODEL's assumption set, not the contract's own list (the contract's
# `assumptions` field is informational, exactly as in the ALFWorld IR).
ASSUMPTIONS = {
    "flat_header_authoritative":
        ("a flattened header cell \"Parent > Child\" states the document's own column path for that cell index, "
         "so it can be used as ground truth for a column binding", "FORMAT"),
    "index_alignment":
        ("in a split header, the sub-label row's k-th cell is the sub-label of the parent row's k-th column",
         "EMPIRICAL"),
    "year_inheritance":
        ("a data row whose label names only a month inherits the year of the nearest preceding row label that "
         "names one", "EMPIRICAL"),
    "table_scale_applies_to_rows":
        ("a scale stated in a table heading applies to the numbers in the rows below it, until another scale is "
         "stated", "EMPIRICAL"),
    "grep_cap_not_reached":
        ("when fewer than 20 lines came back, grep returned every occurrence of the pattern in that file, so an "
         "absence conclusion about it is licensed", "TOOL"),
}

# Assumptions the saved records can contradict directly (the OfficeQA analogue of learn.DIRECTLY_TESTABLE).
DIRECTLY_TESTABLE = ("index_alignment", "year_inheritance", "flat_header_authoritative")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def vocab_digest() -> str:
    """Digest of everything a model is frozen against."""
    return sha(canon({"version": VOCAB_VERSION,
                      "verbs": {v: {"args": s["args"], "roles": s["roles"], "report": s["report"]}
                                for v, s in VERBS.items()},
                      "atoms": {a: list(v) for a, v in ATOMS.items()},
                      "assumptions": {a: list(v) for a, v in ASSUMPTIONS.items()}}))


# ── outcome of one tool step ────────────────────────────────────────────────────
_ERROR_OBS = re.compile(r"^\[(no matches|read error|glob error|grep error|error)", re.I)


def outcome_of(step: dict) -> str:
    """ok / fail / unknown for one recorded tool step, from its observation alone.

    `fail` is reserved for the cases the tool itself reports as unproductive: an error string, `[no matches]`, or
    an empty observation. Everything else that returned text is `ok`. A non-tool step is `unknown`.

    This is deliberately coarser than ALFWorld's outcome reading, and that is the point recorded in the module
    docstring: OfficeQA tool calls rarely fail, so almost every step is `ok` and the refutation pressure has to
    come from `effect` contracts rather than from outcomes.
    """
    if (step or {}).get("kind") != "tool":
        return "unknown"
    obs = (step.get("obs") or "").strip()
    if not obs:
        return "fail"
    if _ERROR_OBS.match(obs):
        return "fail"
    return "ok"


def covered_verbs() -> list:
    return sorted(VERBS)


def describe_atom(name: str) -> str:
    reading, basis = ATOMS[name]
    return f"{name}: {reading}  [{basis}]"


def describe_assumption(name: str) -> str:
    reading, basis = ASSUMPTIONS[name]
    return f"{name}: {reading}  [{basis}]"
