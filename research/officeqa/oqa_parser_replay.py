"""Offline replay: does vLLM's qwen3_coder parser turn job 66190733's raw reply into a usable OfficeQA tool call?

Why. Job 66190733 proved the model emits a well-formed call, but in Qwen3-Coder XML, while the server ran
`--tool-call-parser hermes`, which expects JSON inside `<tool_call>`. So `tool_calls` came back empty and the
OfficeQA loop would have seen nothing. Before spending another A40 reservation on a different parser, replay the
EXACT bytes the model produced through the candidate parser and then through the adapter's own tool runtime.

No GPU, no model, no network: this loads a tokenizer from the local cache, runs a text parser, and reads one small
fixture file.

Two phases, because the two things live in different pinned environments:

  --phase parse     in the vLLM environment. Reads the raw assistant text out of a finished run's
                    tool_probe.json, builds the named parser through vLLM's own ToolParserManager, and reports the
                    structured calls it extracts. This is the same class the server would use, so a pass here is
                    evidence about the server's behaviour rather than about a reimplementation.
  --phase validate  in the controller environment. Takes the parsed call, checks it against the OfficeQA adapter's
                    own tool schemas (every required field present and non-empty), then EXECUTES it through
                    skillopt.envs.officeqa.tool_runtime.run_tool against a small allowed fixture and checks the
                    returned line.

Both phases write a JSON report and exit non-zero on failure, so a driver can stop before submitting a job.

Note on the two registered names: in this vLLM build `qwen3_coder` and `qwen3_xml` both resolve to
`Qwen3EngineToolParser` (vllm/tool_parsers/__init__.py), so they are one parser under two names, not two
candidates to choose between.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The adapter's three function schemas, kept here as a literal so this script can run in the vLLM environment,
# which does not have the controller's dependencies. Phase `validate` asserts they still match the adapter's own
# definitions, so this copy cannot drift silently.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "glob",
        "description": "Find candidate local document files by filename or relative-path glob pattern.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                       "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "read",
        "description": "Read a local text document excerpt by path and line window.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                                                        "start": {"type": "integer"},
                                                        "limit": {"type": "integer"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Search for a pattern inside one local text document.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"},
                                                        "path": {"type": "string"}},
                       "required": ["pattern", "path"]}}},
]

# The fixture and the execution check live in oqa_fixture.py, shared with the live probe.
import oqa_fixture as FIX  # noqa: E402


def load_raw_reply(probe_json: str) -> dict:
    """The exact assistant text from a finished probe run, with a completeness check.

    tool_probe.json stores `content_head` (the first 400 characters) next to `content_chars` (the true length), so
    the stored text is the complete reply only when those agree. Refuse rather than replay a truncated string.
    """
    with open(probe_json, encoding="utf-8") as f:
        probe = json.load(f)
    head = probe.get("content_head") or ""
    length = probe.get("content_chars")
    if length is None:
        raise SystemExit(f"{probe_json}: no content_chars field; cannot confirm the reply is complete")
    if len(head) != int(length):
        raise SystemExit(
            f"{probe_json}: stored content_head is {len(head)} chars but the reply was {length}; it is truncated, "
            f"so replaying it would not be the model's actual output"
        )
    return {"raw": head, "chars": int(length), "source": os.path.abspath(probe_json),
            "source_job_verdict": probe.get("verdict"), "source_parser": probe.get("vllm_extra_args")}


def phase_parse(args) -> dict:
    """Run the named vLLM tool parser over the raw reply. Must run in the vLLM environment."""
    from transformers import AutoTokenizer  # noqa: PLC0415
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest  # noqa: PLC0415
    from vllm.tool_parsers import ToolParserManager  # noqa: PLC0415

    reply = load_raw_reply(args.probe_json)
    parser_cls = ToolParserManager.get_tool_parser(args.parser)
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision or None, local_files_only=True)
    parser = parser_cls(tokenizer)
    request = ChatCompletionRequest(model=args.model, messages=[], tools=TOOL_SCHEMAS, tool_choice="auto")
    extracted = parser.extract_tool_calls(reply["raw"], request)

    calls = []
    for call in (getattr(extracted, "tool_calls", None) or []):
        function = getattr(call, "function", None)
        calls.append({"name": getattr(function, "name", None),
                      "arguments": getattr(function, "arguments", None)})
    report = {
        "phase": "parse",
        "parser": args.parser,
        "parser_class": f"{parser_cls.__module__}.{parser_cls.__name__}",
        "raw_reply": reply,
        "tools_calls_found": getattr(extracted, "tools_called", None),
        "calls": calls,
        "leftover_content": getattr(extracted, "content", None),
    }
    report["verdict"] = "PASS" if calls else "FAIL"
    if not calls:
        report["diagnosis"] = (f"parser {args.parser!r} extracted no tool call from the model's actual reply; it "
                               f"is not the parser this model's output needs")
    return report


def phase_validate(args) -> dict:
    """Validate the parsed call against the adapter's schemas, then execute it. Controller environment."""
    from skillopt.envs.officeqa.rollout import _TOOL_SCHEMAS  # noqa: PLC0415

    with open(args.parse_report, encoding="utf-8") as f:
        parsed = json.load(f)

    # The literal copy above must still match the adapter, or this script is validating against stale schemas.
    drift = []
    adapter = {s["function"]["name"]: s["function"]["parameters"].get("required", []) for s in _TOOL_SCHEMAS}
    mine = {s["function"]["name"]: s["function"]["parameters"].get("required", []) for s in TOOL_SCHEMAS}
    if set(adapter) != set(mine):
        drift.append(f"tool names differ: adapter {sorted(adapter)} vs replay {sorted(mine)}")
    for name in sorted(set(adapter) & set(mine)):
        if sorted(adapter[name]) != sorted(mine[name]):
            drift.append(f"{name} required fields differ: adapter {adapter[name]} vs replay {mine[name]}")

    report = {"phase": "validate", "schema_drift": drift, "checked_calls": []}
    if drift:
        report["verdict"] = "FAIL"
        report["diagnosis"] = "the replay's tool schemas no longer match the adapter's; fix the copy before trusting"
        return report

    required = FIX.required_fields_from_adapter()
    report["fixture"] = {"dir": os.path.abspath(args.fixture_dir), "file": FIX.FIXTURE_NAME,
                         "expected_substring": FIX.EXPECTED_MATCH_SUBSTRING,
                         "expected_absent": FIX.EXPECTED_ABSENT_SUBSTRING}
    overall = "FAIL"
    for call in parsed.get("calls", []):
        entry = FIX.execute_and_check(call.get("name"), call.get("arguments"), args.fixture_dir, required)
        if entry.get("result") == FIX.PASS_RESULT:
            overall = "PASS"
        report["checked_calls"].append(entry)

    report["verdict"] = overall
    if overall != "PASS":
        report["diagnosis"] = ("no parsed call survived schema validation and execution against the fixture; see "
                               "checked_calls")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=["parse", "validate"], required=True)
    ap.add_argument("--probe-json", help="tool_probe.json of the run whose raw reply is replayed (phase parse)")
    ap.add_argument("--parse-report", help="the JSON written by phase parse (phase validate)")
    ap.add_argument("--parser", default="qwen3_coder")
    ap.add_argument("--model", default="Qwen/Qwen3.5-4B")
    ap.add_argument("--revision", default="")
    ap.add_argument("--fixture-dir", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.phase == "parse" and not args.probe_json:
        ap.error("--probe-json is required for --phase parse")
    if args.phase == "validate":
        if not args.parse_report:
            ap.error("--parse-report is required for --phase validate")
        if not args.fixture_dir:
            ap.error("--fixture-dir is required for --phase validate")

    report = phase_parse(args) if args.phase == "parse" else phase_validate(args)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
