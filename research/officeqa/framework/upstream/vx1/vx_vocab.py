"""VX-1 vocabularies: the EXISTING one (OLD = lc.vocab.v1 atoms) and the researcher-designed EXTENSION (EXT = lc.vocab.x1).

Amendment A5 (docs/research/VX1_AMENDMENT_A5.md) section 3. This is a RESEARCHER-DESIGNED vocabulary extension; the
learner chooses hypotheses inside it but did not discover the vocabulary.

Every element carries one CATEGORY:
  TASK        a requirement defined by the task sentence (OTYPE / LTYPE placeholders, the task form)
  OBS         a direct observation / record fact (event atoms with their source steps; listings; arrivals)
  MACHINERY   researcher-supplied extraction or mathematics (parsers, three-valued logic, typed existential variables,
              the derived co_located relation, the evaluation of persistence rules, timing of facts)
  LEARNED     an empirical hypothesis proposed by the learner and gated by code (contracts of every kind)
  ASSUMPTION  an unresolved named assumption the learner may adopt or drop (menu below); never established knowledge

Shared study interface (BOTH conditions; NOT part of the deployed lc.vocab.v1, documented as an unavoidable addition):
  * the kind `completes` (a candidate completion relation), scoped to one verb or to every step ("any");
  * the task placeholders OTYPE / LTYPE as type words (from the task sentence).
EXT only:
  * typed existential entity variables ("?o", "?l", ...) declared in a contract's "exists" map (args beyond the command's
    roles);
  * event atoms activated(X) / picked(X) / released(X) (record facts with source steps);
  * the state atom on(X) (an observed activation makes it true AT that step; later only through a LEARNED `persists`
    rule; UNKNOWN otherwise; False only through a persists rule's invalidation, an observed turn-off, or `init_off`);
  * the derived relation co_located(X) := holding(X) or exists R. at(R) and in(X, R)  (MACHINERY definition; its value
    follows the three-valued values of at / in / holding under the model's assumptions);
  * guards on effect contracts; the kind `persists`; the assumption `init_off`.
Nothing here states that lamps stay on, or what completes a task: those are for the learner to propose (or abstain).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
for _p in (os.path.join(RESEARCH, "learned_contracts"), os.path.join(RESEARCH, "trace_only")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import vocab as V1  # noqa: E402  (frozen lc.vocab.v1; read-only use)

NAME = V1.NAME
VERBS = V1.VERBS
type_of = V1.type_of
parse_command = V1.parse_command
outcome_of = V1.outcome_of

TURN_OFF = re.compile(r"You turn off the (" + NAME + r")\.")


def parse_obs(obs: str) -> list:
    """lc.vocab.v1 parser + one EXT extraction rule: 'You turn off the X.' (never seen in the records so far; the v1
    parser leaves it as a raw sentence). Used by BOTH conditions' estimator (OLD has no atom that reads it)."""
    out = []
    for a in V1.parse_obs(obs):
        if a.get("kind") == "raw":
            m = TURN_OFF.fullmatch((a.get("text") or "").strip())
            if m:
                a = {"kind": "turn_off", "obj": m.group(1), "quote": a.get("quote")}
        out.append(a)
    return out


def task_info(task: str) -> dict | None:
    """TASK: the task form and its placeholders, from the task sentence only."""
    p = V1.parse_task(task)
    if p is None:
        return None
    if p["form"] == "light":
        return {"form": "light", "OTYPE": p["object_type"], "LTYPE": p["target_type"]}
    return {"form": p["form"], "OTYPE": p["object_type"], "RTYPE": p["target_type"]}


PLACEHOLDERS = ("OTYPE", "LTYPE")
VAR_RE = re.compile(r"\?[a-z][a-z0-9_]*")

# atom -> (argument kinds, category, one-line meaning). Argument kinds: O / R role-typed (OLD: must be the verb's role
# of that name), X any entity, TYPE a type word or placeholder.
ATOMS_OLD = {
    "at": (("R",), "MACHINERY", "the actor is at R's location (true iff the latest arrival named R)"),
    "arrived": (("R",), "OBS", "the latest 'You arrive at ...' sentence named R"),
    "holding": (("O",), "MACHINERY", "the actor carries O (latest pick-up / move / inventory report about O)"),
    "holding_any": ((), "MACHINERY", "the actor carries some object"),
    "in": (("O", "R"), "MACHINERY", "O is in/on R (latest pick-up / move / listing about O and R)"),
    "listed": (("O", "R"), "OBS", "the latest contents listing of R named O"),
    "open": (("R",), "MACHINERY", "R is open (latest open/close report)"),
    "openable": (("R",), "MACHINERY", "R was reported open or closed at some step"),
    "heated": (("O",), "OBS", "a successful heat of O is recorded earlier"),
    "cooled": (("O",), "OBS", "a successful cool of O is recorded earlier"),
    "cleaned": (("O",), "OBS", "a successful clean of O is recorded earlier"),
    "is_a": (("X", "TYPE"), "MACHINERY", "the type word of X's name is TYPE"),
}
ATOMS_EXT = {k: (tuple("X" if a in ("O", "R") else a for a in v[0]), v[1], v[2]) for k, v in ATOMS_OLD.items()}
ATOMS_EXT.update({
    "activated": (("X",), "OBS", "an activation of X is recorded at or before this state ('You turn on the X.'), "
                                 "with its source step"),
    "picked": (("X",), "OBS", "a pick-up of X is recorded at or before this state, with its source step"),
    "released": (("X",), "OBS", "a move/put of X into a receptacle is recorded at or before this state"),
    "on": (("X",), "LEARNED-DEPENDENT", "X is switched on: TRUE at the step whose observation reports its activation; "
                                       "at later steps only through an adopted `persists` rule; otherwise UNKNOWN"),
    "co_located": (("X",), "MACHINERY", "X is at the actor's place: held, or in/on some R with at(R)"),
})
EVENT_ATOMS = ("activated", "picked", "released")

KINDS_OLD = ("requires", "suffices", "effect", "completes")
KINDS_EXT = ("requires", "suffices", "effect", "completes", "persists")

ASSUMPTIONS_OLD = dict(V1.ASSUMPTIONS)
ASSUMPTIONS_EXT = dict(V1.ASSUMPTIONS)
ASSUMPTIONS_EXT["init_off"] = "a device is off at the start of an episode (until an activation is recorded)"

# persists rules (EXT): state on(X) established by activated(X); the learner picks invalidating events from this menu
PERSIST_STATES = {"on": "activated"}
INVALIDATORS = {
    "picked": "a pick-up of X is recorded after the activation",
    "any_arrival": "the actor arrives anywhere after the activation",
    "arrival_elsewhere": "the actor arrives at a receptacle other than the one of its latest arrival at the activation",
    "any_step": "any later step (the state is not carried beyond the activation's own observation)",
    "turn_off": "an observation 'You turn off the X.' after the activation",
}

VOCABS = {
    "OLD": {"version": "vx.vocab.old (lc.vocab.v1 atoms + shared completes interface)", "atoms": ATOMS_OLD,
            "kinds": KINDS_OLD, "assumptions": ASSUMPTIONS_OLD, "variables": False, "guards": False},
    "EXT": {"version": "vx.vocab.x1 (lc.vocab.x1)", "atoms": ATOMS_EXT, "kinds": KINDS_EXT,
            "assumptions": ASSUMPTIONS_EXT, "variables": True, "guards": True},
}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha_file(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def vocab_digest(name: str) -> str:
    v = VOCABS[name]
    return sha(canon({"version": v["version"], "atoms": {k: list(a) for k, a in v["atoms"].items()},
                      "kinds": list(v["kinds"]), "assumptions": v["assumptions"], "variables": v["variables"],
                      "guards": v["guards"], "verbs": {k: list(s["roles"]) for k, s in VERBS.items()},
                      "invalidators": INVALIDATORS if name == "EXT" else None, "placeholders": list(PLACEHOLDERS)}))


def element_table() -> list:
    """Every vocabulary element with its category and the condition(s) that contain it (for the report)."""
    rows = []
    for k, (args, cat, mean) in ATOMS_EXT.items():
        rows.append({"element": f"{k}({','.join(args)})" if args else k, "category": cat, "meaning": mean,
                     "in_OLD": k in ATOMS_OLD, "in_EXT": True})
    rows += [
        {"element": "OTYPE / LTYPE", "category": "TASK", "meaning": "object and lamp type words of the task sentence",
         "in_OLD": True, "in_EXT": True},
        {"element": "kind completes", "category": "LEARNED (kind supplied)", "meaning": "candidate completion relation "
         "(biconditional within its scope)", "in_OLD": True, "in_EXT": True},
        {"element": "?vars + exists", "category": "MACHINERY", "meaning": "typed existential entity variables",
         "in_OLD": False, "in_EXT": True},
        {"element": "effect guard", "category": "LEARNED", "meaning": "a pre-state condition on an effect contract",
         "in_OLD": False, "in_EXT": True},
        {"element": "kind persists", "category": "LEARNED (kind supplied)", "meaning": "on(X) persists after "
         "activated(X) until a chosen invalidator", "in_OLD": False, "in_EXT": True},
    ]
    rows += [{"element": f"assumption {k}", "category": "ASSUMPTION", "meaning": v, "in_OLD": k in ASSUMPTIONS_OLD,
              "in_EXT": True} for k, v in ASSUMPTIONS_EXT.items()]
    rows += [{"element": f"invalidator {k}", "category": "LEARNED (menu supplied)", "meaning": v, "in_OLD": False,
              "in_EXT": True} for k, v in INVALIDATORS.items()]
    return rows
