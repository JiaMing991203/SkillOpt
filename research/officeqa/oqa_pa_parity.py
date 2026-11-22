#!/usr/bin/env python3
"""PA-1: the implementation-parity probes for the OfficeQA learned contract.

Amendment D-6. OFFLINE and READ-ONLY with respect to every existing artifact: this module reads the
frozen records and the two saved learned models and writes new JSON into its own output directory.
No provider call, no learner call, no GPU, nothing in the ALFWorld worktree.

Two questions, each answered by a measurement rather than by reading the source:

  callpath()  WHERE does the learned contract enter a conclusion? It runs every frozen attempt's
              stage report twice - once with the contract and once with it ABSENT - counting the
              calls to `oqa_ln_learn.applies` and to `oqa_ln_check.classify_operand`, and recording
              which top-level fields of the report differ. "Absent" is the checker's own `None`
              path, not a mutilated copy, so both sides come from the same code.

  target()    IS the learned rule an empirical relationship, or a theorem of the supplied resolver?
              The rule is `operand_positions(=1) AND operand_source_tool(grep) -> operand_sourced`.
              `oqa_occ.corroborations` sets QUERY when the occurrence's pattern tokens meet its line
              tokens, and QUERY alone makes a unique position SUPPORTED, which IS `operand_sourced`.
              A grep hits a line BECAUSE its pattern occurs there. This counts how often that holds.

Neither probe reads a score, an answer key, a held-out record or a validation record.
"""
from __future__ import annotations

import collections
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_ln_learn as LN  # noqa: E402
import oqa_occ as OCC  # noqa: E402

SCHEMA = "lso.officeqa_parity"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _seal(doc: dict) -> dict:
    """A CONTENT digest: the clock is excluded, so two identical measurements digest identically."""
    doc["self_sha256"] = hashlib.sha256(
        IR.canon({k: v for k, v in doc.items() if k != "written_utc"}).encode("utf-8")).hexdigest()
    return doc


# ── PA-1: the call path ──────────────────────────────────────────────────────
def callpath(freeze: dict, model: dict) -> dict:
    contract = IR.active(model)[0]
    log = collections.Counter()
    decisions: list = []

    real_applies, real_classify = LN.applies, CK.classify_operand

    def applies_spy(c, features):
        log["applies_calls"] += 1
        v, why = real_applies(c, features)
        log["applies_true" if v is True else "applies_false" if v is False else "applies_none"] += 1
        return v, why

    def classify_spy(operand, verdict, dep_row, q_nums):
        log["classify_calls"] += 1
        # Is the contract, its id, its version or its conclusion reachable from what the classifier
        # is handed? If not, the classification cannot depend on the learned rule.
        blob = IR.canon({"operand": operand, "verdict": verdict, "dep_row": dep_row})
        if any(n in blob for n in (contract["id"], "operand_sourced", "LEARNED", "contract")):
            log["classify_saw_contract"] += 1
        out = real_classify(operand, verdict, dep_row, q_nums)
        decisions.append({
            "operand": operand["as_written"], "class": out["class"], "settled": out["settled"],
            "decided_by": ("the resolver's verdict['sourced']" if verdict["sourced"]
                           else "the dependency row" if (dep_row or {}).get("candidates")
                           else "the operand's own features, the question, or verdict['match']")})
        return out

    LN.applies, CK.classify_operand = applies_spy, classify_spy
    try:
        per_attempt = []
        for row in freeze["attempts"]:
            before = collections.Counter(log)
            with_rule = CK.stage_report(freeze, row, contract)
            mid = collections.Counter(log)
            without = CK.stage_report(freeze, row, None)

            def states(st):
                return {o["obligation"]: o["state"] for o in st["obligations"]}

            def classes(st):
                return [(o["operand"], o["class"], o["origin_settled"])
                        for o in st.get("supporting_operands") or []]

            per_attempt.append({
                "attempt_id": row["attempt_id"],
                "applies_calls_rule_applied": log["applies_calls"] - before["applies_calls"],
                "applies_calls_rule_absent": log["applies_calls"] - mid["applies_calls"],
                "classify_calls_rule_applied": log["classify_calls"] - before["classify_calls"],
                "classify_calls_rule_absent": log["classify_calls"] - mid["classify_calls"],
                "states_equal": states(with_rule) == states(without),
                "operand_classes_equal": classes(with_rule) == classes(without),
                "states": states(with_rule),
                "fields_that_differ": sorted(
                    k for k in set(with_rule) | set(without)
                    if IR.canon(with_rule.get(k)) != IR.canon(without.get(k))),
            })
    finally:
        LN.applies, CK.classify_operand = real_applies, real_classify

    return _seal({
        "schema": SCHEMA + ".callpath.v1", "written_utc": _utc(),
        "question": "where does the learned contract enter a conclusion?",
        "freeze_sha256": freeze["self_sha256"], "model_sha256": model.get("sha256"),
        "contract": f"{contract['id']}@v{contract['version']}",
        "contract_sha256": IR.sha(IR.canon(contract)),
        "n_attempts": len(freeze["attempts"]),
        "totals": dict(log),
        "n_attempts_reaching_the_operand_loop": sum(
            1 for p in per_attempt if p["applies_calls_rule_applied"]),
        "all_obligation_states_equal": all(p["states_equal"] for p in per_attempt),
        "all_operand_classes_equal": all(p["operand_classes_equal"] for p in per_attempt),
        "fields_that_ever_differ": sorted({k for p in per_attempt for k in p["fields_that_differ"]}),
        "classifier_decisions": decisions[:16],
        "per_attempt": per_attempt,
    })


# ── PA-2: is the target empirical, or a theorem of the supplied resolver? ────
def target(freeze: dict) -> dict:
    tab: collections.Counter = collections.Counter()
    examples: list = []
    for row in freeze["attempts"]:
        pdir = os.path.join(EV.source_root(freeze), "train_p%d" % row["pass"], "predictions")
        att = EV.read_attempt(pdir, row["item"])
        occ = OCC.observed_occurrences(att["items"], numeric_cells_only=True)
        binding = OCC.bind(att["items"], numeric_cells_only=True)
        # An occurrence carries `pattern_tokens` only when the call that returned it carried a
        # pattern, i.e. when it was a grep. A read has none.
        pat: dict = {}
        for o in occ:
            pat.setdefault((o["document"], o["line"], o["field"]),
                           (bool(o["pattern_tokens"]), o["pattern_tokens"], o["line_tokens"]))
        for bd in binding["bindings"]:
            if bd["match"] != OCC.UNIQUE_POSITION:
                continue
            p = bd["position"]
            grep, ptok, ltok = pat.get((p["document"], p["line"], p["field"]), (None, [], []))
            tab[("grep" if grep else "read_or_none",
                 "QUERY" if "QUERY" in bd["corroborations"] else "-",
                 "SUPPORTED" if bd["supporting"] else "unknown")] += 1
            if grep and len(examples) < 4:
                examples.append({"attempt": row["attempt_id"], "value": bd["value"], "position": p,
                                 "pattern_tokens": sorted(set(ptok))[:8],
                                 "pattern_tokens_also_on_the_line": sorted(set(ptok) & set(ltok))[:8],
                                 "corroborations": bd["corroborations"],
                                 "field_text": bd.get("field_text")})

    def tot(pred):
        return sum(v for k, v in tab.items() if pred(k))

    g = tot(lambda k: k[0] == "grep")
    r = tot(lambda k: k[0] != "grep")
    return _seal({
        "schema": SCHEMA + ".target.v1", "written_utc": _utc(),
        "question": "is `operand_positions(=1) AND operand_source_tool(grep) -> operand_sourced` an "
                    "empirical relationship about the documents, or a theorem of oqa_occ?",
        "freeze_sha256": freeze["self_sha256"],
        "scope": "every actor-written value with exactly one observed numeric cell, over all frozen "
                 "attempts, under the corrected (D-2) extraction",
        "contingency": {" | ".join(k): v for k, v in sorted(tab.items())},
        "from_a_grep_hit": {"n": g, "QUERY_fired": tot(lambda k: k[0] == "grep" and k[1] == "QUERY"),
                            "SUPPORTED": tot(lambda k: k[0] == "grep" and k[2] == "SUPPORTED")},
        "from_a_read": {"n": r, "SUPPORTED": tot(lambda k: k[0] != "grep" and k[2] == "SUPPORTED")},
        "P_supported_given_QUERY": (tot(lambda k: k[1] == "QUERY" and k[2] == "SUPPORTED")
                                    / max(1, tot(lambda k: k[1] == "QUERY"))),
        "examples_grep": examples,
    })


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    with open(a.freeze, encoding="utf-8") as f:
        freeze = json.load(f)
    with open(a.model, encoding="utf-8") as f:
        model = json.load(f)
    os.makedirs(a.out, exist_ok=True)
    for name, doc in (("PA1_CALLPATH.json", callpath(freeze, model)),
                      ("PA2_TARGET.json", target(freeze))):
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        print(f"{name}  sha256 {doc['self_sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
