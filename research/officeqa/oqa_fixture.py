"""The shared OfficeQA tool-execution fixture, and the check that a tool call actually works on it.

Both the offline parser replay (oqa_parser_replay.py) and the live probe (oqa_tool_probe.py) have to answer the
same question - does a structured call returned by the server actually execute through the OfficeQA adapter and
come back with the right line? - so the fixture and the check live here once instead of in two places that could
drift.

Nothing here needs a GPU, a model or a network: it writes one small text file and calls the adapter's own
`run_tool`, whose `read` and `grep` are plain file operations restricted to `allowed_roots` and `allowed_files`.
"""
from __future__ import annotations

import json
import os

FIXTURE_NAME = "treasury_bulletin_1977_04.txt"

# The probe prompt is FIXED (it is part of what the rerun holds constant), so the model asks for this file name
# and greps for "interest-bearing debt". The fixture therefore has to carry that name and be able to SATISFY the
# criterion with the model's own pattern.
#
# The first version of this fixture could not. It put the label only in column headers ("Total interest-bearing
# debt") with the figures in separate rows, so a grep for "interest-bearing debt" correctly returned header lines
# and no figure, and the check failed on the fixture rather than on the parser or the adapter. A stub-and-figure
# layout, which Treasury bulletins also use, puts the label and its value on one line, so a match carries data.
FIXTURE_LINES = [
    "TABLE IBD-1. - INTEREST-BEARING DEBT OUTSTANDING",
    "(In millions of dollars)",
    "",
    "Interest-bearing debt, April 1977 .................... 646,379",
    "Interest-bearing debt, March 1977 .................... 641,032",
    "Matured debt, April 1977 ............................. 1,142",
    "",
    "Note: figures are rounded and exclude guaranteed obligations.",
]

# A match must carry the April 1977 figure, and must NOT carry the matured-debt row - otherwise the observation
# would prove only that the file was read, not that the pattern filtered it.
EXPECTED_MATCH_SUBSTRING = "646,379"
EXPECTED_ABSENT_SUBSTRING = "Matured debt"

PASS_RESULT = "executed and returned the expected matching line, with non-matching rows filtered"


def write_fixture(fixture_dir: str) -> str:
    """Create the fixture file and return its path."""
    os.makedirs(fixture_dir, exist_ok=True)
    path = os.path.join(fixture_dir, FIXTURE_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(FIXTURE_LINES) + "\n")
    return path


def execute_and_check(name: str, raw_arguments: str, fixture_dir: str, required: dict) -> dict:
    """Validate one returned tool call against the adapter's schema, run it, and judge the observation.

    `required` maps tool name -> its schema's required field list, taken from the adapter itself by the caller, so
    this function never carries its own copy of the schemas.

    Returns an entry dict whose "result" equals PASS_RESULT only when the call executed through `run_tool` and the
    observation carries the expected figure while omitting the row the pattern should have filtered out.
    """
    from skillopt.envs.officeqa.tool_runtime import run_tool  # noqa: PLC0415  (adapter import, controller env)

    entry: dict = {"name": name, "raw_arguments": raw_arguments}
    if name not in required:
        entry["result"] = "unknown tool"
        return entry
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        entry["result"] = f"arguments are not JSON: {exc}"
        return entry
    if not isinstance(arguments, dict):
        entry["result"] = "arguments are not a JSON object"
        return entry

    entry["arguments"] = arguments
    absent = [k for k in required[name] if not str(arguments.get(k, "")).strip()]
    entry["missing_required"] = absent
    if absent:
        entry["result"] = "missing required arguments; run_tool could not act on it"
        return entry

    write_fixture(fixture_dir)

    # The probe prompt lists a bare file name, so the model returns a bare name. The real rollout puts absolute
    # candidate paths in the prompt; resolving the basename against the fixture root is the equivalent step, and it
    # is recorded so the report does not hide it.
    path_given = str(arguments.get("path") or "")
    exec_args = dict(arguments)
    if path_given and not os.path.isabs(path_given):
        exec_args["path"] = os.path.join(fixture_dir, os.path.basename(path_given))
        entry["path_resolution"] = f"bare name {path_given!r} resolved against the fixture root"

    cmd, obs = run_tool(name, exec_args, allowed_roots=[os.path.abspath(fixture_dir)],
                        allowed_files=[FIXTURE_NAME])
    entry["executed_cmd"] = cmd
    entry["observation"] = obs
    entry["expected_substring"] = EXPECTED_MATCH_SUBSTRING
    entry["expected_absent"] = EXPECTED_ABSENT_SUBSTRING

    if obs.startswith("[") and ("error" in obs or "no matches" in obs):
        entry["result"] = "the adapter refused or found nothing"
    elif EXPECTED_MATCH_SUBSTRING not in obs:
        entry["result"] = "executed but the expected figure is absent from the returned lines"
    elif EXPECTED_ABSENT_SUBSTRING in obs:
        entry["result"] = ("executed and returned the figure, but also a line the pattern should have filtered "
                           "out: the observation does not show the filter working")
    else:
        entry["result"] = PASS_RESULT
    return entry


def required_fields_from_adapter() -> dict:
    """tool name -> required fields, read from the adapter's own schemas (controller environment)."""
    from skillopt.envs.officeqa.rollout import _TOOL_SCHEMAS  # noqa: PLC0415
    return {s["function"]["name"]: s["function"]["parameters"].get("required", []) for s in _TOOL_SCHEMAS}
