"""No-GPU regression check for research/officeqa/oqa_tool_probe.py.

Why this exists. Job 66171008 spent a 30-minute A40 reservation and answered nothing: the probe called
`chat_target_messages` without selecting a target backend, so the request was routed to the Azure path and never
reached the job-local vLLM server. Nothing caught it beforehand because the probe's call path had never been
executed - only syntax-checked.

What this test does. It stands up a mock OpenAI-compatible server on loopback, points the TARGET_QWEN_CHAT_*
environment at it exactly as research/cluster/lib.sh does after start_vllm, and runs the probe's real `main()`.
Then it asserts, from the request the mock actually received:

  * the probe resolved the target backend to `qwen_chat` (not the Azure default);
  * it sent the request to the job-local endpoint, on the mock's own port;
  * the body carries the adapter's three tool definitions and `tool_choice: "auto"`;
  * the model is the one the config pins.

It also exercises the three verdict paths (usable tool call, tool syntax returned as content, nothing) and the
fail-fast guards, so a future change to either cannot pass unnoticed.

This needs Python >= 3.10 (SkillOpt uses `dataclass(slots=True)`), which the workstation has as python3.12:
    python3.12 -m pytest tests/test_officeqa_tool_probe.py
No GPU, no cluster, no network beyond loopback, and no paid call.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "research", "officeqa"))

pytest.importorskip("skillopt.model", reason="needs the SkillOpt package importable (Python >= 3.10)")

import oqa_fixture as FIX  # noqa: E402
import oqa_tool_probe as PROBE  # noqa: E402

CONFIG = os.path.join(REPO, "configs", "research", "officeqa_check3.yaml")

# What the mock returns for each scenario.
TOOL_CALL_REPLY = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "grep",
                     "arguments": json.dumps({"pattern": "interest-bearing debt",
                                              "path": "treasury_bulletin_1977_04.txt"})},
    }],
}
CONTENT_ONLY_REPLY = {
    "role": "assistant",
    "content": '<tool_call>\n{"name": "grep", "arguments": {"pattern": "debt"}}\n</tool_call>',
}
PLAIN_REPLY = {"role": "assistant", "content": "I cannot inspect the documents."}
MISSING_ARGS_REPLY = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{
        "id": "call_2", "type": "function",
        # `grep` requires both pattern and path; this omits path, so run_tool could not act on it.
        "function": {"name": "grep", "arguments": json.dumps({"pattern": "debt"})},
    }],
}


class _Mock(HTTPServer):
    reply: dict = PLAIN_REPLY
    requests: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json({"data": [{"id": "Qwen/Qwen3.5-4B", "object": "model"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else "{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"_unparsable": raw}
        self.server.requests.append({"path": self.path, "body": body,
                                     "authorization_present": bool(self.headers.get("Authorization"))})
        self._json({
            "id": "chatcmpl-mock", "object": "chat.completion", "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": self.server.reply, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })


@pytest.fixture()
def mock_server(monkeypatch):
    server = _Mock(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.reply = PLAIN_REPLY
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    # Exactly the variables research/cluster/lib.sh exports after start_vllm.
    monkeypatch.setenv("TARGET_QWEN_CHAT_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("TARGET_QWEN_CHAT_API_KEY", "mock-key-not-a-secret")
    monkeypatch.setenv("TARGET_QWEN_CHAT_MODEL", "Qwen/Qwen3.5-4B")
    monkeypatch.setenv("LSO_VLLM_EXTRA_ARGS", "--enable-auto-tool-choice --tool-call-parser hermes")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _run(tmp_path, argv_extra=(), with_fixture=True):
    out = tmp_path / "tool_probe.json"
    argv = ["oqa_tool_probe.py", "--out", str(out), "--config", CONFIG, *argv_extra]
    if with_fixture:
        argv += ["--fixture-dir", str(tmp_path / "fixture")]
    old = sys.argv
    sys.argv = argv
    try:
        code = PROBE.main()
    finally:
        sys.argv = old
    return code, json.loads(out.read_text())


# ── the regression this test exists for ────────────────────────────────────────

def test_probe_targets_the_job_local_qwen_server_and_sends_the_tools(mock_server, tmp_path):
    """The exact failure of job 66171008: wrong backend, request never reaching the job-local server."""
    mock_server.reply = TOOL_CALL_REPLY
    code, report = _run(tmp_path)

    assert mock_server.requests, "the probe sent no request at all - this is the 66171008 failure"
    request = mock_server.requests[-1]

    # 1. It reached the job-local endpoint, on the mock's port.
    port = mock_server.server_address[1]
    assert report["base_url"] == f"http://127.0.0.1:{port}/v1"
    assert "/chat/completions" in request["path"]

    # 2. It resolved to the qwen_chat backend, not the Azure default.
    assert report["wiring"]["configured_target_backend"] == "qwen_chat"
    assert report["wiring"]["config_target_backend"] == "qwen_chat"

    # 3. It sent the tool DEFINITIONS, with auto choice.
    body = request["body"]
    assert body.get("tool_choice") == "auto"
    sent = {t["function"]["name"] for t in body.get("tools", [])}
    assert sent == {"glob", "read", "grep"}, f"tool definitions not sent correctly: {sent}"
    grep_schema = next(t["function"] for t in body["tools"] if t["function"]["name"] == "grep")
    assert set(grep_schema["parameters"]["required"]) == {"pattern", "path"}

    # 4. The model is the one the config pins.
    assert body.get("model") == "Qwen/Qwen3.5-4B"

    # 5. The verdict reflects a usable structured call that ALSO executed against the fixture.
    assert report["verdict"] == "PASS", report
    assert report["usable_tool_calls"] == [
        {"tool": "grep", "arguments": {"pattern": "interest-bearing debt",
                                       "path": "treasury_bulletin_1977_04.txt"}}
    ]
    assert len(report["execution"]) == 1
    execution = report["execution"][0]
    assert execution["result"] == FIX.PASS_RESULT, execution
    assert FIX.EXPECTED_MATCH_SUBSTRING in execution["observation"]
    assert FIX.EXPECTED_ABSENT_SUBSTRING not in execution["observation"]
    assert code == 0


def test_structure_without_execution_is_not_a_pass(mock_server, tmp_path):
    """The criterion is receive AND execute. A structured call alone must not score PASS."""
    mock_server.reply = TOOL_CALL_REPLY
    code, report = _run(tmp_path, with_fixture=False)
    assert report["verdict"] == "PARTIAL"
    assert "did not execute" in report["diagnosis"]
    assert "execution_skipped" in report
    assert code == 1


def test_a_call_the_adapter_cannot_execute_is_not_a_pass(mock_server, tmp_path):
    """A well-formed call whose pattern matches nothing in the fixture must not score PASS."""
    mock_server.reply = {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "c", "type": "function", "function": {
            "name": "grep",
            "arguments": json.dumps({"pattern": "zzz-not-in-the-fixture",
                                     "path": "treasury_bulletin_1977_04.txt"})}}],
    }
    code, report = _run(tmp_path)
    assert report["verdict"] == "PARTIAL"
    assert report["execution"][0]["result"] == "the adapter refused or found nothing"
    assert code == 1


def test_tool_syntax_in_content_is_reported_as_a_server_flag_problem(mock_server, tmp_path):
    mock_server.reply = CONTENT_ONLY_REPLY
    code, report = _run(tmp_path)
    assert report["verdict"] == "FAIL"
    assert report["content_contains_tool_syntax"] is True
    assert "--enable-auto-tool-choice" in report["diagnosis"]
    assert code == 1


def test_no_tool_call_and_no_syntax_is_reported_as_template_or_model(mock_server, tmp_path):
    mock_server.reply = PLAIN_REPLY
    code, report = _run(tmp_path)
    assert report["verdict"] == "FAIL"
    assert report["content_contains_tool_syntax"] is False
    assert "template" in report["diagnosis"]
    assert code == 1


def test_a_call_missing_a_required_argument_is_not_a_pass(mock_server, tmp_path):
    """A call that parses but omits `path` cannot be executed by run_tool, so it must not score as usable."""
    mock_server.reply = MISSING_ARGS_REPLY
    code, report = _run(tmp_path)
    assert report["verdict"] == "PARTIAL"
    assert report["usable_tool_calls"] == []
    assert report["missing_required_arguments"] == [
        {"tool": "grep", "absent": ["path"], "got": ["pattern"]}
    ]
    assert code == 1


# ── the fail-fast guards ───────────────────────────────────────────────────────

def test_guard_refuses_a_non_qwen_backend():
    with pytest.raises(SystemExit) as exc:
        PROBE.assert_target_is_job_local_qwen(
            {"configured_target_backend": "openai_chat", "config": CONFIG})
    assert "not 'qwen_chat'" in str(exc.value)


def test_guard_refuses_a_missing_endpoint(monkeypatch):
    monkeypatch.delenv("TARGET_QWEN_CHAT_BASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        PROBE.assert_target_is_job_local_qwen(
            {"configured_target_backend": "qwen_chat", "config": CONFIG})
    assert "TARGET_QWEN_CHAT_BASE_URL is unset" in str(exc.value)


def test_guard_refuses_a_non_loopback_endpoint(monkeypatch):
    monkeypatch.setenv("TARGET_QWEN_CHAT_BASE_URL", "http://10.0.0.5:8000/v1")
    with pytest.raises(SystemExit) as exc:
        PROBE.assert_target_is_job_local_qwen(
            {"configured_target_backend": "qwen_chat", "config": CONFIG})
    assert "not loopback" in str(exc.value)


def test_config_wiring_reads_the_real_config():
    """The wiring must come from the config file, not from a copy of its values kept in the probe."""
    wiring = PROBE.configure_target_from_config(CONFIG)
    assert wiring["configured_target_backend"] == "qwen_chat"
    assert wiring["config_target_model"] == "Qwen/Qwen3.5-4B"
