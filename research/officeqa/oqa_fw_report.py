#!/usr/bin/env python3
"""The report the ported framework delivers to the optimizer, and its binding to the checked result.

WHAT A REPORT HERE MAY AND MAY NOT SAY. It carries, per written operand: the citation of the cell it
came from, the obligation states, WHAT EACH STATE DOES AND DOES NOT ESTABLISH, and what the state RESTS
ON - the learned rule ids and the adopted assumptions. It never carries a score, an answer key, a
held-out record or the skill. The same answer-key audit the frozen campaign's reports pass
(`oqa_trace.audit_no_gold`) is run here and a failure raises rather than warns.

THE BINDING between the checked structured result and the delivered text is a pair of digests, and it
is the thing that makes a report attributable at all:
  trace_sha256  the conversation and prompt the report was derived from;
  model_sha256  the frozen model whose rules and assumptions licensed its conclusions.
A report is valid for that (attempt, model version) pair and no other. The campaign's own cache key
already includes both, which is why a second epoch cannot be handed a first-epoch report.

CITATIONS WITHOUT LINE NUMBERS. Every citation is (step, table, row label, column label) from the
observation's own structure, so a cell from an unnumbered `read` can be cited. The LN generation could
cite only line-numbered grep output, and reads carry three quarters of this corpus's tables.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import fw_ir as IR  # noqa: E402
import oqa_fw_domain as DOM  # noqa: E402
import oqa_fw_extract as E  # noqa: E402
import oqa_fw_stage as ST  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_trace as TR  # noqa: E402

CONDITION = "FW"
HEADING = "Document Interpretation and Stage Report"
SCHEMA = "lso.officeqa_framework_report.v1"
MAX_ROWS = 14

QUALIFICATION = """\
What this report is. Three obligations per written number, resolved from the SAVED RECORD of this
attempt alone. It reads no score, no answer key and no other attempt.

What each state means:
  DISCHARGED    the record establishes it.
  OUTSTANDING   the record establishes that something the question required was not done.
  UNKNOWN       the record does not settle it. This is not a fault of the agent and not a discharge.
  NOT_REQUIRED  the question does not ask for it.

Where a conclusion comes from. Every obligation names what it RESTS ON. `learned` is an empirical rule
proposed by the learner and accepted only with supporting cases and no counterexample in the permitted
records; `assumptions` are named readings of document structure that are adopted, not established. A
conclusion resting on either can be withdrawn by a later counterexample, and when it is withdrawn the
obligation becomes UNKNOWN - never a complaint about the agent.

What it does NOT establish. Not that the cited cell carries the measure, period or column the question
asks about; not that the committed answer is correct; not that any state caused the outcome.
"""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def structured(pred_root: str, item_id: str, question: str, model_path: str) -> dict:
    """The CHECKED STRUCTURED RESULT: what the rendered report is bound to, digest and all."""
    model = IR.load_frozen(model_path)
    a = EV.read_attempt(pred_root, item_id)
    steps = [{"i": i, "kind": k, "cmd": c, "obs": o, "text": t}
             for (i, k, c, o, t) in EV._flat(a["items"])]
    ents = E.document_entities(steps)
    ents = E.operand_entities(steps, ents)
    import re
    m = re.search(r"\b(thousand|million|billion)s?\b", (question or ""), re.I)
    dom = DOM.OfficeQADomain(entities=ents, asked_scale=(m.group(1).lower() + "s") if m else None,
                             model=model)
    if model["vocab_digest"] != dom.digest():
        raise RuntimeError("the frozen model was built against another vocabulary")
    est = dom.estimator(None, steps, model["assumptions"])
    rep = ST.attempt_report(est, len(steps), dom)
    doc = {
        "schema": SCHEMA, "condition": CONDITION, "item": item_id,
        "trace_sha256": a["trace_sha256"], "model_sha256": model.get("sha256"),
        "model_version": model["version"], "vocab_digest": model["vocab_digest"],
        "assumptions": list(model["assumptions"]),
        "contracts": [{"id": c["id"], "status": c.get("status"), "rendered": IR.render(c, dom),
                       "evidence": {k: v for k, v in (c.get("evidence") or {}).items()
                                    if k in ("applicable", "n_support", "n_counter", "n_consistent",
                                             "undetermined")}}
                      for c in model["contracts"]],
        "asked_scale": dom.asked_scale, "counts": rep["counts"], "n_operands": rep["n_operands"],
        "accusations": rep["accusations"], "learned_ids": rep["learned_ids"],
        "assumptions_used": rep["assumptions_used"],
        "operands": rep["operands"],
    }
    doc["result_sha256"] = _sha(json.dumps({k: v for k, v in doc.items() if k != "result_sha256"},
                                           sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode())
    return doc


def render(doc: dict) -> str:
    """The delivered text. Every line is derived from `doc`, and the digest of `doc` is printed in it."""
    L = [f"## {HEADING}", "", QUALIFICATION.strip(), ""]
    L.append(f"Attempt `{doc['item']}` - trace `{doc['trace_sha256'][:16]}`, "
             f"learned model v{doc['model_version']} `{(doc['model_sha256'] or '')[:16]}`, "
             f"checked result `{doc['result_sha256'][:16]}`.")
    L.append("")
    L.append("### The learned model this report used")
    if not doc["contracts"]:
        L.append("- (empty model: no learned rule, so every interpretation obligation below is UNKNOWN)")
    for c in doc["contracts"]:
        ev = c["evidence"]
        L.append(f"- `{c['id']}` [{c['status']}] {c['rendered']}")
        L.append(f"  - support {ev.get('n_support', 0)}, counterexamples {ev.get('n_counter', 0)}, "
                 f"consistent {ev.get('n_consistent', 0)}, applicable {ev.get('applicable', 0)}, "
                 f"undetermined {ev.get('undetermined', 0)} (permitted records seen so far)")
    L.append(f"- adopted assumptions: {', '.join(doc['assumptions']) or '(none)'}")
    L.append("")
    L.append("### What the question asks for")
    L.append(f"- money scale asked: {doc['asked_scale'] or '(none stated in the question)'}")
    L.append("")
    L.append("### Obligations, per written number")
    for name, (stage, means, notmeans) in ST.OBLIGATIONS.items():
        c = doc["counts"].get(name) or {}
        L.append(f"- **{name}** (stage {stage}): "
                 + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
        L.append(f"  - establishes: {means}")
        L.append(f"  - does NOT establish: {notmeans}")
    L.append("")
    interesting = [r for r in doc["operands"]
                   if any(r["obligations"][n]["state"] in (ST.OUTSTANDING, ST.DISCHARGED)
                          for n in ST.OBLIGATIONS)]
    L.append(f"### The {min(len(interesting), MAX_ROWS)} resolved numbers "
             f"(of {doc['n_operands']} written; the rest are UNKNOWN and listed as such below)")
    if not interesting:
        L.append("- none: no written number had a resolved column or scale in this attempt.")
    for r in interesting[:MAX_ROWS]:
        L.append(f"- `{r['text']}` at step {r['step']} -> {r['citation']}")
        for n in ST.OBLIGATIONS:
            ob = r["obligations"][n]
            rest = ob.get("rests_on") or {}
            dep = []
            if rest.get("learned"):
                dep.append("learned " + ", ".join(rest["learned"]))
            if rest.get("assumptions"):
                dep.append("assuming " + ", ".join(rest["assumptions"]))
            L.append(f"  - {n}: **{ob['state']}** - {ob['why']}"
                     + (f"  [rests on: {'; '.join(dep)}]" if dep else ""))
    unknown = [r for r in doc["operands"] if r not in interesting]
    if unknown:
        why = {}
        for r in unknown:
            w = r["obligations"]["column_identified"]["why"]
            why[w] = why.get(w, 0) + 1
        L.append("")
        L.append(f"### The {len(unknown)} numbers this report could not resolve, and why")
        for w, n in sorted(why.items(), key=lambda kv: -kv[1])[:4]:
            L.append(f"- {n}x {w}")
        L.append("- These are gaps in the RECORD or in this checker, not findings about the agent.")
    return "\n".join(L) + "\n"


def build_one_fw(pred_root: str, item_id: str, question: str, results_path: str,
                 model_path: str) -> tuple:
    """The signature `skillopt.gradient.reflect` already calls for, so the existing epoch integration
    needs one dispatch line and no new plumbing."""
    packet = TR.build_packet(pred_root, item_id, question=question)
    if not packet.get("steps"):
        raise ValueError(f"{item_id}: no conversation events were recorded at {pred_root}")
    audit = TR.audit_no_gold(packet, results_path)
    if audit["verdict"] != "PASS":
        raise AnswerKeyLeak(f"{item_id}: {audit.get('problems')}")
    doc = structured(pred_root, item_id, question, model_path)
    text = render(doc)
    record = {"id": item_id, "condition": CONDITION, "heading": HEADING,
              "trace_sha256": doc["trace_sha256"], "model_sha256": doc["model_sha256"],
              "result_sha256": doc["result_sha256"], "report_sha256": _sha(text.encode()),
              "n_operands": doc["n_operands"], "counts": doc["counts"],
              "accusations": doc["accusations"], "learned_ids": doc["learned_ids"],
              "report_chars": len(text)}
    return text, record


class AnswerKeyLeak(RuntimeError):
    pass


def verify_binding(text: str, record: dict, doc: dict) -> dict:
    """Is the delivered TEXT the one the checked result produced? Re-renders and compares digests."""
    again = render(doc)
    return {"result_sha256": doc["result_sha256"],
            "report_sha256_recorded": record["report_sha256"],
            "report_sha256_rerendered": _sha(again.encode()),
            "bound": _sha(again.encode()) == record["report_sha256"] == _sha(text.encode()),
            "trace_sha256": doc["trace_sha256"], "model_sha256": doc["model_sha256"]}
