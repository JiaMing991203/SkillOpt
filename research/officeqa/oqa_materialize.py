"""Materialize the OfficeQA development questions and a sparse document corpus for the feasibility study.

What it does.
  1. Reads the released split manifest `data/officeqa_id_split/<split>/items.json` (ids plus `source_files` and
     `source_docs` hints; it contains no questions and no answers).
  2. Joins it on `uid` with the gated `officeqa_full.csv` from the Hugging Face dataset `databricks/officeqa`.
  3. Writes only the frozen selection (rule OQA-SEL-1) to a split directory the OfficeQA dataloader can read, as a
     CSV with the columns that `skillopt/envs/officeqa/dataloader.py` normalizes.
  4. Prints the exact list of parsed-document files that selection needs, so a sparse corpus can be fetched instead
     of the full 1.25 GB (`--print-docs`), and verifies a corpus once it is in place (`--verify-docs`).

Access. The dataset is `gated: "auto"`: metadata and the file tree are public, but a download needs an
authenticated token whose holder has accepted the dataset conditions (verified 2026-11-15: an anonymous GET of
`resolve/main/officeqa_full.csv` returns HTTP 401 "Access to dataset databricks/officeqa is restricted"). This
script never fetches anything itself and never takes a token: it reads a CSV that is already on disk, so the
credential stays in the hands of whoever downloads the file.

Answer keys. `ground_truth` is written into the split CSV because the dataloader and the native evaluator need it.
It never reaches the actor (verified in `rollout._build_user`: the user message contains the question, the supplied
pages, the candidate files and the source hints, and no answer field) and it never reaches the contract input
(`oqa_trace.build_packet` reads only the user prompt and the conversation; `oqa_trace.audit_no_gold` checks that).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys

SELECTION_RULE = "OQA-SEL-1"

# The split manifest was built from this revision (data/officeqa_id_split/split_manifest.json), so the CSV must be
# downloaded from it and not from `main`: `main` is a different size (155,772 vs 154,868 bytes, checked 2026-11-15)
# and its rows may not correspond to the manifest's uids.
PINNED_REVISION = "8ecbf18d3833daf4750a903d14963e4c4c1d4cd8"
PINNED_CSV_BYTES = 154868
# Verified 2026-11-15 from the pinned revision's own header:
#   uid,question,answer,source_docs,source_files,difficulty
# The gold column is `answer` (the dataloader also accepts `ground_truth`); `category` comes from the manifest.
PINNED_CSV_HEADER = ("uid", "question", "answer", "source_docs", "source_files", "difficulty")

# Frozen 2026-11-15 from data/officeqa_id_split/train/items.json, before any question or answer was visible.
# Rule: order the train split by id; drop items whose source_files and source_docs counts differ (UID0019);
# stratify by (category, single-source vs multi-source); B3 = first item of (easy,single), (hard,single),
# (hard,multi); P12 = the next three of each of the four strata, disjoint from B3.
SELECTION = {
    "B3": ["UID0002", "UID0007", "UID0018"],
    "P12": ["UID0033", "UID0034", "UID0046", "UID0017", "UID0030", "UID0031",
            "UID0014", "UID0026", "UID0079", "UID0028", "UID0065", "UID0085"],
    "EXCLUDED": ["UID0019"],
    "SHA256": "5d1e27cd32e46902bd5954d373878f9327619380bfb17c5952930b5d692d856d",  # over rule + B3 + P12 + EXCLUDED (selection_digest)
}

CSV_COLUMNS = ["uid", "question", "ground_truth", "category", "source_files", "source_docs", "split"]


def _lines(value) -> list[str]:
    text = str(value or "").strip()
    return [p.strip() for p in re.split(r"[\r\n]+", text) if p.strip()] if text else []


def selection_digest() -> str:
    payload = {"rule": SELECTION_RULE, "b3": SELECTION["B3"], "p12": SELECTION["P12"],
               "excluded": SELECTION["EXCLUDED"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_manifest(repo_root: str, split: str = "train") -> dict:
    path = os.path.join(repo_root, "data", "officeqa_id_split", split, "items.json")
    with open(path, encoding="utf-8") as f:
        return {str(row["id"]): row for row in json.load(f)}


def needed_documents(repo_root: str, which: str) -> list[str]:
    manifest = load_manifest(repo_root)
    ids = SELECTION["B3"] + SELECTION["P12"] if which == "all" else SELECTION[which]
    files: set[str] = set()
    for item_id in ids:
        files.update(_lines(manifest[item_id].get("source_files")))
    return sorted(files)


def materialize(repo_root: str, csv_path: str, out_dir: str, which: str) -> dict:
    """Join the manifest with the gated CSV and write <out_dir>/officeqa_<which>.csv."""
    manifest = load_manifest(repo_root)
    ids = SELECTION["B3"] + SELECTION["P12"] if which == "all" else SELECTION[which]
    actual_bytes = os.path.getsize(csv_path)
    if actual_bytes != PINNED_CSV_BYTES:
        raise SystemExit(
            f"{csv_path} is {actual_bytes} bytes; the pinned revision {PINNED_REVISION[:7]} is "
            f"{PINNED_CSV_BYTES}. Download the CSV from that revision, not from main, or the manifest's uids may "
            f"not correspond to these rows."
        )
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = tuple(reader.fieldnames or ())
        if header != PINNED_CSV_HEADER:
            print(f"note: CSV header is {header}, expected {PINNED_CSV_HEADER}; continuing because the loader "
                  f"accepts both `answer` and `ground_truth`", file=sys.stderr)
        rows = {str(row.get("uid") or row.get("id") or "").strip(): row for row in reader}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise SystemExit(f"these selected ids are absent from {csv_path}: {missing}")

    # SplitDataLoader._load_all_splits requires train/, val/ and test/ to exist and to contain a loadable file.
    # The selection is a TRAIN-split development set, so val/ and test/ get header-only CSVs: present, and empty.
    train_dir = os.path.join(out_dir, "train")
    os.makedirs(train_dir, exist_ok=True)
    for empty_split in ("val", "test"):
        empty_dir = os.path.join(out_dir, empty_split)
        os.makedirs(empty_dir, exist_ok=True)
        with open(os.path.join(empty_dir, "officeqa_empty.csv"), "w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()
    out_path = os.path.join(train_dir, f"officeqa_{which.lower()}.csv")
    written = []
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item_id in ids:
            src, man = rows[item_id], manifest[item_id]
            question = str(src.get("question") or "").strip()
            gold = str(src.get("ground_truth") or src.get("answer") or "").strip()
            if not question or not gold:
                raise SystemExit(f"{item_id}: the CSV row has no question or no ground truth; check the revision")
            writer.writerow({
                "uid": item_id,
                "question": question,
                "ground_truth": gold,
                "category": man.get("category") or src.get("category") or "officeqa",
                # the manifest's hints are authoritative: they are what the released split was built from
                "source_files": "\n".join(_lines(man.get("source_files"))),
                "source_docs": "\n".join(_lines(man.get("source_docs"))),
                "split": which.lower(),
            })
            written.append(item_id)
    meta = {
        "selection_rule": SELECTION_RULE,
        "selection_sha256": selection_digest(),
        "selection_matches_frozen_digest": selection_digest() == SELECTION["SHA256"],
        "which": which,
        "ids": written,
        "out_csv": os.path.abspath(out_path),
        "out_csv_sha256": hashlib.sha256(open(out_path, "rb").read()).hexdigest(),
        "source_csv": os.path.abspath(csv_path),
        "source_csv_bytes": actual_bytes,
        "pinned_revision": PINNED_REVISION,
        "source_csv_sha256": hashlib.sha256(open(csv_path, "rb").read()).hexdigest(),
        "documents_needed": needed_documents(repo_root, which),
    }
    meta["split_dir"] = os.path.abspath(out_dir)
    meta["note"] = ("val/ and test/ are intentionally empty: this is a TRAIN-split development set, and no "
                    "validation or test item is materialized by this script")
    with open(os.path.join(out_dir, f"materialize_{which.lower()}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def verify_docs(repo_root: str, docs_root: str, which: str) -> dict:
    """Check that every needed .txt and its parsed .json are present in a (possibly sparse) corpus."""
    needed = needed_documents(repo_root, which)
    transformed = os.path.join(docs_root, "transformed")
    jsons = os.path.join(docs_root, "jsons")
    report = {"docs_root": os.path.abspath(docs_root), "needed": len(needed),
              "layout_ok": os.path.isdir(transformed) and os.path.isdir(jsons),
              "missing_txt": [], "missing_json": [], "present": []}
    for name in needed:
        stem = name[:-4] if name.endswith(".txt") else name
        txt_ok = os.path.isfile(os.path.join(transformed, name))
        json_ok = os.path.isfile(os.path.join(jsons, stem + ".json"))
        if not txt_ok:
            report["missing_txt"].append(name)
        if not json_ok:
            report["missing_json"].append(stem + ".json")
        if txt_ok and json_ok:
            report["present"].append(name)
    report["verdict"] = "PASS" if report["layout_ok"] and not report["missing_txt"] \
        and not report["missing_json"] else "FAIL"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap.add_argument("--which", choices=["B3", "P12", "all"], default="B3")
    ap.add_argument("--csv", help="local path to the already-downloaded officeqa_full.csv")
    ap.add_argument("--out-dir", help="split directory to write (env.split_dir of the run config)")
    ap.add_argument("--print-docs", action="store_true", help="list the parsed documents the selection needs")
    ap.add_argument("--verify-docs", metavar="DOCS_ROOT", help="check a corpus for the needed txt and json files")
    args = ap.parse_args()
    repo_root = os.path.abspath(args.repo_root)

    if args.print_docs:
        needed = needed_documents(repo_root, args.which)
        print(f"# selection {SELECTION_RULE} ({args.which}): {len(needed)} bulletins, "
              f"{len(needed) * 2} files (one .txt + one parsed .json each)")
        for name in needed:
            stem = name[:-4]
            print(f"treasury_bulletins_parsed/transformed/{name}")
            print(f"treasury_bulletins_parsed/jsons/{stem}.json")
        return 0

    if args.verify_docs:
        print(json.dumps(verify_docs(repo_root, args.verify_docs, args.which), indent=2))
        return 0

    if not args.csv or not args.out_dir:
        ap.error("--csv and --out-dir are required (or use --print-docs / --verify-docs)")
    meta = materialize(repo_root, args.csv, args.out_dir, args.which)
    print(json.dumps(meta, indent=2))
    if not meta["selection_matches_frozen_digest"]:
        print("REFUSED SEMANTICS: the selection no longer matches its frozen digest", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
