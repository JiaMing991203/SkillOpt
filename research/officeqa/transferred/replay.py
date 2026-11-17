"""Prefix-only prediction from a FROZEN model, then reveal and classify (design v0.1 section 4.D). No network.

`Revealer` holds one finished episode privately. For each step it hands out the command, accepts the checker's
prediction (`commit`, which appends the prediction's sha256 to an order log and to the caller's sink), and only
then releases that step's observation (`reveal`). Revealing an uncommitted step raises. The checker's state
estimate is built from released observations only, so a prediction can never see its own outcome.

Prediction of one attempt from the model's ACTIVE contracts for the verb (and context):
    fail          some requires-condition is False (and no suffices-condition True)
    ok            some suffices-condition is True (and no requires-condition False)
    conflict      both
    undetermined  no decisive contract, and some condition is unknown
    no_claim      every applicable requires-condition is True and no suffices applies (necessary conditions
                  alone never predict success)
    uncovered     no applicable contract, or the command is outside the covered verbs
Classes after reveal (outcome ok / fail / unknown read from the observation, vocab.outcome_of):
    refuted_requires  predicted fail, observed ok   -> counterexample to the false requires contract(s), or a
                                                       faulty state estimate / extraction (retained as alternatives)
    confirmed_fail    predicted fail, observed fail -> conditional execution diagnosis (known unmet requirement)
    refuted_suffices  predicted ok, observed fail   -> counterexample to the suffices contract(s), or state error
    confirmed_ok      predicted ok, observed ok
    conflict          the model contradicts itself on this attempt
    undetermined | no_claim_ok | unexplained_fail (no contract explains a failure: a possible missing requirement)
    uncovered | outcome_unknown
Effects (after an ok outcome): each applicable effect literal is checked on the post-state (the state estimate
after the step's own observation): False -> effect_violated (counterexample), True -> confirmed, None -> undetermined.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ir import active, applies, eval_cnf, ground_args, lit_value  # noqa: E402
from state import Estimator, lit_key  # noqa: E402
from vocab import canon, parse_command, sha, VERBS  # noqa: E402


class RevealOrderError(RuntimeError):
    pass


class Revealer:
    def __init__(self, ep: dict):
        self._ep = ep
        self.n = len(ep["steps"])
        self.log: list = []
        self._committed: dict = {}
        self._revealed = -1

    def action(self, t: int) -> str:
        if t != self._revealed + 1:
            raise RevealOrderError(f"step {t} requested out of order")
        return self._ep["steps"][t]["action"]

    def commit(self, t: int, prediction: dict) -> str:
        if t != self._revealed + 1 or t in self._committed:
            raise RevealOrderError(f"commit of step {t} out of order")
        h = sha(canon(prediction))
        prev = self.log[-1]["chain"] if self.log else ""
        self._committed[t] = h
        self.log.append({"event": "commit", "t": t, "sha256": h, "chain": sha(prev + h)})
        return h

    def reveal(self, t: int) -> str:
        if t not in self._committed or t != self._revealed + 1:
            raise RevealOrderError(f"reveal of step {t} before its prediction was committed")
        self._revealed = t
        prev = self.log[-1]["chain"]
        self.log.append({"event": "reveal", "t": t, "chain": sha(prev + f"reveal:{t}")})
        return self._ep["steps"][t]["obs"]


def binding_of(cmd: dict) -> dict:
    return dict(cmd.get("args") or {})


def lit_fact(est: Estimator, t: int, lit: dict, binding: dict) -> dict:
    return est.value(t, lit["atom"], ground_args(lit, binding))


def evaluate_contract(c: dict, est: Estimator, t: int, binding: dict, facts: dict) -> object:
    """Kleene value of a requires/suffices condition at the pre-state of command t; facts collects the ground
    literal values used (key -> fact)."""
    def valfn(l):
        g = ground_args(l, binding)
        key = lit_key(l["atom"], g)
        if key not in facts:
            facts[key] = est.value(t, l["atom"], g)
        return lit_value(l, facts[key]["v"])
    return eval_cnf(c["condition"], valfn)


def predict(model: dict, cmd: dict, est: Estimator, t: int, task_type: str | None) -> dict:
    if cmd.get("kind") != "covered":
        return {"label": "uncovered", "why": f"command kind {cmd.get('kind')}", "contracts": {}, "facts": {},
                "effects": []}
    binding = binding_of(cmd)
    facts: dict = {}
    per = {}
    fail, ok, unk, req_n = [], [], [], 0
    effects = []
    for c in active(model):
        if not applies(c, cmd["verb"], task_type):
            continue
        if c["kind"] == "effect":
            effects.append({"contract": c["id"], "lits": c["effects"]})
            continue
        v = evaluate_contract(c, est, t, binding, facts)
        per[c["id"]] = v
        if c["kind"] == "requires":
            req_n += 1
            if v is False:
                fail.append(c["id"])
            elif v is None:
                unk.append(c["id"])
        else:
            if v is True:
                ok.append(c["id"])
            elif v is None:
                unk.append(c["id"])
    if not per:
        label = "uncovered"
    elif fail and ok:
        label = "conflict"
    elif fail:
        label = "fail"
    elif ok:
        label = "ok"
    elif unk:
        label = "undetermined"
    else:
        label = "no_claim"
    return {"label": label, "contracts": per, "fail_by": fail, "ok_by": ok, "unknown_by": unk, "facts": facts,
            "effects": effects}


def classify(pred: dict, outcome: str) -> str:
    lab = pred["label"]
    if lab == "uncovered":
        return "uncovered"
    if outcome == "unknown":
        return "outcome_unknown"
    if lab == "conflict":
        return "conflict"
    if lab == "fail":
        return "refuted_requires" if outcome == "ok" else "confirmed_fail"
    if lab == "ok":
        return "confirmed_ok" if outcome == "ok" else "refuted_suffices"
    if lab == "undetermined":
        return "undetermined"
    return "no_claim_ok" if outcome == "ok" else "unexplained_fail"


def replay_episode(ep: dict, model: dict, sink=None) -> dict:
    """Prefix-only replay of one finished episode with a frozen model. sink(record) is called with each
    prediction BEFORE its observation is revealed (the caller persists it)."""
    rev = Revealer(ep)
    est = Estimator(model.get("assumptions") or [], ep.get("task_type"))
    attempts = []
    for t in range(rev.n):
        action = rev.action(t)
        cmd = parse_command(action)
        pred = predict(model, cmd, est, t, ep.get("task_type"))
        rec = {"aid": f"{ep['episode_id']}@{t}", "episode": ep["episode_id"], "task_id": ep.get("task_id"),
               "task_type": ep.get("task_type"), "t": t, "action": action, "verb": cmd.get("verb"),
               "cmd_kind": cmd["kind"], "args": cmd.get("args") or {}, "model_sha256": model.get("sha256"),
               "prediction": pred}
        h = rev.commit(t, rec)
        if sink is not None:
            sink(dict(rec, commit_sha256=h))
        obs = rev.reveal(t)
        s = est.observe(action, obs)
        rec["outcome"] = s["outcome"]
        rec["obs"] = obs
        rec["class"] = classify(pred, s["outcome"])
        effs = []
        if s["outcome"] == "ok":
            for e in pred["effects"]:
                for l in e["lits"]:
                    f = est.value(t + 1, l["atom"], ground_args(l, binding_of(cmd)))
                    v = lit_value(l, f["v"])
                    effs.append({"contract": e["contract"], "lit": l, "post": f,
                                 "class": "effect_confirmed" if v is True else
                                 ("effect_violated" if v is False else "effect_undetermined")})
        rec["effect_checks"] = effs
        attempts.append(rec)
    return {"episode": ep["episode_id"], "attempts": attempts, "reveal_log": rev.log}


COVERED_CLASSES = ("refuted_requires", "confirmed_fail", "refuted_suffices", "confirmed_ok", "conflict")


def score(attempts: list) -> dict:
    """Pre-update performance of the frozen model on these attempts (or post-fit agreement when the attempts were
    replayed with the fitted model: the caller labels which)."""
    cls = {}
    per_verb: dict = {}
    for a in attempts:
        cls[a["class"]] = cls.get(a["class"], 0) + 1
        if a["cmd_kind"] == "covered":
            pv = per_verb.setdefault(a["verb"], {"attempts": 0, "ok": 0, "fail": 0, "unknown": 0, "determinate": 0,
                                                  "correct": 0, "wrong": 0})
            pv["attempts"] += 1
            pv[a["outcome"]] += 1
            if a["class"] in COVERED_CLASSES:
                pv["determinate"] += 1
                pv["correct"] += a["class"] in ("confirmed_fail", "confirmed_ok")
                pv["wrong"] += a["class"] in ("refuted_requires", "refuted_suffices", "conflict")
    covered = [a for a in attempts if a["cmd_kind"] == "covered" and a["outcome"] != "unknown"]
    det = [a for a in covered if a["class"] in COVERED_CLASSES]
    eff = [e for a in attempts for e in a.get("effect_checks", [])]
    return {"attempts": len(attempts), "covered_known_outcome": len(covered),
            "outcomes": {o: sum(1 for a in covered if a["outcome"] == o) for o in ("ok", "fail")},
            "uncovered_commands": sum(1 for a in attempts if a["cmd_kind"] != "covered"),
            "determinate_predictions": len(det),
            "coverage": (len(det) / len(covered)) if covered else None,
            "correct": sum(1 for a in det if a["class"] in ("confirmed_fail", "confirmed_ok")),
            "wrong": sum(1 for a in det if a["class"] in ("refuted_requires", "refuted_suffices", "conflict")),
            "classes": dict(sorted(cls.items())), "per_verb": per_verb,
            "effects": {k: sum(1 for e in eff if e["class"] == k) for k in
                        ("effect_confirmed", "effect_violated", "effect_undetermined")}}


# ---- evidence for one contract over finished episodes (learning / re-scoring; prefix-limited facts) -----------
class Evidence:
    """All finished episodes known so far, with cached estimators per assumption set."""

    def __init__(self, episodes: list):
        self.episodes = list(episodes)
        self._est: dict = {}

    def add(self, episodes: list):
        self.episodes += list(episodes)

    def est(self, ep: dict, assumptions) -> Estimator:
        key = (ep["episode_id"], tuple(sorted(assumptions)))
        if key not in self._est:
            self._est[key] = Estimator.of_episode(ep, assumptions)
        return self._est[key]

    def attempts(self, verb: str | None = None):
        for ep in self.episodes:
            for t, st in enumerate(ep["steps"]):
                cmd = parse_command(st["action"])
                if cmd["kind"] != "covered" or (verb and cmd["verb"] != verb):
                    continue
                yield ep, t, cmd


def score_contract(c: dict, ev: Evidence, assumptions, epoch_of=None) -> dict:
    """Support / counterexamples of one contract over all evidence (direction-aware):
    requires: ok & cond False -> COUNTER; fail & cond False -> support_fail (explains a failure);
              ok & cond True -> support_ok (consistent success); fail & cond True -> neutral; unknown -> undetermined
    suffices: fail & cond True -> COUNTER; ok & cond True -> support_ok; otherwise neutral / undetermined
    effect:   ok & effect literal False after -> COUNTER; all True -> support_ok; otherwise undetermined"""
    out = {"applicable": 0, "counter": [], "support_fail": [], "support_ok": [], "neutral": 0, "undetermined": 0,
           "rows": []}
    for ep, t, cmd in ev.attempts(c["verb"]):
        if not applies(c, cmd["verb"], ep.get("task_type")):
            continue
        est = ev.est(ep, assumptions)
        o = est.steps[t]["outcome"]
        aid = f"{ep['episode_id']}@{t}"
        out["applicable"] += 1
        b = binding_of(cmd)
        if c["kind"] == "effect":
            if o != "ok":
                out["neutral"] += 1
                out["rows"].append({"aid": aid, "out": o, "vals": None, "cls": "neutral"})
                continue
            vals = [lit_value(l, est.value(t + 1, l["atom"], ground_args(l, b))["v"]) for l in c["effects"]]
            cls = "counter" if any(v is False for v in vals) else ("support_ok" if all(v is True for v in vals)
                                                                      else "undetermined")
        else:
            facts: dict = {}
            vals = [[lit_value(l, est.value(t, l["atom"], ground_args(l, b))["v"]) for l in cl]
                    for cl in c["condition"]]
            v = evaluate_contract(c, est, t, b, facts)
            if o == "unknown" or v is None:
                cls = "undetermined"
            elif c["kind"] == "requires":
                cls = {(False, "ok"): "counter", (False, "fail"): "support_fail", (True, "ok"): "support_ok",
                       (True, "fail"): "neutral"}[(v, o)]
            else:
                cls = {(True, "fail"): "counter", (True, "ok"): "support_ok"}.get((v, o), "neutral")
        if cls in ("counter", "support_fail", "support_ok"):
            out[cls].append(aid)
        else:
            out[cls if cls == "undetermined" else "neutral"] = out[cls if cls == "undetermined" else "neutral"] + 1
        out["rows"].append({"aid": aid, "out": o, "vals": vals, "cls": cls, "task_id": ep.get("task_id")})
    out["support"] = len(out["support_fail"]) + len(out["support_ok"])
    out["tasks_supporting"] = sorted({r["task_id"] for r in out["rows"]
                                      if r["cls"] in ("support_fail", "support_ok") and r.get("task_id")})
    return out


def jsonl_sink(path: str):
    f = open(path, "a", encoding="utf-8")

    def sink(rec):
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return sink


def covered_verbs() -> list:
    return sorted(VERBS)
