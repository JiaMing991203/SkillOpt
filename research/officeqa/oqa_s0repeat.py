#!/usr/bin/env python3
"""Summarise the corrected-client S0 reproducibility measurement: 8 passes of the 24 validation questions.

Rule OQA-REC-2, stage 7. WHAT THIS IS AND IS NOT.

IT IS a post hoc baseline measurement of ONE frozen skill (S0) on the 24-question validation set, taken
through the corrected client, so that the project has a reproducibility figure that is not contaminated
by the answer-field defect. The campaign's own replicate measurement of a FIXED document - 1, 1, 2, 2, 3,
5, 6 out of 24, sd 1.95 - was taken through the broken client, so it measures the spread of a defect and
sampling together.

IT IS NOT a repair of the historical gate decisions, and NOT a new comparison of the contract arms. The
gate decisions were made on the scores that were taken at the time; nothing here changes them.

WHAT IT REPORTS. Per pass: the score, answer emission, recovered answers, termination causes. Per
question: how many of the 8 passes answered it and how many scored it correct - the paired view, which
is the one that separates a question that is always missed from a question that flips. And the identity
checks that decide whether a difference between passes can be attributed to sampling at all: the skill
digest, the settings fingerprint, the config digest and the transmitted serving controls must be equal
across passes, and provider errors are counted separately from wrong answers.

usage:
  python3 -I research/officeqa/oqa_s0repeat.py --pass p1=<dir> --pass p2=<dir> ... --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import statistics
import sys

RULE = "OQA-REC-2"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _read_jsonl(path: str) -> list:
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _provider_failures(run_dir: str) -> dict:
    """Errors the provider or the transport returned, from the call log. NOT wrong answers."""
    out = {"attempts": 0, "errors": 0, "error_types": {}, "finish_reasons": {},
           "completion_tokens": 0, "provider_cost_usd": 0.0}
    candidates = (sorted(glob.glob(os.path.join(run_dir, "actor_calls*.jsonl")))
                  + sorted(glob.glob(os.path.join(os.path.dirname(run_dir), "actor_calls*.jsonl"))))
    for path in candidates:
        records = _read_jsonl(path)
        if not records:
            continue
        for rec in records:
            if rec.get("schema") != "lso.openai_compatible_call.v1":
                continue
            out["attempts"] += 1
            if rec.get("outcome") == "error":
                out["errors"] += 1
                t = str((rec.get("error") or {}).get("type") or "?")
                out["error_types"][t] = out["error_types"].get(t, 0) + 1
                continue
            resp = rec.get("response") or {}
            fr = str(resp.get("finish_reason") or "")
            out["finish_reasons"][fr] = out["finish_reasons"].get(fr, 0) + 1
            usage = resp.get("usage") or {}
            out["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            out["provider_cost_usd"] += float(usage.get("cost_usd") or 0.0)
        out["call_log"] = os.path.basename(path)
        break        # one log per pass; the first non-empty one is that pass's
    out["provider_cost_usd"] = round(out["provider_cost_usd"], 8)
    return out


def pass_rows(run_dir: str, label: str) -> dict:
    rows = _read_jsonl(os.path.join(run_dir, "results.jsonl"))
    causes: dict = {}
    for r in rows:
        c = str(r.get("termination_cause") or "unavailable")
        causes[c] = causes.get(c, 0) + 1
    emitted = [r for r in rows if str(r.get("predicted_answer") or "").strip()]
    return {
        "pass": label, "dir": os.path.abspath(run_dir),
        "n_questions": len(rows),
        "hard": sum(int(r.get("hard") or 0) for r in rows),
        "soft_mean": round(statistics.fmean([float(r.get("soft") or 0.0) for r in rows]), 4) if rows else 0.0,
        "answers_emitted": len(emitted),
        "answers_recovered": sum(1 for r in rows if r.get("answer_recovered")),
        "recovery_policy": sorted({str(r.get("answer_recovery_policy")) for r in rows}),
        "termination_causes": causes,
        "identity": {
            "skill_digests": sorted({_system_digest(r) for r in rows}),
            "settings_fingerprints": sorted({str(r.get("rollout_settings_fingerprint") or "") for r in rows}),
        },
        "provider": _provider_failures(run_dir),
        "per_question": {str(r.get("id")): {
            "hard": int(r.get("hard") or 0),
            "emitted": bool(str(r.get("predicted_answer") or "").strip()),
            "recovered": bool(r.get("answer_recovered")),
            "termination_cause": r.get("termination_cause"),
            "predicted": str(r.get("predicted_answer") or "")[:80],
        } for r in rows},
    }


def _system_digest(row: dict) -> str:
    import hashlib
    return hashlib.sha256(str(row.get("target_system_prompt") or "").encode("utf-8")).hexdigest()[:16]


def summarise(passes: list, out_dir: str) -> dict:
    per_pass = []
    for spec in passes:
        label, _, d = spec.partition("=")
        per_pass.append(pass_rows(d, label))
    scores = [p["hard"] for p in per_pass]
    ids = sorted({q for p in per_pass for q in p["per_question"]})
    per_q = {}
    for q in ids:
        hits = [p["per_question"][q]["hard"] for p in per_pass if q in p["per_question"]]
        em = [p["per_question"][q]["emitted"] for p in per_pass if q in p["per_question"]]
        rec = [p["per_question"][q]["recovered"] for p in per_pass if q in p["per_question"]]
        per_q[q] = {"passes_seen": len(hits), "times_correct": sum(hits),
                    "times_answered": sum(1 for x in em if x),
                    "times_recovered": sum(1 for x in rec if x),
                    "flips": 0 < sum(hits) < len(hits)}
    # IDENTITY ACROSS PASSES. A difference between passes may be attributed to sampling only if the skill,
    # the prompt-shaping settings and the serving controls were the same in all of them.
    skills = sorted({d for p in per_pass for d in p["identity"]["skill_digests"]})
    fps = sorted({d for p in per_pass for d in p["identity"]["settings_fingerprints"]})
    doc = {
        "rule": RULE, "utc": _utc(), "n_passes": len(per_pass),
        "scores_hard_of_24": scores,
        "score_mean": round(statistics.fmean(scores), 3) if scores else 0.0,
        "score_sd": round(statistics.stdev(scores), 3) if len(scores) > 1 else 0.0,
        "score_min": min(scores) if scores else None, "score_max": max(scores) if scores else None,
        "answers_emitted": [p["answers_emitted"] for p in per_pass],
        "answers_recovered": [p["answers_recovered"] for p in per_pass],
        "provider_errors": [p["provider"]["errors"] for p in per_pass],
        "attempts": [p["provider"]["attempts"] for p in per_pass],
        "provider_cost_usd": round(sum(p["provider"]["provider_cost_usd"] for p in per_pass), 8),
        "identity_across_passes": {
            "distinct_system_prompt_digests": skills,
            "distinct_settings_fingerprints": fps,
            "one_skill": len(skills) == 1, "one_settings_fingerprint": len(fps) == 1,
        },
        "per_question": per_q,
        "questions_never_answered": sorted(q for q, v in per_q.items() if v["times_answered"] == 0),
        "questions_always_correct": sorted(q for q, v in per_q.items()
                                           if v["passes_seen"] and v["times_correct"] == v["passes_seen"]),
        "questions_that_flip": sorted(q for q, v in per_q.items() if v["flips"]),
        "by_pass": per_pass,
        "caveat": ("a post hoc baseline on a validation set that has already been used; it does not "
                   "repair the historical gate decisions and is not a comparison of the arms"),
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "S0_REPEAT.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pass", dest="passes", action="append", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = summarise(args.passes, args.out)
    print(f"rule {RULE}  {doc['n_passes']} pass(es) of 24: {doc['scores_hard_of_24']}  "
          f"mean {doc['score_mean']} sd {doc['score_sd']} range "
          f"{doc['score_min']}-{doc['score_max']}")
    print(f"  answers emitted {doc['answers_emitted']}  recovered {doc['answers_recovered']}  "
          f"provider errors {doc['provider_errors']}  attempts {doc['attempts']}")
    print(f"  one skill across passes: {doc['identity_across_passes']['one_skill']}  "
          f"one settings fingerprint: {doc['identity_across_passes']['one_settings_fingerprint']}")
    print(f"  never answered {len(doc['questions_never_answered'])}, always correct "
          f"{len(doc['questions_always_correct'])}, flipping {len(doc['questions_that_flip'])}")
    for p in doc["by_pass"]:
        print(f"    {p['pass']:4s} hard {p['hard']:2d}/{p['n_questions']} soft {p['soft_mean']:.3f} "
              f"emitted {p['answers_emitted']:2d} recovered {p['answers_recovered']:2d} "
              f"attempts {p['provider']['attempts']:4d} errors {p['provider']['errors']}")
    print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
