#!/usr/bin/env python3
"""Run the learner repetitions end to end, preserving every proposal and every decision. Rule OQA-LN-1.

ONE REPETITION, in the order the instruction fixes:

  1. learn a candidate contract from the LEARNING records                       [1 paid call]
  2. validate it against the vocabulary, score it on those records, FREEZE it with a digest
  3. predict on the CHECKING questions - questions the learner never saw - using only the prefix
     features; the predictions are written and digested BEFORE any verdict is read
  4. read the verdicts and classify each discrepancy
  5. if a genuine contradiction exists, ask the learner to repair the hypothesis, supplying the
     counterexamples' features and NOT the value it should have produced                 [1 paid call]
  6. recheck the LEARNING records with the repaired contract, so a repair that breaks what already
     worked is visible as a regression rather than reported as an improvement
  7. write the stage reports, including the recovery controls

Nothing is deleted: a superseded contract version is kept with its reason, and the proposal that
produced it is kept with its full reply.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_learn as LN  # noqa: E402
import oqa_ln_vocab as V  # noqa: E402

SCHEMA = "lso.officeqa_learned_source_run.v1"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _write(path: str, doc: dict) -> str:
    body = json.dumps(doc, indent=1, sort_keys=True, default=str)
    digest = IR.sha(body)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return digest


def one_repetition(rep: int, freeze_doc: dict, learn_rows: list, check_rows: list, outdir: str,
                   cap: int) -> dict:
    tag = f"r{rep}"
    rec: dict = {"repetition": rep, "started_utc": _utc(), "calls": [], "paid_calls": 0}

    # ── 1. learn ──────────────────────────────────────────────────────────────
    call = LN.propose(learn_rows, freeze_doc, cap=cap)
    rec["paid_calls"] += 1
    _write(os.path.join(outdir, f"{tag}_call1_propose.json"), call)
    rec["calls"].append({"stage": "propose", "prompt_sha256": call["prompt_sha256"],
                         "prompt_chars": call["prompt_chars"], "reply_chars": call["reply_chars"],
                         "usage": call["usage"], "problem": call["problem"]})
    if call["parsed"] is None:
        rec["outcome"] = "the learner's reply could not be parsed; nothing was accepted"
        rec["problem"] = call["problem"]
        return rec

    # ── 2. validate, score on the LEARNING records, freeze ───────────────────
    built = LN.compile_proposal(call["parsed"], learn_rows)
    rec["proposal"] = {"problems": built["problems"],
                       "rendered": V.render(built["contract"]) if not built["problems"] else None,
                       "reason": built["contract"].get("reason"),
                       "expected_failures": built["expected_failures"],
                       "score_on_learning_records": built["score"]}
    if built["problems"]:
        rec["outcome"] = "the proposal was REJECTED by the validator before it was scored"
        return rec
    c1 = built["contract"]
    m1 = V.model([c1], epoch=1, scope="LEARNING records of OQA-LN-1",
                 learned_from=sorted({r["attempt_id"] for r in learn_rows}))
    m1_sha = IR.model_sha(m1)
    _write(os.path.join(outdir, f"{tag}_model_v1.json"), {**m1, "sha256": m1_sha,
                                                          "frozen_utc": _utc()})
    rec["model_v1_sha256"] = m1_sha
    rec["contract_v1_sha256"] = IR.sha(IR.canon(c1))

    # ── 3. predict on the CHECKING questions, and digest BEFORE any verdict ──
    preds = CK.predict(c1, check_rows)
    rec["predictions_sha256"] = _write(os.path.join(outdir, f"{tag}_predictions_check.json"), preds)
    rec["n_predicted_sourced_on_check"] = preds["n_predicted_sourced"]

    # ── 4. verdicts, read only now ───────────────────────────────────────────
    verd = CK.resolve(preds, check_rows)
    rec["verdicts_sha256"] = _write(os.path.join(outdir, f"{tag}_verdicts_check.json"), verd)
    rec["check_counts"] = verd["counts"]
    rec["score_on_checking_records"] = LN.score_contract(c1, check_rows)

    counters = [r for r in verd["results"] if r["discrepancy_kind"] == CK.MODEL_REFUTATION]
    rec["counterexamples"] = [{k: r[k] for k in ("attempt_id", "item", "as_written", "statement",
                                                 "verdict_match", "why")} for r in counters[:20]]
    rec["n_counterexamples"] = len(counters)

    # ── 5. repair, only on a genuine contradiction ───────────────────────────
    if not counters:
        rec["repair"] = {"attempted": False,
                         "why": "no operand on the checking questions was predicted sourced and then "
                                "found to have a unique position with no corroboration, so there is "
                                "no natural contradiction to repair from. This is reported as it is; "
                                "a contradiction is not manufactured."}
        rec["outcome"] = "learned and checked; no repair was warranted"
        return rec

    rcall = LN.repair_call(c1, check_rows, rec["score_on_checking_records"],
                           len(freeze_doc["selection"]["check_questions"]), cap=min(cap, 4000))
    rec["paid_calls"] += 1
    _write(os.path.join(outdir, f"{tag}_call2_repair.json"), rcall)
    rec["calls"].append({"stage": "repair", "prompt_sha256": rcall["prompt_sha256"],
                         "prompt_chars": rcall["prompt_chars"], "reply_chars": rcall["reply_chars"],
                         "usage": rcall["usage"], "problem": rcall["problem"]})
    if rcall["parsed"] is None:
        rec["repair"] = {"attempted": True, "accepted": False, "problem": rcall["problem"]}
        rec["outcome"] = "the repair reply could not be parsed; v1 stands"
        return rec
    parsed = rcall["parsed"]
    if parsed.get("separable") is False or not parsed.get("extra_condition"):
        rec["repair"] = {"attempted": True, "accepted": False,
                         "learner_said_not_separable": True,
                         "note": str(parsed.get("note") or ""),
                         "why": "the learner reported that no available predicate separates the "
                                "counterexamples from the correct predictions. Recorded as the "
                                "learner's own finding; no condition was invented for it."}
        rec["outcome"] = "a contradiction exists and the learner declined to repair it"
        return rec

    extra = V.clause(parsed["extra_condition"].get("pred", ""),
                     parsed["extra_condition"].get("args") or [],
                     parsed["extra_condition"].get("value", True))
    old, c2 = V.repair(c1, extra, str(parsed.get("reason") or "counterexample-driven narrowing"),
                       [f'{r["attempt_id"]}#{r["operand_key"]}' for r in counters[:20]])
    problems = V.validate(c2)
    rec["repair"] = {"attempted": True, "accepted": not problems,
                     "extra_condition": extra, "problems": problems,
                     "reason": str(parsed.get("reason") or ""),
                     "rendered_v2": V.render(c2) if not problems else None,
                     "superseded_v1_kept": old["status"] == "superseded"}
    if problems:
        rec["outcome"] = "the repair was REJECTED by the validator; v1 stands and v2 was not admitted"
        return rec

    m2 = V.model([old, c2], epoch=2, scope="repaired from counterexamples on the CHECKING questions",
                 learned_from=sorted({r["attempt_id"] for r in learn_rows}))
    m2["parent_sha256"] = m1_sha
    m2_sha = IR.model_sha(m2)
    _write(os.path.join(outdir, f"{tag}_model_v2.json"), {**m2, "sha256": m2_sha,
                                                          "frozen_utc": _utc()})
    rec["model_v2_sha256"] = m2_sha

    # ── 6. recheck: the checking records AND the learning records ────────────
    rec["after_repair"] = {
        "score_on_checking_records": LN.score_contract(c2, check_rows),
        "score_on_learning_records": LN.score_contract(c2, learn_rows),
        "regression_on_learning_records": None,
    }
    before = built["score"]
    after = rec["after_repair"]["score_on_learning_records"]
    lost = sorted(set(before["confirmation_keys"]) - set(after["confirmation_keys"]))
    rec["after_repair"]["regression_on_learning_records"] = {
        "confirmations_before": before["confirmations"], "confirmations_after": after["confirmations"],
        "counterexamples_before": before["counterexamples"],
        "counterexamples_after": after["counterexamples"],
        "confirmations_lost": lost[:20], "n_confirmations_lost": len(lost),
        "verdict": ("no regression: every confirmation the first version had is still a confirmation"
                    if not lost else
                    f"REGRESSION: {len(lost)} operand(s) the first version got right are no longer "
                    f"covered"),
    }
    rec["outcome"] = "learned, refuted, repaired and rechecked"
    return rec


def stage_reports(freeze_doc: dict, contract: dict, outdir: str, tag: str) -> dict:
    """Every recovery control, plus every checking attempt, through the stage chain."""
    wanted = {r["attempt_id"] for r in freeze_doc["recovery_controls"]}
    rows = [a for a in freeze_doc["attempts"]
            if a["role"] == "check" or a["attempt_id"] in wanted]
    reports = [CK.stage_report(freeze_doc, a, contract) for a in rows]
    doc = {"schema": SCHEMA + ".stage", "written_utc": _utc(),
           "contract": f"{contract['id']}@v{contract['version']}",
           "contract_sha256": IR.sha(IR.canon(contract)),
           "n_attempts": len(reports),
           "recovery_controls": [r for r in reports if r["recovery_shape"].get("qualifies")],
           "reports": reports}
    digest = _write(os.path.join(outdir, f"{tag}_stage_reports.json"), doc)
    return {"digest": digest, "n_attempts": len(reports),
            "n_recovery_controls": len(doc["recovery_controls"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repetitions", type=int, default=2)
    ap.add_argument("--cap", type=int, default=8000)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    with open(a.freeze, encoding="utf-8") as f:
        freeze_doc = json.load(f)
    learn_rows = LN.rows_for(freeze_doc, "learn")
    check_rows = LN.rows_for(freeze_doc, "check")
    print(f"freeze {freeze_doc['self_sha256'][:16]}  learn {len(learn_rows)} operands  "
          f"check {len(check_rows)} operands")

    reps, total_paid = [], 0
    for i in range(1, a.repetitions + 1):
        rec = one_repetition(i, freeze_doc, learn_rows, check_rows, a.out, a.cap)
        total_paid += rec["paid_calls"]
        # the stage reports use the LAST admitted version of this repetition's contract
        model_path = os.path.join(a.out, f"r{i}_model_v2.json")
        if not os.path.exists(model_path):
            model_path = os.path.join(a.out, f"r{i}_model_v1.json")
        if os.path.exists(model_path):
            with open(model_path, encoding="utf-8") as f:
                m = json.load(f)
            active = [c for c in m["contracts"] if c["status"] == "active"]
            if active:
                rec["stage_reports"] = stage_reports(freeze_doc, active[-1], a.out, f"r{i}")
        reps.append(rec)
        print(f"  rep {i}: {rec.get('outcome')}  paid calls {rec['paid_calls']}")

    summary = {
        "schema": SCHEMA, "rule": LN.V.RULE, "written_utc": _utc(),
        "freeze_sha256": freeze_doc["self_sha256"],
        "vocabulary_bundle": {"label": V.BUNDLE_LABEL, "digest": V.bundle_digest()},
        "initial_model": V.initial_model(),
        "learn_questions": freeze_doc["selection"]["learn_questions"],
        "check_questions": freeze_doc["selection"]["check_questions"],
        "n_learn_operands": len(learn_rows), "n_check_operands": len(check_rows),
        "n_learn_sourced": sum(1 for r in learn_rows if r["verdict"]["sourced"]),
        "n_check_sourced": sum(1 for r in check_rows if r["verdict"]["sourced"]),
        "repetitions": reps,
        "total_paid_calls": total_paid,
    }
    digest = _write(os.path.join(a.out, "LN_RUN.json"), summary)
    print(f"wrote {os.path.join(a.out, 'LN_RUN.json')}  digest {digest[:16]}  "
          f"paid calls {total_paid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
