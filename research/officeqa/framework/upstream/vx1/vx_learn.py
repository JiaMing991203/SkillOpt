"""VX-1 model revision (amendment A5 section 5.3): the v1 gate (learn.apply_proposal), extended to every VX-1 kind.

apply_proposal(prev, proposal, ev, batch, source, vname) -> (next model, decisions). Order: assumption ops, then
persists ops, then the other contract ops. GATE (identical for both vocabularies and every source):
  * schema / vocabulary valid (vx_ir.validate for the model's vocabulary);
  * ZERO determinate counterexamples over ALL permitted evidence so far, and at least one supporting case
    (completes: True at a completion state; requires: an explained failure or a consistent success);
  * an assumption or persists change is refused if the records contradict it directly, or if it would create
    counterexamples for active contracts that the same proposal does not revise or retire;
  * at most one active persists rule per state (a second one must be proposed as a revision).
Afterwards every still-active contract with a counterexample becomes `refuted` (kept, with its counterexamples).
Nothing is deleted; decisions carry the proposer's reason and the code's reasons.
"""
from __future__ import annotations

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_vocab as XV  # noqa: E402
import learn as L1  # noqa: E402  (frozen v1: direct_contradictions for the v1 assumptions)

MAX_IDS = 10


def summarize(sc: dict) -> dict:
    return {"applicable": sc["applicable"], "n_support": len(sc["support"]), "support": sc["support"][:MAX_IDS],
            "n_consistent": len(sc["consistent"]), "n_counter": len(sc["counter"]), "counter": sc["counter"][:MAX_IDS],
            "counter_kind": {k: sc["counter_kind"][k] for k in sc["counter"][:MAX_IDS] if k in sc.get("counter_kind", {})},
            "undetermined": sc["undetermined"], "und_at_completion": sc.get("und_at_completion", 0)}


def direct_contradictions(name: str, ev) -> list:
    if name == "init_off":
        out = []
        for ep in ev.episodes:
            seen_on = set()
            for i, st in enumerate(ep["steps"]):
                for a in XV.parse_obs(st["obs"]):
                    if a["kind"] == "turn_on":
                        seen_on.add(a["obj"])
                    elif a["kind"] == "turn_off" and a["obj"] not in seen_on:
                        out.append(f"{ep['episode_id']}@{i}: {a['obj']} turned off before any recorded activation")
        return out
    if name in L1.DIRECTLY_TESTABLE:
        return L1.direct_contradictions(name, ev)
    return []


DIRECTLY_TESTABLE = tuple(L1.DIRECTLY_TESTABLE) + ("init_off",)


def _find(m, cid):
    c = [x for x in m["contracts"] if x["id"] == cid and x.get("status") in ("active", "refuted")]
    return c[-1] if c else None


def _same_head(a: dict, b: dict) -> bool:
    if a.get("kind") != b.get("kind"):
        return False
    k = a["kind"]
    return (a.get("verb") == b.get("verb")) if k in ("requires", "suffices", "effect") else \
        (a.get("scope") == b.get("scope")) if k == "completes" else (a.get("state") == b.get("state"))


def apply_proposal(prev: dict, proposal: dict, ev, batch: int, source: str) -> tuple:
    vname = prev["vocab"]
    m = copy.deepcopy(prev)
    for k in ("sha256", "frozen_utc"):
        m.pop(k, None)
    m.update(version=prev["version"] + 1, batch=batch, parent_sha256=prev.get("sha256"), decisions=[])
    dec = m["decisions"]
    A = set(m["assumptions"])
    ops = [op for op in (proposal.get("contract_ops") or []) if isinstance(op, dict)]
    for op in (proposal.get("contract_ops") or []):
        if not isinstance(op, dict):
            dec.append({"type": "contract", "op": None, "decision": "rejected", "source": source,
                        "why": [f"malformed operation {str(op)[:120]!r}"]})
    touched = {op.get("target") for op in ops if op.get("op") in ("revise", "retire")}

    def persists_now():
        return IR.active(m, "persists")

    def counters(assumptions, persists):
        return {c["id"]: K.score(c, ev, assumptions, persists, vname)["counter"]
                for c in IR.active(m) if c["id"] not in touched and c["kind"] != "persists"}

    aops = [op for op in (proposal.get("assumption_ops") or []) if isinstance(op, dict)]
    for op in (proposal.get("assumption_ops") or []):
        if not isinstance(op, dict):
            dec.append({"type": "assumption", "op": None, "decision": "rejected", "source": source,
                        "why": [f"malformed operation {str(op)[:120]!r}"]})
    for op in aops:
        name, kind = op.get("name"), op.get("op")
        d = {"type": "assumption", "op": kind, "name": name, "reason": op.get("reason", ""), "source": source}
        if name not in XV.VOCABS[vname]["assumptions"] or kind not in ("adopt", "drop"):
            d.update(decision="rejected", why=["unknown assumption or operation for this vocabulary"])
        elif (kind == "adopt") == (name in A):
            d.update(decision="no_change", why=[f"already {'adopted' if name in A else 'absent'}"])
        else:
            newA = (A | {name}) if kind == "adopt" else (A - {name})
            contra = direct_contradictions(name, ev) if kind == "adopt" else []
            before, after = counters(A, persists_now()), counters(newA, persists_now())
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
                         ["not directly testable from the records; challenged only through contract counterexamples"])
        dec.append(d)
    m["assumptions"] = sorted(A)

    def _is_persists(op):
        c = op.get("contract")
        t = _find(m, op.get("target")) if isinstance(op.get("target"), str) else None
        return (isinstance(c, dict) and c.get("kind") == "persists") or (t or {}).get("kind") == "persists"
    ordered = sorted(ops, key=lambda op: 0 if _is_persists(op) else 1)
    for op in ordered:
        try:
            _apply_contract_op(m, op, ev, batch, source, vname, A, dec, persists_now, counters)
        except Exception as e:  # noqa: BLE001  a malformed operation never stops the update (VX-1 deviation 3)
            dec.append({"type": "contract", "op": op.get("op"), "target": op.get("target"), "decision": "rejected",
                        "source": source, "why": [f"malformed operation ({type(e).__name__}: {str(e)[:160]})"],
                        "proposed": op.get("contract")})
    return _finish(m, dec, ev, A, batch, vname)


def _apply_contract_op(m, op, ev, batch, source, vname, A, dec, persists_now, counters):
    if True:
        kind = op.get("op")
        d = {"type": "contract", "op": kind, "target": op.get("target"), "reason": op.get("reason", ""),
             "witnesses": op.get("witnesses", []), "source": source}
        if kind in ("add", "revise"):
            c = op.get("contract")
            errs = IR.validate(c, vname)
            tgt = _find(m, op.get("target")) if kind == "revise" else None
            if kind == "revise" and tgt is None:
                errs.append(f"revise target {op.get('target')!r} is not an active or refuted contract")
            if kind == "revise" and tgt is not None and isinstance(c, dict) and not _same_head(c, tgt):
                errs.append("a revision must keep the kind and the verb / scope / state")
            if not errs and c["kind"] == "persists" and kind == "add" and \
                    any(x["state"] == c["state"] for x in IR.active(m, "persists")):
                errs.append("an active persists rule for this state exists: propose a revision instead")
            if errs:
                d.update(decision="rejected", why=errs, proposed=c)
                dec.append(d)
                return
            nc = IR.normalize(c)
            dup = [x for x in IR.active(m) if IR.semantic_sha(x) == IR.semantic_sha(nc) and (tgt is None or x is not tgt)]
            if dup:
                d.update(decision="no_change", why=[f"identical to active contract {dup[0]['id']}"], proposed=nc)
                dec.append(d)
                return
            if nc["kind"] == "persists":
                newP = [x for x in persists_now() if x is not tgt] + [dict(nc, id="(new)", status="active")]
                sc = K.score(nc, ev, A, newP, vname)
                before, after = counters(A, persists_now()), counters(A, newP)
                new_cx = {cid: [x for x in after[cid] if x not in before.get(cid, [])] for cid in after}
                new_cx = {k: v for k, v in new_cx.items() if v}
                why = K.gate(sc, "persists") + (["would create counterexamples for active contracts not revised in "
                                                 "this proposal: " + "; ".join(f"{k}: {v[:3]}" for k, v in new_cx.items())]
                                                if new_cx else [])
            else:
                sc = K.score(nc, ev, A, persists_now(), vname)
                why = K.gate(sc, nc["kind"])
            d["evidence"] = summarize(sc)
            d["rendered"] = IR.render(nc)
            if why:
                d.update(decision="rejected", why=why, proposed=nc)
                m.setdefault("rejected", []).append(dict(nc, id=op.get("target") or "(new)", batch=batch,
                                                         reason=op.get("reason", ""), why=why, source=source))
                dec.append(d)
                return
            if kind == "add":
                nc.update(id=IR.next_id(m, nc), version=1, parent=None, operation="add")
            else:
                tgt["status"] = "superseded"
                tgt["superseded_batch"] = batch
                nc.update(id=tgt["id"], version=tgt["version"] + 1, parent=f"{tgt['id']}@v{tgt['version']}",
                          operation="revise")
            nc.update(status="active", batch=batch, reason=op.get("reason", ""), witnesses=op.get("witnesses", []),
                      source=source, evidence=summarize(sc), sha256=IR.semantic_sha(nc))
            m["contracts"].append(nc)
            d.update(decision="accepted", id=nc["id"], version=nc["version"])
        elif kind in ("retire", "keep"):
            tgt = _find(m, op.get("target"))
            if tgt is None:
                d.update(decision="rejected", why=[f"target {op.get('target')!r} not found"])
            elif kind == "retire":
                tgt.update(status="retired", retired_batch=batch, retire_reason=op.get("reason", ""))
                d.update(decision="accepted")
            else:
                d.update(decision="no_change")
        else:
            d.update(decision="rejected", why=[f"unknown operation {kind!r}"])
        dec.append(d)



def _finish(m, dec, ev, A, batch, vname):
    P = IR.active(m, "persists")
    for c in IR.active(m):
        sc = K.score(c, ev, A, P, vname)
        c["evidence"] = summarize(sc)
        if sc["counter"]:
            c.update(status="refuted", refuted_batch=batch,
                     refute_reason=f"counterexample(s) {', '.join(sc['counter'][:6])}; no accepted repair")
            dec.append({"type": "contract", "op": "auto_refute", "target": c["id"], "decision": "refuted",
                        "why": [c["refute_reason"]], "source": "gate"})
    return m, dec
