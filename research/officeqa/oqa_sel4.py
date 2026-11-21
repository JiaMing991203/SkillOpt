"""Freeze the three question sets for the TWO-BASELINE OfficeQA campaign: rule OQA-SEL-4 (12 / 12 / 30).

Offline. Reads only `data/officeqa_id_split/` (the tracked ID manifest of the released OfficeQA splits) and the
two previous freeze records. No document, no question text, no score, no trace and no availability verdict
takes part in the choice, so the sets cannot be chosen with hindsight. Availability verdicts are COPIED onto
the output for reporting; `build_set` never reads them.

THE RULE, STATED BEFORE IT IS RUN.

  train    Exactly OQA-SEL-2's 12 training items, unchanged. No new draw. Three reasons, in order:
           they are already 3/3/3/3 over category x document count; every one lies inside OQA-SEL-3's 24, so
           this is a subset of the set the three-arm campaign trained on; and all 12 already have a
           corrected-client S0 trajectory in the 24-question collection of 2026-11-18, which is what lets
           Trace2Skill reuse paid work instead of re-collecting it.

  val      Exactly OQA-SEL-2's 12 validation items, unchanged. No new draw. These are 12 of the released
           24-item val split. The stratum mix is reported, not imposed: OQA-SEL-2 drew them balanced, and
           whatever that produced is what the gate sees.

  heldout  30 items of the released test split (172 available). OQA-SEL-2's 24 held-out items are RETAINED
           FIRST - they are 6/6/6/6 - so the new measurement overlaps the two historical held-out numbers on
           24 of its 30 questions and the series stays comparable. The remaining 6 are drawn from
           OQA-SEL-3's 60, so this set is also a subset of the set the three-arm campaign evaluated, and no
           question enters the campaign that an earlier freeze had not already placed in the held-out role.
           Per-stratum quota 8/8/7/7 in the declared STRATA order, which is 30 spread as evenly as four
           strata allow.

  Draw order inside a stratum: the items not already frozen, ordered by sha256("OQA-SEL-4" + uid) ascending.
           A fixed permutation that depends on the rule name and the item id and on nothing else.

  Excluded UID0046 only, from the train pool, carried forward from OQA-SEL-2 and OQA-SEL-3 rather than applied
           silently. It is not in OQA-SEL-2's 12, so for this rule the exclusion changes nothing; it is
           recorded so a reader does not have to go back two documents to find out why.

  Strata   `category` ("easy" / "hard") x document count ("single" = one source document, "multi" = more than
           one). Both come from the released metadata. NO OfficeQA task category is invented here.

WHAT THIS SCRIPT ALSO RECORDS, because the instruction asks for it by name.

  prior research exposure   Per item, every earlier OfficeQA role this project put it in, each entry naming the
                            record it was read from. Exposure is a FACT ABOUT THE PAST, not a filter: nothing
                            is included or excluded because of it. All 30 held-out items were evaluated during
                            the three-arm campaign, under the BROKEN actor client, and the summary says so.

  source-document overlap   Which parsed Treasury Bulletin documents each set draws on, and the intersections
                            between the three sets. A held-out question resting on a document a training
                            question also rests on is a real leakage channel for a document-specific edit, so
                            it is counted and listed rather than assumed absent.

usage: python3 -I research/officeqa/oqa_sel4.py --out docs/research/OFFICEQA_FROZEN_SETS_V4.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os

RULE = "OQA-SEL-4"
TARGETS = {"train": 12, "val": 12, "heldout": 30}
RELEASED_SPLIT = {"train": "train", "val": "val", "heldout": "test"}
STRATA = ("easy/single", "easy/multi", "hard/single", "hard/multi")
# 30 over four strata: the first two in STRATA order take the extra item each.
HELDOUT_QUOTA = {"easy/single": 8, "easy/multi": 8, "hard/single": 7, "hard/multi": 7}
EXCLUDED = {
    "UID0046": "OQA-SEL-2: the evidence test found no retrievable value for this question in the supplied "
               "page or anywhere in the parsed document. Carried forward as an exclusion from the TRAIN pool "
               "only. It is not among OQA-SEL-2's 12 training items, so this rule's train set is unaffected.",
}

# Earlier roles, each keyed to the record it is read from. Used for REPORTING only.
EXPOSURE_SOURCES = {
    "OQA-SEL-2": "docs/research/OFFICEQA_FROZEN_SETS.json",
    "OQA-SEL-3": "docs/research/OFFICEQA_FROZEN_SETS_V3.json",
}


def _repo() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _split_items(split: str) -> list:
    with open(os.path.join(_repo(), "data", "officeqa_id_split", split, "items.json"), encoding="utf-8") as f:
        return json.load(f)


def _docs_of(item: dict) -> list[str]:
    """The source documents of one question.

    The CSV populates `source_docs` and `source_files` inconsistently AND separates multiple files with a
    NEWLINE, not a comma - the bug fixed in commit 455e267. Both separators are accepted here so a
    multi-document question is never read as one long filename.
    """
    best: list[str] = []
    for field in ("source_docs", "source_files"):
        raw = str(item.get(field) or "")
        parts = [p.strip() for p in raw.replace(",", "\n").splitlines() if p.strip()]
        if len(parts) > len(best):
            best = parts
    return best


def _multi(item: dict) -> bool:
    return len(_docs_of(item)) > 1


def stratum(item: dict) -> str:
    return f"{str(item.get('category') or 'unknown')}/{'multi' if _multi(item) else 'single'}"


def _order_key(uid: str) -> str:
    return hashlib.sha256((RULE + "\x00" + uid).encode()).hexdigest()


def _frozen(path: str) -> dict:
    with open(os.path.join(_repo(), path), encoding="utf-8") as f:
        return json.load(f)


def _counts(ids: list, by_id: dict) -> dict:
    out = {s: 0 for s in STRATA}
    for uid in ids:
        s = stratum(by_id[uid])
        out[s] = out.get(s, 0) + 1
    return out


def build_set(name: str, pool: list, *, keep: list | None = None,
              retain: list | None = None, allowed: set | None = None) -> dict:
    """Draw one set.

    `keep` - take exactly these ids, in sorted order, with no draw at all (train and val).
    `retain` + `allowed` - retain these first, then draw from `allowed` by the rule's permutation (heldout).
    """
    target = TARGETS[name]
    by_id = {str(i["id"]): i for i in pool}
    excluded = sorted(uid for uid in by_id if uid in EXCLUDED) if name == "train" else []
    usable = {uid: it for uid, it in by_id.items() if uid not in excluded}

    if keep is not None:
        missing = sorted(uid for uid in keep if uid not in usable)
        chosen = sorted(uid for uid in keep if uid in usable)
        return {"ids": chosen, "method": "carried forward unchanged from OQA-SEL-2, no draw",
                "balanced": False, "excluded": excluded, "per_stratum": _counts(chosen, usable),
                "shortfall": target - len(chosen), "not_in_released_split": missing,
                "retained_from_previous": chosen, "previous_items_dropped": []}

    retain = retain or []
    chosen: list = []
    shortfalls = {}
    for s in STRATA:
        per = HELDOUT_QUOTA[s]
        in_s = [uid for uid, it in usable.items() if stratum(it) == s]
        kept = [uid for uid in retain if uid in in_s]
        pool_s = [uid for uid in in_s if uid not in kept and (allowed is None or uid in allowed)]
        rest = sorted(pool_s, key=_order_key)
        take = kept[:per] + rest[: max(0, per - len(kept))]
        if len(take) < per:
            shortfalls[s] = per - len(take)
        chosen += take
    dropped = [uid for uid in retain if uid in usable and uid not in chosen]
    return {"ids": sorted(chosen),
            "method": "OQA-SEL-2's 24 retained first, then drawn from OQA-SEL-3's 60 by "
                      'sha256("OQA-SEL-4"+uid) ascending, to the quota ' + json.dumps(HELDOUT_QUOTA),
            "balanced": True, "excluded": excluded, "per_stratum": _counts(chosen, usable),
            "shortfall": target - len(chosen), "per_stratum_shortfall": shortfalls,
            "retained_from_previous": sorted(uid for uid in retain if uid in chosen),
            "previous_items_dropped": dropped}


def exposure(ids_by_set: dict, prev2: dict, prev3: dict) -> dict:
    """Every earlier role each selected item held, with the record each claim is read from."""
    roles: dict = {}
    for label, doc in (("OQA-SEL-2", prev2), ("OQA-SEL-3", prev3)):
        for role in ("train", "val", "heldout"):
            for uid in (doc["sets"][role].get("ids") or []):
                roles.setdefault(str(uid), []).append(
                    {"freeze": label, "role": role, "record": EXPOSURE_SOURCES[label]})
    out = {}
    for name, ids in ids_by_set.items():
        out[name] = {uid: roles.get(uid, []) for uid in ids}
    return out


def doc_overlap(ids_by_set: dict, by_id: dict) -> dict:
    """Which parsed documents each set rests on, and the intersections between sets."""
    docs = {name: sorted({d for uid in ids for d in _docs_of(by_id[uid])}) for name, ids in ids_by_set.items()}
    pairs = {}
    for a, b in (("train", "val"), ("train", "heldout"), ("val", "heldout")):
        shared = sorted(set(docs[a]) & set(docs[b]))
        items = {}
        if shared:
            for name in (a, b):
                items[name] = sorted(uid for uid in ids_by_set[name]
                                     if set(_docs_of(by_id[uid])) & set(shared))
        pairs[f"{a}|{b}"] = {"n_shared_documents": len(shared), "shared_documents": shared,
                             "items_touching_a_shared_document": items}
    return {"documents_per_set": {k: {"n": len(v), "documents": v} for k, v in docs.items()}, "pairs": pairs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    prev2 = _frozen(EXPOSURE_SOURCES["OQA-SEL-2"])
    prev3 = _frozen(EXPOSURE_SOURCES["OQA-SEL-3"])
    verdicts: dict = {}
    for role in ("train", "val", "heldout"):
        verdicts.update(prev3["sets"][role].get("verdicts") or {})
        verdicts.update({k: v for k, v in (prev2["sets"][role].get("verdicts") or {}).items()
                         if k not in verdicts})

    sets: dict = {}
    problems: list = []
    all_items: dict = {}

    for name in ("train", "val", "heldout"):
        pool = _split_items(RELEASED_SPLIT[name])
        by_id = {str(i["id"]): i for i in pool}
        all_items.update(by_id)
        if name == "heldout":
            built = build_set(name, pool, retain=list(prev2["sets"]["heldout"]["ids"]),
                              allowed=set(prev3["sets"]["heldout"]["ids"]))
        else:
            built = build_set(name, pool, keep=list(prev2["sets"][name]["ids"]))
        built["released_split"] = RELEASED_SPLIT[name]
        built["released_split_size"] = len(pool)
        built["target"] = TARGETS[name]
        built["strata"] = {uid: stratum(by_id[uid]) for uid in built["ids"]}
        built["source_documents"] = {uid: _docs_of(by_id[uid]) for uid in built["ids"]}
        built["verdicts"] = {uid: verdicts.get(uid, "UNTESTED") for uid in built["ids"]}
        built["n_untested"] = sum(1 for v in built["verdicts"].values() if v == "UNTESTED")
        built["n_available"] = sum(1 for v in built["verdicts"].values() if v.startswith("AVAILABLE"))
        if built["shortfall"]:
            problems.append(f"{name}: {built['shortfall']} short of {TARGETS[name]}")
        if built.get("previous_items_dropped"):
            problems.append(f"{name}: previously frozen items dropped: {built['previous_items_dropped']}")
        if built.get("not_in_released_split"):
            problems.append(f"{name}: ids absent from the released split: {built['not_in_released_split']}")
        sets[name] = built

    ids = {n: set(sets[n]["ids"]) for n in sets}
    for a, b in (("train", "val"), ("train", "heldout"), ("val", "heldout")):
        overlap = sorted(ids[a] & ids[b])
        if overlap:
            problems.append(f"{a} and {b} overlap: {overlap}")

    # Every selected item must come from its own released split, and each set must sit inside the freeze it
    # claims to subset. Both are checked rather than asserted in prose.
    subset_of = {}
    for name in sets:
        prior = set(prev3["sets"][name]["ids"]) if name != "val" else set(prev2["sets"][name]["ids"])
        label = "OQA-SEL-3" if name != "val" else "OQA-SEL-2"
        outside = sorted(ids[name] - prior)
        subset_of[name] = {"of": label, "items_outside": outside, "is_subset": not outside}
        if outside:
            problems.append(f"{name}: not a subset of {label}: {outside}")

    ids_by_set = {n: sets[n]["ids"] for n in sets}
    payload = {
        "rule": RULE,
        "schema": "lso.officeqa_frozen_sets.v4",
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": {"train": 12, "val": 12, "heldout": 30,
                   "heldout_attempts_per_question_per_endpoint": 2},
        "supersedes": {"rule": prev3.get("rule"), "digest": prev3.get("digest")},
        "source": {"manifest": "data/officeqa_id_split",
                   "released_counts": {n: sets[n]["released_split_size"] for n in sets}},
        "excluded": EXCLUDED,
        "sets": sets,
        "disjoint": not any("overlap" in p for p in problems),
        "subset_of_previous_freeze": subset_of,
        "prior_research_exposure": exposure(ids_by_set, prev2, prev3),
        "source_document_overlap": doc_overlap(ids_by_set, all_items),
        "problems": problems,
    }
    body = json.dumps(payload, indent=1, sort_keys=True)
    payload["digest"] = hashlib.sha256(body.encode()).hexdigest()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(json.dumps({"out": args.out, "digest": payload["digest"][:16], "disjoint": payload["disjoint"],
                      "problems": problems,
                      "counts": {n: len(sets[n]["ids"]) for n in sets},
                      "per_stratum": {n: sets[n]["per_stratum"] for n in sets}}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
