#!/usr/bin/env python3
"""Prefix-only state estimate: `value(t, atom, args)` answers about the PRE-state of step t from steps
0..t-1 and nothing later.

PORT PROVENANCE. The class shape, the `_v_<atom>` dispatch, the predicted-basis propagation through
synthetic steps, the unnamed-entity token and the persistence evaluation are
`upstream/vx1/vx_state.EstimatorX` with the ALFWorld atoms lifted out. `_persist_value` is `_v_on`
generalised over (state, establishing event, invalidator menu); ALFWorld's lamp is then one instance of
it and no lamp rule is carried over.

THE PREFIX RULE IS THE WHOLE POINT. A checker that may read step t's observation while judging step t
cannot be said to predict anything, and a learner that may read the suffix is fitting, not learning. So
`value` takes an explicit t and every atom implementation is written against `self.steps[:t]`.
"""
from __future__ import annotations

from fw_kleene import fact, is_unnamed, k_and, k_or, merge, unnamed, unnamed_type


class Estimator:
    """Base class. A domain subclasses it and adds one `_v_<atom>` method per atom it declares."""

    def __init__(self, dom, assumptions=(), task_type=None, persists=()):
        self.dom = dom
        self.A = set(assumptions)
        self.task_type = task_type
        self.steps: list = []
        self.persists = [c for c in persists
                         if c.get("kind") == "persists" and c.get("status", "active") == "active"]

    # ---- record ---------------------------------------------------------------------------------------
    def observe(self, step: dict) -> dict:
        """Append one recorded step. A domain may override to parse more, but must call super()."""
        s = dict(step)
        # `_cmd` and not `cmd`: a record may already carry a raw `cmd` string of its own, and
        # overwriting it would quietly change what every downstream reader sees.
        s["_cmd"] = self.dom.parse_command(step)
        s.setdefault("outcome", self.dom.outcome_of(s))
        self.steps.append(s)
        return s

    def add_synthetic(self, action, facts: dict, by: list) -> dict:
        """A PREDICTED step: its facts come from the learned effect contracts `by`, not from a record."""
        s = {"action": action, "_cmd": self.dom.parse_command({"cmd": action}), "obs": None,
             "outcome": "predicted", "synthetic": list(by), "predicted_facts": dict(facts)}
        self.steps.append(s)
        return s

    @classmethod
    def of_steps(cls, dom, task_type, steps, assumptions=(), persists=()):
        e = cls(dom, assumptions, task_type, persists)
        for st in steps:
            e.observe(st)
        return e

    # ---- values ---------------------------------------------------------------------------------------
    def value(self, t: int, atom: str, args: list) -> dict:
        fn = getattr(self, "_v_" + atom, None)
        if fn is None:
            raise KeyError(f"{self.dom.name} has no evaluator for atom {atom!r}")
        f = fn(t, *args)
        syn = [k for k in (f.get("steps") or [])
               if 0 <= k < len(self.steps) and self.steps[k].get("synthetic") is not None]
        if syn and f["v"] is not None:
            f = dict(f, basis="predicted",
                     learned=sorted(set(f.get("learned") or [])
                                    | {c for k in syn for c in self.steps[k]["synthetic"]}))
        return f

    # ---- entities -------------------------------------------------------------------------------------
    def names(self, t: int) -> set:
        """Every entity named in steps 0..t-1. A domain overrides this."""
        return set()

    def entities_of_type(self, t: int, ty: str) -> list:
        """Named instances of `ty`, plus one unnamed instance so an existential is never decided False
        merely because no instance has been named yet."""
        return sorted(n for n in self.names(t) if self.dom.type_of(n) == ty) + [unnamed(ty)]

    def introduced_at(self, t: int, ty: str) -> list:
        """Entities of `ty` that the observation of step t-1 introduced. A domain overrides."""
        return self.entities_of_type(t, ty)

    def pool(self, t: int, ty: str, scope: str = "all") -> list:
        return self.introduced_at(t, ty) if scope == "introduced" else self.entities_of_type(t, ty)

    # ---- generic persistence ---------------------------------------------------------------------------
    def _events(self, t: int, kind: str, entity) -> list:
        """Steps in the prefix whose record carries event `kind` about `entity`. A domain overrides."""
        return []

    def _persist_value(self, t: int, state: str, entity, est_event: str, off_event: str | None = None,
                       init_assumption: str | None = None) -> dict:
        """`state(entity)` at the pre-state of t: True at the establishing step; later only through an
        adopted `persists` rule; False through an invalidation, an observed off-event or `init_assumption`;
        UNKNOWN otherwise. Generalised `vx_state._v_on`."""
        on = self._events(t, est_event, entity)
        off = self._events(t, off_event, entity) if off_event else []
        last_on = max(on) if on else None
        last_off = max(off) if off else None
        if last_off is not None and (last_on is None or last_off > last_on):
            return fact(False, "observed", [last_off])
        if last_on is None:
            if init_assumption and init_assumption in self.A:
                return fact(False, "inferred", [-1], (init_assumption,))
            return fact(None, "none")
        if last_on == t - 1:
            return fact(True, "observed", [last_on])
        rules = [c for c in self.persists if c.get("state") == state]
        if not rules:
            return fact(None, "stale", [last_on])
        r = rules[0]
        inv = self._invalidated(entity, last_on, t, r.get("invalidated_by") or [])
        if inv is not None:
            f = fact(False, "inferred", [last_on, inv[0]])
            f["learned"], f["invalidated_by"] = [r["id"]], inv[1]
            return f
        f = fact(True, "inferred", [last_on])
        f["learned"] = [r["id"]]
        return f

    def _invalidated(self, entity, k_est: int, t: int, invs) -> tuple | None:
        """The first invalidating event after the establishing step. A domain overrides for its menu."""
        if "any_step" in invs and k_est + 1 < min(t, len(self.steps)):
            return k_est + 1, "any_step"
        return None
