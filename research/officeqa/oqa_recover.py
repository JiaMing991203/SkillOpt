#!/usr/bin/env python3
"""Audit, plan and stage the recovery of OfficeQA arm-epochs that a budget refusal silently turned into a skip.

WHAT WENT WRONG. `skillopt/model/spend_ledger.py` enforced the authorization on money that had been CLAIMED
and never released what a process did not spend. In the three-arm campaign of 2026-11-17 the optimizer's claims
reached the $4.00 cap with $2.19 actually paid, and from that moment every optimizer request was refused. The
trainer does not distinguish "the analyst found nothing to say" from "the analyst was never asked": with zero
patches it records `action: "skip_no_patches"` and moves on, and a refused slow update or meta skill is recorded
as `action: "no_content"`. Four arm-epochs therefore look, in the record, like ordinary quiet epochs.

WHAT THIS TOOL DOES, in four steps that are meant to be run in order:

  audit    Reads every arm-epoch's artifacts AND the ledger's own per-process refusal records, and classifies
           each stage. The point of reading both is that the artifacts alone cannot tell a starved stage from a
           quiet one, and the ledger alone cannot say which stage of which epoch a refusal belonged to. A stage
           is only called `starved` when a refusal is recorded by the job that produced it.

           It also explains, rather than flags, the shapes that merely LOOK wrong: a missing ranking call when
           the merged edits already fit the edit budget, and one fewer analyst call in an epoch that scored
           zero and so had no success group to reflect on.

  plan     Works out, for each starved arm-epoch, which saved artifacts may be reused. Reuse is decided by
           PROVENANCE, not by position: every stored trace records the system prompt it ran under, so the
           producing skill is recovered from the trace itself and compared against the skill the recovered
           state will put in force. A rollout whose producing skill cannot be identified is never reused.

  prepare  Archives the original records, then rewinds exactly as much state as the recovery needs: the arm's
           `runtime_state.json` back to the last sound step, and the `no_content` result files that the
           trainer's resume logic would otherwise take for finished work. Saved rollouts are left in place so
           they are reused; rollouts the plan marked stale are moved aside.

  verify   After the jobs have run, checks that what was reused really was reused, that what should have been
           regenerated was, and that the recovered epochs now have real optimizer output.

WHY THE ROLLOUTS CAN BE REUSED AT ALL. `run_batch` resumes by id from `results.jsonl`, so a saved rollout
directory left in place costs nothing and returns the identical trajectories. That is the same mechanism that
gave the three arms byte-identical epoch-1 traces. With SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1 it also verifies
each saved row against the skill now in force and re-rolls only what genuinely changed, which is what makes a
conditional dependency - "epoch 4's paired rollout is stale if and only if epoch 3's gate accepted" - safe to
resolve at run time instead of guessing in advance.

usage:
  python3 -I research/officeqa/oqa_recover.py audit   --campaign <root> --bundle <dir> [--bundle <dir>] \\
      --out <root>/recovery/RECOVERY_AUDIT.json
  python3 -I research/officeqa/oqa_recover.py plan    --campaign <root> --audit <file> \\
      --out <root>/recovery/RECOVERY_PLAN.json
  python3 -I research/officeqa/oqa_recover.py prepare --campaign <root> --plan <file> --apply \\
      --out <root>/recovery/RECOVERY_RECORD.json
  python3 -I research/officeqa/oqa_recover.py verify  --campaign <root> --record <file>
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sys

RULE = "OQA-REC-2"
ARMS = ("A", "B", "C")
EPOCHS = (1, 2, 3, 4)
# The stages of one arm-epoch, in the order the trainer runs them.
STAGES = ("rollout", "analyst", "merge", "ranking", "gate", "slow_update", "meta_skill")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


# ── the job that produced each arm-epoch ─────────────────────────────────────────────────────────────────────

_RUNDIR = re.compile(r"run_(\d+)_arm_([ABC])_e(\d)$")


def job_map(bundles: list[str]) -> dict[str, list[str]]:
    """`{"A.4": ["66273862"], ...}` from the per-job run directories the body creates in each bundle.

    An arm-epoch can have MORE than one job: arm C epoch 1 ran twice, because the first attempt died on the
    concurrent-import race. Both are returned, so refusals in either are attributed to that arm-epoch.
    """
    out: dict[str, list[str]] = {}
    for b in bundles:
        for path in sorted(glob.glob(os.path.join(b, "run_*_arm_*_e*"))):
            m = _RUNDIR.search(os.path.basename(path))
            if not m:
                continue
            out.setdefault(f"{m.group(2)}.{int(m.group(3))}", []).append(m.group(1))
    return out


def ledger_refusals(ledger: str) -> dict[str, dict[str, int]]:
    """`{job_id: {stage: n_refusals}}` read from the per-process spend records."""
    out: dict[str, dict[str, int]] = {}
    spend = os.path.join(ledger, "spend")
    for name in sorted(os.listdir(spend)) if os.path.isdir(spend) else []:
        if not name.endswith(".jsonl"):
            continue
        job = re.split(r"r\d+p", name[:-len(".jsonl")])[0]
        for line in open(os.path.join(spend, name), encoding="utf-8"):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") != "refused":
                continue
            d = out.setdefault(job, {})
            stage = str(row.get("stage") or "?")
            d[stage] = d.get(stage, 0) + 1
    return out


# ── audit ────────────────────────────────────────────────────────────────────────────────────────────────────

def audit(campaign: str, bundles: list[str]) -> dict:
    jobs = job_map(bundles)
    refusals = ledger_refusals(os.path.join(campaign, "ledger"))
    rows: list[dict] = []

    for arm in ARMS:
        root = os.path.join(campaign, f"arm_{arm}")
        if not os.path.isdir(root):
            continue
        for epoch in EPOCHS:
            key = f"{arm}.{epoch}"
            step = _load(os.path.join(root, "steps", f"step_{epoch:04d}", "step_record.json"), {}) or {}
            slow = _load(os.path.join(root, "slow_update", f"epoch_{epoch:02d}", "slow_result.json"), {}) or {}
            meta = _load(os.path.join(root, "meta_skill", f"epoch_{epoch:02d}",
                                     "meta_skill_result.json"), {}) or {}
            pairs = _load(os.path.join(root, "slow_update", f"epoch_{epoch:02d}",
                                       "comparison_pairs.json"), None)
            tok = step.get("tokens") or {}
            analyst_calls = int((tok.get("analyst") or {}).get("calls", 0) or 0)
            merge_calls = int((tok.get("merge") or {}).get("calls", 0) or 0)
            ranking_calls = int((tok.get("ranking") or {}).get("calls", 0) or 0)
            ref = {}
            for job in jobs.get(key, []):
                for stage, n in (refusals.get(job) or {}).items():
                    ref[stage] = ref.get(stage, 0) + n

            findings: dict[str, dict] = {}

            # ── the ordinary update: rollout -> analyst -> merge -> ranking -> gate ──
            findings["rollout"] = {
                "status": "ok" if step.get("rollout_n") else "missing",
                "n": step.get("rollout_n"), "hard": step.get("rollout_hard"),
                "note": "the paid training rollout; present even in a starved epoch",
            }
            n_patches = step.get("n_patches")
            if ref.get("analyst"):
                findings["analyst"] = {
                    "status": "starved", "calls": analyst_calls,
                    "refusals": ref["analyst"],
                    "note": f"{ref['analyst']} analyst request(s) were REFUSED by the spend ledger, so "
                            f"n_patches={n_patches} records an analyst that was never asked, not one that "
                            f"found nothing",
                }
            elif analyst_calls == 0:
                findings["analyst"] = {"status": "missing", "calls": 0,
                                       "note": "no analyst call and no recorded refusal - unexplained"}
            else:
                expected = 4
                if analyst_calls < expected:
                    hard = float(step.get("rollout_hard") or 0.0)
                    if hard == 0.0:
                        findings["analyst"] = {
                            "status": "ok", "calls": analyst_calls,
                            "note": f"{analyst_calls} calls rather than {expected}, and no refusal: this "
                                    f"epoch scored 0/{step.get('rollout_n')}, so there was no SUCCESS group "
                                    f"to reflect on. Legitimate control flow.",
                        }
                    else:
                        findings["analyst"] = {
                            "status": "short", "calls": analyst_calls,
                            "note": f"{analyst_calls} calls rather than {expected} and no refusal recorded, "
                                    f"and the epoch did not score zero - investigate",
                        }
                else:
                    findings["analyst"] = {"status": "ok", "calls": analyst_calls}

            findings["merge"] = ({"status": "ok", "calls": merge_calls,
                                  "n_edits_merged": step.get("n_edits_merged")}
                                 if merge_calls else
                                 {"status": "starved" if ref.get("analyst") else "missing",
                                  "calls": 0,
                                  "note": "no merge call, which follows from having no patches to merge"})

            merged, budget = step.get("n_edits_merged"), step.get("edit_budget")
            if ranking_calls:
                findings["ranking"] = {"status": "ok", "calls": ranking_calls,
                                       "n_edits_ranked": step.get("n_edits_ranked")}
            elif merged is not None and budget is not None and merged <= budget:
                findings["ranking"] = {
                    "status": "ok", "calls": 0,
                    "note": f"no ranking call because the {merged} merged edit(s) already fit the edit budget "
                            f"of {budget}; all of them were applied. Legitimate control flow.",
                }
            else:
                findings["ranking"] = {
                    "status": "starved" if ref.get("analyst") else "missing", "calls": 0,
                    "note": "no ranking call and nothing to rank, because no patch was produced",
                }

            action = step.get("action")
            if action in ("reject", "accept_new_best"):
                findings["gate"] = {"status": "ok", "action": action,
                                    "candidate": step.get("candidate_gate_score"),
                                    "current": step.get("current_score")}
            elif action == "skip_no_patches":
                findings["gate"] = {
                    "status": "starved" if ref.get("analyst") else "ok", "action": action,
                    "note": ("the gate never ran because there was no candidate, and there was no candidate "
                             "because the analyst was refused - this is the MASKED FAILURE"
                             if ref.get("analyst") else
                             "the gate never ran because the analyst genuinely produced no patch"),
                }
            else:
                findings["gate"] = {"status": "missing", "action": action}

            # ── the gated slow update and the meta skill ──
            n_pairs = len(pairs) if isinstance(pairs, list) else None
            sa = slow.get("action")
            if epoch == 1:
                findings["slow_update"] = {"status": "ok", "action": sa,
                                           "note": "epoch 1 injects an empty placeholder and makes no "
                                                   "optimizer call, by design"}
                findings["meta_skill"] = {"status": "ok", "action": meta.get("action"),
                                          "note": "epoch 1 has no previous epoch to remember, by design"}
            else:
                if sa == "no_content" and ref.get("slow_update"):
                    findings["slow_update"] = {
                        "status": "starved", "action": sa, "n_pairs": n_pairs,
                        "refusals": ref["slow_update"],
                        "note": f"the {n_pairs}-pair comparison was built and PAID FOR, then the optimizer "
                                f"request was refused by the ledger. `no_content` here is a budget refusal, "
                                f"not an empty reply and not an over-large prompt.",
                    }
                elif sa == "no_content":
                    findings["slow_update"] = {"status": "genuine_no_content", "action": sa,
                                               "n_pairs": n_pairs,
                                               "note": "no guidance came back and no refusal was recorded"}
                elif sa in ("reject", "accept_new_best", "accept"):
                    findings["slow_update"] = {"status": "ok", "action": sa, "n_pairs": n_pairs,
                                               "gate_score": slow.get("selection_hard"),
                                               "guidance_chars": len(slow.get("slow_update_content") or "")}
                else:
                    findings["slow_update"] = {"status": "missing", "action": sa, "n_pairs": n_pairs}

                ma = meta.get("action")
                if ma == "no_content" and ref.get("meta_skill"):
                    findings["meta_skill"] = {"status": "starved", "action": ma,
                                              "refusals": ref["meta_skill"],
                                              "note": "the meta-skill request was refused by the ledger"}
                elif ma == "no_content":
                    findings["meta_skill"] = {"status": "genuine_no_content", "action": ma}
                elif ma:
                    findings["meta_skill"] = {"status": "ok", "action": ma}
                else:
                    findings["meta_skill"] = {"status": "missing", "action": ma}

            starved = sorted(k for k, v in findings.items() if v["status"] == "starved")
            rows.append({
                "key": key, "arm": arm, "epoch": epoch, "jobs": jobs.get(key, []),
                "refusals_by_stage": ref, "stages": findings, "starved_stages": starved,
                "affected": bool(starved),
            })

    affected = [r["key"] for r in rows if r["affected"]]
    unexplained = [(r["key"], s) for r in rows for s, v in r["stages"].items()
                   if v["status"] in ("missing", "short", "genuine_no_content")]
    # The earliest affected epoch per arm is where that arm's recovery has to start.
    earliest: dict[str, int] = {}
    for r in rows:
        if r["affected"]:
            earliest[r["arm"]] = min(earliest.get(r["arm"], 99), r["epoch"])
    return {"rule": RULE, "utc": _utc(), "campaign": campaign, "bundles": bundles,
            "job_map": jobs, "rows": rows, "affected": affected,
            "earliest_affected_epoch_per_arm": earliest,
            "unexplained": [{"key": k, "stage": s} for k, s in unexplained],
            "total_refusals": sum(sum(d.values()) for d in refusals.values())}


# ── plan ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _system_prompt_of(row: dict) -> str:
    return str(row.get("target_system_prompt") or "")


def _skill_candidates(arm_root: str, campaign: str) -> dict[str, list[str]]:
    """`{skill digest: [paths]}` over every skill this arm ever held, plus the shared initial skill."""
    out: dict[str, list[str]] = {}
    paths = sorted(glob.glob(os.path.join(arm_root, "skills", "skill_v*.md")))
    for extra in (os.path.join(arm_root, "best_skill.md"),):
        if os.path.exists(extra):
            paths.append(extra)
    for p in paths:
        out.setdefault(_digest(p), []).append(p)
    return out


def _identify_producer(results_path: str, candidates: dict[str, list[str]]) -> dict:
    """Which skill produced this rollout? Decided from the trace, by matching the recorded system prompt.

    The system prompt embeds the skill verbatim, so a candidate skill whose text appears in EVERY row's
    recorded system prompt is a producer. Several candidates can match when one skill's text is a prefix of
    another's - S0 is a prefix of "S0 plus an empty SLOW_UPDATE placeholder" - so the LONGEST match wins, and
    the ambiguity is reported rather than hidden.
    """
    if not os.path.exists(results_path):
        return {"status": "absent"}
    prompts: set[str] = set()
    n = 0
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            prompts.add(_system_prompt_of(row))
    if not prompts:
        return {"status": "empty", "n_rows": 0}
    if len(prompts) > 1:
        return {"status": "mixed", "n_rows": n, "n_distinct_system_prompts": len(prompts),
                "note": "the rows in this directory did not all run under one system prompt"}
    prompt = next(iter(prompts))
    matches = []
    for dig, paths in candidates.items():
        text = open(paths[0], encoding="utf-8").read().strip()
        if text and text in prompt:
            matches.append((len(text), dig, [os.path.basename(p) for p in paths]))
    if not matches:
        return {"status": "unidentified", "n_rows": n,
                "note": "no known skill of this arm appears in the recorded system prompt"}
    matches.sort(reverse=True)
    best = matches[0]
    return {"status": "identified", "n_rows": n, "producer_digest": best[1],
            "producer_files": best[2],
            "also_matched": [m[1][:16] for m in matches[1:]] or None}


def plan(campaign: str, audit_doc: dict, *, usd_per_question: float, requests_per_question: float) -> dict:
    earliest = audit_doc["earliest_affected_epoch_per_arm"]
    items: list[dict] = []

    for arm in ARMS:
        if arm not in earliest:
            continue
        start = earliest[arm]
        arm_root = os.path.join(campaign, f"arm_{arm}")
        cands = _skill_candidates(arm_root, campaign)
        for epoch in [e for e in EPOCHS if e >= start]:
            row = next((r for r in audit_doc["rows"] if r["key"] == f"{arm}.{epoch}"), None)
            if row is None:
                continue
            # The skill in force at the START of epoch E is skill_v{E-1}; the one in force at its END is
            # skill_v{E}. One ordinary step per epoch, which is what `batch_size 40 > 24 questions` gives.
            start_skill = os.path.join(arm_root, "skills", f"skill_v{epoch - 1:04d}.md")
            end_skill = os.path.join(arm_root, "skills", f"skill_v{epoch:04d}.md")
            # Is this epoch's STARTING skill settled? Only for the FIRST epoch this arm recovers. A later
            # epoch starts from a skill that an earlier recovered gate may rewrite - arm C epoch 4 begins from
            # skill_v0003, which recovering epoch 3 will replace if its gate now accepts - so the rollouts
            # that epoch 4 would otherwise reuse are conditional on that outcome, not on anything visible now.
            start_settled = (epoch == start)
            cond_note = ("" if start_settled else
                         f" CONDITIONAL: this epoch starts from skill_v{epoch - 1:04d}, which recovering "
                         f"epoch {epoch - 1} rewrites if its gate accepts. The provenance guard re-rolls "
                         f"these rows in that case and reuses them otherwise.")
            artifacts = []

            train_dir = os.path.join(arm_root, "steps", f"step_{epoch:04d}", "rollout")
            ident = _identify_producer(os.path.join(train_dir, "results.jsonl"), cands)
            want = _digest(start_skill) if os.path.exists(start_skill) else ""
            reuse = ident.get("status") == "identified" and ident.get("producer_digest") == want
            artifacts.append({
                "path": os.path.relpath(train_dir, campaign), "kind": "training rollout",
                "n": ident.get("n_rows"), "identification": ident,
                "expected_producer": os.path.basename(start_skill), "expected_digest": want,
                "decision": ("reuse" if start_settled else "reuse_if_provenance_holds") if reuse
                            else "regenerate",
                "why": (("the producing skill is the one the recovered state puts in force at the start of "
                         "this epoch, and recovering a later stage cannot change it" + cond_note)
                        if reuse else
                        "the producing skill does not match the recovered state, or could not be identified"),
                "conditional": not start_settled,
            })

            if epoch >= 2:
                slow_dir = os.path.join(arm_root, "slow_update", f"epoch_{epoch:02d}")
                prev_dir = os.path.join(slow_dir, "rollout_prev")
                ident_p = _identify_producer(os.path.join(prev_dir, "results.jsonl"), cands)
                want_p = _digest(start_skill) if os.path.exists(start_skill) else ""
                reuse_p = ident_p.get("status") == "identified" and ident_p.get("producer_digest") == want_p
                artifacts.append({
                    "path": os.path.relpath(prev_dir, campaign), "kind": "slow-update rollout_prev",
                    "n": ident_p.get("n_rows"), "identification": ident_p,
                    "expected_producer": os.path.basename(start_skill), "expected_digest": want_p,
                    "decision": ("reuse" if start_settled else "reuse_if_provenance_holds") if reuse_p
                                else "regenerate",
                    "why": (("rollout_prev runs the PREVIOUS epoch's skill, which recovering THIS epoch does "
                             "not touch" + cond_note) if reuse_p
                            else "the producing skill does not match"),
                    "conditional": not start_settled,
                })

                curr_dir = os.path.join(slow_dir, "rollout_curr")
                ident_c = _identify_producer(os.path.join(curr_dir, "results.jsonl"), cands)
                want_c = _digest(end_skill) if os.path.exists(end_skill) else ""
                artifacts.append({
                    "path": os.path.relpath(curr_dir, campaign), "kind": "slow-update rollout_curr",
                    "n": ident_c.get("n_rows"), "identification": ident_c,
                    "expected_producer": f"whatever epoch {epoch}'s recovered gate leaves in force",
                    "expected_digest_if_unchanged": want_c,
                    "decision": "reuse_if_provenance_holds",
                    "why": "rollout_curr runs the skill in force at the END of this epoch, which is exactly "
                           "what the recovered gate decides. It is reusable if and only if the gate rejects "
                           "again. SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1 settles that at run time: the stored "
                           "rows carry the system prompt they ran under, so the 10 rollouts are reused on a "
                           "reject and re-rolled on an accept, with no guess either way.",
                    "conditional": True,
                })

                pairs_path = os.path.join(slow_dir, "comparison_pairs.json")
                pairs = _load(pairs_path, None)
                artifacts.append({
                    "path": os.path.relpath(pairs_path, campaign), "kind": "comparison pairs",
                    "n": len(pairs) if isinstance(pairs, list) else None,
                    "decision": "rebuild",
                    "why": "the pairs are derived from the two rollouts at no API cost, so they are rebuilt "
                           "from whichever trajectories survive the provenance check rather than reused",
                    "conditional": False,
                })

            # What has to be paid for: the gate passes, which never ran.
            gate_q = 24 if "gate" in row["starved_stages"] else 0
            slow_gate_q = 24 if "slow_update" in row["starved_stages"] else 0
            regen_q = sum(a.get("n") or 0 for a in artifacts if a["decision"] == "regenerate")
            cond_q = sum(a.get("n") or 0 for a in artifacts
                         if a["decision"] == "reuse_if_provenance_holds")
            items.append({
                "key": f"{arm}.{epoch}", "arm": arm, "epoch": epoch,
                "starved_stages": row["starved_stages"],
                "was_affected": row["affected"],
                "reason_included": ("starved" if row["affected"] else
                                    "downstream of a starved epoch in the same arm"),
                "artifacts": artifacts,
                "questions_certain": gate_q + slow_gate_q + regen_q,
                "questions_conditional": cond_q,
                "optimizer_calls_expected": 9 if epoch >= 2 else 7,
            })

    certain_q = sum(i["questions_certain"] for i in items)
    cond_q = sum(i["questions_conditional"] for i in items)
    opt_calls = sum(i["optimizer_calls_expected"] for i in items)
    problems = []
    for i in items:
        for a in i["artifacts"]:
            st = (a.get("identification") or {}).get("status")
            if st in ("mixed", "unidentified"):
                problems.append(f"{i['key']} {a['kind']}: provenance {st} - it will be regenerated, "
                                f"not reused")
    return {
        "rule": RULE, "utc": _utc(), "campaign": campaign,
        "order": [i["key"] for i in items],
        "items": items, "problems": problems,
        "cost": {
            "usd_per_question": usd_per_question, "requests_per_question": requests_per_question,
            "questions_certain": certain_q, "questions_conditional": cond_q,
            "actor_usd_certain": round(certain_q * usd_per_question, 4),
            "actor_usd_worst_case": round((certain_q + cond_q) * usd_per_question, 4),
            "actor_attempts_certain": int(round(certain_q * requests_per_question)),
            "actor_attempts_worst_case": int(round((certain_q + cond_q) * requests_per_question)),
            "optimizer_calls_expected": opt_calls,
            # Measured on this campaign: a full later-epoch arm job spent $0.28-$0.36 of optimizer.
            "optimizer_usd_expected": round(0.36 * len(items), 4),
        },
    }


# ── prepare ──────────────────────────────────────────────────────────────────────────────────────────────────

def prepare(campaign: str, plan_doc: dict, *, apply: bool, stamp: str) -> dict:
    """Archive the originals, then rewind only what the trainer's resume logic needs rewound."""
    archive = os.path.join(campaign, "recovery", "original", stamp)
    actions: list[dict] = []
    per_arm: dict[str, int] = {}
    for item in plan_doc["items"]:
        per_arm[item["arm"]] = min(per_arm.get(item["arm"], 99), item["epoch"])

    def _archive(src: str) -> dict:
        rel = os.path.relpath(src, campaign)
        dst = os.path.join(archive, rel)
        rec = {"archive": os.path.relpath(dst, campaign), "exists": os.path.exists(src)}
        if not rec["exists"]:
            return rec
        rec["digest"] = _digest(src) if os.path.isfile(src) else None
        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        return rec

    for arm, start in sorted(per_arm.items()):
        arm_root = os.path.join(campaign, f"arm_{arm}")

        # 1. Everything that records the arm's state, archived whole. Cheap and total: these are small files,
        #    and a recovery that cannot be compared against what it replaced is not a recovery.
        for name in ("runtime_state.json", "history.json", "summary.json", "best_skill.md"):
            actions.append({"arm": arm, "action": "archive", "path": name,
                            **_archive(os.path.join(arm_root, name))})
        actions.append({"arm": arm, "action": "archive", "path": "skills/",
                        **_archive(os.path.join(arm_root, "skills"))})
        for epoch in [e for e in EPOCHS if e >= start]:
            for rel in (os.path.join("steps", f"step_{epoch:04d}", "step_record.json"),
                        os.path.join("slow_update", f"epoch_{epoch:02d}", "slow_result.json"),
                        os.path.join("meta_skill", f"epoch_{epoch:02d}", "meta_skill_result.json"),
                        os.path.join("slow_update", f"epoch_{epoch:02d}", "comparison_pairs.json")):
                actions.append({"arm": arm, "action": "archive", "path": rel,
                                **_archive(os.path.join(arm_root, rel))})
        if arm != "A":
            cdir = os.path.join(campaign, "contracts", f"arm_{arm}")
            for p in sorted(glob.glob(os.path.join(cdir, "*.json"))) + \
                     sorted(glob.glob(os.path.join(cdir, "*.jsonl"))):
                actions.append({"arm": arm, "action": "archive",
                                "path": os.path.relpath(p, campaign), **_archive(p)})

        # 2. Rewind runtime_state to the end of the last SOUND step, so the trainer re-runs from `start`.
        #    The trainer reads `last_completed_step` and resumes at the next one; the score and origin fields
        #    have to match that moment too, or the gate would compare against the wrong baseline.
        rs_path = os.path.join(arm_root, "runtime_state.json")
        prev = start - 1
        prev_rec = _load(os.path.join(arm_root, "steps", f"step_{prev:04d}", "step_record.json"), {}) or {}
        # The state at the START of epoch `start` is the state after epoch `prev` COMPLETED, and an epoch
        # does not end with its ordinary step: the gated slow update runs after it and can accept. So the
        # step record is only the right source when that slow update did NOT accept. Taking it on faith
        # would hand the recovered gate the wrong incumbent to beat - which is the one thing a gate must
        # not get wrong. Every slow gate in this campaign rejected, so in fact nothing moves; the
        # reconstruction is here because a tool that is accidentally right is not right.
        rewound = {
            "last_completed_step": prev,
            "current_skill_path": os.path.join(arm_root, "skills", f"skill_v{prev:04d}.md"),
            "current_score": prev_rec.get("current_score"),
            "best_skill_path": os.path.join(arm_root, "best_skill.md"),
            "best_score": prev_rec.get("best_score"),
            "best_step": prev_rec.get("best_step"),
            "current_origin": prev_rec.get("current_origin"),
            "best_origin": prev_rec.get("best_origin"),
        }
        prev_slow = _load(os.path.join(arm_root, "slow_update", f"epoch_{prev:02d}",
                                       "slow_result.json"), {}) or {}
        slow_note = "the previous epoch's slow update did not move the state"
        if prev_slow.get("action") in ("accept", "accept_new_best"):
            sel = prev_slow.get("selection_hard")
            if sel is None:
                rewound["current_score"] = None      # forces a refusal below
                slow_note = (f"epoch {prev}'s slow update ACCEPTED but records no selection_hard, so the "
                             f"incumbent at the start of epoch {start} cannot be determined")
            else:
                rewound["current_score"] = sel
                rewound["current_origin"] = f"slow_update_epoch_{prev:02d}"
                if prev_slow["action"] == "accept_new_best":
                    rewound["best_score"] = sel
                    rewound["best_step"] = prev
                    rewound["best_origin"] = f"slow_update_epoch_{prev:02d}"
                slow_note = (f"epoch {prev}'s slow update was {prev_slow['action']} at {sel}, so the "
                             f"incumbent comes from it and not from the step record")
        missing = [k for k, v in rewound.items() if v is None]
        act = {"arm": arm, "action": "rewind runtime_state", "path": "runtime_state.json",
               "from": _load(rs_path, {}), "to": rewound, "fields_not_recoverable": missing,
               "previous_epoch_slow_update": prev_slow.get("action"), "note": slow_note}
        if missing:
            act["status"] = "REFUSED: step_%04d/step_record.json does not carry %s" % (prev, missing)
        elif apply:
            with open(rs_path, "w", encoding="utf-8") as f:
                json.dump(rewound, f, indent=2)
            act["status"] = "rewound"
        else:
            act["status"] = "would rewind"
        actions.append(act)

        # 2b. Trim history.json to the last sound step. The trainer LOADS history whole and APPENDS, with no
        #     trimming on resume, so re-running step N would leave two records for step N. Nothing in the
        #     gate path reads the duplicate - `sel_cache` is keyed on candidate content, and the slow
        #     update's `prev_epoch_records[-1]["step"]` is the same either way - but `summary.json` counts
        #     accepts, rejects and skips straight out of history, so the final readout would double-count
        #     exactly the epochs under repair. The originals are in the archive.
        hist_path = os.path.join(arm_root, "history.json")
        hist = _load(hist_path, []) or []
        kept = [h for h in hist if int(h.get("step", 0) or 0) < start]
        a = {"arm": arm, "action": "trim history", "path": "history.json",
             "records_before": len(hist), "records_kept": len(kept),
             "dropped_steps": [h.get("step") for h in hist if int(h.get("step", 0) or 0) >= start]}
        if len(kept) != len(hist):
            if apply:
                with open(hist_path, "w", encoding="utf-8") as f:
                    json.dump(kept, f, ensure_ascii=False, indent=2)
                a["status"] = "trimmed (archived above)"
            else:
                a["status"] = "would trim"
        else:
            a["status"] = "nothing to trim"
        actions.append(a)

        # 3. Remove the result files the trainer takes for finished work. The slow update and the meta skill
        #    resume on the EXISTENCE of their result file, so a `no_content` left in place would be skipped
        #    again - silently, and for the second time.
        for epoch in [e for e in EPOCHS if e >= start]:
            for rel in (os.path.join("slow_update", f"epoch_{epoch:02d}", "slow_result.json"),
                        os.path.join("meta_skill", f"epoch_{epoch:02d}", "meta_skill_result.json")):
                p = os.path.join(arm_root, rel)
                if not os.path.exists(p):
                    continue
                a = {"arm": arm, "action": "remove resume marker", "path": rel,
                     "was_action": (_load(p, {}) or {}).get("action")}
                if apply:
                    os.remove(p)
                    a["status"] = "removed (archived above)"
                else:
                    a["status"] = "would remove"
                actions.append(a)

        # 3b. Contract-model artifacts the recovery may legitimately need to rewrite with DIFFERENT content.
        #
        #     `oqa_lc_ir.freeze` refuses to overwrite a frozen model whose content digest differs, and the
        #     body only WARNS when the evolution fails. So a model file left in place where the inputs have
        #     changed would stop the evolution and leave the old file standing - silently, and under a name
        #     that says it describes an epoch it no longer describes.
        #
        #     Which epochs can change? model_arm<X>_epoch<E>.json is evolved from epoch E's rollout, and
        #     epoch E's rollout changes only if E's STARTING skill changes, which happens only for E > start.
        #     So the model for the first recovered epoch is kept - its inputs are identical, the re-freeze is
        #     a no-op, and the file keeps its original bytes including frozen_utc - and the later ones are
        #     moved aside so the evolution can write them fresh.
        if arm != "A":
            cdir = os.path.join(campaign, "contracts", f"arm_{arm}")
            for epoch in [e for e in EPOCHS if e > start]:
                for name in (f"model_arm{arm}_epoch{epoch:02d}.json",
                             f"EVOLVE_arm{arm}_epoch{epoch:02d}.json",
                             f"predictions_epoch{epoch:02d}.jsonl"):
                    src = os.path.join(cdir, name)
                    if not os.path.exists(src):
                        continue
                    dst = os.path.join(archive, "contracts", f"arm_{arm}", name + ".superseded")
                    a = {"arm": arm, "action": "move aside a contract artifact",
                         "path": os.path.relpath(src, campaign),
                         "to": os.path.relpath(dst, campaign),
                         "why": f"epoch {epoch} is after the first recovered epoch ({start}), so its traces "
                                f"can change; freeze() would refuse to overwrite this with new content"}
                    if apply:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.move(src, dst)
                        a["status"] = "moved"
                    else:
                        a["status"] = "would move"
                    actions.append(a)
            keep = os.path.join(cdir, f"model_arm{arm}_epoch{start:02d}.json")
            actions.append({"arm": arm, "action": "keep the first recovered epoch's model",
                            "path": os.path.relpath(keep, campaign),
                            "exists": os.path.exists(keep),
                            "digest": _digest(keep) if os.path.exists(keep) else None,
                            "status": "kept",
                            "why": f"epoch {start}'s rollout is reused unchanged, so re-evolving it is a "
                                   f"no-op and freeze() keeps this file byte-for-byte"})

        # 4. Rollouts the plan marked stale: moved aside, never deleted.
        for item in plan_doc["items"]:
            if item["arm"] != arm:
                continue
            for art in item["artifacts"]:
                if art["decision"] != "regenerate":
                    continue
                src = os.path.join(campaign, art["path"])
                if not os.path.isdir(src):
                    continue
                dst = os.path.join(archive, art["path"] + ".stale")
                a = {"arm": arm, "action": "move aside a stale rollout", "path": art["path"],
                     "to": os.path.relpath(dst, campaign), "why": art["why"]}
                if apply:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    a["status"] = "moved"
                else:
                    a["status"] = "would move"
                actions.append(a)

    refused = [a for a in actions if str(a.get("status", "")).startswith("REFUSED")]
    return {"rule": RULE, "utc": _utc(), "campaign": campaign, "stamp": stamp,
            "archive": os.path.relpath(archive, campaign), "applied": apply,
            "recover_from": per_arm, "actions": actions, "refused": refused,
            "plan_order": plan_doc["order"]}


# ── verify ───────────────────────────────────────────────────────────────────────────────────────────────────

def verify(campaign: str, record: dict) -> dict:
    checks: list[dict] = []
    archive = os.path.join(campaign, record["archive"])
    for arm, start in sorted(record["recover_from"].items()):
        arm_root = os.path.join(campaign, f"arm_{arm}")
        for epoch in [e for e in EPOCHS if e >= start]:
            step = _load(os.path.join(arm_root, "steps", f"step_{epoch:04d}", "step_record.json"), {}) or {}
            slow = _load(os.path.join(arm_root, "slow_update", f"epoch_{epoch:02d}",
                                     "slow_result.json"), {}) or {}
            meta = _load(os.path.join(arm_root, "meta_skill", f"epoch_{epoch:02d}",
                                     "meta_skill_result.json"), {}) or {}
            tok = step.get("tokens") or {}
            analyst = int((tok.get("analyst") or {}).get("calls", 0) or 0)
            checks.append({
                "key": f"{arm}.{epoch}", "analyst_calls": analyst,
                "ordinary_action": step.get("action"),
                "slow_action": slow.get("action"), "meta_action": meta.get("action"),
                "ok": analyst > 0 and step.get("action") in ("reject", "accept_new_best")
                      and slow.get("action") not in (None, "no_content"),
            })
    # Did the reuse actually happen? A reused rollout is byte-identical to its archived copy.
    reuse: list[dict] = []
    for a in record["actions"]:
        if a.get("action") != "archive" or not a.get("digest"):
            continue
        live = os.path.join(campaign, a["path"]) if os.path.isabs(a["path"]) is False else a["path"]
        arm = a.get("arm")
        live = os.path.join(campaign, f"arm_{arm}", a["path"]) if arm else live
        if os.path.isfile(live):
            reuse.append({"path": a["path"], "arm": arm, "unchanged": _digest(live) == a["digest"]})
    # The contract models: the one kept must be byte-identical afterwards, because its inputs were reused.
    models: list[dict] = []
    for a in record["actions"]:
        if a.get("action") != "keep the first recovered epoch's model" or not a.get("digest"):
            continue
        live = os.path.join(campaign, a["path"])
        models.append({"path": a["path"], "exists": os.path.isfile(live),
                       "unchanged": os.path.isfile(live) and _digest(live) == a["digest"],
                       "why": "its rollout was reused, so re-evolving it must be a no-op"})
    for a in record["actions"]:
        if a.get("action") != "move aside a contract artifact":
            continue
        live = os.path.join(campaign, a["path"])
        models.append({"path": a["path"], "exists": os.path.isfile(live),
                       "regenerated": os.path.isfile(live),
                       "why": "moved aside so the evolution could write it fresh"})
    failed = [c for c in checks if not c["ok"]]
    model_problems = [m for m in models if m.get("unchanged") is False] + \
                     [m for m in models if m.get("regenerated") is False]
    return {"rule": RULE, "utc": _utc(), "campaign": campaign, "archive": archive,
            "per_epoch": checks, "unchanged_after_recovery": reuse, "contract_models": models,
            "verdict": "PASS" if not (failed or model_problems) else "PROBLEMS",
            "failed": failed + model_problems}


# ── cli ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _print_audit(doc: dict) -> None:
    print(f"rule {doc['rule']}   {doc['total_refusals']} budget refusal(s) in the ledger")
    print("\n%-6s %-9s %-7s %-16s %-14s %-16s %s" % (
        "epoch", "analyst", "gate", "ordinary", "slow update", "meta skill", "starved"))
    for r in doc["rows"]:
        s = r["stages"]
        print("%-6s %-9s %-7s %-16s %-14s %-16s %s" % (
            r["key"],
            f"{s['analyst'].get('calls', 0)} {s['analyst']['status'][:5]}",
            s["gate"]["status"][:7],
            str(s["gate"].get("action"))[:16],
            f"{s['slow_update'].get('action') or '-'}"[:14],
            f"{s['meta_skill'].get('action') or '-'}"[:16],
            ",".join(r["starved_stages"]) or "-"))
    print(f"\naffected arm-epochs: {', '.join(doc['affected']) or 'none'}")
    print(f"recovery starts at:  " + ", ".join(
        f"arm {a} epoch {e}" for a, e in sorted(doc["earliest_affected_epoch_per_arm"].items())))
    notes = [(r["key"], st, v["note"]) for r in doc["rows"] for st, v in r["stages"].items()
             if v.get("note") and "Legitimate control flow" in v["note"]]
    if notes:
        print("\nshapes that look wrong and are not:")
        for k, st, n in notes:
            print(f"  {k} {st}: {n}")
    if doc["unexplained"]:
        print("\nUNEXPLAINED (not attributable to a recorded refusal):")
        for u in doc["unexplained"]:
            print(f"  {u['key']} {u['stage']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit")
    a.add_argument("--campaign", required=True)
    a.add_argument("--bundle", action="append", default=[])
    a.add_argument("--out", default="")

    p = sub.add_parser("plan")
    p.add_argument("--campaign", required=True)
    p.add_argument("--audit", required=True)
    p.add_argument("--usd-per-question", type=float, default=0.00656)
    p.add_argument("--requests-per-question", type=float, default=10.32)
    p.add_argument("--out", default="")

    q = sub.add_parser("prepare")
    q.add_argument("--campaign", required=True)
    q.add_argument("--plan", required=True)
    q.add_argument("--apply", action="store_true")
    q.add_argument("--stamp", default=datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    q.add_argument("--out", default="")

    v = sub.add_parser("verify")
    v.add_argument("--campaign", required=True)
    v.add_argument("--record", required=True)
    v.add_argument("--out", default="")

    args = ap.parse_args()

    if args.cmd == "audit":
        doc = audit(args.campaign, args.bundle)
        _print_audit(doc)
    elif args.cmd == "plan":
        doc = plan(args.campaign, _load(args.audit) or {},
                   usd_per_question=args.usd_per_question,
                   requests_per_question=args.requests_per_question)
        print(f"rule {doc['rule']}   recovery order: {' -> '.join(doc['order'])}")
        for i in doc["items"]:
            print(f"\n  {i['key']}  ({i['reason_included']}; starved: "
                  f"{','.join(i['starved_stages']) or 'none'})")
            for art in i["artifacts"]:
                n = art.get("n")
                print(f"    {art['decision']:<28} {art['kind']:<28} "
                      f"{'' if n is None else str(n) + ' items'}")
        c = doc["cost"]
        print(f"\n  certain:    {c['questions_certain']:>4} question-rollouts  "
              f"${c['actor_usd_certain']:.4f}  {c['actor_attempts_certain']} attempts")
        print(f"  worst case: {c['questions_certain'] + c['questions_conditional']:>4} question-rollouts  "
              f"${c['actor_usd_worst_case']:.4f}  {c['actor_attempts_worst_case']} attempts")
        print(f"  optimizer:  {c['optimizer_calls_expected']} calls, about "
              f"${c['optimizer_usd_expected']:.2f}")
        if doc["problems"]:
            print("\n  PROBLEMS")
            for s in doc["problems"]:
                print(f"    - {s}")
    elif args.cmd == "prepare":
        doc = prepare(args.campaign, _load(args.plan) or {}, apply=args.apply, stamp=args.stamp)
        print(f"rule {doc['rule']}   {'APPLIED' if doc['applied'] else 'DRY RUN'}   "
              f"archive {doc['archive']}")
        for act in doc["actions"]:
            if act["action"] == "archive":
                continue
            print(f"  {act['status']:<26} {act['action']:<30} arm {act['arm']} {act['path']}")
        print(f"  archived {sum(1 for a in doc['actions'] if a['action'] == 'archive')} original record(s)")
        if doc["refused"]:
            print("\n  REFUSED")
            for r in doc["refused"]:
                print(f"    {r}")
        if not doc["applied"]:
            print("\n  dry run - nothing moved. Pass --apply.")
    else:
        doc = verify(args.campaign, _load(args.record) or {})
        print(f"rule {doc['rule']}   verdict {doc['verdict']}")
        for c in doc["per_epoch"]:
            print(f"  {c['key']}  analyst={c['analyst_calls']}  ordinary={c['ordinary_action']}  "
                  f"slow={c['slow_action']}  meta={c['meta_action']}  {'ok' if c['ok'] else 'PROBLEM'}")

    out = getattr(args, "out", "")
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        print(f"\nwritten: {out}")
    bad = doc.get("problems") or doc.get("refused") or doc.get("failed") or doc.get("unexplained")
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
