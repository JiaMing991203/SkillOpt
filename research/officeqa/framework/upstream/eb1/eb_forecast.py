"""EB-1 forecast semantics F2: a PROGRAMMER CORRECTION of how a learned model is executed for pre-action forecasts.
It is not a learner repair and adds no knowledge. VX-1's `vx_check.predict_episode` (= F1) is unchanged and kept.

The defect in F1. F1 forecasts command t by appending a synthetic step built ONLY from learned effect contracts. A
command with no learned effect gets an EMPTY synthetic step, and every fact is then carried through it under
`persistence`. So F1 says "nothing changes", e.g. on(desklamp 1) stays False (init_off) through 'use desklamp 1'.
That gives confident NOT forecasts which nothing in the model supports.

F2, per fact of the forecast post-state of command t:
  KNOWN    the fact is decided by a learned effect that fires (guard True); success of t assumed. Same as F1.
  OPEN     the fact is one the command's declared success report CONCERNS, and no learned effect decides it -> UNKNOWN
           (basis `open_effect`). A fact whose derivation depends on whether that report happens (e.g. a learned
           persists rule invalidated by an arrival) is OPEN too: it is UNKNOWN when its value differs between the
           world without and the world with the report event. Monotone record facts that are already True (picked,
           released, activated, heated, cooled, cleaned: "recorded at or before now") stay True.
  CARRIED  every other fact keeps its pre-state value, under the model's own `persistence` assumption plus the declared
           frame assumption REPORT_SCOPE (programmer-supplied; recorded in the deps of every F2 forecast).
REPORT_SCOPE (declared, not learned): a command can change only the facts its declared success report (the frozen
vocabulary's VERBS[v]['report'], the same table `outcome_of` uses to read a step's outcome) concerns. The report is
used ONLY to scope uncertainty. Its content (e.g. "use R reports turn_on(R)") is NEVER used as an effect value, so F2
supplies no mechanics the model lacks. Information commands (look/inventory/examine/help, declared `info` by the
vocabulary) change nothing. Unparsed commands open every non-static fact.

Success vs effects. F2 gives three things per step:
  * `if_success`: the completion verdict assuming t succeeds (as above);
  * `success`: the model's own success prediction for t (requires / suffices rules; `uncovered` when it has none);
  * `unconditional`: if_success when success is predicted ok; the failure-world verdict when fail is predicted; when
    success is not predicted, the common verdict if both worlds agree, else UNKNOWN. The failure world is the declared
    reading of a failed command ("Nothing happens." changes nothing).
Commitment: every F2 forecast is committed (frozen v1 Revealer hash chain) before that step's observation is
revealed. On saved records it is still a RETROSPECTIVE REPLAY (an audit), never a prior prediction.

F2 only removes information: each base fact is either the F1 value or UNKNOWN, and Kleene logic is monotone, so each
relation's F2 value is F1's or UNKNOWN (`refines` checks this per row; Lean re-checks it from the exported literals).
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VX = os.path.join(os.path.dirname(HERE), "vocab_ext")
sys.path.insert(0, VX)
import vx_check as K  # noqa: E402  (frozen VX-1 checker: F1)
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402
from vx_state import EstimatorX  # noqa: E402
from replay import Revealer  # noqa: E402  (frozen v1 reveal-order guard)

F2_VERSION = "eb1.forecast.f2.v1"
FRAME = "report_scope"
# declared success-report kind -> the fact families it concerns, as (atom, field-of-the-report or "*" = any value)
CONCERNS = {
    "arrive": (("at", "*"), ("arrived", "*")),
    "pick_up": (("holding", "obj"), ("holding_any",), ("in", "obj", "*"), ("picked", "obj")),
    "move_done": (("holding", "obj"), ("holding_any",), ("in", "obj", "*"), ("released", "obj")),
    "open_done": (("open", "recep"),),
    "close_done": (("open", "recep"),),
    "turn_on": (("on", "obj"), ("activated", "obj")),
    "heat_done": (("heated", "obj"),),
    "cool_done": (("cooled", "obj"),),
    "clean_done": (("cleaned", "obj"),),
}
MONOTONE = ("picked", "released", "activated", "heated", "cooled", "cleaned")
STATIC = ("is_a", "openable")
DERIVED = ("co_located",)          # computed from base atoms through value(), so masking propagates
NAME_RE = re.compile(r"[a-z]+ \d+")


def report_event(cmd: dict) -> dict | None:
    """The declared success-report atom of a covered command (used only to scope uncertainty)."""
    if cmd.get("kind") != "covered":
        return None
    kind, fields = XV.VERBS[cmd["verb"]]["report"]
    ev = {"kind": kind}
    for f, role in fields.items():
        ev[f] = cmd["args"].get(role)
    if kind == "pick_up" or kind == "move_done":
        ev.setdefault("recep", cmd["args"].get("R"))
    return ev


def concerned(cmd: dict) -> list:
    """Patterns (atom, args) the command may change; args entries are names or '*'. ['*ALL*'] = everything non-static."""
    k = cmd.get("kind")
    if k == "info":
        return []
    if k != "covered":
        return ["*ALL*"]
    ev = report_event(cmd)
    out = []
    for pat in CONCERNS[ev["kind"]]:
        atom, rest = pat[0], pat[1:]
        out.append((atom, tuple("*" if f == "*" else ev.get(f) for f in rest)))
    return out


def matches(pats, atom: str, args) -> bool:
    if atom in STATIC:
        return False
    for p in pats:
        if p == "*ALL*":
            return True
        a, pa = p
        if a == atom and len(pa) == len(args) and all(x == "*" or x == y for x, y in zip(pa, args)):
            return True
    return False


class EstimatorF2(EstimatorX):
    """EstimatorX whose LAST step is a synthetic forecast step k with F2 semantics for queries at t > k."""

    def setup(self, k: int, pats: list, twin: EstimatorX | None, cmd: dict):
        self.k, self.pats, self.twin, self.cmd = k, pats, twin, cmd
        self.opened = []

    def value(self, t: int, atom: str, args: list) -> dict:
        f = super().value(t, atom, args)
        k = getattr(self, "k", None)
        if k is None or t <= k or atom in DERIVED or atom in STATIC:
            return f
        decided = k in (f.get("steps") or [])          # supported by the synthetic step's KNOWN (learned) atoms
        if atom in MONOTONE and f["v"] is True:
            return f
        why = None
        if not decided and matches(self.pats, atom, args):
            why = "concerned by the command's declared report; no learned effect decides it"
        elif self.twin is not None:
            g = EstimatorX.value(self.twin, t, atom, args)
            if g["v"] != f["v"]:
                why = "its value depends on whether the command's report event happens"
        if why is None:
            return f
        o = {"v": None, "basis": "open_effect", "steps": [k], "assumptions": [],
             "open_effect": {"step": k, "verb": self.cmd.get("verb"), "fact": f"{atom}({','.join(map(str, args))})",
                             "why": why, "f1_value": f["v"]}}
        self.opened.append(o["open_effect"]["fact"])
        return o


def lit_table(model: dict, est, t: int, ti: dict, tt) -> dict:
    """Per in-scope completion relation: the CNF shape (literal indices per clause) and, per exists-binding, the
    Kleene value of every literal (polarity applied) at the post-state of step t. Exported to Lean (trusted values)."""
    cmd = est.steps[t]["cmd"]
    verb = cmd.get("verb") if cmd.get("kind") == "covered" else None
    out = {}
    for c in IR.active(model, "completes"):
        if not K.in_scope(c, verb, tt):
            continue
        base = dict(cmd.get("args") or {}) if c["scope"] != "any" else {}
        shape, i = [], 0
        for cl in c["condition"]:
            shape.append(list(range(i, i + len(cl))))
            i += len(cl)
        rows = []
        for b in IR.bindings(c, est, t + 1, base, ti):
            vals = []
            for cl in c["condition"]:
                for l in cl:
                    _, f = IR.lit_fact(est, t + 1, l, b, ti, model["vocab"])
                    vals.append(None if f["v"] is None else (f["v"] if l["value"] else (not f["v"])))
            rows.append(vals)
        out[c["id"]] = {"shape": shape, "bindings": rows}
    return out


def _sim(model, est, action, atoms, by, cls=EstimatorX):
    s = cls(model["assumptions"], est.task_type, IR.active(model, "persists"))
    s.steps = list(est.steps)
    s.add_synthetic(action, atoms, by)
    return s


def forecast_step(model: dict, est: EstimatorX, t: int, action: str, ti: dict, tt) -> dict:
    """F2 forecast of command t from the observed prefix `est` (steps 0..t-1)."""
    cmd = XV.parse_command(action)
    ap = K.action_prediction(model, est, t, cmd, ti, tt)
    atoms, by, skipped = K.synthetic_atoms(model, est, t, cmd, ti, tt)
    # F1 (unchanged semantics, recomputed here for the row-wise comparison)
    f1sim = _sim(model, est, action, atoms, by)
    f1 = K.verdict_at(model, f1sim, t, ti, tt)
    # F2 success world: known atoms; open facts masked; twin = known atoms + the report event (scoping only)
    ev = report_event(cmd)
    twin = _sim(model, est, action, atoms + ([ev] if ev else []), by) if ev else None
    w0 = _sim(model, est, action, atoms, by, EstimatorF2)
    w0.setup(t, concerned(cmd), twin, cmd)
    f2 = K.verdict_at(model, w0, t, ti, tt)
    # failure world (declared reading: "Nothing happens." changes nothing)
    fw = _sim(model, est, action, [{"kind": "nothing_happens"}], [])
    vf = K.verdict_at(model, fw, t, ti, tt)["verdict"]
    lab = ap["label"]
    if lab == "ok":
        unc, rule = f2["verdict"], "success predicted"
    elif lab == "fail":
        unc, rule = vf, "failure predicted"
    elif f2["verdict"] == vf and f2["verdict"] in ("COMPLETE", "NOT"):
        unc, rule = vf, "both worlds agree"
    else:
        unc, rule = "UNKNOWN", "success not predicted and the worlds differ or are undetermined"
    return {"t": t, "action": action, "verb": cmd.get("verb"), "cmd_kind": cmd.get("kind"),
            "known_effects": {"atoms": [{x: y for x, y in a.items() if x != "quote"} for a in atoms], "by": by,
                              "skipped": skipped},
            "frame": {"assumption": FRAME, "concerned": [p if p == "*ALL*" else f"{p[0]}({','.join(p[1])})"
                                                         for p in concerned(cmd)]},
            "f1": {"verdict": f1["verdict"], "deps": f1["deps"]},
            "if_success": {"verdict": f2["verdict"], "deps": f2["deps"], "unknown": f2["unknown"],
                           "opened": sorted(set(w0.opened))},
            "success": {"label": lab, "fail_by": ap["fail_by"], "ok_by": ap["ok_by"]},
            "failure_world": vf, "unconditional": {"verdict": unc, "rule": rule},
            "_per": {cid: r["v"] for cid, r in f2["per"].items()}, "_per_f1": {cid: r["v"] for cid, r in f1["per"].items()},
            "_lits_f1": lit_table(model, f1sim, t, ti, tt), "_lits_f2": lit_table(model, w0, t, ti, tt)}


def forecast_episode(model: dict, ep_view: dict) -> list:
    """F2 forecasts for every step of an episode VIEW (no outcome field). Each forecast is committed (hash chain)
    before its observation is revealed (frozen v1 Revealer)."""
    assert "outcome" not in ep_view, "forecast views must not carry the outcome"
    rv = Revealer(ep_view)
    ti = K.tinfo_of(ep_view)
    tt = ep_view.get("task_type")
    est = EstimatorX(model["assumptions"], tt, IR.active(model, "persists"))
    out = []
    for t in range(rv.n):
        action = rv.action(t)
        p = forecast_step(model, est, t, action, ti, tt)
        p["commit"] = rv.commit(t, {k: v for k, v in p.items() if not k.startswith("_")})
        out.append(p)
        est.observe(action, rv.reveal(t))
    return out


def refines(per2: dict, per1: dict) -> bool:
    """Information order, per completion relation: each F2 relation value equals F1's or is UNKNOWN (None). (The
    verdict rule itself is not monotone across SEVERAL relations: [T, F] is CONFLICT but [T, U] is COMPLETE; the replay
    reports any such transition separately. Every EXT model has one relation.)"""
    return set(per2) == set(per1) and all(per2[c] is None or per2[c] == per1[c] for c in per1)
