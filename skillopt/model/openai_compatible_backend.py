"""Generic OpenAI-compatible chat backend for optimizer and target paths.

This backend talks to *any* service that exposes an OpenAI-compatible
``/chat/completions`` endpoint through the official ``openai`` SDK. A single
implementation therefore covers a large family of providers, for example:

* DeepSeek           (``https://api.deepseek.com``)
* Groq               (``https://api.groq.com/openai/v1``)
* Together AI        (``https://api.together.xyz/v1``)
* Mistral / Fireworks / OpenRouter / Perplexity / xAI Grok
* Ollama             (``http://localhost:11434/v1``)
* vLLM / SGLang / TGI self-hosted servers
* LiteLLM proxy      (``http://localhost:4000``)
* Azure OpenAI and OpenAI itself

Unlike the Azure backend it never assumes Azure-specific auth or the Responses
API — it only needs a ``base_url`` and an ``api_key`` (some local servers accept
any key, so the key is optional and falls back to a harmless placeholder).

The module mirrors the callable surface of the other chat backends
(:mod:`skillopt.model.qwen_backend`, :mod:`skillopt.model.minimax_backend`) so
it can be selected as the optimizer and/or target backend and routed through
:mod:`skillopt.model`.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from skillopt.model.common import (
    TokenTracker,
    _unmodelled_message_fields,
    compat_message_from_chat_message,
    default_model_for_backend,
    recovery_mode,
    response_metadata,
    usage_from_openai_usage,
)
from skillopt.model import spend_ledger

BACKEND_NAME = "openai_compatible"

# A neutral, widely-available default. Real deployments should set the model
# explicitly (e.g. "deepseek-chat", "llama-3.3-70b-versatile", "qwen2.5:7b").
_DEFAULT_BASE_URL = "https://api.openai.com/v1"


@dataclass
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    deployment: str
    timeout_seconds: float
    max_tokens: int
    temperature: float | None


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    return float(raw) if raw else None


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    raw = str(value).strip()
    return int(raw) if raw else default


def _role_env(role: str, key: str, default: str) -> str:
    """Resolve a config value, preferring role-specific over shared env vars."""
    role_key = f"{role.upper()}_OPENAI_COMPATIBLE_{key}"
    generic_key = f"OPENAI_COMPATIBLE_{key}"
    return os.environ.get(role_key) or os.environ.get(generic_key) or default


def _initial_config(role: str) -> OpenAICompatibleConfig:
    role_upper = role.upper()
    deployment_env = "OPTIMIZER_DEPLOYMENT" if role == "optimizer" else "TARGET_DEPLOYMENT"
    return OpenAICompatibleConfig(
        base_url=_role_env(role, "BASE_URL", _DEFAULT_BASE_URL),
        api_key=_role_env(role, "API_KEY", ""),
        deployment=(
            os.environ.get(f"{role_upper}_OPENAI_COMPATIBLE_MODEL")
            or os.environ.get("OPENAI_COMPATIBLE_MODEL")
            or os.environ.get(deployment_env)
            or default_model_for_backend(BACKEND_NAME)
        ),
        timeout_seconds=float(_role_env(role, "TIMEOUT_SECONDS", "300") or 300),
        max_tokens=_parse_int(_role_env(role, "MAX_TOKENS", "8000"), 8000),
        temperature=_parse_optional_float(_role_env(role, "TEMPERATURE", "")),
    )


OPTIMIZER_CONFIG = _initial_config("optimizer")
TARGET_CONFIG = _initial_config("target")

_config_lock = threading.Lock()
_client_lock = threading.Lock()
tracker = TokenTracker()

_optimizer_client: OpenAI | None = None
_target_client: OpenAI | None = None


def _config_for(role: str) -> OpenAICompatibleConfig:
    return OPTIMIZER_CONFIG if role == "optimizer" else TARGET_CONFIG


def _build_client(config: OpenAICompatibleConfig) -> OpenAI:
    return OpenAI(
        base_url=config.base_url.rstrip("/") or _DEFAULT_BASE_URL,
        # Some OpenAI-compatible servers (Ollama, vLLM, local proxies) do not
        # require an API key. The SDK still expects a non-empty string, so fall
        # back to a harmless placeholder when none is configured.
        api_key=config.api_key or "dummy",
        timeout=config.timeout_seconds,
    )


def _get_client(role: str) -> OpenAI:
    global _optimizer_client, _target_client
    with _client_lock:
        if role == "optimizer":
            if _optimizer_client is None:
                _optimizer_client = _build_client(OPTIMIZER_CONFIG)
            return _optimizer_client
        if _target_client is None:
            _target_client = _build_client(TARGET_CONFIG)
        return _target_client


def _reset_clients() -> None:
    global _optimizer_client, _target_client
    with _client_lock:
        _optimizer_client = None
        _target_client = None


# ── Optional call log (research diagnostics) ─────────────────────────────────
#
# When ``<ROLE>_OPENAI_COMPATIBLE_CALL_LOG`` (or ``OPENAI_COMPATIBLE_CALL_LOG``)
# names a file, every attempt in ``_chat_messages_impl`` appends one JSON line:
# the request (model, max_tokens, temperature, messages), the outcome, latency,
# and either the response (content, provider reasoning text, finish reason,
# full usage) or the error. The API key and headers are never written. Unset
# (the default) writes nothing. The log never alters a request, a return value
# or the retry loop: writing errors are swallowed.

_call_log_lock = threading.Lock()
_raw_capture_lock = threading.Lock()

# ── Serving controls the SDK does not model, and the raw body (amendment A-2) ──
#
# TWO SEPARATE THINGS, kept separate on purpose. `<ROLE>_OPENAI_COMPATIBLE_EXTRA_BODY` is a GENERATION
# setting - it changes what the model does. The answer-recovery policy in `common.py` is an ANSWER-FIELD
# setting - it changes only what the client reads from a reply it already has. Conflating them would make
# "thinking off" and "read the field we were dropping" indistinguishable in a result.
#
# Both are off unless set, and what is actually TRANSMITTED is recorded, because a flag that a provider
# ignores is not a condition. OQA-HTTP-1 verified `enable_thinking: false` on the wire for qwen3-5-4b
# (documented, default true): accepted with 200, the `reasoning` key disappears, completion tokens fall
# about fivefold.


def _extra_body(role: str) -> dict | None:
    raw = _role_env(role, "EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{role.upper()}_OPENAI_COMPATIBLE_EXTRA_BODY is not valid JSON ({exc}); refusing to send a "
            f"request whose serving controls cannot be recorded") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"{role.upper()}_OPENAI_COMPATIBLE_EXTRA_BODY must be a JSON object, got {type(parsed).__name__}")
    return parsed or None


def _raw_capture_path(role: str) -> str:
    return _role_env(role, "RAW_CAPTURE", "")


def _msg_sha(messages: Any, role: str) -> str:
    for m in messages or []:
        if m.get("role") == role:
            return hashlib.sha256(str(m.get("content") or "").encode("utf-8")).hexdigest()
    return ""


def _capture_raw(path: str, *, role: str, stage: str, attempt: int, kwargs: dict,
                 extra: dict | None, raw: Any, parsed: Any, compat: Any) -> None:
    """The four layers of ONE exchange: transmitted request, raw HTTP body, SDK view, compat message.

    The episode's own decision is not written here - it is in the rollout row, joined by `response_id`.
    Writing it here would mean guessing it. No credential can reach this file: the request record is the
    serialized BODY, and the response headers are filtered to the same allow-list the probe uses.
    """
    try:
        http = getattr(raw, "http_response", None)
        body = getattr(http, "text", "") or ""
        choice = (getattr(parsed, "choices", None) or [None])[0]
        message = getattr(choice, "message", None)
        body_json = None
        if body.strip().startswith("{"):
            try:
                body_json = json.loads(body)
            except json.JSONDecodeError:
                body_json = None
        sent = {k: v for k, v in kwargs.items() if k != "messages"}
        rec = {
            "schema": "lso.raw_exchange.v1",
            "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "role": role, "stage": stage, "attempt": attempt + 1,
            "slurm_job": os.environ.get("SLURM_JOB_ID", ""),
            "request": {
                "transmitted": sent,
                "extra_body": extra,
                "n_messages": len(kwargs.get("messages") or []),
                "message_roles": [m.get("role") for m in (kwargs.get("messages") or [])],
                # Which EPISODE this exchange belongs to. The messages themselves are not copied here
                # (they are already in the call log and in the saved prompt files), but the digests of
                # the system and first user message identify the episode exactly, which is how the
                # OQA-HTTP-1 selection rule identified one too.
                "system_sha256": _msg_sha(kwargs.get("messages"), "system"),
                "first_user_sha256": _msg_sha(kwargs.get("messages"), "user"),
                "tool_names": [t.get("function", {}).get("name") for t in (kwargs.get("tools") or [])],
                "serialized_sha256": hashlib.sha256(json.dumps(
                    {**sent, "extra_body": extra, "messages": kwargs.get("messages")},
                    sort_keys=True, default=str).encode()).hexdigest(),
            },
            "http": {
                "status_code": getattr(http, "status_code", None),
                "headers": {k.lower(): v for k, v in dict(getattr(http, "headers", {}) or {}).items()
                            if k.lower() in _HEADER_ALLOW},
                "body_bytes": len(body.encode("utf-8")),
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "body": body_json if body_json is not None else body[:20000],
            },
            "sdk": {
                "response_id": getattr(parsed, "id", None),
                "model": getattr(parsed, "model", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "content_is_null": getattr(message, "content", None) is None,
                "content_chars": len(str(getattr(message, "content", "") or "")),
                "reasoning_content_is_null": getattr(message, "reasoning_content", None) is None,
                "model_extra_keys": sorted((getattr(message, "model_extra", None) or {}).keys()),
                "metadata": response_metadata(message, parsed, choice),
            },
            "compat_message": {
                "content_chars": len(compat.content or ""),
                "content": (compat.content or "")[:4000],
                "tool_calls": [tc.model_dump() for tc in (compat.tool_calls or [])],
                "answer_recovery": (compat.metadata or {}).get("answer_recovery"),
                "recovery_policy": recovery_mode(),
            },
        }
        with _raw_capture_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - diagnostics must never affect the call
        pass


# Response headers worth keeping; everything else is dropped so that no authentication material and no
# per-account identifier reaches an artifact. Same list as research/officeqa/oqa_http_probe.py.
_HEADER_ALLOW = ("content-type", "content-length", "x-request-id", "openai-version",
                 "openai-processing-ms", "x-ratelimit-limit-requests",
                 "x-ratelimit-remaining-requests", "retry-after", "server", "date")


def _reasoning_log(message: Any) -> dict:
    from skillopt.model.common import reasoning_text
    text, field = reasoning_text(message)
    return {"field": field, "chars": len(text), "has_answer_tag": "<answer>" in text.lower(),
            "prefix": text[:400], "suffix": text[-400:] if text else ""}


def _plain(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return dict(vars(obj)) if hasattr(obj, "__dict__") else str(obj)


def _log_call(
    path: str,
    *,
    role: str,
    stage: str,
    attempt: int,
    retries: int,
    kwargs: dict[str, Any],
    started: float,
    resp: Any = None,
    error: Exception | None = None,
    extra_body: dict | None = None,
    compat: Any = None,
) -> None:
    try:
        record: dict[str, Any] = {
            "schema": "lso.openai_compatible_call.v1",
            "started_utc": datetime.datetime.fromtimestamp(started, datetime.timezone.utc).isoformat(),
            "latency_s": round(time.time() - started, 3),
            "role": role,
            "stage": stage,
            "attempt": attempt + 1,
            "max_attempts": retries,
            "request": {
                "model": kwargs.get("model"),
                "max_tokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
                "timeout": kwargs.get("timeout"),
                "tools": bool(kwargs.get("tools")),
                "extra_body": extra_body,
                "messages": kwargs.get("messages"),
            },
            "outcome": "error" if error is not None else "ok",
        }
        if resp is not None:
            choice = (getattr(resp, "choices", None) or [None])[0]
            message = getattr(choice, "message", None)
            # OQA-FOR-1: the previous shape omitted the reply's TOOL CALLS and any field this client
            # does not model, so the log could not say which tool a reply asked for, nor whether the
            # answer had arrived somewhere nobody read.
            tcs = getattr(message, "tool_calls", None) or []
            record["response"] = {
                "id": getattr(resp, "id", None),
                "model": getattr(resp, "model", None),
                "system_fingerprint": getattr(resp, "system_fingerprint", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "content": getattr(message, "content", None),
                "reasoning_content": getattr(message, "reasoning_content", None),
                # OQA-HTTP-1: this provider uses `reasoning`, not `reasoning_content`, and the previous
                # shape looked only for the latter - so 7,909 logged attempts all recorded null.
                "reasoning_field": _reasoning_log(message),
                "usage": _plain(getattr(resp, "usage", None)),
                "tool_calls": [{
                    "id": getattr(tc, "id", None),
                    "type": getattr(tc, "type", None),
                    "name": getattr(getattr(tc, "function", None), "name", None),
                    "arguments": getattr(getattr(tc, "function", None), "arguments", None),
                } for tc in tcs],
                "n_tool_calls": len(tcs),
                "unmodelled_message_fields": _unmodelled_message_fields(message),
            }
        if compat is not None:
            record["recovery"] = (getattr(compat, "metadata", None) or {}).get("answer_recovery")
        if error is not None:
            record["error"] = {
                "type": type(error).__name__,
                "status_code": getattr(error, "status_code", None),
                "message": str(error)[:4000],
            }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _call_log_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - diagnostics must never affect the call
        pass


def count_tokens(text: str, model: str | None = None) -> int:
    """Best-effort token count for a string.

    Uses ``tiktoken`` when available (per-model encoding, falling back to the
    ``cl100k_base`` encoding). If ``tiktoken`` is not installed or fails — which
    is common for non-OpenAI models served through compatible APIs — it falls
    back to a character-based estimate of roughly four characters per token.
    """
    if not text:
        return 0
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model or "gpt-4o")
        except Exception:  # noqa: BLE001 - unknown/non-OpenAI model name
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:  # noqa: BLE001 - tiktoken missing or encoding failure
        # Rough heuristic: ~4 characters per token for English-like text.
        return max(1, (len(text) + 3) // 4)


def _chat_messages_impl(
    messages: list[dict[str, Any]],
    max_completion_tokens: int,
    retries: int,
    stage: str,
    *,
    role: str,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    deployment: str | None = None,
    timeout: float | None = None,
) -> tuple[Any, dict[str, int]]:
    config = _config_for(role)
    client = _get_client(role)
    kwargs: dict[str, Any] = {
        "model": deployment or config.deployment,
        "messages": messages,
        # ``max_tokens`` (rather than ``max_completion_tokens``) is the field
        # understood by the broadest set of OpenAI-compatible providers.
        "max_tokens": min(max_completion_tokens, config.max_tokens),
    }
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature
    if tools:
        kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
    if timeout is not None:
        kwargs["timeout"] = timeout

    call_log = _role_env(role, "CALL_LOG", "")
    extra = _extra_body(role)
    raw_capture = _raw_capture_path(role)
    last_err: Exception | None = None
    for attempt in range(retries):
        # Authorization BEFORE the request, not accounting after it. `reserve` raises
        # BudgetExhausted / LedgerUnusable, and it is called outside the try below so that a
        # refusal stops the loop instead of consuming the remaining retries. With
        # SKILLOPT_SPEND_LEDGER_DIR unset it is a no-op and this loop is upstream's.
        spend_ledger.reserve(role, stage)
        started = time.time()
        try:
            # ONE exchange, two views. With a capture directory set the request goes through
            # `with_raw_response`, so the complete HTTP body and the object the SDK parsed come from the
            # SAME exchange - a separate raw call would be a different exchange and could not settle a
            # conversion question. Without it the call is byte-for-byte what it was before.
            raw_wrapper = None
            if raw_capture:
                raw_wrapper = client.chat.completions.with_raw_response.create(
                    **kwargs, extra_body=extra or None)
                resp = raw_wrapper.parse()
            else:
                resp = client.chat.completions.create(**kwargs, extra_body=extra or None)
            choices = getattr(resp, "choices", None) or []
            if not choices:
                raise RuntimeError(
                    f"OpenAI-compatible API returned no choices: {resp!r}"
                )
            choice = choices[0]
            message = choice.message
            text = message.content or ""
            raw_usage = getattr(resp, "usage", None)
            usage_info = usage_from_openai_usage(raw_usage)
            # The provider's own charge when it reports one. Recorded beside the computed figure; the
            # cap is still enforced on the computed figure, so this cannot weaken the guard.
            provider_cost = getattr(raw_usage, "cost_usd", None)
            try:
                provider_cost = float(provider_cost) if provider_cost is not None else None
            except (TypeError, ValueError):
                provider_cost = None
            tracker.record(
                stage,
                usage_info["prompt_tokens"],
                usage_info["completion_tokens"],
            )
            spend_ledger.record_usage(
                role, stage,
                usage_info["prompt_tokens"],
                usage_info["completion_tokens"],
                cached_tokens=int(getattr(raw_usage, "prompt_cache_hit_tokens", 0) or 0),
                provider_cost_usd=provider_cost,
            )
            compat = compat_message_from_chat_message(message, resp, choice)
            if call_log:
                _log_call(call_log, role=role, stage=stage, attempt=attempt, retries=retries,
                          kwargs=kwargs, started=started, resp=resp, extra_body=extra, compat=compat)
            if raw_capture and raw_wrapper is not None:
                _capture_raw(raw_capture, role=role, stage=stage, attempt=attempt, kwargs=kwargs,
                             extra=extra, raw=raw_wrapper, parsed=resp, compat=compat)
            if return_message:
                return compat, usage_info
            # The plain-text path is UNCHANGED: it returns `message.content`, so the answer-recovery
            # policy never touches an optimizer call or any other caller that does not ask for the
            # message object. Promoting reasoning text into a reply that is then parsed as JSON is a
            # different question and is not decided here.
            return text, usage_info
        except Exception as e:  # noqa: BLE001
            last_err = e
            if call_log:
                _log_call(call_log, role=role, stage=stage, attempt=attempt, retries=retries,
                          kwargs=kwargs, started=started, error=e, extra_body=extra)
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(
        f"OpenAI-compatible chat call failed after {retries} retries: {last_err}"
    )


# ── Public API (mirrors the other chat backends) ─────────────────────────────


def chat_optimizer(
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "optimizer",
    reasoning_effort: str | None = None,
    timeout: float | None = None,
) -> tuple[str, dict[str, int]]:
    del reasoning_effort  # not forwarded — kept for a uniform signature
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return _chat_messages_impl(
        messages,
        max_completion_tokens,
        retries,
        stage,
        role="optimizer",
        timeout=timeout,
    )


def chat_target(
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    reasoning_effort: str | None = None,
    timeout: float | None = None,
) -> tuple[str, dict[str, int]]:
    del reasoning_effort
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return _chat_messages_impl(
        messages,
        max_completion_tokens,
        retries,
        stage,
        role="target",
        timeout=timeout,
    )


def chat_optimizer_messages(
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "optimizer",
    reasoning_effort: str | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: float | None = None,
) -> tuple[Any, dict[str, int]]:
    del reasoning_effort
    return _chat_messages_impl(
        messages,
        max_completion_tokens,
        retries,
        stage,
        role="optimizer",
        tools=tools,
        tool_choice=tool_choice,
        return_message=return_message,
        timeout=timeout,
    )


def chat_target_messages(
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    reasoning_effort: str | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: float | None = None,
) -> tuple[Any, dict[str, int]]:
    del reasoning_effort
    return _chat_messages_impl(
        messages,
        max_completion_tokens,
        retries,
        stage,
        role="target",
        tools=tools,
        tool_choice=tool_choice,
        return_message=return_message,
        timeout=timeout,
    )


# ── Configuration / lifecycle ────────────────────────────────────────────────


def _update_config(
    config: OpenAICompatibleConfig,
    role: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    deployment: str | None = None,
    temperature: float | str | None = None,
    timeout_seconds: float | str | None = None,
    max_tokens: int | str | None = None,
) -> None:
    env_prefix = role.upper()
    if base_url is not None:
        config.base_url = str(base_url).strip() or config.base_url
        os.environ[f"{env_prefix}_OPENAI_COMPATIBLE_BASE_URL"] = config.base_url
    if api_key is not None:
        config.api_key = str(api_key).strip()
        os.environ[f"{env_prefix}_OPENAI_COMPATIBLE_API_KEY"] = config.api_key
    if deployment is not None:
        config.deployment = str(deployment).strip() or config.deployment
        os.environ[f"{env_prefix}_OPENAI_COMPATIBLE_MODEL"] = config.deployment
    if temperature is not None:
        raw = str(temperature).strip()
        config.temperature = float(raw) if raw else None
        os.environ[f"{env_prefix}_OPENAI_COMPATIBLE_TEMPERATURE"] = raw
    if timeout_seconds is not None:
        config.timeout_seconds = float(timeout_seconds)
        os.environ[f"{env_prefix}_OPENAI_COMPATIBLE_TIMEOUT_SECONDS"] = str(timeout_seconds)
    if max_tokens is not None:
        config.max_tokens = int(max_tokens)
        os.environ[f"{env_prefix}_OPENAI_COMPATIBLE_MAX_TOKENS"] = str(max_tokens)


def configure_openai_compatible(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    temperature: float | str | None = None,
    timeout_seconds: float | str | None = None,
    max_tokens: int | str | None = None,
    optimizer_base_url: str | None = None,
    optimizer_api_key: str | None = None,
    optimizer_model: str | None = None,
    optimizer_temperature: float | str | None = None,
    optimizer_timeout_seconds: float | str | None = None,
    optimizer_max_tokens: int | str | None = None,
    target_base_url: str | None = None,
    target_api_key: str | None = None,
    target_model: str | None = None,
    target_temperature: float | str | None = None,
    target_timeout_seconds: float | str | None = None,
    target_max_tokens: int | str | None = None,
) -> None:
    """Configure the generic OpenAI-compatible backend at runtime.

    Shared values apply to both the optimizer and target roles; the
    ``optimizer_*`` / ``target_*`` variants override them per role.
    """
    with _config_lock:
        if base_url is not None:
            os.environ["OPENAI_COMPATIBLE_BASE_URL"] = str(base_url).strip()
        if api_key is not None:
            os.environ["OPENAI_COMPATIBLE_API_KEY"] = str(api_key).strip()
        if model is not None:
            os.environ["OPENAI_COMPATIBLE_MODEL"] = str(model).strip()
        if temperature is not None:
            os.environ["OPENAI_COMPATIBLE_TEMPERATURE"] = str(temperature).strip()
        if timeout_seconds is not None:
            os.environ["OPENAI_COMPATIBLE_TIMEOUT_SECONDS"] = str(timeout_seconds)
        if max_tokens is not None:
            os.environ["OPENAI_COMPATIBLE_MAX_TOKENS"] = str(max_tokens)
        _update_config(
            OPTIMIZER_CONFIG,
            "optimizer",
            base_url=optimizer_base_url if optimizer_base_url is not None else base_url,
            api_key=optimizer_api_key if optimizer_api_key is not None else api_key,
            deployment=optimizer_model if optimizer_model is not None else model,
            temperature=optimizer_temperature if optimizer_temperature is not None else temperature,
            timeout_seconds=optimizer_timeout_seconds if optimizer_timeout_seconds is not None else timeout_seconds,
            max_tokens=optimizer_max_tokens if optimizer_max_tokens is not None else max_tokens,
        )
        _update_config(
            TARGET_CONFIG,
            "target",
            base_url=target_base_url if target_base_url is not None else base_url,
            api_key=target_api_key if target_api_key is not None else api_key,
            deployment=target_model if target_model is not None else model,
            temperature=target_temperature if target_temperature is not None else temperature,
            timeout_seconds=target_timeout_seconds if target_timeout_seconds is not None else timeout_seconds,
            max_tokens=target_max_tokens if target_max_tokens is not None else max_tokens,
        )
    _reset_clients()


def get_max_tokens() -> int:
    return TARGET_CONFIG.max_tokens


def get_token_summary() -> dict[str, dict[str, int]]:
    return tracker.summary()


def reset_token_tracker() -> None:
    tracker.reset()


def set_reasoning_effort(effort: str | None) -> None:
    # Reasoning effort is provider-specific and not universally supported by
    # OpenAI-compatible endpoints, so it is intentionally a no-op here.
    del effort


def set_target_deployment(deployment: str) -> None:
    TARGET_CONFIG.deployment = deployment or default_model_for_backend(BACKEND_NAME)
    os.environ["TARGET_DEPLOYMENT"] = TARGET_CONFIG.deployment
    _reset_clients()


def set_optimizer_deployment(deployment: str) -> None:
    OPTIMIZER_CONFIG.deployment = deployment or default_model_for_backend(BACKEND_NAME)
    os.environ["OPTIMIZER_DEPLOYMENT"] = OPTIMIZER_CONFIG.deployment
    _reset_clients()
