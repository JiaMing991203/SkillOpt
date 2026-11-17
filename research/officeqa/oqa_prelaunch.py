"""The whole offline pre-launch check for the three-arm OfficeQA comparison, in one reproducible run.

Offline: no actor call, no paid call, no GPU, no network. It drives the pieces that already exist rather than
re-implementing any of them, and writes one PRELAUNCH.json with a single verdict.

WHAT IT DOES, in order:

  1. freeze the three question sets (rule OQA-SEL-3) and check they are disjoint and that the previous sets are
     subsets of them, so every saved development trace stays a trace of a current item;
  2. seed M0, the one contract model both contract arms start from, on the SHARED epoch-1 rollout;
  3. run two saved epochs through `scripts/train.py:main()` for EACH arm, one phase per epoch, which is the shape
     the campaign itself must use because the frozen model is named by an environment variable;
  4. evolve each contract arm's model from its OWN epoch-2 traces and record what justified each repair or retain;
  5. run one more single-epoch pass under the EVOLVED model, to show that a changed model reaches the optimizer -
     a different digest in the report records and different contracts named in the report text;
  6. check that arm A's analyst input carries no report, that arm B's carries the Action-Only report with no stage
     material, that arm C's carries both halves, and that arm C's report contains arm B's line for line.

usage:
  python3 research/officeqa/oqa_prelaunch.py --shared <epoch1 rollout> --epoch2 <rollout> --val <rollout> \
      --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

ARMS = ("A", "B", "C")
CONTRACT_ARMS = ("B", "C")


def run(cmd: list, env: dict | None = None, label: str = "") -> dict:
    """One child process, with its output captured. A non-zero exit is reported, never swallowed."""
    e = dict(os.environ)
    e["PYTHONPATH"] = REPO + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    if env:
        e.update(env)
    p = subprocess.run(cmd, cwd=REPO, env=e, capture_output=True, text=True)
    print(f"  [{label or cmd[1]}] exit={p.returncode}")
    return {"label": label, "cmd": cmd, "returncode": p.returncode,
            "stdout_tail": p.stdout[-4000:], "stderr_tail": p.stderr[-4000:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shared", required=True, help="the shared epoch-1 rollout (results.jsonl + predictions/)")
    ap.add_argument("--epoch2", required=True, help="a later epoch's rollout, used as each arm's own epoch 2")
    ap.add_argument("--val", required=True, help="a recorded validation rollout, used to seed every gate")
    ap.add_argument("--out", required=True, help="output directory; must not exist")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if os.path.exists(args.out):
        print(f"refusing to write into an existing directory: {args.out}", file=sys.stderr)
        return 2
    out = os.path.abspath(args.out)
    os.makedirs(out)
    PY = args.python
    steps: list = []
    record: dict = {"generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "repo": REPO, "out": out,
                    "collections": {"shared_epoch1": os.path.abspath(args.shared),
                                    "epoch2": os.path.abspath(args.epoch2),
                                    "validation": os.path.abspath(args.val)}}

    # 1. the three question sets
    sel = os.path.join(out, "OFFICEQA_FROZEN_SETS_V3.json")
    steps.append(run([PY, "-I", os.path.join(HERE, "oqa_sel3.py"), "--out", sel], label="sel3"))
    with open(sel, encoding="utf-8") as f:
        sets = json.load(f)
    record["splits"] = {"rule": sets["rule"], "digest": sets["digest"],
                        "counts": {k: len(v["ids"]) for k, v in sets["sets"].items()},
                        "per_stratum": {k: v["per_stratum"] for k, v in sets["sets"].items()},
                        "disjoint": sets["disjoint"],
                        "previous_sets_are_subsets": {k: v["is_superset"] for k, v in
                                                      sets["previous_sets_are_subsets"].items()},
                        "availability_untested": {k: v["n_untested"] for k, v in sets["sets"].items()},
                        "problems": sets["problems"]}
    shutil.copyfile(sel, os.path.join(out, "splits_manifest.json"))

    # 2. M0
    models = os.path.join(out, "models")
    os.makedirs(models, exist_ok=True)
    steps.append(run([PY, "-I", os.path.join(HERE, "oqa_lc_arm.py"), "seed",
                      "--pred", os.path.join(args.shared, "predictions"),
                      "--order", os.path.join(args.shared, "results.jsonl"),
                      "--out", os.path.join(models, "seed")], label="seed M0"))
    with open(os.path.join(models, "seed", "SEED.json"), encoding="utf-8") as f:
        seed_rec = json.load(f)
    m0 = os.path.join(models, "seed", "model_M0.json")
    record["M0"] = {"path": m0, "model": seed_rec["model"], "items_admitted": seed_rec["items_admitted"],
                    "tool_steps_admitted": seed_rec["tool_steps_admitted"],
                    "assumption_decisions": [{"name": d.get("name"), "decision": d.get("decision"),
                                              "why": (d.get("why") or [""])[0][:300]}
                                             for e in seed_rec["events"] if e["phase"] == "seed_assumptions"
                                             for d in e["decisions"]],
                    "contracts_adopted": [{"id": r["id"], "rendered": r["rendered"], "why": r["why"]}
                                          for r in seed_rec["retained"]],
                    "in_sample_warning": seed_rec["in_sample_warning"]}

    # 3. two saved epochs through the real entry point, one phase per epoch
    record["arms"] = {}
    for arm in ARMS:
        armdir = os.path.join(out, f"arm_{arm}")
        lc = [] if arm == "A" else ["--lc-model", m0]
        for phase, epochs in (("e1", 1), ("e2", 2)):
            cmd = [PY, os.path.join(HERE, "oqa_dryrun.py"), "--arm", arm, "--out", armdir,
                   "--epoch1", args.shared, "--epoch2", args.epoch2, "--val", args.val,
                   "--epochs", str(epochs), "--phase", phase] + lc + (["--resume"] if phase == "e2" else [])
            steps.append(run(cmd, label=f"arm {arm} {phase}"))
        with open(os.path.join(armdir, "_dryrun", "DRYRUN_e2.json"), encoding="utf-8") as f:
            dry = json.load(f)
        record["arms"][arm] = {
            "out_root": armdir, "status": dry["status"], "error": dry["error"],
            "report_condition": dry["report_condition"],
            "optimizer_calls_epoch2_phase": dry["optimizer_calls"],
            "analyst_input_check": dry["analyst_input_check"],
            "report_provenance_check": dry["report_provenance_check"],
            "model_in_force": dry["lc_model"],
        }

    # 4. evolve each contract arm from its own epoch-2 traces
    for arm in CONTRACT_ARMS:
        armdir = os.path.join(out, f"arm_{arm}")
        mdir = os.path.join(models, f"arm{arm}")
        os.makedirs(mdir, exist_ok=True)
        hist = os.path.join(mdir, "history.jsonl")
        with open(hist, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pred": os.path.join(os.path.abspath(args.shared), "predictions"),
                                "order": os.path.join(os.path.abspath(args.shared), "results.jsonl"),
                                "epoch": 1, "arm": "shared"}) + "\n")
        steps.append(run([PY, "-I", os.path.join(HERE, "oqa_lc_arm.py"), "evolve", "--prev", m0,
                          "--pred", os.path.join(armdir, "steps", "step_0002", "rollout", "predictions"),
                          "--order", os.path.join(armdir, "steps", "step_0002", "rollout", "results.jsonl"),
                          "--epoch", "2", "--arm", arm, "--out", mdir, "--history", hist],
                         label=f"evolve arm {arm}"))
        with open(os.path.join(mdir, f"EVOLVE_arm{arm}_epoch02.json"), encoding="utf-8") as f:
            ev = json.load(f)
        record["arms"][arm]["model_evolution"] = {
            "prior_model": ev["prior_model"], "new_model": ev["model"],
            "model_changed": ev["model_changed"], "digest_differs_in_fields": ev["digest_differs_in_fields"],
            "evidence_carried_forward": {"items": len(ev["evidence_carried_forward"]["items"]),
                                         "tool_steps": ev["evidence_carried_forward"]["tool_steps"]},
            "new_evidence": {"items": len(ev["new_evidence"]["items"]),
                             "tool_steps": ev["new_evidence"]["tool_steps"]},
            "out_of_sample_before_learning": ev["out_of_sample_score_before_learning"],
            "counterexamples": ev["counterexamples_found"],
            "decisions": [{"phase": e["phase"],
                           "ops": [{"op": d.get("op"), "target": d.get("target") or d.get("name") or d.get("id"),
                                    "decision": d.get("decision"), "why": (d.get("why") or [""])[0][:300]}
                                   for d in e["decisions"]]}
                          for e in ev["events"]],
            "retained": [{"id": r["id"], "decision": r["decision"], "why": r["why"]} for r in ev["retained"]],
            "retained_in_error": [r["id"] for r in ev["retained"] if r["decision"] == "retained_in_error"],
        }

        # 5. one more pass under the EVOLVED model: the digest and the named contracts must change
        newm = ev["frozen"]["path"]
        e3 = os.path.join(out, f"arm_{arm}_evolved")
        steps.append(run([PY, os.path.join(HERE, "oqa_dryrun.py"), "--arm", arm, "--out", e3,
                          "--epoch1", args.epoch2, "--epoch2", args.epoch2, "--val", args.val,
                          "--epochs", "1", "--phase", "evolved", "--lc-model", newm],
                         label=f"arm {arm} under the evolved model"))
        with open(os.path.join(e3, "_dryrun", "DRYRUN_evolved.json"), encoding="utf-8") as f:
            d3 = json.load(f)
        before = set(record["arms"][arm]["report_provenance_check"].get("model_digests_seen") or [])
        after = set(d3["report_provenance_check"].get("model_digests_seen") or [])
        record["arms"][arm]["evolved_model_reaches_the_optimizer"] = {
            "out_root": e3, "status": d3["status"],
            "model_digests_before": sorted(before), "model_digests_after": sorted(after),
            "digest_changed": bool(after) and after != before,
            "analyst_input_check": d3["analyst_input_check"],
            "report_provenance_check": {k: d3["report_provenance_check"].get(k)
                                        for k in ("verdict", "n_reports", "problems")},
        }

    # 6. arm C's report must contain arm B's, line for line, on every item of both collections
    import oqa_reports as RP
    REPORT = RP._lc_modules()[3]
    containment = {"checked": 0, "action_only_pass": 0, "containment_pass": 0, "problems": []}
    for coll in (args.shared, args.epoch2):
        res = os.path.join(coll, "results.jsonl")
        pred = os.path.join(coll, "predictions")
        rows = [json.loads(l) for l in open(res, encoding="utf-8") if l.strip()]
        for r in rows:
            tid = str(r["id"])
            try:
                a, _ = RP.build_one_lc(pred, tid, r.get("question", ""), res, m0, condition=RP.LC_ACTION)
                c, _ = RP.build_one_lc(pred, tid, r.get("question", ""), res, m0, condition=RP.LC_STAGE)
            except Exception as exc:  # noqa: BLE001
                containment["problems"].append(f"{tid}: {type(exc).__name__}: {exc}")
                continue
            containment["checked"] += 1
            aa = REPORT.audit_action_only(a)
            cc = REPORT.audit_containment(a, c)
            containment["action_only_pass"] += aa["verdict"] == "PASS"
            containment["containment_pass"] += cc["verdict"] == "PASS"
            if aa["verdict"] != "PASS":
                containment["problems"].append(f"{tid}: action-only carries {aa['stage_words_found']}")
            if cc["verdict"] != "PASS":
                containment["problems"].append(f"{tid}: arm C omits {cc['n_missing']} of arm B's lines")
    containment["verdict"] = "FAIL" if containment["problems"] else "PASS"
    record["report_separation_check"] = containment

    # ── the single verdict ───────────────────────────────────────────────────────
    problems: list = []
    if record["splits"]["problems"]:
        problems += [f"splits: {p}" for p in record["splits"]["problems"]]
    for arm, a in record["arms"].items():
        if a["status"] != "COMPLETED":
            problems.append(f"arm {arm}: the training entry point did not complete ({a['error']})")
        if a["analyst_input_check"]["verdict"] != "PASS":
            problems += [f"arm {arm} analyst input: {p}" for p in a["analyst_input_check"]["problems"]]
        if a["report_provenance_check"]["verdict"] not in ("PASS", "SKIPPED"):
            problems += [f"arm {arm} provenance: {p}" for p in a["report_provenance_check"].get("problems", [])]
        ev = a.get("model_evolution")
        if ev and ev["retained_in_error"]:
            problems.append(f"arm {arm}: contracts retained with a counterexample: {ev['retained_in_error']}")
        e3 = a.get("evolved_model_reaches_the_optimizer")
        if e3 and not e3["digest_changed"]:
            problems.append(f"arm {arm}: the evolved model's digest did not reach the report records")
    if containment["verdict"] != "PASS":
        problems += [f"report separation: {p}" for p in containment["problems"][:8]]
    nonzero = [s for s in steps if s["returncode"] != 0]
    for s in nonzero:
        problems.append(f"{s['label']}: exit {s['returncode']}: {s['stderr_tail'][-300:]}")

    record["steps"] = steps
    record["verdict"] = "FAIL" if problems else "PASS"
    record["problems"] = problems
    with open(os.path.join(out, "PRELAUNCH.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1, default=str)
    print(json.dumps({"verdict": record["verdict"], "problems": problems,
                      "splits": record["splits"]["counts"],
                      "arms": {k: {"status": v["status"],
                                   "analyst_input": v["analyst_input_check"]["verdict"],
                                   "provenance": v["report_provenance_check"]["verdict"],
                                   "digest_changed": (v.get("evolved_model_reaches_the_optimizer") or {})
                                   .get("digest_changed")}
                               for k, v in record["arms"].items()},
                      "report_separation": {k: containment[k] for k in
                                            ("checked", "action_only_pass", "containment_pass", "verdict")},
                      "out": out}, indent=1))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
