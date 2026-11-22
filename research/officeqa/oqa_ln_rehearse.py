#!/usr/bin/env python3
"""One isolated PAIRED rehearsal through SkillOpt's real analyst entry point. Rule OQA-LN-1.

WHAT IS COMPARED, and what is held identical.

  ordinary            `skillopt.gradient.reflect.run_error_analyst_minibatch` with the permitted raw
                      training trajectory, the skill document and the optimizer context. This is the
                      upstream path with no OfficeQA report variable set, so the prompt is what the
                      baseline analyst receives.
  contract-assisted   THE SAME CALL, with `OFFICEQA_STAGE_REPORT_DIR` pointing at a directory holding
                      the checked contract report for exactly this trace. That variable is the
                      production injection hook: `fmt_minibatch_trajectories` appends the report under
                      its own heading. The report is placed at the cache path that
                      `reflect._officeqa_report_cache_key` computes, so it is delivered BY the
                      production mechanism rather than pasted into a prompt by this script.

Identical in both: the model, the edit budget, the sampling settings, the number of calls (one each,
with upstream's own `retries`), the skill, the item and the trajectory. The contract condition gets no
extra revision opportunity.

NOT DONE HERE, deliberately: no proposed edit is applied to any skill, no validation runs, no held-out
record is touched, and nothing is written into a deployed run directory.

ONE DEVIATION FROM THE PRODUCTION PATH, recorded rather than hidden: the production analyst receives
`fail_reason`, which states that the scorer marked the attempt incorrect. It is omitted from BOTH
conditions here so that no score information reaches either analyst. Both are identical in this
respect, so the comparison is unaffected.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_ln_vocab as V  # noqa: E402

SCHEMA = "lso.officeqa_learned_source_rehearsal.v1"
EDIT_BUDGET = 4

SELECTION_RULE = ("among the CHECKING-side attempts: those with a committed answer whose "
                  "`source_traced` state is not UNKNOWN, ordered by attempt id, first one taken. "
                  "Declared before the rehearsal and applied mechanically; no score is read.")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def configure_route(config_path: str) -> dict:
    """The same router problem as everywhere else: configure it before sending, and fail fast."""
    import oqa_t2s as T2S
    effective = T2S.configure_models(config_path, os.environ.get("OPTIMIZER_MODEL", ""))
    if not effective.get("api_key_present"):
        raise SystemExit(f"the optimizer route has no API key: {effective}")
    return effective


def permitted_item(freeze_doc: dict, attempt: dict) -> tuple:
    """The item dict the analyst is given, built from PERMITTED fields only."""
    pdir = os.path.join(freeze_doc["source_root"], "train_p%d" % attempt["pass"], "predictions")
    att = EV.read_attempt(pdir, attempt["item"])
    ex = EV.extract(att)
    results = os.path.join(os.path.dirname(pdir), "results.jsonl")
    row = {}
    if os.path.exists(results):
        for line in open(results, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                if r.get("id") == attempt["item"]:
                    row = r
                    break
    item = {
        "id": attempt["item"],
        "task_description": ex["question"],
        "question": ex["question"],
        "task_type": str(row.get("task_type") or ""),
        "n_turns": row.get("n_turns"),
        "target_system_prompt": att["system_prompt"],
        "target_user_prompt": att["user_prompt"],
    }
    return item, pdir, att, ex


def choose(freeze_doc: dict, contract: dict) -> dict:
    cands = []
    for a in sorted(freeze_doc["attempts"], key=lambda d: d["attempt_id"]):
        if a["role"] != "check":
            continue
        rep = CK.stage_report(freeze_doc, a, contract)
        st = next((o["state"] for o in rep["obligations"] if o["obligation"] == "source_traced"), None)
        if rep["committed_answer_raw"] and st != CK.UNKNOWN:
            cands.append({"attempt_id": a["attempt_id"], "source_traced": st,
                          "n_superseded": len(rep["superseded_calculations"]),
                          "attempt": a, "report": rep})
    return {"candidates": [{k: c[k] for k in ("attempt_id", "source_traced", "n_superseded")}
                           for c in cands],
            "chosen": cands[0] if cands else None}


def _analyst(skill: str, item: dict, pred_dir: str, *, report_dir: str | None) -> dict:
    """ONE call through the real entry point. The only difference is the report variable."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.gradient import reflect
    prev = os.environ.get("OFFICEQA_STAGE_REPORT_DIR")
    if report_dir:
        os.environ["OFFICEQA_STAGE_REPORT_DIR"] = report_dir
    else:
        os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
    try:
        traj = reflect.fmt_minibatch_trajectories([item], pred_dir)
        patch = reflect.run_error_analyst_minibatch(skill, [item], pred_dir,
                                                    edit_budget=EDIT_BUDGET)
    finally:
        if prev is None:
            os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
        else:
            os.environ["OFFICEQA_STAGE_REPORT_DIR"] = prev
    return {"trajectory_text_sha256": IR.sha(traj), "trajectory_text_chars": len(traj),
            "report_heading_present": "Stage and Action Report" in traj,
            "patch": patch}


def cache_path(report_dir: str, pred_dir: str, tid: str, question: str) -> str:
    """The path the PRODUCTION hook will read, computed by the production hook's own key function."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.gradient import reflect
    condition = os.environ.get("OFFICEQA_STAGE_REPORT_CONDITION", "C")
    key = reflect._officeqa_report_cache_key(pred_dir, tid, question, condition)
    return os.path.join(report_dir, f"{tid}.{key}.md")


def edits_of(patch) -> list:
    if not patch:
        return []
    p = patch.get("patch") or {}
    return list(p.get("edits") or [])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn-root", required=True)
    ap.add_argument("--stage-root", default=None,
                    help="where the stage reports live, if not beside the models (the restaged ones)")
    ap.add_argument("--lean-root", required=True)
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--skill", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default="configs/research/officeqa_base2.yaml")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    route = configure_route(a.config)
    print("optimizer route: " + json.dumps({k: v for k, v in route.items()
                                            if k != "api_key_present"}))
    with open(a.freeze, encoding="utf-8") as f:
        freeze_doc = json.load(f)
    with open(a.skill, encoding="utf-8") as f:
        skill = f.read()

    # the contract: the last ADMITTED version of the first repetition that produced one
    models = sorted(glob.glob(os.path.join(a.learn_root, "r*_model_v*.json")))
    if not models:
        print(f"no frozen model under {a.learn_root}", file=sys.stderr)
        return 2
    with open(models[-1], encoding="utf-8") as f:
        model = json.load(f)
    active = [c for c in model["contracts"] if c["status"] == "active"]
    contract = active[-1]
    model_sha = model.get("sha256") or IR.model_sha(model)

    lean = None
    lp = os.path.join(a.lean_root, "LN_LEAN.json")
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            lr = json.load(f)
        lean = {"version": lr.get("version"), "returncode": (lr.get("result") or {}).get("returncode"),
                "source_sha256": lr.get("source_sha256"), "n_examples": lr.get("n_examples"),
                "scope": lr.get("scope")}

    sel = choose(freeze_doc, contract)
    if not sel["chosen"]:
        print("no checking attempt satisfies the declared selection rule", file=sys.stderr)
        return 2
    chosen = sel["chosen"]
    item, pred_dir, att, ex = permitted_item(freeze_doc, chosen["attempt"])

    n_learn = sum(1 for _ in [1])  # replaced below from the run summary when present
    run_summary = os.path.join(a.learn_root, "LN_RUN.json")
    if os.path.exists(run_summary):
        with open(run_summary, encoding="utf-8") as f:
            n_learn = json.load(f).get("n_learn_operands", 0)

    report_text = CK.render_report(chosen["report"], n_learn_operands=n_learn,
                                   contract_rendered=V.render(contract), model_sha256=model_sha,
                                   lean=lean)
    report_sha = IR.sha(report_text)
    report_dir = os.path.join(a.out, "reports")
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(a.out, "DELIVERED_REPORT.md"), "w", encoding="utf-8") as f:
        f.write(report_text)
    cp = cache_path(report_dir, pred_dir, item["id"], item["question"])
    with open(cp, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"rehearsing on {chosen['attempt_id']}  report {report_sha[:16]}  "
          f"contract {contract['id']}@v{contract['version']}")
    ordinary = _analyst(skill, item, pred_dir, report_dir=None)
    assisted = _analyst(skill, item, pred_dir, report_dir=report_dir)

    doc = {
        "schema": SCHEMA, "rule": V.RULE, "written_utc": _utc(),
        "freeze_sha256": freeze_doc["self_sha256"],
        "selection_rule": SELECTION_RULE,
        "selection": {k: sel[k] for k in ("candidates",)},
        "attempt_id": chosen["attempt_id"], "item": item["id"],
        "trace_sha256": att["trace_sha256"],
        "skill_path": os.path.abspath(a.skill), "skill_sha256": IR.sha(skill),
        "skill_bytes": len(skill),
        "contract": f"{contract['id']}@v{contract['version']}",
        "contract_sha256": IR.sha(IR.canon(contract)),
        "model_sha256": model_sha, "model_path": models[-1],
        "lean": lean,
        "report_sha256": report_sha, "report_bytes": len(report_text),
        "report_delivered_at": cp,
        "edit_budget": EDIT_BUDGET,
        "deviation_from_production": (
            "`fail_reason` is omitted from both conditions, because it states that the scorer marked "
            "the attempt incorrect. Identical in both, so the comparison is unaffected."),
        "conditions": {
            "ordinary": {
                "trajectory_text_sha256": ordinary["trajectory_text_sha256"],
                "trajectory_text_chars": ordinary["trajectory_text_chars"],
                "report_present": ordinary["report_heading_present"],
                "patch": ordinary["patch"],
                "n_edits": len(edits_of(ordinary["patch"])),
            },
            "contract_assisted": {
                "trajectory_text_sha256": assisted["trajectory_text_sha256"],
                "trajectory_text_chars": assisted["trajectory_text_chars"],
                "report_present": assisted["report_heading_present"],
                "patch": assisted["patch"],
                "n_edits": len(edits_of(assisted["patch"])),
            },
        },
        "inputs_identical_except_the_report": (
            assisted["trajectory_text_chars"] - ordinary["trajectory_text_chars"] >= len(report_text) - 200
            and ordinary["report_heading_present"] is False),
        "paid_calls": 2,
        "optimizer_route_effective": route,
    }
    body = json.dumps(doc, indent=1, sort_keys=True, default=str)
    doc["self_sha256"] = IR.sha(body)
    with open(os.path.join(a.out, "LN_REHEARSAL.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    for name, cond in (("ordinary", ordinary), ("contract_assisted", assisted)):
        with open(os.path.join(a.out, f"EDITS_{name}.json"), "w", encoding="utf-8") as f:
            json.dump({"condition": name, "patch": cond["patch"],
                       "edits": edits_of(cond["patch"])}, f, indent=1, sort_keys=True, default=str)
    print(f"  ordinary          edits {doc['conditions']['ordinary']['n_edits']}  "
          f"report_present {doc['conditions']['ordinary']['report_present']}")
    print(f"  contract-assisted edits {doc['conditions']['contract_assisted']['n_edits']}  "
          f"report_present {doc['conditions']['contract_assisted']['report_present']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
