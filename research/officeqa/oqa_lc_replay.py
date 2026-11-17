"""Prefix-only prediction from a FROZEN OfficeQA model, then reveal and classify (design v0.1 section 4.D).

Port of `transferred/replay.py` (source sha256
`...` recorded in OFFICEQA_TRANSFER_MANIFEST.json). The commit-before-reveal discipline, the prediction labels, the
discrepancy classes, `Evidence` and `score_contract` are the ALFWorld semantics; what changed is below. No network.

`Revealer` holds one finished attempt privately. For each tool step it hands out the command, accepts the
checker's prediction (`commit`, which appends the prediction's sha256 to a hash-chained order log and to the
caller's sink), and only then releases that step's observation (`reveal`). Revealing an uncommitted step raises.

What this port changed, and the one structural consequence worth stating plainly.

In ALFWorld a contract's condition is about the world state before a command, which the prefix determines, so a
`requires` contract makes a real prefix-only prediction. Every OfficeQA atom is a property of the observation the
step is about to return (see oqa_lc_vocab's docstring for why), and the prefix by definition does not contain it.
So:

  * `requires` / `suffices` conditions evaluate to UNKNOWN at PRE, and the prediction label is `undetermined`.
    That is not a bug to be worked around; it is the honest reading of what the prefix supports. A condition-based
    OfficeQA contract would need atoms about the REQUEST (the path, the pattern, the window), which this
    vocabulary does not yet have.
  * `effect` contracts are where the content is. Each effect literal becomes a FORECAST: a claim, made at PRE and
    recorded before the observation is released, about what the observation will show. At POST it resolves as
    CONFIRMED, VIOLATED or UNRESOLVED. Amendment A4 3.1: a forecast never becomes a fact by itself.

Classes after reveal (outcome ok / fail / unknown from `oqa_lc_vocab.outcome_of`):
    confirmed_ok / confirmed_fail        the condition-based label agreed with the outcome
    refuted_requires / refuted_suffices  it did not: a counterexample to the contract, or a faulty extraction
                                         (both are retained as alternatives, as in the ALFWorld port)
    effect_confirmed / effect_violated / effect_undetermined   per forecast literal
    undetermined | no_claim_ok | unexplained_fail | uncovered | outcome_unknown
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oqa_lc_cells as CELLS  # noqa: E402
from oqa_lc_ir import active, applies, eval_cnf, lit_value  # noqa: E402
from oqa_lc_vocab import VERBS, canon, outcome_of, sha  # noqa: E402

FORECAST_CONFIRMED, FORECAST_VIOLATED, FORECAST_UNRESOLVED = "confirmed", "violated", "unresolved"


def trace_key(attempt: dict) -> str:
    """The identity of ONE recorded attempt, which is not the same thing as the identity of the question.

    Evidence accumulates across epochs, and the same training question is attempted again in every epoch. Keyed by
    the item id alone, two different attempts of UID0063 are indistinguishable: the atom cache returns the first
    attempt's values for the second one's steps, and a counterexample "UID0063@6" does not say which attempt it
    was found in. A caller that admits more than one collection sets `trace_key` on each packet (the arm driver
    uses "<item>/e<epoch>"); callers with a single collection need not, and fall back to the item id.
    """
    return str(attempt.get("trace_key") or attempt.get("id"))


class RevealOrderError(RuntimeError):
    pass


class Revealer:
    """One finished attempt, released one step at a time, prediction first."""

    def __init__(self, attempt: dict):
        self._steps = [s for s in (attempt.get("steps") or []) if s.get("kind") == "tool"]
        self.id = attempt.get("id")
        self.n = len(self._steps)
        self.log: list = []
        self._committed: dict = {}
        self._revealed = -1

    def request(self, t: int) -> dict:
        """The step's command and arguments - everything except its observation."""
        if t != self._revealed + 1:
            raise RevealOrderError(f"step {t} requested out of order")
        s = self._steps[t]
        return {"i": s["i"], "kind": "tool", "tool": s.get("tool"), "args": s.get("args") or {},
                "cmd": s.get("cmd", "")}

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
        return self._steps[t]


# ── prediction ──────────────────────────────────────────────────────────────────

def predict(model: dict, request: dict, answer_kind: str | None, split_pages: bool = False) -> dict:
    """Prefix-only prediction for one tool step. No part of the step's observation is read.

    `split_pages` says whether the actor is holding supplied pages rendered in split-header form. When it is, an
    adopted `index_alignment` assumption is itself a prediction about any flattened header the step may return -
    that merging those pages' headers by cell index will agree with it - so it is committed as a forecast in its
    own right, with no contract needed. Amendment A4 3.1 calls this kind FORECAST: made at PRE, resolved at POST,
    never entered as a fact.
    """
    verb = request.get("tool")
    if verb not in VERBS:
        return {"label": "uncovered", "why": f"tool {verb!r} is outside the vocabulary", "contracts": {},
                "forecasts": []}
    per: dict = {}
    fail, ok, unk = [], [], []
    forecasts = []
    for c in active(model):
        if not applies(c, verb, answer_kind):
            continue
        if c["kind"] == "effect":
            for l in c["effects"]:
                forecasts.append({"contract": c["id"], "version": c.get("version"), "atom": l["atom"],
                                  "predicted": bool(l["value"])})
            continue
        # Every atom is a property of the observation this step has not returned yet, so the prefix cannot value
        # any literal: the Kleene value is UNKNOWN by construction, which is recorded rather than hidden.
        v = eval_cnf(c["condition"], lambda l: lit_value(l, None))
        per[c["id"]] = v
        if c["kind"] == "requires":
            if v is False:
                fail.append(c["id"])
            elif v is None:
                unk.append(c["id"])
        else:
            if v is True:
                ok.append(c["id"])
            elif v is None:
                unk.append(c["id"])
    if not per and not forecasts:
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
    if split_pages and "index_alignment" in set(model.get("assumptions") or []):
        forecasts.append({"contract": "ASSUMED(index_alignment)", "version": None, "atom": "col_leaf_sound",
                          "predicted": True})
    if not per and not forecasts:
        label = "uncovered"
    return {"label": label, "contracts": per, "fail_by": fail, "ok_by": ok, "unknown_by": unk,
            "forecasts": forecasts}


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


def resolve_forecasts(pred: dict, atoms: dict) -> list:
    """Each forecast against the revealed observation's atom values."""
    out = []
    for f in pred["forecasts"]:
        got = atoms.get(f["atom"])
        if got is None:
            cls = FORECAST_UNRESOLVED
        elif bool(got) == f["predicted"]:
            cls = FORECAST_CONFIRMED
        else:
            cls = FORECAST_VIOLATED
        out.append({**f, "observed": got, "class": cls})
    return out


def replay_attempt(attempt: dict, model: dict, sink=None) -> dict:
    """Prefix-only replay of one finished attempt with a frozen model.

    `sink(record)` is called with each prediction BEFORE its observation is revealed, so the caller can persist
    the committed prediction independently of the outcome.
    """
    rev = Revealer(attempt)
    supplied = []
    for page in attempt.get("oracle_pages") or []:
        supplied += CELLS.tables(page.get("text") or "")
    answer_kind = (attempt.get("answer_kind") or None)
    A = set(model.get("assumptions") or [])
    records = []
    has_split = any(t["header_form"] in ("split", "single") for t in supplied)
    for t in range(rev.n):
        req = rev.request(t)
        pred = predict(model, req, answer_kind, split_pages=has_split)
        rec = {"aid": f"{trace_key(attempt)}@{req['i']}", "item": attempt.get("id"), "t": t, "step": req["i"],
               "tool": req["tool"], "args": req["args"], "model_sha256": model.get("sha256"),
               "model_version": model.get("version"), "prediction": pred}
        h = rev.commit(t, rec)
        if sink is not None:
            sink(dict(rec, commit_sha256=h))
        step = rev.reveal(t)
        atoms = CELLS.atom_values(step, A, supplied)
        rec["commit_sha256"] = h
        rec["outcome"] = outcome_of(step)
        rec["atoms"] = atoms
        rec["class"] = classify(pred, rec["outcome"])
        rec["forecast_checks"] = resolve_forecasts(pred, atoms)
        rec["witnesses"] = (CELLS.disagreement_witnesses(step, supplied, "leaf")
                            if atoms.get("col_leaf_sound") is False else [])
        records.append(rec)
    return {"item": attempt.get("id"), "attempts": records, "reveal_log": rev.log}


COVERED_CLASSES = ("refuted_requires", "confirmed_fail", "refuted_suffices", "confirmed_ok", "conflict")


def score(records: list) -> dict:
    """Pre-update performance of the frozen model on these steps."""
    cls: dict = {}
    fc: dict = {"confirmed": 0, "violated": 0, "unresolved": 0}
    for r in records:
        cls[r["class"]] = cls.get(r["class"], 0) + 1
        for f in r.get("forecast_checks") or []:
            fc[f["class"]] += 1
    det = [r for r in records if r["class"] in COVERED_CLASSES]
    return {"steps": len(records), "classes": dict(sorted(cls.items())),
            "determinate_condition_predictions": len(det),
            "forecasts": fc,
            "forecast_coverage": (fc["confirmed"] + fc["violated"]) / sum(fc.values()) if sum(fc.values()) else None}


# ── evidence over finished attempts ─────────────────────────────────────────────

class Evidence:
    """Every finished attempt known so far, with cached atom values per assumption set.

    The cache key includes the assumption set because `oqa_lc_cells.atom_values` consults it: the column atoms are
    UNKNOWN when `index_alignment` is not assumed, which is exactly what makes a repair measurable.
    """

    def __init__(self, attempts: list):
        self.attempts_ = list(attempts)
        self._atoms: dict = {}

    def add(self, attempts: list):
        self.attempts_ += list(attempts)
        self._atoms.clear()

    def _supplied(self, attempt: dict) -> list:
        out = []
        for page in attempt.get("oracle_pages") or []:
            out += CELLS.tables(page.get("text") or "")
        return out

    def atoms(self, attempt: dict, step: dict, assumptions) -> dict:
        key = (trace_key(attempt), step["i"], tuple(sorted(assumptions)))
        if key not in self._atoms:
            self._atoms[key] = CELLS.atom_values(step, set(assumptions), self._supplied(attempt))
        return self._atoms[key]

    def steps(self, verb: str | None = None):
        for a in self.attempts_:
            for s in a.get("steps") or []:
                if s.get("kind") != "tool":
                    continue
                if verb and s.get("tool") != verb:
                    continue
                yield a, s


def score_contract(c: dict, ev: Evidence, assumptions, max_ids: int = 12) -> dict:
    """Support / counterexamples of one contract over all evidence (direction-aware).

    requires: ok & cond False -> COUNTER; fail & cond False -> support_fail; ok & cond True -> support_ok;
              fail & cond True -> neutral; unknown -> undetermined
    suffices: fail & cond True -> COUNTER; ok & cond True -> support_ok; otherwise neutral / undetermined
    effect:   ok & some effect literal False -> COUNTER; all True -> support_ok; otherwise undetermined
    """
    out = {"applicable": 0, "counter": [], "support_fail": [], "support_ok": [], "neutral": 0, "undetermined": 0,
           "rows": []}
    for attempt, step in ev.steps(c["verb"]):
        if not applies(c, step.get("tool"), attempt.get("answer_kind")):
            continue
        out["applicable"] += 1
        o = outcome_of(step)
        aid = f"{trace_key(attempt)}@{step['i']}"
        atoms = ev.atoms(attempt, step, assumptions)
        if c["kind"] == "effect":
            if o != "ok":
                out["neutral"] += 1
                out["rows"].append({"aid": aid, "out": o, "vals": None, "cls": "neutral"})
                continue
            vals = [lit_value(l, atoms.get(l["atom"])) for l in c["effects"]]
            cls = ("counter" if any(v is False for v in vals)
                   else "support_ok" if all(v is True for v in vals) else "undetermined")
        else:
            vals = [[lit_value(l, atoms.get(l["atom"])) for l in cl] for cl in c["condition"]]
            v = eval_cnf(c["condition"], lambda l: lit_value(l, atoms.get(l["atom"])))
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
            out[cls] = out.get(cls, 0) + 1
        out["rows"].append({"aid": aid, "out": o, "vals": vals, "cls": cls, "item": attempt.get("id")})
    out["support"] = len(out["support_fail"]) + len(out["support_ok"])
    out["items_supporting"] = sorted({r["item"] for r in out["rows"]
                                      if r["cls"] in ("support_fail", "support_ok") and r.get("item")})
    out["counter"] = out["counter"][:]
    return out


def jsonl_sink(path: str):
    f = open(path, "a", encoding="utf-8")

    def sink(rec):
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return sink
