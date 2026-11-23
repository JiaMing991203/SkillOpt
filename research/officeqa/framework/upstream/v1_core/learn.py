"""Model revision with explicit counterexamples (design v0.1 section 4.E). No network.

`apply_proposal(prev, proposal, ev, epoch, source)` turns a proposer's operations into the next model version:
  1. assumption_ops  {"op": "adopt"|"drop", "name", "reason"}: adopting is refused if the records directly contradict
                     the assumption, or if it would create counterexamples for contracts the proposal does not also
                     revise or retire.
  2. contract_ops    {"op": "add", "contract", "reason", "witnesses"}
                     {"op": "revise", "target": <id>, "contract", "reason", "witnesses"}
                     {"op": "retire", "target": <id>, "reason"}
                     {"op": "keep", "target": <id>, "reason"}
     GATE (the same for every source): schema and vocabulary valid, ZERO determinate counterexamples over ALL
     evidence so far (every earlier permitted example, not only the current batch), at least one supporting attempt.
  3. Every contract that is still active but has a counterexample under the final assumption set becomes `refuted`
     (inactive), with the counterexamples and "no accepted repair" recorded. Nothing is deleted: superseded,
     retired, refuted and rejected versions stay in the model with their reasons.
The proposer never changes the gate. An unchanged model is a valid result.

`mech_induce` / `mech_repair` are the deterministic, no-model proposer used by the readiness test (and available as
a comparator): single-literal requires contracts that explain at least one failure without counterexample, and
repairs by substituting a weaker vocabulary atom (arrived -> at, listed -> in) or retiring.
"""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ir import active, normalize, next_id, render, semantic_sha, validate_contract  # noqa: E402
from replay import Evidence, score_contract  # noqa: E402
from vocab import ASSUMPTIONS, VERBS, parse_command  # noqa: E402

MAX_IDS = 12


def summarize(sc: dict) -> dict:
    return {"applicable": sc["applicable"], "counter": sc["counter"][:MAX_IDS], "n_counter": len(sc["counter"]),
            "support_fail": sc["support_fail"][:MAX_IDS], "n_support_fail": len(sc["support_fail"]),
            "support_ok": sc["support_ok"][:MAX_IDS], "n_support_ok": len(sc["support_ok"]),
            "neutral": sc["neutral"], "undetermined": sc["undetermined"], "tasks_supporting": sc["tasks_supporting"]}


def gate(sc: dict) -> list:
    why = []
    if sc["counter"]:
        why.append(f"{len(sc['counter'])} counterexample(s) in the evidence so far: {', '.join(sc['counter'][:6])}")
    if sc["support"] == 0:
        why.append("no supporting attempt in the evidence so far")
    return why


# ---- direct contradictions of assumptions by the records --------------------------------------------------------
def direct_contradictions(name: str, ev: Evidence) -> list:
    out = []
    for ep in ev.episodes:
        est = ev.est(ep, ())
        S = est.steps
        if name == "init_empty_hands":
            for k, s in enumerate(S):
                if any(a["kind"] == "pick_up" for a in s["atoms"]):
                    break
                for a in s["atoms"]:
                    if a["kind"] == "carrying" and a["items"]:
                        out.append(f"{ep['episode_id']}@{k}: carrying {a['items']} before any pick-up")
        elif name == "openable_reported":
            arrived_plain = {}
            for k, s in enumerate(S):
                kinds = {(a["kind"], a.get("recep")) for a in s["atoms"]}
                for a in s["atoms"]:
                    if a["kind"] == "arrive" and not any(kk in ("is_open", "is_closed") and r == a["recep"]
                                                         for kk, r in kinds):
                        arrived_plain.setdefault(a["recep"], k)
                    if a["kind"] in ("is_open", "is_closed", "open_done", "close_done") and a["recep"] in arrived_plain:
                        out.append(f"{ep['episode_id']}@{k}: {a['recep']} reported open/closed after an arrival at "
                                   f"step {arrived_plain[a['recep']]} reported neither")
        elif name == "listing_complete":
            last: dict = {}
            for k, s in enumerate(S):
                for a in s["atoms"]:
                    if a["kind"] == "pick_up" and a["recep"] in last and a["obj"] not in last[a["recep"]][1] \
                            and last[a["recep"]][0] == k - 1:
                        out.append(f"{ep['episode_id']}@{k}: picked up {a['obj']} from {a['recep']}, whose listing at "
                                   f"step {k - 1} did not name it")
                    if a["kind"] in ("see_on", "see_in"):
                        last[a["recep"]] = (k, set(a["items"]))
        elif name == "persistence":
            # only changes the record attributes to nobody: an open/closed report that flips with no recorded
            # open/close. (Objects listed at two receptacles are NOT evidence against persistence: B3.)
            state: dict = {}
            for k, s in enumerate(S):
                for a in s["atoms"]:
                    if a["kind"] in ("is_open", "is_closed"):
                        v = a["kind"] == "is_open"
                        if a["recep"] in state and state[a["recep"]][0] != v:
                            out.append(f"{ep['episode_id']}@{k}: {a['recep']} reported {'open' if v else 'closed'} "
                                       f"after {'open' if state[a['recep']][0] else 'closed'} at step "
                                       f"{state[a['recep']][1]} with no recorded open/close")
                        state[a["recep"]] = (v, k)
                    elif a["kind"] in ("open_done", "close_done"):
                        state[a["recep"]] = (a["kind"] == "open_done", k)
    return out


DIRECTLY_TESTABLE = ("init_empty_hands", "openable_reported", "listing_complete", "persistence")


# ---- applying a proposal ----------------------------------------------------------------------------------------
def _find(m: dict, cid: str):
    cands = [c for c in m["contracts"] if c["id"] == cid and c.get("status") in ("active", "refuted")]
    return cands[-1] if cands else None


def apply_proposal(prev: dict, proposal: dict, ev: Evidence, epoch: int, source: str) -> tuple:
    m = copy.deepcopy(prev)
    for k in ("sha256", "frozen_utc"):
        m.pop(k, None)
    m["version"] = prev["version"] + 1
    m["epoch"] = epoch
    m["parent_sha256"] = prev.get("sha256")
    m["decisions"] = []
    dec = m["decisions"]
    A = set(m["assumptions"])
    touched = {op.get("target") for op in proposal.get("contract_ops") or [] if op.get("op") in ("revise", "retire")}

    def counters(assumptions):
        return {c["id"]: score_contract(c, ev, assumptions)["counter"] for c in active(m) if c["id"] not in touched}

    for op in proposal.get("assumption_ops") or []:
        name, kind = op.get("name"), op.get("op")
        d = {"type": "assumption", "op": kind, "name": name, "reason": op.get("reason", ""), "source": source}
        if name not in ASSUMPTIONS or kind not in ("adopt", "drop"):
            d.update(decision="rejected", why=["unknown assumption or operation"])
        elif (kind == "adopt") == (name in A):
            d.update(decision="no_change", why=[f"already {'adopted' if name in A else 'absent'}"])
        else:
            newA = (A | {name}) if kind == "adopt" else (A - {name})
            contra = direct_contradictions(name, ev) if kind == "adopt" else []
            before, after = counters(A), counters(newA)
            new_cx = {cid: [x for x in after[cid] if x not in before.get(cid, [])] for cid in after}
            new_cx = {k: v for k, v in new_cx.items() if v}
            if contra:
                d.update(decision="rejected", why=["contradicted by the records: " + "; ".join(contra[:4])])
            elif new_cx:
                d.update(decision="rejected", why=["would create counterexamples for active contracts not revised in "
                                                    "this proposal: " + "; ".join(f"{k}: {v[:3]}" for k, v in new_cx.items())])
            else:
                A = newA
                d.update(decision="accepted", why=[] if name in DIRECTLY_TESTABLE else
                         ["not directly testable from the records; can be challenged only through contract counterexamples"])
        dec.append(d)
    m["assumptions"] = sorted(A)

    for op in proposal.get("contract_ops") or []:
        kind = op.get("op")
        d = {"type": "contract", "op": kind, "target": op.get("target"), "reason": op.get("reason", ""),
             "witnesses": op.get("witnesses", []), "source": source}
        if kind in ("add", "revise"):
            c = op.get("contract")
            errs = validate_contract(c)
            tgt = _find(m, op.get("target")) if kind == "revise" else None
            if kind == "revise" and tgt is None:
                errs.append(f"revise target {op.get('target')!r} is not an active or refuted contract")
            if kind == "revise" and tgt is not None and c and c.get("verb") != tgt["verb"]:
                errs.append("a revision must keep the verb")
            if errs:
                d.update(decision="rejected", why=errs, proposed=c)
                dec.append(d)
                continue
            nc = normalize(c)
            dup = [x for x in active(m) if semantic_sha(x) == semantic_sha(nc) and (tgt is None or x is not tgt)]
            if dup:
                d.update(decision="no_change", why=[f"identical to active contract {dup[0]['id']}"], proposed=nc)
                dec.append(d)
                continue
            sc = score_contract(nc, ev, A)
            why = gate(sc)
            d["evidence"] = summarize(sc)
            d["rendered"] = render(nc)
            if why:
                d.update(decision="rejected", why=why, proposed=nc)
                m.setdefault("rejected", []).append(dict(nc, id=op.get("target") or "(new)", epoch=epoch,
                                                         reason=op.get("reason", ""), why=why, source=source))
                dec.append(d)
                continue
            if kind == "add":
                nc.update(id=next_id(m, nc["verb"], nc["kind"]), version=1, parent=None, operation="add")
            else:
                tgt["status"] = "superseded"
                tgt["superseded_epoch"] = epoch
                nc.update(id=tgt["id"], version=tgt["version"] + 1, parent=f"{tgt['id']}@v{tgt['version']}",
                          operation="revise")
            nc.update(status="active", epoch=epoch, reason=op.get("reason", ""), witnesses=op.get("witnesses", []),
                      source=source, evidence=summarize(sc), sha256=semantic_sha(nc))
            m["contracts"].append(nc)
            d.update(decision="accepted", id=nc["id"], version=nc["version"])
        elif kind in ("retire", "keep"):
            tgt = _find(m, op.get("target"))
            if tgt is None:
                d.update(decision="rejected", why=[f"target {op.get('target')!r} not found"])
            elif kind == "retire":
                tgt.update(status="retired", retired_epoch=epoch, retire_reason=op.get("reason", ""))
                d.update(decision="accepted")
            else:
                d.update(decision="no_change")
        else:
            d.update(decision="rejected", why=[f"unknown operation {kind!r}"])
        dec.append(d)

    for c in active(m):
        sc = score_contract(c, ev, A)
        c["evidence"] = summarize(sc)
        if sc["counter"]:
            c.update(status="refuted", refuted_epoch=epoch,
                     refute_reason=f"counterexample(s) {', '.join(sc['counter'][:6])}; no accepted repair")
            dec.append({"type": "contract", "op": "auto_refute", "target": c["id"], "decision": "refuted",
                        "why": [c["refute_reason"]], "source": "gate"})
    return m, dec


# ---- deterministic proposer (no model) -----------------------------------------------------------------------
def L(atom, args, value=True):
    return {"atom": atom, "args": list(args), "value": value}


TEMPLATES = {
    "go": [L("at", ["R"], False), L("arrived", ["R"], False)],
    "take": [L("at", ["R"]), L("arrived", ["R"]), L("in", ["O", "R"]), L("listed", ["O", "R"]),
             L("holding_any", [], False), L("holding", ["O"], False)],
    "move": [L("holding", ["O"]), L("at", ["R"]), L("arrived", ["R"])],
    "open": [L("at", ["R"]), L("arrived", ["R"]), L("open", ["R"], False)],
    "close": [L("at", ["R"]), L("arrived", ["R"]), L("open", ["R"])],
    "heat": [L("holding", ["O"]), L("at", ["R"]), L("arrived", ["R"]), L("is_a", ["R", "microwave"])],
    "cool": [L("holding", ["O"]), L("at", ["R"]), L("arrived", ["R"]), L("is_a", ["R", "fridge"])],
    "clean": [L("holding", ["O"]), L("at", ["R"]), L("arrived", ["R"]), L("is_a", ["R", "sinkbasin"])],
    "use": [L("at", ["R"]), L("arrived", ["R"])],
}
WEAKER = {"arrived": "at", "listed": "in"}


def mech_induce(ev: Evidence, assumptions, verbs=None, model=None) -> dict:
    ops = []
    have = {semantic_sha(c) for c in active(model)} if model else set()
    for verb in verbs or sorted(VERBS):
        for lit in TEMPLATES.get(verb, []):
            c = {"verb": verb, "kind": "requires", "condition": [[lit]]}
            if semantic_sha(c) in have:
                continue
            sc = score_contract(c, ev, assumptions)
            if not sc["counter"] and sc["support_fail"]:
                ops.append({"op": "add", "contract": c, "reason": f"explains {len(sc['support_fail'])} failure(s) "
                            f"with no counterexample (deterministic inducer)", "witnesses": sc["support_fail"][:5]})
    return {"assumption_ops": [], "contract_ops": ops, "source": "mech"}


def mech_repair(model: dict, ev: Evidence) -> dict:
    ops = []
    for c in active(model):
        sc = score_contract(c, ev, model["assumptions"])
        if not sc["counter"] or c["kind"] != "requires" or len(c["condition"]) != 1 or len(c["condition"][0]) != 1:
            continue
        lit = c["condition"][0][0]
        if lit["atom"] in WEAKER:
            nc = {"verb": c["verb"], "kind": "requires",
                  "condition": [[dict(lit, atom=WEAKER[lit["atom"]])]]}
            ops.append({"op": "revise", "target": c["id"], "contract": nc,
                        "reason": f"counterexample(s) {sc['counter'][:3]}: the record-level atom {lit['atom']} is "
                                  f"stronger than the state it stands for; weaken to {WEAKER[lit['atom']]}",
                        "witnesses": sc["counter"][:3]})
        else:
            ops.append({"op": "retire", "target": c["id"], "reason": f"counterexample(s) {sc['counter'][:3]}"})
    return {"assumption_ops": [], "contract_ops": ops, "source": "mech"}


def uncovered_commands(episodes: list) -> dict:
    out: dict = {}
    for ep in episodes:
        for st in ep["steps"]:
            c = parse_command(st["action"])
            if c["kind"] != "covered":
                out[c.get("verb") or "(empty)"] = out.get(c.get("verb") or "(empty)", 0) + 1
    return out
