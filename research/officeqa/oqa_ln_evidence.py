#!/usr/bin/env python3
"""The frozen evidence set for the learned source-to-calculation contract. Rule OQA-LN-1.

WHAT THIS EXTRACTS, and why each piece is separated from the others.

The relationship under study is the narrow one the instruction prioritises:

    question requirement -> relevant lookup -> supported value -> calculation -> final answer

and the only part of it that is LEARNED is a prediction about when a number the actor wrote down can be
traced to a source it observed. Everything else is supplied, and is labelled as such:

  TASK    the question sentence, and the numbers that appear in it.
  GIVEN   the parse of an arithmetic statement into operands and a result; the arithmetic itself.
  OBS     the observations the tools returned, in order, and the surface form of each number as the
          actor wrote it.
  MATH    that a-b=c is a definition, not a discovery.
  LEARNED only the conditional prediction, proposed by the API learner and checked here.

THE PREFIX DISCIPLINE. Features for a calculation written in message k are computed from items[0:k] -
the observations the actor had actually received - plus the SURFACE FORM of the operand as written in
message k. The resolver verdict uses items[0:k+1], because the occurrence resolver needs the writing
message's own words to find a corroboration. So the prediction is made from what was observable before
the number was committed, and the verdict uses the message that committed it. Nothing later is read.

WHAT IS DELIBERATELY WITHHELD FROM THE LEARNER. The resolver's two corroborations (ROW_LABEL, QUERY in
`oqa_occ`) are its own inputs; handing them to the learner would make any rule that used them a
restatement of the resolver's definition rather than an empirical claim. The learner sees BEHAVIOURAL
correlates instead - which tool returned the number, how long ago, how the actor wrote it. Those are not
independent of the corroborations (QUERY can only arise from a grep) and that limitation is stated with
every result rather than hidden.

Answer keys never enter: `ground_truth` and `soft` are not read, and `hard` is not read either - the
recovery control is selected MECHANICALLY, by the shape of the trajectory, so no outcome filters the
evidence.
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_occ as OCC  # noqa: E402

RULE = "OQA-LN-1"
SCHEMA = "lso.officeqa_learned_source_evidence.v1"

# A binary arithmetic statement with its result written down. Deliberately narrow: a chained expression
# is COUNTED and reported as out of scope rather than parsed badly.
_CALC = re.compile(
    r"(?<![\d.,])(-?\d[\d,]*(?:\.\d+)?)\s*([-+*/x×÷])\s*(-?\d[\d,]*(?:\.\d+)?)\s*=\s*(-?\d[\d,]*(?:\.\d+)?)")
_CHAINED = re.compile(r"(?<![\d.,])-?\d[\d,]*(?:\.\d+)?(?:\s*[-+*/x×÷]\s*-?\d[\d,]*(?:\.\d+)?){2,}\s*=")
_ANSWER = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.S)
_NUM_IN = re.compile(r"(?<![\d.,])-?\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z]{4,}")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def num(raw: str):
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _is_power_of_ten(v) -> bool:
    if v is None or v <= 0:
        return False
    s = ("%.10g" % v).lstrip("0.")
    return s.startswith("1") and set(s[1:]) <= {"0"} and v >= 10


# ── reading one attempt ───────────────────────────────────────────────────────

def read_attempt(pred_dir: str, item: str) -> dict:
    """The permitted record of one attempt: the saved conversation and the saved prompts. Nothing else."""
    conv = os.path.join(pred_dir, item, "conversation.json")
    with open(conv, encoding="utf-8") as f:
        items = json.load(f)
    sys_p = os.path.join(pred_dir, item, "target_system_prompt.txt")
    usr_p = os.path.join(pred_dir, item, "target_user_prompt.txt")
    return {
        "item": item,
        "conversation_path": os.path.abspath(conv),
        "items": items,
        "trace_sha256": sha(json.dumps(items, sort_keys=True, ensure_ascii=False)),
        "system_prompt": open(sys_p, encoding="utf-8").read() if os.path.exists(sys_p) else "",
        "user_prompt": open(usr_p, encoding="utf-8").read() if os.path.exists(usr_p) else "",
    }


def question_of(user_prompt: str) -> str:
    """The question sentence, from the saved prompt. TASK provenance."""
    m = re.search(r"##\s*Question\s*\n(.+?)(?:\n##|\Z)", user_prompt, re.S)
    return (m.group(1).strip() if m else "").strip()


def _flat(items: list) -> list:
    """items in order as (index, kind, cmd, obs, text). `kind` is 'tool' or 'message'."""
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        kind = it.get("kind") or {"tool_call": "tool", "message": "message"}.get(it.get("type"))
        if kind == "tool":
            out.append((i, "tool", str(it.get("cmd") or ""), str(it.get("obs") or ""), ""))
        elif kind == "message":
            out.append((i, "message", "", "", str(it.get("content") or it.get("text") or "")))
    return out


def _positions_in_prefix(prefix_items: list) -> dict:
    """value -> the observed occurrences of it in the prefix, from `oqa_occ`'s own extractor."""
    by_value: dict = {}
    for occ in OCC.observed_occurrences(prefix_items):
        by_value.setdefault(occ["value"], []).append(occ)
    return by_value


def extract(attempt: dict) -> dict:
    """Every written binary calculation, its operands, their prefix-only features and the verdict.

    The verdict is computed LAST and kept in its own field, so a prediction file written from the
    features alone can be digested before any verdict is looked at.
    """
    items = attempt["items"]
    flat = _flat(items)
    question = question_of(attempt["user_prompt"])
    q_nums = {num(m.group(0)) for m in _NUM_IN.finditer(question)} - {None}
    q_words = {w.lower() for w in _WORD.findall(question)} - OCC.STOPWORDS

    calcs, chained_skipped = [], 0
    earlier_results: list = []
    for pos, (idx, kind, cmd, obs, text) in enumerate(flat):
        if kind != "message" or not text:
            continue
        chained_skipped += len(_CHAINED.findall(text))
        prefix_items = items[:idx]
        prefix_flat = flat[:pos]
        positions = _positions_in_prefix(prefix_items)
        msg_words = {w.lower() for w in _WORD.findall(text)} - OCC.STOPWORDS
        quotes_question_term = bool(msg_words & q_words)
        for m in _CALC.finditer(text):
            a_raw, op, b_raw, r_raw = m.group(1), m.group(2), m.group(3), m.group(4)
            a, b, r = num(a_raw), num(b_raw), num(r_raw)
            if a is None or b is None or r is None:
                continue
            expected = {"-": a - b, "+": a + b, "*": a * b, "x": a * b, "×": a * b,
                        "/": (a / b if b else None), "÷": (a / b if b else None)}[op]
            consistent = (expected is not None
                          and abs(expected - r) <= max(0.5, abs(r) * 1e-6))
            operands = []
            for raw, value in ((a_raw, a), (b_raw, b)):
                occs = positions.get(value, [])
                # Which tool returned it. `oqa_occ`'s occurrence record carries the grep PATTERN's
                # tokens and nothing else about the call, so a non-empty pattern is the observable
                # that distinguishes a grep from a read. This is weaker than the resolver's QUERY
                # corroboration, which additionally requires the pattern's words to appear ON the
                # line - so it is a correlate of the verdict, not the verdict's own input.
                tools = sorted({("grep" if o.get("pattern_tokens") else "read") for o in occs})
                docs = sorted({o["document"] for o in occs})
                operands.append({
                    "as_written": raw,
                    "value": value,
                    # ── the features the learner may condition on (prefix-only) ──
                    "features": {
                        "written_with_separator": "," in raw,
                        "round_power_of_ten": _is_power_of_ten(value),
                        "digits": len(re.sub(r"\D", "", raw)),
                        "appears_in_question": value in q_nums,
                        "equals_earlier_result": value in earlier_results,
                        "n_positions_in_prefix": (0 if not occs else 1 if len(occs) == 1 else 2),
                        "source_tool": (tools[0] if len(tools) == 1 else
                                        "none" if not tools else "mixed"),
                        "n_source_documents": len(docs),
                        "message_quotes_question_term": quotes_question_term,
                        "prefix_n_observations": sum(1 for e in prefix_flat if e[1] == "tool"),
                    },
                })
            calcs.append({
                "message_item_index": idx,
                "message_order": pos,
                "statement": m.group(0).strip(),
                "op": op,
                "result": r,
                "result_as_written": r_raw,
                "arithmetic_consistent": consistent,          # MATH, not learned
                "operands": operands,
            })
            earlier_results.append(r)

    # the committed answer, under the campaign's narrow committed-answer policy
    answers = [mm.group(1).strip() for (_i, k, _c, _o, t) in flat if k == "message"
               for mm in _ANSWER.finditer(t)]
    committed = answers[-1] if answers else None
    committed_value = num(re.sub(r"[^\d.,-]", "", committed)) if committed else None

    return {
        "item": attempt["item"],
        "trace_sha256": attempt["trace_sha256"],
        "conversation_path": attempt["conversation_path"],
        "question": question,
        "n_items": len(items),
        "n_observations": sum(1 for e in flat if e[1] == "tool"),
        "n_messages": sum(1 for e in flat if e[1] == "message"),
        "calculations": calcs,
        "chained_statements_out_of_scope": chained_skipped,
        "committed_answer_raw": committed,
        "committed_answer_value": committed_value,
    }


def verdicts(attempt: dict, extracted: dict) -> dict:
    """The resolver's verdict per (calculation, operand), computed from items[0:k+1]. Computed SEPARATELY
    from `extract` so that a prediction built on the features can be frozen before any verdict exists."""
    items = attempt["items"]
    out = {}
    for ci, calc in enumerate(extracted["calculations"]):
        upto = items[: calc["message_item_index"] + 1]
        index = OCC.support_index(upto)
        for oi, operand in enumerate(calc["operands"]):
            b = index.get(operand["value"])
            out[f"{ci}/{oi}"] = {
                "value": operand["value"],
                "match": (b or {}).get("match", OCC.NO_OCCURRENCE),
                "binding": (b or {}).get("binding", OCC.UNKNOWN),
                "sourced": bool(b and b.get("binding") == OCC.SUPPORTED),
                "corroborations": (b or {}).get("corroborations", []),
                "supporting": (b or {}).get("supporting", []),
                "n_positions": (b or {}).get("n_positions", 0),
                "position": (b or {}).get("position"),
                "why": (b or {}).get("why", "the operand was not written in any actor message"),
            }
    return out


# ── the recovery control, selected by SHAPE and not by any score ─────────────

def recovery_shape(extracted: dict) -> dict:
    """Does this attempt contain a replaced calculation and a committed answer?

    MECHANICAL, so no outcome filters the evidence set: two calculations whose results DIFFER, the later
    one's result equal to the committed answer. The earlier one is then superseded by the record's own
    structure, not by a judgement about which was right.
    """
    calcs = extracted["calculations"]
    cv = extracted["committed_answer_value"]
    if cv is None or len(calcs) < 2:
        return {"qualifies": False, "why": "no committed answer" if cv is None else "fewer than two calculations"}
    supporting = [i for i, c in enumerate(calcs)
                  if abs(c["result"] - cv) <= max(0.5, abs(cv) * 1e-6)]
    if not supporting:
        return {"qualifies": False, "why": "no written calculation yields the committed answer"}
    last = supporting[-1]
    superseded = [i for i, c in enumerate(calcs)
                  if i < last and abs(c["result"] - cv) > max(0.5, abs(cv) * 1e-6)]
    return {
        "qualifies": bool(superseded),
        "supporting_calculation": last,
        "superseded_calculations": superseded,
        "committed_answer_value": cv,
        "why": ("a later calculation yields the committed answer and at least one earlier one does not"
                if superseded else "every earlier calculation already yields the committed answer"),
    }


# ── the frozen record set ─────────────────────────────────────────────────────

PASSES = (1, 2, 3, 4)


def select(s0_root: str) -> dict:
    """The record set and its roles, decided MECHANICALLY and frozen before any learner call.

    TASK-SEPARATED CHECKING. The twelve frozen training questions are ordered by
    `sha256("OQA-LN-1|" + uid)` and split 6/6: every attempt on the first six is a LEARNING record and
    every attempt on the other six is a CHECKING record. So no checking record is an attempt on a
    question the learner saw, which is the strongest separation this evidence allows - there is no
    later epoch to check on, because the actor is frozen and all four passes are contemporaneous.

    WHAT THE SPLIT IS NOT. It is not a sample of unseen data: all 48 attempts are REUSED DEVELOPMENT
    RECORDS, already read by me and already consumed by Trace2Skill as its trajectory collection. They
    can show that a learned rule transfers across questions. They cannot serve as fresh confirmation.
    """
    items = sorted(os.listdir(os.path.join(s0_root, "train_p1", "predictions")))
    ordered = sorted(items, key=lambda u: sha(f"{RULE}|{u}"))
    learn_q, check_q = ordered[:6], ordered[6:]
    attempts = []
    for p in PASSES:
        pd = os.path.join(s0_root, "train_p%d" % p, "predictions")
        for item in sorted(os.listdir(pd)):
            attempts.append({
                "attempt_id": f"p{p}/{item}",
                "pass": p, "item": item,
                "role": "learn" if item in learn_q else "check",
                "prediction_dir": os.path.abspath(pd),
            })
    return {
        "rule": RULE,
        "split_key": f'sha256("{RULE}|" + uid), ascending; first six questions learn, last six check',
        "learn_questions": learn_q,
        "check_questions": check_q,
        "attempts": attempts,
        "n_learn_attempts": sum(1 for a in attempts if a["role"] == "learn"),
        "n_check_attempts": sum(1 for a in attempts if a["role"] == "check"),
    }


EXPOSURE = {
    "provenance": "the 48 fixed-S0 trajectories of job 66552421, four passes over the twelve frozen "
                  "OQA-SEL-4 TRAINING questions, actor frozen, enable_thinking false",
    "already_used_for": [
        "Trace2Skill's trajectory collection - the same 48 trajectories are its entire input, so they "
        "are development records that a baseline method has already consumed",
        "my own reading: I have read UID0026's final messages in all four passes, and historically "
        "UID0014, UID0019, UID0028 and UID0044 in detail",
    ],
    "label": "REUSED DEVELOPMENT RECORDS, not fresh confirmation data",
    "not_read": ["ground_truth", "soft", "the provider's alternate reasoning field",
                 "hard (the score) - the recovery control is selected by trajectory SHAPE instead, so "
                 "no outcome filters the evidence set"],
    "campaign_validation_and_heldout": "untouched; no validation or held-out record is read here",
}

OBSERVATIONAL_ASSUMPTIONS = {
    "line_numbered_pipe_rows_only": "an occurrence exists only where an observation line carries a line "
                                    "number and pipe-delimited fields; a read without line numbers "
                                    "yields no position, and that is recorded rather than guessed",
    "binary_statements_only": "a written calculation is parsed only when it is `a OP b = c`; chained "
                              "statements are counted and reported as out of scope",
    "numeric_equality_is_not_identity": "an operand equal to a cell's value is a numeric match; the "
                                        "source binding requires an independent corroboration "
                                        "(oqa_occ SUPPORTING), and SOLE_IN_ROW is excluded as "
                                        "non-informative",
    "prefix_only": "features from items[0:k]; verdict from items[0:k+1] because the resolver needs the "
                   "writing message's own words",
}


def freeze(s0_root: str, out_path: str) -> dict:
    """Write the manifest: the records, their roles, their digests, the vocabulary and the assumptions.

    This file is written BEFORE any learner call and its digest is quoted with every later result, so
    that the record set cannot be adjusted after a result is seen.
    """
    sel = select(s0_root)
    rows, totals = [], {"calculations": 0, "operands": 0, "chained_out_of_scope": 0}
    recovery = []
    for a in sel["attempts"]:
        att = read_attempt(a["prediction_dir"], a["item"])
        ex = extract(att)
        shape = recovery_shape(ex)
        rows.append({
            "attempt_id": a["attempt_id"], "role": a["role"], "item": a["item"], "pass": a["pass"],
            "trace_sha256": att["trace_sha256"],
            "n_observations": ex["n_observations"], "n_messages": ex["n_messages"],
            "n_calculations": len(ex["calculations"]),
            "n_operands": sum(len(c["operands"]) for c in ex["calculations"]),
            "chained_out_of_scope": ex["chained_statements_out_of_scope"],
            "committed_answer_raw": ex["committed_answer_raw"],
            "recovery_shaped": shape["qualifies"],
        })
        totals["calculations"] += len(ex["calculations"])
        totals["operands"] += sum(len(c["operands"]) for c in ex["calculations"])
        totals["chained_out_of_scope"] += ex["chained_statements_out_of_scope"]
        if shape["qualifies"]:
            recovery.append({"attempt_id": a["attempt_id"], "role": a["role"], **shape})
    doc = {
        "schema": SCHEMA, "rule": RULE, "written_utc": _utc(),
        "source_root": os.path.abspath(s0_root),
        "selection": {k: v for k, v in sel.items() if k != "attempts"},
        "attempts": rows,
        "totals": totals,
        "recovery_controls": recovery,
        "exposure": EXPOSURE,
        "observational_assumptions": OBSERVATIONAL_ASSUMPTIONS,
    }
    body = json.dumps(doc, indent=1, sort_keys=True)
    doc["self_sha256"] = sha(body)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    return doc


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--s0-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    doc = freeze(a.s0_root, a.out)
    print(f"froze {len(doc['attempts'])} attempts  digest {doc['self_sha256'][:16]}")
    print(f"  learn questions: {' '.join(doc['selection']['learn_questions'])}")
    print(f"  check questions: {' '.join(doc['selection']['check_questions'])}")
    print(f"  calculations {doc['totals']['calculations']}  operands {doc['totals']['operands']}"
          f"  chained out of scope {doc['totals']['chained_out_of_scope']}")
    print(f"  recovery-shaped attempts: "
          f"{', '.join(r['attempt_id'] + ' (' + r['role'] + ')' for r in doc['recovery_controls']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
