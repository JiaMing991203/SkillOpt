#!/usr/bin/env python3
"""RP-1 selection (counterexample-repair pilot after VX-1; protocol docs/research/RP1_PROTOCOL.md). Local, offline.

  python3 -I -B rp_select.py --out RUNROOT

Mechanical, fixed BEFORE any learner call:
  * the counterexample CX = the natural lamp episode named by the USER (tr:0215, visible-content digest bbd540c89f6f),
    rebuilt from its RAW trace file (executed_action + env_feedback only; the actor's reasoning, raw response, prompts,
    admissible lists and per-step won flags are NOT copied into the learner view);
  * the repair evidence = the 84 VX-1 learning records (byte-identical to the learning job's input) + CX;
  * S3, the follow-up natural checking partition = every ORD-2 natural lamp episode (68 distinct, de-duplicated by
    visible content) whose task directory differs from CX's; S4 = the other episodes of CX's own task directory
    (reported separately: same task, never confirmation);
  * recovery cases (mechanical): some instance of the task's object type has a recorded pick-up, then a recorded
    put-down (move), then another pick-up, in the observation atoms of the EXT grammar.
Per-step won flags go to eval_only/ (scoring only). Nothing here is given to a learner except repair_input/.
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_vocab as XV  # noqa: E402

VX1 = os.path.expanduser("~/dev/lean_skillopt/runs/20261118-vx1")
ORD2 = os.path.expanduser("~/dev/lean_skillopt/runs/20261118-order-audit-2/ORD2_WITNESSES.json")
CX_VIS, CX_TR = "bbd540c89f6f", "tr:0215"
START = ("EXT2", "55da91f31189d84b3c84f5308e63dde697037ed445e4c78b9eaf1058a67b469c")
REF = ("EXT1", "34d9fda0b5735bffd52b8795ca1bb248261681dabc55dc54cdb26740e850a366")


def sf(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(o):
    return hashlib.sha256(canon(o).encode()).hexdigest()


def jdump(o, p, ro=True):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        raise SystemExit(f"{p} exists (selection is written once)")
    json.dump(o, open(p, "w"), indent=1, ensure_ascii=False)
    if ro:
        os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def raw_view(copy: dict, witness: dict) -> tuple:
    """(analyst view, evaluator-only won flags) from the raw trace file; asserted equal to the ORD-2 witness."""
    assert sf(copy["path"]) == copy["file_sha256"], copy["path"]
    t = json.load(open(copy["path"], encoding="utf-8"))
    st = [s for s in t["steps"] if s.get("executed_action") is not None]
    assert len(st) == len(witness["steps"])
    for s, w in zip(st, witness["steps"]):
        assert s["executed_action"] == w["executed"] and s.get("env_feedback") == w["observation"]
    won = [bool(s.get("won")) for s in st]
    ok = bool((t.get("final") or {}).get("won"))
    view = {"task": t["task_description"], "task_type": t.get("task_type") or "look_at_obj_in_light",
            "outcome": "success" if ok else "failure",
            "steps": [{"i": i, "action": s["executed_action"], "obs": s.get("env_feedback")} for i, s in enumerate(st)]}
    # harness rule (declared analyst-visible): won only at the last step of a success
    assert won == [ok and i == len(st) - 1 for i in range(len(st))], copy["path"]
    return view, won


def recovery(view: dict) -> dict:
    ot = (XV.task_info(view["task"]) or {}).get("OTYPE")
    seq = collections.defaultdict(list)
    for s in view["steps"]:
        for a in XV.parse_obs(s["obs"] or ""):
            if a["kind"] in ("pick_up", "move_done") and a.get("obj") and XV.type_of(a["obj"]) == ot:
                seq[a["obj"]].append(("p" if a["kind"] == "pick_up" else "m", s["i"]))
    hit = None
    for o, ev in sorted(seq.items()):
        st = 0
        steps = []
        for k, i in ev:
            if (st, k) in ((0, "p"), (1, "m"), (2, "p")):
                st += 1
                steps.append(i)
                if st == 3:
                    hit = {"object": o, "pick": steps[0], "put_down": steps[1], "retake": steps[2]}
                    break
        if hit:
            break
    return {"recovery": hit is not None, "witness": hit,
            "events": {o: "".join(k for k, _ in ev) for o, ev in seq.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = a.out
    W = json.load(open(ORD2))
    assert W["n_distinct_lamp_episodes"] == 68 and W["by_role"] == {"checking": 8, "development": 60}
    # ---- VX-1 learning records: byte-identical to the VX-1 learning job's input ------------------------------
    lsha = {}
    for line in open(os.path.join(VX1, "jobs", "learn2_66413112", "input", "learning.sha256")):
        h, f = line.split()
        lsha[f] = h
    ldir = os.path.join(VX1, "collect", "learning")
    files = sorted(os.listdir(ldir))
    assert files == sorted(lsha) and all(sf(os.path.join(ldir, f)) == lsha[f] for f in files), "learning records differ"
    vx1_dirs = {}
    for p in ("learning", "checking"):
        vx1_dirs[p] = sorted({json.load(open(f))["task_dir"] for f in glob.glob(os.path.join(VX1, "collect", f"raw_{p}", "*", "*.json"))})
    # ---- natural episodes ----------------------------------------------------------------------------------
    rows, views = [], {}
    cx = None
    for e in W["episodes"]:
        v, won = raw_view(e["copies"][0], e)
        eid = "N" + e["vis_sha256"][:12]
        rec = recovery(v)
        row = {"id": eid, "vis_sha256": e["vis_sha256"], "ord2_role": e["role"], "task_dir": e["task_dir"],
               "trial_dir": e["trial_dir"], "tr_ids": sorted({c["id"] for c in e["copies"]}), "n_copies": len(e["copies"]),
               "first_copy": {k: e["copies"][0][k] for k in ("pool", "run", "step", "id", "file_sha256")},
               "outcome": v["outcome"], "n_steps": len(v["steps"]), "recovery": rec["recovery"],
               "recovery_witness": rec["witness"], "object_events": rec["events"]}
        views[eid] = (v, won)
        if e["vis_sha256"].startswith(CX_VIS):
            assert cx is None and e["role"] == "development" and CX_TR in row["tr_ids"] and v["outcome"] == "success"
            cx = row
        rows.append(row)
    assert cx is not None and cx["recovery"]
    cdir = cx["task_dir"]
    assert cdir not in vx1_dirs["learning"] + vx1_dirs["checking"]
    nat_dirs = sorted({r["task_dir"] for r in rows})
    assert not set(nat_dirs) & set(vx1_dirs["learning"] + vx1_dirs["checking"]), "natural dirs overlap VX-1 records"
    S3 = [r for r in rows if r["task_dir"] != cdir]
    S4 = [r for r in rows if r["task_dir"] == cdir and r["id"] != cx["id"]]
    for r in rows:
        r["partition"] = "CX" if r is cx else ("S3_checking" if r in S3 else "S4_same_task")
        r["exposure"] = ("researcher-exposed: read in ORD-1 / ORD-2 / VX-1 post-hoc; reused training-split actor trace"
                         + ("; AWC-1 checking pool (already exposed in ORD-1)" if r["ord2_role"] == "checking" else ""))
        r["seen_by_repair_learner"] = r is cx
        r["task_disjoint_from_cx"] = r["task_dir"] != cdir
    # ---- write -----------------------------------------------------------------------------------------------
    cxv, cxwon = views[cx["id"]]
    learner_cx = dict(cxv, episode_id="B5-e01")
    jdump(learner_cx, os.path.join(out, "repair_input", "cx_view.json"))
    for r in S3:
        v, won = views[r["id"]]
        jdump(dict(v, episode_id=r["id"]), os.path.join(out, "natural_checking", r["id"] + ".json"))
        jdump({"id": r["id"], "won": won}, os.path.join(out, "eval_only", "natural_checking", r["id"] + ".json"))
    for r in S4 + [cx]:
        v, won = views[r["id"]]
        jdump(dict(v, episode_id=r["id"]), os.path.join(out, "natural_same_task", r["id"] + ".json"))
        jdump({"id": r["id"], "won": won}, os.path.join(out, "eval_only", "natural_same_task", r["id"] + ".json"))
    shutil.copytree(ldir, os.path.join(out, "repair_input", "learning"))
    shutil.copy(os.path.join(VX1, "selection", "VX1_SELECTION.json"), os.path.join(out, "repair_input", "VX1_SELECTION.json"))
    shutil.copy(os.path.join(VX1, "learn", START[0], "FINAL.json"), os.path.join(out, "repair_input", "START_MODEL_EXT2_M4.json"))
    s3ids = sorted(r["id"] for r in S3)

    def counts(rs):
        return {"episodes": len(rs), "states": sum(r["n_steps"] for r in rs), "task_dirs": len({r["task_dir"] for r in rs}),
                "successes": sum(r["outcome"] == "success" for r in rs),
                "failures": sum(r["outcome"] == "failure" for r in rs),
                "recovery_successes": sum(r["recovery"] and r["outcome"] == "success" for r in rs),
                "recovery_failures": sum(r["recovery"] and r["outcome"] == "failure" for r in rs),
                "recovery_success_task_dirs": sorted({r["task_dir"] for r in rs if r["recovery"] and r["outcome"] == "success"}),
                "ord2_role": dict(collections.Counter(r["ord2_role"] for r in rs))}
    sel = {"rule": "RP-1", "schema": "rp.selection.v1",
           "counterexample": {k: cx[k] for k in ("id", "vis_sha256", "tr_ids", "task_dir", "trial_dir", "first_copy",
                                                 "n_steps", "outcome", "recovery_witness", "object_events", "exposure")},
           "cx_learner_view_sha256": sf(os.path.join(out, "repair_input", "cx_view.json")),
           "cx_learner_view_id": "B5-e01",
           "start_model": {"stream": START[0], "sha256": START[1]}, "reference_model": {"stream": REF[0], "sha256": REF[1]},
           "repair_evidence": {"vx1_learning_files": len(files), "vx1_learning_list_sha256": sha(sorted(lsha.items())),
                               "plus": "CX"},
           "vx1_task_dirs": vx1_dirs, "natural_task_dirs": nat_dirs,
           "S3_checking": {"ids": s3ids, "ids_sha256": sha(s3ids), **counts(S3)},
           "S4_same_task": {"ids": sorted(r["id"] for r in S4), **counts(S4)},
           "all_natural": counts(rows), "episodes": rows,
           "source": {"ord2_witnesses_sha256": sf(ORD2)}}
    sel["sha256"] = sha({k: v for k, v in sel.items()})
    jdump(sel, os.path.join(out, "selection", "RP1_SELECTION.json"))
    print(json.dumps({"cx": cx["id"], "cx_dir": cdir, "S3": sel["S3_checking"] | {"ids": len(s3ids)},
                      "S4": sel["S4_same_task"], "sha": sel["sha256"][:16]}, indent=1))


if __name__ == "__main__":
    main()
