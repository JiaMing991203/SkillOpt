"""Preflight check for the two model endpoints used by the baseline.

Runs on a compute node. It does not change any SkillOpt behaviour; it calls
the same backend modules SkillOpt uses and records what the servers report.

  --optimizer   DeepSeek through SkillOpt's openai_compatible backend: list
                models, then two tiny calls (plain text; JSON parsed by
                SkillOpt's extract_json). Records resolved model id, whether
                reasoning_content came back and whether SkillOpt excludes it,
                full token usage, and estimated cost.
  --target      Job-local vLLM (qwen_chat): list models, then send one real
                ALFWorld-format prompt and check that an <action> tag comes back.

Writes a JSON report to --out. API keys are read from the environment and are
never written to the report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _get_json(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# USD per 1M tokens for deepseek-flash, as listed on
# https://api-docs.deepseek.com/quick_start/pricing (read 2026-10-20).
# Peak = 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.
DEEPSEEK_FLASH_PRICES = {
    "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.6},
    "peak": {"cache_hit": 0.006, "cache_miss": 0.3, "output": 1.2},
}


def _deepseek_cost(usage: dict) -> dict:
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or (int(usage.get("prompt_tokens") or 0) - hit))
    out = int(usage.get("completion_tokens") or 0)
    return {
        tier: round((hit * p["cache_hit"] + miss * p["cache_miss"] + out * p["output"]) / 1e6, 6)
        for tier, p in DEEPSEEK_FLASH_PRICES.items()
    }


def check_optimizer(report: dict) -> bool:
    """Two tiny calls through SkillOpt's own openai_compatible backend.

    The backend is configured exactly as the trainer does it, from the research
    YAML (base URL, max_tokens) plus the requested model id. The OpenAI client
    inside the backend is wrapped so the raw server response (resolved model,
    reasoning_content, full usage) is recorded next to what SkillOpt returns.
    No call is made unless the requested model id is listed by GET /models.
    """
    from skillopt.config import flatten_config, load_config
    from skillopt.model import openai_compatible_backend as oc
    from skillopt.utils.json_utils import extract_json

    cfg = flatten_config(load_config(os.path.join(REPO, "configs/research/alfworld_qwen35_deepseek.yaml")))
    base = str(cfg["optimizer_openai_compatible_base_url"]).rstrip("/")
    key = os.environ["OPTIMIZER_OPENAI_COMPATIBLE_API_KEY"]
    out: dict = {"base_url": base, "calls": []}
    report["optimizer"] = out

    models = _get_json(f"{base}/models", key)
    out["available_models"] = sorted(m.get("id") for m in models.get("data", []))
    requested = os.environ.get("OPTIMIZER_MODEL", "").strip()
    out["requested_model"] = requested
    if not requested or requested not in out["available_models"]:
        out["status"] = f"SKIPPED_CHAT: requested model {requested!r} not in available_models (no paid call made)"
        return False

    oc.configure_openai_compatible(
        optimizer_base_url=base,
        optimizer_model=requested,
        optimizer_max_tokens=cfg.get("optimizer_openai_compatible_max_tokens"),
    )
    out["skillopt_backend_settings"] = {
        "model": oc.OPTIMIZER_CONFIG.deployment,
        "max_tokens_cap": oc.OPTIMIZER_CONFIG.max_tokens,
        "temperature": oc.OPTIMIZER_CONFIG.temperature,
        "sends_reasoning_controls": False,  # backend drops reasoning_effort; no extra_body
    }

    raw: list = []
    completions = oc._get_client("optimizer").chat.completions
    original_create = completions.create

    def recording_create(*args, **kwargs):
        resp = original_create(*args, **kwargs)
        raw.append((kwargs, resp))
        return resp

    completions.create = recording_create

    probes = [
        ("plain", "You are a helpful assistant.", "Reply with exactly the word OK."),
        ("json", "You revise skill documents. Respond with a JSON object only.",
         'Return {"edits": [{"op": "append", "content": "<one short rule>"}]} where the rule tells '
         "a household robot to open closed containers before concluding an object is absent."),
    ]
    ok = True
    for name, system, user in probes:
        raw.clear()
        t0 = time.time()
        text, usage = oc.chat_optimizer(system=system, user=user, retries=1, stage=f"preflight_{name}")
        call: dict = {
            "probe": name,
            "latency_s": round(time.time() - t0, 2),
            "skillopt_returned_text": text,
            "skillopt_recorded_usage": usage,
            "raw_captured": bool(raw),
        }
        if raw:
            kwargs, resp = raw[-1]
            msg = resp.choices[0].message
            reasoning = getattr(msg, "reasoning_content", None) or (getattr(msg, "model_extra", None) or {}).get("reasoning_content")
            usage_full = resp.usage.model_dump() if resp.usage else {}
            call.update({
                "request_fields_sent": sorted(k for k in kwargs if k != "messages"),
                "request_max_tokens": kwargs.get("max_tokens"),
                "resolved_model": resp.model,
                "system_fingerprint": getattr(resp, "system_fingerprint", None),
                "finish_reason": resp.choices[0].finish_reason,
                "raw_content": msg.content,
                "reasoning_present": bool(reasoning),
                "reasoning_chars": len(reasoning or ""),
                "reasoning_preview": (reasoning or "")[:300],
                "skillopt_text_equals_content": text == (msg.content or ""),
                "skillopt_text_contains_reasoning": bool(reasoning) and reasoning[:80] in (text or ""),
                "usage_full": usage_full,
                "estimated_cost_usd": _deepseek_cost(usage_full),
            })
        if name == "plain":
            call["status"] = "PASS" if (text or "").strip() else "FAIL: empty content"
        else:
            parsed = extract_json(text or "")
            call["parsed_by_skillopt_extract_json"] = parsed
            call["status"] = "PASS" if isinstance(parsed, dict) and parsed.get("edits") else "FAIL: no edits parsed"
        ok = ok and call["status"] == "PASS"
        out["calls"].append(call)

    completions.create = original_create
    out["thinking_active"] = any(c.get("reasoning_present") for c in out["calls"])
    out["total_estimated_cost_usd"] = {
        tier: round(sum(c.get("estimated_cost_usd", {}).get(tier, 0) for c in out["calls"]), 6)
        for tier in DEEPSEEK_FLASH_PRICES
    }
    out["status"] = "PASS" if ok else "FAIL"
    return ok


def check_target(report: dict) -> bool:
    base = os.environ["TARGET_QWEN_CHAT_BASE_URL"].rstrip("/")
    key = os.environ["TARGET_QWEN_CHAT_API_KEY"]
    out: dict = {"base_url": base}
    report["target"] = out
    out["served_models"] = [m.get("id") for m in _get_json(f"{base}/models", key).get("data", [])]

    # Exercise the exact SkillOpt code path: qwen_chat backend + ALFWorld prompt
    # template + skill injection, as run_alfworld_batch builds it.
    from skillopt.envs.alfworld.rollout import _build_skill_prompt, _extract_action
    from skillopt.envs.alfworld.vendor.alfworld_prompts import ALFWORLD_TEMPLATE_NO_HIS
    from skillopt.model import qwen_backend

    obs = (
        "-= Welcome to TextWorld, ALFRED! =-\n\nYou are in the middle of a room. Looking quickly "
        "around you, you see a cabinet 1, a countertop 1, a drawer 1, a fridge 1, and a sinkbasin 1."
        "\n\nYour task is to: put a clean apple in fridge."
    )
    admissible = "\n ".join(f"'{a}'" for a in [
        "go to cabinet 1", "go to countertop 1", "go to drawer 1", "go to fridge 1",
        "go to sinkbasin 1", "inventory", "look",
    ])
    skill = open(os.path.join(REPO, "skillopt/envs/alfworld/skills/initial.md"), encoding="utf-8").read()
    prompt = _build_skill_prompt(skill) + "\n" + ALFWORLD_TEMPLATE_NO_HIS.format(
        current_observation=obs, admissible_actions=admissible
    )
    cfg = qwen_backend.TARGET_CONFIG
    out["request_settings"] = {
        "model": cfg.deployment, "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens, "thinking_mode": cfg.thinking_mode,
    }
    t0 = time.time()
    text, usage = qwen_backend.chat_target(
        system="You are an expert agent operating in the ALFRED Embodied Environment.",
        user=prompt, max_completion_tokens=16384, retries=2, stage="preflight",
    )
    action = _extract_action(text or "")
    out.update({
        "latency_s": round(time.time() - t0, 2),
        "usage": usage,
        "response": text,
        "extracted_action": action,
        "action_is_admissible": action in admissible,
    })
    out["status"] = "PASS" if action else "FAIL: no <action> tag (rollout would substitute 'look')"
    return action is not None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--optimizer", action="store_true")
    p.add_argument("--target", action="store_true")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    report: dict = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    ok = True
    for flag, fn, name in ((a.optimizer, check_optimizer, "optimizer"), (a.target, check_target, "target")):
        if not flag:
            continue
        try:
            ok = fn(report) and ok
        except Exception as e:  # noqa: BLE001 - record and keep going
            report.setdefault(name, {})["status"] = f"ERROR: {type(e).__name__}: {e}"
            ok = False
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
