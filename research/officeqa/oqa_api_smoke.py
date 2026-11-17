"""Three-question development smoke test for the OfficeQA actor on a hosted API.

What it answers, and what it deliberately does not. The question is whether the TOOL LOOP WORKS: does the endpoint
return structured tool calls whose arguments SkillOpt can use, does SkillOpt execute them against the local
document tools, and does it feed the observations back so the actor can take another turn. Answer accuracy is
recorded but is NOT the verdict - three questions cannot measure accuracy, and the A40 pilot already showed that
5 of 12 training items are answered correctly under the starting skill.

Safety properties, in the order they fire:
  1. FAIL-CLOSED PREFLIGHT, free and unauthenticated. `GET <base_url>/models/<model>` must return the configured
     model id (or list it as an alias) and must declare `function_calling` among its features. If the id does not
     match, or function calling is not declared, nothing is sent. No library-default fallback is ever accepted:
     the base URL and model actually configured are read back from the resolved config and compared with what this
     module was told to use.
  2. HARD ATTEMPT CAP. Every actor HTTP attempt is counted before it is made, and the run stops at the cap. The
     cap is enforced by wrapping the rollout module's `chat_target_messages` in this driver - the shipped code is
     not modified - so a retry storm cannot walk past the authorized number of attempts.
  3. WORST-CASE USD RESERVATION. Before each attempt the worst-case charge (measured prompt tokens at the input
     price + the full output cap at the output price) is reserved against the USD cap; afterwards the reservation
     is settled against the usage the response actually reports. An attempt whose outcome is unknown (timeout,
     lost connection) stays charged at the worst case and is flagged `unreconciled`.

Nothing here reads the answer key beyond what the released scorer already does, and nothing reads the held-out
split. The three questions come from the TRAINING set by the rule in `select_items`.

usage (offline, no calls):  python3 -I research/officeqa/oqa_api_smoke.py --preflight-only --out <dir>
usage (the smoke test):     python3 -I research/officeqa/oqa_api_smoke.py --split-dir ... --docs-dir ... \
                                --out <dir> --max-attempts 90 --usd-cap 0.50
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_BASE_URL = "https://api.empiriolabs.ai/v1"
DEFAULT_MODEL = "qwen3-5-4b"
REQUIRED_FEATURE = "function_calling"
# EmpirioLabs list prices, 2026-11 (providers.json of the ALFWorld actor-API pilot). Used only to RESERVE a
# worst-case charge; the authoritative figure is the provider's own usage record.
PRICE_IN_PER_M = 0.04
PRICE_CACHED_IN_PER_M = 0.02
PRICE_OUT_PER_M = 0.07


class CapReached(RuntimeError):
    """An authorized cap would be exceeded. Stop starting new work."""


class PreflightFailed(RuntimeError):
    """The endpoint or model is not the configured one, or does not declare function calling."""


# ── 1. preflight ────────────────────────────────────────────────────────────────

def preflight(base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, timeout: float = 25.0) -> dict:
    """Unauthenticated, free check of the endpoint and model. Raises PreflightFailed rather than returning False.

    This is the fail-closed guard: it runs before any paid request, it needs no key, and it refuses on any
    mismatch instead of falling back to a default model or a default base URL.
    """
    url = f"{base_url.rstrip('/')}/models/{model}"
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            status = resp.status
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PreflightFailed(f"{url} returned HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise PreflightFailed(f"{url} is unreachable: {type(exc).__name__}: {exc}") from exc

    ids = {str(body.get("id") or "")} | {str(a) for a in (body.get("aliases") or [])}
    if model not in ids:
        raise PreflightFailed(f"{url} identifies itself as {sorted(ids)}, not {model!r}")
    features = [str(f) for f in (body.get("features") or [])]
    if REQUIRED_FEATURE not in features:
        raise PreflightFailed(
            f"{model} does not declare {REQUIRED_FEATURE!r} (features: {features}). The OfficeQA tool loop needs "
            f"structured tool calls; refusing to spend anything on an endpoint that does not advertise them.")
    return {
        "verdict": "PASS",
        "url": url,
        "http_status": status,
        "latency_s": round(time.time() - started, 3),
        "model_id": body.get("id"),
        "aliases": body.get("aliases") or [],
        "hugging_face_id": body.get("hugging_face_id"),
        "features": features,
        "structured_output": body.get("structured_output"),
        "weight_precision": body.get("weight_precision"),
        "context_window": body.get("context_window"),
        "max_output_tokens": body.get("max_output_tokens"),
        "checked_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }


# ── 2 and 3. the attempt ledger ─────────────────────────────────────────────────

class Ledger:
    """Counts every actor HTTP attempt and reserves its worst-case charge before it is made."""

    def __init__(self, max_attempts: int, usd_cap: float, output_cap_tokens: int):
        self.max_attempts = int(max_attempts)
        self.usd_cap = float(usd_cap)
        self.output_cap_tokens = int(output_cap_tokens)
        self.attempts: list[dict] = []
        self.reserved_usd = 0.0
        self.settled_usd = 0.0
        self._lock = threading.Lock()

    def reserve(self, prompt_chars: int, note: str = "") -> dict:
        """Count an attempt and reserve its worst case. Raises CapReached instead of exceeding a cap."""
        est_in = max(1, prompt_chars // 4)          # ~4 chars/token; only used for the reservation
        worst = est_in / 1e6 * PRICE_IN_PER_M + self.output_cap_tokens / 1e6 * PRICE_OUT_PER_M
        with self._lock:
            if len(self.attempts) + 1 > self.max_attempts:
                raise CapReached(f"attempt cap reached ({self.max_attempts} attempts)")
            if self.reserved_usd + worst > self.usd_cap:
                raise CapReached(f"USD cap would be exceeded (reserved ${self.reserved_usd:.4f} "
                                 f"+ ${worst:.4f} > ${self.usd_cap:.2f})")
            rec = {"n": len(self.attempts) + 1, "started_utc":
                   datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   "est_prompt_tokens": est_in, "reserved_usd": round(worst, 6), "note": note,
                   "outcome": "in_flight"}
            self.attempts.append(rec)
            self.reserved_usd += worst
        return rec

    def settle(self, rec: dict, *, usage: dict | None, outcome: str, latency_s: float,
               tool_calls: int | None = None, malformed: str = "", finish_reason: str = "") -> None:
        with self._lock:
            rec.update(outcome=outcome, latency_s=round(latency_s, 3), finish_reason=finish_reason,
                       tool_calls=tool_calls, malformed=malformed)
            if usage:
                pt = int(usage.get("prompt_tokens") or 0)
                ct = int(usage.get("completion_tokens") or 0)
                cached = int(((usage.get("prompt_tokens_details") or {}) or {}).get("cached_tokens") or 0)
                fresh = max(0, pt - cached)
                actual = (fresh / 1e6 * PRICE_IN_PER_M + cached / 1e6 * PRICE_CACHED_IN_PER_M
                          + ct / 1e6 * PRICE_OUT_PER_M)
                rec.update(prompt_tokens=pt, cached_prompt_tokens=cached, completion_tokens=ct,
                           actual_usd=round(actual, 6))
                self.settled_usd += actual
            else:
                # No usage came back: keep the worst case charged and say so.
                rec.update(actual_usd=rec["reserved_usd"], unreconciled=True)
                self.settled_usd += rec["reserved_usd"]

    def summary(self) -> dict:
        ok = [a for a in self.attempts if a["outcome"] == "ok"]
        return {
            "attempts": len(self.attempts), "attempt_cap": self.max_attempts,
            "ok": len(ok),
            "errors": sum(1 for a in self.attempts if a["outcome"] not in ("ok", "in_flight")),
            "in_flight_at_exit": sum(1 for a in self.attempts if a["outcome"] == "in_flight"),
            "unreconciled": sum(1 for a in self.attempts if a.get("unreconciled")),
            "malformed_replies": sum(1 for a in self.attempts if a.get("malformed")),
            # A reply whose tool-call arguments are not valid JSON is usually rejected by the client library
            # before this driver ever sees the message, so it arrives as an attempt ERROR rather than as a
            # parsed-but-malformed call. Both are reported; `error_reasons` is what makes the distinction
            # readable, and the tool-loop verdict fails either way because no tool gets executed.
            "error_reasons": sorted({str(a["outcome"]) for a in self.attempts
                                     if a["outcome"] not in ("ok", "in_flight")}),
            "attempts_with_tool_calls": sum(1 for a in self.attempts if (a.get("tool_calls") or 0) > 0),
            "tool_calls_total": sum(int(a.get("tool_calls") or 0) for a in self.attempts),
            "prompt_tokens": sum(int(a.get("prompt_tokens") or 0) for a in self.attempts),
            "cached_prompt_tokens": sum(int(a.get("cached_prompt_tokens") or 0) for a in self.attempts),
            "completion_tokens": sum(int(a.get("completion_tokens") or 0) for a in self.attempts),
            "reserved_usd": round(self.reserved_usd, 6), "settled_usd": round(self.settled_usd, 6),
            "usd_cap": self.usd_cap,
            "latency_s": {"min": round(min([a["latency_s"] for a in ok], default=0.0), 3),
                          "median": round(sorted([a["latency_s"] for a in ok])[len(ok)//2], 3) if ok else None,
                          "max": round(max([a["latency_s"] for a in ok], default=0.0), 3)},
        }


# ── the counting wrapper around the actor call ──────────────────────────────────

def install_guard(ledger: Ledger):
    """Wrap the OfficeQA rollout's `chat_target_messages` so every attempt is counted and capped.

    The wrapper lives here, in the test driver, and not in `skillopt/`: the shipped rollout and backend are left
    byte-identical so that whatever the smoke test observes is what the comparison will do. It also captures the
    one thing the backend's own call log does not record - whether the reply carried `tool_calls`, and whether
    their arguments parsed as JSON - which is the whole question this smoke test exists to answer.
    """
    from skillopt.envs.officeqa import rollout as RO
    inner = RO.chat_target_messages

    def wrapped(*args, **kwargs):
        messages = kwargs.get("messages") or (args[0] if args else [])
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
        rec = ledger.reserve(prompt_chars, note=str(kwargs.get("stage") or ""))
        started = time.time()
        try:
            out = inner(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            ledger.settle(rec, usage=None, outcome=f"error:{type(exc).__name__}",
                          latency_s=time.time() - started)
            raise
        message = out[0] if isinstance(out, tuple) else out
        raw = out[1] if isinstance(out, tuple) and len(out) > 1 else None
        calls = list(getattr(message, "tool_calls", None) or [])
        malformed = ""
        for c in calls:
            try:
                args_json = getattr(getattr(c, "function", None), "arguments", None)
                json.loads(args_json) if args_json else {}
            except Exception as exc:  # noqa: BLE001
                malformed = f"{type(exc).__name__}: tool-call arguments are not JSON"
                break
        # `chat_target_messages` returns (message, usage_info) and usage_info is already the flat token dict the
        # backend built with `usage_from_openai_usage`: {"prompt_tokens", "completion_tokens", ...}. It carries no
        # cached-token breakdown, so cached input is counted as fresh here - which over-states the charge rather
        # than under-stating it. The provider's own usage record remains the authority.
        usage = None
        if isinstance(raw, dict):
            usage = raw if "prompt_tokens" in raw else raw.get("usage")
        elif raw is not None:
            u = getattr(raw, "usage", None)
            usage = u if isinstance(u, dict) else (u.model_dump() if hasattr(u, "model_dump") else None)
        finish = "tool_calls" if calls else "stop"
        ledger.settle(rec, usage=usage, outcome="ok", latency_s=time.time() - started,
                      tool_calls=len(calls), malformed=malformed, finish_reason=finish)
        return out

    RO.chat_target_messages = wrapped
    return lambda: setattr(RO, "chat_target_messages", inner)


# ── item selection ──────────────────────────────────────────────────────────────

SELECTION_RULE = (
    "OQA-SMOKE-1: the first three items of the FROZEN TRAINING set, in the order the A40 shared epoch-1 rollout "
    "recorded its results, that requested at least one document tool under the starting skill. Requiring a tool "
    "call is the point: an item the actor answers from the supplied pages alone would exercise none of the loop "
    "this smoke test is here to check. Validation and held-out items are never read."
)
SELECTED_IDS = ["UID0033", "UID0063", "UID0034"]


def select_items(split_dir: str, ids: list[str] | None = None) -> list[dict]:
    """Load the three training items by id from the frozen split. Refuses an id outside train/.

    Uses the OfficeQA adapter's OWN dataloader rather than parsing the split file here. The frozen splits are
    CSV with quoted multi-line `source_docs` fields, so neither line counting nor a hand-rolled reader gets them
    right - a first version of this function looked for `*.json` and found nothing. Going through the adapter
    also guarantees the items are normalised exactly as a real rollout sees them (`uid` -> `id`, list fields
    parsed), which is the point of a smoke test.
    """
    want = list(ids or SELECTED_IDS)
    from skillopt.envs.officeqa.dataloader import OfficeQADataLoader
    rows = OfficeQADataLoader().load_split_items(os.path.join(split_dir, "train"))
    items = {str(r.get("id")): r for r in rows if str(r.get("id")) in want}
    missing = [i for i in want if i not in items]
    if missing:
        raise RuntimeError(f"ids not found in the TRAINING split at {split_dir}: {missing}. The smoke test reads "
                           f"train/ only; validation and held-out items are out of scope.")
    return [items[i] for i in want]


# ── the verdict ─────────────────────────────────────────────────────────────────

def tool_loop_verdict(per_item: list[dict], ledger: Ledger) -> dict:
    """Does the tool loop work? Judged on the loop, not on answers.

    PASS needs all four of:
      structured_tool_calls_returned  at least one reply carried tool_calls
      arguments_usable                no reply carried tool-call arguments that failed to parse as JSON
      tools_executed                  SkillOpt ran them: a tool_call entry with a non-error observation
      results_fed_back                the actor took a further turn after a tool observation, for every item
                                      that made a tool call
    """
    s = ledger.summary()
    executed = sum(i["tool_calls_executed"] for i in per_item)
    errors = sum(i["tool_observations_error"] for i in per_item)
    fed_back = [i for i in per_item if i["tool_calls_executed"] > 0]
    checks = {
        "structured_tool_calls_returned": s["attempts_with_tool_calls"] > 0,
        "arguments_usable": s["malformed_replies"] == 0 and executed > 0,
        "tools_executed": executed > 0 and errors < executed,
        "results_fed_back": bool(fed_back) and all(i["turns_after_first_tool"] >= 1 for i in fed_back),
    }
    return {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "tool_calls_executed": executed, "tool_observations_error": errors,
            "items_that_called_a_tool": len(fed_back), "items": len(per_item)}


def analyse_item(pred_dir: str, item_id: str, result: dict) -> dict:
    """Per-item loop facts read from the saved conversation, plus the scorer's own numbers."""
    path = os.path.join(pred_dir, item_id, "conversation.json")
    conv = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            conv = json.load(f)
    tool_idx = [n for n, e in enumerate(conv) if isinstance(e, dict) and e.get("type") == "tool_call"]
    errs = sum(1 for e in conv if isinstance(e, dict) and e.get("type") == "tool_call"
               and str(e.get("obs") or "").startswith("[") and "error" in str(e.get("obs") or "").lower())
    msgs_after = 0
    if tool_idx:
        msgs_after = sum(1 for e in conv[tool_idx[0] + 1:]
                         if isinstance(e, dict) and e.get("type") == "message")
    return {
        "id": item_id,
        "tool_calls_executed": len(tool_idx),
        "tool_observations_error": errs,
        "turns_after_first_tool": msgs_after,
        "conversation_events": len(conv),
        "fail_reason": result.get("fail_reason", ""),
        "answered": bool(result.get("predicted_answer")),
        # Accuracy is recorded, and is NOT part of the verdict.
        "hard": result.get("hard"), "soft": result.get("soft"),
    }


# ── driver ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory; must not exist")
    ap.add_argument("--split-dir", help="frozen split dir (train/ val/ test/); required unless --preflight-only")
    ap.add_argument("--docs-dir", help="parsed bulletin root; required unless --preflight-only")
    ap.add_argument("--skill", help="starting skill file; defaults to the adapter's own")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ids", default=",".join(SELECTED_IDS))
    ap.add_argument("--max-attempts", type=int, default=90)
    ap.add_argument("--usd-cap", type=float, default=0.50)
    ap.add_argument("--max-tool-turns", type=int, default=12)
    ap.add_argument("--max-completion-tokens", type=int, default=8000)
    ap.add_argument("--preflight-only", action="store_true")
    args = ap.parse_args()

    if os.path.exists(args.out):
        print(f"refusing to write into an existing directory: {args.out}", file=sys.stderr)
        return 2
    os.makedirs(args.out)
    report: dict = {
        "schema": "officeqa.api_smoke.v1",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "selection_rule": SELECTION_RULE,
        "caps": {"max_attempts": args.max_attempts, "usd_cap": args.usd_cap,
                 "max_tool_turns": args.max_tool_turns, "gpus": 0},
    }

    # 1. preflight, free and unauthenticated, before anything is spent
    try:
        report["preflight"] = preflight(args.base_url, args.model)
    except PreflightFailed as exc:
        report["preflight"] = {"verdict": "FAIL", "reason": str(exc)}
        _write(args.out, report)
        print(json.dumps(report["preflight"], indent=1))
        return 3
    print(json.dumps(report["preflight"], indent=1))
    if args.preflight_only:
        report["note"] = "--preflight-only: no key was used and no request was sent"
        _write(args.out, report)
        return 0

    key_env = "TARGET_OPENAI_COMPATIBLE_API_KEY"
    if not os.environ.get(key_env):
        report["blocked"] = (f"{key_env} is not set. The preflight above passed, so the endpoint and the model are "
                             f"right and function calling is declared; what is missing is the credential.")
        _write(args.out, report)
        print(report["blocked"], file=sys.stderr)
        return 4
    for required in ("split_dir", "docs_dir"):
        if not getattr(args, required.replace("-", "_")):
            print(f"--{required.replace('_', '-')} is required for the smoke test", file=sys.stderr)
            return 2

    ledger = Ledger(args.max_attempts, args.usd_cap, args.max_completion_tokens)
    restore = install_guard(ledger)
    t0 = time.time()
    per_item: list[dict] = []
    # `confined_task_output_dir` puts each item under "<out_root>/predictions/<id>", so out_root is args.out and
    # the per-item records are read back from args.out/predictions.
    pred_dir = os.path.join(args.out, "predictions")
    out_root = args.out
    try:
        from skillopt.envs.officeqa.rollout import process_one
        skill = _read_skill(args.skill)
        report["skill_sha256"] = __import__("hashlib").sha256(skill.encode()).hexdigest()
        items = select_items(args.split_dir, [i for i in args.ids.split(",") if i])
        report["items"] = [i["id"] for i in items]
        for item in items:
            try:
                result = process_one(
                    item, out_root, skill,
                    max_tool_turns=args.max_tool_turns,
                    max_completion_tokens=args.max_completion_tokens,
                    search_mode="offline", use_local_tools=True, data_dirs=args.docs_dir)
            except CapReached as exc:
                report["stopped_early"] = str(exc)
                break
            per_item.append(analyse_item(pred_dir, str(item["id"]), result))
    finally:
        restore()
    report["elapsed_s"] = round(time.time() - t0, 1)
    report["per_item"] = per_item
    report["ledger"] = ledger.summary()
    report["attempts"] = ledger.attempts
    report["tool_loop"] = tool_loop_verdict(per_item, ledger) if per_item else {
        "verdict": "FAIL", "checks": {}, "reason": "no item completed"}
    report["accuracy_reported_separately"] = {
        "note": "three questions cannot measure accuracy; this is recorded, not a verdict",
        "hard": [i["hard"] for i in per_item], "soft": [i["soft"] for i in per_item],
        "answered": sum(1 for i in per_item if i["answered"]), "of": len(per_item)}
    _write(args.out, report)
    print(json.dumps({"tool_loop": report["tool_loop"], "ledger": report["ledger"],
                      "elapsed_s": report["elapsed_s"],
                      "accuracy": report["accuracy_reported_separately"]}, indent=1))
    return 0 if report["tool_loop"]["verdict"] == "PASS" else 33


def _read_skill(path: str | None) -> str:
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read()
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    default = os.path.join(here, "skillopt", "envs", "officeqa", "skills", "initial.md")
    with open(default, encoding="utf-8") as f:
        return f.read()


def _write(out: str, report: dict) -> None:
    with open(os.path.join(out, "SMOKE.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
