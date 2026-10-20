"""Offline analysis of a traced ALFWorld rollout (no inference, no API calls).

Inputs: <out_root>/results.jsonl, predictions/<id>/{conversation,trace}.json.
Reports (JSON):
  output_tokens        completion-token distribution (from trace call.usage)
  action_position      where the first <action> sits in non-fallback replies
  capped               every reply that hit --max-tokens: episode outcome,
                       fallback or not, action tags and their positions
  walltime             lockstep critical path: per step the slowest active
                       request; time attributable to capped requests;
                       remainder; non-request overhead
  by_outcome           long generations in successful vs failed episodes
  reflection_prompts   EXACT analyst prompts SkillOpt would build (captured
                       by replacing chat_optimizer with a recorder) for the
                       observed failure / success groups, under the strict and
                       qwen_compat parsers
  planned_train_batch  the train tasks the smoke config's dataloader would
                       draw at epoch 1, step 1 (seed from the config)

Usage: python research/analyze_rollout.py <out_root> --out F [--max-tokens 8000]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SENTINEL_PREFIX = ("<think>empty model response</think>", "<think>missing action tag</think>", "<think>error</think>")


def quantiles(xs: list[float]) -> dict:
    s = sorted(xs)
    if not s:
        return {}
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]  # noqa: E731
    return {"n": len(s), "min": s[0], "p25": q(.25), "median": q(.5), "p75": q(.75), "p90": q(.9),
            "p99": q(.99), "max": s[-1], "mean": round(sum(s) / len(s), 1)}


def load(out_root: str):
    with open(os.path.join(out_root, "results.jsonl"), encoding="utf-8") as f:
        results = [json.loads(line) for line in f if line.strip()]
    eps = []
    for r in results:
        d = os.path.join(out_root, "predictions", str(r["id"]))
        conv = json.load(open(os.path.join(d, "conversation.json"), encoding="utf-8"))
        trace = json.load(open(os.path.join(d, "trace.json"), encoding="utf-8"))
        eps.append((r, conv, trace))
    return eps


def capture_prompts(items: list[dict], out_root: str, parser: str, skill: str, kind: str) -> dict:
    """Build the analyst prompt exactly as SkillOpt would, without calling the API."""
    from skillopt.envs.alfworld.response_compat import extract_reasoning
    from skillopt.gradient import reflect

    tmp = tempfile.mkdtemp()
    try:
        for it in items:
            conv = json.load(open(os.path.join(out_root, "predictions", str(it["id"]), "conversation.json"),
                                  encoding="utf-8"))
            conv = [dict(s, reasoning=extract_reasoning(s.get("model_response") or "", parser)[0]) for s in conv]
            os.makedirs(os.path.join(tmp, str(it["id"])))
            json.dump(conv, open(os.path.join(tmp, str(it["id"]), "conversation.json"), "w"))
        captured = {}

        def recorder(system, user, **kwargs):
            captured.update(system=system, user=user, max_completion_tokens=kwargs.get("max_completion_tokens"),
                            retries=kwargs.get("retries"))
            return '{"patch": {"reasoning": "offline", "edits": []}}', {}

        original = reflect.chat_optimizer
        reflect.chat_optimizer = recorder
        try:
            from skillopt.prompts import load_prompt

            # Same env-specific system prompt the ALFWorld adapter passes (EnvAdapter._load_env_prompt).
            if kind == "failure":
                fn, sys_prompt = reflect.run_error_analyst_minibatch, load_prompt("analyst_error", env="alfworld")
            else:
                fn, sys_prompt = reflect.run_success_analyst_minibatch, load_prompt("analyst_success", env="alfworld")
            fn(skill_content=skill, items=items, prediction_dir=tmp, edit_budget=4, system_prompt=sys_prompt)
        finally:
            reflect.chat_optimizer = original
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    chars = len(captured.get("system", "")) + len(captured.get("user", ""))
    return {"episodes": [it["id"] for it in items], "parser": parser, "prompt_chars": chars,
            "est_tokens_chars_div4": chars // 4, "est_tokens_chars_div3": chars // 3,
            "max_completion_tokens": captured.get("max_completion_tokens"), "retries_arg": captured.get("retries")}


def planned_train_batch(config: str) -> dict:
    from skillopt.config import flatten_config, load_config
    from skillopt.envs.alfworld.dataloader import ALFWorldDataLoader

    cfg = flatten_config(load_config(os.path.join(REPO, config)))
    dl = ALFWorldDataLoader(split_dir=os.path.join(REPO, cfg["split_dir"]), split_mode=cfg.get("split_mode", "split_dir"),
                            seed=cfg.get("seed", 42), train_size=cfg.get("train_size", 0))
    dl.setup(cfg)
    batches = dl.plan_train_epoch(epoch=1, steps_per_epoch=1, accumulation=cfg.get("accumulation", 1),
                                  batch_size=cfg["batch_size"], seed=cfg.get("seed", 42))
    items = list(batches[0].payload or [])
    return {"config": config, "seed": cfg.get("seed", 42), "batch_size": cfg["batch_size"],
            "tasks": [{"id": i["id"], "task_type": i["task_type"]} for i in items]}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("out_root")
    p.add_argument("--out", required=True)
    p.add_argument("--max-tokens", type=int, default=8000)
    p.add_argument("--train-config", default="configs/research/alfworld_smoke.yaml")
    a = p.parse_args()
    eps = load(a.out_root)
    report: dict = {}

    # Output-token distribution and per-step critical path
    comp, rows = [], []
    for r, conv, trace in eps:
        for s in trace["steps"]:
            call = s.get("call") or {}
            ct = (call.get("usage") or {}).get("completion_tokens")
            if ct is None:
                continue
            comp.append(ct)
            rows.append({"task": r["id"], "hard": r["hard"], "step": s["step"], "latency": call.get("latency_s", 0.0),
                         "tokens": ct, "fallback": "unparsed_response" in call or "error" in call,
                         "raw": s.get("raw_response") or "", "unparsed": call.get("unparsed_response")})
    bins = [0, 100, 200, 500, 1000, 2000, 4000, 7999, 10**9]
    hist = collections.Counter()
    for c in comp:
        for lo, hi in zip(bins, bins[1:]):
            if lo <= c < hi:
                hist[f"{lo}-{hi - 1 if hi < 10**9 else 'max'}"] += 1
                break
    report["output_tokens"] = {"quantiles": quantiles(comp), "histogram": dict(hist),
                               "n_capped": sum(c >= a.max_tokens for c in comp)}

    # Where the action tag sits in non-fallback replies
    pos, after = [], []
    for row in rows:
        raw = row["raw"]
        if row["fallback"] or raw.startswith(SENTINEL_PREFIX) or "<action>" not in raw:
            continue
        i = raw.index("<action>")
        pos.append(round(i / max(1, len(raw)), 3))
        after.append(len(raw) - (raw.index("</action>") + len("</action>")) if "</action>" in raw else None)
    report["action_position"] = {
        "first_action_fraction_of_reply": quantiles(pos),
        "chars_after_closing_tag": quantiles([x for x in after if x is not None]),
        "replies_with_multiple_action_tags": sum(r["raw"].count("<action>") > 1 for r in rows if not r["fallback"]),
    }

    # The capped replies
    capped = []
    for row in rows:
        if row["tokens"] < a.max_tokens:
            continue
        text = row["unparsed"] if row["unparsed"] is not None else row["raw"]
        idx = [k for k in range(len(text)) if text.startswith("<action>", k)]
        capped.append({"task": row["task"], "episode_hard": row["hard"], "step": row["step"],
                       "latency_s": row["latency"], "fallback": row["fallback"], "text_chars": len(text),
                       "action_tags": len(idx), "first_action_fraction": round(idx[0] / len(text), 3) if idx else None,
                       "first_action": text[idx[0] + 8: text.find("</action>", idx[0])][:60] if idx else None})
    report["capped"] = capped

    # Lockstep walltime attribution
    by_step = collections.defaultdict(list)
    for row in rows:
        by_step[row["step"]].append(row)
    crit_total = capped_crit = 0.0
    capped_iters = 0
    per_iter = []
    for k in sorted(by_step):
        slowest = max(by_step[k], key=lambda x: x["latency"])
        crit_total += slowest["latency"]
        if slowest["tokens"] >= a.max_tokens:
            capped_crit += slowest["latency"]
            capped_iters += 1
        per_iter.append(round(slowest["latency"], 1))
    report["walltime"] = {
        "lockstep_iterations": len(by_step),
        "sum_per_iteration_max_latency_s": round(crit_total, 1),
        "iterations_whose_slowest_request_was_capped": capped_iters,
        "time_in_those_iterations_s": round(capped_crit, 1),
        "sum_of_all_capped_request_latencies_s": round(sum(c["latency_s"] for c in capped), 1),
        "remaining_critical_path_s": round(crit_total - capped_crit, 1),
        "per_iteration_max_latency_s": per_iter,
        "sum_of_all_request_latencies_s": round(sum(r["latency"] for r in rows), 1),
    }

    # Long generations by episode outcome
    out = {}
    for label, want in (("successful_episodes", 1), ("failed_episodes", 0)):
        sel = [r for r in rows if r["hard"] == want]
        out[label] = {"episodes": len({r["task"] for r in sel}), "requests": len(sel),
                      "requests_ge_2000_tokens": sum(r["tokens"] >= 2000 for r in sel),
                      "requests_capped": sum(r["tokens"] >= a.max_tokens for r in sel),
                      "completion_tokens": quantiles([r["tokens"] for r in sel])}
    report["by_outcome"] = out

    # Exact analyst prompts for the observed groups
    skill = open(os.path.join(REPO, "skillopt/envs/alfworld/skills/initial.md"), encoding="utf-8").read()
    failures = [r for r, _, _ in eps if r["hard"] == 0]
    successes = [r for r, _, _ in eps if r["hard"] == 1]
    prompts = []
    for parser in ("strict", "qwen_compat"):
        if failures:
            prompts.append(dict(capture_prompts(failures, a.out_root, parser, skill, "failure"), group="all failures"))
        if successes:
            prompts.append(dict(capture_prompts(successes, a.out_root, parser, skill, "success"), group="all successes"))
        for r in failures:  # single worst-case episodes
            prompts.append(dict(capture_prompts([r], a.out_root, parser, skill, "failure"), group=f"single {r['id']}"))
    report["reflection_prompts"] = prompts
    report["planned_train_batch"] = planned_train_batch(a.train_config)

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in ("reflection_prompts",)}, indent=1)[:4000])
    for pr in prompts:
        print(f"prompt {pr['parser']:<11} {pr['group']:<22} chars={pr['prompt_chars']:>7} ~tokens={pr['est_tokens_chars_div4']}-{pr['est_tokens_chars_div3']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
