"""RP-1 readable repair reports (protocol section 6.5), generated from the SAME structured results: EVAL_RP1.json, the
frozen candidate models, their decisions, the CX record and the Lean receipts. Local, offline; no optimizer call.

  python3 -I -B rp_report.py --root RPROOT     -> RPROOT/reports/REPAIR_REPORT_<A|B>.md + OPTIMIZER_VIEW_<A|B>.txt
                                                  + REPORTS_BIND.json (digests of every input and output)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402
import rp_eval as RE  # noqa: E402
from vx_state import EstimatorX  # noqa: E402


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def facts_at(model, cx, t, binding):
    est = EstimatorX.of_steps(cx["task_type"], cx["steps"], model["assumptions"], IR.active(model, "persists"))
    ti = K.tinfo_of(cx)
    out = []
    for c in IR.active(model, "completes") or [x for x in model["contracts"] if x["kind"] == "completes"][-1:]:
        for cl in c["condition"]:
            for l in cl:
                kinds = XV.VOCABS[model["vocab"]]["atoms"][l["atom"]][0]
                g = [IR.ground(a, k, binding, ti) for k, a in zip(kinds, l["args"])]
                f = est.value(t, l["atom"], g)
                v = None if f["v"] is None else (f["v"] if l["value"] else not f["v"])
                out.append(f"{IR.render_lit(l)} with {', '.join(g)}: {'true' if v else 'false' if v is False else 'unknown'}"
                           f" ({f['basis']}; steps {f.get('steps')}; assumptions {f.get('assumptions') or []}"
                           f"{'; learned ' + str(f['learned']) if f.get('learned') else ''})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    ev = json.load(open(os.path.join(a.root, "eval", "EVAL_RP1.json")))
    ms = RE.load_models(a.root)
    cx = json.load(open(os.path.join(a.root, "repair_input", "cx_view.json")))
    old = ms["EXT2_M4"]
    old_rel = IR.active(old, "completes")[0]
    b = {"?o": "pencil 1", "?l": "desklamp 1"}
    T = len(cx["steps"])          # post-state of the last step
    os.makedirs(os.path.join(a.root, "reports"), exist_ok=True)
    bind = {"eval_sha256": XV.sha_file(os.path.join(a.root, "eval", "EVAL_RP1.json")), "reports": {}}
    for s in ("A", "B"):
        m = ms[s]
        cl = ev["classification"][s]
        by = ev["models"][s]["by_set"]
        rg = ev["regressions_vs_EXT2_M4"][s]
        rc = json.load(open(os.path.join(a.root, "repair", s, "lean", "RECEIPT.json")))
        dec = m["decisions"]
        rel = IR.active(m, "completes")
        lab = cl["label"][0]
        L = [f"# RP-1 repair report, attempt {s}", "",
             f"- candidate model `{m['sha256'][:16]}` (parent EXT2 M4 `{old['sha256'][:16]}`); Lean receipt "
             f"`{(rc.get('lean_sha256') or 'none')[:16]}` rc {rc.get('rc')}; evaluation `{bind['eval_sha256'][:16]}`",
             f"- outcome class (frozen rules): **{cl['label']}**", "",
             "## 1. The record [FACT: commands and observations as recorded]",
             f"Task: {cx['task']}  Outcome: {cx['outcome']} (the episode ended after step {T - 1}).",
             *[f"    [{st['i']}] {st['action']} -> {st['obs']}" for st in cx["steps"] if st["i"] in (0, 1, 2, 3, 26, T - 1)],
             "", "## 2. What the old learned rule said [LEARNED in VX-1, now refuted]",
             f"`{old_rel['id']}` v{old_rel.get('version')}: {IR.render(old_rel)}",
             f"At step {T - 1} it gave NOT. Values with ?o = pencil 1, ?l = desklamp 1:",
             *[f"- {x}" for x in facts_at(old, cx, T, b)],
             "", "## 3. Reading of the discrepancy",
             "- **Model refutation, not an actor error.** The episode succeeded. The put-down at step 2 and the re-take "
             "at step 3 did not prevent completion, so the learned conjunct `not released(?o)` is over-restrictive. The "
             "actor's sequence violated no recorded obligation; it cost two extra steps (an efficiency remark only).",
             "- The conflict comes from the record fact released(pencil 1) (step 2), not from extraction or location: "
             "the same instance name is picked up at step 1 and step 3, and every other conjunct is true at step 27.",
             "", "## 4. What the learner did, and the gate's decisions [FACT: decisions as recorded]"]
        for d in dec:
            L.append(f"- {d['type']} {d['op']} {d.get('target') or d.get('name') or d.get('id') or ''} ({d.get('source')}): "
                     f"**{d.get('decision')}**" + (f" -- {'; '.join(d.get('why') or [])[:400]}" if d.get("why") else "")
                     + (f"\n  learner's reason: {d.get('reason', '')[:600]}" if d.get("reason") else ""))
        L += ["", "## 5. The model after repair [LEARNED, gated on 84 learning records + CX]"]
        L += [f"- `{c['id']}` v{c.get('version')}: {IR.render(c)} (support {c['evidence']['n_support']}, counterexamples "
              f"{c['evidence']['n_counter']}, undetermined {c['evidence']['undetermined']})" for c in IR.active(m)]
        L += [f"- assumptions [ASSUMPTION]: {m['assumptions']}"]
        if rel:
            L += [f"At step {T - 1} the repaired relation is evaluated as:", *[f"- {x}" for x in facts_at(m, cx, T, b)]]
        else:
            L += ["- No active completion relation: the model no longer claims anything about completion."]
        L += ["", "## 6. Behaviour [MEASURED; post-observation verdicts]",
              f"- CX step {T - 1}: {cl['cx_verdict_at_step_27']} (old: NOT)",
              f"- regressions vs EXT2 M4: S1 {rg['S1']['n_regressions']}, S2 {rg['S2']['n_regressions']}, S3 "
              f"{rg['S3']['n_regressions']}; improvements S3 {rg['S3']['n_improvements']}",
              f"- false completion claims, all sets: {cl['false_completion_states_all_sets']}",
              f"- natural S3: exact {cl['natural_S3_exact']}, recovery successes exact {cl['natural_S3_recovery_exact']}, "
              f"abstentions {by['S3']['post']['abstain']}",
              "", "## 7. What is trusted [TRUSTED, not proved]",
              "- the observation parser and the exported atom values other than on(X); the completion labels (outcome + "
              "harness rule); that the Lean rows cover the records; the adopted assumptions above. Lean compilation checks "
              "the counts under these, not environment mechanics."]
        rep = "\n".join(L) + "\n"
        # the optimizer-facing view (an example: NOT sent; no optimizer call)
        if lab == "U" or lab == "P":
            finding = (f"FACT  the learned completion rule required that the object never be put down; this success refutes "
                       f"that. The rule was revised to: {IR.render(rel[0]).split('exactly when ', 1)[1]}.")
            advice = ("INFERENCE  advice that forbids putting the requested object down, or that treats the first pick-up "
                      "as the only valid one, is not supported by the records. What the records support is a condition "
                      "on the CURRENT state at the final step: holding the requested object at a turned-on lamp of the "
                      "requested type. A put-down and re-take is harmless (here it cost 2 steps).")
        elif lab == "W":
            finding = "FACT  the learned completion rule was contradicted by this success and withdrawn; no replacement passed the check."
            advice = ("INFERENCE  do not use the withdrawn rule ('never put the object down') as advice; completion is not "
                      "modelled until a supported rule exists.")
        else:
            finding = "FACT  the learned completion rule was contradicted by this success; the attempt did not produce a checked repair."
            advice = "INFERENCE  the old rule should not be used as advice; this report gives no positive guidance."
        opt = "\n".join([
            f"[CONTRACT FEEDBACK -- RP-1 example for SkillOpt's optimizer; NOT sent; attempt {s}, model {m['sha256'][:12]}]",
            f"Trajectory: '{cx['task']}' -- SUCCESS (ended at step {T - 1}).",
            "Finding type: MODEL REFUTATION (an over-restrictive learned rule), NOT an actor error.",
            "FACT  steps 1-3: took pencil 1 from desk 1, put it back on desk 1, took it again.",
            f"FACT  step 26: arrived at sidetable 1 (desklamp 1 listed there); step {T - 1}: 'use desklamp 1' -> "
            "'You turn on the desklamp 1.'; the task completed while pencil 1 was held.",
            finding, advice,
            "NOT ESTABLISHED  that re-taking helps; that the repaired rule is complete; the environment's mechanics "
            "(Lean checked counts under trusted parsing and assumptions)."]) + "\n"
        for name, txt in ((f"REPAIR_REPORT_{s}.md", rep), (f"OPTIMIZER_VIEW_{s}.txt", opt)):
            open(os.path.join(a.root, "reports", name), "w").write(txt)
            bind["reports"][name] = sha(txt)
        bind[f"model_{s}"] = m["sha256"]
        bind[f"lean_{s}"] = rc.get("lean_sha256")
    json.dump(bind, open(os.path.join(a.root, "reports", "REPORTS_BIND.json"), "w"), indent=1)
    print(json.dumps(bind, indent=1))


if __name__ == "__main__":
    main()
