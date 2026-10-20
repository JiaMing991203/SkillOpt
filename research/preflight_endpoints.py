"""Preflight check for the two model endpoints used by the baseline.

Runs on a compute node. It does not change any SkillOpt behaviour; it calls
the same backend modules SkillOpt uses and records what the servers report.

  --optimizer   DeepSeek (openai_compatible): list models, one tiny chat call,
                record the resolved model id / fingerprint / token usage.
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


def check_optimizer(report: dict) -> bool:
    from openai import OpenAI

    base = os.environ["DEEPSEEK_BASE_URL"].rstrip("/")
    key = os.environ["OPTIMIZER_OPENAI_COMPATIBLE_API_KEY"]
    out: dict = {"base_url": base}
    report["optimizer"] = out

    models = _get_json(f"{base}/models", key)
    out["available_models"] = sorted(m.get("id") for m in models.get("data", []))
    requested = os.environ.get("OPTIMIZER_MODEL", "").strip()
    out["requested_model"] = requested
    if not requested:
        out["status"] = "SKIPPED_CHAT: OPTIMIZER_MODEL not set; choose one of available_models"
        return False

    client = OpenAI(base_url=base, api_key=key, timeout=120)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=requested,
        messages=[{"role": "user", "content": "Reply with exactly the word OK."}],
        max_tokens=512,
    )
    msg = resp.choices[0].message
    out.update({
        "latency_s": round(time.time() - t0, 2),
        "resolved_model": resp.model,
        "system_fingerprint": getattr(resp, "system_fingerprint", None),
        "finish_reason": resp.choices[0].finish_reason,
        "content": msg.content,
        "has_reasoning_content": bool(getattr(msg, "reasoning_content", None)),
        "usage": resp.usage.model_dump() if resp.usage else None,
    })
    out["status"] = "PASS" if (msg.content or "").strip() else "FAIL: empty content"
    return out["status"] == "PASS"


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
