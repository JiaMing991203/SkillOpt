"""Prove, inside the process that is about to spend money, that each role's calls go where they should.

WHY A SEPARATE PROCESS CANNOT DO THIS. `skillopt.model.chat_optimizer` and `chat_target_messages` are ROUTERS.
Which backend they dispatch to is process state set from the config at startup, so a checker running in its own
process can establish only that the config file and the code would resolve correctly - not that the process that
sends actually did resolve that way. Job 66271190 is the recorded cost of that gap: the openai-compatible
endpoint, model and key were all correct, the router dispatched to AZURE because `OPTIMIZER_BACKEND` defaults to
"openai_chat", the Azure call raised, `run_slow_update` swallowed the exception and the result was recorded as
`no_content`. Zero attempts, zero charge - and a result that reads like a DeepSeek failure and is not one.

WHAT THIS CHECKS, for each role named in the requirement:

  1. THE SELECTED BACKEND, read back from the live router after the trainer has configured it.
  2. THE RESOLVED CONFIG - base URL and model - read back from the backend, so no library default can stand in.
     An unset base URL sends to api.openai.com; an unset model silently becomes "gpt-4o-mini".
  3. THE DISPATCH PATH, proven by temporarily replacing `_chat_messages_impl` with a function that raises a
     unique marker and making one call through the public router. The marker is raised before any client is
     built, so NOTHING IS SENT and no attempt is consumed.
  4. That the API key is present and non-empty.

It also records the state of the spend ledger, so one artifact answers both "where did the calls go" and "what
was authorized".

OFF BY DEFAULT. With ``SKILLOPT_REQUIRE_ROUTE`` unset this module does nothing and the trainer is upstream's.

Format of SKILLOPT_REQUIRE_ROUTE - semicolons between roles, pipes inside one:

    target=openai_compatible|https://api.empiriolabs.ai/v1|qwen3-5-4b;optimizer=openai_compatible|https://api.deepseek.com|deepseek-flash

A role may be given as ``<role>=none`` to assert that it must NOT be usable in this job - the shared rollout and
the held-out evaluation make no optimizer call, and a job that cannot reach the optimizer at all is a stronger
guarantee than one that merely does not intend to.

SKILLOPT_ROUTE_GUARD_RECORD names a JSON file for the verdict. Nothing secret is written: the key contributes
only its length.
"""
from __future__ import annotations

import datetime
import json
import os

MARKER = "skillopt-route-guard-dispatch-marker"


class RouteMismatch(RuntimeError):
    """The live route is not the one the job requires. Nothing may be sent."""


def _requirement() -> dict[str, dict]:
    raw = os.environ.get("SKILLOPT_REQUIRE_ROUTE", "").strip()
    if not raw:
        return {}
    out: dict[str, dict] = {}
    for clause in raw.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        if "=" not in clause:
            raise RouteMismatch(f"SKILLOPT_REQUIRE_ROUTE clause {clause!r} has no '='")
        role, spec = clause.split("=", 1)
        role = role.strip().lower()
        parts = [p.strip() for p in spec.split("|")]
        if parts and parts[0] == "none":
            out[role] = {"forbidden": True}
            continue
        if len(parts) != 3:
            raise RouteMismatch(
                f"SKILLOPT_REQUIRE_ROUTE clause {clause!r}: want '<role>=<backend>|<base_url>|<model>' or "
                f"'<role>=none'")
        out[role] = {"forbidden": False, "backend": parts[0], "base_url": parts[1], "model": parts[2]}
    return out


def _dispatch_check(role: str) -> str:
    """Prove the router reaches the openai-compatible backend for `role`, sending nothing."""
    import skillopt.model as M
    import skillopt.model.openai_compatible_backend as B

    def raiser(*_a, **_k):
        raise RuntimeError(MARKER)

    real = B._chat_messages_impl
    B._chat_messages_impl = raiser
    try:
        if role == "optimizer":
            M.chat_optimizer(system="route-guard", user="route-guard",
                             max_completion_tokens=16, retries=1, stage="route_guard")
        else:
            M.chat_target_messages([{"role": "user", "content": "route-guard"}],
                                   max_completion_tokens=16, retries=1, stage="route_guard")
    except Exception as exc:  # noqa: BLE001 - the marker IS the expected outcome
        if MARKER not in str(exc):
            raise RouteMismatch(
                f"{role}: a call did NOT reach the openai-compatible backend. It raised "
                f"{type(exc).__name__}: {str(exc)[:300]}") from exc
        return "PASS: the call reaches the openai-compatible backend"
    else:
        raise RouteMismatch(f"{role}: the call returned without reaching _chat_messages_impl, so the router "
                            f"dispatched somewhere else entirely")
    finally:
        B._chat_messages_impl = real


def _check_role(role: str, want: dict) -> dict:
    from skillopt.model.backend_config import get_optimizer_backend, get_target_backend
    import skillopt.model.openai_compatible_backend as B

    live = get_optimizer_backend() if role == "optimizer" else get_target_backend()
    if want.get("forbidden"):
        key = os.environ.get(f"{role.upper()}_OPENAI_COMPATIBLE_API_KEY", "").strip()
        if key:
            raise RouteMismatch(
                f"{role}: this job requires that the {role} role be unusable, but "
                f"{role.upper()}_OPENAI_COMPATIBLE_API_KEY is set. Unset it before the run.")
        return {"role": role, "required": "none", "live_backend": live, "api_key_present": False,
                "verdict": "PASS: no key, so no call of this role can be made"}

    if live != want["backend"]:
        raise RouteMismatch(
            f"{role}: the live backend is {live!r}, not the required {want['backend']!r}. This is the Azure "
            f"misdispatch of job 66271190; nothing was sent.")
    key = os.environ.get(f"{role.upper()}_OPENAI_COMPATIBLE_API_KEY", "").strip()
    if not key:
        raise RouteMismatch(f"{role}: {role.upper()}_OPENAI_COMPATIBLE_API_KEY is empty")
    B._reset_clients()
    cfg = B._config_for(role)
    got = {"role": role, "required": want, "live_backend": live, "base_url": cfg.base_url,
           "model": cfg.deployment, "max_tokens": cfg.max_tokens, "temperature": cfg.temperature,
           "timeout_seconds": cfg.timeout_seconds, "api_key_len": len(key)}
    if cfg.base_url.rstrip("/") != want["base_url"].rstrip("/"):
        raise RouteMismatch(f"{role}: the resolved base URL is {cfg.base_url!r}, not {want['base_url']!r}")
    if cfg.deployment != want["model"]:
        raise RouteMismatch(f"{role}: the resolved model is {cfg.deployment!r}, not {want['model']!r}")
    got["dispatch_check"] = _dispatch_check(role)
    got["verdict"] = "PASS"
    return got


def verify() -> dict:
    """Check every required role. Raises RouteMismatch on the first failure; returns the record on success."""
    requirement = _requirement()
    if not requirement:
        return {"required": False}
    from skillopt.model import spend_ledger

    record = {
        "schema": "lso.route_guard.v1",
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "slurm_job": os.environ.get("SLURM_JOB_ID", ""),
        "slurm_restart_count": os.environ.get("SLURM_RESTART_COUNT", ""),
        "required": True,
        "roles": {},
    }
    for role in sorted(requirement):
        record["roles"][role] = _check_role(role, requirement[role])
    try:
        record["spend_ledger"] = spend_ledger.snapshot()
    except Exception as exc:  # noqa: BLE001 - reported, and the ledger itself fails closed on use
        record["spend_ledger"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    record["verdict"] = "PASS"
    path = os.environ.get("SKILLOPT_ROUTE_GUARD_RECORD", "").strip()
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, sort_keys=True)
        except OSError:
            pass
    print("  [route guard] " + "; ".join(
        f"{role}: {info.get('live_backend')} -> {info.get('base_url', 'n/a')} "
        f"({info.get('model', 'no key')})" for role, info in record["roles"].items()))
    return record


def verify_or_die() -> dict:
    """What the entry points call: verify, and let a mismatch stop the job before anything is sent."""
    try:
        return verify()
    except RouteMismatch as exc:
        print(f"\n  ROUTE GUARD FAILED: {exc}\n  Nothing was sent.\n")
        raise
