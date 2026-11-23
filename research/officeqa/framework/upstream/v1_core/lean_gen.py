"""Generate and run the Lean checks of one model version (design v0.1 section 5). Lean runs on the cluster only.

For every contract (active, and refuted ones for their counterexamples), one theorem states the evidence counts the
Python gate computed over ALL evidence so far; Lean recomputes them from exported ATOM values (not Python's literal
values or classes) with the contract's own clauses and polarities. Every countermodel / proved obligation of the
composition check becomes a theorem as well. Each theorem is followed by `#print axioms`.

Statuses: a generated file is GENERATED until Lean has compiled it; a theorem is PROVED only if Lean reported no
error in its line range and its axioms are standard (no sorryAx); otherwise FAILED. Obligations with more than
LEAN_MAX_VARS variables are left to Python and labelled `not_attempted_size`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ir import active, applies, ground_args  # noqa: E402
from replay import Evidence, binding_of, score_contract  # noqa: E402
from state import lit_key  # noqa: E402
from vocab import sha  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lean", "LearnedContracts.lean")
LEAN_MAX_VARS = 11
STD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
TRI = {True: ".t", False: ".f", None: ".u"}
OUT = {"ok": ".ok", "fail": ".fail", "unknown": ".unk"}
KIND = {"requires": ".req", "suffices": ".suf", "effect": ".eff"}


def ident(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def contract_slots(c: dict):
    lits = c["effects"] if c["kind"] == "effect" else [l for cl in c["condition"] for l in cl]
    slots: dict = {}
    for l in lits:
        k = lit_key(l["atom"], list(l["args"]))
        slots.setdefault(k, len(slots))
    if c["kind"] == "effect":
        cnf = [[(slots[lit_key(l["atom"], list(l["args"]))], bool(l["value"]))] for l in c["effects"]]
    else:
        cnf = [[(slots[lit_key(l["atom"], list(l["args"]))], bool(l["value"])) for l in cl] for cl in c["condition"]]
    return slots, cnf


def lean_rows(c: dict, ev: Evidence, assumptions) -> list:
    slots, _ = contract_slots(c)
    lits = {}
    for l in (c["effects"] if c["kind"] == "effect" else [l for cl in c["condition"] for l in cl]):
        lits.setdefault(lit_key(l["atom"], list(l["args"])), l)
    rows = []
    for ep, t, cmd in ev.attempts(c["verb"]):
        if not applies(c, cmd["verb"], ep.get("task_type")):
            continue
        est = ev.est(ep, assumptions)
        o = est.steps[t]["outcome"]
        b = binding_of(cmd)
        if c["kind"] == "effect" and o != "ok":
            vals = []
        else:
            tt = t + 1 if c["kind"] == "effect" else t
            vals = [None] * len(slots)
            for key, i in slots.items():
                l = lits[key]
                vals[i] = est.value(tt, l["atom"], ground_args(l, b))["v"]
        rows.append((f"{ep['episode_id']}@{t}", vals, o))
    return rows


def fm(f) -> str:
    k = f[0]
    if k == "v":
        return f"(.v {f[1]})"
    if k == "c":
        return ".tt" if f[1] else ".ff"
    if k == "not":
        return f"(.neg {fm(f[1])})"
    xs = f[1]
    op = ".conj" if k == "and" else ".disj"
    out = fm(xs[-1])
    for x in reversed(xs[:-1]):
        out = f"({op} {fm(x)} {out})"
    return out


class Bundle:
    def __init__(self, title: str):
        self.title = title
        self.lines = [open(LIB).read().replace("end LC", "").rstrip(), "", f"-- ==== {title} ====", ""]
        self.thms: list = []

    def add(self, text: str):
        self.lines += text.split("\n")

    def theorem(self, name: str, prop: str, tactic: str, meta: dict):
        start = len(self.lines) + 1
        self.lines.append(f"theorem {name} : {prop} := by {tactic}")
        self.lines.append(f"#print axioms {name}")
        self.thms.append(dict(meta, name=name, line=start))

    def text(self) -> str:
        return "\n".join(self.lines + ["", "end LC", ""])


def add_evidence(b: Bundle, model: dict, ev: Evidence, tag: str, include_refuted=True) -> list:
    A = model.get("assumptions") or []
    out = []
    cs = [c for c in model["contracts"] if c.get("status") == "active" or
          (include_refuted and c.get("status") == "refuted" and c.get("refuted_epoch") == model.get("epoch"))]
    for c in cs:
        n = ident(f"{tag}_{c['id']}_v{c.get('version', 1)}")
        slots, cnf = contract_slots(c)
        rows = lean_rows(c, ev, A)
        sc = score_contract(c, ev, A)
        py = (len(sc["counter"]), len(sc["support_fail"]), len(sc["support_ok"]), sc["neutral"], sc["undetermined"])
        cnf_s = "[" + ", ".join("[" + ", ".join(f"({i}, {'true' if p else 'false'})" for i, p in cl) + "]"
                                for cl in cnf) + "]"
        rows_s = ",\n    ".join(f"⟨[{', '.join(TRI[v] for v in vals)}], {OUT[o]}⟩" for _, vals, o in rows)
        b.add(f"/-- {c['id']} v{c.get('version', 1)} ({c['kind']}, {c['status']}); slots: "
              f"{', '.join(f'{i}={k}' for k, i in slots.items())} -/")
        b.add(f"def c_{n} : List (List (Nat × Bool)) := {cnf_s}")
        b.add(f"def rows_{n} : List Row := [\n    {rows_s}]")
        b.theorem(f"ev_{n}", f"count {KIND[c['kind']]} c_{n} rows_{n} = ⟨{', '.join(map(str, py))}⟩",
                  "decide +kernel", {"kind": "evidence", "contract": c["id"], "version": c.get("version", 1),
                                     "status": c["status"], "python_counts": py, "n_rows": len(rows)})
        for k, (aid, vals, o) in enumerate(rows):
            if aid in sc["counter"][:3]:
                b.theorem(f"cx_{n}_{k}", f"classify {KIND[c['kind']]} c_{n} (nthRow rows_{n} {k}) = .counter",
                          "decide +kernel", {"kind": "counterexample", "contract": c["id"], "attempt": aid})
        out.append(c["id"])
    return out


def add_composition(b: Bundle, comp: dict, tag: str) -> None:
    for tt, r in comp.items():
        for k, o in enumerate(r["obligations"]):
            if o["result"] not in ("proved", "unproved", "vacuous"):
                continue
            n = ident(f"{tag}_{tt}_{o['step']}_{o['contract']}_{k}")
            b.add(f"/-- {tt} step {o['step']} {o['verb']}{tuple(o['binding'].values())}: {o['rendered']} -/")
            b.add(f"def hyp_{n} : Fm := {fm(o['hyp'])}")
            b.add(f"def goal_{n} : Fm := {fm(o['goal'])}")
            meta = {"kind": "composition", "task_type": tt, "step": o["step"], "contract": o["contract"],
                    "obligation": o.get("obligation", "requirement"), "python_result": o["result"],
                    "n_vars": o["n_vars"]}
            if o["result"] == "unproved":
                bits = "[" + ", ".join("true" if x else "false" for x in o["countermodel"]) + "]"
                b.theorem(f"cm_{n}", f"refutes {bits} hyp_{n} goal_{n} = true", "decide +kernel", meta)
            elif o["n_vars"] <= LEAN_MAX_VARS:
                goal = "goal" if o["result"] == "proved" else None
                if goal:
                    b.theorem(f"ob_{n}", f"valid {o['n_vars']} hyp_{n} goal_{n} = true", "decide +kernel", meta)
                else:
                    b.theorem(f"vac_{n}", f"valid {o['n_vars']} hyp_{n} .ff = true", "decide +kernel", meta)
            else:
                o["lean"] = "not_attempted_size"


def run_lean(lean_bin: str, path: str, timeout: int = 900) -> tuple:
    t0 = time.time()
    try:
        p = subprocess.run([lean_bin, os.path.basename(path)], cwd=os.path.dirname(path), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), round(time.time() - t0, 2)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + "\nTIMEOUT", round(time.time() - t0, 2)


def parse_results(b: Bundle, rc: int, output: str) -> dict:
    errs = [int(m.group(1)) for m in re.finditer(r":(\d+):\d+: error", output)]
    ax = {}
    for m in re.finditer(r"'LC\.([A-Za-z0-9_]+)' (?:depends on axioms: \[([^\]]*)\]|does not depend on any axioms)",
                         output):
        ax[m.group(1)] = [x.strip() for x in (m.group(2) or "").split(",") if x.strip()]
    res = []
    for t in b.thms:
        lo, hi = t["line"], t["line"] + 1
        bad = [e for e in errs if lo <= e <= hi]
        axs = ax.get(t["name"])
        ok = rc != 124 and not bad and axs is not None and set(axs) <= STD_AXIOMS
        res.append(dict(t, status="proved" if ok else "failed", axioms=axs, error_lines=bad))
    return {"rc": rc, "n_theorems": len(res), "proved": sum(r["status"] == "proved" for r in res),
            "failed": sum(r["status"] == "failed" for r in res), "errors_outside_theorems":
            [e for e in errs if not any(t["line"] <= e <= t["line"] + 1 for t in b.thms)], "theorems": res}


def write_and_check(b: Bundle, out_dir: str, name: str, lean_bin: str | None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name + ".lean")
    txt = b.text()
    with open(path, "w") as f:
        f.write(txt)
    meta = {"file": os.path.basename(path), "sha256": sha(txt), "status": "generated",
            "n_theorems": len(b.thms), "theorems": b.thms}
    if lean_bin:
        rc, output, secs = run_lean(lean_bin, path)
        with open(path + ".out", "w") as f:
            f.write(output)
        res = parse_results(b, rc, output)
        meta.update(res, status="compiled" if rc == 0 else ("timeout" if rc == 124 else "compile_errors"),
                    seconds=secs)
    with open(path + ".json", "w") as f:
        json.dump(meta, f, indent=1, default=str)
    return meta
