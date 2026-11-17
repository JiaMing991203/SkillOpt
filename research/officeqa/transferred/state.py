"""State estimate before each command, from the analyst-visible PREFIX only (design v0.1 section 4.A).

An `Estimator` holds the steps observed so far (command + observation). `value(t, atom, args)` answers about the
pre-state of command t using steps 0..t-1 only (it never reads step t's observation or anything later), under the
model's named assumptions. Every answer is a `Fact`:
    v            True / False / None (unknown)
    basis        observed (stated by the immediately preceding observation) | record (a fact about the record
                 itself) | static (a name or a property reported at any earlier step) | inferred (needs a named
                 assumption) | stale (an older statement that would need `persistence`) | none (nothing recorded)
    steps        0-based indices of the supporting steps (-1 = the episode start)
    assumptions  the named assumptions the value depends on (empty for observed/record/static facts)
Built-in readings B1-B4 and the assumptions are documented in vocab.py.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vocab import parse_command, parse_obs, outcome_of, type_of


def fact(v, basis, steps=(), assumptions=()):
    return {"v": v, "basis": basis, "steps": list(steps), "assumptions": sorted(set(assumptions))}


UNKNOWN = fact(None, "none")


class Estimator:
    def __init__(self, assumptions=(), task_type: str | None = None):
        self.A = set(assumptions)
        self.task_type = task_type
        self.steps: list = []  # {"action", "cmd", "obs", "atoms", "outcome"}

    def observe(self, action: str, obs: str) -> dict:
        cmd = parse_command(action)
        atoms = parse_obs(obs)
        s = {"action": action, "cmd": cmd, "obs": obs, "atoms": atoms, "outcome": outcome_of(cmd, atoms)}
        self.steps.append(s)
        return s

    @classmethod
    def of_episode(cls, ep: dict, assumptions=()):
        e = cls(assumptions, ep.get("task_type"))
        for st in ep["steps"]:
            e.observe(st["action"], st["obs"])
        return e

    # ---- helpers -------------------------------------------------------------------------------------------
    def _timed(self, t: int, k: int, v, steps, extra=()) -> dict:
        """A value established at step k (k = -1: the start) used at the pre-state of command t."""
        if k == t - 1:
            return fact(v, "inferred" if extra else "observed", steps, extra)
        if "persistence" in self.A:
            return fact(v, "inferred", steps, tuple(extra) + ("persistence",))
        return fact(None, "stale", steps, ())

    def _latest(self, t: int, pred):
        """(k, atom) of the latest atom before command t satisfying pred (atoms of one step: last one wins)."""
        for k in range(min(t, len(self.steps)) - 1, -1, -1):
            for a in reversed(self.steps[k]["atoms"]):
                r = pred(a)
                if r is not None:
                    return k, a, r
        return None, None, None

    # ---- atoms ---------------------------------------------------------------------------------------------
    def value(self, t: int, atom: str, args: list) -> dict:
        fn = getattr(self, "_v_" + atom, None)
        if fn is None:
            raise KeyError(atom)
        return fn(t, *args)

    def _v_arrived(self, t, r):
        k, a, _ = self._latest(t, lambda a: True if a["kind"] == "arrive" else None)
        if k is None:
            return fact(False, "record", [], ())
        return fact(a["recep"] == r, "record", [k])

    def _v_at(self, t, r):
        k, a, _ = self._latest(t, lambda a: True if a["kind"] == "arrive" else None)
        if k is not None and a["recep"] == r:
            return self._timed(t, k, True, [k])
        if "distinct_locations" not in self.A:
            return fact(None, "none" if k is None else "stale", [] if k is None else [k])
        if k is None:
            return fact(False, "inferred", [], ("distinct_locations",))
        return self._timed(t, k, False, [k], ("distinct_locations",))

    def _poss(self, o):
        def p(a):
            if a["kind"] == "pick_up" and a["obj"] == o:
                return True
            if a["kind"] == "move_done" and a["obj"] == o:
                return False
            if a["kind"] == "carrying":
                return o in a["items"]
            return None
        return p

    def _v_holding(self, t, o):
        k, _, v = self._latest(t, self._poss(o))
        if k is not None:
            return self._timed(t, k, v, [k])
        if "init_empty_hands" in self.A:
            return self._timed(t, -1, False, [-1], ("init_empty_hands",))
        return UNKNOWN

    def _v_holding_any(self, t):
        ka, aa, _ = self._latest(t, lambda a: True if a["kind"] == "carrying" else None)
        if ka is None and "init_empty_hands" in self.A:
            ka, held, extra = -1, set(), ("init_empty_hands",)
        elif ka is not None:
            held, extra = set(aa["items"]), ()
        else:
            ka = None
        if ka is not None:
            last = ka
            for k in range(ka + 1, min(t, len(self.steps))):
                for a in self.steps[k]["atoms"]:
                    if a["kind"] == "pick_up":
                        held.add(a["obj"]); last = k
                    elif a["kind"] == "move_done":
                        held.discard(a["obj"]); last = k
            return self._timed(t, last, bool(held), sorted({ka, last}), extra)
        # no anchor: only a currently held object can decide (True)
        picked = {a["obj"] for k in range(min(t, len(self.steps))) for a in self.steps[k]["atoms"] if a["kind"] == "pick_up"}
        for o in sorted(picked):
            f = self._v_holding(t, o)
            if f["v"] is True:
                return f
        return UNKNOWN

    def _v_in(self, t, o, r):
        listing_complete = "listing_complete" in self.A

        def p(a):
            if a["kind"] == "pick_up" and a["obj"] == o:
                return False
            if a["kind"] == "move_done" and a["obj"] == o and a["recep"] == r:
                return True
            if a["kind"] in ("see_on", "see_in") and a["recep"] == r:
                if o in a["items"]:
                    return True
                return False if listing_complete else None
            return None  # a move elsewhere or a listing elsewhere says nothing about R (overlapping receptacles, B3)
        k, a, v = self._latest(t, p)
        if k is None:
            return UNKNOWN
        extra = ("listing_complete",) if (v is False and a["kind"] in ("see_on", "see_in") and a["recep"] == r) else ()
        return self._timed(t, k, v, [k], extra)

    def _v_listed(self, t, o, r):
        k, a, _ = self._latest(t, lambda a: True if a["kind"] in ("see_on", "see_in") and a["recep"] == r else None)
        if k is None:
            return fact(None, "none")
        return fact(o in a["items"], "record", [k])

    def _v_open(self, t, r):
        def p(a):
            if a["kind"] in ("is_open", "open_done") and a["recep"] == r:
                return True
            if a["kind"] in ("is_closed", "close_done") and a["recep"] == r:
                return False
            return None
        k, _, v = self._latest(t, p)
        if k is None:
            return UNKNOWN
        return self._timed(t, k, v, [k])

    def _v_openable(self, t, r):
        k, _, _ = self._latest(t, lambda a: True if a["kind"] in ("is_open", "open_done", "is_closed", "close_done")
                               and a["recep"] == r else None)
        if k is not None:
            return fact(True, "static", [k])
        if "openable_reported" in self.A:
            for k in range(min(t, len(self.steps))):
                if any(a["kind"] == "arrive" and a["recep"] == r for a in self.steps[k]["atoms"]):
                    return fact(False, "inferred", [k], ("openable_reported",))
        return UNKNOWN

    def _transform(self, t, o, kind):
        k, _, _ = self._latest(t, lambda a: True if a["kind"] == kind and a["obj"] == o else None)
        return fact(k is not None, "record", [k] if k is not None else [])

    def _v_heated(self, t, o):
        return self._transform(t, o, "heat_done")

    def _v_cooled(self, t, o):
        return self._transform(t, o, "cool_done")

    def _v_cleaned(self, t, o):
        return self._transform(t, o, "clean_done")

    def _v_is_a(self, t, x, ty):
        return fact(type_of(x) == ty, "static", [])


def lit_key(atom: str, args: list) -> str:
    return atom + "(" + ",".join(args) + ")"
