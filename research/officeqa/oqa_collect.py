#!/usr/bin/env python3
"""Collect corrected-client TRAINING evidence: 24 frozen training questions, one attempt each.

Rule OQA-COL-1. The status packet (`OFFICEQA_STATUS_PACKET.md` §9) established the gap this closes: all
twelve corrected-client training episodes scored 1, so there was no record in which the actor wrote
something down and got it wrong, and the connected action-plus-stage prototype had nothing to be measured
on. Six of those twelve were recorded with the provider's thinking default in force, where the permitted
trace is 20-36 characters - the recovered answer block alone - so they could not contain an operand at
all.

WHAT THIS COLLECTS, and what it deliberately is not. One complete attempt on each of the 24 frozen
TRAINING questions, with the starting skill UNCHANGED and `enable_thinking: false`, which is the only
setting measured to leave a checkable trace (575-8,864 characters, 2-66 monetary operands). These are
NEW attempts on PREVIOUSLY USED training questions. They are not an unseen-task evaluation, they are not
a performance measurement of any skill, and the scorer's verdict on them is reported beside answer
emission rather than as a result. Nothing here changes the skill or asks the actor for more explanation.

THE FREEZE IS WRITTEN BEFORE THE FIRST REQUEST. `freeze` records the question manifest, the skill
digest, the code and configuration digests, the model and the generation settings as they will be
transmitted, the tool interface, the existing turn and token limits, the answer-recovery policy, and the
declared boundary between permitted and audit-only fields. `verify` then reads the run's OWN call log
back and checks that the intended settings were the settings sent - a plan is not evidence that a
request carried it.

THE INFORMATION BOUNDARY, declared here and enforced by what the tools read. The learner/checker input
is `conversation.json`: the actor's own messages, the executed tools and their observations. The
provider's alternate reasoning field, the raw HTTP bodies and the usage records live in
`actor_calls.jsonl` and `raw_capture.jsonl` and are AUDIT-ONLY. The narrow `committed_answer` policy may
place its accepted `<answer>...</answer>` block in the permitted record, because that is what the
environment stores and what the campaign's own analyst would have seen; the prose around it is never
promoted. Scoring stays in the evaluator's own location (the result row's `hard` / `soft`), never in a
checker input.

usage:
  oqa_collect.py freeze    --frozen-sets <json> --split3 <dir> --docs <dir> --skill <S0.md>
                           --config <yaml> --out <dir>
  oqa_collect.py verify    --freeze <dir>/COLLECT_FREEZE.json --run <dir> --out <dir>
  oqa_collect.py summarize --freeze <dir>/COLLECT_FREEZE.json --run <dir>
                           [--cc-check <CC_CHECK_descriptive.json>] [--bind-audit <BIND_AUDIT.json>]
                           --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import hashlib
import json
import os
import re
import sys

RULE = "OQA-COL-1"
SCHEMA = "lso.officeqa_collect.v1"

# The one serving control this collection sets, and nothing else. Named so the report can say what was
# transmitted rather than what was intended.
CONDITION = {
    "label": "thinking_off",
    "extra_body": {"enable_thinking": False},
    "why": ("the only setting measured to leave a checkable permitted trace: 575-8,864 characters with "
            "2-66 monetary operands, against 20-36 characters and zero operands under the provider's "
            "thinking default (OFFICEQA_STATUS_PACKET.md section 3b)"),
}
RECOVERY_POLICY = "committed_answer"

# The declared information boundary. Every path a checker or learner may read, and every path it may not.
BOUNDARY = {
    "permitted_learner_and_checker_input": [
        "predictions/<id>/conversation.json - the actor's own messages, the executed tool calls and "
        "their observations, and the committed <answer> block the narrow policy accepted",
        "predictions/<id>/target_system_prompt.txt and target_user_prompt.txt - what the actor was given",
    ],
    "audit_only_never_in_a_checker_input": [
        "actor_calls.jsonl - the transmitted request, the provider's alternate reasoning field and its "
        "length, the usage record, the recovery decision",
        "raw_capture.jsonl - the complete HTTP body, the SDK view and the compatibility message",
    ],
    "evaluator_only": [
        "results.jsonl `hard` / `soft` / `ground_truth` - the scorer's verdict and the answer key stay "
        "in the result row and are never passed to a checker as evidence",
    ],
    "note": ("the narrow committed-answer policy MAY expose its accepted answer block in the permitted "
             "record, because that is exactly what the environment stored and what the campaign's own "
             "analyst received; the reasoning prose around it is never promoted"),
}

# The modules whose source decides what the client sends and what it reads back. Digested so that a
# later reader can tell whether the code moved under a record.
CODE_UNDER_FREEZE = (
    "skillopt/model/common.py",
    "skillopt/envs/officeqa/rollout.py",
    "skillopt/envs/officeqa/tool_runtime.py",
    "scripts/eval_only.py",
)


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_on_path() -> None:
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (repo, os.path.dirname(os.path.abspath(__file__))):
        if p not in sys.path:
            sys.path.insert(0, p)


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


# ───────────────────────── 1. the freeze, before any request ─────────────────────────

def cmd_freeze(frozen_sets: str, split3: str, docs: str, skill: str, config: str, out_dir: str) -> dict:
    """The 24 training questions and every setting, recorded BEFORE the first request is sent."""
    _tree_on_path()
    from skillopt.envs.officeqa.rollout import rollout_settings_fingerprint, _TOOL_SCHEMAS
    from skillopt.envs.officeqa.tool_runtime import resolve_candidate_files, resolve_docs_roots
    import yaml

    sets = json.load(open(frozen_sets, encoding="utf-8"))["sets"]
    train = list(sets["train"]["ids"])
    val, held = set(sets["val"]["ids"]), set(sets["heldout"]["ids"])
    ids = sorted(train)
    problems = []

    # MEMBERSHIP IS CHECKED, not assumed. A question that also appears in the validation or held-out set
    # stops the freeze: collecting on it would contaminate a split this collection must not touch.
    membership = []
    for i in ids:
        where = ["train"] if i in set(train) else []
        if i in val:
            where.append("val")
        if i in held:
            where.append("heldout")
        membership.append({"id": i, "sets": where})
        if where != ["train"]:
            problems.append("%s is in %s, not training only" % (i, where or "no frozen set"))
    if len(ids) != 24:
        problems.append("the frozen training set has %d questions, not 24" % len(ids))

    rows, header = [], None
    for path in sorted(glob.glob(os.path.join(split3, "train", "*.csv"))):
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            for row in reader:
                if str(row.get("uid") or row.get("id") or "").strip() in set(ids):
                    rows.append(row)
    if len(rows) != len(ids):
        problems.append("found %d of %d questions in %s/train" % (len(rows), len(ids), split3))

    # The split this collection runs against: the 24 training questions and nothing else. `val/` and
    # `test/` are created EMPTY, so the dataloader cannot reach another split even if asked.
    split_dir = os.path.join(out_dir, "split")
    for sub in ("train", "val", "test"):
        os.makedirs(os.path.join(split_dir, sub), exist_ok=True)
    csv_path = os.path.join(split_dir, "train", "collect24.csv")
    if rows and header:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            for r in sorted(rows, key=lambda r: str(r.get("uid") or r.get("id"))):
                w.writerow(r)
        for sub in ("val", "test"):
            with open(os.path.join(split_dir, sub, "empty.csv"), "w", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=header).writeheader()

    docs_roots = resolve_docs_roots(docs)
    per_question = []
    for r in sorted(rows, key=lambda r: str(r.get("uid") or r.get("id"))):
        qid = str(r.get("uid") or r.get("id"))
        raw_files = str(r.get("source_files") or "")
        try:
            want = json.loads(raw_files) if raw_files.strip().startswith("[") else (
                [p.strip() for p in raw_files.split(",") if p.strip()])
        except json.JSONDecodeError:
            want = [raw_files]
        resolved = resolve_candidate_files(want, docs_roots)
        per_question.append({
            "id": qid,
            "task_type": r.get("task_type") or r.get("difficulty") or "",
            "question_sha256_12": sha_text(str(r.get("question") or ""))[:12],
            "question_chars": len(str(r.get("question") or "")),
            "source_files": want,
            "resolved_documents": [{"path": p, "sha256_12": sha_file(p)[:12],
                                    "bytes": os.path.getsize(p)}
                                   for p in resolved if os.path.isfile(p)],
            # The ground truth is NOT recorded here. A frozen manifest that carried the answer key would
            # put it in an artifact that gets pasted into a report.
            "ground_truth_recorded": False,
        })

    cfg = yaml.safe_load(open(config, encoding="utf-8")) or {}
    env = cfg.get("env") or {}
    limits = {
        "search_mode": env.get("search_mode", "offline"),
        "use_local_tools": bool(env.get("use_local_tools", True)),
        "max_tool_turns": int(env.get("max_tool_turns", 24)),
        "max_queries_per_turn": int(env.get("max_queries_per_turn", 4)),
        "max_completion_tokens": int(env.get("max_completion_tokens", 16384)),
        "workers": int(env.get("workers", 4)),
    }
    target = (cfg.get("target") or cfg.get("model") or {})
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    code = {}
    for rel in CODE_UNDER_FREEZE:
        p = os.path.join(repo, rel)
        code[rel] = sha_file(p)[:16] if os.path.isfile(p) else "ABSENT"

    doc = {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(),
        "what_this_is": ("new attempts on previously used TRAINING questions, one each, starting skill "
                         "unchanged; not an unseen-task evaluation and not a performance measurement"),
        "ids": ids, "n_questions": len(ids), "attempts_per_question": 1,
        "membership": membership,
        "questions": per_question,
        "split_dir": os.path.abspath(split_dir),
        "collect_csv_sha256_12": sha_file(csv_path)[:12] if os.path.isfile(csv_path) else None,
        "skill": {"path": os.path.abspath(skill), "sha256_12": sha_file(skill)[:12],
                  "bytes": os.path.getsize(skill),
                  "note": "the unchanged starting skill S0; not edited for this collection"},
        "config": {"path": os.path.abspath(config), "sha256_12": sha_file(config)[:12]},
        "code_digests": code,
        "model_and_generation": {
            "model_in_config": target.get("model") or target.get("name") or "(from the environment)",
            "condition": CONDITION,
            "intended_extra_body": CONDITION["extra_body"],
            "note": ("temperature, max_tokens and the tool list come from the config and the adapter; "
                     "`verify` reads them back out of the transmitted requests"),
        },
        "limits": limits,
        "settings_fingerprint": rollout_settings_fingerprint(
            search_mode=limits["search_mode"], use_local_tools=limits["use_local_tools"],
            max_tool_turns=limits["max_tool_turns"],
            max_queries_per_turn=limits["max_queries_per_turn"],
            max_completion_tokens=limits["max_completion_tokens"]),
        "tool_names": [t.get("function", {}).get("name") for t in _TOOL_SCHEMAS],
        "tool_schemas_sha256_12": sha_text(json.dumps(_TOOL_SCHEMAS, sort_keys=True))[:12],
        "docs_dir": os.path.abspath(docs),
        "answer_recovery_policy": RECOVERY_POLICY,
        "information_boundary": BOUNDARY,
        "contract_feedback": "none - the actor receives no report in this collection",
        "problems": problems,
    }
    doc["freeze_sha256_12"] = sha_text(json.dumps(
        {k: v for k, v in doc.items() if k != "utc"}, sort_keys=True, default=str))[:12]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "COLLECT_FREEZE.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── 2. were the intended settings actually transmitted? ─────────────────────────

def cmd_verify(freeze_path: str, run_dir: str, out_dir: str) -> dict:
    """Read the run's OWN call log back and check the settings against the freeze.

    A plan is not evidence that a request carried it. This compares the transmitted model, sampling
    controls, tool flag and `extra_body`, and the recovery policy the client actually applied, against
    what the freeze said they would be. A disagreement is reported, never averaged away.
    """
    fr = json.load(open(freeze_path, encoding="utf-8"))
    calls = _read_jsonl(os.path.join(run_dir, "actor_calls.jsonl"))
    rows = _read_jsonl(os.path.join(run_dir, "results.jsonl"))
    seen = {"model": set(), "temperature": set(), "max_tokens": set(), "tools": set(),
            "extra_body": set(), "policy": set()}
    outcomes, http_bad, recovered = {}, 0, 0
    alt_chars = []
    for c in calls:
        req = c.get("request") or {}
        seen["model"].add(str(req.get("model")))
        seen["temperature"].add(str(req.get("temperature")))
        seen["max_tokens"].add(str(req.get("max_tokens")))
        seen["tools"].add(str(req.get("tools")))
        seen["extra_body"].add(json.dumps(req.get("extra_body"), sort_keys=True))
        rec = c.get("recovery") or {}
        if rec:
            seen["policy"].add(str(rec.get("policy")))
            out = str(rec.get("outcome"))
            outcomes[out] = outcomes.get(out, 0) + 1
            if rec.get("recovered"):
                recovered += 1
            if rec.get("source_chars") is not None:
                alt_chars.append(int(rec["source_chars"]))
        if str(c.get("outcome")) not in ("ok", "None", ""):
            http_bad += 1

    want_body = json.dumps(fr["model_and_generation"]["intended_extra_body"], sort_keys=True)
    checks = [
        {"check": "exactly one model was transmitted", "ok": len(seen["model"]) == 1,
         "observed": sorted(seen["model"])},
        {"check": "exactly one sampling temperature was transmitted", "ok": len(seen["temperature"]) == 1,
         "observed": sorted(seen["temperature"])},
        {"check": "exactly one completion cap was transmitted", "ok": len(seen["max_tokens"]) == 1,
         "observed": sorted(seen["max_tokens"])},
        {"check": "tools were enabled on every request", "ok": seen["tools"] == {"True"},
         "observed": sorted(seen["tools"])},
        {"check": "the intended extra_body was transmitted on EVERY request, and nothing else",
         "ok": seen["extra_body"] == {want_body},
         "intended": want_body, "observed": sorted(seen["extra_body"])},
        {"check": "the narrow recovery policy was in force on every exchange that recorded one",
         "ok": seen["policy"] == {fr["answer_recovery_policy"]},
         "intended": fr["answer_recovery_policy"], "observed": sorted(seen["policy"])},
        {"check": "every frozen question has exactly one result row",
         "ok": sorted(str(r.get("id")) for r in rows) == sorted(fr["ids"]),
         "observed_n": len(rows), "intended_n": len(fr["ids"]),
         "missing": sorted(set(fr["ids"]) - {str(r.get("id")) for r in rows}),
         "unexpected": sorted({str(r.get("id")) for r in rows} - set(fr["ids"]))},
        {"check": "the settings fingerprint on every row matches the freeze",
         "ok": {str(r.get("rollout_settings_fingerprint")) for r in rows} == {fr["settings_fingerprint"]},
         "intended": fr["settings_fingerprint"],
         "observed": sorted({str(r.get("rollout_settings_fingerprint")) for r in rows})},
        {"check": "no provider error went unresolved", "ok": http_bad == 0,
         "observed_non_ok_calls": http_bad},
    ]
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "freeze": freeze_path, "freeze_sha256_12": fr.get("freeze_sha256_12"), "run": run_dir,
           "n_calls": len(calls), "n_rows": len(rows),
           "recovery_outcomes": outcomes, "calls_that_recovered": recovered,
           "alternate_field_chars": {"min": min(alt_chars) if alt_chars else None,
                                     "max": max(alt_chars) if alt_chars else None,
                                     "note": "audit-only; never copied into a checker input"},
           "checks": checks,
           "all_ok": all(c["ok"] for c in checks)}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "COLLECT_VERIFY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── 3. what the permitted trace contains ─────────────────────────

_ARITH = re.compile(r"\d[\d,\.]*\s*(?:[-+*/×÷]|times|divided by|multiplied by|minus|plus)\s*\d")
_FACTOR = re.compile(r"(?:divide[d]?\s+by|multipl(?:y|ied)\s+by|/|\*)\s*1[,\d]*(?:\.\d+)?|1e-?\d+", re.I)
_EQUALS = re.compile(r"=\s*-?\$?\s?\d")
_SCALE_WORD = re.compile(r"\b(thousand|million|billion)s?\b", re.I)


def permitted_trace(pred_root: str, item: str) -> dict:
    """The actor-visible record of one attempt, and the observable features a checker could use.

    The size is the SUM of the actor's message contents: the harness's separators between turns are not
    actor text (OFFICEQA_STATUS_PACKET.md, the counting defect fixed in 855612c).
    """
    path = os.path.join(pred_root, item, "conversation.json")
    out = {"conversation": path, "present": os.path.isfile(path)}
    if not out["present"]:
        return out
    conv = json.load(open(path, encoding="utf-8"))
    msgs, tools, tool_names, obs_chars = [], 0, {}, 0
    for i, m in enumerate(conv):
        if not isinstance(m, dict):
            continue
        kind = m.get("type") or m.get("role")
        if kind == "tool_call":
            tools += 1
            cmd = str(m.get("cmd") or "")
            name = cmd.split("(", 1)[0] or "?"
            tool_names[name] = tool_names.get(name, 0) + 1
            obs_chars += len(str(m.get("obs") or ""))
            continue
        if i == 0:
            continue
        text = str(m.get("content") or "")
        if text:
            msgs.append({"index": i, "chars": len(text)})
    actor = "\n".join(str(m.get("content") or "") for i, m in enumerate(conv)
                      if isinstance(m, dict) and i > 0
                      and (m.get("type") or m.get("role")) != "tool_call")
    out.update({
        "n_entries": len(conv),
        "n_tool_calls": tools,
        "tools_by_name": tool_names,
        "observation_chars": obs_chars,
        "actor_messages_with_text": len(msgs),
        "actor_written_chars": sum(m["chars"] for m in msgs),
        "last_entry_index": len(conv) - 1,
        "arithmetic_written": bool(_ARITH.search(actor)),
        "conversion_factor_stated": bool(_FACTOR.search(actor)),
        "an_equals_result_written": bool(_EQUALS.search(actor)),
        "a_money_scale_named": bool(_SCALE_WORD.search(actor)),
        "answer_block_present": "<answer>" in actor,
    })
    if msgs:
        last = msgs[-1]
        out["final_message_index"] = last["index"]
        out["final_message_chars"] = last["chars"]
        out["final_message_is_the_last_entry"] = (last["index"] == len(conv) - 1)
        out["one_final_message_carries_most_of_the_text"] = (
            len(msgs) == 1 or last["chars"] >= 0.8 * out["actor_written_chars"])
    return out


# ───────────────────────── 4. the 24-row inventory ─────────────────────────

def _cc_index(cc_check: dict) -> dict:
    """item -> what the frozen prototype said about that attempt."""
    index = {}
    for r in (cc_check or {}).get("results") or []:
        preds = r.get("predictions") or r.get("prediction_dir") or ""
        states, truths, convs = {}, {}, {}
        for p in r.get("predictions_list") or r.get("predictions_detail") or []:
            pass
        for p in r.get("preds") or r.get("predictions_made") or []:
            pass
        index[str(r.get("item"))] = {
            "trace_sha256_12": str(r.get("trace_sha256") or "")[:12],
            "problems": r.get("problems") or [],
            "n_predictions": len(r.get("predictions") or []) if isinstance(r.get("predictions"), list) else None,
            "raw": {k: v for k, v in r.items() if k in ("item", "trace_key", "asked_scale")},
            "_dir": preds if isinstance(preds, str) else "",
            "_states": states, "_truths": truths, "_conversions": convs,
        }
    return index


def _bind_index(bind_audit: dict) -> dict:
    """item -> the per-operand causes the existing bind audit computed, including its P-1 column."""
    index = {}
    for group in (bind_audit or {}).get("groups") or []:
        for r in group.get("results") or []:
            cands = r.get("candidates") or []
            mon = [c for c in cands if c.get("verdict") == "MONETARY"]
            causes, match, states, p1 = {}, {}, {}, {}
            holds = 0
            for c in mon:
                causes[str(c.get("primary_cause"))] = causes.get(str(c.get("primary_cause")), 0) + 1
                match[str(c.get("match_status"))] = match.get(str(c.get("match_status")), 0) + 1
                for b in c.get("bindings") or []:
                    if b.get("truth") == "HOLDS":
                        holds += 1
                    st = (b.get("resolution_from_later_steps") or {}).get("state")
                    if st:
                        states[st] = states.get(st, 0) + 1
                    s1 = (b.get("resolution_if_the_operand_message_were_read_too_PROPOSAL_P1")
                          or {}).get("state")
                    if s1:
                        p1[s1] = p1.get(s1, 0) + 1
            index[str(r.get("item"))] = {
                "group": group.get("which"),
                "candidate_numbers": len(cands),
                "monetary_operands": len(mon),
                "operands_with_a_holding_binding": holds,
                "primary_causes": causes,
                "match_status": match,
                "resolution_states_deployed": states,
                "resolution_states_under_P1_diagnostic_only": p1,
                "committed_at_step": (r.get("prefix") or {}).get("committed_at_step"),
                "prefix_steps": (r.get("prefix") or {}).get("n_prefix_steps"),
                "problems": r.get("problems") or [],
            }
    return index


def cmd_summarize(freeze_path: str, run_dir: str, cc_check_path: str, bind_audit_path: str,
                  out_dir: str) -> dict:
    fr = json.load(open(freeze_path, encoding="utf-8"))
    rows = {str(r.get("id")): r for r in _read_jsonl(os.path.join(run_dir, "results.jsonl"))}
    calls = _read_jsonl(os.path.join(run_dir, "actor_calls.jsonl"))
    cc = json.load(open(cc_check_path, encoding="utf-8")) if cc_check_path and os.path.isfile(cc_check_path) else {}
    bind = _bind_index(json.load(open(bind_audit_path, encoding="utf-8"))) if bind_audit_path and os.path.isfile(bind_audit_path) else {}

    # the alternate-field size per episode, from the AUDIT record only, so the report can say how much
    # text existed without any of it reaching a checker input
    alt = {}
    for c in calls:
        rec = c.get("recovery") or {}
        if rec.get("source_chars") is not None:
            rid = str((c.get("response") or {}).get("id"))
            alt[rid] = int(rec["source_chars"])

    pred_root = os.path.join(run_dir, "predictions")
    out_rows, problems = [], []
    for qid in fr["ids"]:
        r = rows.get(qid)
        if r is None:
            problems.append("no result row for %s" % qid)
            out_rows.append({"item": qid, "status": "MISSING"})
            continue
        vis = permitted_trace(pred_root, qid)
        b = bind.get(qid) or {}
        out_rows.append({
            "item": qid,
            "status": "complete",
            "task_type": r.get("task_type"),
            "hard": r.get("hard"), "soft": r.get("soft"),
            "emitted": bool(str(r.get("predicted_answer") or "").strip()),
            "predicted_answer": r.get("predicted_answer"),
            "termination_cause": r.get("termination_cause"),
            "last_finish_reason": r.get("last_finish_reason"),
            "answer_recovered": r.get("answer_recovered"),
            "answer_recovery_source": r.get("answer_recovery_source"),
            "answer_recovery_outcome": r.get("answer_recovery_outcome"),
            "n_turns": r.get("n_turns"), "model_turns_used": r.get("model_turns_used"),
            "fail_reason": r.get("fail_reason"),
            "permitted_trace": vis,
            "frozen_prototype": b,
            "diagnostic_opportunity": _opportunity(r, vis, b),
        })

    done = [x for x in out_rows if x["status"] == "complete"]
    failures = [x for x in done if not float(x.get("hard") or 0)]
    with_arith = [x for x in done if (x["permitted_trace"] or {}).get("arithmetic_written")]
    with_mon = [x for x in done if (x.get("frozen_prototype") or {}).get("monetary_operands")]
    with_holds = [x for x in done if (x.get("frozen_prototype") or {}).get("operands_with_a_holding_binding")]
    summary = {
        "n_frozen": len(fr["ids"]), "n_complete": len(done), "n_missing": len(fr["ids"]) - len(done),
        "correct": sum(int(x.get("hard") or 0) for x in done),
        "emitted": sum(1 for x in done if x["emitted"]),
        "failures": len(failures),
        "failures_whose_trace_carries_arithmetic": sum(
            1 for x in failures if (x["permitted_trace"] or {}).get("arithmetic_written")),
        "failures_with_a_monetary_operand": sum(
            1 for x in failures if (x.get("frozen_prototype") or {}).get("monetary_operands")),
        "failures_with_a_holding_binding": sum(
            1 for x in failures if (x.get("frozen_prototype") or {}).get("operands_with_a_holding_binding")),
        "records_whose_trace_carries_arithmetic": len(with_arith),
        "records_with_a_monetary_operand": len(with_mon),
        "records_with_a_holding_binding": len(with_holds),
        "termination_causes": _tally(done, "termination_cause"),
        "answer_sources": _tally(done, "answer_recovery_source"),
        "alternate_field_chars_audit_only": {"min": min(alt.values()) if alt else None,
                                             "max": max(alt.values()) if alt else None,
                                             "n_exchanges": len(alt)},
        "resolution_states_deployed": _merge(done, "resolution_states_deployed"),
        "resolution_states_under_P1_diagnostic_only": _merge(done, "resolution_states_under_P1_diagnostic_only"),
        "primary_causes": _merge(done, "primary_causes"),
        "match_status": _merge(done, "match_status"),
    }
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "freeze": freeze_path, "freeze_sha256_12": fr.get("freeze_sha256_12"),
           "run": run_dir, "cc_check": cc_check_path or None, "bind_audit": bind_audit_path or None,
           "cc_check_results": len((cc or {}).get("results") or []),
           "records": out_rows, "summary": summary, "problems": problems}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "COLLECT_SUMMARY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def _tally(rows: list, key: str) -> dict:
    out = {}
    for r in rows:
        v = str(r.get(key))
        out[v] = out.get(v, 0) + 1
    return out


def _merge(rows: list, key: str) -> dict:
    out = {}
    for r in rows:
        for k, v in ((r.get("frozen_prototype") or {}).get(key) or {}).items():
            out[k] = out.get(k, 0) + int(v)
    return out


def _opportunity(row: dict, vis: dict, bind: dict) -> dict:
    """What a checker could observably work with on this attempt - and what it could not.

    Deliberately conservative, and the asymmetry is the point: the presence of arithmetic in the trace
    is evidence that a calculation happened, but its ABSENCE is not evidence that none did. The actor may
    have computed silently; with the thinking default in force it demonstrably did. So the field below
    says `not_written_in_the_permitted_trace`, never `no_calculation`.
    """
    vis = vis or {}
    kinds = []
    if not float(row.get("hard") or 0):
        kinds.append("incorrect_answer")
    if not str(row.get("predicted_answer") or "").strip():
        kinds.append("no_answer_emitted")
    if vis.get("arithmetic_written"):
        kinds.append("written_arithmetic")
    if vis.get("conversion_factor_stated"):
        kinds.append("stated_conversion_factor")
    if bind.get("monetary_operands"):
        kinds.append("monetary_operand_extracted")
    if bind.get("operands_with_a_holding_binding"):
        kinds.append("operand_bound_to_a_cell")
    if (vis.get("n_tool_calls") or 0) == 0:
        kinds.append("no_tool_call_at_all")
    if vis.get("one_final_message_carries_most_of_the_text") and vis.get("final_message_is_the_last_entry"):
        kinds.append("same_message_operands_and_answer")
    if str(row.get("last_finish_reason")) == "length":
        kinds.append("truncated_at_the_completion_cap")
    return {
        "kinds": kinds,
        "calculation_in_the_permitted_trace": (
            "written" if vis.get("arithmetic_written") else "not_written_in_the_permitted_trace"),
        "caveat": ("the absence of written arithmetic is NOT evidence that no calculation occurred; "
                   "it is only evidence that none is observable here"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freeze")
    for flag in ("--frozen-sets", "--split3", "--docs", "--skill", "--config", "--out"):
        f.add_argument(flag, required=True)

    v = sub.add_parser("verify")
    v.add_argument("--freeze", required=True)
    v.add_argument("--run", required=True)
    v.add_argument("--out", required=True)

    s = sub.add_parser("summarize")
    s.add_argument("--freeze", required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--cc-check", default="")
    s.add_argument("--bind-audit", default="")
    s.add_argument("--out", required=True)

    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        doc = cmd_freeze(a.frozen_sets, a.split3, a.docs, a.skill, a.config, a.out)
        print(json.dumps({"written_to": doc["written_to"], "freeze": doc["freeze_sha256_12"],
                          "n_questions": doc["n_questions"], "ids": doc["ids"],
                          "settings_fingerprint": doc["settings_fingerprint"],
                          "extra_body": doc["model_and_generation"]["intended_extra_body"],
                          "problems": doc["problems"]}, indent=1, default=str))
        return 1 if doc["problems"] else 0
    if a.cmd == "verify":
        doc = cmd_verify(a.freeze, a.run, a.out)
        print(json.dumps({"written_to": doc["written_to"], "all_ok": doc["all_ok"],
                          "failed": [c for c in doc["checks"] if not c["ok"]],
                          "recovery_outcomes": doc["recovery_outcomes"]}, indent=1, default=str))
        return 0 if doc["all_ok"] else 1
    doc = cmd_summarize(a.freeze, a.run, a.cc_check, a.bind_audit, a.out)
    print(json.dumps({"written_to": doc["written_to"], "summary": doc["summary"],
                      "problems": doc["problems"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
