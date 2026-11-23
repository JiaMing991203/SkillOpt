"""Shared record reading for the 12-task stage diagnostic study (amendment A2). P, S and C all use exactly this.

What it is: an incremental reader of ONE analyst-visible prefix (condition A: task sentence + executed commands and
their observations). It keeps what the record says about object instances, holdings, recorded transformations,
listings, open/closed reports, lamp uses and placements. It never reads command lists, actor reasoning, engine rules,
hidden state or later steps.

Declared observational assumptions (shared by every variant; listed on every finding that uses them):
  name_reading      an entity's type is its name word ("cup 2" -> cup); equal names denote the same instance
  persistence       a fact established at an earlier step still holds until an observed command changes it
                    (single actor)
  init_empty_hands  nothing is held before the first step
Assumption used ONLY by EXTENDED claims (never by a primary finding):
  listing_complete  a contents listing names every object in / on that receptacle at that step

Netted placement semantics (shared; amendment A2 2.4.1). For every object moved into a receptacle of the task's
destination type: the move report ESTABLISHES the placement [OBS]; it is carried forward under persistence; an
observed pick-up of that object WITHDRAWS it [OBS]; moving it there again RESTORES one placement (keyed by instance,
never double counted); a later listing of that receptacle that names it CONFIRMS it [OBS]; a later listing that omits
it marks it CONFLICT (not counted). Progress counts are the requested-type instances with a supported placement.

Recorded events vs properties: a "heat / cool / clean of o recorded at step k" is a REC fact, never "o is hot /
cold / clean"; "the lamp was turned on at step k" is a REC fact, never "the lamp is on now".
"""
from __future__ import annotations

ASSUMPTIONS = {
    "name_reading": "an entity's type is its name word; equal names denote the same instance",
    "persistence": "a fact established earlier still holds until an observed command changes it (single actor)",
    "init_empty_hands": "nothing is held before the first step",
}
EXTENDED_ASSUMPTIONS = {
    "listing_complete": "a contents listing names every object in / on that receptacle at that step",
}
TRANSFORM_OF = {"heat": "heat", "cool": "cool", "clean": "clean"}


def spec_of(goal: dict) -> dict:
    """Task requirements read from the task sentence only [TASK]."""
    form = goal["form"]
    return {"form": form, "otype": goal["object_type"],
            "dest": None if form == "light" else goal["target_type"],
            "lamp": goal["target_type"] if form == "light" else None,
            "transform": TRANSFORM_OF.get(form), "count": 2 if form == "two" else 1}


class Reading:
    """Record reading after each visible step. Call .apply(step) in order; read the fields afterwards."""

    def __init__(self, sp: dict, type_of):
        self.sp, self.T = sp, type_of
        self.held: dict = {}           # obj -> step of the pick-up (or carrying report)
        self.picks: dict = {}          # obj -> [steps]
        self.moves: dict = {}          # obj -> [(step, recep)]
        self.transforms: dict = {}     # obj -> [(step, kind, appliance)]
        self.listing: dict = {}        # recep -> {"step", "items", "scope"}
        self.openstate: dict = {}      # recep -> (step, True/False)
        self.visits: dict = {}         # recep -> [arrival steps]
        self.arrival = None            # (step, recep) latest arrival
        self.seen: dict = {}           # obj -> (step, recep) first listing naming obj
        self.listed_at: dict = {}      # obj -> (step, recep) latest listing naming obj
        self.moved_in: dict = {}       # recep -> [(step, obj)] since its latest listing
        self.placed: dict = {}         # obj -> {"recep", "step", "basis", "confirmed": [steps]}
        self.pevents: list = []        # (step, obj, recep, event)  event: placed | restored | withdrawn | confirmed | conflict
        self.uses: list = []           # (step, lamp, sorted held objs, arrival recep)
        self.goal_step = None
        self.goal_objs: list = []

    # ---------------------------------------------------------------- derived readings
    def is_dest(self, r):
        return self.sp["dest"] is not None and self.T(r) == self.sp["dest"]

    def supported(self) -> list:
        """requested-type instances with a supported (not CONFLICT) placement in a destination-type receptacle"""
        return sorted(o for o, p in self.placed.items() if self.T(o) == self.sp["otype"] and p["basis"] != "conflict")

    def transformed_before(self, o, k) -> list:
        tk = self.sp["transform"]
        return [j for j, kind, _ in self.transforms.get(o, []) if kind == tk and j < k]

    def goal_now(self) -> list:
        """objects that support the goal now (shared definition; the GOAL stage of S uses the same function)"""
        sp = self.sp
        if sp["form"] == "light":
            return sorted({o for _, lamp, h, _ in self.uses if self.T(lamp) == sp["lamp"]
                           for o in h if self.T(o) == sp["otype"]})
        sup = self.supported()
        if sp["transform"]:
            sup = [o for o in sup if self.transformed_before(o, self.placed[o]["step"])]
        return sup

    def available(self) -> list:
        """requested-type instances known to be at a receptacle and not held, not counted as placed (persistence):
        either the latest listing of that receptacle names them and they were not picked up since, or the actor
        moved them there and did not pick them up since. Returns (instance, receptacle, step of the evidence)."""
        out = []
        sup = set(self.supported())
        for o, mv in self.moves.items():
            k, r = mv[-1]
            if self.T(o) != self.sp["otype"] or o in self.held or o in sup or o in self.listed_at:
                continue
            if any(p > k for p in self.picks.get(o, [])) or not self._not_omitted(o, r, k):
                continue
            out.append((o, r, k))
        for o, (k, r) in self.listed_at.items():
            if self.T(o) != self.sp["otype"] or o in self.held or o in sup:
                continue
            mv = self.moves.get(o)
            if mv and mv[-1][0] > k:      # moved by the actor after that listing: the move is the newer evidence
                if not any(p > mv[-1][0] for p in self.picks.get(o, [])) and \
                        self._not_omitted(o, mv[-1][1], mv[-1][0]):
                    out.append((o, mv[-1][1], mv[-1][0]))
                continue
            if any(p > k for p in self.picks.get(o, [])):
                continue
            if self.listing.get(r, {}).get("step") != k:   # a newer listing of r no longer names o
                continue
            out.append((o, r, k))
        return sorted(out)

    def _not_omitted(self, o, r, k) -> bool:
        """no listing of r newer than step k omits o"""
        L = self.listing.get(r)
        return L is None or L["step"] <= k or o in L["items"]

    # ---------------------------------------------------------------- update from one visible step
    def apply(self, s: dict) -> None:
        k = s["i"]
        if s["outcome"] == "fail":
            return
        T, sp = self.T, self.sp
        for a in s["atoms"]:
            kd = a["kind"]
            if kd == "arrive":
                self.arrival = (k, a["recep"])
                self.visits.setdefault(a["recep"], []).append(k)
            elif kd in ("is_open", "open_done"):
                self.openstate[a["recep"]] = (k, True)
            elif kd in ("is_closed", "close_done"):
                self.openstate[a["recep"]] = (k, False)
            elif kd in ("see_on", "see_in"):
                r = a["recep"]
                scope = "surface listing" if kd == "see_on" else "listing of the open receptacle"
                self.listing[r] = {"step": k, "items": list(a["items"]), "scope": scope}
                self.moved_in[r] = []
                for it in a["items"]:
                    self.seen.setdefault(it, (k, r))
                    self.listed_at[it] = (k, r)
                for o, p in self.placed.items():
                    if p["recep"] == r and p["basis"] != "conflict":
                        if o in a["items"]:
                            p["confirmed"].append(k)
                            if p["basis"] == "observed":
                                p["basis"] = "confirmed"
                            self.pevents.append((k, o, r, "confirmed"))
                        else:
                            p["basis"] = "conflict"
                            self.pevents.append((k, o, r, "conflict"))
            elif kd == "pick_up":
                o = a["obj"]
                self.held[o] = k
                self.picks.setdefault(o, []).append(k)
                if o in self.placed:
                    self.pevents.append((k, o, self.placed[o]["recep"], "withdrawn"))
                    del self.placed[o]
            elif kd == "move_done":
                o, r = a["obj"], a["recep"]
                self.held.pop(o, None)
                self.moves.setdefault(o, []).append((k, r))
                self.moved_in.setdefault(r, []).append((k, o))
                if self.is_dest(r):
                    ev = "restored" if any(e[1] == o and e[3] == "withdrawn" for e in self.pevents) else "placed"
                    self.placed[o] = {"recep": r, "step": k, "basis": "observed", "confirmed": []}
                    self.pevents.append((k, o, r, ev))
            elif kd == "carrying":
                for o in list(self.held):
                    if o not in a["items"]:
                        del self.held[o]
                for o in a["items"]:
                    self.held.setdefault(o, k)
            elif kd in ("heat_done", "cool_done", "clean_done"):
                self.transforms.setdefault(a["obj"], []).append((k, kd[:-5], a["appliance"]))
            elif kd == "turn_on":
                arr = self.arrival[1] if self.arrival else None
                self.uses.append((k, a["obj"], sorted(self.held), arr))
        if self.goal_step is None:
            g = self.goal_now()
            if len(g) >= (1 if sp["form"] == "light" else sp["count"]):
                self.goal_step, self.goal_objs = k, g


def read_prefix(sp, vis, type_of) -> Reading:
    R = Reading(sp, type_of)
    for s in vis:
        R.apply(s)
    return R


# ------------------------------------------------------------------ problem families (shared taxonomy)
FAMILY = {  # finding category -> (family name, the field that names the instance / receptacle)
    "wrong_object_pickup": ("WRONG_TYPE", "obj"), "wrong_object_transform": ("WRONG_TYPE", "obj"),
    "wrong_object_delivery": ("WRONG_TYPE", "obj"), "redo_mismatched": ("WRONG_TYPE", "obj"),
    "untransformed_delivery": ("UNTRANSFORMED", "obj"),
    "wrong_transform": ("WRONG_TRANSFORM", "obj"), "unneeded_transform": ("WRONG_TRANSFORM", "obj"),
    "repeat_transform": ("REPEAT_TRANSFORM", "obj"),
    "cycle": ("PLACEMENT_UNDONE", "obj"), "removal_of_placed": ("PLACEMENT_UNDONE", "obj"),
    "task_object_elsewhere": ("ELSEWHERE", "obj"),
    "seen_not_taken": ("MISSED_ACQUIRE", "obj"), "acquire_available_skipped": ("MISSED_ACQUIRE", "obj"),
    "unproductive_revisits": ("SEARCH_REVISIT", "recep"), "find_known_contents": ("SEARCH_REVISIT", "recep"),
    "find_unopened_left": ("SEARCH_UNOPENED", "recep"),
    "consecutive_repeat": ("REPEAT_CMD", "action"), "repeated_info": ("REPEAT_CMD", "action"),
    "failed": ("FAILED", "action"),
    "rule_unexplained_fail": ("FAILED", "action"), "rule_undetermined_fail": ("FAILED", "action"),
    "light_without_object": ("LAMP", "lamp"), "lamp_seen_not_used": ("LAMP", "lamp"),
    "handoff_unmet": ("HANDOFF", "obj"), "rule_fail_predicted": ("HANDOFF", "obj"),
    "rule_refuted": ("RULE_COUNTER", "action"), "rule_effect_violated": ("RULE_EFFECT", "action"),
    "recovery": ("RECOVERY", None), "goal_supported": ("GOAL", None),
}
POSITIVE = ("recovery", "goal_supported")


def families(f: dict) -> list:
    """problem identities of one finding: [(family, instance-or-receptacle)]; one per occurrence receptacle for the
    revisit / unopened kinds"""
    fam, fld = FAMILY[f["cat"]]
    if fam == "SEARCH_REVISIT":   # PTA occurrences are (visit step, R, leave step); stage occurrences are dicts
        return sorted({(fam, o["recep"] if isinstance(o, dict) else o[1]) for o in f.get("occ", [])})
    if fld is None:
        return [(fam, "-")]
    return [(fam, f.get(fld) or (f["key"].split(":", 1)[1] if ":" in f["key"] else "-"))]


# ------------------------------------------------------------------ per-category provenance (fixed before the replay)
# says: what the finding states; assumptions: declared assumptions it uses (beyond the parser); sources: TASK / MANUAL
# / OBS / REC / RULE; stage: the stage the finding concerns (S's attribution); s0: S0 lines on the same topic
CAT_INFO = {
    # ---- P (also in S and C)
    "wrong_object_pickup": dict(says="pick-up of an instance that is not of the requested type",
                                assumptions=["name_reading"], sources="TASK + OBS (PRE: the command)",
                                stage="ACQUIRE type check", s0=[28, 42]),
    "wrong_object_transform": dict(says="transformation of an instance that is not of the requested type",
                                   assumptions=["name_reading"], sources="TASK + OBS", stage="ACQUIRE type check",
                                   s0=[28]),
    "wrong_object_delivery": dict(says="delivery of a non-requested-type instance into a destination-type receptacle",
                                  assumptions=["name_reading"], sources="TASK + OBS", stage="ACQUIRE type check",
                                  s0=[28]),
    "untransformed_delivery": dict(says="delivery of a requested-type instance with no recorded required "
                                        "transformation of THAT instance", assumptions=["name_reading"],
                                   sources="TASK + REC", stage="DELIVER needs TRANSFORM of the same instance",
                                   s0=[31, 43]),
    "wrong_transform": dict(says="a transformation other than the one the task names", assumptions=[],
                            sources="TASK + REC", stage="TRANSFORM", s0=[31]),
    "unneeded_transform": dict(says="a transformation in a task that names none", assumptions=[],
                               sources="TASK + REC", stage="none", s0=[17, 18, 19]),
    "repeat_transform": dict(says="the same transformation of the same instance again", assumptions=[],
                             sources="REC", stage="TRANSFORM (already recorded)", s0=[45]),
    "cycle": dict(says="taking an object back from where the actor put it", assumptions=[], sources="OBS",
                  stage="DELIVER result withdrawn (if a destination)", s0=[34, 45]),
    "removal_of_placed": dict(says="pick-up of a counted placement: the netted count drops by one",
                              assumptions=["persistence"], sources="OBS", stage="DELIVER result withdrawn",
                              s0=[33]),
    "task_object_elsewhere": dict(says="a requested-type instance moved into a non-destination receptacle",
                                  assumptions=["name_reading"], sources="TASK + OBS", stage="ACQUIRE result ended "
                                  "without DELIVER", s0=[32]),
    "seen_not_taken": dict(says="leaving a visit where a requested-type instance was listed without taking it",
                           assumptions=["name_reading"], sources="TASK + OBS", stage="FIND established, ACQUIRE "
                           "not attempted", s0=[30, 42]),
    "unproductive_revisits": dict(says="a revisit with the same observation as before and no successful action",
                                  assumptions=[], sources="OBS", stage="FIND (search)", s0=[29, 41]),
    "consecutive_repeat": dict(says="the same command three or more times in a row", assumptions=[], sources="REC",
                               stage="none", s0=[34]),
    "repeated_info": dict(says="an information command repeated with an identical observation", assumptions=[],
                          sources="REC", stage="none", s0=[45]),
    "failed": dict(says="the command failed ('Nothing happens.')", assumptions=[], sources="OBS", stage="none",
                   s0=[35]),
    "light_without_object": dict(says="the lamp used while the record shows no requested-type instance held",
                                 assumptions=["persistence", "init_empty_hands"], sources="TASK + OBS",
                                 stage="INSPECT needs ACQUIRE", s0=[19]),
    "lamp_seen_not_used": dict(says="leaving a visit where the lamp was listed while holding the object, without "
                                    "using it", assumptions=["persistence"], sources="TASK + OBS",
                               stage="INSPECT not attempted", s0=[19]),
    "recovery": dict(says="first pick-up of a requested-type instance after a wrong-type pick-up",
                     assumptions=["name_reading"], sources="TASK + OBS", stage="ACQUIRE re-established", s0=[]),
    "goal_supported": dict(says="the goal is supported by the record (shared definition)",
                           assumptions=["name_reading", "persistence", "init_empty_hands"],
                           sources="TASK + OBS + REC", stage="GOAL", s0=[]),
    # ---- S only (also in C)
    "redo_mismatched": dict(says="take / transform / move of a non-requested-type instance after it was already "
                                 "delivered: the failed handoff is ACQUIRE's type check, so redoing later stages on "
                                 "it cannot discharge the open stage", assumptions=["name_reading"],
                            sources="TASK + OBS + MANUAL (stage order)", stage="ACQUIRE type check -> FIND/ACQUIRE "
                            "open", s0=[21, 28]),
    "find_known_contents": dict(says="EXTENDED: while FIND is open (nothing held), going to a receptacle whose latest "
                                     "listing named no instance of the searched type cannot discharge FIND",
                                assumptions=["listing_complete", "persistence"],
                                sources="OBS (the listing named no such instance) + MANUAL (FIND) + ASSUMED",
                                stage="FIND", s0=[29, 41]),
    "find_unopened_left": dict(says="leaving a receptacle reported closed, never opened or listed, while FIND is "
                                    "open: FIND was not discharged there", assumptions=[],
                               sources="OBS (closed, no listing) + MANUAL (FIND needs a listing naming o)",
                               stage="FIND", s0=[29]),
    "acquire_available_skipped": dict(says="nothing held, a slot unfinished, and a requested-type instance known at "
                                           "another receptacle: going elsewhere does not discharge ACQUIRE",
                                      assumptions=["persistence", "init_empty_hands"],
                                      sources="OBS + MANUAL (ACQUIRE follows FIND)", stage="ACQUIRE", s0=[30, 42]),
    "handoff_unmet": dict(says="transform / move of an instance the record does not show held: TRANSFORM / DELIVER "
                               "need ACQUIRE of the same instance", assumptions=["persistence", "init_empty_hands"],
                          sources="MANUAL (held while transformed / moved) + OBS", stage="ACQUIRE -> TRANSFORM / "
                          "DELIVER handoff", s0=[20, 21, 22]),
    # ---- C only
    "rule_fail_predicted": dict(says="a learned requirement is false before the command: failure predicted",
                                assumptions=["model assumptions"], sources="RULE", stage="action", s0=[]),
    "rule_unexplained_fail": dict(says="the command failed although every applicable learned requirement held: "
                                       "not explained by the learned contracts", assumptions=["model assumptions"],
                                  sources="RULE + OBS", stage="action", s0=[]),
    "rule_undetermined_fail": dict(says="the command failed; a learned requirement was unknown before it",
                                   assumptions=["model assumptions"], sources="RULE + OBS", stage="action", s0=[]),
    "rule_refuted": dict(says="a learned contract's prediction was contradicted by the observation",
                         assumptions=["model assumptions"], sources="RULE + OBS", stage="action", s0=[]),
    "rule_effect_violated": dict(says="a learned effect did not hold after a successful command",
                                 assumptions=["model assumptions"], sources="RULE + OBS", stage="action", s0=[]),
}
assert set(CAT_INFO) == set(FAMILY), set(CAT_INFO) ^ set(FAMILY)
