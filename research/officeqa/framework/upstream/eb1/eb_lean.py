"""EB-1 Lean export of the F1 / F2 forecast replay (one file per EXT model x set). Generated locally; compiled on the
cluster only (Lean 4.20.0).

What Lean checks, from the exported per-literal values (rows merged with a multiplicity):
  * the Kleene value of the model's completion relation (CNF, OR over the exists bindings) under F1 and under F2;
  * REFINEMENT: every F2 literal equals the F1 literal or is unknown (so F2 removes information and adds none);
  * the counts the Python replay reported, over the steps whose command succeeded (conditional forecasts):
    (correct COMPLETE, false COMPLETE, missed = NOT at a completing step, correct NOT, undetermined), for F1 and F2.
What Lean does NOT check (trusted; stated in every file): the observation parser; the literal values (exported from
the Python estimator, F2's open-effect masking and its declared REPORT_SCOPE frame); the completion labels (outcome +
harness rule, equal to the evaluator-only won flags); which steps succeeded (vocabulary reading of the observation);
that the rows cover the records (the sidecar lists the source states). Compilation is not proof of environment
mechanics or of the frame assumption.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRELUDE = r"""namespace EBF2

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

def all3 (xs : List Tri) : Tri := xs.foldl Tri.and .t
def any3 (xs : List Tri) : Tri := xs.foldl Tri.or .f

def cnfVal (cnf : List (List Nat)) (b : List Tri) : Tri :=
  all3 (cnf.map (fun cl => any3 (cl.map (fun i => b.getD i .u))))

def relVal (cnf : List (List Nat)) (bs : List (List Tri)) : Tri :=
  any3 (bs.map (cnfVal cnf))

/-- F2 literal `b` refines F1 literal `a`: equal, or unknown. -/
def litRef (a b : Tri) : Bool := (a == b) || (b == .u)

def bindRef (xs ys : List Tri) : Bool :=
  (xs.length == ys.length) && ((xs.zip ys).all (fun p => litRef p.1 p.2))

/-- rows: (multiplicity, command succeeded, completion label, F1 bindings, F2 bindings) -/
abbrev Row := Nat × Bool × Bool × List (List Tri) × List (List Tri)

def rowRef (r : Row) : Bool :=
  let (_, _, _, b1, b2) := r
  (b1.length == b2.length) && ((b1.zip b2).all (fun p => bindRef p.1 p.2))

/-- over succeeded steps: (correct COMPLETE, false COMPLETE, missed, correct NOT, undetermined) -/
def count (f2 : Bool) (cnf : List (List Nat)) (rows : List Row) : Nat × Nat × Nat × Nat × Nat :=
  rows.foldl (fun acc r =>
    let (s, fc, ms, cs, ud) := acc
    let (n, ok, lab, b1, b2) := r
    if !ok then acc else
    match relVal cnf (if f2 then b2 else b1), lab with
    | .t, true => (s + n, fc, ms, cs, ud)
    | .t, false => (s, fc + n, ms, cs, ud)
    | .f, true => (s, fc, ms + n, cs, ud)
    | .f, false => (s, fc, ms, cs + n, ud)
    | .u, _ => (s, fc, ms, cs, ud + n)) (0, 0, 0, 0, 0)
"""
LEAN_MODELS = ("EXT1_M4", "EXT2_M4", "A", "B")


def tri(v) -> str:
    return ".t" if v is True else ".f" if v is False else ".u"


def py_rel(shape, bs):
    from functools import reduce

    def kand(a, b):
        return False if (a is False or b is False) else (True if (a is True and b is True) else None)

    def kor(a, b):
        return True if (a is True or b is True) else (False if (a is False and b is False) else None)
    vals = [reduce(kand, [reduce(kor, [b[i] for i in cl], False) for cl in shape], True) for b in bs]
    return reduce(kor, vals, False)


def export(model_key, model_sha, set_key, rows, counts_py, f2_version, code_sha):
    shape = None
    merged = collections.OrderedDict()
    src = collections.defaultdict(list)
    for r in rows:
        assert len(r["l1"]) == 1 and len(r["l2"]) == 1 and list(r["l1"]) == list(r["l2"]), "one completion relation"
        (cid, t1), = r["l1"].items()
        t2 = r["l2"][cid]
        assert t1["shape"] == t2["shape"]
        shape = shape or t1["shape"]
        assert shape == t1["shape"]
        key = (r["ok"], r["lab"], json.dumps(t1["bindings"]), json.dumps(t2["bindings"]))
        merged[key] = merged.get(key, 0) + 1
        src[key].append(f"{r['episode']}@{r['t']}")
    # python recomputation from the exported literals == the replay verdicts (guards the export itself)
    for r in rows:
        (cid, t1), = r["l1"].items()
        v1, v2 = py_rel(shape, t1["bindings"]), py_rel(shape, r["l2"][cid]["bindings"])
        sym = {True: "COMPLETE", False: "NOT", None: "UNKNOWN"}
        assert sym[v1] == r["v1"] and sym[v2] == r["v2"], (model_key, set_key, r["episode"], r["t"])
    name = f"EbF2_{model_key}_{set_key}"
    data = []
    for (ok, lab, b1, b2), n in merged.items():
        f = lambda bs: "[" + ", ".join("[" + ", ".join(tri(x) for x in b) + "]" for b in json.loads(bs)) + "]"
        data.append(f"({n}, {'true' if ok else 'false'}, {'true' if lab else 'false'}, {f(b1)}, {f(b2)})")
    cnf = "[" + ", ".join("[" + ", ".join(str(i) for i in cl) + "]" for cl in shape) + "]"

    def tup(c):
        return (f"({c.get('correct_complete', 0)}, {c.get('false_complete', 0)}, {c.get('missed', 0)}, "
                f"{c.get('correct_not', 0)}, {c.get('abstain', 0)})")
    hdr = (f"/-! EB-1 forecast receipt {name}. model {model_key} sha256 {model_sha}; set {set_key}; F2 {f2_version};\n"
           f"    eb_forecast.py sha256 {code_sha}. AUDIT REPLAY of saved records (not prior predictions).\n"
           "    Trusted, not proved: the observation parser; the exported literal values (Python estimator, F2 masking,\n"
           "    the declared REPORT_SCOPE frame); the completion labels; which steps succeeded; that the rows cover the\n"
           "    records (sidecar). Compilation is not proof of environment mechanics. -/")
    body = [hdr, PRELUDE, f"def cnf : List (List Nat) := {cnf}", "",
            f"def rows : List Row := [\n  " + ",\n  ".join(data) + "]", "",
            "theorem f2_refines_f1 : rows.all rowRef = true := by decide",
            f"theorem f1_counts : count false cnf rows = {tup(counts_py['F1_ok'])} := by decide",
            f"theorem f2_counts : count true cnf rows = {tup(counts_py['F2_ok'])} := by decide",
            "", "end EBF2", ""]
    txt = "\n".join(body)
    side = {"name": name, "model": model_key, "model_sha256": model_sha, "set": set_key, "f2_version": f2_version,
            "eb_forecast_sha256": code_sha, "distinct_rows": len(merged), "states": len(rows),
            "row_sources": {str(i): src[k] for i, k in enumerate(merged)},
            "claims": {"f1_counts": counts_py["F1_ok"], "f2_counts": counts_py["F2_ok"]},
            "lean_sha256": hashlib.sha256(txt.encode()).hexdigest()}
    return name, txt, side


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True, help="dir with FORECAST_F2_REPLAY.json + FORECAST_F2_ROWS.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rep = json.load(open(os.path.join(a.replay, "FORECAST_F2_REPLAY.json")))
    rows = json.load(open(os.path.join(a.replay, "FORECAST_F2_ROWS.json")))
    code_sha = hashlib.sha256(open(os.path.join(HERE, "eb_forecast.py"), "rb").read()).hexdigest()
    os.makedirs(a.out, exist_ok=True)
    man = {}
    for key, rs in rows.items():
        mk, sk = key.split("|")
        if mk not in LEAN_MODELS:
            continue
        name, txt, side = export(mk, rep["models"][mk], sk, rs, rep["by"][key], rep["f2_version"], code_sha)
        open(os.path.join(a.out, name + ".lean"), "w").write(txt)
        json.dump(side, open(os.path.join(a.out, name + ".sidecar.json"), "w"), indent=1)
        man[name] = {"lean_sha256": side["lean_sha256"], "distinct_rows": side["distinct_rows"], "states": side["states"]}
    json.dump({"schema": "eb1.f2_lean_manifest.v1", "replay_sha256": hashlib.sha256(
        open(os.path.join(a.replay, "FORECAST_F2_REPLAY.json"), "rb").read()).hexdigest(), "files": man},
        open(os.path.join(a.out, "F2_LEAN_MANIFEST.json"), "w"), indent=1)
    print(json.dumps(man, indent=1))


if __name__ == "__main__":
    main()
