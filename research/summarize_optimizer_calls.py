"""Summarize the optimizer call log of a training run (no API calls).

Inputs
  optimizer_calls.jsonl  one line per attempt, written by the openai_compatible
                         backend when OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG is set
  --job-log F            the Slurm job log; with OPENAI_LOG=debug it holds the
                         OpenAI SDK's own per-HTTP-request lines, including the
                         SDK's internal retries that the backend never sees
  --skillopt-out D       the trainer's out_root, to cross-check token totals
                         against summary.json -> token_summary

Per attempt: stage, outcome, latency, resolved model, finish reason, token
usage (incl. DeepSeek cache hit/miss and reasoning tokens), estimated cost,
and the parse outcome under the same rule the calling SkillOpt code applies
(patch mode):
  analyst  extract_json(content) has a "patch" key      (gradient/reflect.py)
  merge    extract_json(content)["edits"] is a list of dicts (gradient/aggregate.py)
  ranking  extract_json(content) has "selected_indices"  (optimizer/clip.py)

Usage: python research/summarize_optimizer_calls.py <calls.jsonl> --out F
           [--job-log job.log] [--skillopt-out <out_root>]
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from research.preflight_endpoints import DEEPSEEK_FLASH_PRICES, _deepseek_cost  # noqa: E402
from skillopt.optimizer.update_modes import payload_key  # noqa: E402
from skillopt.utils import extract_json  # noqa: E402

EDITS = payload_key("patch")


def _tier(started_utc: str) -> str:
    """DeepSeek peak hours: 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday."""
    t = datetime.datetime.fromisoformat(started_utc)
    peak = t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)
    return "peak" if peak else "off_peak"


def parse_outcome(stage: str, content: str | None) -> dict:
    parsed = extract_json(content or "")
    if stage == "analyst":
        ok = bool(parsed) and "patch" in parsed
        patch = parsed.get("patch") if ok and isinstance(parsed.get("patch"), dict) else {}
        return {"ok": ok, "rule": "has 'patch'", "edits_proposed": len(patch.get(EDITS) or []) if ok else None}
    if stage == "merge":
        items = parsed.get(EDITS) if isinstance(parsed, dict) else None
        ok = isinstance(items, list) and all(isinstance(e, dict) for e in items)
        return {"ok": ok, "rule": f"'{EDITS}' is a list of dicts", "edits_merged": len(items) if ok else None}
    if stage == "ranking":
        ok = bool(parsed) and "selected_indices" in parsed
        return {"ok": ok, "rule": "has 'selected_indices'",
                "selected_indices": parsed.get("selected_indices") if ok else None}
    return {"ok": parsed is not None, "rule": "any JSON object"}


def sdk_http_lines(job_log: str) -> dict:
    """Per-HTTP-request evidence from the OpenAI SDK debug log (content-free lines)."""
    counts: dict = {"http_responses": collections.Counter(), "sdk_retries": 0, "request_ids": 0,
                    "exceptions": collections.Counter(), "http_requests_sent": 0}
    pat_resp = re.compile(r"openai\._base_client:\d+ - DEBUG\] HTTP Response: \S+ (\d{3})")
    pat_exc = re.compile(r"openai\._base_client:\d+ - DEBUG\] Encountered (?:a timeout exception|exception|an HTTP status error): (\S+)")
    with open(job_log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "openai._base_client" not in line:
                continue
            if m := pat_resp.search(line):
                counts["http_responses"][m.group(1)] += 1
            elif m := pat_exc.search(line):
                counts["exceptions"][m.group(1)] += 1
            elif "Sending HTTP Request" in line:
                counts["http_requests_sent"] += 1
            elif "Retrying request in" in line:
                counts["sdk_retries"] += 1
            elif "request_id:" in line:
                counts["request_ids"] += 1
    counts["http_responses"] = dict(counts["http_responses"])
    counts["exceptions"] = dict(counts["exceptions"])
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("calls")
    p.add_argument("--out", required=True)
    p.add_argument("--job-log")
    p.add_argument("--skillopt-out")
    a = p.parse_args()

    rows = []
    with open(a.calls, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    for i, r in enumerate(records):
        resp = r.get("response") or {}
        usage = resp.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        msgs = (r.get("request") or {}).get("messages") or []
        row = {
            "i": i, "started_utc": r["started_utc"], "stage": r["stage"], "attempt": r["attempt"],
            "outcome": r["outcome"], "latency_s": r["latency_s"],
            "prompt_chars": sum(len(str(m.get("content") or "")) for m in msgs),
            "max_tokens": (r.get("request") or {}).get("max_tokens"),
        }
        if r["outcome"] == "ok":
            row.update({
                "model": resp.get("model"), "finish_reason": resp.get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
                "content_chars": len(resp.get("content") or ""),
                "reasoning_chars": len(resp.get("reasoning_content") or ""),
                "parse": parse_outcome(r["stage"], resp.get("content")),
                "price_tier": _tier(r["started_utc"]),
                "estimated_cost_usd": _deepseek_cost(usage),
            })
            row["estimated_cost_usd_at_call_time"] = row["estimated_cost_usd"][row["price_tier"]]
        else:
            row["error"] = r.get("error")
        rows.append(row)

    by_stage: dict = {}
    for row in rows:
        s = by_stage.setdefault(row["stage"], {"attempts": 0, "ok": 0, "errors": 0, "parse_ok": 0,
                                               "prompt_tokens": 0, "completion_tokens": 0,
                                               "reasoning_tokens": 0, "estimated_cost_usd_at_call_time": 0.0})
        s["attempts"] += 1
        if row["outcome"] == "ok":
            s["ok"] += 1
            s["parse_ok"] += bool(row["parse"]["ok"])
            for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
                s[k] += int(row.get(k) or 0)
            s["estimated_cost_usd_at_call_time"] = round(s["estimated_cost_usd_at_call_time"]
                                                         + row["estimated_cost_usd_at_call_time"], 6)
        else:
            s["errors"] += 1
    ok_rows = [r for r in rows if r["outcome"] == "ok"]
    report = {
        "calls_file": os.path.basename(a.calls),
        "prices_usd_per_1m": DEEPSEEK_FLASH_PRICES,
        "totals": {
            "attempts": len(rows), "ok": len(ok_rows), "errors": len(rows) - len(ok_rows),
            "parse_failures": sum(not r["parse"]["ok"] for r in ok_rows),
            "finish_reason_length": sum(r.get("finish_reason") == "length" for r in ok_rows),
            "resolved_models": sorted({r.get("model") for r in ok_rows if r.get("model")}),
            "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in ok_rows),
            "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in ok_rows),
            "reasoning_tokens": sum(int(r.get("reasoning_tokens") or 0) for r in ok_rows),
            "estimated_cost_usd_at_call_time": round(sum(r["estimated_cost_usd_at_call_time"] for r in ok_rows), 6),
            "estimated_cost_usd_if_all_peak": round(sum(r["estimated_cost_usd"]["peak"] for r in ok_rows), 6),
        },
        "by_stage": by_stage,
        "calls": rows,
    }
    if a.job_log and os.path.exists(a.job_log):
        report["sdk_http"] = sdk_http_lines(a.job_log)
    if a.skillopt_out:
        summary_path = os.path.join(a.skillopt_out, "summary.json")
        if os.path.exists(summary_path):
            ts = json.load(open(summary_path, encoding="utf-8")).get("token_summary") or {}
            report["crosscheck_token_summary"] = {
                stage: {"trainer": ts.get(stage), "call_log_ok": {k: by_stage.get(stage, {}).get(k)
                                                                   for k in ("ok", "prompt_tokens", "completion_tokens")}}
                for stage in sorted(set(by_stage) | {k for k in ts if k in ("analyst", "merge", "ranking")})
            }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: report[k] for k in ("totals", "by_stage")}, indent=1))
    if "sdk_http" in report:
        print("sdk_http:", json.dumps(report["sdk_http"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
