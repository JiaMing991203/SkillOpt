#!/usr/bin/env python3
"""Assert the EFFECTIVE actor settings against the transmitted requests. Rule OQA-BASE-1, stage s0.

WHY THIS IS A SEPARATE CHECK FROM THE PREFLIGHT. The preflight asserts the configuration objects the paid
stages will build. That is necessary and not sufficient: between a config field and an HTTP body sit an
adapter, a router and a backend, and this project has already been burnt by each of them -

  * a config with a perfect openai-compatible block dispatched to Azure, raised, had the exception
    swallowed, and recorded `no_content` (job 66271190);
  * `env.max_completion_tokens: 16384` looks exactly like the output cap and is not it - the transmitted cap
    comes from `model.target_openai_compatible_max_tokens`, and the two have differed by 2x all along;
  * the answers were present in a provider field the client never read, so 20 of 24 episodes scored zero
    while the configuration was entirely correct.

So the only trustworthy statement about what was sent is one read back out of the requests that were sent.
This reads the call log and fails the job if the frozen condition is not what went over the wire.

WHAT IS CHECKED, over EVERY logged request:
  model, max_tokens, temperature, and extra_body {"enable_thinking": false}   all must be constant and equal
                                                                             to the frozen values
  the alternate provider reasoning field                                      must be EMPTY in every
                                                                             response; a non-empty one means
                                                                             thinking was not actually off,
                                                                             whatever was transmitted
  recoveries                                                                  counted and reported, not
                                                                             failed - the narrow policy is
                                                                             allowed to fire

The second check is the one that distinguishes "transmitted" from "effective". `enable_thinking: false` can
be sent and ignored by a provider; the evidence that it took effect is that no response carried alternate
reasoning text.

usage: python3 -I research/officeqa/oqa_base_verify.py --call-log <jsonl> --out <json>
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os

RULE = "OQA-BASE-1"
SCHEMA = "lso.officeqa_outbound_verify.v1"

REQUIRED = {
    "model": "qwen3-5-4b",
    "max_tokens": 8000,
    "temperature": 0.7,
}
REQUIRED_EXTRA_BODY = {"enable_thinking": False}
# Provider reasoning fields differ by provider, which is why several names are checked rather than one.
ALT_FIELDS = ("reasoning", "reasoning_content", "thinking", "thought")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _alt_chars(response) -> int:
    """Characters of alternate-field reasoning anywhere in a response, by any of the known field names."""
    total = 0
    stack = [response]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ALT_FIELDS and isinstance(value, str):
                    total += len(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return total


def verify(call_log: str) -> dict:
    seen: dict = {k: collections.Counter() for k in REQUIRED}
    extra = collections.Counter()
    n = 0
    alt_total = 0
    alt_calls = 0
    recoveries = collections.Counter()
    outcomes = collections.Counter()
    malformed = 0

    with open(call_log, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            n += 1
            req = row.get("request") or {}
            for key in REQUIRED:
                seen[key][json.dumps(req.get(key))] += 1
            extra[json.dumps(req.get("extra_body"), sort_keys=True)] += 1
            chars = _alt_chars(row.get("response"))
            if chars:
                alt_calls += 1
                alt_total += chars
            rec = row.get("recovery")
            recoveries[json.dumps(rec) if not isinstance(rec, str) else rec] += 1
            outcomes[str(row.get("outcome"))] += 1

    checks: list = []

    def _add(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "verdict": "PASS" if ok else "FAIL", "detail": detail})

    _add("call_log/non_empty", n > 0, {"n_calls": n, "path": call_log})
    _add("call_log/no_malformed_lines", malformed == 0, {"malformed": malformed})
    for key, want in REQUIRED.items():
        counter = seen[key]
        only = list(counter)
        ok = len(only) == 1 and json.loads(only[0]) == want
        _add(f"transmitted/{key}", ok,
             {"expected": want, "distinct_values_transmitted": dict(counter)})
    only_extra = list(extra)
    ok_extra = len(only_extra) == 1 and json.loads(only_extra[0]) == REQUIRED_EXTRA_BODY
    _add("transmitted/extra_body", ok_extra,
         {"expected": REQUIRED_EXTRA_BODY, "distinct_values_transmitted": dict(extra)})
    # THE EFFECTIVENESS CHECK, which is not the same as the transmission check.
    _add("effective/thinking_actually_off", alt_calls == 0,
         {"calls_with_alternate_reasoning": alt_calls, "alternate_reasoning_chars": alt_total,
          "fields_searched": list(ALT_FIELDS),
          "why": "enable_thinking:false can be sent and ignored; an empty alternate field in every "
                 "response is the evidence that it took effect"})

    failed = [c for c in checks if c["verdict"] == "FAIL"]
    return {
        "schema": SCHEMA, "rule": RULE, "utc": _utc(), "call_log": os.path.abspath(call_log),
        "n_calls": n,
        "transmitted": {k: dict(v) for k, v in seen.items()},
        "transmitted_extra_body": dict(extra),
        "alternate_reasoning": {"calls": alt_calls, "chars": alt_total},
        "recoveries": dict(recoveries),
        "outcomes": dict(outcomes),
        "checks": checks,
        "n_fail": len(failed),
        "verdict": "FROZEN_CONDITION_HELD" if not failed else "CONDITION_VIOLATED",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--call-log", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not os.path.isfile(a.call_log):
        doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(), "verdict": "CONDITION_VIOLATED",
               "n_fail": 1, "checks": [{"check": "call_log/exists", "verdict": "FAIL",
                                        "detail": {"path": a.call_log}}]}
    else:
        doc = verify(a.call_log)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({"out": a.out, "verdict": doc["verdict"], "n_calls": doc.get("n_calls"),
                      "n_fail": doc["n_fail"],
                      "failed": [c["check"] for c in doc["checks"] if c["verdict"] == "FAIL"],
                      "alternate_reasoning": doc.get("alternate_reasoning")}, indent=1, default=str))
    return 1 if doc["n_fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
