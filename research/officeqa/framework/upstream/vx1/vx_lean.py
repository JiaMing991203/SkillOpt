"""VX-1 Lean export of one model version (amendment A5 section 6.4). Generated locally or in the learning job; compiled
on the cluster only (Lean 4.20.0).

What Lean checks, per ACTIVE contract of the version, over the permitted evidence it was gated on:
  * the Kleene three-valued value of the contract at every exported row (state / attempt), OR over the exists bindings;
  * for on(X): the value is RECOMPUTED in Lean from record facts about X since its latest activation (activated? now?
    picked / arrival / arrival elsewhere / any step / turn-off since?) and the model's learned persists rule + init_off;
  * the evidence counts the Python gate reported (support, counterexamples, consistent, undetermined): equal, by decide.
What Lean does NOT check (trusted, stated in every file header): the parser's reading of each observation; the values
of the other atoms (exported from the Python estimator under the model's assumptions); the completion labels (derived
from the analyst-visible outcome and the harness rule); that the rows are all the evidence (the JSON sidecar lists the
state each row came from). Identical rows are merged with a multiplicity.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402

PRELUDE = r"""namespace VX

inductive Tri where
  | t | f | u
  deriving DecidableEq, Repr

def Tri.and : Tri → Tri → Tri
  | .f, _ => .f
  | _, .f => .f
  | .t, .t => .t
  | _, _ => .u

def Tri.or : Tri → Tri → Tri
  | .t, _ => .t
  | _, .t => .t
  | .f, .f => .f
  | _, _ => .u

def Tri.neg : Tri → Tri
  | .t => .f
  | .f => .t
  | .u => .u

def all3 (xs : List Tri) : Tri := xs.foldl Tri.and .t
def any3 (xs : List Tri) : Tri := xs.foldl Tri.or .f

/-- Record facts about one device since its latest recorded activation (exported; trusted extraction). -/
structure OnEv where
  act : Bool
  now : Bool
  picked : Bool
  anyArr : Bool
  arrElse : Bool
  anyStep : Bool
  turnOff : Bool
  deriving DecidableEq, Repr

/-- The model's learned persistence rule for on(X) (has = false: no rule). -/
structure Rule where
  has : Bool
  picked : Bool
  anyArr : Bool
  arrElse : Bool
  anyStep : Bool
  turnOff : Bool
  deriving DecidableEq, Repr

def onVal (r : Rule) (initOff : Bool) (e : OnEv) : Tri :=
  if !e.act then (if initOff then .f else .u)
  else if e.now then .t
  else if !r.has then .u
  else if (r.picked && e.picked) || (r.anyArr && e.anyArr) || (r.arrElse && e.arrElse)
          || (r.anyStep && e.anyStep) || (r.turnOff && e.turnOff) then .f
  else .t

inductive LV where
  | slot (v : Tri)
  | dev (e : OnEv)
  deriving DecidableEq, Repr

def lvVal (r : Rule) (initOff : Bool) : LV → Tri
  | .slot v => v
  | .dev e => onVal r initOff e

def litVal (r : Rule) (initOff : Bool) (b : List LV) (l : Nat × Bool) : Tri :=
  let v := lvVal r initOff (b.getD l.1 (LV.slot .u))
  if l.2 then v else v.neg

def cnfVal (r : Rule) (initOff : Bool) (cnf : List (List (Nat × Bool))) (b : List LV) : Tri :=
  all3 (cnf.map (fun cl => any3 (cl.map (litVal r initOff b))))

def relVal (r : Rule) (initOff : Bool) (cnf : List (List (Nat × Bool))) (bs : List (List LV)) : Tri :=
  any3 (bs.map (cnfVal r initOff cnf))

/-- completes rows: (multiplicity, completion label, bindings) -> (support, false completions, missed, consistent, undetermined) -/
def countC (r : Rule) (initOff : Bool) (cnf : List (List (Nat × Bool)))
    (rows : List (Nat × Bool × List (List LV))) : Nat × Nat × Nat × Nat × Nat :=
  rows.foldl (fun acc row =>
    let (s, fc, ms, cs, ud) := acc
    let (n, lab, bs) := row
    match relVal r initOff cnf bs, lab with
    | .t, true => (s + n, fc, ms, cs, ud)
    | .t, false => (s, fc + n, ms, cs, ud)
    | .f, true => (s, fc, ms + n, cs, ud)
    | .f, false => (s, fc, ms, cs + n, ud)
    | .u, _ => (s, fc, ms, cs, ud + n)) (0, 0, 0, 0, 0)

/-- requires / suffices rows: (multiplicity, outcome 0 ok | 1 fail | 2 unknown, bindings)
    -> (support, counterexamples, consistent, other) -/
def countReq (r : Rule) (initOff : Bool) (cnf : List (List (Nat × Bool)))
    (rows : List (Nat × Nat × List (List LV))) : Nat × Nat × Nat × Nat :=
  rows.foldl (fun acc row =>
    let (s, c, k, o) := acc
    let (n, out, bs) := row
    match relVal r initOff cnf bs, out with
    | .f, 0 => (s, c + n, k, o)
    | .f, 1 => (s + n, c, k, o)
    | .t, 0 => (s, c, k + n, o)
    | _, _ => (s, c, k, o + n)) (0, 0, 0, 0)

def countSuf (r : Rule) (initOff : Bool) (cnf : List (List (Nat × Bool)))
    (rows : List (Nat × Nat × List (List LV))) : Nat × Nat × Nat × Nat :=
  rows.foldl (fun acc row =>
    let (s, c, k, o) := acc
    let (n, out, bs) := row
    match relVal r initOff cnf bs, out with
    | .t, 1 => (s, c + n, k, o)
    | .t, 0 => (s + n, c, k, o)
    | _, _ => (s, c, k, o + n)) (0, 0, 0, 0)

/-- effect rows (successful attempts only): (multiplicity, guard bindings, per effect literal its bindings) -/
def countEff (r : Rule) (initOff : Bool) (guard : List (List (Nat × Bool))) (pols : List Bool)
    (rows : List (Nat × List (List LV) × List (List (List LV)))) : Nat × Nat × Nat × Nat :=
  rows.foldl (fun acc row =>
    let (s, c, k, o) := acc
    let (n, gbs, effs) := row
    if relVal r initOff guard gbs != .t then (s, c, k, o + n)
    else
      let vs := (effs.zip pols).map (fun p => relVal r initOff [[(0, p.2)]] p.1)
      if vs.any (fun v => v == .f) then (s, c + n, k, o)
      else if vs.all (fun v => v == .t) then (s + n, c, k, o)
      else (s, c, k, o + n)) (0, 0, 0, 0)

"""


def lean_ident(s: str) -> str:
    return "c_" + re.sub(r"[^A-Za-z0-9]", "_", s)


def tri(v) -> str:
    return {True: ".t", False: ".f", None: ".u"}[v]


def on_ev(est, t: int, x: str) -> dict | None:
    """Record facts about device x at the pre-state of command t (post-state of step t-1); None if a turn-off exists
    (then the Python value is exported as a slot)."""
    n = min(t, len(est.steps))
    acts = [k for k in range(n) for a in est.steps[k]["atoms"] if a["kind"] == "turn_on" and a.get("obj") == x]
    offs = [k for k in range(n) for a in est.steps[k]["atoms"] if a["kind"] == "turn_off" and a.get("obj") == x]
    if offs:
        return None
    if not acts:
        return dict(act=False, now=False, picked=False, anyArr=False, arrElse=False, anyStep=False, turnOff=False)
    k = max(acts)
    here = est._arrival_at(k)
    after = [a for j in range(k + 1, n) for a in est.steps[j]["atoms"]]
    return dict(act=True, now=(k == t - 1), picked=any(a["kind"] == "pick_up" and a.get("obj") == x for a in after),
                anyArr=any(a["kind"] == "arrive" for a in after),
                arrElse=any(a["kind"] == "arrive" and a["recep"] != here for a in after),
                anyStep=(t - 1 > k), turnOff=False)


def py_onval(rule, init_off, e) -> object:
    if not e["act"]:
        return False if init_off else None
    if e["now"]:
        return True
    if rule is None:
        return None
    inv = set(rule.get("invalidated_by") or [])
    m = {"picked": "picked", "any_arrival": "anyArr", "arrival_elsewhere": "arrElse", "any_step": "anyStep",
         "turn_off": "turnOff"}
    return False if any(e[m[k]] for k in inv) else True


def lv(est, t, lit, binding, tinfo, vname, rule, init_off) -> tuple:
    g, f = IR.lit_fact(est, t, lit, binding, tinfo, vname)
    if lit["atom"] == "on" and g and g[0] is not None:
        e = on_ev(est, t, g[0])
        if e is not None:
            assert py_onval(rule, init_off, e) == f["v"], ("on() mirror differs", g, e, f)
            return ("on", e)
    return ("slot", f["v"])


def lv_lean(x) -> str:
    if x[0] == "slot":
        return f"LV.slot {tri(x[1])}"
    e = x[1]
    b = lambda v: "true" if v else "false"  # noqa: E731
    return ("LV.dev ⟨" + ", ".join(b(e[k]) for k in ("act", "now", "picked", "anyArr", "arrElse", "anyStep", "turnOff"))
            + "⟩")


def cnf_lean(cnf, idx) -> str:
    return "[" + ", ".join("[" + ", ".join(f"({idx[json.dumps(l, sort_keys=True)]}, {'true' if l['value'] else 'false'})"
                                           for l in cl) + "]" for cl in cnf) + "]"


def binding_values(c, cnf_lits, est, t, base, tinfo, vname, rule, init_off) -> list:
    out = []
    for b in IR.bindings(c, est, t, base, tinfo):
        out.append(tuple(lv(est, t, l, b, tinfo, vname, rule, init_off) for l in cnf_lits))
    return out


def freeze_key(x):
    return json.dumps(x, sort_keys=True, default=str)


def export(model: dict, episodes: list, title: str) -> tuple:
    """-> (lean source, sidecar dict). Raises AssertionError if Python's own counts and the Lean-semantics mirror
    disagree (then nothing is written)."""
    vname = model["vocab"]
    A = model["assumptions"]
    P = IR.active(model, "persists")
    rule = P[0] if P else None
    init_off = "init_off" in A
    ev = K.Evidence(episodes)
    rl = rule or {}
    inv = set(rl.get("invalidated_by") or [])
    rule_lean = ("⟨" + ", ".join("true" if v else "false" for v in
                                 (rule is not None, "picked" in inv, "any_arrival" in inv, "arrival_elsewhere" in inv,
                                  "any_step" in inv, "turn_off" in inv)) + "⟩")
    L = [f"-- VX-1 Lean receipt source: {title}",
         f"-- model sha256 {model.get('sha256')} vocabulary {vname} version {model['version']} batch {model.get('batch')}",
         f"-- assumptions {A}; persists rule {IR.render(rule) if rule else 'none'}",
         f"-- evidence: {len(episodes)} permitted episodes, {sum(len(e['steps']) for e in episodes)} states",
         "-- TRUSTED (not proved here): observation parsing, the exported atom values other than on(X), the completion",
         "-- labels (analyst-visible outcome + harness rule), and that the rows cover the evidence (see the sidecar).",
         "", PRELUDE,
         f"def rule : Rule := {rule_lean}", f"def initOff : Bool := {'true' if init_off else 'false'}", ""]
    side = {"model_sha256": model.get("sha256"), "title": title, "contracts": {}}
    for c in IR.active(model):
        if c["kind"] == "persists":
            continue
        cid = lean_ident(c["id"])
        sc = K.score(c, ev, A, P, vname)
        if c["kind"] in ("completes", "requires", "suffices"):
            lits = [l for cl in c["condition"] for l in cl]
        else:
            lits = [l for cl in (c.get("guard") or []) for l in cl]
        uniq = []
        for l in lits:
            k = json.dumps(l, sort_keys=True)
            if k not in uniq:
                uniq.append(k)
        idx = {k: i for i, k in enumerate(uniq)}
        ulits = [json.loads(k) for k in uniq]
        rows = collections.Counter()
        where = collections.defaultdict(list)
        for ep in ev.episodes:
            tt = ep.get("task_type")
            if not IR.applies_task(c, tt):
                continue
            est = ev.est(ep, A, P)
            ti = K.tinfo_of(ep)
            for i, st in enumerate(est.steps):
                cmd = st["cmd"]
                verb = cmd.get("verb") if cmd.get("kind") == "covered" else None
                if c["kind"] == "completes":
                    if not K.in_scope(c, verb, tt):
                        continue
                    base = dict(cmd.get("args") or {}) if c["scope"] != "any" else {}
                    key = (K.label(ep, i), tuple(binding_values(c, ulits, est, i + 1, base, ti, vname, rule, init_off)))
                elif c["kind"] in ("requires", "suffices"):
                    if verb != c["verb"]:
                        continue
                    o = {"ok": 0, "fail": 1}.get(st["outcome"], 2)
                    key = (o, tuple(binding_values(c, ulits, est, i, dict(cmd["args"]), ti, vname, rule, init_off)))
                else:  # effect
                    if verb != c["verb"] or st["outcome"] != "ok":
                        continue
                    g = tuple(binding_values(c, ulits, est, i, dict(cmd["args"]), ti, vname, rule, init_off)) \
                        if c.get("guard") else ((),)
                    effs = tuple(tuple(binding_values(c, [l], est, i + 1, dict(cmd["args"]), ti, vname, rule, init_off))
                                 for l in c["effects"])
                    key = (g, effs)
                kk = freeze_key(key)
                rows[kk] += 1
                where[kk].append(f"{ep['episode_id']}@{i}")
        # Python mirror of the Lean count functions, compared with the gate's own counts
        def kval(cnf, bs):
            vals = []
            for b in bs:
                cv = []
                for cl in cnf:
                    lv_ = []
                    for l in cl:
                        x = b[idx[json.dumps(l, sort_keys=True)]] if b else ("slot", None)
                        v = x[1] if x[0] == "slot" else py_onval(rule, init_off, x[1])
                        lv_.append(None if v is None else (v if l["value"] else not v))
                    cv.append(IR.k_or(lv_) if hasattr(IR, "k_or") else None)
                vals.append(IR.k_and(cv))
            return IR.k_or(vals) if vals else False
        data, mirror = [], collections.Counter()
        for kk, n in sorted(rows.items()):
            key = json.loads(kk)
            if c["kind"] == "completes":
                lab, bs = key
                v = kval(c["condition"], bs)
                mirror[{(True, True): "s", (True, False): "fc", (False, True): "ms", (False, False): "cs"}.get((v, lab), "ud")] += n
                data.append(f"({n}, {'true' if lab else 'false'}, [" + ", ".join("[" + ", ".join(lv_lean(x) for x in b) + "]" for b in bs) + "])")
            elif c["kind"] in ("requires", "suffices"):
                o, bs = key
                v = kval(c["condition"], bs)
                if c["kind"] == "requires":
                    cl = {(False, 0): "c", (False, 1): "s", (True, 0): "k"}.get((v, o), "o")
                else:
                    cl = {(True, 1): "c", (True, 0): "s"}.get((v, o), "o")
                mirror[cl] += n
                data.append(f"({n}, {o}, [" + ", ".join("[" + ", ".join(lv_lean(x) for x in b) + "]" for b in bs) + "])")
            else:
                g, effs = key
                gv = kval(c["guard"], g) if c.get("guard") else True
                if gv is not True:
                    mirror["o"] += n
                else:
                    vs = []
                    for l, bs in zip(c["effects"], effs):
                        vv = []
                        for b in bs:
                            x = b[0]
                            v = x[1] if x[0] == "slot" else py_onval(rule, init_off, x[1])
                            vv.append(None if v is None else (v if l["value"] else not v))
                        vs.append(IR.k_or(vv) if vv else False)
                    mirror["c" if False in vs else ("s" if all(v is True for v in vs) else "o")] += n
                gl = "[" + ", ".join("[" + ", ".join(lv_lean(x) for x in b) + "]" for b in g) + "]" if c.get("guard") else "[[]]"
                el = "[" + ", ".join("[" + ", ".join("[" + ", ".join(lv_lean(x) for x in b) + "]" for b in bs) + "]" for bs in effs) + "]"
                data.append(f"({n}, {gl}, {el})")
        if c["kind"] == "completes":
            fc = sum(1 for x in sc["counter"] if sc["counter_kind"].get(x) == "false_completion")
            ms = len(sc["counter"]) - fc
            exp = (len(sc["support"]), fc, ms, len(sc["consistent"]), sc["undetermined"])
            got = (mirror["s"], mirror["fc"], mirror["ms"], mirror["cs"], mirror["ud"])
            typ, fn, args = "List (Nat × Bool × List (List LV))", "countC", f"rule initOff {cnf_lean(c['condition'], idx)}"
        elif c["kind"] in ("requires", "suffices"):
            exp = (len(sc["support"]), len(sc["counter"]), len(sc["consistent"]))
            got = (mirror["s"], mirror["c"], mirror["k"])
            typ = "List (Nat × Nat × List (List LV))"
            fn, args = ("countReq" if c["kind"] == "requires" else "countSuf"), f"rule initOff {cnf_lean(c['condition'], idx)}"
        else:
            exp = (len(sc["support"]), len(sc["counter"]))
            got = (mirror["s"], mirror["c"])
            typ = "List (Nat × List (List LV) × List (List (List LV)))"
            pols = "[" + ", ".join("true" if l["value"] else "false" for l in c["effects"]) + "]"
            fn, args = "countEff", f"rule initOff {cnf_lean(c.get('guard') or [], idx)} {pols}"
        assert exp == got, (c["id"], "python gate counts", exp, "lean-semantics mirror", got)
        if c["kind"] == "completes":
            claim = f"({got[0]}, {got[1]}, {got[2]}, {got[3]}, {got[4]})"
        elif c["kind"] in ("requires", "suffices"):
            claim = f"({mirror['s']}, {mirror['c']}, {mirror['k']}, {mirror['o']})"
        else:
            claim = f"({mirror['s']}, {mirror['c']}, 0, {mirror['o']})"
        L += [f"-- {c['id']} v{c.get('version', 1)}: {IR.render(c)}",
              f"def {cid}_rows : {typ} := [" + ",\n  ".join(data) + "]",
              f"theorem {cid}_counts : {fn} {args} {cid}_rows = {claim} := by decide", ""]
        side["contracts"][c["id"]] = {"kind": c["kind"], "rendered": IR.render(c), "lean_name": f"{cid}_counts",
                                      "counts_claimed": claim, "n_rows": len(rows), "n_cases": sum(rows.values()),
                                      "rows_from": {str(i): where[k][:50] for i, k in enumerate(sorted(rows))}}
    L.append("end VX")
    return "\n".join(L) + "\n", side
