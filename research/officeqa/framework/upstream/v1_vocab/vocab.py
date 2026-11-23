"""Frozen vocabulary of the learned-contract prototype: commands, outcomes, state atoms, named assumptions.

Design v0.1 sections 3-4. Everything here is read off the ANALYST-VISIBLE record only: the task sentence, the task
type, and per step the executed command and the observation text. No admissible-command list, no opening room
listing, no engine rule, no game file.

Observation sentences are parsed by the transferred, round-trip-tested parser research/trace_only/tor_record.py.

Commands (verb -> typed roles).  O = object argument, R = receptacle / appliance / target argument.
    go to R | take O from R | move O to R (also the older "put O in/on R") | open R | close R
    heat O with R | cool O with R | clean O with R | use R
Other commands (look, inventory, examine X, help, anything unparsed) are INFORMATION or UNKNOWN commands and are
reported as UNCOVERED: no contract may be written for them in this vocabulary.

Outcome of one attempt (documented reading, same as the earlier action-rules study's O1):
    ok       the observation contains the verb's own report sentence with the same arguments
             (go: "You arrive at R."; take: "You pick up the O from the R."; move: "You move the O to the R.";
              open/close: "You open/close the R."; heat/cool/clean: "You heat/cool/clean the O using the R.";
              use: "You turn on the R.")
    fail     the observation is exactly "Nothing happens."
    unknown  anything else (outcome not determinable from the record)

State atoms (ground over entity names; all are THREE-valued: True / False / None = unknown):
    at(R)        the actor is at R's location                                   [state]
    arrived(R)   the latest "You arrive at ..." sentence named R (F before any) [record; always determinate]
    holding(O)   the actor carries O                                            [state]
    holding_any  the actor carries some object                                  [state]
    in(O,R)      O is in/on R                                                   [state]
    listed(O,R)  the latest contents listing of R named O (None: R never listed) [record]
    open(R)      R is open                                                      [state]
    openable(R)  R opens and closes (reported open/closed at some step)         [static]
    heated(O) / cooled(O) / cleaned(O)   a successful heat/cool/clean of O is recorded earlier in this episode
                                         (a RECORD fact; it does not claim the object's temperature/cleanliness)
    is_a(X, t)   the type word of X's name is t (e.g. is_a(R, microwave))     [static, from the name]

Built-in readings (always on; stated, not learned):
    B1  An observation describes the state right after the command of the same step, i.e. the pre-state of the
        next command. A fact read from the IMMEDIATELY preceding observation is "observed-current".
    B2  "You arrive at R." puts the actor at R:  arrived(R) -> at(R).
    B3  A pick-up of O means O is held, hence in no receptacle; a move of O to R puts O in R. Listings do NOT
        exclude other receptacles: the saved records show an object listed at two receptacles (pan 1 on
        stoveburner 1 and on countertop 1, at alternating steps, with no command moving it: overlapping receptacles).
    B4  A "You are carrying: ..." report is complete for that step.

Named assumptions (part of a MODEL; adopted or dropped by the learner; each changes the state estimate and is
recorded as the basis of every value it licenses):
    persistence          a fact established at an earlier step still holds until an observed command changes it
                         (single-actor world). Without it, facts older than the preceding observation are unknown.
    init_empty_hands     the actor carries nothing at the start of an episode.
    distinct_locations   the actor is at R's location only if its latest arrival named R (receptacles with
                         different names are at different locations; before any arrival it is at none).
    listing_complete     a contents listing names every object in/on R at that step (unlisted -> in(O,R) false).
    openable_reported    an arrival at an openable receptacle always reports it open or closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
for _p in (os.path.join(RESEARCH, "trace_only"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import tor_record as TR  # noqa: E402  (transferred; round-trip tested)

VOCAB_VERSION = "lc.vocab.v1"
NAME = r"[a-z]+ \d+"

VERBS = {
    "go": {"patterns": [r"go to (" + NAME + r")"], "roles": ("R",), "report": ("arrive", {"recep": "R"})},
    "take": {"patterns": [r"take (" + NAME + r") from (" + NAME + r")"], "roles": ("O", "R"),
             "report": ("pick_up", {"obj": "O", "recep": "R"})},
    "move": {"patterns": [r"move (" + NAME + r") to (" + NAME + r")", r"put (" + NAME + r") (?:in|on|in/on) (" + NAME + r")"],
             "roles": ("O", "R"), "report": ("move_done", {"obj": "O", "recep": "R"})},
    "open": {"patterns": [r"open (" + NAME + r")"], "roles": ("R",), "report": ("open_done", {"recep": "R"})},
    "close": {"patterns": [r"close (" + NAME + r")"], "roles": ("R",), "report": ("close_done", {"recep": "R"})},
    "heat": {"patterns": [r"heat (" + NAME + r") with (" + NAME + r")"], "roles": ("O", "R"),
             "report": ("heat_done", {"obj": "O", "appliance": "R"})},
    "cool": {"patterns": [r"cool (" + NAME + r") with (" + NAME + r")"], "roles": ("O", "R"),
             "report": ("cool_done", {"obj": "O", "appliance": "R"})},
    "clean": {"patterns": [r"clean (" + NAME + r") with (" + NAME + r")"], "roles": ("O", "R"),
              "report": ("clean_done", {"obj": "O", "appliance": "R"})},
    "use": {"patterns": [r"use (" + NAME + r")"], "roles": ("R",), "report": ("turn_on", {"obj": "R"})},
}
_COMPILED = {v: [re.compile(p) for p in s["patterns"]] for v, s in VERBS.items()}
INFO_VERBS = ("look", "inventory", "examine", "help")

# atom name -> argument kinds ("O"/"R" role-typed entity arguments; "TYPE" a type word; "X" any entity)
ATOMS = {
    "at": ("R",), "arrived": ("R",), "holding": ("O",), "holding_any": (), "in": ("O", "R"), "listed": ("O", "R"),
    "open": ("R",), "openable": ("R",), "heated": ("O",), "cooled": ("O",), "cleaned": ("O",), "is_a": ("X", "TYPE"),
}
STATIC_ATOMS = ("openable", "is_a")
RECORD_ATOMS = ("arrived", "listed", "heated", "cooled", "cleaned")
ASSUMPTIONS = {
    "persistence": "a fact established at an earlier step still holds until an observed command changes it",
    "init_empty_hands": "the actor carries nothing at the start of an episode",
    "distinct_locations": "the actor is at R's location only if its latest arrival named R",
    "listing_complete": "a contents listing names every object in/on R at that step",
    "openable_reported": "an arrival at an openable receptacle always reports it open or closed",
}
TASK_TYPES = ("pick_and_place_simple", "pick_two_obj_and_place", "look_at_obj_in_light",
              "pick_heat_then_place_in_recep", "pick_cool_then_place_in_recep", "pick_clean_then_place_in_recep")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha_file(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def type_of(name: str | None) -> str | None:
    if not name:
        return None
    return name.rsplit(" ", 1)[0] if re.fullmatch(NAME, name) else None


def parse_command(action: str) -> dict:
    """Command string -> {"verb", "args": {role: name}, "kind": covered|info|unknown}."""
    a = (action or "").strip().lower()
    for verb, rxs in _COMPILED.items():
        for rx in rxs:
            m = rx.fullmatch(a)
            if m:
                return {"verb": verb, "args": dict(zip(VERBS[verb]["roles"], m.groups())), "kind": "covered",
                        "text": a}
    first = a.split(" ", 1)[0] if a else ""
    return {"verb": first or None, "args": {}, "kind": "info" if first in INFO_VERBS else "unknown", "text": a}


def parse_obs(obs: str) -> list:
    return TR.parse_obs(obs or "")


def outcome_of(cmd: dict, atoms: list) -> str:
    """ok / fail / unknown (documented reading above)."""
    if cmd.get("kind") != "covered":
        return "unknown"
    kinds = [x["kind"] for x in atoms]
    if kinds == ["nothing_happens"]:
        return "fail"
    rk, rargs = VERBS[cmd["verb"]]["report"]
    for x in atoms:
        if x["kind"] == rk and all(x.get(f) == cmd["args"][role] for f, role in rargs.items()):
            return "ok"
    return "unknown"


TASK_FORMS = [  # task sentence templates (from the task text only) -> roles of the abstract procedure
    (re.compile(r"put (?:a|an|some) (?:hot|warm) ([a-z]+) (?:in|on|in/on) ([a-z]+)\.?"), "heat"),
    (re.compile(r"heat (?:a|an|some) ([a-z]+) and put it (?:in|on|in/on) ([a-z]+)\.?"), "heat"),
    (re.compile(r"put (?:a|an|some) cool ([a-z]+) (?:in|on|in/on) ([a-z]+)\.?"), "cool"),
    (re.compile(r"cool (?:a|an|some) ([a-z]+) and put it (?:in|on|in/on) ([a-z]+)\.?"), "cool"),
    (re.compile(r"put (?:a|an|some) clean ([a-z]+) (?:in|on|in/on) ([a-z]+)\.?"), "clean"),
    (re.compile(r"clean (?:a|an|some) ([a-z]+) and put it (?:in|on|in/on) ([a-z]+)\.?"), "clean"),
    (re.compile(r"(?:put|find) two ([a-z]+) (?:and put them )?(?:in|on|in/on) ([a-z]+)\.?"), "two"),
    (re.compile(r"put (?:a|an|some) ([a-z]+) (?:in|on|in/on) ([a-z]+)\.?"), "simple"),
    # article optional (fixed 2026-11-10: saved records contain "look at alarmclock under the desklamp.")
    (re.compile(r"(?:look at|examine) (?:(?:a|an|the|some) )?([a-z]+) (?:under|with) the ([a-z]+)\.?"), "light"),
]


def parse_task(task: str) -> dict | None:
    t = (task or "").strip().lower()
    for rx, form in TASK_FORMS:
        m = rx.fullmatch(t)
        if m:
            return {"form": form, "object_type": m.group(1), "target_type": m.group(2)}
    return None


APPLIANCE_OF = {"heat": "microwave", "cool": "fridge", "clean": "sinkbasin"}


def vocab_digest() -> str:
    """Digest of everything the learner and checker share (frozen with each model)."""
    return sha(canon({"version": VOCAB_VERSION, "verbs": {v: {"patterns": s["patterns"], "roles": s["roles"],
                                                                  "report": s["report"]} for v, s in VERBS.items()},
                      "atoms": ATOMS, "assumptions": ASSUMPTIONS, "info": INFO_VERBS}))
