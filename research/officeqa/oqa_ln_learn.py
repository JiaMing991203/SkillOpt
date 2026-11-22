#!/usr/bin/env python3
"""The API learner for the source-to-calculation contract, and its counterexample repair. Rule OQA-LN-1.

THE DIVISION OF LABOUR, which is the point of the design. DeepSeek PROPOSES a condition in the fixed
vocabulary of `oqa_ln_vocab`; deterministic code here validates it, scores it over the LEARNING records
only, freezes it with a digest, and later checks it. No proposal is accepted on the proposer's word: a
reply that names a predicate outside the vocabulary, relabels a provenance, or puts a consequence in the
antecedent is rejected by `oqa_ln_vocab.validate` before it is scored.

WHAT THE LEARNER IS SHOWN. A deterministic contingency table over the LEARNING records: every distinct
feature vector, with how many operands carried it and how many of those the occurrence resolver found a
corroborated source for. Plus a small fixed sample of concrete operands. It is NOT shown the resolver's
two corroborations, the benchmark answer, the score, the skill, or any checking record.

WHY A TABLE AND NOT THE RECORDS. 676 operands of raw JSON would be most of a prompt and would make the
call's cost depend on how verbose the actor was. The table is a lossless summary with respect to the
features the vocabulary can condition on, and it is built by code, so the learner cannot be shown a
different view than the checker uses.

THE REPAIR CALL supplies the counterexamples' FEATURES and the fact that the rule predicted sourced
where the resolver found no corroborated source. It does not supply the condition to add, and it does
not supply any target value. A repair the learner proposes is validated and scored exactly like a first
proposal, and `oqa_ln_vocab.repair` keeps the old version marked superseded.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_ln_vocab as V  # noqa: E402

CID = "operand.sourced"
FEATURES = ("n_positions_in_prefix", "source_tool", "written_with_separator",
            "round_power_of_ten", "appears_in_question", "equals_earlier_result",
            "message_quotes_question_term")

# The vocabulary term each feature is expressed by, so a proposal and the checker cannot drift apart.
FEATURE_PREDICATE = {
    "n_positions_in_prefix": ("operand_positions", ("0", "1", "many")),
    "source_tool": ("operand_source_tool", ("grep", "read", "none", "mixed")),
    "written_with_separator": ("operand_written_with_separator", None),
    "round_power_of_ten": ("operand_round_power_of_ten", None),
    "appears_in_question": ("operand_in_question", None),
    "equals_earlier_result": ("operand_equals_earlier_result", None),
    "message_quotes_question_term": ("message_quotes_question_term", None),
}


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def rows_for(freeze_doc: dict, role: str, *,
             numeric_cells_only: bool = EV.NUMERIC_CELLS_ONLY) -> list:
    """Every operand of every attempt in `role`, with its prefix-only features and the verdict.

    The verdict is carried in its own key so a caller can build a prediction file from `features` alone
    and digest it before anything reads `sourced`.
    """
    out = []
    for a in freeze_doc["attempts"]:
        if a["role"] != role:
            continue
        pdir = os.path.join(EV.source_root(freeze_doc), "train_p%d" % a["pass"], "predictions")
        att = EV.read_attempt(pdir, a["item"])
        if att["trace_sha256"] != a["trace_sha256"]:
            raise SystemExit(f"{a['attempt_id']}: the trace digest does not match the freeze; "
                             f"the record set has changed since it was frozen")
        ex = EV.extract(att, numeric_cells_only=numeric_cells_only)
        vd = EV.verdicts(att, ex, numeric_cells_only=numeric_cells_only)
        for ci, calc in enumerate(ex["calculations"]):
            for oi, operand in enumerate(calc["operands"]):
                key = f"{ci}/{oi}"
                out.append({
                    "attempt_id": a["attempt_id"], "item": a["item"], "pass": a["pass"],
                    "operand_key": key, "as_written": operand["as_written"],
                    "value": operand["value"],
                    "statement": calc["statement"],
                    "message_item_index": calc["message_item_index"],
                    "arithmetic_consistent": calc["arithmetic_consistent"],
                    "features": {k: operand["features"][k] for k in FEATURES},
                    "verdict": vd[key],
                })
    return out


def _vec(features: dict) -> tuple:
    def norm(k, v):
        if k == "n_positions_in_prefix":
            return {0: "0", 1: "1", 2: "many"}[v]
        return v
    return tuple((k, norm(k, features[k])) for k in FEATURES)


def contingency(rows: list) -> list:
    """Every distinct feature vector, with n operands and how many had a corroborated source."""
    agg: dict = collections.defaultdict(lambda: {"n": 0, "sourced": 0})
    for r in rows:
        a = agg[_vec(r["features"])]
        a["n"] += 1
        a["sourced"] += 1 if r["verdict"]["sourced"] else 0
    out = []
    for vec, a in agg.items():
        out.append({"features": {k: v for k, v in vec}, "n_operands": a["n"],
                    "n_with_a_corroborated_source": a["sourced"]})
    out.sort(key=lambda d: (-d["n_operands"], json.dumps(d["features"], sort_keys=True)))
    return out


_SYSTEM = ("You propose one structured hypothesis for a contract checker over agent trajectories. "
           "Reply with exactly one JSON object and nothing else. Never invent a predicate, never "
           "relabel a provenance, and never claim a definition as a discovery.")

_PROMPT = """A frozen agent answered document questions by running `grep` and `read` over text documents
and writing arithmetic in its own messages. A deterministic resolver then tries to trace each number the
agent wrote back to a cell it had actually observed. The resolver calls a number SOURCED only when it
matches exactly one observed position AND an independent corroboration holds. You are not told what the
corroborations are, and you must not guess at them as predicates - they are not in your vocabulary.

YOUR TASK. Propose ONE contract whose conclusion is `operand_sourced(operand)` and whose conditions
predict, from the features below, when the resolver will call an operand sourced. A contract with no
condition is not acceptable. Prefer a condition that is wrong on few operands over one that is silent
on most of them, and say in `expected_failures` where you expect it to be wrong.

YOU MAY ONLY USE THESE PREDICATES AS CONDITIONS. The provenance label of each is fixed.
{vocab}

The conclusion must be exactly: {{"pred": "operand_sourced", "args": ["operand"], "value": true}}

THE EVIDENCE. {n_rows} operands from {n_attempts} attempts on {n_questions} questions, grouped by their
feature vector. `n_with_a_corroborated_source` is how many of that group the resolver called sourced.

{table}

{examples}

REPLY WITH THIS JSON AND NOTHING ELSE:
{{"conclusion": {{"pred": "operand_sourced", "args": ["operand"], "value": true}},
 "conditions": [{{"pred": "<predicate>", "args": ["<arg>", ...], "value": true}}, ...],
 "reason": "<one or two sentences: what regularity in the table this states>",
 "expected_failures": "<where you expect this to be wrong, and why>"}}
"""

_REPAIR = """You proposed this contract and it was frozen:

{rendered}

Your stated reason was: {reason}

{how_checked} For each operand the contract's conditions held, so it predicted `operand_sourced`. On
{n_counter} of them the resolver found NO corroborated source. Those are counterexamples to your
hypothesis, not mistakes by the agent: the contract claimed something that is not true of these
operands.

Here are the counterexamples, grouped by feature vector. You are NOT being told what the resolver's
corroborations are, and you are not being given the value it should have produced.

{counter_table}

And here, for comparison, are the operands where the contract predicted sourced and WAS right:

{right_table}

REPAIR THE HYPOTHESIS by adding ONE further condition that excludes the counterexamples while keeping as
many of the correct predictions as possible. Use only the predicates you were given. If no available
predicate can separate them, say so instead of inventing one.

REPLY WITH THIS JSON AND NOTHING ELSE:
{{"extra_condition": {{"pred": "<predicate>", "args": ["<arg>", ...], "value": true|false}},
 "reason": "<why this excludes the counterexamples>",
 "separable": true|false,
 "note": "<if separable is false, what the available predicates cannot distinguish>"}}
"""


def _vocab_block() -> str:
    lines = []
    for p in V.LEARNER_CONDITIONS:
        spec = V.PREDICATES[p]
        sorts = ""
        if p == "operand_positions":
            sorts = "   second argument is one of: 0, 1, many"
        elif p == "operand_source_tool":
            sorts = "   second argument is one of: grep, read, none, mixed"
        lines.append(f"  {p}({', '.join(spec['args'])})   provenance {spec['provenance']}{sorts}")
    return "\n".join(lines)


def _examples(rows: list, k: int = 6) -> str:
    """A fixed, deterministic sample: the first k operands in attempt order, sourced and not."""
    yes = [r for r in rows if r["verdict"]["sourced"]][:k]
    no = [r for r in rows if not r["verdict"]["sourced"]][:k]
    def one(r):
        return (f'  {r["attempt_id"]} "{r["statement"]}"  operand {r["as_written"]}  '
                f'features {json.dumps({k2: v for k2, v in sorted(r["features"].items())})}')
    return ("CONCRETE OPERANDS THE RESOLVER CALLED SOURCED:\n" + "\n".join(map(one, yes))
            + "\n\nCONCRETE OPERANDS IT DID NOT:\n" + "\n".join(map(one, no)))


def build_prompt(rows: list, freeze_doc: dict) -> str:
    table = json.dumps(contingency(rows), indent=1, sort_keys=True)
    if len(table) > 60_000:
        table = table[:60_000] + "\n ... (truncated; the groups are ordered by size)"
    return _PROMPT.format(
        vocab=_vocab_block(), n_rows=len(rows),
        n_attempts=len({r["attempt_id"] for r in rows}),
        n_questions=len({r["item"] for r in rows}),
        table=table, examples=_examples(rows))


def _call(system: str, prompt: str, stage: str, cap: int) -> dict:
    """One paid HTTP attempt through the project's own optimizer route, with the ledger in force."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.model import chat_optimizer
    # `retries` IS THE NUMBER OF ATTEMPTS, not the number of extra tries: the backend's loop is
    # `for attempt in range(retries)`. Passing 0 therefore sent NOTHING and raised "failed after 0
    # retries: None" - job 66580895, zero attempts in the ledger, no call log written. 2 means one
    # request and one retry if it fails, and the ledger counts each attempt, which is the right
    # accounting for a 24-attempt allowance.
    text, usage = chat_optimizer(system, prompt, max_completion_tokens=cap, retries=2, stage=stage)
    raw = str(text or "")
    m = re.search(r"\{.*\}", raw, re.S)
    parsed, problem = None, ""
    if m:
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            problem = f"the reply is not JSON: {e}"
    else:
        problem = "the reply contains no JSON object"
    return {"stage": stage, "utc": _utc(),
            "system": system, "prompt": prompt,
            "prompt_sha256": IR.sha(system + prompt), "prompt_chars": len(system) + len(prompt),
            "max_completion_tokens": cap, "reply": raw, "reply_chars": len(raw),
            "usage": usage, "parsed": parsed, "problem": problem}


def propose(rows: list, freeze_doc: dict, *, cap: int = 8000) -> dict:
    return _call(_SYSTEM, build_prompt(rows, freeze_doc), "ln_propose", cap)


def compile_proposal(parsed: dict, rows: list, *, cid: str = CID) -> dict:
    """Validate against the vocabulary, then score over the records the learner was shown. Deterministic."""
    conc = parsed.get("conclusion") or {}
    conclusion = V.clause(conc.get("pred", ""), conc.get("args") or [], conc.get("value", True))
    conds = [V.clause(c.get("pred", ""), c.get("args") or [], c.get("value", True))
             for c in (parsed.get("conditions") or [])]
    c = V.contract(cid, conclusion, conds, witnesses="source_traced",
                   reason=str(parsed.get("reason") or ""), source="deepseek proposal, OQA-LN-1")
    problems = V.validate(c)
    score = score_contract(c, rows) if not problems else None
    return {"contract": c, "problems": problems, "score": score,
            "expected_failures": str(parsed.get("expected_failures") or "")}


# ── the deterministic evaluator: one place decides what a contract says ──────

def _feature_of(pred: str):
    for feat, (p, _sorts) in FEATURE_PREDICATE.items():
        if p == pred:
            return feat
    return None


def applies(c: dict, features: dict) -> tuple:
    """Does every condition hold for this operand? Returns (verdict, why).

    verdict is True (the contract applies and predicts sourced), False (a condition fails, so the
    contract is SILENT - not a prediction of unsourced), or None (a condition cannot be evaluated).
    """
    reasons = []
    for cl in c["conditions"]:
        pred, args, want = cl["pred"], list(cl.get("args") or []), bool(cl.get("value", True))
        if pred == "operand_of":
            continue                                   # structural, always true of an operand
        feat = _feature_of(pred)
        if feat is None:
            return None, [f"{pred}: this contract conditions on something the evaluator cannot read"]
        value = features[feat]
        if feat == "n_positions_in_prefix":
            arg = str(args[1]) if len(args) > 1 else ""
            got = {0: "0", 1: "1", 2: "many"}[value]
            ok = (got == arg)
        elif feat == "source_tool":
            arg = str(args[1]) if len(args) > 1 else ""
            ok = (value == arg)
        else:
            ok = bool(value)
        if ok != want:
            reasons.append(f"{pred}{tuple(args)} is {ok}, the contract wants {want}")
    return (len(reasons) == 0), reasons


def score_contract(c: dict, rows: list) -> dict:
    """Confirmations, counterexamples and silences, with the operand keys of each."""
    conf, counter, silent = [], [], []
    for r in rows:
        verdict, _why = applies(c, r["features"])
        if verdict is None:
            silent.append(r)
            continue
        if not verdict:
            silent.append(r)
        elif r["verdict"]["sourced"]:
            conf.append(r)
        else:
            counter.append(r)
    def keys(rs):
        return [f'{x["attempt_id"]}#{x["operand_key"]}={x["as_written"]}' for x in rs]
    n_applied = len(conf) + len(counter)
    return {
        "n_rows": len(rows), "n_applied": n_applied,
        "confirmations": len(conf), "counterexamples": len(counter), "silent": len(silent),
        "precision_where_it_applies": (round(len(conf) / n_applied, 4) if n_applied else None),
        "coverage_of_sourced": (round(len(conf) / max(1, sum(1 for r in rows
                                                             if r["verdict"]["sourced"])), 4)),
        "confirmation_keys": keys(conf)[:40],
        "counterexample_keys": keys(counter)[:40],
        "questions_applied": sorted({r["item"] for r in conf + counter}),
    }


PROSPECTIVE = "prospective_on_the_checking_questions"
IN_SAMPLE = "in_sample_on_the_learning_records"


def counterexamples_for_repair(c: dict, check_rows: list, learn_rows: list) -> tuple:
    """Which counterexamples a repair may be built from, and how strong they are.

    PROSPECTIVE ones come first: the contract predicted a source on a question the learner never saw
    and the resolver did not confirm it. If there are none, an IN-SAMPLE counterexample is still a
    genuine contradiction - the rule is wrong about an operand the learner was shown - but repairing
    from it is fitting the same batch rather than responding to new evidence, and it is labelled that
    way everywhere it appears. The first version of this only looked at the prospective set and would
    have reported "no contradiction" while one sat in the learning records.
    """
    pro = [r for r in check_rows if applies(c, r["features"])[0] and not r["verdict"]["sourced"]]
    if pro:
        return pro, check_rows, PROSPECTIVE
    ins = [r for r in learn_rows if applies(c, r["features"])[0] and not r["verdict"]["sourced"]]
    return ins, learn_rows, IN_SAMPLE


_HOW_CHECKED = {
    PROSPECTIVE: ("It was then checked, PROSPECTIVELY, on attempts at {n} questions it had never "
                  "seen."),
    IN_SAMPLE: ("It was then checked on the very records you were shown - there was no counterexample "
                "on the {n} questions it had not seen, so these are operands from the evidence you "
                "learned from."),
}


def build_repair_prompt(c: dict, counters: list, pool: list, provenance: str, n_check_q: int) -> str:
    rights = [r for r in pool if applies(c, r["features"])[0] and r["verdict"]["sourced"]]
    def table(rs):
        agg = collections.Counter(_vec(r["features"]) for r in rs)
        return json.dumps([{"features": {k: v for k, v in vec}, "n_operands": n}
                           for vec, n in agg.most_common()], indent=1, sort_keys=True)
    return _REPAIR.format(rendered=V.render(c), reason=c.get("reason", ""),
                          how_checked=_HOW_CHECKED[provenance].format(n=n_check_q),
                          n_counter=len(counters),
                          counter_table=table(counters), right_table=table(rights) or "  (none)")


def repair_call(c: dict, counters: list, pool: list, provenance: str, n_check_q: int,
                *, cap: int = 4000) -> dict:
    return _call(_SYSTEM, build_repair_prompt(c, counters, pool, provenance, n_check_q),
                 "ln_repair", cap)
