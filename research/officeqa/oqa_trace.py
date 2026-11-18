"""Build a contract-input packet from a recorded OfficeQA rollout, and audit the information boundary.

What it reads. Only the files SkillOpt's OfficeQA rollout writes per item (skillopt/envs/officeqa/rollout.py,
`process_one`):
    <predictions>/<id>/target_user_prompt.txt   the first user message (question, supplied pages, candidate files,
                                                source hints)
    <predictions>/<id>/conversation.json        the recorded turns: {"type":"message"} and
                                                {"type":"tool_call","cmd":...,"obs":...}
The answer key is NOT read. `results.jsonl` is opened only by `audit_no_gold`, which checks that the gold string
does not appear in the packet and returns a verdict, never the gold itself.

Why the audit matters. In the released OfficeQA adapter the rollout record's `fail_reason` is literally
"predicted '<X>' but expected '<GOLD>'", and `fmt_minibatch_trajectories` puts `fail_reason` into the analyst
prompt. So SkillOpt's own optimizer view contains the answer key for every failed item. That is upstream behaviour
and is left alone; it only means the contract input must be built separately, from the trace, and audited - which
is what this module does.

Limitation. The conversation record for the offline tool loop stores `{"type":"message","content":...}` with no turn
index and no reasoning field, so the packet's step numbering is the record order, and any chain of thought the
target produced outside its message content is NOT in the record and cannot be checked.
"""
from __future__ import annotations

import hashlib
import json
import os
import re


def trace_digest(pred_root: str, item_id: str) -> str:
    """Digest of the exact attempt a report must describe: its conversation plus the prompt it was given.

    ONE definition, here in the shared module, because three callers need it: `oqa_reports` writes it
    into every report record, the learner records which attempts it learned from, and the forensics audit
    compares the two. A second copy would make a provenance check a statement about the copy.

    It is deliberately NOT sufficient on its own to identify an attempt. `conversation.json` keeps the
    assistant's content and the tool commands, so when the content is empty two different provider
    interactions save one identical file -- arm C's epoch-3 UID0019 trace is byte-identical to the shared
    epoch-1 trace. Pair it with the collection it came from and the saved result row.
    """
    h = hashlib.sha256()
    for name in ("conversation.json", "target_user_prompt.txt"):
        h.update(b"\x00" + name.encode() + b"\x00")
        try:
            with open(os.path.join(pred_root, item_id, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()

_SECTION = re.compile(r"^## (.+)$", re.M)


def parse_user_prompt(text: str) -> dict:
    """Split the first user message into its sections (built by rollout._build_user)."""
    out: dict[str, str] = {}
    marks = list(_SECTION.finditer(text or ""))
    for idx, m in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        out[m.group(1).strip()] = text[m.end():end].strip()
    return out


def _oracle_pages(block: str) -> list[dict]:
    """Split the "Oracle Parsed Pages" section into its per-page blocks ("### <file> page <n>")."""
    pages: list[dict] = []
    parts = re.split(r"^### (.+?) page (\d+)\s*$", block or "", flags=re.M)
    for i in range(1, len(parts) - 2, 3):
        file_name, page_no, body = parts[i].strip(), int(parts[i + 1]), parts[i + 2]
        url = ""
        m = re.match(r"\s*Source URL:\s*(\S+)", body)
        if m:
            url, body = m.group(1), body[m.end():]
        pages.append({"file": file_name, "page": page_no, "url": url, "text": body.strip()})
    return pages


_TOOL_CALL = re.compile(r"^(glob|read|grep)\((.*)\)$", re.S)


def _parse_cmd(cmd: str) -> tuple[str, dict]:
    """Recover the tool name and arguments from the recorded `cmd` string (run_tool's repr form)."""
    m = _TOOL_CALL.match((cmd or "").strip())
    if not m:
        return (cmd or "").strip()[:40], {}
    tool, body = m.group(1), m.group(2)
    args: dict = {}
    for key, raw in re.findall(r"(\w+)=('[^']*'|\"[^\"]*\"|-?\d+)", body):
        args[key] = raw[1:-1] if raw[0] in "'\"" else int(raw)
    return tool, args


def build_packet(prediction_dir: str, item_id: str, question: str = "") -> dict:
    """One item's directory -> the packet that oqa_check.report consumes."""
    base = os.path.join(prediction_dir, item_id)
    with open(os.path.join(base, "target_user_prompt.txt"), encoding="utf-8") as f:
        user = f.read()
    with open(os.path.join(base, "conversation.json"), encoding="utf-8") as f:
        conversation = json.load(f)
    sections = parse_user_prompt(user)
    steps: list[dict] = []
    final_answer = ""
    index = 0
    for event in conversation:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool_call":
            index += 1
            tool, args = _parse_cmd(event.get("cmd", ""))
            steps.append({"i": index, "kind": "tool", "tool": tool, "args": args,
                          "cmd": event.get("cmd", ""), "obs": event.get("obs", "")})
        elif event.get("type") == "message" or (event.get("role") == "assistant"):
            index += 1
            text = event.get("content") or ""
            steps.append({"i": index, "kind": "message", "text": text})
            m = re.search(r"(?is)<answer>(.*?)</answer>", text)
            if m:
                final_answer = m.group(1).strip()
    candidate = [line.strip("- ").strip() for line in (sections.get("Candidate Files") or "").splitlines()
                 if line.strip().startswith("-") and "none resolved" not in line]
    hints = [line.strip("- ").strip() for line in (sections.get("Source Hints") or "").splitlines()
             if line.strip().startswith("-")]
    return {
        "id": item_id,
        "question": question or (sections.get("Question") or "").strip(),
        "candidate_files": [c.rsplit("/", 1)[-1] for c in candidate],
        "source_hints": hints,
        "oracle_pages": _oracle_pages(sections.get("Oracle Parsed Pages") or ""),
        "steps": steps,
        "final_answer": final_answer,
        "provenance": {"prediction_dir": os.path.abspath(base),
                       "sections_found": sorted(sections),
                       "conversation_events": len(conversation)},
    }


PACKET_KEYS = {"id", "question", "candidate_files", "source_hints", "oracle_pages", "steps", "final_answer",
               "provenance", "family", "intended", "expect"}

EVALUATOR_ONLY_FIELDS = ("ground_truth", "answers", "gold_answer", "fail_reason", "hard", "soft", "em", "f1",
                         "predicted_answer")


def audit_no_gold(packet: dict, results_jsonl: str) -> dict:
    """Verify that no evaluator-only information entered the packet.

    What this can and cannot establish. The gold VALUE is usually present somewhere in the source documents - that
    is what makes the question answerable - so finding the gold string inside page text proves nothing and is
    reported as INFO. What must be absent is evaluator-only information: any field of the rollout record that
    exists only because the answer key was consulted, above all `fail_reason`, which the released adapter writes as
    "predicted '<X>' but expected '<GOLD>'" and which SkillOpt's analyst prompt does include.

    Reads the answer key only to assert these absences; it is never returned or logged.
    """
    row = None
    with open(results_jsonl, encoding="utf-8") as f:
        for line in f:
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(candidate.get("id")) == str(packet["id"]):
                row = candidate
                break
    if row is None:
        return {"verdict": "UNKNOWN", "reason": f"item {packet['id']} not found in {results_jsonl}"}

    problems: list[str] = []
    extra_keys = sorted(set(packet) - PACKET_KEYS)
    if extra_keys:
        problems.append(f"packet carries keys outside the declared whitelist: {extra_keys}")
    blob = json.dumps(packet, ensure_ascii=False)
    for field in EVALUATOR_ONLY_FIELDS:
        if f'"{field}"' in blob:
            problems.append(f"evaluator-only field {field!r} occurs in the packet")
    fail_reason = str(row.get("fail_reason") or "")
    if fail_reason.strip() and fail_reason.strip() in blob:
        problems.append("the evaluator's fail_reason text occurs in the packet")
    gold = str(row.get("ground_truth") or "").strip()
    gold_in_pages = bool(gold) and any(gold in (p.get("text") or "") for p in packet.get("oracle_pages") or [])
    return {
        "verdict": "FAIL" if problems else "PASS",
        "problems": problems,
        "info": {
            "gold_present_in_supplied_pages": gold_in_pages,
            "note": "the gold value occurring in page text is expected and is not a boundary violation; it is the "
                    "document evidence the question is asked about",
        },
        "checked": {"keys": "whitelist", "evaluator_fields": list(EVALUATOR_ONLY_FIELDS),
                    "fail_reason_text": True},
    }
