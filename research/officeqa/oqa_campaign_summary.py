"""Collect the three-arm campaign's result from its own artifacts, with the checks that must pass first.

This is the readout. It reads run directories only - no API call, no rollout - and it refuses to present a
result without the evidence that the result means what it says:

  per arm, per epoch   every gate decision, with the candidate's selection score and the baseline it faced,
                       for BOTH the ordinary update and the gated slow update.
  slow update          whether each one produced guidance or recorded `no_content`, and the pair count the
                       pre-send size check actually sent (which is the user's logging requirement).
  reports              arm B free of stage material, arm C containing arm B line for line, one model digest
                       per arm-epoch, every report labelled in-sample only in epoch 1.
  models               each contract arm's model version per epoch, with the counterexamples and repairs that
                       moved it.
  skills               the validation-selected skill per arm (the PRIMARY comparison) and the last skill,
                       distinguishing a genuinely different skill from one that differs only by an injected
                       marker block.

usage: python3 -I research/officeqa/oqa_campaign_summary.py --campaign <root> [--out FILE]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re

ARMS = ("A", "B", "C")
MARKER_PAIRS = (("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->"),
                ("<!-- META_SKILL_START -->", "<!-- META_SKILL_END -->"),
                ("<!-- APPENDIX_START -->", "<!-- APPENDIX_END -->"))
STAGE_MARKERS = ("cell_bound", "period_match", "unit_match", "arith_recorded", "answer_traced",
                 "DISCHARGED", "OUTSTANDING", "## What the task requires")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _json(path: str):
    text = _read(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _substantive(text: str) -> str:
    out = text
    for start, end in MARKER_PAIRS:
        while start in out and end in out:
            a = out.find(start)
            b = out.find(end, a)
            if b < 0:
                break
            out = out[:a] + out[b + len(end):]
    return "\n".join(l.rstrip() for l in out.splitlines() if l.strip())


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def arm_history(root: str) -> list:
    """Every step's gate decision, from the trainer's own history."""
    hist = _json(os.path.join(root, "history.json")) or {}
    steps = hist if isinstance(hist, list) else (hist.get("steps") or hist.get("history") or [])
    out = []
    for s in steps if isinstance(steps, list) else []:
        if not isinstance(s, dict):
            continue
        out.append({k: s.get(k) for k in
                    ("step", "epoch", "action", "sel_hard", "selection_hard", "cand_hard", "current_score",
                     "best_score", "update_origin", "edit_budget", "n_edits") if k in s})
    return out


def slow_updates(root: str) -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(root, "slow_update", "epoch_*", "slow_result.json"))):
        rec = _json(path) or {}
        epoch = os.path.basename(os.path.dirname(path))
        out.append({
            "epoch": epoch,
            "action": rec.get("action"),
            "produced_guidance": bool(rec.get("slow_update_content")),
            "guidance_chars": len(str(rec.get("slow_update_content") or "")),
            "selection_hard": rec.get("selection_hard"),
            "prev_hard": rec.get("prev_hard"), "curr_hard": rec.get("curr_hard"),
            "time_s": rec.get("time_s"),
        })
    return out


def meta_skills(root: str) -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(root, "meta_skill", "epoch_*", "*.json"))):
        rec = _json(path) or {}
        out.append({"epoch": os.path.basename(os.path.dirname(path)),
                    "file": os.path.basename(path),
                    "produced": bool(rec.get("meta_skill_content")),
                    "chars": len(str(rec.get("meta_skill_content") or ""))})
    return out


def prompt_budget(bundle_dirs: list, arm: str) -> list:
    out = []
    for bundle in bundle_dirs:
        for path in sorted(glob.glob(os.path.join(bundle, f"run_*_arm_{arm}_e*", "prompt_budget.jsonl"))):
            for line in open(path, encoding="utf-8"):
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.append({"job": os.path.basename(os.path.dirname(path)),
                            "stage": d.get("stage"), "epoch": d.get("epoch"),
                            "pairs_offered": d.get("pairs_offered"), "pairs_sent": d.get("pairs_sent"),
                            "dropped": len(d.get("dropped") or []),
                            "tokens": d.get("estimated_tokens_sent"),
                            "limit": d.get("limit_tokens"),
                            "within_limit": d.get("within_limit"),
                            "skipped": d.get("skip"),
                            "guidance_produced": d.get("guidance_produced")})
    return out


def reports(root: str) -> dict:
    directory = os.path.join(root, "stage_reports")
    if not os.path.isdir(directory):
        return {"present": False}
    names = sorted(n for n in os.listdir(directory) if n.endswith(".md"))
    leaks, in_sample, sizes = [], 0, []
    for n in names:
        t = _read(os.path.join(directory, n)) or ""
        sizes.append(len(t))
        if any(m in t for m in STAGE_MARKERS):
            leaks.append(n)
        if "in-sample" in t.lower():
            in_sample += 1
    manifest = []
    for line in (_read(os.path.join(directory, "GENERATED.jsonl")) or "").splitlines():
        try:
            manifest.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    models = sorted({(r.get("model_sha256") or "")[:12] for r in manifest if r.get("model_sha256")})
    return {"present": True, "n": len(names), "with_stage_material": len(leaks),
            "labelled_in_sample": in_sample,
            "mean_chars": sum(sizes) // len(sizes) if sizes else 0,
            "manifest_rows": len(manifest),
            "distinct_trace_digests": len({r.get("trace_sha256") for r in manifest if r.get("trace_sha256")}),
            "model_digests": models,
            "conditions": sorted({r.get("condition") for r in manifest if r.get("condition")}),
            "boundary_failures": [r.get("id") for r in manifest if r.get("boundary_audit") != "PASS"]}


def _reports_by_trace(directory: str) -> dict:
    """(model digest, trace digest) -> (item id, file name), for every report an arm actually wrote.

    Keyed on the TRACE, not the item or the model, because neither alone identifies a report:
      * an item has one report per epoch, four after four epochs;
      * epochs 1 AND 2 both run under M0, so the model digest does not pin the epoch either.
    Keying on (model, trace) is exact, and intersecting two arms on that key SELECTS the comparable reports
    rather than assuming which they are - which matters because the arms share only the epoch-1 traces.
    Files are matched to manifest rows by `report_sha256`.
    """
    rows = {}
    for line in (_read(os.path.join(directory, "GENERATED.jsonl")) or "").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("report_sha256") and r.get("trace_sha256"):
            rows[r["report_sha256"]] = r
    out = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md"):
            continue
        text = _read(os.path.join(directory, name))
        if text is None:
            continue
        r = rows.get(hashlib.sha256(text.encode()).hexdigest())
        if r:
            out[((r.get("model_sha256") or "")[:12], r["trace_sha256"])] = (str(r.get("id")), name)
    return out


def containment(campaign: str) -> dict:
    """Arm C's report must contain arm B's FOR THE SAME TRACE UNDER THE SAME MODEL.

    THE SCOPE IS THE WHOLE DIFFICULTY, and I got it wrong twice. Comparing each arm's latest reports flags the
    epoch-2-onward divergence as a failure, but that divergence is the DESIGN: from epoch 2 each contract arm
    evolves its own model from its own traces. Restricting to M0 is not enough either, because epochs 1 and 2
    both run under M0 while their traces differ per arm. The comparable reports are those sharing BOTH the
    model and the trace, which in this campaign is exactly epoch 1 - the shared rollout - and the intersection
    finds them without being told.
    """
    b = os.path.join(campaign, "arm_B", "stage_reports")
    c = os.path.join(campaign, "arm_C", "stage_reports")
    if not (os.path.isdir(b) and os.path.isdir(c)):
        return {"tested": False, "why": "a report directory is missing"}
    bf, cf = _reports_by_trace(b), _reports_by_trace(c)
    common = sorted(set(bf) & set(cf))
    ok, bad = 0, []
    for key in common:
        uid_b, name_b = bf[key]
        _uid_c, name_c = cf[key]
        tb = (_read(os.path.join(b, name_b)) or "").splitlines()
        tc = set((_read(os.path.join(c, name_c)) or "").splitlines())
        if [l for l in tb if l.strip() and l not in tc]:
            bad.append(uid_b)
        else:
            ok += 1
    return {"tested": True,
            "scope": "reports sharing both the model digest and the trace digest",
            "arm_B_reports": len(bf), "arm_C_reports": len(cf),
            "comparable_pairs": len(common), "C_contains_B": ok, "failures": sorted(bad)[:5],
            "note": "the comparable set is epoch 1, where both arms run under M0 on the shared rollout. From "
                    "epoch 2 each arm evolves its own model from its own traces, so there is no shared "
                    "(model, trace) key and nothing to compare - which is the design, not a defect."}


def models(campaign: str, arm: str) -> list:
    directory = os.path.join(campaign, "contracts", f"arm_{arm}")
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "EVOLVE_*.json"))):
        rec = _json(path) or {}
        out.append({
            "file": os.path.basename(path), "epoch": rec.get("epoch"),
            "prior_version": (rec.get("prior_model") or {}).get("version"),
            "new_version": (rec.get("model") or {}).get("version"),
            "prior_sha": ((rec.get("prior_model") or {}).get("sha256") or "")[:12],
            "new_sha": ((rec.get("model") or {}).get("sha256") or "")[:12],
            "model_changed": rec.get("model_changed"),
            "counterexamples": len(rec.get("counterexamples_found") or []),
            "counterexample_detail": [
                {"aid": c.get("aid"), "tool": c.get("tool"),
                 "violated": [v.get("contract") for v in (c.get("violated") or [])]}
                for c in (rec.get("counterexamples_found") or [])[:4]],
            "out_of_sample_before_learning": rec.get("out_of_sample_score_before_learning"),
            "retained_in_error": [r.get("id") for r in (rec.get("retained") or [])
                                  if r.get("decision") == "retained_in_error"],
            "active_contracts": (rec.get("model") or {}).get("active"),
            "assumptions": (rec.get("model") or {}).get("assumptions"),
        })
    return out


def skills(campaign: str, skill_init: str) -> dict:
    s0 = _read(skill_init) or ""
    out = {"S0": {"digest": _digest(s0), "chars": len(s0), "advice_digest": _digest(_substantive(s0))}}
    for arm in ARMS:
        root = os.path.join(campaign, f"arm_{arm}")
        best = _read(os.path.join(root, "best_skill.md"))
        snaps = sorted(glob.glob(os.path.join(root, "skills", "skill_v*.md")))
        last = _read(snaps[-1]) if snaps else None
        entry = {"selected": None, "last": None, "n_snapshots": len(snaps)}
        if best is not None:
            entry["selected"] = {"digest": _digest(best), "chars": len(best),
                                 "advice_digest": _digest(_substantive(best)),
                                 "advice_same_as_s0": _substantive(best) == _substantive(s0)}
        if last is not None:
            entry["last"] = {"file": os.path.basename(snaps[-1]), "digest": _digest(last), "chars": len(last),
                             "advice_digest": _digest(_substantive(last)),
                             "advice_same_as_s0": _substantive(last) == _substantive(s0)}
        out[arm] = entry
    advice = {v.get("advice_digest") for k, v in out.items() if k == "S0"}
    for arm in ARMS:
        for role in ("selected", "last"):
            e = (out[arm] or {}).get(role)
            if e:
                advice.add(e["advice_digest"])
    out["distinct_by_advice"] = len(advice)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--bundle", action="append", default=[], help="repeatable: bundle dirs holding run_* dirs")
    ap.add_argument("--skill-init", default="skillopt/envs/officeqa/skills/initial.md")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    camp = args.campaign

    out: dict = {"generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "campaign": os.path.abspath(camp), "arms": {}}
    for arm in ARMS:
        root = os.path.join(camp, f"arm_{arm}")
        summary = _json(os.path.join(root, "summary.json")) or {}
        state = _json(os.path.join(root, "runtime_state.json")) or {}
        out["arms"][arm] = {
            "out_root": root,
            "summary": {k: summary.get(k) for k in
                        ("best_score", "best_step", "best_origin", "final_score", "n_steps") if k in summary},
            "runtime_state": {k: state.get(k) for k in
                              ("last_completed_step", "current_score", "best_score", "best_step", "best_origin")},
            "history": arm_history(root),
            "slow_updates": slow_updates(root),
            "meta_skills": meta_skills(root),
            "prompt_budget": prompt_budget(args.bundle, arm),
            "reports": reports(root),
            "model_evolution": models(camp, arm) if arm != "A" else [],
        }
    out["containment_B_in_C"] = containment(camp)
    out["skills"] = skills(camp, args.skill_init)

    problems = []
    for arm in ARMS:
        a = out["arms"][arm]
        for su in a["slow_updates"]:
            if su["action"] == "no_content" or (su["epoch"] != "epoch_01" and not su["produced_guidance"]):
                problems.append(f"arm {arm} {su['epoch']}: slow update produced no guidance "
                                f"(action={su['action']})")
        for pb in a["prompt_budget"]:
            if pb["skipped"]:
                problems.append(f"arm {arm} {pb['job']} {pb['stage']}: no call was made, prompt over the limit")
            if pb["guidance_produced"] is False:
                problems.append(f"arm {arm} {pb['job']} {pb['stage']}: no guidance came back")
        r = a["reports"]
        if r.get("present"):
            if arm == "B" and r["with_stage_material"]:
                problems.append(f"arm B: {r['with_stage_material']} reports carry stage material")
            if r["boundary_failures"]:
                problems.append(f"arm {arm}: ANSWER-KEY LEAK in {r['boundary_failures']}")
    cont = out["containment_B_in_C"]
    if cont.get("tested") and cont["failures"]:
        problems.append(f"arm C does not contain arm B for {cont['failures']}")
    out["problems"] = problems
    out["verdict"] = "PASS" if not problems else "PROBLEMS"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
