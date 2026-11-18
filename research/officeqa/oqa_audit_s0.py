#!/usr/bin/env python3
"""ONE authoritative record per intended (pass, question) of the corrected-client S0 measurement.

Rule OQA-AUD-1. Offline audit of `OFFICEQA_RECOVERY_PACKET.md` section 7. Reads saved artifacts only:
no API call, no rollout, no scorer re-run. Nothing it writes touches the measurement's own directories.

WHY IT EXISTS. The packet's section 7 published four aggregates that cannot all be true of one
192-episode measurement: a per-pass table summing to 170 emitted answers, a per-question table summing
to the same 170, a termination table over "179 completed question-rollouts" counting 158 answers, and a
sentence claiming 20 of 24 questions were answered in every pass although one pass emitted only 19.
Three of those four come from the same complete source. This tool rebuilds the grid from the rows and
makes every summary a function of it, so a disagreement becomes a failed assertion instead of prose.

THE MANIFEST IS THE ONLY THING THE SUMMARIES MAY READ. One record per intended (pass, question id),
where the intended set is declared from the FROZEN split record and the pass labels - not derived from
the rows, because a grid derived from the rows cannot notice a missing row.

PROVENANCE OF A RECORD, and why each field is there:

  source_file / row_index / row_sha256   the row this record came from, by position and by content
  job_of_record                          which job actually produced it, from the row's OWN final
                                         response id matched against the call log and the job windows -
                                         not from a directory timestamp
  job_original / job_continuation         the pass's first job and, when a pass was resumed, the job
                                         that finished it. `run_batch` resumes by question id, so a
                                         resumed pass holds rows from two jobs in one file
  attempt identity                       final response id, the recorded HTTP attempts for this
                                         question, and how they split between the jobs
  fingerprints                           sha256 of the actor-visible system prompt and user prompt,
                                         plus the rollout settings fingerprint
  status                                 complete / missing / duplicate. A duplicate is NEVER resolved
                                         by score: provenance decides, or the record stays uncertain

ABANDONED ATTEMPTS ARE NOT DUPLICATE RECORDS. When a job is killed mid-pass, the questions that were in
flight have recorded HTTP attempts but no row; the resuming job re-runs them from scratch. Those
attempts are real spend attached to no record, and they are reported as such.

UNRESOLVED REQUESTS ARE ACCOUNTED SEPARATELY. A request sent whose reply never arrived has no call-log
record at all; it exists only as the difference between the ledger's `attempt` and `usage` records for
the owning process. It is not a provider error and not an episode outcome.

usage:
  oqa_audit_s0.py manifest --pass p1=<dir> ... --intended <frozen sets json> --split val
                           --job <id>=<startISO>,<endISO> ... [--ledger <dir>] --out <dir>
  oqa_audit_s0.py summaries   --manifest <S0_AUDIT_MANIFEST.json> --out <dir>
  oqa_audit_s0.py discrepancies --manifest <...> --summary <...> [--truncate p8=11] --out <dir>
  oqa_audit_s0.py inputs --pass p1=<dir> ... --baseline <campaign results.jsonl>
                         [--skill <label>=<path> ...] --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import statistics
import sys

RULE = "OQA-AUD-1"
SCHEMA = "lso.officeqa_s0_audit.v1"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: str) -> list:
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append((i, json.loads(line)))
            except json.JSONDecodeError:
                out.append((i, None))
    return out


def _dt(s: str):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ───────────────────────── the call log, projected ─────────────────────────

def call_index(pass_dir: str) -> dict:
    """The pass's recorded HTTP attempts, indexed by response id and by first-user-message digest.

    One record per attempt that RETURNED (ok or error). A request still in flight when its process was
    killed never reaches this log; that gap is the unresolved-request accounting, not a silence here.
    """
    recs = []
    for path in sorted(glob.glob(os.path.join(pass_dir, "actor_calls*.jsonl"))):
        for _, r in _read_jsonl(path):
            if not isinstance(r, dict) or r.get("schema") != "lso.openai_compatible_call.v1":
                continue
            req, resp = (r.get("request") or {}), (r.get("response") or {})
            msgs = req.get("messages") or []
            first_user = next((m.get("content") or "" for m in msgs if m.get("role") == "user"), "")
            system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
            recs.append({
                "log": os.path.basename(path), "started_utc": r.get("started_utc"),
                "outcome": r.get("outcome"), "attempt": r.get("attempt"),
                "response_id": resp.get("id"), "finish_reason": resp.get("finish_reason"),
                "error_type": str((r.get("error") or {}).get("type") or "") or None,
                "completion_tokens": int(((resp.get("usage") or {}).get("completion_tokens") or 0)),
                "cost_usd": float(((resp.get("usage") or {}).get("cost_usd") or 0.0)),
                "first_user_sha256": sha(first_user)[:16],
                "system_sha256": sha(system)[:16],
                "model": req.get("model"), "temperature": req.get("temperature"),
                "max_tokens": req.get("max_tokens"),
                "extra_body": json.dumps(req.get("extra_body"), sort_keys=True),
                "tools": req.get("tools"),
            })
    by_resp = {c["response_id"]: c for c in recs if c.get("response_id")}
    return {"records": recs, "by_response_id": by_resp}


def job_of(ts: str, windows: list, pass_label: str | None = None) -> tuple:
    """Which job produced work at this instant for this pass: (job, candidates).

    WALL CLOCK ALONE IS NOT ENOUGH, and assuming it is produced a wrong answer the first time this ran.
    Jobs 66346539 and 66346540 were submitted together and ran CONCURRENTLY, so every instant before
    00:02:19 lies inside both windows. What separates them is each job's own declaration of the passes it
    ran, transcribed from its log ("S0 reproducibility: passes 1..4 ..."). So a window owns a pass, and
    the clock then separates the two jobs that shared one pass. When both remain the caller is told, and
    nothing is guessed.
    """
    t = _dt(ts)
    if t is None:
        return None, []
    cands = [w["job"] for w in windows
             if w["start"] <= t <= w["end"]
             and (not w.get("passes") or pass_label is None or pass_label in w["passes"])]
    return (cands[0] if len(cands) == 1 else None), cands


# ───────────────────────── the manifest ─────────────────────────

ROW_FIELDS = ("id", "hard", "soft", "predicted_answer", "ground_truth", "termination_cause",
              "fail_reason", "answer_recovered", "answer_recovery_source", "answer_recovery_outcome",
              "answer_recovery_policy", "last_finish_reason", "n_turns", "model_turns_used",
              "agent_ok", "use_local_tools", "task_type", "rollout_settings_fingerprint",
              "response_metadata_source", "oracle_parsed_pages_included")


def cmd_manifest(passes: list, intended_path: str, split: str, jobs: list,
                 ledger_dir: str | None, out_dir: str) -> dict:
    windows = []
    for spec in jobs:
        job, _, rng = spec.partition("=")
        parts = rng.split(",")
        a = parts[0] if parts else ""
        b = parts[1] if len(parts) > 1 else ""
        labels = [x for x in (parts[2].split("|") if len(parts) > 2 else []) if x]
        windows.append({"job": job, "start": _dt(a), "end": _dt(b), "start_raw": a, "end_raw": b,
                        "passes": labels})
    if any(w["start"] is None or w["end"] is None for w in windows):
        raise SystemExit("every --job needs <id>=<startISO>,<endISO>[,<p1|p2|...>]")

    intended_ids, intended_src = [], None
    if intended_path:
        doc = json.load(open(intended_path, encoding="utf-8"))
        intended_ids = list(((doc.get("sets") or {}).get(split) or {}).get("ids") or [])
        intended_src = {"file": os.path.abspath(intended_path), "split": split,
                        "rule": doc.get("rule"), "digest": doc.get("digest"),
                        "n_ids": len(intended_ids)}
    if not intended_ids:
        raise SystemExit("the intended question set must come from the frozen split record")

    labels = [s.partition("=")[0] for s in passes]
    dirs = {s.partition("=")[0]: s.partition("=")[2] for s in passes}

    records, duplicates, extras, abandoned, per_pass_jobs = [], [], [], [], {}
    for label in labels:
        d = dirs[label]
        rows = _read_jsonl(os.path.join(d, "results.jsonl"))
        calls = call_index(d)
        # every row of the file, keyed by id, keeping ALL of them so a duplicate is visible
        by_id: dict = {}
        for line_no, row in rows:
            if not isinstance(row, dict):
                extras.append({"pass": label, "row_index": line_no, "problem": "unparsable row"})
                continue
            by_id.setdefault(str(row.get("id") or ""), []).append((line_no, row))
        # attempts per question, from the call log, via the user prompt the row itself carries
        want = {}
        for line_no, row in rows:
            if isinstance(row, dict):
                want[sha(str(row.get("target_user_prompt") or ""))[:16]] = str(row.get("id") or "")
        attempts: dict = {}
        unmatched_calls = 0
        for c in calls["records"]:
            qid = want.get(c["first_user_sha256"])
            if qid is None:
                unmatched_calls += 1
                continue
            slot = attempts.setdefault(qid, {"total": 0, "by_job": {}, "errors": 0,
                                             "completion_tokens": 0, "cost_usd": 0.0,
                                             "first_started": None, "last_started": None})
            slot["total"] += 1
            j, _c = job_of(c["started_utc"], windows, label)
            j = j or "unattributed"
            slot["by_job"][j] = slot["by_job"].get(j, 0) + 1
            slot["errors"] += 1 if c["outcome"] == "error" else 0
            slot["completion_tokens"] += c["completion_tokens"]
            slot["cost_usd"] = round(slot["cost_usd"] + c["cost_usd"], 8)
            for k, cmp_ in (("first_started", min), ("last_started", max)):
                slot[k] = c["started_utc"] if slot[k] is None else cmp_(slot[k], c["started_utc"])

        jobs_seen = {}
        for qid in sorted(by_id):
            for line_no, row in by_id[qid]:
                rid = (row.get("response_metadata") or {}).get("response_id")
                c = calls["by_response_id"].get(rid)
                j = job_of(c["started_utc"], windows, label)[0] if c else None
                if j:
                    jobs_seen[j] = min(jobs_seen.get(j, c["started_utc"]), c["started_utc"])
        ordered_jobs = sorted(jobs_seen, key=lambda j: jobs_seen[j])
        per_pass_jobs[label] = {
            "jobs_in_order": ordered_jobs,
            "job_original": ordered_jobs[0] if ordered_jobs else None,
            "job_continuation": ordered_jobs[1] if len(ordered_jobs) > 1 else None,
            "n_rows_in_file": len([r for _, r in rows if isinstance(r, dict)]),
            "unmatched_calls": unmatched_calls,
            "call_logs": sorted({c["log"] for c in calls["records"]}),
            "recorded_attempts": len(calls["records"]),
        }

        for qid in intended_ids:
            hits = by_id.get(qid) or []
            base = {"pass": label, "question_id": qid, "pass_dir": os.path.abspath(d),
                    "source_file": os.path.join(os.path.abspath(d), "results.jsonl"),
                    "trace_path": os.path.join(os.path.abspath(d), "predictions", qid),
                    "call_log": os.path.join(os.path.abspath(d), "actor_calls.jsonl"),
                    "job_original_for_pass": per_pass_jobs[label]["job_original"],
                    "job_continuation_for_pass": per_pass_jobs[label]["job_continuation"],
                    "uncertainty": []}
            if not hits:
                records.append({**base, "status": "missing", "row_index": None,
                                "uncertainty": ["no row for this intended (pass, question)"]})
                continue
            if len(hits) > 1:
                duplicates.append({"pass": label, "question_id": qid,
                                   "row_indexes": [n for n, _ in hits],
                                   "note": "two rows for one intended record; NOT resolved by score"})
            line_no, row = hits[0]
            rid = (row.get("response_metadata") or {}).get("response_id")
            c = calls["by_response_id"].get(rid)
            jr, jcands = job_of(c["started_utc"], windows, label) if c else (None, [])
            att = attempts.get(qid) or {}
            rec = {**base,
                   "status": "complete" if len(hits) == 1 else "duplicate",
                   "row_index": line_no,
                   "row_sha256": sha(json.dumps(row, sort_keys=True, default=str))[:16],
                   "emitted": bool(str(row.get("predicted_answer") or "").strip()),
                   "correct": int(row.get("hard") or 0),
                   "soft": float(row.get("soft") or 0.0),
                   "final_response_id": rid,
                   "final_call_started_utc": c["started_utc"] if c else None,
                   "job_of_record": jr, "job_candidates": jcands,
                   "attempts_recorded": att.get("total"),
                   "attempts_by_job": att.get("by_job"),
                   "attempt_errors": att.get("errors"),
                   "completion_tokens": att.get("completion_tokens"),
                   "provider_cost_usd": att.get("cost_usd"),
                   "system_prompt_sha256": sha(str(row.get("target_system_prompt") or ""))[:16],
                   "user_prompt_sha256": sha(str(row.get("target_user_prompt") or ""))[:16],
                   **{k: row.get(k) for k in ROW_FIELDS}}
            if rid is None:
                rec["uncertainty"].append("the row carries no final response id")
            elif c is None:
                rec["uncertainty"].append("the row's final response id is not in the call log")
            if jr is None and c is not None:
                rec["uncertainty"].append(
                    "the final call falls in no declared job window" if not jcands else
                    f"the final call lies inside more than one declared window: {jcands}")
            if len(hits) > 1:
                rec["uncertainty"].append("duplicate rows for this record; the first in file order is "
                                          "reported and the duplicate is listed separately")
            records.append(rec)

        # attempts attached to no kept record: a rollout the killed job began and the resuming job redid
        kept_jobs = {}
        for r in records:
            if r["pass"] == label and r.get("status") == "complete":
                kept_jobs[r["question_id"]] = r.get("job_of_record")
        for qid, slot in sorted(attempts.items()):
            for j, n in sorted(slot["by_job"].items()):
                if kept_jobs.get(qid) and j != kept_jobs[qid]:
                    abandoned.append({"pass": label, "question_id": qid, "job": j, "attempts": n,
                                      "kept_row_from_job": kept_jobs[qid],
                                      "note": ("this job began the rollout and was killed before the "
                                               "row was written; the resuming job re-ran the question "
                                               "from scratch, so these attempts back no record")})
        for qid in sorted(set(by_id) - set(intended_ids)):
            extras.append({"pass": label, "question_id": qid,
                           "problem": "row for a question outside the intended set"})

    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "intended": {"passes": labels, "question_ids": intended_ids,
                        "n_intended": len(labels) * len(intended_ids), "source": intended_src},
           "jobs": [{"job": w["job"], "start": w["start_raw"], "end": w["end_raw"],
                     "declared_passes": w["passes"],
                     "declared_passes_source": "the job's own log line, transcribed"}
                    for w in windows],
           "per_pass_provenance": per_pass_jobs,
           "records": records, "duplicates": duplicates, "extra_rows": extras,
           "abandoned_attempts": abandoned,
           "unresolved_requests": ledger_unresolved(ledger_dir, [w["job"] for w in windows])
           if ledger_dir else {"note": "no ledger directory given"},
           "note": ("the intended grid is declared from the frozen split record and the pass labels; "
                    "a missing row is a missing observation and may not be counted as anything else")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "S0_AUDIT_MANIFEST.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def ledger_unresolved(ledger_dir: str, jobs: list) -> dict:
    """Requests sent whose reply never arrived, per owning process, from the ledger alone.

    `attempt` records count requests the process was about to send; `usage` records count replies it
    accounted for. The difference is the in-flight set at the moment the process stopped. A process that
    exits cleanly writes a `release`; one that is killed writes none and keeps its whole claim, which
    overcharges the authorization rather than under-charging it.
    """
    out = {"by_owner": [], "total_unresolved": 0, "owners_without_a_release": []}
    spend = os.path.join(ledger_dir, "spend")
    claims = os.path.join(ledger_dir, "claims")
    claimed: dict = {}
    for f in sorted(glob.glob(os.path.join(claims, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        o = str(d.get("owner") or "")
        if any(o.startswith(j) for j in jobs):
            slot = claimed.setdefault(o, {})
            slot[str(d.get("resource"))] = slot.get(str(d.get("resource")), 0) + int(d.get("amount") or 0)
    for f in sorted(glob.glob(os.path.join(spend, "*.jsonl"))):
        owner = os.path.basename(f)[: -len(".jsonl")]
        if not any(owner.startswith(j) for j in jobs):
            continue
        kinds: dict = {}
        release = None
        for _, r in _read_jsonl(f):
            if not isinstance(r, dict):
                continue
            k = str(r.get("kind"))
            kinds[k] = kinds.get(k, 0) + 1
            if k == "release":
                release = r
        unres = int(kinds.get("attempt", 0)) - int(kinds.get("usage", 0))
        out["by_owner"].append({"owner": owner, "attempt_records": kinds.get("attempt", 0),
                                "usage_records": kinds.get("usage", 0), "unresolved": unres,
                                "released": bool(release),
                                "given_back": (release or {}).get("given_back"),
                                "claimed": claimed.get(owner)})
        out["total_unresolved"] += max(0, unres)
        if release is None:
            out["owners_without_a_release"].append(owner)
    out["note"] = ("an unresolved request is neither a provider error nor an episode outcome: it has no "
                   "call-log record and no row. Its claim stays charged, which is the safe direction.")
    return out


# ───────────────────────── summaries, as functions of the manifest ─────────────────────────

def summarise(man: dict) -> dict:
    labels = man["intended"]["passes"]
    qids = man["intended"]["question_ids"]
    recs = man["records"]
    at = {(r["pass"], r["question_id"]): r for r in recs}
    observed = {k: r for k, r in at.items() if r.get("status") in ("complete", "duplicate")}

    per_pass = []
    for p in labels:
        rows = [r for (pp, _), r in observed.items() if pp == p]
        got_ids = [r["question_id"] for r in rows]
        causes: dict = {}
        sources: dict = {}
        for r in rows:
            causes[str(r.get("termination_cause") or "unavailable")] = \
                causes.get(str(r.get("termination_cause") or "unavailable"), 0) + 1
            if r.get("emitted"):
                src = str(r.get("answer_recovery_source") or "content")
                sources[src] = sources.get(src, 0) + 1
        per_pass.append({
            "pass": p, "n_records": len(rows), "unique_question_ids": len(set(got_ids)),
            "duplicate_ids": sorted({q for q in got_ids if got_ids.count(q) > 1}),
            "missing_question_ids": sorted(set(qids) - set(got_ids)),
            "correct": sum(r["correct"] for r in rows),
            "soft_mean": round(statistics.fmean([r["soft"] for r in rows]), 4) if rows else 0.0,
            "emitted": sum(1 for r in rows if r.get("emitted")),
            "recovered": sum(1 for r in rows if r.get("answer_recovered")),
            "answer_sources": sources,
            "termination_causes": causes,
            "attempts_recorded": sum(int(r.get("attempts_recorded") or 0) for r in rows),
            "attempt_errors": sum(int(r.get("attempt_errors") or 0) for r in rows),
            "jobs": sorted({str(r.get("job_of_record")) for r in rows}),
            "settings_fingerprints": sorted({str(r.get("rollout_settings_fingerprint")) for r in rows}),
            "system_prompt_digests": sorted({str(r.get("system_prompt_sha256")) for r in rows}),
        })

    per_q = {}
    for q in qids:
        rows = [r for (_, qq), r in observed.items() if qq == q]
        n = len(rows)
        per_q[q] = {
            "observations": n, "missing": len(labels) - n,
            "times_correct": sum(r["correct"] for r in rows),
            "times_answered": sum(1 for r in rows if r.get("emitted")),
            "times_recovered": sum(1 for r in rows if r.get("answer_recovered")),
            # a label may be asserted only over a COMPLETE row of observations
            "complete": n == len(labels),
        }
    n_pass = len(labels)

    def _class(key: str) -> dict:
        always, never, sometimes, undetermined = [], [], [], []
        for q, v in per_q.items():
            k = v[key]
            if not v["complete"]:
                undetermined.append(q)
            elif k == n_pass:
                always.append(q)
            elif k == 0:
                never.append(q)
            else:
                sometimes.append(q)
        return {"always": sorted(always), "never": sorted(never), "sometimes": sorted(sometimes),
                "undetermined_incomplete_observations": sorted(undetermined)}

    scores = [p["correct"] for p in per_pass]
    emitted = [p["emitted"] for p in per_pass]
    totals = {
        "records_intended": man["intended"]["n_intended"],
        "records_observed": len(observed),
        "records_missing": man["intended"]["n_intended"] - len(observed),
        "correct": sum(scores), "emitted": sum(emitted),
        "recovered": sum(p["recovered"] for p in per_pass),
        "attempts_recorded": sum(p["attempts_recorded"] for p in per_pass),
        "attempt_errors": sum(p["attempt_errors"] for p in per_pass),
        "termination_causes": _merge([p["termination_causes"] for p in per_pass]),
        "answer_sources": _merge([p["answer_sources"] for p in per_pass]),
        "score_mean": round(statistics.fmean(scores), 3) if scores else 0.0,
        "score_sd": round(statistics.stdev(scores), 3) if len(scores) > 1 else 0.0,
        "score_min": min(scores) if scores else None, "score_max": max(scores) if scores else None,
        "emitted_mean": round(statistics.fmean(emitted), 3) if emitted else 0.0,
        "emitted_min": min(emitted) if emitted else None,
        "emitted_max": max(emitted) if emitted else None,
    }
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "manifest_sha256": sha(json.dumps(man.get("records"), sort_keys=True, default=str))[:16],
           "per_pass": per_pass, "per_question": per_q,
           "answering": _class("times_answered"), "correctness": _class("times_correct"),
           "totals": totals}
    doc["assertions"] = assertions(man, doc)
    doc["all_assertions_pass"] = all(a["ok"] for a in doc["assertions"])
    return doc


def _merge(dicts: list) -> dict:
    out: dict = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return out


def assertions(man: dict, s: dict) -> list:
    """Every consistency claim the audit makes, checked rather than asserted in prose."""
    labels = man["intended"]["passes"]
    qids = man["intended"]["question_ids"]
    n_pass, n_q = len(labels), len(qids)
    out = []

    def add(name, ok, detail):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    for p in s["per_pass"]:
        add(f"pass_{p['pass']}_has_{n_q}_unique_intended_ids",
            p["unique_question_ids"] == n_q and not p["missing_question_ids"] and not p["duplicate_ids"],
            {"unique": p["unique_question_ids"], "missing": p["missing_question_ids"],
             "duplicates": p["duplicate_ids"]})
        add(f"pass_{p['pass']}_terminations_sum_to_records",
            sum(p["termination_causes"].values()) == p["n_records"],
            {"sum": sum(p["termination_causes"].values()), "records": p["n_records"]})
        add(f"pass_{p['pass']}_answer_sources_sum_to_emitted",
            sum(p["answer_sources"].values()) == p["emitted"],
            {"sum": sum(p["answer_sources"].values()), "emitted": p["emitted"]})
    bad = sorted(q for q, v in s["per_question"].items() if v["observations"] != n_pass)
    add("every_question_has_one_observation_per_pass", not bad,
        {"questions_with_other_than_%d_observations" % n_pass: bad})
    add("terminations_sum_to_observed_records",
        sum(s["totals"]["termination_causes"].values()) == s["totals"]["records_observed"],
        {"sum": sum(s["totals"]["termination_causes"].values()),
         "observed": s["totals"]["records_observed"]})
    add("answer_sources_sum_to_emitted",
        sum(s["totals"]["answer_sources"].values()) == s["totals"]["emitted"],
        {"sum": sum(s["totals"]["answer_sources"].values()), "emitted": s["totals"]["emitted"]})
    add("per_pass_totals_equal_question_level_totals",
        sum(p["emitted"] for p in s["per_pass"]) == sum(v["times_answered"] for v in s["per_question"].values())
        and sum(p["correct"] for p in s["per_pass"]) == sum(v["times_correct"] for v in s["per_question"].values())
        and sum(p["recovered"] for p in s["per_pass"]) == sum(v["times_recovered"] for v in s["per_question"].values()),
        {"emitted_by_pass": sum(p["emitted"] for p in s["per_pass"]),
         "emitted_by_question": sum(v["times_answered"] for v in s["per_question"].values()),
         "correct_by_pass": sum(p["correct"] for p in s["per_pass"]),
         "correct_by_question": sum(v["times_correct"] for v in s["per_question"].values()),
         "recovered_by_pass": sum(p["recovered"] for p in s["per_pass"]),
         "recovered_by_question": sum(v["times_recovered"] for v in s["per_question"].values())})
    floor = min([p["emitted"] for p in s["per_pass"]] or [0])
    add("always_answered_cannot_exceed_the_smallest_per_pass_emission",
        len(s["answering"]["always"]) <= floor,
        {"always_answered": len(s["answering"]["always"]), "smallest_per_pass_emission": floor})
    add("no_record_is_missing", s["totals"]["records_missing"] == 0,
        {"missing": s["totals"]["records_missing"]})
    add("no_duplicate_records", not man.get("duplicates"), {"duplicates": man.get("duplicates")})
    add("no_rows_outside_the_intended_set", not man.get("extra_rows"),
        {"extra_rows": man.get("extra_rows")})
    add("one_settings_fingerprint_across_all_passes",
        len({f for p in s["per_pass"] for f in p["settings_fingerprints"]}) == 1,
        {"fingerprints": sorted({f for p in s["per_pass"] for f in p["settings_fingerprints"]})})
    add("one_actor_visible_system_prompt_across_all_passes",
        len({f for p in s["per_pass"] for f in p["system_prompt_digests"]}) == 1,
        {"digests": sorted({f for p in s["per_pass"] for f in p["system_prompt_digests"]})})
    add("every_record_is_attributed_to_a_job",
        not [r for r in man["records"] if r.get("status") == "complete" and not r.get("job_of_record")],
        {"unattributed": [f"{r['pass']}/{r['question_id']}" for r in man["records"]
                          if r.get("status") == "complete" and not r.get("job_of_record")]})
    return out


def cmd_summaries(manifest_path: str, out_dir: str) -> dict:
    man = json.load(open(manifest_path, encoding="utf-8"))
    doc = summarise(man)
    doc["manifest"] = os.path.abspath(manifest_path)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "S0_AUDIT_SUMMARY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── discrepancies against the published claims ─────────────────────────

# The four published aggregates of OFFICEQA_RECOVERY_PACKET.md section 7, transcribed with their
# location so each can be checked rather than remembered.
PUBLISHED = {
    "per_pass_scores": {"claim": [11, 9, 12, 11, 9, 11, 8, 7], "where": "section 7, per-pass table"},
    "per_pass_emitted": {"claim": [22, 19, 21, 22, 21, 22, 20, 23], "where": "section 7, per-pass table"},
    "per_pass_recovered": {"claim": [16, 13, 17, 17, 15, 15, 12, 12], "where": "section 7, per-pass table"},
    "score_mean": {"claim": 9.75, "where": "section 7, per-pass table"},
    "score_sd": {"claim": 1.753, "where": "section 7, per-pass table"},
    "attempts_recorded": {"claim": 1476, "where": "section 7, 'zero provider errors in 1,476 logged attempts'"},
    "provider_errors": {"claim": 0, "where": "section 7, per-pass table"},
    "termination_rollouts": {"claim": 179, "where": "section 7, 'over the 179 completed question-rollouts'"},
    "termination_causes": {"claim": {"answer_recovered_from_reasoning": 111, "answer_emitted": 47,
                                     "tool_turn_budget_exhausted_after_tool_call": 17,
                                     "completion_truncated_at_cap": 3,
                                     "empty_reply_content_null+recovery_incomplete_answer_tag": 1},
                           "where": "section 7, termination block"},
    "no_answer_rollouts": {"claim": 21, "where": "section 7, '21 of 179 rollouts produced no answer'"},
    "answers_in_content": {"claim": 47, "where": "section 7, '47 answers did arrive in ordinary content'"},
    "always_answered": {"claim": 20, "where": "section 7, paired table 'answered in all 8 passes 20 of 24'"},
    "never_answered": {"claim": ["UID0161"], "where": "section 7, paired table"},
    "always_correct": {"claim": ["UID0072", "UID0142", "UID0190", "UID0233", "UID0235"],
                       "where": "section 7, paired table"},
    "never_correct": {"claim": ["UID0027", "UID0041", "UID0052", "UID0109", "UID0154", "UID0161",
                                "UID0213", "UID0220", "UID0240"], "where": "section 7, paired table"},
    "flips": {"claim": ["UID0001", "UID0039", "UID0070", "UID0086", "UID0091", "UID0159", "UID0170",
                        "UID0217", "UID0234", "UID0239"], "where": "section 7, paired table"},
}


def truncated_aggregate(man: dict, cuts: dict) -> dict:
    """What the same aggregation yields when a pass is read at an earlier length.

    This is the demonstration, not an alternative result: if the published figures reappear exactly when
    pass 8 is read at its first 11 rows, the published figures were computed from that shorter file, and
    the complete records were never at issue.
    """
    keep = []
    for r in man["records"]:
        if r.get("status") not in ("complete", "duplicate"):
            continue
        lim = cuts.get(r["pass"])
        if lim is not None and (r.get("row_index") or 0) > lim:
            continue
        keep.append(r)
    causes, sources = {}, {}
    for r in keep:
        c = str(r.get("termination_cause") or "unavailable")
        causes[c] = causes.get(c, 0) + 1
        if r.get("emitted"):
            s = str(r.get("answer_recovery_source") or "content")
            sources[s] = sources.get(s, 0) + 1
    per_q: dict = {}
    for r in keep:
        v = per_q.setdefault(r["question_id"], {"seen": 0, "answered": 0, "correct": 0})
        v["seen"] += 1
        v["answered"] += 1 if r.get("emitted") else 0
        v["correct"] += r["correct"]
    n_pass = len(man["intended"]["passes"])
    return {
        "cuts": cuts, "rollouts": len(keep),
        "termination_causes": causes, "answers": sum(1 for r in keep if r.get("emitted")),
        "no_answer": sum(1 for r in keep if not r.get("emitted")),
        "answer_sources": sources,
        "answered_in_every_pass_seen": sum(1 for v in per_q.values()
                                           if v["seen"] and v["answered"] == v["seen"]),
        "answered_at_least_seven_times": sum(1 for v in per_q.values() if v["answered"] >= 7),
        "answered_in_all_%d_passes" % n_pass: sum(1 for v in per_q.values() if v["answered"] == n_pass),
        "always_correct_over_passes_seen": sum(1 for v in per_q.values()
                                               if v["seen"] and v["correct"] == v["seen"]),
    }


def cmd_discrepancies(manifest_path: str, summary_path: str, truncate: list, out_dir: str) -> dict:
    man = json.load(open(manifest_path, encoding="utf-8"))
    s = json.load(open(summary_path, encoding="utf-8"))
    cuts = {}
    for spec in truncate or []:
        label, _, k = spec.partition("=")
        cuts[label] = int(k)
    demo = truncated_aggregate(man, cuts) if cuts else None

    t = s["totals"]
    corrected = {
        "per_pass_scores": [p["correct"] for p in s["per_pass"]],
        "per_pass_emitted": [p["emitted"] for p in s["per_pass"]],
        "per_pass_recovered": [p["recovered"] for p in s["per_pass"]],
        "score_mean": t["score_mean"], "score_sd": t["score_sd"],
        "attempts_recorded": t["attempts_recorded"],
        "provider_errors": t["attempt_errors"],
        "termination_rollouts": t["records_observed"],
        "termination_causes": t["termination_causes"],
        "no_answer_rollouts": t["records_observed"] - t["emitted"],
        "answers_in_content": t["answer_sources"].get("content", 0),
        "always_answered": len(s["answering"]["always"]),
        "never_answered": s["answering"]["never"],
        "always_correct": s["correctness"]["always"],
        "never_correct": s["correctness"]["never"],
        "flips": s["correctness"]["sometimes"],
    }
    rows = []
    for key, pub in PUBLISHED.items():
        got = corrected.get(key)
        same = json.dumps(got, sort_keys=True) == json.dumps(pub["claim"], sort_keys=True)
        rows.append({"quantity": key, "published": pub["claim"], "corrected": got,
                     "agrees": same, "where": pub["where"]})
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "manifest": os.path.abspath(manifest_path), "summary": os.path.abspath(summary_path),
           "comparison": rows,
           "n_disagreements": sum(1 for r in rows if not r["agrees"]),
           "truncation_demonstration": demo,
           "note": ("'published' is transcribed from OFFICEQA_RECOVERY_PACKET.md section 7; "
                    "'corrected' is computed from the manifest by this tool")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "S0_AUDIT_DISCREPANCIES.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── the input match (section 4 of the audit brief) ─────────────────────────

def _first_difference(a: str, b: str) -> dict:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return {"identical": a == b, "first_differing_offset": None if a == b else i,
            "len_a": len(a), "len_b": len(b),
            "a_around": None if a == b else repr(a[max(0, i - 40):i + 40]),
            "b_around": None if a == b else repr(b[max(0, i - 40):i + 40])}


def transmitted_controls(path: str) -> dict:
    """The distinct generation payloads a call log actually carries, with their counts.

    This is what settles "same generation parameters and thinking controls": not a config file, which
    says what was meant, but the request as sent. `extra_body: null` is the historical payload - no
    thinking control transmitted at all - and is what condition A means.
    """
    counts: dict = {}
    n = 0
    for _, r in _read_jsonl(path):
        if not isinstance(r, dict) or r.get("schema") != "lso.openai_compatible_call.v1":
            continue
        q = r.get("request") or {}
        key = json.dumps({"model": q.get("model"), "temperature": q.get("temperature"),
                          "max_tokens": q.get("max_tokens"), "tools_sent": bool(q.get("tools")),
                          "extra_body": q.get("extra_body"), "role": r.get("role")},
                         sort_keys=True)
        counts[key] = counts.get(key, 0) + 1
        n += 1
    return {"file": os.path.abspath(path), "records": n,
            "distinct_payloads": [{"payload": json.loads(k), "count": v}
                                  for k, v in sorted(counts.items())]}


def cmd_inputs(passes: list, baseline_path: str, skills: list, out_dir: str,
               calls: list | None = None) -> dict:
    """Compare what the ACTOR SAW, not the file digests: the rendered system prompt and user prompt.

    A skill file's digest can differ by a trailing newline while the prompt built from it is identical,
    and the prompt is what the actor is given. Both are reported, and the prompt decides.
    """
    base_rows = {str(r.get("id")): r for _, r in _read_jsonl(baseline_path) if isinstance(r, dict)}
    out_passes = []
    sysdig, cfg = {}, {}
    for spec in passes:
        label, _, d = spec.partition("=")
        rows = {str(r.get("id")): r for _, r in _read_jsonl(os.path.join(d, "results.jsonl"))
                if isinstance(r, dict)}
        shared = sorted(set(rows) & set(base_rows))
        same_sys = [q for q in shared
                    if str(rows[q].get("target_system_prompt") or "")
                    == str(base_rows[q].get("target_system_prompt") or "")]
        same_user = [q for q in shared
                     if str(rows[q].get("target_user_prompt") or "")
                     == str(base_rows[q].get("target_user_prompt") or "")]
        diff_user = [q for q in shared if q not in same_user]
        example = None
        if diff_user:
            q = diff_user[0]
            example = {"question_id": q,
                       **_first_difference(str(base_rows[q].get("target_user_prompt") or ""),
                                           str(rows[q].get("target_user_prompt") or ""))}
        sysdig[label] = sorted({sha(str(r.get("target_system_prompt") or ""))[:16] for r in rows.values()})
        cfg[label] = sorted({str(r.get("rollout_settings_fingerprint")) for r in rows.values()})
        out_passes.append({
            "pass": label, "n_rows": len(rows), "shared_question_ids": len(shared),
            "identical_system_prompt": len(same_sys), "identical_user_prompt": len(same_user),
            "user_prompts_that_differ": diff_user,
            "first_user_prompt_difference": example,
            "system_prompt_digests": sysdig[label],
            "settings_fingerprints": cfg[label],
            "sources_identical": all(sorted(rows[q].get("resolved_source_paths") or [])
                                     == sorted(base_rows[q].get("resolved_source_paths") or [])
                                     for q in shared),
            "oracle_pages_included_both": all(bool(rows[q].get("oracle_parsed_pages_included"))
                                              == bool(base_rows[q].get("oracle_parsed_pages_included"))
                                              for q in shared),
            "tools_setting_identical": all(bool(rows[q].get("use_local_tools"))
                                           == bool(base_rows[q].get("use_local_tools"))
                                           for q in shared),
        })
    base_sys = sorted({sha(str(r.get("target_system_prompt") or ""))[:16] for r in base_rows.values()})
    base_fp = sorted({str(r.get("rollout_settings_fingerprint")) for r in base_rows.values()})

    skill_docs = []
    texts = {}
    for spec in skills or []:
        label, _, p = spec.partition("=")
        if not os.path.isfile(p):
            skill_docs.append({"label": label, "path": p, "problem": "not found"})
            continue
        raw = open(p, "rb").read()
        text = raw.decode("utf-8", "replace")
        texts[label] = text
        skill_docs.append({"label": label, "path": os.path.abspath(p), "bytes": len(raw),
                           "sha256_12": hashlib.sha256(raw).hexdigest()[:12],
                           "ends_with_newline": raw.endswith(b"\n"),
                           "sha256_12_of_stripped": hashlib.sha256(
                               text.strip().encode("utf-8")).hexdigest()[:12]})
    pairwise = []
    labels = sorted(texts)
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            pairwise.append({"a": a, "b": b, "raw": _first_difference(texts[a], texts[b]),
                             "stripped_identical": texts[a].strip() == texts[b].strip()})

    control_sets = {}
    for spec in calls or []:
        label, _, path = spec.partition("=")
        control_sets[label] = transmitted_controls(path) if os.path.isfile(path) else \
            {"file": path, "problem": "not found"}
    payload_sets = {lab: json.dumps([d["payload"] for d in v.get("distinct_payloads") or []],
                                    sort_keys=True) for lab, v in control_sets.items()}

    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "transmitted_controls": control_sets,
           "transmitted_controls_identical_across_labels":
               (len(set(payload_sets.values())) == 1) if len(payload_sets) > 1 else None,
           "baseline": {"file": os.path.abspath(baseline_path), "n_rows": len(base_rows),
                        "system_prompt_digests": base_sys, "settings_fingerprints": base_fp},
           "passes": out_passes, "skill_files": skill_docs, "skill_pairwise": pairwise,
           "verdict": {
               "same_actor_visible_system_prompt_as_the_campaign_baseline":
                   bool(base_sys) and all(p["system_prompt_digests"] == base_sys for p in out_passes),
               "same_user_prompts":
                   all(not p["user_prompts_that_differ"] for p in out_passes),
               "same_settings_fingerprint_as_the_campaign_baseline":
                   bool(base_fp) and all(p["settings_fingerprints"] == base_fp for p in out_passes),
           },
           "note": ("the rendered prompts are the actor-visible inputs; a settings fingerprint may "
                    "differ legitimately when a NEW field is added to it, which is a change to the "
                    "fingerprint's definition and not to what the actor saw")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "S0_AUDIT_INPUT_MATCH.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── cli ─────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest")
    m.add_argument("--pass", dest="passes", action="append", required=True)
    m.add_argument("--intended", required=True)
    m.add_argument("--split", default="val")
    m.add_argument("--job", dest="jobs", action="append", required=True)
    m.add_argument("--ledger")
    m.add_argument("--out", required=True)

    s = sub.add_parser("summaries")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True)

    d = sub.add_parser("discrepancies")
    d.add_argument("--manifest", required=True)
    d.add_argument("--summary", required=True)
    d.add_argument("--truncate", action="append")
    d.add_argument("--out", required=True)

    i = sub.add_parser("inputs")
    i.add_argument("--pass", dest="passes", action="append", required=True)
    i.add_argument("--baseline", required=True)
    i.add_argument("--skill", dest="skills", action="append")
    i.add_argument("--calls", dest="calls", action="append",
                   help="<label>=<actor_calls.jsonl>: the generation payload AS SENT")
    i.add_argument("--out", required=True)

    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        doc = cmd_manifest(a.passes, a.intended, a.split, a.jobs, a.ledger, a.out)
        print(f"rule {RULE}  intended {doc['intended']['n_intended']} records "
              f"({len(doc['intended']['passes'])} passes x {len(doc['intended']['question_ids'])} questions)")
        by = {}
        for r in doc["records"]:
            by[r["status"]] = by.get(r["status"], 0) + 1
        print(f"  records by status: {by}")
        print(f"  duplicates {len(doc['duplicates'])}  extra rows {len(doc['extra_rows'])}  "
              f"abandoned-attempt groups {len(doc['abandoned_attempts'])}")
        for label, pv in sorted(doc["per_pass_provenance"].items()):
            print(f"    {label}: rows {pv['n_rows_in_file']} jobs {pv['jobs_in_order']} "
                  f"attempts {pv['recorded_attempts']}")
        ur = doc.get("unresolved_requests") or {}
        print(f"  unresolved requests: {ur.get('total_unresolved')}  "
              f"owners without a release: {ur.get('owners_without_a_release')}")
        print(f"wrote {doc['written_to']}")
    elif a.cmd == "summaries":
        doc = cmd_summaries(a.manifest, a.out)
        t = doc["totals"]
        print(f"rule {RULE}  observed {t['records_observed']} of {t['records_intended']} intended, "
              f"missing {t['records_missing']}")
        print(f"  scores {[p['correct'] for p in doc['per_pass']]} mean {t['score_mean']} "
              f"sd {t['score_sd']} range {t['score_min']}-{t['score_max']}")
        print(f"  emitted {[p['emitted'] for p in doc['per_pass']]} mean {t['emitted_mean']} "
              f"total {t['emitted']}  recovered {t['recovered']}")
        print(f"  terminations {t['termination_causes']}")
        print(f"  answer sources {t['answer_sources']}")
        print(f"  answered: always {len(doc['answering']['always'])} never "
              f"{len(doc['answering']['never'])} sometimes {len(doc['answering']['sometimes'])}")
        print(f"  correct : always {len(doc['correctness']['always'])} never "
              f"{len(doc['correctness']['never'])} sometimes {len(doc['correctness']['sometimes'])}")
        bad = [x for x in doc["assertions"] if not x["ok"]]
        print(f"  assertions: {len(doc['assertions']) - len(bad)} pass, {len(bad)} FAIL")
        for x in bad:
            print(f"    FAIL {x['name']}: {x['detail']}")
        print(f"wrote {doc['written_to']}")
    elif a.cmd == "discrepancies":
        doc = cmd_discrepancies(a.manifest, a.summary, a.truncate, a.out)
        print(f"rule {RULE}  {doc['n_disagreements']} of {len(doc['comparison'])} published "
              f"quantities disagree with the manifest")
        for r in doc["comparison"]:
            if not r["agrees"]:
                print(f"    {r['quantity']}: published {r['published']} -> corrected {r['corrected']}")
        if doc.get("truncation_demonstration"):
            t = doc["truncation_demonstration"]
            print(f"  truncation demonstration {t['cuts']}: {t['rollouts']} rollouts, "
                  f"{t['answers']} answers, causes {t['termination_causes']}")
        print(f"wrote {doc['written_to']}")
    else:
        doc = cmd_inputs(a.passes, a.baseline, a.skills, a.out, a.calls)
        print(f"rule {RULE}  input match against {doc['baseline']['file']}")
        for p in doc["passes"]:
            print(f"    {p['pass']}: system prompt identical for {p['identical_system_prompt']} of "
                  f"{p['shared_question_ids']}, user prompt for {p['identical_user_prompt']}")
        for sk in doc["skill_files"]:
            print(f"    skill {sk.get('label')}: {sk.get('bytes')} B sha {sk.get('sha256_12')} "
                  f"stripped {sk.get('sha256_12_of_stripped')} newline {sk.get('ends_with_newline')}")
        for lab, v in sorted((doc.get("transmitted_controls") or {}).items()):
            print(f"    payload {lab}: {v.get('records')} records "
                  f"{[d['payload'] for d in v.get('distinct_payloads') or []]}")
        if doc.get("transmitted_controls_identical_across_labels") is not None:
            print(f"    payloads identical across labels: "
                  f"{doc['transmitted_controls_identical_across_labels']}")
        for pw in doc["skill_pairwise"]:
            print(f"    {pw['a']} vs {pw['b']}: identical {pw['raw']['identical']}, "
                  f"stripped identical {pw['stripped_identical']}, "
                  f"first difference at {pw['raw']['first_differing_offset']}")
        print(f"  verdict {doc['verdict']}")
        print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
