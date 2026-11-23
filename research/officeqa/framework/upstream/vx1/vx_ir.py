"""VX-1 contracts (amendment A5 section 3.3): schema, validation per vocabulary, rendering, three-valued evaluation.

Kinds (LEARNED hypotheses; the kinds themselves are supplied):
  requires   {verb, condition[, exists, context]}        success of the verb  -> condition held before (necessary)
  suffices   {verb, condition[, exists, context]}        condition held before -> success
  effect     {verb, effects[, guard, exists, context]}   success (and guard before, EXT) -> each literal holds after
  completes  {scope, condition[, exists, context]}       scope = a verb or "any": for every step in scope, the task is
                                                         complete right after the step  <=>  condition holds then
  persists   {state: "on", established_by: "activated", invalidated_by: [...]}   (EXT only)
lit = {"atom", "args", "value"}; args are command roles ("O"/"R"; OLD and EXT), declared variables "?x" (EXT), and for
TYPE positions a lowercase type word or a placeholder OTYPE / LTYPE (both).
Evaluation is Kleene three-valued; an `exists` variable ranges over the entities of its type named so far plus one
unnamed instance (vx_state). Every value carries the assumptions and learned rule ids it depends on.
"""
from __future__ import annotations

import copy
import itertools
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_vocab as XV  # noqa: E402
from vx_state import k_and, k_or  # noqa: E402

MODEL_SCHEMA = "vx.model.v1"
TYPE_WORD = re.compile(r"[a-z]+")


# ---- validation -------------------------------------------------------------------------------------------------
def _roles(c: dict) -> tuple:
    verb = c.get("verb") if c.get("kind") != "completes" else (c.get("scope") if c.get("scope") != "any" else None)
    return tuple(XV.VERBS[verb]["roles"]) if verb in XV.VERBS else ()


def validate_lit(lit, c: dict, vname: str) -> list:
    V = XV.VOCABS[vname]
    if not isinstance(lit, dict) or set(lit) - {"atom", "args", "value"} or "atom" not in lit:
        return [f"literal {lit!r}: must be {{atom, args, value}}"]
    atom = lit["atom"]
    if atom not in V["atoms"]:
        return [f"literal {lit!r}: unknown atom {atom!r} (vocabulary: {sorted(V['atoms'])})"]
    kinds = V["atoms"][atom][0]
    args = lit.get("args", [])
    if not isinstance(args, list) or len(args) != len(kinds):
        return [f"literal {lit!r}: {atom} takes {len(kinds)} argument(s)"]
    errs = [] if isinstance(lit.get("value"), bool) else [f"literal {lit!r}: value must be true or false"]
    roles, ex = _roles(c), (c.get("exists") or {})
    for kind, a in zip(kinds, args):
        if kind == "TYPE":
            if not (isinstance(a, str) and (a in XV.PLACEHOLDERS or TYPE_WORD.fullmatch(a))):
                errs.append(f"literal {lit!r}: a type argument must be a lowercase type word or OTYPE / LTYPE")
        elif isinstance(a, str) and XV.VAR_RE.fullmatch(a):
            if not V["variables"]:
                errs.append(f"literal {lit!r}: variables like {a!r} are not part of this vocabulary")
            elif a not in ex:
                errs.append(f"literal {lit!r}: variable {a!r} is not declared in \"exists\"")
        elif a not in roles:
            errs.append(f"literal {lit!r}: argument {a!r} is not a role of {c.get('verb') or c.get('scope')} {roles}"
                        + (" or a declared variable" if V["variables"] else ""))
        elif not V["variables"] and kind in ("O", "R") and a != kind:
            errs.append(f"literal {lit!r}: {atom} expects role {kind} here, got {a}")
    return errs


def _cnf_errs(cnf, c, vname, what="condition") -> list:
    if not isinstance(cnf, list) or not cnf or not all(isinstance(cl, list) and cl for cl in cnf):
        return [f"{what} must be a non-empty list of non-empty clauses (CNF)"]
    return [e for cl in cnf for l in cl for e in validate_lit(l, c, vname)]


def validate(c, vname: str) -> list:
    V = XV.VOCABS[vname]
    if not isinstance(c, dict):
        return ["contract must be an object"]
    kind = c.get("kind")
    if kind not in V["kinds"]:
        return [f"unknown kind {kind!r} (kinds: {list(V['kinds'])})"]
    allowed = {"requires": {"verb", "condition", "exists", "context", "assumptions"},
               "suffices": {"verb", "condition", "exists", "context", "assumptions"},
               "effect": {"verb", "effects", "guard", "exists", "context", "assumptions"},
               "completes": {"scope", "condition", "exists", "context", "assumptions"},
               "persists": {"state", "established_by", "invalidated_by", "context", "assumptions"}}[kind]
    extra = set(c) - allowed - {"kind", "id", "reason", "witnesses"}
    errs = [f"unexpected field(s) {sorted(extra)} for kind {kind}"] if extra else []
    ex = c.get("exists")
    if ex is not None:
        if not V["variables"]:
            errs.append("\"exists\" (typed variables) is not part of this vocabulary")
        elif not isinstance(ex, dict) or not all(isinstance(k, str) and XV.VAR_RE.fullmatch(k) and isinstance(v, str)
                                                 and (v in XV.PLACEHOLDERS or TYPE_WORD.fullmatch(v)) for k, v in ex.items()):
            errs.append("\"exists\" must map variables like \"?l\" to OTYPE / LTYPE or a lowercase type word")
        elif len(ex) > 3:
            errs.append("at most 3 variables per contract")
    if kind in ("requires", "suffices", "effect") and c.get("verb") not in XV.VERBS:
        return errs + [f"unknown verb {c.get('verb')!r} (covered verbs: {sorted(XV.VERBS)})"]
    if kind in ("requires", "suffices"):
        errs += _cnf_errs(c.get("condition"), c, vname)
    elif kind == "effect":
        eff = c.get("effects")
        if not isinstance(eff, list) or not eff:
            errs.append("effect contract needs a non-empty effects list")
        else:
            errs += [e for l in eff for e in validate_lit(l, c, vname)]
        if c.get("guard") is not None:
            if not V["guards"]:
                errs.append("effect guards are not part of this vocabulary")
            else:
                errs += _cnf_errs(c["guard"], c, vname, "guard")
    elif kind == "completes":
        sc = c.get("scope")
        if sc != "any" and sc not in XV.VERBS:
            errs.append(f"completes scope must be a covered verb or \"any\", got {sc!r}")
        errs += _cnf_errs(c.get("condition"), c, vname)
    elif kind == "persists":
        if c.get("state") not in XV.PERSIST_STATES:
            errs.append(f"persists state must be one of {sorted(XV.PERSIST_STATES)}")
        elif c.get("established_by") != XV.PERSIST_STATES[c["state"]]:
            errs.append(f"persists {c['state']} is established by {XV.PERSIST_STATES[c['state']]}")
        inv = c.get("invalidated_by")
        if not isinstance(inv, list) or any(i not in XV.INVALIDATORS for i in inv):
            errs.append(f"invalidated_by must be a list drawn from {sorted(XV.INVALIDATORS)} (may be empty)")
    ctx = c.get("context") or {}
    if not isinstance(ctx, dict) or set(ctx) - {"task_types"}:
        errs.append("context must be {task_types: [...]}")
    for a in c.get("assumptions") or []:
        if a not in V["assumptions"]:
            errs.append(f"unknown assumption {a!r}")
    return errs


def normalize(c: dict) -> dict:
    out = {"kind": c["kind"], "context": {"task_types": sorted((c.get("context") or {}).get("task_types") or [])}}
    L = lambda l: {"atom": l["atom"], "args": list(l.get("args") or []), "value": bool(l["value"])}  # noqa: E731
    if c["kind"] in ("requires", "suffices", "effect"):
        out["verb"] = c["verb"]
    if c["kind"] in ("requires", "suffices", "completes"):
        out["condition"] = [[L(l) for l in cl] for cl in c["condition"]]
    if c["kind"] == "effect":
        out["effects"] = [L(l) for l in c["effects"]]
        if c.get("guard"):
            out["guard"] = [[L(l) for l in cl] for cl in c["guard"]]
    if c["kind"] == "completes":
        out["scope"] = c["scope"]
    if c["kind"] == "persists":
        out.update(state=c["state"], established_by=c["established_by"], invalidated_by=sorted(set(c["invalidated_by"])))
    if c.get("exists"):
        out["exists"] = dict(sorted(c["exists"].items()))
    return out


def semantic_sha(c: dict) -> str:
    return XV.sha(XV.canon(normalize(c)))


def applies_task(c: dict, task_type) -> bool:
    tts = (c.get("context") or {}).get("task_types") or []
    return not tts or task_type in tts


def render_lit(l: dict) -> str:
    a = l.get("args") or []
    s = f"{l['atom']}({','.join(a)})" if a else l["atom"]
    return s if l["value"] else "not " + s


def render_cnf(cnf) -> str:
    return " and ".join("(" + " or ".join(render_lit(l) for l in cl) + ")" if len(cl) > 1 else render_lit(cl[0])
                        for cl in cnf)


HEADS = {"go": "go to R", "take": "take O from R", "move": "move O to R", "open": "open R", "close": "close R",
         "heat": "heat O with R", "cool": "cool O with R", "clean": "clean O with R", "use": "use R"}


def render(c: dict) -> str:
    ctx = (c.get("context") or {}).get("task_types") or []
    scope = f" [task types: {', '.join(ctx)}]" if ctx else ""
    ex = c.get("exists") or {}
    q = ("for some " + ", ".join(f"{v}:{t}" for v, t in ex.items()) + ": ") if ex else ""
    k = c["kind"]
    if k == "requires":
        return f"'{HEADS[c['verb']]}' succeeds only if {q}{render_cnf(c['condition'])}{scope}"
    if k == "suffices":
        return f"if {q}{render_cnf(c['condition'])}, then '{HEADS[c['verb']]}' succeeds{scope}"
    if k == "effect":
        g = f" when {render_cnf(c['guard'])} held before" if c.get("guard") else ""
        return f"after a successful '{HEADS[c['verb']]}'{g}: {q}" + " and ".join(render_lit(l) for l in c["effects"]) + scope
    if k == "completes":
        w = "after any step" if c["scope"] == "any" else f"after a '{HEADS[c['scope']]}' step"
        return f"{w}, the task is complete exactly when {q}{render_cnf(c['condition'])}{scope}"
    if k == "persists":
        inv = ", ".join(c["invalidated_by"]) or "nothing recorded"
        return f"on(X) holds from a recorded activation of X until {inv}{scope}"
    return str(c)


# ---- evaluation ---------------------------------------------------------------------------------------------------
def ground(arg: str, kind: str, binding: dict, tinfo: dict):
    if kind == "TYPE":
        return tinfo.get(arg) if arg in XV.PLACEHOLDERS else arg
    return binding.get(arg)


def lit_fact(est, t: int, lit: dict, binding: dict, tinfo: dict, vname: str) -> tuple:
    kinds = XV.VOCABS[vname]["atoms"][lit["atom"]][0]
    g = [ground(a, k, binding, tinfo) for k, a in zip(kinds, lit["args"])]
    if any(x is None for x in g):
        from state import fact
        return g, fact(None, "none")
    return g, est.value(t, lit["atom"], g)


def eval_cnf(cnf, est, t, binding, tinfo, vname, used: list) -> object:
    vals = []
    for cl in cnf:
        cv = []
        for l in cl:
            g, f = lit_fact(est, t, l, binding, tinfo, vname)
            v = None if f["v"] is None else (f["v"] if l["value"] else (not f["v"]))
            used.append({"lit": l, "ground": g, "fact": f, "v": v})
            cv.append(v)
        vals.append(k_or(cv))
    return k_and(vals)


def bindings(c: dict, est, t: int, base: dict, tinfo: dict):
    ex = c.get("exists") or {}
    if not ex:
        yield dict(base)
        return
    names = list(ex)
    pools = []
    for v in names:
        ty = tinfo.get(ex[v]) if ex[v] in XV.PLACEHOLDERS else ex[v]
        pools.append(est.entities_of_type(t, ty) if ty else [])
    for combo in itertools.product(*pools):
        b = dict(base)
        b.update(dict(zip(names, combo)))
        yield b


def evaluate(c: dict, cnf, est, t: int, base: dict, tinfo: dict, vname: str) -> dict:
    """Kleene value of `cnf` (a field of contract c) at the pre-state of command t, OR over the exists bindings.
    Returns {v, witness, deps: {assumptions, learned, bases}, unknown: [ground literal keys]}."""
    best_t, unknown, all_used = None, [], []
    vals = []
    for b in bindings(c, est, t, base, tinfo):
        used = []
        v = eval_cnf(cnf, est, t, b, tinfo, vname, used)
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
        unknown = sorted({f"{u['lit']['atom']}({','.join(str(x) for x in u['ground'])}):{u['fact']['basis']}" for u in dec})
    A, Lr, B = set(), set(), {}
    for u in dec:
        A |= set(u["fact"].get("assumptions") or [])
        Lr |= set(u["fact"].get("learned") or [])
        B[u["fact"]["basis"]] = B.get(u["fact"]["basis"], 0) + 1
    return {"v": v, "witness": wit, "deps": {"assumptions": sorted(A), "learned": sorted(Lr), "bases": B},
            "unknown": unknown[:12]}


# ---- models ----------------------------------------------------------------------------------------------------
def empty_model(vname: str) -> dict:
    return {"schema": MODEL_SCHEMA, "vocab": vname, "vocab_digest": XV.vocab_digest(vname), "version": 0, "batch": 0,
            "assumptions": [], "contracts": [], "decisions": [], "parent_sha256": None}


def active(model: dict, kind: str | None = None) -> list:
    return [c for c in model["contracts"] if c.get("status") == "active" and (kind is None or c["kind"] == kind)]


def model_sha(model: dict) -> str:
    return XV.sha(XV.canon({k: v for k, v in model.items() if k not in ("sha256", "frozen_utc")}))


def freeze(model: dict, path: str) -> str:
    import datetime
    import json
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
    import json
    m = json.load(open(path))
    if model_sha(m) != m.get("sha256"):
        raise RuntimeError(f"{path}: content does not match its frozen digest")
    return m


def next_id(model: dict, c: dict) -> str:
    head = c.get("verb") or (f"completes.{c['scope']}" if c["kind"] == "completes" else c["kind"])
    pre = f"{head}.{c['kind']}" if c["kind"] in ("requires", "suffices", "effect") else head
    n = 1 + sum(1 for x in model["contracts"] if x["id"].startswith(pre + ".") and x.get("parent") is None)
    return f"{pre}.{n}"
