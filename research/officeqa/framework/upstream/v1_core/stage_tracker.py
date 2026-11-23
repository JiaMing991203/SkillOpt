"""S: the stage report of the 12-task stage study (amendment A2): P plus explicit stage contracts for all six task
types. Everything is computed from ONE prefix through the shared reading (stage_common.Reading).

Stage definitions (MANUALLY SUPPLIED by the implementation session; their order coincides with S0 lines 17-22).
Source tags: TASK = the task sentence; MANUAL = the supplied procedure (including the mechanics it states);
OBS = an observation; REC = a fact about the record; ASSUMED(x) = a declared assumption; RULE = a learned action
contract (attached only by C).
"""
from __future__ import annotations

import plain_report as PR
import stage_common as SC

_FIND = {"stage": "FIND(o)", "requires": [("type(o) = requested type", "TASK")],
         "establishes": ("o named by a listing of receptacle r at step k", "OBS"),
         "needed_by": "ACQUIRE(o)"}
_ACQ = {"stage": "ACQUIRE(o)", "requires": [("type(o) = requested type", "TASK"),
                                            ("o listed at r (FIND)", "OBS + ASSUMED persistence")],
        "establishes": ("holding(o) from the pick-up step", "OBS at the pick-up; ASSUMED persistence later"),
        "needed_by": "{next}(o)"}


def _tr(kind):
    return {"stage": f"{kind.upper()}(o)",
            "requires": [("holding(o): the SAME instance as ACQUIRE", "MANUAL (an object is held while it is "
                          f"{kind}ed); OBS + ASSUMED persistence, init_empty_hands")],
            "establishes": (f"a successful {kind} of o is RECORDED at step k (never a claim about o's "
                            "physical state)", "REC"),
            "needed_by": "DELIVER(o)"}


def _del(tr):
    req = [("holding(o)", "MANUAL (an object is held when it is moved); OBS + ASSUMED persistence")]
    if tr:
        req.append((f"a {tr} of the SAME o recorded before the move", "TASK ('" + tr + " ... and put it'; "
                    "'it' = the same instance) + REC"))
    req.append(("type(d) = destination type", "TASK"))
    return {"stage": "DELIVER(o,d)", "requires": req,
            "establishes": ("in(o,d) from the move step", "OBS; ASSUMED persistence later; withdrawn by an observed "
                            "pick-up; CONFLICT if a later listing of d omits o"),
            "needed_by": "GOAL"}


def stage_defs(form: str) -> list:
    if form in ("heat", "cool", "clean"):
        a = dict(_ACQ, needed_by=f"{form.upper()}(o), DELIVER(o)")
        return [_FIND, a, _tr(form), _del(form),
                {"stage": "GOAL", "requires": [("an instance of the requested type placed in d", "TASK"),
                                               (f"o is {form}ed (hot / cold / clean)", "NOT OBSERVABLE: kept unknown")],
                 "establishes": (f"supported only as 'placement after a recorded {form} of the same instance'",
                                 "derived (REC + OBS)"), "needed_by": "-"}]
    if form == "simple":
        return [_FIND, dict(_ACQ, needed_by="DELIVER(o)"), _del(None),
                {"stage": "GOAL", "requires": [("an instance of the requested type placed in d", "TASK")],
                 "establishes": ("a supported placement", "OBS + ASSUMED persistence"), "needed_by": "-"}]
    if form == "two":
        return [dict(_FIND, stage="FIND(o_i)", requires=[("type(o_i) = requested type", "TASK"),
                                                         ("o_2 differs from o_1", "TASK ('two')")]),
                dict(_ACQ, stage="ACQUIRE(o_i)", needed_by="DELIVER(o_i)"), dict(_del(None), stage="DELIVER(o_i,d)"),
                {"stage": "GOAL", "requires": [("two DISTINCT instances with supported placements", "TASK")],
                 "establishes": ("netted count = 2", "OBS + ASSUMED persistence"), "needed_by": "-"}]
    if form == "light":
        return [_FIND, dict(_ACQ, needed_by="INSPECT(o)"),
                {"stage": "FIND(lamp)", "requires": [("type(l) = lamp type named by the task", "TASK")],
                 "establishes": ("l named by a listing of receptacle r", "OBS"), "needed_by": "INSPECT(o)"},
                {"stage": "INSPECT(o,l)", "requires": [("holding(o) when l is used", "MANUAL + OBS + ASSUMED "
                                                        "persistence"),
                                                       ("at l's place (the latest arrival is where l was listed)",
                                                        "MANUAL; kept unknown when l's place is not observed")],
                 "establishes": ("'l turned on while o is held' RECORDED at step k (not a claim that l stays on)",
                                 "REC"), "needed_by": "GOAL"},
                {"stage": "GOAL", "requires": [("o examined under the lamp", "TASK")],
                 "establishes": ("supported only as the recorded INSPECT event", "REC"), "needed_by": "-"}]
    raise ValueError(form)


# ------------------------------------------------------------------ open stages (a status, recomputed per prefix)
def open_stages(R: SC.Reading) -> list:
    """the next stage needed for each unfinished slot, with the bound instance when one exists"""
    sp, T = R.sp, R.T
    ot = sp["otype"]
    if sp["form"] == "light":
        if R.goal_now():
            return []
        held = sorted(o for o in R.held if T(o) == ot)
        if held:
            lamps = sorted({(o, r) for o, (k, r) in R.listed_at.items() if T(o) == sp["lamp"]})
            if lamps:
                return [f"INSPECT({held[0]}): use {lamps[0][0]} at {lamps[0][1]} while holding {held[0]}"]
            return [f"FIND({sp['lamp']}) while holding {held[0]}"]
        av = R.available()
        return [f"ACQUIRE({av[0][0]}) at {av[0][1]}"] if av else [f"FIND({ot})"]
    done = R.goal_now()
    out, used = [], set(done)
    for _ in range(sp["count"] - len(done)):
        nxt = None
        for o in sorted(R.held):
            if T(o) == ot and o not in used:
                tr = sp["transform"]
                nxt = (o, f"{tr.upper()}({o})" if tr and not R.transformed_before(o, 10 ** 6) else f"DELIVER({o})")
                break
        if nxt is None:
            for o, r, _ in R.available():
                if o not in used:
                    nxt = (o, f"ACQUIRE({o}) at {r}")
                    break
        if nxt is None:
            for o in R.supported():
                if o not in used:
                    nxt = (o, f"ACQUIRE({o}) again (placed without a recorded {sp['transform']})")
                    break
        if nxt is None:
            out.append(f"FIND({ot})")
            continue
        used.add(nxt[0])
        out.append(nxt[1])
    return out


def find_targets(R: SC.Reading) -> list:
    """types whose FIND stage is open now: the requested object type when no instance is held, available or
    supported for an unfinished slot; for an examine task also the lamp type when no lamp was ever listed"""
    sp, T = R.sp, R.T
    out = []
    for x in open_stages(R):
        if x.startswith("FIND(") and not x.startswith(f"FIND({sp['lamp']})"):
            out.append(sp["otype"])
            break
    if sp["form"] == "light" and not any(T(o) == sp["lamp"] for o in R.seen):
        out.append(sp["lamp"])
    return out


def analyze(goal, vis, pending, type_of) -> dict:
    """{key: finding}: P's findings (unchanged) + the stage claims."""
    F = dict(PR.analyze(goal, vis, pending, type_of))
    sp = SC.spec_of(goal)
    ot = sp["otype"]
    C: dict = {}
    add = PR._adder(C)
    pend_t = pending["i"] if pending else None

    def pre(s, R, status):
        k, cmd = s["i"], s["cmd"]
        v, args = cmd.get("verb"), cmd.get("args") or {}
        O, Rc = args.get("O"), args.get("R")
        if cmd["kind"] != "covered":
            return
        if v == "go":
            tg = find_targets(R)
            # (1) EXTENDED: revisiting a receptacle whose latest listing named no instance of a type being searched
            for typ in tg:
                if typ == ot and R.held:
                    continue
                if Rc not in R.listing:
                    continue
                L = R.listing[Rc]
                sup = set(R.supported())
                has = [x for x in L["items"] if type_of(x) == typ and x not in sup]
                mv = [x for _, x in R.moved_in.get(Rc, []) if type_of(x) == typ]
                if not has and not mv:
                    oc = add(f"find_known_contents:{typ}", "find_known_contents", "attempted", k, target=typ,
                             tier="extended", layer="S").setdefault("occ", [])
                    oc.append({"t": k, "recep": Rc, "listing_step": L["step"], "listing": list(L["items"]),
                               "scope": L["scope"],
                               "open_at_listing": L["scope"] == "listing of the open receptacle" or None,
                               "contradicted": None})
            # (2) PRIMARY: leaving a receptacle reported closed and never opened while FIND is open
            if R.arrival and tg and (ot not in tg or not R.held):
                ka, A = R.arrival
                st = R.openstate.get(A)
                if A != Rc and st and st[1] is False and st[0] >= ka and A not in R.listing:
                    add(f"find_unopened_left:{A}", "find_unopened_left", "attempted", k, recep=A, targets=list(tg),
                        layer="S").setdefault("occ", []).append({"t": k, "closed_step": st[0], "arrival": ka})
            # (3) going elsewhere while a requested instance is known available, nothing held, a slot unfinished
            if not R.held:
                if len(R.goal_now()) < (1 if sp["form"] == "light" else sp["count"]):
                    for o, r, kl in R.available():
                        if r != Rc:
                            add(f"acquire_available_skipped:{o}", "acquire_available_skipped", "attempted", k, obj=o,
                                layer="S").setdefault("occ", []).append({"t": k, "listed_at": r, "listing_step": kl,
                                                                         "go_to": Rc})
        if v in ("take", "heat", "cool", "clean", "move") and O and sp["form"] != "light":
            # (4) redo on an instance whose ACQUIRE type check failed, after it was already delivered
            if type_of(O) != ot and any(R.is_dest(r) for _, r in R.moves.get(O, [])):
                add(f"redo_mismatched:{O}", "redo_mismatched", status, k, obj=O, layer="S",
                    earlier_delivery=[j for j, r in R.moves[O] if R.is_dest(r)]).setdefault("occ", []).append((k, v))
        if v in ("heat", "cool", "clean", "move") and O:
            # (5) handoff: TRANSFORM / DELIVER need ACQUIRE of the same instance
            if O not in R.held:
                c = add(f"handoff_unmet:{v}:{O}", "handoff_unmet", "attempted", k, obj=O, verb=v, layer="S")
                c.setdefault("occ", []).append({"t": k, "last_pick": (R.picks.get(O) or [None])[-1],
                                                "last_move": (R.moves.get(O) or [(None,)])[-1][0],
                                                "observed": None})

    def post(s, R):
        k = s["i"]
        for c in C.values():
            if c["cat"] == "find_known_contents":
                for oc in c["occ"]:
                    if oc["t"] == k:
                        L = R.listing.get(oc["recep"])
                        if L and L["step"] == k:
                            new = [x for x in L["items"] if type_of(x) == c["target"] and x not in R.supported()]
                            oc["contradicted"] = new or None
            elif c["cat"] == "handoff_unmet":
                for oc in c["occ"]:
                    if oc["t"] == k:
                        oc["observed"] = s["outcome"]

    PR.walk(goal, vis, pending, type_of, pre, post)
    for c in C.values():
        if c["cat"] == "handoff_unmet" and any(oc["observed"] == "ok" for oc in c["occ"]):
            c["contradicted"] = [oc["t"] for oc in c["occ"] if oc["observed"] == "ok"]
        if c["cat"] == "find_known_contents":
            c["contradicted"] = [oc["t"] for oc in c["occ"] if oc["contradicted"]]
    assert not set(C) & set(F)
    F.update(C)
    return F


def status(goal, vis, type_of) -> dict:
    """S's stage block at a prefix: open stages, per-instance stage results, timeline of placement events."""
    sp = SC.spec_of(goal)
    R = SC.read_prefix(sp, vis, type_of)
    ot = sp["otype"]
    inst = {}
    for o in sorted(set(R.seen) | set(R.picks) | set(R.transforms) | set(R.moves)):
        if type_of(o) != ot and not (R.transforms.get(o) or any(R.is_dest(r) for _, r in R.moves.get(o, []))):
            continue
        inst[o] = {"type_ok": type_of(o) == ot, "find": R.seen.get(o), "picks": R.picks.get(o, []),
                   "held": o in R.held, "transforms": [list(x) for x in R.transforms.get(o, [])],
                   "placement": dict(R.placed[o]) if o in R.placed else None}
    lamps = sorted({(o, r) for o, (k, r) in R.listed_at.items() if sp["lamp"] and type_of(o) == sp["lamp"]})
    unopened = sorted(r for r, (k, st) in R.openstate.items() if st is False and r not in R.listing)
    return {"open": open_stages(R), "instances": inst, "lamps": lamps, "goal_step": R.goal_step,
            "goal_objs": list(R.goal_objs), "pevents": [list(e) for e in R.pevents],
            "closed_never_listed": unopened, "uses": [list(u) for u in R.uses]}
