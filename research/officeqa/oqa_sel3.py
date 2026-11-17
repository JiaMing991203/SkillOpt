"""Freeze the three question sets for the THREE-ARM OfficeQA API comparison: rule OQA-SEL-3.

Offline. Reads only `data/officeqa_id_split/` (the tracked ID manifest of the released OfficeQA splits) and the
two records of the previous freeze. No document, no question text, no score and no trace is read, so the sets
cannot be chosen with hindsight.

THE RULE, stated before it is run.

  val      Every item of the released OfficeQA val split. All 24, no selection and no stratification, because the
           user asked for "the released 24-question validation set". The natural stratum mix (5/6/6/7) is
           whatever the benchmark released; nothing is balanced here.

  train    24 items of the released train split (50 available), balanced 6/6/6/6 over
           category x document count. OQA-SEL-2's 12 training items are RETAINED FIRST - they are exactly
           3/3/3/3 - so every saved development trace remains a trace of a current training item and the earlier
           offline work stays usable as development evidence.

  heldout  60 items of the released test split (172 available), balanced 15/15/15/15. OQA-SEL-2's 24 held-out
           items (6/6/6/6) are RETAINED FIRST, so the previous held-out set is a subset of this one.

  Draw order inside a stratum: the items not already frozen, ordered by sha256("OQA-SEL-3" + uid) ascending.
           A fixed permutation of the pool. It depends on the rule name and the item id and on nothing else -
           not on a score, not on a trace, not on an availability verdict.

  Excluded UID0046 only, from the train pool. OQA-SEL-2 removed it because the evidence test found no retrievable
           value for that question in its supplied page or anywhere in the parsed document. The exclusion is
           carried forward and recorded here rather than applied silently. It is NOT applied to val: the user
           asked for the released set, and filtering the gate would be a selection the benchmark did not make.

  Strata   `category` ("easy" / "hard") x document count ("single" = one source document, "multi" = more than
           one), which is the stratification OQA-SEL-2 used.

WHAT THIS SCRIPT CANNOT ESTABLISH. Availability - whether the asked value is actually retrievable from the
supplied page or the parsed document - needs the documents, which live on the cluster only. Items carried over
from OQA-SEL-2 have a recorded verdict; newly drawn items are marked UNTESTED and counted, so the launch packet
can state exactly how much of each set is unverified.

usage: python3 -I research/officeqa/oqa_sel3.py --out docs/research/OFFICEQA_FROZEN_SETS_V3.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

RULE = "OQA-SEL-3"
TARGETS = {"train": 24, "val": 24, "heldout": 60}
RELEASED_SPLIT = {"train": "train", "val": "val", "heldout": "test"}
STRATA = ("easy/single", "easy/multi", "hard/single", "hard/multi")
EXCLUDED = {
    "UID0046": "OQA-SEL-2: the evidence test found no retrievable value for this question in the supplied page "
               "or anywhere in the parsed document (page_chars 158, page_candidate_values 0, references a "
               "figure). Carried forward as an exclusion from the TRAIN pool only.",
}


def _repo() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _split_items(split: str) -> list:
    path = os.path.join(_repo(), "data", "officeqa_id_split", split, "items.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _multi(item: dict) -> bool:
    """More than one source document. Counted from both fields, because the CSV populates them inconsistently."""
    n = 0
    for field in ("source_docs", "source_files"):
        raw = str(item.get(field) or "")
        parts = [p.strip() for p in raw.replace(",", "\n").splitlines() if p.strip()]
        n = max(n, len(parts))
    return n > 1


def stratum(item: dict) -> str:
    return f"{str(item.get('category') or 'unknown')}/{'multi' if _multi(item) else 'single'}"


def _order_key(uid: str) -> str:
    return hashlib.sha256((RULE + "\x00" + uid).encode()).hexdigest()


def _previous() -> dict:
    """OQA-SEL-2's three sets and its per-item availability verdicts."""
    path = os.path.join(_repo(), "docs", "research", "OFFICEQA_FROZEN_SETS.json")
    with open(path, encoding="utf-8") as f:
        prev = json.load(f)
    sets = {name: list(prev["sets"][name]["ids"]) for name in ("train", "val", "heldout")}
    verdicts = {}
    for name in ("train", "val", "heldout"):
        verdicts.update(prev["sets"][name].get("verdicts") or {})
    return {"rule": prev.get("rule"), "digest": prev.get("digest"), "sets": sets, "verdicts": verdicts}


def _evidence_verdicts() -> dict:
    path = os.path.join(_repo(), "docs", "research", "OFFICEQA_EVIDENCE_CHECK.json")
    try:
        with open(path, encoding="utf-8") as f:
            return {k: str(v.get("verdict") or "UNTESTED") for k, v in json.load(f).items()}
    except OSError:
        return {}


def build_set(name: str, pool: list, retain: list, balanced: bool) -> dict:
    """Draw one set. `retain` items come first; the rest follow the rule's fixed permutation."""
    target = TARGETS[name]
    by_id = {str(i["id"]): i for i in pool}
    excluded = [uid for uid in by_id if uid in EXCLUDED] if name == "train" else []
    usable = {uid: it for uid, it in by_id.items() if uid not in excluded}

    if not balanced:
        chosen = sorted(usable)
        return {"ids": chosen, "balanced": False, "excluded": excluded,
                "per_stratum": _counts(chosen, usable), "shortfall": target - len(chosen)}

    per = target // len(STRATA)
    chosen: list = []
    shortfalls = {}
    for s in STRATA:
        in_s = [uid for uid, it in usable.items() if stratum(it) == s]
        kept = [uid for uid in retain if uid in in_s]
        rest = sorted((uid for uid in in_s if uid not in kept), key=_order_key)
        take = kept[:per] + rest[: max(0, per - len(kept))]
        if len(take) < per:
            shortfalls[s] = per - len(take)
        chosen += take
    # A retained item that is no longer in its stratum's quota would be dropped silently; refuse instead.
    dropped = [uid for uid in retain if uid in usable and uid not in chosen]
    return {"ids": chosen, "balanced": True, "excluded": excluded, "per_stratum": _counts(chosen, usable),
            "shortfall": target - len(chosen), "per_stratum_shortfall": shortfalls,
            "retained_from_previous": [uid for uid in retain if uid in chosen],
            "previous_items_dropped": dropped}


def _counts(ids: list, by_id: dict) -> dict:
    out = {s: 0 for s in STRATA}
    for uid in ids:
        out[stratum(by_id[uid])] = out.get(stratum(by_id[uid]), 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    prev = _previous()
    verdicts = _evidence_verdicts()
    sets: dict = {}
    problems: list = []

    for name in ("train", "val", "heldout"):
        pool = _split_items(RELEASED_SPLIT[name])
        built = build_set(name, pool, prev["sets"][name], balanced=(name != "val"))
        by_id = {str(i["id"]): i for i in pool}
        built["released_split"] = RELEASED_SPLIT[name]
        built["released_split_size"] = len(pool)
        built["target"] = TARGETS[name]
        built["strata"] = {uid: stratum(by_id[uid]) for uid in built["ids"]}
        built["verdicts"] = {uid: verdicts.get(uid, "UNTESTED") for uid in built["ids"]}
        built["n_untested"] = sum(1 for v in built["verdicts"].values() if v == "UNTESTED")
        built["n_available"] = sum(1 for v in built["verdicts"].values() if v.startswith("AVAILABLE"))
        if built["shortfall"]:
            problems.append(f"{name}: {built['shortfall']} short of {TARGETS[name]}")
        if built.get("previous_items_dropped"):
            problems.append(f"{name}: previously frozen items dropped: {built['previous_items_dropped']}")
        sets[name] = built

    # Disjointness, and the superset property that keeps the earlier traces usable.
    ids = {n: set(sets[n]["ids"]) for n in sets}
    for a, b in (("train", "val"), ("train", "heldout"), ("val", "heldout")):
        overlap = sorted(ids[a] & ids[b])
        if overlap:
            problems.append(f"{a} and {b} overlap: {overlap}")
    superset = {}
    for name in sets:
        missing = sorted(set(prev["sets"][name]) - ids[name])
        superset[name] = {"previous_count": len(prev["sets"][name]),
                          "previous_items_not_carried": missing,
                          "is_superset": not missing}
        if missing and name != "train":
            problems.append(f"{name}: not a superset of {prev['rule']}: missing {missing}")

    payload = {
        "rule": RULE,
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "supersedes": {"rule": prev["rule"], "digest": prev["digest"]},
        "source": {"manifest": "data/officeqa_id_split", "released_counts":
                   {n: sets[n]["released_split_size"] for n in sets}},
        "excluded": EXCLUDED,
        "sets": sets,
        "disjoint": all("overlap" not in p for p in problems),
        "previous_sets_are_subsets": superset,
        "problems": problems,
    }
    body = json.dumps({k: v for k, v in payload.items() if k != "digest"}, sort_keys=True, separators=(",", ":"))
    payload["digest"] = hashlib.sha256(body.encode()).hexdigest()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(json.dumps({"rule": RULE, "digest": payload["digest"],
                      "counts": {n: len(sets[n]["ids"]) for n in sets},
                      "per_stratum": {n: sets[n]["per_stratum"] for n in sets},
                      "untested_availability": {n: sets[n]["n_untested"] for n in sets},
                      "disjoint": payload["disjoint"],
                      "previous_subsets": {n: superset[n]["is_superset"] for n in superset},
                      "problems": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
