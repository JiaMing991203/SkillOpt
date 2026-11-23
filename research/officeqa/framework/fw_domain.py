#!/usr/bin/env python3
"""What a benchmark must supply for the shared learned-contract core: the `Domain`.

PORT PROVENANCE. This module is the part of `upstream/vx1/vx_vocab.py` that is NOT ALFWorld: its shape
(atoms -> (argument kinds, category, meaning); kinds; assumptions; typed variables; guards; persistence
states and invalidators; one digest covering all of it) is kept exactly, and the ALFWorld content moves
into a subclass. ALFWorld's own vocabulary stays in `upstream/`; OfficeQA's is `oqa_fw_domain.py`.

EVERY vocabulary element carries one CATEGORY, and the categories are the point of the whole exercise:

  TASK        a requirement defined by the question or task sentence
  GIVEN       supplied by the environment or the benchmark (a document page, a tool contract)
  OBS         a direct observation: something a saved record literally shows
  MATH        supplied mathematics (arithmetic, unit algebra)
  MACHINERY   researcher-supplied extraction or logic (parsers, Kleene evaluation, typed variables)
  LEARNED     an empirical hypothesis PROPOSED BY THE LEARNER and gated by code
  ASSUMPTION  an unresolved named assumption the learner may adopt or drop; never established knowledge

Only LEARNED and ASSUMPTION are the learner's. Reporting a MATH or MACHINERY fact as a discovery is the
specific error this split exists to prevent.

KINDS. The five ALFWorld kinds are unchanged; `concludes` is the generalisation of `completes` from "the
task is complete" to any named consequence, which is what a document-interpretation rule needs:

  requires   {verb, condition}            success of the verb -> condition held before (necessary)
  suffices   {verb, condition}            condition held before -> success
  effect     {verb, effects[, guard]}     success (and the guard before) -> each literal holds after
  completes  {scope, condition}           within scope: task complete right after the step <-> condition
  concludes  {scope, subject, conditions, consequence[, direction]}
                                          within scope, for EACH subject binding: conditions -> consequence
                                          (direction "iff" also refutes consequence-without-conditions)
  persists   {state, established_by, invalidated_by}
                                          a state carries forward from its establishing event until an
                                          invalidating one

A domain declares which kinds it uses. A kind a domain does not declare is unused there, not removed.
"""
from __future__ import annotations

import hashlib
import json
import re

CATEGORIES = ("TASK", "GIVEN", "OBS", "MATH", "MACHINERY", "LEARNED", "ASSUMPTION", "LEARNED-DEPENDENT")
ALL_KINDS = ("requires", "suffices", "effect", "completes", "concludes", "persists")
VAR_RE = re.compile(r"\?[a-z][a-z0-9_]*")
TYPE_WORD = re.compile(r"[a-z][a-z0-9_]*")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Domain:
    """One benchmark's vocabulary and record reader. Subclasses set the attributes and override the hooks."""

    name = "abstract"
    version = "fw.domain.v0"

    # ---- vocabulary ------------------------------------------------------------------------------------
    verbs: dict = {}            # verb -> {"roles": (...), "head": "a rendering of the command"}
    atoms: dict = {}            # atom -> (argument kinds, CATEGORY, one-line meaning)
    kinds: tuple = ()           # the subset of ALL_KINDS this domain declares
    assumptions: dict = {}      # name -> (reading, basis)
    directly_testable: tuple = ()   # assumptions the saved records can contradict directly
    placeholders: tuple = ()    # task-sentence type placeholders, e.g. OTYPE / LTYPE
    types: tuple = ()           # entity type words an `exists` / `subject` variable may range over
    variables = False           # typed existential variables allowed?
    guards = False              # effect guards allowed?
    subjects = False            # `concludes` subject variables allowed?
    persist_states: dict = {}   # state atom -> establishing event atom
    invalidators: dict = {}     # event name -> meaning
    completion_atom: str | None = None   # the atom `completes` is about, when the domain has one
    max_vars = 3

    # ---- argument kinds --------------------------------------------------------------------------------
    # "TYPE" a type word or placeholder; "X" any entity; anything else must be a role of the verb.
    def role_kinds(self, atom: str) -> tuple:
        return tuple(self.atoms[atom][0])

    def category(self, atom: str) -> str:
        return self.atoms[atom][1]

    # ---- record reading (hooks) ------------------------------------------------------------------------
    def parse_command(self, action) -> dict:
        """-> {"kind": "covered"|"other", "verb": ..., "args": {role: entity}}"""
        raise NotImplementedError

    def outcome_of(self, step) -> str:
        """ok | fail | unknown for one recorded step."""
        raise NotImplementedError

    def task_info(self, task) -> dict:
        """TASK placeholders read from the task/question text only."""
        return {}

    def type_of(self, entity) -> str | None:
        raise NotImplementedError

    def estimator(self, task_type, steps, assumptions=(), persists=()):
        """A prefix-only state estimate over `steps` (see fw_state.Estimator)."""
        raise NotImplementedError

    def direct_contradictions(self, name: str, ev) -> list:
        """Records that contradict an assumption outright. [] when it is not directly testable."""
        return []

    def head(self, verb: str) -> str:
        return (self.verbs.get(verb) or {}).get("head") or verb

    # ---- digest ----------------------------------------------------------------------------------------
    def digest(self) -> str:
        """One hash over everything a frozen model is pinned to. A vocabulary change invalidates models."""
        return sha(canon({
            "name": self.name, "version": self.version,
            "verbs": {v: list(s.get("roles") or ()) for v, s in self.verbs.items()},
            "atoms": {a: list(t) for a, t in self.atoms.items()},
            "kinds": list(self.kinds), "assumptions": {k: list(v) for k, v in self.assumptions.items()},
            "directly_testable": list(self.directly_testable), "placeholders": list(self.placeholders),
            "types": list(self.types), "variables": self.variables, "guards": self.guards,
            "subjects": self.subjects, "persist_states": self.persist_states,
            "invalidators": self.invalidators, "completion_atom": self.completion_atom,
        }))

    # ---- the report's own element table ----------------------------------------------------------------
    def element_table(self) -> list:
        rows = [{"element": f"{a}({','.join(t[0])})" if t[0] else a, "category": t[1], "meaning": t[2]}
                for a, t in sorted(self.atoms.items())]
        rows += [{"element": f"kind {k}", "category": "LEARNED (kind supplied)",
                  "meaning": "a hypothesis of this shape may be proposed"} for k in self.kinds]
        rows += [{"element": f"assumption {k}", "category": "ASSUMPTION", "meaning": v[0]}
                 for k, v in sorted(self.assumptions.items())]
        rows += [{"element": f"invalidator {k}", "category": "LEARNED (menu supplied)", "meaning": v}
                 for k, v in sorted(self.invalidators.items())]
        if self.variables:
            rows.append({"element": "?vars + exists", "category": "MACHINERY",
                         "meaning": "typed existential entity variables"})
        if self.subjects:
            rows.append({"element": "?vars + subject", "category": "MACHINERY",
                         "meaning": "the variables a `concludes` rule is checked pointwise over"})
        return rows
