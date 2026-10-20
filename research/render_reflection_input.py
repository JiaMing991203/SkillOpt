"""Render the trajectory text SkillOpt's reflection step would send to the optimizer.

Uses SkillOpt's own, unmodified ``fmt_minibatch_trajectories`` on a saved
rollout directory (``results.jsonl`` + ``predictions/<id>/conversation.json``),
so the output is exactly the trajectory block of the analyst prompt. No model
or API call is made. Diagnostic only: shows what information reaches the
optimizer (e.g. whether agent reasoning is present).

Usage: python research/render_reflection_input.py <rollout_dir> --out preview.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main() -> int:
    from skillopt.gradient.reflect import fmt_minibatch_trajectories

    p = argparse.ArgumentParser()
    p.add_argument("rollout_dir")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with open(os.path.join(a.rollout_dir, "results.jsonl"), encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]
    text = fmt_minibatch_trajectories(items, os.path.join(a.rollout_dir, "predictions"))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    lines = text.splitlines()
    print(f"reflection trajectory block: {len(lines)} lines, {len(text)} chars; "
          f"think lines: {sum(' think] ' in ln for ln in lines)}, "
          f"action lines: {sum(' action] ' in ln for ln in lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
