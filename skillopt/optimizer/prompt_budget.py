"""Check a rendered optimizer prompt against a conservative size limit before it is sent.

WHY. This repository's slow update deliberately disables per-trajectory truncation, so its prompt carries both
trajectories of every comparison pair in full. The size therefore depends on the DATA, not on the configuration:
with ten pairs drawn from the OfficeQA training set the rendered request measured between 199k and 244k tokens
depending on which pairs the sampler drew. One realistic ten-pair request was PROVEN to work - job 66271290,
2026-11-17, prompt_tokens 243,986, finish_reason "stop", 7,562 completion tokens, usable parsed guidance - and
nothing larger has ever been tried. Sending a draw that happens to land above that is a call spent for nothing:
the provider rejects it, `run_slow_update` swallows the error and the trainer records `no_content`, so a gated
slow update is reported as having produced no guidance for a reason that has nothing to do with the experiment.

WHAT THIS DOES. Before the call, the prompt is rendered and measured. If it is over the limit, comparison pairs
are dropped one at a time, in a FIXED order stated below, and the prompt is re-rendered, until it fits. The pair
count actually sent, the sizes, and whether guidance came back are all written to a log so that any epoch's slow
update can be read off the record rather than inferred.

THE DROP ORDER - rule OQA-PB-1, fixed in advance and identical in every arm.

  Pairs are dropped from the LOWEST priority category first, in SkillOpt's own stated order of interest (see
  `format_comparison_text`, which prints regressions first and labels them "HIGHEST PRIORITY"):

        stable_success  ->  improved  ->  persistent_fail  ->  regressed

  and within a category the LAST pair in the sampler's order goes first, so the subset that survives is a prefix
  of that category's pairs. The order depends only on the category and the sampled position - not on the arm,
  not on a score, not on which pair happens to be large. A `stable_success` pair carries no trajectory, so
  dropping one of those saves little; it is still dropped first because it also informs the optimizer least.

  If even the single highest-priority pair is over the limit, NO CALL IS MADE. There is no smaller non-empty
  subset, and spending the call to be rejected tells us nothing.

THE LIMIT. Chosen by the caller through SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT, and intended to sit inside the
proven point with room for the completion:

      225,000 prompt tokens + the full 16,384-token completion allowance = 241,384
      against the 251,548 tokens (243,986 prompt + 7,562 completion) that job 66271290 demonstrably processed.

THE ESTIMATE. 3.20 characters per token. The same measurement calibrates it: 786,843 rendered characters came
back as 243,986 prompt tokens, i.e. 3.2250 chars/token, so 3.20 overstates the token count by about 0.8%. The
bias is deliberate and in the safe direction. It is an estimate, not a tokenizer: DeepSeek publishes no local
tokenizer for this model, and `count_tokens`'s tiktoken fallback is calibrated for OpenAI models.

INERT BY DEFAULT. With SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT unset, `fit` returns the pairs it was given and
nothing is measured, logged or dropped, so upstream behaviour is unchanged (CLAUDE.md section 5).

Environment:
  SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT   the limit in estimated prompt tokens. Unset -> inert.
  SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG    JSONL file for one record per checked prompt.
  SKILLOPT_ARM_LABEL                      recorded in each log line, so one log can hold several arms.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import threading
from typing import Callable, Iterable

SCHEMA = "lso.prompt_budget.v1"
RULE = "OQA-PB-1"
CHARS_PER_TOKEN = 3.20

#: Lowest interest first: this is the order in which pairs are dropped.
DROP_ORDER = ("stable_success", "improved", "persistent_fail", "regressed")

#: The measurement this module's limit is calibrated against.
PROVEN = {
    "job": "66271290", "utc": "2026-11-17T07:30Z", "endpoint": "https://api.deepseek.com",
    "model": "deepseek-flash", "prompt_tokens": 243986, "completion_tokens": 7562,
    "finish_reason": "stop", "rendered_chars": 786843, "chars_per_token_measured": 3.2250,
}

_log_lock = threading.Lock()


def limit_tokens() -> int:
    """The configured limit, or 0 when this module is inert."""
    raw = os.environ.get("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", "").strip()
    if not raw:
        return 0
    try:
        value = int(float(raw))
    except ValueError:
        return 0
    return value if value > 0 else 0


def active() -> bool:
    return limit_tokens() > 0


def estimate_tokens(*texts: str) -> int:
    """Estimated prompt tokens for the given strings, rounded up and deliberately biased high."""
    total = sum(len(t or "") for t in texts)
    return int(math.ceil(total / CHARS_PER_TOKEN))


def drop_sequence(pairs: list[dict]) -> list[int]:
    """Indices of `pairs` in the order rule OQA-PB-1 drops them. Deterministic; no data beyond category+order."""
    order: list[int] = []
    for category in DROP_ORDER:
        members = [i for i, p in enumerate(pairs) if str(p.get("category")) == category]
        order.extend(reversed(members))
    # A category the rule does not name (none exists upstream) is dropped last, in reverse order, so that an
    # unknown category can never be dropped before a named one and can never be silently retained for ever.
    named = set(DROP_ORDER)
    order.extend(reversed([i for i, p in enumerate(pairs) if str(p.get("category")) not in named]))
    return order


def fit(
    system: str,
    pairs: list[dict],
    build_user: Callable[[list[dict]], str],
    *,
    stage: str,
    extra: Iterable[str] = (),
) -> tuple[str, list[dict], dict]:
    """Render the prompt, trim it to the limit by rule OQA-PB-1, and return (user, pairs_sent, record).

    `record["skip"]` is True when not even one pair fits; the caller must then make NO call.
    """
    limit = limit_tokens()
    user = build_user(pairs)
    record: dict = {
        "schema": SCHEMA, "rule": RULE, "stage": stage,
        "arm": os.environ.get("SKILLOPT_ARM_LABEL", ""),
        "epoch": os.environ.get("SKILLOPT_EPOCH_LABEL", ""),
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "limit_tokens": limit, "chars_per_token": CHARS_PER_TOKEN,
        "pairs_offered": len(pairs),
        "categories_offered": _counts(pairs),
        "checked": bool(limit),
    }
    if not limit:
        record.update({"pairs_sent": len(pairs), "dropped": [], "skip": False})
        return user, list(pairs), record

    tokens = estimate_tokens(system, user, *extra)
    record["estimated_tokens_offered"] = tokens
    record["chars_offered"] = len(system) + len(user) + sum(len(e) for e in extra)
    kept = list(pairs)
    dropped: list[dict] = []
    if tokens > limit:
        sequence = drop_sequence(pairs)
        for index in sequence:
            if len(kept) <= 1:
                break
            victim = pairs[index]
            kept = [p for p in kept if p is not victim]
            dropped.append({"id": str(victim.get("id")), "category": str(victim.get("category"))})
            user = build_user(kept)
            tokens = estimate_tokens(system, user, *extra)
            if tokens <= limit:
                break
    record.update({
        "pairs_sent": len(kept),
        "categories_sent": _counts(kept),
        "dropped": dropped,
        "estimated_tokens_sent": tokens,
        "chars_sent": len(system) + len(user) + sum(len(e) for e in extra),
        "within_limit": tokens <= limit,
        "skip": tokens > limit,
    })
    if record["skip"]:
        record["skip_reason"] = (
            f"the single highest-priority comparison pair renders to about {tokens} tokens, over the "
            f"{limit}-token limit; rule {RULE} has no smaller non-empty subset, so no call was made")
    return user, kept, record


def _counts(pairs: list[dict]) -> dict:
    out: dict[str, int] = {}
    for p in pairs:
        key = str(p.get("category"))
        out[key] = out.get(key, 0) + 1
    return out


def log(record: dict, *, guidance_produced: bool | None = None, note: str = "") -> None:
    """Append the record once the call's outcome is known. Never raises; never alters a call."""
    path = os.environ.get("SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG", "").strip()
    if not path:
        return
    try:
        entry = dict(record)
        entry["guidance_produced"] = guidance_produced
        if note:
            entry["note"] = note
        entry["logged_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        line = json.dumps(entry, sort_keys=True, default=str)
        with _log_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - a log must never change what the optimizer does
        pass
