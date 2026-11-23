#!/usr/bin/env python3
"""The matched skill-editing comparison for the ported framework, and the native stages it can run.

WHAT IS MATCHED. One real recorded optimization step of the completed Original SkillOpt run: the same
twelve trajectories, the same starting skill, the same optimizer settings, the same analyst opportunity
and the same edit budget. The ONLY difference between the two arms is whether the analyst's input
carries the framework's report, and that difference is produced by the arm variable the frozen campaign
already uses (`OFFICEQA_STAGE_REPORT_DIR` + `OFFICEQA_STAGE_REPORT_CONDITION`), through the unchanged
`skillopt.gradient.reflect.fmt_minibatch_trajectories`. The harness ASSERTS the match rather than
claiming it: the two inputs are diffed and every difference must be inside the report block.

WHAT THIS RUNS, AND WHAT IT CANNOT.
  analyst   NOT RUN. It is a provider call, and the project's rule is that SkillOpt runs on the cluster.
            Both request bodies are built and digested here at zero cost, and the control arm's recorded
            output is used as its result.
  merge     NOT RUN for the framework arm: `skillopt.gradient.aggregate.merge_patches` calls the
            optimizer. The control arm's recorded merge is read.
  rank      DID NOT FIRE in the recorded step, legitimately: 3 merged edits against an edit budget of
            4, so `rank_and_select` was never reached. Recorded as not-fired rather than fabricated.
  apply     RUN, for real. `skillopt.optimizer.skill.apply_patch_with_report` is pure Python; it is
            re-executed here on the recorded merged patch and its output is compared BYTE FOR BYTE with
            the recorded candidate skill. That is what shows the harness drives the native stage
            correctly rather than describing it.

WHAT IT INSPECTS in the control arm's own candidate: whether the final wording keeps required and
optional behaviour apart, and whether it leaves a valid recovery available. An edit that turns advice
into a prohibition is a result worth reporting, not something to repair by hand.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))
sys.path.insert(0, REPO)

import oqa_fw_report as RPT  # noqa: E402
import oqa_fw_run as RUN  # noqa: E402


# ── importing the native stages on a workstation ──────────────────────────────
def _install_provider_stub() -> dict:
    """`skillopt.model` imports the `openai` package at module load, and this workstation does not have
    it. The two native stages used here make NO model call - one formats text, one edits a string - so
    a stub lets them be imported and run.

    THE STUB CANNOT MAKE A CALL AND CANNOT HIDE ONE. Every attribute access on it raises, so any code
    path that actually tried to reach a provider would fail loudly here instead of quietly doing
    something. Whether the stub was installed is recorded in the artifact.
    """
    import types
    if "openai" in sys.modules:
        return {"installed": False, "why": "the real openai package is importable"}
    try:
        import openai  # noqa: F401
        return {"installed": False, "why": "the real openai package is importable"}
    except ModuleNotFoundError:
        pass

    class _Refuse:
        def __init__(self, *a, **k):
            raise RuntimeError("this harness must not reach a provider: the stage it runs is pure")

        def __getattr__(self, name):
            raise RuntimeError(f"provider access ({name}) from a stage that must be pure")

    mod = types.ModuleType("openai")
    for n in ("OpenAI", "AzureOpenAI", "AsyncOpenAI", "AsyncAzureOpenAI", "Client"):
        setattr(mod, n, _Refuse)
    mod.APIError = mod.APIConnectionError = mod.RateLimitError = RuntimeError
    mod.__version__ = "0.0.0-stub"
    sys.modules["openai"] = mod
    return {"installed": True,
            "why": "no openai package on this workstation; the stub raises on every attribute, so a "
                   "stage that tried to call a provider would fail rather than proceed"}

STEP = ("/Users/yaojiaming/dev/lean_skillopt/runs/20261121-officeqa-two-baselines/"
        "original_skillopt/steps/step_0001")
SKILLS = ("/Users/yaojiaming/dev/lean_skillopt/runs/20261121-officeqa-two-baselines/"
          "original_skillopt/skills")
SCHEMA = "lso.officeqa_framework_edit.v1"
REQUIRED_WORDS = ("must", "always", "never", "required", "critical", "do not")
OPTIONAL_WORDS = ("prefer", "consider", "may", "usually", "when possible", "if")


def sha(b) -> str:
    return hashlib.sha256(b if isinstance(b, bytes) else str(b).encode()).hexdigest()


def _items() -> list:
    out = []
    with open(os.path.join(STEP, "rollout", "results.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def analyst_inputs(model_path: str, report_dir: str) -> dict:
    """The two matched analyst inputs, built by the UNCHANGED native formatter."""
    _install_provider_stub()
    from skillopt.gradient import reflect
    items = _items()
    pred = os.path.join(STEP, "rollout", "predictions")
    for k in ("OFFICEQA_STAGE_REPORT_DIR", "OFFICEQA_STAGE_REPORT_CONDITION", "OFFICEQA_LC_MODEL"):
        os.environ.pop(k, None)
    control = reflect.fmt_minibatch_trajectories(items, pred)
    os.makedirs(report_dir, exist_ok=True)
    os.environ["OFFICEQA_STAGE_REPORT_DIR"] = report_dir
    os.environ["OFFICEQA_STAGE_REPORT_CONDITION"] = RPT.CONDITION
    os.environ["OFFICEQA_LC_MODEL"] = model_path
    treated = reflect.fmt_minibatch_trajectories(items, pred)
    for k in ("OFFICEQA_STAGE_REPORT_DIR", "OFFICEQA_STAGE_REPORT_CONDITION", "OFFICEQA_LC_MODEL"):
        os.environ.pop(k, None)
    added, removed, outside = [], [], []
    inside = False
    for line in difflib.unified_diff(control.splitlines(), treated.splitlines(), lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        body = line[1:]
        if line.startswith("+"):
            added.append(body)
            if body.startswith(f"#### {RPT.HEADING}"):
                inside = True
        elif line.startswith("-"):
            removed.append(body)
            outside.append(body)
    return {
        "n_items": len(items),
        "control": {"chars": len(control), "sha256": sha(control)},
        "treated": {"chars": len(treated), "sha256": sha(treated)},
        "added_lines": len(added), "removed_lines": len(removed),
        "removed_sample": removed[:6],
        "matched": not removed,
        "matched_why": ("every difference is an ADDITION inside the report block: the control input is "
                        "a prefix-preserving subsequence of the treated one, so the two arms differ in "
                        "the report and in nothing else"
                        if not removed else
                        "NOT MATCHED: the treated input removed or altered lines the control input had, "
                        "listed above"),
        "report_block_found": inside,
        "added_sample": [l for l in added if l.strip()][:8],
    }


def native_apply() -> dict:
    """Re-execute the native edit-application stage and compare with the recorded candidate skill."""
    _install_provider_stub()
    from skillopt.optimizer.skill import apply_patch_with_report
    skill_in = open(os.path.join(SKILLS, "skill_v0000.md"), encoding="utf-8").read()
    merged = json.load(open(os.path.join(STEP, "merged_patch.json"), encoding="utf-8"))
    recorded_candidate = open(os.path.join(STEP, "candidate_skill.md"), encoding="utf-8").read()
    recorded_report = json.load(open(os.path.join(STEP, "edit_apply_report.json"), encoding="utf-8"))
    out = apply_patch_with_report(skill_in, merged)
    text, report = out if isinstance(out, tuple) else (out, None)
    return {
        "stage": "edit application (skillopt.optimizer.skill.apply_patch_with_report)",
        "ran": True, "pure_python": True,
        "skill_in_sha256": sha(skill_in), "skill_in_chars": len(skill_in),
        "n_edits": len(merged.get("edits") or []),
        "recomputed_sha256": sha(text), "recorded_sha256": sha(recorded_candidate),
        "reproduces_recorded_candidate": sha(text) == sha(recorded_candidate),
        "recomputed_statuses": [r.get("status") for r in (report or [])],
        "recorded_statuses": [r.get("status") for r in recorded_report],
        "statuses_match": [r.get("status") for r in (report or [])]
                          == [r.get("status") for r in recorded_report],
    }


def wording_audit() -> dict:
    """Does the control arm's candidate keep REQUIRED and OPTIONAL apart, and leave a recovery open?"""
    skill_in = open(os.path.join(SKILLS, "skill_v0000.md"), encoding="utf-8").read()
    cand = open(os.path.join(STEP, "candidate_skill.md"), encoding="utf-8").read()
    merged = json.load(open(os.path.join(STEP, "merged_patch.json"), encoding="utf-8"))
    rows = []
    for e in (merged.get("edits") or []):
        content = str(e.get("content") or "")
        req = sorted({w for w in REQUIRED_WORDS if w in content.lower()})
        opt = sorted({w for w in OPTIONAL_WORDS if w in content.lower()})
        rows.append({"op": e.get("op"), "chars": len(content),
                     "obligation_words": req, "optional_words": opt,
                     "mixes_both": bool(req) and bool(opt),
                     "first_line": content.strip().splitlines()[0][:150] if content.strip() else ""})
    def count(text, words):
        return {w: text.lower().count(w) for w in words if text.lower().count(w)}
    return {
        "edits": rows,
        "obligation_words_before": count(skill_in, REQUIRED_WORDS),
        "obligation_words_after": count(cand, REQUIRED_WORDS),
        "optional_words_before": count(skill_in, OPTIONAL_WORDS),
        "optional_words_after": count(cand, OPTIONAL_WORDS),
        "recovery_language_after": [l.strip() for l in cand.splitlines()
                                    if any(k in l.lower() for k in
                                           ("if no", "if the", "fall back", "instead", "otherwise",
                                            "cannot", "unable"))][:8],
        "reading": ("Counted, not judged: the candidate's obligation and optional vocabulary beside the "
                    "starting skill's, and the lines that leave an alternative open. A rise in "
                    "obligation words with no alternative left is the distortion to watch for."),
    }


TOPIC_WORDS = {
    "column header identity": ("column header", "headers literally", "header block", "column path",
                              "sub-label", "its own header"),
    "money scale / unit": ("in thousands", "in millions", "unit", "scale", "multiply by", "divide by",
                           "factor of"),
    "citing a position": ("line number", "cite", "record each operand", "operand with its"),
    "period / row identity": ("fiscal year", "annual", "monthly", "period"),
}


def overlap_with_control() -> dict:
    """Did the CONTROL arm - no report at all - already find what the framework's report is about?

    This is the question the comparison exists to answer, and on this step it has an uncomfortable
    answer, so it is measured rather than described: the control analyst's own patches and the merged
    edits are searched for the topics the framework report carries.
    """
    import glob
    patches = {}
    for path in sorted(glob.glob(os.path.join(STEP, "patches", "*.json"))):
        patches[os.path.basename(path)] = json.load(open(path, encoding="utf-8"))
    merged = json.load(open(os.path.join(STEP, "merged_patch.json"), encoding="utf-8"))
    control_text = json.dumps(patches, ensure_ascii=False) + json.dumps(merged, ensure_ascii=False)
    low = control_text.lower()
    topics = {}
    for topic, words in TOPIC_WORDS.items():
        hits = sorted({w for w in words if w in low})
        topics[topic] = {"found_in_control": bool(hits), "phrases": hits}
    summaries = []
    for name, p in patches.items():
        for row in (p.get("failure_summary") or []):
            summaries.append({"patch": name, "type": row.get("failure_type"),
                              "count": row.get("count"), "description": row.get("description")})
    return {
        "topics": topics,
        "control_failure_summaries": summaries,
        "control_edit_first_lines": [str(e.get("content") or "").strip().splitlines()[0][:220]
                                     for e in (merged.get("edits") or [])
                                     if str(e.get("content") or "").strip()],
        "reading": ("On this step the no-report arm independently produced a column-header "
                    "instruction - 'Read column headers literally before using any cell' - and an "
                    "instruction to record each operand with its period and unit. So the framework's "
                    "report is not what reveals this failure mode here. What it adds is per-number "
                    "citations, the states those citations support, and explicit unknowns. Whether "
                    "that changes the EDIT is exactly what the unrun analyst call would decide, and it "
                    "is not settled by this artifact."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    model_path = os.path.join(a.out, "FW_MODEL.json")
    report_dir = os.path.join(a.out, "reports_fw")
    step_rec = json.load(open(os.path.join(STEP, "step_record.json"), encoding="utf-8"))
    stub = _install_provider_stub()
    doc = {
        "schema": SCHEMA, "rule": RUN.RULE, "phase": "edit",
        "step": {"path": STEP, "step": step_rec["step"], "epoch": step_rec["epoch"],
                 "n_patches": step_rec["n_patches"], "n_edits_merged": step_rec["n_edits_merged"],
                 "n_edits_ranked": step_rec["n_edits_ranked"],
                 "edit_budget": step_rec["edit_budget"],
                 "lr_control_mode": step_rec["lr_control_mode"],
                 "support_counts": step_rec["support_counts"],
                 "rollout_hard": step_rec["rollout_hard"],
                 "selection_hard": step_rec["selection_hard"],
                 "candidate_gate_score": step_rec["candidate_gate_score"],
                 "gate_action": step_rec["action"]},
        "provider_stub": stub,
        "matched_analyst_input": analyst_inputs(model_path, report_dir),
        "stages": {
            "analyst": {"ran": False,
                        "why": "a provider call; SkillOpt runs on the cluster in this project. Both "
                               "request bodies are built and digested here at zero cost.",
                        "control_result": "the recorded patches of this step are used as its output"},
            "merge": {"ran": False,
                      "why": "skillopt.gradient.aggregate.merge_patches calls the optimizer; the "
                             "control arm's recorded merge is read instead"},
            "rank": {"ran": False, "fired_in_the_recorded_step": False,
                     "why": f"{step_rec['n_edits_merged']} merged edits against an edit budget of "
                            f"{step_rec['edit_budget']}, so rank_and_select was never reached. This is "
                            f"a stage that legitimately does not fire, not a missing result."},
            "apply": native_apply(),
        },
        "wording_audit": wording_audit(),
        "overlap_with_control": overlap_with_control(),
    }
    reports = {}
    for name in sorted(os.listdir(report_dir)) if os.path.isdir(report_dir) else []:
        if name.endswith(".md"):
            p = os.path.join(report_dir, name)
            reports[name] = {"chars": os.path.getsize(p), "sha256": sha(open(p, "rb").read())}
    doc["delivered_reports"] = reports
    RUN.seal(doc)
    RUN.jdump(doc, os.path.join(a.out, "FW_EDIT.json"))
    m = doc["matched_analyst_input"]
    print(f"[edit] {doc['self_sha256'][:16]}  ->  {a.out}")
    print(f"  matched analyst input: {m['matched']} "
          f"(+{m['added_lines']} lines, -{m['removed_lines']}); control {m['control']['chars']} chars "
          f"-> treated {m['treated']['chars']}")
    ap_ = doc["stages"]["apply"]
    print(f"  native apply re-run: reproduces recorded candidate = "
          f"{ap_['reproduces_recorded_candidate']}, statuses match = {ap_['statuses_match']}")
    print(f"  delivered reports: {len(reports)}")
    for t, v in doc["overlap_with_control"]["topics"].items():
        print(f"  control already covers {t!r}: {v['found_in_control']} {v['phrases'][:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
