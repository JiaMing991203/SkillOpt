"""Structured action contracts (design v0.1 section 4.B): schema, deterministic validation, Kleene evaluation,
models and freezing. No network.

Contract (JSON):
  id          stable identifier (assigned by the validator when absent): "<verb>.<kind>.<n>"
  verb        one of vocab.VERBS
  kind        "requires"  success -> condition held before   (necessary; direction: outcome => condition)
              "suffices"  condition held before -> success    (sufficient; direction: condition => outcome)
              "effect"    success -> each effect literal holds right after the command
  condition   CNF over literals: [[lit, ...] (a disjunction), ...] (a conjunction); requires / suffices only
  effects     [lit, ...]; effect only
  context     {"task_types": [...]} ([] or absent = every task type)
  assumptions named model assumptions the author says the contract relies on (informational; the evaluation uses
              the MODEL's assumption set)
  reason, witnesses   the proposer's explanation and attempt ids (kept verbatim)
  -- bookkeeping added by the validator / learner --
  status      active | refuted | retired | superseded | rejected
  version, parent, operation (add | revise | retire), epoch, history
lit = {"atom": <vocab.ATOMS name>, "args": [role or type word], "value": true|false}
Roles are the verb's roles ("O", "R"); is_a's second argument is a lowercase type word.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vocab import ASSUMPTIONS, ATOMS, VERBS, canon, sha, vocab_digest  # noqa: E402

KINDS = ("requires", "suffices", "effect")
MODEL_SCHEMA = "lc.model.v1"


# ---- Kleene logic ------------------------------------------------------------------------------------------
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


def ground_args(lit: dict, binding: dict) -> list:
    out = []
    kinds = ATOMS[lit["atom"]]
    for kind, a in zip(kinds, lit["args"]):
        out.append(a if kind == "TYPE" else binding[a])
    return out


def eval_cnf(cnf: list, valfn) -> object:
    """valfn(lit) -> True/False/None (the literal's value). Kleene AND of ORs."""
    return k_and(k_or(valfn(l) for l in clause) for clause in cnf)


def lits_of(c: dict) -> list:
    if c["kind"] == "effect":
        return list(c.get("effects") or [])
    return [l for cl in c.get("condition") or [] for l in cl]


# ---- validation ----------------------------------------------------------------------------------------------
def validate_lit(lit, verb: str) -> list:
    errs = []
    if not isinstance(lit, dict) or set(lit) - {"atom", "args", "value"} or "atom" not in lit:
        return [f"literal {lit!r}: must be {{atom, args, value}}"]
    atom = lit["atom"]
    if atom not in ATOMS:
        return [f"literal {lit!r}: unknown atom {atom!r} (vocabulary: {sorted(ATOMS)})"]
    args = lit.get("args", [])
    if not isinstance(args, list) or len(args) != len(ATOMS[atom]):
        return [f"literal {lit!r}: {atom} takes {len(ATOMS[atom])} argument(s)"]
    if not isinstance(lit.get("value"), bool):
        errs.append(f"literal {lit!r}: value must be true or false")
    roles = VERBS[verb]["roles"]
    for kind, a in zip(ATOMS[atom], args):
        if kind == "TYPE":
            if not (isinstance(a, str) and re.fullmatch(r"[a-z]+", a)):
                errs.append(f"literal {lit!r}: type argument must be a lowercase type word")
        elif a not in roles:
            errs.append(f"literal {lit!r}: argument {a!r} is not a role of {verb} {roles}")
        elif kind in ("O", "R") and a != kind:
            errs.append(f"literal {lit!r}: {atom} expects role {kind} here, got {a}")
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
            errs.append("effect contracts take no condition in this vocabulary")
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
    if not isinstance(ctx, dict) or set(ctx) - {"task_types"}:
        errs.append("context must be {task_types: [...]}")
    for a in c.get("assumptions") or []:
        if a not in ASSUMPTIONS:
            errs.append(f"unknown assumption {a!r}")
    return errs


def normalize(c: dict) -> dict:
    """The semantic content of a contract (what is checked and frozen)."""
    out = {"verb": c["verb"], "kind": c["kind"],
           "context": {"task_types": sorted((c.get("context") or {}).get("task_types") or [])},
           "assumptions": sorted(set(c.get("assumptions") or []))}
    if c["kind"] == "effect":
        out["effects"] = [{"atom": l["atom"], "args": list(l["args"]), "value": bool(l["value"])} for l in c["effects"]]
    else:
        out["condition"] = [[{"atom": l["atom"], "args": list(l["args"]), "value": bool(l["value"])} for l in cl]
                            for cl in c["condition"]]
    return out


def semantic_sha(c: dict) -> str:
    return sha(canon(normalize(c)))


def applies(c: dict, verb: str, task_type: str | None) -> bool:
    tts = (c.get("context") or {}).get("task_types") or []
    return c["verb"] == verb and (not tts or task_type in tts)


def render_lit(l: dict) -> str:
    s = f"{l['atom']}({','.join(l['args'])})" if l["args"] else l["atom"]
    return s if l["value"] else "not " + s


def render(c: dict) -> str:
    head = {"go": "go to R", "take": "take O from R", "move": "move O to R", "open": "open R", "close": "close R",
            "heat": "heat O with R", "cool": "cool O with R", "clean": "clean O with R", "use": "use R"}[c["verb"]]
    ctx = (c.get("context") or {}).get("task_types") or []
    scope = f" [task types: {', '.join(ctx)}]" if ctx else ""
    if c["kind"] == "effect":
        return f"after a successful '{head}': " + " and ".join(render_lit(l) for l in c["effects"]) + scope
    cond = " and ".join("(" + " or ".join(render_lit(l) for l in cl) + ")" if len(cl) > 1 else render_lit(cl[0])
                        for cl in c["condition"])
    if c["kind"] == "requires":
        return f"'{head}' succeeds only if {cond}" + scope
    return f"if {cond}, then '{head}' succeeds" + scope


# ---- models ----------------------------------------------------------------------------------------------------
def empty_model() -> dict:
    return {"schema": MODEL_SCHEMA, "version": 0, "epoch": 0, "vocab": vocab_digest(), "assumptions": [],
            "contracts": [], "decisions": [], "parent_sha256": None}


def active(model: dict) -> list:
    return [c for c in model["contracts"] if c.get("status") == "active"]


def model_sha(model: dict) -> str:
    m = {k: v for k, v in model.items() if k not in ("sha256", "frozen_utc")}
    return sha(canon(m))


def freeze(model: dict, path: str) -> str:
    import datetime
    m = copy.deepcopy(model)
    m["sha256"] = model_sha(model)
    m["frozen_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if os.path.exists(path):
        old = json.load(open(path))
        if old.get("sha256") != m["sha256"]:
            raise RuntimeError(f"{path} is frozen with another content")
        return old["sha256"]
    with open(path, "w") as f:
        json.dump(m, f, indent=1, sort_keys=True)
    os.chmod(path, 0o444)
    return m["sha256"]


def load_frozen(path: str) -> dict:
    m = json.load(open(path))
    if model_sha(m) != m.get("sha256"):
        raise RuntimeError(f"{path}: content does not match its frozen digest")
    return m


def next_id(model: dict, verb: str, kind: str) -> str:
    n = 1 + sum(1 for c in model["contracts"] if c["verb"] == verb and c["kind"] == kind and c.get("parent") is None)
    return f"{verb}.{kind}.{n}"
