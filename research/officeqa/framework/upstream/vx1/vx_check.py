"""VX-1 checker (amendment A5 section 3.4 and 6): completion labels, post-observation verdicts, pre-action predictions,
and the gate scores of every contract kind. No network.

Episode (analyst view, the ONLY input of learner and checker): {episode_id, task, task_type, outcome, steps:[{i, action,
obs}]}. `outcome` is success | failure. Harness rule (declared analyst-visible, A5 section 4): an episode ends as soon as
the task is complete, so in a success the task became complete right after the LAST step and was not complete after any
earlier step; in a failure it was never complete. `label(ep, i)` is derived from that and nothing else. The evaluator-only
per-step `won` flags are never read here.

Completion verdict of a model at a state (post-state of step i), from its active `completes` relations in scope
(scope = the step's verb, or "any"; task type in context):
  COMPLETE (some relation True, none False) | NOT (all in-scope relations False) | UNKNOWN (otherwise) |
  CONFLICT (one True and one False: abstention) | NO_CLAIM (no relation in scope: abstention)
Pre-action prediction of step i: the same relations evaluated on a PREDICTED post-state = observed steps 0..i-1 plus a
synthetic step i built from the learned effect contracts of command i (guards checked on the pre-state; success
assumed); it is committed (hash chain, replay.Revealer) before observation i is revealed.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402
from vx_state import EstimatorX, k_and  # noqa: E402
from replay import Revealer  # noqa: E402  (frozen v1 reveal-order guard)


def label(ep: dict, i: int) -> bool:
    return ep["outcome"] == "success" and i == len(ep["steps"]) - 1


def tinfo_of(ep: dict) -> dict:
    return XV.task_info(ep["task"]) or {}


class Evidence:
    """The permitted episodes seen so far (analyst views with outcomes), with cached estimators."""

    def __init__(self, episodes=()):
        self.episodes = list(episodes)
        self._est = {}

    def add(self, eps):
        self.episodes += list(eps)

    def est(self, ep, assumptions, persists=()):
        key = (ep["episode_id"], tuple(sorted(assumptions)), XV.canon([IR.normalize(c) for c in persists]))
        if key not in self._est:
            self._est[key] = EstimatorX.of_steps(ep.get("task_type"), ep["steps"], assumptions, persists)
        return self._est[key]


def in_scope(c: dict, verb, task_type) -> bool:
    return IR.applies_task(c, task_type) and (c["scope"] == "any" or c["scope"] == verb)


def verdict_at(model: dict, est, i: int, tinfo: dict, task_type, rels=None) -> dict:
    """Completion verdict for the post-state of step i (t = i + 1)."""
    rels = IR.active(model, "completes") if rels is None else rels
    cmd = est.steps[i]["cmd"]
    verb = cmd.get("verb") if cmd.get("kind") == "covered" else None
    per = {}
    for c in rels:
        if not in_scope(c, verb, task_type):
            continue
        base = dict(cmd.get("args") or {}) if c["scope"] != "any" else {}
        per[c["id"]] = IR.evaluate(c, c["condition"], est, i + 1, base, tinfo, model["vocab"])
    vs = [r["v"] for r in per.values()]
    if not per:
        v = "NO_CLAIM"
    elif True in vs and False in vs:
        v = "CONFLICT"
    elif True in vs:
        v = "COMPLETE"
    elif all(x is False for x in vs):
        v = "NOT"
    else:
        v = "UNKNOWN"
    deps = {"assumptions": sorted({a for r in per.values() for a in r["deps"]["assumptions"]}),
            "learned": sorted({x for r in per.values() for x in r["deps"]["learned"]} | (set(per) if per else set()))}
    return {"verdict": v, "per": per, "deps": deps,
            "unknown": sorted({u for r in per.values() for u in r["unknown"]})[:12]}


def post_verdicts(model: dict, ep: dict) -> list:
    est = EstimatorX.of_steps(ep.get("task_type"), ep["steps"], model["assumptions"], IR.active(model, "persists"))
    ti = tinfo_of(ep)
    return [verdict_at(model, est, i, ti, ep.get("task_type")) for i in range(len(ep["steps"]))]


# ---- action prediction (requires / suffices) -------------------------------------------------------------------
def action_prediction(model: dict, est, t: int, cmd: dict, tinfo: dict, task_type) -> dict:
    if cmd.get("kind") != "covered":
        return {"label": "uncovered", "fail_by": [], "ok_by": []}
    fail, ok, unk, req = [], [], [], 0
    for c in IR.active(model):
        if c["kind"] not in ("requires", "suffices") or c["verb"] != cmd["verb"] or not IR.applies_task(c, task_type):
            continue
        r = IR.evaluate(c, c["condition"], est, t, dict(cmd["args"]), tinfo, model["vocab"])
        if c["kind"] == "requires":
            req += 1
            if r["v"] is False:
                fail.append(c["id"])
            elif r["v"] is None:
                unk.append(c["id"])
        elif r["v"] is True:
            ok.append(c["id"])
    if fail and ok:
        lab = "conflict"
    elif fail:
        lab = "fail"
    elif ok:
        lab = "ok"
    elif unk:
        lab = "undetermined"
    elif req:
        lab = "no_claim"
    else:
        lab = "uncovered"
    return {"label": lab, "fail_by": fail, "ok_by": ok}


SYNTH = {("at", True): lambda a: {"kind": "arrive", "recep": a[0]},
         ("on", True): lambda a: {"kind": "turn_on", "obj": a[0]},
         ("activated", True): lambda a: {"kind": "turn_on", "obj": a[0]},
         ("open", True): lambda a: {"kind": "open_done", "recep": a[0]},
         ("open", False): lambda a: {"kind": "close_done", "recep": a[0]}}


def synthetic_atoms(model: dict, est, t: int, cmd: dict, tinfo: dict, task_type) -> tuple:
    """Atoms of the predicted observation of command t from the learned effects (guards on the pre-state)."""
    atoms, by, skipped = [], [], []
    if cmd.get("kind") != "covered":
        return atoms, by, skipped
    args = dict(cmd["args"])
    for c in IR.active(model, "effect"):
        if c["verb"] != cmd["verb"] or not IR.applies_task(c, task_type):
            continue
        if c.get("guard"):
            g = IR.evaluate(c, c["guard"], est, t, args, tinfo, model["vocab"])
            if g["v"] is not True:
                skipped.append({"contract": c["id"], "why": f"guard {g['v']}"})
                continue
        for l in c["effects"]:
            if any(x.startswith("?") for x in l["args"]):
                skipped.append({"contract": c["id"], "why": "effect with a variable is not simulated"})
                continue
            kinds = XV.VOCABS[model["vocab"]]["atoms"][l["atom"]][0]
            g = [IR.ground(a, k, args, tinfo) for k, a in zip(kinds, l["args"])]
            key = (l["atom"], bool(l["value"]))
            if key in SYNTH:
                atoms.append(SYNTH[key](g))
            elif key == ("holding", True):
                atoms.append({"kind": "pick_up", "obj": g[0], "recep": args.get("R")})
            elif key in (("holding", False),) and args.get("R"):
                atoms.append({"kind": "move_done", "obj": g[0], "recep": args.get("R")})
            elif key == ("in", True):
                atoms.append({"kind": "move_done", "obj": g[0], "recep": g[1]})
            else:
                skipped.append({"contract": c["id"], "why": f"{l['atom']}={l['value']} is not simulated"})
                continue
            by.append(c["id"])
    order = {"arrive": 0}
    atoms.sort(key=lambda a: order.get(a["kind"], 1))
    return atoms, sorted(set(by)), skipped


def predict_episode(model: dict, ep_view: dict) -> list:
    """Pre-action predictions for every step of an episode VIEW (no outcome field is read). Uses the v1 Revealer: each
    prediction is committed (hash chain) before that step's observation is released."""
    assert "outcome" not in ep_view, "prediction views must not carry the outcome"
    rv = Revealer(ep_view)
    ti = tinfo_of(ep_view)
    A, P = model["assumptions"], IR.active(model, "persists")
    est = EstimatorX(A, ep_view.get("task_type"), P)
    out = []
    for t in range(rv.n):
        action = rv.action(t)
        cmd = XV.parse_command(action)
        ap = action_prediction(model, est, t, cmd, ti, ep_view.get("task_type"))
        atoms, by, skipped = synthetic_atoms(model, est, t, cmd, ti, ep_view.get("task_type"))
        sim = EstimatorX(A, ep_view.get("task_type"), P)
        sim.steps = list(est.steps)
        sim.add_synthetic(action, atoms, by)
        cv = verdict_at(model, sim, t, ti, ep_view.get("task_type"))
        pred = {"t": t, "action": action, "action_prediction": ap,
                "completion": {"verdict": cv["verdict"], "deps": cv["deps"], "unknown": cv["unknown"]},
                "synthetic": {"atoms": [{k: v for k, v in a.items() if k != "quote"} for a in atoms], "by": by,
                              "skipped": skipped}}
        pred["commit"] = rv.commit(t, pred)
        out.append(pred)
        est.observe(action, rv.reveal(t))
    return out


# ---- gate scores ---------------------------------------------------------------------------------------------------
def score(c: dict, ev: Evidence, assumptions, persists, vname: str) -> dict:
    out = {"applicable": 0, "counter": [], "support": [], "consistent": [], "undetermined": 0, "neutral": 0,
           "counter_kind": {}, "und_at_completion": 0}
    for ep in ev.episodes:
        tt = ep.get("task_type")
        if not IR.applies_task(c, tt):
            continue
        est = ev.est(ep, assumptions, persists)
        ti = tinfo_of(ep)
        for i, st in enumerate(est.steps):
            aid = f"{ep['episode_id']}@{i}"
            cmd = st["cmd"]
            verb = cmd.get("verb") if cmd.get("kind") == "covered" else None
            if c["kind"] == "completes":
                if not in_scope(c, verb, tt):
                    continue
                out["applicable"] += 1
                base = dict(cmd.get("args") or {}) if c["scope"] != "any" else {}
                v = IR.evaluate(c, c["condition"], est, i + 1, base, ti, vname)["v"]
                lab = label(ep, i)
                if v is None:
                    out["undetermined"] += 1
                    out["und_at_completion"] += int(lab)
                elif v and lab:
                    out["support"].append(aid)
                elif v and not lab:
                    out["counter"].append(aid)
                    out["counter_kind"][aid] = "false_completion"
                elif (not v) and lab:
                    out["counter"].append(aid)
                    out["counter_kind"][aid] = "missed_completion"
                else:
                    out["consistent"].append(aid)
                continue
            if c["kind"] == "persists" or verb != c.get("verb"):
                continue
            out["applicable"] += 1
            o = st["outcome"]
            if c["kind"] == "effect":
                if o != "ok":
                    out["neutral"] += 1
                    continue
                if c.get("guard"):
                    g = IR.evaluate(c, c["guard"], est, i, dict(cmd["args"]), ti, vname)["v"]
                    if g is not True:
                        out["neutral" if g is False else "undetermined"] += 1
                        continue
                vals = [IR.evaluate({"exists": c.get("exists")}, [[l]], est, i + 1, dict(cmd["args"]), ti, vname)["v"]
                        for l in c["effects"]]
                if any(v is False for v in vals):
                    out["counter"].append(aid)
                elif all(v is True for v in vals):
                    out["support"].append(aid)
                else:
                    out["undetermined"] += 1
                continue
            v = IR.evaluate(c, c["condition"], est, i, dict(cmd["args"]), ti, vname)["v"]
            if o == "unknown" or v is None:
                out["undetermined"] += 1
            elif c["kind"] == "requires":
                if v is False and o == "ok":
                    out["counter"].append(aid)
                elif v is False:
                    out["support"].append(aid)
                elif o == "ok":
                    out["consistent"].append(aid)
                else:
                    out["neutral"] += 1
            else:
                if v is True and o == "fail":
                    out["counter"].append(aid)
                elif v is True:
                    out["support"].append(aid)
                else:
                    out["neutral"] += 1
    if c["kind"] == "persists":
        out.update(persists_direct(c, ev))
    return out


def persists_direct(c: dict, ev: Evidence) -> dict:
    """Direct record contradictions of a persists rule: an observed turn-off while the rule says on (no listed
    invalidator before it). None occur in the grammar so far; repeated 'You turn on' reports are NOT contradictions."""
    counter = []
    for ep in ev.episodes:
        on_since = {}
        for i, st in enumerate(ep["steps"]):
            for a in XV.parse_obs(st["obs"]):
                if a["kind"] == "turn_on":
                    on_since[a["obj"]] = i
                elif a["kind"] == "turn_off" and a["obj"] in on_since and "turn_off" not in c["invalidated_by"]:
                    counter.append(f"{ep['episode_id']}@{i}")
    return {"counter": counter, "direct_check": "observed turn-off inside a claimed on-interval"}


def gate(sc: dict, kind: str) -> list:
    why = []
    if sc["counter"]:
        why.append(f"{len(sc['counter'])} counterexample(s) in the evidence so far: {', '.join(sc['counter'][:6])}")
    n_support = len(sc["support"]) + (len(sc["consistent"]) if kind == "requires" else 0)   # v1: consistent
    if kind != "persists" and not n_support:                                                # successes support
        why.append("no supporting case in the evidence so far")                            # a requires contract
    return why
