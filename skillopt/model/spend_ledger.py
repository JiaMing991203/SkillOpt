"""A durable, shared authorization ledger for paid HTTP attempts.

WHY THIS EXISTS. The per-process counters this project used before (``research/officeqa/oqa_api_smoke.py``,
``research/officeqa/oqa_slow_probe.py``) bound one driver in one process. The three-arm campaign runs up to four
Slurm jobs at once against ONE account-wide authorization, and a Slurm requeue restarts a job with a fresh
process, so a per-process counter would respend the whole authorization on every restart. That failure mode is
recorded in ``research/cluster/oqa_api_body.sh`` and in the launch plan; this module is the fix.

WHAT IT GUARANTEES, and how.

  Nothing is counted after the fact. Before an attempt reaches the provider, the process must already hold a
  CLAIM covering it. Claims are taken in blocks from a shared directory, and a claim is created with
  ``O_CREAT|O_EXCL`` under a sequence number, so two processes can never be granted the same block. The sum of
  all claims is therefore an upper bound on what every process together may spend, and that sum is checked
  against the cap before each new block is granted:

        spent  <=  claimed  <=  cap

  Concurrency. Claims live in one directory on shared storage and carry their amount IN THE FILE NAME, so
  granting a block costs one directory listing and one exclusive create. No lock is taken and no file is
  rewritten, which is what makes this safe on NFS.

  Restarts. Claims and the per-attempt record are files. A restarted process re-reads the directory and sees
  every block its predecessor took, so a requeue cannot reset the counter. The unused remainder of a dead
  process's block is LOST rather than recycled - the conservative direction, and the reason blocks are small.

  Money. A call's cost is known only after it returns, so the USD cap is enforced by requiring headroom of
  ``per_call_reserve_usd`` before each attempt. A call that costs more than its reserve is honoured and recorded
  as ``reserve_exceeded``; the overshoot is bounded by that excess and is never silent.

  Fail closed. If the ledger is configured but its directory cannot be read or written, or a USD cap is set
  without prices, every attempt raises. A misconfigured ledger spends nothing.

INERT BY DEFAULT. With ``SKILLOPT_SPEND_LEDGER_DIR`` unset this module does nothing at all and the backends
behave exactly as upstream. It is instrumentation behind an explicit flag (CLAUDE.md section 5).

Environment:
  SKILLOPT_SPEND_LEDGER_DIR                 the shared ledger directory. Unset -> inert.
  SKILLOPT_LEDGER_MAX_ATTEMPTS_<ROLE>       attempt cap for that role (TARGET / OPTIMIZER). Required.
  SKILLOPT_LEDGER_MAX_USD_<ROLE>            USD cap for that role. Required.
  SKILLOPT_LEDGER_PRICE_<ROLE>              "prompt,completion,cached" USD per 1M tokens. Required with a USD cap.
  SKILLOPT_LEDGER_RESERVE_USD_<ROLE>        headroom required before one attempt (default 0.02 / 0.15).
  SKILLOPT_LEDGER_BLOCK_ATTEMPTS_<ROLE>     attempts per claimed block (default 50 / 4).
  SKILLOPT_LEDGER_BLOCK_USD_<ROLE>          USD per claimed block (default 0.25 / 0.30).
  SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_<ROLE>   optional ceiling on what THIS process may claim, so one runaway job
  SKILLOPT_LEDGER_JOB_MAX_USD_<ROLE>        cannot consume an authorization the other arms still need. It is a
                                            second, per-process limit; the shared cap still applies.

usage (verification, no HTTP):  python3 -I -m skillopt.model.spend_ledger report <dir>
"""
from __future__ import annotations

import datetime
import json
import os
import re
import threading

SCHEMA = "lso.spend_ledger.v1"
ROLES = ("target", "optimizer")

_DEFAULT_RESERVE_USD = {"target": 0.02, "optimizer": 0.15}
_DEFAULT_BLOCK_ATTEMPTS = {"target": 50, "optimizer": 4}
_DEFAULT_BLOCK_USD = {"target": 0.25, "optimizer": 0.30}

_CLAIM_NAME = re.compile(r"^(\d{6})-(target|optimizer)-(attempts|usdmicro)-(\d+)\.json$")

_lock = threading.Lock()
_state: dict[str, dict] = {}
_owner = ""


class BudgetExhausted(RuntimeError):
    """The authorization this ledger holds has no room for another attempt."""


class LedgerUnusable(RuntimeError):
    """The ledger is configured but cannot be used. Nothing may be spent."""


# ── configuration ─────────────────────────────────────────────────────────────

def directory() -> str:
    return os.environ.get("SKILLOPT_SPEND_LEDGER_DIR", "").strip()


def active() -> bool:
    return bool(directory())


def _env_float(name: str, default: float | None) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        if default is None:
            raise LedgerUnusable(f"{name} is required when the spend ledger is active")
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LedgerUnusable(f"{name}={raw!r} is not a number") from exc


def _env_int(name: str, default: int | None) -> int:
    return int(_env_float(name, None if default is None else float(default)))


def _prices(role: str) -> tuple[float, float, float]:
    """USD per 1M tokens: (prompt, completion, cached_prompt)."""
    raw = os.environ.get(f"SKILLOPT_LEDGER_PRICE_{role.upper()}", "").strip()
    if not raw:
        raise LedgerUnusable(
            f"SKILLOPT_LEDGER_PRICE_{role.upper()} is required: a USD cap cannot be enforced without prices")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) not in (2, 3):
        raise LedgerUnusable(f"SKILLOPT_LEDGER_PRICE_{role.upper()}={raw!r}: want 'prompt,completion[,cached]'")
    try:
        values = [float(p) for p in parts]
    except ValueError as exc:
        raise LedgerUnusable(f"SKILLOPT_LEDGER_PRICE_{role.upper()}={raw!r} is not numeric") from exc
    if len(values) == 2:
        values.append(values[0])
    return values[0], values[1], values[2]


def config(role: str) -> dict:
    role = role.lower()
    if role not in ROLES:
        raise LedgerUnusable(f"unknown role {role!r}")
    up = role.upper()
    prompt, completion, cached = _prices(role)
    return {
        "role": role,
        "cap_attempts": _env_int(f"SKILLOPT_LEDGER_MAX_ATTEMPTS_{up}", None),
        "cap_usd": _env_float(f"SKILLOPT_LEDGER_MAX_USD_{up}", None),
        "reserve_usd": _env_float(f"SKILLOPT_LEDGER_RESERVE_USD_{up}", _DEFAULT_RESERVE_USD[role]),
        "block_attempts": _env_int(f"SKILLOPT_LEDGER_BLOCK_ATTEMPTS_{up}", _DEFAULT_BLOCK_ATTEMPTS[role]),
        "block_usd": _env_float(f"SKILLOPT_LEDGER_BLOCK_USD_{up}", _DEFAULT_BLOCK_USD[role]),
        "price_per_1m": {"prompt": prompt, "completion": completion, "cached_prompt": cached},
        # A second ceiling, on this process alone. 0 means "no per-process ceiling".
        "job_max_attempts": _env_int(f"SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_{up}", 0),
        "job_max_usd": _env_float(f"SKILLOPT_LEDGER_JOB_MAX_USD_{up}", 0.0),
    }


def cost_usd(role: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    prompt, completion, cached = _prices(role)
    fresh = max(0, int(prompt_tokens) - int(cached_tokens))
    return (fresh * prompt + int(cached_tokens) * cached + int(completion_tokens) * completion) / 1_000_000.0


# ── the claim allocator ───────────────────────────────────────────────────────

def _claims_dir() -> str:
    return os.path.join(directory(), "claims")


def _owner_token() -> str:
    global _owner
    if not _owner:
        job = os.environ.get("SLURM_JOB_ID", "nojob")
        restart = os.environ.get("SLURM_RESTART_COUNT", "0")
        _owner = f"{job}r{restart}p{os.getpid()}"
    return _owner


def _granted() -> dict[tuple[str, str], int]:
    """Everything every process has ever claimed, read from the claim FILE NAMES.

    The amount is in the name, so one directory listing answers the question. No file is opened and no lock is
    taken, which is what makes this safe and cheap on shared storage.
    """
    totals: dict[tuple[str, str], int] = {}
    try:
        names = os.listdir(_claims_dir())
    except OSError as exc:
        raise LedgerUnusable(f"cannot read the claim directory {_claims_dir()}: {exc}") from exc
    for name in names:
        m = _CLAIM_NAME.match(name)
        if not m:
            continue
        key = (m.group(2), m.group(3))
        totals[key] = totals.get(key, 0) + int(m.group(4))
    return totals


def _next_seq(names: list[str]) -> int:
    best = 0
    for name in names:
        m = _CLAIM_NAME.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def _grant(role: str, resource: str, amount: int, cap_amount: int, note: str) -> int:
    """Claim a block of `amount` for (role, resource). Returns what was granted; 0 means the cap is reached.

    The claim is created with O_CREAT|O_EXCL under a sequence number, so two processes racing for the same
    sequence cannot both succeed: the loser re-reads the directory and tries the next number, which also means
    it re-checks the cap against the winner's claim.
    """
    claims = _claims_dir()
    for _ in range(500):
        try:
            names = os.listdir(claims)
        except OSError as exc:
            raise LedgerUnusable(f"cannot read the claim directory {claims}: {exc}") from exc
        granted = 0
        for name in names:
            m = _CLAIM_NAME.match(name)
            if m and (m.group(2), m.group(3)) == (role, resource):
                granted += int(m.group(4))
        room = cap_amount - granted
        if room <= 0:
            return 0
        give = min(amount, room)
        seq = _next_seq(names)
        path = os.path.join(claims, f"{seq:06d}-{role}-{resource}-{give}.json")
        payload = {
            "schema": SCHEMA, "seq": seq, "role": role, "resource": resource, "amount": give,
            "owner": _owner_token(), "note": note,
            "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "granted_before_this": granted, "cap": cap_amount,
        }
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        except OSError as exc:
            raise LedgerUnusable(f"cannot create a claim in {claims}: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            raise LedgerUnusable(f"cannot write the claim {path}: {exc}") from exc
        return give
    raise LedgerUnusable(f"could not allocate a claim sequence in {claims} after 500 tries")


# ── the per-attempt record ────────────────────────────────────────────────────

def _record(entry: dict) -> None:
    path = os.path.join(directory(), "spend", f"{_owner_token()}.jsonl")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        raise LedgerUnusable(f"cannot append to the spend record {path}: {exc}") from exc


def _init(role: str) -> dict:
    st = _state.get(role)
    if st is not None:
        return st
    root = directory()
    for sub in ("claims", "spend"):
        try:
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        except OSError as exc:
            raise LedgerUnusable(f"cannot create {os.path.join(root, sub)}: {exc}") from exc
    cfg = config(role)
    st = {"cfg": cfg, "claimed_attempts": 0, "used_attempts": 0,
          "claimed_usdmicro": 0, "used_usdmicro": 0, "reserve_exceeded": 0}
    _state[role] = st
    _record({"schema": SCHEMA, "kind": "open", "role": role, "owner": _owner_token(),
             "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "slurm_job": os.environ.get("SLURM_JOB_ID", ""),
             "slurm_restart_count": os.environ.get("SLURM_RESTART_COUNT", ""),
             "cfg": cfg})
    return st


def reserve(role: str, stage: str = "") -> None:
    """Authorize ONE HTTP attempt, or raise. Must be called before the request is sent.

    Raises BudgetExhausted when the shared authorization has no room, and LedgerUnusable when the ledger itself
    cannot be trusted. Both stop the attempt.
    """
    if not active():
        return
    role = role.lower()
    with _lock:
        st = _init(role)
        cfg = st["cfg"]
        cap_usdmicro = int(round(cfg["cap_usd"] * 1_000_000))
        reserve_usdmicro = int(round(cfg["reserve_usd"] * 1_000_000))

        job_max_attempts = int(cfg["job_max_attempts"] or 0)
        if job_max_attempts and st["used_attempts"] + 1 > job_max_attempts:
            _record({"schema": SCHEMA, "kind": "refused", "role": role, "stage": stage,
                     "reason": "this job's own attempt ceiling", "job_max_attempts": job_max_attempts,
                     "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
            raise BudgetExhausted(
                f"{role}: this job's own ceiling of {job_max_attempts} attempts is reached (the shared "
                f"authorization may still have room, which the other arms need). No request was sent.")
        job_max_usd = float(cfg["job_max_usd"] or 0.0)
        if job_max_usd and st["used_usdmicro"] >= int(round(job_max_usd * 1_000_000)):
            _record({"schema": SCHEMA, "kind": "refused", "role": role, "stage": stage,
                     "reason": "this job's own usd ceiling", "job_max_usd": job_max_usd,
                     "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
            raise BudgetExhausted(
                f"{role}: this job's own ceiling of ${job_max_usd:.2f} is reached; it has spent "
                f"${st['used_usdmicro'] / 1e6:.4f}. No request was sent.")

        if st["used_attempts"] + 1 > st["claimed_attempts"]:
            got = _grant(role, "attempts", int(cfg["block_attempts"]), int(cfg["cap_attempts"]),
                         note=f"stage={stage}")
            if got <= 0:
                _record({"schema": SCHEMA, "kind": "refused", "role": role, "stage": stage,
                         "reason": "attempt cap reached", "cap_attempts": cfg["cap_attempts"],
                         "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
                raise BudgetExhausted(
                    f"{role}: the authorized attempt cap of {cfg['cap_attempts']} is fully claimed; "
                    f"this process has used {st['used_attempts']}. No request was sent.")
            st["claimed_attempts"] += got

        while st["used_usdmicro"] + reserve_usdmicro > st["claimed_usdmicro"]:
            got = _grant(role, "usdmicro", int(round(cfg["block_usd"] * 1_000_000)), cap_usdmicro,
                         note=f"stage={stage}")
            if got <= 0:
                _record({"schema": SCHEMA, "kind": "refused", "role": role, "stage": stage,
                         "reason": "usd cap reached", "cap_usd": cfg["cap_usd"],
                         "used_usd": st["used_usdmicro"] / 1e6,
                         "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
                raise BudgetExhausted(
                    f"{role}: the authorized ${cfg['cap_usd']:.2f} is fully claimed; this process has spent "
                    f"${st['used_usdmicro'] / 1e6:.4f}. No request was sent.")
            st["claimed_usdmicro"] += got

        st["used_attempts"] += 1
        _record({"schema": SCHEMA, "kind": "attempt", "role": role, "stage": stage,
                 "n_in_process": st["used_attempts"],
                 "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})


def record_usage(role: str, stage: str, prompt_tokens: int, completion_tokens: int,
                 cached_tokens: int = 0) -> float:
    """Charge one completed attempt against this process's claimed money. Returns the USD charged."""
    if not active():
        return 0.0
    role = role.lower()
    usd = cost_usd(role, prompt_tokens, completion_tokens, cached_tokens)
    with _lock:
        st = _init(role)
        micro = int(round(usd * 1_000_000))
        st["used_usdmicro"] += micro
        over = st["used_usdmicro"] > st["claimed_usdmicro"]
        if over:
            st["reserve_exceeded"] += 1
        _record({"schema": SCHEMA, "kind": "usage", "role": role, "stage": stage,
                 "prompt_tokens": int(prompt_tokens), "completion_tokens": int(completion_tokens),
                 "cached_prompt_tokens": int(cached_tokens), "usd": round(usd, 8),
                 "used_usd_in_process": round(st["used_usdmicro"] / 1e6, 6),
                 "claimed_usd_in_process": round(st["claimed_usdmicro"] / 1e6, 6),
                 "reserve_exceeded": bool(over),
                 "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return usd


def snapshot() -> dict:
    """What the shared ledger says right now. Reads the directory; makes no claim."""
    if not active():
        return {"active": False}
    granted = _granted()
    out: dict = {"active": True, "dir": directory(), "owner": _owner_token(), "roles": {}}
    for role in ROLES:
        try:
            cfg = config(role)
        except LedgerUnusable as exc:
            out["roles"][role] = {"error": str(exc)}
            continue
        st = _state.get(role) or {}
        out["roles"][role] = {
            "cap_attempts": cfg["cap_attempts"], "cap_usd": cfg["cap_usd"],
            "claimed_attempts_all_processes": granted.get((role, "attempts"), 0),
            "claimed_usd_all_processes": round(granted.get((role, "usdmicro"), 0) / 1e6, 6),
            "used_attempts_this_process": st.get("used_attempts", 0),
            "used_usd_this_process": round(st.get("used_usdmicro", 0) / 1e6, 6),
            "reserve_exceeded_this_process": st.get("reserve_exceeded", 0),
        }
    return out


def observed() -> dict:
    """Read every process's spend record: what was actually attempted and actually charged.

    This is the audit view, independent of the claims. ``claimed >= observed`` must hold.
    """
    root = os.path.join(directory(), "spend")
    out: dict[str, dict] = {r: {"attempts": 0, "usd": 0.0, "refusals": 0, "reserve_exceeded": 0,
                                "prompt_tokens": 0, "completion_tokens": 0} for r in ROLES}
    owners: dict[str, dict] = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = str(rec.get("role") or "")
                if role not in out:
                    continue
                own = owners.setdefault(name[:-6], {"attempts": 0, "usd": 0.0, "restart": rec.get(
                    "slurm_restart_count", "")})
                kind = rec.get("kind")
                if kind == "attempt":
                    out[role]["attempts"] += 1
                    own["attempts"] += 1
                elif kind == "usage":
                    out[role]["usd"] += float(rec.get("usd") or 0.0)
                    out[role]["prompt_tokens"] += int(rec.get("prompt_tokens") or 0)
                    out[role]["completion_tokens"] += int(rec.get("completion_tokens") or 0)
                    own["usd"] += float(rec.get("usd") or 0.0)
                    if rec.get("reserve_exceeded"):
                        out[role]["reserve_exceeded"] += 1
                elif kind == "refused":
                    out[role]["refusals"] += 1
    for role in out:
        out[role]["usd"] = round(out[role]["usd"], 6)
    for own in owners.values():
        own["usd"] = round(own["usd"], 6)
    return {"by_role": out, "by_owner": owners}


def report() -> dict:
    snap = snapshot()
    if not snap.get("active"):
        return snap
    obs = observed()
    snap["observed"] = obs
    problems = []
    for role in ROLES:
        r = snap["roles"].get(role) or {}
        if "error" in r:
            problems.append(f"{role}: {r['error']}")
            continue
        o = obs["by_role"][role]
        if o["attempts"] > r["claimed_attempts_all_processes"]:
            problems.append(f"{role}: {o['attempts']} attempts recorded but only "
                            f"{r['claimed_attempts_all_processes']} claimed")
        if o["attempts"] > r["cap_attempts"]:
            problems.append(f"{role}: {o['attempts']} attempts exceeds the cap of {r['cap_attempts']}")
        if o["usd"] > r["cap_usd"]:
            problems.append(f"{role}: ${o['usd']:.4f} exceeds the cap of ${r['cap_usd']:.2f}")
    snap["problems"] = problems
    snap["verdict"] = "PASS" if not problems else "FAIL"
    return snap


def _main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "report":
        os.environ["SKILLOPT_SPEND_LEDGER_DIR"] = argv[2]
        _state.clear()
        print(json.dumps(report(), indent=2, sort_keys=True))
        return 0
    print(__doc__.strip().splitlines()[-1])
    return 2


if __name__ == "__main__":  # pragma: no cover - verification entry point
    import sys
    raise SystemExit(_main(sys.argv))
