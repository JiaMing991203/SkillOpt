"""The opt-in optimizer call log records attempts without changing any call.

<ROLE>_OPENAI_COMPATIBLE_CALL_LOG is a research diagnostic: requests, return
values, token accounting and the retry loop must be identical with and
without it, and the API key must never be written.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from skillopt.model import openai_compatible_backend as backend

SECRET = "sk-test-secret-should-never-be-logged"


class _Usage(SimpleNamespace):
    def model_dump(self) -> dict:
        return dict(vars(self))


class _Completions:
    """Scripted create(): each entry is a reply text or an exception."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        message = SimpleNamespace(content=item, reasoning_content="private thinking", tool_calls=[])
        usage = _Usage(prompt_tokens=20, completion_tokens=7, total_tokens=27,
                       prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=20)
        return SimpleNamespace(id="resp-1", model="DeepSeek-V4.1-Flash-test", system_fingerprint=None,
                               choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    saved = vars(backend.OPTIMIZER_CONFIG).copy()
    backend.OPTIMIZER_CONFIG.api_key = SECRET
    backend.OPTIMIZER_CONFIG.deployment = "deepseek-flash"
    backend.OPTIMIZER_CONFIG.max_tokens = 16384
    backend.OPTIMIZER_CONFIG.temperature = None
    for var in ("OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG", "OPENAI_COMPATIBLE_CALL_LOG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(backend.time, "sleep", lambda s: None)
    backend.reset_token_tracker()
    yield
    backend.reset_token_tracker()
    vars(backend.OPTIMIZER_CONFIG).update(saved)
    backend._reset_clients()


def _call(monkeypatch, script):
    completions = _Completions(script)
    monkeypatch.setattr(backend, "_get_client", lambda role: SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    try:
        result = backend.chat_optimizer("SYSTEM PROMPT", "USER PROMPT", max_completion_tokens=16384,
                                        retries=3, stage="analyst")
    except RuntimeError as e:
        result = ("raised", str(e).split(":")[0])
    return result, completions.calls, backend.get_token_summary()


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_log_off_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result, calls, _ = _call(monkeypatch, ['{"patch": {}}'])
    assert result[0] == '{"patch": {}}'
    assert list(tmp_path.iterdir()) == []


def test_log_does_not_change_requests_results_or_tokens(tmp_path, monkeypatch):
    script = [RuntimeError("HTTP 503"), '{"patch": {"edits": []}}']
    plain = _call(monkeypatch, list(script))
    backend.reset_token_tracker()
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG", str(log))
    logged = _call(monkeypatch, list(script))
    assert logged == plain  # same return value, same create() kwargs per attempt, same token totals

    first, second = _records(log)
    assert (first["attempt"], first["outcome"], first["error"]["type"]) == (1, "error", "RuntimeError")
    assert second["attempt"] == 2 and second["outcome"] == "ok" and second["max_attempts"] == 3
    assert second["stage"] == "analyst" and second["role"] == "optimizer"
    assert second["request"]["model"] == "deepseek-flash" and second["request"]["max_tokens"] == 16384
    assert second["request"]["messages"] == [{"role": "system", "content": "SYSTEM PROMPT"},
                                             {"role": "user", "content": "USER PROMPT"}]
    resp = second["response"]
    assert resp["content"] == '{"patch": {"edits": []}}'
    assert resp["reasoning_content"] == "private thinking"
    assert resp["model"] == "DeepSeek-V4.1-Flash-test" and resp["finish_reason"] == "stop"
    assert resp["usage"]["prompt_cache_miss_tokens"] == 20
    assert SECRET not in log.read_text()


def test_exhausted_retries_are_all_logged_and_still_raise(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG", str(log))
    result, calls, tokens = _call(monkeypatch, [TimeoutError("t1"), TimeoutError("t2"), TimeoutError("t3")])
    assert result == ("raised", "OpenAI-compatible chat call failed after 3 retries")
    assert len(calls) == 3
    assert [r["outcome"] for r in _records(log)] == ["error"] * 3
    assert tokens.get("analyst") is None or tokens["analyst"]["calls"] == 0


def test_unwritable_log_never_affects_the_call(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG", str(tmp_path / "missing_dir" / "calls.jsonl"))
    result, calls, tokens = _call(monkeypatch, ['{"patch": {}}', '{"unused": 1}'])
    assert result[0] == '{"patch": {}}'
    assert len(calls) == 1  # no extra attempt caused by the failed write
    assert tokens["analyst"]["calls"] == 1
