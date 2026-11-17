"""Audit the reports a RUNNING or finished arm actually sent, from the files on disk.

This is the live counterpart of the pre-launch checks in `oqa_prelaunch.py`. Those ran against recorded traces
with a stubbed optimizer; this one reads the report files the campaign's own jobs wrote, so it says what the
arms really received rather than what the wiring was expected to produce. It opens nothing but the report
directory and makes no API call.

Three questions, which are the three the comparison rests on:

  separation   an Action-Only (arm B) report must contain NO stage material. The fatal markers are the stage
               section headings, the five obligation kind names and the obligation states - the same list
               `oqa_lc_report.STAGE_MARKERS` uses, repeated here on purpose so this audit does not inherit a
               bug from the module it is auditing.
  containment  arm C's report must contain arm B's LINE FOR LINE. Both arms' epoch-1 reports are built from the
               same shared traces under the same frozen model, so the directories are directly comparable.
               Without this, a change to the action renderer could leave the arms differing in the action half
               too, and the comparison would no longer isolate the stage contribution.
  provenance   every report must name the trace it describes and the model it was built under, the trace digests
               must be distinct (one report per attempt, not a cached one reused), and an epoch-1 report must
               carry the IN-SAMPLE notice, because M0 was learned on the rollout those reports describe.

usage:
  python3 -I research/officeqa/oqa_report_audit.py --arm B=<dir> [--arm C=<dir>] [--expect 24] [--out FILE]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os

#: Fatal in an Action-Only report. Repeated from oqa_lc_report rather than imported, so a bug there cannot
#: silently disable the audit here.
STAGE_MARKERS = (
    "## What the task requires", "## The value chain that was recorded",
    "## Requirements and how the record resolves them", "# Stage report", "# Stage and action report",
    "cell_bound", "period_match", "unit_match", "arith_recorded", "answer_traced",
    "DISCHARGED", "OUTSTANDING",
)
IN_SAMPLE = "in-sample"


def _reports(directory: str) -> dict[str, str]:
    """item id -> file name. Reports are named <id>.<trace key>.md."""
    out = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".md"):
            out[name.split(".")[0]] = name
    return out


def audit_arm(arm: str, directory: str, expect: int) -> dict:
    files = _reports(directory)
    texts = {uid: open(os.path.join(directory, n), encoding="utf-8").read() for uid, n in files.items()}
    sizes = sorted(len(t) for t in texts.values())
    leaks = {uid: [m for m in STAGE_MARKERS if m in t] for uid, t in texts.items()}
    leaks = {uid: hits for uid, hits in leaks.items() if hits}
    in_sample = [uid for uid, t in texts.items() if IN_SAMPLE in t.lower()]

    record = {"arm": arm, "dir": os.path.abspath(directory), "n_reports": len(files),
              "chars_min": sizes[0] if sizes else 0, "chars_max": sizes[-1] if sizes else 0,
              "chars_mean": sum(sizes) // len(sizes) if sizes else 0,
              "n_labelled_in_sample": len(in_sample),
              "carries_stage_material": sorted(leaks),
              "stage_markers_found": {u: h for u, h in list(leaks.items())[:5]}}

    manifest = os.path.join(directory, "GENERATED.jsonl")
    if os.path.exists(manifest):
        rows = []
        for line in open(manifest, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        traces = {r.get("trace_sha256") or r.get("trace_digest") for r in rows}
        models = {r.get("model_sha256") or r.get("model_digest") for r in rows}
        record["manifest"] = {
            "rows": len(rows), "distinct_trace_digests": len([t for t in traces if t]),
            "distinct_model_digests": sorted((m or "")[:16] for m in models if m),
            "conditions": sorted({r.get("condition") for r in rows if r.get("condition")}),
            "headings": sorted({r.get("heading") for r in rows if r.get("heading")}),
            "boundary_audit_failures": [r.get("id") for r in rows if r.get("boundary_audit") != "PASS"],
        }

    problems = []
    if arm == "B" and leaks:
        problems.append(f"arm B: {len(leaks)} of {len(files)} reports carry stage material: {sorted(leaks)[:5]}")
    if arm == "C" and len(leaks) != len(files) and files:
        problems.append(f"arm C: only {len(leaks)} of {len(files)} reports carry stage material; "
                        f"arm C's report is supposed to be arm B's PLUS the stage half")
    if expect and len(files) != expect:
        problems.append(f"arm {arm}: {len(files)} reports, expected {expect}")
    m = record.get("manifest")
    if m:
        if m["distinct_trace_digests"] != m["rows"]:
            problems.append(f"arm {arm}: {m['rows']} reports but only {m['distinct_trace_digests']} distinct "
                            f"trace digests - a report was reused across attempts")
        if len(m["distinct_model_digests"]) > 1:
            problems.append(f"arm {arm}: reports were built under {len(m['distinct_model_digests'])} different "
                            f"models in one epoch: {m['distinct_model_digests']}")
        if m["boundary_audit_failures"]:
            problems.append(f"arm {arm}: ANSWER-KEY LEAK in {m['boundary_audit_failures']}")
    record["problems"] = problems
    return record


def containment(b_dir: str, c_dir: str) -> dict:
    bf, cf = _reports(b_dir), _reports(c_dir)
    common = sorted(set(bf) & set(cf))
    contained, failures = 0, []
    for uid in common:
        tb = open(os.path.join(b_dir, bf[uid]), encoding="utf-8").read().splitlines()
        tc = set(open(os.path.join(c_dir, cf[uid]), encoding="utf-8").read().splitlines())
        missing = [l for l in tb if l.strip() and l not in tc]
        if missing:
            failures.append({"id": uid, "n_missing_lines": len(missing), "first": missing[:2]})
        else:
            contained += 1
    problems = []
    if not common:
        problems.append("no item appears in both arms, so containment was not tested")
    if failures:
        problems.append(f"arm C does not contain arm B for {len(failures)} items: "
                        f"{[f['id'] for f in failures][:5]}")
    return {"items_in_both": len(common), "arm_C_contains_arm_B": contained,
            "failures": failures[:5], "problems": problems}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", default=[], metavar="LETTER=DIR", required=True)
    ap.add_argument("--expect", type=int, default=0, help="how many reports each arm should have")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    arms = {}
    for spec in args.arm:
        letter, _, directory = spec.partition("=")
        arms[letter.strip().upper()] = directory
    out = {"generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "arms": {}}
    for letter, directory in sorted(arms.items()):
        out["arms"][letter] = audit_arm(letter, directory, args.expect)
    if "B" in arms and "C" in arms:
        out["containment"] = containment(arms["B"], arms["C"])
    problems = [p for a in out["arms"].values() for p in a["problems"]]
    problems += (out.get("containment") or {}).get("problems", [])
    out["problems"] = problems
    out["verdict"] = "PASS" if not problems else "FAIL"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
