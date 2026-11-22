#!/usr/bin/env python3
"""Does the learned rule add anything?  A deterministic 2x2, and a resolver-only reference.

Rule OQA-LN-1, follow-up section 4.

THE DESIGN. Two factors, crossed, with everything else held fixed - the same frozen records, the same
task facts, the same observation-derived facts, the same assumptions, the same stage structure and the
same supplied checking logic:

    extraction : the superseded one  |  the D-2 corrected one
    learned rule : APPLIED            |  ABSENT

ABSENT IS NOT FALSE. With no rule there is no prediction, so nothing is refuted and nothing is
witnessed; every fact the permitted observations establish on their own is untouched. That is what
`oqa_ln_check._applies` returns `None` for.

THE RESOLVER-ONLY REFERENCE. The question this answers is whether the learner supplies information
beyond approximating labels the pipeline already computes. `classify_operand` reads
`verdict["sourced"]` - the SUPPLIED resolver's own label - and the learned rule reads behavioural
correlates of that same label. So the reference condition is the rule-absent condition, and it is
LABELLED SUPPLIED CHECKING LOGIC, not learning and not a replacement research design. It is not
weakened to make the learner look better: it is the identical stage code with the rule removed.

FOUR KINDS OF DIFFERENCE, which the instruction asks to be kept apart:
  NEW_KNOWLEDGE        a conclusion present only with the rule, that the resolver could not reach.
  RECEIPT_ONLY         a difference that exists only because the code wants a learned-rule receipt.
  ANNOTATION_ONLY      a provenance or wording change with no diagnostic difference.
  ALREADY_SUPPLIED     a finding the supplied resolver reaches on its own.
"""
from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_vocab as V  # noqa: E402

SCHEMA = "lso.officeqa_d2_rule.v1"

NEW_KNOWLEDGE = "NEW_KNOWLEDGE"
RECEIPT_ONLY = "RECEIPT_ONLY"
ANNOTATION_ONLY = "ANNOTATION_ONLY"
ALREADY_SUPPLIED = "ALREADY_SUPPLIED"

CONDITIONS = (
    ("superseded_extraction/rule_applied", False, True),
    ("superseded_extraction/rule_absent", False, False),
    ("D2_extraction/rule_applied", True, True),
    ("D2_extraction/rule_absent", True, False),
)


def _attempt_rows(freeze_doc: dict) -> list:
    wanted = {r["attempt_id"] for r in freeze_doc["recovery_controls"]}
    return [a for a in freeze_doc["attempts"]
            if a["role"] == "check" or a["attempt_id"] in wanted]


def run(freeze_doc: dict, contract: dict, n_learn_operands: int, model_sha256: str) -> dict:
    rows = _attempt_rows(freeze_doc)
    cells: dict = {}
    for name, cells_only, rule_on in CONDITIONS:
        reports = [CK.stage_report(freeze_doc, a, contract if rule_on else None,
                                   numeric_cells_only=cells_only) for a in rows]
        rendered = {
            r["attempt_id"]: CK.render_report(
                r, n_learn_operands=n_learn_operands,
                contract_rendered=(V.render(contract) if rule_on else ""),
                model_sha256=(model_sha256 if rule_on else ""))
            for r in reports}
        cells[name] = {"reports": {r["attempt_id"]: r for r in reports}, "rendered": rendered}

    out: dict = {"schema": SCHEMA, "n_attempts": len(rows),
                 "contract": f"{contract['id']}@v{contract['version']}",
                 "contract_sha256": IR.sha(IR.canon(contract)),
                 "conditions": [c[0] for c in CONDITIONS],
                 "obligation_states": {}, "operand_classes": {},
                 "rule_effect": [], "diagnosis_counts": {}, "report_text": {}}

    for name in cells:
        states = collections.Counter()
        classes = collections.Counter()
        counts = collections.Counter()
        for r in cells[name]["reports"].values():
            for o in r["obligations"]:
                states[(o["obligation"], o["state"])] += 1
            for o in r["supporting_operands"]:
                classes[o["class"]] += 1
            for k, v in r["diagnosis_counts"].items():
                counts[k] += v
        out["obligation_states"][name] = {f"{a}/{b}": n for (a, b), n in sorted(states.items())}
        out["operand_classes"][name] = dict(sorted(classes.items()))
        out["diagnosis_counts"][name] = dict(sorted(counts.items()))
        out["report_text"][name] = {
            "total_chars": sum(len(t) for t in cells[name]["rendered"].values()),
            "sha256_per_attempt": {k: IR.sha(v) for k, v in
                                   sorted(cells[name]["rendered"].items())},
        }

    # ── the rule's effect, attempt by attempt, under each extraction ────────
    for ext, on_name, off_name in (("superseded_extraction",
                                    "superseded_extraction/rule_applied",
                                    "superseded_extraction/rule_absent"),
                                   ("D2_extraction",
                                    "D2_extraction/rule_applied",
                                    "D2_extraction/rule_absent")):
        for aid in sorted(cells[on_name]["reports"]):
            on, off = cells[on_name]["reports"][aid], cells[off_name]["reports"][aid]
            on_states = {o["obligation"]: o["state"] for o in on["obligations"]}
            off_states = {o["obligation"]: o["state"] for o in off["obligations"]}
            moved = [{"obligation": k, "with_the_rule": on_states[k],
                      "without_it": off_states.get(k)}
                     for k in on_states if on_states[k] != off_states.get(k)]
            on_cls = [o["class"] for o in on["supporting_operands"]]
            off_cls = [o["class"] for o in off["supporting_operands"]]
            same_text = (cells[on_name]["rendered"][aid].replace(V.render(contract), "")
                         == cells[off_name]["rendered"][aid]
                         .replace("NO LEARNED RULE WAS APPLIED. Every state above comes from the "
                                  "supplied resolver and the supplied stage logic.", ""))
            kind = (NEW_KNOWLEDGE if moved and any(
                        m["with_the_rule"] not in ("UNKNOWN",) for m in moved)
                    else RECEIPT_ONLY if moved
                    else ALREADY_SUPPLIED if on_cls == off_cls
                    else ANNOTATION_ONLY)
            out["rule_effect"].append({
                "extraction": ext, "attempt_id": aid,
                "obligations_moved": moved,
                "operand_classes_identical": on_cls == off_cls,
                "operand_classes": on_cls,
                "classification": (ALREADY_SUPPLIED if not moved and on_cls == off_cls else kind),
                "report_text_identical_apart_from_the_rule_block": same_text,
            })

    # ── the one warning the paired rehearsal actually carried ──────────────
    out["the_rehearsal_warning"] = _warning_trace(cells)
    out["summary"] = _summarise(out)
    out["self_sha256"] = IR.sha(json.dumps(
        {k: v for k, v in out.items() if k != "self_sha256"}, sort_keys=True, default=str))
    return out


def _warning_trace(cells: dict) -> dict:
    """Where `source_traced`'s unresolved-operand warning comes from, condition by condition.

    This is the specific line the paired rehearsal's assisted analyst used: "one operand (86,000) was
    not uniquely traceable". The rehearsal item is p1/UID0063.
    """
    aid = "p1/UID0063"
    out = {}
    for name, cell in cells.items():
        r = cell["reports"].get(aid)
        if r is None:
            continue
        ob = [o for o in r["obligations"] if o["obligation"] == "source_traced"]
        unresolved = [o for o in r["supporting_operands"] if not o.get("origin_settled", True)]
        out[name] = {
            "source_traced_state": ob[0]["state"] if ob else None,
            "source_traced_provenance": ob[0]["provenance"] if ob else None,
            "unresolved_operands": [o["operand"] for o in unresolved],
            "unresolved_reason": [o.get("origin_why") for o in unresolved],
            "learned_rule_applied": r["learned_rule_applied"],
        }
    out["reading"] = (
        "The warning names the operand and says why its origin is not settled. Both come from "
        "`classify_operand`, which reads the SUPPLIED resolver's verdict and the supplied dependency "
        "structure. The learned rule is not consulted to produce it. Its only possible effect on this "
        "obligation is to SUPPRESS it - a refutation of the rule sets the obligation to UNKNOWN - so "
        "removing the rule cannot remove the warning.")
    return out


def _summarise(out: dict) -> dict:
    kinds = collections.Counter(e["classification"] for e in out["rule_effect"])
    moved = [e for e in out["rule_effect"] if e["obligations_moved"]]
    return {
        "effects_by_kind": dict(kinds),
        "n_attempts_where_the_rule_changed_any_state": len(moved),
        "attempts_changed": [e["attempt_id"] for e in moved],
        "verdict": ("NO DEMONSTRATED LEARNED INCREMENT: every state the rule-applied condition "
                    "reports is reached by the supplied resolver without it"
                    if not moved else
                    "the rule changed at least one obligation state; see rule_effect for which and "
                    "in which direction"),
    }
