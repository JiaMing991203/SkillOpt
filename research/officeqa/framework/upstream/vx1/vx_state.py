"""VX-1 state estimate (amendment A5 section 3.2): the frozen lc.vocab.v1 `state.Estimator` (unchanged semantics for
its atoms) plus the EXT atoms. Prefix-only: `value(t, atom, args)` answers about the PRE-state of command t (= the
post-state of step t-1) from steps 0..t-1 only.

Facts are dicts {v, basis, steps, assumptions[, learned]}:
  basis  observed | record | static | inferred | stale | none (as v1) | predicted (supported by a SYNTHETIC step, i.e.
         by learned effect contracts during pre-action prediction; `learned` lists those contract ids)
  learned  ids of LEARNED rules the value depends on (persists rules; effect contracts of a synthetic step)
Unnamed entities: the token "<unnamed:TYPE>" stands for an instance of TYPE not named in the record so far (so an
existential is never decided False just because no instance has been named). Each atom treats it by its own definition
(e.g. holding(<unnamed>) is False once the held set is known; on(<unnamed>) is UNKNOWN unless `init_off`).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_vocab as XV  # noqa: E402  (also puts learned_contracts on sys.path)
from state import Estimator, fact  # noqa: E402  (frozen v1 estimator)

UNNAMED_PREFIX = "<unnamed:"
RECEP_KINDS = ("arrive", "see_on", "see_in", "is_open", "is_closed", "open_done", "close_done", "pick_up", "move_done")


def unnamed(ty: str) -> str:
    return f"{UNNAMED_PREFIX}{ty}>"


def is_unnamed(x) -> bool:
    return isinstance(x, str) and x.startswith(UNNAMED_PREFIX)


def unnamed_type(x: str) -> str:
    return x[len(UNNAMED_PREFIX):-1]


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


def merge(v, facts: list, basis_if_plain: str = "observed") -> dict:
    """A derived fact from the facts that decide it (their assumptions / learned ids / steps are united)."""
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


class EstimatorX(Estimator):
    def __init__(self, assumptions=(), task_type: str | None = None, persists=()):
        super().__init__(assumptions, task_type)
        self.persists = [c for c in persists if c.get("kind") == "persists" and c.get("status", "active") == "active"]

    # ---- record ---------------------------------------------------------------------------------------------
    def observe(self, action: str, obs: str) -> dict:
        cmd = XV.parse_command(action)
        atoms = XV.parse_obs(obs)
        s = {"action": action, "cmd": cmd, "obs": obs, "atoms": atoms, "outcome": XV.outcome_of(cmd, atoms)}
        self.steps.append(s)
        return s

    def add_synthetic(self, action: str, atoms: list, by: list) -> dict:
        """A PREDICTED step (pre-action prediction only): its atoms come from learned effect contracts `by`."""
        cmd = XV.parse_command(action)
        s = {"action": action, "cmd": cmd, "obs": None, "atoms": atoms, "outcome": "predicted", "synthetic": list(by)}
        self.steps.append(s)
        return s

    @classmethod
    def of_steps(cls, task_type, steps, assumptions=(), persists=()):
        e = cls(assumptions, task_type, persists)
        for st in steps:
            e.observe(st["action"], st["obs"])
        return e

    def value(self, t: int, atom: str, args: list) -> dict:
        fn = getattr(self, "_v_" + atom, None)
        if fn is None:
            raise KeyError(atom)
        f = fn(t, *args)
        syn = [k for k in (f.get("steps") or []) if 0 <= k < len(self.steps) and self.steps[k].get("synthetic") is not None]
        if syn and f["v"] is not None:
            f = dict(f, basis="predicted",
                     learned=sorted(set(f.get("learned") or []) | {c for k in syn for c in self.steps[k]["synthetic"]}))
        return f

    # ---- entities -------------------------------------------------------------------------------------------
    def names(self, t: int) -> set:
        out = set()
        for k in range(min(t, len(self.steps))):
            s = self.steps[k]
            out |= set((s["cmd"].get("args") or {}).values())
            for a in s["atoms"]:
                for f in ("obj", "recep"):
                    if a.get(f):
                        out.add(a[f])
                out |= set(a.get("items") or [])
        return {n for n in out if XV.type_of(n)}

    def entities_of_type(self, t: int, ty: str) -> list:
        return sorted(n for n in self.names(t) if XV.type_of(n) == ty) + [unnamed(ty)]

    def receptacles(self, t: int) -> list:
        out = set()
        for k in range(min(t, len(self.steps))):
            s = self.steps[k]
            if s["cmd"].get("args", {}).get("R"):
                out.add(s["cmd"]["args"]["R"])
            for a in s["atoms"]:
                if a["kind"] in RECEP_KINDS and a.get("recep"):
                    out.add(a["recep"])
        return sorted(out)

    # ---- v1 atoms that need an unnamed-entity reading ---------------------------------------------------------
    def _v_is_a(self, t, x, ty):
        if is_unnamed(x):
            return fact(unnamed_type(x) == ty, "static", [])
        return super()._v_is_a(t, x, ty)

    # ---- EXT event atoms (OBS; always determinate) -------------------------------------------------------------
    def _event(self, t, x, kinds):
        ks = [k for k in range(min(t, len(self.steps))) for a in self.steps[k]["atoms"]
              if a["kind"] in kinds and a.get("obj") == x]
        if ks:
            return fact(True, "record", [max(ks)])
        return fact(False, "record", [])

    def _v_activated(self, t, x):
        return self._event(t, x, ("turn_on",))

    def _v_picked(self, t, x):
        return self._event(t, x, ("pick_up",))

    def _v_released(self, t, x):
        return self._event(t, x, ("move_done",))

    # ---- EXT state on(X) -------------------------------------------------------------------------------------
    def _arrival_at(self, k: int):
        for j in range(k, -1, -1):
            for a in reversed(self.steps[j]["atoms"]):
                if a["kind"] == "arrive":
                    return a["recep"]
        return None

    def _invalidated(self, x, k_on, t, invs):
        here = self._arrival_at(k_on)
        for j in range(k_on + 1, min(t, len(self.steps))):
            if "any_step" in invs:
                return j, "any_step"
            for a in self.steps[j]["atoms"]:
                if "picked" in invs and a["kind"] == "pick_up" and a.get("obj") == x:
                    return j, "picked"
                if "any_arrival" in invs and a["kind"] == "arrive":
                    return j, "any_arrival"
                if "arrival_elsewhere" in invs and a["kind"] == "arrive" and a["recep"] != here:
                    return j, "arrival_elsewhere"
                if "turn_off" in invs and a["kind"] == "turn_off" and a.get("obj") == x:
                    return j, "turn_off"
        return None

    def _v_on(self, t, x):
        n = min(t, len(self.steps))
        last_on = last_off = None
        for k in range(n):
            for a in self.steps[k]["atoms"]:
                if a["kind"] == "turn_on" and a.get("obj") == x:
                    last_on = k
                elif a["kind"] == "turn_off" and a.get("obj") == x:
                    last_off = k
        if last_off is not None and (last_on is None or last_off > last_on):
            return self._timed(t, last_off, False, [last_off])   # an observed turn-off (v1 timing rules)
        if last_on is None:
            if "init_off" in self.A:
                return fact(False, "inferred", [-1], ("init_off",))
            return fact(None, "none")
        if last_on == t - 1:
            return fact(True, "observed", [last_on])
        rules = [c for c in self.persists if c.get("state") == "on"]
        if not rules:
            return fact(None, "stale", [last_on])
        r = rules[0]
        inv = self._invalidated(x, last_on, t, r.get("invalidated_by") or [])
        if inv is not None:
            f = fact(False, "inferred", [last_on, inv[0]])
            f["learned"], f["invalidated_by"] = [r["id"]], inv[1]
            return f
        f = fact(True, "inferred", [last_on])
        f["learned"] = [r["id"]]
        return f

    # ---- EXT relation co_located(X) := holding(X) or exists R. at(R) and in(X, R) --------------------------------
    def _v_co_located(self, t, x):
        parts = [self.value(t, "holding", [x])]
        for r in self.receptacles(t):
            a, i = self.value(t, "at", [r]), self.value(t, "in", [x, r])
            v = k_and([a["v"], i["v"]])
            dec = [f for f in (a, i) if f["v"] is False] if v is False else ([a, i] if v else [f for f in (a, i) if f["v"] is None])
            parts.append(merge(v, dec))
        # receptacles not named so far: at(.) is False only under distinct_locations; in(X, .) is unknown
        parts.append(fact(False, "inferred", [], ("distinct_locations",)) if "distinct_locations" in self.A
                     else fact(None, "none"))
        v = k_or([p["v"] for p in parts])
        if v is True:
            return merge(True, [next(p for p in parts if p["v"] is True)])
        if v is False:
            return merge(False, parts)
        return merge(None, [p for p in parts if p["v"] is None])
