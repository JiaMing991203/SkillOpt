"""Stage contracts on top of the learned action contracts (offline prototype; USER request 2026-11-10 night).

What it is: a deterministic reader of ONE analyst-visible prefix (task sentence + steps whose observations are
visible + optionally the next command, whose observation is not yet visible). It tracks STAGES, each defined by the
result it hands forward to a later stage, bound to one object INSTANCE (a name such as "cup 2"). No engine rule, no
admissible list, no room listing, no actor reasoning text.

Stages (sources: TASK = the task sentence; OBS = an observation sentence; REC = a fact about the record itself;
ASSUMED(x) = needs the named assumption x; RULE = a learned action contract, evaluated separately by the driver):

  heat task ("heat some cup and put it in cabinet"), instance o, destination d
    FIND(o)       requires  type(o) = task object type                                   [TASK; type = name word]
                  establishes  o listed at receptacle r at step k                         [OBS]
                  needed by  ACQUIRE(o)
    ACQUIRE(o)    requires  type(o) = task object type [TASK]; in(o,r) and not holding_any [RULE take.requires.1]
                  establishes  holding(o) from the pick-up step k                         [OBS at k; later ASSUMED(persistence)]
                  needed by  HEAT(o) [RULE heat.requires.1 holding(O)], DELIVER(o) [RULE move.requires.1 holding(O)]
    HEAT(o)       requires  holding(o) of the SAME instance                               [handoff from ACQUIRE(o)]
                  establishes  "a successful heat of o is recorded at step k"            [REC; NOT a temperature claim]
                  needed by  DELIVER(o)
    DELIVER(o,d)  requires  holding(o); a heat of the SAME o recorded earlier; type(d) = target type [TASK]
                  establishes  in(o,d) from the move step k                               [OBS at k; later ASSUMED(persistence)]
                  needed by  GOAL
    GOAL          o of the task type in d of the target type, and o hot. "Hot" is never observed in the record:
                  the stage layer supports GOAL only as "placement after a recorded heat of the same instance".
  two-object task ("put two cup in microwave"): slots 1 and 2 need DISTINCT instances [TASK: "two"]
    per slot FIND(o_i) -> ACQUIRE(o_i) -> DELIVER(o_i,d) as above (no transformation)
    progress = the distinct instances of the task type whose placement in a target-type receptacle is CURRENTLY
    supported: established by the move report [OBS], carried forward under ASSUMED(persistence: single actor, only
    an observed command changes where an object is), withdrawn by an observed pick-up of that instance [OBS],
    confirmed by a later listing of d that names it [OBS], and marked CONFLICT (not counted) when a later listing of
    d omits it.

Stage-only claims (keys; each with status attempted = supported BEFORE the action from its command / established =
supported by the resulting observation / decided):
    redo_mismatched:<o>     a take / heat / move of an instance o that is NOT of the task type, after o was already
                            delivered into a target-type receptacle: the failed handoff is ACQUIRE's type check, the
                            open stage is FIND(task type), and redoing later stages on o cannot discharge it
    find_known_contents     while FIND(task type) is open and the record shows nothing held (searching), a 'go to R'
                            whose latest listing named no instance of the
                            task type (other than instances already placed) and into which no such instance was moved
                            since: going there cannot discharge FIND [ASSUMED persistence + listing_complete]
    recovery:<o>            a take of a task-type instance o after an earlier chain on a non-task-type instance
    heat_not_transferred:<o> a delivery of task-type o when a heat is recorded only for ANOTHER instance
    removal_of_placed:<o>   a take of a task-type instance whose placement in a target-type receptacle is supported:
                            the supported count drops by one
    goal_supported          the goal is supported (heat: placement after a recorded heat of the same instance;
                            two: two distinct supported placements), with the temperature caveat for heat
Unknown object locations stay unknown: nothing here infers where an unseen object is.
"""
from __future__ import annotations

FORMS_WITH_OBJECT = ("heat", "cool", "clean", "simple", "two")
TRANSFORM_DONE = {"heat": "heat_done", "cool": "cool_done", "clean": "clean_done"}

STAGE_DEFS = {
    "heat": [
        {"stage": "FIND(o)", "requires": [("type(o) = task object type", "TASK")],
         "establishes": ("o listed at a receptacle r at step k", "OBS"), "needed_by": "ACQUIRE(o)"},
        {"stage": "ACQUIRE(o)", "requires": [("type(o) = task object type", "TASK"),
                                             ("in(o,r) and not holding_any", "RULE take.requires.1 v1")],
         "establishes": ("holding(o) from the pick-up step", "OBS at the pick-up; ASSUMED persistence later"),
         "needed_by": "HEAT(o) [RULE heat.requires.1 v1: holding(O)], DELIVER(o) [RULE move.requires.1 v1: holding(O)]"},
        {"stage": "HEAT(o)", "requires": [("holding(o), same instance as ACQUIRE", "handoff; OBS + ASSUMED persistence"),
                                          ("holding(O) and arrived(R)", "RULE heat.requires.1 v1")],
         "establishes": ("a successful heat of o is RECORDED at step k (no temperature claim)", "REC"),
         "needed_by": "DELIVER(o)"},
        {"stage": "DELIVER(o,d)", "requires": [("holding(o)", "handoff; RULE move.requires.1 v1"),
                                               ("heat of the same o recorded earlier", "handoff; REC"),
                                               ("type(d) = target type", "TASK")],
         "establishes": ("in(o,d) from the move step", "OBS at the move; ASSUMED persistence later"),
         "needed_by": "GOAL"},
        {"stage": "GOAL", "requires": [("type(o) = task type, type(d) = target type", "TASK"),
                                       ("o hot", "NOT OBSERVABLE in the record: kept unknown")],
         "establishes": ("supported only as 'placement after a recorded heat of the same instance'", "derived"),
         "needed_by": "-"},
    ],
    "two": [
        {"stage": "FIND(o_i)", "requires": [("type(o_i) = task object type", "TASK"),
                                            ("o_2 differs from o_1", "TASK ('two')")],
         "establishes": ("o_i listed at a receptacle r at step k", "OBS"), "needed_by": "ACQUIRE(o_i)"},
        {"stage": "ACQUIRE(o_i)", "requires": [("in(o,r) and not holding_any", "RULE take.requires.1 v1")],
         "establishes": ("holding(o_i)", "OBS at the pick-up; ASSUMED persistence later"),
         "needed_by": "DELIVER(o_i,d) [RULE move.requires.1 v1: holding(O)]"},
        {"stage": "DELIVER(o_i,d)", "requires": [("holding(o_i)", "handoff; RULE move.requires.1 v1"),
                                                 ("type(d) = target type", "TASK")],
         "establishes": ("in(o_i,d): counted while supported", "OBS at the move; ASSUMED persistence later; "
                         "withdrawn by an observed pick-up; CONFLICT if a later listing of d omits it"),
         "needed_by": "GOAL (count of distinct supported placements = 2)"},
    ],
}


def track(goal: dict, vis: list, pending: dict | None, type_of) -> dict:
    """goal = vocab.parse_task(task); vis = parsed visible steps (i, action, obs, cmd, atoms, outcome);
    pending = {i, action, cmd} of the next command (observation NOT visible) or None.
    Returns {"claims": {key: claim}, "timeline": [...], "snap": {...}} computed from this prefix only."""
    form, ot, tt = goal["form"], goal["object_type"], goal["target_type"]
    need = 2 if form == "two" else 1
    tdone = TRANSFORM_DONE.get(form)
    C: dict = {}
    TL: list = []          # (t, phase, instance, stage, change, evidence_step, basis)

    def add(key, cat, status, step, **kw):
        c = C.get(key)
        if c is None:
            c = C[key] = dict(key=key, cat=cat, status=status, steps=[], **kw)
        if step not in c["steps"]:
            c["steps"].append(step)
        if status == "established":
            c["status"] = "established"
        return c

    held: dict = {}          # obj -> step of the pick-up (current record reading; persistence)
    heat: dict = {}          # obj -> [steps] of recorded transformations of the task's kind
    listing: dict = {}       # r -> (step, items) latest listing of r
    moved_in: dict = {}      # r -> [(step, obj)] moved into r after its latest listing
    placed: dict = {}        # obj -> {recep, step, basis, confirmed}  placements in TARGET-type receptacles
    seen_task: dict = {}     # task-type instance -> (step, recep) first listing
    delivered: dict = {}     # obj -> [steps] of moves into a target-type receptacle
    picked_other = []        # (step, obj) pick-ups of non-task-type objects
    goal_step = None

    def supported():
        return sorted(o for o, p in placed.items() if type_of(o) == ot and p["basis"] != "conflict")

    def find_open():
        if form not in FORMS_WITH_OBJECT:
            return False
        sup = supported()
        if len(sup) >= need:
            return False
        avail = [o for o in seen_task if o not in sup and o not in held]
        return not avail and not any(type_of(o) == ot for o in held)

    seq = list(vis) + ([dict(pending, pending=True)] if pending else [])
    for s in seq:
        k, cmd, pend = s["i"], s["cmd"], s.get("pending", False)
        status = "attempted" if pend or s["outcome"] != "ok" else "established"
        v, args = cmd.get("verb"), cmd.get("args") or {}
        O, R = args.get("O"), args.get("R")
        if cmd["kind"] == "covered":
            # ---- PRE-action checks (the command of step k + observations of steps < k)
            if v == "go" and find_open() and not held and R in listing:  # searching: hands empty (record reading)
                lk, items = listing[R]
                sup = set(supported())
                has = [x for x in items if type_of(x) == ot and x not in sup]
                mv = [(j, x) for j, x in moved_in.get(R, []) if type_of(x) == ot and x not in sup]
                if not has and not mv:
                    add("find_known_contents", "find_known_contents", "attempted", k).setdefault("occ", []).append(
                        {"t": k, "recep": R, "listing_step": lk, "listing": list(items),
                         "moved_in_since": [x for _, x in moved_in.get(R, [])]})
            if v in ("take", "heat", "cool", "clean", "move") and O and form in FORMS_WITH_OBJECT:
                if type_of(O) != ot and delivered.get(O):
                    add(f"redo_mismatched:{O}", "redo_mismatched", status, k, obj=O,
                        earlier_delivery=list(delivered[O])).setdefault("occ", []).append((k, v))
            if v == "take" and O and type_of(O) == ot and picked_other and \
                    not any(c["cat"] == "recovery" and c["obj"] != O for c in C.values()):
                add(f"recovery:{O}", "recovery", status, k, obj=O, earlier=[list(x) for x in picked_other])
            if v == "take" and O and O in placed and type_of(O) == ot and placed[O]["recep"] == R:
                n0 = len(supported())
                add(f"removal_of_placed:{O}", "removal_of_placed", status, k, obj=O, recep=R).setdefault(
                    "occ", []).append((k, n0, n0 - 1))
            if v == "move" and O and tdone and type_of(O) == ot and type_of(R) == tt and not heat.get(O):
                others = sorted(x for x in heat if type_of(x) == ot and x != O)
                if others:
                    add(f"heat_not_transferred:{O}", "heat_not_transferred", status, k, obj=O, others=others)
        if pend:
            break
        if s["outcome"] == "fail":
            continue
        # ---- POST: update from this step's observation
        for a in s["atoms"]:
            kd = a["kind"]
            if kd in ("see_on", "see_in"):
                r = a["recep"]
                listing[r] = (k, list(a["items"]))
                moved_in[r] = []
                for it in a["items"]:
                    if type_of(it) == ot and it not in seen_task and form in FORMS_WITH_OBJECT:
                        seen_task[it] = (k, r)
                        TL.append((k, "post", it, "FIND", "established", k, "OBS"))
                for o, p in placed.items():
                    if p["recep"] == r and p["basis"] != "conflict":
                        if o in a["items"]:
                            p["confirmed"].append(k)
                            TL.append((k, "post", o, "DELIVER", "confirmed by listing", k, "OBS"))
                        else:
                            p["basis"] = "conflict"
                            TL.append((k, "post", o, "DELIVER", "CONFLICT: listing omits it", k, "OBS"))
            elif kd == "pick_up":
                o = a["obj"]
                held[o] = k
                TL.append((k, "post", o, "ACQUIRE", "established", k, "OBS"))
                if type_of(o) != ot:
                    picked_other.append((k, o))
                if o in placed:
                    del placed[o]
                    TL.append((k, "post", o, "DELIVER", "withdrawn (picked up)", k, "OBS"))
            elif kd == "move_done":
                o, r = a["obj"], a["recep"]
                if o in held:
                    del held[o]
                    TL.append((k, "post", o, "ACQUIRE", "ended (moved)", k, "OBS"))
                moved_in.setdefault(r, []).append((k, o))
                if type_of(r) == tt and form in FORMS_WITH_OBJECT:
                    placed[o] = {"recep": r, "step": k, "basis": "observed", "confirmed": []}
                    delivered.setdefault(o, []).append(k)
                    TL.append((k, "post", o, "DELIVER", f"established in {r}", k, "OBS"))
            elif kd == "carrying":
                for o in list(held):
                    if o not in a["items"]:
                        del held[o]
                        TL.append((k, "post", o, "ACQUIRE", "withdrawn (carrying report)", k, "OBS"))
                for o in a["items"]:
                    held.setdefault(o, k)
            elif tdone and kd == tdone:
                o = a["obj"]
                heat.setdefault(o, []).append(k)
                TL.append((k, "post", o, form.upper(), "recorded", k, "REC"))
        # ---- goal support after this step
        sup = supported()
        if form in FORMS_WITH_OBJECT and goal_step is None:
            if tdone:
                ok_o = [o for o in sup if heat.get(o) and min(heat[o]) < placed[o]["step"]]
            else:
                ok_o = sup
            if len(ok_o) >= need:
                goal_step = k
                add("goal_supported", "goal_supported", "established", k, objs=ok_o,
                    heat_steps={o: heat.get(o, []) for o in ok_o})
    snap = {"held": dict(held), "heat": {o: list(v) for o, v in heat.items()},
            "placed": {o: dict(p) for o, p in placed.items()}, "supported": supported(),
            "seen_task": dict(seen_task), "delivered": {o: list(v) for o, v in delivered.items()},
            "find_open": find_open(), "goal_step": goal_step}
    return {"claims": C, "timeline": TL, "snap": snap}


def chains(goal: dict, steps: list, type_of) -> list:
    """Every DELIVER event (move into a target-type receptacle) with its handoff requirements evaluated from the
    record before that step: same-instance acquisition, a recorded transformation of the same instance, type checks.
    Used for the final stage table of the completed trace."""
    form, ot, tt = goal["form"], goal["object_type"], goal["target_type"]
    tdone = TRANSFORM_DONE.get(form)
    out = []
    for k, s in enumerate(steps):
        for a in s["atoms"]:
            if a["kind"] != "move_done" or type_of(a["recep"]) != tt:
                continue
            o = a["obj"]
            picks = [j for j in range(k) for b in steps[j]["atoms"] if b["kind"] == "pick_up" and b["obj"] == o]
            moves = [j for j in range(k) for b in steps[j]["atoms"] if b["kind"] == "move_done" and b["obj"] == o]
            p = picks[-1] if picks else None
            held = p is not None and not any(m > p for m in moves)
            tr = [j for j in range(k) for b in steps[j]["atoms"] if tdone and b["kind"] == tdone and b["obj"] == o]

            def held_at(j):  # o held just before step j: a pick-up of o before j and no move of o between
                pj = [x for x in picks if x < j]
                return bool(pj) and not any(pj[-1] < m < j for m in moves)
            tr_held = [j for j in tr if held_at(j)]
            out.append({"deliver": k, "obj": o, "recep": a["recep"], "acquire": p, "held_at_deliver": held,
                        "transform_steps": tr, "transform_while_held": tr_held,
                        "type_ok": type_of(o) == ot, "target_ok": True,
                        "complete_for_task": type_of(o) == ot and held and (bool(tr_held) if tdone else True)})
    return out


def plain_tally(goal: dict, steps: list, type_of) -> dict:
    """The plain report's placement tally (as the USER defined it): distinct task-type instances ever OBSERVED placed
    into a target-type receptacle, and directly observed removals (a pick-up of such an instance from that
    receptacle after its placement). Removals are flagged, not netted."""
    ot, tt = goal["object_type"], goal["target_type"]
    placed, removals, where = [], [], {}
    for k, s in enumerate(steps):
        for a in s["atoms"]:
            if a["kind"] == "move_done" and type_of(a["obj"]) == ot and type_of(a["recep"]) == tt:
                if a["obj"] not in placed:
                    placed.append(a["obj"])
                where[a["obj"]] = a["recep"]
            elif a["kind"] == "pick_up" and where.get(a["obj"]) == a["recep"]:
                removals.append((k, a["obj"], a["recep"]))
                where.pop(a["obj"])
    return {"distinct_placed": placed, "removals": removals}
