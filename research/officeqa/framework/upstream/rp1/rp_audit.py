"""RP-1 section 7: audit of VX-1's before-action ("pre") completion forecasts. Local, offline, no network; existing
artifacts only. Nothing here changes forecasting semantics (vx_check.predict_episode is used as frozen).

  python3 -I -B rp_audit.py --root RPROOT      -> RPROOT/audit/AUDIT_FORWARD.json (+ .txt traces)

Labels: ORIGINAL = VX-1's committed PREDICTIONS.json files (learning batches); AUDIT REPLAY = a deterministic
recomputation with the frozen code (asserted equal to VX-1's saved aggregates or commit hashes). The follow-up
natural partition (S3) is read only after CHECKING_OPENED_RP1.json exists.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_check as K  # noqa: E402
import vx_eval as E  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402
from vx_state import EstimatorX  # noqa: E402

VX1 = os.path.expanduser("~/dev/lean_skillopt/runs/20261118-vx1")
STREAMS = ("EXT1", "EXT2")


def view(e):
    return {k: e[k] for k in ("episode_id", "task", "task_type", "steps")}


def tab(per, rows):
    """per: {eid: [verdict]}, rows: [(episode, won flags, step outcomes)] -> forecast table over ALL states."""
    out = collections.Counter()
    by_verb = collections.Counter()
    cond_fail = []
    for e, won, outc in rows:
        vs = per[e["episode_id"]]
        for i, (v, w) in enumerate(zip(vs, won)):
            out[f"{v}|{'completion' if w else 'not-completion'}"] += 1
            if w:
                by_verb[f"{e['steps'][i]['action'].split(' ')[0]}:{v}"] += 1
            if v == "COMPLETE" and outc[i] == "fail":
                cond_fail.append(f"{e['episode_id']}@{i}")
    return {"by_verdict_and_label": dict(sorted(out.items())), "at_completion_by_verb": dict(sorted(by_verb.items())),
            "false_completion_forecasts": out["COMPLETE|not-completion"],
            "completion_forecast_on_a_command_that_then_failed": cond_fail}


def lit_trace(model, est, t, ti, binding):
    c = IR.active(model, "completes")[0]
    rows = []
    for cl in c["condition"]:
        for l in cl:
            kinds = XV.VOCABS[model["vocab"]]["atoms"][l["atom"]][0]
            g = [IR.ground(a, k, binding, ti) for k, a in zip(kinds, l["args"])]
            f = est.value(t, l["atom"], g)
            v = None if f["v"] is None else (f["v"] if l["value"] else not f["v"])
            rows.append({"literal": IR.render_lit(l), "ground": g, "atom_value": f["v"], "literal_value": v,
                         "basis": f["basis"], "steps": f.get("steps"), "assumptions": f.get("assumptions"),
                         "learned": f.get("learned")})
    return rows


def dep_trace(model, e, t, binding):
    """The outcome-free input and the forecast's dependency trace for command t of episode e."""
    ti = K.tinfo_of(e)
    est = EstimatorX(model["assumptions"], e["task_type"], IR.active(model, "persists"))
    for st in e["steps"][:t]:
        est.observe(st["action"], st["obs"])
    cmd = XV.parse_command(e["steps"][t]["action"])
    atoms, by, skipped = K.synthetic_atoms(model, est, t, cmd, ti, e["task_type"])
    sim = EstimatorX(model["assumptions"], e["task_type"], IR.active(model, "persists"))
    sim.steps = list(est.steps)
    sim.add_synthetic(e["steps"][t]["action"], atoms, by)
    cv = K.verdict_at(model, sim, t, ti, e["task_type"])
    return {"input_task": e["task"], "input_prefix": [f"[{s['i']}] {s['action']} -> {s['obs']}" for s in e["steps"][:t]],
            "input_command": f"[{t}] {e['steps'][t]['action']}", "not_given": "the observation of step t and everything after it; the outcome",
            "synthetic_atoms": [{k: v for k, v in a.items() if k != "quote"} for a in atoms], "synthetic_by": by,
            "skipped": skipped, "forecast": cv["verdict"], "deps": cv["deps"], "unknown": cv["unknown"],
            "binding_shown": binding, "literals_at_predicted_post_state": lit_trace(model, sim, t + 1, ti, binding)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    res = {"schema": "rp.audit_forward.v1", "semantics": {}, "original_learning": {}, "replay_checking": {},
           "outcome_independence": {}, "explain_EXT2": {}, "dependency_traces": {}, "action_predictions": {}}
    res["semantics"] = {
        "source": "research/vocab_ext/vx_check.py predict_episode / synthetic_atoms / verdict_at (frozen in VX-1 R2)",
        "procedure": ["the view has no outcome field (asserted)", "replay.Revealer releases command t; the forecast for t is "
                      "computed from observations 0..t-1 + command t, hash-committed, and only then is observation t revealed",
                      "a SYNTHETIC step t is built from the model's learned EFFECT contracts for that verb (guards checked "
                      "on the pre-state; success assumed); the completion relation is evaluated on that predicted post-state",
                      "the action prediction (requires / suffices) is computed separately and does NOT gate the synthetic step"],
        "conditional": "yes: a forecast says 'if command t has its learned effects, the task is / is not complete'. It "
                       "never predicts whether the command succeeds; EXT1 / EXT2 have no requires / suffices items, so "
                       "they make no success prediction at all."}
    # ---- ORIGINAL committed predictions (learning batches) -----------------------------------------------------
    inp = json.load(open(os.path.join(VX1, "learn", "INPUTS.json")))
    eps_l, raws_l = E.load_part(VX1, "learning")
    byid = {e["episode_id"]: e for e in eps_l}
    vmap = {x["view"]: x["src"] for b in inp["batches"] for x in b}
    for r in STREAMS:
        res["original_learning"][r] = {}
        for b in range(1, 5):
            p = json.load(open(os.path.join(VX1, "learn", r, f"batch_{b}", "PREDICTIONS.json")))
            prior = IR.load_frozen(os.path.join(VX1, "learn", r, "M0.json" if b == 1 else f"batch_{b - 1}/M{b - 1}.json"))
            assert prior["sha256"] == p["prior_sha256"]
            per, rows, same = {}, [], 0
            for vid, preds in p["pre"].items():
                e = dict(byid[vmap[vid]], episode_id=vid)
                won = [s["won"] for s in raws_l[vmap[vid]]["steps"]]
                est = EstimatorX.of_steps(e["task_type"], e["steps"])
                rows.append((e, won, [s["outcome"] for s in est.steps]))
                per[vid] = [x["completion"]["verdict"] for x in preds]
                again = K.predict_episode(prior, view(e))     # audit replay of the commitment
                same += [x["commit"] for x in again] == [x["commit"] for x in preds]
            res["original_learning"][r][f"batch_{b}"] = {"prior_model": p["prior_sha256"][:12], "made_utc": p["made_utc"],
                                                         "episodes": len(per), "commit_chain_reproduced": f"{same} / {len(per)}",
                                                         **tab(per, rows)}
    # ---- AUDIT REPLAY on the VX-1 checking records --------------------------------------------------------------
    eps_c, raws_c = E.load_part(VX1, "checking")
    eps_c = [e for e in eps_c if e["steps"]]
    saved = json.load(open(os.path.join(VX1, "eval", "EVAL_checking.json")))
    models = {r: IR.load_frozen(os.path.join(VX1, "learn", r, "FINAL.json")) for r in STREAMS}
    rows_c = [(e, [s["won"] for s in raws_c[e["episode_id"]]["steps"]],
               [s["outcome"] for s in EstimatorX.of_steps(e["task_type"], e["steps"]).steps]) for e in eps_c]
    for r, m in models.items():
        per, recs = {}, {}
        for e in eps_c:
            recs[e["episode_id"]] = K.predict_episode(m, view(e))
            per[e["episode_id"]] = [x["completion"]["verdict"] for x in recs[e["episode_id"]]]
        sc = E.score_verdicts(per, raws_c, r)
        sv = saved["models"][r]["M4"]["pre"]
        for k in ("states", "exact", "false_completion_states", "correct_not", "abstain", "missed_by"):
            assert sc[k] == sv[k], (r, k, sc[k], sv[k])
        res["replay_checking"][r] = {"label": "AUDIT REPLAY (equal to VX-1 EVAL_checking.json pre aggregates)",
                                     "model": m["sha256"][:12], "exact": sc["exact"], "completions": sc["completions"],
                                     "missed_by": sc["missed_by"], "abstain": sc["abstain"], **tab(per, rows_c)}
        res["action_predictions"][r] = dict(collections.Counter(x["action_prediction"]["label"] for v in recs.values() for x in v))
        # outcome independence: withhold every observation from step t on; the forecast for t must not change
        diff, n = [], 0
        for e in eps_c:
            for t in range(len(e["steps"])):
                v2 = dict(view(e), steps=[dict(s, obs=s["obs"] if s["i"] < t else "<withheld>") for s in e["steps"]])
                x = K.predict_episode(m, v2)[t]
                y = recs[e["episode_id"]][t]
                n += 1
                if {k: x[k] for k in ("completion", "synthetic", "action_prediction")} != \
                        {k: y[k] for k in ("completion", "synthetic", "action_prediction")}:
                    diff.append(f"{e['episode_id']}@{t}")
        res["outcome_independence"][r] = {"label": "AUDIT REPLAY", "states_checked": n, "forecast_changed": len(diff),
                                          "examples": diff[:5]}
        # explanation at the 23 completion states
        ex = collections.Counter()
        for e, won, _ in rows_c:
            if not any(won):
                continue
            t = won.index(True)
            rec = recs[e["episode_id"]][t]
            ex[(e["steps"][t]["action"].split(" ")[0], rec["completion"]["verdict"], len(rec["synthetic"]["atoms"]) > 0,
                tuple(rec["completion"]["unknown"][:2]))] += 1
        res["explain_EXT2" if r == "EXT2" else "explain_EXT1"] = [
            {"completing_verb": k[0], "forecast": k[1], "synthetic_step_has_atoms": k[2], "unknown": list(k[3]), "n": n_}
            for k, n_ in sorted(ex.items())]
    # ---- two dependency traces (checking trial m09, the VX-1 report's worked example) --------------------------
    m09 = {raws_c[e["episode_id"]]["variant"]: e for e in eps_c if raws_c[e["episode_id"]]["slot"] == "m09"}
    for name, v in (("arrival_command (m09 L, completing 'go to desk 1')", "L"),
                    ("lamp_use_command (m09 O, completing 'use desklamp 1')", "O")):
        e = m09[v]
        t = len(e["steps"]) - 1
        held = next(a["obj"] for s in e["steps"] for a in XV.parse_obs(s["obs"]) if a["kind"] == "pick_up")
        lamp = "desklamp 1"
        res["dependency_traces"][name] = {r: dep_trace(models[r], e, t, {"?o": held, "?l": lamp}) for r in STREAMS}
    # ---- natural S3 (only once the RP-1 follow-up partition has been opened) ------------------------------------
    if os.path.exists(os.path.join(a.root, "eval", "CHECKING_OPENED_RP1.json")):
        rows_n, res["replay_natural_S3"] = [], {}
        for p in sorted(glob.glob(os.path.join(a.root, "natural_checking", "*.json"))):
            e = json.load(open(p))
            won = json.load(open(os.path.join(a.root, "eval_only", "natural_checking", e["episode_id"] + ".json")))["won"]
            rows_n.append((e, won, [s["outcome"] for s in EstimatorX.of_steps(e["task_type"], e["steps"]).steps]))
        for r, m in models.items():
            per = {e["episode_id"]: [x["completion"]["verdict"] for x in K.predict_episode(m, view(e))] for e, _, _ in rows_n}
            res["replay_natural_S3"][r] = {"label": "AUDIT REPLAY on S3 (after the RP-1 freeze)", **tab(per, rows_n)}
    os.makedirs(os.path.join(a.root, "audit"), exist_ok=True)
    json.dump(res, open(os.path.join(a.root, "audit", "AUDIT_FORWARD.json"), "w"), indent=1, default=str)
    print(json.dumps({k: res[k] for k in ("replay_checking", "outcome_independence", "explain_EXT2", "action_predictions")},
                     indent=1, default=str)[:6000])
    for b, x in res["original_learning"]["EXT2"].items():
        print("EXT2 original", b, x["commit_chain_reproduced"], x["at_completion_by_verb"], x["false_completion_forecasts"])
    for b, x in res["original_learning"]["EXT1"].items():
        print("EXT1 original", b, x["commit_chain_reproduced"], x["at_completion_by_verb"], x["false_completion_forecasts"])


if __name__ == "__main__":
    main()
