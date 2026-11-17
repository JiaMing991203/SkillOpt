"""Offline checks for the OfficeQA API smoke test, including an end-to-end tool loop against a FAKE endpoint.

Why a fake endpoint. The smoke test's real question is whether an OpenAI-compatible API can drive OfficeQA's
document-tool loop. Two things have to work: the provider must emit well-formed `tool_calls`, and SkillOpt must
execute them and feed the observations back. Only the first needs the real provider. The second is testable here,
against a local HTTP server that speaks the OpenAI chat-completions shape and returns a `tool_calls` reply - so
when the live key arrives, the only untested variable is the provider's own behaviour.

Constructed inputs only: these establish software behaviour, not research findings (design v0.1 section 7,
Stage A). No network beyond 127.0.0.1, no API key, no GPU.

Run: pytest tests/test_officeqa_api_smoke.py
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research", "officeqa"))
sys.path.insert(0, ROOT)

import oqa_api_smoke as SMOKE  # noqa: E402


# ── the ledger: caps are hard ───────────────────────────────────────────────────

def test_attempt_cap_is_hard():
    led = SMOKE.Ledger(max_attempts=3, usd_cap=10.0, output_cap_tokens=100)
    for _ in range(3):
        led.reserve(400)
    with pytest.raises(SMOKE.CapReached, match="attempt cap"):
        led.reserve(400)
    assert led.summary()["attempts"] == 3


def test_usd_cap_is_hard_and_reserves_the_worst_case():
    # One attempt's worst case at a 1,000,000-token output cap is 1e6/1e6 * 0.07 = $0.07.
    led = SMOKE.Ledger(max_attempts=100, usd_cap=0.10, output_cap_tokens=1_000_000)
    led.reserve(4)
    with pytest.raises(SMOKE.CapReached, match="USD cap"):
        led.reserve(4)
    assert led.reserved_usd == pytest.approx(0.07, abs=1e-6)


def test_an_attempt_with_no_usage_stays_charged_at_the_worst_case_and_is_flagged():
    led = SMOKE.Ledger(max_attempts=5, usd_cap=1.0, output_cap_tokens=8000)
    rec = led.reserve(4000)
    led.settle(rec, usage=None, outcome="error:Timeout", latency_s=300.0)
    s = led.summary()
    assert s["unreconciled"] == 1
    assert s["settled_usd"] == pytest.approx(rec["reserved_usd"])


def test_settlement_prices_cached_input_lower_than_fresh_input():
    led = SMOKE.Ledger(max_attempts=5, usd_cap=1.0, output_cap_tokens=8000)
    rec = led.reserve(4000)
    led.settle(rec, usage={"prompt_tokens": 1_000_000, "completion_tokens": 0,
                           "prompt_tokens_details": {"cached_tokens": 1_000_000}},
               outcome="ok", latency_s=1.0, tool_calls=1)
    assert rec["actual_usd"] == pytest.approx(SMOKE.PRICE_CACHED_IN_PER_M, abs=1e-9)
    assert rec["cached_prompt_tokens"] == 1_000_000


# ── the preflight: fail closed ──────────────────────────────────────────────────

class _ModelsHandler(http.server.BaseHTTPRequestHandler):
    BODY: dict = {}

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.BODY).encode())

    def log_message(self, *a):  # silence
        return


def _serve(handler_cls):
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}/v1"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_preflight_passes_on_a_model_that_declares_function_calling():
    _ModelsHandler.BODY = {"id": "qwen3-5-4b", "aliases": ["qwen3.5-4b"],
                           "features": ["reasoning", "function_calling", "cache"],
                           "hugging_face_id": "Qwen/Qwen3.5-4B", "weight_precision": "FP8 weights + FP8 KV",
                           "context_window": 262144, "max_output_tokens": 32768}
    srv, base = _serve(_ModelsHandler)
    try:
        out = SMOKE.preflight(base, "qwen3-5-4b")
        assert out["verdict"] == "PASS"
        assert out["hugging_face_id"] == "Qwen/Qwen3.5-4B"
        assert "FP8" in out["weight_precision"]
        # an alias is accepted as the model's own identity
        assert SMOKE.preflight(base, "qwen3.5-4b")["verdict"] == "PASS"
    finally:
        srv.shutdown()


def test_preflight_refuses_a_model_that_does_not_declare_function_calling():
    _ModelsHandler.BODY = {"id": "qwen3-5-4b", "features": ["reasoning", "cache"]}
    srv, base = _serve(_ModelsHandler)
    try:
        with pytest.raises(SMOKE.PreflightFailed, match="function_calling"):
            SMOKE.preflight(base, "qwen3-5-4b")
    finally:
        srv.shutdown()


def test_preflight_refuses_a_substituted_model():
    _ModelsHandler.BODY = {"id": "some-other-model", "features": ["function_calling"]}
    srv, base = _serve(_ModelsHandler)
    try:
        with pytest.raises(SMOKE.PreflightFailed, match="identifies itself"):
            SMOKE.preflight(base, "qwen3-5-4b")
    finally:
        srv.shutdown()


def test_preflight_refuses_an_unreachable_endpoint():
    with pytest.raises(SMOKE.PreflightFailed, match="unreachable|HTTP"):
        SMOKE.preflight(f"http://127.0.0.1:{_free_port()}/v1", "qwen3-5-4b")


# ── the verdict logic ───────────────────────────────────────────────────────────

def _led(tool_call_attempts=1, malformed=0):
    led = SMOKE.Ledger(max_attempts=50, usd_cap=1.0, output_cap_tokens=8000)
    for n in range(max(1, tool_call_attempts)):
        rec = led.reserve(100)
        led.settle(rec, usage={"prompt_tokens": 10, "completion_tokens": 5}, outcome="ok", latency_s=1.0,
                   tool_calls=1 if n < tool_call_attempts else 0,
                   malformed="JSONDecodeError: ..." if n < malformed else "")
    return led


def test_verdict_passes_when_the_loop_works():
    per_item = [{"id": "A", "tool_calls_executed": 3, "tool_observations_error": 0, "turns_after_first_tool": 2}]
    v = SMOKE.tool_loop_verdict(per_item, _led())
    assert v["verdict"] == "PASS" and all(v["checks"].values())


def test_verdict_fails_when_no_tool_call_came_back():
    per_item = [{"id": "A", "tool_calls_executed": 0, "tool_observations_error": 0, "turns_after_first_tool": 0}]
    v = SMOKE.tool_loop_verdict(per_item, _led(tool_call_attempts=0))
    assert v["verdict"] == "FAIL" and v["checks"]["structured_tool_calls_returned"] is False


def test_verdict_fails_on_malformed_arguments_even_if_tools_ran():
    per_item = [{"id": "A", "tool_calls_executed": 1, "tool_observations_error": 0, "turns_after_first_tool": 1}]
    v = SMOKE.tool_loop_verdict(per_item, _led(tool_call_attempts=2, malformed=1))
    assert v["verdict"] == "FAIL" and v["checks"]["arguments_usable"] is False


def test_verdict_fails_when_results_were_never_fed_back():
    per_item = [{"id": "A", "tool_calls_executed": 2, "tool_observations_error": 0, "turns_after_first_tool": 0}]
    v = SMOKE.tool_loop_verdict(per_item, _led())
    assert v["verdict"] == "FAIL" and v["checks"]["results_fed_back"] is False


def test_selection_rule_is_recorded_and_names_the_training_split_only():
    assert SMOKE.SELECTED_IDS == ["UID0033", "UID0063", "UID0034"]
    assert "TRAINING" in SMOKE.SELECTION_RULE and "never read" in SMOKE.SELECTION_RULE


def test_select_items_refuses_an_id_outside_the_training_split(tmp_path):
    train = tmp_path / "train"
    train.mkdir()
    (train / "a.json").write_text(json.dumps([{"id": "UID0033", "question": "q"}]))
    assert [i["id"] for i in SMOKE.select_items(str(tmp_path), ["UID0033"])] == ["UID0033"]
    with pytest.raises(RuntimeError, match="not found"):
        SMOKE.select_items(str(tmp_path), ["UID0091"])   # a validation id


# ── end to end against a fake OpenAI-compatible endpoint ────────────────────────

class _ChatHandler(http.server.BaseHTTPRequestHandler):
    """Speaks just enough of /chat/completions: first reply a tool call, then a final answer."""

    SEEN: list = []
    MALFORMED_ARGS = False

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": "qwen3-5-4b", "features": ["function_calling"]}).encode())

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        _ChatHandler.SEEN.append(body)
        tool_results = [m for m in body.get("messages", []) if m.get("role") == "tool"]
        if not tool_results:
            # Use the path the prompt actually advertises under "## Candidate Files", exactly as the real model
            # does. A bare basename is refused by run_tool ("[grep error: path not allowed]"), so taking the path
            # from the prompt is also what makes this test cover candidate-file resolution.
            blob = "\n".join(str(m.get("content") or "") for m in body.get("messages", []))
            paths = [ln.strip().lstrip("- ").strip() for ln in blob.splitlines()
                     if ln.strip().startswith("-") and "treasury_bulletin" in ln]
            path = paths[0] if paths else "treasury_bulletin_1963_01.txt"
            args = "{not json" if _ChatHandler.MALFORMED_ARGS else json.dumps(
                {"pattern": "Total paper money", "path": path})
            message = {"role": "assistant", "content": "Let me grep the bulletin.",
                       "tool_calls": [{"id": "call_1", "type": "function",
                                       "function": {"name": "grep", "arguments": args}}]}
            finish = "tool_calls"
        else:
            # The observation really did come back to us: quote it, so the test can prove the feed-back path.
            got = str(tool_results[-1].get("content") or "")[:40]
            message = {"role": "assistant", "content": f"I received: {got!r}. <answer>30,442</answer>"}
            finish = "stop"
        out = {"id": "chatcmpl-test", "object": "chat.completion", "model": "qwen3-5-4b",
               "choices": [{"index": 0, "message": message, "finish_reason": finish}],
               "usage": {"prompt_tokens": 1200, "completion_tokens": 40,
                         "prompt_tokens_details": {"cached_tokens": 0}}}
        payload = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        return


@pytest.fixture
def fake_docs(tmp_path):
    """One parsed bulletin page the local document tools can really read."""
    docs = tmp_path / "docs" / "transformed"
    docs.mkdir(parents=True)
    (docs / "treasury_bulletin_1963_01.txt").write_text(
        "MONETARY STATISTICS\n(In millions of dollars)\n"
        "| End of fiscal year or month > End of fiscal year or month | Paper money 2/ > Total paper money |\n"
        "| 1960-December | 30442 |\n| 1961-January | 29385 |\n", encoding="utf-8")
    return str(tmp_path / "docs")


def _run_loop(fake_docs, monkeypatch, malformed=False):
    _ChatHandler.SEEN = []
    _ChatHandler.MALFORMED_ARGS = malformed
    srv, base = _serve(_ChatHandler)
    try:
        monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_BASE_URL", base)
        monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_API_KEY", "test-key-never-real")
        monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_MODEL", "qwen3-5-4b")
        # BOTH roles must be "openai_compatible": skillopt/model dispatches on the COMBINED backend name, and
        # the two roles are separated by their env prefixes (TARGET_* vs OPTIMIZER_*), not by the module. Setting
        # target_backend to "openai_chat" instead routes to AZURE OpenAI - which is what the first version of this
        # test did, and it failed with "Azure OpenAI endpoint is not configured for target". Worth having found
        # offline: configs/research/officeqa_api.yaml sets both keys for the same reason.
        from skillopt.model import backend_config
        backend_config.set_optimizer_backend("openai_compatible")
        backend_config.set_target_backend("openai_compatible")
        # Configure the backend EXPLICITLY rather than relying on the env vars above. The backend keeps a
        # module-level config object per role, and other tests in the suite call configure_openai_compatible(),
        # so a run of the whole suite leaves a stale base URL and the key "dummy" behind - this test passed alone
        # and failed after tests/test_openai_compatible_backend.py with "401 Incorrect API key provided: dummy".
        from skillopt.model import openai_compatible_backend as OC
        OC.configure_openai_compatible(target_base_url=base, target_api_key="test-key-never-real",
                                       target_model="qwen3-5-4b")
        from skillopt.envs.officeqa.rollout import process_one
        led = SMOKE.Ledger(max_attempts=20, usd_cap=1.0, output_cap_tokens=8000)
        restore = SMOKE.install_guard(led)
        try:
            out = os.path.abspath(os.path.join(fake_docs, os.pardir, "out"))
            os.makedirs(out, exist_ok=True)
            result = process_one(
                {"id": "UID0033", "question": "How much total paper money in December 1960, in millions?",
                 "ground_truth": "30,442", "source_files": ["treasury_bulletin_1963_01.txt"], "source_docs": []},
                out, "Answer OfficeQA questions using the document tools.",
                max_tool_turns=4, max_completion_tokens=512, search_mode="offline",
                use_local_tools=True, data_dirs=fake_docs)
        finally:
            restore()
        return result, led, os.path.join(out, "predictions")
    finally:
        srv.shutdown()


def test_the_whole_tool_loop_runs_against_an_openai_compatible_endpoint(fake_docs, monkeypatch):
    """The end-to-end claim the smoke test will make against the real provider, proved here against a fake one:
    a tool_calls reply is parsed, the local document tool is executed, and the observation is fed back."""
    result, led, out = _run_loop(fake_docs, monkeypatch)
    item = SMOKE.analyse_item(out, "UID0033", result)
    assert item["tool_calls_executed"] >= 1, result.get("fail_reason")
    assert item["tool_observations_error"] == 0
    assert item["turns_after_first_tool"] >= 1
    # the tool really read the file: the actor's final message quotes what came back
    assert "I received" in (result.get("response") or ""), result.get("response")
    # and SkillOpt sent the tool result back as a role=tool message
    assert any(m.get("role") == "tool" for body in _ChatHandler.SEEN for m in body.get("messages", []))
    # the schemas really were advertised to the endpoint
    assert any(body.get("tools") for body in _ChatHandler.SEEN)
    v = SMOKE.tool_loop_verdict([item], led)
    assert v["verdict"] == "PASS", v


def test_malformed_tool_arguments_are_detected_rather_than_silently_passed(fake_docs, monkeypatch):
    """A reply whose tool-call arguments are not JSON must fail the verdict, not pass silently.

    It is detected one level earlier than first expected: the client library rejects the malformed response before
    the driver sees a message, so it arrives as an attempt ERROR rather than a parsed-but-bad call. Either way no
    tool is executed, which is what the verdict keys on. The test asserts the real path.
    """
    result, led, out = _run_loop(fake_docs, monkeypatch, malformed=True)
    item = SMOKE.analyse_item(out, "UID0033", result)
    v = SMOKE.tool_loop_verdict([item], led)
    s = led.summary()
    assert s["malformed_replies"] >= 1 or s["errors"] >= 1, s
    assert s["error_reasons"] or s["malformed_replies"], "the reason must be recorded for a reader"
    assert item["tool_calls_executed"] == 0
    assert v["verdict"] == "FAIL"
    assert v["checks"]["tools_executed"] is False and v["checks"]["arguments_usable"] is False
