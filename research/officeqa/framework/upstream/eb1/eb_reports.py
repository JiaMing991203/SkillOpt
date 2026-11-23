"""EB-1 report conditions for one analyst-visible trajectory (task, executed commands, observations, outcome ONLY).

Every condition starts with the SAME two blocks, so no condition gets extra historical knowledge:
  L  learned historical facts (frozen model B = RP-1 candidate d3b4a667, plain words, with support) -- or, for a task
     type the model does not cover, a statement that no learned fact applies;
  P  strong plain progress: goal (TASK), outcome, instance identity (seen / taken / put down / re-taken), lamp events,
     final state, revisits with identical observations, repeated information commands, repeated lamp uses, failed and
     unrecognised commands, closed receptacles left unopened. FACT lines only (no judgement of necessity).
Then:
  P-condition: L + P.
  A-condition: L + P + action-contract checks of model B: coverage (which commands have learned contracts), effect
     checks of every 'use', commands whose learned effect was already established, the post-observation completion
     relation, and the F2 conditional forecasts before the commands (incl. the completing one).
  C-condition: L + P + ONE connected stage-action block: the MANUAL stage procedure bound to instances, its timeline,
     the GOAL as the LEARNED completion relation, a work account of every step (served an open stage / avoidable with
     its support / ambiguous), alternative order, recovery, failure causes left open, and the F2 forecast at the
     completing command.
Tags: TASK (task sentence), MANUAL (supplied stage procedure), OBS / FACT (observation), RULE (learned contract of
model B), ASSUMED(name) (declared observational assumption), INFERENCE, ADVICE (none is generated here).
Length: one per-trajectory cap CAP for every condition; lines are dropped from the end of the lowest-priority block
first and the omission is stated; `render` reports whether anything was dropped (checked before any request).
"""
from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vocab_ext"))
import eb_forecast as F  # noqa: E402
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402

CAP = 3600
MODEL_B = "d3b4a6672df3"   # RP-1 candidate B (the frozen model the reports use)
CONDITIONS = ("P", "A", "C")
HEADER = ("## Trajectory Diagnostic Reports\n"
          "Generated automatically from each trajectory's task sentence, commands and observations only (no hidden "
          "environment information). Tags: FACT = stated by the record; TASK = from the task sentence; MANUAL = a "
          "supplied task procedure; RULE = a learned hypothesis (may be wrong); ASSUMED = a declared assumption; "
          "INFERENCE = derived from the tagged sources. 'sN' = step N (0-based). Treat the reports as evidence about "
          "the trajectories, not as instructions.\n")
INFO = ("look", "inventory", "examine", "help")


def S(ts, n=6):
    ts = sorted(set(ts))
    return ", ".join(f"s{t}" for t in ts[:n]) + (f" (+{len(ts) - n} more)" if len(ts) > n else "")


# ======================================================================================== shared analysis
def analyze(view: dict, model: dict) -> dict:
    ti = XV.task_info(view["task"]) or {}
    light = ti.get("form") == "light"
    OT, LT = ti.get("OTYPE"), ti.get("LTYPE")
    steps = []
    for s in view["steps"]:
        cmd = XV.parse_command(s["action"])
        atoms = XV.parse_obs(s["obs"])
        steps.append({"i": s["i"], "action": s["action"], "obs": s["obs"], "cmd": cmd, "atoms": atoms,
                      "outcome": XV.outcome_of(cmd, atoms)})
    n = len(steps)
    an = {"view": view, "ti": ti, "light": light, "OT": OT, "LT": LT, "steps": steps, "n": n,
          "success": view["outcome"] == "success"}
    held, on = set(), {}
    seen = collections.defaultdict(list)          # instance -> [(step, recep)]
    picks, puts, uses, fails, unrec, wrong = [], [], [], [], [], []
    visits = collections.defaultdict(list)        # recep -> [step]
    last_obs = {}                                 # recep -> (listing items or None, closed?)
    put_into = collections.defaultdict(list)      # recep -> steps the actor put something there
    info_rep = collections.defaultdict(list)      # (action, obs) -> steps
    revisit = []                                  # dicts
    closed_left = []
    held_at = []
    cur = None
    state_change_since = 0
    for st in steps:
        i, cmd, atoms = st["i"], st["cmd"], st["atoms"]
        held_at.append(sorted(held))
        kinds = [a["kind"] for a in atoms]
        if kinds == ["nothing_happens"]:
            fails.append(i)
        if cmd["kind"] == "unknown":
            unrec.append(i)
        if cmd["kind"] == "info" or cmd.get("verb") in INFO:
            info_rep[(st["action"], st["obs"])].append((i, state_change_since))
        for a in atoms:
            k = a["kind"]
            if k == "arrive":
                r = a["recep"]
                prev = last_obs.get(r)
                if visits[r]:
                    revisit.append({"t": i, "r": r, "n": len(visits[r]) + 1, "prev": prev,
                                    "put_since": [x for x in put_into[r] if x > visits[r][-1]]})
                visits[r].append(i)
                cur = r
            elif k in ("see_on", "see_in"):
                last_obs[a["recep"]] = (tuple(a.get("items") or []), False)
                for x in a.get("items") or []:
                    seen[x].append((i, a["recep"]))
            elif k == "is_closed":
                if a["recep"] not in last_obs or last_obs[a["recep"]][1]:
                    last_obs[a["recep"]] = (None, True)
            elif k == "pick_up":
                held.add(a["obj"]); picks.append((i, a["obj"], a.get("recep"))); state_change_since += 1
                if OT and XV.type_of(a["obj"]) != OT:
                    wrong.append((i, a["obj"]))
            elif k == "move_done":
                held.discard(a["obj"]); puts.append((i, a["obj"], a.get("recep"))); put_into[a["recep"]].append(i)
                state_change_since += 1
            elif k == "turn_on":
                again = a["obj"] in on
                uses.append((i, a["obj"], again)); on.setdefault(a["obj"], i)
                state_change_since += 0 if again else 1      # re-switching a lamp that is already on changes nothing
            elif k in ("open_done", "close_done"):
                state_change_since += 1
    for r, vs in visits.items():
        lo = last_obs.get(r)
        if lo and lo[1]:
            closed_left.append((r, vs))
    an.update(held_end=sorted(held), on=on, seen=dict(seen), picks=picks, puts=puts, uses=uses, fails=fails,
              unrec=unrec, wrong=wrong, visits=dict(visits), revisit=revisit, closed_left=closed_left, held_at=held_at,
              info_rep={k: v for k, v in info_rep.items() if len(v) > 1})
    # retakes of the same instance
    rt = []
    for (tp, o, r) in puts:
        nxt = [p for p in picks if p[1] == o and p[0] > tp]
        if nxt:
            between = [steps[j] for j in range(tp + 1, nxt[0][0])]
            only_info = all(b["cmd"]["kind"] == "info" or b["cmd"].get("verb") in INFO for b in between)
            rt.append({"obj": o, "put": tp, "recep": r, "retake": nxt[0][0], "only_info_between": only_info})
    an["retakes"] = rt
    # learned completion relation (retrospective, post-observation) and F2 forecasts (model B)
    an["covered"] = light and bool(IR.active(model, "completes"))
    if an["covered"]:
        ep = {k: view[k] for k in ("episode_id", "task", "task_type", "outcome", "steps")}
        an["post"] = [v["verdict"] for v in K.post_verdicts(model, ep)]
        fv = F.forecast_episode(model, {k: v for k, v in ep.items() if k != "outcome"})
        an["f2"] = [{"t": f["t"], "if": f["if_success"]["verdict"], "opened": f["if_success"]["opened"],
                     "unc": f["unconditional"]["verdict"], "success": f["success"]["label"]} for f in fv]
    return an


def first(lst, pred):
    for x in lst:
        if pred(x):
            return x
    return None


def stage_times(an):
    """MANUAL stage results (lamp task): first listing / holding of a requested instance, first lamp listing / on."""
    OT, LT = an["OT"], an["LT"]
    def first_seen(ty):
        c = [(ts[0][0], x, ts[0][1]) for x, ts in an["seen"].items() if XV.type_of(x) == ty]
        return min(c) if c else None
    acq = first(an["picks"], lambda p: XV.type_of(p[1]) == OT)
    on = min(((t, x) for x, t in an["on"].items() if XV.type_of(x) == LT), default=None)
    return {"find_o": first_seen(OT), "acq": acq, "find_l": first_seen(LT), "on": on,
            "goal": (an["n"] - 1) if an["success"] else None}


# ======================================================================================== blocks
def block_L(an, model) -> list:
    if not an["covered"]:
        return [(0, "LEARNED FACTS: the learned model (frozen model B) covers only 'look at / examine X under / with "
                    "the lamp' tasks; no learned fact applies to this task type (outside coverage).")]
    c = {x["id"]: x for x in IR.active(model)}
    rel = c["completes.any.1"].get("evidence") or {}
    reg = "; 0 false completion claims on 4,902 saved states" if str(model.get("sha256", "")).startswith(MODEL_B) else ""
    return [(0, "LEARNED FACTS (frozen model B, learned from earlier permitted records; hypotheses that may be wrong):"),
            (0, f"- RULE completes.any.1: such a task was recorded complete exactly when the actor HELD an object of the "
                f"requested type at the SAME PLACE as a SWITCHED-ON lamp of the requested type (support "
                f"{rel.get('n_support', '?')} records, {rel.get('n_counter', '?')} counterexamples{reg}). It is a "
                f"condition on the current state: the order (lamp first or object first) did not matter, and an earlier "
                f"put-down and re-take of the object did not prevent completion."),
            (0, "- RULE persists.1: a lamp stayed on from 'You turn on the <lamp>.' until a turn-off (no turn-off occurs "
                "in the records). RULE use.effect.1: after a successful 'use <lamp>' the lamp was on."),
            (0, "- Not learned: when any command succeeds; any effect of go / take / move / open.")]


def block_P(an) -> list:
    v, OT, LT = an["view"], an["OT"], an["LT"]
    out = []
    ti = an["ti"]
    if an["light"]:
        goal = f"examine a {OT} under/with a {LT}"
    elif ti:
        goal = f"form '{ti.get('form')}': object {ti.get('OTYPE')}, destination {ti.get('RTYPE')}"
    else:
        goal = "(task sentence not parsed)"
    out.append((1, f"FACT goal (TASK): {goal}. Outcome: {v['outcome']} after {an['n']} steps"
                   + (f" (recorded complete after s{an['n'] - 1})." if an["success"] else ".")))
    # identity
    if OT:
        inst = sorted({x for x in an["seen"] if XV.type_of(x) == OT})
        if inst:
            out.append((1, "FACT requested-type instances listed: " + "; ".join(
                f"{x} at {an['seen'][x][0][1]} (s{an['seen'][x][0][0]})" for x in inst[:4]) + "."))
        else:
            out.append((1, f"FACT no observation listed an instance of the requested type {OT}."))
    ev = []
    for (t, o, r) in an["picks"]:
        ev.append((t, f"s{t} took {o}" + (f" (NOT a {OT})" if OT and XV.type_of(o) != OT else "")))
    for (t, o, r) in an["puts"]:
        ev.append((t, f"s{t} put {o} on/in {r}"))
    for x in an["retakes"]:
        ev.append((x["retake"], f"s{x['retake']} took {x['obj']} again (same instance)"))
    if ev:
        out.append((1, "FACT object events: " + "; ".join(e for _, e in sorted(ev)[:8]) + (" ..." if len(ev) > 8 else "") + "."))
    if LT:
        lamps = sorted({x for x in an["seen"] if XV.type_of(x) == LT})
        if lamps:
            out.append((1, "FACT lamps listed: " + "; ".join(f"{x} at {an['seen'][x][0][1]} (s{an['seen'][x][0][0]})"
                                                          for x in lamps[:3]) + "."))
    if an["uses"]:
        byl = collections.defaultdict(list)
        for (t, o, again) in an["uses"]:
            byl[o].append(t)
        for o, ts in byl.items():
            out.append((1, f"FACT 'use {o}' answered 'You turn on the {o}.' {len(ts)}x ({S(ts)}); no turn-off observed."))
    out.append((1, f"FACT at the end: holding {', '.join(an['held_end']) or 'nothing'}"
                   + (f"; at {an['view']['steps'][-1]['action'][6:]}" if an['view']['steps'][-1]['action'].startswith('go to ') else "")
                   + "."))
    # repetition
    same = [r for r in an["revisit"] if r["prev"] and r["prev"][0] is not None]
    if an["revisit"]:
        byr = collections.defaultdict(list)
        for r in an["revisit"]:
            byr[r["r"]].append(r["t"])
        out.append((2, f"FACT revisits ({len(an['revisit'])} arrivals at already-visited receptacles): " +
                       "; ".join(f"{r} {len(ts)}x ({S(ts, 4)})" for r, ts in sorted(byr.items(), key=lambda kv: kv[1][0])[:6]) + "."))
    if an["info_rep"]:
        parts = [f"'{a}' {len(ts)}x ({S([t for t, _ in ts], 4)})" for (a, o), ts in
                 sorted(an["info_rep"].items(), key=lambda kv: kv[1][0][0])[:5]]
        out.append((2, "FACT information commands repeated with an identical observation: " + "; ".join(parts) + "."))
    if an["fails"]:
        fa = collections.defaultdict(list)
        for t in an["fails"]:
            fa[an["steps"][t]["action"]].append(t)
        out.append((2, "FACT 'Nothing happens.' after: " + "; ".join(
            f"'{a}' ({S(ts, 3)}; holding {', '.join(an['held_at'][ts[0]]) or 'nothing'})" for a, ts in list(fa.items())[:5]) + "."))
    if an["unrec"]:
        out.append((2, "FACT commands not in a recognised command form: " + ", ".join(
            f"'{an['steps'][t]['action']}' (s{t})" for t in an["unrec"][:4]) + "."))
    if an["closed_left"]:
        out.append((3, "FACT receptacles seen closed and never opened: " + ", ".join(
            f"{r} ({S(vs, 3)})" for r, vs in an["closed_left"][:5]) + "."))
    return out


def block_A(an, model) -> list:
    out = [(4, "ACTION-CONTRACT CHECKS (frozen model B):")]
    if not an["covered"]:
        out.append((4, "- RULE coverage: no learned contract of model B applies to this task type; no command is "
                       "checked (coverage limitation)."))
        return out
    verbs = collections.Counter(s["cmd"].get("verb") if s["cmd"]["kind"] == "covered" else s["cmd"]["kind"]
                                for s in an["steps"])
    out.append((4, f"- RULE coverage: learned requirement / success condition for NO command, so no step's success is "
                   f"predicted; learned effect for 'use' only ({verbs.get('use', 0)} of {an['n']} commands); go/take/"
                   f"move/open/info commands ({an['n'] - verbs.get('use', 0)}) are unchecked."))
    rep = [t for (t, o, again) in an["uses"] if again]
    if an["uses"]:
        out.append((4, f"- RULE use.effect.1: every 'use' observation agrees with the learned effect (on). "
                       + (f"At {S(rep)} the lamp was already on (RULE persists.1 since its first activation, no "
                          f"turn-off): the effect was already established before the command." if rep else "")))
    post = an["post"]
    ch = [i for i in range(len(post)) if post[i] != (post[i - 1] if i else None)]
    out.append((4, "- RULE completes.any.1 after each step (retrospective): " +
                   "; ".join(f"{post[i]} from s{i}" for i in ch[:6]) + "."))
    f2 = an["f2"]
    cnt = collections.Counter(f["if"] for f in f2)
    last = f2[-1]
    out.append((4, f"- F2 conditional forecasts (before each command, assuming it succeeds): COMPLETE {cnt.get('COMPLETE', 0)}, "
                   f"NOT {cnt.get('NOT', 0)}, UNKNOWN {cnt.get('UNKNOWN', 0)}. Before the last command "
                   f"('{an['steps'][-1]['action']}'): {last['if']}"
                   + (f" (open: {', '.join(last['opened'][:3])}: no learned effect)" if last["opened"] else "") + "."))
    unexpl = [t for t in an["fails"]]
    if unexpl:
        out.append((4, f"- 'Nothing happens.' at {S(unexpl)}: no learned requirement explains it (unexplained by model B)."))
    return out


def productive(an, s, t) -> bool:
    """A successful command that advances an open stage: a take of a requested-type instance; the FIRST switch-on of
    a lamp; an open of a receptacle whose contents were not listed before; in a non-lamp task a move of a requested
    instance; in any task a move that frees the hands of a non-requested object (preparation)."""
    if s["outcome"] != "ok":
        return False
    v, args, OT = s["cmd"].get("verb"), s["cmd"].get("args") or {}, an["OT"]
    if v == "take":
        return bool(OT) and XV.type_of(args.get("O") or "") == OT
    if v == "use":
        return (s["i"], args.get("R"), False) in an["uses"]
    if v == "open":
        r = args.get("R")
        return not any(x["kind"] in ("see_in", "see_on") and x.get("recep") == r
                       for st in an["steps"][: s["i"]] for x in st["atoms"])
    if v == "move":
        o = args.get("O") or ""
        return (not an["light"] and XV.type_of(o) == OT) or (bool(OT) and XV.type_of(o) != OT)
    return False


def block_C(an, model) -> list:
    st = stage_times(an) if an["light"] else None
    out = [(5, "CONNECTED STAGE-ACTION CHECK (MANUAL stages bound to instances; GOAL = "
               + ("RULE completes.any.1):" if an["covered"] else "MANUAL reading, no learned GOAL for this task type):"))]
    if not an["covered"]:
        out.append((5, "- MANUAL stages from the task sentence (find -> take -> transform if named -> deliver); the GOAL "
                       "reading is MANUAL here, not learned (outside the learned model's coverage), so no completion "
                       "claim below is learned."))
    else:
        f = lambda x: "not reached" if x is None else f"s{x[0]}"
        out.append((5, f"- MANUAL stages: FIND {an['OT']} {f(st['find_o'])}"
                       + (f" ({st['find_o'][1]} at {st['find_o'][2]})" if st['find_o'] else "")
                       + f"; ACQUIRE {f(st['acq'])}" + (f" ({st['acq'][1]})" if st['acq'] else "")
                       + f"; FIND {an['LT']} {f(st['find_l'])}" + (f" ({st['find_l'][1]} at {st['find_l'][2]})" if st['find_l'] else "")
                       + f"; SWITCH_ON {f(st['on'])}; GOAL "
                       + (f"s{st['goal']} (RULE completes.any.1 true after s{st['goal']})" if st["goal"] is not None
                          else "never (RULE completes.any.1 never true)") + ". The two branches need no fixed order."))
        if st["on"] and st["acq"] and st["on"][0] < st["acq"][0]:
            out.append((5, f"- ALTERNATIVE ORDER (supported): the lamp was switched on (s{st['on'][0]}) before the object "
                           f"was held (s{st['acq'][0]}); RULE completes.any.1 is a state condition"
                           + (f" and the task completed at s{st['goal']} without a new 'use'." if st["goal"] is not None and
                              an["steps"][st["goal"]]["cmd"].get("verb") != "use" else ".")))
    # work account
    OT, LT = an["OT"], an["LT"]
    avoid, amb, nec = collections.defaultdict(list), collections.defaultdict(list), []
    for r in an["revisit"]:
        t = r["t"]
        held = an["held_at"][t]
        need_o = OT and not any(XV.type_of(x) == OT for x in held)
        lamp_on = any(XV.type_of(x) == LT for x in an["on"] if an["on"][x] < t) if LT else False
        need_l = bool(LT) and (not lamp_on or not need_o)
        prev = r["prev"]
        nxt = next((v for v in an["visits"][r["r"]] if v > t), an["n"])
        nxt = min([nxt] + [k for k in range(t + 1, an["n"]) if any(x["kind"] == "arrive" for x in an["steps"][k]["atoms"])])
        prod = any(productive(an, s, t) for s in an["steps"][t + 1: nxt])
        last = an["success"] and t == an["n"] - 1
        if prev is None or prev[0] is None:
            amb["revisit of a receptacle whose contents were never listed"].append(t)
        elif r["put_since"]:
            amb["revisit after the actor put something there"].append(t)
        elif last or prod:
            nec.append(t)
        elif not any(XV.type_of(x) in ({OT} if need_o else set()) | ({LT} if need_l else set()) for x in prev[0]):
            avoid["revisit: its latest listing named nothing still needed and nothing was put there since "
                  "[ASSUMED listing_complete, persistence]"].append(t)
        else:
            amb["revisit to a listed needed item without using it"].append(t)
    for (a, o), ts in an["info_rep"].items():
        rep = [ts[j][0] for j in range(1, len(ts)) if ts[j][1] == ts[j - 1][1]]   # nothing changed since the previous one
        if rep:
            avoid["information command repeated with no change in between and an identical observation [OBS]"].extend(rep)
    for (t, o, again) in an["uses"]:
        if again:
            avoid["'use' of a lamp already on [RULE persists.1, use.effect.1]"].append(t)
    for x in an["retakes"]:
        if x["only_info_between"]:
            avoid[f"put-down and immediate re-take of {x['obj']} (harmless: RULE completes.any.1 ignores it)"].extend(
                range(x["put"], x["retake"] + 1))
    for (t, o) in an["wrong"]:
        avoid[f"pick-up of a non-requested type [TASK, OBS]"].append(t)
    lines = [f"{k}: {S(v)}" for k, v in avoid.items()]
    if lines:
        out.append((5, f"- AVOIDABLE work ({sum(len(v) for v in avoid.values())} steps, each with its support): " + "; ".join(lines) + "."))
    else:
        out.append((5, "- AVOIDABLE work: none supported."))
    if amb:
        out.append((6, "- AMBIGUOUS (not called avoidable): " + "; ".join(f"{k}: {S(v)}" for k, v in amb.items()) + "."))
    firsts = sorted(vs[0] for vs in an["visits"].values())
    out.append((6, f"- NECESSARY or enabling: first visits {S(firsts)} (search/travel while a stage was open)"
                   + (f"; returns that led to a needed action {S(nec)}" if nec else "") + "."))
    for x in an["retakes"]:
        if not x["only_info_between"]:
            out.append((5, f"- RECOVERY: {x['obj']} put down at s{x['put']} ({x['recep']}) and re-taken at s{x['retake']} "
                           f"(same instance; ACQUIRE withdrawn then restored)."))
    if an["fails"] and an["covered"]:
        ta = [t for t in an["fails"] if an["steps"][t]["cmd"].get("verb") == "take" or an["steps"][t]["action"].startswith("take")]
        if ta:
            t = ta[0]
            out.append((5, f"- OPEN CAUSE: s{t} '{an['steps'][t]['action']}' -> 'Nothing happens.' while holding "
                           f"{', '.join(an['held_at'][t]) or 'nothing'}"
                           + (" and not in the 'take X from R' form" if an["steps"][t]["cmd"]["kind"] != "covered" else "")
                           + ": model B has no learned requirement, so which condition failed is not established."))
    if an["covered"] and an["success"]:
        lf = an["f2"][-1]
        why = (f"open: {', '.join(lf['opened'][:2])}; no learned effect, so UNKNOWN rather than a confident NOT"
               if lf["if"] == "UNKNOWN" and lf["opened"] else "from the learned contracts" if lf["if"] == "COMPLETE"
               else "the learned relation is not satisfied in the forecast state")
        out.append((6, f"- FORECAST before the completing command s{an['n'] - 1} (conditional on its success): F2 "
                       f"{lf['if']} ({why})."))
    return out


def _cap(lines, cap):
    txt = [l for _, l in lines]
    full = "\n".join(txt)
    if len(full) <= cap:
        return full, 0
    order = sorted(range(len(lines)), key=lambda k: (-lines[k][0], -k))
    keep = set(range(len(lines)))
    note = "[... {n} lower-priority line(s) omitted for length]"
    for k in order:
        keep.discard(k)
        t = "\n".join(lines[j][1] for j in sorted(keep)) + "\n" + note.format(n=len(lines) - len(keep))
        if len(t) <= cap:
            return t, len(lines) - len(keep)
    return note.format(n=len(lines)), len(lines)


def render(cond: str, an: dict, model: dict, cap: int = CAP) -> dict:
    lines = block_L(an, model) + block_P(an)
    if cond == "A":
        lines += block_A(an, model)
    elif cond == "C":
        lines += block_C(an, model)
    txt, dropped = _cap(lines, cap)
    return {"text": txt, "chars": len(txt), "dropped": dropped, "lines": len(lines)}
