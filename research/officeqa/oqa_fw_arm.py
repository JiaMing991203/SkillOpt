#!/usr/bin/env python3
"""Per-epoch evolution of the ported framework's model across a campaign. Rule OQA-FW-1.

WHY THIS FILE EXISTS. `oqa_fw_run.py` learns once over a frozen set, which is what the bounded test
needed. A campaign is different: the bank starts EMPTY and the model has to evolve from each epoch's
own traces, with every report resting on a model frozen BEFORE the traces that report describes. That
is what `oqa_lc_arm.py` does for the LC generation, and this is the same shape for the framework.

  seed()    runs the learner once over the shared epoch-1 rollout and freezes M0.
  evolve()  at the end of epoch N, takes the model frozen at the end of epoch N-1 and that arm's own
            epoch-N traces, runs one learner pass over ALL evidence seen so far, and freezes M_N.

So a report in epoch N rests on a model frozen before epoch N's traces existed. The one exception,
recorded rather than hidden: M0 is learned on the shared epoch-1 rollout, which is also the rollout
epoch 1's reports describe, so **epoch 1's reports are IN-SAMPLE** and say so.

THE RECHECK IS EXPLICIT, AND THAT IS A LESSON INHERITED FROM `oqa_lc_arm`, NOT A FLOURISH.
`fw_learn._finish` is what re-scores every surviving contract and refutes whatever the evidence now
contradicts - but it only runs when `apply_proposal` is called. In the bounded test the proposer
offered something every time, so the refutation happened as a side effect. Across epochs it will not:
if the search proposes nothing new at epoch 2, `apply_proposal` is never called, and a counterexample
the new traces have just produced would leave the contract active and unrepaired. An EMPTY proposal is
therefore applied explicitly whenever any active contract is contradicted, which runs the recheck,
marks it refuted, and lets the repair target it.

No network call is made from this module.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
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
import oqa_fw_run as RUN  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402

SCHEMA = "lso.officeqa_framework_arm.v1"
SUBJECT, CONSEQUENCE = "column", "col_label_agrees_flat"
SEED_ASSUMPTIONS = ("index_alignment", "table_scale_applies_to_rows")
SEED_WHY = ("seed: binding a figure to a column and a unit is not possible without reading a split "
            "header's own sub-label row, nor without carrying a table's stated scale to its rows")


# ── records ───────────────────────────────────────────────────────────────────
def read_order(path: str) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_attempts(pred_dir: str, order: list, tag: str = "") -> list:
    """The permitted records of one epoch's rollout, in the order the rollout produced them.

    Each record carries its own content digest, so "learned from" can be checked against the ATTEMPT
    rather than against the question id, which recurs in every epoch.
    """
    recs = []
    for item in order:
        tid = str(item.get("id"))
        if not os.path.isdir(os.path.join(pred_dir, tid)):
            continue
        a = EV.read_attempt(pred_dir, tid)
        steps = [{"i": i, "kind": k, "cmd": c, "obs": o, "text": t}
                 for (i, k, c, o, t) in EV._flat(a["items"])]
        ents = E.document_entities(steps)
        ents = E.operand_entities(steps, ents)
        recs.append({"episode_id": f"{tag}{tid}" if tag else tid, "item": tid,
                     "pass": 0, "role": "campaign",
                     "task": EV.question_of(a["user_prompt"]), "task_type": None,
                     "steps": steps, "entities": ents,
                     "asked_scale": RUN.asked_scale(a["user_prompt"]),
                     "trace_sha256": a["trace_sha256"]})
    return recs


# ── one learner pass ──────────────────────────────────────────────────────────
def learn_over(model: dict, recs: list, epoch: int, *, seed_assumptions: bool) -> tuple:
    """Assumptions (first time only), search, EXPLICIT recheck, then repair. One order, every epoch."""
    dom, ev = RUN.evidence_of(recs, model=model)
    events: list = []

    if seed_assumptions:
        prop = {"assumption_ops": [{"op": "adopt", "name": a, "reason": SEED_WHY}
                                   for a in SEED_ASSUMPTIONS], "contract_ops": []}
        model, dec = LN.apply_proposal(model, prop, ev, epoch, "seed", dom)
        events.append({"phase": "seed_assumptions", "decisions": dec,
                       "assumptions_after": list(model["assumptions"])})
        dom, ev = RUN.evidence_of(recs, model=model)

    sp = PROP.SearchProposer(dom, SUBJECT, CONSEQUENCE)
    induced = sp.propose(model, ev)
    if induced["contract_ops"]:
        model, dec = LN.apply_proposal(model, induced, ev, epoch, "search", dom)
        events.append({"phase": "induce", "n_candidates": induced["n_candidates"],
                       "n_acceptable": induced["n_acceptable"],
                       "n_proposed": len(induced["contract_ops"]), "decisions": dec})
        dom, ev = RUN.evidence_of(recs, model=model)
    else:
        events.append({"phase": "induce", "n_candidates": induced["n_candidates"],
                       "n_acceptable": 0, "n_proposed": 0, "decisions": [],
                       "note": "the search offered nothing the gate would accept on this evidence"})

    # THE EXPLICIT RECHECK. See the module docstring: without this an active contract that the new
    # epoch's traces contradict stays active, because nothing called apply_proposal.
    contradicted = {}
    for c in IR.active(model):
        sc = K.score(c, ev, model["assumptions"], IR.active(model, "persists"), dom)
        if sc["counter"]:
            contradicted[c["id"]] = sc["counter"][:6]
    if contradicted:
        model, dec = LN.apply_proposal(model, {"assumption_ops": [], "contract_ops": []}, ev, epoch,
                                       "recheck", dom)
        events.append({"phase": "recheck", "contradicted": contradicted, "decisions": dec})
        dom, ev = RUN.evidence_of(recs, model=model)

    # repair every contract the recheck (or an earlier epoch) left refuted
    refuted = [c["id"] for c in model["contracts"] if c.get("status") == "refuted"]
    for cid in refuted:
        att = RP.attempt(model, cid, ev, dom, epoch, "repair")
        events.append({"phase": "repair", "contract": cid,
                       "accepted_shape": (att["candidates"][att["accepted"]]["shape"]
                                          if att["accepted"] is not None else None),
                       "selection_policy": att.get("selection_policy"),
                       "candidates": [{"shape": r["shape"], "decision": r["decision"],
                                       "gate_why": r["gate_why"],
                                       "regression": r.get("regression")}
                                      for r in att["candidates"]]})
        model = att["model"]
        dom, ev = RUN.evidence_of(recs, model=model)
    return model, events, dom, ev


def snapshot(model: dict, dom) -> dict:
    return {"version": model.get("version"), "sha256": model.get("sha256"),
            "assumptions": list(model.get("assumptions") or []),
            "active": [f"{c['id']}@v{c['version']}" for c in IR.active(model)],
            "refuted": [f"{c['id']}@v{c['version']}" for c in (model.get("contracts") or [])
                        if c.get("status") == "refuted"],
            "retired": [f"{c['id']}@v{c['version']}" for c in (model.get("contracts") or [])
                        if c.get("status") == "retired"],
            "n_rejected": len(model.get("rejected") or []),
            "rendered_active": [IR.render(c, dom) for c in IR.active(model)]}


def retained(model: dict, ev, dom) -> list:
    """For every surviving contract: the evidence that justifies keeping it, as it now stands."""
    out = []
    for c in IR.active(model):
        sc = K.score(c, ev, model["assumptions"], IR.active(model, "persists"), dom)
        n_sup = len(sc["support"])
        if sc["counter"]:
            why = (f"RETAINED IN ERROR: {n_sup} supporting case(s) but {len(sc['counter'])} "
                   f"counterexample(s) ({', '.join(sc['counter'][:4])}) the recheck did not act on")
        else:
            why = (f"{n_sup} supporting case(s) and no counterexample in the evidence as it now "
                   f"stands; {sc['undetermined']} undetermined")
        out.append({"id": f"{c['id']}@v{c['version']}", "rendered": IR.render(c, dom), "why": why,
                    "evidence": K.summarize(sc)})
    return out


def freeze_to(model: dict, path: str, *, parent_sha256=None, epoch=None) -> dict:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    m = copy.deepcopy(model)
    m["frozen_at_epoch"] = epoch
    if parent_sha256:
        m["parent_sha256"] = parent_sha256
    try:
        digest = IR.freeze(m, path)
        problem = None
    except RuntimeError as exc:
        digest = IR.model_sha(m)
        problem = str(exc)
    model["sha256"] = digest
    return {"path": path, "sha256": digest, "freeze_problem": problem}


# ── the two commands ──────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("seed", "evolve"))
    ap.add_argument("--pred", required=True, help="this epoch's predictions/ directory")
    ap.add_argument("--order", required=True, help="this epoch's results.jsonl (rollout order)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev", help="evolve: the model frozen at the end of the previous epoch")
    ap.add_argument("--epoch", type=int, default=1)
    ap.add_argument("--arm", default="FW")
    ap.add_argument("--carry", action="append", default=[],
                    help="evolve: an earlier epoch's predictions dir:order pair, repeatable, so the "
                         "gate sees ALL evidence so far rather than only this epoch's")
    a = ap.parse_args(argv)

    recs = load_attempts(a.pred, read_order(a.order), tag=f"e{a.epoch}/")
    for spec in a.carry:
        pred, order = spec.split(":", 1)
        ep = os.path.basename(os.path.dirname(os.path.abspath(pred))) or "prev"
        recs = load_attempts(pred, read_order(order), tag=f"{ep}/") + recs

    if a.mode == "seed":
        dom0 = DOM.OfficeQADomain()
        model = IR.empty_model(dom0)
        parent = None
    else:
        if not a.prev:
            ap.error("evolve needs --prev")
        model = IR.load_frozen(a.prev)
        parent = model.get("sha256")

    before = {"version": model["version"], "assumptions": list(model["assumptions"]),
              "n_active": len(IR.active(model))}
    model, events, dom, ev = learn_over(model, recs, a.epoch,
                                        seed_assumptions=(a.mode == "seed"))
    path = os.path.join(a.out, f"FW_MODEL_e{a.epoch}.json")
    fr = freeze_to(model, path, parent_sha256=parent, epoch=a.epoch)

    doc = {"schema": SCHEMA, "rule": RUN.RULE, "mode": a.mode, "arm": a.arm, "epoch": a.epoch,
           "evidence": {"n_attempts": len(recs),
                        "attempts": [{"episode_id": r["episode_id"], "item": r["item"],
                                      "trace_sha256": r["trace_sha256"][:16]} for r in recs]},
           "model_before": before, "events": events, "frozen": fr,
           "snapshot": snapshot(model, dom), "retained": retained(model, ev, dom),
           "in_sample_warning": ("epoch 1's reports describe the very rollout M0 was learned on, so "
                                 "they are IN-SAMPLE" if a.mode == "seed" else None)}
    RUN.seal(doc)
    RUN.jdump(doc, os.path.join(a.out, f"FW_ARM_{a.arm}_e{a.epoch}.json"))
    print(f"[{a.mode} epoch {a.epoch} arm {a.arm}] model v{model['version']} "
          f"{fr['sha256'][:16]} -> {path}")
    print(f"  active: {doc['snapshot']['active']}  refuted: {doc['snapshot']['refuted']}  "
          f"retired: {doc['snapshot']['retired']}")
    for e in events:
        print(f"  {e['phase']}: " + json.dumps({k: v for k, v in e.items()
                                                if k not in ("decisions", "candidates")})[:150])
    if fr["freeze_problem"]:
        print(f"  FREEZE PROBLEM: {fr['freeze_problem']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
