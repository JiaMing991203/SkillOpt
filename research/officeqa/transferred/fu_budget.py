"""DeepSeek budget for concurrent paid jobs of the evolution pilot (research; no network).

docs/research/EVOLUTION_PILOT.md section 9. The earlier full-update stages serialized every paid job
(Slurm singleton) so that one shared ledger needed no reservations. The pilot runs up to three paid
condition jobs at once, so the shared ledger now holds RESERVATIONS:

  * global ledger (the existing full_update ledger file, profile full_update: 2,000 attempts, $20 since
    2026-10-27 (250 / $2.50 before), a $0.10 reserve kept free): spent totals + one reservation per running job. A job reserves its own
    cap (attempts, peak-priced dollars) once at start; the reservation is accepted only if
        spent + all open reservations + this cap  <=  the profile caps.
    At the end the job is SETTLED: its actual spending (recounted from its call logs) replaces the
    reservation. A job that dies unsettled keeps its whole reservation (conservative) until settled
    by hand from its call logs (`settle`, recovery).
  * job ledger (one file per job, single writer): every HTTP attempt is checked BEFORE it is sent:
        job spent + worst case of every attempt in flight + worst case of this attempt <= job cap,
    where the worst case of one attempt is the prompt at 2.5 characters per token (cache miss) plus
    max_tokens of output, both at the peak rate. An attempt is recorded as "in flight" (persisted)
    before the request and settled from the response's usage after it; an error attempt is charged
    an extra $0.02 to the upper bound (it may still be billed). In-flight records left by a killed
    job are charged their worst case at settlement.

Locking: the global ledger is changed only inside a lock directory (mkdir is atomic on NFS; flock is
not relied on across nodes) and written in place with fsync (no rename, so no stale directory entry on
another NFS client). Global transactions are rare (reserve and settle once per job); the per-attempt
checks touch only the job's own file.

Prices: peak $0.30/M input (cache miss), $0.006/M (cache hit), $1.20/M output; off-peak half
(provider pricing page, re-checked 2026-10-26; peak 01-04 and 06-10 UTC on weekdays). The ledger
records three figures: an upper bound at the peak rate, the off-peak figure, and the tariff that
applied at the request's start time. None is a billing guarantee: the provider's invoice is the truth.

CLI (the job body):
  reserve --job ID --attempts N --usd X --ledger GLOBAL --job-ledger FILE
  settle  --job ID --ledger GLOBAL --job-ledger FILE --logs LOG [LOG ...]
  show    --ledger GLOBAL
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import socket
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lean_guided"))

PROFILE = "full_update"
MAX_CALLS, MAX_USD = 2500, 40.20      # = research/lean_guided/llm.py PROFILES["full_update"] (checked in tests); user, 2026-10-27 ($20), raised to $30 / $35.74 on 2026-11-02; Experiment 2 phase cap (user, 2026-11-02 21:2x EST): 1,735 / $32.507; Experiment 3 amendment (user, 2026-11-08 ~21:40 EST): cumulative 2,500 attempts / $40.20 = ledger 1,424 / $18.8224 + campaign worst case with recoveries 1,016 / $19.77 + location replay cap 60 / $1.50 + $0.10 reserve
RESERVE_USD = 0.10
PEAK = {"in_miss": 0.30e-6, "in_hit": 0.006e-6, "out": 1.20e-6}
OFFPEAK = {"in_miss": 0.15e-6, "in_hit": 0.003e-6, "out": 0.60e-6}
ERROR_CHARGE = 0.02
CHARS_PER_TOKEN = 2.5
STALE_LOCK_S = 120.0


class BudgetExceeded(Exception):
    """A reservation or settlement is refused (raised outside SkillOpt code)."""


class BudgetStop(BaseException):
    """Raised inside the trainer when the next attempt would pass the job cap. A BaseException, so
    SkillOpt's broad `except Exception` handlers cannot swallow it: the run stops instead of silently
    treating the refused call as a failed analysis."""


def cost(usage: dict, rate: dict) -> float:
    usage = usage or {}
    pt = usage.get("prompt_tokens") or 0
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max(0, pt - hit)
    return miss * rate["in_miss"] + hit * rate["in_hit"] + (usage.get("completion_tokens") or 0) * rate["out"]


def is_peak(t: float) -> bool:
    d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    return d.weekday() < 5 and (1 <= d.hour < 4 or 6 <= d.hour < 10)


def worst_case(prompt_chars: int, max_tokens: int) -> float:
    return prompt_chars / CHARS_PER_TOKEN * PEAK["in_miss"] + max_tokens * PEAK["out"]


def _write_in_place(path: str, obj) -> None:
    data = json.dumps(obj, indent=1).encode()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, 0)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


class GlobalLedger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    @contextlib.contextmanager
    def locked(self, timeout: float = 300.0):
        lockdir = self.path + ".lockdir"
        t0 = time.time()
        while True:
            try:
                os.mkdir(lockdir)
                break
            except FileExistsError:
                try:
                    age = time.time() - os.stat(lockdir).st_mtime
                except FileNotFoundError:
                    continue
                if age > STALE_LOCK_S:  # held for minutes by a process that died: the section is milliseconds long
                    with contextlib.suppress(OSError):
                        os.rmdir(lockdir)
                    self._note("broke a stale lock directory (age %.0f s)" % age)
                    continue
                if time.time() - t0 > timeout:
                    raise BudgetExceeded("ledger lock not acquired")
                time.sleep(0.5)
        try:
            with open(os.path.join(lockdir, "owner"), "w") as f:
                f.write(f"{socket.gethostname()} {os.getpid()} {time.time():.0f}\n")
            self.d = self._load()
            yield self.d
        finally:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(lockdir, "owner"))
            with contextlib.suppress(OSError):
                os.rmdir(lockdir)

    def _note(self, msg: str) -> None:
        with contextlib.suppress(OSError):
            with open(self.path + ".notes", "a") as f:
                f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")

    def _load(self) -> dict:
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            d = json.load(open(self.path))
        else:
            d = {"calls": 0, "usd_upper_peak": 0.0, "usd_offpeak": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                 "reasoning_tokens": 0, "by_stage": {}, "logs": {}, "profile": PROFILE}
        if d.get("profile", "lean_guided_pilot") != PROFILE:
            raise BudgetExceeded(f"ledger belongs to profile {d.get('profile')}, not {PROFILE}")
        d.setdefault("reservations", {})
        d.setdefault("jobs", {})
        d.setdefault("usd_tariff", d.get("usd_upper_peak", 0.0))
        return d

    def save(self) -> None:
        _write_in_place(self.path, self.d)

    @staticmethod
    def committed(d: dict) -> tuple:
        """(attempts, upper-bound dollars) spent or reserved."""
        ra = sum(r["attempts"] for r in d["reservations"].values())
        ru = sum(r["usd"] for r in d["reservations"].values())
        return d["calls"] + ra, d["usd_upper_peak"] + ru

    def reserve_job(self, job: str, attempts: int, usd: float) -> dict:
        with self.locked() as d:
            if job in d["reservations"] or job in d["jobs"]:
                raise BudgetExceeded(f"job {job} already has a reservation or was settled")
            ca, cu = self.committed(d)
            if ca + attempts > MAX_CALLS:
                raise BudgetExceeded(f"attempt cap: {ca} committed + {attempts} > {MAX_CALLS}")
            if cu + usd > MAX_USD - RESERVE_USD + 1e-12:
                raise BudgetExceeded(f"dollar cap: {cu:.4f} committed + {usd:.4f} > {MAX_USD - RESERVE_USD:.2f}")
            d["reservations"][job] = {"attempts": attempts, "usd": usd, "t": time.time(), "host": socket.gethostname()}
            self.save()
            return dict(d)

    def settle_job(self, job: str, totals: dict) -> dict:
        with self.locked() as d:
            if job in d["jobs"]:
                raise BudgetExceeded(f"job {job} was already settled")
            d["reservations"].pop(job, None)
            for k in ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens"):
                d[k] += totals.get(k, 0)
            for k in ("usd_upper_peak", "usd_offpeak", "usd_tariff"):
                d[k] += totals.get(k, 0.0)
            for st, b in totals.get("by_stage", {}).items():
                g = d["by_stage"].setdefault(st, {"calls": 0, "usd_upper_peak": 0.0, "errors": 0})
                for k in ("calls", "usd_upper_peak", "errors"):
                    g[k] += b.get(k, 0)
            d["jobs"][job] = dict(totals, settled_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            self.save()
            return dict(d)


def totals_from_logs(logs: list) -> dict:
    """Recount every attempt of the given call logs (the authoritative record of what was sent)."""
    t = {"calls": 0, "errors": 0, "usd_upper_peak": 0.0, "usd_offpeak": 0.0, "usd_tariff": 0.0, "prompt_tokens": 0,
         "completion_tokens": 0, "reasoning_tokens": 0, "by_stage": {}}
    for path in logs:
        if not os.path.exists(path):
            continue
        for line in open(path):
            if not line.strip():
                continue
            r = json.loads(line)
            u = (r.get("response") or {}).get("usage") or {}
            ok = r.get("outcome") == "ok"
            up = cost(u, PEAK) + (0.0 if ok else ERROR_CHARGE)
            try:
                started = datetime.datetime.fromisoformat(r["started_utc"]).timestamp()
            except Exception:  # noqa: BLE001
                started = time.time()
            t["calls"] += 1
            t["errors"] += 0 if ok else 1
            t["usd_upper_peak"] += up
            t["usd_offpeak"] += cost(u, OFFPEAK)
            t["usd_tariff"] += cost(u, PEAK if is_peak(started) else OFFPEAK) + (0.0 if ok else ERROR_CHARGE)
            t["prompt_tokens"] += u.get("prompt_tokens") or 0
            t["completion_tokens"] += u.get("completion_tokens") or 0
            t["reasoning_tokens"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            b = t["by_stage"].setdefault(r.get("stage", "?"), {"calls": 0, "usd_upper_peak": 0.0, "errors": 0})
            b["calls"] += 1
            b["usd_upper_peak"] += up
            b["errors"] += 0 if ok else 1
    return t


class JobBudget:
    """Per-attempt guard of one job (single process; threads share it through a lock)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.d = json.load(open(path))
        if self.d.get("profile") != PROFILE:
            raise BudgetExceeded("job ledger has the wrong profile")

    @staticmethod
    def create(path: str, job: str, attempts: int, usd: float) -> None:
        if os.path.exists(path):
            raise BudgetExceeded(f"{path} exists")
        _write_in_place(path, {"profile": PROFILE, "job": job, "cap_attempts": attempts, "cap_usd": usd, "attempts": 0,
                               "errors": 0, "usd_upper_peak": 0.0, "usd_offpeak": 0.0, "usd_tariff": 0.0,
                               "prompt_tokens": 0, "completion_tokens": 0, "inflight": {}, "by_stage": {}, "refused": []})

    def _save(self):
        _write_in_place(self.path, self.d)

    def headroom(self) -> tuple:
        infl = self.d["inflight"].values()
        return (self.d["cap_attempts"] - self.d["attempts"] - len(infl),
                self.d["cap_usd"] - self.d["usd_upper_peak"] - sum(x["worst"] for x in infl))

    def precheck(self, attempts: int, worst_each: float) -> None:
        """Refuse to START a stage whose worst case does not fit (raises BudgetStop)."""
        with self._lock:
            ha, hu = self.headroom()
            if attempts > ha or attempts * worst_each > hu:
                self.d["refused"].append({"t": time.time(), "precheck": attempts, "worst_each": worst_each})
                self._save()
                raise BudgetStop(f"job cap: {attempts} attempts (worst ${attempts * worst_each:.4f}) do not fit "
                                 f"the headroom ({ha} attempts, ${hu:.4f})")

    def begin(self, prompt_chars: int, max_tokens: int, stage: str) -> str:
        w = worst_case(prompt_chars, max_tokens)
        with self._lock:
            ha, hu = self.headroom()
            if ha < 1 or w > hu:
                self.d["refused"].append({"t": time.time(), "stage": stage, "worst": w})
                self._save()
                raise BudgetStop(f"job cap: next {stage} attempt (worst ${w:.4f}) does not fit the headroom "
                                 f"({ha} attempts, ${hu:.4f})")
            tok = uuid.uuid4().hex[:12]
            self.d["inflight"][tok] = {"worst": w, "stage": stage, "t": time.time()}
            self._save()
            return tok

    def end(self, tok: str, usage: dict | None, ok: bool, started: float) -> None:
        with self._lock:
            rec = self.d["inflight"].pop(tok)
            u = usage or {}
            up = cost(u, PEAK) + (0.0 if ok else ERROR_CHARGE)
            self.d["attempts"] += 1
            self.d["errors"] += 0 if ok else 1
            self.d["usd_upper_peak"] += up
            self.d["usd_offpeak"] += cost(u, OFFPEAK)
            self.d["usd_tariff"] += cost(u, PEAK if is_peak(started) else OFFPEAK) + (0.0 if ok else ERROR_CHARGE)
            self.d["prompt_tokens"] += u.get("prompt_tokens") or 0
            self.d["completion_tokens"] += u.get("completion_tokens") or 0
            b = self.d["by_stage"].setdefault(rec["stage"], {"calls": 0, "usd_upper_peak": 0.0, "errors": 0})
            b["calls"] += 1
            b["usd_upper_peak"] += up
            b["errors"] += 0 if ok else 1
            self._save()


class EndpointStop(BaseException):
    """A request to another base URL or model than the configured DeepSeek optimizer endpoint, refused before it is
    sent (FOLLOWUP_EXPERIMENT.md amendment FU-A2). A BaseException, like BudgetStop, so SkillOpt cannot swallow it."""


def usage_dict(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    if hasattr(u, "model_dump"):
        return u.model_dump()
    return dict(u) if isinstance(u, dict) else dict(vars(u))


def guard_client(client, budget: JobBudget | None, stage_of=lambda kwargs: "optimizer", expect: tuple | None = None):
    """Wrap client.chat.completions.create: one budget check per HTTP request (SDK retries are disabled
    by the caller with max_retries=0, so one create() is one request). With expect = (base URL, model), every
    request must go to that base URL with that model; anything else raises EndpointStop before it is sent
    (amendment FU-A2: endpoint checks on every DeepSeek request path)."""
    comp = client.chat.completions
    orig = comp.create

    def create(*args, **kwargs):
        if expect is not None:
            base = str(getattr(client, "base_url", "") or "").rstrip("/")
            if base != str(expect[0]).rstrip("/") or kwargs.get("model") != expect[1]:
                raise EndpointStop(f"request to {base!r} with model {kwargs.get('model')!r} refused before sending: "
                                   f"the configured optimizer endpoint is {expect[0]!r} with model {expect[1]!r}")
        if budget is None:
            return orig(*args, **kwargs)
        msgs = kwargs.get("messages") or []
        chars = sum(len(m.get("content") or "") if isinstance(m.get("content"), str) else len(json.dumps(m.get("content")))
                    for m in msgs)
        started = time.time()
        tok = budget.begin(chars, int(kwargs.get("max_tokens") or 16384), stage_of(kwargs))
        try:
            resp = orig(*args, **kwargs)
        except BaseException:
            budget.end(tok, None, False, started)
            raise
        budget.end(tok, usage_dict(resp), True, started)
        return resp

    comp.create = create
    return client


def settle(job: str, ledger: str, job_ledger: str, logs: list) -> dict:
    """Fold a finished (or dead) job into the global ledger: totals recounted from its call logs, plus the
    worst case of any attempt still recorded as in flight (sent, response never logged)."""
    t = totals_from_logs(logs)
    jl = json.load(open(job_ledger)) if os.path.exists(job_ledger) else {}
    infl = list((jl.get("inflight") or {}).values())
    if infl:
        t["calls"] += len(infl)
        extra = sum(x["worst"] for x in infl)
        t["usd_upper_peak"] += extra
        t["usd_tariff"] += extra
        t["unlogged_inflight_charged"] = len(infl)
    t["job_ledger"] = {k: jl.get(k) for k in ("attempts", "errors", "usd_upper_peak", "cap_attempts", "cap_usd")}
    t["reconciled"] = (jl.get("attempts") == t["calls"] - len(infl)) and \
        abs((jl.get("usd_upper_peak") or 0.0) - (t["usd_upper_peak"] - sum(x["worst"] for x in infl))) < 1e-6
    if not t["reconciled"]:  # e.g. a call-log write failed: keep the larger of the two counts (conservative)
        t["calls"] = max(t["calls"], (jl.get("attempts") or 0) + len(infl))
        gap = (jl.get("usd_upper_peak") or 0.0) - (t["usd_upper_peak"] - sum(x["worst"] for x in infl))
        if gap > 0:
            t["usd_upper_peak"] += gap
            t["usd_tariff"] += gap
    GlobalLedger(ledger).settle_job(job, t)
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("reserve")
    for k in ("--job", "--ledger", "--job-ledger"):
        p.add_argument(k, required=True)
    p.add_argument("--attempts", type=int, required=True)
    p.add_argument("--usd", type=float, required=True)
    p = sub.add_parser("settle")
    for k in ("--job", "--ledger", "--job-ledger"):
        p.add_argument(k, required=True)
    p.add_argument("--logs", nargs="+", required=True)
    p = sub.add_parser("show")
    p.add_argument("--ledger", required=True)
    a = ap.parse_args()
    try:
        if a.cmd == "reserve":
            d = GlobalLedger(a.ledger).reserve_job(a.job, a.attempts, a.usd)
            JobBudget.create(a.job_ledger, a.job, a.attempts, a.usd)
            ca, cu = GlobalLedger.committed(d)
            print(f"reserved {a.attempts} attempts / ${a.usd:.2f} for job {a.job}; committed {ca} of {MAX_CALLS}, "
                  f"${cu:.4f} of ${MAX_USD - RESERVE_USD:.2f}")
        elif a.cmd == "settle":
            print(json.dumps(settle(a.job, a.ledger, a.job_ledger, a.logs), indent=1))
        else:
            d = GlobalLedger(a.ledger)._load() if os.path.exists(a.ledger) else {}
            print(json.dumps(d, indent=1))
    except BudgetExceeded as e:
        print("BUDGET STOP:", e)
        return 8
    return 0


if __name__ == "__main__":
    sys.exit(main())
