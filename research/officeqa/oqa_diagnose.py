#!/usr/bin/env python3
"""Diagnose WHY the proposed skill edits scored badly, from the saved campaign records alone.

Rule OQA-DIAG-1. This tool spends nothing: it reads the 2026-11-17 three-arm campaign directory and
answers four questions from files that already exist.

  gates      Account for every candidate gate: arm, epoch, the exact candidate document, the skill it was
             built from, the threshold it had to beat, its score, and the decision -- and check that the
             document on disk is the document the actor was actually given, and that the actor's settings,
             documents, questions and turn limits were the same ones S0 was measured under.
  questions  The 24 selection questions x (S0 + every candidate): what each one did on each question,
             what was gained, what was lost, and whether a loss was a wrong answer, no answer at all, or
             an execution failure.
  chains     For a named case, the chain from the training observation to the deployed wording and the
             actor's behaviour on the selection set.
  contracts  For arms B and C, what the contract layer actually contributed.

WHY IT IS BUILT THIS WAY. Three things are easy to get wrong when reading a run like this one and each
has its own guard here:

  * The retained `skills/skill_v*.md` files hold only ACCEPTED skills, so reading them tells you nothing
    about 20 of the 21 candidates. The candidates are in `steps/step_000N/candidate_skill.md` and
    `slow_update/epoch_0N/candidate_skill.md`, and this tool proves each file is the evaluated document
    by finding it verbatim inside the `target_system_prompt` of all 24 of its own selection rollouts.

  * A score difference is only evidence about a skill if nothing else moved. So every evaluation is
    checked against the shared S0 baseline for the same question ids, byte-identical user prompts (which
    carry the documents, the oracle pages and the round policy), the same tool setting, and the same
    non-skill system scaffold. Differences are reported, not assumed away.

  * `hard` is exact-match, so a blank prediction and a wrong number score the same 0. They are not the
    same failure, and the gate cannot tell them apart. This tool does.

usage:
  python3 -I research/officeqa/oqa_diagnose.py gates     --campaign <root> --out <root>/diagnostic/DIAG_GATES.json
  python3 -I research/officeqa/oqa_diagnose.py questions --campaign <root> --out <root>/diagnostic/DIAG_QUESTIONS.json
  python3 -I research/officeqa/oqa_diagnose.py contracts --campaign <root> --out <root>/diagnostic/DIAG_CONTRACTS.json
  python3 -I research/officeqa/oqa_diagnose.py chains    --campaign <root> --case B.2.step --out <...>.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

RULE = "OQA-DIAG-1"
ARMS = ("A", "B", "C")
N_SEL = 24

# The one place the skill is spliced into the actor's system prompt
# (`skillopt/envs/officeqa/prompts/rollout_system.md`, line 3: `{skill_section}## Rules`), and the one
# shape `_build_system` gives it: `## Skill\n{skill.strip()}\n\n`.
SKILL_OPEN = "## Skill\n"
SKILL_CLOSE = "\n\n## Rules"


def sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def skill_from_system(system: str) -> str | None:
    """The skill document the actor was given, recovered from its own system prompt.

    This is the provenance route: the system prompt embeds the skill verbatim, so the trace itself says
    which document produced it. No file on disk has to be trusted for this.
    """
    if SKILL_OPEN not in system or SKILL_CLOSE not in system:
        return None
    body = system.split(SKILL_OPEN, 1)[1]
    if SKILL_CLOSE not in body:
        return None
    return body.rsplit(SKILL_CLOSE, 1)[0]


def scaffold_of(system: str) -> str:
    """The system prompt with the skill replaced by a marker: everything the skill did NOT control."""
    s = skill_from_system(system)
    if s is None:
        return system
    return system.replace(SKILL_OPEN + s + SKILL_CLOSE, SKILL_OPEN + "<SKILL>" + SKILL_CLOSE, 1)


def any_config(campaign: str) -> dict:
    """An arm's config. Arm B is preferred only because it is the middle arm; any arm will do.

    The three arms' configs are compared digest-by-digest in `gates`, so reading one here is not an
    assumption that they agree - it is checked there, and reported if they do not.
    """
    for arm in ("B",) + ARMS:
        p = os.path.join(campaign, f"arm_{arm}", "config.json")
        if os.path.isfile(p):
            return read_json(p)
    return {}


# ───────────────────────── the gate list ─────────────────────────

def gate_specs(campaign: str) -> list[dict]:
    """Every candidate gate, in the order the trainer ran it: step E, then epoch E's slow update.

    Epoch 1's slow update is `inject_placeholder` -- it writes the empty marker block and evaluates
    nothing -- so it is not a gate and is not counted as one.
    """
    out: list[dict] = []
    for arm in ARMS:
        root = os.path.join(campaign, f"arm_{arm}")
        if not os.path.isdir(root):
            continue
        for epoch in (1, 2, 3, 4):
            sdir = os.path.join(root, "steps", f"step_{epoch:04d}")
            if os.path.isfile(os.path.join(sdir, "step_record.json")):
                out.append({"arm": arm, "epoch": epoch, "kind": "step",
                            "label": f"{arm}.{epoch}.step", "dir": sdir})
            udir = os.path.join(root, "slow_update", f"epoch_{epoch:02d}")
            rec = os.path.join(udir, "slow_result.json")
            if os.path.isfile(rec):
                r = read_json(rec)
                if r.get("action") == "inject_placeholder":
                    continue
                out.append({"arm": arm, "epoch": epoch, "kind": "slow",
                            "label": f"{arm}.{epoch}.slow", "dir": udir})
    return out


def eval_dir(spec: dict) -> str:
    return os.path.join(spec["dir"], "selection_eval")


def candidate_path(spec: dict) -> str:
    return os.path.join(spec["dir"], "candidate_skill.md")


def input_rollout_results(spec: dict) -> str:
    """The rollout whose system prompt carries the skill the candidate was built FROM.

    An ordinary step builds its candidate by applying the ranked edits to the skill that step's own
    training rollout ran under. A slow update builds its candidate from the skill in force after that
    step, which is what `rollout_curr` ran under.
    """
    if spec["kind"] == "step":
        return os.path.join(spec["dir"], "rollout", "results.jsonl")
    return os.path.join(spec["dir"], "rollout_curr", "results.jsonl")


# ───────────────────────── outcome classification ─────────────────────────

NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def numbers(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def numeric_relation(predicted: str, truth: str) -> str:
    """How a wrong answer relates to the right one, as far as arithmetic alone can say.

    Mechanical only. It says `scale_1e3` when the prediction is a thousand times the truth, which is
    what a millions/thousands unit error looks like; it does NOT say the actor made a unit error, which
    would need the trace. Anything it cannot place is `unrelated_value`, never a guess.
    """
    p, g = numbers(predicted), numbers(truth)
    if not predicted.strip():
        return "no_number_predicted"
    if not p or not g:
        return "non_numeric"
    if len(p) != len(g):
        return "wrong_value_count"
    rels = set()
    for a, b in zip(p, g):
        if a == b:
            rels.add("equal")
        elif b != 0 and abs(a + b) < 1e-12:
            rels.add("sign_flipped")
        elif b != 0 and abs(a - b) <= abs(b) * 1e-4:
            rels.add("rounding")
        elif b != 0:
            r = a / b
            placed = False
            for k in (6, 3, 2, 1):
                for cand, name in ((10.0 ** k, f"scale_1e{k}"), (10.0 ** -k, f"scale_1e-{k}")):
                    if abs(r - cand) <= abs(cand) * 1e-6:
                        rels.add(name)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                rels.add("unrelated_value")
        else:
            rels.add("unrelated_value")
    if rels == {"equal"}:
        return "numerically_equal_scored_wrong"
    if rels <= {"equal", "rounding"}:
        return "rounding"
    if len(rels) == 1:
        return rels.pop()
    return "mixed(" + ",".join(sorted(rels)) + ")"


def classify(row: dict, max_turns: int) -> dict:
    """What happened on one question, in the three categories `hard` cannot express.

    `agent_ok` is NOT a health flag for this purpose. `rollout.py` sets `agent_ok = not fail_reason` on the
    EXECUTION-level reason, and then writes a scorer-level reason into the `fail_reason` FIELD when the
    answer was merely wrong -- so `agent_ok` is False for the commonest outcome in this campaign, an actor
    that ended a turn with neither a tool call nor an `<answer>`. Reading `not agent_ok` as "execution
    error" put 16 of S0's 24 questions in the wrong box on the first pass. The categories here come from
    the reason text, which is exact:

      wrong_answer       the actor committed an answer and the scorer rejected it
      no_answer          nothing to score: a silent turn, or an answer that parsed empty
      execution_failure  the run itself ran out: the tool-turn budget, or an exception
      correct            exact match

    Over this campaign's 528 selection observations the shares are 107 / 381 / 3 / 37, which is why the
    distinction matters: the gate metric treats the first three alike.
    """
    pred = str(row.get("predicted_answer") or "")
    hard = int(row.get("hard") or 0)
    agent_ok = bool(row.get("agent_ok", True))
    fr = str(row.get("fail_reason") or "")
    finish = str(row.get("last_finish_reason") or "")
    n_turns = int(row.get("n_turns") or 0)
    low = fr.lower()
    blank = not pred.strip()
    if hard == 1:
        outcome, why = "correct", ""
    elif low.startswith("error:"):
        outcome, why = "execution_failure", "exception"
    elif "turn budget" in low or "turn-turn" in low or "ended without" in low:
        outcome, why = "execution_failure", "turn_budget_exceeded"
    elif "neither produced a tool request nor a final answer" in low \
            or "neither produced search queries nor a final answer" in low:
        outcome, why = "no_answer", "silent_turn"
    elif blank:
        outcome, why = "no_answer", ("empty_parse" if agent_ok else "no_answer_unexplained")
    else:
        outcome, why = "wrong_answer", ""
    return {
        "outcome": outcome, "why": why,
        "predicted": pred[:200], "hard": hard, "soft": float(row.get("soft") or 0.0),
        "n_turns": n_turns, "finish_reason": finish, "agent_ok": agent_ok,
        "fail_reason": fr[:200],
        "at_turn_cap": n_turns >= max_turns,
        "truncated": finish == "length",
        "relation": (numeric_relation(pred, str(row.get("ground_truth") or ""))
                     if outcome in ("wrong_answer", "no_answer", "execution_failure") else ""),
    }


# ───────────────────────── trace features ─────────────────────────

CMD = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# The only tools the actor has (`skillopt/envs/officeqa/rollout.py`, the tool spec at the top of the file).
# Anything else it "calls" is a tool that does not exist -- the slow-update guidance in several arms claims
# the actor invents an `answer` tool and then goes silent, and this is how that claim gets checked.
REAL_TOOLS = ("glob", "read", "grep")


def trace_features(pred_dir: str) -> dict:
    """What the actor DID, from its saved conversation: which files it touched and how it used tools.

    Only what the records settle. `conversation.json` holds the user prompt, then alternating assistant
    messages and `{"type": "tool_call", "cmd": ..., "obs": ...}` entries, so the tool name, the file path
    and repeated identical calls are all exact. Nothing here infers a reason.
    """
    path = os.path.join(pred_dir, "conversation.json")
    if not os.path.isfile(path):
        return {"present": False}
    conv = read_json(path)
    calls, tools, paths, pats = [], {}, set(), []
    for entry in conv:
        if not isinstance(entry, dict) or entry.get("type") != "tool_call":
            continue
        cmd = str(entry.get("cmd") or "")
        calls.append(cmd)
        m = CMD.match(cmd)
        name = m.group(1) if m else "?"
        tools[name] = tools.get(name, 0) + 1
        for pm in re.finditer(r"path\s*=\s*'([^']*)'", cmd):
            paths.add(os.path.basename(pm.group(1)))
        for pm in re.finditer(r"pattern\s*=\s*'([^']*)'", cmd):
            pats.append(pm.group(1))
        obs = str(entry.get("obs") or "")
        entry_empty = ("[no matches]" in obs) or (not obs.strip())
        if entry_empty:
            tools["__empty_obs"] = tools.get("__empty_obs", 0) + 1
    dupes = len(calls) - len(set(calls))
    invented = {k: v for k, v in tools.items()
                if k not in REAL_TOOLS and not k.startswith("__")}
    assistant_text = [str(e.get("content") or "") for e in conv
                      if isinstance(e, dict) and e.get("type") == "message"]
    return {
        "present": True,
        "n_tool_calls": len(calls),
        "n_repeated_calls": dupes,
        "tools": {k: v for k, v in sorted(tools.items())},
        "files_touched": sorted(paths),
        "n_distinct_patterns": len(set(pats)),
        "invented_tool_calls": invented,
        "n_empty_observations": tools.get("__empty_obs", 0),
        "assistant_chars": sum(len(t) for t in assistant_text),
        "last_assistant_chars": len(assistant_text[-1]) if assistant_text else 0,
    }


# ───────────────────────── subcommand: gates ─────────────────────────

def cmd_gates(campaign: str) -> dict:
    base_res = os.path.join(campaign, "shared", "val_baseline", "results.jsonl")
    base_rows = read_jsonl(base_res)
    base_by_id = {r["id"]: r for r in base_rows}
    base_ids = sorted(base_by_id)
    base_sys = {r["id"]: str(r.get("target_system_prompt") or "") for r in base_rows}
    base_usr = {r["id"]: str(r.get("target_user_prompt") or "") for r in base_rows}
    base_hard = sum(int(r.get("hard") or 0) for r in base_rows)
    s0_text = skill_from_system(base_sys[base_ids[0]])
    base_scaffolds = {sha(scaffold_of(s)) for s in base_sys.values()}

    cfgs, cfg_digests = {}, {}
    for arm in ARMS:
        p = os.path.join(campaign, f"arm_{arm}", "config.json")
        if os.path.isfile(p):
            cfgs[arm] = read_json(p)
            cfg_digests[arm] = sha(json.dumps(cfgs[arm], sort_keys=True))
    ref = cfgs.get("B") or (list(cfgs.values())[0] if cfgs else {})
    cfg_diffs = {}
    for arm, c in cfgs.items():
        d = {k: (ref.get(k), c.get(k)) for k in set(ref) | set(c) if ref.get(k) != c.get(k)}
        if d:
            cfg_diffs[arm] = d
    max_turns = int(ref.get("max_tool_turns") or 24)

    # The arms' injected copies of the baseline must be the same bytes, or "S0 = 4/24" is three numbers.
    base_copies = {}
    for arm in ARMS:
        p = os.path.join(campaign, f"arm_{arm}", "selection_eval_baseline", "results.jsonl")
        base_copies[arm] = sha(open(p, "rb").read()) if os.path.isfile(p) else None
    with open(base_res, "rb") as f:
        base_digest = sha(f.read())

    specs = gate_specs(campaign)
    gates: list[dict] = []
    state = {arm: {"current": base_hard / len(base_rows), "best": base_hard / len(base_rows)}
             for arm in ARMS}
    for i, spec in enumerate(specs, 1):
        arm = spec["arm"]
        cpath = candidate_path(spec)
        crec: dict = {"ordinal": i, "arm": arm, "epoch": spec["epoch"], "kind": spec["kind"],
                      "label": spec["label"]}
        cand_text = read_text(cpath) if os.path.isfile(cpath) else None
        if cand_text is None:
            crec["problem"] = f"no candidate_skill.md at {cpath}"
            gates.append(crec)
            continue
        crec["candidate_path"] = os.path.relpath(cpath, campaign)
        crec["candidate_sha256"] = sha(cand_text)
        crec["candidate_bytes"] = len(cand_text.encode("utf-8"))

        if spec["kind"] == "step":
            rec = read_json(os.path.join(spec["dir"], "step_record.json"))
            crec["trainer_candidate_hash"] = rec.get("candidate_hash")
            crec["recorded_score"] = rec.get("selection_hard")
            crec["decision"] = rec.get("action")
            crec["optimizer_calls"] = {k: (rec.get("tokens", {}).get(k, {}) or {}).get("calls")
                                       for k in ("analyst", "merge", "ranking")}
            crec["edits"] = {"merged": rec.get("n_edits_merged"), "ranked": rec.get("n_edits_ranked"),
                             "budget": rec.get("edit_budget"), "applied":
                             (rec.get("edit_apply_summary") or {}).get("applied"),
                             "support_counts": rec.get("support_counts")}
            crec["train_hard"] = rec.get("rollout_hard")
            crec["train_seeds"] = [b.get("batch_seed") for b in (rec.get("accumulation_batches") or [])]
            crec["recorded_current_after"] = rec.get("current_score")
            crec["recorded_best_after"] = rec.get("best_score")
        else:
            rec = read_json(os.path.join(spec["dir"], "slow_result.json"))
            crec["trainer_candidate_hash"] = rec.get("candidate_hash")
            crec["recorded_score"] = rec.get("selection_hard")
            crec["decision"] = rec.get("action")
            crec["optimizer_calls"] = {"slow_update": 1}
            pairs_path = os.path.join(spec["dir"], "comparison_pairs.json")
            pairs = read_json(pairs_path) if os.path.isfile(pairs_path) else []
            crec["pairs_sent"] = len(pairs)
            crec["pair_categories"] = {}
            for p in pairs:
                c = str(p.get("category") or "?")
                crec["pair_categories"][c] = crec["pair_categories"].get(c, 0) + 1
            crec["guidance_chars"] = len(str(rec.get("slow_update_content") or ""))
            crec["pair_hard"] = {"prev": rec.get("prev_hard"), "curr": rec.get("curr_hard")}
            crec["recorded_current_after"] = None
            crec["recorded_best_after"] = None

        # the skill the candidate was built FROM, taken from the producing rollout's own system prompt
        inp = input_rollout_results(spec)
        if os.path.isfile(inp):
            rows = read_jsonl(inp)
            skills = {sha(skill_from_system(str(r.get("target_system_prompt") or "")) or "\x00")
                      for r in rows}
            crec["input_skill_sha256"] = sorted(skills)[0] if len(skills) == 1 else None
            crec["input_skill_uniform"] = len(skills) == 1
            crec["input_skill_source"] = os.path.relpath(inp, campaign)
        else:
            crec["input_skill_sha256"] = None
            crec["input_skill_uniform"] = None
            crec["input_skill_source"] = None

        # the gate threshold, reconstructed in trainer order, and the decision re-derived from the rule
        thr = state[arm]["current"]
        crec["threshold"] = thr
        crec["threshold_source"] = "reconstructed in trainer order from the shared S0 baseline"
        score = crec.get("recorded_score")
        crec["decision_recomputed"] = ("accept_new_best" if (score is not None and score > thr
                                                             and score > state[arm]["best"])
                                       else "accept" if (score is not None and score > thr)
                                       else "reject")
        crec["decision_agrees"] = (crec["decision_recomputed"] == crec["decision"])
        if score is not None and score > thr:
            state[arm]["current"] = score
            state[arm]["best"] = max(state[arm]["best"], score)

        # the candidate's own selection evaluation
        eres = os.path.join(eval_dir(spec), "results.jsonl")
        if os.path.isfile(eres):
            rows = read_jsonl(eres)
            crec["n_items"] = len(rows)
            crec["correct"] = sum(int(r.get("hard") or 0) for r in rows)
            crec["score_recomputed"] = crec["correct"] / len(rows) if rows else None
            crec["score_agrees"] = (crec["score_recomputed"] is not None and score is not None
                                    and abs(crec["score_recomputed"] - score) < 1e-9)
            # is the file on disk the document the actor was given?
            embedded = {skill_from_system(str(r.get("target_system_prompt") or "")) for r in rows}
            crec["skill_embedded_verbatim"] = (len(embedded) == 1
                                               and embedded.pop() == cand_text.strip())
            scaf = {sha(scaffold_of(str(r.get("target_system_prompt") or ""))) for r in rows}
            crec["scaffold_sha256"] = sorted(scaf)[0] if len(scaf) == 1 else None
            crec["scaffold_matches_baseline"] = (len(scaf) == 1 and scaf <= base_scaffolds)
            ids = sorted(r["id"] for r in rows)
            crec["question_ids_match_baseline"] = (ids == base_ids)
            mism = [r["id"] for r in rows
                    if str(r.get("target_user_prompt") or "") != base_usr.get(r["id"])]
            crec["user_prompts_identical_to_baseline"] = not mism
            crec["user_prompt_mismatches"] = mism
            crec["use_local_tools_all_true"] = all(bool(r.get("use_local_tools")) for r in rows)
            crec["oracle_pages_identical"] = all(
                r.get("oracle_parsed_pages_chars") == base_by_id[r["id"]].get("oracle_parsed_pages_chars")
                for r in rows if r["id"] in base_by_id)
            crec["sources_identical"] = all(
                list(r.get("resolved_source_paths") or [])
                == list(base_by_id[r["id"]].get("resolved_source_paths") or [])
                for r in rows if r["id"] in base_by_id)
            cls = [classify(r, max_turns) for r in rows]
            crec["health"] = {
                "correct": sum(1 for c in cls if c["outcome"] == "correct"),
                "wrong_answer": sum(1 for c in cls if c["outcome"] == "wrong_answer"),
                "no_answer": sum(1 for c in cls if c["outcome"] == "no_answer"),
                "execution_failure": sum(1 for c in cls if c["outcome"] == "execution_failure"),
                "silent_turn": sum(1 for c in cls if c["why"] == "silent_turn"),
                "empty_parse": sum(1 for c in cls if c["why"] == "empty_parse"),
                "at_turn_cap": sum(1 for c in cls if c["at_turn_cap"]),
                "truncated": sum(1 for c in cls if c["truncated"]),
            }
            whys: dict[str, int] = {}
            for c in cls:
                if c["why"]:
                    key = c["why"].split(":", 1)[0]
                    whys[key] = whys.get(key, 0) + 1
            crec["blank_reasons"] = whys
        gates.append(crec)

    # baseline health, by the same classifier
    bcls = [classify(r, max_turns) for r in base_rows]
    dup_hashes: dict[str, list[str]] = {}
    for g in gates:
        if g.get("candidate_sha256"):
            dup_hashes.setdefault(g["candidate_sha256"], []).append(g["label"])

    return {
        "rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campaign": campaign,
        "actor_settings": {k: ref.get(k) for k in (
            "target_openai_compatible_model", "target_openai_compatible_temperature",
            "target_openai_compatible_max_tokens", "max_tool_turns", "max_queries_per_turn",
            "search_mode", "use_local_tools", "max_completion_tokens", "seed", "split_seed",
            "split_dir", "sel_env_num", "samples_per_epoch", "edit_budget", "min_edit_budget",
            "merge_batch_size", "minibatch_size", "slow_update_samples",
            "slow_update_gate_with_selection", "use_gate", "use_slow_update", "use_meta_skill",
            "num_epochs", "optimizer_model", "reasoning_effort")},
        "sampling_seed_sent_to_actor_api": False,
        "sampling_note": ("the actor is sampled at temperature "
                          f"{ref.get('target_openai_compatible_temperature')} and no per-request seed is "
                          "sent by `skillopt/model/openai_compatible_backend.py`, so re-running the same "
                          "skill on the same questions does NOT reproduce the same answers; `seed`/"
                          "`split_seed` fix which items are drawn, not what the actor says"),
        "config_digests": cfg_digests,
        "config_differences_vs_arm_B": cfg_diffs,
        "baseline": {
            "path": os.path.relpath(base_res, campaign), "sha256": base_digest,
            "n": len(base_rows), "correct": base_hard, "hard": base_hard / len(base_rows),
            "arm_copies_sha256": base_copies,
            "identical_across_arms": len(set(base_copies.values())) == 1
            and set(base_copies.values()) == {base_digest},
            "s0_sha256": sha(s0_text or ""), "s0_bytes": len((s0_text or "").encode("utf-8")),
            "health": {
                "correct": sum(1 for c in bcls if c["outcome"] == "correct"),
                "wrong_answer": sum(1 for c in bcls if c["outcome"] == "wrong_answer"),
                "no_answer": sum(1 for c in bcls if c["outcome"] == "no_answer"),
                "execution_failure": sum(1 for c in bcls if c["outcome"] == "execution_failure"),
                "silent_turn": sum(1 for c in bcls if c["why"] == "silent_turn"),
                "empty_parse": sum(1 for c in bcls if c["why"] == "empty_parse"),
                "at_turn_cap": sum(1 for c in bcls if c["at_turn_cap"]),
                "truncated": sum(1 for c in bcls if c["truncated"]),
            },
        },
        "n_gates": len(gates),
        "gates": gates,
        "duplicate_candidate_documents": {k: v for k, v in dup_hashes.items() if len(v) > 1},
        "checks": {
            "every_gate_has_a_candidate_file": all("candidate_sha256" in g for g in gates),
            "every_candidate_embedded_verbatim": all(g.get("skill_embedded_verbatim") for g in gates),
            "every_score_recomputes": all(g.get("score_agrees") for g in gates),
            "every_decision_recomputes": all(g.get("decision_agrees") for g in gates),
            "every_question_set_matches": all(g.get("question_ids_match_baseline") for g in gates),
            "every_user_prompt_matches": all(g.get("user_prompts_identical_to_baseline") for g in gates),
            "every_scaffold_matches": all(g.get("scaffold_matches_baseline") for g in gates),
            "every_input_skill_uniform": all(g.get("input_skill_uniform") for g in gates),
            "sources_identical_everywhere": all(g.get("sources_identical") for g in gates),
            "oracle_pages_identical_everywhere": all(g.get("oracle_pages_identical") for g in gates),
        },
    }


# ───────────────────────── subcommand: questions ─────────────────────────

def cmd_questions(campaign: str, with_traces: bool = True) -> dict:
    base_res = os.path.join(campaign, "shared", "val_baseline", "results.jsonl")
    base_rows = read_jsonl(base_res)
    cfg = any_config(campaign)
    max_turns = int(cfg.get("max_tool_turns") or 24)
    base_pred = os.path.join(campaign, "shared", "val_baseline", "predictions")

    base: dict[str, dict] = {}
    for r in base_rows:
        c = classify(r, max_turns)
        c["ground_truth"] = str(r.get("ground_truth") or "")
        c["question"] = str(r.get("question") or "")[:300]
        c["task_type"] = r.get("task_type")
        if with_traces:
            c["trace"] = trace_features(os.path.join(base_pred, r["id"]))
        base[r["id"]] = c

    specs = gate_specs(campaign)
    per_gate: dict[str, dict] = {}
    per_question: dict[str, dict] = {q: {"ground_truth": base[q]["ground_truth"],
                                         "task_type": base[q]["task_type"],
                                         "question": base[q]["question"],
                                         "s0": {k: base[q][k] for k in
                                                ("outcome", "why", "predicted", "n_turns", "relation")},
                                         "candidates": {}} for q in base}
    for spec in specs:
        eres = os.path.join(eval_dir(spec), "results.jsonl")
        if not os.path.isfile(eres):
            continue
        rows = read_jsonl(eres)
        pdir = os.path.join(eval_dir(spec), "predictions")
        gained, lost, kept_c, kept_w, moved = [], [], [], [], []
        counts: dict[str, int] = {}
        rels: dict[str, int] = {}
        src_changed, tool_delta, rep_delta = [], [], []
        invented: dict[str, int] = {}
        tool_total = 0
        for r in rows:
            qid = r["id"]
            c = classify(r, max_turns)
            if with_traces:
                c["trace"] = trace_features(os.path.join(pdir, qid))
            b = base.get(qid)
            counts[c["outcome"]] = counts.get(c["outcome"], 0) + 1
            if c["relation"]:
                rels[c["relation"]] = rels.get(c["relation"], 0) + 1
            if b:
                if b["outcome"] == "correct" and c["outcome"] == "correct":
                    kept_c.append(qid)
                elif b["outcome"] == "correct":
                    lost.append(qid)
                elif c["outcome"] == "correct":
                    gained.append(qid)
                else:
                    kept_w.append(qid)
                    if b["outcome"] != c["outcome"]:
                        moved.append({"id": qid, "s0": b["outcome"], "cand": c["outcome"]})
                if with_traces and c.get("trace", {}).get("present"):
                    for k, v in (c["trace"].get("invented_tool_calls") or {}).items():
                        invented[k] = invented.get(k, 0) + v
                    tool_total += c["trace"]["n_tool_calls"]
                if with_traces and b.get("trace", {}).get("present") and c["trace"].get("present"):
                    if set(b["trace"]["files_touched"]) != set(c["trace"]["files_touched"]):
                        src_changed.append(qid)
                    tool_delta.append(c["trace"]["n_tool_calls"] - b["trace"]["n_tool_calls"])
                    rep_delta.append(c["trace"]["n_repeated_calls"] - b["trace"]["n_repeated_calls"])
            per_question[qid]["candidates"][spec["label"]] = {
                k: c[k] for k in ("outcome", "why", "predicted", "n_turns", "relation")}
        per_gate[spec["label"]] = {
            "arm": spec["arm"], "epoch": spec["epoch"], "kind": spec["kind"],
            "correct": counts.get("correct", 0), "n": len(rows),
            "outcomes": counts, "relations": rels,
            "gained": sorted(gained), "lost": sorted(lost),
            "kept_correct": sorted(kept_c), "kept_incorrect": sorted(kept_w),
            "n_gained": len(gained), "n_lost": len(lost),
            "moved_within_incorrect": moved,
            "files_touched_changed_on": sorted(src_changed),
            "tool_calls_delta_mean": (sum(tool_delta) / len(tool_delta)) if tool_delta else None,
            "repeated_calls_delta_mean": (sum(rep_delta) / len(rep_delta)) if rep_delta else None,
            "tool_calls_total": tool_total,
            "invented_tool_calls": invented,
        }

    for qid, rec in per_question.items():
        cands = rec["candidates"]
        rec["n_candidates"] = len(cands)
        rec["n_candidates_correct"] = sum(1 for v in cands.values() if v["outcome"] == "correct")
        rec["candidate_outcomes"] = {}
        for v in cands.values():
            rec["candidate_outcomes"][v["outcome"]] = rec["candidate_outcomes"].get(v["outcome"], 0) + 1
        rec["gained_by"] = sorted(k for k, v in cands.items()
                                  if v["outcome"] == "correct" and rec["s0"]["outcome"] != "correct")
        rec["lost_by"] = sorted(k for k, v in cands.items()
                                if v["outcome"] != "correct" and rec["s0"]["outcome"] == "correct")

    s0_invented: dict[str, int] = {}
    s0_tool_total = 0
    for v in base.values():
        t = v.get("trace") or {}
        if t.get("present"):
            s0_tool_total += t["n_tool_calls"]
            for k, n in (t.get("invented_tool_calls") or {}).items():
                s0_invented[k] = s0_invented.get(k, 0) + n
    tot = {
        "s0_tool_calls_total": s0_tool_total,
        "s0_invented_tool_calls": s0_invented,
        "s0_correct": sum(1 for v in base.values() if v["outcome"] == "correct"),
        "s0_outcomes": {},
        "candidate_correct_total": sum(g["correct"] for g in per_gate.values()),
        "candidate_observations": sum(g["n"] for g in per_gate.values()),
        "gains_total": sum(g["n_gained"] for g in per_gate.values()),
        "losses_total": sum(g["n_lost"] for g in per_gate.values()),
        "questions_no_candidate_ever_solved": sorted(q for q, r in per_question.items()
                                                     if r["n_candidates_correct"] == 0
                                                     and r["s0"]["outcome"] != "correct"),
        "questions_solved_by_someone": sorted(q for q, r in per_question.items()
                                              if r["n_candidates_correct"] > 0
                                              or r["s0"]["outcome"] == "correct"),
    }
    for v in base.values():
        tot["s0_outcomes"][v["outcome"]] = tot["s0_outcomes"].get(v["outcome"], 0) + 1
    return {"rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "campaign": campaign, "max_tool_turns": max_turns,
            "s0": base, "per_question": per_question, "per_gate": per_gate, "totals": tot}


# ───────────────────────── subcommand: contracts ─────────────────────────

CONTRACT_VOCAB = ("RULE(", "col_path_sound", "col_leaf_sound", "sub_row_aligned",
                  "pattern_in_every_line", "OBLIGATION", "DISCHARGED", "OUTSTANDING", "VIOLATED",
                  "ASSUMED(", "stage ", "obligation")


def cmd_contracts(campaign: str) -> dict:
    out: dict = {"rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "campaign": campaign, "arms": {}}
    for arm in ("B", "C"):
        gpath = os.path.join(campaign, f"arm_{arm}", "stage_reports", "GENERATED.jsonl")
        if not os.path.isfile(gpath):
            continue
        rows = read_jsonl(gpath)
        af = {"confirmed": 0, "violated": 0, "unresolved": 0}
        ob: dict[str, int] = {}
        diags: dict[str, int] = {}
        viol_items, oblig_viol_items = [], []
        for r in rows:
            a = r.get("action_forecasts") or {}
            for k in af:
                af[k] += int(a.get(k) or 0)
            if int(a.get("violated") or 0) > 0:
                viol_items.append({"id": r.get("id"), "report": r.get("report_sha256", "")[:12],
                                   "violated": a.get("violated"),
                                   "dir": str(r.get("prediction_dir") or "").split("/arm_")[-1]})
            o = r.get("obligations")
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, int):
                        ob[k] = ob.get(k, 0) + v
                if int(o.get("VIOLATED") or 0) > 0:
                    oblig_viol_items.append({"id": r.get("id"), "violated": o.get("VIOLATED"),
                                             "dir": str(r.get("prediction_dir") or "").split("/arm_")[-1]})
            n = r.get("n_distinct_diagnoses")
            diags[str(n)] = diags.get(str(n), 0) + 1
        out["arms"][arm] = {
            "n_reports": len(rows),
            "action_forecasts": af,
            "violated_forecast_rate": (af["violated"] / max(1, sum(af.values()))),
            "obligations": ob,
            "n_distinct_diagnoses_histogram": diags,
            "reports_with_a_violated_forecast": viol_items,
            "reports_with_a_violated_obligation": oblig_viol_items,
            "boundary_audit": {},
            "model_versions": {},
        }
        for r in rows:
            d = out["arms"][arm]["boundary_audit"]
            k = str(r.get("boundary_audit"))
            d[k] = d.get(k, 0) + 1
            d2 = out["arms"][arm]["model_versions"]
            k2 = str(r.get("model_version"))
            d2[k2] = d2.get(k2, 0) + 1

    # Did contract vocabulary reach the edits? If an edit never names anything only a contract report
    # could have supplied, its diagnosis was available to the ordinary analyst too.
    edits: dict[str, list] = {}
    for arm in ARMS:
        for epoch in (1, 2, 3, 4):
            for name, path in (
                ("ranked", os.path.join(campaign, f"arm_{arm}", "steps", f"step_{epoch:04d}",
                                        "ranked_edits.json")),
                ("merged", os.path.join(campaign, f"arm_{arm}", "steps", f"step_{epoch:04d}",
                                        "merged_patch.json")),
            ):
                if not os.path.isfile(path):
                    continue
                doc = read_json(path)
                for j, e in enumerate(doc.get("edits") or []):
                    text = (str(e.get("content") or "") + " " + str(e.get("target") or ""))
                    hits = [v for v in CONTRACT_VOCAB if v in text]
                    edits.setdefault(f"{arm}.{epoch}.{name}", []).append(
                        {"index": j, "op": e.get("op"), "support_count": e.get("support_count"),
                         "source_type": e.get("source_type"),
                         "contract_vocabulary_hits": hits,
                         "chars": len(str(e.get("content") or "")),
                         "first_line": str(e.get("content") or "").strip().splitlines()[:1]})
    out["edits_scanned_for_contract_vocabulary"] = edits
    out["note"] = ("A vocabulary hit is necessary, not sufficient, for an edit to depend on the contract "
                   "layer: the words could be coincidental. A MISS is the stronger signal -- an edit that "
                   "names nothing from the report could have been written from the trace alone.")
    return out


# ───────────────────────── subcommand: chains ─────────────────────────

def cmd_chains(campaign: str, cases: list[str], n_excerpt: int = 2) -> dict:
    out: dict = {"rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "campaign": campaign, "cases": {}}
    cfg = any_config(campaign)
    max_turns = int(cfg.get("max_tool_turns") or 24)
    base_rows = read_jsonl(os.path.join(campaign, "shared", "val_baseline", "results.jsonl"))
    base = {r["id"]: r for r in base_rows}
    for case in cases:
        arm, epoch_s, kind = case.split(".")
        epoch = int(epoch_s)
        spec = next((s for s in gate_specs(campaign)
                     if s["arm"] == arm and s["epoch"] == epoch and s["kind"] == kind), None)
        if spec is None:
            out["cases"][case] = {"problem": "no such gate"}
            continue
        rec: dict = {"label": spec["label"]}
        if kind == "step":
            sr = read_json(os.path.join(spec["dir"], "step_record.json"))
            tdp = os.path.join(spec["dir"], "trajectory_digest.json")
            rec["training_observation"] = read_json(tdp) if os.path.isfile(tdp) else None
            rec["patches"] = []
            pdir = os.path.join(spec["dir"], "patches")
            for name in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
                d = read_json(os.path.join(pdir, name))
                rec["patches"].append({"file": name, "source_type": d.get("source_type"),
                                       "batch_size": d.get("batch_size"),
                                       "failure_summary": d.get("failure_summary"),
                                       "success_summary": d.get("success_summary"),
                                       "reasoning": str((d.get("patch") or {}).get("reasoning") or "")[:1500],
                                       "n_edits": len((d.get("patch") or {}).get("edits") or [])})
            for name in ("merged_patch", "ranked_edits", "edit_apply_report"):
                p = os.path.join(spec["dir"], f"{name}.json")
                rec[name] = read_json(p) if os.path.isfile(p) else None
            rec["step_record"] = sr
        else:
            rec["slow_result"] = read_json(os.path.join(spec["dir"], "slow_result.json"))
            pp = os.path.join(spec["dir"], "comparison_pairs.json")
            rec["comparison_pairs"] = read_json(pp) if os.path.isfile(pp) else None
        cp = candidate_path(spec)
        rec["candidate_skill"] = read_text(cp) if os.path.isfile(cp) else None
        inp = input_rollout_results(spec)
        if os.path.isfile(inp):
            rows = read_jsonl(inp)
            rec["input_skill"] = skill_from_system(str(rows[0].get("target_system_prompt") or ""))
        # what the actor did with it, on the questions that moved
        eres = os.path.join(eval_dir(spec), "results.jsonl")
        if os.path.isfile(eres):
            rows = {r["id"]: r for r in read_jsonl(eres)}
            pdir = os.path.join(eval_dir(spec), "predictions")
            moved = []
            for qid, r in sorted(rows.items()):
                b = base.get(qid)
                if b is None:
                    continue
                bc, cc = classify(b, max_turns), classify(r, max_turns)
                if bc["outcome"] != cc["outcome"]:
                    moved.append({"id": qid, "ground_truth": str(b.get("ground_truth") or ""),
                                  "question": str(b.get("question") or "")[:400],
                                  "s0": bc, "candidate": cc,
                                  "s0_trace": trace_features(
                                      os.path.join(campaign, "shared", "val_baseline",
                                                   "predictions", qid)),
                                  "candidate_trace": trace_features(os.path.join(pdir, qid)),
                                  "candidate_response_tail": str(r.get("response") or "")[-1200:],
                                  "s0_response_tail": str(b.get("response") or "")[-1200:]})
            rec["questions_that_moved"] = moved
        out["cases"][case] = rec
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("gates", "questions", "contracts"):
        p = sub.add_parser(name)
        p.add_argument("--campaign", required=True)
        p.add_argument("--out", default="")
        if name == "questions":
            p.add_argument("--no-traces", action="store_true")
    p = sub.add_parser("chains")
    p.add_argument("--campaign", required=True)
    p.add_argument("--case", action="append", required=True)
    p.add_argument("--out", default="")
    args = ap.parse_args()

    if args.cmd == "gates":
        doc = cmd_gates(args.campaign)
        print(f"rule {RULE}  {doc['n_gates']} gates   S0 {doc['baseline']['correct']}/"
              f"{doc['baseline']['n']}")
        for k, v in doc["checks"].items():
            print(f"  {'PASS' if v else 'FAIL'}  {k}")
        for g in doc["gates"]:
            print(f"  {g['label']:12s} cand {str(g.get('candidate_sha256'))[:12]} "
                  f"{g.get('correct')}/{g.get('n_items')} vs thr "
                  f"{g.get('threshold', 0) * N_SEL:.0f}/{N_SEL}  {g.get('decision')}")
    elif args.cmd == "questions":
        doc = cmd_questions(args.campaign, with_traces=not args.no_traces)
        t = doc["totals"]
        print(f"rule {RULE}  S0 {t['s0_correct']}/24 {t['s0_outcomes']}")
        print(f"  candidates: {t['candidate_correct_total']} correct of "
              f"{t['candidate_observations']} observations; "
              f"{t['gains_total']} gains, {t['losses_total']} losses")
        print(f"  never solved by anyone: {len(t['questions_no_candidate_ever_solved'])} of 24")
    elif args.cmd == "contracts":
        doc = cmd_contracts(args.campaign)
        for arm, d in doc["arms"].items():
            print(f"  arm {arm}: {d['n_reports']} reports  forecasts {d['action_forecasts']}  "
                  f"obligations {d['obligations']}")
    else:
        doc = cmd_chains(args.campaign, args.case)
        for case, d in doc["cases"].items():
            print(f"  {case}: {len(d.get('questions_that_moved') or [])} question(s) changed outcome")
    if getattr(args, "out", ""):
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
