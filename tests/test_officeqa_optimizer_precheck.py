"""The optimizer endpoint is checked, and the call budget estimated, before any paid call is made.

    python3.12 -m pytest tests/test_officeqa_optimizer_precheck.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "research", "officeqa"))

pytest.importorskip("skillopt.config", reason="needs SkillOpt importable (Python >= 3.10)")

import oqa_optimizer_precheck as PC  # noqa: E402

SCRIPT = os.path.join(REPO, "research", "officeqa", "oqa_optimizer_precheck.py")
CONFIG = os.path.join(REPO, "configs", "research", "officeqa_cmp.yaml")


# ── the call budget ────────────────────────────────────────────────────────────

def test_the_budget_matches_what_the_reflect_and_aggregate_stages_do():
    """9 failures and 3 successes at M=8: two failure minibatches, one success minibatch."""
    budget = PC.expected_calls(9, 3, 8, steps=4)
    assert budget["analyst_calls"] == 3          # ceil(9/8) + ceil(3/8)
    assert budget["merge_calls"] == 1            # the two failure patches are merged; one success patch is not
    assert budget["final_merge_calls"] == 1      # both groups produced edits
    assert budget["per_step_min"] == 5 and budget["per_step_max"] == 6
    assert budget["run_min"] == 20 and budget["run_max"] == 24


def test_a_single_minibatch_needs_no_merge_call():
    budget = PC.expected_calls(5, 0, 8, steps=1)
    assert budget["analyst_calls"] == 1
    assert budget["merge_calls"] == 0 and budget["final_merge_calls"] == 0
    assert budget["per_step_max"] == 2           # the analyst call plus at most the ranking call


def test_every_item_failing_still_fits_two_minibatches():
    budget = PC.expected_calls(12, 0, 8, steps=4)
    assert budget["analyst_calls"] == 2 and budget["merge_calls"] == 1
    assert budget["run_max"] == 16


# ── the endpoint check, against a mock endpoint; no paid call anywhere ─────────

class _Models(BaseHTTPRequestHandler):
    MODELS = ["deepseek-flash", "deepseek-chat"]
    calls: list = []

    def do_GET(self):  # noqa: N802
        type(self).calls.append(self.path)
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_response(401); self.end_headers(); return
        body = json.dumps({"data": [{"id": m} for m in self.MODELS]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        type(self).calls.append("POST " + self.path)
        self.send_response(500); self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture()
def endpoint():
    _Models.calls = []
    server = HTTPServer(("127.0.0.1", 0), _Models)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", _Models
    server.shutdown()


def test_the_model_list_is_read_and_nothing_is_posted(endpoint):
    base, handler = endpoint
    models = PC.list_models(base, "test-key")
    assert models == ["deepseek-chat", "deepseek-flash"]
    assert handler.calls == ["/models"], "the precheck must not send a chat completion"


def test_a_missing_key_is_refused(endpoint):
    """An unauthenticated model listing must raise rather than be treated as "no models"."""
    base, _ = endpoint
    with pytest.raises(urllib.error.HTTPError):
        PC.list_models(base, "")


def _run(tmp_path, env_extra, rollout=None):
    env = dict(os.environ, PYTHONPATH=REPO, **env_extra)
    out = tmp_path / "precheck.json"
    cmd = [sys.executable, SCRIPT, "--config", CONFIG, "--out", str(out)]
    if rollout:
        cmd += ["--rollout", str(rollout)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc, json.loads(out.read_text()) if out.exists() else None


def test_an_empty_model_id_fails_closed(tmp_path):
    proc, report = _run(tmp_path, {"OPTIMIZER_MODEL": "", "OPTIMIZER_OPENAI_COMPATIBLE_API_KEY": "k"})
    assert proc.returncode == 1
    assert report["verdict"] == "FAIL"
    assert any("OPTIMIZER_MODEL is empty" in p for p in report["problems"])
    assert report["paid_calls_made"] == 0


def test_a_missing_api_key_fails_closed(tmp_path):
    proc, report = _run(tmp_path, {"OPTIMIZER_MODEL": "deepseek-flash",
                                   "OPTIMIZER_OPENAI_COMPATIBLE_API_KEY": ""})
    assert proc.returncode == 1
    assert any("API_KEY" in p for p in report["problems"])


def test_an_over_budget_run_is_reported_as_a_problem(tmp_path):
    """The point of the estimate: an overrun surfaces before the first paid call, not after it."""
    rollout = tmp_path / "rollout"
    rollout.mkdir()
    rows = [{"id": f"UID{i:04d}", "hard": 0} for i in range(9)] + [{"id": "UID9001", "hard": 1}]
    (rollout / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    budget = PC.expected_calls(9, 1, 8, steps=4)
    assert budget["run_max"] == 24
    tight = PC.expected_calls(9, 1, 8, steps=5)
    assert tight["run_max"] == 30, "five steps would exceed the approved 24"
