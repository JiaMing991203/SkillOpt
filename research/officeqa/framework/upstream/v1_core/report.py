"""Faithful reports (design v0.1 section 4.E items 1-5 and section 5). No network.

Every English sentence is rendered deterministically from the structured content (contracts, scores, witnesses,
Lean results); nothing is paraphrased by a model. Skill-edit implications are PROPOSALS rendered from a contract's
logical form and evidence, and they carry its scope: assumptions, support by attempts and distinct tasks,
counterexamples, and whether the support explains failures or only agrees with successes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ir import render, render_lit  # noqa: E402

FACT_BASIS = {"observed": "stated by the preceding observation", "record": "a fact about the record",
              "static": "from the name / an earlier report", "inferred": "inferred", "stale": "older statement; "
              "unknown without persistence", "none": "nothing recorded"}


def fact_text(key: str, f: dict) -> str:
    v = {True: "true", False: "false", None: "unknown"}[f["v"]]
    extra = f" under {', '.join(f['assumptions'])}" if f.get("assumptions") else ""
    st = f" (steps {f['steps']})" if f.get("steps") else ""
    return f"{key} = {v} [{FACT_BASIS.get(f['basis'], f['basis'])}{extra}]{st}"


def scope_text(c: dict) -> str:
    e = c.get("evidence") or {}
    parts = [f"{e.get('n_support_fail', 0)} failure(s) explained", f"{e.get('n_support_ok', 0)} consistent success(es)",
             f"{e.get('n_counter', 0)} counterexample(s)", f"{e.get('undetermined', 0)} undetermined",
             f"tasks supporting: {len(e.get('tasks_supporting') or [])}"]
    return "; ".join(parts)


def skill_implication(c: dict, assumptions: list) -> str:
    """A PROPOSED skill instruction with the contract's scope (never stronger than the contract)."""
    e = c.get("evidence") or {}
    basis = ("explains observed failures" if e.get("n_support_fail") else
             "only agrees with successes (no failure explained; weak evidence of necessity)")
    a = f" Relies on: {', '.join(assumptions)}." if assumptions else ""
    head = render(c)
    if c["kind"] == "requires":
        cond = " and ".join("(" + " or ".join(render_lit(l) for l in cl) + ")" if len(cl) > 1 else render_lit(cl[0])
                            for cl in c["condition"])
        verb = {"go": "go to R", "take": "take O from R", "move": "move O to R", "open": "open R",
                "close": "close R", "heat": "heat O with R", "cool": "cool O with R", "clean": "clean O with R",
                "use": "use R"}[c["verb"]]
        return (f"PROPOSAL: before '{verb}', check that {cond}; otherwise the command is expected to do nothing. "
                f"Scope: {scope_text(c)}; {basis}.{a}")
    return f"PROPOSAL (scope-limited): {head}. Scope: {scope_text(c)}.{a}"


def discrepancy_text(a: dict, model: dict) -> str:
    byid = {c["id"]: c for c in model["contracts"] if c.get("status") == "active"}
    p = a["prediction"]
    lines = [f"- {a['aid']} [{a['class']}] command '{a['action']}' -> observed: \"{a['obs']}\" (outcome {a['outcome']})"]
    for cid in p.get("fail_by", []) + p.get("ok_by", []):
        c = byid.get(cid)
        if c:
            lines.append(f"    contract {cid} v{c.get('version', 1)}: {render(c)}")
    for k, f in sorted(p.get("facts", {}).items()):
        lines.append(f"    pre-state: {fact_text(k, f)}")
    alt = {"refuted_requires": "either the requires contract is false, or the state estimate is wrong (an "
                               "assumption or the extraction), or the observation was misread",
           "refuted_suffices": "either the suffices contract is false or the state estimate is wrong",
           "confirmed_fail": "the command was attempted while a stated requirement was known to be unmet "
                             "(a conditional execution diagnosis; it does not establish that this caused task failure)",
           "unexplained_fail": "no contract explains this failure: a possible missing requirement"}
    if a["class"] in alt:
        lines.append(f"    reading: {alt[a['class']]}")
    return "\n".join(lines)


def decisions_text(decs: list) -> str:
    out = []
    for d in decs:
        tgt = f" {d['target']}" if d.get("target") else (f" {d['name']}" if d.get("name") else "")
        why = ("; ".join(d.get("why") or []))
        r = f" -- {d['rendered']}" if d.get("rendered") else ""
        ev = d.get("evidence")
        evs = (f" [evidence: {ev['n_support_fail']} explained failures, {ev['n_support_ok']} consistent successes, "
               f"{ev['n_counter']} counterexamples, {ev['undetermined']} undetermined]") if ev else ""
        out.append(f"- {d.get('type')} {d.get('op')}{tgt}: {d.get('decision')}{r}{evs}"
                   f"{(' because ' + why) if why else ''}{(' | proposer reason: ' + d['reason']) if d.get('reason') else ''}")
    return "\n".join(out) or "- (no operations)"


def model_table(model: dict) -> str:
    rows = ["| id | v | status | contract | evidence |", "|---|---|---|---|---|"]
    for c in model["contracts"]:
        rows.append(f"| {c['id']} | {c.get('version', 1)} | {c.get('status')} | {render(c)} | {scope_text(c)} |")
    return "\n".join(rows)


def composition_text(comp: dict) -> str:
    out = []
    for tt, r in comp.items():
        s = r["summary"]
        out.append(f"- {tt}: {' ; '.join(r['steps'])} | assumptions {r['model_assumptions'] or 'none'} | "
                   f"proved {s['proved']}, unproved {s['unproved']}, vacuous {s['vacuous']}, "
                   f"no requirement stated {s['no_requirement_stated']}")
        ss = r.get("success_summary")
        if ss and (ss["proved"] or ss["unproved"] or ss["vacuous"] or ss["too_large"]):
            out.append(f"    success obligations (suffices contracts): proved {ss['proved']}, unproved {ss['unproved']}, "
                       f"vacuous {ss['vacuous']}, too large {ss['too_large']}; steps without a suffices contract "
                       f"{ss['steps_without_suffices']}")
        for o in r["obligations"]:
            if o.get("obligation") == "success":
                out.append(f"    step {o['step']} {o['verb']}{tuple(o['binding'].values())} {o['contract']} "
                           f"[SUCCESS obligation]: {o['result']}"
                           + (f"; failing literal(s) in a countermodel: {', '.join(o['failing_literals'])}"
                              if o["result"] == "unproved" else ""))
                continue
            if o["result"] == "unproved":
                out.append(f"    step {o['step']} {o['verb']}{tuple(o['binding'].values())} {o['contract']}: UNPROVED; "
                           f"failing literal(s) in a countermodel: {', '.join(o['failing_literals'])}")
            elif o["result"] == "proved":
                out.append(f"    step {o['step']} {o['verb']}{tuple(o['binding'].values())} {o['contract']}: proved "
                           f"({o['n_vars']} variables){' [Lean: ' + o['lean'] + ']' if o.get('lean') else ''}")
            elif o["result"] == "no_requirement_stated":
                out.append(f"    step {o['step']} {o['verb']}{tuple(o['binding'].values())}: no requires contract "
                           f"(uncovered by requirements)")
            else:
                out.append(f"    step {o['step']} {o['verb']}: {o['result']}")
    return "\n".join(out)
