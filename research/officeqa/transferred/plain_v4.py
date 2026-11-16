"""P4: the corrected plain progress report (amendment A3 section 3). Ordinary progress tracking, no stage model.

What P4 is: goal-relative FACTS and COUNTERS from one prefix (shared reading; progress_v4 state: need, done, held,
remaining, hands free, known available, to locate) plus checks of the SELECTED command at PRE against the task sentence
and the record. P4 never states a next required stage, never carries an obligation from one step to the next, and
never claims that a command cannot discharge an open stage.

Changes from v3 P (each one a correction recorded in amendment A3 section 3):
  * seen_not_taken (leaving a visit where a requested instance was listed, without taking it) is now ADVICE only when
    the hands are free, the task still needs an instance and the instance is still known available. When the hands
    hold a requested instance and another is still needed later, P4 issues a NOTE (known_instance_note) instead; when
    the task needs no further instance it issues nothing.
  * revisit_known_contents (CONDITIONAL tier): P4 assesses the selected 'go' at PRE: the target receptacle's latest
    listing named no instance of a type the task still needs to locate. (v3 P only flagged a revisit after leaving it.)
  * Everything else is v3 P unchanged (paired_trace_analysis findings, removal of a counted placement, recovery, goal
    support, the record-level connection on an untransformed delivery).
"""
from __future__ import annotations

import paired_trace_analysis as PTA
import plain_report as PR
import progress_v4 as PV
import stage_common as SC


def _revisit(goal, vis, pending, type_of, F):
    """P4's PRE assessment of every selected 'go' (prefix only)."""
    sp = SC.spec_of(goal)
    add = PR._adder(F)
    pend_t = pending["i"] if pending else None
    occs = {}

    def pre(s, R, status):
        k, cmd = s["i"], s["cmd"]
        if cmd["kind"] != "covered" or cmd.get("verb") != "go":
            return
        Rc = (cmd.get("args") or {}).get("R")
        if Rc not in R.listing:
            return
        L = R.listing[Rc]
        sup = set(R.goal_now())   # v6: an untransformed placement must still be re-acquired (transform tasks)
        for typ in PV.to_locate(R):
            has = [x for x in L["items"] if type_of(x) == typ and x not in sup]
            mv = [x for _, x in R.moved_in.get(Rc, []) if type_of(x) == typ]
            if has or mv:
                continue
            f = add(f"revisit_known_contents:{typ}", "revisit_known_contents", "attempted", k, target=typ,
                    tier="conditional", layer="P")
            oc = {"t": k, "recep": Rc, "listing_step": L["step"], "listing": list(L["items"]), "scope": L["scope"],
                  "open_at_listing": L["scope"] == "listing of the open receptacle" or None, "contradicted": None}
            f.setdefault("occ", []).append(oc)
            occs[(k, typ)] = oc

    def post(s, R):
        k = s["i"]
        for (t, typ), oc in occs.items():
            if t == k:
                L = R.listing.get(oc["recep"])
                if L and L["step"] == k:
                    new = [x for x in L["items"] if type_of(x) == typ and x not in R.supported()]
                    oc["contradicted"] = new or None

    PR.walk(goal, vis, pending, type_of, pre, post)
    for f in F.values():
        if f["cat"] == "revisit_known_contents":
            f["contradicted"] = [oc["t"] for oc in f["occ"] if oc["contradicted"]]
    return pend_t


def analyze(goal, vis, pending, type_of) -> dict:
    """{key: finding} from this prefix only (PRE: pending = the selected command; POST: pending = None)."""
    F = PR.analyze(goal, vis, pending, type_of)
    sp = SC.spec_of(goal)
    # ---- corrected seen_not_taken: re-classify every v3 occurrence with the shared progress state at its prefix
    old = {k: f for k, f in F.items() if f["cat"] == "seen_not_taken"}
    for k in old:
        del F[k]
    add = PR._adder(F)
    seq = list(vis) + ([dict(pending, pending=True)] if pending else [])
    by_t = {}
    for f in old.values():
        o = f["obj"] if f.get("obj") else f["key"].split(":", 1)[1]
        for (ks, r, k_leave) in f.get("occ", []):
            by_t.setdefault(k_leave, []).append((o, ks, r))
    if by_t:
        R = SC.Reading(sp, type_of)
        for s in seq:
            k = s["i"]
            for (o, ks, r) in by_t.get(k, []):
                av = {x[0]: x[1] for x in R.available()}
                rem, hf = PV.remaining(R), PV.hands_free(R)
                if o not in av or rem == 0:
                    continue
                tgt = (s["cmd"].get("args") or {}).get("R") if s["cmd"].get("verb") == "go" else None
                if hf and tgt in av.values():   # v6: the actor goes to a known needed instance: no advice
                    continue
                if hf:
                    add(f"seen_not_taken:{o}", "seen_not_taken", "decided", k, obj=o, layer="P",
                        tier="primary").setdefault("occ", []).append((ks, r, k))
                else:
                    add(f"known_instance_note:{o}", "known_instance_note", "decided", k, obj=o, layer="P",
                        tier="note").setdefault("occ", []).append(
                        {"t": k, "recep": r, "ref_step": ks, "held": sorted(R.held), "remaining": rem})
            if s.get("pending"):
                break
            R.apply(s)
    # ---- PRE assessment of the selected 'go' (conditional tier)
    _revisit(goal, vis, pending, type_of, F)
    for f in F.values():
        f.setdefault("layer", "P")
    return F


def progress(goal, vis, type_of) -> dict:
    """P4's progress block at a prefix: v3 P's block + the shared v4 counters (known instances are NOTEs)."""
    out = PR.progress(goal, vis, type_of)
    out["v4"] = PV.state_row(SC.read_prefix(SC.spec_of(goal), vis, type_of))
    return out
