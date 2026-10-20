"""Resolve research configs exactly as scripts/train.py reads them (no run).

For each config: the flattened key/value set (load_config + flatten_config),
the keys that differ from BASE, the train batch the dataloader would draw at
epoch 1 / step 1, and the selection (val) task ids the gate would use.

Usage: python research/resolve_config.py BASE CFG [CFG ...] --out F
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from research.analyze_rollout import planned_train_batch  # noqa: E402
from skillopt.config import flatten_config, load_config  # noqa: E402
from skillopt.envs.alfworld.dataloader import ALFWorldDataLoader  # noqa: E402


def resolve(path: str) -> dict:
    return flatten_config(load_config(os.path.join(REPO, path)))


def selection_ids(cfg: dict) -> list[str]:
    dl = ALFWorldDataLoader(split_dir=os.path.join(REPO, cfg["split_dir"]), split_mode=cfg.get("split_mode", "split_dir"),
                            seed=cfg.get("seed", 42), train_size=cfg.get("train_size", 0))
    dl.setup(cfg)
    items = dl.val_items
    n = int(cfg.get("sel_env_num") or 0)
    return [i["id"] for i in (items[:n] if n > 0 else items)]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("configs", nargs="+")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    base = resolve(a.base)
    report = {"base": a.base, "configs": {}}
    for path in [a.base] + a.configs:
        cfg = resolve(path)
        diff = {k: [base.get(k), cfg.get(k)] for k in sorted(set(base) | set(cfg)) if base.get(k) != cfg.get(k)}
        entry = {"diff_vs_base": diff, "resolved": dict(sorted(cfg.items()))}
        if cfg.get("env") == "alfworld" and cfg.get("split_dir"):
            entry["planned_train_batch"] = planned_train_batch(path)
            entry["selection_task_ids"] = selection_ids(cfg)
        report["configs"][path] = entry
        print(f"{path}: diff_vs_base={json.dumps(diff, default=str)}")
        if "planned_train_batch" in entry:
            print(f"  train batch: {[t['id'] for t in entry['planned_train_batch']['tasks']]}")
            print(f"  selection tasks: {len(entry['selection_task_ids'])}")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
