#!/usr/bin/env python3
"""Trace2Skill on OfficeQA: analysis, parallel patch proposal, hierarchical consolidation. Rule OQA-T2S-1.

UPSTREAM: github.com/Qwen-Applications/Trace2Skill at 3d0b52a140f002a512930252b613c49048f7d5ac
(2026-05-02), Apache 2.0, arXiv:2603.25158. The prompts and the declared adaptations are in
research/officeqa/oqa_t2s_prompts.py; the structure reproduced here is upstream's
`ParallelSkillEvolver`:

    ANALYSIS   one call per trajectory. Error analyst for a failed trajectory, success analyst for a
               correct one, which is upstream's own triage from the official evaluation.
    MAP        one call per analysis record -> a semantic patch, in parallel.
    REDUCE     HIERARCHICAL. Repeatedly split the patches into balanced batches of `merge_batch_size`
               and merge each batch in parallel, until one patch remains or `max_merge_levels` is hit;
               then one final coalescing merge. `chunk_list` reproduces upstream's balancing exactly,
               including the round-robin distribution of a remainder smaller than half a batch.
    APPLY      the merged patch becomes the new skill document.
    VERIFY     every item of the merged patch is checked against the edited document.

THIS IS NOT SkillOpt AND MUST NOT BORROW FROM IT. Trace2Skill has no epochs, no minibatches, no
validation-selection loop and no slow update. Its consolidated skill IS its endpoint. Validation may
MEASURE that endpoint, but a loop that validated candidates and kept the best would be a different
method wearing this one's name, so nothing here reads the validation split at all.

THE INFORMATION BOUNDARY, enforced rather than described.
  permitted   predictions/<uid>/conversation.json - the actor's own messages, the executed tool calls
              and their observations (which for OfficeQA contain the document text the actor read), and
              target_{system,user}_prompt.txt.
  triage only results.jsonl `hard`, the binary 0/1 verdict, used ONLY to choose which analyst runs.
  forbidden   `ground_truth` and `soft`, and the provider's alternate reasoning field. Every outbound
              prompt is scanned for the answer key of every question in the campaign before it is sent,
              and the run aborts if one appears. That is a mechanical check, not a convention.

RESUME. Every call is cached on disk under out/cache/<phase>/<sha256 of the exact prompt>.json. A
resubmission after a TIMEOUT reuses every paid reply whose prompt is unchanged, so recovery costs only
the calls that had not yet happened.

usage:
  python3 -I research/officeqa/oqa_t2s.py run --config <cfg> --s0-dir <dir> --skill <md> \
      --optimizer-model deepseek-flash --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import datetime
import glob
import hashlib
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

RULE = "OQA-T2S-1"
SCHEMA = "lso.officeqa_trace2skill.v1"

# Upstream's markdown default is merge batches of 8 with at most 4 hierarchy levels. THREE, HERE, FOR
# A MEASURED REASON. In job 66553058 all eight level-1 merges succeeded (4,032-11,334 completion
# tokens) and then BOTH level-2 merges truncated at exactly 16,384 from ~77,000-character prompts,
# because a level-2 merge combines already-MERGED patches carrying ~10 items each. The consolidation
# therefore degraded to a mechanical coalesce with no conflict resolution - losing the one property
# the method is named for. Smaller batches mean more levels and smaller outputs, which is how
# upstream's own hierarchy is supposed to absorb this.
MERGE_BATCH_SIZE = 3
MAX_MERGE_LEVELS = 6
# Two DeepSeek requests in flight, by instruction.
MAX_WORKERS = 2
PASSES = (1, 2, 3, 4)


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tree() -> str:
    return os.environ.get("OQA_TREE") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── the permitted view ──────────────────────────────────────────────────────────────────────────────────

def render_permitted(conv: list) -> str:
    """The chat log as the analyst may see it.

    Entry shapes, as the saved trajectories actually use them:
        {"role": ..., "content": ...}        the task prompt
        {"type": "message", "content": ...}  an actor message
        {"type": "tool_call", "cmd", "obs"}  an executed tool call and what it returned
    Nothing else is emitted, so a field added to the trace later cannot leak into the prompt silently.
    """
    out: list[str] = []
    for entry in conv:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "message":
            out.append(f"[AGENT]\n{entry.get('content') or ''}")
        elif kind == "tool_call":
            out.append(f"[TOOL CALL]\n{entry.get('cmd') or ''}\n[OBSERVATION]\n{entry.get('obs') or ''}")
        elif entry.get("role") is not None:
            out.append(f"[{str(entry.get('role')).upper()}]\n{entry.get('content') or ''}")
    return "\n\n".join(out)


def collect_trajectories(s0_dir: str) -> tuple[list, dict]:
    """The 48 fixed-S0 trajectories: four passes over the 12 training questions."""
    trajectories: list = []
    per_pass: dict = {}
    for p in PASSES:
        run = os.path.join(s0_dir, f"train_p{p}")
        rows: dict = {}
        results = os.path.join(run, "results.jsonl")
        if os.path.isfile(results):
            with open(results, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        row = json.loads(line)
                        rows[str(row.get("id"))] = row
        found = 0
        for conv_path in sorted(glob.glob(os.path.join(run, "predictions", "*", "conversation.json"))):
            uid = os.path.basename(os.path.dirname(conv_path))
            with open(conv_path, encoding="utf-8") as f:
                conv = json.load(f)
            row = rows.get(uid) or {}
            # TRIAGE ONLY. `hard` is the binary verdict; `ground_truth` is never read here.
            hard = row.get("hard")
            trajectories.append({
                "uid": uid, "pass": p, "trajectory_id": f"{uid}@p{p}",
                "conversation_path": conv_path,
                "permitted_view": render_permitted(conv),
                "n_entries": len(conv),
                "n_tool_calls": sum(1 for e in conv if isinstance(e, dict) and e.get("type") == "tool_call"),
                "verdict": ("correct" if hard in (1, 1.0, True) else
                            ("incorrect" if hard in (0, 0.0, False) else "unknown")),
                "termination_cause": row.get("termination_cause"),
            })
            found += 1
        per_pass[f"p{p}"] = found
    return trajectories, per_pass


def answer_keys(split_dir: str) -> list[str]:
    """Every ground-truth string in the campaign, so an outbound prompt can be checked against them."""
    keys: list[str] = []
    import csv as _csv
    for sub in ("train", "val", "test"):
        for path in glob.glob(os.path.join(split_dir, sub, "*.csv")):
            with open(path, newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    gold = str(row.get("ground_truth") or "").strip()
                    # Short numeric keys would match by coincidence; only distinctive strings are usable
                    # as a leak test, and the threshold is recorded rather than tuned.
                    if len(gold) >= 6:
                        keys.append(gold)
    return sorted(set(keys))


# ── parsing: the upstream output formats ────────────────────────────────────────────────────────────────

_ITEM_HEADS = ("Failure Cause Item", "Failure Memory Item", "Success Cause Item", "Success Memory Item")


def parse_analysis(text: str) -> dict:
    """The analyst's two sections, as Title / Description / Content triples."""
    items: list = []
    pattern = re.compile(r"^#\s*(" + "|".join(re.escape(h) for h in _ITEM_HEADS) + r")\s*(\d*)\s*$",
                         re.MULTILINE)
    marks = list(pattern.finditer(text or ""))
    for i, m in enumerate(marks):
        body = (text[m.end():marks[i + 1].start()] if i + 1 < len(marks) else text[m.end():])
        fields = {}
        for name in ("Title", "Description", "Content"):
            fm = re.search(rf"^##\s*{name}\s*$\n(.*?)(?=^##\s|\Z)", body,
                           re.MULTILINE | re.DOTALL)
            fields[name.lower()] = (fm.group(1).strip() if fm else "")
        kind = m.group(1)
        items.append({"kind": kind, "index": m.group(2),
                      "section": "memory" if "Memory" in kind else "cause", **fields})
    return {"items": items,
            "n_cause": sum(1 for i in items if i["section"] == "cause"),
            "n_memory": sum(1 for i in items if i["section"] == "memory"),
            "parsed": bool(items)}


_PATCH_RE = re.compile(r"=====\s*PATCH START\s*=====(.*?)=====\s*PATCH END\s*=====", re.DOTALL)
_ITEM_RE = re.compile(r"\[ITEM_(\d+)_START\](.*?)(?=\[ITEM_\d+_START\]|\Z)", re.DOTALL)
_FIELDS = ("Target File", "Edit Intent", "Location Hint", "Change Instruction")


def parse_patches(text: str) -> list:
    """Upstream's semantic patch blocks. A block whose items do not parse is DROPPED, and the caller
    records that it was dropped: a half-parsed patch applied as if whole is worse than no patch."""
    patches: list = []
    for block in _PATCH_RE.findall(text or ""):
        reasoning = ""
        rm = re.search(r"Reasoning:\s*(.*?)(?=\n\s*Changelog:|\n\s*Items:|\Z)", block, re.DOTALL)
        if rm:
            reasoning = rm.group(1).strip()
        changelog = ""
        cm = re.search(r"Changelog:\s*(.*?)(?=\n\s*Items:|\Z)", block, re.DOTALL)
        if cm:
            changelog = cm.group(1).strip()
        items: list = []
        items_part = block[block.index("Items:") + len("Items:"):] if "Items:" in block else block
        for idx, body in _ITEM_RE.findall(items_part):
            got = {}
            for fi, name in enumerate(_FIELDS):
                nxt = "|".join(re.escape(n) + r"\s*:" for n in _FIELDS[fi + 1:]) or r"\Z"
                fm = re.search(rf"{re.escape(name)}\s*:\s*(.*?)(?=\n\s*(?:{nxt})|\Z)", body, re.DOTALL)
                got[name] = (fm.group(1).strip() if fm else "")
            if got["Edit Intent"] and got["Change Instruction"]:
                items.append({"index": int(idx), "target_file": got["Target File"] or "SKILL.md",
                              "edit_intent": got["Edit Intent"], "location_hint": got["Location Hint"],
                              "change_instruction": got["Change Instruction"]})
        if items:
            patches.append({"reasoning": reasoning, "changelog": changelog, "items": items})
    return patches


def render_patch(patch: dict) -> str:
    """A patch back in upstream's schema, which is what the merge and apply phases consume."""
    lines = ["===== PATCH START =====", "Reasoning:", patch.get("reasoning") or "", "",
             "Changelog:", patch.get("changelog") or "", "", "Items:", ""]
    for n, item in enumerate(patch.get("items") or [], start=1):
        lines += [f"[ITEM_{n}_START]",
                  f"Target File: {item.get('target_file') or 'SKILL.md'}",
                  f"Edit Intent: {item.get('edit_intent') or ''}",
                  f"Location Hint: {item.get('location_hint') or ''}",
                  "Change Instruction:", item.get("change_instruction") or "", ""]
    lines.append("===== PATCH END =====")
    return "\n".join(lines)


def chunk_list(items: list, batch_size: int) -> list:
    """Upstream's balanced batching, reproduced exactly.

    If the remainder is smaller than half the batch size, distribute its items round-robin into earlier
    batches. Otherwise keep it as its own batch.
    """
    total = len(items)
    if total == 0:
        return []
    if total <= batch_size:
        return [list(items)]
    n_full = total // batch_size
    remainder = total % batch_size
    if remainder == 0:
        return [list(items[i:i + batch_size]) for i in range(0, total, batch_size)]
    batches = [list(items[i:i + batch_size]) for i in range(0, n_full * batch_size, batch_size)]
    if remainder < batch_size / 2:
        for i, item in enumerate(items[n_full * batch_size:]):
            batches[i % len(batches)].append(item)
    else:
        batches.append(list(items[n_full * batch_size:]))
    return batches


# ── the optimizer, through SkillOpt's own route-pinned, ledger-enforced path ─────────────────────────────

class Caller:
    """Every optimizer call, cached by prompt digest and provenance-checked before it is sent.

    THE GUARD THIS CLASS FIRST IMPLEMENTED WAS WRONG, AND IT FAILED CLOSED, WHICH IS WHY IT COST
    NOTHING. Job 66552508 aborted after two seconds with "1 ground-truth string(s) appear in an
    outbound prompt", before a single paid call. The hits were real and they were not leaks:

        UID0017  the key `[10102000000, 4.73]`  found in AN ACTOR MESSAGE
        UID0033  the key `March 3, 1977`        found in AN ACTOR MESSAGE

    Both are the question's own key, and in both the actor had ANSWERED CORRECTLY. On a correct episode
    the actor's own answer IS the ground-truth string - that is what "correct" means - so scanning an
    outbound prompt for answer keys fires on exactly the episodes the SUCCESS analyst needs. The
    predicate conflated "the answer key was supplied to the analyst" with "the actor produced the right
    answer". The same conflation would fire on any value the actor legitimately READ, since the answer
    to a document question is a number printed in the document.

    SO THE GUARD IS NOW ABOUT PROVENANCE, WHICH IS THE ACTUAL REQUIREMENT. A permitted corpus is built
    once from the material the prompts are allowed to be made of - every trajectory's permitted view
    plus the skill document. A ground-truth string found in a prompt is:

        EXPLAINED    it also occurs in the permitted corpus, so it reached the prompt by the actor
                     having read or written it. Recorded and counted, not a violation.
        UNEXPLAINED  it does NOT occur in the permitted corpus, so it can only have come from the
                     evaluator's fields. The run aborts.

    That keeps the fail-closed property for a genuine leak while not refusing a correct episode.
    """

    def __init__(self, out_dir: str, keys: list, permitted_corpus: str = "", dry_run: bool = False,
                 default_max_tokens: int = 16384):
        self.cache_dir = os.path.join(out_dir, "cache")
        self.calls: list = []
        self.keys = keys
        self.permitted = permitted_corpus or ""
        self.dry_run = dry_run
        self.lock = threading.Lock()
        self.leaks: list = []
        self.explained: list = []
        self.truncated: list = []
        self.default_max_tokens = int(default_max_tokens)
        os.makedirs(self.cache_dir, exist_ok=True)

    def extend_permitted(self, text: str) -> None:
        """Admit text DERIVED from already-guarded material into the permitted corpus.

        THE CHAIN-OF-CUSTODY ARGUMENT, and it is what makes the guard usable past the first phase.
        Every phase's prompt is guarded before it is sent, so every phase's OUTPUT is derived only from
        material that passed the guard. An analyst's diagnosis, a proposed patch and a merged patch are
        therefore legitimate, even when they contain a number that matches an answer key - the analyst
        only ever saw the permitted view, and it may reformat a value it read (writing "1,169.41" for a
        trace's "1169.41") so that the string no longer occurs literally in the trajectory.

        Job 66552979 failed on exactly that, in phase `map`, after the analysis phase had completed:
        the corpus held the trajectories and the skill but not the analysts' own output.

        BE CLEAR ABOUT WHAT THIS COSTS. Admitting derived text makes the string scan PERMISSIVE
        downstream; after the analysis phase it will accept almost anything. The scan is therefore a
        tripwire on the one phase where raw trajectories enter, not the main protection. THE MAIN
        PROTECTION IS STRUCTURAL: `collect_trajectories` reads only `hard` and `termination_cause` from
        results.jsonl, and nothing in this module reads `ground_truth` or `soft` at all. A test asserts
        that, and it is the claim worth relying on.
        """
        with self.lock:
            self.permitted = self.permitted + "\n" + (text or "")

    def _guard(self, phase: str, system: str, user: str) -> None:
        """A key in a prompt is a violation only if the permitted corpus cannot explain it."""
        blob = system + "\n" + user
        unexplained: list = []
        for key in self.keys:
            if key not in blob:
                continue
            if key in self.permitted:
                with self.lock:
                    self.explained.append({
                        "phase": phase, "chars": len(key),
                        "why": "the string also occurs in the permitted corpus - the actor read it or "
                               "wrote it - so it did not come from the evaluator"})
            else:
                unexplained.append(key)
        if unexplained:
            with self.lock:
                self.leaks.append({"phase": phase, "n_unexplained": len(unexplained),
                                   "example_len": len(unexplained[0])})
            raise SystemExit(
                f"BOUNDARY VIOLATION in phase {phase}: {len(unexplained)} ground-truth string(s) "
                f"appear in an outbound prompt and the permitted corpus does NOT contain them, so they "
                f"can only have come from the evaluator. Nothing further is sent.")

    def __call__(self, phase: str, system: str, user: str, *, max_tokens: int = 0) -> str:
        max_tokens = int(max_tokens or self.default_max_tokens)
        self._guard(phase, system, user)
        # THE KEY MUST COVER EVERYTHING THAT AFFECTS THE REPLY, not just the prompt. `max_tokens` was
        # missing, so raising it to fix a TRUNCATED reply would have silently returned the truncated
        # one from cache - a cache that defeats the repair it is meant to survive.
        digest = hashlib.sha256(json.dumps(
            {"p": phase, "s": system, "u": user, "max_tokens": max_tokens},
            sort_keys=True).encode()).hexdigest()
        path = os.path.join(self.cache_dir, phase, f"{digest}.json")
        # A LEGACY ENTRY, from before `max_tokens` joined the key. Adding it to the key was correct and
        # would otherwise discard 95 already-paid replies, so a legacy entry is MIGRATED - but only if
        # it was not truncated. A truncated reply is exactly what the key change exists to stop being
        # reused, so migrating one would defeat the fix.
        legacy = os.path.join(self.cache_dir, phase, f"{hashlib.sha256(json.dumps({'p': phase, 's': system, 'u': user}, sort_keys=True).encode()).hexdigest()}.json")
        migrated = False
        if not os.path.isfile(path) and os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as f:
                old_rec = json.load(f)
            was_truncated = int((old_rec.get("usage") or {}).get("completion_tokens") or 0) >= max_tokens
            if not was_truncated:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                old_rec["migrated_from_legacy_key"] = True
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(old_rec, f, indent=1, sort_keys=True, default=str)
                migrated = True
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            with self.lock:
                self.calls.append({"phase": phase, "digest": digest[:12], "cached": True,
                                   "migrated": migrated, "usage": rec.get("usage")})
            return rec.get("text") or ""
        if self.dry_run:
            with self.lock:
                self.calls.append({"phase": phase, "digest": digest[:12], "cached": False,
                                   "dry_run": True})
            return ""
        sys.path.insert(0, _tree())
        from skillopt.model import chat_optimizer  # type: ignore
        text, usage = chat_optimizer(system=system, user=user,
                                     max_completion_tokens=max_tokens, stage=f"t2s_{phase}")
        # TRUNCATION IS THE FAILURE MODE THAT COST THIS STAGE ITS CONSOLIDATION, so it is detected
        # here rather than inferred downstream from an unparseable reply. Job 66553058: the two
        # level-2 merges and the verify step all came back at EXACTLY completion_tokens 16384 with no
        # terminator, from ~77,000-character prompts.
        completion = int((usage or {}).get("completion_tokens") or 0)
        truncated = completion >= max_tokens
        if truncated:
            with self.lock:
                self.truncated.append({"phase": phase, "completion_tokens": completion,
                                       "max_tokens": max_tokens, "prompt_chars": len(user),
                                       "reply_chars": len(text or "")})
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"phase": phase, "system": system, "user": user, "text": text,
                       "usage": usage, "utc": _utc()}, f, indent=1, sort_keys=True, default=str)
        with self.lock:
            self.calls.append({"phase": phase, "digest": digest[:12], "cached": False,
                               "usage": usage, "chars": len(text or ""), "truncated": truncated})
        return text or ""


def configure_models(config_path: str, optimizer_model: str, optimizer_max_tokens: int = 0) -> dict:
    """Point the model layer at DeepSeek before anything is sent, exactly as the trainer does.

    WHY THIS EXISTS, and it is the second time this project has paid for the lesson.
    `skillopt.model.chat_optimizer` is a ROUTER whose default is Azure, and a config file does not
    reach it by itself - `skillopt.engine.trainer` calls `set_optimizer_backend` and
    `configure_openai_compatible`. Without that step every call raises

        ValueError: Azure OpenAI endpoint is not configured for optimizer

    which is what job 66552741 did, 48 times, after a dry run had reported everything healthy. The
    campaign body's own header already warned about this router (job 66271190 dispatched a perfect
    openai-compatible config to Azure and recorded `no_content`); the preflight was fixed for it
    earlier the same night and this module was not.

    It runs in DRY RUN TOO. The dry run's whole purpose is to exercise the real path without sending,
    and it missed this precisely because it short-circuited before configuring anything.
    """
    sys.path.insert(0, _tree())
    from skillopt.config import load_config  # type: ignore
    from skillopt.model import (configure_openai_compatible,  # type: ignore
                                set_optimizer_backend)

    cfg = load_config(config_path) if config_path else {}

    def _get(path, default=None):
        if path in cfg:
            return cfg[path]
        node = cfg
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    backend = _get("model.optimizer_backend", "openai_compatible") or "openai_compatible"
    set_optimizer_backend(backend)
    configure_openai_compatible(
        optimizer_base_url=_get("model.optimizer_openai_compatible_base_url") or None,
        optimizer_model=optimizer_model or _get("model.optimizer_openai_compatible_model") or None,
        # RAISING THE CAP HAS TO GO THROUGH THE CONFIG. The backend sends
        # `min(max_completion_tokens, config.max_tokens)`, so a larger per-call argument is silently
        # CLAMPED by the configured value - the same silent-clamp pattern as the actor's output cap.
        # Job 66553156's final merge truncated at 16,384 while emitting 47 consolidated items, and no
        # amount of batching fixes that: the final patch has to fit in one reply.
        optimizer_max_tokens=(optimizer_max_tokens
                              or _get("model.optimizer_openai_compatible_max_tokens")),
        optimizer_timeout_seconds=_get("model.optimizer_openai_compatible_timeout_seconds"),
    )
    from skillopt.model import openai_compatible_backend as B  # type: ignore
    live = B._config_for("optimizer")
    effective = {"backend": backend, "base_url": live.base_url, "model": live.deployment,
                 "max_tokens": live.max_tokens, "timeout_seconds": live.timeout_seconds,
                 "api_key_present": bool(live.api_key)}
    # Fail here rather than 48 times inside the analysis phase.
    if backend == "openai_compatible" and "deepseek" not in str(live.base_url):
        raise SystemExit(f"the optimizer is not pointed at DeepSeek: {effective}")
    return effective


def _parallel(fn, items: list, workers: int = MAX_WORKERS) -> list:
    """Run fn over items, preserving input order, never losing an exception silently."""
    out: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, item): i for i, item in enumerate(items)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                out[i] = {"ok": True, "value": fut.result()}
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                out[i] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return [out[i] for i in sorted(out)]


# ── the phases ──────────────────────────────────────────────────────────────────────────────────────────

def phase_analysis(call: Caller, trajectories: list, P) -> list:
    """One call per trajectory. The verdict chooses the analyst; it never enters the prompt as text."""
    def one(traj: dict) -> dict:
        correct = traj["verdict"] == "correct"
        system = P.SUCCESS_ANALYSIS_SYSTEM if correct else P.ERROR_ANALYSIS_SYSTEM
        user = ("# Agent chat log\n\n" + traj["permitted_view"] +
                "\n\n# Task\nAnalyse the log above and produce the two sections in the required format.")
        text = call("analysis", system, user)
        parsed = parse_analysis(text)
        return {"trajectory_id": traj["trajectory_id"], "uid": traj["uid"], "pass": traj["pass"],
                "analyst": "success" if correct else "error",
                "verdict_used_for_triage_only": traj["verdict"],
                "termination_cause": traj["termination_cause"],
                "raw_chars": len(text or ""), **parsed}
    results = _parallel(one, trajectories)
    records: list = []
    for traj, res in zip(trajectories, results):
        if res["ok"]:
            records.append(res["value"])
        else:
            records.append({"trajectory_id": traj["trajectory_id"], "uid": traj["uid"],
                            "pass": traj["pass"], "parsed": False, "items": [],
                            "n_cause": 0, "n_memory": 0, "error": res["error"]})
    return records


def phase_map(call: Caller, records: list, skill: str, P) -> tuple[list, list]:
    """One semantic patch per analysis record, in parallel. Upstream's MAP."""
    usable = [r for r in records if r.get("parsed") and r.get("items")]

    def render_record(rec: dict) -> str:
        lines = [f"## Analysis record ({rec['analyst']} analysis)"]
        for item in rec["items"]:
            lines += [f"### {item['kind']} {item['index']}".rstrip(),
                      f"Title: {item.get('title')}",
                      f"Description: {item.get('description')}",
                      f"Content: {item.get('content')}", ""]
        return "\n".join(lines)

    def one(rec: dict):
        user = ("# Current skill document (SKILL.md)\n\n" + skill +
                "\n\n# Analysis record\n\n" + render_record(rec) +
                "\n\n# Task\nPropose a minimal semantic patch in the required format.")
        text = call("map", P.MAP_SYSTEM, user)
        return {"record": rec["trajectory_id"], "patches": parse_patches(text),
                "raw_chars": len(text or "")}

    results = _parallel(one, usable)
    patches: list = []
    problems: list = []
    for rec, res in zip(usable, results):
        if not res["ok"]:
            problems.append({"record": rec["trajectory_id"], "phase": "map", "error": res["error"]})
            continue
        got = res["value"]["patches"]
        if not got:
            problems.append({"record": rec["trajectory_id"], "phase": "map",
                             "error": "no parseable patch block in the response"})
            continue
        for patch in got:
            patch["origin"] = rec["trajectory_id"]
            patches.append(patch)
    return patches, problems


def phase_reduce(call: Caller, patches: list, skill: str, P,
                 batch_size: int = MERGE_BATCH_SIZE, max_levels: int = MAX_MERGE_LEVELS) -> tuple:
    """HIERARCHICAL consolidation, upstream's REDUCE. A failed batch merge keeps its inputs rather
    than dropping them, so a provider error loses no proposal."""
    levels: list = []
    if not patches:
        return None, levels
    if len(patches) == 1:
        return patches[0], levels
    current = list(patches)
    level = 0
    while len(current) > 1 and level < max_levels:
        level += 1
        batches = chunk_list(current, batch_size)

        def one(batch: list):
            user = ("# Current skill document (SKILL.md)\n\n" + skill +
                    "\n\n# Patches to merge\n\n" +
                    "\n\n".join(render_patch(p) for p in batch) +
                    "\n\n# Task\nMerge the patches above into one coherent, non-redundant set.")
            return parse_patches(call("merge", P.MERGE_SYSTEM, user))

        results = _parallel(one, batches)
        nxt: list = []
        ok = fail = 0
        for batch, res in zip(batches, results):
            merged = res["value"] if res["ok"] else None
            if merged:
                nxt.extend(merged)
                ok += 1
            else:
                nxt.extend(batch)      # keep the inputs; a failed merge must not lose a proposal
                fail += 1
        levels.append({"level": level, "patches_in": len(current), "batches": len(batches),
                       "merged_ok": ok, "merge_failed": fail, "patches_out": len(nxt)})
        if len(nxt) >= len(current):
            # No progress. Another identical level would only spend money, so stop and coalesce.
            levels[-1]["stopped"] = "the level did not reduce the patch count"
            current = nxt
            break
        current = nxt
    if len(current) > 1:
        user = ("# Current skill document (SKILL.md)\n\n" + skill +
                "\n\n# Patches to merge\n\n" + "\n\n".join(render_patch(p) for p in current) +
                "\n\n# Task\nMerge the patches above into exactly ONE coherent, non-redundant set.")
        # The batch merges above are shielded by `_parallel`; this one was NOT, so a provider error here
        # propagated and destroyed every proposal the run had paid for. It now degrades to the local
        # coalesce below, which loses no item - only the model's conflict resolution.
        try:
            final = parse_patches(call("merge", P.MERGE_SYSTEM, user))
            final_error = None
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            final, final_error = [], f"{type(exc).__name__}: {exc}"
        levels.append({"level": "final", "patches_in": len(current),
                       "patches_out": len(final) if final else 0,
                       **({"error": final_error} if final_error else {})})
        if final:
            if len(final) == 1:
                return final[0], levels
            # Still several: concatenate their items rather than discard any, and say so.
            items: list = []
            for p in final:
                items.extend(p["items"])
            return {"reasoning": "coalesced from several merged patches that the final merge did not "
                                 "reduce to one; every item is preserved",
                    "changelog": "coalesced", "items": items, "coalesced": True}, levels
        # The final merge produced nothing usable. Coalesce locally rather than return one patch and
        # silently discard the rest: no model resolved the conflicts, and the report says so.
        items = []
        for patch in current:
            items.extend(patch["items"])
        return {"reasoning": "the final merge returned no usable patch, so every item of every "
                            "surviving patch is preserved WITHOUT model conflict resolution",
                "changelog": "coalesced without a successful final merge",
                "items": items, "coalesced": True,
                "conflicts_unresolved": True}, levels
    return current[0], levels


def phase_apply(call: Caller, skill: str, merged: dict, P) -> str:
    user = ("# Original skill document (SKILL.md)\n\n" + skill +
            "\n\n# Merged patch to apply\n\n" + render_patch(merged) +
            "\n\n# Task\nOutput the complete edited document and nothing else.")
    text = call("apply", P.APPLY_SYSTEM, user)
    out = (text or "").strip()
    # A model asked for a bare document sometimes fences it anyway; unwrap one outer fence.
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", out, re.DOTALL)
    if fence:
        out = fence.group(1).strip()
    return out


VERIFY_ITEMS_PER_CALL = 12


def phase_verify(call: Caller, original: str, merged: dict, edited: str, P) -> dict:
    """Check the merged patch against the edited document, IN CHUNKS OF ITEMS.

    One call for everything truncated in job 66553058: a 76,925-character prompt asking for per-item
    JSON over 77 items came back at exactly 16,384 completion tokens with the JSON unterminated, so the
    run had no verification at all. The item list is therefore chunked; each chunk reports only its own
    items, and the verdicts are concatenated. The documents are repeated in every chunk, which costs
    prompt tokens - the cheap direction - to buy a reply that fits.
    """
    items = merged.get("items") or []
    chunks = [items[i:i + VERIFY_ITEMS_PER_CALL]
              for i in range(0, len(items), VERIFY_ITEMS_PER_CALL)] or [[]]
    all_items, unrequested, failures = [], [], []

    for n, chunk in enumerate(chunks, start=1):
        part = dict(merged)
        part["items"] = chunk
        user = ("# Original document\n\n" + original +
                "\n\n# Merged patch (items " +
                f"{(n - 1) * VERIFY_ITEMS_PER_CALL + 1}-{(n - 1) * VERIFY_ITEMS_PER_CALL + len(chunk)}"
                f" of {len(items)})\n\n" + render_patch(part) +
                "\n\n# Edited document\n\n" + edited +
                "\n\n# Task\nReport the JSON described above, for THESE ITEMS ONLY.")
        text = call("verify", P.VERIFY_SYSTEM, user)
        raw = (text or "").strip()
        fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1)
        try:
            got = json.loads(raw)
        except ValueError as exc:
            failures.append({"chunk": n, "error": str(exc), "raw_chars": len(text or "")})
            continue
        for item in (got.get("items") or []):
            item["chunk"] = n
            all_items.append(item)
        unrequested.extend(got.get("unrequested") or [])

    verdicts = collections.Counter(str(i.get("verdict")) for i in all_items)
    return {"parsed": not failures or bool(all_items),
            "chunks": len(chunks), "items_per_call": VERIFY_ITEMS_PER_CALL,
            "n_items_in_patch": len(items), "n_items_verified": len(all_items),
            "verdicts": dict(verdicts),
            "items": all_items, "unrequested": unrequested,
            "chunk_failures": failures,
            "complete": len(all_items) == len(items) and not failures}


# ── the run ─────────────────────────────────────────────────────────────────────────────────────────────

def run(args) -> int:
    sys.path.insert(0, _tree())
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import oqa_t2s_prompts as P  # type: ignore

    os.makedirs(args.out, exist_ok=True)
    # BEFORE anything is built or sent, and in dry run too: the router's default is Azure.
    effective_optimizer = configure_models(args.config, args.optimizer_model,
                                           args.optimizer_max_tokens)
    trajectories, per_pass = collect_trajectories(args.s0_dir)
    with open(args.skill, encoding="utf-8") as f:
        skill = f.read()
    keys = answer_keys(args.split_dir) if args.split_dir else []
    # THE PERMITTED CORPUS: everything the prompts are allowed to be made of. A ground-truth string
    # found in a prompt is explained if it occurs here, and a violation only if it does not.
    permitted_corpus = "\n".join([t["permitted_view"] for t in trajectories] + [skill])
    call = Caller(args.out, keys, permitted_corpus=permitted_corpus, dry_run=args.dry_run,
                  default_max_tokens=int(effective_optimizer.get("max_tokens") or 16384))

    problems: list = []
    if not trajectories:
        problems.append("no trajectories found under --s0-dir; nothing to analyse")
    expected = len(PASSES) * 12
    if len(trajectories) != expected:
        problems.append(f"{len(trajectories)} trajectories, expected {expected} ({per_pass})")
    if not keys:
        problems.append("no answer keys were loaded, so the boundary check could not run")

    report: dict = {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(), "commit": os.environ.get("OQA_COMMIT"),
        "upstream": P.UPSTREAM,
        "method_note": "Trace2Skill: analysis -> parallel patch proposal -> hierarchical consolidation. "
                       "No epochs, no minibatches, no slow update and no validation-selection loop: the "
                       "consolidated skill is the endpoint.",
        "inputs": {"s0_dir": args.s0_dir, "skill": args.skill, "split_dir": args.split_dir,
                   "optimizer_model": args.optimizer_model},
        "effective_optimizer": effective_optimizer,
        "optimizer_output_cap": int(effective_optimizer.get("max_tokens") or 0),
        "boundary": {
            "permitted": "conversation.json (actor messages, tool calls, observations) and the saved "
                         "target prompts",
            "triage_only": "results.jsonl `hard`, the binary verdict, used only to choose the analyst",
            "forbidden": "`ground_truth`, `soft`, and the provider's alternate reasoning field",
            "answer_keys_checked_per_prompt": len(keys),
            "guard": "STRUCTURAL and primary: collect_trajectories reads only `hard` and "
                     "`termination_cause`; nothing in this module reads `ground_truth` or `soft`. The "
                     "string scan is a secondary tripwire on the ANALYSIS phase, where raw "
                     "trajectories enter. It admits text derived from already-guarded prompts, so it "
                     "is deliberately permissive downstream; it is not the main protection.",
            "permitted_corpus_chars": len(permitted_corpus),
        },
        "trajectories": {"n": len(trajectories), "per_pass": per_pass,
                         "correct": sum(1 for t in trajectories if t["verdict"] == "correct"),
                         "incorrect": sum(1 for t in trajectories if t["verdict"] == "incorrect"),
                         "unknown": sum(1 for t in trajectories if t["verdict"] == "unknown")},
        "skill_before": {"path": args.skill, "bytes": len(skill.encode()),
                         "sha256_12": hashlib.sha256(skill.encode()).hexdigest()[:12]},
    }

    if problems and not trajectories:
        report["problems"] = problems
        report["verdict"] = "BLOCKED"
        _write(args.out, report, None)
        print(json.dumps({"verdict": "BLOCKED", "problems": problems}, indent=1))
        return 1

    records = phase_analysis(call, trajectories, P)
    analysis_errors = collections.Counter(str(r["error"]) for r in records if r.get("error"))
    report["analysis"] = {
        "n_records": len(records),
        "parsed": sum(1 for r in records if r.get("parsed")),
        "failed": [r["trajectory_id"] for r in records if not r.get("parsed")],
        # Surfaced, not buried. The first version reported only "parsed: 0" while 48 identical
        # exceptions sat in the per-record field, so the summary said nothing about the cause.
        "errors": dict(analysis_errors),
        "by_analyst": {k: sum(1 for r in records if r.get("analyst") == k) for k in ("error", "success")},
        "cause_items": sum(r.get("n_cause", 0) for r in records),
        "memory_items": sum(r.get("n_memory", 0) for r in records),
        "records": records,
    }

    # The analysts' output is derived from guarded prompts, so it joins the permitted corpus before
    # the MAP phase quotes it back. See Caller.extend_permitted.
    call.extend_permitted("\n".join(
        f"{i.get('title')}\n{i.get('description')}\n{i.get('content')}"
        for r in records for i in (r.get("items") or [])))

    patches, map_problems = phase_map(call, records, skill, P)
    problems += map_problems
    report["map"] = {"patches_proposed": len(patches),
                     "items_proposed": sum(len(p["items"]) for p in patches),
                     "records_without_a_patch": len(map_problems),
                     "patches": patches}

    call.extend_permitted("\n".join(render_patch(p) for p in patches))

    merged, levels = phase_reduce(call, patches, skill, P, args.merge_batch_size, args.max_merge_levels)
    if merged:
        call.extend_permitted(render_patch(merged))
    report["reduce"] = {"levels": levels, "merged_items": len(merged["items"]) if merged else 0,
                        "merged": merged}

    edited = phase_apply(call, skill, merged, P) if merged else ""
    if not edited:
        # A DRY RUN RETURNS NOTHING BY DESIGN - it builds and guards every prompt and sends none - so
        # an empty document there is the expected outcome, not a fault. Reporting it as a problem made
        # every dry run exit 1, which trains a reader to ignore the one signal the self-check exists to
        # give. The dry run's real failures are a boundary violation or an exception, both of which
        # still surface.
        if args.dry_run:
            report["dry_run"] = ("prompts were built and guarded for every phase; nothing was sent, "
                                 "so no document was produced and the skill is untouched")
        else:
            problems.append("the apply phase returned no document; the skill is UNCHANGED")
        edited = skill
    report["skill_after"] = {"bytes": len(edited.encode()),
                             "sha256_12": hashlib.sha256(edited.encode()).hexdigest()[:12],
                             "changed": edited.strip() != skill.strip()}
    report["verify"] = phase_verify(call, skill, merged, edited, P) if merged else {"parsed": False}

    usage = {"calls": len(call.calls), "cached": sum(1 for c in call.calls if c.get("cached")),
             "paid": sum(1 for c in call.calls if not c.get("cached"))}
    for field in ("prompt_tokens", "completion_tokens"):
        usage[field] = sum(int((c.get("usage") or {}).get(field) or 0) for c in call.calls
                           if not c.get("cached"))
    report["optimizer_usage"] = usage
    report["calls"] = call.calls
    report["boundary_violations"] = call.leaks
    report["truncated_calls"] = {
        "n": len(call.truncated),
        "note": "a reply that reached the output cap is incomplete; it is detected at the call rather "
                "than inferred downstream from an unparseable patch",
        "calls": call.truncated}
    report["boundary_explained_hits"] = {
        "n": len(call.explained),
        "note": "answer-key strings that appeared in a prompt AND in the permitted corpus, so the "
                "actor had read or written them. Counted for transparency; not leaks.",
        "sample": call.explained[:6]}
    report["problems"] = problems
    report["verdict"] = "CONSOLIDATED" if report["skill_after"]["changed"] else "UNCHANGED"
    _write(args.out, report, edited)
    print(json.dumps({"verdict": report["verdict"], "trajectories": report["trajectories"],
                      "effective_optimizer": effective_optimizer,
                      "analysis": {k: report["analysis"][k] for k in
                                   ("n_records", "parsed", "by_analyst", "cause_items",
                                    "memory_items", "errors")},
                      "map": {k: report["map"][k] for k in
                              ("patches_proposed", "items_proposed", "records_without_a_patch")},
                      "reduce_levels": levels,
                      "merged_items": report["reduce"]["merged_items"],
                      "skill": {"before": report["skill_before"]["sha256_12"],
                                "after": report["skill_after"]["sha256_12"],
                                "changed": report["skill_after"]["changed"]},
                      "optimizer_usage": usage,
                      "truncated_calls": report["truncated_calls"]["n"],
                      "problems": problems}, indent=1, default=str))
    # A dry run is successful when it got through every phase without a boundary violation or an
    # exception. It cannot change a skill, so "unchanged" must not be read as failure.
    if args.dry_run:
        return 1 if (call.leaks or any("error" in str(r) for r in records)) else 0
    return 1 if (problems and not report["skill_after"]["changed"]) else 0


def _write(out_dir: str, report: dict, edited: str | None) -> None:
    with open(os.path.join(out_dir, "T2S_REPORT.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True, default=str)
    if edited:
        with open(os.path.join(out_dir, "trace2skill_skill.md"), "w", encoding="utf-8") as f:
            f.write(edited if edited.endswith("\n") else edited + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", default="")
    r.add_argument("--s0-dir", required=True)
    r.add_argument("--skill", required=True)
    r.add_argument("--split-dir", default=os.environ.get("OQA_SPLIT4_DIR", ""))
    r.add_argument("--optimizer-model", default="deepseek-flash")
    r.add_argument("--optimizer-max-tokens", type=int, default=0,
                   help="raise the optimizer's OUTPUT cap. 0 keeps the config's value. The backend "
                        "clamps each call to the configured max, so this must go through the config.")
    r.add_argument("--merge-batch-size", type=int, default=MERGE_BATCH_SIZE)
    r.add_argument("--max-merge-levels", type=int, default=MAX_MERGE_LEVELS)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
