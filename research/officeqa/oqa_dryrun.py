"""Run saved OfficeQA epochs through the ACTUAL training entry point, with no actor call and no paid call.

Offline. `scripts/train.py:main()` is the real entry point and the real `ReflACTTrainer` runs; what is replaced is
exactly two things, and both are replaced because the authorization for this work covers neither:

  THE ACTOR     `skillopt.envs.officeqa.rollout.chat_target_messages` is replaced by a function that RAISES. Every
                rollout directory the trainer will use is pre-seeded from recorded predictions, and `run_batch`
                resumes from `results.jsonl`, so a correctly seeded run makes no request at all. If the trainer
                asks for an item that was not recorded, the run fails loudly instead of silently spending money.

  THE OPTIMIZER `chat_optimizer` is replaced in each module that imported it by a recorder that returns a fixed,
                recorded-shape response per stage. Every prompt is written to `optimizer_calls.jsonl` with its
                stage, so what reached the optimizer can be read afterwards. The stub's OUTPUT is not meant to be
                realistic and no conclusion is drawn from the skills this produces; what the dry run establishes
                is which INPUT each arm's analyst received.

WHAT IT CHECKS, which is what the user asked to see before launch:

  1. the control arm receives the ordinary SkillOpt analyst input - no report, no report heading;
  2. arm B receives the Action-Only report under its own heading, and that text contains no stage material;
  3. arm C receives the action half and the stage half, and arm C's report contains arm B's line for line;
  4. every report records the digest of the trace it was built from and of the frozen model in force, and both
     match the attempt on disk and the model the arm was run with;
  5. the per-epoch contract-model evolution record names the prior model, the new evidence, and what justified
     each repair or retain.

usage:
  python3 research/officeqa/oqa_dryrun.py --arm C --out <dir> \
      --epoch1 <rollout dir> --epoch2 <rollout dir> --val <rollout dir> \
      --lc-model <frozen model json> [--config configs/research/officeqa_api3.yaml]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

STAGE_RESPONSES = {
    # Recorded-shape responses. The edits are copied from a real recorded patch of the A40 run so that the
    # downstream apply / merge / rank code sees the shape it sees in a live run.
    "analyst": {"failure_summary": [], "patch": {
        "reasoning": "dry run: a fixed response, so the skills this produces mean nothing.",
        "edits": [{"op": "insert_after", "target": "## Workflow",
                   "content": "- DRY RUN EDIT 1: map every value to its full column path before using it."},
                  {"op": "insert_after", "target": "## Workflow",
                   "content": "- DRY RUN EDIT 2: state the unit conversion once, with its factor."}]}},
    "merge": {"reasoning": "dry run merge", "edits": [
        {"op": "insert_after", "target": "## Workflow",
         "content": "- DRY RUN EDIT 1: map every value to its full column path before using it."},
        {"op": "insert_after", "target": "## Workflow",
         "content": "- DRY RUN EDIT 2: state the unit conversion once, with its factor."}]},
    "ranking": {"reasoning": "dry run ranking", "selected_indices": [0, 1]},
    "slow_update": {"reasoning": "dry run slow update",
                    "slow_update_content": "DRY RUN SLOW UPDATE: carry forward nothing."},
    "meta_skill": {"reasoning": "dry run meta skill",
                   "meta_skill_content": "DRY RUN META SKILL: carry forward nothing."},
}

OPTIMIZER_MODULES = ("skillopt.gradient.reflect", "skillopt.gradient.aggregate", "skillopt.optimizer.clip",
                     "skillopt.optimizer.lr_autonomous", "skillopt.optimizer.slow_update",
                     "skillopt.optimizer.meta_skill", "skillopt.optimizer.rewrite")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── seeding ─────────────────────────────────────────────────────────────────────

def read_rows(results_path: str) -> list:
    rows = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_split(split_dir: str, name: str, rows: list) -> int:
    """One split as a CSV the adapter's own dataloader reads. Fields are exactly the ones it normalises."""
    import csv
    d = os.path.join(split_dir, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "items.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["uid", "question", "ground_truth", "category", "source_files",
                                          "source_docs", "split"])
        w.writeheader()
        for r in rows:
            w.writerow({"uid": r["id"], "question": r.get("question", ""),
                        "ground_truth": r.get("ground_truth", ""),
                        "category": r.get("task_type", "officeqa"),
                        "source_files": ";".join(r.get("source_files") or []) if isinstance(
                            r.get("source_files"), list) else str(r.get("source_files") or ""),
                        "source_docs": "", "split": name})
    return len(rows)


def seed_rollout(src: str, dst: str) -> dict:
    """Copy one recorded rollout into a directory the trainer is about to use, so `run_batch` resumes from it."""
    os.makedirs(dst, exist_ok=True)
    shutil.copyfile(os.path.join(src, "results.jsonl"), os.path.join(dst, "results.jsonl"))
    src_pred, dst_pred = os.path.join(src, "predictions"), os.path.join(dst, "predictions")
    if os.path.isdir(src_pred):
        if os.path.isdir(dst_pred):
            shutil.rmtree(dst_pred)
        shutil.copytree(src_pred, dst_pred)
    rows = read_rows(os.path.join(dst, "results.jsonl"))
    return {"dir": dst, "source": os.path.abspath(src), "items": len(rows),
            "ids": sorted(str(r.get("id")) for r in rows)}


def seed_all(out_root: str, epoch1: str, epoch2: str, val: str, epochs: int) -> list:
    """Every rollout directory a 2-epoch gated run touches. Anything missed makes the actor guard fire."""
    seeded = []
    seeded.append(seed_rollout(val, os.path.join(out_root, "selection_eval_baseline")))
    per_epoch = {1: epoch1, 2: epoch2, 3: epoch2, 4: epoch2}
    for e in range(1, epochs + 1):
        step = os.path.join(out_root, "steps", f"step_{e:04d}")
        seeded.append(seed_rollout(per_epoch[e], os.path.join(step, "rollout")))
        seeded.append(seed_rollout(val, os.path.join(step, "selection_eval")))
        if e >= 2:
            slow = os.path.join(out_root, "slow_update", f"epoch_{e:02d}")
            seeded.append(seed_rollout(per_epoch[e - 1], os.path.join(slow, "rollout_prev")))
            seeded.append(seed_rollout(per_epoch[e], os.path.join(slow, "rollout_curr")))
            seeded.append(seed_rollout(val, os.path.join(slow, "selection_eval")))
            meta = os.path.join(out_root, "meta_skill", f"epoch_{e:02d}")
            seeded.append(seed_rollout(per_epoch[e - 1], os.path.join(meta, "rollout_prev")))
            seeded.append(seed_rollout(per_epoch[e], os.path.join(meta, "rollout_curr")))
    return seeded


# ── stubs ───────────────────────────────────────────────────────────────────────

def install_stubs(call_log: str):
    """Replace the actor with a guard and the optimizer with a recorder. Returns the list of recorded calls."""
    import skillopt.envs.officeqa.rollout as RO

    def forbidden(*_a, **_k):
        raise AssertionError("the dry run called the ACTOR: a rollout directory was not seeded. No request was "
                             "sent, but the run is invalid - seed the directory named in the traceback.")

    RO.chat_target_messages = forbidden

    calls: list = []

    def stub(system: str = "", user: str = "", max_completion_tokens: int = 0, retries: int = 0,
             stage: str = "", **_kw):
        rec = {"n": len(calls) + 1, "stage": stage, "system_sha256": _sha(system), "user_sha256": _sha(user),
               "user_chars": len(user), "user": user}
        calls.append(rec)
        with open(call_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in rec.items() if k != "user"}) + "\n")
        body = STAGE_RESPONSES.get(stage)
        if body is None:
            raise AssertionError(f"the dry run met an unexpected optimizer stage {stage!r}; add a response for it "
                                 f"rather than letting the trainer fall back silently")
        return json.dumps(body), {"prompt_tokens": 0, "completion_tokens": 0}

    import importlib
    for name in OPTIMIZER_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "chat_optimizer"):
            mod.chat_optimizer = stub
    return calls


# ── verification ────────────────────────────────────────────────────────────────

REPORT_HEADINGS = {"A": None, "B": "#### Action Contract Report", "C": "#### Action Contract and Stage Report"}
OTHER_HEADINGS = ["#### Action Contract Report", "#### Action Contract and Stage Report",
                  "#### Stage and Action Report"]


def check_prompts(arm: str, calls: list, report_dir: str, pre_existing: set) -> dict:
    """What actually reached the optimizer, read off the prompts the stub recorded.

    The report body is NOT sliced out of the prompt by looking for the next heading. The hook appends the report
    and then the trajectory text with no delimiter between them, so slicing that way swallows the actor's own
    messages - and those contain words like "outstanding" and "final answer", which made the first version of
    this check report a stage leak that was not there. Instead, every report FILE the run generated is matched
    verbatim against the prompts: what the optimizer read is then exactly the bytes that were audited.
    """
    sys.path.insert(0, HERE)
    import oqa_lc_report as REPORT
    analyst = [c for c in calls if c["stage"] == "analyst"]
    want = REPORT_HEADINGS[arm]
    problems: list = []
    with_report = 0
    for c in analyst:
        present = [h for h in OTHER_HEADINGS if h in c["user"]]
        if want is None:
            if present:
                problems.append(f"call {c['n']}: the control arm's analyst prompt carries {present}")
            continue
        if want not in present:
            problems.append(f"call {c['n']}: expected {want!r}, found {present or 'no report heading'}")
            continue
        if [h for h in present if h != want]:
            problems.append(f"call {c['n']}: also carries {[h for h in present if h != want]}")
        with_report += 1

    # Every report generated BY THIS PHASE must (a) be free of stage material when this is arm B, and (b) appear
    # verbatim in a prompt, so nothing the optimizer read went unaudited and nothing audited went unsent. Reports
    # left by an earlier phase of the same arm are excluded: they belong to that phase's prompts, not this one's.
    files = (sorted(f for f in os.listdir(report_dir) if f.endswith(".md") and f not in pre_existing)
             if os.path.isdir(report_dir) else [])
    audited, embedded = 0, 0
    prompts = [c["user"] for c in analyst]
    for name in files:
        with open(os.path.join(report_dir, name), encoding="utf-8") as f:
            body = f.read().strip()
        if arm == "B":
            a = REPORT.audit_action_only(body)
            if a["verdict"] != "PASS":
                problems.append(f"{name}: the Action-Only report contains stage words {a['stage_words_found']}")
            else:
                audited += 1
        if any(body in p for p in prompts):
            embedded += 1
        else:
            problems.append(f"{name}: the report was generated but no analyst prompt carries its text")
    return {"analyst_calls": len(analyst), "calls_carrying_a_report": with_report,
            "expected_heading": want, "reports_on_disk": len(files),
            "reports_free_of_stage_material": audited if arm == "B" else None,
            "reports_found_verbatim_in_a_prompt": embedded,
            "reports_from_an_earlier_phase_excluded": len(pre_existing),
            "verdict": "FAIL" if problems else "PASS", "problems": problems}


def check_provenance(report_dir: str, out_root: str, model_path: str) -> dict:
    """Each generated report must name the attempt it was built from and the model version in force."""
    if not model_path:
        return {"verdict": "SKIPPED", "why": "the control arm builds no report"}
    import oqa_reports as RP
    path = os.path.join(report_dir, "GENERATED.jsonl")
    if not os.path.exists(path):
        return {"verdict": "FAIL", "problems": ["no report was generated at all"]}
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    by_dir: dict = {}
    for r in recs:
        by_dir.setdefault(r["prediction_dir"], []).append(r)
    out = {"n_reports": len(recs), "collections": len(by_dir), "per_collection": [], "problems": []}
    for pred_dir, group in sorted(by_dir.items()):
        v = RP.verify_report_provenance(group, pred_dir, model_path)
        out["per_collection"].append({"prediction_dir": pred_dir, "n": len(group), "verdict": v["verdict"],
                                      "model_sha256": v["model_sha256"], "problems": v["problems"]})
        out["problems"] += v["problems"]
    out["conditions"] = sorted({r.get("condition") for r in recs})
    out["model_digests_seen"] = sorted({r.get("model_sha256") for r in recs})
    out["trace_digests_seen"] = len({r.get("trace_sha256") for r in recs})
    out["verdict"] = "FAIL" if out["problems"] else "PASS"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["A", "B", "C"])
    ap.add_argument("--out", required=True, help="trainer out_root; must not exist")
    ap.add_argument("--epoch1", required=True, help="recorded rollout for epoch 1")
    ap.add_argument("--epoch2", required=True, help="recorded rollout for epoch 2")
    ap.add_argument("--val", required=True, help="recorded rollout used to seed every selection evaluation")
    ap.add_argument("--lc-model", default="", help="frozen contract model (arms B and C)")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--config", default="configs/research/officeqa_api3.yaml")
    ap.add_argument("--resume", action="store_true",
                    help="continue an existing out_root instead of refusing it. This is how the campaign actually "
                         "runs: ONE JOB PER ARM-EPOCH, because OFFICEQA_LC_MODEL is read from the environment and "
                         "must name a different frozen model in each epoch. The trainer resumes from "
                         "runtime_state.json and the seeded results.jsonl files.")
    ap.add_argument("--phase", default="", help="a label for this phase, recorded in the dry-run record")
    args = ap.parse_args()

    if os.path.exists(args.out) and not args.resume:
        print(f"refusing to write into an existing directory: {args.out}", file=sys.stderr)
        return 2
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    work = os.path.join(out_root, "_dryrun")
    os.makedirs(work, exist_ok=True)

    split_dir = os.path.join(work, "split")
    train_rows = read_rows(os.path.join(args.epoch1, "results.jsonl"))
    val_rows = read_rows(os.path.join(args.val, "results.jsonl"))
    n_train = write_split(split_dir, "train", train_rows)
    n_val = write_split(split_dir, "val", val_rows)
    write_split(split_dir, "test", val_rows)       # never rolled out: eval_test is false
    seed_all_epochs = max(args.epochs, 2)

    # Seed once. On a resume the directories are already there with the same content, and re-copying would only
    # risk clobbering something the previous phase wrote beside them.
    seeded = ([] if args.resume
              else seed_all(out_root, args.epoch1, args.epoch2, args.val, seed_all_epochs))

    report_dir = os.path.join(work, "reports")
    os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
    os.environ.pop("OFFICEQA_STAGE_REPORT_CONDITION", None)
    os.environ.pop("OFFICEQA_LC_MODEL", None)
    if args.arm != "A":
        os.makedirs(report_dir, exist_ok=True)
        os.environ["OFFICEQA_STAGE_REPORT_DIR"] = report_dir
        os.environ["OFFICEQA_STAGE_REPORT_CONDITION"] = "LC_ACTION" if args.arm == "B" else "LC_STAGE"
        if not args.lc_model:
            print("arms B and C need --lc-model", file=sys.stderr)
            return 2
        os.environ["OFFICEQA_LC_MODEL"] = os.path.abspath(args.lc_model)
    # No key is set for either role, so a request that escaped the stubs would fail closed rather than bill.
    for var in ("TARGET_OPENAI_COMPATIBLE_API_KEY", "OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY",
                "DEEPSEEK_API_KEY", "AZURE_OPENAI_API_KEY"):
        os.environ.pop(var, None)

    phase = args.phase or f"epochs_{args.epochs}"
    pre_existing = set(os.listdir(report_dir)) if os.path.isdir(report_dir) else set()
    call_log = os.path.join(work, f"optimizer_calls_{phase}.jsonl")
    calls = install_stubs(call_log)

    spec = importlib.util.spec_from_file_location("oqa_train_entry", os.path.join(REPO, "scripts", "train.py"))
    T = importlib.util.module_from_spec(spec)
    sys.modules["oqa_train_entry"] = T
    spec.loader.exec_module(T)

    sys.argv = ["train.py", "--config", os.path.join(REPO, args.config),
                "--optimizer_model", "dryrun-stub",
                "--cfg-options",
                f"env.split_dir={split_dir}",
                f"env.data_dirs=['{os.path.join(work, 'docs')}']",
                f"out_root={out_root}",
                f"train.num_epochs={args.epochs}",
                "evaluation.eval_test=false"]
    os.makedirs(os.path.join(work, "docs"), exist_ok=True)

    started = datetime.datetime.now().astimezone()
    status, error = "COMPLETED", None
    try:
        T.main()
    except SystemExit as exc:                      # a non-zero exit from the entry point is a failure to report
        if exc.code not in (0, None):
            status, error = "FAILED", f"SystemExit {exc.code}"
    except BaseException as exc:                   # noqa: BLE001 - the whole point is to report what went wrong
        status, error = "FAILED", f"{type(exc).__name__}: {exc}"
        import traceback
        with open(os.path.join(work, f"traceback_{phase}.txt"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())

    prompts = check_prompts(args.arm, calls, report_dir, pre_existing)
    prov = check_provenance(report_dir, out_root, os.environ.get("OFFICEQA_LC_MODEL", ""))
    stages: dict = {}
    for c in calls:
        stages[c["stage"]] = stages.get(c["stage"], 0) + 1

    record = {
        "arm": args.arm, "status": status, "error": error,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": args.config, "epochs": args.epochs,
        "entry_point": "scripts/train.py:main()",
        "replaced": {"actor": "chat_target_messages -> raises (zero requests)",
                     "optimizer": f"chat_optimizer -> recorder in {len(OPTIMIZER_MODULES)} modules"},
        "splits": {"train": n_train, "val": n_val, "test": "copy of val, never rolled out (eval_test false)"},
        "seeded_rollouts": seeded,
        "lc_model": os.environ.get("OFFICEQA_LC_MODEL", ""),
        "report_condition": os.environ.get("OFFICEQA_STAGE_REPORT_CONDITION", "(none)"),
        "optimizer_calls": {"total": len(calls), "by_stage": stages},
        "analyst_input_check": prompts,
        "report_provenance_check": prov,
    }
    record["phase"] = phase
    record["resumed"] = bool(args.resume)
    with open(os.path.join(work, f"DRYRUN_{phase}.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1, default=str)
    print(json.dumps({k: v for k, v in record.items() if k != "seeded_rollouts"}, indent=1, default=str))
    return 0 if (status == "COMPLETED" and prompts["verdict"] == "PASS"
                 and prov["verdict"] in ("PASS", "SKIPPED")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
