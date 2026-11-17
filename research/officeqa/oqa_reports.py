"""Generate the stage-and-action reports that arm B adds to the optimizer's prompt.

Arm A (original SkillOpt) and arm B (SkillOpt + stage and action feedback) see the SAME traces. The only
difference is that arm B's analyst prompt carries one of these reports per failed item, appended by the opt-in
hook in skillopt/gradient/reflect.py (`OFFICEQA_STAGE_REPORT_DIR`).

The reports are produced here, outside SkillOpt, so that:
  * they are deterministic - no model call, so the comparison is not confounded by a second sampled model;
  * they are auditable - the file handed to the optimizer is on disk and can be read;
  * they cannot see the answer key - the packet is built from the recorded user prompt and conversation only, and
    `oqa_trace.audit_no_gold` is run on every one of them.

Writes <out>/<task id>.md for each failed item, plus a manifest with the audit verdicts and the digest of each
report, so the exact bytes the optimizer saw can be reconstructed later.

usage: python3 oqa_reports.py <rollout out_root> --out <dir> [--condition C] [--all-items]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_check as CHK  # noqa: E402
import oqa_trace as TR  # noqa: E402


class AnswerKeyLeak(RuntimeError):
    """The packet a report would be built from carries evaluator-only information. Never continue past this."""


class StageLeak(RuntimeError):
    """An Action-Only report carries stage material, so arm B would not be the arm it claims to be."""


# The report conditions of the THREE-ARM comparison. "LC" is kept as an alias of LC_STAGE so the two-arm
# artefacts and tests that name it keep working.
LC_CONDITION = "LC"
LC_ACTION = "LC_ACTION"        # arm B, Action Contracts Only
LC_STAGE = "LC_STAGE"          # arm C, Action Contracts + Stages
LC_CONDITIONS = (LC_CONDITION, LC_ACTION, LC_STAGE)
# The heading the analyst prompt puts the report under. Arm B's must not say "stage": the heading is part of what
# the optimizer reads.
LC_HEADINGS = {LC_ACTION: "Action Contract Report",
               LC_STAGE: "Action Contract and Stage Report",
               LC_CONDITION: "Stage and Action Report"}


def lc_model_digest(model_path: str) -> str:
    """The frozen learned-contract model's own digest, verified against its content before it is used.

    `oqa_lc_ir.load_frozen` refuses a file whose content does not match the digest it carries, so a report keyed
    by this value cannot be attributed to a model version that was edited after freezing.
    """
    IR = _lc_modules()[1]
    return str(IR.load_frozen(model_path).get("sha256") or "")


_LC_NAMES = ("oqa_lc_ir", "oqa_lc_chain", "oqa_lc_report", "oqa_lc_replay", "oqa_obligations")
_LC_LOCK = threading.Lock()
_LC_CACHE: tuple | None = None


def _lc_modules():
    """Import the learned-contract modules by path, ONCE per process and under a lock.

    WHY THE LOCK AND THE CACHE. This function used to execute all five modules on EVERY call, and the analyst
    minibatches run in a ThreadPoolExecutor (`gradient.analyst_workers`), so two threads could be inside it at
    once. The old body did

        sys.modules.setdefault(name, mod)   # registers an EMPTY module
        spec.loader.exec_module(mod)        # then fills it in

    which leaves a half-built module visible in `sys.modules` for the duration of the exec. Thread 2, reaching
    `oqa_lc_report`, would run its module-level `import oqa_lc_chain as CHAIN`, get thread 1's half-built
    `oqa_lc_chain`, and die on `_ORDER = {CHAIN.VIOLATED: ...}` because line 45 had not run yet.

    THAT IS NOT HYPOTHETICAL: it killed arm C's epoch-1 job 66272740 after 16 of its 24 reports had been
    written, with `AttributeError: module 'oqa_lc_chain' has no attribute 'VIOLATED'`, while arm B's job - same
    code, same two threads - got away with it. An intermittent failure of exactly the kind that is worst to
    diagnose from a result rather than a crash.

    A module already in `sys.modules` is now REUSED rather than executed again. Under the lock nothing else can
    be mid-exec, so anything present there is complete - and reusing it also removes a second, quieter hazard:
    `oqa_lc_report` imports `oqa_lc_replay` and `oqa_obligations`, which come after it in the load order, so the
    old loop executed those twice and returned a DIFFERENT instance from the one `oqa_lc_report` holds. Two
    instances of a module mean two separate caches, and this project has already been bitten once by a stale
    cache in exactly these modules (`Evidence` keyed on the item id rather than the attempt).
    """
    global _LC_CACHE
    cached = _LC_CACHE
    if cached is not None:
        return cached
    with _LC_LOCK:
        if _LC_CACHE is None:
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            if here not in sys.path:
                sys.path.insert(0, here)
            loaded: dict = {}
            for name in _LC_NAMES:
                existing = sys.modules.get(name)
                if existing is not None:
                    loaded[name] = existing
                    continue
                spec = importlib.util.spec_from_file_location(name, os.path.join(here, f"{name}.py"))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                try:
                    spec.loader.exec_module(mod)
                except BaseException:
                    # Never leave a half-built module behind for the next caller to trip over.
                    sys.modules.pop(name, None)
                    raise
                loaded[name] = sys.modules.get(name, mod)
            _LC_CACHE = (None, loaded["oqa_lc_ir"], loaded["oqa_lc_chain"], loaded["oqa_lc_report"],
                         loaded["oqa_lc_replay"], loaded["oqa_obligations"])
    return _LC_CACHE


def build_one_lc(pred_root: str, item_id: str, question: str, results_path: str,
                 model_path: str, condition: str = LC_CONDITION) -> tuple[str, dict]:
    """The LEARNED-CONTRACT report for one item: obligations with a resolution, under a frozen model.

    This is the treatment arm's report for the API comparison. It differs from `build_one` in what it says, not in
    what it may read: the same packet, the same answer-key audit, no skill and no held-out data. Two things are
    recorded that the old report had no notion of, and both are what makes a report attributable:
      * `trace_sha256`  the conversation and user prompt the report was derived from;
      * `model_sha256`  the frozen model whose assumptions licensed its bindings.
    A report is valid only for that (attempt, model version) pair, and `verify_report_provenance` checks it.
    """
    _, IR, CHAIN, REPORT, REPLAY, OB = _lc_modules()
    if condition not in LC_CONDITIONS:
        raise ValueError(f"unknown learned-contract condition {condition!r}; expected one of {LC_CONDITIONS}")
    packet = TR.build_packet(pred_root, item_id, question=question)
    if not packet.get("steps"):
        raise ValueError(f"{item_id}: no conversation events were recorded at {pred_root}")
    audit = TR.audit_no_gold(packet, results_path)
    if audit["verdict"] != "PASS":
        raise AnswerKeyLeak(f"{item_id}: {audit.get('problems')}")
    model = IR.load_frozen(model_path)
    # The contracts are scoped by the kind of answer the question asks for, which is read from the question text
    # alone (`oqa_obligations.derive`). Without it a scoped contract would never apply and arm B's report would be
    # silently empty.
    packet["answer_kind"] = (OB.derive(packet.get("question") or "")["answer_kind"]["kind"]
                             if packet.get("question") else None)
    replay = REPLAY.replay_attempt(packet, model)
    chain = CHAIN.build(packet, model)
    if condition == LC_ACTION:
        text = REPORT.render_action(replay, model)
        action_audit = REPORT.audit_action_only(text)
        if action_audit["verdict"] != "PASS":
            # Arm B carrying stage material would make the three-arm comparison measure nothing. Refuse loudly.
            raise StageLeak(f"{item_id}: the Action-Only report contains stage vocabulary "
                            f"{action_audit['stage_words_found']}")
    else:
        text = REPORT.render(chain, model, replay=replay if condition == LC_STAGE else None)
    ledger = CHAIN.summary(chain["ledger"])
    fc = REPLAY.score(replay["attempts"])
    record = {
        "id": item_id,
        "condition": condition,
        "heading": LC_HEADINGS[condition],
        "action_forecasts": fc["forecasts"],
        "tool_steps": fc["steps"],
        "report_chars": len(text),
        "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "trace_sha256": trace_digest(pred_root, item_id),
        "model_sha256": str(model.get("sha256") or ""),
        "model_version": model.get("version"),
        "model_assumptions": list(model.get("assumptions") or []),
        "boundary_audit": audit["verdict"],
        "boundary_problems": audit.get("problems", []),
        "obligations": ledger if condition != LC_ACTION else None,
        "n_distinct_diagnoses": len(REPORT.group(chain["ledger"])) if condition != LC_ACTION else 0,
    }
    return text, record


def trace_digest(pred_root: str, item_id: str) -> str:
    """Digest of the exact attempt a report must describe: its conversation plus the prompt the actor was given."""
    h = hashlib.sha256()
    for name in ("conversation.json", "target_user_prompt.txt"):
        h.update(b"\x00" + name.encode() + b"\x00")
        try:
            with open(os.path.join(pred_root, item_id, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def verify_report_provenance(records: list, pred_root: str, model_path: str) -> dict:
    """Every report must come from the attempt it claims and from the frozen model version in force.

    Returns {"verdict", "checked", "problems"}. This is the check the stale-report failure of the A40 run would
    have caught: there, eight epoch-2 failures were handed reports built from their epoch-1 trajectories.
    """
    IR = _lc_modules()[1]
    want_model = str(IR.load_frozen(model_path).get("sha256") or "")
    problems = []
    for rec in records:
        tid = str(rec.get("id"))
        actual = trace_digest(pred_root, tid)
        if rec.get("trace_sha256") != actual:
            problems.append(f"{tid}: report was built from trace {str(rec.get('trace_sha256'))[:12]} but the "
                            f"attempt on disk is {actual[:12]}")
        if rec.get("model_sha256") != want_model:
            problems.append(f"{tid}: report was built under model {str(rec.get('model_sha256'))[:12]} but the "
                            f"frozen model in force is {want_model[:12]}")
    return {"verdict": "FAIL" if problems else "PASS", "checked": len(records), "problems": problems,
            "model_sha256": want_model}


def build_one(pred_root: str, item_id: str, question: str, results_path: str,
              condition: str = "C") -> tuple[str, dict]:
    """Build one item's report text plus its record. Raises AnswerKeyLeak if the packet fails the audit.

    Shared by this script's batch mode and by the opt-in hook in skillopt/gradient/reflect.py, so arm B's report
    is produced by exactly one code path whether it is written ahead of a run or generated during one.
    """
    packet = TR.build_packet(pred_root, item_id, question=question)
    if not packet.get("steps"):
        # A packet with no conversation events cannot support a report. Raising here means the caller records the
        # item as a problem instead of handing the optimizer an empty block and leaving no trace of why.
        raise ValueError(f"{item_id}: no conversation events were recorded at {pred_root}")
    audit = TR.audit_no_gold(packet, results_path)
    if audit["verdict"] != "PASS":
        raise AnswerKeyLeak(f"{item_id}: {audit.get('problems')}")
    result = CHK.report(packet, condition)
    base = CHK.report(packet, "P")
    if condition in ("S", "C"):
        # The containment invariant: a richer condition may never drop what the plain view already said.
        CHK.structural_check(base, result, f"{item_id}: {condition} over P")
    text = CHK.render(result, packet)
    record = {
        "id": item_id,
        "report_chars": len(text),
        "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "boundary_audit": audit["verdict"],
        "boundary_problems": audit.get("problems", []),
        "findings": [{"cat": f["cat"], "kind": f["kind"], "layer": f["layer"], "problem": f["problem"],
                      "first_step": f["first"]} for f in result["findings"]],
        "n_advice": sum(1 for f in result["findings"] if f["kind"] == "advice"),
        "n_warning": sum(1 for f in result["findings"] if f["kind"] == "warning"),
    }
    return text, record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_root", help="rollout directory containing results.jsonl and predictions/")
    ap.add_argument("--out", required=True, help="directory to write <id>.md reports into")
    ap.add_argument("--condition", default="C", choices=["P", "S", "C"],
                    help="C = stage handoffs + action contracts (arm B); S = stages only; P = plain (control)")
    ap.add_argument("--all-items", action="store_true",
                    help="also report on items the scorer accepted (default: failures only, which is what the "
                         "analyst minibatch receives)")
    args = ap.parse_args()

    results_path = os.path.join(args.out_root, "results.jsonl")
    rows = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    os.makedirs(args.out, exist_ok=True)
    pred_root = os.path.join(args.out_root, "predictions")
    manifest = {"out_root": os.path.abspath(args.out_root), "condition": args.condition,
                "report_dir": os.path.abspath(args.out), "items": [], "problems": []}

    for row in rows:
        item_id = str(row.get("id"))
        failed = not float(row.get("hard") or 0)
        if not failed and not args.all_items:
            continue
        try:
            text, record = build_one(pred_root, item_id, row.get("question", ""), results_path, args.condition)
            path = os.path.join(args.out, f"{item_id}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            record["scored_correct"] = not failed
            record["report_file"] = os.path.basename(path)
            manifest["items"].append(record)
        except AnswerKeyLeak as exc:
            manifest["items"].append({"id": item_id, "boundary_audit": "FAIL", "boundary_problems": [str(exc)],
                                      "report_chars": 0, "n_advice": 0, "n_warning": 0, "findings": []})
        except Exception as exc:  # noqa: BLE001
            manifest["problems"].append({"id": item_id, "error": f"{type(exc).__name__}: {exc}"})

    manifest["totals"] = {
        "reports": len(manifest["items"]),
        "boundary_audit_pass": sum(1 for i in manifest["items"] if i["boundary_audit"] == "PASS"),
        "boundary_audit_not_pass": sum(1 for i in manifest["items"] if i["boundary_audit"] != "PASS"),
        "items_with_advice": sum(1 for i in manifest["items"] if i["n_advice"]),
        "total_report_chars": sum(i["report_chars"] for i in manifest["items"]),
    }
    with open(os.path.join(args.out, "REPORT_MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps(manifest["totals"], indent=2))
    if manifest["problems"]:
        print(f"{len(manifest['problems'])} item(s) failed; see REPORT_MANIFEST.json", file=sys.stderr)
        return 1
    # A boundary failure must stop the pipeline: the whole point is that arm B adds reasoning, not the answer key.
    if manifest["totals"]["boundary_audit_not_pass"]:
        print("REFUSED: at least one report's packet failed the answer-key audit", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
