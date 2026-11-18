"""Amendment A4 sections 3.3 and 3.4: MALFORMED executed commands and the INSPECT stage revision (MANUAL-REVISED).

Nothing here changes a frozen module. The frozen vocabulary (vocab.py, digest 5005c74b...) and the shared reading
(stage_common.Reading) are wrapped:

* parse_steps(steps): the shared step list [{i, action, cmd, obs, atoms, outcome}] with one addition. A command whose
  first word is a command verb of the vocabulary (or "put") but which the frozen grammar cannot parse (for example
  "take tissuebox 1" without "from R") gets cmd["kind"] = "malformed" instead of "unknown". If its observation is
  exactly "Nothing happens." its outcome is "fail" (an OBSERVED failure, a record fact); otherwise "unknown".
  The step keeps its index. No contract covers it (the contract vocabulary is unchanged).

* ReadingA4: stage_common.Reading with the revised INSPECT goal reading for look_at_obj_in_light tasks:
    (a) [unchanged] the lamp was used while o was held, or
    (b) [MANUAL-REVISED, from the counterexample Q/FRESH/s3/tr:0293] o is held now and a lamp of the named type was
        turned on earlier (no recorded command turns a lamp off) while either o was already held, or the actor then
        picked o up from the receptacle of the arrival at which the lamp was turned on, with no arrival elsewhere in
        between. (Review fix before the freeze: a branch "o was listed at that receptacle" was removed, because an
        object can be listed at two receptacles (reading B3) and the actor may pick it up elsewhere.)
  Assumptions of (b): lamp_stays_on (no recorded command turns a lamp off) and same_place (the receptacle of that
  arrival is o's place). The contract vocabulary has no lamp-state atom: the lamp state is a stage-layer RECORD
  event, never a contract fact.

* rev=2 (POST-FREEZE stage revision, 2026-11-11 04:25 EST; MANUAL-REVISED; trigger: a blinded Study A reviewer's
  remark on tr:3287, then a deterministic check over all 187 records: in 81 of 84 successful episodes the goal reading
  first holds exactly at the last step, i.e. episodes end on success; in 3 it held earlier, so the old GOAL / DELIVER
  reading was too PERMISSIVE there):
    (c) pick_two_obj: the two requested instances must have supported placements in the SAME destination receptacle
        instance (counterexamples Q/STD/s1/tr:3287 s31 (cabinet 4 + cabinet 1; success only at s40 when both were in
        cabinet 1) and Q/SELF/s3/tr:2785 s28 (drawer 2 + drawer 1; success at s30)). Assumption: same_receptacle.
    (d) heat / cool: the task's transformation must be the LATEST temperature transformation (heat or cool) of that
        instance before its placement: a later cool supersedes a heat and vice versa (counterexample
        Q/EVOL/s3/tr:2658: heat s41, cool s43, placed s44 = no success; heat s47, placed s49 = success). Assumption:
        temperature_overrides. These are still RECORD readings of events; no temperature is asserted.
  rev=1 reproduces the reading used for Study A's frozen reports.
"""
from __future__ import annotations

import stage_common as SC

COMMAND_WORDS = ("go", "take", "move", "put", "open", "close", "heat", "cool", "clean", "use")
INSPECT_ASSUMPTIONS = {
    "lamp_stays_on": "no recorded command turns a lamp off (ALFWorld's command set has none)",
    "same_place": "the receptacle of the actor's latest arrival when the lamp was turned on is the object's place "
                  "if the object was listed there or later picked up there without another arrival in between",
}


def classify_command(cmd: dict) -> dict:
    """the frozen parse, with kind 'malformed' for unparsed commands that start with a command verb"""
    if cmd.get("kind") == "unknown":
        first = (cmd.get("text") or "").split(" ", 1)[0]
        if first in COMMAND_WORDS:
            return dict(cmd, kind="malformed")
    return cmd


def parse_steps(steps: list, parse_command, parse_obs, outcome_of) -> list:
    out = []
    for i, st in enumerate(steps):
        cmd = classify_command(parse_command(st["action"]))
        atoms = parse_obs(st["obs"])
        if cmd["kind"] == "malformed":
            outcome = "fail" if [a["kind"] for a in atoms] == ["nothing_happens"] else "unknown"
        else:
            outcome = outcome_of(cmd, atoms)
        out.append({"i": i, "action": st["action"], "cmd": cmd, "obs": st["obs"], "atoms": atoms,
                    "outcome": outcome})
    return out


def malformed_steps(P: list) -> list:
    return [{"t": s["i"], "action": s["action"], "obs": s["obs"], "outcome": s["outcome"]} for s in P
            if s["cmd"].get("kind") == "malformed"]


class ReadingA4(SC.Reading):
    """stage_common.Reading + the INSPECT revision (b). Everything else is inherited unchanged."""

    def __init__(self, sp, type_of, rev=2):
        super().__init__(sp, type_of)
        self.rev = rev
        self.lamp_on: list = []      # (step, lamp, arrival recep, arrival step, requested objs listed there, held)
        self.pick_from: dict = {}    # obj -> [(step, recep, arrival step at the pick-up)]
        self.inspect_basis = None    # "a" | "b" for the goal reading (first time the goal is supported)

    def _listed_here(self, r):
        L = self.listing.get(r)
        if not L:
            return []
        return [x for x in L["items"] if self.T(x) == self.sp["otype"]]

    def transformed_before(self, o, k) -> list:
        """rev 2 (d): the task's transformation counts only if no OTHER temperature transformation of o follows it
        before step k"""
        if self.rev < 2 or self.sp["transform"] not in ("heat", "cool"):
            return super().transformed_before(o, k)
        tk = self.sp["transform"]
        temp = [(j, kind) for j, kind, _ in self.transforms.get(o, []) if kind in ("heat", "cool") and j < k]
        return [temp[-1][0]] if temp and temp[-1][1] == tk else []

    def goal_now(self) -> list:
        sp = self.sp
        if sp["form"] == "two" and self.rev >= 2:
            sup = super().goal_now()   # supported placements (netted) in destination-type receptacles
            by_r = {}
            for o in sup:
                by_r.setdefault(self.placed[o]["recep"], []).append(o)
            best = max(by_r.values(), key=len) if by_r else []
            return sorted(best)       # (c) the instances that share ONE destination receptacle instance
        if sp["form"] != "light":
            return super().goal_now()
        a = set(super().goal_now())
        b = set()
        for o in self.held:
            if self.T(o) != sp["otype"] or o in a:
                continue
            for (k, lamp, arr, ka, listed, held) in self.lamp_on:
                if self.T(lamp) != sp["lamp"]:
                    continue
                if o in held:
                    b.add(o)
                    break
                for (kp, r, kap) in self.pick_from.get(o, []):
                    if kp > k and r == arr and kap == ka:   # picked at that receptacle, no arrival in between
                        b.add(o)
                        break
                if o in b:
                    break
        return sorted(a | b)

    def apply(self, s: dict) -> None:
        k = s["i"]
        if s["outcome"] != "fail":
            for at in s["atoms"]:
                if at["kind"] == "turn_on":
                    arr = self.arrival[1] if self.arrival else None
                    self.lamp_on.append((k, at["obj"], arr, self.arrival[0] if self.arrival else None,
                                         self._listed_here(arr) if arr else [], sorted(self.held)))
                elif at["kind"] == "pick_up":
                    self.pick_from.setdefault(at["obj"], []).append(
                        (k, at.get("recep"), self.arrival[0] if self.arrival else None))
        before = self.goal_step
        super().apply(s)
        if before is None and self.goal_step is not None and self.sp["form"] == "light":
            self.inspect_basis = "a" if set(self.goal_objs) & set(SC.Reading.goal_now(self)) else "b"


def read_prefix(sp, vis, type_of, rev=2) -> ReadingA4:
    R = ReadingA4(sp, type_of, rev)
    for s in vis:
        R.apply(s)
    return R
