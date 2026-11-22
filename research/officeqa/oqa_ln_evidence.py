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

# ── amendment D-5: two more shapes the parser cannot represent ───────────────
#
# (a) A COMMITTED ANSWER THAT IS NOT ONE NUMBER. `<answer>[10102000000, 4.73]</answer>` and
#     `<answer>March 3, 1977</answer>` were pushed through a numeric strip and became 101020000004.73
#     and 31977.0 - values no calculation could ever yield, so `answer_traced` was VIOLATED and the
#     actor was accused. Eight of the 48 frozen attempts commit an answer of this kind.
# (b) ARITHMETIC WRITTEN WITH ABSOLUTE-VALUE BARS. `|2.0% - 0.2%| = 1.8%` is work the actor DID write
#     down; the binary-statement regex cannot match across the bars, so the attempt reported no
#     written calculation at all. All four UID0065 attempts are of this kind.
#
# Both are OUT OF SCOPE, not false. A value the parser cannot represent yields UNKNOWN.
_SINGLE_ANSWER = re.compile(r"^\s*\$?\s*-?\d[\d,]*(?:\.\d+)?\s*%?\s*$")
_ABS_STATEMENT = re.compile(
    r"\|\s*-?\$?\d[\d,]*(?:\.\d+)?\s*%?\s*[-+*/x\u00d7\u00f7]\s*"
    r"-?\$?\d[\d,]*(?:\.\d+)?\s*%?\s*\|\s*=")


def committed_value(raw):
    """The committed answer as ONE number, or None with the reason it cannot be read as one."""
    if raw is None:
        return None, "no <answer> block was written"
    if _SINGLE_ANSWER.match(raw):
        return num(re.sub(r"[^\d.,-]", "", raw)), ""
    return None, ("the committed answer is not a single number this parser can represent "
                  f"({raw!r}), so nothing about its provenance can be checked")


# ── amendment D-4: a binary match inside a larger expression is not a statement ─
#
# `_CHAINED` counts `a OP b OP c =` and declares it out of scope. It does not fire when the larger
# expression contains a parenthesis, so `(472,027,235 / 3,564,583,961) x 100 = 13.2421309` leaks
# through `_CALC` as the BINARY statement `3,564,583,961 x 100 = 13.2421309` - the divisor read as a
# left operand, the numerator dropped - which then "does not hold as written". The actor's arithmetic
# was right; the parse was wrong, and the milestone report repeated the parse as an actor error.
#
# A match is a FRAGMENT when the first non-space character before it is an operator or a closing
# parenthesis that is itself preceded by a digit, a closing parenthesis or a percent sign - that is,
# when the match continues an expression that started earlier. A markdown bullet "- 100 - 50 = 50" is
# not a fragment, because the "-" is preceded by a line break rather than by a value.
#
# A fragment is kept in the record and FLAGGED, not deleted: deleting it would change the frozen
# operand counts and make every earlier number incomparable. What changes is how it is READ - an
# out-of-scope expression is unknown, never false.
_FRAG_OPS = frozenset("/*+-x\u00d7\u00f7")
_FRAG_BEFORE = frozenset(")%")


def is_fragment(text: str, start: int) -> bool:
    """Does the binary match at `start` continue an expression that began earlier in `text`?"""
    i = start - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i < 0 or (text[i] not in _FRAG_OPS and text[i] != ")"):
        return False
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j >= 0 and (text[j].isdigit() or text[j] in _FRAG_BEFORE)


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

# AMENDMENT D-2 IS THE DEFAULT IN THIS PIPELINE. `oqa_occ` keeps the superseded behaviour as ITS
# default so the pinned historical report path cannot move; the learned-contract pipeline asks for the
# corrected one, and every function below takes the switch so one code path produces both sides of the
# comparison.
NUMERIC_CELLS_ONLY = True


def source_root(freeze_doc: dict) -> str:
    """The frozen source root, or a local mirror of it named by `OQA_LN_SOURCE_ROOT`.

    The freeze records the CLUSTER path and its digest covers that string, so the path cannot be
    rewritten without changing the digest that witnesses the frozen set. The override names a
    different COPY of the same records, and every consumer verifies each attempt's `trace_sha256`
    against the freeze, so a mirror that is not byte-identical is refused rather than used.
    """
    return os.environ.get("OQA_LN_SOURCE_ROOT") or freeze_doc["source_root"]


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


def _positions_in_prefix(prefix_items: list, numeric_cells_only: bool) -> dict:
    """value -> the observed occurrences of it in the prefix, from `oqa_occ`'s own extractor."""
    by_value: dict = {}
    for occ in OCC.observed_occurrences(prefix_items, numeric_cells_only=numeric_cells_only):
        by_value.setdefault(occ["value"], []).append(occ)
    return by_value


def extract(attempt: dict, *, numeric_cells_only: bool = NUMERIC_CELLS_ONLY) -> dict:
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
        positions = _positions_in_prefix(prefix_items, numeric_cells_only)
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
            fragment = is_fragment(text, m.start())
            calcs.append({
                "message_item_index": idx,
                "message_order": pos,
                "statement": m.group(0).strip(),
                "op": op,
                "result": r,
                "result_as_written": r_raw,
                "arithmetic_consistent": consistent,          # MATH, not learned
                # D-4: this match continues a larger expression, so the three numbers above are not
                # the statement the actor wrote. Its arithmetic is OUT OF SCOPE, not false.
                "fragment_of_larger_expression": fragment,
                "arithmetic_in_scope": not fragment,
                "operands": operands,
            })
            earlier_results.append(r)

    # the committed answer, under the campaign's narrow committed-answer policy
    answers = [mm.group(1).strip() for (_i, k, _c, _o, t) in flat if k == "message"
               for mm in _ANSWER.finditer(t)]
    committed = answers[-1] if answers else None
    committed_val, committed_why = committed_value(committed)
    message_text = "\n".join(t for (_i, k, _c, _o, t) in flat if k == "message" and t)
    n_unrepresented = (len(_ABS_STATEMENT.findall(message_text))
                       + chained_skipped
                       + sum(1 for c in calcs if c["fragment_of_larger_expression"]))

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
        "numeric_cells_only": numeric_cells_only,
        "committed_answer_raw": committed,
        "committed_answer_value": committed_val,
        "committed_answer_unreadable_reason": committed_why,
        # D-5: arithmetic the actor wrote and this parser cannot represent - absolute-value bars,
        # chained expressions, and binary matches that are fragments of a larger expression.
        "n_unrepresented_arithmetic": n_unrepresented,
        "n_absolute_value_statements": len(_ABS_STATEMENT.findall(message_text)),
    }


def verdicts(attempt: dict, extracted: dict, *,
             numeric_cells_only: bool = NUMERIC_CELLS_ONLY) -> dict:
    """The resolver's verdict per (calculation, operand), computed from items[0:k+1]. Computed SEPARATELY
    from `extract` so that a prediction built on the features can be frozen before any verdict exists."""
    items = attempt["items"]
    out = {}
    for ci, calc in enumerate(extracted["calculations"]):
        upto = items[: calc["message_item_index"] + 1]
        index = OCC.support_index(upto, numeric_cells_only=numeric_cells_only)
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
                "field_text": (b or {}).get("field_text"),
                "why": (b or {}).get("why", "the operand was not written in any actor message"),
            }
    return out


# ── amendment D-3e: occurrence-level dependencies between written calculations ─
#
# "Superseded" used to mean "an earlier calculation whose result is not the committed answer", and the
# report printed every one of them under WORK THAT WAS REPLACED. That conflates two different things.
# In p4/UID0063 the statement `5,541,720 - 5,459,946 = 81,774` was printed as replaced, and 81,774 is
# an OPERAND of the calculation that supports the committed answer: the final answer consumes its
# result. It was not abandoned; it is an ancestor.
#
# WHAT THIS DOES NOT DO. A dependency is proposed by numeric equality and nothing else, because the
# records carry nothing else - so a value that several earlier calculations could have produced keeps
# ALL of them as candidates and is reported AMBIGUOUS rather than resolved to one. An identity such as
# `86,000 - 0 = 86,000` is marked as a RESTATEMENT: it reproduces its own operand and establishes no
# origin at all, so following the edge only moves the same question one step back.

_CLOSE = 1e-6


def _near(x, y) -> bool:
    if x is None or y is None:
        return False
    return abs(x - y) <= max(0.5, abs(y) * _CLOSE)


def _is_identity(calc: dict) -> bool:
    """Does this statement merely restate one of its own operands?  x-0=x, x+0=x, x*1=x, x/1=x."""
    a, b = (o["value"] for o in calc["operands"])
    r = calc["result"]
    if calc["op"] in ("-", "+") and (_near(b, 0.0) and _near(r, a)):
        return True
    if calc["op"] in ("+",) and (_near(a, 0.0) and _near(r, b)):
        return True
    if calc["op"] in ("*", "x", "\u00d7") and ((_near(b, 1.0) and _near(r, a))
                                            or (_near(a, 1.0) and _near(r, b))):
        return True
    if calc["op"] in ("/", "\u00f7") and _near(b, 1.0) and _near(r, a):
        return True
    return False


def dependencies(extracted: dict) -> dict:
    """For every operand of every calculation, which EARLIER calculations could have produced it.

    Returns {calc_index: {operand_index: {"candidates": [j...], "edge": CLASS}}} with CLASS one of
    `no_earlier_producer`, `one_earlier_producer`, `several_earlier_producers`. Nothing here decides
    that an edge IS the origin; `several_earlier_producers` is kept as an ambiguity.
    """
    calcs = extracted["calculations"]
    out: dict = {}
    for i, c in enumerate(calcs):
        row = {}
        for oi, operand in enumerate(c["operands"]):
            cands = [j for j in range(i) if _near(calcs[j]["result"], operand["value"])]
            row[oi] = {
                "value": operand["value"], "as_written": operand["as_written"],
                "candidates": cands,
                "edge": ("no_earlier_producer" if not cands else
                         "one_earlier_producer" if len(cands) == 1 else
                         "several_earlier_producers"),
                "candidate_statements": [calcs[j]["statement"] for j in cands][:8],
                "candidates_are_identities": [j for j in cands if _is_identity(calcs[j])],
            }
        out[i] = row
    return out


def consumed_by(extracted: dict, target: int) -> dict:
    """Every earlier calculation whose result could reach `target`, with the path's ambiguity kept.

    A calculation is CONSUMED when the target's own operands, or the operands of something the target
    consumes, could have come from it. "Could" is the honest word: the edge is numeric equality, so a
    consumed set may be larger than the truth, and that is the safe direction - it stops a live
    ancestor being reported as abandoned work.
    """
    deps = dependencies(extracted)
    reached, frontier = set(), [target]
    while frontier:
        i = frontier.pop()
        for _oi, e in deps.get(i, {}).items():
            for j in e["candidates"]:
                if j not in reached:
                    reached.add(j)
                    frontier.append(j)
    return {"target": target, "consumed": sorted(reached), "dependencies": deps}


def classify_earlier(extracted: dict, supporting: int, superseded: list) -> list:
    """Split the calculations the shape test called superseded into abandoned work and live ancestors."""
    if supporting is None:
        return []
    consumed = set(consumed_by(extracted, supporting)["consumed"])
    calcs = extracted["calculations"]
    out = []
    for i in superseded:
        out.append({
            "index": i, "statement": calcs[i]["statement"], "result": calcs[i]["result"],
            "still_recorded": True,
            "supports_the_answer": False,
            "dependency_class": ("CONSUMED_ANCESTOR" if i in consumed else "ABANDONED_ALTERNATIVE"),
            "why": ("its result could still reach the calculation that supports the committed answer, "
                    "so it was not replaced - the answer consumes it"
                    if i in consumed else
                    "no later calculation on the path to the committed answer uses its result"),
            "is_identity_restatement": _is_identity(calcs[i]),
        })
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
    empty = {"qualifies": False, "supporting_calculation": None, "superseded_calculations": [],
             "committed_answer_value": cv}
    if cv is None:
        return {**empty, "why": "no committed answer"}
    # THE SUPPORTING CALCULATION IS FOUND WHETHER OR NOT THIS IS A RECOVERY. The first version
    # returned early on `len(calcs) < 2`, so an attempt whose SINGLE written calculation yields the
    # committed answer reported no supporting calculation at all - and the stage layer reads that as
    # "the committed answer is the result of no written calculation" and marks `answer_traced`
    # VIOLATED. That is a FALSE ACCUSATION against an actor that did exactly the right thing, and it
    # is the defect the Lean cross-check led to: Lean disagreed about `sourceTraced` on attempts with
    # no supporting calculation, and following that disagreement is what exposed this.
    close = lambda x: abs(x - cv) <= max(0.5, abs(cv) * 1e-6)  # noqa: E731
    supporting = [i for i, c in enumerate(calcs) if close(c["result"])]
    if not supporting:
        return {**empty, "why": "no written calculation yields the committed answer"}
    last = supporting[-1]
    superseded = [i for i, c in enumerate(calcs) if i < last and not close(c["result"])]
    return {
        "qualifies": bool(superseded),
        "supporting_calculation": last,
        "superseded_calculations": superseded,
        "committed_answer_value": cv,
        "why": ("a later calculation yields the committed answer and at least one earlier one does not"
                if superseded else "the committed answer is the result of written work, with nothing "
                                   "replaced"),
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
    # THE DIGEST IS OVER THE CONTENT, NOT THE CLOCK. The first version hashed the whole document
    # including `written_utc`, so re-freezing the identical record set produced a different digest
    # (63c10e469f45930e and ba3ab77882193c84 are the same 48 attempts, the same 6/6 split and the same
    # 676 calculations, written four minutes apart). A digest that changes with the clock cannot
    # witness "this record set was fixed before any learner call", which is the only thing it is for.
    # The same defect was found and fixed in oqa_ln_check.predict by a test that changed only a
    # verdict; this one was found by two freezes of one record set disagreeing.
    doc["written_utc"] = _utc()
    doc["self_sha256"] = sha(json.dumps(
        {k: v for k, v in doc.items() if k not in ("written_utc", "self_sha256")},
        indent=1, sort_keys=True))
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
