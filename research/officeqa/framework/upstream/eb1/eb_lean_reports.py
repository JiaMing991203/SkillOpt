"""EB-1 Lean receipts for the A and C report claims of every covered cohort case (post-freeze verification tool; it
reads only the frozen inputs and report texts). One file per case; Lean 4.20.0 on the cluster.

Per case, Lean checks (by decide), from exported facts:
  timeline  model B's completion relation after every step: Kleene value over the exists bindings, with on(X)
            RECOMPUTED in Lean from record facts + the learned persists rule + init_off (vx_lean's prelude); the list
            must equal the verdicts the A block states ("RULE completes.any.1 after each step") and the GOAL step the
            C block states
  reuse     each 'use' the A / C blocks call "lamp already on": on(lamp) at the pre-state, recomputed in Lean = true
  revisit   the avoidable-revisit predicate of the C block (listed before AND nothing needed listed AND nothing put
            there since AND no needed action followed AND not the completing arrival) on every revisit; the number
            of rows satisfying it must equal the number the C block states
Each file names the SHA-256 of the A and C report texts actually sent (edit_input/reports/<cond>/<id>.txt).
Trusted, not proved: the observation parser, the exported literal values other than on(X), the revisit facts (an
independent Python recomputation, asserted equal to the report's own classification), that the rows cover the record.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vocab_ext"))
import eb_reports as R  # noqa: E402
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_lean as XL  # noqa: E402
import vx_vocab as XV  # noqa: E402
from vx_state import EstimatorX  # noqa: E402


def sf(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def count_listed(txt, label_start):
    """number of steps listed in a C-block category ('s1, s2 (+3 more)')."""
    m = re.search(re.escape(label_start) + r"[^:]*\]: ([^;.]*)", txt)
    if not m:
        return 0
    part = m.group(1)
    n = len(re.findall(r"\bs\d+", part.split("(+")[0]))
    more = re.search(r"\(\+(\d+) more\)", part)
    return n + (int(more.group(1)) if more else 0)


def revisit_rows(an):
    """independent recomputation of the C block's revisit predicate inputs"""
    OT, LT = an["OT"], an["LT"]
    rows = []
    for r in an["revisit"]:
        t = r["t"]
        held = an["held_at"][t]
        need_o = bool(OT) and not any(XV.type_of(x) == OT for x in held)
        lamp_on = any(XV.type_of(x) == LT and an["on"][x] < t for x in an["on"]) if LT else False
        need_l = bool(LT) and (not lamp_on or not need_o)
        prev = r["prev"]
        listed = prev is not None and prev[0] is not None
        needed = listed and any(XV.type_of(x) in ({OT} if need_o else set()) | ({LT} if need_l else set()) for x in prev[0])
        nxt = next((v for v in an["visits"][r["r"]] if v > t), an["n"])
        nxt = min([nxt] + [k for k in range(t + 1, an["n"]) if any(x["kind"] == "arrive" for x in an["steps"][k]["atoms"])])
        prod = any(R.productive(an, s, t) for s in an["steps"][t + 1: nxt])
        final = an["success"] and t == an["n"] - 1
        rows.append({"t": t, "listed": listed, "needed": needed, "put": bool(r["put_since"]), "prod": prod, "final": final})
    return rows


def case_file(c, B, rep_dir):
    view = c["view"]
    an = R.analyze(view, B)
    vname, A, P = B["vocab"], B["assumptions"], IR.active(B, "persists")
    rule = P[0] if P else None
    init_off = "init_off" in A
    rel = IR.active(B, "completes")[0]
    lits = []
    for cl in rel["condition"]:
        for l in cl:
            k = json.dumps(l, sort_keys=True)
            if k not in lits:
                lits.append(k)
    idx = {k: i for i, k in enumerate(lits)}
    ulits = [json.loads(k) for k in lits]
    est = EstimatorX.of_steps(view["task_type"], view["steps"], A, P)
    ti = K.tinfo_of(view)
    tl = []
    for i in range(len(view["steps"])):
        bs = XL.binding_values(rel, ulits, est, i + 1, {}, ti, vname, rule, init_off)
        tl.append("[" + ", ".join("[" + ", ".join(XL.lv_lean(x) for x in b) + "]" for b in bs) + "]")
    post = an["post"]
    tri = {"COMPLETE": ".t", "NOT": ".f", "UNKNOWN": ".u"}
    claimed = "[" + ", ".join(tri[v] for v in post) + "]"
    ctext = open(os.path.join(rep_dir, "C", c["id"] + ".txt")).read()
    atext = open(os.path.join(rep_dir, "A", c["id"] + ".txt")).read()
    # the A block's timeline statement and the C block's GOAL must be exactly this list
    ch = [i for i in range(len(post)) if post[i] != (post[i - 1] if i else None)]
    assert ("after each step (retrospective): " + "; ".join(f"{post[i]} from s{i}" for i in ch[:6])) in atext, c["id"]
    g = next((i for i, v in enumerate(post) if v == "COMPLETE"), None)
    assert (f"GOAL s{g} (RULE" in ctext) if g is not None else ("GOAL never" in ctext), c["id"]
    reuse = []
    for (t, lamp, again) in an["uses"]:
        if again:
            e = XL.on_ev(est, t, lamp)
            assert e is not None and XL.py_onval(rule, init_off, e) is True
            reuse.append("⟨" + ", ".join("true" if e[k] else "false" for k in
                                        ("act", "now", "picked", "anyArr", "arrElse", "anyStep", "turnOff")) + "⟩")
    rv = revisit_rows(an)
    n_av = sum(1 for r in rv if r["listed"] and not r["needed"] and not r["put"] and not r["prod"] and not r["final"])
    n_claim = count_listed(ctext, "revisit: its latest listing named nothing still needed")
    assert n_av == n_claim, (c["id"], n_av, n_claim)
    n_reuse_claim = count_listed(ctext, "'use' of a lamp already on")
    assert n_reuse_claim == len(reuse), (c["id"], n_reuse_claim, len(reuse))
    inv = set((rule or {}).get("invalidated_by") or [])
    rule_lean = ("⟨" + ", ".join("true" if v else "false" for v in
                                 (rule is not None, "picked" in inv, "any_arrival" in inv, "arrival_elsewhere" in inv,
                                  "any_step" in inv, "turn_off" in inv)) + "⟩")
    b = lambda v: "true" if v else "false"  # noqa: E731
    rvl = "[" + ", ".join(f"⟨{b(r['listed'])}, {b(r['needed'])}, {b(r['put'])}, {b(r['prod'])}, {b(r['final'])}⟩" for r in rv) + "]"
    name = "EbRep_" + c["id"].replace(":", "_")
    L = [f"/-! EB-1 report receipt {name}: case {c['id']} ({c['category']}), model B {B['sha256']}.",
         f"    Report texts sent: A sha256 {sf(os.path.join(rep_dir, 'A', c['id'] + '.txt'))},",
         f"    C sha256 {sf(os.path.join(rep_dir, 'C', c['id'] + '.txt'))}.",
         "    Trusted, not proved: observation parsing, literal values other than on(X), the revisit facts, row coverage. -/",
         XL.PRELUDE,
         f"def rule : Rule := {rule_lean}", f"def initOff : Bool := {b(init_off)}",
         f"def cnf : List (List (Nat × Bool)) := {XL.cnf_lean(rel['condition'], idx)}",
         "def timeline : List (List (List LV)) := [\n  " + ",\n  ".join(tl) + "]",
         f"theorem timeline_as_reported : timeline.map (relVal rule initOff cnf) = {claimed} := by decide",
         "def reuse : List OnEv := [" + ", ".join(reuse) + "]",
         "theorem reuse_lamp_already_on : reuse.all (fun e => onVal rule initOff e == .t) = true := by decide",
         "structure Rv where\n  listed : Bool\n  needed : Bool\n  put : Bool\n  prod : Bool\n  final : Bool\n  deriving DecidableEq, Repr",
         "def avoidable (r : Rv) : Bool := r.listed && !r.needed && !r.put && !r.prod && !r.final",
         f"def revisits : List Rv := {rvl}",
         f"theorem avoidable_revisits_as_reported : (revisits.filter avoidable).length = {n_av} := by decide",
         "", "end VX", ""]
    txt = "\n".join(L)
    return name, txt, {"case": c["id"], "A_sha256": sf(os.path.join(rep_dir, "A", c["id"] + ".txt")),
                       "C_sha256": sf(os.path.join(rep_dir, "C", c["id"] + ".txt")), "steps": len(post),
                       "goal_step": g, "reuse_claims": len(reuse), "avoidable_revisits": n_av, "revisits": len(rv),
                       "lean_sha256": hashlib.sha256(txt.encode()).hexdigest()}


def main():
    run = os.path.expanduser(sys.argv[1])
    coh = json.load(open(os.path.join(run, "cohort", "COHORT.json")))
    B = IR.load_frozen(os.path.expanduser("~/dev/lean_skillopt/runs/20261121-rp1/repair/B/CANDIDATE.json"))
    rep = os.path.join(run, "edit_input", "reports")
    out = os.path.join(run, "report_lean")
    os.makedirs(out, exist_ok=True)
    man = {}
    for c in coh["cases"]:
        if not c["category"].startswith("PROBE"):
            name, txt, side = case_file(c, B, rep)
            open(os.path.join(out, name + ".lean"), "w").write(txt)
            man[name] = side
    json.dump({"schema": "eb1.report_lean.v1", "files": man}, open(os.path.join(out, "REPORT_LEAN_MANIFEST.json"), "w"), indent=1)
    for k, v in man.items():
        print(k, {x: v[x] for x in ("steps", "goal_step", "reuse_claims", "avoidable_revisits", "revisits")})


if __name__ == "__main__":
    main()
