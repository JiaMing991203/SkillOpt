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

from skillopt.config import flatten_config, load_config  # noqa: E402
from skillopt.envs.officeqa.rollout import _TOOL_SCHEMAS  # noqa: E402
from skillopt.model import (  # noqa: E402
    chat_target_messages,
    configure_qwen_chat,
    get_target_backend,
    set_target_backend,
    set_target_deployment,
)

DEFAULT_CONFIG = os.path.join(REPO, "configs", "research", "officeqa_check3.yaml")


def configure_target_from_config(config_path: str) -> dict:
    """Select and configure the target backend exactly as SkillOpt's evaluation code does.

    Job 66171008 failed here. The first version of this probe called `chat_target_messages` without selecting a
    backend, so `TARGET_BACKEND` kept its module default and the request was routed to the Azure path, which is
    not configured in our jobs ("ValueError: Azure OpenAI endpoint is not configured for target"). The probe
    therefore never reached the vLLM server and said nothing about tool calling.

    scripts/eval_only.py does this inline in `main()` (set_target_backend at line 642, configure_qwen_chat at 667),
    so there is no function to import. This reads the SAME config file through SkillOpt's own loader and calls the
    SAME two functions with the SAME keys, so the values (model, temperature, max tokens, thinking mode) come from
    the config rather than from a copy kept in this file. Where a config value is absent the backend falls back to
    the TARGET_QWEN_CHAT_* environment variables that research/cluster/lib.sh exports for the job-local server -
    which is the mechanism research/preflight_endpoints.py relies on.
    """
    cfg = flatten_config(load_config(config_path))
    set_target_backend(cfg.get("target_backend", "openai_chat"))
    set_target_deployment(cfg.get("target_model") or cfg.get("target") or "")
    # One deliberate difference from eval_only.py, and the reason it matters: qwen_backend builds TARGET_CONFIG at
    # MODULE IMPORT time from the TARGET_QWEN_CHAT_* variables. eval_only.py passes only the config's values (ours
    # leaves the endpoint unset, because it is the job-local port chosen at run time) and so depends on the
    # environment already being exported before Python starts. That holds inside the job, where lib.sh exports it
    # in start_vllm, but it makes the endpoint silently import-order dependent. Passing it explicitly here removes
    # that dependency: the config wins when it sets a value, the job environment otherwise.
    configure_qwen_chat(
        target_base_url=(cfg.get("target_qwen_chat_base_url")
                         or os.environ.get("TARGET_QWEN_CHAT_BASE_URL") or None),
        target_api_key=(cfg.get("target_qwen_chat_api_key")
                        or os.environ.get("TARGET_QWEN_CHAT_API_KEY") or None),
        target_temperature=cfg.get("target_qwen_chat_temperature"),
        target_timeout_seconds=cfg.get("target_qwen_chat_timeout_seconds"),
        target_max_tokens=cfg.get("target_qwen_chat_max_tokens"),
        target_enable_thinking=cfg.get("target_qwen_chat_enable_thinking"),
        target_thinking_mode=cfg.get("target_qwen_chat_thinking_mode"),
    )
    return {
        "config": config_path,
        "configured_target_backend": get_target_backend(),
        "config_target_backend": cfg.get("target_backend"),
        "config_target_model": cfg.get("target_model") or cfg.get("target"),
    }


def assert_target_is_job_local_qwen(wiring: dict) -> str:
    """Fail before spending GPU time if the probe is not pointed at the job-local Qwen server.

    This is the guard whose absence made job 66171008 a wasted A40 reservation: the misrouting was only visible
    from the exception text after vLLM had already been started and the preflight had passed.
    """
    backend = wiring["configured_target_backend"]
    if backend != "qwen_chat":
        raise SystemExit(
            f"REFUSED: target backend is {backend!r}, not 'qwen_chat'. The probe would not reach the job-local "
            f"vLLM server. Check 'target_backend' in {wiring['config']}."
        )
    base_url = os.environ.get("TARGET_QWEN_CHAT_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit(
            "REFUSED: TARGET_QWEN_CHAT_BASE_URL is unset. research/cluster/lib.sh exports it after start_vllm; "
            "without it the backend has no endpoint to call."
        )
    host = base_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"REFUSED: the target endpoint {base_url!r} is not loopback. Every vLLM listener in this project must "
            f"stay on loopback, and a non-loopback endpoint means the probe is talking to something else."
        )
    return base_url

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
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="config whose target_* settings wire the backend (default: officeqa_check3.yaml)")
    args = ap.parse_args()

    wiring = configure_target_from_config(args.config)
    base_url = assert_target_is_job_local_qwen(wiring)

    report = {
        "probe": "officeqa_tool_calling",
        "base_url": base_url,
        "model": os.environ.get("TARGET_QWEN_CHAT_MODEL", "(unset)"),
        "tool_names": [schema["function"]["name"] for schema in _TOOL_SCHEMAS],
        "vllm_extra_args": os.environ.get("LSO_VLLM_EXTRA_ARGS", "(unset)"),
        "wiring": wiring,
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
        # "Usable arguments" is checked against the adapter's own schemas: the name must be one of the three tools
        # and every field that schema marks required must be present and non-empty. A call that parses as JSON but
        # omits `path` is not usable by run_tool, so it must not be scored as a pass.
        schemas = {s["function"]["name"]: s["function"] for s in _TOOL_SCHEMAS}
        unknown, bad_args, missing_required, usable = [], [], [], []
        for call in report["tool_calls"]:
            name = call["name"]
            if name not in schemas:
                unknown.append(name)
                continue
            try:
                parsed = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                bad_args.append(name)
                continue
            if not isinstance(parsed, dict):
                bad_args.append(name)
                continue
            required = schemas[name]["parameters"].get("required", [])
            absent = [k for k in required if not str(parsed.get(k, "")).strip()]
            if absent:
                missing_required.append({"tool": name, "absent": absent, "got": sorted(parsed)})
            else:
                usable.append({"tool": name, "arguments": parsed})
        report["unknown_tools"] = unknown
        report["unparsable_arguments"] = bad_args
        report["missing_required_arguments"] = missing_required
        report["usable_tool_calls"] = usable
        if usable and not unknown and not bad_args and not missing_required:
            report["verdict"] = "PASS"
        elif usable:
            report["verdict"] = "PARTIAL"
            report["diagnosis"] = ("at least one tool call is usable, but others are malformed; the loop would "
                                   "run, with some turns wasted on refused or ill-formed calls")
        else:
            report["verdict"] = "PARTIAL"
            report["diagnosis"] = ("tool calls were returned but none carries the arguments its schema requires, "
                                   "so run_tool could not act on any of them")
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
