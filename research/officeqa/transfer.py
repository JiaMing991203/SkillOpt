"""Provenance-recording transfer into the OfficeQA worktree.

Modelled on contract_work/tools/transfer.py / transfer_add.py of the learned-contract session
(same rule: copy byte-for-byte, never modify or delete a source file, refuse a differing overwrite).
Writes/extends OFFICEQA_TRANSFER_MANIFEST.json in the destination worktree.

usage: python3 -I research/officeqa/transfer.py --spec SPEC.json [--apply]
SPEC = {"src_root": "...", "dest_subdir": "...", "files": {"<rel path>": ["<role>", "<why>"]}}
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess

DEST_WT = "/Users/yaojiaming/dev/lean_skillopt/SkillOpt-officeqa"
MANIFEST = os.path.join(DEST_WT, "OFFICEQA_TRANSFER_MANIFEST.json")


def sha_file(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def git_status(root: str, rel: str) -> str:
    try:
        out = subprocess.run(["git", "-C", root, "status", "--short", "--", rel],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"unknown ({exc})"
    return out or "tracked, unmodified"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    spec = json.load(open(args.spec))
    src_root = spec["src_root"]
    dest_subdir = spec["dest_subdir"]
    rows, problems = [], []
    for rel, (role, why) in spec["files"].items():
        src = os.path.join(src_root, rel)
        dest = os.path.join(DEST_WT, dest_subdir, os.path.basename(rel))
        if not os.path.isfile(src):
            problems.append(f"missing source: {rel}")
            continue
        s = sha_file(src)
        row = {
            "path": rel,
            "dest": os.path.relpath(dest, DEST_WT),
            "role": role,
            "why": why,
            "source_status": git_status(src_root, rel),
            "source_sha256": s,
            "bytes": os.path.getsize(src),
            "source_mtime": datetime.datetime.fromtimestamp(os.path.getmtime(src)).isoformat(timespec="seconds"),
        }
        if os.path.exists(dest):
            if sha_file(dest) == s:
                row["action"] = "verified, already present"
            else:
                row["action"] = "REFUSED: destination differs"
                problems.append(f"destination differs: {row['dest']}")
                rows.append(row)
                continue
        elif args.apply:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            row["action"] = "copied"
        else:
            row["action"] = "would copy (dry run)"
        if os.path.exists(dest):
            row["dest_sha256"] = sha_file(dest)
        rows.append(row)

    entry = {
        "schema": "officeqa.transfer_manifest.v1",
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_tree": src_root,
        "source_branch": subprocess.run(["git", "-C", src_root, "branch", "--show-current"],
                                        capture_output=True, text=True).stdout.strip(),
        "source_head": subprocess.run(["git", "-C", src_root, "rev-parse", "HEAD"],
                                      capture_output=True, text=True).stdout.strip(),
        "dest_worktree": DEST_WT,
        "dest_branch": subprocess.run(["git", "-C", DEST_WT, "branch", "--show-current"],
                                      capture_output=True, text=True).stdout.strip(),
        "tool": {"path": os.path.abspath(__file__), "sha256": sha_file(os.path.abspath(__file__))},
        "rule": ("copy only the listed files byte-for-byte; never modify or delete source files; refuse overwrites. "
                 "Transferred files stay ALFWorld-domain references and are not edited here."),
        "files": rows,
        "problems": problems,
    }
    print(json.dumps(entry, indent=1))
    if args.apply:
        existing = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {"transfers": []}
        existing.setdefault("transfers", []).append(entry)
        with open(MANIFEST, "w") as f:
            json.dump(existing, f, indent=1)
        print(f"\nmanifest updated: {MANIFEST}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
