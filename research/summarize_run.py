"""Summarize a SkillOpt output directory without modifying it.

For every rollout (any directory with results.jsonl) it reports the native
score plus trajectory-validity counts that the rollout code otherwise hides.

Fallback detection
------------------
skillopt/envs/alfworld/rollout.py replaces the model response with one of
three fixed strings and plays ``look`` when the target call raises, returns
empty text, or has no ``<action>`` tag. The recorded ``model_response`` is
then the synthetic string, so fallbacks are identified by EXACT equality with
those strings (``FALLBACK_SENTINELS``). A genuine ``look`` keeps the model's
own text and is counted separately.

Limitations (cannot be fixed without changing the rollout code):
  * The original response (missing-tag case) and the exception (error case)
    are discarded by rollout.py; only the category is recoverable.
  * The two exception paths (backend error after its internal retries, and a
    failed worker future) write the same sentinel and cannot be told apart.
  * Retries inside the backend that eventually succeed leave no trace.
  * A model that emits a sentinel string verbatim would be misclassified.

Other counts:
  nothing_happens    ALFWorld replied "Nothing happens." (action not valid)
  format_invalid     response fails the vendored projection's validity rule
                     (no <think>...</think> or contains CJK characters); this
                     flag is computed by ALFWorld's env manager but not stored
                     or used for scoring

With --run-dir, also reads the wrapper's vLLM /metrics snapshots to report
target-model throughput for the stage.

Usage: python research/summarize_run.py <out_root> [--run-dir DIR] --out run_summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import re

FALLBACK_SENTINELS = {
    "<think>empty model response</think><action>look</action>": "empty_response",
    "<think>missing action tag</think><action>look</action>": "missing_action_tag",
    "<think>error</think><action>look</action>": "call_error",
}
_CJK = re.compile(r"[一-鿿]")


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def classify_step(step: dict) -> dict:
    resp = str(step.get("model_response") or "")
    fallback = FALLBACK_SENTINELS.get(resp)
    action = (step.get("action") or "").strip().lower()
    return {
        "fallback": fallback,
        "genuine_look": fallback is None and action == "look",
        "nothing_happens": "Nothing happens." in str(step.get("env_feedback", "")),
        "format_invalid": fallback is None and (
            "<think>" not in resp or "</think>" not in resp or bool(_CJK.search(resp))
        ),
    }


def summarize_rollout(rollout_dir: str) -> dict:
    episodes = []
    with open(os.path.join(rollout_dir, "results.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    for row in rows:
        conv = _load_json(os.path.join(rollout_dir, "predictions", str(row.get("id")), "conversation.json")) or []
        cls = [classify_step(s) for s in conv]
        fb: dict[str, int] = {}
        for c in cls:
            if c["fallback"]:
                fb[c["fallback"]] = fb.get(c["fallback"], 0) + 1
        episodes.append({
            "id": row.get("id"),
            "task_type": row.get("task_type"),
            "hard": row.get("hard"),
            "n_turns": row.get("n_turns"),
            "fail_reason": row.get("fail_reason"),
            "trajectory_steps": len(conv),
            "fallback_actions": sum(fb.values()),
            "fallback_by_kind": fb,
            "genuine_look": sum(c["genuine_look"] for c in cls),
            "nothing_happens": sum(c["nothing_happens"] for c in cls),
            "format_invalid": sum(c["format_invalid"] for c in cls),
        })
    n = len(episodes)

    def total(key):
        return sum(e[key] for e in episodes)

    return {
        "n_episodes": n,
        "hard_mean": (sum(e["hard"] or 0 for e in episodes) / n) if n else None,
        "total_steps": total("trajectory_steps"),
        "fallback_actions": total("fallback_actions"),
        "genuine_look": total("genuine_look"),
        "nothing_happens": total("nothing_happens"),
        "format_invalid": total("format_invalid"),
        "episodes": episodes,
    }


def _prom_totals(path: str) -> dict[str, float]:
    """Sum every sample of each metric name across labels."""
    totals: dict[str, float] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                name_labels, _, value = line.rstrip().rpartition(" ")
                name = name_labels.split("{", 1)[0]
                try:
                    totals[name] = totals.get(name, 0.0) + float(value)
                except ValueError:
                    pass
    except OSError:
        return {}
    return totals


def vllm_throughput(run_dir: str) -> dict | None:
    before = _prom_totals(os.path.join(run_dir, "vllm_metrics_before.prom"))
    after = _prom_totals(os.path.join(run_dir, "vllm_metrics_after.prom"))
    if not before or not after:
        return None
    try:
        t0 = float(open(os.path.join(run_dir, "vllm_metrics_before.time")).read())
        t1 = float(open(os.path.join(run_dir, "vllm_metrics_after.time")).read())
    except (OSError, ValueError):
        return None

    def d(name):
        return after.get(name, 0.0) - before.get(name, 0.0)

    wall = t1 - t0
    reqs = d("vllm:request_success_total")
    gen = d("vllm:generation_tokens_total")
    prompt = d("vllm:prompt_tokens_total")
    lat_n = d("vllm:e2e_request_latency_seconds_count")
    return {
        "wall_seconds": round(wall, 1),
        "requests": reqs,
        "prompt_tokens": prompt,
        "generation_tokens": gen,
        "mean_prompt_tokens_per_request": round(prompt / reqs, 1) if reqs else None,
        "mean_generation_tokens_per_request": round(gen / reqs, 1) if reqs else None,
        "mean_e2e_latency_s": round(d("vllm:e2e_request_latency_seconds_sum") / lat_n, 3) if lat_n else None,
        "generation_tokens_per_s": round(gen / wall, 1) if wall > 0 else None,
        "requests_per_s": round(reqs / wall, 3) if wall > 0 else None,
    }


def gpu_usage(run_dir: str) -> dict | None:
    """Peak/mean from the 5-second nvidia-smi samples (MiB, %)."""
    mem, util, total = [], [], None
    try:
        with open(os.path.join(run_dir, "gpu_usage.csv"), encoding="utf-8") as f:
            for line in f:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue
                try:
                    mem.append(float(parts[1])); total = float(parts[2]); util.append(float(parts[3]))
                except ValueError:
                    pass
    except OSError:
        return None
    if not mem:
        return None
    return {
        "samples": len(mem),
        "peak_memory_used_mib": max(mem),
        "memory_total_mib": total,
        "peak_utilization_pct": max(util),
        "mean_utilization_pct": round(sum(util) / len(util), 1),
    }


def vllm_http_statuses(run_dir: str) -> dict | None:
    """Count chat-completion HTTP status codes in vLLM's access log."""
    counts: dict[str, int] = {}
    pat = re.compile(r'"POST /v1/chat/completions HTTP/[\d.]+" (\d{3})')
    try:
        with open(os.path.join(run_dir, "vllm.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    except OSError:
        return None
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("out_root")
    p.add_argument("--run-dir", default="")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    root = os.path.abspath(a.out_root)

    rollouts = {}
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            if "results.jsonl" in files:
                rollouts[os.path.relpath(dirpath, root)] = summarize_rollout(dirpath)

    history = _load_json(os.path.join(root, "history.json"))
    gate = []
    if isinstance(history, list):
        for rec in history:
            gate.append({k: rec.get(k) for k in (
                "step", "epoch", "action", "edit_budget", "rollout_hard", "rollout_n",
                "n_patches", "n_edits_merged", "n_edits_ranked", "edit_apply_summary",
                "selection_hard", "selection_soft", "candidate_gate_score",
                "current_score", "best_score", "tokens", "wall_time_s",
            ) if k in rec})

    summary = _load_json(os.path.join(root, "summary.json")) or _load_json(os.path.join(root, "eval_summary.json"))
    report = {
        "out_root": root,
        "rollouts": rollouts,
        "gate_decisions": gate,
        "token_summary": (summary or {}).get("token_summary"),
        "skills": sorted(os.listdir(os.path.join(root, "skills"))) if os.path.isdir(os.path.join(root, "skills")) else [],
        "vllm_throughput": vllm_throughput(a.run_dir) if a.run_dir else None,
        "vllm_http_statuses": vllm_http_statuses(a.run_dir) if a.run_dir else None,
        "gpu_usage": gpu_usage(a.run_dir) if a.run_dir else None,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    for name, r in rollouts.items():
        print(f"{name}: episodes={r['n_episodes']} hard={r['hard_mean']} steps={r['total_steps']} "
              f"fallback={r['fallback_actions']} genuine_look={r['genuine_look']} "
              f"nothing_happens={r['nothing_happens']} format_invalid={r['format_invalid']}")
    for g in gate:
        print("gate:", {k: g.get(k) for k in ("step", "action", "selection_hard", "current_score", "best_score")})
    for key in ("vllm_throughput", "vllm_http_statuses", "gpu_usage"):
        if report[key]:
            print(f"{key}:", report[key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
