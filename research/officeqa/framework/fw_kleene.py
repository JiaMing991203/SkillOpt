#!/usr/bin/env python3
"""Three-valued (Kleene) logic and fact provenance.

PORT PROVENANCE. `k_and`, `k_or` and `merge` are the bodies of `upstream/vx1/vx_state.py`; `fact` is the
body of `upstream/v1_core/state.py`. They contain no vocabulary reference, so they are carried over
unchanged and `tests/test_officeqa_fw_parity.py` asserts they agree with the snapshot on every input.

A FACT is {v, basis, steps, assumptions[, learned]}:
  v            True / False / None, where None means UNKNOWN - the records do not settle it. Never False.
  basis        observed | record | static | inferred | stale | none | predicted
  steps        the record steps that decide it
  assumptions  the named assumptions it rests on (a value under an assumption is not knowledge)
  learned      the ids of learned contracts it rests on (present only when it rests on one)
"""
from __future__ import annotations

UNNAMED_PREFIX = "<unnamed:"


def fact(v, basis: str, steps=(), assumptions=()) -> dict:
    return {"v": v, "basis": basis, "steps": list(steps), "assumptions": list(assumptions)}


def k_and(vs):
    out = True
    for v in vs:
        if v is False:
            return False
        if v is None:
            out = None
    return out


def k_or(vs):
    out = False
    for v in vs:
        if v is True:
            return True
        if v is None:
            out = None
    return out


def k_not(v):
    return None if v is None else (not v)


def merge(v, facts: list, basis_if_plain: str = "observed") -> dict:
    """A derived fact from the facts that decide it: their assumptions, learned ids and steps are united."""
    A, L, S, bases = set(), set(), set(), set()
    for f in facts:
        A |= set(f.get("assumptions") or [])
        L |= set(f.get("learned") or [])
        S |= set(f.get("steps") or [])
        bases.add(f.get("basis"))
    if v is None:
        basis = "stale" if "stale" in bases else "none"
    elif "predicted" in bases:
        basis = "predicted"
    elif A or L or "inferred" in bases:
        basis = "inferred"
    elif bases <= {"record", "static"}:
        basis = "record"
    else:
        basis = basis_if_plain
    out = fact(v, basis, sorted(S), sorted(A))
    if L:
        out["learned"] = sorted(L)
    return out


def unnamed(ty: str) -> str:
    """The token for an instance of `ty` not named in the record so far: an existential is never decided
    False merely because no instance has been seen."""
    return f"{UNNAMED_PREFIX}{ty}>"


def is_unnamed(x) -> bool:
    return isinstance(x, str) and x.startswith(UNNAMED_PREFIX)


def unnamed_type(x: str) -> str:
    return x[len(UNNAMED_PREFIX):-1]
