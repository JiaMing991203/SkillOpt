"""VX-1 learner requests (amendment A5 section 4-5): system prompts for OLD and EXT, the user message of each batch, the
feedback round, reply parsing and the leak check. No network.

The two system prompts are assembled from the SAME shared blocks; they differ only in the blocks marked VOCAB-SPECIFIC
(state vocabulary, assumption menu, contract format). `prompt_diff()` returns exactly those blocks. Neither prompt
states that a lamp stays on, or what completes a task: the learner may propose such relations or abstain.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402

SHARED_HEAD = """You maintain a small, versioned model of ACTION CONTRACTS and TASK-COMPLETION RELATIONS for a text-based
household environment. You learn it only from the records you are given. You have no other knowledge source about this
environment: do not rely on what you believe its rules are; rely on the records. You may abstain: an unchanged model,
or a relation left unknown, is a valid answer when the records do not decide a question.

## Records
Each record gives a task sentence, its task type, the episode outcome (success or failure), and per step the command
an automated agent issued and the observation text it received. Episode identifiers are arbitrary labels.
Harness rule: an episode ends as soon as its task is complete. So in a SUCCESS the task became complete right after
the LAST listed step and was not complete after any earlier step; in a FAILURE the task was never complete.

## Commands covered (verb identifier: command form; roles: O = object, R = receptacle / appliance / target)
go: "go to R" | take: "take O from R" | move: "move O to R" | open: "open R" | close: "close R" |
heat: "heat O with R" | cool: "cool O with R" | clean: "clean O with R" | use: "use R"
In contracts, "verb" is the identifier (e.g. "go", never "go to"). Other commands (look, inventory, examine X, ...)
are not covered; list them in "uncovered" if they matter.
Outcome of an attempt (computed by code): ok = the observation contains the command's own report sentence (e.g. "You
pick up the O from the R.", "You turn on the R."); fail = the observation is exactly "Nothing happens."; unknown otherwise.

## Task placeholders
For tasks of type look_at_obj_in_light ("look at X under the Y." / "examine the X with the Y."): OTYPE = the type word
X and LTYPE = the type word Y. Use them as type arguments, e.g. is_a(O, OTYPE).
"""

VOCAB_OLD = """## State vocabulary [VOCAB-SPECIFIC]
Each value is true, false or unknown; code computes it from the records, either BEFORE a command (pre-state) or right
AFTER a step (post-state). Arguments must be roles of the contract's verb (for "completes", of its scope verb), or a
type word / placeholder in the second argument of is_a.
- at(R): the agent is at R's location. arrived(R): the latest "You arrive at ..." sentence named R (a record fact).
- holding(O): the agent carries O. holding_any: the agent carries some object.
- in(O,R): O is in/on R. listed(O,R): the latest contents listing of R named O (a record fact).
- open(R): R is open. openable(R): R was reported open or closed at some step.
- heated(O) / cooled(O) / cleaned(O): a successful heat / cool / clean of O is recorded earlier in the episode.
- is_a(X, t): the type word of X's name is t (t may be OTYPE or LTYPE).
Built-in readings: an observation describes the state right after its command. "You arrive at R." means at(R).
A pick-up of O means O is held and in no receptacle; a move of O to R puts O in R. Objects can be listed at two
receptacles (overlapping receptacles), so a listing elsewhere does not make in(O,R) false.

## Model assumptions (adopt or drop; each changes how values are computed, and every value records its basis) [VOCAB-SPECIFIC]
- persistence: a fact established earlier still holds until an observed command changes it (otherwise facts older
  than the immediately preceding observation are unknown).
- init_empty_hands: the agent carries nothing at the start of an episode.
- distinct_locations: the agent is at R's location only if its latest arrival named R.
- listing_complete: a contents listing names every object in/on R at that step.
- openable_reported: an arrival at an openable receptacle always reports it open or closed.

## Contract format (JSON) [VOCAB-SPECIFIC]
{"kind": "requires" | "suffices" | "effect", "verb": "<verb>",
 "condition": [[<lit>, ...], ...],      // requires / suffices: CNF = AND of clauses, each clause an OR of literals
 "effects": [<lit>, ...],               // effect only: literals true right after a successful command
 "context": {"task_types": []}}         // optional; [] = all task types
{"kind": "completes", "scope": "<verb>" | "any", "condition": [[<lit>, ...], ...], "context": {...}}
<lit> = {"atom": "<state atom>", "args": ["O" | "R" | "<type word>" | "OTYPE" | "LTYPE"], "value": true | false}
"""

VOCAB_EXT = """## State vocabulary [VOCAB-SPECIFIC]
Each value is true, false or unknown; code computes it from the records, either BEFORE a command (pre-state) or right
AFTER a step (post-state). Arguments may be roles of the contract's verb (for "completes", of its scope verb), or
VARIABLES declared in the contract's "exists" map (e.g. {"?l": "LTYPE"}: "for some instance of LTYPE"), or, in the second
argument of is_a, a type word / placeholder. A variable ranges over the instances of its type named in the record so
far, plus one instance not yet named.
- at(R): the agent is at R's location (true when its latest arrival named R). arrived(R): the latest "You arrive at
  ..." sentence named R (a record fact).
- holding(X): the agent carries X. holding_any: the agent carries some object.
- in(X,R): X is in/on R. listed(X,R): the latest contents listing of R named X (a record fact).
- open(R): R is open. openable(R): R was reported open or closed at some step.
- heated(X) / cooled(X) / cleaned(X): a successful heat / cool / clean of X is recorded earlier in the episode.
- is_a(X, t): the type word of X's name is t (t may be OTYPE or LTYPE).
- activated(X): an observation "You turn on the X." is recorded at or before this point (a record fact, with its step).
- picked(X): a pick-up of X is recorded at or before this point. released(X): a move of X into a receptacle is recorded.
- on(X): X is switched on. It is TRUE right after the step whose observation reports "You turn on the X.". At later
  points it is computed only through a "persists" rule in the model (see the format); without such a rule it is
  UNKNOWN. It is FALSE only through such a rule's invalidating event, an observed "You turn off the X.", or the
  assumption init_off.
- co_located(X): X is at the agent's place, defined as: holding(X), or for some receptacle R: at(R) and in(X,R).
  (A latest-arrival name alone does not place X with the agent; X's own location must be known.)
Built-in readings: an observation describes the state right after its command. "You arrive at R." means at(R).
A pick-up of X means X is held and in no receptacle; a move of X to R puts X in R. Objects can be listed at two
receptacles (overlapping receptacles), so a listing elsewhere does not make in(X,R) false.

## Model assumptions (adopt or drop; each changes how values are computed, and every value records its basis) [VOCAB-SPECIFIC]
- persistence: a fact established earlier still holds until an observed command changes it (otherwise facts older
  than the immediately preceding observation are unknown). It does not apply to on(X).
- init_empty_hands: the agent carries nothing at the start of an episode.
- distinct_locations: the agent is at R's location only if its latest arrival named R.
- listing_complete: a contents listing names every object in/on R at that step.
- openable_reported: an arrival at an openable receptacle always reports it open or closed.
- init_off: a device is off at the start of an episode (until an activation is recorded).

## Contract format (JSON) [VOCAB-SPECIFIC]
{"kind": "requires" | "suffices", "verb": "<verb>", "condition": [[<lit>, ...], ...], "exists": {...}, "context": {...}}
{"kind": "effect", "verb": "<verb>", "effects": [<lit>, ...], "guard": [[<lit>, ...], ...], "exists": {...}}
      // guard (optional): a CNF that must hold BEFORE the command for the effects to be claimed
{"kind": "completes", "scope": "<verb>" | "any", "condition": [[<lit>, ...], ...], "exists": {...}, "context": {...}}
{"kind": "persists", "state": "on", "established_by": "activated", "invalidated_by": [<events>]}
      // on(X) stays true after a recorded activation of X until the first listed event; events, choose any (or none):
      // "picked" (a pick-up of X), "any_arrival" (the agent arrives anywhere), "arrival_elsewhere" (the agent arrives
      // at a receptacle other than where it was at the activation), "any_step" (the next step), "turn_off" (an
      // observed "You turn off the X."). At most one active persists rule.
"exists" (optional) maps variables to types: {"?o": "OTYPE", "?l": "LTYPE"} (at most 3 variables).
<lit> = {"atom": "<state atom>", "args": ["O" | "R" | "?var" | "<type word>" | "OTYPE" | "LTYPE"], "value": true | false}
"""

SHARED_TAIL = """
## Directions
"requires" = a SUCCESS of the verb implies the condition held before (necessary; a success with the condition false is
a counterexample). "suffices" = the condition held before implies success (a failure with it true is a counterexample).
"effect" = after a success, each effect literal holds (a false one is a counterexample).
"completes" = for every step in its scope (steps with that verb, or every step for "any"), the task is complete right
after the step EXACTLY when the condition holds then: true at a non-completion point is a counterexample (a false
completion), false at the completion point is a counterexample (a missed completion). Unknown is neither.

## How your proposals are checked (by code, not by you)
Every added or revised item is scored against ALL records seen so far, not only the new ones. It is rejected if it has
ANY determinate counterexample, or (except persists rules) no supporting case. Unknown values never count as support
or counterexample. A contract that keeps a counterexample after this update becomes inactive ("refuted") with the
counterexample recorded. Adopting an assumption or a persists rule is refused if the records contradict it directly, or
if it would create counterexamples for active contracts you do not revise in the same proposal.

## What to do
Read the new records, the frozen model's predictions on them (made before each observation was revealed), and the
discrepancies. Then propose operations:
- "add" an item supported by the records; "revise" an existing item (keep its kind and verb / scope / state) when a
  counterexample shows it is wrong or too strong, explaining what the counterexample shows; "retire" an item that no
  longer helps; "keep" (or nothing) when the evidence does not call for a change.
- "adopt" / "drop" an assumption, with the reason.
- Do NOT force changes. Prefer fewer, well-supported items. If the vocabulary cannot express what you see, say so in
  "uncovered" instead of writing an approximate item.
- When a discrepancy could be explained either by a wrong item or by a wrong value (an assumption or a misread
  observation), say so in the reason.
- Cite witnesses as step ids exactly as given (e.g. "B2-e04@7").

## Output
Return ONE JSON object inside a ```json fenced block, with exactly these keys:
{"assumption_ops": [{"op": "adopt"|"drop", "name": "...", "reason": "..."}],
 "contract_ops": [{"op": "add", "contract": {...}, "reason": "...", "witnesses": ["..."]},
                  {"op": "revise", "target": "<id>", "contract": {...}, "reason": "...", "witnesses": ["..."]},
                  {"op": "retire" | "keep", "target": "<id>", "reason": "..."}],
 "uncovered": ["<what you could not model>"],
 "notes": "<short>"}
"""


def system_prompt(vname: str) -> str:
    return SHARED_HEAD + "\n" + {"OLD": VOCAB_OLD, "EXT": VOCAB_EXT}[vname] + SHARED_TAIL


def prompt_diff() -> dict:
    return {"shared_head_sha": XV.sha(SHARED_HEAD), "shared_tail_sha": XV.sha(SHARED_TAIL),
            "OLD_specific_sha": XV.sha(VOCAB_OLD), "EXT_specific_sha": XV.sha(VOCAB_EXT),
            "OLD_sha": XV.sha(system_prompt("OLD")), "EXT_sha": XV.sha(system_prompt("EXT"))}


# ---- leak check ---------------------------------------------------------------------------------------------------
FORBIDDEN = ["admissible", "Looking quickly around you", "You are in the middle of a room", "## Skill Knowledge",
             "expert", "pddl", "PDDL", "json_2.1.1", "trial_T", "game.tw", "(:action", "receptacleAtLocation",
             "atLocation", "Now it's your turn", "variant", "N-loc", "N-obj", "N-off", "N-drop", "lamp_stays_on",
             "same_arrival_place", "clause (e)", "ORD-", "ReadingR3", "rev 2", "rev2", "scripted"]
FORBIDDEN_RE = [re.compile(r"\bwon\b"), re.compile(r"\bstays? on\b"), re.compile(r"\bremains? on\b")]


class LeakError(RuntimeError):
    pass


def leak_check(messages: list) -> None:
    for m in messages:
        txt = m["content"]
        for f in FORBIDDEN:
            if f in txt:
                raise LeakError(f"learner request contains forbidden material {f!r}: nothing sent")
        for rx in FORBIDDEN_RE:
            if rx.search(txt):
                raise LeakError(f"learner request matches forbidden pattern {rx.pattern!r}: nothing sent")


# ---- user message ----------------------------------------------------------------------------------------------------
def episode_text(ep: dict) -> str:
    lines = [f"### Episode {ep['episode_id']}  (task type {ep['task_type']}; outcome {ep['outcome']})",
             f"Task: {ep['task']}"]
    for st in ep["steps"]:
        lines.append(f"[{st['i']}] {st['action']}  ->  {st['obs']}")
    return "\n".join(lines)


def model_text(model: dict) -> str:
    rows = []
    for c in model["contracts"]:
        if c.get("status") not in ("active", "refuted"):
            continue
        e = c.get("evidence") or {}
        body = {k: c[k] for k in ("kind", "verb", "scope", "state", "established_by", "invalidated_by", "condition",
                                  "effects", "guard", "exists", "context") if k in c}
        rows.append(f"- {c['id']} v{c.get('version', 1)} [{c['status']}] {IR.render(c)}\n  json: {json.dumps(body)}\n"
                    f"  evidence so far: {e.get('n_support', 0)} supporting, {e.get('n_consistent', 0)} consistent, "
                    f"{e.get('n_counter', 0)} counterexamples {e.get('counter', [])[:4] if e.get('n_counter') else ''}, "
                    f"{e.get('undetermined', 0)} undetermined")
    return "\n".join(rows) or "(no items yet: the model is empty)"


SYM = {"COMPLETE": "C", "NOT": "n", "UNKNOWN": "?", "CONFLICT": "!", "NO_CLAIM": "-"}


def prediction_lines(ep: dict, pre: list, post: list) -> list:
    lab = ["C" if (ep["outcome"] == "success" and i == len(ep["steps"]) - 1) else "n" for i in range(len(ep["steps"]))]
    out = [f"- {ep['episode_id']}: completion before each step  [{' '.join(SYM[p['completion']['verdict']] for p in pre)}]",
           f"  {' ' * len(ep['episode_id'])}  completion after each step   [{' '.join(SYM[v['verdict']] for v in post)}]",
           f"  {' ' * len(ep['episode_id'])}  recorded (from the outcome)  [{' '.join(lab)}]"]
    for i, (p, v) in enumerate(zip(pre, post)):
        ap = p["action_prediction"]["label"]
        if ap in ("fail", "conflict") or (v["verdict"] == "COMPLETE") != (lab[i] == "C") or \
                (lab[i] == "C" and v["verdict"] != "COMPLETE"):
            out.append(f"    @{i} '{p['action']}': action prediction {ap}; after-step verdict {v['verdict']}"
                       + (f"; unknown values {v['unknown'][:4]}" if v["unknown"] else "")
                       + (f"; relied on {v['deps']['learned'] + v['deps']['assumptions']}" if v["verdict"] == "COMPLETE" else ""))
    return out


def build_request(vname: str, batch: int, n_batches: int, model: dict, new_eps: list, preds: dict, posts: dict,
                  earlier: dict) -> list:
    A = model.get("assumptions") or []
    parts = [f"Batch {batch} of at most {n_batches}. Frozen model before this batch: version {model['version']}, "
             f"assumptions {A or 'none'}.", "", "## Current model", model_text(model), "",
             "## New records (this batch)"]
    for ep in new_eps:
        parts += [episode_text(ep), ""]
    parts += ["## The frozen model's completion verdicts on the new records",
              "Symbols per step: C = complete, n = not complete, ? = unknown, ! = conflicting relations, - = no relation "
              "applies. 'before each step' = predicted before the command ran (from learned effects); 'after each step' "
              "= computed after its observation. Lines '@i' list steps where a verdict disagrees with the outcome, "
              "and failed-action predictions.", ""]
    for ep in new_eps:
        parts += prediction_lines(ep, preds[ep["episode_id"]], posts[ep["episode_id"]])
    parts += ["", "## Earlier records (all earlier batches; the code rechecks every proposal against them)",
              earlier.get("text") or "- none"]
    parts += ["", "Propose operations (or none) following the system instructions. Return one JSON object in a "
              "```json block."]
    msgs = [{"role": "system", "content": system_prompt(vname)}, {"role": "user", "content": "\n".join(parts)}]
    leak_check(msgs)
    return msgs


def feedback_request(prev_msgs: list, reply: str, decisions: list) -> list:
    rej = [d for d in decisions if d.get("decision") == "rejected"]
    lines = ["The code checked your proposal against all records so far. Rejected operations:"]
    for d in rej:
        lines.append(f"- {d.get('type')} {d.get('op')} {d.get('target') or d.get('name') or ''}: "
                     f"{'; '.join(d.get('why') or [])}")
    lines += ["Accepted operations are already applied. You may send corrected versions of the rejected operations "
              "(or none). Same JSON format; do not repeat accepted operations."]
    msgs = prev_msgs + [{"role": "assistant", "content": reply or ""}, {"role": "user", "content": "\n".join(lines)}]
    leak_check(msgs)
    return msgs


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
    if not isinstance(out["assumption_ops"], list) or not isinstance(out["contract_ops"], list):
        raise ValueError("operations must be lists")
    return out
