#!/usr/bin/env python3
"""Why does the stage-diagnosis GROUP count read 17 where the historical record reads 14?

An amendment claimed C-3 had returned this count to its historical value and that the claim was
"independent support for C-3 being the right repair". The test says otherwise - `assert 17 == 14` -
so the claim is wrong and the question is whether 17 is CORRECT under the current semantics or the
residue of a defect. This probe answers that from the groups themselves rather than by argument: it
prints every group with its class, state and subject count, and separates the groups whose state is
an accusation from those that are UNKNOWN.

Free: no model call, no HTTP, no credential.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_lc_chain as CHAIN  # noqa: E402
import oqa_lc_ir as IR  # noqa: E402
import oqa_lc_report as REPORT  # noqa: E402
import oqa_trace as TR  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--arm", default="C")
    ap.add_argument("--item", default="UID0014")
    ap.add_argument("--epoch", type=int, default=3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    gen = os.path.join(a.campaign, f"arm_{a.arm}", "stage_reports", "GENERATED.jsonl")
    recs = [json.loads(l) for l in open(gen, encoding="utf-8") if l.strip()]
    rec = next(r for r in recs
               if r["id"] == a.item and f"step_{a.epoch:04d}" in str(r.get("prediction_dir", "")))
    pred = rec["prediction_dir"]
    results = os.path.join(os.path.dirname(pred), "results.jsonl")
    question = next(json.loads(l)["question"] for l in open(results, encoding="utf-8")
                    if l.strip() and json.loads(l)["id"] == a.item)
    model_path = os.path.join(a.campaign, "contracts", f"arm_{a.arm}",
                              f"model_arm{a.arm}_epoch{a.epoch - 1:02d}.json")
    model = IR.load_frozen(model_path)
    packet = TR.build_packet(pred, a.item, question=question)
    packet["trace_sha256"] = TR.trace_digest(pred, a.item)
    packet["collection"] = os.path.abspath(pred)
    chain = CHAIN.build(packet, model)
    groups = REPORT.group(chain["ledger"])

    rows = [{"class": g.get("kind"), "state": g["state"], "n_subjects": g["n"],
             "why": str(g.get("why") or "")[:160]} for g in groups]
    by_state = collections.Counter(r["state"] for r in rows)
    accusing = [r for r in rows if r["state"] in ("VIOLATED", "OUTSTANDING")]
    unknownish = [r for r in rows if r["state"] in ("UNKNOWN", "REOPENED")]
    out = {
        "item": a.item, "arm": a.arm, "epoch": a.epoch,
        "historical_n_distinct_diagnoses": rec["n_distinct_diagnoses"],
        "now_n_stage_diagnosis_groups": len(groups),
        "delta": len(groups) - rec["n_distinct_diagnoses"],
        "ledger_entries": len(chain["ledger"]),
        "groups_by_state": dict(by_state),
        "n_accusing_groups": len(accusing),
        "n_unknown_groups": len(unknownish),
        "groups": rows,
        "reading": ("the rise is accounted for by UNKNOWN groups if n_unknown_groups is at least the "
                    "delta, which is what amendment C-2's defect-A repair predicts: a withdrawn "
                    "binding now emits cell_bound, period_match and unit_match as UNKNOWN instead of "
                    "making the requirements vanish, and three more obligation CLASSES are three more "
                    "groups"),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(json.dumps({k: out[k] for k in ("historical_n_distinct_diagnoses",
                                          "now_n_stage_diagnosis_groups", "delta", "ledger_entries",
                                          "groups_by_state", "n_accusing_groups",
                                          "n_unknown_groups")}, indent=1, sort_keys=True))
    for r in rows:
        print(f"  {r['state']:<12} {str(r['class']):<28} n={r['n_subjects']:<3} {r['why'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
