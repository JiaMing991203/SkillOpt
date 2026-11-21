#!/usr/bin/env python3
"""Does amendment C-1 change what the chain reports, and in the direction predicted? Rule OQA-OCC-1.

C-1 replaced a tie-break with a refusal: when several cells hold an operand's value, no cell is
selected unless an occurrence binding corroborates one independently of the number. The prediction was
COMMITTED IN THE AMENDMENT BEFORE this ran:

    "Fewer reported source bindings, not more, because the change only ever WITHHOLDS a selection.
     The count of operands carrying a selected cell will FALL; some obligations currently DISCHARGED
     or VIOLATED will become UNKNOWN; and the number of supported bindings will not rise above the 64
     the occurrence measurement found. If the replay shows MORE selections, the implementation is
     wrong rather than the prediction. An increase in coverage would be a defect here, not a result."

So this reports the direction as well as the size, and says explicitly whether the prediction held.

HOW BOTH SIDES ARE PRODUCED WITHOUT A SWITCH IN PRODUCTION CODE. The gate cannot be disabled - there
is no flag for it, deliberately, because a flag that restores a defect eventually gets set. Instead
this tool recomputes the OLD behaviour itself from the same candidate list: `_old_tie_break` is the
exact ranking `_best_cell` used, kept here as the thing being compared against rather than as a code
path anyone can take. Both operand sets are then passed through the SAME `obligations()` so the ledger
difference is attributable to the selection and to nothing else.

Label: REUSED DEVELOPMENT EVIDENCE. These are training-split records, re-analysed offline.

usage:
  oqa_occ_replay.py --predictions <dir> --model <CC_MODEL.json> --records <results.jsonl> --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_lc_chain as CHAIN          # noqa: E402
import oqa_lc_cells as CELLS          # noqa: E402
import oqa_trace as TRACE             # noqa: E402

RULE = "OQA-OCC-1"
SCHEMA = "lso.officeqa_occ_replay.v1"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _old_tie_break(hits: list):
    """`_best_cell`'s exact former behaviour, kept HERE as the comparison target, not as a code path.

    It returns a cell whenever any cell's value matches, choosing by exact match, then a FLAT column,
    then the earliest step. That choice is what C-1 refuses to make without corroboration.
    """
    if not hits:
        return None
    return sorted(hits, key=lambda c: (c["scale_factor"] != 1.0,
                                       c.get("column_basis") != CELLS.COL_FLAT,
                                       c["available_from"]))[0]


def _ledger_states(chain: dict, operands: list) -> dict:
    """Run the SAME obligations() over a given operand set, so only the selection differs.

    The VIOLATED records are carried out with their witnesses. Amendment C-2's prediction 5 says a
    restored binding can support an accusation and that "any increase must be inspected witness by
    witness before it is believed", so the witnesses have to be IN the artifact; a count alone cannot
    be inspected.
    """
    variant = dict(chain)
    variant["operands"] = operands
    ledger = CHAIN.obligations(variant)
    violated = [{"kind": o.get("kind"), "subject": o.get("subject"), "why": o.get("why"),
                 "witness": o.get("witness"), "supports": o.get("supports")}
                for o in ledger if str(o.get("state")) == "VIOLATED"]
    return {"states": collections.Counter(str(o.get("state")) for o in ledger),
            "n": len(ledger),
            "by_kind_state": collections.Counter(
                f'{o.get("kind")}:{o.get("state")}' for o in ledger),
            "violated": violated}


def replay_one(prediction_dir: str, item: str, question: str, model: dict) -> dict:
    packet = TRACE.build_packet(prediction_dir, item, question)
    chain = CHAIN.build(packet, model)

    gated = chain["operands"]
    old = [{**o, "cell": _old_tie_break(o.get("cells") or [])} for o in gated]

    def _selected(ops):
        return sum(1 for o in ops if o.get("cell"))

    bases = collections.Counter(str((o.get("selection") or {}).get("basis")) for o in gated)
    refusals = [o for o in gated
                if (o.get("selection") or {}).get("basis") == CHAIN.REFUSED_NO_CORROBORATION]
    corroborated = [o for o in gated
                    if (o.get("selection") or {}).get("basis") == CHAIN.OCCURRENCE_CORROBORATED]

    before = _ledger_states(chain, old)
    after = _ledger_states(chain, gated)
    return {
        "item": item,
        "n_operands": len(gated),
        "selected_old": _selected(old),
        "selected_gated": _selected(gated),
        "selection_bases": dict(bases),
        "n_refused": len(refusals),
        "n_occurrence_corroborated": len(corroborated),
        "refusal_witnesses": [
            {"value": o.get("value"), "step": o.get("step"),
             "n_candidates": (o.get("selection") or {}).get("n_candidates"),
             "occurrence_match": (o.get("selection") or {}).get("occurrence_match"),
             "rivals": (o.get("selection") or {}).get("rivals")}
            for o in refusals[:4]],
        "corroborated_witnesses": [
            {"value": o.get("value"), "step": o.get("step"),
             "occurrence": (o.get("selection") or {}).get("occurrence"),
             "corroborated_by": (o.get("selection") or {}).get("corroborated_by")}
            for o in corroborated[:4]],
        "ledger_old": {"states": dict(before["states"]), "n": before["n"],
                       "n_violated": len(before["violated"])},
        "ledger_gated": {"states": dict(after["states"]), "n": after["n"],
                         "n_violated": len(after["violated"]),
                         # every accusation the gated arm makes, with its witness, so prediction 5's
                         # "inspect it witness by witness" is a thing a reader can actually do
                         "violated": after["violated"]},
        "ledger_state_delta": {k: after["states"].get(k, 0) - before["states"].get(k, 0)
                               for k in set(before["states"]) | set(after["states"])
                               if after["states"].get(k, 0) != before["states"].get(k, 0)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--records", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    with open(a.model, encoding="utf-8") as f:
        model = json.load(f)
    questions: dict = {}
    if a.records and os.path.isfile(a.records):
        with open(a.records, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    questions[str(row.get("id"))] = str(row.get("question") or "")

    items = sorted(d for d in os.listdir(a.predictions)
                   if os.path.isfile(os.path.join(a.predictions, d, "conversation.json")))
    per_item, problems = [], []
    for item in items:
        try:
            per_item.append(replay_one(a.predictions, item, questions.get(item, ""), model))
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            problems.append({"item": item, "error": f"{type(exc).__name__}: {exc}"})

    def tot(key):
        return sum(r.get(key) or 0 for r in per_item)

    bases = collections.Counter()
    delta = collections.Counter()
    for r in per_item:
        bases.update(r["selection_bases"])
        delta.update(r["ledger_state_delta"])

    selected_old, selected_gated = tot("selected_old"), tot("selected_gated")
    prediction_held = (selected_gated <= selected_old)
    # Prediction 1 of C-2: the two arms must have the SAME ledger size, because the gate changes
    # obligation STATES and never their existence. Asserted here so the artifact answers it directly.
    ledger_old_n = sum(r["ledger_old"]["n"] for r in per_item)
    ledger_gated_n = sum(r["ledger_gated"]["n"] for r in per_item)
    all_violated = [v for r in per_item for v in (r["ledger_gated"].get("violated") or [])]
    doc = {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(), "commit": os.environ.get("OQA_COMMIT"),
        "label": "REUSED DEVELOPMENT EVIDENCE: training-split records, re-analysed offline. No "
                 "validation or held-out record takes part.",
        "amendment": "C-1",
        "committed_prediction": "fewer reported source bindings, not more; the change only ever "
                                "withholds a selection, so an increase would be a defect",
        "inputs": {"predictions": a.predictions, "model": a.model, "records": a.records},
        "totals": {
            "records": len(per_item), "operands": tot("n_operands"),
            "selected_old_tie_break": selected_old,
            "selected_under_C1": selected_gated,
            "withdrawn": selected_old - selected_gated,
            "refused_no_corroboration": tot("n_refused"),
            "occurrence_corroborated": tot("n_occurrence_corroborated"),
            "selection_bases": dict(bases),
            "ledger_state_delta": dict(delta),
            "ledger_obligations_old": ledger_old_n,
            "ledger_obligations_gated": ledger_gated_n,
            "ledger_size_identical": ledger_old_n == ledger_gated_n,
            "accusations_under_the_gate": len(all_violated),
            "accusation_kinds": dict(collections.Counter(v["kind"] for v in all_violated)),
        },
        "every_accusation_with_its_witness": all_violated,
        "prediction_held": prediction_held,
        "verdict": ("the prediction HELD: the gate only withdrew selections"
                    if prediction_held else
                    "THE PREDICTION FAILED: more cells are selected under C-1 than under the old "
                    "tie-break, which means the implementation is wrong"),
        "per_item": per_item,
        "problems": problems,
    }
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "OCC_REPLAY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({"written_to": path, "verdict": doc["verdict"],
                      "prediction_held": prediction_held,
                      "totals": {k: v for k, v in doc["totals"].items()},
                      "problems": problems[:4]}, indent=1, default=str))
    return 0 if prediction_held and not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
