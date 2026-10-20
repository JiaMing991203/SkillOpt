"""Strict vs. qwen_compat reasoning on an already-saved ALFWorld rollout.

Re-parses the saved raw replies (``model_response``) with both parsers and
renders the reflection-prompt trajectory block with SkillOpt's own, unmodified
``fmt_minibatch_trajectories``. No inference, no API call, and the source
rollout is never modified (copies go to --out-dir).

Outputs in --out-dir:
  reflection_input_strict.txt       what the optimizer sees today
  reflection_input_qwen_compat.txt  what it would see with the compat parser
  reasoning_parser_demo.json        per-step comparison

Usage: python research/demo_reasoning_parser.py <rollout_out_root> --out-dir DIR
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PARSERS = ("strict", "qwen_compat")


def main() -> int:
    from skillopt.envs.alfworld.response_compat import extract_reasoning
    from skillopt.gradient.reflect import fmt_minibatch_trajectories

    p = argparse.ArgumentParser()
    p.add_argument("rollout_dir")
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()

    with open(os.path.join(a.rollout_dir, "results.jsonl"), encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]

    report: dict = {"episodes": []}
    for item in items:
        tid = str(item["id"])
        with open(os.path.join(a.rollout_dir, "predictions", tid, "conversation.json"), encoding="utf-8") as f:
            conv = json.load(f)
        per_parser = {name: [] for name in PARSERS}
        steps_report = []
        for step in conv:
            raw = step.get("model_response") or ""
            row = {"step": step.get("step"), "action": step.get("action"), "raw_response_chars": len(raw)}
            for name in PARSERS:
                reasoning, source = extract_reasoning(raw, name)
                per_parser[name].append(dict(step, reasoning=reasoning))
                row[f"{name}_reasoning_chars"] = len(reasoning or "")
                if name == "qwen_compat":
                    row["compat_source"] = source
                    row["compat_preview"] = (reasoning or "")[:160]
            steps_report.append(row)
        for name in PARSERS:
            d = os.path.join(a.out_dir, f"predictions_{name}", tid)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "conversation.json"), "w", encoding="utf-8") as f:
                json.dump(per_parser[name], f, ensure_ascii=False, indent=2)
        report["episodes"].append({"id": tid, "hard": item.get("hard"), "steps": steps_report})

    for name in PARSERS:
        text = fmt_minibatch_trajectories(items, os.path.join(a.out_dir, f"predictions_{name}"))
        with open(os.path.join(a.out_dir, f"reflection_input_{name}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        report[f"{name}_think_lines"] = sum(" think] " in ln for ln in text.splitlines())
        report[f"{name}_chars"] = len(text)

    with open(os.path.join(a.out_dir, "reasoning_parser_demo.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "episodes"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
