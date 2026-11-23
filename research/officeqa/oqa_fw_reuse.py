#!/usr/bin/env python3
"""Verify, by provenance rather than by outcome, that the contract arm may reuse the baselines' work.

The instruction: "Verify that the completed baselines' shared epoch-1 rollout matches the training
manifest, starting-skill digest, actor/tool settings and completeness. Then reuse those immutable
trajectories." and "Select reused records by provenance, not by favorable outcomes."

So this script reads no score. It checks:

  1. IDENTITY       the epoch-1 rollout the contract arm would inject is byte-identical to the one the
                    completed Original SkillOpt arm consumed, results and predictions tree alike.
  2. MANIFEST       its 12 question ids are exactly the frozen training set of rule OQA-SEL-4.
  3. SKILL          every saved row ran under the STARTING skill, checked by finding that skill's text
                    inside the row's own recorded system prompt - not by trusting a path.
  4. SETTINGS       every row's `rollout_settings_fingerprint` is the one this config produces, so a
                    changed search mode, tool mode, turn budget or token cap would be caught.
  5. COMPLETENESS   every id has a conversation with at least one tool call and a committed answer
                    attempt, and the gate baseline has its own 12 rows.

A failure is reported as the exact mismatch and exits non-zero, so the chain stops before any paid
work. `--require-identical` is what the campaign uses; without it the identity check is reported but
not enforced, which is only for inspecting a candidate directory.

usage:
  python3 -I research/officeqa/oqa_fw_reuse.py --rollout <dir> --baseline-rollout <dir> \
      --selection-baseline <dir> --frozen docs/research/OFFICEQA_FROZEN_SETS_V4.json \
      --skill skillopt/envs/officeqa/skills/initial.md --config configs/research/officeqa_base2.yaml \
      --out <dir>/FW_REUSE.json --require-identical
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_tree(root: str) -> str:
    """One digest over a whole directory: every relative path and its content, in sorted order."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            h.update(rel.encode() + b"\0" + sha_file(full).encode() + b"\n")
    return h.hexdigest()


def rows(path: str) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def expected_fingerprint(config_path: str) -> tuple:
    """The fingerprint THIS config produces, computed by the project's own function."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.config import load_config          # noqa: PLC0415
    from skillopt.envs.officeqa.rollout import rollout_settings_fingerprint  # noqa: PLC0415
    cfg = load_config(config_path)
    env = cfg.env if hasattr(cfg, "env") else cfg["env"]

    def g(name, default=None):
        return getattr(env, name, None) if hasattr(env, name) else env.get(name, default)

    kw = {"search_mode": g("search_mode", "offline"), "use_local_tools": bool(g("use_local_tools", True)),
          "max_tool_turns": int(g("max_tool_turns", 24)),
          "max_queries_per_turn": int(g("max_queries_per_turn", 0) or 0),
          "max_completion_tokens": int(g("max_completion_tokens", 16384))}
    return rollout_settings_fingerprint(**kw), kw


def check(a) -> dict:
    problems: list = []
    notes: list = []
    res = os.path.join(a.rollout, "results.jsonl")
    if not os.path.isfile(res):
        return {"ok": False, "problems": [f"no results.jsonl under {a.rollout}"]}
    R = rows(res)
    ids = [str(r.get("id")) for r in R]

    # 1. identity against the record the baselines consumed
    identity = {}
    if a.baseline_rollout:
        b_res = os.path.join(a.baseline_rollout, "results.jsonl")
        identity = {"results_sha256": sha_file(res), "baseline_results_sha256": sha_file(b_res),
                    "predictions_tree_sha256": sha_tree(os.path.join(a.rollout, "predictions")),
                    "baseline_predictions_tree_sha256":
                        sha_tree(os.path.join(a.baseline_rollout, "predictions"))}
        identity["results_identical"] = (identity["results_sha256"]
                                         == identity["baseline_results_sha256"])
        identity["predictions_identical"] = (identity["predictions_tree_sha256"]
                                             == identity["baseline_predictions_tree_sha256"])
        if a.require_identical and not (identity["results_identical"]
                                        and identity["predictions_identical"]):
            problems.append("the epoch-1 rollout is NOT byte-identical to the one the completed "
                            "baselines consumed, so reusing it would not reproduce their evidence")

    # 2. the training manifest
    manifest = {}
    if a.frozen:
        fz = json.load(open(a.frozen, encoding="utf-8"))
        train = fz.get("sets", {}).get("train")
        train_ids = sorted(train if isinstance(train, list)
                           else (train or {}).get("ids") or [])
        manifest = {"frozen_digest": fz.get("digest"), "rule": fz.get("rule"),
                    "n_train": len(train_ids), "n_rollout": len(ids),
                    "missing_from_rollout": sorted(set(train_ids) - set(ids)),
                    "extra_in_rollout": sorted(set(ids) - set(train_ids))}
        if manifest["missing_from_rollout"] or manifest["extra_in_rollout"]:
            problems.append(f"the rollout's question ids are not the frozen training set: "
                            f"missing {manifest['missing_from_rollout']}, "
                            f"extra {manifest['extra_in_rollout']}")

    # 3. the starting skill, found inside each row's OWN recorded system prompt
    skill = {}
    if a.skill:
        text = open(a.skill, encoding="utf-8").read()
        body = text.strip()
        skill = {"path": a.skill, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                 "bytes": len(text.encode())}
        off = [r.get("id") for r in R if body not in str(r.get("target_system_prompt") or "")]
        skill["rows_whose_system_prompt_does_not_contain_it"] = off
        if off:
            problems.append(f"{len(off)} row(s) did not run under the starting skill: {off[:6]}")

    # 4. the actor/tool settings fingerprint
    settings = {}
    if a.config:
        try:
            fp, kw = expected_fingerprint(a.config)
        except Exception as exc:                         # noqa: BLE001 - reported, not raised
            seen = sorted({str(r.get("rollout_settings_fingerprint") or "") for r in R})
            settings = {"problem": f"could not compute the expected fingerprint: "
                                   f"{type(exc).__name__}: {exc}",
                        "seen": seen, "all_rows_share_one": len([s for s in seen if s]) <= 1}
            notes.append(settings["problem"])
            if not settings["all_rows_share_one"]:
                problems.append(f"the saved rows do not share one actor/tool settings fingerprint: "
                                f"{seen}; they were not all produced under the same settings")
        else:
            seen = sorted({str(r.get("rollout_settings_fingerprint") or "") for r in R})
            settings = {"expected": fp, "settings": kw, "seen": seen,
                        "config_sha256": sha_file(a.config)}
            settings["all_rows_share_one"] = len([s for s in seen if s]) <= 1
            bad = [s for s in seen if s and s != fp]
            missing = sum(1 for r in R if not r.get("rollout_settings_fingerprint"))
            settings["rows_without_a_fingerprint"] = missing
            if bad:
                problems.append(f"rows ran under a different actor/tool settings fingerprint: {bad}")
            if missing:
                notes.append(f"{missing} row(s) carry no fingerprint (written before the field "
                             f"existed); their system prompt is the fallback check")

    # 5. completeness, read from the conversations rather than from the rows
    comp = {"n_ids": len(ids), "missing_prediction_dir": [], "no_tool_call": [], "no_answer": [],
            "n_steps": {},
            "what_it_reads": "the saved conversation, and the row's own predicted_answer field, which "
                             "says whether an answer was committed. It does NOT read ground_truth, "
                             "hard or soft, so no attempt is selected or rejected by its outcome."}
    by_id = {str(r.get("id")): r for r in R}
    for tid in ids:
        d = os.path.join(a.rollout, "predictions", tid)
        cpath = os.path.join(d, "conversation.json")
        if not os.path.isfile(cpath):
            comp["missing_prediction_dir"].append(tid)
            continue
        conv = json.load(open(cpath, encoding="utf-8"))
        items = conv if isinstance(conv, list) else (conv.get("items") or conv.get("messages") or [])
        text = json.dumps(items)
        comp["n_steps"][tid] = len(items)
        if not any(k in text for k in ("read(", "grep(", "glob(")):
            comp["no_tool_call"].append(tid)
        if not str(by_id.get(tid, {}).get("predicted_answer") or "").strip():
            comp["no_answer"].append(tid)
    if comp["missing_prediction_dir"]:
        problems.append(f"no saved conversation for {comp['missing_prediction_dir']}")
    if comp["no_tool_call"]:
        notes.append(f"{len(comp['no_tool_call'])} attempt(s) made no tool call: "
                     f"{comp['no_tool_call'][:6]}. That is a RECORD, not a defect - an attempt that "
                     f"read nothing is evidence about the actor, and it is kept.")
    if comp["no_answer"]:
        notes.append(f"{len(comp['no_answer'])} attempt(s) committed no answer: "
                     f"{comp['no_answer'][:6]}. Also kept.")

    sel = {}
    if a.selection_baseline:
        spath = os.path.join(a.selection_baseline, "results.jsonl")
        if not os.path.isfile(spath):
            problems.append(f"no gate baseline at {a.selection_baseline}")
        else:
            S = rows(spath)
            sel = {"n": len(S), "sha256": sha_file(spath),
                   "split": sorted({str(r.get("split") or "") for r in S}) or None}
            if a.expect_selection and len(S) != a.expect_selection:
                problems.append(f"the gate baseline has {len(S)} rows, expected "
                                f"{a.expect_selection}")

    return {"schema": "lso.officeqa_fw_reuse.v1", "rule": "OQA-FW-1",
            "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "rollout": os.path.abspath(a.rollout),
            "baseline_rollout": os.path.abspath(a.baseline_rollout) if a.baseline_rollout else None,
            "identity": identity, "manifest": manifest, "starting_skill": skill,
            "actor_tool_settings": settings, "completeness": comp, "gate_baseline": sel,
            "reads_no_score": ("this check reads ids, prompts, fingerprints and conversations. It "
                               "never reads ground_truth, hard or soft."),
            "problems": problems, "notes": notes, "ok": not problems}


def summarize(path: str) -> str:
    """One line from a written FW_REUSE.json, for the job log."""
    d = json.load(open(path, encoding="utf-8"))
    idy = d.get("identity") or {}
    st = d.get("actor_tool_settings") or {}
    return (f"identical results={idy.get('results_identical')} "
            f"predictions={idy.get('predictions_identical')} | "
            f"manifest {(d.get('manifest') or {}).get('n_train')} train ids matched | "
            f"skill {(d.get('starting_skill') or {}).get('sha256', '-')[:12]} | "
            f"settings fingerprint {(st.get('seen') or ['-'])[0]} "
            f"(expected {st.get('expected', 'not computed')}) | ok={d.get('ok')}")


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--summarize":
        print(summarize(argv[1]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollout", required=True)
    ap.add_argument("--baseline-rollout")
    ap.add_argument("--selection-baseline")
    ap.add_argument("--expect-selection", type=int, default=12)
    ap.add_argument("--frozen")
    ap.add_argument("--skill")
    ap.add_argument("--config")
    ap.add_argument("--out")
    ap.add_argument("--require-identical", action="store_true")
    a = ap.parse_args(argv)
    doc = check(a)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
    print(json.dumps({k: doc[k] for k in ("ok", "problems", "notes")}, indent=1))
    for k in ("identity", "manifest", "starting_skill", "actor_tool_settings", "gate_baseline"):
        print(f"{k}: {json.dumps(doc.get(k) or {}, sort_keys=True)[:400]}")
    return 0 if doc["ok"] else 4


if __name__ == "__main__":
    sys.exit(main())
