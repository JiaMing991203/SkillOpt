"""Amendment A4 sections 3.1-3.2: the shared observational fact ledger and learned-effect propagation.

What it is. `LedgerEstimator` is the frozen prefix-only state estimator (state.Estimator) plus a ledger of facts
inferred from LEARNED EFFECTS after OBSERVED successes. Every literal value it returns lists all of its supports:

  OBSERVED   the frozen estimator's value (observation sentences read through the built-in readings B1-B4 and the
             model's named assumptions; basis observed / record / static / inferred(assumption)).
  INFERRED   a learned effect literal of contract c (id, version) after the observed success of command k: valid from
             the pre-state of k+1; at later steps only under the model's `persistence` assumption (exactly like an
             observation); withdrawn when a later observed command's own report changes that literal; CONTRADICTED
             when a later observation states a different value that no observed command explains.
  FORECAST   at PRE of command t: what its learned effects would make true IF it succeeds. Never entered as a fact;
             resolved at POST as CONFIRMED (success observed), DISCARDED (failure observed) or UNRESOLVED.

Precedence (fixed before use): the newest support wins; at equal age the observation wins. A value is
  OBS_ONLY      the observation-based estimator decides it; no inferred support
  OVERLAP       observed and inferred supports agree (the learned effect adds no information)
  ADDITIONAL    only an inferred support decides it: the model's observation estimator says unknown / stale AND the
                ordinary tracker (the observation estimator under the shared reading's declared assumptions
                persistence + init_empty_hands, stage_common) does not give the same value either
  OVERLAP_TRACKER  the model's estimator says unknown, but the ordinary tracker gives the same value: overlapping
                evidence, NOT a contribution of the learned effect
  CONFLICT      an observed and an inferred support disagree; the newer one decides it (the inferred one only if it
                is strictly newer), the other is recorded
A necessary condition never establishes success; a predicted success is never stored as an observed result.

propagate=False gives exactly the frozen estimator (the checking-only role of effects; arms F / R, conditions A / B).
"""
from __future__ import annotations

import ir
import state
from state import fact
from vocab import parse_command, type_of

# A successful command whose OWN report atom concerns a literal changes it (the inferred support is WITHDRAWN: the
# literal's value now comes from that command). Anything else that states a different value (an inventory report, an
# arrival listing, examine) CONTRADICTS the inferred support (review fix 2026-11-11, before the freeze).
def _changes(a, atom, args):
    k = a["kind"]
    if atom in ("at", "arrived"):
        return k == "arrive"
    if atom == "holding_any":
        return k in ("pick_up", "move_done")
    if atom == "holding":
        return k in ("pick_up", "move_done") and a.get("obj") == args[0]
    if atom == "in":
        return k in ("pick_up", "move_done") and a.get("obj") == args[0]
    if atom == "open":
        return k in ("open_done", "close_done") and a.get("recep") == args[0]
    return False


TRACKER_ASSUMPTIONS = ("persistence", "init_empty_hands")   # stage_common.ASSUMPTIONS (name_reading is implicit)


def lit_key(atom, args):
    return state.lit_key(atom, list(args))


class LedgerEstimator(state.Estimator):
    def __init__(self, assumptions=(), task_type=None, model=None, propagate=True):
        super().__init__(assumptions, task_type)
        self.model = model
        self.propagate = bool(propagate and model is not None)
        self.inferred: dict = {}     # key -> [support]  support = {v, k, contract, version, status, ...}
        self.forecasts: list = []    # {t, key, v, contract, version, status}
        self.events: list = []       # contradictions / withdrawals: {t, key, kind, support}
        self.queries: list = []      # audit: every value query {t, key, class, v, observed, inferred}
        self._trk = state.Estimator(TRACKER_ASSUMPTIONS, task_type)   # the ordinary tracker's reading
        self._trk.steps = self.steps                                     # shares the same observed steps

    # ---------------------------------------------------------------- effects of one command (grounded)
    def effect_lits(self, cmd):
        if self.model is None or cmd.get("kind") != "covered":
            return []
        out = []
        b = dict(cmd.get("args") or {})
        for c in ir.active(self.model):
            if c["kind"] != "effect" or not ir.applies(c, cmd["verb"], self.task_type):
                continue
            for l in c["effects"]:
                try:
                    g = ir.ground_args(l, b)
                except KeyError:
                    continue
                out.append({"key": lit_key(l["atom"], g), "atom": l["atom"], "args": g, "v": bool(l["value"]),
                            "contract": c["id"], "version": c.get("version")})
        return out

    def forecast(self, t, action):
        """FORECAST literals for command t (never facts)"""
        cmd = parse_command(action)
        fs = [dict(e, t=t, status="PENDING", kind="FORECAST") for e in self.effect_lits(cmd)]
        self.forecasts.extend(fs)
        return fs

    # ---------------------------------------------------------------- observe one step (POST of command k)
    def observe(self, action, obs):
        s = super().observe(action, obs)
        k = len(self.steps) - 1
        for f in self.forecasts:
            if f["t"] == k and f["status"] == "PENDING":
                f["status"] = {"ok": "CONFIRMED", "fail": "DISCARDED"}.get(s["outcome"], "UNRESOLVED")
        if not self.propagate:
            return s
        own = []   # report atoms of THIS command (its own success sentence), only when it succeeded
        if s["outcome"] == "ok" and s["cmd"].get("kind") == "covered":
            rk = __import__("vocab").VERBS[s["cmd"]["verb"]]["report"][0]
            own = [a for a in s["atoms"] if a["kind"] == rk]
        # 1. withdrawals (an observed command changed the literal) / contradictions (an observation disagrees)
        for key, sups in self.inferred.items():
            act = [x for x in sups if x["status"] == "ACTIVE"]
            if not act:
                continue
            sup = act[-1]
            atom, args = sup["atom"], sup["args"]
            if any(_changes(a, atom, args) for a in own):
                sup["status"], sup["ended"] = "WITHDRAWN", k
                self.events.append({"t": k, "key": key, "kind": "WITHDRAWN", "support": dict(sup)})
                continue
            obs_f = super().value(k + 1, atom, args)
            if obs_f["v"] is None or k not in obs_f["steps"] or obs_f["v"] == sup["v"]:
                continue
            sup["status"], sup["ended"] = "CONTRADICTED", k
            self.events.append({"t": k, "key": key, "kind": "CONTRADICTED", "support": dict(sup),
                                "observed": obs_f})
        # 2. new inferred supports from this command's learned effects after an OBSERVED success
        if s["outcome"] == "ok":
            for e in self.effect_lits(s["cmd"]):
                for x in self.inferred.get(e["key"], []):
                    if x["status"] == "ACTIVE":
                        x["status"] = "SUPERSEDED"
                        x["ended"] = k
                self.inferred.setdefault(e["key"], []).append(
                    dict(e, k=k, status="ACTIVE", kind="INFERRED",
                         assumptions=sorted(self.model.get("assumptions") or [])))
        return s

    # ---------------------------------------------------------------- values with supports
    def inferred_at(self, t, key):
        """the newest inferred support usable at the pre-state of command t (or None), timed like an observation"""
        best = None
        for x in self.inferred.get(key, []):
            if x["k"] >= t:
                continue
            ended = x.get("ended")
            if ended is not None and ended < t:
                continue
            if best is None or x["k"] > best["k"]:
                best = x
        if best is None:
            return None
        if best["k"] == t - 1:
            return dict(best, timed="current")
        if "persistence" in self.A:
            return dict(best, timed="persistence")
        return None

    def value(self, t, atom, args):
        f = super().value(t, atom, args)
        if not self.propagate:
            return f
        key = lit_key(atom, args)
        inf = self.inferred_at(t, key)
        if inf is None:
            cls, out = "OBS_ONLY", f
        else:
            ia = sorted(set(inf["assumptions"]) | ({"persistence"} if inf["timed"] == "persistence" else set()))
            isup = {"contract": inf["contract"], "version": inf["version"], "step": inf["k"], "v": inf["v"],
                    "assumptions": ia}
            obs_k = max(f["steps"]) if f["steps"] else -2
            if f["v"] is None:
                tv = self._trk.value(t, atom, args)
                cls = "OVERLAP_TRACKER" if tv["v"] == inf["v"] else "ADDITIONAL"
                out = fact(inf["v"], "effect", [inf["k"]], ia)
            elif f["v"] == inf["v"]:
                cls, out = "OVERLAP", dict(f)
            elif inf["k"] > obs_k:
                cls = "CONFLICT"
                out = fact(inf["v"], "effect", [inf["k"]], ia)
            else:
                cls, out = "CONFLICT", dict(f)
            out = dict(out, effect_support=isup)
        out = dict(out, support_class=cls)
        self.queries.append({"t": t, "key": key, "class": cls, "v": out["v"],
                             "observed": {"v": f["v"], "basis": f["basis"], "steps": f["steps"]},
                             "inferred": out.get("effect_support")})
        return out


def replay_episode(ep, model, propagate, sink=None):
    """state-based replay (replay.replay_episode semantics) with the ledger estimator; forecasts at PRE, commit
    before reveal (the caller's sink receives the prediction before the observation is read)."""
    import replay as RP
    rev = RP.Revealer(ep)
    est = LedgerEstimator(model.get("assumptions") or [], ep.get("task_type"), model, propagate)
    att = []
    for t in range(rev.n):
        action = rev.action(t)
        cmd = parse_command(action)
        nq = len(est.queries)
        pred = RP.predict(model, cmd, est, t, ep.get("task_type"))
        used = [q for q in est.queries[nq:] if q["t"] == t]
        fc = est.forecast(t, action)
        rec = {"aid": f"{ep['episode_id']}@{t}", "t": t, "action": action, "verb": cmd.get("verb"),
               "cmd_kind": cmd["kind"], "prediction": pred, "support_classes": sorted({q["class"] for q in used}),
               "forecast": [{k: f[k] for k in ("key", "v", "contract")} for f in fc]}
        h = rev.commit(t, rec)
        if sink is not None:
            sink(dict(rec, commit_sha256=h))
        obs = rev.reveal(t)
        s = est.observe(action, obs)
        rec["outcome"] = s["outcome"]
        rec["class"] = RP.classify(pred, s["outcome"])
        rec["forecast_status"] = sorted({f["status"] for f in est.forecasts if f["t"] == t})
        effs = []
        if s["outcome"] == "ok":
            for e in pred["effects"]:
                for l in e["lits"]:
                    g = ir.ground_args(l, RP.binding_of(cmd))
                    fv = state.Estimator.value(est, t + 1, l["atom"], g)   # checking role: observation-based only
                    v = ir.lit_value(l, fv["v"])
                    effs.append({"contract": e["contract"], "key": lit_key(l["atom"], g),
                                 "class": "effect_confirmed" if v is True else
                                 ("effect_violated" if v is False else "effect_undetermined"),
                                 "restates": bool(fv["steps"]) and t in fv["steps"]})
        rec["effect_checks"] = effs
        att.append(rec)
    return {"episode": ep["episode_id"], "attempts": att, "queries": est.queries, "events": est.events,
            "forecasts": est.forecasts, "inferred": est.inferred, "reveal_log": rev.log}


def support_summary(res):
    """counts of value classes over the predictions of one replay (what propagation changed)"""
    from collections import Counter
    c = Counter(q["class"] for q in res["queries"])
    return dict(c)
