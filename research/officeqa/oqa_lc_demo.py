"""Run the whole learned-contract loop over saved OfficeQA traces: learn, freeze, predict, repair, recheck, Lean.

Offline. No GPU job, no paid call, no network. It reads only recorded prediction directories, writes a new output
directory, and never modifies a run artifact.

The replay rule, declared before the loop runs so it cannot be chosen with hindsight:

    Attempts are processed in the order the run recorded its results (`results.jsonl`), which with four workers is
    the completion order of the rollout, not a designed curriculum. After each attempt, the deterministic proposer
    runs on the evidence seen SO FAR and the resulting model is FROZEN by digest. The NEXT attempt is then replayed
    against that frozen model: each step's prediction is committed, with its sha256 in a hash chain, before the
    step's observation is released. A model therefore never predicts an observation it was learned from.

    A second collection (a later epoch of the same training items) is replayed afterwards against the final model
    as independent confirmation. Held-out items are never read.

usage:
  python3 -I research/officeqa/oqa_lc_demo.py --learn <predictions dir> --order <results.jsonl> \
      --confirm <predictions dir> --confirm-order <results.jsonl> --out <new dir> [--regression UID0014]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oqa_lc_chain as CHAIN  # noqa: E402
import oqa_lc_ir as IR  # noqa: E402
import oqa_lc_lean as LEAN  # noqa: E402
import oqa_lc_learn as LEARN  # noqa: E402
import oqa_lc_replay as REPLAY  # noqa: E402
import oqa_lc_report as REPORT  # noqa: E402
import oqa_lc_vocab as VOCAB  # noqa: E402
import oqa_trace as TR  # noqa: E402

SEED_ASSUMPTIONS = ["flat_header_authoritative", "index_alignment", "year_inheritance",
                    "table_scale_applies_to_rows", "grep_cap_not_reached"]


def read_order(path: str) -> list:
    """Item ids in the order the run recorded them."""
    ids = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("id") and row["id"] not in ids:
                ids.append(str(row["id"]))
    return ids


def load_attempts(pred_dir: str, order: list) -> list:
    out = []
    for uid in order:
        d = os.path.join(pred_dir, uid)
        if not os.path.isdir(d):
            continue
        pk = TR.build_packet(pred_dir, uid)
        pk["answer_kind"] = (pk.get("question") and
                             __import__("oqa_obligations").derive(pk["question"])["answer_kind"]["kind"]) or None
        out.append(pk)
    return out


def loop(attempts: list, out_dir: str) -> dict:
    """Incremental learn-freeze-replay over the attempts, in the given order."""
    os.makedirs(os.path.join(out_dir, "frozen"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "predictions"), exist_ok=True)
    model = IR.empty_model()
    ev = REPLAY.Evidence([])
    rounds = []
    for k, attempt in enumerate(attempts):
        step_rec: dict = {"n": k, "item": attempt["id"], "events": []}

        # 1. Replay this attempt against the model frozen from the evidence seen so far.
        if model["version"] > 0 or model["assumptions"]:
            sink_path = os.path.join(out_dir, "predictions", f"{k:02d}_{attempt['id']}.jsonl")
            res = REPLAY.replay_attempt(attempt, model, REPLAY.jsonl_sink(sink_path))
            step_rec["replay"] = {"model_version": model["version"], "model_sha256": model.get("sha256"),
                                  "score": REPLAY.score(res["attempts"]),
                                  "predictions_file": os.path.basename(sink_path),
                                  "reveal_log_sha256": VOCAB.sha(VOCAB.canon(res["reveal_log"]))}
            viol = [r for r in res["attempts"]
                    if any(f["class"] == REPLAY.FORECAST_VIOLATED for f in r.get("forecast_checks") or [])]
            step_rec["violations"] = [{"aid": r["aid"], "step": r["step"], "tool": r["tool"],
                                       "violated": [f for f in r["forecast_checks"]
                                                    if f["class"] == REPLAY.FORECAST_VIOLATED],
                                       "witnesses": r.get("witnesses") or []} for r in viol]
            step_rec["forecast_records"] = [
                {"aid": r["aid"], "contract": f["contract"], "atom": f["atom"], "predicted": f["predicted"],
                 "observed": f["observed"], "class": f["class"]}
                for r in res["attempts"] for f in r.get("forecast_checks") or []]

        # 2. Admit this attempt into the evidence.
        ev.add([attempt])

        # 3. Seed the assumption set on the first round, then propose.
        if k == 0:
            prop = {"assumption_ops": [{"op": "adopt", "name": a,
                                        "reason": "seed: the binding of a figure to a column, a period and a unit "
                                                  "is not possible without it"} for a in SEED_ASSUMPTIONS],
                    "contract_ops": [], "source": "seed"}
            model, dec = LEARN.apply_proposal(model, prop, ev, epoch=k + 1, source="seed")
            step_rec["events"].append({"phase": "seed_assumptions", "decisions": dec})

        induced = LEARN.mech_induce(ev, model["assumptions"], model=model)
        if induced["contract_ops"]:
            model, dec = LEARN.apply_proposal(model, induced, ev, epoch=k + 1, source="mech")
            step_rec["events"].append({"phase": "induce", "n_proposed": len(induced["contract_ops"]),
                                       "decisions": dec})

        # 4a. Re-test every adopted, directly testable assumption against the evidence as it now stands.
        acheck = LEARN.assumption_recheck(model, ev)
        if acheck["assumption_ops"]:
            before = LEARN.recheck(model, ev)
            model, dec = LEARN.apply_proposal(model, acheck, ev, epoch=k + 1, source="mech-assumption-recheck")
            step_rec["events"].append({"phase": "assumption_recheck", "decisions": dec,
                                       "recheck_before": before, "recheck_after": LEARN.recheck(model, ev)})

        # 4b. Repair whatever the evidence now refutes, and recheck every contract afterwards.
        repair = LEARN.mech_repair(model, ev)
        if repair["assumption_ops"] or repair["contract_ops"]:
            before = LEARN.recheck(model, ev)
            model, dec = LEARN.apply_proposal(model, repair, ev, epoch=k + 1, source="mech-repair")
            step_rec["events"].append({"phase": "repair", "decisions": dec,
                                       "recheck_before": before, "recheck_after": LEARN.recheck(model, ev)})

        # 5. Freeze.
        path = os.path.join(out_dir, "frozen", f"model_v{model['version']:02d}_{k:02d}_{attempt['id']}.json")
        try:
            model["sha256"] = IR.freeze(model, path)
        except RuntimeError as exc:
            step_rec["freeze_problem"] = str(exc)
            model["sha256"] = IR.model_sha(model)
        step_rec["model"] = {"version": model["version"], "sha256": model["sha256"],
                            "assumptions": list(model["assumptions"]),
                            "active": [c["id"] for c in IR.active(model)],
                            "refuted": [c["id"] for c in model["contracts"] if c.get("status") == "refuted"],
                            "rejected": len(model.get("rejected") or [])}
        rounds.append(step_rec)
    return {"rounds": rounds, "model": model, "evidence_items": [a["id"] for a in ev.attempts_]}


def regression_on(attempts: list, model: dict, item_id: str) -> dict:
    """The known false warning, before and after: the old report's claim against the obligation resolution."""
    import oqa_check as CHK
    pk = next((a for a in attempts if a["id"] == item_id), None)
    if pk is None:
        return {"item": item_id, "problem": "not in this collection"}
    old = CHK.report(pk, "C")
    old_scale = [f for f in old["findings"] if f["cat"] == "scale_conversion_missing"]
    ch = CHAIN.build(pk, model)
    unit = [o for o in ch["ledger"] if o["kind"] == "unit_match"]
    return {
        "item": item_id,
        "old_report": {"n_scale_conversion_missing_findings": len(old_scale),
                       "n_occurrences": sum(len(f["occ"]) for f in old_scale),
                       "texts": [f["text"][:300] for f in old_scale]},
        "obligation_report": {"unit_match": CHAIN.summary(unit),
                              "discharged": [o["why"][:260] for o in unit if o["state"] == CHAIN.DISCHARGED][:4],
                              "violated": [o["why"][:260] for o in unit if o["state"] == CHAIN.VIOLATED][:4],
                              "outstanding": [o["why"][:200] for o in unit
                                              if o["state"] == CHAIN.OUTSTANDING][:2]},
        "plain_comparison": REPORT.compare_with_plain(pk, ch),
    }


def lean_bundle(rounds: list, attempts: list, model: dict, out_dir: str) -> dict:
    """Generate the Lean file for the general theorems and the adjudicated evidence, and run the Python mirror."""
    atom_rows = []
    for r in rounds:
        for f in r.get("forecast_records") or []:
            if f["observed"] is not None:
                atom_rows.append(f)
    operand_rows = []
    for a in attempts:
        ch = CHAIN.build(a, model)
        by_subject: dict = {}
        for ob in ch["ledger"]:
            if ob["kind"] in ("cell_bound", "period_match", "unit_match"):
                by_subject.setdefault(ob["subject"], {})[ob["kind"]] = ob
        asked = ch["task"]["scale"]["scale"]
        for subject, obs in by_subject.items():
            w = (obs.get("cell_bound") or {}).get("witness") or (obs.get("unit_match") or {}).get("witness")
            applied = 0
            unit = obs.get("unit_match") or {}
            if unit.get("state") in (CHAIN.DISCHARGED, CHAIN.VIOLATED, CHAIN.OUTSTANDING):
                applied = _applied_exp(unit)
            operand_rows.append(LEAN.operand_row({
                "aid": f"{a['id']}_{_safe(subject)}", "witness": w, "asked_scale": asked,
                "period_match": obs.get("period_match"), "applied_exp": applied}))
    # Keep the generated file small enough to compile quickly: one row per distinct Lean shape.
    seen, unique_ops = set(), []
    for row in operand_rows:
        key = (row["basis"], row["periodAsked"], row["cellExp"], row["askedExp"], row["appliedExp"])
        if key in seen:
            continue
        seen.add(key)
        unique_ops.append(row)
    seen_a, unique_atoms = set(), []
    for row in atom_rows:
        key = (row["atom"], row["predicted"], row["observed"])
        if key in seen_a:
            continue
        seen_a.add(key)
        unique_atoms.append(row)
    src, mirror = LEAN.generate(unique_ops, unique_atoms)
    path = os.path.join(out_dir, "lean", "OqaContracts.lean")
    digest = LEAN.write(src, path)
    return {"file": os.path.relpath(path, out_dir), "sha256": digest, "mirror": mirror,
            "operand_shapes": len(unique_ops), "from_operands": len(operand_rows),
            "atom_shapes": len(unique_atoms), "from_atoms": len(atom_rows),
            "compiled": False,
            "why_not_compiled": "no Lean toolchain on the workstation (lean / lake / elan absent); compiling needs "
                                "an authorized cluster CPU job"}


def _applied_exp(unit_ob: dict) -> int:
    """log10 of the factor the actor applied, recorded on the obligation by `oqa_lc_chain._unit_obligation`."""
    return int(unit_ob.get("applied_exp") or 0)


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn", required=True, help="predictions dir of the development collection")
    ap.add_argument("--order", required=True, help="results.jsonl giving that collection's recorded order")
    ap.add_argument("--confirm", help="predictions dir of a later collection, replayed against the final model")
    ap.add_argument("--confirm-order")
    ap.add_argument("--regression", default="UID0014", help="item whose known false warning is the regression case")
    ap.add_argument("--out", required=True, help="output directory; must not exist")
    args = ap.parse_args()
    if os.path.exists(args.out):
        print(f"refusing to write into an existing directory: {args.out}", file=sys.stderr)
        return 2
    os.makedirs(args.out)

    dev_order = read_order(args.order)
    dev = load_attempts(args.learn, dev_order)
    result = loop(dev, args.out)
    model = result["model"]

    confirm = {}
    if args.confirm and args.confirm_order:
        later = load_attempts(args.confirm, read_order(args.confirm_order))
        ev2 = REPLAY.Evidence([])
        recs = []
        os.makedirs(os.path.join(args.out, "confirm"), exist_ok=True)
        for a in later:
            sink = REPLAY.jsonl_sink(os.path.join(args.out, "confirm", f"{a['id']}.jsonl"))
            res = REPLAY.replay_attempt(a, model, sink)
            recs += res["attempts"]
            ev2.add([a])
        confirm = {"items": [a["id"] for a in later], "score": REPLAY.score(recs),
                   "forecast_records": [{"aid": r["aid"], "contract": f["contract"], "atom": f["atom"],
                                         "predicted": f["predicted"], "observed": f["observed"],
                                         "class": f["class"]}
                                        for r in recs for f in r.get("forecast_checks") or []
                                        if f["observed"] is not None],
                   "violations": [{"aid": r["aid"], "step": r["step"], "witnesses": r.get("witnesses") or []}
                                  for r in recs
                                  if any(f["class"] == REPLAY.FORECAST_VIOLATED
                                         for f in r.get("forecast_checks") or [])],
                   "recheck_against_final_model": LEARN.recheck(model, ev2),
                   # Independent confirmation: re-test every assumption the repair dropped against a collection
                   # the repair never saw. A dropped assumption that the later records also contradict was
                   # dropped for a reason that generalises beyond the one item that refuted it.
                   "dropped_assumptions_retested": {
                       name: {"n_contradictions": len(LEARN.direct_contradictions(name, ev2)),
                              "witnesses": LEARN.direct_contradictions(name, ev2)[:6]}
                       for name in SEED_ASSUMPTIONS if name not in (model.get("assumptions") or [])}}
        result["rounds"] = result["rounds"] + [{"n": "confirm", "item": "(later collection)",
                                                "forecast_records": confirm["forecast_records"]}]

    # The regression case is run on BOTH collections. The item has a recorded attempt in each, and they are
    # different attempts: in the development collection it requested no tool at all, while the false warning the
    # audits documented is in the later collection's attempt, which converted the value at its first message.
    # Reporting only one of them would pick the answer.
    later_attempts = (load_attempts(args.confirm, read_order(args.confirm_order))
                      if args.confirm and args.confirm_order else [])
    reg = {"item": args.regression,
           "development_collection": regression_on(dev, model, args.regression),
           "later_collection": regression_on(later_attempts, model, args.regression) if later_attempts else None}
    reg_source = later_attempts or dev

    # The Lean evidence covers BOTH collections, because the operand shapes that matter most - a conversion
    # applied with the right factor and one applied with the wrong one - occur in the later collection's attempt.
    lean = lean_bundle(result["rounds"], dev + later_attempts, model, args.out)

    # one full report, for the worked example
    worked_id = args.regression
    worked = None
    for a in (reg_source or dev):
        if a["id"] == worked_id:
            ch = CHAIN.build(a, model)
            worked = REPORT.render(ch, model)
            with open(os.path.join(args.out, f"report_{worked_id}.md"), "w", encoding="utf-8") as f:
                f.write(worked)
            break

    summary = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "vocab_digest": VOCAB.vocab_digest(),
        "learn_collection": os.path.abspath(args.learn), "learn_order": dev_order,
        "confirm_collection": os.path.abspath(args.confirm) if args.confirm else None,
        "final_model": {"version": model["version"], "sha256": model.get("sha256"),
                        "assumptions": model["assumptions"],
                        "contracts": [{"id": c.get("id"), "version": c.get("version"), "status": c.get("status"),
                                       "rendered": IR.render(c), "evidence": c.get("evidence"),
                                       "reason": c.get("reason", ""), "refute_reason": c.get("refute_reason", "")}
                                      for c in model["contracts"]],
                        "rejected": model.get("rejected") or []},
        "rounds": [{k: v for k, v in r.items() if k != "forecast_records"} for r in result["rounds"]],
        "confirm": confirm, "regression": reg, "lean": lean,
    }
    with open(os.path.join(args.out, "LOOP.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str)
    print(json.dumps({"out": args.out, "final_model_version": model["version"],
                      "assumptions": model["assumptions"],
                      "active": [c["id"] for c in IR.active(model)],
                      "refuted": [c["id"] for c in model["contracts"] if c.get("status") == "refuted"],
                      "lean": {k: lean[k] for k in ("file", "sha256", "operand_shapes", "atom_shapes")},
                      "mirror_mismatches": lean["mirror"]["mismatches"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
