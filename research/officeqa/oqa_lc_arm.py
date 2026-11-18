"""Per-arm evolution of the learned contract model across the epochs of the three-arm comparison.

Offline. Reads recorded prediction directories and a frozen model; writes a new frozen model and one record of
what changed and why. No network, no GPU, no paid call.

WHAT THIS IMPLEMENTS, in the user's words: "The two contract arms should start from the same contract model
learned on the shared rollout. After that, each arm evolves its model from its own training traces using the same
learner, evidence rules, and repair schedule."

  seed()    runs the learner once over the SHARED epoch-1 rollout and freezes M0. Arm B and arm C both start from
            this one file, so the two contract arms begin the comparison with a byte-identical model.
  evolve()  at the end of epoch N, takes the model frozen at the end of epoch N-1 and that ARM'S OWN epoch-N
            training traces, and produces M_N. The two arms run the same code with the same settings; what
            differs is only the traces, because the arms' actors saw different skills.

THE ORDER WITHIN AN EPOCH, which is what keeps a prediction a prediction:

    rollout -> reports built under M_{N-1} -> analyst -> gate -> END OF EPOCH -> evolve to M_N

  A report in epoch N therefore rests on a model frozen before epoch N's traces existed. `evolve` replays the new
  traces against M_{N-1} FIRST, committing each prediction's digest to a hash chain before the observation is
  released, and only then admits them as evidence.

THE ONE EXCEPTION, recorded rather than hidden. M0 is learned on the shared epoch-1 rollout, which is also the
rollout that epoch 1's reports describe. Epoch 1's reports are therefore IN-SAMPLE: their forecasts were induced
to hold on those very observations and are not predictions. `seed` writes the evidence item list into the frozen
model so that a report on one of those items says so in its own text. From epoch 2 onward every report is
out-of-sample with respect to the model in force.

usage:
  python3 -I research/officeqa/oqa_lc_arm.py seed   --pred <shared epoch1 predictions> --order <results.jsonl> \
      --out <dir>
  python3 -I research/officeqa/oqa_lc_arm.py evolve --prev <model json> --pred <epoch N predictions> \
      --order <results.jsonl> --epoch N --arm B --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oqa_lc_ir as IR  # noqa: E402
import oqa_lc_learn as LEARN  # noqa: E402
import oqa_lc_replay as REPLAY  # noqa: E402
import oqa_lc_vocab as VOCAB  # noqa: E402
import oqa_obligations as OB  # noqa: E402
import oqa_trace as TR  # noqa: E402

SEED_ASSUMPTIONS = ["flat_header_authoritative", "index_alignment", "year_inheritance",
                    "table_scale_applies_to_rows", "grep_cap_not_reached"]


def read_order(path: str) -> list:
    ids: list = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("id") and str(row["id"]) not in ids:
                ids.append(str(row["id"]))
    return ids


def load_attempts(pred_dir: str, order: list, tag: str = "") -> list:
    """The recorded attempts of one collection, each tagged so it is distinguishable from the same question's
    attempt in another epoch (see `oqa_lc_replay.trace_key`)."""
    out = []
    for uid in order:
        if not os.path.isdir(os.path.join(pred_dir, uid)):
            continue
        pk = TR.build_packet(pred_dir, uid)
        pk["answer_kind"] = (OB.derive(pk["question"])["answer_kind"]["kind"]) if pk.get("question") else None
        pk["trace_key"] = f"{uid}/{tag}" if tag else uid
        pk["collection"] = os.path.abspath(pred_dir)
        # AMENDMENT A-1: the content digest of the attempt, so "learned from" can be checked against the
        # attempt rather than against the question id, which recurs in every epoch.
        pk["trace_sha256"] = TR.trace_digest(pred_dir, uid)
        out.append(pk)
    return out


def _learn_over(model: dict, ev: REPLAY.Evidence, epoch: int, seed_assumptions: bool) -> tuple:
    """One pass of the learner: seed (first time only), induce, RECHECK, re-test assumptions, repair.

    Same order and the same settings for the seed and for both arms, so the arms differ by their traces only.

    The RECHECK step is not in the port's demonstrated loop and was added here after it produced a wrong result.
    `apply_proposal` is what re-scores every surviving contract and refutes whatever the evidence now contradicts,
    and `mech_repair` only ever targets a contract already marked refuted. In the port's loop `mech_induce`
    proposed something on every round, so `apply_proposal` always ran and the refutation happened as a side
    effect. Across epochs it does not: at epoch 2 the learner proposed nothing new, `apply_proposal` was never
    called, and a counterexample the replay had just found (read.effect.1 = width_uniform at UID0028@42) left the
    contract active and unrepaired. An empty proposal is applied explicitly whenever the evidence contradicts an
    active contract, which runs the recheck and marks it refuted so the repair can target it.
    """
    events: list = []
    if seed_assumptions:
        prop = {"assumption_ops": [{"op": "adopt", "name": a,
                                    "reason": "seed: binding a figure to a column, a period and a unit is not "
                                              "possible without it"} for a in SEED_ASSUMPTIONS],
                "contract_ops": [], "source": "seed"}
        model, dec = LEARN.apply_proposal(model, prop, ev, epoch=epoch, source="seed")
        events.append({"phase": "seed_assumptions", "decisions": dec})

    induced = LEARN.mech_induce(ev, model["assumptions"], model=model)
    if induced["contract_ops"]:
        model, dec = LEARN.apply_proposal(model, induced, ev, epoch=epoch, source="mech")
        events.append({"phase": "induce", "n_proposed": len(induced["contract_ops"]), "decisions": dec})

    contradicted = {cid: r for cid, r in LEARN.recheck(model, ev).items()
                    if r.get("status") == "active" and r.get("n_counter")}
    if contradicted:
        model, dec = LEARN.apply_proposal(model, {"assumption_ops": [], "contract_ops": []}, ev, epoch=epoch,
                                          source="mech-recheck")
        events.append({"phase": "recheck", "contradicted": {cid: r["counter"][:6] for cid, r in
                                                            contradicted.items()}, "decisions": dec})

    acheck = LEARN.assumption_recheck(model, ev)
    if acheck["assumption_ops"]:
        before = LEARN.recheck(model, ev)
        model, dec = LEARN.apply_proposal(model, acheck, ev, epoch=epoch, source="mech-assumption-recheck")
        events.append({"phase": "assumption_recheck", "decisions": dec,
                       "recheck_before": before, "recheck_after": LEARN.recheck(model, ev)})

    repair = LEARN.mech_repair(model, ev)
    if repair["assumption_ops"] or repair["contract_ops"]:
        before = LEARN.recheck(model, ev)
        model, dec = LEARN.apply_proposal(model, repair, ev, epoch=epoch, source="mech-repair")
        events.append({"phase": "repair", "decisions": dec,
                       "recheck_before": before, "recheck_after": LEARN.recheck(model, ev)})
    return model, events


def _snapshot(model: dict) -> dict:
    return {"version": model.get("version"), "sha256": model.get("sha256"),
            "assumptions": list(model.get("assumptions") or []),
            "active": [f"{c['id']}@v{c['version']}" for c in IR.active(model)],
            "refuted": [f"{c['id']}@v{c['version']}" for c in model.get("contracts") or []
                        if c.get("status") == "refuted"],
            "n_rejected": len(model.get("rejected") or [])}


def _freeze(model: dict, path: str, *, parent_sha256: str | None = None,
            frozen_at_epoch: int | None = None) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        model["sha256"] = IR.freeze(model, path, parent_sha256=parent_sha256,
                                    frozen_at_epoch=frozen_at_epoch)
        problem = None
    except RuntimeError as exc:
        model["sha256"] = IR.model_sha(model)
        problem = str(exc)
    return {"path": path, "sha256": model["sha256"], "freeze_problem": problem}


def _retained(model: dict, ev: REPLAY.Evidence) -> list:
    """For every contract that survived: the evidence that justifies keeping it, as it now stands."""
    out = []
    for c in IR.active(model):
        sc = REPLAY.score_contract(c, ev, model["assumptions"])
        n_sup = len(sc["support_ok"]) + len(sc["support_fail"])
        why = (f"{n_sup} supporting step(s) over {len(sc['items_supporting'])} item(s) and no counterexample in "
               f"the evidence as it now stands")
        if sc["counter"]:
            # An active contract with a counterexample is a defect, not a retention: say so instead of printing
            # "no counterexample" beside a non-zero count, which is what the first version of this did.
            why = (f"RETAINED IN ERROR: {n_sup} supporting step(s) over {len(sc['items_supporting'])} item(s) but "
                   f"{len(sc['counter'])} counterexample(s) ({', '.join(sc['counter'][:4])}) that the recheck did "
                   f"not act on")
        out.append({"id": f"{c['id']}@v{c['version']}", "rendered": IR.render(c),
                    "decision": "retain" if not sc["counter"] else "retained_in_error", "why": why,
                    "n_support": len(sc["support_ok"]) + len(sc["support_fail"]),
                    "items_supporting": sc["items_supporting"], "n_counter": len(sc["counter"])})
    return out


def seed(pred_dir: str, order_path: str, out_dir: str) -> dict:
    """M0: the one model both contract arms start from, learned on the SHARED epoch-1 rollout."""
    order = read_order(order_path)
    attempts = load_attempts(pred_dir, order, tag="e1")
    ev = REPLAY.Evidence(attempts)
    model = IR.empty_model()
    model, events = _learn_over(model, ev, epoch=1, seed_assumptions=True)
    # Recorded so a report on one of these items can say in its own text that its forecasts are in-sample.
    model["evidence_items"] = [a["id"] for a in attempts]
    model["evidence_attempts"] = sorted(
        ({"trace_key": a.get("trace_key"), "trace_sha256": a.get("trace_sha256"),
          "collection": a.get("collection")} for a in attempts),
        key=lambda d: str(d["trace_key"]))
    model["evidence_scope"] = "shared epoch-1 rollout"
    frozen = _freeze(model, os.path.join(out_dir, "model_M0.json"), frozen_at_epoch=1)
    rec = {"kind": "seed", "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "vocab_digest": VOCAB.vocab_digest(),
           "collection": os.path.abspath(pred_dir), "order": order,
           "items_admitted": [a["id"] for a in attempts],
           "tool_steps_admitted": sum(1 for a in attempts for s in a["steps"] if s.get("kind") == "tool"),
           "prior_model": None, "events": events, "model": _snapshot(model), "frozen": frozen,
           "retained": _retained(model, ev),
           "in_sample_warning": "M0 is learned on the rollout that epoch 1's reports describe, so epoch 1's "
                                "forecasts are in-sample and are not predictions."}
    with open(os.path.join(out_dir, "SEED.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, default=str)
    return rec


def read_history(path: str) -> list:
    """The collections this arm has already admitted, one JSON object per line: {"pred", "order", "epoch"}."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def evolve(prev_path: str, pred_dir: str, order_path: str, epoch: int, arm: str, out_dir: str,
           history_path: str = "") -> dict:
    """M_N from M_{N-1} and one arm's own epoch-N traces. Replay first, then admit, then learn.

    EVIDENCE ACCUMULATES. `history_path` lists the collections this arm has already admitted, and they are all
    loaded back into the evidence before the learner runs, so "recheck the earlier cases" means what it says. The
    first version of this function built the evidence from the new epoch alone, which silently discarded every
    earlier counterexample and would have let a contract be re-induced in epoch 3 that epoch 2 had refuted.
    """
    prev = IR.load_frozen(prev_path)
    prior = _snapshot(prev)
    order = read_order(order_path)
    attempts = load_attempts(pred_dir, order, tag=f"e{epoch}")
    os.makedirs(out_dir, exist_ok=True)

    history = read_history(history_path)
    prior_attempts: list = []
    for h in history:
        prior_attempts += load_attempts(h["pred"], read_order(h["order"]),
                                        tag=f"e{h.get('epoch')}" if h.get("epoch") else "")

    # 1. Out-of-sample replay against the model that was in force during this epoch. Each prediction's digest is
    #    committed to a hash chain before its observation is released.
    sink_path = os.path.join(out_dir, f"predictions_epoch{epoch:02d}.jsonl")
    sink = REPLAY.jsonl_sink(sink_path)
    records, chains = [], []
    for a in attempts:
        res = REPLAY.replay_attempt(a, prev, sink)
        records += res["attempts"]
        chains.append({"item": a["id"], "reveal_log_sha256": VOCAB.sha(VOCAB.canon(res["reveal_log"]))})
    pre_score = REPLAY.score(records)
    violations = [{"aid": r["aid"], "step": r["step"], "tool": r["tool"],
                   "violated": [{"contract": f["contract"], "version": f.get("version"), "atom": f["atom"],
                                 "predicted": f["predicted"], "observed": f["observed"]}
                                for f in r["forecast_checks"] if f["class"] == REPLAY.FORECAST_VIOLATED],
                   "witnesses": (r.get("witnesses") or [])[:6]}
                  for r in records
                  if any(f["class"] == REPLAY.FORECAST_VIOLATED for f in r.get("forecast_checks") or [])]

    # 2. Admit the new traces ON TOP of everything this arm has already seen, then run the learner.
    ev = REPLAY.Evidence(prior_attempts + attempts)
    model = dict(prev)
    model, events = _learn_over(model, ev, epoch=epoch, seed_assumptions=False)
    # The new model has seen every trace in `ev`, so a report built under it on any of those items is in-sample.
    # A report in epoch N+1 is about a trace that does not exist yet, which is the point of freezing here.
    model["evidence_items"] = sorted({a["id"] for a in prior_attempts + attempts})
    model["evidence_attempts"] = sorted(
        ({"trace_key": a.get("trace_key"), "trace_sha256": a.get("trace_sha256"),
          "collection": a.get("collection")} for a in prior_attempts + attempts),
        key=lambda d: str(d["trace_key"]))
    model["evidence_scope"] = (f"arm {arm}: the shared epoch-1 rollout and this arm's own epochs "
                               f"2..{epoch} training traces")
    after = _snapshot(model)
    changed = (after["active"] != prior["active"] or after["assumptions"] != prior["assumptions"]
               or after["refuted"] != prior["refuted"])
    # A new digest with no contract change is normal and must not look like a contradiction: the frozen file also
    # carries the version, the epoch, the parent digest and the evidence scope, and all of those move.
    digest_moved_by = sorted(k for k in set(prev) | set(model)
                             if k not in ("sha256", "frozen_utc") and prev.get(k) != model.get(k))
    # AMENDMENT A-1: the model this one was frozen FROM, and the epoch the freeze happened at. The
    # campaign recorded neither, and the existing `epoch` field is the last CHANGE, not the freeze.
    frozen = _freeze(model, os.path.join(out_dir, f"model_arm{arm}_epoch{epoch:02d}.json"),
                     parent_sha256=str(prev.get("sha256") or "") or None, frozen_at_epoch=epoch)
    now = _snapshot(model)   # after the freeze, so `sha256` is the digest the file carries

    rec = {"kind": "evolve", "arm": arm, "epoch": epoch,
           "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "vocab_digest": VOCAB.vocab_digest(),
           "collection": os.path.abspath(pred_dir), "order": order,
           "prior_model": prior, "prior_model_path": os.path.abspath(prev_path),
           "evidence_carried_forward": {"collections": history,
                                        "items": [a["id"] for a in prior_attempts],
                                        "tool_steps": sum(1 for a in prior_attempts for s in a["steps"]
                                                          if s.get("kind") == "tool")},
           "new_evidence": {"items": [a["id"] for a in attempts],
                            "tool_steps": sum(1 for a in attempts for s in a["steps"] if s.get("kind") == "tool"),
                            "reveal_chains": chains,
                            "predictions_file": os.path.basename(sink_path)},
           "out_of_sample_score_before_learning": pre_score,
           "counterexamples_found": violations,
           "events": events,
           "model": now, "frozen": frozen,
           "model_changed": changed,
           "digest_differs_in_fields": digest_moved_by,
           "retained": _retained(model, ev),
           "recheck_after": LEARN.recheck(model, ev)}
    with open(os.path.join(out_dir, f"EVOLVE_arm{arm}_epoch{epoch:02d}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, default=str)
    if history_path:
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pred": os.path.abspath(pred_dir), "order": os.path.abspath(order_path),
                                "epoch": epoch, "arm": arm}) + "\n")
    with open(os.path.join(os.path.dirname(out_dir.rstrip("/")) or ".", "MODEL_EVOLUTION.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps({"arm": arm, "epoch": epoch, "prior_sha256": prior["sha256"],
                            "new_sha256": model["sha256"], "prior_version": prior["version"],
                            "new_version": model["version"], "model_changed": changed,
                            "items": [a["id"] for a in attempts],
                            "n_counterexamples": len(violations),
                            "assumptions_before": prior["assumptions"],
                            "assumptions_after": list(model["assumptions"]),
                            "active_before": prior["active"], "active_after": _snapshot(model)["active"]},
                           default=str) + "\n")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--pred", required=True)
    s.add_argument("--order", required=True)
    s.add_argument("--out", required=True)
    e = sub.add_parser("evolve")
    e.add_argument("--prev", required=True)
    e.add_argument("--pred", required=True)
    e.add_argument("--order", required=True)
    e.add_argument("--epoch", type=int, required=True)
    e.add_argument("--arm", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--history", default="", help="jsonl of the collections this arm has already admitted; the "
                                                 "new one is appended to it")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.cmd == "seed":
        rec = seed(args.pred, args.order, args.out)
        print(json.dumps({"model": rec["model"], "frozen": rec["frozen"],
                          "items_admitted": len(rec["items_admitted"]),
                          "tool_steps": rec["tool_steps_admitted"]}, indent=1, default=str))
    else:
        rec = evolve(args.prev, args.pred, args.order, args.epoch, args.arm, args.out, args.history)
        print(json.dumps({"arm": rec["arm"], "epoch": rec["epoch"],
                          "prior": {k: rec["prior_model"][k] for k in ("version", "sha256", "assumptions")},
                          "new": {k: rec["model"][k] for k in ("version", "sha256", "assumptions")},
                          "model_changed": rec["model_changed"],
                          "digest_differs_in_fields": rec["digest_differs_in_fields"],
                          "retained_in_error": [r["id"] for r in rec["retained"]
                                                if r["decision"] == "retained_in_error"],
                          "n_counterexamples": len(rec["counterexamples_found"]),
                          "out_of_sample": rec["out_of_sample_score_before_learning"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
