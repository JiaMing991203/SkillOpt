#!/usr/bin/env python3
"""Which optimizer requests were RECORDED, which were reconstructed, and which are unavailable.

Rule OQA-COL-1, the provenance half. Two of this project's own reports disagree, and the disagreement is
the subject here:

  * `OFFICEQA_THREE_ARM_LAUNCH.md` section 6 claims "12 of 12 reports per phase found VERBATIM in an
    analyst prompt" and "24 reports over 2 collections ... every one matching the attempt on disk".
  * `OFFICEQA_STATUS_PACKET.md` section 7b claims the exact optimizer request is MISSING, that the
    campaign "wrote no `optimizer_calls.jsonl`", and that the minibatch composition is unrecoverable.

The second is WRONG, and this file is how that was established. `oqa_campaign_body.sh` sets
`OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG` to a path inside the JOB's output directory, which lives in the
deployment bundle, NOT in the campaign run tree. A search confined to the run tree finds nothing and
concludes nothing was written. The logs exist, they carry the complete prompt text, and the minibatch
composition is recoverable from the prompt itself.

WHAT THIS DISTINGUISHES, because they are different grades of evidence:

  RECORDED        the bytes are on disk, in a log the job wrote while it ran
  DERIVED         computed here from recorded bytes by a stated rule (which items a prompt names, which
                  saved patch record a response matches) - checkable, and not a guess
  RECONSTRUCTED   re-rendered now from saved rows by re-running the formatter; it may differ from what
                  was sent and is never promoted to recorded evidence
  UNAVAILABLE     no artifact carries it

usage:
  oqa_provenance.py audit --campaign <dir> --bundles <glob> [--bundles <glob> ...]
                          --item UID0014 --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import glob as globmod
import hashlib
import json
import os
import re

RULE = "OQA-COL-1"
SCHEMA = "lso.officeqa_provenance.v1"

REPORT_HEADINGS = ("#### Action Contract and Stage Report", "#### Action Contract Report",
                   "#### Stage and Action Report")
# job name -> (arm, epoch), read off the directory name the body created
_RUNDIR = re.compile(r"run_(\d+)_arm_([ABC])_e(\d+)")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _user_text(call: dict) -> str:
    req = call.get("request") or {}
    return "".join(str(m.get("content") or "")
                   for m in (req.get("messages") or []) if m.get("role") == "user")


def _system_text(call: dict) -> str:
    req = call.get("request") or {}
    return "".join(str(m.get("content") or "")
                   for m in (req.get("messages") or []) if m.get("role") == "system")


def _patch_records(campaign: str, arm: str, epoch: int) -> dict:
    out = {}
    for p in sorted(globmod.glob(os.path.join(campaign, "arm_%s" % arm, "steps",
                                              "step_%04d" % epoch, "patches", "*.json"))):
        try:
            out[os.path.basename(p)] = json.load(open(p, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _match_patch(response_text: str, patches: dict) -> list:
    """Which saved patch record is this response? DERIVED, by its own reasoning prefix.

    The patch record stores what the optimizer returned, so a response whose reasoning prefix appears in
    the record is that record's call. The prefix is used rather than equality because the record is the
    parsed object and the response is the raw text.
    """
    hits = []
    for name, pat in patches.items():
        reason = ((pat.get("patch") or {}).get("reasoning") or "")[:90]
        if reason and reason in response_text:
            hits.append({"patch_record": name, "batch_size": pat.get("batch_size"),
                         "source_type": pat.get("source_type")})
    return hits


def audit_log(path: str, ids: list, campaign: str, item: str, report_text: str) -> dict:
    m = _RUNDIR.search(path)
    job, arm, epoch = (m.group(1), m.group(2), int(m.group(3))) if m else ("?", "?", 0)
    calls = _read_jsonl(path)
    patches = _patch_records(campaign, arm, epoch) if arm != "?" else {}
    out_calls, item_call = [], None
    for i, c in enumerate(calls):
        user, system = _user_text(c), _system_text(c)
        resp = str((c.get("response") or {}).get("content") or "")
        present = [q for q in ids if q in user]
        heads = {h: user.count(h) for h in REPORT_HEADINGS if user.count(h)}
        entry = {
            "index": i,
            "stage": c.get("stage"),
            "model": (c.get("request") or {}).get("model"),
            "started_utc": c.get("started_utc"),
            "user_chars": len(user),
            "user_sha256_12": sha(user)[:12],
            "system_chars": len(system),
            "system_sha256_12": sha(system)[:12],
            "response_chars": len(resp),
            "finish_reason": (c.get("response") or {}).get("finish_reason"),
            "outcome": c.get("outcome"),
            "report_headings": heads,
            "n_report_headings": sum(heads.values()),
            "minibatch_items_DERIVED": present,
            "matches_saved_patch_record_DERIVED": _match_patch(resp, patches),
        }
        if item and item in present:
            entry["carries_the_named_item"] = True
            entry["named_item_report_verbatim"] = bool(report_text) and report_text.strip() in user
            item_call = entry
        out_calls.append(entry)
    return {
        "log": path, "job": job, "arm": arm, "epoch": epoch,
        "RECORDED": "the complete prompt text and response of every optimizer call this job made",
        "n_calls": len(calls),
        "stages": sorted({str(c.get("stage")) for c in calls}),
        "analyst_calls": sum(1 for c in calls if c.get("stage") == "analyst"),
        "calls": out_calls,
        "the_named_items_call": item_call,
    }


def cmd_audit(campaign: str, bundle_globs: list, item: str, out_dir: str) -> dict:
    freeze = os.path.join(campaign, "collect_20261118", "COLLECT_FREEZE.json")
    ids = sorted(json.load(open(freeze, encoding="utf-8"))["ids"]) if os.path.isfile(freeze) else []

    # the delivered report for the named item, matched to its attempt BY DIGEST, not by file name
    report_text, report_file, report_sha = "", None, None
    gen = os.path.join(campaign, "arm_C", "stage_reports", "GENERATED.jsonl")
    if item and os.path.isfile(gen):
        want = None
        for g in _read_jsonl(gen):
            if str(g.get("id")) == item and "step_0003" in str(g.get("prediction_dir", "")):
                want = str(g.get("report_sha256"))
                break
        for cand in sorted(globmod.glob(os.path.join(campaign, "arm_C", "stage_reports",
                                                     "%s.*.md" % item))):
            text = open(cand, encoding="utf-8").read()
            if want and sha(text) == want:
                report_text, report_file, report_sha = text, cand, want
                break

    logs = []
    for g in bundle_globs:
        logs.extend(sorted(globmod.glob(g)))
    logs = sorted(set(p for p in logs if os.path.isfile(p)))
    audited = [audit_log(p, ids, campaign, item, report_text) for p in logs]

    # where the named item appears, across every recorded log
    item_rows = []
    for a in audited:
        for c in a["calls"]:
            if c.get("carries_the_named_item"):
                item_rows.append({
                    "job": a["job"], "arm": a["arm"], "epoch": a["epoch"],
                    "call_index": c["index"], "stage": c["stage"],
                    "user_chars": c["user_chars"], "user_sha256_12": c["user_sha256_12"],
                    "n_report_headings": c["n_report_headings"],
                    "named_item_report_verbatim": c.get("named_item_report_verbatim"),
                    "minibatch_items_DERIVED": c["minibatch_items_DERIVED"],
                    "matches_saved_patch_record_DERIVED": c["matches_saved_patch_record_DERIVED"],
                })

    claims = [
        {
            "claim": ("OFFICEQA_THREE_ARM_LAUNCH.md section 6: '12 of 12 reports per phase found "
                      "verbatim in an analyst prompt', '24 reports over 2 collections'"),
            "concerns": ("the PRE-LAUNCH DRY RUN runs/20261117-officeqa-3arm-prelaunch, driven by "
                         "oqa_prelaunch.py with the actor replaced by a function that raises and the "
                         "optimizer replaced by a recorder - NOT the campaign"),
            "recorded": ("PRELAUNCH.json's verdicts (arms.<X>.analyst_input_check, "
                         "report_separation_check, report_provenance_check) and, per call, the stage, "
                         "user_chars, user_sha256 and system_sha256 in arm_*/_dryrun/"
                         "optimizer_calls_*.jsonl"),
            "not_recorded": ("the dry run's prompt TEXT. The containment check ran in-process on the "
                             "in-memory prompt; only its verdict and the prompt's digest survive"),
            "status": "TRUE OF THE DRY RUN, and its scope was not stated clearly enough",
        },
        {
            "claim": ("OFFICEQA_STATUS_PACKET.md section 7b: 'the exact optimizer request is MISSING; "
                      "the campaign wrote no optimizer_calls.jsonl'"),
            "concerns": "the 2026-11-17 three-arm campaign",
            "recorded": ("WRONG - %d optimizer call logs exist, with the COMPLETE prompt text and "
                         "response of every call" % len(audited)),
            "why_it_was_missed": ("oqa_campaign_body.sh writes the log to the JOB's output directory "
                                  "inside the deployment bundle ($B/run_<job>_<phase>), not into the "
                                  "campaign run tree; a search confined to the run tree finds nothing"),
            "status": "WITHDRAWN",
        },
        {
            "claim": "OFFICEQA_STATUS_PACKET.md section 7b: 'the minibatch composition is MISSING'",
            "concerns": "the campaign",
            "recorded": ("the composition is not a field, but it is DERIVABLE from the recorded prompt: "
                         "the items a prompt names, cross-checked against the saved patch record's "
                         "batch_size and its reasoning prefix"),
            "status": "WITHDRAWN as 'missing'; it is DERIVED, by a stated rule, from recorded bytes",
        },
        {
            "claim": ("OFFICEQA_STATUS_PACKET.md section 7b: the two analyst views in status_20261118/ "
                      "are a RECONSTRUCTION"),
            "concerns": "this session's re-render with fmt_minibatch_trajectories",
            "status": ("STANDS, and is now superseded for arm C epoch 3 by the recorded request; the "
                       "reconstruction is NOT promoted to recorded evidence"),
        },
        {
            "claim": ("OFFICEQA_STATUS_PACKET.md section 7a: 'the gate, not the contract layer, is "
                      "where it died'"),
            "concerns": "arm C step 3's rejected candidate",
            "status": ("OVERSTATED. What is recorded is that the instruction was proposed, ranked, "
                       "applied to the candidate, and that the candidate was rejected at 0.0833 "
                       "against 0.1667, and that no retained skill contains it. That the GATE caused "
                       "the absence of improvement does NOT follow: a candidate carrying one correct "
                       "instruction can still be worse overall, and a gate rejecting a worse candidate "
                       "is the gate working. UNAVAILABLE: any per-instruction attribution of the "
                       "candidate's score"),
        },
    ]

    unavailable = [
        "the prompt TEXT of any pre-launch dry-run call (only its digest was persisted)",
        ("any per-call record for the campaign chains that ran before "
         "OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG was set, if any such chain exists - the logs found are "
         "listed above and each names its own job"),
        ("a per-report Lean certificate, for any arm at any epoch - no delivered report carries one"),
        ("an attribution of a candidate's gate score to any single instruction inside it"),
    ]

    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "campaign": campaign, "named_item": item,
           "named_item_report": {"file": report_file, "sha256_12": (report_sha or "")[:12],
                                 "chars": len(report_text) or None},
           "logs_found": len(audited),
           "total_calls_recorded": sum(a["n_calls"] for a in audited),
           "logs": audited,
           "where_the_named_item_appears": item_rows,
           "claims": claims,
           "unavailable": unavailable}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "PROVENANCE.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--campaign", required=True)
    a.add_argument("--bundles", action="append", required=True,
                   help="glob for optimizer call logs, e.g. '<root>/oqa_campaign_*/run_*/optimizer_calls.jsonl'")
    a.add_argument("--item", default="UID0014")
    a.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    doc = cmd_audit(args.campaign, args.bundles, args.item, args.out)
    print(json.dumps({
        "written_to": doc["written_to"],
        "logs_found": doc["logs_found"],
        "total_calls_recorded": doc["total_calls_recorded"],
        "where_the_named_item_appears": doc["where_the_named_item_appears"],
        "claim_statuses": [{c["claim"][:70]: c["status"][:60]} for c in doc["claims"]],
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
