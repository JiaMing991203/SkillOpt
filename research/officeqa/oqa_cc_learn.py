#!/usr/bin/env python3
"""Select the permitted records, ask DeepSeek for a structured hypothesis, and compile it deterministically.

Amendment B-1. The division of labour is the point: the paid model PROPOSES a condition in a fixed
vocabulary, and deterministic code VALIDATES, SCORES and COMPILES it. No proposal is accepted on the
proposer's word, and a proposal that names a predicate outside the vocabulary, or mislabels a definition
as a discovery, is rejected by `oqa_cc_ir.validate` before it is scored.

THE RECORD SPLIT, mechanical and frozen before any learner call:
  * candidates are arm C's TRAINING items whose epoch-2 and epoch-3 traces both exist and whose epoch-2
    trace yields at least one table with a stated money scale and at least one operand the actor wrote;
  * item ids sorted ascending; the first four items' EPOCH-2 traces are the LEARNING records; the next
    four items' EPOCH-3 traces are the CHECKING records. Those four question ids therefore appear only
    in checking - more than the two required - and no checking record is from an epoch the learner saw.
  * all of it is saved TRAINING material. No validation and no held-out record is read.

WHAT THE PROPOSER IS SHOWN: extracted structure only - tables with their stated scale, cells with their
column path and row label, the units those headers and labels themselves state, and the conversion
factors the actor wrote down. Not the ground truth, not the skill, not a score.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_check as CHK  # noqa: E402
import oqa_cc_ir as IR  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402
import oqa_trace as TR  # noqa: E402

RULE = "OQA-CC-1"
N_LEARN, N_CHECK = 4, 4


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _packet(pred_dir: str, item: str, epoch: int):
    pk = TR.build_packet(pred_dir, item)
    pk["trace_key"] = f"{item}/e{epoch}"
    pk["trace_sha256"] = TR.trace_digest(pred_dir, item)
    pk["collection"] = os.path.abspath(pred_dir)
    pk["epoch"] = epoch
    return pk


def _usable(pk: dict) -> dict:
    """What makes a trace usable for this relationship, counted so the selection can be audited."""
    steps = pk.get("steps") or []
    supplied = pk.get("oracle_pages") or []
    tabs = CHK.tables_in(steps, supplied)
    scaled = [t for t in tabs if CELLS.canon_scale(t.get("scale") or "") in CHK.SCALE_EXP]
    ops = [o for s in steps if s.get("kind") == "message" for o in CHK.operands_in(s)]
    factors = [CHK.stated_factor_exp(str(s.get("text") or "")) for s in steps
               if s.get("kind") == "message"]
    return {"n_tables": len(tabs), "n_scaled_tables": len(scaled), "n_operands": len(ops),
            "n_stated_factors": sum(1 for f in factors if f is not None),
            "usable": bool(scaled and ops)}


def cmd_select(campaign: str, arm: str = "C", rule_version: int = 2) -> dict:
    """Pick the permitted records.

    RULE v1 (recorded, and its outcome is a finding rather than a failure): arm C only, items present in
    both epoch 2 and epoch 3, learning = the first four items' epoch-2 traces, checking = the next
    four's epoch-3 traces. It produced THREE usable traces of 24 and an EMPTY checking set, because the
    binding constraint is not tables - 14 of 24 had a scaled table - but OPERANDS: only 7 of 24 traces
    contain any number the actor wrote in a message, and NONE contains a stated conversion factor. That
    is Workstream A's finding arriving here: the actor's visible text never reached the record, so the
    "evidence use" this relationship is about is largely unobservable in these traces.

    RULE v2, used below: the same usability test over a WIDER pool - arms C and B, epochs 2 (learning)
    and 3 (checking) - which is still all saved TRAINING material. Learning and checking stay disjoint
    by epoch, and a checking item may not appear in the learning set, so its question id is reserved.
    """
    rows, by_epoch = [], {2: [], 3: []}
    arms = [arm] if rule_version == 1 else ["C", "B"]
    for a in arms:
        for epoch in ((2, 3) if rule_version != 1 else (2,)):
            pred = os.path.join(campaign, f"arm_{a}", "steps", f"step_{epoch:04d}", "rollout",
                                "predictions")
            if not os.path.isdir(pred):
                continue
            for item in sorted(os.listdir(pred)):
                pk = _packet(pred, item, epoch)
                u = _usable(pk)
                row = {"arm": a, "item": item, "epoch": epoch, "predictions": pred,
                       "trace_sha256": pk["trace_sha256"], **u}
                rows.append(row)
                if u["usable"]:
                    by_epoch[epoch].append(row)
    learn = sorted(by_epoch[2], key=lambda r: (r["arm"], r["item"]))[:N_LEARN]
    learn_items = {r["item"] for r in learn}
    check = [r for r in sorted(by_epoch[3], key=lambda r: (r["arm"], r["item"]))
             if r["item"] not in learn_items][:N_CHECK]
    doc = {
        "rule": RULE, "utc": _utc(), "campaign": campaign, "arm": arm,
        "rule_version": rule_version,
        "selection_rule": (
            f"v{rule_version}: TRAINING traces of arms {'/'.join(arms)}, usable = at least one table "
            "with a stated money scale AND at least one number the actor wrote in a message; ordered by "
            f"(arm, item); the first {N_LEARN} usable EPOCH-2 traces learn, the first {N_CHECK} usable "
            "EPOCH-3 traces whose item is not in the learning set check."),
        "rule_v1_outcome": ("3 usable of 24 and an EMPTY checking set; the binding constraint is "
                            "operands (7 of 24 traces contain any number the actor wrote, 0 contain a "
                            "stated conversion factor), not tables (14 of 24 have a scaled table)"),
        "n_candidates": len(rows), "n_usable": len(learn) + len(check),
        "candidates": rows,
        "learning": [{"arm": r["arm"], "item": r["item"], "epoch": r["epoch"],
                      "predictions": r["predictions"], "trace_sha256": r["trace_sha256"]}
                     for r in learn],
        "checking": [{"arm": r["arm"], "item": r["item"], "epoch": r["epoch"],
                      "predictions": r["predictions"], "trace_sha256": r["trace_sha256"]}
                     for r in check],
        "reserved_for_checking_only": sorted({r["item"] for r in check} - learn_items),
        "development_material": ("ALL OF IT IS REUSED DEVELOPMENT MATERIAL: these are the campaign's own "
                                 "training traces, already seen by the campaign's analyst and by the "
                                 "restricted learner. Nothing here is a fresh sample."),
    }
    doc["records_sha256"] = IR.sha(IR.canon(
        {"learning": doc["learning"], "checking": doc["checking"]}))
    return doc


# ───────────────────────── what the proposer is shown ─────────────────────────

def evidence_for(pk: dict) -> dict:
    steps = pk.get("steps") or []
    supplied = pk.get("oracle_pages") or []
    tabs = CHK.tables_in(steps, supplied)
    cells = CHK.cells_in(steps, supplied, ("flat_header_authoritative",))
    out_tabs, out_cells = [], []
    for i, t in enumerate(tabs[:8]):
        out_tabs.append({"table": i, "stated_scale": t.get("scale"),
                         "header_form": t.get("header_form"), "n_rows": len(t.get("rows") or []),
                         "origin": t.get("origin")})
    for c in cells[:40]:
        out_cells.append({"raw": c.get("raw"), "row_label": str(c.get("row_label"))[:60],
                          "column": str(c.get("column"))[:70], "table_scale": c.get("scale"),
                          "column_states_unit": CHK.column_unit(c),
                          "row_label_states_scale": CHK.row_unit(c),
                          "column_basis": c.get("column_basis")})
    facts = []
    for s in steps:
        if s.get("kind") != "message":
            continue
        e = CHK.stated_factor_exp(str(s.get("text") or ""))
        if e is not None:
            facts.append({"step": s.get("i"), "stated_factor_exp10": e,
                          "excerpt": str(s.get("text") or "")[:240]})
    m = re.search(r"\b(thousand|million|billion)s?\b", str(pk.get("question") or ""), re.I)
    return {"item": pk.get("id"), "trace_key": pk.get("trace_key"),
            "question_asks_scale": CELLS.canon_scale(m.group(1)) if m else None,
            "tables": out_tabs, "cells": out_cells, "actor_stated_factors": facts}


_PROMPT = """You are proposing ONE structured hypothesis for a contract checker. Answer with JSON only.

THE QUESTION TO ANSWER
Under what observable conditions does a table's stated scale annotation (for example "in millions of
dollars") apply to a particular numeric cell of that table, so that a value read from the cell carries
that scale? And what observation must WITHDRAW that binding?

YOU MAY ONLY USE THESE PREDICATES. Each one's provenance label is fixed and you must not change it.
{vocab}

RULES
- The conclusion is exactly: scale_binds(table, cell, scale).
- You must give at least one condition. An unconditional claim is useless here.
- Conditions are conjunctive. Use value=false for a condition that must NOT hold (a withdrawal).
- Do NOT propose a condition whose predicate is labelled MATH or GIVEN as if it were your discovery;
  you may still use them as conditions, with their own labels.
- Use only the entity names: table, header, cell, scale, exp10, value, calculation.

THE RECORDS. Extracted structure from {n} saved training attempts. `column_states_unit` and
`row_label_states_scale` are what the cell's own column header and row label say, which may differ from
the table's stated scale.
{records}

REPLY WITH THIS JSON SHAPE AND NOTHING ELSE:
{{"conclusion": {{"pred": "scale_binds", "args": ["table", "cell", "scale"], "value": true}},
  "conditions": [{{"pred": "...", "args": ["...", "..."], "value": true, "provenance": "..."}}],
  "reason": "one sentence on what in the records supports this",
  "withdrawal_reason": "one sentence on which observation withdraws the binding",
  "witnesses_obligation": "unit_match"}}
"""


def build_prompt(learning: list) -> str:
    vocab = "\n".join(
        f"  {p}({', '.join(s['args'])})   provenance {s['provenance']}"
        for p, s in sorted(IR.PREDICATES.items()))
    recs = json.dumps([evidence_for(pk) for pk in learning], indent=1, sort_keys=True)
    if len(recs) > 90_000:
        recs = recs[:90_000] + "\n... (truncated)"
    return _PROMPT.format(vocab=vocab, n=len(learning), records=recs)


def propose(learning: list, *, model: str | None = None) -> dict:
    """ONE paid call. The reply is parsed, never executed, and validated before it is scored."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.model import chat_optimizer
    prompt = build_prompt(learning)
    system = ("You propose structured hypotheses for a contract checker. Reply with one JSON object and "
              "nothing else. Never invent a predicate. Never relabel a provenance.")
    # ROOM FOR THE REASONING PHASE. The first successful call (job 66341547) returned
    # finish_reason "length", content "" and 8,788 characters of `reasoning_content`: deepseek-flash
    # spent the whole 2,000-token cap reasoning and never reached the JSON. That is the same shape as
    # the actor failure Workstream A found, on a different provider and with a different field name -
    # EmpirioLabs uses `reasoning`, DeepSeek uses `reasoning_content`. The cap is now the campaign's.
    cap = int(os.environ.get("OQA_CC_MAX_COMPLETION_TOKENS", "16000"))
    text, usage = chat_optimizer(system, prompt, max_completion_tokens=cap, retries=1,
                                 stage="cc_propose")
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
    return {"prompt_sha256": IR.sha(system + prompt), "prompt_chars": len(prompt) + len(system),
            "max_completion_tokens": cap,
            "reply_chars": len(raw), "reply_prefix": raw[:1500], "usage": usage,
            "parsed": parsed, "problem": problem,
            "model": model or os.environ.get("OPTIMIZER_MODEL") or "deepseek-flash"}


def compile_proposal(parsed: dict, learning: list, *, cid: str = "scale.binds") -> dict:
    """Validate, score over the LEARNING records only, and compile. Deterministic throughout."""
    conc = parsed.get("conclusion") or {}
    conclusion = IR.clause(conc.get("pred", ""), conc.get("args") or [], conc.get("value", True))
    conds = []
    for cl in parsed.get("conditions") or []:
        conds.append(IR.clause(cl.get("pred", ""), cl.get("args") or [], cl.get("value", True)))
    c = IR.contract(cid, conclusion, conds, witnesses=parsed.get("witnesses_obligation"),
                    reason=str(parsed.get("reason") or "")[:400], source="deepseek+deterministic")
    problems = IR.validate(c)
    score = {"HOLDS": 0, "UNKNOWN": 0, "CONFLICT": 0, "cells": 0}
    if not problems:
        for pk in learning:
            steps, supplied = pk.get("steps") or [], pk.get("oracle_pages") or []
            tabs = CHK.tables_in(steps, supplied)
            cells = CHK.cells_in(steps, supplied, ("flat_header_authoritative",))
            for cell in cells[:60]:
                table = next((t for t in tabs
                              if CELLS.canon_scale(t.get("scale") or "")
                              == CELLS.canon_scale(cell.get("scale") or "")), None)
                if table is None:
                    continue
                score["cells"] += 1
                score[CHK.evaluate_binding(c, table, cell)["truth"]] += 1
    return {"contract": c, "problems": problems, "score": score,
            "withdrawal_reason": str(parsed.get("withdrawal_reason") or "")[:300]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--campaign", required=True)
    s.add_argument("--arm", default="C")
    s.add_argument("--rule-version", type=int, default=2)
    s.add_argument("--out", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--records", required=True)
    p.add_argument("--out", required=True)
    q = sub.add_parser("prompt")
    q.add_argument("--records", required=True)
    q.add_argument("--out", default="")
    args = ap.parse_args()

    if args.cmd == "select":
        doc = cmd_select(args.campaign, args.arm, args.rule_version)
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "CC_RECORDS.json" if args.rule_version == 1
                            else f"CC_RECORDS_v{args.rule_version}.json")
        if os.path.exists(path):
            old = json.load(open(path, encoding="utf-8"))
            if old.get("records_sha256") != doc["records_sha256"]:
                raise RuntimeError(f"{path} holds a different frozen split; refusing to overwrite")
            print(f"split unchanged: {old['records_sha256'][:16]}")
            return 0
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        print(f"rule {RULE}  {doc['n_usable']} usable of {doc['n_candidates']} candidate item(s)")
        print(f"  learning (epoch 2): {[(r['arm'], r['item']) for r in doc['learning']]}")
        print(f"  checking  (epoch 3): {[(r['arm'], r['item']) for r in doc['checking']]}")
        print(f"  reserved for checking only: {doc['reserved_for_checking_only']}")
        print(f"frozen: {path}   records {doc['records_sha256'][:16]}")
        return 0

    rec = json.load(open(args.records, encoding="utf-8"))
    learning = [_packet(r["predictions"], r["item"], r["epoch"]) for r in rec["learning"]]
    if args.cmd == "prompt":
        text = build_prompt(learning)
        print(f"prompt chars {len(text)}   sha {IR.sha(text)[:16]}")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"wrote {args.out}")
        return 0

    out = propose(learning)
    comp = compile_proposal(out.get("parsed") or {}, learning) if out.get("parsed") else None
    doc = {"rule": RULE, "utc": _utc(), "records_sha256": rec.get("records_sha256"),
           "proposal": out, "compiled": comp}
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "PROPOSAL.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True, default=str)
    print(f"rule {RULE}  reply {out['reply_chars']} chars  usage {out['usage']}")
    if out["problem"]:
        print(f"  PROBLEM: {out['problem']}")
    if comp:
        print(f"  validate: {comp['problems'] or 'OK'}")
        print(f"  score over the learning records: {comp['score']}")
        print(IR.render(comp["contract"]))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
