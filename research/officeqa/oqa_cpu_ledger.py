#!/usr/bin/env python3
"""Rebuild the OfficeQA CPU ledger from individual Slurm job records, under one stated convention.

Rule OQA-BIND-1, the accounting half. The running total reported since Grant 3 mixes two units: the
campaign phase was counted in CPU-core-hours and every grant after it in requested walltime. This file
rebuilds all three figures from the job records, maps every job to a grant, and reports the discrepancy
instead of picking whichever unit is convenient.

THE COUNTING RULES, each because of a specific way of getting this wrong:

  1. ONE RECORD PER JOB. `sacct -X` returns allocations only, never the `.batch` / `.extern` step
     records, so a job cannot be counted twice. The tool asserts no JobID contains a '.'.
  2. STARTED JOBS ONLY, AT THEIR FULL REQUESTED LIMIT. A job that never started reserved nothing and is
     excluded; a job that started is charged its whole requested walltime whether or not it used it,
     which is the established rule and the conservative direction. "Never started" is read from the
     Start field being None/Unknown, not from the state - three CANCELLED jobs here did start.
  3. OFFICEQA MEMBERSHIP IS CHECKED, NOT ASSUMED. Only the `oqa-` / `officeqa` naming convention counts,
     and every other prefix in the window is listed in the output so a reader can see what was excluded
     and why. The window holds ALFWorld (`awc-`, `lc-`, `ac-`), Experiment 2/3 (`e2`, `e3`) and the
     November trace-only work (`lso-`, `s0-`), none of which is OfficeQA.
  4. THE PRE-CAMPAIGN FEASIBILITY JOBS ARE A SEPARATE PHASE. `oqa-feas` and the connectivity controls ran
     2026-11-15/16, BEFORE the campaign authorization of 2026-11-17, so they cannot count against a
     ceiling that did not exist. They are reported, under their own label, outside the 44-hour total.

THREE UNITS, REPORTED SEPARATELY AND NEVER SUMMED TOGETHER:

    requested   sum of Timelimit over started jobs      - what a reservation-based ceiling counts
    elapsed     sum of ElapsedRaw                       - wall-clock actually consumed
    core_hours  sum of CPUTimeRAW                       - elapsed x allocated CPUs

usage:
  oqa_cpu_ledger.py --sacct <file.psv> --out <dir>
     where the file is produced by, and the field order is fixed by:
     sacct -u $USER -S <date> -X -P -n -o \\
       JobID,JobName%34,State%14,TimelimitRaw,ElapsedRaw,ReqCPUS,AllocCPUS,CPUTimeRAW,Submit,Start,Partition
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re

RULE = "OQA-BIND-1"
SCHEMA = "lso.officeqa_cpu_ledger.v1"

FIELDS = ("JobID", "JobName", "State", "TimelimitRaw", "ElapsedRaw", "ReqCPUS", "AllocCPUS",
          "CPUTimeRAW", "Submit", "Start", "Partition")

# A job belongs to OfficeQA when its name matches this. Everything else in the window is listed as
# excluded, with its prefix, so the exclusion is visible rather than silent.
OFFICEQA = re.compile(r"^(oqa|officeqa)[-_]", re.I)

# Grant membership, by job-name prefix. Each mapping is checked against the grant's own recorded figure
# in OFFICEQA_AUTHORIZATION.md, and a mismatch is reported rather than hidden.
GRANTS = [
    {"grant": "pre-campaign feasibility (OUTSIDE the 44-hour ceiling)",
     "prefixes": ("oqa-feas",),
     "authorized_requested_h": None,
     "note": "ran 2026-11-15/16, before the campaign authorization of 2026-11-17 existed"},
    {"grant": "Grants 1-2: the three-arm campaign",
     "prefixes": ("oqa-camp", "oqa-A-", "oqa-B-", "oqa-C-", "oqa-rec", "oqa-ho-", "oqa-shared",
                  "oqa-tests-full", "oqa-diag", "oqa-slow", "oqa-forensics", "oqa-lean",
                  "oqa-control", "oqa-ctl", "oqa-api-smoke"),
     "authorized_requested_h": 40.0,
     "note": "the ceiling the 20 -> 40 amendment set; section 3b of the authorization reported this "
             "phase in CORE-HOURS (27.61), which is where the unit mixing begins"},
    {"grant": "Grant 3: the OQA-FOR-1 follow-up",
     "prefixes": ("oqa-fu-", "oqa-cc-"),
     "authorized_requested_h": 4.0,
     "note": "recorded as 4.00 requested job-hours over 17 jobs"},
    {"grant": "Grant 4: the answer-recovery follow-up",
     "prefixes": ("oqa-fu2",),
     "authorized_requested_h": 8.0,
     "note": "recorded as 6.88 requested job-hours over 14 jobs"},
    {"grant": "Grant 5: the offline audit",
     "prefixes": ("oqa-aud",),
     "authorized_requested_h": 2.0,
     "note": "recorded as 0.87 requested job-hours over 3 jobs"},
    {"grant": "Grant 6: the status packet",
     "prefixes": ("oqa-st-",),
     "authorized_requested_h": 1.0,
     "note": "recorded as 0.90 requested job-hours over 4 jobs"},
    {"grant": "Grant 7: the training collection",
     "prefixes": ("oqa-col",),
     "authorized_requested_h": 1.5,
     "note": "recorded as 1.418 requested job-hours over 5 jobs"},
    {"grant": "Grant 8: this source-binding investigation",
     "prefixes": ("oqa-bind",),
     "authorized_requested_h": 0.5,
     "note": "a NEW separate allocation; explicitly not retrospective approval of any overrun"},
]


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _rows(path: str) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) < len(FIELDS):
                parts += [""] * (len(FIELDS) - len(parts))
            out.append(dict(zip(FIELDS, parts[:len(FIELDS)])))
    return out


def _started(r: dict) -> bool:
    """Did this job ever start? Read from Start, not from State.

    Three CANCELLED jobs in this window DID start and did consume a reservation; several did not. The
    state alone cannot tell them apart, so the Start field decides.
    """
    s = (r.get("Start") or "").strip()
    return s not in ("", "None", "Unknown")


def _num(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except ValueError:
        return 0


def _grant_of(name: str):
    for g in GRANTS:
        for p in g["prefixes"]:
            if name.startswith(p):
                return g["grant"]
    return None


def reconcile(sacct_path: str) -> dict:
    rows = _rows(sacct_path)
    problems = []

    # rule 1: one record per job
    steps = [r["JobID"] for r in rows if "." in r["JobID"]]
    if steps:
        problems.append("step records present, so a job could be double counted: %s" % steps[:5])

    oqa = [r for r in rows if OFFICEQA.match(r["JobName"] or "")]
    other = [r for r in rows if not OFFICEQA.match(r["JobName"] or "")]
    excluded_prefixes = {}
    for r in other:
        pre = re.split(r"[-_0-9]", r["JobName"] or "?", 1)[0] or "?"
        excluded_prefixes[pre] = excluded_prefixes.get(pre, 0) + 1

    # rule 3: every OfficeQA job must map to a grant
    unmapped = sorted({r["JobName"] for r in oqa if _grant_of(r["JobName"]) is None})
    if unmapped:
        problems.append("OfficeQA jobs that map to no grant: %s" % unmapped)

    def tally(subset: list) -> dict:
        started = [r for r in subset if _started(r)]
        never = [r for r in subset if not _started(r)]
        return {
            "jobs_total": len(subset),
            "jobs_started": len(started),
            "jobs_never_started": len(never),
            "never_started_ids": sorted(r["JobID"] for r in never),
            # the established rule: a started job is charged its FULL requested limit
            "requested_h": round(sum(_num(r["TimelimitRaw"]) for r in started) * 60 / 3600.0, 3),
            "elapsed_h": round(sum(_num(r["ElapsedRaw"]) for r in started) / 3600.0, 3),
            "core_h": round(sum(_num(r["CPUTimeRAW"]) for r in started) / 3600.0, 3),
            "requested_h_if_never_started_were_counted": round(
                sum(_num(r["TimelimitRaw"]) for r in subset) * 60 / 3600.0, 3),
            "states": _count(subset, "State"),
            "alloc_cpus": sorted({_num(r["AllocCPUS"]) for r in started}),
        }

    per_grant = []
    for g in GRANTS:
        subset = [r for r in oqa if _grant_of(r["JobName"]) == g["grant"]]
        t = tally(subset)
        t["grant"] = g["grant"]
        t["authorized_requested_h"] = g["authorized_requested_h"]
        t["note"] = g["note"]
        t["job_names"] = sorted({r["JobName"] for r in subset})
        if g["authorized_requested_h"] is not None:
            t["requested_over_authorized"] = round(t["requested_h"] - g["authorized_requested_h"], 3)
        per_grant.append(t)

    # the 44-hour ceiling covers the campaign and every grant after it, NOT the pre-campaign phase
    in_ceiling = [g for g in per_grant if not g["grant"].startswith("pre-campaign")]
    ceiling_rows = [r for r in oqa
                    if _grant_of(r["JobName"]) and not _grant_of(r["JobName"]).startswith("pre-campaign")]
    totals = tally(ceiling_rows)
    totals["label"] = "everything the 44-hour ceiling covers (campaign + Grants 3-8)"
    pre = tally([r for r in oqa if (_grant_of(r["JobName"]) or "").startswith("pre-campaign")])
    pre["label"] = "pre-campaign feasibility, outside the ceiling"
    allo = tally(oqa)
    allo["label"] = "every OfficeQA job in the window, for reference only"

    # what the previously reported figures were, and why they differ
    mixed = round(sum(g["requested_h"] for g in in_ceiling
                      if not g["grant"].startswith("Grants 1-2"))
                  + next(g["core_h"] for g in in_ceiling if g["grant"].startswith("Grants 1-2")), 3)
    explain = {
        "41.69_as_reported": {
            "what_it_is": "the MIXED figure reported since Grant 3",
            "how_it_is_built": "CORE-HOURS for the campaign phase plus REQUESTED walltime for every "
                               "grant after it",
            "rebuilt_here": mixed,
            "verdict": "reproducible, and not a quantity in any single unit",
        },
        "79.65_as_reported": {
            "what_it_is": "requested walltime over EVERY oqa- job in the window, including the "
                          "pre-campaign feasibility phase and including jobs that never started",
            "rebuilt_here": allo["requested_h_if_never_started_were_counted"],
            "verdict": "an over-count on two separate grounds; superseded by the figure below",
        },
        "43.92_as_reported": {
            "what_it_is": "core-hours over EVERY oqa- job in the window, including the pre-campaign "
                          "feasibility phase",
            "rebuilt_here": allo["core_h"],
            "verdict": "an over-count on one ground: the pre-campaign phase is not inside the ceiling",
        },
    }

    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "sacct_source": sacct_path,
           "counting_rules": [
               "one record per job: sacct -X, no step records (asserted)",
               "started jobs only, each charged its FULL requested limit",
               "never-started jobs excluded (Start is None/Unknown, not read from State)",
               "OfficeQA membership by the oqa-/officeqa naming convention, with every other prefix "
               "in the window listed as excluded",
               "the pre-campaign feasibility phase is reported separately, outside the 44-hour ceiling",
           ],
           "n_records_read": len(rows),
           "officeqa_jobs": len(oqa),
           "excluded_non_officeqa_jobs": len(other),
           "excluded_prefixes": excluded_prefixes,
           "per_grant": per_grant,
           "inside_the_ceiling": totals,
           "outside_the_ceiling": pre,
           "all_officeqa_for_reference": allo,
           "previously_reported_figures": explain,
           "problems": problems}
    return doc


def _count(rows: list, key: str) -> dict:
    out = {}
    for r in rows:
        v = (r.get(key) or "").split(" by ")[0]
        out[v] = out.get(v, 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sacct", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    doc = reconcile(a.sacct)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "CPU_LEDGER.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({
        "written_to": path,
        "inside_the_ceiling": {k: doc["inside_the_ceiling"][k]
                               for k in ("jobs_started", "jobs_never_started", "requested_h",
                                         "elapsed_h", "core_h")},
        "outside_the_ceiling": {k: doc["outside_the_ceiling"][k]
                                for k in ("jobs_started", "requested_h", "elapsed_h", "core_h")},
        "previously_reported": {k: v["rebuilt_here"] for k, v in
                                doc["previously_reported_figures"].items()},
        "per_grant": [{g["grant"][:46]: [g["jobs_started"], g["requested_h"], g["core_h"]]}
                      for g in doc["per_grant"]],
        "problems": doc["problems"],
    }, indent=1, default=str))
    return 1 if doc["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
