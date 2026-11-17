#!/usr/bin/env python3
"""Freeze the skills a held-out evaluation will score, by digest, BEFORE any of it is submitted.

WHY. The held-out split is the pre-registered primary result and it is read ONCE. That only means something if
what was scored can be identified afterwards, and if nothing moved between the decision to score it and the
scoring. Two things could move without anyone noticing:

  * A skill file could be rewritten. `best_skill.md` and `skills/skill_v*.md` live in the arm's run directory,
    which a resumed or re-run trainer job writes to. The recovery of the starved arm-epochs rewrote exactly
    those files on purpose. A held-out number reported against "arm B's selected skill" is meaningless if that
    file is not the one that was scored.

  * The SET could change. Which skills are distinct is itself a result - in the A40 two-arm run every candidate
    was rejected and both arms' final skills came out byte-identical to S0, collapsing five requested skills to
    one. Recording the set, with digests, is what makes "2 distinct by digest, 1 by advice" checkable rather
    than asserted.

So: `freeze` records every skill in the evaluation set with its sha256, its byte count, the role it plays
(S0 / validation_selected / last), the arm it came from, and a digest over the whole record. `verify` checks
every one of them again and refuses on any mismatch. `oqa_heldout_submit.sh` calls `verify` before it submits,
so a moved skill stops the evaluation instead of quietly changing what it measures.

ROLES, and why only one of them is a comparison:

  S0                   the shared starting skill, scored once
  validation_selected  each arm's `best_skill.md` - what that arm's own validation gate chose. THE PRIMARY
                       COMPARISON is S0 against these.
  last                 each arm's highest-numbered `skills/skill_v*.md` - what the arm happened to be holding
                       when it stopped, which its gate may well have REJECTED. EXPLORATORY: a number from one
                       of these is not a comparison result.

usage:
  python3 -I research/officeqa/oqa_freeze_skills.py freeze --campaign <root> \\
      --skill-init skillopt/envs/officeqa/skills/initial.md --out <root>/heldout/SKILLS_FROZEN.json
  python3 -I research/officeqa/oqa_freeze_skills.py verify --frozen <root>/heldout/SKILLS_FROZEN.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

RULE = "OQA-FREEZE-1"
ARMS = ("A", "B", "C")
# Marker blocks the trainer injects into a skill. Two skills that differ ONLY inside these carry the same
# advice to the actor, which is why "distinct by digest" and "distinct by advice" are different counts.
_MARKERS = re.compile(
    r"\n*<!--\s*(SLOW_UPDATE|META_SKILL|APPENDIX)\s*-->.*?<!--\s*/\1\s*-->\n*",
    re.DOTALL,
)
_HEAD = re.compile(r"\n*##+\s*(SLOW_UPDATE|META_SKILL|APPENDIX)\s*\n.*?(?=\n##\s|\Z)", re.DOTALL)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def substantive(text: str) -> str:
    """The skill with the injected marker blocks stripped: what the actor is actually advised to do."""
    out = _MARKERS.sub("\n", text)
    out = _HEAD.sub("\n", out)
    return "\n".join(line.rstrip() for line in out.strip().splitlines() if line.strip())


def last_skill(arm_root: str) -> str | None:
    d = os.path.join(arm_root, "skills")
    best, best_n = None, -1
    for name in os.listdir(d) if os.path.isdir(d) else []:
        m = re.fullmatch(r"skill_v(\d+)\.md", name)
        if m and int(m.group(1)) > best_n:
            best, best_n = os.path.join(d, name), int(m.group(1))
    return best


def collect(campaign: str, skill_init: str) -> list[dict]:
    entries: list[dict] = []

    def add(role: str, arm: str | None, path: str) -> None:
        if not path or not os.path.isfile(path):
            entries.append({"role": role, "arm": arm, "path": path, "exists": False})
            return
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", "replace")
        entries.append({
            "role": role, "arm": arm, "path": os.path.relpath(path, campaign)
            if path.startswith(campaign) else path,
            "abs_path": os.path.abspath(path), "exists": True,
            "sha256": _sha(raw), "bytes": len(raw),
            "advice_sha256": _sha(substantive(text).encode("utf-8")),
            "advice_bytes": len(substantive(text).encode("utf-8")),
        })

    add("S0", None, skill_init)
    for arm in ARMS:
        root = os.path.join(campaign, f"arm_{arm}")
        if not os.path.isdir(root):
            continue
        add("validation_selected", arm, os.path.join(root, "best_skill.md"))
        add("last", arm, last_skill(root) or "")
    return entries


def freeze(campaign: str, skill_init: str) -> dict:
    entries = collect(campaign, skill_init)
    present = [e for e in entries if e.get("exists")]
    by_digest: dict[str, list[str]] = {}
    by_advice: dict[str, list[str]] = {}
    for e in present:
        who = f"{e['role']}{'/' + e['arm'] if e['arm'] else ''}"
        by_digest.setdefault(e["sha256"], []).append(who)
        by_advice.setdefault(e["advice_sha256"], []).append(who)
    primary = [e for e in present if e["role"] in ("S0", "validation_selected")]
    primary_digests = sorted({e["sha256"] for e in primary})
    doc = {
        "rule": RULE,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campaign": campaign, "skill_init": skill_init,
        "entries": entries,
        "requested": len(present),
        "distinct_by_digest": len(by_digest),
        "distinct_by_advice": len(by_advice),
        "by_digest": by_digest, "by_advice": by_advice,
        "primary_distinct_by_digest": len(primary_digests),
        "primary_distinct_by_advice": len({e["advice_sha256"] for e in primary}),
        "missing": [e for e in entries if not e.get("exists")],
        "note": ("PRIMARY = S0 and each arm's validation_selected skill. A `last` skill is EXPLORATORY: its "
                 "own gate may have rejected it, so a number from it is not a comparison result."),
    }
    doc["record_sha256"] = _sha(json.dumps(
        {k: v for k, v in doc.items() if k not in ("utc", "record_sha256")},
        sort_keys=True).encode("utf-8"))
    return doc


def verify(frozen_path: str) -> dict:
    with open(frozen_path, encoding="utf-8") as f:
        doc = json.load(f)
    problems: list[str] = []
    checked = 0
    for e in doc.get("entries", []):
        if not e.get("exists"):
            continue
        path = e.get("abs_path") or e.get("path")
        who = f"{e['role']}{'/' + e['arm'] if e.get('arm') else ''}"
        if not os.path.isfile(path):
            problems.append(f"{who}: {path} no longer exists")
            continue
        with open(path, "rb") as f:
            raw = f.read()
        checked += 1
        if _sha(raw) != e["sha256"]:
            problems.append(f"{who}: {path} has CHANGED since it was frozen "
                            f"({_sha(raw)[:12]} now, {e['sha256'][:12]} frozen)")
    # The record must not have been edited either.
    want = doc.get("record_sha256")
    got = _sha(json.dumps({k: v for k, v in doc.items() if k not in ("utc", "record_sha256")},
                          sort_keys=True).encode("utf-8"))
    if want and want != got:
        problems.append(f"the frozen record itself has been edited ({got[:12]} vs {want[:12]})")
    return {"rule": RULE, "frozen": frozen_path, "checked": checked, "problems": problems,
            "verdict": "PASS" if not problems else "FAIL"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--campaign", required=True)
    f.add_argument("--skill-init", default="skillopt/envs/officeqa/skills/initial.md")
    f.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--frozen", required=True)
    args = ap.parse_args()

    if args.cmd == "freeze":
        doc = freeze(args.campaign, args.skill_init)
        print(f"rule {RULE}   {doc['requested']} skill(s) requested, "
              f"{doc['distinct_by_digest']} distinct by digest, {doc['distinct_by_advice']} by advice")
        for dig, who in sorted(doc["by_digest"].items()):
            e = next(x for x in doc["entries"] if x.get("sha256") == dig)
            print(f"  {dig[:16]}  {e['bytes']:>6} bytes  advice {e['advice_sha256'][:12]} "
                  f"({e['advice_bytes']} bytes)  {', '.join(who)}")
        print(f"\n  PRIMARY (S0 + each arm's validation-selected): "
              f"{doc['primary_distinct_by_digest']} distinct by digest, "
              f"{doc['primary_distinct_by_advice']} by advice")
        if doc["missing"]:
            print(f"  missing: {[m['role'] for m in doc['missing']]}")
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        print(f"\nfrozen: {args.out}   record {doc['record_sha256'][:16]}")
        return 0

    res = verify(args.frozen)
    print(f"rule {RULE}   verdict {res['verdict']}   {res['checked']} skill file(s) checked")
    for p in res["problems"]:
        print(f"  - {p}")
    return 0 if res["verdict"] == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
