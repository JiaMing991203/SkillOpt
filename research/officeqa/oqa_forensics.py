#!/usr/bin/env python3
"""Why attempts ended without an answer, and why the contract reports said so little. Rule OQA-FOR-1.

This reads the frozen 2026-11-17 three-arm campaign and spends nothing. Two subcommands:

  noanswer   Classifies every saved selection observation by WHY it produced no answer, following the chain
             from the provider's own reply through the backend conversion, the OfficeQA tool loop and the
             answer extractor. It joins two sources that have never been joined before:
                * the RAW provider responses, in `<run>/actor_calls.jsonl`, which the campaign body
                  switched on via TARGET_OPENAI_COMPATIBLE_CALL_LOG and which carry `finish_reason`,
                  `content`, `reasoning_content` and `usage` per attempt;
                * what SkillOpt SAVED, in `results.jsonl` and `predictions/<id>/conversation.json`.
             The two differ, and the difference is the finding: the row's `last_finish_reason` is empty for
             every rollout in this campaign because `last_response_metadata` is never assigned in the
             local-tools branch of `rollout.py`, and `compat_message_from_chat_message` does not carry a
             finish reason or a reasoning channel through at all.

  contracts  The learner's expressible space against what it learned, the model timeline per epoch, the
             obligation statuses, and where a finding could be lost between the checker and the optimizer.

usage:
  python3 -I research/officeqa/oqa_forensics.py noanswer --campaign <root> --logs <bundle-root> --out <json>
  python3 -I research/officeqa/oqa_forensics.py contracts --campaign <root> --out <json>
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sys

RULE = "OQA-FOR-1"
ARMS = ("A", "B", "C")
# The only tools the actor has (`skillopt/envs/officeqa/rollout.py`, _TOOL_SCHEMAS).
REAL_TOOLS = ("glob", "read", "grep")
SKILL_OPEN = "## Skill\n"
SKILL_CLOSE = "\n\n## Rules"


def sha(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def officeqa_modules():
    """The REAL answer extractor and the REAL scorer, not copies of them.

    The missed-extraction experiment asks what the extractor would have returned from a reply the loop never
    passed to it, and what the scorer would have said. Reimplementing either would make the answer a
    statement about my copy. `python3 -I` drops the script's directory and ignores PYTHONPATH, so the paths
    are inserted here explicitly; the modules are pure (regex, string, collections) and import no backend.
    """
    root = repo_root()
    for p in (root, os.path.join(root, "research", "officeqa")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import importlib.util
    out = {}
    for name, rel, want in (
        ("evaluator", os.path.join("skillopt", "envs", "officeqa", "evaluator.py"), ("evaluate",)),
    ):
        path = os.path.join(root, rel)
        spec = importlib.util.spec_from_file_location(f"oqa_for_{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for w in want:
            if not hasattr(mod, w):
                raise RuntimeError(f"{path} no longer defines {w}")
        out[name] = mod
    return out


# `_extract_answer`'s fallback, quoted from `skillopt/envs/officeqa/rollout.py` because that function cannot
# be loaded without the model package. The tag branch is `_FINAL_RE`; the fallback is the last non-empty
# line. `verify_extractor_quote` below checks this against the live source and FAILS if it has drifted.
_FINAL_RE = re.compile(r"<answer>(.*?)</answer>", re.IGNORECASE | re.DOTALL)
_EXTRACTOR_QUOTE = (
    "    match = _FINAL_RE.search(text)\n"
    "    if match:\n"
    "        return match.group(1).strip()\n"
    "    lines = [line.strip() for line in text.splitlines() if line.strip()]\n"
    "    return lines[-1] if lines else text.strip()\n")


def extract_answer(text: str) -> str:
    match = _FINAL_RE.search(text)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else text.strip()


def verify_extractor_quote() -> dict:
    path = os.path.join(repo_root(), "skillopt", "envs", "officeqa", "rollout.py")
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        return {"checked": False, "problem": str(e)}
    i = src.find("def _extract_answer(text: str) -> str:")
    body = src[i:].split("\n", 1)[1][:len(_EXTRACTOR_QUOTE)] if i >= 0 else ""
    return {"checked": True, "matches": body == _EXTRACTOR_QUOTE,
            "source": os.path.relpath(path, repo_root())}


def skill_from_system(system: str):
    if SKILL_OPEN not in system:
        return None
    body = system.split(SKILL_OPEN, 1)[1]
    if SKILL_CLOSE not in body:
        return None
    return body.rsplit(SKILL_CLOSE, 1)[0]


# ───────────────────────── the raw provider log ─────────────────────────

def call_log_files(roots: list) -> list:
    """Every actor call log under the given roots, with the held-out evaluation EXCLUDED.

    The held-out split is read once under its own release procedure. Nothing in this audit may touch it, so
    the exclusion is by path and is reported in the output rather than left to the caller's glob.
    """
    out, skipped = [], []
    for root in roots:
        for p in sorted(glob.glob(os.path.join(root, "**", "actor_calls.jsonl"), recursive=True)):
            if "heldout" in p or "held_out" in p:
                skipped.append(p)
            else:
                out.append(p)
    return out, skipped


def load_calls(paths: list) -> tuple:
    """Every logged attempt, keyed by (system prompt digest, first user message digest).

    An episode's calls all share that key and grow by two messages a turn, so a record whose request holds
    exactly two messages begins a new episode. Keys repeat across phases within a job (a training rollout
    and a slow-update pair rollout can run the same skill over the same item), which is why the per-item
    join below is made only for the selection evaluations, where the candidate document makes the system
    prompt unique.
    """
    by_key: dict = {}
    stats = {"records": 0, "files": len(paths), "outcomes": {}, "finish_reasons": {},
             "content_kinds": {}, "reasoning_present": 0, "max_tokens_requested": {},
             "response_has_tool_calls_field": 0}
    for path in paths:
        job = os.path.basename(os.path.dirname(path))
        for rec in read_jsonl(path):
            stats["records"] += 1
            stats["outcomes"][rec.get("outcome")] = stats["outcomes"].get(rec.get("outcome"), 0) + 1
            msgs = (rec.get("request") or {}).get("messages") or []
            mt = (rec.get("request") or {}).get("max_tokens")
            stats["max_tokens_requested"][str(mt)] = stats["max_tokens_requested"].get(str(mt), 0) + 1
            resp = rec.get("response") or {}
            if "tool_calls" in resp:
                stats["response_has_tool_calls_field"] += 1
            fr = str(resp.get("finish_reason"))
            stats["finish_reasons"][fr] = stats["finish_reasons"].get(fr, 0) + 1
            c = resp.get("content")
            kind = "null" if c is None else ("empty" if not str(c).strip() else "text")
            stats["content_kinds"][kind] = stats["content_kinds"].get(kind, 0) + 1
            if resp.get("reasoning_content") is not None:
                stats["reasoning_present"] += 1
            if len(msgs) < 2:
                continue
            key = (sha(str(msgs[0].get("content") or "")), sha(str(msgs[1].get("content") or "")))
            u = resp.get("usage") or {}
            by_key.setdefault(key, []).append({
                "job": job, "started_utc": rec.get("started_utc"), "n_messages": len(msgs),
                "attempt": rec.get("attempt"), "outcome": rec.get("outcome"),
                "finish_reason": resp.get("finish_reason"),
                "content": c, "content_kind": kind,
                "reasoning_content": resp.get("reasoning_content"),
                "completion_tokens": (u.get("completion_tokens") if isinstance(u, dict) else None),
                "prompt_tokens": (u.get("prompt_tokens") if isinstance(u, dict) else None),
                "max_tokens": mt,
            })
    for key in by_key:
        by_key[key].sort(key=lambda r: (str(r["started_utc"]), r["n_messages"]))
    return by_key, stats


def episodes_for_key(calls: list) -> list:
    """Split one key's calls into episodes: a two-message request starts a new one."""
    eps, cur = [], []
    for c in calls:
        if c["n_messages"] <= 2 and cur:
            eps.append(cur)
            cur = [c]
        else:
            cur.append(c)
    if cur:
        eps.append(cur)
    return eps


# ───────────────────────── the saved side ─────────────────────────

def gate_evaluations(campaign: str) -> list:
    """The 22 saved evaluations: the shared S0 baseline and the 21 candidates' selection evaluations."""
    out = [{"label": "S0.baseline", "arm": "-", "epoch": 0, "kind": "baseline",
            "dir": os.path.join(campaign, "shared", "val_baseline")}]
    for arm in ARMS:
        root = os.path.join(campaign, f"arm_{arm}")
        if not os.path.isdir(root):
            continue
        for epoch in (1, 2, 3, 4):
            sd = os.path.join(root, "steps", f"step_{epoch:04d}", "selection_eval")
            if os.path.isfile(os.path.join(sd, "results.jsonl")):
                out.append({"label": f"{arm}.{epoch}.step", "arm": arm, "epoch": epoch,
                            "kind": "step", "dir": sd})
            ud = os.path.join(root, "slow_update", f"epoch_{epoch:02d}")
            rec = os.path.join(ud, "slow_result.json")
            if os.path.isfile(rec) and read_json(rec).get("action") != "inject_placeholder":
                sd2 = os.path.join(ud, "selection_eval")
                if os.path.isfile(os.path.join(sd2, "results.jsonl")):
                    out.append({"label": f"{arm}.{epoch}.slow", "arm": arm, "epoch": epoch,
                                "kind": "slow", "dir": sd2})
    return out


def conversation_facts(pred_dir: str) -> dict:
    """What the saved conversation says about one attempt, with nothing inferred.

    `conversation.json` is [the user prompt] then, per turn, one `{"type": "message"}` entry followed by one
    `{"type": "tool_call"}` entry per tool the turn requested. So a message entry followed by a tool_call
    entry IS a turn that requested a tool, which is the case the loop never checks for `<answer>`.
    """
    path = os.path.join(pred_dir, "conversation.json")
    if not os.path.isfile(path):
        return {"present": False}
    conv = read_json(path)
    turns = []           # one per assistant message: (content, [tool names])
    for i, e in enumerate(conv):
        if not isinstance(e, dict) or e.get("type") != "message":
            continue
        names, unknown = [], []
        j = i + 1
        while j < len(conv) and isinstance(conv[j], dict) and conv[j].get("type") == "tool_call":
            cmd = str(conv[j].get("cmd") or "")
            obs = str(conv[j].get("obs") or "")
            name = cmd.strip().split("(")[0].strip() or "?"
            names.append(name)
            if "[tool error: unknown tool" in obs or name not in REAL_TOOLS:
                unknown.append(name)
            j += 1
        turns.append({"content": str(e.get("content") or ""), "tools": names, "unknown_tools": unknown})
    n_tool_calls = sum(len(t["tools"]) for t in turns)
    unknown_calls = [n for t in turns for n in t["unknown_tools"]]
    first_unknown_turn = next((i for i, t in enumerate(turns) if t["unknown_tools"]), None)
    answer_in_a_tool_turn = [i for i, t in enumerate(turns)
                             if t["tools"] and "<answer>" in t["content"].lower()]
    return {
        "present": True, "n_turns": len(turns), "n_tool_calls": n_tool_calls,
        "turns": turns, "unknown_tool_calls": len(unknown_calls),
        "unknown_tool_names": sorted(set(unknown_calls)),
        "first_unknown_turn": first_unknown_turn,
        "turns_after_first_unknown": (len(turns) - 1 - first_unknown_turn
                                      if first_unknown_turn is not None else None),
        "answer_tag_in_a_tool_calling_turn": answer_in_a_tool_turn,
        "last_content": turns[-1]["content"] if turns else "",
        "last_turn_had_tools": bool(turns and turns[-1]["tools"]),
    }


def classify_row(row: dict, conv: dict, ep: list, max_tool_turns: int) -> dict:
    """Why this attempt ended as it did, from the loop's own control flow.

    `rollout.py`'s local-tools loop, in order:
        response = message.content or ""        # a reasoning channel, if any, is already gone
        if message.tool_calls:   run them; continue       # <- an <answer> in THIS turn is never looked at,
                                                         #    and an unknown tool is just another tool call
        if "<answer>" in response.lower():  extract; break
        if turn == max_tool_turns:   fail_reason = "Exceeded tool-turn budget (N)"
        else:                        fail_reason = "...neither produced a tool request nor a final answer"; break
    So a turn with no tool call and no literal `<answer>` ENDS the episode at once, whatever the content
    was; and an episode that is still calling tools on the last allowed turn falls out of the `for` with
    NO fail_reason at all, which the record then fills in with the scorer's message.
    """
    pred = str(row.get("predicted_answer") or "")
    hard = int(row.get("hard") or 0)
    fr = str(row.get("fail_reason") or "")
    low = fr.lower()
    last = ep[-1] if ep else None
    out = {
        "id": row.get("id"), "hard": hard, "predicted": pred[:120],
        "row_fail_reason": fr[:140], "row_last_finish_reason": str(row.get("last_finish_reason") or ""),
        "n_turns_saved": conv.get("n_turns"), "n_tool_calls": conv.get("n_tool_calls"),
        "unknown_tool_calls": conv.get("unknown_tool_calls"),
        "raw_joined": bool(ep),
        "raw_calls": len(ep) if ep else 0,
        "raw_finish_reason": (last or {}).get("finish_reason"),
        "raw_content_kind": (last or {}).get("content_kind"),
        "raw_completion_tokens": (last or {}).get("completion_tokens"),
        "raw_reasoning_present": bool((last or {}).get("reasoning_content") is not None),
        "raw_max_tokens": (last or {}).get("max_tokens"),
    }
    if hard == 1:
        out["category"] = "answered_correct"
        return out
    if pred.strip():
        out["category"] = "answered_wrong"
        return out

    # No answer was produced. Which branch ended it?
    if "turn budget" in low:
        out["category"] = "turn_budget_exhausted_recorded"
    elif low.startswith("error:"):
        out["category"] = "exception"
    elif "neither produced a tool request nor a final answer" in low:
        # the loop broke on a turn with no tool call and no literal tag. The RAW reply says which kind.
        k = out["raw_content_kind"]
        if not out["raw_joined"]:
            out["category"] = "silent_turn_raw_unavailable"
        elif str(out["raw_finish_reason"]) == "length":
            out["category"] = "completion_truncated_at_cap"
        elif k in ("null", "empty"):
            out["category"] = ("empty_reply_after_generating"
                               if (out["raw_completion_tokens"] or 0) > 0 else "empty_reply_no_tokens")
        else:
            txt = str((last or {}).get("content") or "")
            out["category"] = ("prose_with_tag_but_unmatched" if "<answer" in txt.lower()
                               else "prose_without_answer_tag")
    elif conv.get("present") and conv.get("n_turns") and conv["n_turns"] >= max_tool_turns \
            and conv.get("last_turn_had_tools"):
        out["category"] = "turn_budget_exhausted_unrecorded"
    else:
        out["category"] = "no_answer_unexplained"
    return out


def cmd_noanswer(campaign: str, log_roots: list) -> dict:
    paths, skipped = call_log_files(log_roots)
    by_key, log_stats = load_calls(paths)
    cfg = next((read_json(os.path.join(campaign, f"arm_{a}", "config.json"))
                for a in ("B",) + ARMS
                if os.path.isfile(os.path.join(campaign, f"arm_{a}", "config.json"))), {})
    max_tool_turns = int(cfg.get("max_tool_turns") or 24)
    MODS = officeqa_modules()
    evaluate = MODS["evaluator"].evaluate

    per_eval, pooled = {}, {}
    unsupported = {"episodes": 0, "by_outcome": {}, "examples": [], "total_calls": 0}
    missed = {"candidates": 0, "would_score": 0, "examples": []}
    key_collisions = 0
    for ev in gate_evaluations(campaign):
        rows = read_jsonl(os.path.join(ev["dir"], "results.jsonl"))
        pdir = os.path.join(ev["dir"], "predictions")
        counts, items = {}, []
        for row in rows:
            conv = conversation_facts(os.path.join(pdir, str(row["id"])))
            key = (sha(str(row.get("target_system_prompt") or "")),
                   sha(str(row.get("target_user_prompt") or "")))
            eps = episodes_for_key(by_key.get(key, []))
            if len(eps) > 1:
                key_collisions += 1
            ep = eps[-1] if eps else []
            rec = classify_row(row, conv, ep, max_tool_turns)
            rec["eval"] = ev["label"]
            counts[rec["category"]] = counts.get(rec["category"], 0) + 1
            pooled[rec["category"]] = pooled.get(rec["category"], 0) + 1
            items.append(rec)

            if conv.get("present") and conv.get("unknown_tool_calls"):
                unsupported["episodes"] += 1
                unsupported["total_calls"] += conv["unknown_tool_calls"]
                o = rec["category"]
                unsupported["by_outcome"][o] = unsupported["by_outcome"].get(o, 0) + 1
                if len(unsupported["examples"]) < 8:
                    unsupported["examples"].append(
                        {"eval": ev["label"], "id": row["id"], "names": conv["unknown_tool_names"],
                         "calls": conv["unknown_tool_calls"],
                         "turn_of_first": conv["first_unknown_turn"],
                         "turns_after": conv["turns_after_first_unknown"],
                         "outcome": rec["category"]})

            # What the extractor would have returned from a reply the loop never handed it.
            if rec["category"] in ("prose_without_answer_tag", "completion_truncated_at_cap",
                                  "prose_with_tag_but_unmatched"):
                txt = str((ep[-1] if ep else {}).get("content") or "") or conv.get("last_content", "")
                if txt.strip():
                    guess = extract_answer(txt)
                    sc = evaluate(guess, str(row.get("ground_truth") or ""))
                    missed["candidates"] += 1
                    if int(sc.get("em") or 0) == 1:
                        missed["would_score"] += 1
                    if len(missed["examples"]) < 10:
                        missed["examples"].append(
                            {"eval": ev["label"], "id": row["id"], "category": rec["category"],
                             "ground_truth": str(row.get("ground_truth") or "")[:40],
                             "fallback_extraction": guess[:120], "em": int(sc.get("em") or 0),
                             "f1": round(float(sc.get("f1") or 0.0), 3)})
        per_eval[ev["label"]] = {"arm": ev["arm"], "epoch": ev["epoch"], "kind": ev["kind"],
                                 "n": len(rows), "counts": counts, "items": items}
    return {
        "rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campaign": campaign, "max_tool_turns": max_tool_turns,
        "call_logs": {"files_read": paths, "files_skipped_heldout": skipped, **log_stats},
        "extractor_quote_check": verify_extractor_quote(),
        "per_evaluation": per_eval, "pooled": pooled,
        "unsupported_tool_episodes": unsupported,
        "missed_extraction_experiment": missed,
        "key_collisions": key_collisions,
        "what_the_records_cannot_settle": [
            "The row field `last_finish_reason` is empty for every rollout in this campaign: "
            "`rollout.py` only assigns `last_response_metadata` in the custom-search, azure-search and "
            "no-local-tools branches, and the local-tools branch this campaign used never touches it. A "
            "count of truncations taken from that field is a count of nothing.",
            "`compat_message_from_chat_message` (skillopt/model/common.py) builds the message the loop "
            "sees from `content` and `tool_calls` only, so `finish_reason` and `reasoning_content` are "
            "dropped before the OfficeQA loop can record them.",
            "The call log's `response` record has no `tool_calls` field, so which tools a reply requested "
            "is recovered from the SAVED conversation, not from the raw reply.",
            "Job 66272737 (the shared epoch-1 rollout and the S0 selection baseline) wrote no "
            "actor_calls.jsonl, so for S0's 24 observations there is no raw provider reply: the reason a "
            "turn was silent cannot be separated into an empty reply, prose, or a truncation for those.",
        ],
    }


# ───────────────────────── part 2: the contract machinery ─────────────────────────

def lc_modules():
    """The learner's own vocabulary and template set, loaded rather than retyped.

    The question "what could the learner express?" is answered by VERBS x TEMPLATES x polarity in
    `oqa_lc_vocab.py` and `oqa_lc_learn.py`. Those files are the definition; a second copy here would make
    the answer a statement about the copy. The modules insert their own directory into sys.path when
    imported, but the FIRST import needs it there already, which `python3 -I` has removed.
    """
    d = os.path.join(repo_root(), "research", "officeqa")
    if d not in sys.path:
        sys.path.insert(0, d)
    import importlib
    vocab = importlib.import_module("oqa_lc_vocab")
    learn = importlib.import_module("oqa_lc_learn")
    return vocab, learn


def expressible_space() -> dict:
    """Every contract the deterministic proposer could ever put forward, and what each one would say."""
    vocab, learn = lc_modules()
    cands = []
    for verb in sorted(vocab.VERBS):
        for atom in learn.TEMPLATES.get(verb, ()):
            for value in (True, False):
                cands.append({"verb": verb, "atom": atom, "polarity": bool(value),
                              "reads": f"a successful {verb}(...) returns: "
                                       f"{'' if value else 'not '}{atom}",
                              "atom_basis": vocab.ATOMS[atom][1]})
    return {
        "vocab_version": vocab.VOCAB_VERSION,
        "vocab_digest": vocab.vocab_digest(),
        "verbs": sorted(vocab.VERBS),
        "atoms": {a: {"reading": v[0], "basis": v[1]} for a, v in vocab.ATOMS.items()},
        "atoms_in_the_template_set": {v: list(learn.TEMPLATES.get(v, ())) for v in sorted(vocab.VERBS)},
        "atoms_excluded_from_templates": sorted(set(vocab.ATOMS) - set(
            a for v in vocab.VERBS for a in learn.TEMPLATES.get(v, ()))),
        "assumptions": {a: {"reading": v[0], "basis": v[1]} for a, v in vocab.ASSUMPTIONS.items()},
        "directly_testable_assumptions": list(vocab.DIRECTLY_TESTABLE),
        "min_items_for_acceptance": learn.MIN_ITEMS,
        "n_expressible_contracts": len(cands),
        "expressible_contracts": cands,
        "contract_kinds_possible": ["effect"],
        "note": ("Every atom is 0-ary and is evaluated on ONE tool observation's text, and an `effect` "
                 "contract takes no condition. Nothing about the actor's own output - whether it answered, "
                 "what period or unit it used, what arithmetic it did - is expressible here at all."),
    }


def model_records(campaign: str) -> dict:
    """Every frozen model in the campaign, with what it was learned from and when."""
    out = {}
    c = os.path.join(campaign, "contracts")
    for path in [os.path.join(c, "model_M0.json")] + sorted(
            glob.glob(os.path.join(c, "arm_*", "model_arm*_epoch*.json"))):
        if not os.path.isfile(path):
            continue
        d = read_json(path)
        contracts = []
        for k in d.get("contracts") or []:
            ev = k.get("evidence") or {}
            contracts.append({
                "id": k.get("id"), "version": k.get("version"), "status": k.get("status"),
                "kind": k.get("kind"), "verb": k.get("verb"),
                "reads": _render_contract(k),
                "n_support_ok": ev.get("n_support_ok"), "n_counter": ev.get("n_counter"),
                "undetermined": ev.get("undetermined"),
                "items_supporting": len(ev.get("items_supporting") or []),
                "reason": str(k.get("reason") or "")[:220],
                "withdraw_reason": str(k.get("refute_reason") or k.get("retire_reason") or "")[:220],
                "epoch": k.get("epoch"), "source": k.get("source"),
            })
        out[os.path.relpath(path, campaign)] = {
            "sha256": d.get("sha256"), "version": d.get("version"), "epoch": d.get("epoch"),
            "frozen_utc": d.get("frozen_utc"), "parent_sha256": d.get("parent_sha256"),
            "assumptions": d.get("assumptions"), "evidence_scope": d.get("evidence_scope"),
            "n_evidence_items": len(d.get("evidence_items") or []),
            "evidence_items": sorted(d.get("evidence_items") or []),
            "evidence_items_are_ids_not_traces": True,
            "decisions": [{k2: (str(v)[:200] if not isinstance(v, (int, float, bool, type(None))) else v)
                           for k2, v in dec.items() if k2 != "evidence"}
                          for dec in (d.get("decisions") or [])],
            "contracts": contracts,
            "mtime_utc": datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc).isoformat(),
        }
    return out


def _render_contract(c: dict) -> str:
    effs = c.get("effects") or []
    parts = []
    for e in effs:
        parts.append(("" if e.get("value") else "not ") + str(e.get("atom")))
    return f"a successful {c.get('verb')}(...) returns: " + ", ".join(parts)


def cmd_contracts(campaign: str) -> dict:
    space = expressible_space()
    models = model_records(campaign)
    evo_path = os.path.join(campaign, "contracts", "MODEL_EVOLUTION.jsonl")
    evolution = read_jsonl(evo_path) if os.path.isfile(evo_path) else []

    # which contracts were ever learned, against what could have been
    learned = {}
    for name, m in models.items():
        for c in m["contracts"]:
            learned.setdefault(c["reads"], {"ids": set(), "statuses": set()})
            learned[c["reads"]]["ids"].add(f"{c['id']}@v{c['version']}")
            learned[c["reads"]]["statuses"].add(c["status"])
    learned_readable = {k: {"ids": sorted(v["ids"]), "statuses": sorted(v["statuses"])}
                        for k, v in learned.items()}
    never = [c["reads"] for c in space["expressible_contracts"] if c["reads"] not in learned]

    # the reports: which model each one cites, and whether the in-sample note could fire
    per_arm = {}
    for arm in ("B", "C"):
        gpath = os.path.join(campaign, f"arm_{arm}", "stage_reports", "GENERATED.jsonl")
        if not os.path.isfile(gpath):
            continue
        recs = read_jsonl(gpath)
        hist_path = os.path.join(campaign, "contracts", f"arm_{arm}", "history.jsonl")
        history = read_jsonl(hist_path) if os.path.isfile(hist_path) else []
        # the trace digests of every attempt the learner was pointed at, by epoch
        learned_traces, learned_dirs = {}, {}
        for h in history:
            ep = int(h.get("epoch") or 0)
            pred = str(h.get("pred") or "")
            learned_dirs.setdefault(ep, set()).add(pred)
            if os.path.isdir(pred):
                for item in sorted(os.listdir(pred)):
                    learned_traces.setdefault(ep, {})[item] = _trace_digest(pred, item)
        shared_e1 = os.path.join(campaign, "shared", "epoch1", "predictions")
        if os.path.isdir(shared_e1):
            for item in sorted(os.listdir(shared_e1)):
                learned_traces.setdefault(1, {})[item] = _trace_digest(shared_e1, item)
            learned_dirs.setdefault(1, set()).add(shared_e1)

        by_epoch = {}
        for r in recs:
            pd = str(r.get("prediction_dir") or "")
            m = re.search(r"step_(\d+)", pd)
            ep = int(m.group(1)) if m else 0
            b = by_epoch.setdefault(ep, {"n": 0, "model_sha256": set(), "model_version": set(),
                                         "items": [], "in_sample_by_id": 0, "in_sample_by_trace": 0,
                                         "trace_not_in_any_learning_corpus": 0,
                                         "violated_forecasts": [], "violated_obligations": [],
                                         "obligation_totals": {}, "diagnoses": {}})
            b["n"] += 1
            b["model_sha256"].add(str(r.get("model_sha256")))
            b["model_version"].add(r.get("model_version"))
            b["items"].append(r.get("id"))
            # the cited model's evidence item list
            cited = next((m2 for m2 in models.values()
                          if str(m2["sha256"]) == str(r.get("model_sha256"))), None)
            if cited and str(r.get("id")) in set(cited["evidence_items"]):
                b["in_sample_by_id"] += 1
            # the decisive test: was THIS TRACE in any corpus the cited model was learned from?
            tsha = str(r.get("trace_sha256") or "")
            cited_epoch = int((cited or {}).get("epoch") or 0)
            seen = {d for e, mp in learned_traces.items() if e <= cited_epoch for d in mp.values()}
            if tsha and tsha in seen:
                b["in_sample_by_trace"] += 1
            elif tsha:
                b["trace_not_in_any_learning_corpus"] += 1
            af = r.get("action_forecasts") or {}
            if int(af.get("violated") or 0) > 0:
                b["violated_forecasts"].append({"id": r.get("id"), "n": af.get("violated"),
                                                "report": r.get("report_sha256", "")[:12]})
            ob = r.get("obligations")
            if isinstance(ob, dict):
                for k2, v in ob.items():
                    if isinstance(v, int):
                        b["obligation_totals"][k2] = b["obligation_totals"].get(k2, 0) + v
                if int(ob.get("VIOLATED") or 0) > 0:
                    b["violated_obligations"].append({"id": r.get("id"), "n": ob.get("VIOLATED")})
            nd = str(r.get("n_distinct_diagnoses"))
            b["diagnoses"][nd] = b["diagnoses"].get(nd, 0) + 1
        for ep, b in by_epoch.items():
            b["model_sha256"] = sorted(b["model_sha256"])
            b["model_version"] = sorted(str(x) for x in b["model_version"])
            b["items"] = sorted(b["items"])
        per_arm[arm] = {"by_epoch": by_epoch,
                        "learning_corpus_dirs": {str(k): sorted(v) for k, v in learned_dirs.items()},
                        "n_learning_traces_by_epoch": {str(k): len(v) for k, v in learned_traces.items()}}

    # how the stage condition resolved each obligation kind, parsed from the reports the optimizer read
    kinds = {}
    for arm in ("B", "C"):
        d = os.path.join(campaign, f"arm_{arm}", "stage_reports")
        if not os.path.isdir(d):
            continue
        per_kind = {}
        for name in sorted(os.listdir(d)):
            if not name.startswith("UID") or not name.endswith(".md"):
                continue
            with open(os.path.join(d, name), encoding="utf-8") as f:
                text = f.read()
            for m in re.finditer(r"^- \*\*(\w+)\*\* \[(\w+)\] (.+?)$", text, re.M):
                state, kind = m.group(1), m.group(2)
                per_kind.setdefault(kind, {})
                per_kind[kind][state] = per_kind[kind].get(state, 0) + 1
            for m in re.finditer(r"the same for (\d+) value\(s\)", text):
                per_kind.setdefault("_grouped_multiplicity", {})
                per_kind["_grouped_multiplicity"][m.group(1)] = \
                    per_kind["_grouped_multiplicity"].get(m.group(1), 0) + 1
        kinds[arm] = per_kind

    return {
        "rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campaign": campaign,
        "expressible_space": space,
        "contracts_ever_learned": learned_readable,
        "expressible_but_never_learned": never,
        "models": models,
        "evolution": evolution,
        "reports_by_arm_and_epoch": per_arm,
        "obligation_kinds_from_the_rendered_reports": kinds,
        "action_only_diagnosis_count_is_hardcoded": {
            "where": "research/officeqa/oqa_reports.py, the record built in build_one_lc",
            "expression": 'len(REPORT.group(chain["ledger"])) if condition != LC_ACTION else 0',
            "consequence": ("`n_distinct_diagnoses` is the literal 0 for every Action-Only report, whatever "
                            "that report contains. It is not a measurement of arm B's output, so "
                            "'arm B produced zero distinct diagnoses' says nothing about arm B."),
        },
        "rendering_path": {
            "stage_groups_printed": "all of them (oqa_lc_report.render loops over group(ledger) without a cap)",
            "subjects_listed_per_group": "at most _MAX_LISTED = 12, then 'and N more'",
            "grouping_key": ("(kind, state, the reason with every number replaced by '#') - so N obligation "
                             "records of one kind and reason collapse to ONE line reading 'the same for N "
                             "value(s)', and n_distinct_diagnoses counts the groups, not the records"),
            "to_the_optimizer": ("skillopt/gradient/reflect.py appends the report text verbatim under its "
                                 "heading into the analyst prompt header; nothing truncates it there"),
        },
    }


def _trace_digest(pred_root: str, item_id: str) -> str:
    """The same digest `oqa_reports.trace_digest` writes into each report record."""
    h = hashlib.sha256()
    for name in ("conversation.json", "target_user_prompt.txt"):
        h.update(b"\x00" + name.encode() + b"\x00")
        try:
            with open(os.path.join(pred_root, item_id, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("noanswer")
    n.add_argument("--campaign", required=True)
    n.add_argument("--logs", action="append", required=True)
    n.add_argument("--out", default="")
    c = sub.add_parser("contracts")
    c.add_argument("--campaign", required=True)
    c.add_argument("--out", default="")
    args = ap.parse_args()

    if args.cmd == "noanswer":
        doc = cmd_noanswer(args.campaign, args.logs)
        cl = doc["call_logs"]
        print(f"rule {RULE}  {cl['files']} call log(s), {cl['records']} raw attempts; "
              f"{len(cl['files_skipped_heldout'])} held-out log(s) skipped")
        print(f"  finish_reason {cl['finish_reasons']}")
        print(f"  content       {cl['content_kinds']}   reasoning present {cl['reasoning_present']}")
        print(f"  extractor quote matches the live source: {doc['extractor_quote_check']}")
        print(f"  pooled over the 22 saved evaluations:")
        for k, v in sorted(doc["pooled"].items(), key=lambda x: -x[1]):
            print(f"    {v:4d}  {k}")
        u = doc["unsupported_tool_episodes"]
        print(f"  unsupported-tool episodes {u['episodes']} ({u['total_calls']} calls): {u['by_outcome']}")
        m = doc["missed_extraction_experiment"]
        print(f"  missed-extraction experiment: {m['would_score']} of {m['candidates']} would have scored")
    else:
        doc = cmd_contracts(args.campaign)
        s = doc["expressible_space"]
        print(f"rule {RULE}  expressible contracts {s['n_expressible_contracts']}, "
              f"ever learned {len(doc['contracts_ever_learned'])}, "
              f"never learned {len(doc['expressible_but_never_learned'])}")
        for arm, d in doc["reports_by_arm_and_epoch"].items():
            for ep in sorted(d["by_epoch"]):
                b = d["by_epoch"][ep]
                print(f"  arm {arm} epoch {ep}: {b['n']} reports  model {[x[:12] for x in b['model_sha256']]} "
                      f"v{b['model_version']}  in-sample by id {b['in_sample_by_id']}, "
                      f"by trace {b['in_sample_by_trace']}, fresh {b['trace_not_in_any_learning_corpus']}")
    if getattr(args, "out", ""):
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True, default=str)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
