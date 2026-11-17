"""Fetch the parsed Treasury Bulletin files a frozen question set needs, and nothing else.

WHY. The corpus on the cluster is deliberately SPARSE: the feasibility protocol fetched only the bulletins the
twelve development questions referenced (86 files, 63 MB) instead of the dataset's 1.25 GB. Rule OQA-SEL-3's 108
questions reference 153 distinct bulletins, so most of the new items have no document at all. An item whose
parsed file is absent cannot be answered by any arm: the oracle page comes from `jsons/<stem>.json` and the grep
and read tools from `transformed/<stem>.txt`. Running the campaign without these files would spend the actor
budget on questions that are unanswerable for an avoidable reason.

WHAT THIS DOES. Reads a frozen set record, works out the files its three sets need, skips the ones already on
disk, and downloads the rest from the PINNED dataset revision. Each download is verified for size and recorded
with its sha256. Nothing else in the dataset is touched - no PDFs, no other bulletins, no CSV.

ACCESS. The dataset is gated, so a token is required. It is read from the environment (``HF_TOKEN`` or
``HUGGING_FACE_HUB_TOKEN``), never written to a file, never printed, and never put in the manifest. The script
refuses to run without one rather than failing halfway through.

THIS IS A DOWNLOAD, NOT A PAID CALL. It spends no API budget, needs no GPU and makes no model request.

usage:
  python3 -I research/officeqa/oqa_fetch_docs.py --frozen docs/research/OFFICEQA_FROZEN_SETS_V3.json \\
      --docs-root <corpus root> --out FETCH.json [--plan] [--limit N]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import oqa_materialize as MAT  # noqa: E402

DATASET = "databricks/officeqa"
REVISION = MAT.PINNED_REVISION


def needed_files(frozen_path: str) -> dict:
    """The distinct source files each frozen set needs, from the tracked ID manifest."""
    with open(frozen_path, encoding="utf-8") as f:
        frozen = json.load(f)
    per_set: dict[str, list[str]] = {}
    per_item: dict[str, list[str]] = {}
    for set_name, split in (("train", "train"), ("val", "val"), ("heldout", "test")):
        manifest = MAT.load_manifest(REPO, split)
        files: set[str] = set()
        for item_id in frozen["sets"][set_name]["ids"]:
            row = manifest[item_id]
            names = MAT._lines(row.get("source_files"))
            per_item[item_id] = names
            files.update(names)
        per_set[set_name] = sorted(files)
    every = sorted({n for names in per_set.values() for n in names})
    return {"rule": frozen.get("rule"), "digest": frozen.get("digest"),
            "per_set": per_set, "per_item": per_item, "all": every}


def plan(docs_root: str, names: list[str]) -> dict:
    """What is present, what is missing, and the dataset paths of the missing halves."""
    report = MAT.verify_docs_for(docs_root, names)
    want: list[tuple[str, str]] = []
    for name in report["missing_txt"]:
        want.append((f"treasury_bulletins_parsed/transformed/{name}", os.path.join("transformed", name)))
    for name in report["missing_json"]:
        want.append((f"treasury_bulletins_parsed/jsons/{name}", os.path.join("jsons", name)))
    return {"verify": report, "to_download": want}


def _token() -> str:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    raise SystemExit("no Hugging Face token in the environment (HF_TOKEN); the dataset is gated, so nothing "
                     "can be fetched. Nothing was downloaded.")


def fetch(docs_root: str, want: list[tuple[str, str]], limit: int = 0) -> dict:
    from huggingface_hub import hf_hub_download

    token = _token()
    records = []
    errors = []
    todo = want[:limit] if limit else want
    for repo_path, local_rel in todo:
        target = os.path.join(docs_root, local_rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            cached = hf_hub_download(repo_id=DATASET, filename=repo_path, repo_type="dataset",
                                     revision=REVISION, token=token)
        except Exception as exc:  # noqa: BLE001 - one file's failure must not lose the rest
            errors.append({"file": repo_path, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            continue
        with open(cached, "rb") as src:
            payload = src.read()
        tmp = target + ".part"
        with open(tmp, "wb") as dst:
            dst.write(payload)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, target)
        records.append({"file": local_rel, "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()})
    return {"fetched": records, "errors": errors,
            "bytes_total": sum(r["bytes"] for r in records)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--docs-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plan", action="store_true", help="report what is missing and download nothing")
    ap.add_argument("--limit", type=int, default=0, help="download at most N files (a bounded first trip)")
    args = ap.parse_args()

    need = needed_files(args.frozen)
    before = plan(args.docs_root, need["all"])
    out: dict = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": DATASET, "revision": REVISION,
        "frozen_rule": need["rule"], "frozen_digest": need["digest"],
        "files_needed": len(need["all"]),
        "per_set_files": {k: len(v) for k, v in need["per_set"].items()},
        "before": {"present": len(before["verify"]["present"]),
                   "missing_txt": len(before["verify"]["missing_txt"]),
                   "missing_json": len(before["verify"]["missing_json"]),
                   "verdict": before["verify"]["verdict"]},
        "items_with_no_document": sorted(
            item for item, names in need["per_item"].items()
            if names and all(n in set(before["verify"]["missing_txt"]) for n in names)),
        "plan_only": bool(args.plan),
    }
    if not args.plan:
        out["download"] = fetch(args.docs_root, before["to_download"], limit=args.limit)
        after = plan(args.docs_root, need["all"])
        out["after"] = {"present": len(after["verify"]["present"]),
                        "missing_txt": len(after["verify"]["missing_txt"]),
                        "missing_json": len(after["verify"]["missing_json"]),
                        "verdict": after["verify"]["verdict"],
                        "still_missing_txt": after["verify"]["missing_txt"][:40]}
        out["verdict"] = after["verify"]["verdict"]
    else:
        out["would_download"] = len(before["to_download"])
        out["verdict"] = "PLAN"
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print(json.dumps(out, indent=2, sort_keys=True)[:4000])
    return 0 if out["verdict"] in ("PASS", "PLAN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
