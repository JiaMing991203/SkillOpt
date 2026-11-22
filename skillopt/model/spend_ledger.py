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
  reading what has been granted costs one directory listing and opens no file, which is what makes
  READING cheap and safe on NFS. GRANTING is serialized by an exclusive lock - see the next paragraph for
  why it has to be.

  THE DEFECT THAT MADE THIS NECESSARY, recorded because the first version's reasoning was wrong.
  The claim was that O_CREAT|O_EXCL under a sequence number stops two processes being granted the same
  block, because the loser re-reads and re-checks the cap. It does not: the file NAME carries the amount,
  so two processes that reach the same sequence number with DIFFERENT amounts write DIFFERENT names and
  both succeed. The exclusive create never fires. They reach different amounts because
  `give = min(block, cap - granted)` and `granted` came from two separate directory listings - claims,
  then releases - so a release landing between those two listings lowered one process's `granted` and not
  the other's. Near the cap one then computed a partial block and the other a full one. Reproduced
  deterministically: eleven claims of 2 against a cap of 23, one ordinary grant, then one racer with a
  stale claims view and a fresh release view, gives `000012-target-attempts-1.json` AND
  `000012-target-attempts-2.json` and a net grant of 24. That is the reported "four processes spent 24
  against a cap of 23", and the same defect was present in `_write_release`, where two releases at one
  sequence number would both be subtracted.

  Three changes close it, each sufficient on its own for a different failure shape:
    * granting and releasing both run under ONE exclusive lock (`grant.lock`), so the read-decide-create
      sequence cannot interleave with another process's at all;
    * the sequence number is reserved by an amount-FREE token file (`{seq}-{role}-{resource}.seq`) created
      with O_CREAT|O_EXCL, so exclusivity no longer depends on two processes choosing the same amount;
    * `granted` reads RELEASES FIRST and claims second, so anything that lands in between can only make
      the figure larger, never smaller. A claim missed by a stale listing is the only dangerous
      direction, and this ordering removes it.
  A stale listing that persists is treated as a ledger that cannot be trusted: the grant FAILS CLOSED
  rather than guessing.

  Restarts. Claims and the per-attempt record are files. A restarted process re-reads the directory and sees
  every block its predecessor took, so a requeue cannot reset the counter.

  Releasing what was not used. A process that exits cleanly RELEASES its unused claim, by writing a record in
  `release/` that `_granted` subtracts. A process that dies without releasing keeps its claim, which is the
  conservative direction.

  WHY THE RELEASE EXISTS, recorded because its absence cost a campaign. The first version never released, so
  every process kept the headroom it was holding at exit - about one `per_call_reserve_usd` each, plus block
  rounding. Over the three-arm campaign's 13 processes that claimed the ENTIRE $4.00 optimizer authorization
  while actually spending $2.19: 45 per cent of the money was claimed-but-unspent and unreachable. Once the
  claims reached the cap the ledger refused every further attempt, exactly as designed, and the last four
  arm-epochs made NO optimizer call at all - 24 refusals - while the provider had been paid $2.19 of an
  authorized $4.00. The cap did its job on the wrong quantity.

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
import time

SCHEMA = "lso.spend_ledger.v1"
ROLES = ("target", "optimizer")

_DEFAULT_RESERVE_USD = {"target": 0.02, "optimizer": 0.15}
_DEFAULT_BLOCK_ATTEMPTS = {"target": 50, "optimizer": 4}
# Waste is bounded by one block plus one reserve per process, so a block must be SMALL
# against the cap. $0.25 against a $4.00 optimizer cap is 6 per cent per process.
_DEFAULT_BLOCK_USD = {"target": 0.05, "optimizer": 0.05}

_CLAIM_NAME = re.compile(r"^(\d{6})-(target|optimizer)-(attempts|usdmicro)-(\d+)\.json$")
_RELEASE_NAME = _CLAIM_NAME
# The amount-FREE sequence token. It carries no amount on purpose: that is what makes the exclusive
# create actually exclusive. It is deliberately NOT matched by _CLAIM_NAME, so it is never counted as a
# claim - a token with no claim beside it means a process won the sequence and died before writing, and
# since `_grant` only returns after the claim is written, such a process never spent anything.
_SEQ_TOKEN = re.compile(r"^(\d{6})-(target|optimizer)-(attempts|usdmicro)\.seq$")
_LOCK_NAME = "grant.lock"

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


def _release_dir() -> str:
    return os.path.join(directory(), "release")


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
    # RELEASES FIRST. A release read here whose claim is read below is always legitimate to subtract,
    # because a claim is always created before the release that gives part of it back. Reading claims
    # first instead left the window that produced the over-grant: a release written between the two
    # listings was subtracted while a claim written in the same window was missed, so `granted` came out
    # too SMALL - the one direction that lets the cap be exceeded.
    try:
        released = os.listdir(_release_dir())
    except OSError:
        released = []
    for name in released:
        m = _RELEASE_NAME.match(name)
        if not m:
            continue
        key = (m.group(2), m.group(3))
        totals[key] = totals.get(key, 0) - int(m.group(4))
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


def orphan_seq_tokens() -> list[str]:
    """Sequence tokens with no claim beside them: a process won a sequence and died before claiming.

    It spent nothing - `_grant` returns only after the claim file exists - but the token is evidence of
    an interrupted grant, so it is reported rather than silently ignored.
    """
    try:
        names = os.listdir(_claims_dir())
    except OSError:
        return []
    claimed = set()
    for name in names:
        m = _CLAIM_NAME.match(name)
        if m:
            claimed.add((m.group(1), m.group(2), m.group(3)))
    out = []
    for name in sorted(names):
        m = _SEQ_TOKEN.match(name)
        if m and (m.group(1), m.group(2), m.group(3)) not in claimed:
            out.append(name)
    return out


def _next_seq(names: list[str]) -> int:
    """The next free sequence number, counting BOTH claims and bare sequence tokens.

    A token must advance the sequence even when its claim was never written, or a crashed grant would
    make every later caller collide on the same number for ever.
    """
    best = 0
    for name in names:
        m = _CLAIM_NAME.match(name) or _SEQ_TOKEN.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


# ── the grant lock: one writer at a time ─────────────────────────────────────

def _lock_path() -> str:
    return os.path.join(directory(), _LOCK_NAME)


def _acquire_grant_lock(what: str) -> str:
    """Serialize the whole read-decide-create sequence. O_CREAT|O_EXCL, which is the one atomicity this
    design already depends on; no fcntl, because fcntl locking over NFS is the part that is unreliable.

    Bounded wait, then a stale lock older than `SKILLOPT_LEDGER_LOCK_STALE_S` is broken and the breaking
    is RECORDED. Breaking is itself racy, so the exclusive create after it is what decides the winner.
    """
    path = _lock_path()
    wait_s = _env_float("SKILLOPT_LEDGER_LOCK_WAIT_S", 60.0)
    stale_s = _env_float("SKILLOPT_LEDGER_LOCK_STALE_S", 300.0)
    deadline = time.monotonic() + wait_s
    delay = 0.005
    broke = 0
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = max(0.0, time.time() - os.stat(path).st_mtime)
            except OSError:
                age = 0.0
            if age > stale_s and broke < 3:
                broke += 1
                try:
                    os.unlink(path)
                except OSError:
                    pass
                try:
                    _record({"schema": SCHEMA, "kind": "lock_broken", "owner": _owner_token(),
                             "age_s": round(age, 3), "for": what,
                             "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
                except Exception:  # noqa: BLE001 - never fail a grant because the note could not be written
                    pass
                continue
            if time.monotonic() > deadline:
                raise LedgerUnusable(
                    f"the grant lock {path} was held for longer than {wait_s:.0f}s (age {age:.0f}s). "
                    f"No claim was made and no request was sent.")
            time.sleep(delay)
            delay = min(delay * 2, 0.25)
            continue
        except OSError as exc:
            raise LedgerUnusable(f"cannot take the grant lock {path}: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"owner": _owner_token(), "pid": os.getpid(), "for": what,
                           "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                          f, sort_keys=True)
        except OSError:
            pass
        return path


def _release_grant_lock(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _grant(role: str, resource: str, amount: int, cap_amount: int, note: str) -> int:
    """Claim a block of `amount` for (role, resource). Returns what was granted; 0 means the cap is reached.

    Runs under the shared grant lock, so the read-decide-create sequence is atomic with respect to every
    other process's. See the module docstring for the defect this replaced.
    """
    lock = _acquire_grant_lock(f"grant {role}/{resource}")
    try:
        return _grant_locked(role, resource, amount, cap_amount, note)
    finally:
        _release_grant_lock(lock)


def _grant_locked(role: str, resource: str, amount: int, cap_amount: int, note: str) -> int:
    """The body of a grant. The caller holds the lock.

    Two guards beyond the lock, because a lock on shared storage is not the only thing that can go wrong:
      * the sequence number is reserved by an amount-FREE token, so the exclusive create fires whatever
        amounts two callers computed;
      * `granted` reads releases before claims, so a listing that is out of date can only overstate what
        has been granted, never understate it.
    If the directory listing keeps failing to show a claim that demonstrably exists, this FAILS CLOSED
    instead of claiming against a view it cannot trust.
    """
    claims = _claims_dir()
    stale = 0
    for _ in range(64):
        # releases first - the conservative direction
        released = 0
        try:
            for name in os.listdir(_release_dir()):
                m = _RELEASE_NAME.match(name)
                if m and (m.group(2), m.group(3)) == (role, resource):
                    released += int(m.group(4))
        except OSError:
            pass
        try:
            names = os.listdir(claims)
        except OSError as exc:
            raise LedgerUnusable(f"cannot read the claim directory {claims}: {exc}") from exc
        granted = -released
        for name in names:
            m = _CLAIM_NAME.match(name)
            if m and (m.group(2), m.group(3)) == (role, resource):
                granted += int(m.group(4))
        room = cap_amount - granted
        if room <= 0:
            return 0
        give = min(amount, room)
        seq = _next_seq(names)

        # Reserve the sequence number with a file whose name does NOT contain the amount. This is the
        # step the old code got wrong: with the amount in the name, two callers at one sequence number
        # wrote two different names and both were counted.
        token = os.path.join(claims, f"{seq:06d}-{role}-{resource}.seq")
        try:
            tfd = os.open(token, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            stale += 1
            if stale > 8:
                raise LedgerUnusable(
                    f"sequence {seq:06d} for {role}/{resource} is already taken but the listing of "
                    f"{claims} does not show it, {stale} times over. The ledger's view of shared "
                    f"storage cannot be trusted, so nothing was claimed and no request was sent.")
            time.sleep(0.01)
            continue
        except OSError as exc:
            raise LedgerUnusable(f"cannot reserve a sequence in {claims}: {exc}") from exc
        try:
            with os.fdopen(tfd, "w", encoding="utf-8") as f:
                json.dump({"owner": _owner_token(), "seq": seq, "role": role, "resource": resource}, f,
                          sort_keys=True)
        except OSError:
            pass

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
            # The token was ours, so this cannot be another process. It is the same sequence and amount
            # written twice by this one, which would mean the token scheme is broken.
            raise LedgerUnusable(f"the claim {path} already exists although this process reserved "
                                 f"sequence {seq:06d}; the ledger is not behaving as designed")
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
    raise LedgerUnusable(f"could not allocate a claim sequence in {claims} after 64 tries")


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
    for sub in ("claims", "spend", "release"):
        try:
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        except OSError as exc:
            raise LedgerUnusable(f"cannot create {os.path.join(root, sub)}: {exc}") from exc
    cfg = config(role)
    st = {"cfg": cfg, "claimed_attempts": 0, "used_attempts": 0,
          "claimed_usdmicro": 0, "used_usdmicro": 0, "reserve_exceeded": 0,
          # An attempt is RESOLVED once its usage has been recorded. `used_attempts - resolved_attempts` is
          # the number of requests this process sent whose cost it never learned: they may have been billed,
          # so their reserve is NOT released. See `release`.
          "resolved_attempts": 0}
    _state[role] = st
    _install_atexit()
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
                 cached_tokens: int = 0, provider_cost_usd: float | None = None) -> float:
    """Charge one completed attempt against this process's claimed money. Returns the USD charged.

    `provider_cost_usd` is the provider's OWN figure when it reports one. It is recorded and never used:
    the charge, the cap and every refusal stay on `cost_usd(...)` computed from this project's price
    table, which on the OfficeQA campaign was conservative by about 38% because the provider does not
    bill cache reads. Recording both is what lets that be checked without trusting either in the guard.
    """
    if not active():
        return 0.0
    role = role.lower()
    usd = cost_usd(role, prompt_tokens, completion_tokens, cached_tokens)
    with _lock:
        st = _init(role)
        micro = int(round(usd * 1_000_000))
        st["used_usdmicro"] += micro
        st["resolved_attempts"] += 1
        over = st["used_usdmicro"] > st["claimed_usdmicro"]
        if over:
            st["reserve_exceeded"] += 1
        rec_extra = ({} if provider_cost_usd is None
                     else {"provider_cost_usd": round(float(provider_cost_usd), 8)})
        _record({"schema": SCHEMA, "kind": "usage", "role": role, "stage": stage,
                 "prompt_tokens": int(prompt_tokens), "completion_tokens": int(completion_tokens),
                 "cached_prompt_tokens": int(cached_tokens), "usd": round(usd, 8), **rec_extra,
                 "used_usd_in_process": round(st["used_usdmicro"] / 1e6, 6),
                 "claimed_usd_in_process": round(st["claimed_usdmicro"] / 1e6, 6),
                 "reserve_exceeded": bool(over),
                 "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return usd


def release(reason: str = "clean exit") -> dict:
    """Give back what this process claimed and did not spend, so another process can have it.

    Called from an `atexit` hook, and safe to call twice. The release never exceeds `claimed - used`, so the
    invariant `spent <= claimed - released <= cap` still holds; and `claimed` is reset to `used` in this
    process's state, so if it somehow keeps going it will claim afresh rather than spend what it gave back.

    A process that dies without reaching this keeps its claim. That is the conservative direction and it is
    why a claim BLOCK must be small against the cap, not merely small in absolute terms.

    What is NOT released: one `reserve_usd` for every request this process sent and never got a reply for.
    Those may have been billed by the provider, so the money is treated as spent even though no usage record
    exists. `research/officeqa/oqa_ledger_reconcile.py` applies the same rule to a ledger whose processes have
    already exited without releasing.
    """
    if not active():
        return {"active": False}
    given: dict = {}
    retained: dict = {}
    with _lock:
        for role, st in list(_state.items()):
            # Requests this process sent and never saw a reply for. The provider may have billed them, so
            # their reserve stays claimed: releasing it would let another process spend money that is
            # already gone. Attempts themselves are always resolved in this sense - the attempt was made -
            # so only the money is held back.
            unresolved = max(0, int(st.get("used_attempts", 0)) - int(st.get("resolved_attempts", 0)))
            reserve_usdmicro = int(round(float(st["cfg"]["reserve_usd"]) * 1_000_000))
            hold = {"attempts": 0, "usdmicro": unresolved * reserve_usdmicro}
            if unresolved:
                retained[role] = {"unresolved_requests": unresolved,
                                  "usd_held": round(hold["usdmicro"] / 1e6, 6)}
            for resource, used_key, claimed_key in (("attempts", "used_attempts", "claimed_attempts"),
                                                    ("usdmicro", "used_usdmicro", "claimed_usdmicro")):
                unused = (int(st.get(claimed_key, 0)) - int(st.get(used_key, 0))
                          - int(hold.get(resource, 0)))
                if unused <= 0:
                    continue
                try:
                    _write_release(role, resource, unused, reason)
                except Exception as exc:  # noqa: BLE001 - a release must never fail a finishing process
                    given[f"{role}/{resource}"] = f"error: {type(exc).__name__}"
                    continue
                st[claimed_key] = int(st[claimed_key]) - unused
                given[f"{role}/{resource}"] = unused
        if given or retained:
            try:
                _record({"schema": SCHEMA, "kind": "release", "owner": _owner_token(), "reason": reason,
                         "given_back": given, "retained_for_unresolved": retained,
                         "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
            except Exception:  # noqa: BLE001
                pass
    return {"active": True, "given_back": given, "retained_for_unresolved": retained}


def _write_release(role: str, resource: str, amount: int, reason: str) -> None:
    """Write one release record, under the SAME lock as a grant.

    This had the identical defect: the amount was in the name, so two releases at one sequence number
    wrote two names and both were subtracted - which lowers `granted` and is therefore the direction
    that lets the cap be exceeded. It is serialized against granting too, not only against other
    releases, because a release changes the quantity a grant is about to read.
    """
    lock = _acquire_grant_lock(f"release {role}/{resource}")
    try:
        _write_release_locked(role, resource, amount, reason)
    finally:
        _release_grant_lock(lock)


def _write_release_locked(role: str, resource: str, amount: int, reason: str) -> None:
    rel = _release_dir()
    os.makedirs(rel, exist_ok=True)
    for _ in range(64):
        names = os.listdir(rel)
        seq = _next_seq(names)
        token = os.path.join(rel, f"{seq:06d}-{role}-{resource}.seq")
        try:
            tfd = os.open(token, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            time.sleep(0.01)
            continue
        os.close(tfd)
        path = os.path.join(rel, f"{seq:06d}-{role}-{resource}-{amount}.json")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"schema": SCHEMA, "kind": "release", "seq": seq, "role": role, "resource": resource,
                       "amount": amount, "owner": _owner_token(), "reason": reason,
                       "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        return
    raise LedgerUnusable(f"could not allocate a release sequence in {rel} after 64 tries")


_atexit_installed = False


def _install_atexit() -> None:
    global _atexit_installed
    if _atexit_installed:
        return
    import atexit
    atexit.register(release, "atexit")
    _atexit_installed = True


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
            "unresolved_requests_this_process": max(
                0, int(st.get("used_attempts", 0)) - int(st.get("resolved_attempts", 0))),
        }
    return out


def observed() -> dict:
    """Read every process's spend record: what was actually attempted and actually charged.

    This is the audit view, independent of the claims. ``claimed >= observed`` must hold.
    """
    root = os.path.join(directory(), "spend")
    out: dict[str, dict] = {r: {"attempts": 0, "resolved": 0, "usd": 0.0, "refusals": 0,
                                "reserve_exceeded": 0, "prompt_tokens": 0, "completion_tokens": 0}
                            for r in ROLES}
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
                own = owners.setdefault(name[:-6], {"attempts": 0, "resolved": 0, "usd": 0.0,
                                                    "restart": rec.get("slurm_restart_count", "")})
                kind = rec.get("kind")
                if kind == "attempt":
                    out[role]["attempts"] += 1
                    own["attempts"] += 1
                elif kind == "usage":
                    out[role]["resolved"] += 1
                    own["resolved"] += 1
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
        out[role]["unresolved"] = max(0, out[role]["attempts"] - out[role]["resolved"])
    for own in owners.values():
        own["usd"] = round(own["usd"], 6)
        own["unresolved"] = max(0, own["attempts"] - own["resolved"])
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
    # An interrupted grant leaves a sequence token with no claim. It spent nothing, but it is evidence
    # that a process died inside a grant, so it is reported rather than left to be noticed by accident.
    orphans = orphan_seq_tokens()
    snap["orphan_seq_tokens"] = orphans
    if orphans:
        snap["notes"] = snap.get("notes", []) + [
            f"{len(orphans)} sequence token(s) have no claim beside them: a grant was interrupted. "
            f"Nothing was spent against them - a grant returns only after its claim is written."]
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
