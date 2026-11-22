"""Reconstruct the 2026-11-21 phase's paid consumption from every source that recorded it.

WHY THIS EXISTS. The shared spend ledger was inert for most of the campaign: `SKILLOPT_SPEND_LEDGER_DIR`
was never exported until job 66553248, so `skillopt/model/spend_ledger.py` did nothing and recorded
nothing for every stage before it. Activating a ledger does not retrospectively incorporate spending that
already happened, so this phase's consumption has to be rebuilt from the records that DO exist, with the
overlaps named rather than summed twice.

THE SOURCES, and what each one can and cannot answer.

  1. PER-JOB CALL LOGS - `<bundle>/run_<job>_<stage>/actor_calls.jsonl` and `optimizer_calls.jsonl`,
     written by the OpenAI-compatible backend. One line per HTTP ATTEMPT, including retries (`attempt`,
     `max_attempts`, `outcome`), the resolved request, and the provider's OWN `usage.cost_usd`. This is
     the finest-grained source and the only one that carries the provider's figure per call.
     It does NOT exist for a stage whose body never exported the variable.

  2. THE SHARED LEDGER - `<run root>/ledger/{claims,release,spend}`. One `spend/*.jsonl` per PROCESS,
     with an `attempt` record before each request and a `usage` record after each reply. It is the only
     source for the three held-out jobs, whose deployed body (commit c35df7ef6ba6) did not export the
     actor call-log path - verified by reading the deployed script, not inferred from the absence.
     It covers nothing before job 66553248.

  3. THE PROVIDER'S FIGURE - available per call INSIDE source 1 (`usage.cost_usd`) and per usage record
     inside source 2 when the backend passed it through. There is no separate account statement here, so
     "provider accounting" means the provider's per-call figure, not an invoice.

WHAT THIS DOES NOT DO. It does not modify a single historical record. It reads them and writes one new
adjustment document. Where a source cannot answer, the gap is reported with a conservative UPPER bound
derived from what the stage could at most have spent, never with an invented point estimate.

usage:
    python3 -I research/officeqa/oqa_phase_acct.py --jobdirs <dir> --ledger <dir> --out <file.json>
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sys

SCHEMA = "lso.oqa_phase_accounting.v1"

# The price table the campaign's bodies exported, USD per 1,000,000 tokens: prompt, completion, cached.
# Recorded here so the computed charge can be rebuilt without the cluster environment.
PRICES = {
    "target": (0.04, 0.07, 0.02),
    "optimizer": (0.30, 1.20, 0.006),
}

# The authorization this phase was granted (2026-11-21). Attempts AND dollars, both binding.
PHASE_CAPS = {
    "target": {"attempts": 20000, "usd": 15.00},
    "optimizer": {"attempts": 400, "usd": 20.00},
}

# The reserve one unresolved request holds, from the same bodies. An unresolved request may have been
# billed, so its reserve is treated as spent.
RESERVE_USD = {"target": 0.02, "optimizer": 0.15}

_RUNDIR = re.compile(r"run_(\d+)_([a-z0-9_]+)$")
_CLAIM = re.compile(r"^(\d{6})-(target|optimizer)-(attempts|usdmicro)-(\d+)\.json$")


def computed_usd(role: str, prompt: int, completion: int, cached: int) -> float:
    """The project's own price table, which is what every cap and refusal was enforced on."""
    p, c, k = PRICES[role]
    uncached = max(0, int(prompt) - int(cached))
    return (uncached * p + int(completion) * c + int(cached) * k) / 1_000_000.0


def _jsonl(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


# ── source 1: the per-job call logs ───────────────────────────────────────────

def read_call_logs(jobdirs_root: str) -> dict:
    """One record per (job, stage, role), from the backend's own attempt log."""
    out: dict[tuple[str, str], dict] = {}
    for path in sorted(glob.glob(os.path.join(jobdirs_root, "*", "run_*", "*_calls.jsonl"))):
        rundir = os.path.basename(os.path.dirname(path))
        m = _RUNDIR.match(rundir)
        if not m:
            continue
        job, stage = m.group(1), m.group(2)
        role = "target" if os.path.basename(path).startswith("actor") else "optimizer"
        key = (job, role)
        rec = out.setdefault(key, {
            "job": job, "stage": stage, "role": role, "source": "call_log",
            "paths": [], "attempts": 0, "retries": 0, "outcomes": {},
            "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
            "provider_cost_usd": 0.0, "provider_cost_calls": 0,
            "computed_usd": 0.0, "no_usage_rows": 0, "models": {},
        })
        rec["paths"].append(os.path.relpath(path, jobdirs_root))
        for row in _jsonl(path):
            rec["attempts"] += 1
            if int(row.get("attempt") or 1) > 1:
                rec["retries"] += 1
            oc = str(row.get("outcome") or "unknown")
            rec["outcomes"][oc] = rec["outcomes"].get(oc, 0) + 1
            req = row.get("request") or {}
            mdl = str(req.get("model") or "")
            if mdl:
                rec["models"][mdl] = rec["models"].get(mdl, 0) + 1
            usage = ((row.get("response") or {}).get("usage")) or {}
            if not usage:
                rec["no_usage_rows"] += 1
                continue
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            det = usage.get("prompt_tokens_details") or {}
            cached = int(det.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0)
            rec["prompt_tokens"] += pt
            rec["completion_tokens"] += ct
            rec["cached_tokens"] += cached
            rec["computed_usd"] += computed_usd(role, pt, ct, cached)
            if usage.get("cost_usd") is not None:
                rec["provider_cost_usd"] += float(usage["cost_usd"])
                rec["provider_cost_calls"] += 1
    for rec in out.values():
        rec["computed_usd"] = round(rec["computed_usd"], 6)
        rec["provider_cost_usd"] = round(rec["provider_cost_usd"], 6)
    return out


# ── source 2: the shared ledger ───────────────────────────────────────────────

def read_ledger(ledger_root: str) -> dict:
    """Per-process spend records, plus the claim/release state that says what is still reserved."""
    procs: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(ledger_root, "spend", "*.jsonl"))):
        owner = os.path.basename(path)[: -len(".jsonl")]
        job = owner.split("r")[0]
        for row in _jsonl(path):
            role = str(row.get("role") or "")
            # `release` and some `open` records carry no role. They are not spending and must not
            # create a (job, role) row - an earlier version of this let them through and invented an
            # empty-role row per job, which read as a job with zero attempts from the ledger.
            if role not in PRICES:
                continue
            rec = procs.setdefault((owner, role), {
                "owner": owner, "job": job, "role": role, "source": "ledger",
                "attempts": 0, "usage_records": 0, "refusals": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
                "computed_usd": 0.0, "computed_micro": 0, "provider_cost_usd": 0.0,
                "provider_cost_calls": 0, "reserve_exceeded": 0, "stages": {},
            })
            kind = row.get("kind")
            if kind == "attempt":
                rec["attempts"] += 1
                st = str(row.get("stage") or "")
                rec["stages"][st] = rec["stages"].get(st, 0) + 1
            elif kind == "usage":
                rec["usage_records"] += 1
                rec["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
                rec["completion_tokens"] += int(row.get("completion_tokens") or 0)
                rec["cached_tokens"] += int(row.get("cached_prompt_tokens") or 0)
                rec["computed_usd"] += float(row.get("usd") or 0.0)
                # The ledger charges in INTEGER micro-dollars and the invariant it enforces is an
                # integer comparison. Summing the rounded dollar figures instead made this
                # reconstruction report `spent` 12 micro-dollars ABOVE `claimed` on the real campaign
                # ledger - a cap violation that existed only in my own arithmetic. Keep the integer.
                rec["computed_micro"] += int(round(float(row.get("usd") or 0.0) * 1_000_000))
                if row.get("provider_cost_usd") is not None:
                    rec["provider_cost_usd"] += float(row["provider_cost_usd"])
                    rec["provider_cost_calls"] += 1
                if row.get("reserve_exceeded"):
                    rec["reserve_exceeded"] += 1
            elif kind == "refused":
                rec["refusals"] += 1
    for rec in procs.values():
        rec["computed_usd"] = round(rec["computed_usd"], 6)
        rec["provider_cost_usd"] = round(rec["provider_cost_usd"], 6)
        rec["unresolved_requests"] = max(0, rec["attempts"] - rec["usage_records"])

    # claims and releases, from the file NAMES exactly as the ledger reads them
    state: dict[tuple[str, str], dict] = {}
    for sub, sign in (("claims", 1), ("release", -1)):
        d = os.path.join(ledger_root, sub)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            m = _CLAIM.match(name)
            if not m:
                continue
            key = (m.group(2), m.group(3))
            s = state.setdefault(key, {"claimed": 0, "released": 0, "n_claims": 0, "n_releases": 0})
            if sign > 0:
                s["claimed"] += int(m.group(4))
                s["n_claims"] += 1
            else:
                s["released"] += int(m.group(4))
                s["n_releases"] += 1
    return {"processes": procs, "claim_state": state}


# ── overlap, totals, gaps ─────────────────────────────────────────────────────

def reconcile(call_logs: dict, ledger: dict, job_catalogue: dict) -> dict:
    """Decide, per (job, role), which source is authoritative, and name every overlap and gap.

    THE RULE. A call log counts one line per HTTP attempt and carries the provider's own figure, so where
    a call log exists it is authoritative. The ledger is authoritative exactly where no call log exists.
    A (job, role) covered by both is counted ONCE from the call log, and the two figures are compared so
    a disagreement is visible instead of averaged away.
    """
    led_by_job: dict[tuple[str, str], dict] = {}
    for (owner, role), rec in ledger["processes"].items():
        key = (rec["job"], role)
        agg = led_by_job.setdefault(key, {
            "job": rec["job"], "role": role, "owners": [], "attempts": 0, "usage_records": 0,
            "refusals": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
            "computed_usd": 0.0, "provider_cost_usd": 0.0, "provider_cost_calls": 0,
            "unresolved_requests": 0,
        })
        agg["owners"].append(owner)
        for k in ("attempts", "usage_records", "refusals", "prompt_tokens", "completion_tokens",
                  "cached_tokens", "provider_cost_calls", "unresolved_requests"):
            agg[k] += rec[k]
        agg["computed_usd"] += rec["computed_usd"]
        agg["provider_cost_usd"] += rec["provider_cost_usd"]
    for agg in led_by_job.values():
        agg["computed_usd"] = round(agg["computed_usd"], 6)
        agg["provider_cost_usd"] = round(agg["provider_cost_usd"], 6)

    rows = []
    keys = sorted(set(call_logs) | set(led_by_job))
    for key in keys:
        job, role = key
        cl, led = call_logs.get(key), led_by_job.get(key)
        row = {
            "job": job, "role": role,
            "stage": (cl or {}).get("stage") or job_catalogue.get(job, {}).get("stage", "unknown"),
            "in_call_log": cl is not None, "in_ledger": led is not None,
            "coverage": ("both" if cl and led else "call_log_only" if cl else "ledger_only"),
            "counted_from": "call_log" if cl else "ledger",
        }
        src = cl or led
        row["attempts"] = src["attempts"]
        row["retries"] = src.get("retries", "unavailable_in_ledger")
        row["prompt_tokens"] = src["prompt_tokens"]
        row["completion_tokens"] = src["completion_tokens"]
        row["cached_tokens"] = src["cached_tokens"]
        row["computed_usd"] = round(src["computed_usd"], 6)
        row["provider_cost_usd"] = round(src["provider_cost_usd"], 6)
        row["provider_cost_calls"] = src["provider_cost_calls"]
        if cl and led:
            row["disagreement"] = {
                "attempts_call_log": cl["attempts"], "attempts_ledger": led["attempts"],
                "attempts_delta": cl["attempts"] - led["attempts"],
                "computed_usd_call_log": round(cl["computed_usd"], 6),
                "computed_usd_ledger": round(led["computed_usd"], 6),
            }
        if led:
            row["unresolved_requests"] = led["unresolved_requests"]
            row["refusals"] = led["refusals"]
        if cl and led:
            row["cross_check"] = ("the two independent sources AGREE on this (job, role)"
                                  if cl["attempts"] == led["attempts"]
                                  and abs(cl["computed_usd"] - led["computed_usd"]) < 1e-6
                                  else "THE TWO SOURCES DISAGREE - neither may be used alone")
        rows.append(row)

    totals = {}
    for role in ("target", "optimizer"):
        sel = [r for r in rows if r["role"] == role]
        t = {
            "attempts": sum(r["attempts"] for r in sel),
            "retries_known": sum(r["retries"] for r in sel if isinstance(r["retries"], int)),
            "prompt_tokens": sum(r["prompt_tokens"] for r in sel),
            "completion_tokens": sum(r["completion_tokens"] for r in sel),
            "cached_tokens": sum(r["cached_tokens"] for r in sel),
            "computed_usd": round(sum(r["computed_usd"] for r in sel), 6),
            "provider_cost_usd": round(sum(r["provider_cost_usd"] for r in sel), 6),
            "unresolved_requests": sum(r.get("unresolved_requests", 0) for r in sel),
            "jobs_call_log_only": sorted(r["job"] for r in sel if r["coverage"] == "call_log_only"),
            "jobs_ledger_only": sorted(r["job"] for r in sel if r["coverage"] == "ledger_only"),
            "jobs_both": sorted(r["job"] for r in sel if r["coverage"] == "both"),
        }
        # An unresolved request may have been billed, so its reserve is charged on top as a bound.
        t["unresolved_reserve_usd"] = round(t["unresolved_requests"] * RESERVE_USD[role], 6)
        t["charged_usd_upper"] = round(t["computed_usd"] + t["unresolved_reserve_usd"], 6)
        # A provider total of 0.0 means one of two very different things: the provider reported zero, or
        # it reported nothing at all. Say which, because the second must never be read as "free".
        t["provider_cost_calls"] = sum(r["provider_cost_calls"] for r in sel)
        if t["provider_cost_calls"] == 0:
            t["provider_cost_usd"] = "unavailable"
            t["provider_cost_note"] = ("no record for this role carried the provider's own cost, so "
                                       "there is no independent figure to compare the computed charge "
                                       "against; this is NOT a report of zero cost")
        else:
            t["provider_cost_coverage"] = f"{t['provider_cost_calls']} of {t['attempts']} attempts"
            t["provider_vs_computed_ratio"] = (round(t["provider_cost_usd"] / t["computed_usd"], 4)
                                               if t["computed_usd"] else None)
        totals[role] = t
    return {"rows": rows, "totals": totals, "claim_state": ledger["claim_state"]}


def gaps(rows: list, job_catalogue: dict) -> list:
    """Jobs the catalogue says made paid requests that NEITHER source covers."""
    covered = {(r["job"], r["role"]) for r in rows}
    out = []
    for job, meta in sorted(job_catalogue.items()):
        for role in meta.get("paid_roles", []):
            if (job, role) not in covered:
                out.append({
                    "job": job, "role": role, "stage": meta.get("stage", ""),
                    "why_uncovered": meta.get("why", "no call log and no ledger record"),
                    "conservative_upper_bound": meta.get("bound", {}),
                })
    return out
