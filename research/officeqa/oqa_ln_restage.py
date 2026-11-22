#!/usr/bin/env python3
"""Regenerate the stage reports from the FROZEN models, without a single paid call. Rule OQA-LN-1.

WHY THIS EXISTS. The Lean cross-check disagreed with the Python side about `source_traced` on attempts
with no supporting calculation, and following that disagreement exposed a defect in
`oqa_ln_evidence.recovery_shape`: it returned early on `len(calculations) < 2`, so an attempt whose
SINGLE written calculation yields the committed answer reported no supporting calculation at all - and
the stage layer reads that as "the committed answer is the result of no written calculation" and marks
`answer_traced` VIOLATED. Two attempts in the checking set, p2/UID0063 and p3/UID0063, were accused
that way for doing exactly the right thing.

The learned contracts themselves are unaffected: they are about operands, the defect was about which
calculation the stage layer looked at, and the models are already frozen with their digests. So the
stage reports are rebuilt from those same frozen models, deterministically and for nothing, into a NEW
versioned directory. The originals are left byte-identical, because a corrected result that overwrites
the result it corrects leaves no way to check the correction.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_run as RUN  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn-root", required=True, help="the directory holding the frozen models")
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True, help="a NEW directory; the originals are not touched")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    with open(a.freeze, encoding="utf-8") as f:
        freeze_doc = json.load(f)

    changed = []
    for model_path in sorted(glob.glob(os.path.join(a.learn_root, "r*_model_v*.json"))):
        tag = os.path.basename(model_path).split("_model_")[0]
        with open(model_path, encoding="utf-8") as f:
            model = json.load(f)
        active = [c for c in model["contracts"] if c["status"] == "active"]
        if not active:
            continue
        contract = active[-1]
        info = RUN.stage_reports(freeze_doc, contract, a.out, tag)
        # compare against the superseded version, so the correction is visible rather than asserted
        old_path = os.path.join(a.learn_root, f"{tag}_stage_reports.json")
        diff = None
        if os.path.exists(old_path):
            with open(old_path, encoding="utf-8") as f:
                old = json.load(f)
            with open(os.path.join(a.out, f"{tag}_stage_reports.json"), encoding="utf-8") as f:
                new = json.load(f)
            old_states = {r["attempt_id"]: {o["obligation"]: o["state"] for o in r["obligations"]}
                          for r in old["reports"]}
            new_states = {r["attempt_id"]: {o["obligation"]: o["state"] for o in r["obligations"]}
                          for r in new["reports"]}
            moved = []
            for aid, obs in sorted(new_states.items()):
                for ob, st in sorted(obs.items()):
                    was = old_states.get(aid, {}).get(ob)
                    if was != st:
                        moved.append({"attempt_id": aid, "obligation": ob, "was": was, "now": st})
            diff = {"n_obligations_moved": len(moved), "moved": moved,
                    "accusations_withdrawn": [m for m in moved
                                              if m["was"] in ("VIOLATED", "OUTSTANDING")
                                              and m["now"] not in ("VIOLATED", "OUTSTANDING")],
                    "accusations_added": [m for m in moved
                                          if m["now"] in ("VIOLATED", "OUTSTANDING")
                                          and m["was"] not in ("VIOLATED", "OUTSTANDING")]}
        changed.append({"tag": tag, "model": model_path,
                        "model_sha256": model.get("sha256"),
                        "contract": f"{contract['id']}@v{contract['version']}",
                        "stage_reports": info, "difference_from_the_superseded_version": diff})

    doc = {"schema": "lso.officeqa_learned_source_restage.v1",
           "written_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "why": "rebuilt after the recovery_shape defect the Lean cross-check led to; the "
                  "superseded reports are preserved at --learn-root and are not modified",
           "freeze_sha256": freeze_doc["self_sha256"],
           "paid_calls": 0,
           "results": changed}
    doc["self_sha256"] = IR.sha(json.dumps(
        {k: v for k, v in doc.items() if k not in ("written_utc", "self_sha256")},
        sort_keys=True, default=str))
    out = os.path.join(a.out, "LN_RESTAGE.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(f"wrote {out}  digest {doc['self_sha256'][:16]}  paid calls 0")
    for c in changed:
        d = c["difference_from_the_superseded_version"] or {}
        print(f"  {c['tag']}: {c['stage_reports']['n_attempts']} attempts, "
              f"{d.get('n_obligations_moved', '?')} obligation(s) moved, "
              f"{len(d.get('accusations_withdrawn', []))} accusation(s) WITHDRAWN, "
              f"{len(d.get('accusations_added', []))} added")
        for m in (d.get("accusations_withdrawn") or [])[:6]:
            print(f"     withdrawn: {m['attempt_id']} {m['obligation']} {m['was']} -> {m['now']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
