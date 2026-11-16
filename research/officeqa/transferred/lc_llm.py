"""DeepSeek transport of the learned-contract pilot (paid; cluster only). Modelled on the reviewed transport
research/trace_only/tor_llm.py (original tree, sha256 08864db776bd181999791a7089d1cf7ab5089a0abf07a01758da5a3adf4bd781),
with the pilot's OWN budget profile and ledger.

Safeguards (every request):
  * endpoint: the frozen config's base URL and model are compared with the client's before sending (EndpointStop),
    and fu_budget.guard_client(expect=...) checks them again inside the client;
  * budget: fu_budget (copied verbatim; TRANSFER_MANIFEST addenda) with the profile chosen by LC_BUDGET_PROFILE
    (PROFILES below: the pilot's 750 / $10.00, or the information-access studies' 4000 / $15.00); a ledger file
    of another profile (e.g. Experiment 3's) is refused;
    one worst-case check per HTTP attempt before it is sent; settlement from the call log at the end;
  * retries: SDK retries off (max_retries=0); the caller decides on a further attempt; every attempt is logged;
  * concurrency: at most `max_in_flight` requests in flight (2); `set_limit` lowers it while an Experiment 3 job
    runs (coordination ruling of 2026-11-10 12:2x);
  * provider trouble: any HTTP 429, or 3 consecutive errors, raises ProviderStop (the pilot stops; exit 32);
  * credential: DEEPSEEK_API_KEY from the job environment (loaded on the compute node from the cluster secrets
    file); never logged or printed;
  * recovery: a completed response is cached under cache_dir by (request digest, tag); a resumed job reuses it
    instead of paying again (recorded as `cached`, not an attempt). The tag carries the episode, step and attempt
    (or epoch and learner round), so identical prompts in DIFFERENT episodes are always sent fresh.
Settings: max_tokens (required), thinking ({"type": "enabled"|"disabled"}, required: explicit), temperature (only
with thinking disabled), reasoning_effort (optional); provider fields go in extra_body.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "full_update"))
import fu_budget as fb  # noqa: E402

# Budget profiles (each its own ledger; fu_budget refuses a ledger of another profile). Selected by the job
# environment LC_BUDGET_PROFILE; anything else than these names stops before any request.
PROFILES = {
    "pilot": ("learned_contract_pilot", 750, 10.00),   # USER, 2026-11-10 ~00:40 EST: pilot ceilings (closed)
    "info": ("learned_contract_info", 4000, 15.00),    # USER, 2026-11-10 ~12:05 EST: Study 1 + Study 2 ceilings
}
PROFILE_KEY = os.environ.get("LC_BUDGET_PROFILE", "pilot")
if PROFILE_KEY not in PROFILES:
    raise SystemExit(f"unknown LC_BUDGET_PROFILE {PROFILE_KEY!r}")
fb.PROFILE, fb.MAX_CALLS, fb.MAX_USD = PROFILES[PROFILE_KEY]
sys.path.insert(0, HERE)
from vocab import canon, sha  # noqa: E402

ALLOWED = ("max_tokens", "thinking", "temperature", "reasoning_effort")


class ProviderStop(BaseException):
    """Rate limiting or an outage: stop starting new calls (coordination rule)."""


class EndpointStop(BaseException):
    pass


def body_of(model: str, settings: dict) -> dict:
    bad = [k for k in settings if k not in ALLOWED]
    if bad:
        raise ValueError(f"unsupported request settings {bad}")
    if not isinstance(settings.get("max_tokens"), int) or not 1 <= settings["max_tokens"] <= 65536:
        raise ValueError("max_tokens must be an explicit integer in [1, 65536]")
    th = settings.get("thinking")
    if not isinstance(th, dict) or th.get("type") not in ("enabled", "disabled"):
        raise ValueError("thinking must be given explicitly as {'type': 'enabled'|'disabled'}")
    kw = {"model": model, "max_tokens": settings["max_tokens"]}
    if "temperature" in settings:
        if th["type"] != "disabled":
            raise ValueError("temperature is only sent with thinking disabled")
        kw["temperature"] = float(settings["temperature"])
    extra = {"thinking": th}
    if "reasoning_effort" in settings:
        extra["reasoning_effort"] = settings["reasoning_effort"]
    kw["extra_body"] = extra
    return kw


def _plain(x):
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if hasattr(x, "model_dump"):
        return x.model_dump()
    return dict(x) if isinstance(x, dict) else str(x)


class Limiter:
    """A semaphore whose limit can be lowered or raised while requests run (coordination: at most 2 lc requests in
    flight in total while an Experiment 3 job runs). Lowering never interrupts a request already in flight."""

    def __init__(self, limit: int):
        self.limit, self.busy = int(limit), 0
        self._cv = threading.Condition()

    def set_limit(self, n: int) -> None:
        with self._cv:
            self.limit = max(1, int(n))
            self._cv.notify_all()

    def __enter__(self):
        with self._cv:
            while self.busy >= self.limit:
                self._cv.wait()
            self.busy += 1
        return self

    def __exit__(self, *exc):
        with self._cv:
            self.busy -= 1
            self._cv.notify_all()
        return False


class Transport:
    def __init__(self, base_url: str, model: str, job_ledger: str | None, call_log: str, cache_dir: str,
                 client=None, max_in_flight: int = 2):
        self.base_url, self.model = base_url.rstrip("/"), model
        self.call_log, self.cache_dir = call_log, cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        os.makedirs(os.path.dirname(call_log), exist_ok=True)
        self.budget = fb.JobBudget(job_ledger) if job_ledger else None
        if client is None:
            from openai import OpenAI
            key = os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                raise RuntimeError("DEEPSEEK_API_KEY is not set (load it inside the job)")
            client = OpenAI(base_url=self.base_url, api_key=key, max_retries=0)
        self.client = fb.guard_client(client, self.budget, stage_of=lambda kw: getattr(self._tl, "stage", "?"),
                                      expect=(self.base_url, self.model))
        self._raw_base = lambda: str(getattr(client, "base_url", "") or "").rstrip("/")
        self._sem = Limiter(max_in_flight)
        self._tl = threading.local()
        self._lock = threading.Lock()
        self.consecutive_errors = 0
        self.stopped = None
        self.stats = {"attempts": 0, "errors": 0, "cached": 0}

    def _log(self, rec: dict):
        with self._lock:
            with open(self.call_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                f.flush()

    def request_sha(self, messages: list, settings: dict) -> str:
        return sha(canon({"model": self.model, "settings": settings, "messages": messages}))

    def chat(self, messages: list, settings: dict, stage: str, tag: str, timeout_s: float) -> dict:
        if self.stopped:
            raise ProviderStop(self.stopped)
        rsha = self.request_sha(messages, settings)
        # cache key = request content AND its position (tag = episode/step/attempt or epoch/learner round): a resumed
        # job reuses its own paid reply, but a later epoch never receives an earlier epoch's reply for an identical
        # prompt (pilot job 65818925 replayed epoch 1 as epoch 2 when the key was the content alone).
        ckey = sha(rsha + "|" + tag)
        cpath = os.path.join(self.cache_dir, ckey + ".json")
        if os.path.exists(cpath):
            out = json.load(open(cpath))
            with self._lock:
                self.stats["cached"] += 1
            return dict(out, cached=True)
        kw = body_of(self.model, settings)
        if self._raw_base() != self.base_url or kw["model"] != self.model:  # fail closed, before the guard too
            raise EndpointStop(f"client endpoint {self._raw_base()!r}/{kw['model']!r} differs from the frozen "
                               f"{self.base_url!r}/{self.model!r}: nothing sent")
        with self._sem:
            self._tl.stage = stage
            started = time.time()
            rec = {"schema": "lc.call.v1", "stage": stage, "tag": tag, "request_sha256": rsha,
                   "started_utc": datetime.datetime.fromtimestamp(started, datetime.timezone.utc).isoformat(),
                   "request": {"model": kw["model"], "max_tokens": kw["max_tokens"],
                               "temperature": kw.get("temperature"), "extra_body": kw.get("extra_body"),
                               "timeout": timeout_s, "messages": messages}}
            try:
                resp = self.client.chat.completions.create(messages=messages, timeout=timeout_s, **kw)
            except (fb.BudgetStop, fb.EndpointStop):
                raise
            except Exception as e:  # noqa: BLE001 - logged; the caller decides
                code = getattr(e, "status_code", None)
                rec.update(latency_s=round(time.time() - started, 3), outcome="error",
                           error={"type": type(e).__name__, "status_code": code, "message": str(e)[:2000]})
                self._log(rec)
                with self._lock:
                    self.stats["attempts"] += 1
                    self.stats["errors"] += 1
                    self.consecutive_errors += 1
                    if code == 429 or self.consecutive_errors >= 3:
                        self.stopped = f"provider trouble: status {code}, {self.consecutive_errors} consecutive errors"
                if self.stopped:
                    raise ProviderStop(self.stopped)
                return {"ok": False, "error": rec["error"], "content": None, "request_sha256": rsha}
            choice = (getattr(resp, "choices", None) or [None])[0]
            msg = getattr(choice, "message", None)
            out = {"ok": True, "id": getattr(resp, "id", None), "model": getattr(resp, "model", None),
                   "fingerprint": getattr(resp, "system_fingerprint", None),
                   "finish_reason": getattr(choice, "finish_reason", None),
                   "content": getattr(msg, "content", None), "reasoning_content": getattr(msg, "reasoning_content", None),
                   "usage": _plain(getattr(resp, "usage", None)), "latency_s": round(time.time() - started, 3),
                   "request_sha256": rsha, "cache_key": ckey, "tag": tag}
            rec.update(latency_s=out["latency_s"], outcome="ok",
                       response={k: out[k] for k in ("id", "model", "fingerprint", "finish_reason", "content",
                                                     "reasoning_content", "usage")})
            self._log(rec)
            with self._lock:
                self.stats["attempts"] += 1
                self.consecutive_errors = 0
            tmp = cpath + ".tmp"
            json.dump(out, open(tmp, "w"))
            os.replace(tmp, cpath)
            return out
