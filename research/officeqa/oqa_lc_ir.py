"""Structured action contracts for OfficeQA: schema, deterministic validation, Kleene evaluation, models, freezing.

Port of the ALFWorld prototype's `ir.py` (transferred reference `transferred/ir.py`, source sha256
`7bf531504c8103dbc6a9bc82ee228dec518c865ddf55c3de2e59ba531cc7321f`). The Kleene logic, the validation discipline,
the normalize / semantic_sha pair, the model record and the freeze-by-digest rule are kept; what changed is listed
under "What this port changed" below. Design v0.1 section 4.B. No network.

Contract (JSON):
  id          stable identifier (assigned by the validator when absent): "<verb>.<kind>.<n>"
  verb        one of oqa_lc_vocab.VERBS ("glob" | "read" | "grep")
  kind        "requires"  success -> condition held before   (necessary; direction: outcome => condition)
              "suffices"  condition held before -> success    (sufficient; direction: condition => outcome)
              "effect"    success -> each effect literal holds of the observation the call returned
  condition   CNF over literals: [[lit, ...] (a disjunction), ...] (a conjunction); requires / suffices only
  effects     [lit, ...]; effect only
  context     {"answer_kinds": [...]} ([] or absent = every question)
  assumptions named model assumptions the author says the contract relies on (informational; the evaluation uses
              the MODEL's assumption set, exactly as in the ALFWorld IR)
  reason, witnesses   the proposer's explanation and attempt ids (kept verbatim)
  -- bookkeeping added by the validator / learner --
  status      active | refuted | retired | superseded | rejected
  version, parent, operation (add | revise | retire), epoch, history
lit = {"atom": <oqa_lc_vocab.ATOMS name>, "args": [], "value": true|false}

What this port changed, and why:
  1. Atoms are 0-ARY. Every OfficeQA atom is a property of the step's own observation, so there is no entity to
     bind and `ground_args` is the identity. The ALFWorld role check ("argument 'O' is not a role of take") has no
     counterpart and is replaced by "0-ary atoms take no arguments".
  2. `context.task_types` became `context.answer_kinds`, whose values are the question's answer kind as
     `oqa_obligations.derive` reads it ("number" | "date" | "name" | "unknown"). It plays the same role: a contract
     may be scoped to a subset of questions.
  3. `render`'s per-verb head map is the document tools instead of the ALFWorld commands.
  4. An `effect` contract still takes NO condition. That restriction is inherited deliberately, and section 5 of
     docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md records the one place it bites, the concrete example, and the
     smallest extension that would lift it. The semantics are NOT changed here.
Everything else - the gate's inputs, the Kleene truth tables, the freeze-by-digest rule and the "nothing is ever
deleted" bookkeeping - is the ALFWorld behaviour.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oqa_lc_vocab import ASSUMPTIONS, ATOMS, VERBS, canon, sha, vocab_digest  # noqa: E402

KINDS = ("requires", "suffices", "effect")
MODEL_SCHEMA = "oqa.lc.model.v1"
ANSWER_KINDS = ("number", "date", "name", "unknown")


# ---- Kleene logic (unchanged from ir.py) ------------------------------------------------------------------------
def k_not(a):
    return None if a is None else (not a)


def k_and(xs):
    out = True
    for x in xs:
        if x is False:
            return False
        if x is None:
            out = None
    return out


def k_or(xs):
    out = False
    for x in xs:
        if x is True:
            return True
        if x is None:
            out = None
    return out


def lit_value(lit: dict, fact_v):
    if fact_v is None:
        return None
    return fact_v if lit["value"] else (not fact_v)


def eval_cnf(cnf: list, valfn) -> object:
    """valfn(lit) -> True/False/None (the literal's value). Kleene AND of ORs."""
    return k_and(k_or(valfn(l) for l in clause) for clause in cnf)


def lits_of(c: dict) -> list:
    if c["kind"] == "effect":
        return list(c.get("effects") or [])
    return [l for cl in c.get("condition") or [] for l in cl]


# ---- validation ------------------------------------------------------------------------------------------------
def validate_lit(lit, verb: str) -> list:
    errs = []
    if not isinstance(lit, dict) or set(lit) - {"atom", "args", "value"} or "atom" not in lit:
        return [f"literal {lit!r}: must be {{atom, args, value}}"]
    atom = lit["atom"]
    if atom not in ATOMS:
        return [f"literal {lit!r}: unknown atom {atom!r} (vocabulary: {sorted(ATOMS)})"]
    args = lit.get("args", [])
    if not isinstance(args, list) or args:
        errs.append(f"literal {lit!r}: {atom} is 0-ary and takes no arguments")
    if not isinstance(lit.get("value"), bool):
        errs.append(f"literal {lit!r}: value must be true or false")
    if verb not in VERBS:
        errs.append(f"literal {lit!r}: unknown verb {verb!r}")
    return errs


def validate_contract(c) -> list:
    if not isinstance(c, dict):
        return ["contract must be an object"]
    errs = []
    verb, kind = c.get("verb"), c.get("kind")
    if verb not in VERBS:
        return [f"unknown verb {verb!r} (covered verbs: {sorted(VERBS)})"]
    if kind not in KINDS:
        return [f"unknown kind {kind!r} (kinds: {KINDS})"]
    if kind == "effect":
        eff = c.get("effects")
        if not isinstance(eff, list) or not eff:
            errs.append("effect contract needs a non-empty effects list")
        else:
            for l in eff:
                errs += validate_lit(l, verb)
        if c.get("condition"):
            errs.append("effect contracts take no condition in this vocabulary (see the module docstring, note 4)")
    else:
        cnf = c.get("condition")
        if not isinstance(cnf, list) or not cnf or not all(isinstance(cl, list) and cl for cl in cnf):
            errs.append("condition must be a non-empty list of non-empty clauses (CNF)")
        else:
            for cl in cnf:
                for l in cl:
                    errs += validate_lit(l, verb)
        if c.get("effects"):
            errs.append(f"{kind} contracts take no effects")
    ctx = c.get("context") or {}
    if not isinstance(ctx, dict) or set(ctx) - {"answer_kinds"}:
        errs.append("context must be {answer_kinds: [...]}")
    else:
        for k in ctx.get("answer_kinds") or []:
            if k not in ANSWER_KINDS:
                errs.append(f"unknown answer kind {k!r} (kinds: {ANSWER_KINDS})")
    for a in c.get("assumptions") or []:
        if a not in ASSUMPTIONS:
            errs.append(f"unknown assumption {a!r}")
    return errs


def normalize(c: dict) -> dict:
    """The semantic content of a contract (what is checked and frozen)."""
    out = {"verb": c["verb"], "kind": c["kind"],
           "context": {"answer_kinds": sorted((c.get("context") or {}).get("answer_kinds") or [])},
           "assumptions": sorted(set(c.get("assumptions") or []))}
    if c["kind"] == "effect":
        out["effects"] = [{"atom": l["atom"], "args": [], "value": bool(l["value"])} for l in c["effects"]]
    else:
        out["condition"] = [[{"atom": l["atom"], "args": [], "value": bool(l["value"])} for l in cl]
                            for cl in c["condition"]]
    return out


def semantic_sha(c: dict) -> str:
    return sha(canon(normalize(c)))


def applies(c: dict, verb: str, answer_kind: str | None) -> bool:
    ks = (c.get("context") or {}).get("answer_kinds") or []
    return c["verb"] == verb and (not ks or answer_kind in ks)


def render_lit(l: dict) -> str:
    return l["atom"] if l["value"] else "not " + l["atom"]


_HEAD = {"glob": "glob(pattern)", "read": "read(path, start, limit)", "grep": "grep(pattern, path)"}


def render(c: dict) -> str:
    head = _HEAD[c["verb"]]
    ks = (c.get("context") or {}).get("answer_kinds") or []
    scope = f" [answer kinds: {', '.join(ks)}]" if ks else ""
    if c["kind"] == "effect":
        return (f"of the observation a successful '{head}' returns: "
                + " and ".join(render_lit(l) for l in c["effects"]) + scope)
    cond = " and ".join("(" + " or ".join(render_lit(l) for l in cl) + ")" if len(cl) > 1 else render_lit(cl[0])
                        for cl in c["condition"])
    if c["kind"] == "requires":
        return f"'{head}' returns content only if {cond}" + scope
    return f"if {cond}, then '{head}' returns content" + scope


# ---- models ----------------------------------------------------------------------------------------------------
def empty_model() -> dict:
    return {"schema": MODEL_SCHEMA, "version": 0, "epoch": 0, "vocab": vocab_digest(), "assumptions": [],
            "contracts": [], "decisions": [], "parent_sha256": None}


def active(model: dict) -> list:
    return [c for c in model["contracts"] if c.get("status") == "active"]


def model_sha(model: dict) -> str:
    m = {k: v for k, v in model.items() if k not in ("sha256", "frozen_utc")}
    return sha(canon(m))


def freeze(model: dict, path: str, *, parent_sha256: str | None = None,
           frozen_at_epoch: int | None = None) -> str:
    """Write the model read-only under its own digest. Refuses to overwrite with different content.

    AMENDMENT A-1. Two provenance fields, both optional so that a caller that passes neither gets the
    byte-identical behaviour and digest it got before:

      parent_sha256   the model this one was frozen FROM. It was null in all seven of the 2026-11-17
                      campaign's models, so the chain of models was reconstructible only from
                      MODEL_EVOLUTION.jsonl.
      frozen_at_epoch the epoch the freeze happened at, which is NOT what the existing `epoch` field
                      records: that one holds the epoch of the last model-CHANGING decision, so every
                      arm-C model in the campaign says 1 although they were frozen at epochs 2, 3 and 4.
                      Deriving a learning corpus from `epoch` understates it.

    Both are covered by the digest, so a frozen model cannot have its provenance edited afterwards.
    """
    m = copy.deepcopy(model)
    if parent_sha256 is not None and not m.get("parent_sha256"):
        m["parent_sha256"] = str(parent_sha256)
    if frozen_at_epoch is not None:
        m["frozen_at_epoch"] = int(frozen_at_epoch)
    m["sha256"] = model_sha(m)
    m["frozen_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("sha256") != m["sha256"]:
            raise RuntimeError(f"{path} is frozen with another content")
        return old["sha256"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, sort_keys=True)
    os.chmod(path, 0o444)
    return m["sha256"]


def load_frozen(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if model_sha(m) != m.get("sha256"):
        raise RuntimeError(f"{path}: content does not match its frozen digest")
    return m


def next_id(model: dict, verb: str, kind: str) -> str:
    n = 1 + sum(1 for c in model["contracts"]
                if c["verb"] == verb and c["kind"] == kind and c.get("parent") is None)
    return f"{verb}.{kind}.{n}"


def model_summary(model: dict) -> list:
    """Readable lines for a report: one per contract, with its status, version and evidence counts."""
    lines = [f"model v{model['version']} (epoch {model['epoch']}), assumptions: "
             f"{', '.join(model['assumptions']) or '(none)'}"]
    for c in model["contracts"]:
        ev = c.get("evidence") or {}
        lines.append(f"  {c.get('id', '(unassigned)')}@v{c.get('version', '?')} [{c.get('status')}] "
                     f"{render(c)}"
                     + (f"  -- support {ev.get('n_support_ok', 0) + ev.get('n_support_fail', 0)}, "
                        f"counterexamples {ev.get('n_counter', 0)}" if ev else ""))
    return lines


_IDENT = re.compile(r"[^A-Za-z0-9_]")


def ident(s: str) -> str:
    """A Lean-safe identifier for a contract id."""
    return _IDENT.sub("_", s)
