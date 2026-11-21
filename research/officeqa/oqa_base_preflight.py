#!/usr/bin/env python3
"""The bounded preflight for the two-baseline OfficeQA campaign. Rule OQA-BASE-1.

FREE. No key is loaded, no HTTP request is made, no document is fetched. Everything here is a local
assertion about files and about the configuration objects the paid stages will use, which is the point: a
preflight that spends money to discover the configuration is wrong has already failed.

WHAT IT CHECKS, and why each check exists rather than being assumed.

  1. SPLIT INTEGRITY. The materialized split directory is compared against the frozen manifest id by id, in
     all three roles. A split directory is generated from a CSV, and a CSV column read with the wrong
     separator silently produces the wrong set - exactly the bug fixed in commit 455e267, where
     `source_files` split on commas instead of newlines. Counts are not enough; the ids must match.

  2. DISJOINTNESS, recomputed here. The manifest asserts it, but the manifest and the materialized directory
     are two different artifacts and only the second one is what the trainer reads.

  3. THE RESOLVED CONFIGURATION. The config is loaded through the same loader the trainer uses, and the
     fields that define the experiment are printed from the RESOLVED object, not read off the YAML text.
     A `_base_` chain means the YAML text of one file is not the configuration.

  4. THE EFFECTIVE OUTBOUND SETTINGS. This is the check the project learned to want. Two config fields look
     like the actor's output cap and only one is it: `model.target_openai_compatible_max_tokens` is
     transmitted, `env.max_completion_tokens` reaches no prompt and no API call and feeds only the
     provenance fingerprint. So the backend's own config object is built and asserted, together with the
     environment variables that carry the recovery policy and the thinking switch. The GROUND TRUTH for
     what is actually transmitted is a call log, which only a paid stage can produce; `--prior-collection`
     supplies one from a previous run so the assertion has real evidence behind it, and the s0 stage
     re-asserts it against its own log afterwards.

  5. PRIOR-COLLECTION COMPATIBILITY. Whether saved S0 trajectories may count toward Trace2Skill's 48 is a
     question about identity, not convenience: the skill bytes, the settings fingerprint, the transmitted
     serving controls and the recovery policy must all match. The verdict is reported; reuse is never
     assumed from a directory name or a date.

  6. THE PLANNED WORKLOAD AGAINST THE ALLOWANCE. The episode and call counts the design implies are
     computed and compared with the phase ceilings, so a campaign that cannot fit is not started.

Exit status is 1 if any check FAILS, 0 otherwise. A WARN does not fail the preflight.

usage:
  python3 -I research/officeqa/oqa_base_preflight.py --config <cfg> --frozen <json> \
      --split-dir <dir> --docs-dir <dir> [--prior-collection <dir>] --out <json>
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import sys

RULE = "OQA-BASE-1"
SCHEMA = "lso.officeqa_base_preflight.v1"

# The design, as numbers, so the workload arithmetic is checkable rather than asserted in prose.
DESIGN = {
    "train": 12, "val": 12, "heldout": 30,
    "epochs": 4, "slow_update_samples": 6, "s0_passes": 4, "heldout_passes": 2,
}
# What must be transmitted. Each value is the one the answer-recovery work established.
REQUIRED_OUTBOUND = {
    "model": "qwen3-5-4b",
    "max_tokens": 8000,
    "temperature": 0.7,
    "base_url": "https://api.empiriolabs.ai/v1",
}
REQUIRED_ENV = {
    "SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK": "committed_answer",
    "TARGET_OPENAI_COMPATIBLE_EXTRA_BODY": '{"enable_thinking": false}',
}
# The materialized layout is train/ val/ test/ (oqa_materialize.materialize_frozen); the trainer reaches
# val and test through the aliases `valid_seen` and `valid_unseen` (skillopt/datasets/base.py _SPLIT_ALIAS).
SPLIT_ROLE_DIR = {"train": "train", "val": "val", "heldout": "test"}

# The backend's own fallbacks, which are a live hazard rather than a hypothetical: with the role
# environment unset, `_initial_config` yields gpt-4o-mini on OpenAI's endpoint. A preflight that reads the
# backend without applying the config reports exactly these and calls them the campaign's settings.
BACKEND_FALLBACKS = {"model": "gpt-4o-mini", "base_url_host": "api.openai.com"}


def is_backend_fallback(model, base_url) -> bool:
    """True if these are the backend's unconfigured defaults rather than a configured endpoint."""
    return (str(model) == BACKEND_FALLBACKS["model"]
            or BACKEND_FALLBACKS["base_url_host"] in str(base_url))


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tree() -> str:
    """`python -I` implies -E, so PYTHONPATH is ignored; the extracted tree is put on sys.path explicitly."""
    return os.environ.get("OQA_TREE") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _check(results: list, name: str, ok: bool, detail, *, warn_only: bool = False) -> bool:
    results.append({"check": name, "verdict": ("PASS" if ok else ("WARN" if warn_only else "FAIL")),
                    "detail": detail})
    return ok or warn_only


def _split_ids(split_dir: str, role: str) -> tuple[list, str]:
    """The ids the materialized split directory actually holds for one role."""
    sub = os.path.join(split_dir, SPLIT_ROLE_DIR[role])
    for name in sorted(os.listdir(sub)) if os.path.isdir(sub) else []:
        if name.endswith(".csv"):
            path = os.path.join(sub, name)
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            key = next((k for k in (rows[0].keys() if rows else []) if k.lower() in ("id", "uid")), None)
            return ([str(r[key]).strip() for r in rows] if key else []), path
    return [], os.path.join(sub, "<no csv>")


def check_splits(results: list, frozen: dict, split_dir: str) -> dict:
    found = {}
    for role in ("train", "val", "heldout"):
        want = sorted(frozen["sets"][role]["ids"])
        got, path = _split_ids(split_dir, role)
        found[role] = {"path": path, "n": len(got), "ids": sorted(got)}
        _check(results, f"split/{role}/count", len(got) == DESIGN[role],
               {"expected": DESIGN[role], "found": len(got), "path": path})
        _check(results, f"split/{role}/ids_match_frozen", sorted(got) == want,
               {"only_in_frozen": sorted(set(want) - set(got)),
                "only_in_split_dir": sorted(set(got) - set(want))})
    for a, b in (("train", "val"), ("train", "heldout"), ("val", "heldout")):
        overlap = sorted(set(found[a]["ids"]) & set(found[b]["ids"]))
        _check(results, f"split/disjoint/{a}|{b}", not overlap, {"overlap": overlap})
    return found


def check_config(results: list, cfg_path: str) -> dict:
    sys.path.insert(0, _tree())
    resolved: dict = {}
    try:
        from skillopt.config import load_config  # type: ignore
        cfg = load_config(cfg_path)
    except Exception as exc:  # noqa: BLE001 - the loader's failure IS the finding
        _check(results, "config/loads", False, {"error": f"{type(exc).__name__}: {exc}"})
        return resolved

    def _get(path: str, default=None):
        """Read one field from the RESOLVED config, tolerating either shape.

        load_config returns "structured or flat depending on the YAML", so a value may live at
        cfg["env"]["workers"] or at cfg["env.workers"]. Both are tried before giving up, so a loader that
        flattens does not make every config check silently report None.
        """
        if path in cfg:
            return cfg[path]
        node = cfg
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    resolved = {
        "env.split_mode": _get("env.split_mode"),
        "env.search_mode": _get("env.search_mode"),
        "env.use_local_tools": _get("env.use_local_tools"),
        "env.workers": _get("env.workers"),
        "env.max_tool_turns": _get("env.max_tool_turns"),
        "env.max_completion_tokens": _get("env.max_completion_tokens"),
        "env.limit": _get("env.limit"),
        "train.num_epochs": _get("train.num_epochs"),
        "train.batch_size": _get("train.batch_size"),
        "optimizer.use_slow_update": _get("optimizer.use_slow_update"),
        "optimizer.slow_update_gate_with_selection": _get("optimizer.slow_update_gate_with_selection"),
        "optimizer.use_meta_skill": _get("optimizer.use_meta_skill"),
        "optimizer.slow_update_samples": _get("optimizer.slow_update_samples"),
        "optimizer.longitudinal_pair_policy": _get("optimizer.longitudinal_pair_policy"),
        "evaluation.use_gate": _get("evaluation.use_gate"),
        "evaluation.sel_env_num": _get("evaluation.sel_env_num"),
        "evaluation.eval_test": _get("evaluation.eval_test"),
        "model.target_backend": _get("model.target_backend"),
        "model.optimizer_backend": _get("model.optimizer_backend"),
        "model.target_openai_compatible_max_tokens": _get("model.target_openai_compatible_max_tokens"),
        "model.target_openai_compatible_temperature": _get("model.target_openai_compatible_temperature"),
        "model.target_openai_compatible_model": _get("model.target_openai_compatible_model"),
        "model.target_openai_compatible_base_url": _get("model.target_openai_compatible_base_url"),
    }
    _check(results, "config/loads", True, {"path": cfg_path})
    _check(results, "config/epochs", resolved["train.num_epochs"] == DESIGN["epochs"],
           {"expected": DESIGN["epochs"], "found": resolved["train.num_epochs"]})
    _check(results, "config/slow_update_samples",
           resolved["optimizer.slow_update_samples"] == DESIGN["slow_update_samples"],
           {"expected": DESIGN["slow_update_samples"],
            "found": resolved["optimizer.slow_update_samples"]})
    _check(results, "config/slow_update_is_gated",
           bool(resolved["optimizer.use_slow_update"])
           and bool(resolved["optimizer.slow_update_gate_with_selection"]),
           {"use_slow_update": resolved["optimizer.use_slow_update"],
            "gate_with_selection": resolved["optimizer.slow_update_gate_with_selection"]})
    _check(results, "config/meta_skill", bool(resolved["optimizer.use_meta_skill"]),
           {"found": resolved["optimizer.use_meta_skill"]})
    _check(results, "config/gate_on", bool(resolved["evaluation.use_gate"]),
           {"found": resolved["evaluation.use_gate"]})
    _check(results, "config/eval_test_off", resolved["evaluation.eval_test"] is False,
           {"found": resolved["evaluation.eval_test"],
            "why": "the held-out set is scored by its own stage over the DISTINCT endpoints, so the "
                   "trainer must not touch it"})
    # ONE ordinary update per epoch requires batch_size >= the training set, so ceil(12/bs) == 1.
    bs = resolved["train.batch_size"]
    _check(results, "config/one_step_per_epoch", isinstance(bs, int) and bs >= DESIGN["train"],
           {"batch_size": bs, "train": DESIGN["train"],
            "steps_per_epoch": (DESIGN["train"] + bs - 1) // bs if isinstance(bs, int) and bs else None})
    _check(results, "config/offline_tools",
           resolved["env.search_mode"] == "offline" and bool(resolved["env.use_local_tools"]),
           {"search_mode": resolved["env.search_mode"],
            "use_local_tools": resolved["env.use_local_tools"]})
    _check(results, "config/turn_limit", resolved["env.max_tool_turns"] == 24,
           {"found": resolved["env.max_tool_turns"]})
    return resolved


def check_outbound(results: list, resolved: dict) -> dict:
    """The EFFECTIVE actor settings, measured the way the trainer produces them.

    THE MISTAKE THIS FUNCTION MADE ON ITS FIRST RUN, kept here because it is the whole point of the
    check. It read `_initial_config("target")` in a process that had never applied the config, and
    `_initial_config` reads only environment variables. With those unset the backend falls back to
    `gpt-4o-mini` on `https://api.openai.com/v1`, so the preflight reported the BACKEND'S DEFAULTS and
    called them the campaign's settings. Job 66552338 failed on exactly that and it was right to.

    The config never reaches the backend by itself. `skillopt.engine.trainer` calls
    `configure_openai_compatible(...)`, which writes the resolved values into both the module's config
    object and the environment (`_update_config`), so the CONFIG wins and the environment is its record.
    This function therefore performs the same application before asserting anything - which is what
    makes the assertion a statement about the paid stages rather than about an unconfigured process.
    """
    sys.path.insert(0, _tree())
    eff: dict = {}
    try:
        from skillopt.model import openai_compatible_backend as B  # type: ignore
        # The same call the trainer makes, with the same fields, so the path under test is the real one.
        B.configure_openai_compatible(
            target_base_url=resolved.get("model.target_openai_compatible_base_url") or None,
            target_model=resolved.get("model.target_openai_compatible_model") or None,
            target_temperature=resolved.get("model.target_openai_compatible_temperature"),
            target_max_tokens=resolved.get("model.target_openai_compatible_max_tokens"),
        )
        cfg = B._config_for("target")      # the LIVE config, after the config has been applied
        eff = {"base_url": cfg.base_url, "model": cfg.deployment, "max_tokens": cfg.max_tokens,
               "temperature": cfg.temperature, "timeout_seconds": cfg.timeout_seconds,
               "api_key_present": bool(cfg.api_key),
               "applied_via": "configure_openai_compatible, as skillopt.engine.trainer does"}
    except Exception as exc:  # noqa: BLE001
        _check(results, "outbound/backend_config_builds", False,
               {"error": f"{type(exc).__name__}: {exc}"})
        return eff
    _check(results, "outbound/backend_config_builds", True, {"from": "openai_compatible_backend"})
    # The backend's fallbacks are a live hazard, not a hypothetical: an unset base URL silently becomes
    # OpenAI's. Assert they are NOT what is in force, so a future config that fails to apply is caught
    # here rather than by a bill.
    _check(results, "outbound/not_the_backend_fallback",
           not is_backend_fallback(eff.get("model"), eff.get("base_url")),
           {"effective_model": eff.get("model"), "effective_base_url": eff.get("base_url"),
            "fallbacks_that_must_not_be_in_force": BACKEND_FALLBACKS})
    for key, want in REQUIRED_OUTBOUND.items():
        got = eff.get(key)
        ok = (abs(float(got) - float(want)) < 1e-9) if isinstance(want, float) else (got == want)
        _check(results, f"outbound/{key}", ok, {"expected": want, "effective": got})
    for key, want in REQUIRED_ENV.items():
        got = os.environ.get(key)
        ok = got is not None and json.loads(got) == json.loads(want) \
            if key.endswith("EXTRA_BODY") else got == want
        _check(results, f"outbound/env/{key}", bool(ok), {"expected": want, "found": got})
    # The two token fields, side by side, so the inert one can never be mistaken for the cap again.
    _check(results, "outbound/the_two_token_fields_differ_as_documented",
           int(eff.get("max_tokens") or 0) == 8000
           and int(resolved.get("env.max_completion_tokens") or 0) == 16384,
           {"transmitted_cap_model.target_openai_compatible_max_tokens": eff.get("max_tokens"),
            "inert_env.max_completion_tokens": resolved.get("env.max_completion_tokens"),
            "why": "env.max_completion_tokens reaches no prompt and no API call; it feeds only "
                   "rollout_settings_fingerprint, which the provenance guard compares"},
           warn_only=True)
    return eff


def check_prior_collection(results: list, prior: str, skill_path: str) -> dict:
    """Whether saved S0 trajectories may count toward Trace2Skill's 48. Identity, not convenience."""
    out: dict = {"supplied": bool(prior)}
    if not prior:
        _check(results, "prior_collection/supplied", True,
               {"note": "none supplied; all 48 S0 trajectories will be collected fresh"}, warn_only=True)
        return out
    freeze_path = os.path.join(prior, "COLLECT_FREEZE.json")
    if not os.path.isfile(freeze_path):
        _check(results, "prior_collection/freeze_present", False, {"looked_for": freeze_path},
               warn_only=True)
        return out
    with open(freeze_path, encoding="utf-8") as f:
        freeze = json.load(f)
    want_skill = None
    if os.path.isfile(skill_path):
        with open(skill_path, "rb") as f:
            want_skill = hashlib.sha256(f.read()).hexdigest()[:12]
    cond = ((freeze.get("model_and_generation") or {}).get("condition") or {})
    out.update({
        "freeze": freeze.get("freeze_sha256_12"),
        "ids": freeze.get("ids") or [],
        "attempts_per_question": freeze.get("attempts_per_question"),
        "recovery_policy": freeze.get("answer_recovery_policy"),
        "skill_sha256_12_recorded": (freeze.get("skill") or {}).get("sha256_12"),
        "skill_sha256_12_now": want_skill,
        "limits": freeze.get("limits") or {},
        "extra_body": (cond.get("extra_body") or {}),
        "settings_fingerprint": freeze.get("settings_fingerprint"),
    })
    _check(results, "prior_collection/recovery_policy", out["recovery_policy"] == "committed_answer",
           {"found": out["recovery_policy"]}, warn_only=True)
    _check(results, "prior_collection/thinking_off", out["extra_body"].get("enable_thinking") is False,
           {"found": out["extra_body"]}, warn_only=True)
    _check(results, "prior_collection/turn_limit_matches", out["limits"].get("max_tool_turns") == 24,
           {"found": out["limits"].get("max_tool_turns")}, warn_only=True)
    same_skill = (want_skill is not None and out["skill_sha256_12_recorded"] == want_skill)
    _check(results, "prior_collection/skill_identical", same_skill,
           {"recorded": out["skill_sha256_12_recorded"], "now": want_skill,
            "why": "a trajectory produced under different skill bytes is a trajectory of a different "
                   "policy and cannot count as an S0 attempt"}, warn_only=True)
    out["verdict"] = ("REUSABLE_SUBJECT_TO_PER_ROW_GUARD" if same_skill
                      else "NOT_REUSABLE_recorded_skill_differs")
    return out


def check_workload(results: list, caps: dict) -> dict:
    """The episode and call counts the design implies, against this phase's ceilings."""
    d = DESIGN
    # Actor EPISODES. Original SkillOpt: epoch 1 reuses the injected S0 pass and its gate baseline, so it
    # pays only for the candidate's gate pass. Epochs 2-4 each pay for a training rollout, the slow
    # update's two sides over `slow_update_samples` questions, and two gate passes (ordinary + slow).
    arm_e1 = d["val"]
    arm_rest = d["epochs"] - 1
    arm_each = d["train"] + 2 * d["slow_update_samples"] + 2 * d["val"]
    episodes = {
        "s0_train_4_passes": d["s0_passes"] * d["train"],
        "s0_val_baseline": d["val"],
        "original_skillopt_epoch1_gate": arm_e1,
        "original_skillopt_epochs_2_to_4": arm_rest * arm_each,
        "trace2skill_rollouts": 0,
        "heldout_3_endpoints_2_passes": 3 * d["heldout_passes"] * d["heldout"],
    }
    total_episodes = sum(episodes.values())
    # Attempts per episode, measured: 285 actor HTTP attempts over 24 episodes in the 2026-11-18
    # collection = 11.9. Rounded UP to 14 to leave room for longer tool loops and retries.
    per_episode = 14
    attempts = total_episodes * per_episode
    usd = attempts * (0.180 / 286)          # measured cost per attempt, same collection
    opt_calls = {"original_skillopt": d["epochs"] * 3, "trace2skill": d["s0_passes"] * d["train"] + 8}
    total_opt = sum(opt_calls.values())
    out = {"episodes": episodes, "total_episodes": total_episodes,
           "attempts_per_episode_assumed": per_episode, "projected_actor_attempts": attempts,
           "projected_actor_usd": round(usd, 3), "optimizer_calls": opt_calls,
           "projected_optimizer_calls": total_opt, "caps": caps}
    _check(results, "workload/actor_attempts_fit", attempts <= caps["actor_attempts"],
           {"projected": attempts, "cap": caps["actor_attempts"]})
    _check(results, "workload/actor_usd_fit", usd <= caps["actor_usd"],
           {"projected_usd": round(usd, 3), "cap": caps["actor_usd"]})
    _check(results, "workload/optimizer_calls_fit", total_opt <= caps["opt_calls"],
           {"projected": total_opt, "cap": caps["opt_calls"]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--split-dir", required=True)
    ap.add_argument("--docs-dir", required=True)
    ap.add_argument("--prior-collection", default="")
    ap.add_argument("--skill", default="skillopt/envs/officeqa/skills/initial.md")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    with open(a.frozen, encoding="utf-8") as f:
        frozen = json.load(f)
    results: list = []

    _check(results, "frozen/rule", frozen.get("rule") == "OQA-SEL-4", {"found": frozen.get("rule")})
    _check(results, "frozen/disjoint", bool(frozen.get("disjoint")), {"found": frozen.get("disjoint")})
    _check(results, "frozen/no_problems", not frozen.get("problems"),
           {"problems": frozen.get("problems")})
    _check(results, "docs_dir/exists", os.path.isdir(a.docs_dir), {"path": a.docs_dir})

    found = check_splits(results, frozen, a.split_dir)
    resolved = check_config(results, a.config)
    effective = check_outbound(results, resolved)
    prior = check_prior_collection(results, a.prior_collection, a.skill)
    caps = {
        "actor_attempts": int(os.environ.get("SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET", "20000")),
        "actor_usd": float(os.environ.get("SKILLOPT_LEDGER_MAX_USD_TARGET", "15.00")),
        "opt_calls": int(os.environ.get("SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER", "400")),
        "opt_usd": float(os.environ.get("SKILLOPT_LEDGER_MAX_USD_OPTIMIZER", "20.00")),
    }
    workload = check_workload(results, caps)

    failed = [r for r in results if r["verdict"] == "FAIL"]
    warned = [r for r in results if r["verdict"] == "WARN"]
    doc = {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(),
        "commit": os.environ.get("OQA_COMMIT"),
        "inputs": {"config": a.config, "frozen": a.frozen, "split_dir": a.split_dir,
                   "docs_dir": a.docs_dir, "prior_collection": a.prior_collection},
        "design": DESIGN,
        "split_dir_contents": {k: {"n": v["n"], "path": v["path"]} for k, v in found.items()},
        "resolved_config": resolved,
        "effective_outbound_settings": effective,
        "prior_collection": prior,
        "workload": workload,
        "checks": results,
        "n_pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "n_warn": len(warned), "n_fail": len(failed),
        "verdict": "READY" if not failed else "BLOCKED",
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({"out": a.out, "verdict": doc["verdict"], "n_pass": doc["n_pass"],
                      "n_warn": doc["n_warn"], "n_fail": doc["n_fail"],
                      "failed": [r["check"] for r in failed],
                      "warned": [r["check"] for r in warned],
                      "projected": {k: workload[k] for k in
                                    ("total_episodes", "projected_actor_attempts",
                                     "projected_actor_usd", "projected_optimizer_calls")}},
                     indent=1, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
