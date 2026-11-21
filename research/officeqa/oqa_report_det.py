#!/usr/bin/env python3
"""Is a generated contract report byte-deterministic? Rule OQA-OCC-1, diagnostic.

WHY THIS EXISTS. A byte-reproduction test on two real campaign reports gave OPPOSITE results in two
jobs whose report-generating code was identical:

    job 66552984 (commit 67fed00)  the test expecting CHANGED bytes failed -> the bytes were IDENTICAL
    job 66553083 (commit b4a730c)  the test expecting IDENTICAL bytes failed -> the bytes DIFFERED

`git diff` over oqa_lc_chain.py, oqa_reports.py, oqa_lc_report.py, oqa_cc_extract.py, oqa_lc_cells.py
and oqa_occ.py between those commits is EMPTY. Same code, same inputs, opposite outcomes. So either the
generation is non-deterministic or one of those two readings is wrong - and until that is settled, any
claim that a repair "restored the report byte-identically" is unfounded.

Note `python -I` implies `-E`, so PYTHONHASHSEED from the environment is IGNORED and string-hash
randomisation is active with a fresh per-process seed. Any set iteration that reaches the report text
would therefore vary BETWEEN processes and be stable WITHIN one. That is the first hypothesis this
separates:

    same digest within a process, differing across processes  -> hash-seed sensitivity (set iteration)
    differing within a single process                         -> ordinary non-determinism
    identical everywhere                                      -> generation is deterministic, and one
                                                                 of the two job readings must be
                                                                 explained some other way

usage:
  oqa_report_det.py --campaign <root> --arm C --item UID0014 --epoch 3 --condition LC_STAGE \
      --repeats 8 --out <json>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys

RULE = "OQA-OCC-1"
SCHEMA = "lso.officeqa_report_determinism.v1"


def _one(campaign: str, arm: str, item: str, epoch: int, condition: str) -> dict:
    """Regenerate the report once, in THIS process, and return its digest."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import oqa_reports as R  # type: ignore

    gen = os.path.join(campaign, f"arm_{arm}", "stage_reports", "GENERATED.jsonl")
    recs = [json.loads(l) for l in open(gen, encoding="utf-8") if l.strip()]
    rec = next(r for r in recs
               if r["id"] == item and f"step_{epoch:04d}" in str(r.get("prediction_dir", "")))
    pred = rec["prediction_dir"]
    results = os.path.join(os.path.dirname(pred), "results.jsonl")
    model = os.path.join(campaign, "contracts",
                         "model_M0.json" if epoch == 2 else
                         os.path.join(f"arm_{arm}", f"model_arm{arm}_epoch{epoch - 1:02d}.json"))
    question = next(json.loads(l)["question"] for l in open(results, encoding="utf-8")
                    if l.strip() and json.loads(l)["id"] == item)
    text, _ = R.build_one_lc(pred, item, question, results, model, condition=condition)
    return {"digest": hashlib.sha256(text.encode()).hexdigest(),
            "chars": len(text), "historical": rec["report_sha256"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--arm", default="C")
    ap.add_argument("--item", default="UID0014")
    ap.add_argument("--epoch", type=int, default=3)
    ap.add_argument("--condition", default="LC_STAGE")
    ap.add_argument("--repeats", type=int, default=8)
    ap.add_argument("--child", action="store_true", help="internal: emit one digest and exit")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    if a.child:
        print(json.dumps(_one(a.campaign, a.arm, a.item, a.epoch, a.condition)))
        return 0

    # WITHIN one process
    within = [_one(a.campaign, a.arm, a.item, a.epoch, a.condition) for _ in range(a.repeats)]
    # ACROSS processes, each with its own fresh hash seed
    across = []
    for _ in range(a.repeats):
        p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--child",
                            "--campaign", a.campaign, "--arm", a.arm, "--item", a.item,
                            "--epoch", str(a.epoch), "--condition", a.condition],
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            across.append(json.loads(p.stdout.strip().splitlines()[-1]))
        else:
            across.append({"error": (p.stderr or "")[-400:]})

    w = collections.Counter(r.get("digest") for r in within)
    x = collections.Counter(r.get("digest") for r in across if r.get("digest"))
    historical = within[0]["historical"] if within else None
    all_digests = set(w) | set(x)
    verdict = ("DETERMINISTIC" if len(all_digests) == 1 else
               "HASH_SEED_SENSITIVE" if len(w) == 1 and len(x) > 1 else
               "NON_DETERMINISTIC")
    doc = {"schema": SCHEMA, "rule": RULE, "verdict": verdict,
           "repeats": a.repeats,
           "target": {"arm": a.arm, "item": a.item, "epoch": a.epoch, "condition": a.condition},
           "historical_digest": historical,
           "distinct_digests_within_one_process": len(w),
           "distinct_digests_across_processes": len(x),
           "within": dict(w), "across": dict(x),
           "matches_historical": {"within": sum(n for d, n in w.items() if d == historical),
                                  "across": sum(n for d, n in x.items() if d == historical)},
           "note": "python -I implies -E, so PYTHONHASHSEED is ignored and string-hash randomisation "
                   "is active with a fresh per-process seed. Stable within a process but varying "
                   "across processes points at set iteration reaching the report text."}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(json.dumps(doc, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
