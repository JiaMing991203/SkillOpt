#!/usr/bin/env python3
"""A CONNECTED contract: a conditional, entity-bound action claim wired to a stage obligation.

Amendment B-1. This is a prototype BESIDE the restricted 20-effect learner (`oqa_lc_*`), which it does
not touch. The restricted learner can state exactly 20 unconditional claims of the form "a successful
read(...) returns: <atom>", where every atom is a 0-ary property of one observation's text. It cannot
state the relationship the campaign most needed:

    under what observable conditions does a table's scale annotation apply to a particular cell,
    and when must that binding be withdrawn or revised?

WHAT IS NEW HERE, and nothing more than this:

  * BOUND ENTITIES. A clause mentions `table`, `header`, `cell`, `value` or `calculation` by name and is
    evaluated with those names bound to extracted objects, so a claim can be about THIS cell of THAT
    table instead of about a whole observation.
  * AN APPLICABILITY CONDITION. A contract is `conclusion IF conditions`. A cell the conditions do not
    cover is not a counterexample: the contract is SILENT about it. The restricted form has no such
    notion, which is why both polarities of `row_period_bound` were induced and then refuted and the
    atom had to be struck from the template set.
  * THREE TRUTH VALUES AND CONFLICT. holds / violated / unknown, plus an explicit `conflict` when two
    pieces of evidence disagree, instead of a silent majority.
  * VERSIONED REPAIR. A counterexample produces a new version with a narrowed condition and a recorded
    reason; the superseded version is kept.
  * A STAGE LINK. A contract may declare which stage obligation its conclusion witnesses, so that
    withdrawing the binding withdraws the obligation's witness rather than inventing a violation.

PROVENANCE IS PART OF THE REPRESENTATION, because the contract audit's central complaint was that
ordinary arithmetic had been presented as a discovery. Every clause carries exactly one label:

    TASK     read from the question sentence
    GIVEN    manually supplied structure or representation (not learned, not discovered)
    MATH     a definition - thousands to billions is 1e-6 because that is what the words mean
    LEARNED  an empirical hypothesis induced from the permitted records
    OBS      a value read off a supplied page or a returned observation

`validate` REFUSES a contract whose conclusion is labelled MATH or GIVEN, and refuses a LEARNED label on
a clause whose predicate is in the mathematical or structural set. A learner cannot claim to have
discovered a definition.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os

SCHEMA = "oqa.cc.model.v1"
RULE = "OQA-CC-1"

HOLDS, VIOLATED, UNKNOWN, CONFLICT = "HOLDS", "VIOLATED", "UNKNOWN", "CONFLICT"
TRUTHS = (HOLDS, VIOLATED, UNKNOWN, CONFLICT)

TASK, GIVEN, MATH, LEARNED, OBS = "TASK", "GIVEN", "MATH", "LEARNED", "OBS"
PROVENANCE = (TASK, GIVEN, MATH, LEARNED, OBS)

ENTITIES = ("table", "header", "cell", "value", "calculation")
# Entities an extension namespace may bind (amendment D-1). Kept here so `repair` widens `binds`
# correctly for an extended contract without ENTITIES itself changing - the frozen prototype digests
# ENTITIES into its semantic bundle label.
_EXTRA_ENTITIES = ("operand", "answer")

# The predicate vocabulary of the prototype, each with the arity, the entities it binds, and the ONLY
# provenance label it may carry. A learner may not relabel one of these.
PREDICATES = {
    # structure, supplied by the extraction rather than discovered
    "cell_in_table":            {"args": ("cell", "table"), "provenance": GIVEN},
    "cell_column_header":       {"args": ("cell", "header"), "provenance": GIVEN},
    "value_matches_cell":       {"args": ("value", "cell"), "provenance": GIVEN},
    # read off the document or the actor's own message
    "table_states_scale":       {"args": ("table", "scale"), "provenance": OBS},
    "column_header_states_unit": {"args": ("header", "scale"), "provenance": OBS},
    "row_label_states_scale":   {"args": ("cell", "scale"), "provenance": OBS},
    "nearer_scale_between":     {"args": ("table", "cell"), "provenance": OBS},
    "calculation_uses_factor":  {"args": ("calculation", "exp10"), "provenance": OBS},
    # read from the question sentence
    "asked_scale":              {"args": ("scale",), "provenance": TASK},
    # a definition
    "factor_exp":               {"args": ("scale", "scale", "exp10"), "provenance": MATH},
    # the learned conclusion
    "scale_binds":              {"args": ("table", "cell", "scale"), "provenance": LEARNED},
}
# Predicates a LEARNED clause may never be: a definition or supplied structure is not a discovery.
NOT_LEARNABLE = tuple(p for p, s in PREDICATES.items() if s["provenance"] in (MATH, GIVEN))

# The stage obligations a contract may witness (the names the campaign's stage layer already uses).
STAGE_OBLIGATIONS = ("unit_match", "cell_bound", "period_match", "arith_recorded", "answer_traced")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def clause(pred: str, args: list, value: bool = True, provenance: str | None = None,
           evidence: list | None = None, vocab: dict | None = None) -> dict:
    """One clause of a condition or a conclusion, with its provenance fixed by its predicate.

    `vocab` (amendment D-1) lets a separate namespace supply additional predicates without extending
    this module's own dict, which the 2026-11-17 learner enumerates into its prompt. Omitted, nothing
    changes.
    """
    spec = (vocab or PREDICATES).get(pred)
    return {"pred": pred, "args": list(args), "value": bool(value),
            "provenance": provenance or (spec or {}).get("provenance") or OBS,
            "evidence": list(evidence or [])}


def contract(cid: str, conclusion: dict, conditions: list, *, version: int = 1,
             witnesses: str | None = None, reason: str = "", source: str = "",
             entities: tuple | None = None) -> dict:
    ents = entities or ENTITIES
    return {
        "id": cid, "version": version, "status": "active", "kind": "binding",
        "binds": sorted({a for c in [conclusion] + list(conditions) for a in c["args"]
                         if a in ents}),
        "conclusion": conclusion, "conditions": list(conditions),
        "witnesses_obligation": witnesses, "reason": reason, "source": source,
        "parent": None, "repair_reason": None,
        "evidence": {"supporting": [], "counterexamples": [], "unknown": [], "conflicting": []},
    }


def validate(c: dict, vocab: dict | None = None, entities: tuple | None = None,
             obligations: tuple | None = None) -> list:
    """Every reason this contract may not be admitted. An empty list means it is well formed.

    The three optional arguments (amendment D-1) let an extension namespace validate against its own
    vocabulary with exactly this logic. Called with none of them, behaviour is unchanged.
    """
    PRED = vocab or PREDICATES
    ENTS = entities or ENTITIES
    OBLS = obligations or STAGE_OBLIGATIONS
    not_learnable = tuple(p for p, sp in PRED.items() if sp["provenance"] in (MATH, GIVEN))
    problems = []
    conc = c.get("conclusion") or {}
    if conc.get("pred") not in PRED:
        problems.append(f"unknown conclusion predicate {conc.get('pred')!r}")
    elif PRED[conc["pred"]]["provenance"] in (MATH, GIVEN):
        problems.append(f"{conc['pred']} is {PRED[conc['pred']]['provenance']}: a definition or a "
                        f"supplied structure cannot be the CONCLUSION of a learned contract")
    for cl in c.get("conditions") or []:
        p = cl.get("pred")
        if p not in PRED:
            problems.append(f"unknown condition predicate {p!r}")
            continue
        want = PRED[p]["provenance"]
        if cl.get("provenance") != want:
            problems.append(f"{p} must be labelled {want}, not {cl.get('provenance')!r}")
        if cl.get("provenance") == LEARNED and p in not_learnable:
            problems.append(f"{p} cannot be LEARNED: it is a definition or supplied structure")
        if len(cl.get("args") or []) != len(PRED[p]["args"]):
            problems.append(f"{p} takes {len(PRED[p]['args'])} argument(s), "
                            f"got {len(cl.get('args') or [])}")
    if not (c.get("conditions") or []):
        problems.append("a connected contract must state an applicability condition; an unconditional "
                        "claim is what the restricted learner already does")
    if c.get("witnesses_obligation") and c["witnesses_obligation"] not in OBLS:
        problems.append(f"unknown stage obligation {c['witnesses_obligation']!r}")
    bound = set(c.get("binds") or [])
    for cl in [conc] + list(c.get("conditions") or []):
        for a in cl.get("args") or []:
            if a in ENTS and a not in bound:
                problems.append(f"{cl.get('pred')} mentions unbound entity {a!r}")
    return sorted(set(problems))


def render(c: dict) -> str:
    def one(cl):
        neg = "" if cl.get("value", True) else "NOT "
        return f"{neg}{cl['pred']}({', '.join(map(str, cl['args']))})  [{cl['provenance']}]"
    head = one(c["conclusion"])
    body = "\n      AND ".join(one(cl) for cl in c["conditions"])
    out = f"{c['id']}@v{c['version']} [{c['status']}]\n    {head}\n    IF  {body}"
    if c.get("witnesses_obligation"):
        out += f"\n    WITNESSES stage obligation {c['witnesses_obligation']}"
    return out


def repair(c: dict, extra_condition: dict, reason: str, counterexamples: list) -> tuple:
    """A counterexample narrows the condition. The old version is kept, marked superseded.

    This is the operation the restricted form cannot perform: with no condition to narrow, its only
    repair is to RETIRE the rule, which is what happened to `read.effect.2` - the campaign's single
    counterexample in six rounds deleted a rule instead of improving one.
    """
    old = copy.deepcopy(c)
    old["status"] = "superseded"
    old["repair_reason"] = reason
    new = copy.deepcopy(c)
    new["version"] = int(c["version"]) + 1
    new["status"] = "active"
    new["parent"] = f"{c['id']}@v{c['version']}"
    new["repair_reason"] = reason
    new["conditions"] = list(c["conditions"]) + [extra_condition]
    new["evidence"] = {"supporting": [], "counterexamples": list(counterexamples), "unknown": [],
                       "conflicting": []}
    new["binds"] = sorted(set(new["binds"]) | {a for a in extra_condition["args"]
                                              if a in ENTITIES or a in _EXTRA_ENTITIES})
    return old, new


def model(contracts: list, *, epoch: int, scope: str, learned_from: list) -> dict:
    return {"schema": SCHEMA, "rule": RULE, "version": 1, "epoch": int(epoch),
            "frozen_at_epoch": int(epoch), "parent_sha256": None,
            "evidence_scope": scope, "evidence_attempts": list(learned_from),
            "contracts": list(contracts)}


def model_sha(m: dict) -> str:
    return sha(canon({k: v for k, v in m.items() if k not in ("sha256", "frozen_utc")}))


def freeze(m: dict, path: str, *, parent_sha256: str | None = None) -> str:
    out = copy.deepcopy(m)
    if parent_sha256:
        out["parent_sha256"] = parent_sha256
    out["sha256"] = model_sha(out)
    out["frozen_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("sha256") != out["sha256"]:
            raise RuntimeError(f"{path} is already frozen with other content")
        return old["sha256"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.chmod(path, 0o444)
    return out["sha256"]


def load_frozen(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if model_sha(m) != m.get("sha256"):
        raise RuntimeError(f"{path}: content does not match its frozen digest")
    return m


def active(m: dict) -> list:
    return [c for c in m.get("contracts") or [] if c.get("status") == "active"]
