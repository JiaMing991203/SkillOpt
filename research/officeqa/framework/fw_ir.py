#!/usr/bin/env python3
"""Contract representation: schema, validation against a domain, rendering, Kleene evaluation, models.

PORT PROVENANCE. A domain-parameterised `upstream/vx1/vx_ir.py`. Every ALFWorld function is here with
the same body and the module-level `XV` vocabulary replaced by a `dom` argument:

  validate_lit / validate / normalize / semantic_sha / applies_task / render_lit / render_cnf / render
  ground / lit_fact / eval_cnf / bindings / evaluate
  empty_model / active / model_sha / freeze / load_frozen / next_id

ADDED FOR OfficeQA, and available to every domain:
  * `subject_scope` - TEMPORAL SCOPE for a `concludes` rule, and the three readings are different
    claims, not three speeds:
      "all"        every entity named so far, re-checked at every step (ALFWorld's `completes` reading;
                   right for a property of the STATE).
      "introduced" only the entities the step's own observation introduced - what a returned window
                   licenses on arrival.
      "once"       each entity exactly once, at the FIRST state where the record settles both sides of
                   the rule. Right for a property of an ENTITY: a column's label is what it is, and the
                   rendering that adjudicates it may arrive thirty steps after the column did. Every
                   value is still read prefix-only at that state, so nothing later than the state being
                   judged is consulted - the state is chosen by when the evidence arrives, not by what
                   the evidence says.
  * the kind `concludes` - `completes` generalised from "the task is complete" to a named consequence,
    with `subject` variables it is checked POINTWISE over (see `evaluate_pointwise`); a document
    interpretation rule is about every cell, not about some cell.
  * `repair_ops` - add / revise / retire / keep were already here through `vx_learn`; `drop_conjunct`
    and `narrow` build the two revisions a counterexample actually calls for, including the DELETION of
    a condition, which the OfficeQA CC/LN generation could not express.
  * statuses active | superseded | retired | refuted (ALFWorld's four; CC/LN had two).
"""
from __future__ import annotations

import copy
import datetime
import itertools
import json
import os

from fw_domain import TYPE_WORD, VAR_RE, canon, sha
from fw_kleene import fact, is_unnamed, k_and, k_or

MODEL_SCHEMA = "fw.model.v1"
STATUSES = ("active", "superseded", "retired", "refuted")
FIELDS = {
    "requires": {"verb", "condition", "exists", "context", "assumptions"},
    "suffices": {"verb", "condition", "exists", "context", "assumptions"},
    "effect": {"verb", "effects", "guard", "exists", "context", "assumptions"},
    "completes": {"scope", "condition", "exists", "context", "assumptions"},
    "concludes": {"scope", "subject", "subject_scope", "conditions", "consequence", "direction",
                  "guard", "exists", "context", "assumptions"},
    "persists": {"state", "established_by", "invalidated_by", "context", "assumptions"},
}


# ---- validation ---------------------------------------------------------------------------------------
def _roles(c: dict, dom) -> tuple:
    if c.get("kind") in ("completes", "concludes"):
        verb = c.get("scope") if c.get("scope") != "any" else None
    else:
        verb = c.get("verb")
    return tuple((dom.verbs.get(verb) or {}).get("roles") or ()) if verb in dom.verbs else ()


def _declared(c: dict) -> dict:
    out = dict(c.get("exists") or {})
    out.update(c.get("subject") or {})
    return out


def validate_lit(lit, c: dict, dom) -> list:
    if not isinstance(lit, dict) or set(lit) - {"atom", "args", "value"} or "atom" not in lit:
        return [f"literal {lit!r}: must be {{atom, args, value}}"]
    atom = lit["atom"]
    if atom not in dom.atoms:
        return [f"literal {lit!r}: unknown atom {atom!r} (vocabulary: {sorted(dom.atoms)})"]
    kinds = dom.role_kinds(atom)
    args = lit.get("args", [])
    if not isinstance(args, list) or len(args) != len(kinds):
        return [f"literal {lit!r}: {atom} takes {len(kinds)} argument(s)"]
    errs = [] if isinstance(lit.get("value"), bool) else [f"literal {lit!r}: value must be true or false"]
    roles, declared = _roles(c, dom), _declared(c)
    for kind, a in zip(kinds, args):
        if kind == "TYPE":
            if not (isinstance(a, str) and (a in dom.placeholders or TYPE_WORD.fullmatch(a))):
                errs.append(f"literal {lit!r}: a type argument must be a lowercase type word or one of "
                            f"{list(dom.placeholders)}")
        elif isinstance(a, str) and VAR_RE.fullmatch(a):
            if not dom.variables:
                errs.append(f"literal {lit!r}: variables like {a!r} are not part of this vocabulary")
            elif a not in declared:
                errs.append(f"literal {lit!r}: variable {a!r} is not declared in \"exists\" or \"subject\"")
            elif kind in dom.types and declared[a] != kind:
                # TYPED ARGUMENTS. An atom position that names an entity type admits only a variable
                # declared with that type, so `header_split(?c)` with ?c a column is a schema error
                # rather than a hypothesis that is merely always unknown.
                errs.append(f"literal {lit!r}: {atom} takes a {kind} here, but {a!r} is declared "
                            f"{declared[a]}")
        elif a not in roles:
            errs.append(f"literal {lit!r}: argument {a!r} is not a role of "
                        f"{c.get('verb') or c.get('scope')} {roles}"
                        + (" or a declared variable" if dom.variables else ""))
    return errs


def _cnf_errs(cnf, c, dom, what="condition") -> list:
    if not isinstance(cnf, list) or not cnf or not all(isinstance(cl, list) and cl for cl in cnf):
        return [f"{what} must be a non-empty list of non-empty clauses (CNF)"]
    return [e for cl in cnf for l in cl for e in validate_lit(l, c, dom)]


def _vars_errs(c, dom, field: str, cap: int) -> list:
    v = c.get(field)
    if v is None:
        return []
    if field == "subject" and not dom.subjects:
        return ["\"subject\" variables are not part of this vocabulary"]
    if field == "exists" and not dom.variables:
        return ["\"exists\" (typed variables) is not part of this vocabulary"]
    if not isinstance(v, dict) or not all(
            isinstance(k, str) and VAR_RE.fullmatch(k) and isinstance(t, str)
            and (t in dom.placeholders or t in dom.types) for k, t in v.items()):
        return [f"\"{field}\" must map variables like \"?x\" to one of {list(dom.types)} "
                f"or {list(dom.placeholders)}"]
    if len(v) > cap:
        return [f"at most {cap} {field} variable(s) per contract"]
    return []


def validate(c, dom) -> list:
    if not isinstance(c, dict):
        return ["contract must be an object"]
    kind = c.get("kind")
    if kind not in dom.kinds:
        return [f"unknown kind {kind!r} (kinds: {list(dom.kinds)})"]
    extra = set(c) - FIELDS[kind] - {"kind", "id", "reason", "witnesses"}
    errs = [f"unexpected field(s) {sorted(extra)} for kind {kind}"] if extra else []
    errs += _vars_errs(c, dom, "exists", dom.max_vars) + _vars_errs(c, dom, "subject", dom.max_vars)
    if set(c.get("exists") or {}) & set(c.get("subject") or {}):
        errs.append("a variable cannot be both a subject and an existential")
    if kind in ("requires", "suffices", "effect") and c.get("verb") not in dom.verbs:
        return errs + [f"unknown verb {c.get('verb')!r} (covered verbs: {sorted(dom.verbs)})"]
    if kind in ("requires", "suffices"):
        errs += _cnf_errs(c.get("condition"), c, dom)
    elif kind == "effect":
        eff = c.get("effects")
        if not isinstance(eff, list) or not eff:
            errs.append("effect contract needs a non-empty effects list")
        else:
            errs += [e for l in eff for e in validate_lit(l, c, dom)]
        if c.get("guard") is not None:
            errs += ([] if dom.guards else ["effect guards are not part of this vocabulary"]) \
                + _cnf_errs(c["guard"], c, dom, "guard")
    elif kind == "completes":
        if c.get("scope") != "any" and c.get("scope") not in dom.verbs:
            errs.append(f"completes scope must be a covered verb or \"any\", got {c.get('scope')!r}")
        errs += _cnf_errs(c.get("condition"), c, dom)
    elif kind == "concludes":
        if c.get("scope") != "any" and c.get("scope") not in dom.verbs:
            errs.append(f"concludes scope must be a covered verb or \"any\", got {c.get('scope')!r}")
        if not (c.get("subject") or {}):
            errs.append("a concludes rule needs at least one subject variable: it is a claim about each "
                        "entity of that type, not about some entity")
        errs += _cnf_errs(c.get("conditions"), c, dom, "conditions")
        errs += validate_lit(c.get("consequence"), c, dom)
        if c.get("direction") not in (None, "implies", "iff"):
            errs.append("direction must be \"implies\" (default) or \"iff\"")
        if c.get("subject_scope") not in (None, "all", "introduced", "once"):
            errs.append("subject_scope must be \"all\" (default), \"introduced\" or \"once\"")
        if c.get("guard") is not None:
            errs += ([] if dom.guards else ["guards are not part of this vocabulary"]) \
                + _cnf_errs(c["guard"], c, dom, "guard")
    elif kind == "persists":
        if c.get("state") not in dom.persist_states:
            errs.append(f"persists state must be one of {sorted(dom.persist_states)}")
        elif c.get("established_by") != dom.persist_states[c["state"]]:
            errs.append(f"persists {c['state']} is established by {dom.persist_states[c['state']]}")
        inv = c.get("invalidated_by")
        if not isinstance(inv, list) or any(i not in dom.invalidators for i in inv):
            errs.append(f"invalidated_by must be a list drawn from {sorted(dom.invalidators)} (may be empty)")
    ctx = c.get("context") or {}
    if not isinstance(ctx, dict) or set(ctx) - {"task_types"}:
        errs.append("context must be {task_types: [...]}")
    for a in c.get("assumptions") or []:
        if a not in dom.assumptions:
            errs.append(f"unknown assumption {a!r}")
    return errs


def normalize(c: dict) -> dict:
    L = lambda l: {"atom": l["atom"], "args": list(l.get("args") or []), "value": bool(l["value"])}  # noqa: E731
    C = lambda cnf: [[L(l) for l in cl] for cl in cnf]                                              # noqa: E731
    k = c["kind"]
    out = {"kind": k, "context": {"task_types": sorted((c.get("context") or {}).get("task_types") or [])}}
    if k in ("requires", "suffices", "effect"):
        out["verb"] = c["verb"]
    if k in ("requires", "suffices", "completes"):
        out["condition"] = C(c["condition"])
    if k == "effect":
        out["effects"] = [L(l) for l in c["effects"]]
    if k == "concludes":
        out["conditions"] = C(c["conditions"])
        out["consequence"] = L(c["consequence"])
        out["direction"] = c.get("direction") or "implies"
        out["subject_scope"] = c.get("subject_scope") or "all"
    if k in ("completes", "concludes"):
        out["scope"] = c["scope"]
    if k in ("effect", "concludes") and c.get("guard"):
        out["guard"] = C(c["guard"])
    if k == "persists":
        out.update(state=c["state"], established_by=c["established_by"],
                   invalidated_by=sorted(set(c["invalidated_by"])))
    for f in ("exists", "subject"):
        if c.get(f):
            out[f] = dict(sorted(c[f].items()))
    return out


def semantic_sha(c: dict) -> str:
    return sha(canon(normalize(c)))


def applies_task(c: dict, task_type) -> bool:
    tts = (c.get("context") or {}).get("task_types") or []
    return not tts or task_type in tts


# ---- rendering ----------------------------------------------------------------------------------------
def render_lit(l: dict) -> str:
    a = l.get("args") or []
    s = f"{l['atom']}({','.join(str(x) for x in a)})" if a else l["atom"]
    return s if l["value"] else "not " + s


def render_cnf(cnf) -> str:
    return " and ".join("(" + " or ".join(render_lit(l) for l in cl) + ")" if len(cl) > 1
                        else render_lit(cl[0]) for cl in cnf)


def _quant(c, field, word):
    v = c.get(field) or {}
    return (f"{word} " + ", ".join(f"{k}:{t}" for k, t in v.items()) + ": ") if v else ""


def render(c: dict, dom) -> str:
    ctx = (c.get("context") or {}).get("task_types") or []
    scope = f" [task types: {', '.join(ctx)}]" if ctx else ""
    q = _quant(c, "exists", "for some")
    k = c["kind"]
    if k == "requires":
        return f"'{dom.head(c['verb'])}' succeeds only if {q}{render_cnf(c['condition'])}{scope}"
    if k == "suffices":
        return f"if {q}{render_cnf(c['condition'])}, then '{dom.head(c['verb'])}' succeeds{scope}"
    if k == "effect":
        g = f" when {render_cnf(c['guard'])} held before" if c.get("guard") else ""
        return (f"after a successful '{dom.head(c['verb'])}'{g}: {q}"
                + " and ".join(render_lit(l) for l in c["effects"]) + scope)
    if k == "completes":
        w = "after any step" if c["scope"] == "any" else f"after a '{dom.head(c['scope'])}' step"
        return f"{w}, the task is complete exactly when {q}{render_cnf(c['condition'])}{scope}"
    if k == "concludes":
        w = "after any step" if c["scope"] == "any" else f"after a '{dom.head(c['scope'])}' step"
        arrow = "exactly when" if c.get("direction") == "iff" else "whenever"
        g = f" (only when {render_cnf(c['guard'])})" if c.get("guard") else ""
        return (f"{w}{g}, {_quant(c, 'subject', 'for every')}{render_lit(c['consequence'])} holds "
                f"{arrow} {q}{render_cnf(c['conditions'])}{scope}")
    if k == "persists":
        inv = ", ".join(c["invalidated_by"]) or "nothing recorded"
        return f"{c['state']}(X) holds from a recorded {c['established_by']} of X until {inv}{scope}"
    return str(c)


# ---- evaluation ---------------------------------------------------------------------------------------
def ground(arg: str, kind: str, binding: dict, tinfo: dict):
    if kind == "TYPE":
        return tinfo.get(arg) if arg in tinfo or not TYPE_WORD.fullmatch(str(arg)) else arg
    return binding.get(arg)


def lit_fact(est, t: int, lit: dict, binding: dict, tinfo: dict, dom) -> tuple:
    kinds = dom.role_kinds(lit["atom"])
    g = [ground(a, k, binding, tinfo) for k, a in zip(kinds, lit["args"])]
    if any(x is None for x in g):
        return g, fact(None, "none")
    return g, est.value(t, lit["atom"], g)


def eval_cnf(cnf, est, t, binding, tinfo, dom, used: list):
    vals = []
    for cl in cnf:
        cv = []
        for l in cl:
            g, f = lit_fact(est, t, l, binding, tinfo, dom)
            v = None if f["v"] is None else (f["v"] if l["value"] else (not f["v"]))
            used.append({"lit": l, "ground": g, "fact": f, "v": v})
            cv.append(v)
        vals.append(k_or(cv))
    return k_and(vals)


def bindings(c: dict, est, t: int, base: dict, tinfo: dict, field: str = "exists"):
    ex = c.get(field) or {}
    if not ex:
        yield dict(base)
        return
    scope = (c.get("subject_scope") or "all") if field == "subject" else "all"
    names = list(ex)
    pools = []
    for v in names:
        ty = tinfo.get(ex[v]) if ex[v] in tinfo else ex[v]
        pool = est.pool(t, ty, scope) if ty else []
        if field == "subject":
            # A universal claim about an entity nothing has named is vacuous, so the unnamed token is
            # dropped from the SUBJECT pool. It stays in the existential pool, where its whole purpose
            # is to keep "there is no such thing" from being read as "there is one and it is false".
            pool = [x for x in pool if not is_unnamed(x)]
        pools.append(pool)
    for combo in itertools.product(*pools):
        b = dict(base)
        b.update(dict(zip(names, combo)))
        yield b


def _deps(dec: list) -> dict:
    A, Lr, B = set(), set(), {}
    for u in dec:
        A |= set(u["fact"].get("assumptions") or [])
        Lr |= set(u["fact"].get("learned") or [])
        B[u["fact"]["basis"]] = B.get(u["fact"]["basis"], 0) + 1
    return {"assumptions": sorted(A), "learned": sorted(Lr), "bases": B}


def evaluate(c: dict, cnf, est, t: int, base: dict, tinfo: dict, dom) -> dict:
    """Kleene value of `cnf` at the pre-state of step t, OR-ed over the `exists` bindings."""
    best_t, unknown, all_used = None, [], []
    vals = []
    for b in bindings(c, est, t, base, tinfo):
        used = []
        v = eval_cnf(cnf, est, t, b, tinfo, dom, used)
        vals.append(v)
        all_used.append((b, v, used))
        if v is True and best_t is None:
            best_t = (b, used)
    v = k_or(vals) if vals else False
    if v is True:
        b, used = best_t
        dec = [u for u in used if u["v"] is True]
        wit = {k: x for k, x in b.items() if k.startswith("?")}
    elif v is False:
        dec = [u for (_, vv, used) in all_used for u in used if u["v"] is False]
        wit = None
    else:
        dec = [u for (_, vv, used) in all_used if vv is None for u in used if u["v"] is None]
        wit = None
        unknown = sorted({f"{u['lit']['atom']}({','.join(str(x) for x in u['ground'])}):{u['fact']['basis']}"
                          for u in dec})
    return {"v": v, "witness": wit, "deps": _deps(dec), "unknown": unknown[:12]}


def evaluate_pointwise(c: dict, est, t: int, base: dict, tinfo: dict, dom, only=None) -> list:
    """One row per SUBJECT binding of a `concludes` rule: its conditions, its consequence, and the class.

    This is the difference between "some cell" and "every cell". The conditions keep ALFWorld's
    existential reading over `exists`; the subject is universal, so a single subject binding with True
    conditions and a False consequence refutes the rule.
    """
    rows = []
    subs = ([{**base, list((c.get("subject") or {}))[0]: only}] if only is not None
            else bindings(c, est, t, base, tinfo, "subject"))
    for b in subs:
        cond = evaluate(c, c["conditions"], est, t, b, tinfo, dom)
        cons = evaluate(c, [[c["consequence"]]], est, t, b, tinfo, dom)
        subj = {k: x for k, x in b.items() if k in (c.get("subject") or {})}
        if cond["v"] is None or cons["v"] is None:
            cls = "undetermined"
        elif cond["v"] and cons["v"]:
            cls = "support"
        elif cond["v"] and not cons["v"]:
            cls = "counter"
        elif (not cond["v"]) and cons["v"]:
            cls = "counter_converse" if c.get("direction") == "iff" else "neutral"
        else:
            cls = "consistent"
        rows.append({"subject": subj, "cond": cond, "cons": cons, "class": cls})
    return rows


# ---- models -------------------------------------------------------------------------------------------
def empty_model(dom) -> dict:
    return {"schema": MODEL_SCHEMA, "domain": dom.name, "vocab": dom.version,
            "vocab_digest": dom.digest(), "version": 0, "batch": 0, "assumptions": [], "contracts": [],
            "decisions": [], "parent_sha256": None}


def active(model: dict, kind: str | None = None) -> list:
    return [c for c in model["contracts"]
            if c.get("status") == "active" and (kind is None or c["kind"] == kind)]


def model_sha(model: dict) -> str:
    return sha(canon({k: v for k, v in model.items() if k not in ("sha256", "frozen_utc")}))


def freeze(model: dict, path: str) -> str:
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


def next_id(model: dict, c: dict) -> str:
    k = c["kind"]
    if k in ("requires", "suffices", "effect"):
        pre = f"{c['verb']}.{k}"
    elif k in ("completes", "concludes"):
        pre = f"{k}.{c['scope']}" + (f".{c['consequence']['atom']}" if k == "concludes" else "")
    else:
        pre = f"{k}.{c['state']}"
    n = 1 + sum(1 for x in model["contracts"]
                if x["id"].startswith(pre + ".") and x.get("parent") is None)
    return f"{pre}.{n}"


# ---- repair operations --------------------------------------------------------------------------------
def drop_conjunct(c: dict, index: int) -> dict:
    """DELETE one conjunct of a condition/conditions CNF. ALFWorld's one successful RP-1 repair was a
    deletion; the OfficeQA CC/LN `repair` could only append, so it could not express this."""
    out = copy.deepcopy(c)
    f = "conditions" if c["kind"] == "concludes" else ("guard" if c["kind"] == "persists" else "condition")
    cnf = list(out[f])
    if not 0 <= index < len(cnf):
        raise IndexError(f"conjunct {index} of {len(cnf)}")
    if len(cnf) == 1:
        raise ValueError("a condition cannot be emptied by dropping its only conjunct; retire instead")
    del cnf[index]
    out[f] = cnf
    return out


def narrow(c: dict, extra_conjunct: list) -> dict:
    """ADD one conjunct: the narrowing repair (CC/LN's only one). `extra_conjunct` is a clause (a list of
    literals read disjunctively)."""
    out = copy.deepcopy(c)
    f = "conditions" if c["kind"] == "concludes" else "condition"
    out[f] = list(out[f]) + [list(extra_conjunct)]
    return out


def restrict_scope(c: dict, task_types) -> dict:
    """ADD a context restriction: the third repair shape, used when a rule is right for some task types."""
    out = copy.deepcopy(c)
    out["context"] = {"task_types": sorted(task_types)}
    return out
