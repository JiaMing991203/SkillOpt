#!/usr/bin/env python3
"""Look at the ACTUAL HTTP response for the empty actor replies. Rule OQA-HTTP-1.

WHY. OQA-FOR-1 established that 331 of 528 saved selection observations ended on one provider reply with
finish_reason "stop", no `content`, no `reasoning_content`, and a median of 920 completion tokens
generated and billed. It could not say WHY, because nothing in the saved records distinguishes a model
that emitted an end-of-turn token with nothing before it from a server-side template that consumed the
output, and the client's own logger records only the fields it models.

WHAT THIS DOES. For the SAME HTTP exchange it captures five layers:
    1. the exact serialized request, including the tool schemas and every serving control actually sent;
    2. the complete HTTP response body, as bytes, before the SDK parses it;
    3. the SDK's parsed representation;
    4. the compatibility message the environment would receive;
    5. the decision `rollout.py`'s loop would take from it.
`client.chat.completions.with_raw_response` gives (2) and (3) from one exchange, which is the point: a
separate raw call would be a different exchange and could not settle a conversion question.

TWO PHASES, AND THE SELECTION IS FROZEN BEFORE ANYTHING IS SENT.
    select   reads the saved call logs and the saved TRAINING results, picks the contexts by the
             mechanical rule below, and writes SELECTION.json. No network.
    send     refuses without that file, replays each selected context, and writes one record per
             exchange. Every send is counted against a hard in-process cap.

THE SELECTION RULE, recorded so it can be re-run:
    * eligible calls come only from the campaign's TRAINING rollouts. A call is eligible when the
      sha256 of its first user message appears in a TRAINING `results.jsonl`'s `target_user_prompt`.
      Held-out and validation material is excluded by construction, not by intention.
    * only a TERMINAL call counts - the last call of its episode, where an episode is the chain of calls
      sharing (system digest, user digest) and a two-message request starts a new one.
    * EMPTY_1, EMPTY_2: the two terminal calls with finish_reason "stop" and null content, ordered by
      completion_tokens DESCENDING, ties broken by (job, user digest, started_utc) ascending.
    * ANSWERED_1: the terminal call with finish_reason "stop" whose content contains "<answer>", ordered
      by prompt_tokens ASCENDING (the cheapest to replay), same tie-break.

WHAT IS RECONSTRUCTED, AND SAID TO BE. The historical log recorded `tools` as a BOOLEAN, not as the
schemas, and did not record `tool_choice`. Both are taken from the pinned source
(`skillopt/envs/officeqa/rollout.py::_TOOL_SCHEMAS` and the loop's `tool_choice="auto"`), and the record
marks them `reconstructed`. Everything else - model, max_tokens, temperature, timeout, messages - is
replayed from the log, and a setting the log does not show is left OMITTED, never defaulted.

ACCOUNTING (amendment A-2). Every send is authorised by `spend_ledger.reserve` before it leaves and
recorded by `record_usage` after it returns, so a raw-response probe is accounted by the same machinery
as a backend call. The first 23 sends of this probe predate that and were back-filled by
`oqa_ledger_backfill.py`, which refuses to run twice.

CREDENTIALS. No artifact written here contains a key, an Authorization header or any other
authentication material: the request record is built from the serialized BODY only, and the response
headers are filtered to a recorded allow-list.

usage:
  python3 -I research/officeqa/oqa_http_probe.py select --logs <root> --campaign <root> --out <dir>
  python3 -I research/officeqa/oqa_http_probe.py send   --selection <dir>/SELECTION.json --out <dir> \
      --setting historical --reps 3 --max-sends 24
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys

RULE = "OQA-HTTP-1"
# Response headers worth keeping. Everything else is dropped, so no authentication material and no
# per-account identifier can reach an artifact.
HEADER_ALLOW = ("content-type", "content-length", "x-request-id", "openai-version",
                "openai-processing-ms", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
                "retry-after", "server", "date")


def sha(x) -> str:
    if isinstance(x, str):
        x = x.encode("utf-8")
    return hashlib.sha256(x).hexdigest()


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ───────────────────────── phase 1: select ─────────────────────────

def training_user_prompts(campaign: str) -> dict:
    """sha256(user prompt) -> (item id, which training collection it came from).

    Only `steps/step_*/rollout` and `slow_update/*/rollout_{prev,curr}` - the TRAINING side. The
    selection evaluations and anything held-out are not here, so an eligible call cannot come from them.
    """
    out = {}
    pats = [os.path.join(campaign, "arm_*", "steps", "step_*", "rollout", "results.jsonl"),
            os.path.join(campaign, "arm_*", "slow_update", "epoch_*", "rollout_prev", "results.jsonl"),
            os.path.join(campaign, "arm_*", "slow_update", "epoch_*", "rollout_curr", "results.jsonl"),
            os.path.join(campaign, "shared", "epoch1", "results.jsonl")]
    for pat in pats:
        for path in sorted(glob.glob(pat)):
            for row in read_jsonl(path):
                up = str(row.get("target_user_prompt") or "")
                if up:
                    out.setdefault(sha(up), (str(row.get("id")), os.path.relpath(path, campaign)))
    return out


def terminal_calls(log_paths: list, allowed: dict) -> list:
    """The last call of every episode whose first user message is a saved TRAINING prompt."""
    by_key = {}
    for path in log_paths:
        job = os.path.basename(os.path.dirname(path))
        for rec in read_jsonl(path):
            msgs = (rec.get("request") or {}).get("messages") or []
            if len(msgs) < 2:
                continue
            ud = sha(str(msgs[1].get("content") or ""))
            if ud not in allowed:
                continue
            key = (job, sha(str(msgs[0].get("content") or "")), ud)
            by_key.setdefault(key, []).append((rec, path))
    out = []
    for (job, sd, ud), recs in by_key.items():
        recs.sort(key=lambda rp: (str(rp[0].get("started_utc")),
                                  len((rp[0].get("request") or {}).get("messages") or [])))
        episodes, cur = [], []
        for rp in recs:
            if len((rp[0].get("request") or {}).get("messages") or []) <= 2 and cur:
                episodes.append(cur)
                cur = [rp]
            else:
                cur.append(rp)
        if cur:
            episodes.append(cur)
        for ep in episodes:
            rec, path = ep[-1]
            resp = rec.get("response") or {}
            u = resp.get("usage") or {}
            item, coll = allowed[ud]
            out.append({
                "job": job, "log": path, "item_id": item, "training_collection": coll,
                "system_digest": sd, "user_digest": ud,
                "started_utc": rec.get("started_utc"),
                "n_messages": len((rec.get("request") or {}).get("messages") or []),
                "episode_calls": len(ep),
                "finish_reason": resp.get("finish_reason"),
                "content_is_null": resp.get("content") is None,
                "content_has_answer_tag": ("<answer>" in str(resp.get("content") or "").lower()),
                "content_chars": len(str(resp.get("content") or "")) if resp.get("content") else 0,
                "completion_tokens": (u.get("completion_tokens") if isinstance(u, dict) else None),
                "prompt_tokens": (u.get("prompt_tokens") if isinstance(u, dict) else None),
                "response_id": resp.get("id"),
                "request": rec.get("request") or {},
            })
    return out


def cmd_select(log_roots: list, campaign: str) -> dict:
    paths, skipped = [], []
    for root in log_roots:
        for p in sorted(glob.glob(os.path.join(root, "**", "actor_calls.jsonl"), recursive=True)):
            (skipped if ("heldout" in p or "held_out" in p) else paths).append(p)
    allowed = training_user_prompts(campaign)
    cands = terminal_calls(paths, allowed)

    empties = sorted((c for c in cands
                      if str(c["finish_reason"]) == "stop" and c["content_is_null"]),
                     key=lambda c: (-(c["completion_tokens"] or 0), c["job"], c["user_digest"],
                                    str(c["started_utc"])))
    answered = sorted((c for c in cands
                       if str(c["finish_reason"]) == "stop" and c["content_has_answer_tag"]),
                      key=lambda c: ((c["prompt_tokens"] or 0), c["job"], c["user_digest"],
                                     str(c["started_utc"])))
    chosen = []
    for label, pool, i in (("EMPTY_1", empties, 0), ("EMPTY_2", empties, 1), ("ANSWERED_1", answered, 0)):
        if len(pool) > i:
            c = dict(pool[i])
            c["label"] = label
            chosen.append(c)
    doc = {
        "rule": RULE, "utc": _utc(), "campaign": campaign,
        "logs_read": paths, "logs_skipped_heldout": skipped,
        "n_training_prompts_indexed": len(allowed),
        "n_terminal_training_calls": len(cands),
        "n_empty_terminal": len(empties), "n_answered_terminal": len(answered),
        "selection_rule": (
            "eligible = a terminal call of a TRAINING episode (its first user message's sha256 appears "
            "in a training results.jsonl). EMPTY_1/EMPTY_2 = finish_reason stop and null content, by "
            "completion_tokens descending. ANSWERED_1 = finish_reason stop with an <answer> tag, by "
            "prompt_tokens ascending. Ties: (job, user_digest, started_utc) ascending."),
        "selected": chosen,
    }
    doc["selection_sha256"] = sha(json.dumps(
        [{k: c[k] for k in ("label", "job", "item_id", "system_digest", "user_digest", "started_utc",
                            "finish_reason", "completion_tokens", "prompt_tokens", "response_id")}
         for c in chosen], sort_keys=True))
    return doc


# ───────────────────────── phase 2: send ─────────────────────────

def tool_schemas():
    """The tool schemas the historical loop sent. RECONSTRUCTED from the pinned source, not from the log.

    The call log recorded `tools` as a boolean, so the schemas cannot be replayed from it. They come from
    `skillopt/envs/officeqa/rollout.py::_TOOL_SCHEMAS` at this commit, and the record says so.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.envs.officeqa.rollout import _TOOL_SCHEMAS
    return [json.loads(json.dumps(t)) for t in _TOOL_SCHEMAS]


def build_kwargs(ctx: dict, setting: str) -> tuple:
    """The request to send, and a note on every field's provenance.

    A setting the historical log does not show is OMITTED, never defaulted: that is what makes the first
    replay a replay. `setting` adds nothing except under "no_thinking", which adds exactly one documented
    serving control and nothing else.
    """
    req = ctx["request"]
    kwargs = {"model": req.get("model"), "messages": req.get("messages"),
              "max_tokens": req.get("max_tokens")}
    prov = {"model": "replayed", "messages": "replayed", "max_tokens": "replayed"}
    if req.get("temperature") is not None:
        kwargs["temperature"] = req["temperature"]
        prov["temperature"] = "replayed"
    if req.get("timeout") is not None:
        kwargs["timeout"] = req["timeout"]
        prov["timeout"] = "replayed"
    if req.get("tools"):
        kwargs["tools"] = tool_schemas()
        kwargs["tool_choice"] = "auto"
        prov["tools"] = "reconstructed from the pinned source (the log stored a boolean)"
        prov["tool_choice"] = "reconstructed from the loop (the log did not store it)"
    extra = None
    if setting == "no_thinking":
        # THE DOCUMENTED CONTROL FOR THIS MODEL, not the vLLM/SGLang extension.
        # docs.empiriolabs.ai/models/qwen3-5-4b lists, for qwen3-5-4b: `enable_thinking` boolean,
        # DEFAULT TRUE, "Enables reasoning before answering"; `thinking_budget` integer, default 4096,
        # "Max reasoning tokens when thinking is on"; `reasoning_effort` enum, default "medium", where
        # `none` disables thinking. It also warns that "a very low max_tokens can be spent entirely on
        # reasoning". The campaign omitted all three, so it ran with thinking ON at a 4,096-token
        # reasoning budget inside an 8,000-token max_tokens.
        #
        # `skillopt/model/qwen_backend.py` uses `chat_template_kwargs: {"enable_thinking": ...}`, which
        # is the vLLM/SGLang extension and is NOT what this provider documents. Sending the documented
        # field is the point: a flag must be verified on the wire, not assumed.
        extra = {"enable_thinking": False}
        prov["enable_thinking"] = ("ADDED for this setting only; documented for qwen3-5-4b at "
                                   "docs.empiriolabs.ai/models/qwen3-5-4b, default true")
    return kwargs, prov, extra


def loop_decision(compat) -> dict:
    """What `rollout.py`'s local-tools loop would do with this reply, by its own rules.

    Quoted from the source: `response = message.content or ""`; a tool call CONTINUES (and its
    `<answer>` is never looked at); a literal `<answer>` breaks and is extracted; otherwise the episode
    ends with "Model neither produced a tool request nor a final answer".
    """
    response = compat.content or ""
    if compat.tool_calls:
        return {"branch": "tool_calls -> continue",
                "tools_requested": [tc.function.name for tc in compat.tool_calls],
                "answer_tag_in_this_turn_ignored": "<answer>" in response.lower(),
                "episode_ends": False, "fail_reason": ""}
    if "<answer>" in response.lower():
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if root not in sys.path:
            sys.path.insert(0, root)
        from skillopt.envs.officeqa.rollout import _extract_answer
        return {"branch": "<answer> -> extract and break", "episode_ends": True,
                "extracted_answer": _extract_answer(response)[:200], "fail_reason": ""}
    return {"branch": "neither -> break", "episode_ends": True,
            "fail_reason": "Model neither produced a tool request nor a final answer",
            "content_is_empty": not response.strip()}


def cmd_send(selection_path: str, out_dir: str, setting: str, reps: int, max_sends: int,
             labels: list) -> dict:
    sel = json.load(open(selection_path, encoding="utf-8"))
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from openai import OpenAI
    from skillopt.model import spend_ledger as LEDGER
    from skillopt.model.common import compat_message_from_chat_message, response_metadata

    base_url = os.environ.get("TARGET_OPENAI_COMPATIBLE_BASE_URL") or \
        os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or ""
    key = os.environ.get("TARGET_OPENAI_COMPATIBLE_API_KEY") or \
        os.environ.get("OPENAI_COMPATIBLE_API_KEY") or ""
    if not base_url or not key:
        raise RuntimeError("the probe needs TARGET_OPENAI_COMPATIBLE_BASE_URL and _API_KEY in the "
                           "environment; neither is ever written to an artifact")
    client = OpenAI(base_url=base_url, api_key=key)

    wanted = [c for c in sel["selected"] if not labels or c["label"] in labels]
    records, sends = [], 0
    for ctx in wanted:
        for rep in range(1, reps + 1):
            if sends >= max_sends:
                records.append({"label": ctx["label"], "rep": rep, "skipped": "send cap reached"})
                break
            kwargs, prov, extra = build_kwargs(ctx, setting)
            body = dict(kwargs)
            if extra:
                body.update(extra)
            rec = {
                "rule": RULE, "utc": _utc(), "label": ctx["label"], "rep": rep, "setting": setting,
                "historical": {k: ctx[k] for k in
                               ("job", "item_id", "training_collection", "finish_reason",
                                "content_is_null", "completion_tokens", "prompt_tokens",
                                "response_id", "episode_calls", "n_messages")},
                "request": {
                    "provenance": prov,
                    "serialized_sha256": sha(json.dumps(body, sort_keys=True, default=str)),
                    "model": body.get("model"), "max_tokens": body.get("max_tokens"),
                    "temperature": body.get("temperature"), "timeout": body.get("timeout"),
                    "n_messages": len(body.get("messages") or []),
                    "message_roles": [m.get("role") for m in (body.get("messages") or [])],
                    "tools": [t.get("function", {}).get("name") for t in (body.get("tools") or [])],
                    "tool_choice": body.get("tool_choice"),
                    "extra_body": extra,
                    # Omitted because the historical request omitted them. Each has a documented
                    # default for this model which therefore WAS in force:
                    #   enable_thinking true, thinking_budget 4096, reasoning_effort "medium",
                    #   top_p 0.95, top_k 20, seed unset.
                    "omitted_on_purpose": ["seed", "top_p", "top_k", "stop", "stream",
                                           "response_format", "max_completion_tokens",
                                           "enable_thinking", "thinking_budget", "reasoning_effort"],
                    "documented_defaults_in_force_when_omitted": {
                        "enable_thinking": True, "thinking_budget": 4096,
                        "reasoning_effort": "medium", "top_p": 0.95, "top_k": 20,
                        "source": "docs.empiriolabs.ai/models/qwen3-5-4b, read 2026-11-18"},
                },
            }
            try:
                # THROUGH THE CENTRAL ACCOUNTING MECHANISM. The probe talks to the SDK directly, which
                # bypasses the backend where the ledger lives - and that is why its first 23 sends had
                # to be accounted by hand and later back-filled (rule OQA-REC-2). `reserve` authorises
                # one attempt against the shared, durable ledger and raises if there is no room; with
                # SKILLOPT_SPEND_LEDGER_DIR unset it is a no-op, exactly as in the backend.
                LEDGER.reserve("target", "oqa_http_probe")
                raw = client.chat.completions.with_raw_response.create(**kwargs,
                                                                       extra_body=extra or None)
                sends += 1
                http = raw.http_response
                text = http.text
                rec["http"] = {
                    "status_code": http.status_code,
                    "headers": {k.lower(): v for k, v in dict(http.headers).items()
                                if k.lower() in HEADER_ALLOW},
                    "body_sha256": sha(text), "body_bytes": len(text.encode("utf-8")),
                    "body": json.loads(text) if text.strip().startswith("{") else text[:20000],
                }
                parsed = raw.parse()
                choice = (getattr(parsed, "choices", None) or [None])[0]
                msg = getattr(choice, "message", None)
                _u = getattr(parsed, "usage", None)
                if _u is not None:
                    _d = (_u.model_dump() if hasattr(_u, "model_dump") else {}) or {}
                    LEDGER.record_usage(
                        "target", "oqa_http_probe",
                        int(_d.get("prompt_tokens") or 0), int(_d.get("completion_tokens") or 0),
                        cached_tokens=int((_d.get("prompt_tokens_details") or {}).get("cached_tokens")
                                          or 0),
                        provider_cost_usd=_d.get("cost_usd"))
                rec["sdk"] = {
                    "id": getattr(parsed, "id", None),
                    "model": getattr(parsed, "model", None),
                    "finish_reason": getattr(choice, "finish_reason", None),
                    "message_attrs": sorted(k for k in dir(msg) if not k.startswith("_"))[:40],
                    "content_is_none": getattr(msg, "content", None) is None,
                    "content_chars": len(str(getattr(msg, "content", "") or "")),
                    "content_prefix": str(getattr(msg, "content", "") or "")[:400],
                    "reasoning_content_is_none": getattr(msg, "reasoning_content", None) is None,
                    "model_extra_keys": sorted((getattr(msg, "model_extra", None) or {}).keys()),
                    "n_tool_calls": len(getattr(msg, "tool_calls", None) or []),
                }
                compat = compat_message_from_chat_message(msg, parsed, choice)
                rec["compat_message"] = {
                    "content_chars": len(compat.content), "content_prefix": compat.content[:400],
                    "tool_names": [tc.function.name for tc in compat.tool_calls],
                    "metadata": response_metadata(msg, parsed, choice),
                }
                rec["loop_decision"] = loop_decision(compat)
                rec["outcome"] = _outcome(rec)
            except Exception as e:  # noqa: BLE001
                sends += 1   # an attempt that reached the provider counts whatever it returned
                rec["error"] = {"type": type(e).__name__, "message": str(e)[:1500]}
                rec["outcome"] = "request_failed"
            records.append(rec)
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, f"EXCHANGES_{setting}_{stamp}.json")
    doc = {"rule": RULE, "utc": _utc(), "setting": setting, "reps": reps,
           "selection_sha256": sel.get("selection_sha256"), "sends": sends,
           "max_sends": max_sends, "records": records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def _outcome(rec: dict) -> str:
    """Which of the four distinguishable outcomes this exchange is.

    The order matters. "content in the body lost in client handling" is tested FIRST and against the
    message's text WHEREVER it is, because that is the finding: on 2026-11-18 the text was in
    `message.reasoning` with `content` null, and an outcome function that looked only at `content` would
    have called the same exchange "the response carries no usable answer".
    """
    http_body = (rec.get("http") or {}).get("body")
    sdk = rec.get("sdk") or {}
    compat = rec.get("compat_message") or {}
    msg = {}
    if isinstance(http_body, dict):
        msg = (((http_body.get("choices") or [{}])[0]) or {}).get("message") or {}
    http_text = str(msg.get("content") or "")
    http_other = {k: v for k, v in msg.items()
                  if k not in ("role", "content", "tool_calls") and v not in (None, "", [], {})}
    # the model's text, wherever the provider put it
    alt_text = ""
    alt_field = ""
    for name in ("reasoning", "reasoning_content", "thinking"):
        v = msg.get(name)
        if isinstance(v, str) and v.strip():
            alt_text, alt_field = v, name
            break
    any_text = http_text if http_text.strip() else alt_text
    tooled = bool(msg.get("tool_calls") or compat.get("tool_names"))
    if alt_text.strip() and compat.get("content_chars", 0) > 0:
        return f"content_recovered_from_{alt_field}_by_the_fallback"
    if tooled:
        # A turn that requested a tool is not a lost answer: the loop continues on the tool call. The
        # reasoning text going unread still matters, and is named, but it cost nothing here.
        return ("tool_call_returned" if not alt_text.strip()
                else f"tool_call_returned_with_unread_{alt_field}")
    if any_text.strip() and compat.get("content_chars", 0) == 0:
        return ("content_in_the_http_body_lost_in_client_handling"
                + (f" (field {alt_field})" if alt_field and not http_text.strip() else ""))
    if not http_text.strip() and not msg.get("tool_calls"):
        if http_other:
            return "no_usable_content_but_an_alternate_field_is_present"
        return "http_response_itself_carries_no_usable_answer_or_tool_call"
    return "usable_content_returned"


def cmd_rederive(out_dir: str) -> dict:
    """Recompute every saved exchange's outcome with the current taxonomy. Sends nothing.

    The first taxonomy tested only `content`, so a tool-calling turn whose reasoning text went unread
    was labelled "content lost in client handling" - alarming and wrong, because the loop continues on
    the tool call. Re-deriving from the saved bodies is free, and it keeps the artifacts and the
    taxonomy in step without another request.
    """
    changed, files = [], sorted(glob.glob(os.path.join(out_dir, "EXCHANGES_*.json")))
    for path in files:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        for r in doc.get("records") or []:
            if not r.get("http"):
                continue
            was = r.get("outcome")
            now = _outcome(r)
            if now != was:
                changed.append({"file": os.path.basename(path), "label": r.get("label"),
                                "rep": r.get("rep"), "was": was, "now": now})
                r["outcome"] = now
                r["outcome_rederived_utc"] = _utc()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True, default=str)
    return {"rule": RULE, "utc": _utc(), "files": [os.path.basename(p) for p in files],
            "changed": changed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--logs", action="append", required=True)
    s.add_argument("--campaign", required=True)
    s.add_argument("--out", required=True)
    d = sub.add_parser("send")
    d.add_argument("--selection", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--setting", default="historical", choices=("historical", "no_thinking"))
    d.add_argument("--reps", type=int, default=3)
    d.add_argument("--max-sends", type=int, required=True)
    d.add_argument("--label", action="append", default=[])
    rd = sub.add_parser("rederive")
    rd.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.cmd == "rederive":
        doc = cmd_rederive(args.out)
        print(f"rule {RULE}  {len(doc['files'])} file(s), {len(doc['changed'])} outcome(s) re-derived")
        for c in doc["changed"]:
            print(f"  {c['label']:11s} rep {c['rep']}  {c['was']}  ->  {c['now']}")
        return 0

    if args.cmd == "select":
        doc = cmd_select(args.logs, args.campaign)
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "SELECTION.json")
        if os.path.exists(path):
            old = json.load(open(path, encoding="utf-8"))
            if old.get("selection_sha256") != doc["selection_sha256"]:
                raise RuntimeError(f"{path} already holds a DIFFERENT frozen selection "
                                   f"({old.get('selection_sha256')[:12]} vs "
                                   f"{doc['selection_sha256'][:12]}); refusing to overwrite it")
            print(f"selection unchanged: {old['selection_sha256'][:16]}")
            return 0
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        print(f"rule {RULE}  {doc['n_terminal_training_calls']} terminal training call(s); "
              f"{doc['n_empty_terminal']} empty, {doc['n_answered_terminal']} answered")
        for c in doc["selected"]:
            print(f"  {c['label']:11s} {c['item_id']} {c['job']}  finish={c['finish_reason']} "
                  f"ctoks={c['completion_tokens']} ptoks={c['prompt_tokens']} "
                  f"calls_in_episode={c['episode_calls']}  content_chars={c['content_chars']}")
        print(f"frozen: {path}   selection {doc['selection_sha256'][:16]}")
        return 0

    doc = cmd_send(args.selection, args.out, args.setting, args.reps, args.max_sends, args.label)
    print(f"rule {RULE}  setting {doc['setting']}  {doc['sends']} send(s) of at most {doc['max_sends']}")
    for r in doc["records"]:
        if r.get("skipped"):
            print(f"  {r['label']:11s} rep {r['rep']}  SKIPPED: {r['skipped']}")
            continue
        if r.get("error"):
            print(f"  {r['label']:11s} rep {r['rep']}  ERROR {r['error']['type']}: "
                  f"{r['error']['message'][:120]}")
            continue
        print(f"  {r['label']:11s} rep {r['rep']}  http={r['http']['status_code']} "
              f"finish={r['sdk']['finish_reason']} sdk_content={r['sdk']['content_chars']}ch "
              f"compat={r['compat_message']['content_chars']}ch "
              f"tools={r['compat_message']['tool_names']} -> {r['loop_decision']['branch']}")
        print(f"              outcome: {r['outcome']}")
    print(f"wrote {doc['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
