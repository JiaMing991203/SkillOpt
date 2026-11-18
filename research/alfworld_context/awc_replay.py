#!/usr/bin/env python3
"""Replay each frozen attempt under both stage readings, with explicit time boundaries. Rule AWC-1.

THE THREE CONDITIONS, and none of them is written by this file:
  CURRENT        `stage_common.Reading` - the frozen shared reading that P, S and C all used in the
                 12-task stage study. INSPECT is discharged only by a lamp use WHILE the object is held
                 (clause a); a two-object task counts any supported placements; a transformation counts
                 if it happened before the placement.
  A4_REV1        `a4_reading.ReadingA4(sp, type_of, rev=1)`, recorded because its docstring claims it
                 "reproduces the reading used for Study A's frozen reports" and FOR
                 `look_at_obj_in_light` IT DOES NOT: `goal_now` gates clause (c) on `self.rev` and
                 applies the INSPECT clause (b) unconditionally. A/B over rev=1 vs rev=2 is therefore
                 vacuous for that task type, which is why CURRENT is the shared reading instead.
  CONTEXT-AWARE  `a4_reading.ReadingA4(sp, type_of, rev=2)` - the existing revision (amendment A4 3.4):
                 (b) an EARLIER lamp-on connects to a later holding under the stated rules
                 `lamp_stays_on` and `same_place`; (c) a two-object task needs both instances in the
                 SAME destination receptacle instance; (d) the task's transformation must be the LATEST
                 temperature transformation of that instance before its placement.

Both read the same prefix - the task sentence plus the executed commands and their observations - through
the same frozen vocabulary. No PDDL, no engine state, no expert plan, no actor reasoning, no later step.

TIME BOUNDARIES, enforced by construction rather than by care. The Reading object only ever receives
steps 0..t-1 before PRE(t) is recorded, so a PRE record cannot depend on step t's observation: the
reader is literally not holding it yet. After PRE(t) is appended, step t is applied and POST(t) is
recorded. The episode's scored outcome is read ONLY after the last step, for the retrospective block.

usage:
  awc_replay.py --manifest <AWC_MANIFEST.json> --role development|checking|all --out <dir>
  awc_replay.py --trace <trace.json> --label <name> --out <dir>      one extra record, labelled
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = os.path.join(HERE, "frozen", "learned_contracts")
for _p in (FROZEN,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import a4_reading as A4            # noqa: E402  the module under study, unmodified
import stage_common as SC          # noqa: E402  the shared reading, unmodified
import vocab as V                  # noqa: E402  the frozen vocabulary

RULE = "AWC-1"
SCHEMA = "awc.replay.v1"
# (label, factory). CURRENT is the shared reading itself, not ReadingA4 at rev 1 - see the header.
CONDITIONS = ("current", "a4_rev1", "context_aware")


def make_reading(label: str, sp, type_of):
    if label == "current":
        return SC.Reading(sp, type_of)
    return A4.ReadingA4(sp, type_of, 1 if label == "a4_rev1" else 2)


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_episode(trace_path: str) -> dict:
    """One saved trace -> {task_description, task_type, steps:[{action, obs}], scored}.

    `scored` is kept in a SEPARATE field that the replay never reads until the retrospective block.
    """
    d = json.load(open(trace_path, encoding="utf-8"))
    steps = [{"action": s.get("executed_action"), "obs": s.get("env_feedback")}
             for s in (d.get("steps") or [])
             if s.get("executed_action") is not None]
    fin = d.get("final") or {}
    return {"task_id": d.get("task_id"), "task_type": d.get("task_type"),
            "task_description": d.get("task_description"),
            "gamefile": d.get("gamefile"), "max_steps": d.get("max_steps"),
            "initial_observation": d.get("initial_observation"),
            "steps": steps,
            "scored": {"won": fin.get("won"), "hard": fin.get("hard"), "n_steps": fin.get("n_steps"),
                       "timed_out": fin.get("timed_out")}}


def parse_visible(ep: dict) -> list:
    """The analyst-visible step list, through the frozen grammar (A4 3.3 keeps MALFORMED commands)."""
    return A4.parse_steps(ep["steps"], V.parse_command, V.parse_obs, V.outcome_of)


def snapshot(R) -> dict:
    """What the reading supports right now. No later step is consulted."""
    g = R.goal_now()
    return {
        "goal_support": g,
        "goal_reached": len(g) >= (1 if R.sp["form"] == "light" else R.sp["count"]),
        "goal_step": R.goal_step,
        "inspect_basis": getattr(R, "inspect_basis", None),
        "held": sorted(R.held),
        "supported_placements": R.supported(),
        "available": [list(x) for x in R.available()],
        "lamp_uses": [{"step": k, "lamp": lamp, "held": h, "arrival": arr} for k, lamp, h, arr in R.uses],
        "lamp_on": [{"step": k, "lamp": lamp, "arrival_recep": arr, "arrival_step": ka,
                     "requested_listed_there": listed, "held_then": held}
                    for (k, lamp, arr, ka, listed, held) in getattr(R, "lamp_on", [])],
        "transforms": {o: [[j, kind, app] for j, kind, app in v] for o, v in R.transforms.items()},
        "placed": {o: {"recep": p["recep"], "step": p["step"], "basis": p["basis"],
                       "confirmed": list(p["confirmed"])} for o, p in R.placed.items()},
        "placement_events": [list(e) for e in R.pevents],
    }


def replay_one(ep: dict, cond: str) -> dict:
    """PRE(t) before step t is applied, POST(t) after. The reader never holds a later step."""
    goal = V.parse_task(ep["task_description"] or "")
    if goal is None:
        return {"problem": f"the task sentence is outside the frozen grammar: {ep['task_description']!r}"}
    sp = SC.spec_of(goal)
    vis = parse_visible(ep)
    R = make_reading(cond, sp, V.type_of)
    timeline = []
    for t, s in enumerate(vis):
        pre = snapshot(R)                     # committed before the observation of step t is read
        R.apply(s)
        post = snapshot(R)
        changed = {k: [pre.get(k), post.get(k)] for k in ("goal_support", "goal_reached", "goal_step",
                                                          "held", "supported_placements")
                   if pre.get(k) != post.get(k)}
        timeline.append({
            "t": t, "action": s["action"], "obs": (s["obs"] or "")[:300],
            "cmd_kind": s["cmd"].get("kind"), "verb": s["cmd"].get("verb"),
            "outcome": s["outcome"],
            "atoms": [a["kind"] for a in s["atoms"]],
            "pre": pre, "post": post, "changed_by_this_step": changed,
        })
    return {"spec": sp, "goal": goal, "n_steps": len(vis),
            "first_goal_step": R.goal_step, "goal_objs": list(R.goal_objs),
            "inspect_basis": getattr(R, "inspect_basis", None),
            "final": snapshot(R), "timeline": timeline,
            "malformed_steps": A4.malformed_steps(vis),
            "condition": cond,
            "assumptions": {"shared": sorted(SC.ASSUMPTIONS),
                            "extended_not_used_by_primary_findings": sorted(SC.EXTENDED_ASSUMPTIONS),
                            "inspect_revision": (sorted(A4.INSPECT_ASSUMPTIONS)
                                                 if cond != "current" else [])}}


def retrospective(ep: dict, runs: dict) -> dict:
    """Read AFTER the last step only. The outcome never establishes an earlier fact."""
    hard = int((ep["scored"].get("hard") or 0))
    out = {"scored_hard": hard, "won": ep["scored"].get("won"),
           "timed_out": ep["scored"].get("timed_out"), "by_rev": {}}
    for cond, r in runs.items():
        gs = r.get("first_goal_step")
        reached = bool(r.get("final", {}).get("goal_reached"))
        out["by_rev"][cond] = {
            "first_goal_step": gs, "goal_reached_at_end": reached,
            "agrees_with_score": (reached == bool(hard)),
            "verdict": ("recognised" if reached and hard else
                        "missed_completion" if hard and not reached else
                        "claimed_completion_on_a_failure" if reached and not hard else
                        "no_completion_claimed"),
            "inspect_basis": r.get("inspect_basis"),
        }
    return out


def ordinary_analysis(trace_path: str, item: str) -> dict:
    """The saved ordinary SkillOpt analysis for the same attempt, where the run kept one."""
    rollout = os.path.dirname(os.path.dirname(trace_path))         # .../rollout
    step_dir = os.path.dirname(rollout)                            # .../step_000N
    td = os.path.join(step_dir, "trajectory_digest.json")
    if not os.path.isfile(td):
        return {"found": False, "path": td}
    try:
        d = json.load(open(td, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"found": False, "path": td, "problem": "unreadable"}
    pats = [p.get("pattern") for p in (d.get("failure_patterns") or [])
            if item in (p.get("task_ids") or [])]
    return {"found": True, "path": td, "patterns_naming_this_attempt": pats,
            "n_patterns_in_the_digest": len(d.get("failure_patterns") or [])}


def run(manifest_path: str, role: str, out_dir: str) -> dict:
    man = json.load(open(manifest_path, encoding="utf-8"))
    records = []
    for task in man["tasks"]:
        if role != "all" and task["role"] != role:
            continue
        for att in task["attempts"]:
            ep = load_episode(att["trace"])
            digest = hashlib.sha256(open(att["trace"], "rb").read()).hexdigest()
            if digest != att["trace_sha256"]:
                raise SystemExit(f"{att['trace']} changed since the manifest was frozen")
            runs = {c: replay_one(ep, c) for c in CONDITIONS}
            rec = {
                "role": task["role"], "task_type": task["task_type"],
                "type_label": task["type_label"], "id": task["id"], "task_dir": task["task_dir"],
                "attempt": att["attempt"], "run": att["run"], "step": att["step"],
                "trace": att["trace"], "trace_sha256": digest,
                "task_description": ep["task_description"],
                "n_visible_steps": runs["current"].get("n_steps"),
                "in_sample_for_the_revision": task["id"] in REVISION_EVIDENCE,
                "by_rev": dict(runs),
                "retrospective": retrospective(ep, runs),
                "ordinary_analysis": ordinary_analysis(att["trace"], task["id"]),
            }
            records.append(rec)
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "role": role,
           "manifest": os.path.abspath(manifest_path),
           "manifest_sha256": man.get("manifest_sha256"),
           "vocab_version": V.VOCAB_VERSION, "vocab_digest": V.vocab_digest(),
           "n_records": len(records), "records": records,
           "time_boundary_note": ("PRE(t) is computed from steps 0..t-1 only, because the reader has "
                                  "not been given step t; the scored outcome is read only in the "
                                  "retrospective block"),
           "in_sample_note": ("the four task ids the A4 3.4 revision was built from are in-sample for "
                              "it; any result on them is retrospective, not a fresh test")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"AWC_REPLAY_{role}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# The counterexamples amendment A4 3.4 / rev 2 was built from, named in the amendment itself.
REVISION_EVIDENCE = {"tr:0293", "tr:3287", "tr:2785", "tr:2658", "tr:0281"}


def one_trace(trace: str, label: str, out_dir: str) -> dict:
    """One extra record, LABELLED. Used for the named lamp attempt, which the mechanical attempt rule
    did not select: rule 6 forces the lamp TASK into the development slot, and rules 5's
    earliest-success/earliest-failure ordering then picked two other attempts of it."""
    ep = load_episode(trace)
    runs = {c: replay_one(ep, c) for c in CONDITIONS}
    rec = {"label": label, "trace": os.path.abspath(trace),
           "trace_sha256": hashlib.sha256(open(trace, "rb").read()).hexdigest(),
           "task_id": ep["task_id"], "task_type": ep["task_type"],
           "task_description": ep["task_description"],
           "n_visible_steps": runs["current"].get("n_steps"),
           "in_sample_for_the_revision": ep["task_id"] in REVISION_EVIDENCE,
           "by_rev": dict(runs), "retrospective": retrospective(ep, runs),
           "ordinary_analysis": ordinary_analysis(trace, ep["task_id"] or "")}
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "kind": "single_labelled_record",
           "label": label, "vocab_digest": V.vocab_digest(), "record": rec,
           "caveat": ("this attempt is NOT part of the frozen mechanical selection; it is the "
                      "counterexample amendment A4 3.4 was built from, so it is IN-SAMPLE for the "
                      "revision and this is a retrospective regression check, not a fresh test")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"AWC_CASE_{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def scan_type(runs_dir: str, task_type: str, out_dir: str) -> dict:
    """SUPPLEMENTARY, and labelled so: every saved training attempt of one type, to measure how often
    the context connection fires at all and whether it ever fires on a scored failure. Not the frozen
    diagnostic set, and not a performance evaluation."""
    import glob as _g
    recs, changed = [], []
    seen = set()
    for res in sorted(_g.glob(os.path.join(runs_dir, "**", "skillopt", "steps", "step_*", "rollout",
                                           "results.jsonl"), recursive=True)):
        run = os.path.basename(res.split(os.sep + "skillopt" + os.sep)[0])
        if "_evolve_" not in run:
            continue
        step = res.split(os.sep + "steps" + os.sep)[1].split(os.sep)[0]
        for line in open(res, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = str(d.get("id") or "")
            if not iid.startswith("tr:") or d.get("task_type") != task_type:
                continue
            trace = os.path.join(os.path.dirname(res), "predictions", iid, "trace.json")
            if not os.path.isfile(trace) or trace in seen:
                continue
            seen.add(trace)
            ep = load_episode(trace)
            r = {c: replay_one(ep, c) for c in CONDITIONS}
            if any("problem" in r[c] for c in CONDITIONS):
                recs.append({"id": iid, "run": run, "step": step,
                             "problem": r["current"].get("problem")})
                continue
            row = {"id": iid, "run": run, "step": step, "trace": trace,
                   "hard": int(d.get("hard") or 0), "n_steps": r["current"]["n_steps"],
                   "goal_step": {c: r[c]["first_goal_step"] for c in CONDITIONS},
                   "reached": {c: bool(r[c]["final"]["goal_reached"]) for c in CONDITIONS},
                   "inspect_basis": {c: r[c]["inspect_basis"] for c in CONDITIONS},
                   "n_lamp_on": len(r["context_aware"]["final"]["lamp_on"]),
                   "n_lamp_uses_while_holding": sum(
                       1 for u in r["context_aware"]["final"]["lamp_uses"] if u["held"])}
            recs.append(row)
            if row["goal_step"]["current"] != row["goal_step"]["context_aware"]:
                changed.append(row)
    agg = {"n_attempts": len([x for x in recs if "problem" not in x]),
           "n_problems": len([x for x in recs if "problem" in x]),
           "n_changed": len(changed),
           "changed_on_a_scored_success": sum(1 for x in changed if x["hard"] == 1),
           "changed_on_a_scored_failure": sum(1 for x in changed if x["hard"] == 0),
           "basis_b_used": sum(1 for x in recs
                               if x.get("inspect_basis", {}).get("context_aware") == "b"),
           "basis_a_used": sum(1 for x in recs
                               if x.get("inspect_basis", {}).get("context_aware") == "a")}
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "kind": "supplementary_scan",
           "task_type": task_type, "runs_dir": os.path.abspath(runs_dir),
           "vocab_digest": V.vocab_digest(), "aggregate": agg,
           "changed": changed, "attempts": recs,
           "caveat": ("SUPPLEMENTARY. Every saved TRAINING attempt of one task type, to measure how "
                      "often the context connection fires and whether it ever fires on a scored "
                      "failure. It is not the frozen diagnostic set and not a performance evaluation.")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"AWC_SCAN_{task_type}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest")
    ap.add_argument("--role", default="all", choices=["development", "checking", "all"])
    ap.add_argument("--trace")
    ap.add_argument("--label")
    ap.add_argument("--scan-type", dest="scan_type")
    ap.add_argument("--runs")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.trace:
        if not a.label:
            raise SystemExit("--trace needs --label")
        doc = one_trace(a.trace, a.label, a.out)
        r = doc["record"]
        print(f"rule {RULE}  case {a.label}: {r['task_id']} {r['task_type']} "
              f"{r['n_visible_steps']} steps, scored hard={r['retrospective']['scored_hard']}")
        for c in CONDITIONS:
            v = r["retrospective"]["by_rev"][c]
            print(f"    {c:14s} goal@{str(v['first_goal_step']):5s} basis={str(v['inspect_basis']):5s} "
                  f"{v['verdict']}")
        print(f"wrote {doc['written_to']}")
        return 0
    if a.scan_type:
        if not a.runs:
            raise SystemExit("--scan-type needs --runs")
        doc = scan_type(a.runs, a.scan_type, a.out)
        g = doc["aggregate"]
        print(f"rule {RULE}  supplementary scan of {a.scan_type}: {g['n_attempts']} training attempts, "
              f"{g['n_problems']} outside the grammar")
        print(f"    changed by the context connection: {g['n_changed']} "
              f"({g['changed_on_a_scored_success']} on a scored success, "
              f"{g['changed_on_a_scored_failure']} on a scored FAILURE)")
        print(f"    INSPECT basis in force: clause (a) {g['basis_a_used']}, clause (b) {g['basis_b_used']}")
        for x in doc["changed"]:
            print(f"      {x['id']} a hard={x['hard']} steps={x['n_steps']:3d} "
                  f"current@{x['goal_step']['current']} -> context@{x['goal_step']['context_aware']} "
                  f"({x['run'][:44]})")
        print(f"wrote {doc['written_to']}")
        return 0
    if not a.manifest:
        raise SystemExit("one of --manifest, --trace or --scan-type is required")
    doc = run(a.manifest, a.role, a.out)
    print(f"rule {RULE}  role={a.role}  {doc['n_records']} attempt record(s); "
          f"vocab {doc['vocab_digest'][:16]}")
    for r in doc["records"]:
        c = r["retrospective"]["by_rev"]["current"]
        x = r["retrospective"]["by_rev"]["context_aware"]
        flag = "  <-- CHANGED" if (c["first_goal_step"] != x["first_goal_step"]
                                   or c["goal_reached_at_end"] != x["goal_reached_at_end"]) else ""
        print(f"    {r['role'][:4]} {r['type_label']:17s} {r['id']:8s} a{r['attempt']} "
              f"hard={r['retrospective']['scored_hard']} steps={r['n_visible_steps']:3d}  "
              f"current: goal@{c['first_goal_step']} {c['verdict']:34s} "
              f"context: goal@{x['first_goal_step']} {x['verdict']}{flag}")
    print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
