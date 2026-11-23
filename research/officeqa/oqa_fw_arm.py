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

WHO PROPOSES. From 2026-11-23 the campaign learner is DEEPSEEK, through `oqa_fw_teacher`: it reads the
records and the code-computed atom values and proposes operations, and code validates, scores, gates and
repairs them. This module therefore DOES make paid provider calls on the `teacher` path, under the live
spend ledger, and records every attempt. The finite enumeration stays available as `--proposer search`
and runs free and unapplied as a diagnostic beside the teacher; it is never substituted for it.
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
import oqa_fw_teacher as TEACH  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402

SCHEMA = "lso.officeqa_framework_arm.v1"
SUBJECT, CONSEQUENCE = "column", "col_label_agrees_flat"
SEED_ASSUMPTIONS = ("index_alignment", "table_scale_applies_to_rows")
SEED_WHY = ("seed: binding a figure to a column and a unit is not possible without reading a split "
            "header's own sub-label row, nor without carrying a table's stated scale to its rows")
TEACHER_ROUNDS = 3              # paid attempts per learner pass, including the feedback rounds
TEACHER_CAP = 8000              # completion tokens per call


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


# ── the two proposers ─────────────────────────────────────────────────────────
def _teacher_pass(model: dict, recs: list, dom, ev, epoch: int, *, rounds: int, cap: int,
                  calls: list) -> tuple:
    """Up to `rounds` paid attempts: propose, gate, feed the gate's refusals back, gate again.

    Every attempt is recorded in `calls` whether it parsed or not, and a round that produces nothing the
    gate accepts is an event in its own right rather than a silent absence. A transport failure or an
    unparseable reply is recorded as `problem` and does NOT count as a learning step - the caller can see
    that the learner was never heard from, which is the difference between "proposed nothing" and
    "never ran" that the instruction asks us not to blur.
    """
    events: list = []
    msgs = TEACH.build_messages(model, recs, ev, dom, batch=epoch, n_batches=rounds,
                                earlier=[r["episode_id"] for r in recs][:0])
    for rnd in range(1, max(1, rounds) + 1):
        rec = TEACH.call(msgs, stage=f"fw_learn_e{epoch}_r{rnd}", cap=cap)
        calls.append({k: v for k, v in rec.items() if k != "reply"} | {"reply_head": rec["reply"][:2000]})
        if rec["problem"] or not rec["parsed"]:
            events.append({"phase": "teacher", "round": rnd, "called": True, "parsed": False,
                           "problem": rec["problem"] or "no JSON object in the reply",
                           "usage": rec.get("usage"), "decisions": []})
            break
        comp = TEACH.compile_ops(rec["parsed"], dom)
        model, dec = LN.apply_proposal(model, {"contract_ops": comp["contract_ops"],
                                               "assumption_ops": comp["assumption_ops"]},
                                       ev, epoch, "teacher", dom)
        events.append({"phase": "teacher", "round": rnd, "called": True, "parsed": True,
                       "n_contract_ops": len(comp["contract_ops"]),
                       "n_assumption_ops": len(comp["assumption_ops"]),
                       "n_refused_before_the_gate": len(comp["refused"]),
                       "refused_before_the_gate": comp["refused"][:6],
                       "uncovered": comp["uncovered"][:6], "notes": str(comp["notes"])[:400],
                       "usage": rec.get("usage"), "decisions": dec,
                       "consequence_categories": _categories(comp["contract_ops"], dom)})
        dom, ev = RUN.evidence_of(recs, model=model)
        bad = [d for d in dec if d.get("decision") in ("rejected", "refuted")] + [
            {"type": "contract", "op": r.get("op"), "decision": "rejected", "why": r.get("why")}
            for r in comp["refused"]]
        if not bad or rnd >= rounds:
            break
        msgs = TEACH.feedback_messages(msgs, rec["reply"], bad)
    return model, dom, ev, events


def _categories(ops: list, dom) -> dict:
    """The vocabulary CATEGORY of each proposed consequence, so a rule that merely restates a supplied
    computation is visible in the artifact instead of being discovered later."""
    out = {}
    for op in ops:
        c = op.get("contract") or {}
        atom = (c.get("consequence") or {}).get("atom")
        if atom in dom.atoms:
            out[atom] = dom.atoms[atom][1]
    return out


def _search_pass(model: dict, recs: list, dom, ev, epoch: int) -> tuple:
    sp = PROP.SearchProposer(dom, SUBJECT, CONSEQUENCE)
    induced = sp.propose(model, ev)
    events = []
    if induced["contract_ops"]:
        model, dec = LN.apply_proposal(model, induced, ev, epoch, "search", dom)
        events.append({"phase": "induce", "proposer": "search", "n_candidates": induced["n_candidates"],
                       "n_acceptable": induced["n_acceptable"],
                       "n_proposed": len(induced["contract_ops"]), "decisions": dec})
        dom, ev = RUN.evidence_of(recs, model=model)
    else:
        events.append({"phase": "induce", "proposer": "search", "n_candidates": induced["n_candidates"],
                       "n_acceptable": 0, "n_proposed": 0, "decisions": [],
                       "note": "the search offered nothing the gate would accept on this evidence"})
    return model, dom, ev, events


def _search_diagnostic(model: dict, dom, ev) -> dict:
    """The enumeration run and NOT applied: how big the space was, how much of it the gate would take."""
    sp = PROP.SearchProposer(dom, SUBJECT, CONSEQUENCE)
    induced = sp.propose(model, ev, want=3)
    return {"phase": "search_diagnostic", "applied": False,
            "what_it_is": "the finite enumeration over the ONE researcher-chosen consequence "
                          f"{CONSEQUENCE!r}, scored but NOT applied; it prices the hypothesis space and "
                          "is not the campaign learner",
            "n_candidates": induced["n_candidates"], "n_acceptable": induced["n_acceptable"],
            "top": [{"rendered": r["rendered"], "n_support": r["n_support"],
                     "n_counter": r["n_counter"], "undetermined": r["undetermined"],
                     "gate": r["gate"]} for r in induced["trail"][:5]]}


# ── one learner pass ──────────────────────────────────────────────────────────
def learn_over(model: dict, recs: list, epoch: int, *, seed_assumptions: bool,
               proposer: str = "teacher", rounds: int = TEACHER_ROUNDS, cap: int = TEACHER_CAP,
               calls: list | None = None, diagnostic_search: bool = True) -> tuple:
    """Supplied assumptions (first time only), PROPOSE, EXPLICIT recheck, then repair.

    `proposer` picks who proposes, and the artifact records which one did:

      "teacher"  the DeepSeek learner (`oqa_fw_teacher`). THIS IS THE CAMPAIGN LEARNER. It reads the
                 records and the code-computed atom values, proposes contract and assumption operations
                 over any atom it is allowed to conclude, and is told the gate's reason for every
                 refusal in a feedback round. Each round is one paid attempt under the live ledger.
      "search"   the finite enumeration (`oqa_fw_learn.SearchProposer`). A DIAGNOSTIC, kept because it
                 prices the hypothesis space, and never substituted for the campaign learner: it can
                 only ever propose rules concluding the ONE atom a researcher gave it.

    With `diagnostic_search` the enumeration also runs under the teacher, free and NOT APPLIED, so the
    artifact can say how large the space was and how much of it the gate would have accepted - which is
    the only way to tell a learner that found nothing from a space that contains nothing.
    """
    dom, ev = RUN.evidence_of(recs, model=model)
    events: list = []
    if calls is None:
        calls = []

    if seed_assumptions:
        prop = {"assumption_ops": [{"op": "adopt", "name": a, "reason": SEED_WHY}
                                   for a in SEED_ASSUMPTIONS], "contract_ops": []}
        model, dec = LN.apply_proposal(model, prop, ev, epoch, "seed", dom)
        events.append({"phase": "seed_assumptions", "decisions": dec,
                       "assumptions_after": list(model["assumptions"])})
        dom, ev = RUN.evidence_of(recs, model=model)

    if proposer == "teacher":
        model, dom, ev, ev_events = _teacher_pass(model, recs, dom, ev, epoch, rounds=rounds, cap=cap,
                                                  calls=calls)
        events += ev_events
        if diagnostic_search:
            events.append(_search_diagnostic(model, dom, ev))
    elif proposer == "search":
        model, dom, ev, ev_events = _search_pass(model, recs, dom, ev, epoch)
        events += ev_events
    else:
        raise ValueError(f"unknown proposer {proposer!r}")

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
                    help="evolve: an earlier epoch's predictions:order[:tag] triple, repeatable, so "
                         "the gate sees ALL evidence so far rather than only this epoch's")
    ap.add_argument("--tag", help="episode-id prefix for --pred, default e<epoch>/. Set it to the "
                                 "epoch the TRACES come from, which is not always the epoch whose "
                                 "model is being frozen: the model consumed by epoch N is learned "
                                 "from epoch N-1's traces, and the artifact should say so")
    ap.add_argument("--proposer", choices=("teacher", "search"), default="teacher",
                    help="teacher = the DeepSeek campaign learner (paid); search = the finite "
                         "enumeration, a free diagnostic that is NOT the campaign learner")
    ap.add_argument("--rounds", type=int, default=TEACHER_ROUNDS)
    ap.add_argument("--cap", type=int, default=TEACHER_CAP)
    ap.add_argument("--no-diagnostic-search", action="store_true")
    a = ap.parse_args(argv)

    recs = load_attempts(a.pred, read_order(a.order), tag=(a.tag or f"e{a.epoch}/"))
    for spec in a.carry:
        bits = spec.split(":")
        if len(bits) == 3:
            pred, order, tag = bits
        elif len(bits) == 2:
            pred, order = bits
            tag = (os.path.basename(os.path.dirname(os.path.abspath(pred))) or "prev") + "/"
        else:
            ap.error(f"--carry wants predictions:order[:tag], got {spec!r}")
        recs = load_attempts(pred, read_order(order), tag=tag) + recs
    seen = [r["episode_id"] for r in recs]
    if len(set(seen)) != len(seen):
        ap.error("two records share an episode id; give each --carry its own :tag, or the gate would "
                 "score one record twice and count the same column as two cases")

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
    calls: list = []
    model, events, dom, ev = learn_over(model, recs, a.epoch,
                                        seed_assumptions=(a.mode == "seed"),
                                        proposer=a.proposer, rounds=a.rounds, cap=a.cap,
                                        calls=calls,
                                        diagnostic_search=not a.no_diagnostic_search)
    path = os.path.join(a.out, f"FW_MODEL_e{a.epoch}.json")
    fr = freeze_to(model, path, parent_sha256=parent, epoch=a.epoch)

    doc = {"schema": SCHEMA, "rule": RUN.RULE, "mode": a.mode, "arm": a.arm, "epoch": a.epoch,
           "evidence": {"n_attempts": len(recs), "tag": (a.tag or f"e{a.epoch}/"),
                        "carry": list(a.carry),
                        "attempts": [{"episode_id": r["episode_id"], "item": r["item"],
                                      "trace_sha256": r["trace_sha256"][:16]} for r in recs]},
           "model_before": before, "events": events, "frozen": fr,
           "proposer": a.proposer,
           "prompt_digest": (TEACH.prompt_digest(dom) if a.proposer == "teacher" else None),
           "learner_calls": {"n": len(calls), "n_parsed": sum(1 for c in calls if c.get("parsed")),
                             "usage": [c.get("usage") for c in calls],
                             "prompt_sha256": [c.get("prompt_sha256") for c in calls]},
           "snapshot": snapshot(model, dom), "retained": retained(model, ev, dom),
           "in_sample_warning": ("epoch 1's reports describe the very rollout M0 was learned on, so "
                                 "they are IN-SAMPLE" if a.mode == "seed" else None)}
    RUN.seal(doc)
    RUN.jdump(doc, os.path.join(a.out, f"FW_ARM_{a.arm}_e{a.epoch}.json"))
    if calls:
        with open(os.path.join(a.out, f"FW_LEARNER_CALLS_{a.arm}_e{a.epoch}.jsonl"), "w",
                  encoding="utf-8") as f:
            for c in calls:
                f.write(json.dumps(c, sort_keys=True) + "\n")
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
