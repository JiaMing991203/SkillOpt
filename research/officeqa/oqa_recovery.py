#!/usr/bin/env python3
"""Replay the answer-recovery policy over SAVED provider responses. Rule OQA-REC-2, amendment A-2.

WHY IT EXISTS. The policy must be tested against what the provider actually sent, not against what a
test author imagines it sends. Three sources of real bodies exist and this tool reads all three:

    1. the OQA-HTTP-1 probe's exchanges  (`EXCHANGES_*.json`, 23 complete HTTP bodies)
    2. the raw capture written by the backend during the new episodes (`*.jsonl`, schema
       `lso.raw_exchange.v1`)
    3. nothing else - the campaign's own 7,909 logged attempts do NOT contain the `reasoning` text,
       which is why the historical rate is unknowable and must be re-sampled rather than recomputed.

WHAT IT REPORTS, per exchange: what the OLD adapter did (content only), what the NARROW policy does, and
what `rollout.py`'s loop decides in each case. The difference is the point: "the old client would have
ended this episode with no answer, and the committed answer was in `reasoning`".

IT SENDS NOTHING. No network, no key, no cost.

usage:
  python3 -I research/officeqa/oqa_recovery.py replay --exchanges <dir|file> [...] --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

RULE = "OQA-REC-2"
SUPPORTED_TOOLS = ("glob", "read", "grep")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tree_on_path() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)


# ── a duck-typed stand-in for the SDK's message, built from a raw HTTP body ──────────────────────────
#
# The adapter reads a message through `getattr` and `model_extra`, exactly as the SDK exposes it, so a
# plain object with those attributes exercises the REAL adapter on the REAL body. Nothing is
# reimplemented here; if the adapter changes, this follows.

class _Fn:
    def __init__(self, d):
        self.name = (d or {}).get("name") or ""
        self.arguments = (d or {}).get("arguments") or "{}"


class _Tc:
    def __init__(self, d):
        self.id = (d or {}).get("id") or ""
        self.type = (d or {}).get("type") or "function"
        self.function = _Fn((d or {}).get("function"))


class Msg:
    """`choices[0].message` as the SDK would present it: modelled fields as attributes, rest in extra."""

    _MODELLED = ("role", "content", "tool_calls", "function_call", "refusal", "annotations", "audio",
                 "reasoning_content")

    def __init__(self, raw: dict):
        raw = raw or {}
        self.role = raw.get("role")
        self.content = raw.get("content")
        self.reasoning_content = raw.get("reasoning_content")
        self.tool_calls = [_Tc(t) for t in (raw.get("tool_calls") or [])]
        self.model_extra = {k: v for k, v in raw.items() if k not in self._MODELLED}

    def model_dump(self):
        out = {"role": self.role, "content": self.content,
               "reasoning_content": self.reasoning_content}
        out.update(self.model_extra)
        return out


class Usage:
    def __init__(self, raw: dict):
        self._raw = raw or {}
        for k, v in (raw or {}).items():
            setattr(self, k, v)

    def model_dump(self):
        return dict(self._raw)


class Resp:
    def __init__(self, body: dict):
        body = body or {}
        self.id = body.get("id")
        self.model = body.get("model")
        self.system_fingerprint = body.get("system_fingerprint")
        self.usage = Usage(body.get("usage") or {})


class Choice:
    def __init__(self, ch: dict):
        self.finish_reason = (ch or {}).get("finish_reason")
        self.message = Msg((ch or {}).get("message") or {})


def from_body(body: dict):
    ch = ((body or {}).get("choices") or [{}])[0]
    choice = Choice(ch)
    return choice.message, Resp(body), choice


# ── the loop's decision, quoted from rollout.py ──────────────────────────────────────────────────────

def loop_decision(compat) -> dict:
    """What the local-tools loop does with this compatibility message, by its own rules.

    From the source: `response = message.content or ""`; a tool call CONTINUES and this turn's
    `<answer>` is never looked at; a literal `<answer>` breaks and is extracted; otherwise the episode
    ends with "Model neither produced a tool request nor a final answer".
    """
    _tree_on_path()
    from skillopt.envs.officeqa.rollout import _extract_answer
    response = compat.content or ""
    if compat.tool_calls:
        names = [tc.function.name for tc in compat.tool_calls]
        return {"branch": "tool_calls -> continue", "episode_ends": False,
                "tools_requested": names,
                "unsupported_tools": [n for n in names if n not in SUPPORTED_TOOLS],
                "answer_tag_in_this_turn_ignored": "<answer>" in response.lower()}
    if "<answer>" in response.lower():
        return {"branch": "<answer> -> extract and break", "episode_ends": True,
                "extracted_answer": _extract_answer(response)[:200]}
    return {"branch": "neither -> break", "episode_ends": True,
            "fail_reason": "Model neither produced a tool request nor a final answer",
            "content_is_empty": not response.strip()}


# ── the comparison ──────────────────────────────────────────────────────────────────────────────────

def compare_one(body: dict, label: str = "", source: str = "") -> dict:
    """One saved body under the OLD adapter and under the narrow policy, side by side."""
    _tree_on_path()
    from skillopt.model import common as C
    message, resp, choice = from_body(body)
    env = "SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK"
    keep = os.environ.get(env)
    try:
        os.environ.pop(env, None)                      # the OLD client: no recovery at all
        old = C.compat_message_from_chat_message(message, resp, choice)
        old_dec = loop_decision(old)
        os.environ[env] = "committed_answer"           # the NARROW policy
        new = C.compat_message_from_chat_message(message, resp, choice)
        new_dec = loop_decision(new)
    finally:
        os.environ.pop(env, None)
        if keep is not None:
            os.environ[env] = keep
    rec = (new.metadata or {}).get("answer_recovery") or {}
    rtext, rfield = C.reasoning_text(message)
    differs = (old.content or "") != (new.content or "")
    return {
        "label": label, "source": source,
        "response_id": resp.id, "finish_reason": choice.finish_reason,
        "provider": {
            "content_is_null": message.content is None,
            "content_chars": len(str(message.content or "")),
            "n_tool_calls": len(message.tool_calls),
            "alternate_field": rfield or None,
            "alternate_chars": len(rtext),
            "alternate_has_answer_tag": "<answer>" in rtext.lower(),
            "completion_tokens": getattr(resp.usage, "completion_tokens", None),
            "provider_cost_usd": getattr(resp.usage, "cost_usd", None),
        },
        "old_adapter": {"content_chars": len(old.content or ""), "decision": old_dec},
        "narrow_policy": {"content": (new.content or "")[:400],
                          "content_chars": len(new.content or ""),
                          "recovery": rec, "decision": new_dec},
        "decision_differs": differs,
        # The honest limit on what a difference means, written into every record so it cannot be
        # dropped in the retelling.
        "caveat": ("this is ONE exchange re-decided offline; it does not reconstruct what the rest of "
                   "an episode would have done after a different decision at this turn"),
    }


def _bodies(paths: list) -> list:
    """(label, source, body) for every saved exchange in the given files or directories."""
    out = []
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "EXCHANGES_*.json")))
            files += sorted(glob.glob(os.path.join(p, "*.jsonl")))
        else:
            files.append(p)
    for path in files:
        base = os.path.basename(path)
        if path.endswith(".jsonl"):
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("schema") != "lso.raw_exchange.v1":
                        continue
                    body = (rec.get("http") or {}).get("body")
                    if isinstance(body, dict):
                        out.append((f"{rec.get('stage') or 'turn'}#{n}", base, body))
            continue
        doc = json.load(open(path, encoding="utf-8"))
        for r in doc.get("records") or []:
            body = (r.get("http") or {}).get("body")
            if isinstance(body, dict):
                out.append((f"{r.get('label')}/rep{r.get('rep')}/{r.get('setting')}", base, body))
    return out


def cmd_replay(paths: list, out_dir: str) -> dict:
    rows = [compare_one(b, label, src) for label, src, b in _bodies(paths)]
    counts: dict = {}
    for r in rows:
        key = r["narrow_policy"]["recovery"].get("outcome") or "none"
        counts[key] = counts.get(key, 0) + 1
    doc = {"rule": RULE, "utc": _utc(), "n_exchanges": len(rows),
           "recovery_outcomes": counts,
           "decisions_that_differ": sum(1 for r in rows if r["decision_differs"]),
           "answers_the_old_client_discarded": sum(
               1 for r in rows
               if r["old_adapter"]["decision"].get("branch", "").startswith("neither")
               and r["narrow_policy"]["decision"].get("branch", "").startswith("<answer>")),
           "exchanges": rows}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "RECOVERY_REPLAY.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("--exchanges", action="append", required=True)
    r.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = cmd_replay(args.exchanges, args.out)
    print(f"rule {RULE}  {doc['n_exchanges']} saved exchange(s); "
          f"{doc['decisions_that_differ']} client decision(s) differ; "
          f"{doc['answers_the_old_client_discarded']} committed answer(s) the old client discarded")
    for k, v in sorted(doc["recovery_outcomes"].items()):
        print(f"  {v:4d}  {k}")
    for e in doc["exchanges"]:
        if e["decision_differs"]:
            print(f"  {e['label']:28s} {e['provider']['alternate_field']}:"
                  f"{e['provider']['alternate_chars']}ch -> "
                  f"{e['narrow_policy']['recovery'].get('outcome')} "
                  f"{e['narrow_policy']['content'][:60]!r}")
    print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
