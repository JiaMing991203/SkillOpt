"""The document-availability check for all 108 questions of rule OQA-SEL-3, UID0019 included.

WHAT THIS ANSWERS. For every question in the three frozen sets: is the evidence the question needs actually
present in the text the actor is given, or reachable with the grep and read tools in the parsed document? The
sets were frozen WITHOUT this information - `oqa_sel3.py` reads only the ID manifest, so the draw cannot have
been made with hindsight - and 48 of the 108 items were therefore recorded as UNTESTED. This closes that gap.

IT IS A REPORT, NOT A FILTER. The sets are already frozen and published. An item that turns out to be
unanswerable is recorded and counted, and it lowers every arm's ceiling by the same amount, so it does not bias
the comparison. Replacing it now would be a selection made with knowledge the rule did not have.

UID0019, AND TWO OTHERS LIKE IT. OQA-SEL-1 and OQA-SEL-2 dropped any item whose `source_files` and `source_docs`
counts differ, on the ground that the supplied-page builder pairs those lists positionally. In the 108 items three
have that shape - UID0019 (train), UID0039 (val), UID0064 (held-out) - and all three are ONE file with TWO page
references. `tool_runtime._iter_oracle_refs` has an explicit branch for exactly that case (`len(files) == 1` ->
reuse the single file for every page), so both pages resolve and the supplied page is well defined. The old filter
was over-cautious for this shape. This script records, per item, how many page references the builder actually
resolved, so the claim rests on the builder's own output rather than on reading its source.

A stratum footnote, recorded rather than hidden: `oqa_sel3.stratum` counts documents as
`max(len(source_files), len(source_docs))`, which makes UID0019 "hard/multi", while `oqa_freeze_sets.stratum_of`
counts `len(source_files)` and would call it "hard/single". Both labels appear below. Nothing depends on this -
the stratification is a balancing device whose rule was fixed in advance - but the two scripts do disagree.

KEY-FREE. This module may read `uid` and `question` and nothing else; `_assert_no_key_access` checks that against
this file's own source at import, the same way `oqa_freeze_sets` does. No answer, no score and no trace is read.

usage:
  python3 -I research/officeqa/oqa_avail3.py --csv <officeqa_full.csv> --docs-root <corpus> \\
      --frozen docs/research/OFFICEQA_FROZEN_SETS_V3.json --out AVAILABILITY.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import oqa_freeze_sets as FS  # noqa: E402
import oqa_sel3 as SEL3  # noqa: E402

FORBIDDEN_COLUMNS = ("answer", "ground_truth", "gold", "label")
SPLIT_OF = {"train": "train", "val": "val", "heldout": "test"}


def _assert_no_key_access() -> None:
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        code = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
    for name in FORBIDDEN_COLUMNS:
        for form in ('["%s"]' % name, "['%s']" % name, '.get("%s"' % name, ".get('%s'" % name):
            assert form not in code, f"oqa_avail3 reads the column {name!r}; this check must be key-free"


_assert_no_key_access()


def _manifest(split: str) -> dict:
    """The RELEASED split manifest, unfiltered - so the three files/docs-mismatch items are included."""
    path = os.path.join(REPO, "data", "officeqa_id_split", split, "items.json")
    with open(path, encoding="utf-8") as f:
        return {str(row["id"]): row for row in json.load(f)}


def _previous_verdicts() -> dict:
    path = os.path.join(REPO, "docs", "research", "OFFICEQA_EVIDENCE_CHECK.json")
    try:
        with open(path, encoding="utf-8") as f:
            return {k: str(v.get("verdict") or "") for k, v in json.load(f).items()}
    except OSError:
        return {}


def assess_all(csv_path: str, docs_root: str, frozen_path: str) -> dict:
    from skillopt.envs.officeqa.tool_runtime import _iter_oracle_refs

    with open(frozen_path, encoding="utf-8") as f:
        frozen = json.load(f)
    wanted: list[tuple[str, str]] = []
    for set_name in ("train", "val", "heldout"):
        for item_id in frozen["sets"][set_name]["ids"]:
            wanted.append((set_name, item_id))
    questions = FS.load_questions(csv_path, {i for _, i in wanted})
    previous = _previous_verdicts()
    docs_roots = [docs_root]

    results: dict[str, dict] = {}
    for set_name, item_id in wanted:
        row = _manifest(SPLIT_OF[set_name]).get(item_id)
        question = questions.get(item_id, "")
        files = FS._lines((row or {}).get("source_files"))
        docs = FS._lines((row or {}).get("source_docs"))
        entry: dict = {
            "set": set_name,
            "stratum_sel3": SEL3.stratum(row) if row else "",
            "stratum_freeze_sets": "/".join(FS.stratum_of(row)) if row else "",
            "source_files": files,
            "n_source_files": len(files),
            "n_source_docs": len(docs),
            "files_docs_count_mismatch": len(files) != len(docs),
            "previous_verdict": previous.get(item_id, ""),
        }
        if row is None or not question:
            entry.update({"verdict": "UNTESTED",
                          "why": "the manifest row or the question text is missing"})
            results[item_id] = entry
            continue
        refs = _iter_oracle_refs(row.get("source_files"), row.get("source_docs"))
        entry["oracle_page_refs_resolved"] = len(refs)
        entry["oracle_pages"] = [{"file": f, "page": p} for f, p, _ in refs]
        present = [n for n in files
                   if os.path.isfile(os.path.join(docs_root, "transformed", n))
                   or os.path.isfile(os.path.join(docs_root, n))]
        entry["documents_present"] = len(present)
        entry["documents_absent"] = [n for n in files if n not in present]
        page = FS.supplied_text(row, docs_roots)
        doc = FS.document_text(row, docs_roots)
        if not doc:
            entry.update({"verdict": "NO_DOCUMENT",
                          "why": f"the parsed document is not in this corpus: {files}",
                          "page_chars": len(page)})
            results[item_id] = entry
            continue
        entry.update(FS.assess(question, page, doc))
        entry["question_chars"] = len(question)
        entry["supplied_page_built"] = bool(page)
        results[item_id] = entry
    return results


def summarize(results: dict) -> dict:
    by_set: dict[str, dict] = {}
    for item_id, entry in results.items():
        bucket = by_set.setdefault(entry["set"], {"n": 0, "verdicts": {}, "no_document": [], "absent": [],
                                                  "uncertain": []})
        bucket["n"] += 1
        verdict = entry.get("verdict", "UNTESTED")
        bucket["verdicts"][verdict] = bucket["verdicts"].get(verdict, 0) + 1
        if verdict == "NO_DOCUMENT":
            bucket["no_document"].append(item_id)
        elif verdict == "ABSENT":
            bucket["absent"].append(item_id)
        elif verdict == "UNCERTAIN":
            bucket["uncertain"].append(item_id)
    changed = sorted(
        {item for item, e in results.items()
         if e.get("previous_verdict") and e["previous_verdict"] != e.get("verdict")})
    mismatch = sorted({item for item, e in results.items() if e.get("files_docs_count_mismatch")})
    unresolved = sorted({item for item in mismatch
                         if results[item].get("oracle_page_refs_resolved", 0) < results[item]["n_source_docs"]})
    answerable = sum(1 for e in results.values()
                     if e.get("verdict") in ("AVAILABLE", "AVAILABLE_VIA_TOOLS"))
    return {
        "n_items": len(results),
        "by_set": by_set,
        "answerable": answerable,
        "not_answerable": sorted(i for i, e in results.items()
                                 if e.get("verdict") in ("ABSENT", "NO_DOCUMENT")),
        "verdict_changed_since_the_previous_check": changed,
        "files_docs_count_mismatch_items": mismatch,
        "mismatch_items_whose_pages_did_not_all_resolve": unresolved,
        "untested_remaining": sorted(i for i, e in results.items() if e.get("verdict") == "UNTESTED"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--docs-root", required=True)
    ap.add_argument("--frozen", default=os.path.join(REPO, "docs", "research", "OFFICEQA_FROZEN_SETS_V3.json"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    results = assess_all(args.csv, args.docs_root, args.frozen)
    summary = summarize(results)
    problems = []
    if summary["untested_remaining"]:
        problems.append(f"{len(summary['untested_remaining'])} items still have no verdict")
    if summary["mismatch_items_whose_pages_did_not_all_resolve"]:
        problems.append("a files/docs-mismatch item lost a page reference: "
                        f"{summary['mismatch_items_whose_pages_did_not_all_resolve']}")
    if summary["by_set"].get("train", {}).get("no_document"):
        problems.append("training items have no parsed document: "
                        f"{summary['by_set']['train']['no_document']}")
    out = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": SEL3.RULE,
        "csv": os.path.abspath(args.csv),
        "docs_root": os.path.abspath(args.docs_root),
        "frozen": os.path.abspath(args.frozen),
        "summary": summary,
        "problems": problems,
        "verdict": "PASS" if not problems else "PROBLEMS",
        "items": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    printable = {k: v for k, v in out.items() if k != "items"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
