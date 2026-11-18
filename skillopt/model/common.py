"""Shared model utilities for ReflACT backends."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

_RESPONSES_API_MODELS = {
    "gpt-5.3-codex",
    "gpt-5.1-codex",
    "gpt-5.2-codex",
    "gpt-5-codex",
    "codex-mini",
    "gpt-5.4-pro",
}

_BACKEND_DEFAULT_MODELS = {
    "azure_openai": "gpt-4o",
    "openai_chat": "gpt-4o",
    "codex": "gpt-4o",
    "codex_exec": "gpt-4o",
    "claude": "claude-sonnet-4-6",
    "claude_chat": "claude-sonnet-4-6",
    "claude_code_exec": "claude-sonnet-4-6",
    "cursor_exec": "composer-2.5",
    # copilot_chat / copilot_exec deliberately use the Azure default fallback
    # from default_model_for_backend(). That fallback configures shared role
    # deployments (not the CLI model, which uses the copilot_chat model keys)
    # and keeps the openai_chat optimizer for copilot_exec non-empty.
    "qwen_chat": "Qwen/Qwen3.5-4B",
    "minimax_chat": "MiniMax-M3",
    "openai_compatible": "gpt-4o-mini",
}

_BACKEND_ALIASES = {
    "azure": "azure_openai",
    "azure_openai": "azure_openai",
    "azure-openai": "azure_openai",
    "openai_chat": "openai_chat",
    "openai": "codex",
    "codex": "codex",
    "codex_exec": "codex_exec",
    "claude": "claude_chat",
    "claude_chat": "claude_chat",
    "claude_code_exec": "claude_code_exec",
    "cursor": "cursor_exec",
    "cursor_agent": "cursor_exec",
    "cursor_exec": "cursor_exec",
    "copilot": "copilot_chat",
    "copilot_cli": "copilot_chat",
    "github_copilot": "copilot_chat",
    "copilot_chat": "copilot_chat",
    "copilot_exec": "copilot_exec",
    "anthropic": "claude_chat",
    "qwen": "qwen_chat",
    "qwen_chat": "qwen_chat",
    "minimax": "minimax_chat",
    "minimax_chat": "minimax_chat",
    "openai_compatible": "openai_compatible",
    "openai_compatible_chat": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "compat": "openai_compatible",
}


def normalize_backend_name(name: str | None) -> str:
    normalized = str(name or "").strip().lower()
    return _BACKEND_ALIASES.get(normalized, normalized or "azure_openai")


def default_model_for_backend(backend: str | None) -> str:
    return _BACKEND_DEFAULT_MODELS.get(
        normalize_backend_name(backend),
        _BACKEND_DEFAULT_MODELS["azure_openai"],
    )


def needs_responses_api(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return any(
        normalized == prefix or normalized.startswith(prefix + "-")
        for prefix in _RESPONSES_API_MODELS
    )


class TokenTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, int]] = {}

    def record(self, stage: str, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            if stage not in self._data:
                self._data[stage] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }
            entry = self._data[stage]
            entry["calls"] += 1
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens

    def summary(self) -> dict[str, dict[str, int]]:
        with self._lock:
            out: dict[str, dict[str, int]] = {}
            total_prompt = total_completion = total_calls = 0
            for stage, entry in sorted(self._data.items()):
                prompt_tokens = entry["prompt_tokens"]
                completion_tokens = entry["completion_tokens"]
                out[stage] = {
                    "calls": entry["calls"],
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
                total_prompt += prompt_tokens
                total_completion += completion_tokens
                total_calls += entry["calls"]
            out["_total"] = {
                "calls": total_calls,
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
            }
            return out

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


tracker = TokenTracker()


@dataclass
class CompatToolFunction:
    name: str
    arguments: str

    def model_dump(self, mode: str = "json") -> dict[str, str]:
        del mode
        return {
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class CompatToolCall:
    id: str
    function: CompatToolFunction
    type: str = "function"

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "id": self.id,
            "type": self.type,
            "function": self.function.model_dump(),
        }


@dataclass
class CompatAssistantMessage:
    content: str
    tool_calls: list[CompatToolCall] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        del mode
        data: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [tool_call.model_dump() for tool_call in self.tool_calls]
        return data


def usage_from_openai_usage(usage: Any) -> dict[str, int]:
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def usage_from_responses_usage(usage: Any) -> dict[str, int]:
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = getattr(usage, "input_tokens", 0) or 0
    completion_tokens = getattr(usage, "output_tokens", 0) or 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


# Message fields this adapter models. Anything else the provider returns is carried through in
# `metadata["unmodelled_message_fields"]` rather than dropped, because OQA-FOR-1 could not tell an empty
# reply from one whose text arrived in a field nobody looked at.
_MODELLED_MESSAGE_FIELDS = ("role", "content", "tool_calls", "function_call", "refusal",
                            "annotations", "audio", "parsed",
                            # reported on its own in `response_metadata`, so not a surprise
                            "reasoning_content")


def _unmodelled_message_fields(message: Any) -> dict[str, Any]:
    """Every field the provider's message carries that this adapter does not model.

    Values are summarised, not copied: a type, a length, and a short prefix for a string. The point is
    to make a surprising field VISIBLE without putting an unbounded blob into a row or a log.
    """
    out: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    extra = getattr(message, "model_extra", None)
    if isinstance(extra, dict):
        raw.update(extra)
    dump = getattr(message, "model_dump", None)
    if callable(dump):
        try:
            d = dump()
            if isinstance(d, dict):
                for k, v in d.items():
                    if k not in _MODELLED_MESSAGE_FIELDS:
                        raw.setdefault(k, v)
        except Exception:  # noqa: BLE001 - diagnostics must never affect a call
            pass
    for k, v in raw.items():
        if v is None:
            continue
        if isinstance(v, str):
            out[k] = {"type": "str", "chars": len(v), "prefix": v[:200]}
        elif isinstance(v, (int, float, bool)):
            out[k] = {"type": type(v).__name__, "value": v}
        elif isinstance(v, (list, tuple)):
            out[k] = {"type": "list", "len": len(v), "prefix": str(v)[:200]}
        elif isinstance(v, dict):
            out[k] = {"type": "dict", "keys": sorted(v)[:20]}
        else:
            out[k] = {"type": type(v).__name__, "prefix": str(v)[:200]}
    return out


def response_metadata(message: Any, response: Any = None, choice: Any = None) -> dict[str, Any]:
    """What the provider said about a reply, beyond its content and tool calls.

    WHY IT EXISTS. `rollout.py` already reads `message.metadata` through
    `_message_debug_metadata`, and the three non-local-tools branches already record it; the local-tools
    branch could not, because the adapter never filled it. So a campaign ran to completion with
    `last_finish_reason` empty on every row, and twelve truncations at the token cap were invisible.
    """
    usage = getattr(response, "usage", None)
    usage_plain: dict[str, Any] = {}
    if usage is not None:
        dump = getattr(usage, "model_dump", None)
        if callable(dump):
            try:
                d = dump()
                if isinstance(d, dict):
                    usage_plain = {k: v for k, v in d.items() if v is not None}
            except Exception:  # noqa: BLE001
                usage_plain = {}
        if not usage_plain:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                v = getattr(usage, k, None)
                if v is not None:
                    usage_plain[k] = v
    content = getattr(message, "content", None)
    reasoning = getattr(message, "reasoning_content", None)
    tool_calls = getattr(message, "tool_calls", None) or []
    meta: dict[str, Any] = {
        "finish_reason": getattr(choice, "finish_reason", None),
        "response_id": getattr(response, "id", None),
        "response_model": getattr(response, "model", None),
        "system_fingerprint": getattr(response, "system_fingerprint", None),
        "usage": usage_plain,
        "completion_tokens": usage_plain.get("completion_tokens"),
        "prompt_tokens": usage_plain.get("prompt_tokens"),
        # The provider's own charge when it reports one. RECORDED, never substituted for the ledger's
        # computed figure, which is what the cap is enforced on.
        "provider_cost_usd": usage_plain.get("cost_usd"),
        "content_present": content is not None and bool(str(content).strip()),
        "content_chars": len(str(content)) if content is not None else 0,
        "content_is_null": content is None,
        "reasoning_content_present": reasoning is not None and bool(str(reasoning).strip()),
        "reasoning_content_chars": len(str(reasoning)) if reasoning is not None else 0,
        "n_tool_calls": len(tool_calls),
        "tool_names": [getattr(getattr(tc, "function", None), "name", "") or "" for tc in tool_calls],
        "unmodelled_message_fields": _unmodelled_message_fields(message),
        "metadata_source": "live",
    }
    return meta


def compat_message_from_chat_message(message: Any, response: Any = None,
                                     choice: Any = None) -> CompatAssistantMessage:
    """The adapter. With `response` supplied it also carries the reply's metadata; without it, unchanged.

    The default keeps every other backend's behaviour exactly as it was: `metadata` stays empty unless a
    caller hands over the response object the fields have to come from.
    """
    content = getattr(message, "content", "") or ""
    tool_calls = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        function = getattr(tool_call, "function", None)
        tool_calls.append(
            CompatToolCall(
                id=getattr(tool_call, "id", "") or "",
                function=CompatToolFunction(
                    name=getattr(function, "name", "") or "",
                    arguments=getattr(function, "arguments", "") or "{}",
                ),
            )
        )
    meta = response_metadata(message, response, choice) if response is not None or choice is not None else {}
    return CompatAssistantMessage(content=content, tool_calls=tool_calls, metadata=meta)


def compat_message_from_responses_output(output: list[Any]) -> CompatAssistantMessage:
    text_parts: list[str] = []
    tool_calls: list[CompatToolCall] = []
    for item in output:
        item_type = getattr(item, "type", "") or ""
        if item_type == "function_call":
            raw_arguments = getattr(item, "arguments", None)
            if raw_arguments is None:
                raw_arguments = json.dumps(getattr(item, "input", {}) or {})
            tool_calls.append(
                CompatToolCall(
                    id=getattr(item, "call_id", "") or getattr(item, "id", "") or "",
                    function=CompatToolFunction(
                        name=getattr(item, "name", "") or "",
                        arguments=str(raw_arguments or "{}"),
                    ),
                )
            )
            continue
        if item_type != "message":
            continue
        for part in getattr(item, "content", []) or []:
            part_type = getattr(part, "type", "") or ""
            if part_type in {"output_text", "text"}:
                text_parts.append(getattr(part, "text", "") or "")
    return CompatAssistantMessage(content="".join(text_parts), tool_calls=tool_calls)
