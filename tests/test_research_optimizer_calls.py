"""research/summarize_optimizer_calls.py: parse outcomes, costs, SDK HTTP lines."""
import json
import os
import subprocess
import sys

from research.summarize_optimizer_calls import parse_outcome

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parse_rules_follow_the_calling_code():
    assert parse_outcome("analyst", '{"patch": {"edits": [{"op": "add"}, {"op": "add"}]}}') == \
        {"ok": True, "rule": "has 'patch'", "edits_proposed": 2}
    assert parse_outcome("analyst", "I could not decide.")["ok"] is False
    assert parse_outcome("analyst", '{"edits": []}')["ok"] is False  # reflect.py requires "patch"
    assert parse_outcome("merge", '{"reasoning": "r", "edits": [{"op": "add"}]}')["edits_merged"] == 1
    assert parse_outcome("merge", '{"edits": ["not a dict"]}')["ok"] is False
    assert parse_outcome("ranking", '{"selected_indices": [2, 0]}')["selected_indices"] == [2, 0]
    assert parse_outcome("ranking", '{"order": [2, 0]}')["ok"] is False


def _rec(stage, attempt, outcome, started, content=None, usage=None, error=None):
    r = {"schema": "lso.openai_compatible_call.v1", "started_utc": started, "latency_s": 1.5, "role": "optimizer",
         "stage": stage, "attempt": attempt, "max_attempts": 3, "outcome": outcome,
         "request": {"model": "deepseek-flash", "max_tokens": 16384,
                     "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U" * 10}]}}
    if outcome == "ok":
        r["response"] = {"model": "DeepSeek-V4.1-Flash-1017", "finish_reason": "stop", "content": content,
                         "reasoning_content": "thinking", "usage": usage}
    else:
        r["error"] = error
    return r


def test_summary_end_to_end(tmp_path):
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "prompt_cache_hit_tokens": 0,
             "prompt_cache_miss_tokens": 1_000_000, "completion_tokens_details": {"reasoning_tokens": 600_000}}
    recs = [
        _rec("analyst", 1, "error", "2026-10-21T02:00:00+00:00", error={"type": "APITimeoutError", "message": "t"}),
        _rec("analyst", 2, "ok", "2026-10-21T02:01:00+00:00", '{"patch": {"edits": [{"op": "add"}]}}', usage),  # Wednesday, peak
        _rec("analyst", 1, "ok", "2026-10-21T12:00:00+00:00", "no json here", usage),  # off-peak
    ]
    calls = tmp_path / "optimizer_calls.jsonl"
    calls.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    log = tmp_path / "job.log"
    log.write_text(
        "[2026-10-21 02:00:00 - openai._base_client:1144 - DEBUG] Sending HTTP Request: POST\n"
        "[2026-10-21 02:00:09 - openai._base_client:1154 - DEBUG] Encountered a timeout exception: ReadTimeout\n"
        "[2026-10-21 02:00:09 - openai._base_client:1237 - INFO] Retrying request in 0.4 seconds (retry 1 of 2)\n"
        "[2026-10-21 02:01:00 - openai._base_client:1185 - DEBUG] HTTP Response: POST 200\n"
        "[2026-10-21 02:01:00 - openai._base_client:1190 - DEBUG] request_id: None\n"
        "[STEP 1/1] unrelated trainer line\n")
    out = tmp_path / "summary.json"
    subprocess.run([sys.executable, os.path.join(REPO, "research", "summarize_optimizer_calls.py"), str(calls), "--out", str(out),
                    "--job-log", str(log)], check=True, capture_output=True)
    rep = json.loads(out.read_text())
    t = rep["totals"]
    assert (t["attempts"], t["ok"], t["errors"], t["parse_failures"]) == (3, 2, 1, 1)
    assert t["resolved_models"] == ["DeepSeek-V4.1-Flash-1017"]
    assert t["reasoning_tokens"] == 1_200_000
    # 1M miss + 1M output: peak 0.3 + 1.2 = 1.5; off-peak 0.15 + 0.6 = 0.75
    assert [c.get("price_tier") for c in rep["calls"]] == [None, "peak", "off_peak"]
    assert t["estimated_cost_usd_at_call_time"] == 2.25
    assert t["estimated_cost_usd_if_all_peak"] == 3.0
    assert rep["by_stage"]["analyst"] == {"attempts": 3, "ok": 2, "errors": 1, "parse_ok": 1,
                                          "prompt_tokens": 2_000_000, "completion_tokens": 2_000_000,
                                          "reasoning_tokens": 1_200_000, "estimated_cost_usd_at_call_time": 2.25}
    assert rep["sdk_http"] == {"http_responses": {"200": 1}, "sdk_retries": 1, "request_ids": 1,
                               "exceptions": {"ReadTimeout": 1}, "http_requests_sent": 1}
