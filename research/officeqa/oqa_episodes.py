#!/usr/bin/env python3
"""Freeze and summarise the twelve development episodes of the answer-recovery check. Rule OQA-REC-2.

WHAT IS BEING CHECKED. Workstream A's offline work proves the policy against saved bodies. It cannot
show that an EPISODE now completes: every earlier observation of the defect was a single exchange, and
the loop's later turns were never run. So three TRAINING questions are run to completion, twice, under
two conditions that differ in exactly one documented serving control:

    A   the historical generation payload, thinking controls left OMITTED (so their documented defaults
        are in force: enable_thinking true, thinking_budget 4096, reasoning_effort "medium"), with the
        narrow answer-recovery policy ON.
    B   identical, plus `enable_thinking: false`, same recovery policy.

Neither condition receives contract feedback. Everything else - actor model, skill, temperature, token
limit, tool definitions, corpus, turn budget, timeout - is matched by construction: one config file, one
skill file, one split directory, and the conditions differ only in `TARGET_OPENAI_COMPATIBLE_EXTRA_BODY`.

THESE ARE FUNCTIONAL DEVELOPMENT CHECKS, NOT A COMPARISON OF THINKING MODES. Three questions and two
draws cannot rank two generation settings, and the preferred condition is NOT chosen by whichever scores
better here. The scorer's verdict is reported separately from answer emission for exactly that reason.

usage:
  python3 -I research/officeqa/oqa_episodes.py freeze --selection <SELECTION.json> --frozen-sets <json> \
      --split3 <dir> --docs <dir> --skill <S0.md> --config <yaml> --out <dir>
  python3 -I research/officeqa/oqa_episodes.py summarize --plan <dir>/EPISODE_PLAN.json \
      --run A1=<dir> --run A2=<dir> --run B1=<dir> --run B2=<dir> --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import hashlib
import json
import os
import sys

RULE = "OQA-REC-2"
SUPPORTED_TOOLS = ("glob", "read", "grep")
CONDITIONS = {
    # The serving controls each condition adds to the historical payload, and NOTHING else.
    "A": {"label": "historical_payload", "extra_body": None,
          "note": ("thinking controls OMITTED, as the campaign omitted them, so the documented defaults "
                   "are in force")},
    "B": {"label": "thinking_off", "extra_body": {"enable_thinking": False},
          "note": "the one documented control for qwen3-5-4b, verified on the wire by OQA-HTTP-1"},
}


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tree_on_path() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)


# ───────────────────────── freeze ─────────────────────────

def cmd_freeze(selection: str, frozen_sets: str, split3: str, docs: str, skill: str, config: str,
               out_dir: str) -> dict:
    """The three ids, their documents and every setting, frozen BEFORE any request is sent."""
    _tree_on_path()
    from skillopt.envs.officeqa.rollout import rollout_settings_fingerprint, _TOOL_SCHEMAS
    from skillopt.envs.officeqa.tool_runtime import resolve_candidate_files, resolve_docs_roots
    import yaml

    sel = json.load(open(selection, encoding="utf-8"))
    ids = [c["item_id"] for c in sel["selected"]]
    sets = json.load(open(frozen_sets, encoding="utf-8"))["sets"]
    train, val, held = (set(sets["train"]["ids"]), set(sets["val"]["ids"]), set(sets["heldout"]["ids"]))

    # TRAINING MEMBERSHIP IS CHECKED, not assumed. A question that is not a training question, or that
    # appears in the validation or held-out set, stops the freeze.
    membership = []
    problems = []
    for i in ids:
        where = [n for n, s in (("train", train), ("val", val), ("heldout", held)) if i in s]
        membership.append({"id": i, "sets": where})
        if where != ["train"]:
            problems.append(f"{i} is in {where or 'no frozen set'}, not training only")

    rows, header = [], None
    for path in sorted(glob.glob(os.path.join(split3, "train", "*.csv"))):
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            for row in reader:
                if str(row.get("uid") or row.get("id") or "").strip() in ids:
                    rows.append(row)
    if len(rows) != len(ids):
        problems.append(f"found {len(rows)} of {len(ids)} questions in {split3}/train")

    split_dir = os.path.join(out_dir, "split")
    for sub in ("train", "val", "test"):
        os.makedirs(os.path.join(split_dir, sub), exist_ok=True)
    csv_path = os.path.join(split_dir, "train", "episodes.csv")
    if rows and header:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            for r in sorted(rows, key=lambda r: str(r.get("uid") or r.get("id"))):
                w.writerow(r)
        # `val/` and `test/` must exist and must be EMPTY of these questions: the dataloader reads only
        # what it is asked for, and an empty directory cannot leak a split.
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
            "question_sha256": sha_text(str(r.get("question") or "")),
            "question_chars": len(str(r.get("question") or "")),
            "source_files": want,
            "resolved_documents": [{"path": p, "sha256": sha_file(p), "bytes": os.path.getsize(p)}
                                   for p in resolved if os.path.isfile(p)],
            # The ground truth is NOT recorded here. The scorer reads it from the split; a frozen plan
            # that carried the answer key would put it in an artifact that gets pasted into a report.
            "ground_truth_recorded": False,
        })

    cfg = yaml.safe_load(open(config, encoding="utf-8")) or {}
    env = cfg.get("env") or {}
    settings = {
        "search_mode": env.get("search_mode", "offline"),
        "use_local_tools": bool(env.get("use_local_tools", True)),
        "max_tool_turns": int(env.get("max_tool_turns", 24)),
        "max_queries_per_turn": int(env.get("max_queries_per_turn", 4)),
        "max_completion_tokens": int(env.get("max_completion_tokens", 16384)),
        "workers": int(env.get("workers", 4)),
    }
    plan = {
        "rule": RULE, "utc": _utc(),
        "selection_sha256": sel.get("selection_sha256"),
        "ids": sorted(ids), "membership": membership,
        "questions": per_question,
        "split_dir": os.path.abspath(split_dir),
        "episodes_csv_sha256": sha_file(csv_path) if os.path.isfile(csv_path) else None,
        "skill": {"path": os.path.abspath(skill), "sha256": sha_file(skill),
                  "bytes": os.path.getsize(skill)},
        "config": {"path": os.path.abspath(config), "sha256": sha_file(config)},
        "settings": settings,
        "settings_fingerprint": rollout_settings_fingerprint(
            search_mode=settings["search_mode"], use_local_tools=settings["use_local_tools"],
            max_tool_turns=settings["max_tool_turns"],
            max_queries_per_turn=settings["max_queries_per_turn"],
            max_completion_tokens=settings["max_completion_tokens"]),
        "tool_names": [t.get("function", {}).get("name") for t in _TOOL_SCHEMAS],
        "tool_schemas_sha256": sha_text(json.dumps(_TOOL_SCHEMAS, sort_keys=True)),
        "docs_dir": os.path.abspath(docs),
        "conditions": CONDITIONS,
        "reps_per_condition": 2,
        "recovery_policy": "committed_answer",
        "contract_feedback": "none in either condition",
        "problems": problems,
    }
    plan["plan_sha256"] = sha_text(json.dumps(
        {k: v for k, v in plan.items() if k not in ("utc", "plan_sha256")}, sort_keys=True))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "EPISODE_PLAN.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1, sort_keys=True)
    plan["written_to"] = path
    return plan


# ───────────────────────── summarize ─────────────────────────

def _read_jsonl(path: str) -> list:
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _captures(run_dir: str) -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(run_dir, "raw_capture*.jsonl"))):
        out += [r for r in _read_jsonl(path) if r.get("schema") == "lso.raw_exchange.v1"]
    return out


def episode_rows(run_dir: str, label: str) -> list:
    """One row per episode: where its answer came from, what the loop did, and what it cost."""
    rows = _read_jsonl(os.path.join(run_dir, "results.jsonl"))
    caps = _captures(run_dir)
    by_item: dict = {}
    for row in rows:
        item = str(row.get("id"))
        rec = row.get("response_metadata") or {}
        turns = row.get("answer_recovery_turns") or []
        by_item[item] = {
            "run": label, "item": item,
            "answer_emitted": bool(str(row.get("predicted_answer") or "").strip()),
            "answer_source": ("recovered:" + str(row.get("answer_recovery_source"))
                              if row.get("answer_recovered") else
                              ("content" if str(row.get("predicted_answer") or "").strip() else "none")),
            "termination_cause": row.get("termination_cause"),
            "model_turns_used": row.get("model_turns_used"),
            "n_conversation_events": row.get("n_turns"),
            "last_finish_reason": row.get("last_finish_reason"),
            "last_completion_tokens": row.get("last_completion_tokens"),
            # Correctness is the SCORER's business and is reported separately from emission: an episode
            # that now answers may still answer wrongly, and that is not a defect in the client.
            "hard": row.get("hard"), "soft": row.get("soft"),
            "predicted_answer": str(row.get("predicted_answer") or "")[:120],
            "recovery_turns": turns,
            "recovery_outcomes": sorted({str(t.get("outcome")) for t in turns}),
            "fail_reason": str(row.get("fail_reason") or "")[:200],
            "recovery_policy": row.get("answer_recovery_policy"),
        }
    # EVERY captured exchange IS attributed to its episode, by the digest of the first user message -
    # the same identity the OQA-HTTP-1 selection rule used. Without that join the exchange counts could
    # only be reported for the run as a whole.
    user_sha = {}
    for row in rows:
        user_sha[hashlib.sha256(str(row.get("target_user_prompt") or "")
                                .encode("utf-8")).hexdigest()] = str(row.get("id"))
    for ep in by_item.values():
        ep["exchanges"] = []
    unattributed = 0
    for cap in caps:
        item = user_sha.get(((cap.get("request") or {}).get("first_user_sha256")) or "")
        sdk = cap.get("sdk") or {}
        cm = cap.get("compat_message") or {}
        rec = cm.get("answer_recovery") or {}
        entry = {
            "response_id": sdk.get("response_id"),
            "finish_reason": sdk.get("finish_reason"),
            "content_is_null": sdk.get("content_is_null"),
            "content_chars": sdk.get("content_chars"),
            "alternate_field": (sdk.get("metadata") or {}).get("reasoning_field_name") or None,
            "alternate_chars": (sdk.get("metadata") or {}).get("reasoning_content_chars"),
            "recovery_outcome": rec.get("outcome"),
            "recovered": bool(rec.get("recovered")),
            "tool_calls": [tc.get("function", {}).get("name") for tc in (cm.get("tool_calls") or [])],
            "http_status": (cap.get("http") or {}).get("status_code"),
            "completion_tokens": ((sdk.get("metadata") or {}).get("usage") or {}).get("completion_tokens"),
        }
        if item and item in by_item:
            by_item[item]["exchanges"].append(entry)
        else:
            unattributed += 1
    run = {
        "exchanges_not_attributed_to_an_episode": unattributed,
        "run": label, "dir": os.path.abspath(run_dir),
        "n_episodes": len(by_item),
        "n_exchanges_captured": len(caps),
        "exchange_outcomes": {},
        "unsupported_tool_calls": 0,
        "unsupported_tool_names": sorted({
            n for cap in caps for n in [tc.get("function", {}).get("name")
                                        for tc in ((cap.get("compat_message") or {}).get("tool_calls") or [])]
            if n and n not in SUPPORTED_TOOLS}),
        "prompt_tokens": 0, "completion_tokens": 0, "provider_cost_usd": 0.0,
        "truncated_at_cap": 0, "http_non_200": 0,
        "committed_answers_the_old_client_would_have_dropped": 0,
    }
    for cap in caps:
        sdk = cap.get("sdk") or {}
        meta = sdk.get("metadata") or {}
        cm = cap.get("compat_message") or {}
        rec = cm.get("answer_recovery") or {}
        o = str(rec.get("outcome") or "none")
        run["exchange_outcomes"][o] = run["exchange_outcomes"].get(o, 0) + 1
        usage = meta.get("usage") or {}
        run["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        run["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        run["provider_cost_usd"] += float(usage.get("cost_usd") or 0.0)
        if str(sdk.get("finish_reason") or "") == "length":
            run["truncated_at_cap"] += 1
        if int((cap.get("http") or {}).get("status_code") or 200) != 200:
            run["http_non_200"] += 1
        names = [tc.get("function", {}).get("name") for tc in (cm.get("tool_calls") or [])]
        run["unsupported_tool_calls"] += sum(1 for n in names if n and n not in SUPPORTED_TOOLS)
        # THE LOSS COUNT. An exchange with no content, no tool call and a committed answer in an
        # alternate field is exactly what the old client discarded.
        if rec.get("recovered"):
            run["committed_answers_the_old_client_would_have_dropped"] += 1
    run["provider_cost_usd"] = round(run["provider_cost_usd"], 8)
    # WAS A USABLE COMMITTED ANSWER LOST? By construction the policy recovers every unambiguous,
    # complete, TERMINAL block, so a loss can only be a reply the policy looked at and DECLINED while an
    # answer tag was present. Those are counted and named rather than asserted away, because "nothing
    # was lost" is a claim about the data and not about the code.
    declined_with_tag = ("ambiguous_competing_answers", "answer_tag_not_terminal",
                         "incomplete_answer_tag", "empty_answer_payload")
    for ep in by_item.values():
        ep["n_exchanges"] = len(ep["exchanges"])
        ep["declined_though_a_tag_was_present"] = [
            {"response_id": x["response_id"], "outcome": x["recovery_outcome"],
             "finish_reason": x["finish_reason"]}
            for x in ep["exchanges"] if x.get("recovery_outcome") in declined_with_tag]
        ep["n_recovered_exchanges"] = sum(1 for x in ep["exchanges"] if x.get("recovered"))
    run["declined_though_a_tag_was_present"] = sum(
        len(ep["declined_though_a_tag_was_present"]) for ep in by_item.values())
    return [run, sorted(by_item.values(), key=lambda e: e["item"])]


def cmd_summarize(plan_path: str, runs: list, out_dir: str) -> dict:
    plan = json.load(open(plan_path, encoding="utf-8"))
    per_run, episodes = [], []
    for spec in runs:
        label, _, d = spec.partition("=")
        run, eps = episode_rows(d, label)
        per_run.append(run)
        episodes += eps
    emitted = sum(1 for e in episodes if e["answer_emitted"])
    recovered = sum(1 for e in episodes if str(e["answer_source"]).startswith("recovered"))
    doc = {
        "rule": RULE, "utc": _utc(), "plan_sha256": plan.get("plan_sha256"),
        "n_episodes": len(episodes),
        "answers_emitted": emitted,
        "answers_from_recovery": recovered,
        "correct_by_the_existing_scorer": sum(1 for e in episodes if e.get("hard")),
        "by_run": per_run, "episodes": episodes,
        "termination_causes": {},
        "note": ("answer emission and correctness are reported separately on purpose; three questions "
                 "and two draws cannot compare two generation settings, and the preferred condition is "
                 "not chosen from these scores"),
    }
    for e in episodes:
        c = str(e.get("termination_cause"))
        doc["termination_causes"][c] = doc["termination_causes"].get(c, 0) + 1
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "EPISODE_SUMMARY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


# ───────────────────────── records for a descriptive contract check ─────────────────────────

def cmd_records(runs: list, out_path: str) -> dict:
    """A records file naming the NEW episode traces, for a DESCRIPTIVE connected-contract check.

    These traces are not learning records and not the reserved checking records: they are new material
    collected after the semantic bundle was frozen, and a check over them describes what the contract
    says about them. It is not a test of the hypothesis - nothing here was reserved in advance, and
    three questions cannot test anything.
    """
    _tree_on_path()
    # `oqa_trace` is a SIBLING of this file, not a package module, so the repo root is not enough.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import oqa_trace as TR
    rows = []
    for spec in runs:
        label, _, d = spec.partition("=")
        pred = os.path.join(d, "predictions")
        if not os.path.isdir(pred):
            continue
        for item in sorted(os.listdir(pred)):
            if not os.path.isfile(os.path.join(pred, item, "conversation.json")):
                continue
            rows.append({"item": item, "epoch": 0, "arm": "-", "run": label,
                         "predictions": os.path.abspath(pred),
                         "trace_sha256": TR.trace_digest(pred, item)})
    doc = {"rule": RULE, "utc": _utc(), "kind": "descriptive_only",
           "note": ("new episode traces, collected AFTER the semantic bundle was frozen; a check over "
                    "them is descriptive and tests nothing"),
           "descriptive": rows, "learning": [], "checking": [],
           "records_sha256": sha_text(json.dumps(
               [{k: r[k] for k in ("item", "run", "trace_sha256")} for r in rows], sort_keys=True))}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    doc["written_to"] = out_path
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    for flag in ("--selection", "--frozen-sets", "--split3", "--docs", "--skill", "--config", "--out"):
        f.add_argument(flag, required=True)
    s = sub.add_parser("summarize")
    s.add_argument("--plan", required=True)
    s.add_argument("--run", action="append", required=True)
    s.add_argument("--out", required=True)
    rc = sub.add_parser("records")
    rc.add_argument("--run", action="append", required=True)
    rc.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.cmd == "records":
        doc = cmd_records(args.run, args.out)
        print(f"rule {RULE}  {len(doc['descriptive'])} new episode trace(s), digest "
              f"{doc['records_sha256'][:16]}")
        for r in doc["descriptive"]:
            print(f"  {r['run']:3s} {r['item']:8s} trace {r['trace_sha256'][:12]}")
        print(f"wrote {doc['written_to']}")
        return 0

    if args.cmd == "freeze":
        plan = cmd_freeze(args.selection, args.frozen_sets, args.split3, args.docs, args.skill,
                          args.config, args.out)
        print(f"rule {RULE}  plan {plan['plan_sha256'][:16]}  ids {', '.join(plan['ids'])}")
        for m in plan["membership"]:
            print(f"  {m['id']}  frozen sets: {m['sets'] or 'NONE'}")
        for q in plan["questions"]:
            print(f"  {q['id']}  documents {len(q['resolved_documents'])}: "
                  f"{', '.join(os.path.basename(d['path']) + ':' + d['sha256'][:8] for d in q['resolved_documents'])}")
        print(f"  skill {plan['skill']['sha256'][:12]}  config {plan['config']['sha256'][:12]}  "
              f"settings {plan['settings_fingerprint']}")
        if plan["problems"]:
            print("  PROBLEMS:", plan["problems"])
            return 3
        print(f"wrote {plan['written_to']}")
    else:
        doc = cmd_summarize(args.plan, args.run, args.out)
        print(f"rule {RULE}  {doc['n_episodes']} episode(s); answers emitted "
              f"{doc['answers_emitted']}, of which recovered {doc['answers_from_recovery']}; "
              f"correct by the existing scorer {doc['correct_by_the_existing_scorer']}")
        for r in doc["by_run"]:
            print(f"  {r['run']}: {r['n_episodes']} episodes, {r['n_exchanges_captured']} exchanges, "
                  f"recovered {r['committed_answers_the_old_client_would_have_dropped']}, "
                  f"unsupported tool calls {r['unsupported_tool_calls']}, "
                  f"truncated {r['truncated_at_cap']}, ${r['provider_cost_usd']:.6f}")
        for e in doc["episodes"]:
            print(f"    {e['run']:3s} {e['item']:8s} emitted={str(e['answer_emitted']):5s} "
                  f"src={e['answer_source']:22s} hard={e['hard']} turns={e['model_turns_used']} "
                  f"exit={e['termination_cause']}")
        print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
