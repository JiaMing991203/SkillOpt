#!/usr/bin/env python3
"""Checking: evidence, gate scores for every kind, verdicts, and committed pre-observation predictions.

PORT PROVENANCE. A domain-parameterised `upstream/vx1/vx_check.py`: `Evidence`, `in_scope`, `verdict_at`,
`action_prediction`, `score`, `gate` and the reveal-order guard `Revealer` (from
`upstream/v1_core/replay.py`) have the same bodies with `XV`/`model["vocab"]` replaced by `dom`.

ADDED: the `concludes` branch of `score`, which classifies ONE ROW PER SUBJECT BINDING through
`fw_ir.evaluate_pointwise`, and `conclusions_at`, which is what a report reads.

THE GATE, unchanged from ALFWorld and identical for every kind and every proposer:
  * the schema and the vocabulary must validate;
  * ZERO determinate counterexamples over ALL permitted evidence so far;
  * at least one supporting case (for `requires`, a consistent success also supports).
A rule with a counterexample is never silently dropped: it is kept with status `refuted` and its
witnesses, because the counterexample is the finding.
"""
from __future__ import annotations

import fw_ir as IR
from fw_domain import canon, sha

MAX_IDS = 10


class RevealOrderError(RuntimeError):
    pass


class Revealer:
    """Each prediction is hash-committed before that step's observation is released. Carried over from
    `upstream/v1_core/replay.py` unchanged apart from reading a step dict rather than a (action, obs) pair."""

    def __init__(self, view: dict):
        self._v = view
        self.n = len(view["steps"])
        self.log: list = []
        self._committed: dict = {}
        self._revealed = -1

    def step_in(self, t: int) -> dict:
        if t != self._revealed + 1:
            raise RevealOrderError(f"step {t} requested out of order")
        s = dict(self._v["steps"][t])
        s.pop("obs", None)
        return s

    def commit(self, t: int, prediction: dict) -> str:
        if t != self._revealed + 1 or t in self._committed:
            raise RevealOrderError(f"commit of step {t} out of order")
        h = sha(canon(prediction))
        prev = self.log[-1]["chain"] if self.log else ""
        self._committed[t] = h
        self.log.append({"event": "commit", "t": t, "sha256": h, "chain": sha(prev + h)})
        return h

    def reveal(self, t: int) -> dict:
        if t not in self._committed or t != self._revealed + 1:
            raise RevealOrderError(f"reveal of step {t} before its prediction was committed")
        self._revealed = t
        prev = self.log[-1]["chain"]
        self.log.append({"event": "reveal", "t": t, "chain": sha(prev + f"reveal:{t}")})
        return self._v["steps"][t]


class Evidence:
    """The permitted records seen so far, with cached estimators. `episodes` are analyst views."""

    def __init__(self, dom, episodes=()):
        self.dom = dom
        self.episodes = list(episodes)
        self._est = {}

    def add(self, eps):
        self.episodes += list(eps)

    def est(self, ep, assumptions, persists=()):
        key = (ep["episode_id"], tuple(sorted(assumptions)),
               canon([IR.normalize(c) for c in persists]))
        if key not in self._est:
            self._est[key] = self.dom.estimator(ep.get("task_type"), ep["steps"], assumptions, persists)
        return self._est[key]


def in_scope(c: dict, verb, task_type) -> bool:
    return IR.applies_task(c, task_type) and (c["scope"] == "any" or c["scope"] == verb)


def _verb_of(st: dict):
    cmd = st.get("_cmd") or {}
    return cmd.get("verb") if cmd.get("kind") == "covered" else None


def summarize(sc: dict) -> dict:
    return {"applicable": sc["applicable"], "n_support": len(sc["support"]), "support": sc["support"][:MAX_IDS],
            "n_consistent": len(sc["consistent"]), "n_counter": len(sc["counter"]),
            "counter": sc["counter"][:MAX_IDS],
            "counter_kind": {k: sc["counter_kind"][k] for k in sc["counter"][:MAX_IDS]
                             if k in sc.get("counter_kind", {})},
            "counter_witness": {k: sc.get("counter_witness", {}).get(k)
                                for k in sc["counter"][:MAX_IDS] if k in sc.get("counter_witness", {})},
            "undetermined": sc["undetermined"], "neutral": sc["neutral"],
            "und_at_consequence": sc.get("und_at_consequence", 0)}


# ---- completion / conclusion verdicts ------------------------------------------------------------------
def verdict_at(model: dict, dom, est, i: int, tinfo: dict, task_type, rels=None) -> dict:
    """Completion verdict for the post-state of step i (t = i + 1). Unchanged ALFWorld semantics."""
    rels = IR.active(model, "completes") if rels is None else rels
    cmd = est.steps[i].get("_cmd") or {}
    verb = cmd.get("verb") if cmd.get("kind") == "covered" else None
    per = {}
    for c in rels:
        if not in_scope(c, verb, task_type):
            continue
        base = dict(cmd.get("args") or {}) if c["scope"] != "any" else {}
        per[c["id"]] = IR.evaluate(c, c["condition"], est, i + 1, base, tinfo, dom)
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
            "learned": sorted({x for r in per.values() for x in r["deps"]["learned"]}
                              | (set(per) if per else set()))}
    return {"verdict": v, "per": per, "deps": deps,
            "unknown": sorted({u for r in per.values() for u in r["unknown"]})[:12]}


def conclusions_at(model: dict, dom, est, i: int, tinfo: dict, task_type) -> list:
    """Every `concludes` rule in scope at the post-state of step i, with one row per subject binding.

    This is what a report reads: each row carries the subject, the Kleene values, what the value RESTS ON
    (`deps.assumptions` and `deps.learned`) and the unknown literals when it is undetermined. Nothing is
    rounded to a boolean here - that rounding is where a checker starts accusing an agent of its own gaps.
    """
    out = []
    cmd = est.steps[i].get("_cmd") or {}
    verb = cmd.get("verb") if cmd.get("kind") == "covered" else None
    for c in IR.active(model, "concludes"):
        if not in_scope(c, verb, task_type):
            continue
        base = dict(cmd.get("args") or {}) if c["scope"] != "any" else {}
        if c.get("guard"):
            g = IR.evaluate(c, c["guard"], est, i + 1, base, tinfo, dom)
            if g["v"] is not True:
                out.append({"contract": c["id"], "step": i, "guard": g["v"], "rows": [],
                            "skipped": f"guard {g['v']}"})
                continue
        rows = IR.evaluate_pointwise(c, est, i + 1, base, tinfo, dom)
        out.append({"contract": c["id"], "step": i, "guard": True, "rows": rows, "skipped": None})
    return out


# ---- action prediction ----------------------------------------------------------------------------------
def action_prediction(model: dict, dom, est, t: int, cmd: dict, tinfo: dict, task_type) -> dict:
    if cmd.get("kind") != "covered":
        return {"label": "uncovered", "fail_by": [], "ok_by": []}
    fail, ok, unk, req = [], [], [], 0
    for c in IR.active(model):
        if c["kind"] not in ("requires", "suffices") or c["verb"] != cmd["verb"] \
                or not IR.applies_task(c, task_type):
            continue
        r = IR.evaluate(c, c["condition"], est, t, dict(cmd.get("args") or {}), tinfo, dom)
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


# ---- gate scores ----------------------------------------------------------------------------------------
def _blank() -> dict:
    return {"applicable": 0, "counter": [], "support": [], "consistent": [], "undetermined": 0,
            "neutral": 0, "counter_kind": {}, "counter_witness": {}, "und_at_consequence": 0}


def score(c: dict, ev: Evidence, assumptions, persists, dom, label_fn=None) -> dict:
    """Support, counterexamples, consistent and undetermined cases over ALL permitted evidence so far."""
    out = _blank()
    for ep in ev.episodes:
        tt = ep.get("task_type")
        if not IR.applies_task(c, tt):
            continue
        est = ev.est(ep, assumptions, persists)
        ti = dom.task_info(ep.get("task")) or {}
        for i, st in enumerate(est.steps):
            aid = f"{ep['episode_id']}@{i}"
            verb = _verb_of(st)
            if c["kind"] == "completes":
                if not in_scope(c, verb, tt):
                    continue
                out["applicable"] += 1
                base = dict((st.get("_cmd") or {}).get("args") or {}) if c["scope"] != "any" else {}
                v = IR.evaluate(c, c["condition"], est, i + 1, base, ti, dom)["v"]
                lab = bool(label_fn(ep, i)) if label_fn else False
                if v is None:
                    out["undetermined"] += 1
                    out["und_at_consequence"] += int(lab)
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
            if c["kind"] == "concludes":
                if (c.get("subject_scope") or "all") == "once":
                    continue        # scored once per entity below, not once per step
                if not in_scope(c, verb, tt):
                    continue
                base = dict((st.get("_cmd") or {}).get("args") or {}) if c["scope"] != "any" else {}
                if c.get("guard"):
                    g = IR.evaluate(c, c["guard"], est, i + 1, base, ti, dom)["v"]
                    if g is not True:
                        out["neutral" if g is False else "undetermined"] += 1
                        continue
                for row in IR.evaluate_pointwise(c, est, i + 1, base, ti, dom):
                    out["applicable"] += 1
                    k = row["class"]
                    sid = f"{aid}:{canon(row['subject'])}"
                    if k == "support":
                        out["support"].append(sid)
                    elif k in ("counter", "counter_converse"):
                        out["counter"].append(sid)
                        out["counter_kind"][sid] = ("conditions_without_consequence" if k == "counter"
                                                    else "consequence_without_conditions")
                        out["counter_witness"][sid] = {
                            "subject": row["subject"],
                            "cond_witness": (row["cond"].get("witness") or {}),
                            "cons_basis": (row["cons"]["deps"]["bases"] if row["cons"] else {})}
                    elif k == "consistent":
                        out["consistent"].append(sid)
                    elif k == "undetermined":
                        out["undetermined"] += 1
                    else:
                        out["neutral"] += 1
                continue
            if c["kind"] == "persists" or verb != c.get("verb"):
                continue
            out["applicable"] += 1
            o = st.get("outcome")
            args = dict((st.get("_cmd") or {}).get("args") or {})
            if c["kind"] == "effect":
                if o != "ok":
                    out["neutral"] += 1
                    continue
                if c.get("guard"):
                    g = IR.evaluate(c, c["guard"], est, i, args, ti, dom)["v"]
                    if g is not True:
                        out["neutral" if g is False else "undetermined"] += 1
                        continue
                vals = [IR.evaluate({"exists": c.get("exists")}, [[l]], est, i + 1, args, ti, dom)["v"]
                        for l in c["effects"]]
                if any(v is False for v in vals):
                    out["counter"].append(aid)
                    out["counter_kind"][aid] = "effect_not_observed"
                elif all(v is True for v in vals):
                    out["support"].append(aid)
                else:
                    out["undetermined"] += 1
                continue
            v = IR.evaluate(c, c["condition"], est, i, args, ti, dom)["v"]
            if o == "unknown" or v is None:
                out["undetermined"] += 1
            elif c["kind"] == "requires":
                if v is False and o == "ok":
                    out["counter"].append(aid)
                    out["counter_kind"][aid] = "succeeded_without_the_requirement"
                elif v is False:
                    out["support"].append(aid)
                elif o == "ok":
                    out["consistent"].append(aid)
                else:
                    out["neutral"] += 1
            else:
                if v is True and o == "fail":
                    out["counter"].append(aid)
                    out["counter_kind"][aid] = "sufficient_condition_held_but_the_step_failed"
                elif v is True:
                    out["support"].append(aid)
                else:
                    out["neutral"] += 1
        if c["kind"] == "concludes" and (c.get("subject_scope") or "all") == "once":
            _score_once(c, out, ep, est, ti, tt, dom)
    return out


def settle_step(c: dict, est, ty_var: str, ent, tinfo, dom, n: int):
    """The first state at which the record settles BOTH sides of a `concludes` rule for this entity.

    Returns (t, row) with the pointwise row at that t, or (n, row at n) when it is never settled. The
    scan goes forward through the record and every value is read prefix-only at the t it stops at, so a
    judgement is never made with an observation the judged state had not yet seen.
    """
    last = None
    for t in range(1, n + 1):
        rows = IR.evaluate_pointwise(c, est, t, {}, tinfo, dom, only=ent)
        if not rows:
            continue
        last = (t, rows[0])
        if rows[0]["class"] != "undetermined":
            return last
    return last if last else (n, None)


def _consequence_at_end(est, c, ent, n):
    """The rule's consequence for this entity at the END of the record, memoised on the estimator.

    A sound and large short-cut: if the record never settles the consequence, no earlier state settles
    it either, so the forward scan can be skipped entirely. It is memoised because every candidate
    hypothesis in a search shares the same consequence atom, and the scan is what costs.
    """
    cache = getattr(est, "_cons_cache", None)
    if cache is None:
        cache = est._cons_cache = {}
    lit = c["consequence"]
    key = (lit["atom"], ent, n)
    if key not in cache:
        try:
            cache[key] = est.value(n, lit["atom"], [ent])["v"]
        except KeyError:
            cache[key] = None
    return cache[key]


def _score_once(c: dict, out: dict, ep: dict, est, ti, tt, dom) -> None:
    n = len(est.steps)
    ty = list((c.get("subject") or {}).values())[0]
    var = list((c.get("subject") or {}))[0]
    for ent in est.entities_of_type(n, ty):
        if _consequence_at_end(est, c, ent, n) is None:
            out["undetermined"] += 1
            continue
        t, row = settle_step(c, est, var, ent, ti, dom, n)
        if row is None:
            continue
        out["applicable"] += 1
        sid = f"{ep['episode_id']}@{t}:{ent}"
        k = row["class"]
        if k == "support":
            out["support"].append(sid)
        elif k in ("counter", "counter_converse"):
            out["counter"].append(sid)
            out["counter_kind"][sid] = ("conditions_without_consequence" if k == "counter"
                                        else "consequence_without_conditions")
            out["counter_witness"][sid] = {"subject": {var: ent}, "settled_at_step": t,
                                           "cond_witness": (row["cond"].get("witness") or {}),
                                           "cons_bases": row["cons"]["deps"]["bases"]}
        elif k == "consistent":
            out["consistent"].append(sid)
        elif k == "undetermined":
            out["undetermined"] += 1
        else:
            out["neutral"] += 1


def gate(sc: dict, kind: str) -> list:
    """Why this contract may NOT be accepted. [] means the gate passes."""
    why = []
    if sc["counter"]:
        why.append(f"{len(sc['counter'])} counterexample(s) in the evidence so far: "
                   f"{', '.join(sc['counter'][:6])}")
    n_support = len(sc["support"]) + (len(sc["consistent"]) if kind == "requires" else 0)
    if kind != "persists" and not n_support:
        why.append("no supporting case in the evidence so far")
    return why
