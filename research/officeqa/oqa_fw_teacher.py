#!/usr/bin/env python3
"""The DeepSeek LEARNER for the ported framework: it proposes hypotheses, code decides. Rule OQA-FW-1.

WHY THIS FILE EXISTS, AND WHAT IT REPLACES. `oqa_fw_learn.SearchProposer` enumerates every hypothesis the
declared vocabulary allows for ONE chosen consequence atom and keeps what the gate accepts. That is a real
learner over a finite space, and it is also a researcher's choice of target wearing a learner's clothes: I
picked the consequence. The campaign learner is the provider, exactly as in the ALFWorld studies this
framework is ported from - it reads the records and the code-computed atom values and proposes whatever
relation it thinks the records support, over ANY atom it is allowed to conclude. The search proposer
remains available as a DIAGNOSTIC and every artifact labels which proposer produced which operation; it is
never substituted for the campaign learner.

THE DIVISION OF LABOUR IS THE WHOLE POINT, and it is the same one `upstream/vx1` uses:

    the provider PROPOSES          one JSON object of contract and assumption operations
    code VALIDATES                 schema, vocabulary, typed arguments, no circularity   (fw_ir.validate)
    code SCORES                    support / counterexamples over ALL permitted evidence  (fw_check.score)
    code GATES                     zero determinate counterexamples, >= 1 support, plus
                                   the regression check on assumptions                    (fw_learn.gate)
    code REPAIRS                   drop_conjunct / narrow / restrict_scope / retire        (fw_repair)

Nothing the provider says is believed. A proposal is a candidate; the artifact records the gate's reason
for every rejection, and the rejections are fed back so the learner can narrow - which is how a refuted
general form becomes a supported specific one by evidence rather than by my editing.

PROMPT PROVENANCE. The shape is `upstream/vx1/vx_prompt.py`: shared head, vocabulary-specific block, gate
block, output block, then a user message of (current model, new records, code-computed values, earlier
records, gate feedback). The CONTENT is OfficeQA's own - documents, tables, headers, cells, units, values,
calculations - and the blocks are digested so a later run can prove which prompt was sent.

WHAT THE LEARNER MAY SEE: the permitted analyst view only. The question text, the agent's tool calls, the
observations it received, and the atom values code computes from those observations. NOT the gold answer,
NOT any score, NOT the skill document, NOT the validation or held-out material. `leak_check` refuses to
send a request that contains any of those markers, and it runs on every message before the socket opens.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import fw_ir as IR  # noqa: E402
from fw_domain import sha  # noqa: E402

MAX_ROWS_PER_TYPE = 24          # named rows per entity type per record, first in reference order
MAX_STEPS = 40                  # steps shown per record
OBS_CHARS = 180                 # observation digest per step
MAX_RECORD_CHARS = 9_000        # per record, after which the record text is cut with a marker
MAX_USER_CHARS = 150_000        # the whole user message; the limit guard is in skillopt, this is ours


# ── the system prompt, in blocks, each digested ──────────────────────────────────────────────────────
HEAD = """You maintain a small, versioned model of DOCUMENT-INTERPRETATION CONTRACTS for an offline
table-question-answering environment. You learn it only from the records you are given. You have no other
knowledge source about these documents: do not rely on what you believe about financial statements or
about how tables are usually laid out; rely on the records. You may abstain - an unchanged model, or a
relation left unknown, is a valid answer when the records do not decide a question.

## The environment
An automated agent answers a question about a collection of parsed documents. It has four tools:
  read F        read a document (or a page range of one); the observation is the rendered text
  grep P        search the collection for a pattern; the observation is the matching lines
  glob P        list documents matching a pattern
  answer        commit a final answer, with its working
A question asks for a figure, often with a unit ("in millions", "in thousands"). The agent must find the
right table, the right column and the right row, read the cell, and convert the figure if the scale the
table states differs from the scale the question asks for.

## Records
Each record gives the question, and for each step the tool call the agent issued and a digest of the
observation it received. Step indices are the record's own. Record identifiers are arbitrary labels of
the form <epoch>/<question-id>; the same question id can recur in different epochs as a DIFFERENT record.
Nothing in a record tells you whether the agent's answer was right, and you are not asked to judge it.

## Entities, named by stable observation references
Code extracts the document structure each observation introduced and names it. These names are what you
cite and what your rules quantify over:
  table    tb:s12:1      the 2nd table of the observation at step 12
  column   co:s12:1:3    its 4th column (indices are 0-based)
  cell     ce:s12:1:4:3  the data cell at row 5, column 4
  operand  op:s13:2      the 3rd number the agent wrote in its message at step 13
  scale    sc:thousands  one of units / thousands / millions / billions
A reference is local to its record; cite a witness as "<record id> <reference>".
"""

GATE = """
## Directions, and what counts as a counterexample
"concludes" is the kind to reach for first, and the only kind whose arguments are entities.
  concludes   For EVERY entity bound to each subject variable, in the scope the rule declares, the
              consequence holds exactly when the conditions hold. Conditions true and consequence FALSE
              is a counterexample. With "direction": "iff", conditions false and consequence TRUE is also
              a counterexample. Unknown on either side is neither support nor counterexample.
  requires    A SUCCESS of the verb implies the condition held before it (a success with the condition
              determinately false is a counterexample).
  suffices    The condition holding before implies the command succeeds (a failure with it true is a
              counterexample).
  effect      After a successful command, each effect literal holds (a determinately false one is a
              counterexample).
Note that this vocabulary's atoms take TABLES, COLUMNS, CELLS, OPERANDS and SCALES as arguments, not tool
arguments, so a requires/suffices/effect rule can only speak about entities through "exists" variables.
A concludes rule is usually the honest shape for a claim about document structure.

## How your proposals are checked - by code, not by you
Every added or revised item is scored against ALL records seen so far, not only the new ones. It is
REJECTED if it has ANY determinate counterexample, or if it has no supporting case. Unknown values never
count as support or as a counterexample, so a rule that is merely never evaluable is rejected for lack of
support rather than accepted as vacuously true. A contract that still has a counterexample after the
update becomes "refuted" with the counterexample recorded; nothing is deleted. Adopting or dropping an
assumption is refused if the records contradict it directly, or if it would create counterexamples for
active contracts you do not revise in the same proposal.

## Two things that make a rule worthless even when the gate accepts it
1. CIRCULARITY. A rule may not use its own consequence atom in its conditions, and may not mention any
   atom marked LEARNED-DEPENDENT anywhere: those atoms are true only THROUGH an accepted rule, so using
   one as a condition makes the rule argue from its own conclusion. Such a proposal is rejected.
2. REDUNDANCY. If supplied code already computes your consequence from the same inputs your conditions
   read, the rule can only agree with it; it adds no conclusion and earns no place in the model. Each
   atom below is marked with where its value comes from. Prefer a consequence the code reads from an
   OBSERVATION over one the code DERIVES, and say in your reason what the rule lets the system conclude
   that it could not conclude without it.
"""

OUTPUT = """
## What to do
Read the new records, the current model and its evidence, the code-computed atom values, and any gate
feedback. Then propose operations:
- "add" an item the records support; "revise" an existing item (keep its kind and its consequence atom)
  when a counterexample shows it is wrong or too strong, saying what the counterexample shows;
  "retire" an item that no longer helps; "keep" (or nothing at all) when the evidence calls for no change.
- "adopt" / "drop" an assumption, with the reason.
- Do NOT force changes. Prefer fewer, better-supported items. Propose the MOST GENERAL form you believe
  the records support: if it is wrong the gate will name the counterexamples and you can narrow it, and a
  rule narrowed in response to a named counterexample is worth more than a cautious guess.
- If the vocabulary cannot express what you see, say so in "uncovered" instead of writing an approximate
  item.
- Cite witnesses as "<record id> <reference>", e.g. "e1/UID0014 co:s12:1:3".

## Output — read this before you start thinking
BEGIN YOUR REPLY WITH THE ```json BLOCK. Write nothing before it. Keep your reasoning short and put
whatever of it matters into the "reason" fields and into "notes" - a few sentences, not an essay.

This is not a style preference. The completion budget is a few thousand tokens and it covers your
reasoning AND your reply: a first attempt at this task spent the entire budget on analysis, was cut
off mid-sentence, and returned no content at all, which counts as nothing proposed. A short, decided
answer with one well-supported operation is worth more than a long deliberation that never lands.
If the records do not settle a question, say so in "uncovered" and move on.

Return ONE JSON object inside a ```json fenced block, with exactly these keys:
{"assumption_ops": [{"op": "adopt"|"drop", "name": "...", "reason": "..."}],
 "contract_ops": [{"op": "add", "contract": {...}, "reason": "...", "witnesses": ["..."]},
                  {"op": "revise", "target": "<id>", "contract": {...}, "reason": "...",
                   "witnesses": ["..."]},
                  {"op": "retire" | "keep", "target": "<id>", "reason": "..."}],
 "uncovered": ["<what you could not model>"],
 "notes": "<short>"}
"""


def vocab_block(dom) -> str:
    """The vocabulary-specific block, generated from the domain so prompt and code cannot drift."""
    lines = ["## Atom vocabulary [VOCAB-SPECIFIC]",
             "Every value is true, false or unknown, computed by code from the records. The mark in",
             "brackets says where the value comes from: OBS = read off an observation; MACHINERY =",
             "derived by supplied code from observations and assumptions; MATH = derived from the",
             "question's own arithmetic requirement; LEARNED-DEPENDENT = true only through an accepted",
             "learned rule, and therefore forbidden in your conditions.",
             "A value is computed over the PREFIX: at a given step it uses only what the record has shown",
             "up to that step, so a later observation cannot justify an earlier judgement.", ""]
    for atom in sorted(dom.atoms):
        kinds, cat, why = dom.atoms[atom]
        args = ", ".join(k if k == "TYPE" else f"<{k}>" for k in kinds)
        lines.append(f"  {atom}({args})   [{cat}]")
        for seg in _wrap(why, 96):
            lines.append(f"      {seg}")
    lines += ["", "## Model assumptions (adopt or drop; each changes how values are computed, and every",
              "## value records which assumptions it rested on) [VOCAB-SPECIFIC]"]
    for name in sorted(dom.assumptions):
        reading = dom.assumptions[name]
        txt = reading if isinstance(reading, str) else (reading[0] if reading else name)
        lines.append(f"  {name}")
        for seg in _wrap(str(txt), 96):
            lines.append(f"      {seg}")
    lines += ["", "## Entity types: " + ", ".join(dom.types),
              "## Kinds available: " + ", ".join(dom.kinds), "",
              "## Contract format (JSON) [VOCAB-SPECIFIC]",
              '{"kind": "concludes", "scope": "any" | "<verb>",',
              '  "subject": {"?s": "<entity type>"},            // the rule is a claim about EACH such entity',
              '  "subject_scope": "all" | "introduced" | "once",',
              '  "conditions": [[<lit>, ...], ...],             // CNF: AND of clauses, each clause an OR',
              '  "consequence": <lit>,',
              '  "direction": "implies" | "iff",                // default "implies"',
              '  "exists": {"?t": "<entity type>"},             // optional: "for some such entity"',
              '  "guard": [[<lit>, ...], ...],                  // optional: must hold or the rule is silent',
              '  "context": {"task_types": []}}                 // [] = every question',
              '{"kind": "requires" | "suffices", "verb": "<verb>", "condition": [[<lit>, ...], ...],',
              '  "exists": {...}, "context": {...}}',
              '{"kind": "effect", "verb": "<verb>", "effects": [<lit>, ...], "guard": [[...]], "exists": {...}}',
              '<lit> = {"atom": "<atom>", "args": ["?s" | "?t" | "<role>"], "value": true | false}',
              "",
              'ARGUMENTS ARE TYPED. An atom position that takes a <column> admits only a variable declared',
              'as a column. At most ' + str(getattr(dom, "max_vars", 3)) + ' variables in "subject" and the same in "exists".',
              "",
              'subject_scope, which matters here more than it looks:',
              '  "all"         re-check the rule at every step of the record',
              '  "introduced"  check each entity only at the step whose observation introduced it',
              '  "once"        check each entity ONCE, at the first step where the record settles both',
              '                sides of the rule. Use this when the evidence that decides the consequence',
              '                can arrive much later in the record than the entity itself - which is the',
              '                normal case for a document re-read under a different rendering.']
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list:
    words, out, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out or [""]


def system_prompt(dom) -> str:
    return HEAD + "\n" + vocab_block(dom) + "\n" + GATE + OUTPUT


def prompt_digest(dom) -> dict:
    return {"head_sha": sha(HEAD), "vocab_sha": sha(vocab_block(dom)), "gate_sha": sha(GATE),
            "output_sha": sha(OUTPUT), "system_sha": sha(system_prompt(dom)),
            "domain_version": getattr(dom, "version", None)}


# ── the leak check: what a learner request may never contain ─────────────────────────────────────────
FORBIDDEN = [
    "ground_truth", "gold_answer", "\"gold\"", "correct answer is", "the right answer",
    "hard_score", "soft_score", "\"hard\":", "\"soft\":", "eval_summary",
    "## Skill Knowledge", "skillopt/envs/officeqa/skills", "initial.md", "candidate_skill",
    "valid_unseen", "held-out", "heldout", "OFFICEQA_FROZEN_SETS",
    "SUPPORTED", "operand_sourced", "scale_binds",
]
FORBIDDEN_RE = [re.compile(r"\bthe answer is\b", re.I), re.compile(r"\bscore[d]?\s*[:=]\s*[\d.]")]


class LeakError(RuntimeError):
    pass


def leak_check(messages: list) -> None:
    for m in messages:
        txt = str(m.get("content") or "")
        for f in FORBIDDEN:
            if f in txt:
                raise LeakError(f"learner request contains forbidden material {f!r}: nothing sent")
        for rx in FORBIDDEN_RE:
            mo = rx.search(txt)
            if mo:
                raise LeakError(f"learner request matches forbidden pattern {rx.pattern!r} "
                                f"at {mo.group(0)!r}: nothing sent")


# ── the user message ────────────────────────────────────────────────────────────────────────────────
_PATH = re.compile(r"path\s*=\s*'([^']*)'")
_PAT = re.compile(r"pattern\s*=\s*'([^']*)'")


def _cmd_line(st: dict) -> str:
    raw = str(st.get("cmd") or "").strip()
    if not raw:
        return f"({st.get('kind') or 'message'})"
    return raw[:160]


def _obs_digest(st: dict) -> str:
    obs = str(st.get("obs") or st.get("text") or "")
    if not obs:
        return "(no observation)"
    flat = " ".join(obs.split())
    head = flat[:OBS_CHARS]
    extra = f" [... {len(obs)} chars]" if len(obs) > OBS_CHARS else ""
    return head + extra


def record_text(rec: dict, est) -> str:
    n = len(rec["steps"])
    ents = rec.get("entities") or {}
    parts = [f"### Record {rec['episode_id']}"
             + (f"   (the question asks for: {rec['asked_scale']})" if rec.get("asked_scale") else ""),
             f"Question: {str(rec.get('task') or '').strip()[:600]}",
             f"Steps: {n}.  Entities extracted: "
             + ", ".join(f"{len(ents.get(k) or {})} {k}" for k in ("tables", "columns", "cells", "operands"))]
    shown = rec["steps"][:MAX_STEPS]
    for st in shown:
        parts.append(f"[{st['i']}] {_cmd_line(st)}")
        parts.append(f"      -> {_obs_digest(st)}")
    if n > len(shown):
        parts.append(f"      (... {n - len(shown)} further steps not shown)")
    txt = "\n".join(parts)
    if len(txt) > MAX_RECORD_CHARS:
        txt = txt[:MAX_RECORD_CHARS] + "\n      (record text cut at the per-record character budget)"
    return txt


_TYPE_KEY = {"table": "tables", "column": "columns", "cell": "cells", "operand": "operands"}


def _atoms_of_type(dom, ty: str) -> list:
    """Unary atoms whose single argument is this entity type - the ones a row can show."""
    return sorted(a for a, (kinds, _c, _w) in dom.atoms.items()
                  if len(kinds) == 1 and kinds[0] == ty)


_SYM = {True: "T", False: "F", None: "?"}


def value_tables(recs: list, ev, model: dict, dom) -> str:
    """The code-computed atom values: a census over every entity, then named rows.

    This is the OfficeQA analogue of the per-step state vocabulary the ALFWorld learner is shown. It is
    the learner's whole view of what the records decide: without it the provider would be guessing at
    document structure from truncated observation text.
    """
    A, P = model["assumptions"], IR.active(model, "persists")
    census: dict = {}
    rows: list = []
    for rec in recs:
        ep = next((e for e in ev.episodes if e["episode_id"] == rec["episode_id"]), None)
        if ep is None:
            continue
        est = ev.est(ep, A, P)
        n = len(rec["steps"])
        for ty in dom.types:
            key = _TYPE_KEY.get(ty)
            if not key:
                continue
            atoms = _atoms_of_type(dom, ty)
            if not atoms:
                continue
            refs = sorted((rec.get("entities") or {}).get(key) or {})
            for i, ref in enumerate(refs):
                vals = {}
                for a in atoms:
                    try:
                        vals[a] = est.value(n, a, [ref])["v"]
                    except Exception:                      # noqa: BLE001 - a value we cannot compute
                        vals[a] = None
                for a, v in vals.items():
                    c = census.setdefault(a, {"T": 0, "F": 0, "?": 0})
                    c[_SYM[v]] += 1
                if i < MAX_ROWS_PER_TYPE:
                    rows.append(f"  {rec['episode_id']} {ref}  "
                                + " ".join(f"{a}={_SYM[vals[a]]}" for a in atoms))
    out = ["## Atom values computed by code over these records",
           "Census over EVERY entity of each atom's type, read at the end of its record "
           "(T = true, F = false, ? = unknown):", "",
           f"  {'atom':<26} {'true':>7} {'false':>7} {'unknown':>8}"]
    for a in sorted(census):
        c = census[a]
        out.append(f"  {a:<26} {c['T']:>7} {c['F']:>7} {c['?']:>8}")
    out += ["", f"Named rows (the first {MAX_ROWS_PER_TYPE} entities of each type in each record, in",
            "reference order - a deterministic sample, not a selection of interesting cases):", ""]
    out += rows
    return "\n".join(out)


def model_text(model: dict, dom) -> str:
    rows = []
    for c in model.get("contracts") or []:
        if c.get("status") not in ("active", "refuted"):
            continue
        e = c.get("evidence") or {}
        body = IR.normalize(c)
        rows.append(f"- {c['id']} v{c.get('version', 1)} [{c['status']}] {IR.render(c, dom)}\n"
                    f"  json: {json.dumps(body, sort_keys=True)}\n"
                    f"  evidence so far: {e.get('n_support', 0)} supporting, "
                    f"{e.get('n_consistent', 0)} consistent, {e.get('n_counter', 0)} counterexamples"
                    + (f" {e.get('counter', [])[:4]}" if e.get("n_counter") else "")
                    + f", {e.get('undetermined', 0)} undetermined")
    head = (f"Frozen model before this batch: version {model['version']}, assumptions "
            f"{model.get('assumptions') or 'none'}.")
    return head + "\n" + ("\n".join(rows) or "(no items yet: the model is empty)")


def build_messages(model: dict, recs: list, ev, dom, *, batch: int, n_batches: int,
                   earlier: list = ()) -> list:
    parts = [f"Batch {batch} of at most {n_batches}.", "", "## Current model",
             model_text(model, dom), "", "## New records (this batch)", ""]
    for rec in recs:
        parts += [record_text(rec, None), ""]
    parts += [value_tables(recs, ev, model, dom), ""]
    if earlier:
        parts += ["## Earlier records already in the evidence (the code rechecks every proposal against",
                  "## them as well, so a rule that fits only this batch will be rejected)",
                  "  " + ", ".join(str(x) for x in earlier), ""]
    parts += ["Propose operations, or none, following the system instructions. Return one JSON object in "
              "a ```json block."]
    user = "\n".join(parts)
    if len(user) > MAX_USER_CHARS:
        user = user[:MAX_USER_CHARS] + "\n(message cut at the character budget)"
    msgs = [{"role": "system", "content": system_prompt(dom)}, {"role": "user", "content": user}]
    leak_check(msgs)
    return msgs


def retry_messages(prev: list, rec: dict) -> list:
    """A SHORT corrective round after a reply that produced no JSON object.

    The first campaign attempt (job 66666916) returned `finish_reason: "length"`, empty content and
    31,393 characters of `reasoning_content`: the model reasoned until the completion budget ran out
    and never wrote the block. That is an operational failure, not an abstention, and the right
    response is one more round with the instruction sharpened - not a larger prompt, and not a quieter
    record.
    """
    usage = rec.get("usage") or {}
    truncated = str(rec.get("finish_reason") or "") == "length" or not str(rec.get("reply") or "").strip()
    lines = [f"Your previous reply produced no JSON object"
             + (f": {rec.get('problem')}" if rec.get("problem") else ".")]
    if truncated:
        lines.append(f"It used its whole completion budget "
                     f"({usage.get('completion_tokens', '?')} tokens) before writing any content, so "
                     f"it was cut off and nothing was proposed.")
    lines += ["",
              "Reply NOW with ONLY the ```json block. Put nothing before it. Do not restate the "
              "evidence, do not weigh alternatives in prose, and keep every \"reason\" to one or two "
              "sentences.",
              "",
              "An empty proposal is a legitimate answer and costs nothing:",
              '```json',
              '{"assumption_ops": [], "contract_ops": [], "uncovered": ["..."], "notes": "..."}',
              '```',
              "Send either that or your best-supported operation, in that format, immediately."]
    msgs = list(prev) + [{"role": "assistant",
                          "content": str(rec.get("reply") or "")[:4000]
                                     or "(no content was returned: the reply was cut off)"},
                         {"role": "user", "content": "\n".join(lines)}]
    leak_check(msgs)
    return msgs


def feedback_messages(prev: list, reply: str, decisions: list) -> list:
    bad = [d for d in decisions if d.get("decision") in ("rejected", "refuted")]
    lines = ["The code checked your proposal against every record seen so far."]
    if bad:
        lines.append("These operations did NOT pass:")
        for d in bad:
            lines.append(f"- {d.get('type')} {d.get('op')} "
                         f"{d.get('target') or d.get('name') or ''}: "
                         f"{'; '.join(str(w) for w in (d.get('why') or []))}")
            ev = d.get("evidence") or {}
            if ev:
                lines.append(f"    evidence: {ev.get('n_support', 0)} supporting, "
                             f"{ev.get('n_counter', 0)} counterexamples "
                             f"{list(ev.get('counter') or [])[:4]}, "
                             f"{ev.get('undetermined', 0)} undetermined")
                wit = ev.get("counter_witness") or {}
                for k in list(wit)[:3]:
                    lines.append(f"    counterexample {k}: {json.dumps(wit[k], sort_keys=True)[:300]}")
    else:
        lines.append("Nothing was rejected.")
    lines += ["", "Accepted operations are already applied; do not repeat them. You may send corrected",
              "versions of the rejected operations, or an empty proposal if the records do not support a",
              "narrower form. Same JSON format."]
    msgs = list(prev) + [{"role": "assistant", "content": reply or ""},
                         {"role": "user", "content": "\n".join(lines)}]
    leak_check(msgs)
    return msgs


# ── reply parsing and structural validation ─────────────────────────────────────────────────────────
def parse_reply(text: str) -> dict:
    if not text:
        raise ValueError("empty reply")
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    cand = m[-1] if m else None
    if cand is None:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("no JSON object in the reply")
        cand = text[i:j + 1]
    d = json.loads(cand)
    if not isinstance(d, dict):
        raise ValueError("reply JSON is not an object")
    out = {"assumption_ops": d.get("assumption_ops") or [], "contract_ops": d.get("contract_ops") or [],
           "uncovered": d.get("uncovered") or [], "notes": d.get("notes") or ""}
    for k in ("assumption_ops", "contract_ops"):
        if not isinstance(out[k], list):
            raise ValueError(f"{k} must be a list")
    return out


def circularity(c: dict, dom) -> list:
    """The one check `fw_ir.validate` cannot make: a rule arguing from its own conclusion.

    Rejected here rather than in the gate because it is a schema fault, not an evidence fault: a rule
    that conditions on an atom only an accepted rule can make true would be scored against values its own
    acceptance produced.
    """
    if c.get("kind") != "concludes":
        cnf = c.get("condition") or c.get("guard") or []
        lits = [l for cl in cnf for l in cl] + list(c.get("effects") or [])
        cons_atom = None
    else:
        cnf = list(c.get("conditions") or []) + list(c.get("guard") or [])
        lits = [l for cl in cnf for l in cl]
        cons_atom = (c.get("consequence") or {}).get("atom")
    out = []
    for l in lits:
        a = (l or {}).get("atom")
        if a == cons_atom:
            out.append(f"the condition mentions {a!r}, which is the rule's own consequence")
        if a in dom.atoms and dom.atoms[a][1] == "LEARNED-DEPENDENT":
            out.append(f"the condition mentions {a!r}, which is LEARNED-DEPENDENT: its value exists only "
                       f"through an accepted learned rule, so conditioning on it is circular")
    return out


def compile_ops(parsed: dict, dom) -> dict:
    """Validate every proposed operation structurally. Returns the clean proposal and the refusals.

    A malformed contract is NOT silently dropped: it is returned in `refused` with the validator's own
    message, so the feedback round can tell the learner exactly what was wrong, and the artifact records
    that the learner proposed something rather than that it abstained.
    """
    ops, refused = [], []
    for op in parsed.get("contract_ops") or []:
        if not isinstance(op, dict):
            refused.append({"op": None, "why": [f"not an object: {str(op)[:120]!r}"]})
            continue
        kind = op.get("op")
        if kind in ("retire", "keep"):
            if not op.get("target"):
                refused.append({"op": kind, "why": ["needs a target contract id"]})
            else:
                ops.append(op)
            continue
        if kind not in ("add", "revise"):
            refused.append({"op": kind, "why": [f"unknown operation {kind!r}"]})
            continue
        c = op.get("contract")
        if not isinstance(c, dict):
            refused.append({"op": kind, "why": ["\"contract\" must be an object"]})
            continue
        errs = IR.validate(c, dom) + circularity(c, dom)
        if errs:
            refused.append({"op": kind, "target": op.get("target"), "contract": c, "why": errs})
            continue
        ops.append(op)
    aops = []
    for op in parsed.get("assumption_ops") or []:
        if not isinstance(op, dict) or op.get("op") not in ("adopt", "drop") or not op.get("name"):
            refused.append({"op": "assumption", "why": [f"malformed assumption op {str(op)[:120]!r}"]})
        elif op["name"] not in dom.assumptions:
            refused.append({"op": "assumption", "why": [f"unknown assumption {op['name']!r} "
                                                        f"(menu: {sorted(dom.assumptions)})"]})
        else:
            aops.append(op)
    return {"contract_ops": ops, "assumption_ops": aops, "refused": refused,
            "uncovered": parsed.get("uncovered") or [], "notes": parsed.get("notes") or ""}


# ── the paid call ───────────────────────────────────────────────────────────────────────────────────
def _utc() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


CONFIG = "configs/research/officeqa_base2.yaml"
_ROUTE_CHECKED: dict = {}


def configure(config_path: str = CONFIG, optimizer_model: str = "") -> dict:
    """Point the model layer at DeepSeek, and PROVE the dispatch path, before anything is sent.

    THIS IS THE THIRD TIME THIS PROJECT HAS PAID FOR THE SAME LESSON, so it is enforced here rather
    than documented. `skillopt.model.chat_optimizer*` is a ROUTER. A config file does not reach it: the
    trainer calls `set_optimizer_backend` and `configure_openai_compatible`, and a module that calls
    the router directly must do the same or dispatch to whatever `OPTIMIZER_BACKEND` happens to say -
    which defaults to `openai_chat`, not to this project's endpoint. Job 66271190 sent a perfect
    openai-compatible config to Azure and recorded `no_content`; job 66552741 raised "Azure OpenAI
    endpoint is not configured for optimizer" 48 times after a dry run reported everything healthy.

    And CONFIGURED IS NOT EFFECTIVE. `SKILLOPT_REQUIRE_ROUTE` is only honoured where something calls
    the guard, and the only caller is `skillopt/engine/trainer.py` - so a learner that runs outside the
    trainer had the variable set and the guard inert. `verify_or_die` is therefore called here, in the
    process that will make the call, which is the only place it can mean anything.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    if "oqa_t2s" not in sys.modules:
        sys.path.insert(0, HERE)
    import oqa_t2s as T2S  # noqa: PLC0415
    # The config is named relative to the tree root, and the learner may be run from anywhere.
    cfg_path = ""
    for cand in (config_path, os.path.join(root, config_path)):
        if cand and os.path.isfile(cand):
            cfg_path = cand
            break
    if not cfg_path:
        raise RuntimeError(f"cannot find {config_path!r} from {os.getcwd()!r} or {root!r}. Without it "
                           f"the base URL, model and token cap would come from whatever the "
                           f"environment happens to hold, which is the misdispatch this guards.")
    effective = T2S.configure_models(cfg_path, optimizer_model or os.environ.get("OPTIMIZER_MODEL", ""))
    from skillopt.model.backend_config import get_optimizer_backend  # noqa: PLC0415
    backend = get_optimizer_backend()
    if backend != "openai_compatible":
        raise RuntimeError(f"the optimizer backend resolved to {backend!r}, not 'openai_compatible'. "
                           f"Refusing to send: this is the router misdispatch of job 66271190.")
    route = {"backend": backend, "config": cfg_path or "(none found; env only)",
             "effective": effective}
    if os.environ.get("SKILLOPT_REQUIRE_ROUTE", "").strip() and not _ROUTE_CHECKED:
        from skillopt.model.route_guard import verify_or_die  # noqa: PLC0415
        _ROUTE_CHECKED["guard"] = verify_or_die()
        route["route_guard"] = _ROUTE_CHECKED["guard"]
    else:
        route["route_guard"] = _ROUTE_CHECKED.get("guard") or (
            "SKILLOPT_REQUIRE_ROUTE is not set, so the guard was not run: the backend assertion above "
            "is the only pin")
    return route


def call(messages: list, *, stage: str, cap: int = 8000, retries: int = 2,
         config_path: str = CONFIG) -> dict:
    """ONE paid learner attempt through the project's own optimizer route, with the ledger in force.

    `retries` IS the number of attempts, not the number of extra tries: the backend loop is
    `for attempt in range(retries)`, so 0 sends nothing (job 66580895 learned that at the cost of a
    stage). The ledger counts each attempt.
    """
    leak_check(messages)
    route = configure(config_path)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skillopt.model import chat_optimizer_messages
    text, usage = chat_optimizer_messages(messages, max_completion_tokens=cap, retries=retries,
                                          stage=stage)
    raw = str(text or "")
    finish = (usage or {}).get("finish_reason") or ""
    parsed, problem = None, ""
    try:
        parsed = parse_reply(raw)
    except Exception as exc:                               # noqa: BLE001 - recorded, not raised
        problem = f"{type(exc).__name__}: {exc}"
    return {"stage": stage, "utc": _utc(), "n_messages": len(messages), "route": route,
            "finish_reason": finish,
            "prompt_sha256": sha("".join(m["content"] for m in messages)),
            "prompt_chars": sum(len(m["content"]) for m in messages),
            "max_completion_tokens": cap, "reply": raw, "reply_chars": len(raw),
            "usage": usage, "parsed": parsed, "problem": problem}
