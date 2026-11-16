"""Freeze the training, validation and held-out question sets, with a key-free evidence test.

What this does, and why it is a separate step. The comparison needs three disjoint question sets fixed *before*
any of them is run, so that no set can be chosen with knowledge of how an arm performed on it (CLAUDE.md section 5,
"Experimental discipline"). This script fixes them from the released split manifests, records the rule and the
resulting ids, and then asks one further question of each selected item:

    is the evidence the question needs actually present in the text the actor is given?

That test reads the question and the parsed documents. It never reads the answer key -- not to decide eligibility,
not to decide a replacement, not even to describe an item. `_assert_no_key_access()` enforces this against this
file's own source at import time, in the same style as `tests/test_officeqa_answer_key_isolation.py`.

Selection rule OQA-SEL-2 (extends OQA-SEL-1, which fixed the 12 training questions on 2026-11-15):

  1. The pool for a set is the released split manifest: training from `train`, validation from `val`, held-out
     from `test`. The three sets are therefore disjoint by construction, and the held-out set comes from the
     split upstream reserved for testing.
  2. Drop items whose `source_files` and `source_docs` counts differ: the supplied-page builder pairs those two
     lists positionally, so a mismatch means the supplied page cannot be identified. (This is OQA-SEL-1's filter.)
  3. Stratify by `category` (easy / hard) x source count (single / multi).
  4. Order within a stratum by numeric uid.
  5. Fill the set by round-robin over the strata in the fixed order (easy,single), (easy,multi), (hard,single),
     (hard,multi), taking the next unused item of each. A stratum that runs out is skipped and the shortfall is
     recorded rather than silently rebalanced.
  6. Evidence test. An item whose verdict is ABSENT is replaced by the next item of the SAME stratum, in the same
     id order. Only ABSENT replaces: a chart mention, a short page, or an untestable item never does.

usage:
  python3 oqa_freeze_sets.py plan                       # manifests only: candidate ids + the documents they need
  python3 oqa_freeze_sets.py evidence --csv X --docs-root Y --out e.json
  python3 oqa_freeze_sets.py finalize --evidence e.json --out frozen.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import oqa_materialize as MAT  # noqa: E402  (the frozen OQA-SEL-1 training selection lives there)
import oqa_obligations as OB  # noqa: E402

RULE = "OQA-SEL-2"
STRATUM_ORDER = [("easy", "single"), ("easy", "multi"), ("hard", "single"), ("hard", "multi")]
TARGETS = {"train": ("train", 12), "val": ("val", 12), "heldout": ("test", 24)}

# Column names that would be an answer key. This module may read `uid` and `question` and nothing else.
FORBIDDEN_COLUMNS = ("answer", "ground_truth", "gold", "label")


def _assert_no_key_access() -> None:
    """Fail at import if this file ever reads an answer column. Checked against the source, not by convention."""
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        code = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
    for name in FORBIDDEN_COLUMNS:
        for form in ('["%s"]' % name, "['%s']" % name, '.get("%s"' % name, ".get('%s'" % name):
            assert form not in code, (
                "oqa_freeze_sets reads the column %r; the evidence test must be key-free" % name)


# ── the pools ──────────────────────────────────────────────────────────────────

def _lines(value) -> list[str]:
    text = str(value or "").strip()
    return [p.strip() for p in re.split(r"[\r\n]+", text) if p.strip()] if text else []


def _uid_key(item_id: str) -> int:
    digits = re.sub(r"\D", "", item_id)
    return int(digits) if digits else 0


def pool(split: str) -> tuple[list[dict], list[str]]:
    """Integrity-filtered, id-ordered pool for a released split, plus the ids the filter dropped."""
    path = os.path.join(REPO, "data", "officeqa_id_split", split, "items.json")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    keep, dropped = [], []
    for row in sorted(rows, key=lambda r: _uid_key(str(r["id"]))):
        if len(_lines(row.get("source_files"))) != len(_lines(row.get("source_docs"))):
            dropped.append(str(row["id"]))
        else:
            keep.append(row)
    return keep, dropped


def stratum_of(row: dict) -> tuple[str, str]:
    n = len(_lines(row.get("source_files")))
    return str(row.get("category")), ("single" if n == 1 else "multi")


def strata(rows: list[dict]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {s: [] for s in STRATUM_ORDER}
    for row in rows:
        out.setdefault(stratum_of(row), []).append(str(row["id"]))
    return out


def pick(split: str, size: int, exclude: set[str]) -> dict:
    """Round-robin over the strata in the fixed order; record the per-stratum counts and any shortfall."""
    rows, dropped = pool(split)
    buckets = strata(rows)
    cursor = {s: 0 for s in buckets}
    chosen: list[str] = []
    while len(chosen) < size:
        progressed = False
        for s in STRATUM_ORDER:
            if len(chosen) >= size:
                break
            ids = buckets.get(s, [])
            while cursor[s] < len(ids) and (ids[cursor[s]] in exclude or ids[cursor[s]] in chosen):
                cursor[s] += 1
            if cursor[s] < len(ids):
                chosen.append(ids[cursor[s]])
                cursor[s] += 1
                progressed = True
        if not progressed:
            break
    per_stratum = {f"{c}/{k}": sum(1 for i in chosen if i in buckets.get((c, k), [])) for c, k in STRATUM_ORDER}
    return {"split": split, "target": size, "ids": chosen, "per_stratum": per_stratum,
            "shortfall": size - len(chosen), "integrity_dropped": dropped,
            "pool_sizes": {f"{c}/{k}": len(buckets.get((c, k), [])) for c, k in STRATUM_ORDER},
            "cursor_after": {f"{c}/{k}": cursor.get((c, k), 0) for c, k in STRATUM_ORDER}}


def next_in_stratum(split: str, stratum: tuple[str, str], used: set[str]) -> str | None:
    """The next unused item of one stratum, in id order. This is how an ABSENT item is replaced."""
    rows, _ = pool(split)
    for item_id in strata(rows).get(stratum, []):
        if item_id not in used:
            return item_id
    return None


def plan(extra_exclude: set[str] | None = None) -> dict:
    """The candidate sets. Training is OQA-SEL-1's frozen P12; validation and held-out are picked here."""
    train_ids = list(MAT.SELECTION["P12"])
    # Everything already seen by this study is barred from validation and held-out, although the splits are
    # disjoint anyway: B3 was used for the baseline check, P12 is the training set.
    seen = set(train_ids) | set(MAT.SELECTION["B3"]) | set(MAT.SELECTION["EXCLUDED"]) | set(extra_exclude or ())
    val = pick("val", 12, seen)
    heldout = pick("test", 24, seen | set(val["ids"]))
    rows, dropped = pool("train")
    buckets = strata(rows)
    train = {"split": "train", "target": 12, "ids": train_ids,
             "per_stratum": {f"{c}/{k}": sum(1 for i in train_ids if i in buckets.get((c, k), []))
                             for c, k in STRATUM_ORDER},
             "shortfall": 0, "integrity_dropped": dropped,
             "note": "frozen 2026-11-15 as OQA-SEL-1 before any question text was visible"}
    return {"rule": RULE, "train": train, "val": val, "heldout": heldout}


def needed_documents(ids_by_set: dict) -> dict:
    """Which parsed bulletins each set needs, so a sparse corpus can be fetched instead of the full 1.25 GB."""
    out = {}
    for name, (split, _) in TARGETS.items():
        rows, _ = pool(split)
        index = {str(r["id"]): r for r in rows}
        files: set[str] = set()
        for item_id in ids_by_set.get(name, {}).get("ids", []):
            files.update(_lines(index[item_id].get("source_files")))
        out[name] = sorted(files)
    return out


# ── the evidence test ──────────────────────────────────────────────────────────

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*$", re.I)
_PERCENT = re.compile(r"\d[\d.,]*\s*(?:%|percent)", re.I)
_QUOTED = re.compile(r'"([^"]{3,60})"')


def _norm(tok: str) -> str:
    return tok.replace(",", "").rstrip(".")


def candidate_values(text: str, question: str) -> list[str]:
    """Numeric tokens in the text that could be the evidence an answer is read from.

    Two kinds of number are not evidence and are removed:
      * a number the QUESTION already states (a period, a page label, a rounding instruction) - the actor is told
        it, so finding it in the text establishes nothing;
      * a year, or a day-of-month following a month name - those locate a period rather than measure anything.
    What remains is a value that had to be retrieved from the document.
    """
    stated = {_norm(m.group(0)) for m in _NUM.finditer(question)}
    out: list[str] = []
    for m in _NUM.finditer(text):
        tok, norm = m.group(0), _norm(m.group(0))
        if norm in stated:
            continue
        if norm.isdigit():
            value = int(norm)
            if len(norm) == 4 and 1700 <= value <= 2100:
                continue
            if value <= 31 and _MONTH.search(text[max(0, m.start() - 16):m.start()]):
                continue
        out.append(tok)
    return out


def term_hits(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    return [t for t in terms if len(t) > 3 and t in low]


def _best_lines(text: str, terms: list[str], question: str, limit: int = 3) -> list[dict]:
    """Lines that pair at least two of the question's content words with a retrieved value."""
    scored = []
    for line in text.splitlines():
        hits = term_hits(line, terms)
        if len(hits) < 2:
            continue
        values = candidate_values(line, question)
        if not values:
            continue
        scored.append({"hits": len(hits), "values": len(values), "line": line.strip()[:200]})
    scored.sort(key=lambda d: (-d["hits"], -d["values"]))
    return scored[:limit]


def assess(question: str, page_text: str, doc_text: str) -> dict:
    """Is the evidence this question needs present in the supplied text, or in the document the tools can read?

    Verdicts:
      AVAILABLE            the supplied page carries both the question's subject words and a retrieved value.
      AVAILABLE_VIA_TOOLS  the supplied page does not, but the document does, on a line that pairs them; the
                           actor can reach it with grep/read, so the item is answerable.
      UNCERTAIN            values are present but nothing pairs them with the question's subject words. Tables
                           keep labels in header rows, so this is common and is NOT grounds for exclusion.
      ABSENT               neither the supplied page nor the document holds any retrievable value for this
                           question. This is the only verdict that excludes an item.
    """
    ob = OB.derive(question)
    terms = ob["measure_terms"]
    wants_date = ob["answer_kind"]["kind"] == "date"
    page_values = candidate_values(page_text, question)
    doc_values = candidate_values(doc_text, question)
    page_terms = term_hits(page_text, terms)
    doc_terms = term_hits(doc_text, terms)
    page_lines = _best_lines(page_text, terms, question)
    doc_lines = _best_lines(doc_text, terms, question)
    # A question that quotes a term depends on that exact wording appearing in the document. If the quoted term
    # occurs nowhere in the whole parsed bulletin, the thing the question refers to is not in the text.
    quoted = [q for q in _QUOTED.findall(question) if not q.isdigit()]
    quoted_missing = [q for q in quoted if q.lower() not in doc_text.lower()]

    if wants_date:
        page_ok = bool(re.search(r"\b(19|20)\d{2}\b", page_text) or
                       re.search(r"\b(january|february|march|april|may|june|july|august|september|october|"
                                 r"november|december)\b", page_text, re.I))
        verdict = "AVAILABLE" if (page_ok and page_terms) else ("UNCERTAIN" if page_ok else "ABSENT")
    elif page_lines:
        verdict = "AVAILABLE"
    elif page_values and page_terms:
        verdict = "AVAILABLE"
    elif doc_lines:
        verdict = "AVAILABLE_VIA_TOOLS"
    elif doc_values:
        verdict = "UNCERTAIN"
    else:
        verdict = "ABSENT"

    # Independent of the counts: if the page holds no retrievable value at all AND a term the question puts in
    # quotation marks is absent from the entire document, the referenced content is not textual.
    if not page_values and quoted_missing:
        verdict = "ABSENT"

    return {
        "verdict": verdict,
        "answer_kind": ob["answer_kind"]["kind"],
        "requested_scale": ob["scale"]["scale"],
        "page_chars": len(page_text),
        "page_candidate_values": len(page_values),
        "page_values_sample": page_values[:8],
        "page_term_hits": page_terms,
        "page_percent_tokens": len(_PERCENT.findall(page_text)),
        "page_evidence_lines": page_lines,
        "doc_chars": len(doc_text),
        "doc_candidate_values": len(doc_values),
        "doc_term_hits": doc_terms,
        "doc_evidence_lines": doc_lines,
        "quoted_terms": quoted,
        "quoted_terms_absent_from_document": quoted_missing,
        "references_figure": bool(re.search(r"\b(chart|figure|graph|diagram)\b", question, re.I)),
    }


def load_questions(csv_path: str, ids: set[str]) -> dict:
    """uid -> question, for the requested ids. No other column of the gated CSV is read."""
    actual = os.path.getsize(csv_path)
    if actual != MAT.PINNED_CSV_BYTES:
        raise SystemExit(f"{csv_path} is {actual} bytes; the pinned revision {MAT.PINNED_REVISION[:7]} is "
                         f"{MAT.PINNED_CSV_BYTES}")
    out = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            uid = str(row.get("uid") or "").strip()
            if uid in ids:
                out[uid] = str(row.get("question") or "").strip()
    return out


_BLOCK_HEADER = re.compile(r"^### .+ page \d+\s*$")
_SOURCE_URL = re.compile(r"^Source URL:\s*\S+\s*$")
_PREAMBLE = "The following content is pre-parsed from the oracle"


def page_body(rendered: str) -> str:
    """The parsed page text only, with the wrapper the harness prints around it removed.

    The rendered block carries a preamble sentence, a `### <file> page <n>` heading and a `Source URL:` line.
    Those are harness metadata, and their numbers (a file stem's year, a FRASER title id, the `?page=` index) are
    not document evidence. Leaving them in made a chart-only page look as though it held five retrievable values.
    """
    kept = []
    for line in rendered.splitlines():
        if _BLOCK_HEADER.match(line) or _SOURCE_URL.match(line) or line.startswith(_PREAMBLE):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def supplied_text(row: dict, docs_roots: list[str]) -> str:
    """The oracle parsed pages exactly as the actor is given them, via SkillOpt's own builder."""
    from skillopt.envs.officeqa.tool_runtime import build_oracle_parsed_pages_context
    rendered = build_oracle_parsed_pages_context(row.get("source_files"), row.get("source_docs"), docs_roots)
    return page_body(rendered)


def document_text(row: dict, docs_roots: list[str]) -> str:
    """The whole parsed .txt of each source file: what grep and read can reach."""
    parts = []
    for name in _lines(row.get("source_files")):
        for root in docs_roots:
            for candidate in (os.path.join(root, "transformed", name), os.path.join(root, name)):
                if os.path.isfile(candidate):
                    with open(candidate, encoding="utf-8", errors="replace") as f:
                        parts.append(f.read())
                    break
            else:
                continue
            break
    return "\n".join(parts)


def run_evidence(csv_path: str, docs_root: str, candidates: dict) -> dict:
    """Assess every selected item, plus the next replacement candidate of each stratum."""
    docs_roots = [docs_root]
    index: dict[str, dict] = {}
    for name, (split, _) in TARGETS.items():
        rows, _ = pool(split)
        index[name] = {str(r["id"]): r for r in rows}

    wanted: list[tuple[str, str]] = []
    for name in TARGETS:
        for item_id in candidates[name]["ids"]:
            wanted.append((name, item_id))
    # Replacement candidates: the next two unused items of every stratum, so a replacement can be assessed in the
    # same pass rather than needing a second trip to the gated CSV.
    used = {i for _, i in wanted} | set(MAT.SELECTION["B3"]) | set(MAT.SELECTION["EXCLUDED"])
    for name, (split, _) in TARGETS.items():
        rows, _ = pool(split)
        buckets = strata(rows)
        for stratum in STRATUM_ORDER:
            taken = 0
            for item_id in buckets.get(stratum, []):
                if item_id in used or taken >= 2:
                    continue
                wanted.append((f"{name}:spare", item_id))
                used.add(item_id)
                taken += 1

    questions = load_questions(csv_path, {i for _, i in wanted})
    results = {}
    for name, item_id in wanted:
        row = index[name.split(":")[0]].get(item_id)
        question = questions.get(item_id, "")
        if row is None or not question:
            results[item_id] = {"set": name, "verdict": "UNTESTED", "why": "manifest row or question missing"}
            continue
        page = supplied_text(row, docs_roots)
        doc = document_text(row, docs_roots)
        if not doc:
            results[item_id] = {"set": name, "verdict": "UNTESTED",
                                "why": f"documents not fetched: {_lines(row.get('source_files'))}"}
            continue
        entry = assess(question, page, doc)
        entry["set"] = name
        entry["stratum"] = "/".join(stratum_of(row))
        entry["question_chars"] = len(question)
        entry["source_files"] = _lines(row.get("source_files"))
        results[item_id] = entry
    return results


# ── freezing ───────────────────────────────────────────────────────────────────

def digest(frozen: dict) -> str:
    payload = {"rule": RULE, "train": frozen["train"]["ids"], "val": frozen["val"]["ids"],
               "heldout": frozen["heldout"]["ids"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finalize(candidates: dict, evidence: dict) -> dict:
    """Replace every ABSENT item with the next item of its stratum; record each substitution."""
    frozen = {"rule": RULE, "sets": {}, "substitutions": [], "untested": []}
    used = set()
    for name in TARGETS:
        used.update(candidates[name]["ids"])
    used.update(MAT.SELECTION["B3"])
    used.update(MAT.SELECTION["EXCLUDED"])

    for name, (split, size) in TARGETS.items():
        ids = []
        for item_id in candidates[name]["ids"]:
            entry = evidence.get(item_id, {})
            if entry.get("verdict") == "ABSENT":
                rows, _ = pool(split)
                index = {str(r["id"]): r for r in rows}
                stratum = stratum_of(index[item_id])
                replacement = next_in_stratum(split, stratum, used)
                frozen["substitutions"].append({
                    "set": name, "removed": item_id, "stratum": "/".join(stratum),
                    "reason": "the evidence test found no retrievable value for this question in the supplied "
                              "page or anywhere in the parsed document",
                    "evidence": {k: entry.get(k) for k in
                                 ("page_chars", "page_candidate_values", "page_term_hits", "doc_candidate_values",
                                  "quoted_terms_absent_from_document", "references_figure")},
                    "replacement": replacement,
                    "replacement_verdict": evidence.get(replacement, {}).get("verdict", "UNTESTED"),
                })
                if replacement:
                    used.add(replacement)
                    ids.append(replacement)
            else:
                ids.append(item_id)
                if entry.get("verdict") == "UNTESTED":
                    frozen["untested"].append({"set": name, "id": item_id, "why": entry.get("why")})
        counts: dict[str, int] = {}
        rows, _ = pool(split)
        index = {str(r["id"]): r for r in rows}
        for item_id in ids:
            key = "/".join(stratum_of(index[item_id]))
            counts[key] = counts.get(key, 0) + 1
        frozen["sets"][name] = {
            "split": split, "target": size, "count": len(ids), "ids": ids,
            "per_stratum": {f"{c}/{k}": counts.get(f"{c}/{k}", 0) for c, k in STRATUM_ORDER},
            "shortfall": size - len(ids),
            "verdicts": {vid: evidence.get(vid, {}).get("verdict", "UNTESTED") for vid in ids},
        }
    frozen["digest"] = digest({k: frozen["sets"][k] for k in TARGETS})
    frozen["disjoint"] = (len(set(frozen["sets"]["train"]["ids"]) | set(frozen["sets"]["val"]["ids"]) |
                              set(frozen["sets"]["heldout"]["ids"]))
                          == sum(len(frozen["sets"][n]["ids"]) for n in TARGETS))
    return frozen


def main() -> int:
    _assert_no_key_access()
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["plan", "evidence", "finalize"])
    ap.add_argument("--csv")
    ap.add_argument("--docs-root")
    ap.add_argument("--evidence")
    ap.add_argument("--out")
    args = ap.parse_args()

    candidates = plan()
    if args.command == "plan":
        docs = needed_documents(candidates)
        payload = {"rule": RULE, "candidates": candidates, "documents": docs,
                   "document_counts": {k: len(v) for k, v in docs.items()},
                   "all_documents": sorted({d for v in docs.values() for d in v})}
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        print(json.dumps({"rule": RULE,
                          "sets": {k: {"ids": candidates[k]["ids"], "per_stratum": candidates[k]["per_stratum"],
                                       "shortfall": candidates[k]["shortfall"]} for k in TARGETS},
                          "document_counts": payload["document_counts"],
                          "distinct_documents": len(payload["all_documents"])}, indent=2))
        return 0

    if args.command == "evidence":
        if not (args.csv and args.docs_root and args.out):
            raise SystemExit("evidence needs --csv, --docs-root and --out")
        results = run_evidence(args.csv, args.docs_root, candidates)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"rule": RULE, "candidates": candidates, "evidence": results}, f, indent=2)
        tally: dict[str, int] = {}
        for entry in results.values():
            tally[entry.get("verdict", "?")] = tally.get(entry.get("verdict", "?"), 0) + 1
        print(json.dumps({"assessed": len(results), "verdicts": tally,
                          "absent": [i for i, e in results.items() if e.get("verdict") == "ABSENT"],
                          "untested": [i for i, e in results.items() if e.get("verdict") == "UNTESTED"]}, indent=2))
        return 0

    with open(args.evidence, encoding="utf-8") as f:
        loaded = json.load(f)
    frozen = finalize(loaded["candidates"], loaded["evidence"])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(frozen, f, indent=2)
    print(json.dumps(frozen, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
