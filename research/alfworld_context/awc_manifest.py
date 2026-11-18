#!/usr/bin/env python3
"""Freeze the diagnostic set for the ALFWorld context-checking study. Rule AWC-1.

SAVED TRAINING TRACES ONLY. The pool is every `tr:` (train-split) attempt with a saved `trace.json`
under a TRAINING rollout - `runs/*_evolve_*/skillopt/steps/step_*/rollout/` - and nothing else. No
`*_evaluate_*` run is opened, so no selection or held-out trajectory is read, and the 12 held-out tasks
of the frozen info-study selection stay untouched. Zero API calls, zero GPUs, zero gameplay.

THE RULE, recorded in docs/research/ALFWORLD_CONTEXT_STUDY_PLAN.md section 2 BEFORE this ran:

  1. candidate = a `tr:` task with at least one saved trace in a training rollout;
  2. outcome-balanced = the pool holds both a scored success (hard == 1) and a scored failure of it;
  3. within each type, order by (not outcome_balanced, id) and take the first two;
  4. of the two, the LOWER id is development and the other is checking; their task directories must
     differ, and the rule asserts it;
  5. at most two attempts per task: the earliest-ordered success and the earliest-ordered failure, or
     the two earliest of one outcome when only one exists; order is (run, step, id);
  6. ONE recorded exception: for `look_at_obj_in_light` the development slot is forced to tr:0293, the
     lamp counterexample the instruction names, so it is a development/regression case.

Outcome-balanced selection supports DIAGNOSIS. It does not estimate population performance, and the
manifest says so in its own text.

usage:
  awc_manifest.py inventory --runs <dir> [--out <dir>]
  awc_manifest.py freeze    --runs <dir> --out <dir>
  awc_manifest.py verify    [--snapshot <FROZEN_SNAPSHOT.json>]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys

RULE = "AWC-1"
SCHEMA = "awc.manifest.v1"
HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "frozen", "FROZEN_SNAPSHOT.json")

TYPES = ["pick_and_place", "pick_two_obj_and_place", "pick_clean_then_place_in_recep",
         "pick_heat_then_place_in_recep", "pick_cool_then_place_in_recep", "look_at_obj_in_light"]
TYPE_LABEL = {
    "pick_and_place": "Pick & Place",
    "pick_two_obj_and_place": "Pick Two & Place",
    "pick_clean_then_place_in_recep": "Clean & Place",
    "pick_heat_then_place_in_recep": "Heat & Place",
    "pick_cool_then_place_in_recep": "Cool & Place",
    "look_at_obj_in_light": "Examine in Light",
}
FORCED_DEV = {"look_at_obj_in_light": "tr:0293"}      # rule 6, recorded before the freeze


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def verify_snapshot(path: str = SNAPSHOT) -> dict:
    """The frozen snapshot of another track's modules must still match its source, where reachable."""
    doc = json.load(open(path, encoding="utf-8"))
    out = []
    for f in doc["files"]:
        local = os.path.join(os.path.dirname(path), f["path"])
        lraw = open(local, "rb").read()
        row = {"path": f["path"], "recorded": f["sha256"][:16],
               "local": hashlib.sha256(lraw).hexdigest()[:16],
               "source_reachable": os.path.isfile(f["source"]), "source": None, "drifted": False}
        if row["source_reachable"]:
            sraw = open(f["source"], "rb").read()
            row["source"] = hashlib.sha256(sraw).hexdigest()[:16]
            row["drifted"] = row["source"] != row["recorded"]
        row["snapshot_intact"] = row["local"] == row["recorded"]
        out.append(row)
    return {"rule": RULE, "utc": _utc(), "snapshot": os.path.abspath(path), "files": out,
            "ok": all(r["snapshot_intact"] and not r["drifted"] for r in out),
            "note": ("drifted = the live module in the sibling worktree has changed since the snapshot; "
                     "the study's results describe the snapshot and must be re-run if that happens")}


# ───────────────────────── the pool ─────────────────────────

def inventory(runs_dir: str) -> dict:
    rows = []
    for res in sorted(glob.glob(os.path.join(
            runs_dir, "**", "skillopt", "steps", "step_*", "rollout", "results.jsonl"), recursive=True)):
        run = os.path.basename(res.split(os.sep + "skillopt" + os.sep)[0])
        if "_evolve_" not in run:
            continue                     # TRAINING rollouts only
        step = res.split(os.sep + "steps" + os.sep)[1].split(os.sep)[0]
        pred_root = os.path.join(os.path.dirname(res), "predictions")
        for line in open(res, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = str(d.get("id") or "")
            if not iid.startswith("tr:"):
                continue                 # train split only; no seen:/unseen: id is ever read
            trace = os.path.join(pred_root, iid, "trace.json")
            if not os.path.isfile(trace):
                continue
            gf = str(d.get("gamefile") or "")
            rows.append({
                "run": run, "step": step, "id": iid,
                "task_type": d.get("task_type"),
                "task_dir": gf.split("/")[-3] if gf.count("/") >= 3 else None,
                "gamefile": gf, "trace": trace,
                "hard": d.get("hard"), "soft": d.get("soft"), "agent_ok": d.get("agent_ok"),
                "n_turns": d.get("n_turns"), "fail_reason": d.get("fail_reason"),
                "task_description": d.get("task_description"),
                "order": (run, step, iid),
            })
    by_task: dict = {}
    for r in rows:
        by_task.setdefault((r["task_type"], r["id"]), []).append(r)
    for v in by_task.values():
        v.sort(key=lambda r: r["order"])
    types: dict = {}
    for (t, i), v in by_task.items():
        hs = {int(x["hard"] or 0) for x in v}
        types.setdefault(t, []).append({"id": i, "task_dir": v[0]["task_dir"], "attempts": len(v),
                                        "outcomes": sorted(hs),
                                        "outcome_balanced": {0, 1} <= hs})
    for v in types.values():
        v.sort(key=lambda x: x["id"])
    return {"rule": RULE, "utc": _utc(), "runs_dir": os.path.abspath(runs_dir),
            "n_attempt_rows": len(rows), "n_tasks": len(by_task),
            "types": {t: {"n_tasks": len(v), "n_outcome_balanced": sum(1 for x in v if x["outcome_balanced"]),
                          "tasks": v} for t, v in sorted(types.items())},
            "_rows": rows, "_by_task": {f"{t}|{i}": v for (t, i), v in by_task.items()}}


def pick_attempts(att: list) -> list:
    """Rule 5: the earliest success and the earliest failure, else the two earliest of one outcome."""
    succ = [a for a in att if int(a["hard"] or 0) == 1]
    fail = [a for a in att if int(a["hard"] or 0) == 0]
    if succ and fail:
        return [succ[0], fail[0]]
    return (succ or fail)[:2]


def freeze(runs_dir: str, out_dir: str) -> dict:
    snap = verify_snapshot()
    if not snap["ok"]:
        raise SystemExit(f"the frozen module snapshot is not intact: {json.dumps(snap['files'])}")
    inv = inventory(runs_dir)
    chosen, problems = [], []
    for t in TYPES:
        cands = (inv["types"].get(t) or {}).get("tasks") or []
        if not cands:
            problems.append(f"no saved training task of type {t}: category UNAVAILABLE, not manufactured")
            continue
        ordered = sorted(cands, key=lambda x: (not x["outcome_balanced"], x["id"]))
        forced = FORCED_DEV.get(t)
        if forced:
            f = next((c for c in cands if c["id"] == forced), None)
            if f is None:
                problems.append(f"rule 6: forced development task {forced} has no saved training "
                                f"attempt; falling back to the mechanical order")
            else:
                ordered = [f] + [c for c in ordered if c["id"] != forced]
        two = ordered[:2]
        if len(two) < 2:
            problems.append(f"type {t} has only {len(two)} candidate task(s); the pair is incomplete")
        # rule 4: the LOWER id develops - except where rule 6 forces the development slot
        if forced and two and two[0]["id"] == forced:
            dev, chk = two[0], (two[1] if len(two) > 1 else None)
        else:
            two = sorted(two, key=lambda x: x["id"])
            dev, chk = two[0], (two[1] if len(two) > 1 else None)
        if chk is not None and dev["task_dir"] == chk["task_dir"]:
            problems.append(f"type {t}: development and checking share the task directory "
                            f"{dev['task_dir']}; rule 4 requires different directories")
        for role, c in (("development", dev), ("checking", chk)):
            if c is None:
                continue
            att = pick_attempts(inv["_by_task"][f"{t}|{c['id']}"])
            chosen.append({
                "role": role, "task_type": t, "type_label": TYPE_LABEL[t], "id": c["id"],
                "task_dir": c["task_dir"], "outcome_balanced": c["outcome_balanced"],
                "attempts_available": c["attempts"],
                "selected_by": ("rule 6 (the named lamp regression case)" if role == "development"
                                and FORCED_DEV.get(t) == c["id"] else "rules 3-4"),
                "attempts": [{
                    "attempt": j, "run": a["run"], "step": a["step"], "trace": a["trace"],
                    "trace_sha256": hashlib.sha256(open(a["trace"], "rb").read()).hexdigest(),
                    "hard": a["hard"], "soft": a["soft"], "agent_ok": a["agent_ok"],
                    "n_turns": a["n_turns"], "fail_reason": a["fail_reason"],
                    "task_description": a["task_description"],
                } for j, a in enumerate(att, start=1)],
            })
    doc = {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(),
        "runs_dir": inv["runs_dir"],
        "selection_rule": __doc__.split("THE RULE", 1)[1].split("Outcome-balanced", 1)[0].strip(),
        "pool": {"n_attempt_rows": inv["n_attempt_rows"], "n_tasks": inv["n_tasks"],
                 "by_type": {t: {"n_tasks": v["n_tasks"],
                                 "n_outcome_balanced": v["n_outcome_balanced"]}
                             for t, v in inv["types"].items()}},
        "tasks": chosen,
        "counts": {"tasks": len(chosen),
                   "development": sum(1 for c in chosen if c["role"] == "development"),
                   "checking": sum(1 for c in chosen if c["role"] == "checking"),
                   "attempts": sum(len(c["attempts"]) for c in chosen),
                   "types_covered": sorted({c["task_type"] for c in chosen})},
        "problems": problems,
        "frozen_modules": snap["files"],
        "material": ("ALL OF IT IS REUSED RESEARCH MATERIAL: saved training rollouts of earlier "
                     "campaigns. The checking half is reserved for this patch's assessment, NOT an "
                     "untouched benchmark test. The selection is outcome-balanced on purpose, which "
                     "supports diagnosis and does NOT estimate population performance."),
        "boundary": ("train-split (`tr:`) ids from `*_evolve_*` rollouts only; no `*_evaluate_*` run, "
                     "no `seen:`/`unseen:` id, and no held-out task directory is opened"),
    }
    doc["manifest_sha256"] = sha_text(json.dumps(
        {k: v for k, v in doc.items() if k != "utc"}, sort_keys=True, default=str))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "AWC_MANIFEST.json")
    if os.path.exists(path):
        old = json.load(open(path, encoding="utf-8"))
        if old.get("manifest_sha256") == doc["manifest_sha256"]:
            return old
        raise SystemExit(f"{path} is already frozen with a different manifest "
                         f"({old.get('manifest_sha256', '')[:16]} vs {doc['manifest_sha256'][:16]}); "
                         f"refusing to overwrite it")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    os.chmod(path, 0o444)
    doc["written_to"] = path
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inventory")
    i.add_argument("--runs", required=True)
    i.add_argument("--out")
    f = sub.add_parser("freeze")
    f.add_argument("--runs", required=True)
    f.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--snapshot", default=SNAPSHOT)
    a = ap.parse_args(argv)

    if a.cmd == "verify":
        d = verify_snapshot(a.snapshot)
        for r in d["files"]:
            print(f"  {r['path']:40s} recorded {r['recorded']} local {r['local']} "
                  f"source {r['source'] or '(unreachable)'} drifted={r['drifted']}")
        print(f"rule {RULE}  snapshot ok: {d['ok']}")
        return 0 if d["ok"] else 1
    if a.cmd == "inventory":
        d = inventory(a.runs)
        print(f"rule {RULE}  {d['n_attempt_rows']} training attempt rows, {d['n_tasks']} tasks")
        for t, v in sorted(d["types"].items()):
            print(f"    {t:34s} tasks {v['n_tasks']:3d}  outcome-balanced {v['n_outcome_balanced']:3d}")
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            p = os.path.join(a.out, "AWC_POOL.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({k: v for k, v in d.items() if not k.startswith("_")}, fh,
                          indent=1, sort_keys=True, default=str)
            print(f"wrote {p}")
        return 0
    d = freeze(a.runs, a.out)
    print(f"rule {RULE}  manifest {d['manifest_sha256'][:16]}  "
          f"{d['counts']['tasks']} tasks / {d['counts']['attempts']} attempts, "
          f"{d['counts']['development']} development + {d['counts']['checking']} checking")
    for c in d["tasks"]:
        outs = ",".join(str(x["hard"]) for x in c["attempts"])
        print(f"    {c['role']:11s} {c['type_label']:17s} {c['id']:8s} {c['task_dir'][:46]:46s} "
              f"attempts {len(c['attempts'])} hard[{outs}] balanced={c['outcome_balanced']}")
    for p in d["problems"]:
        print(f"    PROBLEM: {p}")
    print(f"wrote {d.get('written_to', '(already frozen)')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
