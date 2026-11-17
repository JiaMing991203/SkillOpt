"""Is 10 paired questions per slow update practical? Render the real prompts, then test the real DeepSeek path.

Two modes, because the two halves have different costs and must not be confusable:

  --mode render   OFFLINE. No network, no key, nothing spent. Renders the EXACT slow-update and meta-skill user
                  prompts that the planned run would send, by calling `run_slow_update` / `run_meta_skill` with
                  `chat_optimizer` replaced by a capture stub. Same rendering and the same trace-inclusion rules
                  as the campaign, because it is the campaign's own code doing the rendering.
  --mode send     ONE guarded paid attempt per stage, on the cluster where the key lives. A HARD CAP counts
                  actual HTTP sends at the lowest level - the proxy wrapping `client.chat.completions.create` -
                  so no retry path anywhere can exceed it. `retries` is additionally forced to 1, because
                  `_chat_messages_impl` treats `retries=N` as N TOTAL attempts.

WHY THE CAP IS WHERE IT IS. `run_slow_update` hardcodes `retries=3`. Capping by passing a smaller number would
depend on every caller honouring it; counting the sends themselves does not.

A SEND IS JUDGED ON PARSED, USABLE OUTPUT, NOT ON HTTP 200. The success test is the one the trainer applies:
`run_slow_update` must return a dict with a non-empty `slow_update_content`, which means the response parsed as
JSON and carried that field. Anything else is recorded as the trainer would record it - `no_content` - with the
finish reason and the exact error.

usage:
  python3 -I research/officeqa/oqa_slow_probe.py --mode render --prev <rollout> --curr <rollout> \
      [--curr2 <rollout>] --pairs 10 --out <dir>
  python3 -I research/officeqa/oqa_slow_probe.py --mode send   --prev <rollout> --curr <rollout> \
      [--curr2 <rollout>] --pairs 10 --out <dir> --max-attempts 2 [--also-meta-skill]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from skillopt.model.backend_config import get_optimizer_backend, set_optimizer_backend  # noqa: E402
from skillopt.model.openai_compatible_backend import configure_openai_compatible  # noqa: E402
from skillopt.optimizer import meta_skill as META  # noqa: E402
from skillopt.optimizer import slow_update as SLOW  # noqa: E402
from skillopt.optimizer.slow_update import build_comparison_pairs  # noqa: E402

# The project's own calibration, from the A40 run's per-call records: 3.228 characters per prompt token
# (optimizer_calls_summary.json, sum of prompt_chars over sum of prompt_tokens across 8 calls). Used only for the
# offline estimate; a send reports the provider's exact `prompt_tokens`.
CHARS_PER_TOKEN = 3.228
SHOWING = ("regressed", "persistent_fail", "improved")   # the categories format_comparison_text prints a trace for


def rows(rollout_dir: str) -> list:
    path = os.path.join(rollout_dir, "results.jsonl")
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def pairs_from(prev_dir: str, curr_dir: str) -> list:
    """Real comparison pairs, built by the campaign's own builder so the trajectories are read the same way."""
    rp, rc = rows(prev_dir), rows(curr_dir)
    by_id = {str(r["id"]): r for r in rc}
    common = [r for r in rp if str(r["id"]) in by_id]
    items = [{"id": str(r["id"]), "question": r.get("question", ""),
              "task_description": r.get("task_description", "")} for r in common]
    return build_comparison_pairs([r for r in common], [by_id[str(r["id"])] for r in common], items,
                                  prev_rollout_dir=prev_dir, curr_rollout_dir=curr_dir)


def pair_chars(p: dict) -> int:
    return len(str(p.get("prev_trajectory") or "")) + len(str(p.get("curr_trajectory") or ""))


def select(pool: list, n: int, mode: str) -> list:
    """`representative` keeps the recorded order and the natural category mix; `largest` maximises the prompt."""
    if mode == "representative":
        return pool[:n]
    showing = sorted((p for p in pool if p["category"] in SHOWING), key=pair_chars, reverse=True)
    rest = sorted((p for p in pool if p["category"] not in SHOWING), key=pair_chars, reverse=True)
    return (showing + rest)[:n]


def merge_pools(pools: list) -> list:
    """One pair per item id, keeping the heavier recorded attempt. Still a real pair for that item."""
    best: dict = {}
    for pool in pools:
        for p in pool:
            k = str(p["id"])
            if k not in best or pair_chars(p) > pair_chars(best[k]):
                best[k] = p
    return list(best.values())


class Capture:
    """Stands in for `chat_optimizer` to take the rendered prompt without sending anything."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, system: str = "", user: str = "", max_completion_tokens: int = 0, retries: int = 0,
                 stage: str = "", **_kw):
        self.calls.append({"stage": stage, "system": system, "user": user,
                           "max_completion_tokens": max_completion_tokens, "retries": retries})
        # A capture must not be mistaken for a successful call: return something the caller treats as unusable.
        return "", {"prompt_tokens": 0, "completion_tokens": 0}


def render(prev_skill: str, curr_skill: str, pairs: list) -> dict:
    """The exact prompts the planned run would send for these pairs, from the campaign's own code."""
    out: dict = {}
    for name, fn in (("slow_update", lambda: SLOW.run_slow_update(
                         curr_skill, [], [], [], prev_skill=prev_skill, prev_slow_update_content="",
                         comparison_pairs=pairs)),
                     ("meta_skill", lambda: META.run_meta_skill(
                         prev_skill, curr_skill, pairs, prev_meta_skill_content=""))):
        cap = Capture()
        real_slow, real_meta = SLOW.chat_optimizer, META.chat_optimizer
        SLOW.chat_optimizer = cap
        META.chat_optimizer = cap
        try:
            fn()
        finally:
            SLOW.chat_optimizer, META.chat_optimizer = real_slow, real_meta
        if not cap.calls:
            out[name] = {"problem": "the stage made no optimizer call for these inputs"}
            continue
        c = cap.calls[-1]
        total = len(c["system"]) + len(c["user"])
        out[name] = {
            "stage": c["stage"], "retries_the_code_asks_for": c["retries"],
            "max_completion_tokens": c["max_completion_tokens"],
            "system_chars": len(c["system"]), "user_chars": len(c["user"]), "total_chars": total,
            "estimated_prompt_tokens": round(total / CHARS_PER_TOKEN),
            "estimate_basis": f"{CHARS_PER_TOKEN} characters per token, from the A40 run's per-call records",
        }
    return out


def describe(pairs: list) -> dict:
    cats: dict = {}
    for p in pairs:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    return {"n": len(pairs), "categories": dict(sorted(cats.items())),
            "showing": sum(1 for p in pairs if p["category"] in SHOWING),
            "ids": [str(p["id"]) for p in pairs],
            "trajectory_chars_total": sum(pair_chars(p) for p in pairs),
            "trajectory_chars_per_pair": sorted((pair_chars(p) for p in pairs), reverse=True)}


# ── the guarded send ────────────────────────────────────────────────────────────

class SendCap(RuntimeError):
    pass


class PreflightFailed(RuntimeError):
    pass


def preflight(base_url: str, model: str, max_tokens: int, timeout_s: float) -> dict:
    """Pin the BACKEND, the endpoint, the model and the generation settings before anything is sent. Fail closed.

    Three separate things have to be right and the first version of this checked only the second:

      1. THE SELECTED BACKEND. `skillopt.model.chat_optimizer` is a router, and `OPTIMIZER_BACKEND` defaults to
         "openai_chat", which means AZURE. Job 66271190 configured the openai-compatible endpoint perfectly and
         then dispatched to Azure, which raised; `run_slow_update` swallowed the exception and returned None, so
         the probe reported `no_content` after sending nothing. Zero attempts were spent and nothing was charged,
         but the result said nothing about DeepSeek. `set_optimizer_backend` is now called and read back.
      2. THE RESOLVED CONFIG. No library default may stand in: an unset base URL would send this prompt to
         api.openai.com and an unset model would silently become "gpt-4o-mini". The config is read back and
         compared, and the cached per-role client is reset so it is rebuilt from what was just set.
      3. THE DISPATCH PATH ITSELF, proven by `dispatch_check` below without sending anything.
    """
    key = os.environ.get("OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", "").strip()
    if not key:
        raise PreflightFailed("OPTIMIZER_OPENAI_COMPATIBLE_API_KEY is empty; load_secrets must run first")
    if not base_url or not model:
        raise PreflightFailed("--base-url and --model are required: no library default may stand in for them")
    set_optimizer_backend("openai_compatible")
    if get_optimizer_backend() != "openai_compatible":
        raise PreflightFailed(f"the optimizer backend is {get_optimizer_backend()!r}, not 'openai_compatible'")
    configure_openai_compatible(optimizer_base_url=base_url, optimizer_api_key=key, optimizer_model=model,
                                optimizer_max_tokens=max_tokens, optimizer_timeout_seconds=timeout_s)
    import skillopt.model.openai_compatible_backend as B
    B._reset_clients()
    cfg = B._config_for("optimizer")
    got = {"optimizer_backend": get_optimizer_backend(), "base_url": cfg.base_url, "model": cfg.deployment,
           "max_tokens": cfg.max_tokens, "temperature": cfg.temperature, "api_key_len": len(key),
           "timeout_seconds": timeout_s}
    if got["base_url"].rstrip("/") != base_url.rstrip("/") or got["model"] != model:
        raise PreflightFailed(f"the resolved optimizer config is not the one asked for: {got}")
    if got["max_tokens"] != max_tokens:
        raise PreflightFailed(f"max_tokens resolved to {got['max_tokens']}, not {max_tokens}")
    got["dispatch_check"] = dispatch_check()
    return got


_MARKER = "oqa-slow-probe-dispatch-marker"


def dispatch_check() -> str:
    """Prove the optimizer call reaches the openai-compatible backend, WITHOUT sending anything.

    `_chat_messages_impl` is the function that builds the request and calls the client, so replacing it with one
    that raises a unique marker and then making one call through the same wrapper chain the probe will use shows
    that the call arrives there. It raises before any client is touched, so no HTTP attempt is consumed.
    """
    import skillopt.model.openai_compatible_backend as B

    def raiser(*_a, **_k):
        raise RuntimeError(_MARKER)

    real = B._chat_messages_impl
    B._chat_messages_impl = raiser
    try:
        SLOW.chat_optimizer(system="probe", user="probe", max_completion_tokens=16, retries=1, stage="preflight")
    except Exception as exc:  # noqa: BLE001 - the marker is the expected outcome
        if _MARKER not in str(exc):
            raise PreflightFailed(
                f"an optimizer call did NOT reach the openai-compatible backend: {type(exc).__name__}: "
                f"{str(exc)[:300]}") from exc
        return "PASS: the call reaches the openai-compatible backend"
    finally:
        B._chat_messages_impl = real
    raise PreflightFailed("an optimizer call returned without reaching the openai-compatible backend at all")


class Guard:
    """Counts ACTUAL HTTP sends by wrapping the client the backend uses, and refuses to exceed the cap."""

    def __init__(self, cap: int):
        self.cap = cap
        self.sent = 0
        self.records: list = []

    def install(self):
        import skillopt.model.openai_compatible_backend as B
        guard = self

        class _Completions:
            def __init__(self, inner):
                self._inner = inner

            def create(self, **kwargs):
                if guard.sent >= guard.cap:
                    raise SendCap(f"refusing to send: {guard.sent} of {guard.cap} authorized HTTP attempts used")
                guard.sent += 1
                t0 = time.time()
                try:
                    resp = self._inner.create(**kwargs)
                except Exception as exc:
                    guard.records.append({"n": guard.sent, "elapsed_s": round(time.time() - t0, 2),
                                          "model": kwargs.get("model"), "outcome": "error",
                                          "error_type": type(exc).__name__,
                                          "status_code": getattr(exc, "status_code", None),
                                          "error": str(exc)[:2000]})
                    raise
                u = getattr(resp, "usage", None)
                ch = (getattr(resp, "choices", None) or [None])[0]
                guard.records.append({
                    "n": guard.sent, "elapsed_s": round(time.time() - t0, 2),
                    "model_requested": kwargs.get("model"), "model_returned": getattr(resp, "model", None),
                    "max_tokens": kwargs.get("max_tokens"), "temperature": kwargs.get("temperature"),
                    "outcome": "ok", "finish_reason": getattr(ch, "finish_reason", None),
                    "prompt_tokens": getattr(u, "prompt_tokens", None),
                    "completion_tokens": getattr(u, "completion_tokens", None),
                    "prompt_cache_hit_tokens": getattr(u, "prompt_cache_hit_tokens", None),
                    "prompt_cache_miss_tokens": getattr(u, "prompt_cache_miss_tokens", None),
                    "content_chars": len(getattr(getattr(ch, "message", None), "content", "") or ""),
                })
                return resp

        class _Chat:
            def __init__(self, inner):
                self.completions = _Completions(inner.completions)

        class _Client:
            def __init__(self, inner):
                self.chat = _Chat(inner.chat)

        real = B._get_client
        B._get_client = lambda role: _Client(real(role))
        return self


def one_attempt(retries_forced: int = 1, timeout_s: float | None = None):
    """Force every optimizer call made under this context to ask for exactly ONE HTTP attempt.

    `_chat_messages_impl` treats `retries=N` as N TOTAL attempts, so 1 is one attempt. The timeout is forwarded
    explicitly because a 240,000-token prompt takes far longer than a short one and a default must not decide it.
    """
    real_slow, real_meta = SLOW.chat_optimizer, META.chat_optimizer

    def wrap(real):
        def inner(system: str = "", user: str = "", max_completion_tokens: int = 16384, retries: int = 3,
                  stage: str = "", **kw):
            del retries
            if timeout_s is not None:
                kw["timeout"] = timeout_s
            return real(system=system, user=user, max_completion_tokens=max_completion_tokens,
                        retries=retries_forced, stage=stage, **kw)
        return inner

    SLOW.chat_optimizer = wrap(real_slow)
    META.chat_optimizer = wrap(real_meta)
    return (real_slow, real_meta)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["render", "send"])
    ap.add_argument("--prev", required=True, help="the earlier epoch's rollout (training traces)")
    ap.add_argument("--curr", required=True, help="a later epoch's rollout (training traces)")
    ap.add_argument("--curr2", default="", help="a second later-epoch rollout; the heavier attempt per item wins")
    ap.add_argument("--prev-skill", required=True)
    ap.add_argument("--curr-skill", required=True)
    ap.add_argument("--pairs", type=int, default=10)
    ap.add_argument("--batch", default="largest", choices=["representative", "largest"])
    ap.add_argument("--max-attempts", type=int, default=0, help="HARD cap on actual HTTP sends (send mode)")
    ap.add_argument("--also-meta-skill", action="store_true")
    ap.add_argument("--base-url", default="", help="the campaign's optimizer endpoint; required to send")
    ap.add_argument("--model", default="", help="the campaign's resolved optimizer model id; required to send")
    ap.add_argument("--max-tokens", type=int, default=16384, help="the campaign's optimizer output cap")
    ap.add_argument("--timeout-seconds", type=float, default=600.0,
                    help="how long to wait. Affects waiting only, never the request content.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    with open(args.prev_skill, encoding="utf-8") as f:
        prev_skill = f.read()
    with open(args.curr_skill, encoding="utf-8") as f:
        curr_skill = f.read()

    pools = [pairs_from(args.prev, args.curr)]
    if args.curr2:
        pools.append(pairs_from(args.prev, args.curr2))
    pool = merge_pools(pools) if len(pools) > 1 else pools[0]

    rec: dict = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": args.mode, "pairs_requested": args.pairs,
        "collections": {"prev": os.path.abspath(args.prev), "curr": os.path.abspath(args.curr),
                        "curr2": os.path.abspath(args.curr2) if args.curr2 else None},
        "skills": {"prev": os.path.abspath(args.prev_skill), "prev_chars": len(prev_skill),
                   "curr": os.path.abspath(args.curr_skill), "curr_chars": len(curr_skill)},
        "pool": describe(pool),
    }

    batches = {}
    for name in ("representative", "largest"):
        sel = select(pool, args.pairs, name)
        batches[name] = {"pairs": describe(sel), "rendered": render(prev_skill, curr_skill, sel)}
    rec["batches"] = batches
    # What the campaign's own 4-pair setting renders to, for the comparison the user asked for.
    rec["reference_4_pairs"] = {
        "representative": render(prev_skill, curr_skill, select(pool, 4, "representative")),
        "largest": render(prev_skill, curr_skill, select(pool, 4, "largest")),
    }

    if args.mode == "render":
        with open(os.path.join(args.out, "RENDER.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1, default=str)
        print(json.dumps({k: v for k, v in rec.items() if k != "pool"}, indent=1, default=str))
        return 0

    # ── send ────────────────────────────────────────────────────────────────────
    if args.max_attempts <= 0:
        print("send mode needs an explicit --max-attempts", file=sys.stderr)
        return 2
    sel = select(pool, args.pairs, args.batch)
    try:
        resolved = preflight(args.base_url, args.model, args.max_tokens, args.timeout_seconds)
    except PreflightFailed as exc:
        rec["preflight"] = {"verdict": "FAIL", "why": str(exc), "http_attempts_used": 0}
        with open(os.path.join(args.out, "SEND.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1, default=str)
        print(json.dumps(rec["preflight"], indent=1))
        return 3
    rec["preflight"] = {"verdict": "PASS", "resolved": resolved}
    print(json.dumps({"preflight": rec["preflight"]}, indent=1), flush=True)
    guard = Guard(args.max_attempts).install()
    one_attempt(1, args.timeout_seconds)

    results: dict = {}
    order = [("slow_update", lambda: SLOW.run_slow_update(
        curr_skill, [], [], [], prev_skill=prev_skill, prev_slow_update_content="", comparison_pairs=sel))]
    if args.also_meta_skill:
        order.append(("meta_skill", lambda: META.run_meta_skill(
            prev_skill, curr_skill, sel, prev_meta_skill_content="")))

    for name, fn in order:
        before = guard.sent
        t0 = time.time()
        err = None
        try:
            got = fn()
        except SendCap as exc:
            results[name] = {"attempted": False, "why": str(exc)}
            continue
        except BaseException as exc:  # noqa: BLE001 - the exact failure is the deliverable
            got, err = None, f"{type(exc).__name__}: {exc}"
        field = "slow_update_content" if name == "slow_update" else "meta_skill_content"
        content = (got or {}).get(field) or ""
        results[name] = {
            "attempted": True, "http_sends": guard.sent - before,
            "elapsed_s": round(time.time() - t0, 2),
            # The trainer's own test: run_* returns a dict with a non-empty content field, or the epoch records
            # action "no_content". An HTTP 200 that does not parse lands here as no_content too.
            "usable_output": bool(content),
            "trainer_would_record": ("accepted candidate (then gated on the selection set)" if content
                                     else "no_content"),
            "content_chars": len(content),
            "content_preview": content[:400],
            "reasoning_preview": ((got or {}).get("reasoning") or "")[:300],
            "exception": err,
        }
        if not content and err is None:
            results[name]["no_content_reason"] = (
                "run_* returned None or an empty content field: the response either did not parse as JSON or "
                "carried no such key. See the HTTP record's finish_reason and the call log.")
        # A failure must not spend the second attempt on the next stage without the first having succeeded.
        if name == "slow_update" and not content:
            results["meta_skill"] = {"attempted": False,
                                     "why": "the slow update did not produce usable guidance, so the second "
                                            "authorized attempt was left unspent"}
            break

    prices = {"off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.6},
              "peak": {"cache_hit": 0.006, "cache_miss": 0.3, "output": 1.2}}
    charge_off = charge_peak = 0.0
    for r in guard.records:
        if r.get("outcome") != "ok":
            continue
        hit = r.get("prompt_cache_hit_tokens") or 0
        miss = r.get("prompt_cache_miss_tokens")
        if miss is None:
            miss = (r.get("prompt_tokens") or 0) - hit
        comp = r.get("completion_tokens") or 0
        for tier, acc in (("off_peak", "off"), ("peak", "peak")):
            p = prices[tier]
            amount = (hit * p["cache_hit"] + miss * p["cache_miss"] + comp * p["output"]) / 1e6
            if acc == "off":
                charge_off += amount
            else:
                charge_peak += amount

    rec["batch_sent"] = args.batch
    rec["pairs_sent"] = describe(sel)
    rec["authorization"] = {"max_http_attempts": args.max_attempts, "http_attempts_used": guard.sent,
                            "unspent": args.max_attempts - guard.sent}
    rec["http_records"] = guard.records
    rec["results"] = results
    rec["charge_usd"] = {"at_off_peak_prices": round(charge_off, 6), "if_peak": round(charge_peak, 6),
                         "prices_usd_per_1m": prices}
    with open(os.path.join(args.out, "SEND.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, default=str)
    print(json.dumps({"preflight": rec["preflight"], "authorization": rec["authorization"],
                      "http_records": guard.records,
                      "results": results, "charge_usd": rec["charge_usd"],
                      "pairs_sent": {k: rec["pairs_sent"][k] for k in ("n", "categories", "showing")},
                      "rendered": rec["batches"][args.batch]["rendered"]}, indent=1, default=str))
    ok = all(r.get("usable_output") for r in results.values() if r.get("attempted"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
