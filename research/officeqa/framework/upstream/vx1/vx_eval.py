"""VX-1 evaluation (amendment A5 section 8; local, offline; no network).

  vx_eval.py freeze-models --root R            list + verify every learned model version (sha, Lean receipt) and
                                               the two frozen reference checkers -> R/eval/MODELS_FROZEN.json
  vx_eval.py evaluate --root R --part learning|checking
                                               checking: refuses unless MODELS_FROZEN.json verifies; records the
                                               open time in R/eval/CHECKING_OPENED.json before reading any checking file
  vx_eval.py ceiling --root R                  structural ceilings on the LEARNING partition (exhaustive small CNFs)

Evaluator-only data (per-step `won`, variant, policy status) come from raw_<partition>/ and are used for SCORING only.
References: REV2 = a4_reading.ReadingA4(rev=2) (AC-1 frozen module); ORD2C = ord2_candidate.inspect_status (ORD-2
bundle 323747323ec8f8b8). Both are frozen and never saw any VX-1 record.
"""
from __future__ import annotations

import argparse
import collections
import glob
import itertools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "order_audit"))
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402
import vx_script as S  # noqa: E402

RUNS = ("OLD1", "OLD2", "EXT1", "EXT2")
SPEC = {"false_completion_max": 0, "route_exact_min": 0.80, "discovery_route": "L", "min_negative_states": 10}


def jdump(o, p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p, "w"), indent=1, default=str)


def model_files(root):
    out = {}
    for r in RUNS:
        fs = sorted(glob.glob(os.path.join(root, "learn", r, "batch_*", "M*.json")),
                    key=lambda p: int(os.path.basename(p)[1:-5]))
        out[r] = [os.path.join(root, "learn", r, "M0.json")] + fs
    return out


def freeze_models(root):
    rows = {}
    for r, fs in model_files(root).items():
        rows[r] = []
        for p in fs:
            m = IR.load_frozen(p)
            d = os.path.dirname(p)
            rc = json.load(open(os.path.join(d, "lean", "RECEIPT.json"))) if os.path.exists(os.path.join(d, "lean", "RECEIPT.json")) else None
            rows[r].append({"file": os.path.relpath(p, root), "sha256": m["sha256"], "version": m["version"],
                            "batch": m.get("batch"), "lean_rc": rc and rc.get("rc"), "lean_sha256": rc and rc.get("lean_sha256")})
        fin = IR.load_frozen(os.path.join(root, "learn", r, "FINAL.json"))
        assert fin["sha256"] == rows[r][-1]["sha256"], (r, "FINAL differs from the last batch model")
    import subprocess
    ob = subprocess.run([sys.executable, "-I", "-B", os.path.join(os.path.dirname(HERE), "order_audit", "ord2_bundle.py"),
                         "verify", "--out", os.path.expanduser("~/dev/lean_skillopt/runs/20261118-order-audit-2")],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode().strip()
    assert ob.startswith("verified 323747323ec8f8b8"), ob
    ref = {"REV2": "research/api_campaign/overlay/research/api_campaign_lc/frozen/a4_reading.py (rev=2)",
           "ORD2C": "research/order_audit/ord2_candidate.py (ORD-2 bundle 323747323ec8f8b8)"}
    out = {"schema": "vx.models_frozen.v1", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runs": rows,
           "references": ref, "ord2_bundle_verify": ob}
    p = os.path.join(root, "eval", "MODELS_FROZEN.json")
    if os.path.exists(p):
        old = json.load(open(p))
        assert old["runs"] == rows, "MODELS_FROZEN.json exists with other content"
        return old
    jdump(out, p)
    os.chmod(p, 0o444)
    return out


# ---- records ------------------------------------------------------------------------------------------------------
def load_part(root, part):
    eps, raw = [], {}
    for p in sorted(glob.glob(os.path.join(root, "collect", part, "*.json"))):
        eps.append(json.load(open(p)))
    for p in sorted(glob.glob(os.path.join(root, "collect", f"raw_{part}", "*", "*.json"))):
        d = json.load(open(p))
        raw[d["episode_id"]] = d
    return eps, raw


def realized_route(raw) -> str:
    """From evaluator data: the variant, and for completions where the completing step was (go = arrival, use, take)."""
    v = raw["variant"]
    st = raw["policy_status"]
    if st.startswith("setup_failed"):
        return f"{v}|setup_failed"
    if st == "unavailable_same_place":
        return f"{v}|unavailable"
    if raw["final"]["won"]:
        last = raw["steps"][-1]["executed"].split(" ")[0]
        return f"{v}|completed@{last}"
    return f"{v}|not_completed"


# ---- references ---------------------------------------------------------------------------------------------------
def reference_verdicts(ep):
    import ord_common as C
    import ord2_candidate as CAND
    e2 = {"task_description": ep["task"], "steps": [{"action": s["action"], "obs": s["obs"]} for s in ep["steps"]]}
    sp, _ = C.spec(e2)
    vis = C.parse_visible(e2)
    R2 = C.A4.ReadingA4(sp, C.V.type_of, 2)
    R3 = CAND.ReadingR3U(sp, C.V.type_of)
    out = {"REV2": [], "ORD2C": []}
    for s in vis:
        R2.apply(s)
        R3.apply(s)
        out["REV2"].append("COMPLETE" if R2.goal_now() else "NOT")
        st = CAND.inspect_status(R3)["status"]
        out["ORD2C"].append({"SUPPORTED": "COMPLETE", "NOT_SUPPORTED": "NOT", "UNKNOWN": "UNKNOWN"}[st])
    return out


# ---- scoring ------------------------------------------------------------------------------------------------------
def score_verdicts(per_ep, raws, kind):
    """per_ep: {episode_id: [verdict per state]}; returns the A5 section 8 measures."""
    out = {"states": 0, "false_completion_states": 0, "false_completion_episodes": [], "by_variant": {},
           "completions": 0, "exact": 0, "early": 0, "missed_by": collections.Counter(), "abstain": collections.Counter(),
           "correct_not": 0, "determinate": 0, "route": {}}
    for eid, vs in per_ep.items():
        rw = raws[eid]
        won = [s["won"] for s in rw["steps"]]
        v = rw["variant"]
        bv = out["by_variant"].setdefault(v, {"episodes": 0, "states": 0, "COMPLETE@won": 0, "COMPLETE@notwon": 0,
                                             "NOT@notwon": 0, "NOT@won": 0, "abstain@won": 0, "abstain@notwon": 0})
        bv["episodes"] += 1
        fc = False
        for x, w in zip(vs, won):
            out["states"] += 1
            bv["states"] += 1
            if x in ("COMPLETE", "NOT"):
                out["determinate"] += 1
            if x == "COMPLETE" and not w:
                out["false_completion_states"] += 1
                bv["COMPLETE@notwon"] += 1
                fc = True
            elif x == "COMPLETE":
                bv["COMPLETE@won"] += 1
            elif x == "NOT" and not w:
                out["correct_not"] += 1
                bv["NOT@notwon"] += 1
            elif x == "NOT":
                bv["NOT@won"] += 1
            else:
                out["abstain"][x] += 1
                bv["abstain@won" if w else "abstain@notwon"] += 1
        if fc:
            out["false_completion_episodes"].append(eid)
        if any(won):
            ws = won.index(True)
            out["completions"] += 1
            first = next((i for i, x in enumerate(vs) if x == "COMPLETE"), None)
            rr = realized_route(rw)
            ro = out["route"].setdefault(rr, {"completions": 0, "exact": 0})
            ro["completions"] += 1
            if first == ws:
                out["exact"] += 1
                ro["exact"] += 1
            elif first is not None and first < ws:
                out["early"] += 1
            else:
                out["missed_by"][vs[ws]] += 1
    out["coverage"] = round(out["determinate"] / out["states"], 4) if out["states"] else None
    out["missed_by"], out["abstain"] = dict(out["missed_by"]), dict(out["abstain"])
    return out


def evaluate(root, part):
    if part == "checking":
        mf = os.path.join(root, "eval", "MODELS_FROZEN.json")
        if not os.path.exists(mf):
            raise SystemExit("REFUSED: freeze-models first")
        frozen = json.load(open(mf))
        for r, rows in frozen["runs"].items():
            for x in rows:
                assert IR.load_frozen(os.path.join(root, x["file"]))["sha256"] == x["sha256"]
        op = os.path.join(root, "eval", "CHECKING_OPENED.json")
        if not os.path.exists(op):
            jdump({"opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "models_frozen_sha256":
                   XV.sha_file(mf)}, op)
    eps, raws = load_part(root, part)
    eps = [e for e in eps if e["steps"]]
    for e in eps:   # the analyst outcome and the evaluator flags must agree (won only at the last step of a success)
        won = [s["won"] for s in raws[e["episode_id"]]["steps"]]
        assert won == [e["outcome"] == "success" and i == len(won) - 1 for i in range(len(won))], e["episode_id"]
    res = {"part": part, "episodes": len(eps), "trials": len({raws[e["episode_id"]]["trial"] for e in eps}),
           "task_dirs": len({raws[e["episode_id"]]["task_dir"] for e in eps}),
           "states": sum(len(e["steps"]) for e in eps),
           "routes": dict(collections.Counter(realized_route(raws[e["episode_id"]]) for e in eps)),
           "not_collected": {}, "models": {}, "references": {}}
    allraw = {d["episode_id"]: d for d in raws.values()}
    res["not_collected"] = dict(collections.Counter(realized_route(d) for d in allraw.values() if not d["steps"]))
    refs = {e["episode_id"]: reference_verdicts(e) for e in eps}
    for k in ("REV2", "ORD2C"):
        res["references"][k] = score_verdicts({e: refs[e][k] for e in refs}, raws, k)
    for r, fs in model_files(root).items():
        res["models"][r] = {}
        for p in fs:
            m = IR.load_frozen(p)
            tag = f"M{m.get('batch', 0)}"
            post, pre, deps = {}, {}, collections.Counter()
            unk = collections.Counter()
            for e in eps:
                vs = K.post_verdicts(m, e)
                post[e["episode_id"]] = [v["verdict"] for v in vs]
                for v in vs:
                    if v["verdict"] == "COMPLETE":
                        deps[json.dumps(v["deps"], sort_keys=True)] += 1
                    elif v["verdict"] == "UNKNOWN":
                        for u in v["unknown"][:3]:
                            unk[u.split("(")[0] + ":" + u.split(":")[-1]] += 1
                view = {k: e[k] for k in ("episode_id", "task", "task_type", "steps")}
                pre[e["episode_id"]] = [p_["completion"]["verdict"] for p_ in K.predict_episode(m, view)]
            res["models"][r][tag] = {"sha256": m["sha256"], "post": score_verdicts(post, raws, r),
                                     "pre": score_verdicts(pre, raws, r),
                                     "complete_dependencies": {k: n for k, n in deps.most_common(8)},
                                     "unknown_causes": dict(unk.most_common(10)),
                                     "assumptions": m["assumptions"],
                                     "active": [IR.render(c) for c in IR.active(m)]}
    jdump(res, os.path.join(root, "eval", f"EVAL_{part}.json"))
    return res


# ---- structural ceilings (learning partition only) -------------------------------------------------------------------
def ceiling(root):
    eps, raws = load_part(root, "learning")
    eps = [e for e in eps if e["steps"]]
    ev = K.Evidence(eps)
    out = {}
    for vname, assumptions in (("OLD", ["persistence", "init_empty_hands", "distinct_locations", "listing_complete"]),):
        found = []
        for scope in ("use", "go", "take", "any"):
            roles = XV.VERBS[scope]["roles"] if scope != "any" else ()
            lits = []
            for atom, (kinds, _, _) in XV.ATOMS_OLD.items():
                if atom == "is_a":
                    for x in roles:
                        for ty in ("OTYPE", "LTYPE"):
                            lits.append({"atom": "is_a", "args": [x, ty]})
                    continue
                if any(k == "X" or k == "TYPE" for k in kinds):
                    continue
                if all(k in roles for k in kinds):
                    lits.append({"atom": atom, "args": list(kinds)})
            cands = [dict(l, value=v) for l in lits for v in (True, False)]
            for n in (1, 2, 3):
                for combo in itertools.combinations(cands, n):
                    if len({(c["atom"], tuple(c["args"])) for c in combo}) < n:
                        continue
                    c = {"kind": "completes", "scope": scope, "condition": [[x] for x in combo]}
                    sc = K.score(c, ev, assumptions, [], vname)
                    if not sc["counter"] and sc["support"]:
                        found.append({"relation": IR.render(c), "support": len(sc["support"]),
                                      "undetermined": sc["undetermined"], "und_at_completion": sc["und_at_completion"]})
        total = sum(1 for e in eps if e["outcome"] == "success")
        out[vname] = {"assumptions": assumptions, "consistent_relations": sorted(found, key=lambda x: -x["support"])[:20],
                      "n_consistent": len(found), "completions_in_learning": total}
    ref = {"kind": "completes", "scope": "any", "exists": {"?o": "OTYPE", "?l": "LTYPE"},
           "condition": [[{"atom": "holding", "args": ["?o"], "value": True}], [{"atom": "on", "args": ["?l"], "value": True}],
                         [{"atom": "co_located", "args": ["?l"], "value": True}]]}
    per = [{"kind": "persists", "state": "on", "established_by": "activated", "invalidated_by": ["picked"], "id": "ref.p",
            "status": "active"}]
    sc = K.score(ref, ev, ["persistence", "init_empty_hands", "distinct_locations", "listing_complete", "init_off"], per, "EXT")
    out["EXT_reference_expressible"] = {"relation": IR.render(ref) + " + persists(picked)", "support": len(sc["support"]),
                                        "counter": len(sc["counter"]), "undetermined": sc["undetermined"],
                                        "note": "researcher-written reference (never shown to the learner)"}
    jdump(out, os.path.join(root, "eval", "CEILING_learning.json"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("freeze-models", "evaluate", "ceiling"))
    ap.add_argument("--root", required=True)
    ap.add_argument("--part", default="learning")
    a = ap.parse_args()
    if a.cmd == "freeze-models":
        r = freeze_models(a.root)
        print(json.dumps({k: [(x["version"], x["sha256"][:12], x["lean_rc"]) for x in v] for k, v in r["runs"].items()}))
    elif a.cmd == "evaluate":
        r = evaluate(a.root, a.part)
        print(json.dumps({k: r[k] for k in ("part", "episodes", "trials", "task_dirs", "states", "routes")}, indent=1))
    else:
        print(json.dumps(ceiling(a.root), indent=1)[:4000])


if __name__ == "__main__":
    main()
