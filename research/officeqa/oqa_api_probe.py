"""Minimal live tool-calling probe for the OfficeQA actor endpoint. At most 2 HTTP attempts, ~$0.00005.

The cheapest possible answer to "does this endpoint emit usable tool calls for the OfficeQA schemas". Run it
before anything larger whenever the endpoint, the model or the tool schemas change: two attempts settled the
question on 2026-11-17, where a full smoke test would have cost twenty.

It prints response STRUCTURE and usage only and never the key. The key comes from EMPIRIOLABS_API_KEY in the
environment; source it from the private actor env file rather than passing it on a command line.

usage:  set -a; . <actor env file>; set +a; python3 -I research/officeqa/oqa_api_probe.py
exit:   0 tool call returned and the follow-up accepted | 1 HTTP or transport error | 2 no tool call returned
"""
import json, os, sys, time, urllib.request, urllib.error

KEY = os.environ["EMPIRIOLABS_API_KEY"]
URL = "https://api.empiriolabs.ai/v1/chat/completions"
TOOLS = [
    {"type": "function", "function": {"name": "glob", "description": "List document files matching a glob.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "read", "description": "Read a line window of a document.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"},
                                                     "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "grep", "description": "Search a document for a pattern.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["pattern", "path"]}}},
]
SYS = ("You are an OfficeQA research assistant. Use the document tools to find evidence. "
       "Candidate file: treasury_bulletin_1963_01.txt. Return the final answer in <answer></answer>.")
Q = ("How much total paper money was in circulation in December 1960? "
     "Search the candidate file for the figure before answering.")


def call(messages, n):
    body = {"model": "qwen3-5-4b", "messages": messages, "tools": TOOLS, "tool_choice": "auto",
            "temperature": 0.7, "top_p": 1.0, "max_tokens": 512, "enable_thinking": False}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, out = r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("attempt %d: HTTP %s :: %s" % (n, e.code, e.read().decode()[:300]))
        return None
    except Exception as e:
        print("attempt %d: %s: %s" % (n, type(e).__name__, e))
        return None
    lat = time.time() - t0
    ch = out["choices"][0]
    msg = ch["message"]
    tc = msg.get("tool_calls") or []
    print("attempt %d: HTTP %s  returned_model=%s  finish_reason=%s  %.2fs"
          % (n, status, out.get("model"), ch.get("finish_reason"), lat))
    print("   usage: %s" % json.dumps(out.get("usage")))
    print("   content: %r" % (str(msg.get("content"))[:160]))
    print("   reasoning_content present: %s" % bool(msg.get("reasoning_content")))
    print("   tool_calls: %d" % len(tc))
    for c in tc:
        f = c.get("function", {})
        raw = f.get("arguments")
        try:
            parsed, ok = (json.loads(raw) if raw else {}), "JSON OK"
        except Exception as ex:
            parsed, ok = None, "NOT JSON (%s)" % type(ex).__name__
        print("     id=%s type=%s name=%r args=%s -> %s"
              % (c.get("id"), c.get("type"), f.get("name"), ok, json.dumps(parsed)))
    return out, msg, tc


print("=== ATTEMPT 1: does the endpoint emit a structured tool call for the OfficeQA schemas? ===")
r1 = call([{"role": "system", "content": SYS}, {"role": "user", "content": Q}], 1)
if not r1:
    sys.exit(1)
_, msg, tc = r1
if not tc:
    print("\nNo tool_calls returned. Nothing to feed back; the loop would stall here.")
    sys.exit(2)
print("\n=== ATTEMPT 2: does it accept a role=tool result and continue? ===")
msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": Q},
        {"role": "assistant", "content": msg.get("content") or "", "tool_calls": tc},
        {"role": "tool", "tool_call_id": tc[0]["id"],
         "content": "4312: | 1960-December | 32869 | 30442 | 30 |"}]
r2 = call(msgs, 2)
print("\nVERDICT (2 attempts): tool_calls=%s  continued_after_tool_result=%s"
      % (bool(tc), bool(r2)))
