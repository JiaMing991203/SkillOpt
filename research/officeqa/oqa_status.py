#!/usr/bin/env python3
"""What has actually happened since the answer-recovery fix, from the records alone.

Rule OQA-ST-1. Offline evidence gathering for the OfficeQA status packet. Saved artifacts only: no model
call, no GPU, no gameplay. Nothing here modifies a checker, a report or a frozen artifact; the two
pending checker proposals (P-1, P-2) are MEASURED by `oqa_audit_bind` and are not applied by this file.

THREE THINGS IT ESTABLISHES, each separately.

1. `inventory` - the record sets produced through the CORRECTED client, verified from their producing
   configuration rather than from a directory date. A record set counts as corrected only when every row
   carries an `answer_recovery_policy` AND the run's own call log records the policy it ran under; the
   transmitted generation controls are read back from the same log, so "thinking on" and "thinking off"
   are facts about the request rather than about the plan. Training/development membership comes from the
   frozen split record, never from a file name.

   SPLIT DISCIPLINE. The validation record set (the eight S0 passes) is inventoried from the audit
   manifest and its own rows: outcome, answer source, settings, and the size of the actor-written text.
   No contract, extractor or threshold is fitted to it here, and the per-operand extraction that the
   development records get is deliberately NOT run over it.

2. `feedback` - the chain for one historical attempt: trace -> extracted facts -> the contract/stage
   report that was delivered -> the ordinary SkillOpt analysis of the same attempt -> the optimizer's
   proposed edit -> the merge and ranking -> the gate's decision -> whether the instruction survives in
   any retained skill. Where an artifact does not exist it is reported as MISSING rather than inferred.

   THE OPTIMIZER REQUEST IS A RECONSTRUCTION, not the saved bytes. The campaign saved no analyst prompt,
   so `fmt_minibatch_trajectories` is re-run on the saved rows with the report hook off and on. The
   minibatch composition the campaign used is not recorded either, so the rendered view is labelled a
   proxy and its only firm content is the part contributed by the named item.

3. `statements` - the six status claims the packet has to stand behind, each decided by an assertion over
   the artifacts above, with the evidence that decided it.

usage:
  oqa_status.py inventory  --campaign <dir> --frozen-sets <json> [--bind-audit <json>]
                           [--s0-manifest <json>] --out <dir>
  oqa_status.py feedback   --campaign <dir> --item UID0014 --arm C --epoch 3 --out <dir>
  oqa_status.py statements --campaign <dir> --inventory <json> --out <dir>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

RULE = "OQA-ST-1"
SCHEMA = "lso.officeqa_status.v1"

# The narrow recovery policy's own names, from amendment A-2. A row whose policy is one of these ran
# through the corrected client; a row with no policy at all ran before A-2 existed.
CORRECTED_POLICIES = ("committed_answer", "reasoning", "reasoning_text")


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: str) -> list:
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ───────────────────────── 1. the producing configuration ─────────────────────────

def transmitted_configuration(call_log: str) -> dict:
    """The generation controls as SENT, read back from the run's own call log.

    This is the check that separates a corrected record from a directory with a recent date. The request
    layer of each record carries the model, the sampling controls and the `extra_body` that transmits a
    serving control such as `enable_thinking`; the recovery layer carries the policy the client applied.
    Disagreement inside one run is reported, never averaged away.
    """
    rows = _read_jsonl(call_log)
    if not rows:
        return {"call_log": call_log, "present": False}
    models, temps, caps, bodies, policies, tools = set(), set(), set(), set(), set(), set()
    recovered, sources, source_chars = 0, {}, []
    for r in rows:
        req = r.get("request") or {}
        models.add(str(req.get("model")))
        temps.add(str(req.get("temperature")))
        caps.add(str(req.get("max_tokens")))
        bodies.add(json.dumps(req.get("extra_body"), sort_keys=True))
        tools.add(str(req.get("tools")))
        rec = r.get("recovery") or {}
        if rec:
            policies.add(str(rec.get("policy")))
            if rec.get("recovered"):
                recovered += 1
                fld = str(rec.get("source_field"))
                sources[fld] = sources.get(fld, 0) + 1
                if rec.get("source_chars") is not None:
                    source_chars.append(int(rec["source_chars"]))
    return {
        "call_log": call_log,
        "present": True,
        "n_calls": len(rows),
        "model": sorted(models),
        "temperature": sorted(temps),
        "max_tokens": sorted(caps),
        "tools_enabled": sorted(tools),
        "extra_body": sorted(bodies),
        "recovery_policy_in_the_log": sorted(policies),
        "calls_that_recovered": recovered,
        "recovery_source_fields": sources,
        "provider_field_chars_min": min(source_chars) if source_chars else None,
        "provider_field_chars_max": max(source_chars) if source_chars else None,
        "thinking_control": ("enable_thinking=false" if any("enable_thinking\": false" in b for b in bodies)
                             else "omitted, so the serving default is in force"),
    }


# ───────────────────────── 2. what the permitted trace contains ─────────────────────────

_ARITH = re.compile(r"\d[\d,\.]*\s*(?:[-+*/×÷]|times|divided by|multiplied by|minus|plus)\s*\d")
_FACTOR_WORD = re.compile(r"(?:divide[d]?\s+by|multipl(?:y|ied)\s+by|/|\*)\s*1[,\d]*(?:\.\d+)?|1e-?\d+", re.I)


def actor_written(prediction_dir: str, item: str) -> dict:
    """The actor-visible trace of one attempt: what the LEARNER would receive, and nothing else.

    `conversation.json` is the record the environment kept and the only actor text a contract learner or
    an analyst ever sees. Entry 0 is the harness's own user message, so it is excluded: including it would
    credit the actor with the supplied pages and the question.
    """
    path = os.path.join(prediction_dir, item, "conversation.json")
    out = {"conversation": path, "present": os.path.isfile(path)}
    if not out["present"]:
        return out
    conv = _load(path)
    msgs, tools = [], 0
    for i, m in enumerate(conv):
        if not isinstance(m, dict):
            continue
        kind = m.get("type") or m.get("role")
        if kind == "tool_call":
            tools += 1
            continue
        if i == 0:
            continue
        text = str(m.get("content") or "")
        if text:
            msgs.append({"index": i, "chars": len(text)})
    joined = "\n".join(
        str(m.get("content") or "") for i, m in enumerate(conv)
        if isinstance(m, dict) and i > 0 and (m.get("type") or m.get("role")) != "tool_call")
    out.update({
        "n_entries": len(conv),
        "n_tool_calls": tools,
        "actor_messages_with_text": len(msgs),
        "actor_written_chars": len(joined),
        "largest_message": (max(msgs, key=lambda x: x["chars"]) if msgs else None),
        "last_entry_index": len(conv) - 1,
        "arithmetic_written_in_the_trace": bool(_ARITH.search(joined)),
        "a_conversion_factor_is_stated": bool(_FACTOR_WORD.search(joined)),
        "answer_block_in_the_trace": "<answer>" in joined,
    })
    # The same-message question: is every operand, the arithmetic and the answer in ONE final message?
    if msgs:
        last = msgs[-1]
        out["one_final_message_carries_everything"] = (
            len(msgs) == 1 or (last["chars"] >= 0.8 * out["actor_written_chars"]))
        out["final_message_index"] = last["index"]
        out["final_message_chars"] = last["chars"]
    return out


# ───────────────────────── 3. the inventory ─────────────────────────

def membership(frozen_sets: dict, item: str) -> list:
    sets = frozen_sets.get("sets") or {}
    return sorted(name for name, spec in sets.items()
                  if item in set((spec or {}).get("ids") or []))


def _run_key(prediction_dir: str, item: str) -> tuple:
    """(run name, item) - the join key between an inventory row and the existing bind audit.

    Deliberately NOT the absolute path: the audit recorded cluster paths, and an inventory built from a
    copy of the same tree must still join. The run name is the directory that owns `predictions`, which
    is what distinguishes A1's UID0017 from A2's.
    """
    return (os.path.basename(os.path.dirname(os.path.dirname(prediction_dir))), item)


def _bind_audit_index(bind_audit: dict) -> dict:
    """(run name, item) -> what the repaired extractor found, from the existing audit.

    Reused rather than recomputed on purpose: the audit of 2026-11-18 is the frozen measurement, and a
    second implementation of the same count here would be a second thing to trust.
    """
    index = {}
    for group in (bind_audit or {}).get("groups") or []:
        for r in group.get("results") or []:
            cands = r.get("candidates") or []
            mon = [c for c in cands if c.get("verdict") == "MONETARY"]
            holds = [c for c in mon if (c.get("bindings") or [])
                     and any(b.get("truth") == "HOLDS" for b in c["bindings"])]
            states = {}
            for c in mon:
                for b in c.get("bindings") or []:
                    st = ((b.get("resolution_from_later_steps") or {}).get("state"))
                    if st:
                        states[st] = states.get(st, 0) + 1
            index[_run_key(str(r.get("prediction_dir")), str(r.get("item")))] = {
                "group": group.get("which"),
                "audited_prediction_dir": r.get("prediction_dir"),
                "trace_key": r.get("trace_key"),
                "candidate_numbers": len(cands),
                "monetary_operands": len(mon),
                "operands_with_a_holding_binding": len(holds),
                "resolution_states": states,
                "problems": r.get("problems") or [],
                "committed_at_step": (r.get("prefix") or {}).get("committed_at_step"),
            }
    return index


def cmd_inventory(campaign: str, frozen_sets_path: str, bind_audit_path: str,
                  s0_manifest_path: str, out_dir: str) -> dict:
    frozen = _load(frozen_sets_path)
    bind = _bind_audit_index(_load(bind_audit_path)) if bind_audit_path else {}
    sets, problems = [], []

    # (a) the development episodes: every run directory that holds rows and a call log
    for run_dir in sorted(glob.glob(os.path.join(campaign, "followup_*", "episodes", "*"))):
        rows_path = os.path.join(run_dir, "results.jsonl")
        if not os.path.isfile(rows_path):
            continue
        rows = _read_jsonl(rows_path)
        cfg = transmitted_configuration(os.path.join(run_dir, "actor_calls.jsonl"))
        policies = sorted({str(r.get("answer_recovery_policy")) for r in rows})
        corrected = all(str(r.get("answer_recovery_policy")) in CORRECTED_POLICIES for r in rows) and bool(rows)
        records = []
        for r in rows:
            item = str(r.get("id"))
            vis = actor_written(os.path.join(run_dir, "predictions"), item)
            pred_dir = os.path.join(run_dir, "predictions", item)
            key = _run_key(pred_dir, item)
            records.append({
                "item": item,
                "sets": membership(frozen, item),
                "prediction_dir": pred_dir,
                "hard": r.get("hard"),
                "soft": r.get("soft"),
                "emitted": bool(str(r.get("predicted_answer") or "").strip()),
                "answer_source": ("alternate provider field: " + str(r.get("answer_recovery_source"))
                                  if r.get("answer_recovered") else "ordinary content"),
                "recovery_outcome": r.get("answer_recovery_outcome"),
                "recovery_policy": r.get("answer_recovery_policy"),
                "termination_cause": r.get("termination_cause"),
                "last_finish_reason": r.get("last_finish_reason"),
                "n_turns": r.get("n_turns"),
                "actor_visible_trace": vis,
                "repaired_extractor": bind.get(key),
            })
        sets.append({
            "record_set": os.path.relpath(run_dir, campaign),
            "kind": "development episodes",
            "n_records": len(rows),
            "corrected_client": corrected,
            "corrected_client_basis": {
                "row_policies": policies,
                "call_log_policies": cfg.get("recovery_policy_in_the_log"),
                "verified_from": "the rows' own policy field AND the run's call log, not the directory date",
            },
            "configuration": cfg,
            "provider_fields_kept_separately": {
                "actor_calls.jsonl": os.path.isfile(os.path.join(run_dir, "actor_calls.jsonl")),
                "raw_capture.jsonl": os.path.isfile(os.path.join(run_dir, "raw_capture.jsonl")),
                "note": "the learner sees `conversation.json`; the full provider fields live only here",
            },
            "records": records,
        })

    # (b) the validation record set, from the audit manifest. Inventoried, never fitted to.
    if s0_manifest_path and os.path.isfile(s0_manifest_path):
        man = _load(s0_manifest_path)
        recs = man.get("records") or []
        by_pass = {}
        for r in recs:
            p = str(r.get("pass"))
            b = by_pass.setdefault(p, {"pass": p, "n": 0, "correct": 0, "emitted": 0,
                                       "sources": {}, "jobs": set(), "fingerprints": set(),
                                       "policies": set(), "finish_reasons": {}})
            b["n"] += 1
            b["correct"] += int(r.get("correct") or 0)
            b["emitted"] += int(bool(r.get("emitted")))
            src = str(r.get("answer_recovery_source")) if r.get("answer_recovered") else (
                "content" if r.get("emitted") else "none")
            b["sources"][src] = b["sources"].get(src, 0) + 1
            b["jobs"].add(str(r.get("job_of_record")))
            b["fingerprints"].add(str(r.get("rollout_settings_fingerprint")))
            b["policies"].add(str(r.get("answer_recovery_policy")))
            fr = str(r.get("last_finish_reason"))
            b["finish_reasons"][fr] = b["finish_reasons"].get(fr, 0) + 1
        passes = []
        for p in sorted(by_pass):
            b = by_pass[p]
            for k in ("jobs", "fingerprints", "policies"):
                b[k] = sorted(b[k])
            passes.append(b)
        ids = sorted({str(r.get("question_id")) for r in recs})
        sets.append({
            "record_set": os.path.relpath(os.path.dirname(s0_manifest_path), campaign) + " (S0 x 8 passes)",
            "kind": "validation measurement - inventoried only",
            "n_records": len(recs),
            "corrected_client": all(str(r.get("answer_recovery_policy")) in CORRECTED_POLICIES for r in recs),
            "corrected_client_basis": {
                "row_policies": sorted({str(r.get("answer_recovery_policy")) for r in recs}),
                "verified_from": "every manifest record's own policy field",
            },
            "distinct_questions": len(ids),
            "question_membership": sorted({tuple(membership(frozen, i)) for i in ids}),
            "per_pass": passes,
            "split_discipline": "no contract, extractor or threshold is fitted to these records here; "
                                "the per-operand extraction run over the development episodes is not run "
                                "over them",
            "records": [],
        })
    else:
        problems.append("the S0 audit manifest was not found, so the validation set is not inventoried")

    dev = [s for s in sets if s["kind"] == "development episodes"]
    dev_records = [r for s in dev for r in s["records"]]
    training = [r for r in dev_records if "train" in r["sets"]]
    failures = [r for r in training if not float(r.get("hard") or 0)]
    summary = {
        "development_record_sets": len(dev),
        "development_records": len(dev_records),
        "development_records_on_training_questions": len(training),
        "distinct_training_questions": sorted({r["item"] for r in training}),
        "corrected_client_training_failures": len(failures),
        "corrected_client_training_failure_items": sorted({r["item"] for r in failures}),
        "records_whose_trace_carries_a_monetary_operand": sum(
            1 for r in dev_records if (r.get("repaired_extractor") or {}).get("monetary_operands")),
        "records_whose_trace_carries_arithmetic": sum(
            1 for r in dev_records if (r["actor_visible_trace"] or {}).get("arithmetic_written_in_the_trace")),
        "records_with_a_holding_binding": sum(
            1 for r in dev_records
            if (r.get("repaired_extractor") or {}).get("operands_with_a_holding_binding")),
    }
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "campaign": campaign,
           "frozen_sets": frozen_sets_path, "record_sets": sets, "summary": summary,
           "problems": problems}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "STATUS_INVENTORY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── 4. the feedback chain for one attempt ─────────────────────────

def _render_analyst_view(items: list, prediction_dir: str, report_dir) -> str:
    """SkillOpt's own minibatch formatter, with the report hook off or on.

    This is a RECONSTRUCTION. The campaign saved no analyst prompt, and the minibatch composition it used
    is not recorded, so the only part of this text that is certainly what the optimizer saw is the block
    contributed by the named item.
    """
    if report_dir:
        os.environ["OFFICEQA_STAGE_REPORT_DIR"] = report_dir
    else:
        os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
    reflect = importlib.import_module("skillopt.gradient.reflect")
    importlib.reload(reflect)
    return reflect.fmt_minibatch_trajectories(items, prediction_dir)


def cmd_feedback(campaign: str, item: str, arm: str, epoch: int, out_dir: str) -> dict:
    step = os.path.join(campaign, "arm_%s" % arm, "steps", "step_%04d" % epoch)
    rollout = os.path.join(step, "rollout")
    reports = os.path.join(campaign, "arm_%s" % arm, "stage_reports")
    chain, missing = {}, []

    # 1. the trace
    rows = _read_jsonl(os.path.join(rollout, "results.jsonl"))
    row = next((r for r in rows if str(r.get("id")) == item), None)
    if row is None:
        return {"error": "no row for %s in %s" % (item, rollout)}
    chain["1_trace"] = {
        "source_file": os.path.join(rollout, "results.jsonl"),
        "question": row.get("question"),
        "ground_truth": row.get("ground_truth"),
        "hard": row.get("hard"), "soft": row.get("soft"),
        "predicted_answer": row.get("predicted_answer"),
        "fail_reason": row.get("fail_reason"),
        "termination_cause": row.get("termination_cause"),
        "actor_visible_trace": actor_written(os.path.join(rollout, "predictions"), item),
    }

    # 2. the contract/stage report that was delivered
    gen = os.path.join(reports, "GENERATED.jsonl")
    delivered = None
    for g in _read_jsonl(gen):
        if str(g.get("id")) == item and ("step_%04d" % epoch) in str(g.get("prediction_dir", "")):
            delivered = g
            break
    report_text, report_path = None, None
    if delivered:
        for cand in sorted(glob.glob(os.path.join(reports, "%s.*.md" % item))):
            with open(cand, encoding="utf-8") as f:
                text = f.read()
            if sha(text) == str(delivered.get("report_sha256")):
                report_text, report_path = text, cand
                break
    if report_text is None:
        missing.append("the delivered report file for %s at epoch %d could not be matched by digest" % (item, epoch))
    unit_lines = [L for L in (report_text or "").splitlines() if "[unit_match]" in L]
    chain["2_contract_stage_report"] = {
        "generated_record": delivered,
        "report_file": report_path,
        "report_chars": len(report_text or "") or None,
        "obligations": (delivered or {}).get("obligations"),
        "model_sha256": (delivered or {}).get("model_sha256"),
        "model_assumptions": (delivered or {}).get("model_assumptions"),
        "unit_match_lines": unit_lines,
        "in_sample_note": next((L for L in (report_text or "").splitlines()
                                if L.startswith("NOTE:")), None),
        "lean_checked": ("lean" in (report_text or "").lower()),
    }

    # 3. the ordinary SkillOpt analysis of the same attempt - the step digest names its items
    td_path = os.path.join(step, "trajectory_digest.json")
    ordinary = {"file": td_path, "present": os.path.isfile(td_path)}
    if ordinary["present"]:
        td = _load(td_path)
        named, others = [], []
        for pat in td.get("failure_patterns") or []:
            entry = {"pattern": pat.get("pattern"), "count": pat.get("count"),
                     "n_task_ids": len(pat.get("task_ids") or [])}
            (named if item in (pat.get("task_ids") or []) else others).append(entry)
        ordinary.update({"patterns_naming_this_item": named,
                         "patterns_not_naming_this_item": others,
                         "step_action": td.get("action"),
                         "score_before": td.get("score_before"), "score_after": td.get("score_after"),
                         "rejected_edits": td.get("rejected_edits")})
    else:
        missing.append("no trajectory_digest.json at %s" % td_path)
    chain["3_ordinary_skillopt_analysis"] = ordinary

    # 4. the optimizer's own minibatch analyses and proposals. The patch records do NOT name their items.
    patches = []
    for p in sorted(glob.glob(os.path.join(step, "patches", "*.json"))):
        d = _load(p)
        lines = d.get("failure_summary") or d.get("success_patterns") or []
        patches.append({
            "file": p,
            "source_type": d.get("source_type"),
            "batch_size": d.get("batch_size"),
            "names_any_item": any(item in json.dumps(l) for l in lines),
            "analysis_lines": [
                (l if isinstance(l, str) else "%s (count %s): %s" % (
                    l.get("failure_type"), l.get("count"), l.get("description")))
                for l in lines],
            "patch_reasoning": (d.get("patch") or {}).get("reasoning"),
            "proposed_edits": [{"op": e.get("op"), "target": e.get("target"), "content": e.get("content")}
                               for e in ((d.get("patch") or {}).get("edits") or [])],
        })
    chain["4_optimizer_proposals"] = {
        "n_patch_records": len(patches),
        "item_identity_in_the_patch_records": any(p["names_any_item"] for p in patches),
        "note": "the saved patch records carry an aggregated analysis with no item identity; the step "
                "digest is where an item is named",
        "patches": patches,
    }

    # 5. merge, ranking, application
    ranked_path = os.path.join(step, "ranked_edits.json")
    merged_path = os.path.join(step, "merged_patch.json")
    apply_path = os.path.join(step, "edit_apply_report.json")
    ranked = _load(ranked_path) if os.path.isfile(ranked_path) else None
    merged = _load(merged_path) if os.path.isfile(merged_path) else None
    chain["5_merge_and_ranking"] = {
        "merged_edits": len((merged or {}).get("edits") or []) if merged else None,
        "ranked_edits": len((ranked or {}).get("edits") or []) if ranked else None,
        "merge_reasoning": (merged or {}).get("reasoning"),
        "ranking_reasoning": ((ranked or {}).get("ranking_details") or {}).get("reasoning") if ranked else None,
        "selected_indices": ((ranked or {}).get("ranking_details") or {}).get("selected_indices") if ranked else None,
        "retained_edits": [{"op": e.get("op"), "target": e.get("target"), "content": e.get("content"),
                            "support_count": e.get("support_count"), "source_type": e.get("source_type")}
                           for e in ((ranked or {}).get("edits") or [])],
        "apply_report": _load(apply_path) if os.path.isfile(apply_path) else None,
    }

    # 6. the gate, and whether any retained skill carries the instruction
    rec_path = os.path.join(step, "step_record.json")
    rec = _load(rec_path) if os.path.isfile(rec_path) else {}
    chain["6_gate"] = {k: rec.get(k) for k in (
        "step", "epoch", "rollout_hard", "selection_hard", "gate_metric", "candidate_gate_score",
        "action", "current_score", "best_score", "best_step", "best_origin", "n_edits_merged",
        "n_edits_ranked", "edit_budget")}
    chain["6_gate"]["step_record"] = rec_path
    return _survival(campaign, arm, step, chain, item, epoch, missing, out_dir)


_SKILL_PROBE = [
    ("the scale-reconciliation instruction", "1000x slip"),
    ("the thousands-to-billions factor", "1e-6"),
    ("an answer-emission instruction", "Answer Emission"),
]


def _survival(campaign: str, arm: str, step: str, chain: dict, item: str, epoch: int,
              missing: list, out_dir: str) -> dict:
    """Did the proposed instruction reach a skill that was kept?

    A retained instruction is one present in a skill version the arm kept, not one present in the
    candidate: the candidate is written before the gate decides. Both are reported.
    """
    arm_dir = os.path.join(campaign, "arm_%s" % arm)
    cand_path = os.path.join(step, "candidate_skill.md")
    candidate = ""
    if os.path.isfile(cand_path):
        with open(cand_path, encoding="utf-8") as f:
            candidate = f.read()
    skills = []
    for p in sorted(glob.glob(os.path.join(arm_dir, "skills", "*.md"))) + \
            [os.path.join(arm_dir, "best_skill.md")]:
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            text = f.read()
        skills.append({"file": os.path.basename(p), "bytes": len(text.encode("utf-8")),
                       "sha256_12": sha(text)[:12],
                       "probes": {name: (needle in text) for name, needle in _SKILL_PROBE}})
    chain["7_survival"] = {
        "candidate_skill": {"file": cand_path, "bytes": len(candidate.encode("utf-8")) or None,
                            "probes": {name: (needle in candidate) for name, needle in _SKILL_PROBE}},
        "retained_skills": skills,
        "the_instruction_is_in_a_retained_skill": any(
            s["probes"]["the scale-reconciliation instruction"] for s in skills),
    }
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "item": item, "arm": arm, "epoch": epoch,
           "campaign": campaign, "chain": chain, "missing_artifacts": missing}

    # the reconstructed analyst views, labelled as a proxy
    rollout = os.path.join(step, "rollout")
    rows = _read_jsonl(os.path.join(rollout, "results.jsonl"))
    failures = [r for r in rows if not float(r.get("hard") or 0)]
    named = [r for r in failures if str(r.get("id")) == item]
    minibatch = named + [r for r in failures if str(r.get("id")) != item][:7]
    reports = os.path.join(campaign, "arm_%s" % arm, "stage_reports")
    os.makedirs(out_dir, exist_ok=True)
    views = {}
    try:
        without = _render_analyst_view(minibatch, os.path.join(rollout, "predictions"), None)
        with_rep = _render_analyst_view(minibatch, os.path.join(rollout, "predictions"), reports)
        for label, text in (("without_report", without), ("with_report", with_rep)):
            p = os.path.join(out_dir, "ANALYST_VIEW_%s_%s.txt" % (item, label))
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            views[label] = {"file": p, "chars": len(text), "sha256_12": sha(text)[:12]}
        views["added_chars"] = views["with_report"]["chars"] - views["without_report"]["chars"]
        views["report_heading_occurrences"] = with_rep.count("#### Stage and Action Report")
        views["minibatch_items"] = [str(r.get("id")) for r in minibatch]
        views["label"] = ("RECONSTRUCTION, not the saved request: the campaign saved no analyst prompt and "
                          "no minibatch composition, so only the block contributed by %s is certainly "
                          "what the optimizer saw" % item)
    except Exception as exc:                                  # noqa: BLE001 - recorded, not raised
        views = {"error": "%s: %s" % (type(exc).__name__, exc),
                 "note": "the analyst view could not be rendered here; the chain above is unaffected"}
        missing.append("the analyst view could not be reconstructed: %s" % exc)
    doc["reconstructed_optimizer_request"] = views
    doc["missing_artifacts"] = missing

    path = os.path.join(out_dir, "STATUS_FEEDBACK_%s.json" % item)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── 5. the six status statements ─────────────────────────

def cmd_statements(campaign: str, inventory_path: str, out_dir: str) -> dict:
    inv = _load(inventory_path)
    s = inv["summary"]
    dev = [x for x in inv["record_sets"] if x["kind"] == "development episodes"]
    val = [x for x in inv["record_sets"] if x["kind"].startswith("validation")]
    checks = []

    def check(statement, verdict, evidence):
        checks.append({"statement": statement, "verdict": verdict, "evidence": evidence})

    check("Answer recovery was tested in twelve complete training episodes on three questions.",
          "HOLDS" if (s["development_records_on_training_questions"] == 12
                      and len(s["distinct_training_questions"]) == 3) else "FAILS",
          {"records_on_training_questions": s["development_records_on_training_questions"],
           "distinct_training_questions": s["distinct_training_questions"],
           "record_sets": [x["record_set"] for x in dev],
           "every_record_corrected": all(x["corrected_client"] for x in dev)})

    v = val[0] if val else {}
    check("The unchanged starting skill was evaluated eight times on 24 validation questions.",
          "HOLDS" if (len(v.get("per_pass") or []) == 8 and v.get("distinct_questions") == 24
                      and v.get("n_records") == 192) else "FAILS",
          {"passes": len(v.get("per_pass") or []), "distinct_questions": v.get("distinct_questions"),
           "records": v.get("n_records"),
           "settings_fingerprints": sorted({f for p in (v.get("per_pass") or [])
                                            for f in p.get("fingerprints", [])}),
           "question_membership": v.get("question_membership")})

    passes = v.get("per_pass") or []
    mean_correct = (sum(p["correct"] for p in passes) / len(passes)) if passes else None
    mean_emitted = (sum(p["emitted"] for p in passes) / len(passes)) if passes else None
    check("The reconciled averages were 9.75/24 correct and 21.25/24 answers emitted.",
          "HOLDS" if (mean_correct == 9.75 and mean_emitted == 21.25) else "FAILS",
          {"mean_correct": mean_correct, "mean_emitted": mean_emitted,
           "per_pass_correct": [p["correct"] for p in passes],
           "per_pass_emitted": [p["emitted"] for p in passes]})

    # a training comparison would be an arm directory produced through the corrected client
    arm_steps = sorted(glob.glob(os.path.join(campaign, "arm_*", "steps", "step_*", "rollout",
                                              "results.jsonl")))
    corrected_arm_rows = []
    for p in arm_steps:
        for r in _read_jsonl(p):
            if str(r.get("answer_recovery_policy")) in CORRECTED_POLICIES:
                corrected_arm_rows.append(p)
                break
    # An ABSENT artifact is UNDECIDED, never a pass: a statement of the form "nothing ran" is only
    # established by looking at the records that would have been written, and finding none of them is
    # a different fact from finding them unchanged.
    check("No corrected-client three-approach training comparison has run.",
          ("UNDECIDED - no arm rollout records were found to examine" if not arm_steps
           else "HOLDS" if not corrected_arm_rows else "FAILS"),
          {"arm_rollout_files_examined": len(arm_steps),
           "arm_rollout_files_with_a_corrected_client_row": corrected_arm_rows,
           "development_record_sets_outside_the_arms": [x["record_set"] for x in dev]})

    heldout = sorted(glob.glob(os.path.join(campaign, "heldout", "*", "results.jsonl")))
    ho = []
    for p in heldout:
        rows = _read_jsonl(p)
        ho.append({"file": os.path.relpath(p, campaign), "n": len(rows),
                   "hard": sum(int(r.get("hard") or 0) for r in rows),
                   "policies": sorted({str(r.get("answer_recovery_policy")) for r in rows})})
    check("No corrected held-out comparison has been completed.",
          ("UNDECIDED - no held-out run records were found to examine" if not ho
           else "HOLDS" if all(set(h["policies"]) == {"None"} for h in ho) else "FAILS"),
          {"heldout_runs": ho,
           "reading": "a run with no recovery policy at all predates amendment A-2, so it ran through "
                      "the client that discarded the answer"})

    # P-1 and P-2 are proposals exactly while the deployed code does not implement them
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "oqa_cc_check.py"), encoding="utf-8") as f:
        check_src = f.read()
    with open(os.path.join(here, "oqa_cc_extract.py"), encoding="utf-8") as f:
        extract_src = f.read()
    p1_not_applied = "later = rev.beyond(i)" in check_src
    p2_not_applied = "if col_observed and row_observed:" in extract_src
    check("Same-message calculation checking and broader use of observed table-unit scope remain "
          "proposals.",
          "HOLDS" if (p1_not_applied and p2_not_applied) else "FAILS",
          {"P1_the_later_window_starts_after_the_operand_message": p1_not_applied,
           "P1_evidence": "oqa_cc_check.check_attempt reads `steps[i:]`, which excludes the operand's "
                          "own message",
           "P2_absence_still_needs_both_nearer_scopes_observed": p2_not_applied,
           "P2_evidence": "oqa_cc_extract.nearer_scale asserts ABSENT only when the column header AND "
                          "the row label were both observed, and an unbound column counts as "
                          "unobserved"})

    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "campaign": campaign,
           "inventory": inventory_path, "statements": checks,
           "all_hold": all(c["verdict"] == "HOLDS" for c in checks),
           "undecided": [c["statement"] for c in checks if c["verdict"].startswith("UNDECIDED")],
           "failing": [c["statement"] for c in checks if c["verdict"] == "FAILS"]}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "STATUS_STATEMENTS.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    inv = sub.add_parser("inventory")
    inv.add_argument("--campaign", required=True)
    inv.add_argument("--frozen-sets", required=True)
    inv.add_argument("--bind-audit", default="")
    inv.add_argument("--s0-manifest", default="")
    inv.add_argument("--out", required=True)

    fb = sub.add_parser("feedback")
    fb.add_argument("--campaign", required=True)
    fb.add_argument("--item", required=True)
    fb.add_argument("--arm", required=True)
    fb.add_argument("--epoch", type=int, required=True)
    fb.add_argument("--out", required=True)

    st = sub.add_parser("statements")
    st.add_argument("--campaign", required=True)
    st.add_argument("--inventory", required=True)
    st.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "inventory":
        doc = cmd_inventory(args.campaign, args.frozen_sets, args.bind_audit,
                            args.s0_manifest, args.out)
        print(json.dumps({"written_to": doc["written_to"], "summary": doc["summary"],
                          "problems": doc["problems"]}, indent=1, default=str))
    elif args.cmd == "feedback":
        doc = cmd_feedback(args.campaign, args.item, args.arm, args.epoch, args.out)
        if doc.get("error"):
            print(json.dumps(doc, indent=1)); return 1
        print(json.dumps({"written_to": doc["written_to"],
                          "gate": doc["chain"]["6_gate"],
                          "survival": doc["chain"]["7_survival"]["the_instruction_is_in_a_retained_skill"],
                          "views": {k: v for k, v in doc["reconstructed_optimizer_request"].items()
                                    if k != "label"},
                          "missing_artifacts": doc["missing_artifacts"]}, indent=1, default=str))
    else:
        doc = cmd_statements(args.campaign, args.inventory, args.out)
        print(json.dumps({"written_to": doc["written_to"], "all_hold": doc["all_hold"],
                          "verdicts": [{c["statement"][:60]: c["verdict"]} for c in doc["statements"]]},
                         indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
