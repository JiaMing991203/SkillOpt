"""Measure 8 concurrent ALFWorld simulators without any model (CPU only).

Builds the vendored ALFWorld env manager for the tasks in
<split-dir>/train/items.json (one worker process per task, as in a rollout),
resets, then steps every env with a seeded random ADMISSIBLE action for
--steps steps. Times reset and each lockstep env.step. Memory and CPU come
from research/resource_sampler.py running alongside (job cgroup).

Uses SkillOpt's own build_alfworld_env; no agent, no API, no scoring.
Usage: python research/measure_alfworld_envs.py --split-dir D --steps 30 --out F
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main() -> int:
    from skillopt.envs.alfworld.rollout import build_alfworld_env

    p = argparse.ArgumentParser()
    p.add_argument("--split-dir", required=True)
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with open(os.path.join(a.split_dir, "train", "items.json"), encoding="utf-8") as f:
        items = json.load(f)
    gamefiles = [it["gamefile"] for it in items]
    rng = random.Random(a.seed)

    t0 = time.monotonic()
    env = build_alfworld_env(env_num=len(gamefiles), eval_dataset="train", seed=42,
                             is_train=False, specific_gamefiles=gamefiles)
    t_build = time.monotonic() - t0
    t0 = time.monotonic()
    obs, infos = env.reset({})
    t_reset = time.monotonic() - t0

    step_times, dones_seen = [], [False] * len(gamefiles)
    for _ in range(a.steps):
        admissible = env.envs.get_admissible_commands
        actions = [f"<think>measure</think><action>{rng.choice([c for c in adm if c != 'help'])}</action>"
                   for adm in admissible]
        t0 = time.monotonic()
        obs, rewards, dones, infos = env.step(actions)
        step_times.append(time.monotonic() - t0)
        dones_seen = [d or bool(x) for d, x in zip(dones_seen, dones)]
    close = getattr(env.envs, "close", None)
    if callable(close):
        close()

    st = sorted(step_times)
    out = {
        "n_envs": len(gamefiles),
        "task_ids": [it["id"] for it in items],
        "build_s": round(t_build, 2),
        "reset_s": round(t_reset, 2),
        "steps": len(step_times),
        "step_s_mean": round(sum(st) / len(st), 4),
        "step_s_median": round(st[len(st) // 2], 4),
        "step_s_max": round(st[-1], 4),
        "envs_done_by_random_actions": sum(dones_seen),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
