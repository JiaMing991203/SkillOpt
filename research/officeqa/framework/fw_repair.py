#!/usr/bin/env python3
"""Counterexample repair with regression checking against earlier evidence.

PORT PROVENANCE. The mechanism of `upstream/rp1/rp_run.py` + `rp_eval.py`: a refuted contract, its
counterexample witnesses, candidate repairs, the ORDINARY gate, and a regression check over the evidence
that already held. RP-1 drove the proposal step with a model; this module also offers an enumerator so
the machinery can be exercised with no provider call, which is what the OfficeQA demonstration uses.

WHAT A REPAIR MAY NOT DO, and these are the two failure modes worth naming:
  * it may not pass by weakening the checker. Every candidate goes through `fw_learn.apply_proposal`
    unchanged, so a repair faces exactly the gate a first proposal faces.
  * it may not buy the new counterexample by losing old support. `regression` re-scores the repaired
    contract over the evidence the ORIGINAL had, and a candidate that loses support or gains a
    counterexample anywhere is reported as a regression even if the gate would accept it.

CANDIDATE SHAPES, in the order a counterexample suggests them:
  drop_conjunct    DELETE a condition. The one repair ALFWorld's RP-1 actually accepted was a deletion
                   (both of its attempts dropped the same monotone conjunct), and the OfficeQA CC/LN
                   `repair` could only append, so it could not express the repair that worked.
  narrow           ADD a condition that excludes the counterexample.
  restrict_scope   ADD a task-type restriction.
  retire           withdraw the rule. Always a legitimate outcome; a refuted rule is a finding.
"""
from __future__ import annotations

import copy

import fw_check as K
import fw_ir as IR
import fw_learn as LN


def _cnf_field(c: dict) -> str:
    return {"concludes": "conditions"}.get(c["kind"], "condition")


def schema_only(c: dict) -> dict:
    """The contract's SCHEMA fields alone.

    A stored contract carries bookkeeping - status, batch, version, parent, evidence, sha256 - and
    `fw_ir.validate` rejects unexpected fields, so a repair built from the stored object is refused for
    the wrong reason. `normalize` keeps exactly the schema and nothing else.
    """
    return IR.normalize(c)


def candidates(c: dict, dom, extra_conjuncts=()) -> list:
    """Every repair this framework can express for `c`, as proposal operations with a plain-language why.

    `extra_conjuncts` are clauses the caller (a learner, or a fixture) offers as narrowings; the
    deletions and the retirement are enumerated from the contract's own shape.
    """
    out = []
    cid = c["id"]
    c = schema_only(c)
    f = _cnf_field(c)
    cnf = c.get(f) or []
    for i, clause in enumerate(cnf):
        if len(cnf) <= 1:
            break
        try:
            nc = IR.drop_conjunct(c, i)
        except (IndexError, ValueError):
            continue
        out.append({"shape": "drop_conjunct", "index": i,
                    "why": f"delete the conjunct {IR.render_cnf([clause])}",
                    "op": {"op": "revise", "target": cid, "contract": nc}})
    for clause in extra_conjuncts:
        out.append({"shape": "narrow", "clause": clause,
                    "why": f"require also {IR.render_cnf([list(clause)])}",
                    "op": {"op": "revise", "target": cid,
                           "contract": IR.narrow(c, list(clause))}})
    out.append({"shape": "retire", "why": "withdraw the rule: the counterexample stands",
                "op": {"op": "retire", "target": cid}})
    return out


def regression(original: dict, repaired: dict, ev, assumptions, persists, dom, label_fn=None) -> dict:
    """Did the repair keep what already held? Scores both over the SAME evidence and diffs the case sets."""
    a = K.score(schema_only(original), ev, assumptions, persists, dom, label_fn)
    b = K.score(repaired, ev, assumptions, persists, dom, label_fn)
    lost = sorted(set(a["support"]) - set(b["support"]))
    gained_cx = sorted(set(b["counter"]) - set(a["counter"]))
    fixed_cx = sorted(set(a["counter"]) - set(b["counter"]))
    return {
        "before": {"support": len(a["support"]), "counter": len(a["counter"]),
                   "undetermined": a["undetermined"], "consistent": len(a["consistent"])},
        "after": {"support": len(b["support"]), "counter": len(b["counter"]),
                  "undetermined": b["undetermined"], "consistent": len(b["consistent"])},
        "support_lost": lost[:12], "n_support_lost": len(lost),
        "counterexamples_fixed": fixed_cx[:12], "n_fixed": len(fixed_cx),
        "counterexamples_gained": gained_cx[:12], "n_gained": len(gained_cx),
        "newly_undetermined": b["undetermined"] - a["undetermined"],
        "clean": not lost and not gained_cx and bool(fixed_cx),
    }


def attempt(model: dict, cid: str, ev, dom, batch: int, source: str, extra_conjuncts=(),
            label_fn=None) -> dict:
    """Try every candidate repair of `cid` through the ordinary gate; report all of them, accept none
    silently. Returns {contract, candidates: [...], accepted: <index or None>, model: <next model>}.

    The caller decides what to do with it. Nothing here edits a gate, a score or a checker.
    """
    cur = LN._find(model, cid)
    if cur is None:
        return {"contract": cid, "error": "not an active or refuted contract", "candidates": [],
                "accepted": None, "model": model}
    base = copy.deepcopy(cur)
    rows, viable, out_model = [], [], model
    for j, cand in enumerate(candidates(base, dom, extra_conjuncts)):
        m2, dec = LN.apply_proposal(model, {"contract_ops": [dict(cand["op"], reason=cand["why"],
                                                                  source=source)]},
                                    ev, batch, source, dom, label_fn)
        mine = [d for d in dec if d.get("type") == "contract" and d.get("target") == cid
                and d.get("op") == cand["op"]["op"]]
        d = mine[-1] if mine else {}
        row = {"shape": cand["shape"], "why": cand["why"], "decision": d.get("decision"),
               "gate_why": d.get("why", []), "evidence": d.get("evidence"),
               "rendered": d.get("rendered") or (IR.render(IR.normalize(cand["op"]["contract"]), dom)
                                                 if cand["op"].get("contract") else None)}
        if cand["op"]["op"] == "revise":
            row["regression"] = regression(schema_only(base), IR.normalize(cand["op"]["contract"]), ev,
                                           m2["assumptions"], IR.active(m2, "persists"), dom, label_fn)
        rows.append(row)
        if d.get("decision") == "accepted" and cand["shape"] != "retire" and (
                cand["op"]["op"] != "revise" or (row.get("regression") or {}).get("clean")):
            viable.append((j, row, m2))
    # SELECTION POLICY, stated rather than left to enumeration order: among the repairs the gate
    # accepts with a clean regression, take the one that KEEPS THE MOST EVIDENCE (most supporting
    # cases), then the simplest (fewest conjuncts), then a stable name. Taking the first enumerated
    # would pick an arbitrary one - on the fixture it picks a repair with half the support.
    accepted = None
    if viable:
        viable.sort(key=lambda t: (-((t[1].get("evidence") or {}).get("n_support") or 0),
                                   len((t[1].get("rendered") or "")),
                                   t[1].get("rendered") or ""))
        accepted, _row, out_model = viable[0]
    if accepted is None:
        ret = [j for j, r in enumerate(rows) if r["shape"] == "retire" and r["decision"] == "accepted"]
        if ret:
            accepted = ret[0]
            out_model, _ = LN.apply_proposal(
                model, {"contract_ops": [{"op": "retire", "target": cid,
                                          "reason": "no repair passed the gate with a clean regression"}]},
                ev, batch, source, dom, label_fn)
    return {"contract": cid, "original": base, "candidates": rows, "accepted": accepted,
            "selection_policy": ("among repairs the gate accepts with a clean regression: most "
                                 "supporting cases, then simplest, then a stable name"),
            "model": out_model}
