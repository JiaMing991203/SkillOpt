"""Build a split_dir containing only a fixed list of TRAIN-split tasks.

Reads the official manifest ``data/alfworld_path_split/train/items.json`` and
writes ``<out>/train/items.json`` with exactly the listed task ids, in list
order. ``<out>/val`` and ``<out>/test`` are written as EMPTY lists, so a run
using this split_dir cannot evaluate selection or held-out tasks by accident.

Usage: python research/make_task_subset.py --ids-file F --out DIR
"""
from __future__ import annotations

import argparse
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIN_ITEMS = os.path.join(REPO, "data", "alfworld_path_split", "train", "items.json")


def read_ids(path: str) -> list[str]:
    ids = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    return ids


def build_subset(ids: list[str], train_items: list[dict]) -> list[dict]:
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task ids in subset list")
    by_id = {item["id"]: item for item in train_items}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"ids not in the train split: {missing}")
    return [by_id[i] for i in ids]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ids-file", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with open(TRAIN_ITEMS, encoding="utf-8") as f:
        subset = build_subset(read_ids(a.ids_file), json.load(f))
    for split, items in (("train", subset), ("val", []), ("test", [])):
        os.makedirs(os.path.join(a.out, split), exist_ok=True)
        with open(os.path.join(a.out, split, "items.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    print(f"subset: {len(subset)} train tasks -> {a.out} "
          f"({', '.join(sorted({i['task_type'] for i in subset}))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
