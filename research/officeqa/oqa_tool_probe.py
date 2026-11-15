"""Does the job-local vLLM server turn OfficeQA's function tools into `tool_calls`?

Why this exists. The OfficeQA offline rollout sends the adapter's three function schemas with
`tool_choice="auto"` and then reads `message.tool_calls`. vLLM 0.28.0 only populates that field when the server was
started with `--enable-auto-tool-choice --tool-call-parser <parser>`; otherwise the branch at
entrypoints/openai/chat_completion/serving.py (`not self.enable_auto_tools and not is_named_tool_choice and not
is_required_tool_choice`) builds the message from content alone. The failure is therefore SILENT at the HTTP level:
the model's tool syntax arrives as text, `tool_calls` is empty, and SkillOpt's loop records
"Model neither produced a tool request nor a final answer" and stops after one turn.

What this probe establishes, with one request through SkillOpt's own backend (so the result is about the real call
path, not a hand-written HTTP request):
  * whether `tool_calls` is populated, and with which tool and arguments;
  * if it is not, whether the content nevertheless contains tool syntax (which identifies the server flags, not the
    model, as the cause);
  * the prompt and completion token counts of one minimal OfficeQA-shaped request.

It writes a JSON report and exits non-zero when tool calling does not work, so the job fails loudly before any
question is spent on it. No answer key, no OfficeQA data and no optimizer call are involved.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from skillopt.envs.officeqa.rollout import _TOOL_SCHEMAS  # noqa: E402
from skillopt.model import chat_target_messages  # noqa: E402

# A minimal OfficeQA-shaped request: the system rules the adapter uses, and a question that can only be answered by
# calling a tool. No document is supplied, so the model has no alternative to a tool call.
SYSTEM = (
    "You are an expert OfficeQA agent working over local Treasury bulletin text files.\n\n"
    "## Rules\n"
    "1. Use only the provided local document tools to inspect candidate files.\n"
    "2. Do not invent values that are not grounded in the retrieved text.\n"
    "3. If you have enough evidence, return the final answer inside <answer>...</answer>.\n\n"
    "## Tool Use\n"
    "Use the provided function tools directly when you need them. Do not ask the user for permission to use "
    "tools; just call the tools."
)
USER = (
    "## Question\nWhat is the interest-bearing debt figure in the April 1977 bulletin?\n\n"
    "## Candidate Files\n- treasury_bulletin_1977_04.txt\n\n"
    "You have not read any document yet. Call a tool to find the figure."
)

_TOOL_SYNTAX = re.compile(r"<tool_call>|</tool_call>|\"name\"\s*:\s*\"(glob|read|grep)\"|\bfunctions\.(glob|read|grep)\b")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=512)
    args = ap.parse_args()

    report = {
        "probe": "officeqa_tool_calling",
        "base_url": os.environ.get("TARGET_QWEN_CHAT_BASE_URL", "(unset)"),
        "model": os.environ.get("TARGET_QWEN_CHAT_MODEL", "(unset)"),
        "tool_names": [schema["function"]["name"] for schema in _TOOL_SCHEMAS],
        "vllm_extra_args": os.environ.get("LSO_VLLM_EXTRA_ARGS", "(unset)"),
    }
    try:
        message, _ = chat_target_messages(
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
            max_completion_tokens=args.max_tokens,
            retries=2,
            stage="rollout",
            tools=_TOOL_SCHEMAS,
            tool_choice="auto",
            return_message=True,
        )
    except Exception as exc:  # noqa: BLE001
        report["verdict"] = "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"
        _write(args.out, report)
        print(json.dumps(report, indent=2))
        return 1

    content = message.content or ""
    calls = getattr(message, "tool_calls", None) or []
    report["tool_calls"] = [
        {"name": c.function.name, "arguments": c.function.arguments} for c in calls
    ]
    report["content_chars"] = len(content)
    report["content_head"] = content[:400]
    report["content_contains_tool_syntax"] = bool(_TOOL_SYNTAX.search(content))
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        report["finish_reason"] = metadata.get("finish_reason", "")
        report["usage"] = {k: v for k, v in metadata.items() if "token" in str(k)}

    if calls:
        known = {schema["function"]["name"] for schema in _TOOL_SCHEMAS}
        unknown = [c["name"] for c in report["tool_calls"] if c["name"] not in known]
        bad_args = []
        for call in report["tool_calls"]:
            try:
                json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                bad_args.append(call["name"])
        report["unknown_tools"] = unknown
        report["unparsable_arguments"] = bad_args
        report["verdict"] = "PASS" if not unknown and not bad_args else "PARTIAL"
    elif report["content_contains_tool_syntax"]:
        report["verdict"] = "FAIL"
        report["diagnosis"] = (
            "the model emitted tool syntax but the server returned it as content: the server is missing "
            "--enable-auto-tool-choice / --tool-call-parser, or the parser does not match this chat template. "
            "The OfficeQA offline loop would record 'Model neither produced a tool request nor a final answer'."
        )
    else:
        report["verdict"] = "FAIL"
        report["diagnosis"] = (
            "no tool_calls and no tool syntax in the content: the chat template may not render the tools, or the "
            "target declined to call one. Inspect content_head before changing the server flags."
        )

    _write(args.out, report)
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


def _write(path: str, report: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
