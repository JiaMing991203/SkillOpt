#!/usr/bin/env python3
"""The bounded end-to-end test of the ported framework on OfficeQA. Rule OQA-FW-1.

  oqa_fw_run.py learn|check|ablate|repair|all --out DIR [--freeze FR] [--legacy-tolerance]

WHAT IT DOES, in the order the instruction asks for it:
  learn   builds evidence from the permitted LEARN-role records (minus one reserved attempt), starts
          from the EMPTY model, lets the search proposer offer hypotheses in order of generality, and
          applies them through the unchanged gate. Freezes the result.
  check   loads the frozen model and checks it on the RESERVED attempt and on the check-role records,
          prefix-only, with the model's digest verified first.
  ablate  the same stage report twice over all 48 attempts - with the frozen model, and with the model
          ABSENT (not false: absent) - and diffs the obligation states, not just the text.
  repair  the natural counterexample path, and then a clearly labelled CONSTRUCTED fixture that
          exercises the DELETION repair the OfficeQA CC/LN generation could not express.

THE INFORMATION BOUNDARY. Only `conversation.json` and the two saved prompts of each attempt are read.
Never `ground_truth`, `soft`, `hard`, a provider's alternate reasoning field, a skill, the validation
split or any held-out record. No network call is made from this module at all.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import fw_check as K  # noqa: E402
import fw_ir as IR  # noqa: E402
import fw_learn as LN  # noqa: E402
import fw_repair as RP  # noqa: E402
import oqa_fw_domain as DOM  # noqa: E402
import oqa_fw_extract as E  # noqa: E402
import oqa_fw_learn as PROP  # noqa: E402
import oqa_fw_stage as ST  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402

RULE = "OQA-FW-1"
SCHEMA = "lso.officeqa_framework_run.v1"
FREEZE = "/Users/yaojiaming/dev/lean_skillopt/runs/20261121-officeqa-learner/v1_freeze/LN_FREEZE.json"
LOCAL_ROOT = "/Users/yaojiaming/dev/lean_skillopt/runs/20261121-officeqa-two-baselines/s0"
RESERVED = "p3/UID0028"          # held back from learning; checked under the frozen model
SUBJECT, CONSEQUENCE = "column", "col_label_agrees_flat"
_ASK = re.compile(r"\b(thousand|million|billion)s?\b", re.I)
_NOT_CONTENT = ("written_utc", "self_sha256")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def seal(doc: dict) -> dict:
    """A content digest that excludes the clock, so re-deriving the artifact reproduces the digest."""
    doc["written_utc"] = _utc()
    doc["self_sha256"] = sha(json.dumps({k: v for k, v in doc.items() if k not in _NOT_CONTENT},
                                        sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return doc


def jdump(doc: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    return doc.get("self_sha256", "")


# ── records ───────────────────────────────────────────────────────────────────
def asked_scale(user_prompt: str):
    m = _ASK.search(EV.question_of(user_prompt) or "")
    return (m.group(1).lower() + "s") if m else None


def load_attempt(root: str, att: dict, *, legacy_tolerance: bool = False) -> dict:
    a = EV.read_attempt(os.path.join(root, f"train_p{att['pass']}", "predictions"), att["item"])
    steps = [{"i": i, "kind": k, "cmd": c, "obs": o, "text": t}
             for (i, k, c, o, t) in EV._flat(a["items"])]
    ents = E.document_entities(steps)
    ents = E.operand_entities(steps, ents, legacy_tolerance=legacy_tolerance)
    return {"episode_id": att["attempt_id"], "item": att["item"], "pass": att["pass"],
            "role": att["role"], "task": EV.question_of(a["user_prompt"]),
            "task_type": None, "steps": steps, "entities": ents,
            "asked_scale": asked_scale(a["user_prompt"]),
            "trace_sha256": a["trace_sha256"], "frozen_trace_sha256": att["trace_sha256"]}


def load_all(freeze_path: str, *, legacy_tolerance: bool = False) -> tuple:
    fz = json.load(open(freeze_path))
    root = os.environ.get("OQA_LN_SOURCE_ROOT") or LOCAL_ROOT
    recs, mismatched = [], []
    for att in fz["attempts"]:
        r = load_attempt(root, att, legacy_tolerance=legacy_tolerance)
        if r["trace_sha256"] != r["frozen_trace_sha256"]:
            mismatched.append(r["episode_id"])
        recs.append(r)
    return fz, recs, mismatched


def domain_for(rec: dict, model=None):
    return DOM.OfficeQADomain(entities=rec["entities"], asked_scale=rec["asked_scale"], model=model)


class MultiDomain(DOM.OfficeQADomain):
    """One domain over several records: `fw_check.Evidence` asks the domain for each episode's
    estimator, so the per-record entity tables are selected by episode id here rather than by having
    the caller hold a different domain per record."""

    def __init__(self, recs, model=None):
        super().__init__(model=model)
        self.recs = {r["episode_id"]: r for r in recs}

    def estimator(self, task_type, steps, assumptions=(), persists=()):
        rid = None
        for k, r in self.recs.items():
            if r["steps"] is steps:
                rid = k
                break
        rec = self.recs[rid] if rid else next(iter(self.recs.values()))
        self.entities = rec["entities"]
        self.asked_scale = rec["asked_scale"]
        est = DOM.OfficeQAEstimator.of_steps(self, task_type, steps, assumptions, persists)
        est.ents = rec["entities"]
        est.model_rules = self.licence_rules()
        return est


def evidence_of(recs: list, model=None) -> tuple:
    dom = MultiDomain(recs, model=model)
    ev = K.Evidence(dom, [{"episode_id": r["episode_id"], "task": r["task"],
                           "task_type": r["task_type"], "steps": r["steps"]} for r in recs])
    return dom, ev


# ── overlap reporting ─────────────────────────────────────────────────────────
def overlap(recs: list, learn_ids: list, check_ids: list) -> dict:
    def qs(ids):
        return sorted({i.split("/")[1] for i in ids})

    def docs(ids):
        out = set()
        for r in recs:
            if r["episode_id"] not in ids:
                continue
            for s in r["steps"]:
                m = re.search(r"path\s*=\s*'([^']*)'", str(s.get("cmd") or ""))
                if m:
                    out.add(os.path.basename(m.group(1)))
        return sorted(out)

    lq, cq = qs(learn_ids), qs(check_ids)
    ld, cd = docs(learn_ids), docs(check_ids)
    reps = {}
    for i in learn_ids + check_ids:
        reps.setdefault(i.split("/")[1], []).append(i)
    return {
        "learn_attempts": sorted(learn_ids), "check_attempts": sorted(check_ids),
        "learn_questions": lq, "check_questions": cq,
        "question_overlap": sorted(set(lq) & set(cq)),
        "documents_learn": ld, "documents_check": cd,
        "document_overlap": sorted(set(ld) & set(cd)),
        "repeated_attempts_of_one_question": {q: v for q, v in sorted(reps.items()) if len(v) > 1},
        "note": ("A reserved ATTEMPT of a learned question is a weaker confirmation than a new "
                 "question: the documents and the question are the same and only the trajectory "
                 "differs. It is stated here rather than left for a reader to infer."),
    }


# ── learn ─────────────────────────────────────────────────────────────────────
def cmd_learn(args) -> dict:
    fz, recs, mismatched = load_all(args.freeze, legacy_tolerance=args.legacy_tolerance)
    learn = [r for r in recs if r["role"] == "learn" and r["episode_id"] != RESERVED]
    dom, ev = evidence_of(learn)
    model = IR.empty_model(dom)
    doc = {"schema": SCHEMA, "rule": RULE, "phase": "learn", "domain_digest": dom.digest(),
           "freeze": {"path": args.freeze, "self_sha256": fz["self_sha256"]},
           "trace_mismatches": mismatched, "reserved_attempt": RESERVED,
           "evidence": {"n_attempts": len(learn), "attempts": sorted(r["episode_id"] for r in learn)},
           "model_start": {"version": model["version"], "contracts": 0, "assumptions": []},
           "batches": []}

    # batch 1: the assumption the records can test, proposed on its own so its decision is separable.
    prop_a = {"assumption_ops": [
        {"op": "adopt", "name": "index_alignment",
         "reason": "the sub-label row has one cell per column in every split table of these records, "
                   "so merging it with the parent row by index is well defined"},
        {"op": "adopt", "name": "table_scale_applies_to_rows",
         "reason": "a scale stated in a table heading is the scale of the numbers below it"},
    ], "contract_ops": []}
    model, dec = LN.apply_proposal(model, prop_a, ev, 1, "search", dom)
    doc["batches"].append({"batch": 1, "what": "assumption operations", "proposal": prop_a,
                           "decisions": dec, "assumptions_after": model["assumptions"]})

    # batch 2: the contract search. Every candidate is scored; the most general acceptable one wins.
    sp = PROP.SearchProposer(dom, SUBJECT, CONSEQUENCE)
    prop_c = sp.propose(model, ev)
    model, dec = LN.apply_proposal(model, prop_c, ev, 2, "search", dom)
    refuted_general = [r for r in prop_c["trail"] if not r["acceptable"] and r["n_counter"]]
    doc["batches"].append({
        "batch": 2, "what": "contract search",
        "search": {"n_candidates": prop_c["n_candidates"], "n_acceptable": prop_c["n_acceptable"],
                   "most_general_refuted": [
                       {"rendered": r["rendered"], "n_support": r["n_support"],
                        "n_counter": r["n_counter"], "gate": r["gate"],
                        "counter": r["counter"], "counter_witness": r["counter_witness"]}
                       for r in refuted_general[:6]],
                   "accepted_candidates": [
                       {"rendered": r["rendered"], "n_conjuncts": r["n_conjuncts"],
                        "n_support": r["n_support"], "undetermined": r["undetermined"],
                        "applicable": r["applicable"]}
                       for r in prop_c["trail"] if r["acceptable"]][:8]},
        "proposal_ops": prop_c["contract_ops"], "decisions": dec})
    doc["model"] = model
    doc["active"] = [{"id": c["id"], "rendered": IR.render(c, dom), "evidence": c.get("evidence"),
                      "status": c.get("status")} for c in model["contracts"]]
    path = os.path.join(args.out, "FW_MODEL.json")
    os.makedirs(args.out, exist_ok=True)
    if os.path.exists(path):
        os.chmod(path, 0o644)
        os.remove(path)
    doc["model_sha256"] = IR.freeze(model, path)
    doc["model_frozen_at"] = path
    seal(doc)
    jdump(doc, os.path.join(args.out, "FW_LEARN.json"))
    return doc


# ── check ─────────────────────────────────────────────────────────────────────
def cmd_check(args) -> dict:
    fz, recs, mismatched = load_all(args.freeze, legacy_tolerance=args.legacy_tolerance)
    model = IR.load_frozen(os.path.join(args.out, "FW_MODEL.json"))
    learn_ids = [r["episode_id"] for r in recs if r["role"] == "learn" and r["episode_id"] != RESERVED]
    held = [r for r in recs if r["role"] == "check" or r["episode_id"] == RESERVED]
    dom, ev = evidence_of(held, model=model)
    if model["vocab_digest"] != dom.digest():
        raise RuntimeError("the frozen model was built against another vocabulary")
    doc = {"schema": SCHEMA, "rule": RULE, "phase": "check",
           "model": {"version": model["version"], "sha256": model.get("sha256"),
                     "assumptions": model["assumptions"],
                     "active": [{"id": c["id"], "rendered": IR.render(c, dom)}
                                for c in IR.active(model)]},
           "overlap": overlap(recs, learn_ids, [r["episode_id"] for r in held]),
           "trace_mismatches": mismatched, "per_contract": [], "per_attempt": []}
    for c in IR.active(model, "concludes"):
        sc = K.score(c, ev, model["assumptions"], IR.active(model, "persists"), dom)
        doc["per_contract"].append({
            "id": c["id"], "rendered": IR.render(c, dom),
            "prospective": {"applicable": sc["applicable"], "support": len(sc["support"]),
                            "counter": len(sc["counter"]), "consistent": len(sc["consistent"]),
                            "undetermined": sc["undetermined"], "neutral": sc["neutral"],
                            "counter_ids": sc["counter"][:8], "support_ids": sc["support"][:8]},
            "learning_evidence": c.get("evidence")})
    for r in held:
        d2 = domain_for(r, model=model)
        est = d2.estimator(r["task_type"], r["steps"], model["assumptions"])
        t = len(r["steps"])
        rows = K.conclusions_at(model, d2, est, t - 1, {}, None) if r["steps"] else []
        cls = {}
        for blk in rows:
            for row in blk["rows"]:
                cls[row["class"]] = cls.get(row["class"], 0) + 1
        doc["per_attempt"].append({"attempt": r["episode_id"], "role": r["role"],
                                   "reserved": r["episode_id"] == RESERVED,
                                   "asked_scale": r["asked_scale"], "classes": cls})
    seal(doc)
    jdump(doc, os.path.join(args.out, "FW_CHECK.json"))
    return doc


# ── ablate ────────────────────────────────────────────────────────────────────
def cmd_ablate(args) -> dict:
    fz, recs, mismatched = load_all(args.freeze, legacy_tolerance=args.legacy_tolerance)
    model = IR.load_frozen(os.path.join(args.out, "FW_MODEL.json"))
    doc = {"schema": SCHEMA, "rule": RULE, "phase": "ablate",
           "condition_present": {"model_version": model["version"], "sha256": model.get("sha256")},
           "condition_absent": "the same code path with model=None: no licence rule, so `col_bound` "
                               "is UNKNOWN. Absent, never false.",
           "per_attempt": [], "totals": {"present": {}, "absent": {}}, "diffs": []}
    for r in recs:
        out = {}
        for cond, m in (("present", model), ("absent", None)):
            d2 = domain_for(r, model=m)
            est = d2.estimator(r["task_type"], r["steps"],
                               (m or {}).get("assumptions") or model["assumptions"])
            out[cond] = ST.attempt_report(est, len(r["steps"]), d2)
        row = {"attempt": r["episode_id"], "role": r["role"], "asked_scale": r["asked_scale"],
               "n_operands": out["present"]["n_operands"],
               "present": out["present"]["counts"], "absent": out["absent"]["counts"],
               "accusations_present": out["present"]["accusations"],
               "accusations_absent": out["absent"]["accusations"],
               "learned_ids_present": out["present"]["learned_ids"],
               "learned_ids_absent": out["absent"]["learned_ids"]}
        changed = []
        for a, b in zip(out["present"]["operands"], out["absent"]["operands"]):
            for name in ST.OBLIGATIONS:
                if a["obligations"][name]["state"] != b["obligations"][name]["state"]:
                    changed.append({"operand": a["operand"], "obligation": name,
                                    "present": a["obligations"][name]["state"],
                                    "absent": b["obligations"][name]["state"],
                                    "citation": a.get("citation"),
                                    "rests_on": a["obligations"][name].get("rests_on")})
        row["n_state_changes"] = len(changed)
        row["changes"] = changed[:6]
        doc["per_attempt"].append(row)
        for cond in ("present", "absent"):
            for name, c in out[cond]["counts"].items():
                tot = doc["totals"][cond].setdefault(name, {})
                for st, n in c.items():
                    tot[st] = tot.get(st, 0) + n
    doc["summary"] = {
        "attempts": len(recs),
        "attempts_with_a_state_change": sum(1 for r in doc["per_attempt"] if r["n_state_changes"]),
        "total_state_changes": sum(r["n_state_changes"] for r in doc["per_attempt"]),
        "accusations_present": sum(r["accusations_present"] for r in doc["per_attempt"]),
        "accusations_absent": sum(r["accusations_absent"] for r in doc["per_attempt"]),
    }
    seal(doc)
    jdump(doc, os.path.join(args.out, "FW_ABLATE.json"))
    return doc


# ── the 2x2 grid: is the contract doing work, or only the assumption? ─────────
def _unconditional(model: dict):
    """The general form the gate REFUSED, as an active rule. Used only to measure what accepting it
    would have produced - it is never written into a frozen model."""
    m = copy.deepcopy(model)
    for c in m["contracts"]:
        if c["kind"] == "concludes" and c.get("status") == "active":
            c["conditions"] = [[{"atom": "table_observed", "args": ["?t"], "value": True}]]
            c["exists"] = {"?t": "table"}
            c["id"] = c["id"] + ".UNCONDITIONAL"
    return m


GRID = (
    ("A_no_assumption_no_rule", "neither the assumption nor any rule: the condition the CC/LN "
                                "generation was effectively in"),
    ("B_assumption_only", "the assumption adopted, NO learned rule - this is the honest ablation of "
                          "the contract alone"),
    ("C_assumption_and_rule", "the frozen learned model: the assumption and the accepted rule"),
    ("D_assumption_and_refused_rule", "the assumption and the GENERAL form the gate refused, to show "
                                      "what accepting it would have bound"),
)


def cmd_grid(args) -> dict:
    fz, recs, mismatched = load_all(args.freeze, legacy_tolerance=args.legacy_tolerance)
    model = IR.load_frozen(os.path.join(args.out, "FW_MODEL.json"))
    uncond = _unconditional(model)
    doc = {"schema": SCHEMA, "rule": RULE, "phase": "grid",
           "cells": {k: v for k, v in GRID}, "totals": {k: {} for k, _ in GRID},
           "bound_columns": {k: 0 for k, _ in GRID}, "per_attempt": []}
    for r in recs:
        row = {"attempt": r["episode_id"], "role": r["role"], "asked_scale": r["asked_scale"]}
        reps = {}
        for cell, _why in GRID:
            m = {"A_no_assumption_no_rule": None, "B_assumption_only": None,
                 "C_assumption_and_rule": model, "D_assumption_and_refused_rule": uncond}[cell]
            A = () if cell == "A_no_assumption_no_rule" else model["assumptions"]
            d2 = domain_for(r, model=m)
            est = d2.estimator(r["task_type"], r["steps"], A)
            rep = ST.attempt_report(est, len(r["steps"]), d2)
            reps[cell] = rep
            row[cell] = rep["counts"]
            for name, c in rep["counts"].items():
                tot = doc["totals"][cell].setdefault(name, {})
                for st, n in c.items():
                    tot[st] = tot.get(st, 0) + n
            n = len(r["steps"])
            doc["bound_columns"][cell] += sum(
                1 for cref in est.entities_of_type(n, "column")
                if est.value(n, "col_bound", [cref])["v"] is True)
        row["C_vs_B_changes"] = sum(
            1 for a, b in zip(reps["C_assumption_and_rule"]["operands"],
                              reps["B_assumption_only"]["operands"])
            for name in ST.OBLIGATIONS
            if a["obligations"][name]["state"] != b["obligations"][name]["state"])
        row["D_vs_C_changes"] = sum(
            1 for a, b in zip(reps["D_assumption_and_refused_rule"]["operands"],
                              reps["C_assumption_and_rule"]["operands"])
            for name in ST.OBLIGATIONS
            if a["obligations"][name]["state"] != b["obligations"][name]["state"])
        doc["per_attempt"].append(row)
    doc["summary"] = {
        "bound_columns": doc["bound_columns"],
        "C_vs_B_total_changes": sum(r["C_vs_B_changes"] for r in doc["per_attempt"]),
        "D_vs_C_total_changes": sum(r["D_vs_C_changes"] for r in doc["per_attempt"]),
        "reading": ("B -> C is what the CONTRACT adds once the assumption is already adopted. "
                    "C -> D is what accepting the refused general form would have changed, which is "
                    "the cost of the condition the gate forced the learner to find."),
    }
    seal(doc)
    jdump(doc, os.path.join(args.out, "FW_GRID.json"))
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("learn", "check", "ablate", "grid", "repair", "all"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--freeze", default=FREEZE)
    ap.add_argument("--legacy-tolerance", action="store_true",
                    help="use oqa_ln_evidence's max(0.5, ...) comparison, to measure the correction")
    a = ap.parse_args(argv)
    phases = ("learn", "check", "ablate", "grid", "repair") if a.phase == "all" else (a.phase,)
    for p in phases:
        fn = {"learn": cmd_learn, "check": cmd_check, "ablate": cmd_ablate,
              "grid": cmd_grid}.get(p)
        if fn is None:
            print(f"[{p}] in oqa_fw_repair.py")
            continue
        d = fn(a)
        print(f"[{p}] {d.get('self_sha256', '')[:16]}  ->  {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
