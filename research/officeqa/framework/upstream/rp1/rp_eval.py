"""RP-1 evaluation (protocol docs/research/RP1_PROTOCOL.md section 6; local, offline; no network).

  rp_eval.py freeze-models --root R     list + verify every RP-1 model (both attempts: round 1 and candidate) and the
                                        two unchanged references; re-verify the VX-1 plan / code-R2 bundles and the
                                        ORD-2 bundle -> R/eval/MODELS_FROZEN_RP1.json (read-only)
  rp_eval.py evaluate --root R          refuses unless MODELS_FROZEN_RP1.json verifies; writes CHECKING_OPENED_RP1.json
                                        BEFORE reading any follow-up checking (S3) file; scores every model on
                                        S0 (CX), S1 (VX-1 learning), S2 (VX-1 checking), S3 (natural checking),
                                        S4 (CX's own task directory) -> R/eval/EVAL_RP1.json
  rp_eval.py lean-export --root R       Lean receipt sources: candidates + references on S0, S2, S3

Evaluator-only per-step won flags are read for SCORING only, after asserting they equal the outcome-derived labels.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_lean as XL  # noqa: E402
import vx_vocab as XV  # noqa: E402
import rp_select as RS  # noqa: E402

VX1 = os.path.expanduser("~/dev/lean_skillopt/runs/20261118-vx1")
WT = os.path.dirname(os.path.dirname(HERE))
REFS = {"EXT2_M4": ("learn/EXT2/FINAL.json", "55da91f31189d84b3c84f5308e63dde697037ed445e4c78b9eaf1058a67b469c"),
        "EXT1_M4": ("learn/EXT1/FINAL.json", "34d9fda0b5735bffd52b8795ca1bb248261681dabc55dc54cdb26740e850a366")}
SETS = ("S0", "S1", "S2", "S3", "S4")
SET_NAMES = {"S0": "counterexample CX (tr:0215; seen by the repair learner)",
             "S1": "VX-1 learning records (84; repair evidence; in-sample)",
             "S2": "VX-1 checking records (78; regression replay; opened in VX-1)",
             "S3": "follow-up natural checking (63; task-disjoint from CX; reused, researcher-exposed)",
             "S4": "CX's own task directory, other episodes (4; same task, not confirmation)"}
CX_STEP = 27


def jdump(o, p, ro=False):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p, "w"), indent=1, default=str, ensure_ascii=False)
    if ro:
        os.chmod(p, 0o444)


def model_paths(root) -> dict:
    out = {k: os.path.join(VX1, v[0]) for k, v in REFS.items()}
    for s in ("A", "B"):
        out[f"{s}_round1"] = os.path.join(root, "repair", s, "M5_round1.json")
        out[s] = os.path.join(root, "repair", s, "CANDIDATE.json")
    return out


def load_models(root) -> dict:
    ms = {k: IR.load_frozen(p) for k, p in model_paths(root).items()}
    for k, (_, h) in REFS.items():
        assert ms[k]["sha256"] == h, k
    return ms


def freeze_models(root):
    ms = load_models(root)
    rows = {}
    for k, p in model_paths(root).items():
        rc = None
        rp = os.path.join(os.path.dirname(p), "lean", "RECEIPT.json")
        if k in ("A", "B") and os.path.exists(rp):
            rc = json.load(open(rp))
        rows[k] = {"file": p.replace(os.path.expanduser("~"), "~"), "sha256": ms[k]["sha256"],
                   "version": ms[k]["version"], "lean_rc": rc and rc.get("rc"), "lean_sha256": rc and rc.get("lean_sha256")}
    py = sys.executable
    vb = subprocess.run([py, "-I", "-B", os.path.join(HERE, "rp_bundle.py"), "verify-vx1", "--out", VX1],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode().strip()
    ob = subprocess.run([py, "-I", "-B", os.path.join(os.path.dirname(HERE), "order_audit", "ord2_bundle.py"), "verify",
                         "--out", os.path.expanduser("~/dev/lean_skillopt/runs/20261118-order-audit-2")],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode().strip()
    assert ob.startswith("verified 323747323ec8f8b8"), ob
    out = {"schema": "rp.models_frozen.v1", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "models": rows,
           "vx1_bundle_verify": vb, "ord2_bundle_verify": ob}
    p = os.path.join(root, "eval", "MODELS_FROZEN_RP1.json")
    if os.path.exists(p):
        old = json.load(open(p))
        assert old["models"] == rows, "MODELS_FROZEN_RP1.json exists with other content"
        return old
    jdump(out, p, ro=True)
    return out


# ---- records ------------------------------------------------------------------------------------------------------
def load_sets(root) -> dict:
    sel = json.load(open(os.path.join(root, "selection", "RP1_SELECTION.json")))
    out = {}
    cx = json.load(open(os.path.join(root, "repair_input", "cx_view.json")))
    cxid = sel["counterexample"]["id"]
    w = json.load(open(os.path.join(root, "eval_only", "natural_same_task", cxid + ".json")))["won"]
    out["S0"] = [(dict(cx, episode_id=cxid), w, {"recovery": True, "task_dir": sel["counterexample"]["task_dir"]})]
    import vx_eval as E
    for s, part in (("S1", "learning"), ("S2", "checking")):
        eps, raws = E.load_part(VX1, part)
        out[s] = [(e, [x["won"] for x in raws[e["episode_id"]]["steps"]],
                   {"recovery": RS.recovery(e)["recovery"], "task_dir": raws[e["episode_id"]]["task_dir"],
                    "variant": raws[e["episode_id"]]["variant"]})
                  for e in eps if e["steps"]]
    meta = {r["id"]: r for r in sel["episodes"]}
    for s, d in (("S3", "natural_checking"), ("S4", "natural_same_task")):
        rows = []
        for p in sorted(glob.glob(os.path.join(root, d, "*.json"))):
            e = json.load(open(p))
            if e["episode_id"] == cxid:
                continue
            w = json.load(open(os.path.join(root, "eval_only", d, e["episode_id"] + ".json")))["won"]
            rows.append((e, w, {"recovery": meta[e["episode_id"]]["recovery"], "task_dir": meta[e["episode_id"]]["task_dir"],
                                "tr_ids": meta[e["episode_id"]]["tr_ids"]}))
        out[s] = rows
    assert sorted(e["episode_id"] for e, _, _ in out["S3"]) == sel["S3_checking"]["ids"]
    for s, rows in out.items():   # the analyst outcome and the evaluator flags must agree (harness rule)
        for e, w, _ in rows:
            assert w == [e["outcome"] == "success" and i == len(w) - 1 for i in range(len(w))], (s, e["episode_id"])
    return out


# ---- scoring ------------------------------------------------------------------------------------------------------
def correct(v, lab) -> bool:
    return (v == "COMPLETE" and lab) or (v == "NOT" and not lab)


def score(per, rows) -> dict:
    out = {"episodes": len(rows), "states": 0, "completions": 0, "exact": 0, "early": 0, "missed_by": collections.Counter(),
           "false_completion_states": 0, "false_completion_episodes": [], "correct_not": 0,
           "abstain": collections.Counter(), "recovery_successes": 0, "recovery_exact": 0, "recovery_failures": 0,
           "recovery_failure_fc_episodes": 0, "determinate": 0}
    for e, won, meta in rows:
        vs = per[e["episode_id"]]
        fc = False
        for v, w in zip(vs, won):
            out["states"] += 1
            out["determinate"] += v in ("COMPLETE", "NOT")
            if v == "COMPLETE" and not w:
                out["false_completion_states"] += 1
                fc = True
            elif v == "NOT" and not w:
                out["correct_not"] += 1
            elif v not in ("COMPLETE", "NOT"):
                out["abstain"][v] += 1
        if fc:
            out["false_completion_episodes"].append(e["episode_id"])
        if meta["recovery"] and not any(won):
            out["recovery_failures"] += 1
            out["recovery_failure_fc_episodes"] += int(fc)
        if any(won):
            ws = won.index(True)
            out["completions"] += 1
            first = next((i for i, x in enumerate(vs) if x == "COMPLETE"), None)
            ex = first == ws
            out["exact"] += ex
            if first is not None and first < ws:
                out["early"] += 1
            elif not ex:
                out["missed_by"][vs[ws]] += 1
            if meta["recovery"]:
                out["recovery_successes"] += 1
                out["recovery_exact"] += ex
    out["coverage"] = round(out["determinate"] / out["states"], 4) if out["states"] else None
    out["missed_by"], out["abstain"] = dict(out["missed_by"]), dict(out["abstain"])
    return out


def sym(v):
    return {"COMPLETE": "C", "NOT": "n", "UNKNOWN": "?", "CONFLICT": "!", "NO_CLAIM": "-"}[v]


def evaluate(root):
    mf = os.path.join(root, "eval", "MODELS_FROZEN_RP1.json")
    if not os.path.exists(mf):
        raise SystemExit("REFUSED: freeze-models first")
    frozen = json.load(open(mf))
    ms = load_models(root)
    for k, r in frozen["models"].items():
        assert ms[k]["sha256"] == r["sha256"], k
    op = os.path.join(root, "eval", "CHECKING_OPENED_RP1.json")
    if not os.path.exists(op):
        jdump({"opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "models_frozen_sha256": XV.sha_file(mf)}, op)
    sets = load_sets(root)
    res = {"schema": "rp.eval.v1", "sets": {s: {"name": SET_NAMES[s], "episodes": len(r), "states": sum(len(w) for _, w, _ in r),
                                                "task_dirs": len({mt["task_dir"] for _, _, mt in r}),
                                                "completions": sum(any(w) for _, w, _ in r),
                                                "recovery_successes": sum(mt["recovery"] and any(w) for _, w, mt in r),
                                                "recovery_failures": sum(mt["recovery"] and not any(w) for _, w, mt in r)}
                                            for s, r in sets.items()},
           "models": {}, "regressions_vs_EXT2_M4": {}, "witnesses": {}}
    verd = {}
    for k, m in ms.items():
        res["models"][k] = {"sha256": m["sha256"], "assumptions": m["assumptions"],
                            "active": [{"id": c["id"], "version": c.get("version"), "rendered": IR.render(c)} for c in IR.active(m)],
                            "inactive": [{"id": c["id"], "version": c.get("version"), "status": c["status"],
                                          "rendered": IR.render(c)} for c in m["contracts"] if c["status"] != "active"],
                            "by_set": {}}
        verd[k] = {}
        for s, rows in sets.items():
            post, pre, deps, unk = {}, {}, collections.Counter(), collections.Counter()
            for e, won, meta in rows:
                V = K.post_verdicts(m, e)
                post[e["episode_id"]] = [v["verdict"] for v in V]
                for v in V:
                    if v["verdict"] == "COMPLETE":
                        deps[json.dumps(v["deps"], sort_keys=True)] += 1
                    elif v["verdict"] == "UNKNOWN":
                        for u in v["unknown"][:3]:
                            unk[u.split("(")[0] + ":" + u.split(":")[-1]] += 1
                view = {x: e[x] for x in ("episode_id", "task", "task_type", "steps")}
                pre[e["episode_id"]] = [p["completion"]["verdict"] for p in K.predict_episode(m, view)]
            verd[k][s] = post
            res["models"][k]["by_set"][s] = {"post": score(post, rows), "pre_conditional": score(pre, rows),
                                             "complete_dependencies": dict(deps.most_common(8)),
                                             "unknown_causes": dict(unk.most_common(8))}
    # regressions / improvements vs the start model, per state
    for k in ms:
        res["regressions_vs_EXT2_M4"][k] = {}
        for s, rows in sets.items():
            reg, imp, ch = [], [], 0
            for e, won, meta in rows:
                a, b = verd["EXT2_M4"][s][e["episode_id"]], verd[k][s][e["episode_id"]]
                for i, (x, y) in enumerate(zip(a, b)):
                    ch += x != y
                    if correct(x, won[i]) and not correct(y, won[i]):
                        reg.append(f"{e['episode_id']}@{i} '{e['steps'][i]['action']}': {x} -> {y} (won={won[i]})")
                    elif correct(y, won[i]) and not correct(x, won[i]):
                        imp.append(f"{e['episode_id']}@{i} '{e['steps'][i]['action']}': {x} -> {y} (won={won[i]})")
            res["regressions_vs_EXT2_M4"][k][s] = {"changed": ch, "n_regressions": len(reg), "n_improvements": len(imp),
                                                   "regressions": reg[:30], "improvements": imp[:30]}
    # witnesses: CX, every recovery episode, every episode with a false completion or a regression in any model
    wit_eps = set()
    for s, rows in sets.items():
        for e, won, meta in rows:
            bad = any(verd[k][s][e["episode_id"]][i] == "COMPLETE" and not won[i] for k in ms for i in range(len(won)))
            changed = any(verd[k][s][e["episode_id"]] != verd["EXT2_M4"][s][e["episode_id"]] for k in ms)
            if s in ("S0", "S3", "S4") and (meta["recovery"] or bad or changed) or (s in ("S1", "S2") and (bad or changed)):
                wit_eps.add((s, e["episode_id"]))
    for s, rows in sets.items():
        for e, won, meta in rows:
            if (s, e["episode_id"]) not in wit_eps:
                continue
            res["witnesses"][f"{s}/{e['episode_id']}"] = {
                "set": s, "task": e["task"], "outcome": e["outcome"], "meta": meta,
                "won": "".join("W" if w else "." for w in won),
                "verdicts": {k: "".join(sym(v) for v in verd[k][s][e["episode_id"]]) for k in ms},
                "steps": [f"[{st['i']}] {st['action']} -> {st['obs'][:140]}" for st in e["steps"]]}
    # classification (frozen rules, protocol section 6.3)
    res["classification"] = {}
    for k in ("A", "B", "A_round1", "B_round1", "EXT2_M4", "EXT1_M4"):
        by = res["models"][k]["by_set"]
        fc = sum(by[s]["post"]["false_completion_states"] for s in SETS)
        cxv = verd[k]["S0"][sets["S0"][0][0]["episode_id"]][CX_STEP]
        cx_exact = by["S0"]["post"]["exact"] == 1
        reg = sum(res["regressions_vs_EXT2_M4"][k][s]["n_regressions"] for s in ("S1", "S2"))
        has_rel = any(c["kind"] == "completes" for c in IR.active(ms[k]))
        if fc:
            lab = "O overgeneralization (false completion claims introduced)"
        elif cx_exact and reg == 0 and has_rel:
            lab = "U useful repair"
        elif cx_exact:
            lab = "P repair with lost earlier behaviour (CX recognised; regressions on S1/S2)"
        elif cxv in ("UNKNOWN", "NO_CLAIM"):
            lab = "W safe withdrawal (no false claim; CX left unknown / no claim)"
        else:
            lab = "F failure to repair (CX still " + cxv + ")"
        res["classification"][k] = {"label": lab, "cx_verdict_at_step_27": cxv, "false_completion_states_all_sets": fc,
                                    "regressions_S1_S2": reg, "active_completion_relation": has_rel,
                                    "natural_S3_exact": f"{by['S3']['post']['exact']} / {by['S3']['post']['completions']}",
                                    "natural_S3_recovery_exact": f"{by['S3']['post']['recovery_exact']} / {by['S3']['post']['recovery_successes']}"}
    jdump(res, os.path.join(root, "eval", "EVAL_RP1.json"))
    return res


def lean_export(root):
    if not os.path.exists(os.path.join(root, "eval", "CHECKING_OPENED_RP1.json")):
        raise SystemExit("REFUSED: evaluate first")
    ms = load_models(root)
    sets = load_sets(root)
    out = os.path.join(root, "eval", "lean")
    os.makedirs(out, exist_ok=True)
    idx = {}
    for k in ("EXT2_M4", "EXT1_M4", "A", "B"):
        for s in ("S0", "S2", "S3"):
            eps = [e for e, _, _ in sets[s]]
            src, side = XL.export(ms[k], eps, f"RP-1 evaluation receipt: model {k} on {s} ({SET_NAMES[s]})")
            name = f"RpEval_{k}_{s}.lean"
            open(os.path.join(out, name), "w").write(src)
            json.dump(side, open(os.path.join(out, name.replace(".lean", ".sidecar.json")), "w"), indent=1)
            idx[name] = {"model": k, "model_sha256": ms[k]["sha256"], "set": s, "lean_sha256": XV.sha_file(os.path.join(out, name)),
                         "contracts": {c: v["counts_claimed"] for c, v in side["contracts"].items()}}
            print(name, idx[name]["contracts"])
    json.dump(idx, open(os.path.join(out, "INDEX.json"), "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("freeze-models", "evaluate", "lean-export"))
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    if a.cmd == "freeze-models":
        r = freeze_models(a.root)
        print(json.dumps({k: (v["sha256"][:12], v["lean_rc"]) for k, v in r["models"].items()}), r["vx1_bundle_verify"])
    elif a.cmd == "evaluate":
        r = evaluate(a.root)
        print(json.dumps(r["classification"], indent=1))
    else:
        lean_export(a.root)


if __name__ == "__main__":
    main()
