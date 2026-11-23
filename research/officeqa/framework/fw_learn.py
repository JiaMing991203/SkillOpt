#!/usr/bin/env python3
"""Model revision: the gate that turns a proposal into the next model, with its decisions.

PORT PROVENANCE. A domain-parameterised `upstream/vx1/vx_learn.py`. `apply_proposal`,
`_apply_contract_op`, `_finish`, `_same_head`, `_find` and `summarize` keep their bodies; `XV`/`vname`
become `dom`, and `L1.direct_contradictions` becomes `dom.direct_contradictions`.

apply_proposal(prev, proposal, ev, batch, source, dom) -> (next model, decisions)

ORDER: assumption operations, then persistence operations, then every other contract operation - because
a persistence rule changes what the other contracts evaluate to.

THE GATE (identical for every kind and every proposer, including me):
  * the schema and vocabulary validate;
  * zero determinate counterexamples over all permitted evidence so far, and at least one supporting case;
  * an assumption or persistence change is REFUSED if the records contradict it directly, or if it would
    create counterexamples for active contracts that the same proposal does not revise or retire. That
    second clause is the REGRESSION CHECK: a repair may not buy agreement on the new counterexample by
    breaking agreement on evidence that already held.
  * at most one active persistence rule per state.
Afterwards every still-active contract that now has a counterexample becomes `refuted`, kept with its
witnesses. NOTHING IS DELETED: a retired or refuted rule stays in the model with the reason, so the
history of what was believed and why is in the artifact rather than in a report.
"""
from __future__ import annotations

import copy

import fw_check as K
import fw_ir as IR

MAX_IDS = 10


def summarize(sc: dict) -> dict:
    return K.summarize(sc)


def _find(m, cid):
    c = [x for x in m["contracts"] if x["id"] == cid and x.get("status") in ("active", "refuted")]
    return c[-1] if c else None


def _same_head(a: dict, b: dict) -> bool:
    if a.get("kind") != b.get("kind"):
        return False
    k = a["kind"]
    if k in ("requires", "suffices", "effect"):
        return a.get("verb") == b.get("verb")
    if k == "completes":
        return a.get("scope") == b.get("scope")
    if k == "concludes":
        return (a.get("scope") == b.get("scope")
                and (a.get("consequence") or {}).get("atom") == (b.get("consequence") or {}).get("atom"))
    return a.get("state") == b.get("state")


def apply_proposal(prev: dict, proposal: dict, ev, batch: int, source: str, dom, label_fn=None) -> tuple:
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
        """Every active contract's counterexamples under a candidate (assumptions, persistence) pair.
        The regression check compares this before and after."""
        return {c["id"]: K.score(c, ev, assumptions, persists, dom, label_fn)["counter"]
                for c in IR.active(m) if c["id"] not in touched and c["kind"] != "persists"}

    def regressions(before, after):
        new = {cid: [x for x in after[cid] if x not in before.get(cid, [])] for cid in after}
        return {k: v for k, v in new.items() if v}

    # ---- assumption operations ------------------------------------------------------------------------
    aops = [op for op in (proposal.get("assumption_ops") or []) if isinstance(op, dict)]
    for op in (proposal.get("assumption_ops") or []):
        if not isinstance(op, dict):
            dec.append({"type": "assumption", "op": None, "decision": "rejected", "source": source,
                        "why": [f"malformed operation {str(op)[:120]!r}"]})
    for op in aops:
        name, kind = op.get("name"), op.get("op")
        d = {"type": "assumption", "op": kind, "name": name, "reason": op.get("reason", ""),
             "source": source}
        if name not in dom.assumptions or kind not in ("adopt", "drop"):
            d.update(decision="rejected", why=["unknown assumption or operation for this vocabulary"])
        elif (kind == "adopt") == (name in A):
            d.update(decision="no_change", why=[f"already {'adopted' if name in A else 'absent'}"])
        else:
            newA = (A | {name}) if kind == "adopt" else (A - {name})
            contra = dom.direct_contradictions(name, ev) if kind == "adopt" else []
            new_cx = regressions(counters(A, persists_now()), counters(newA, persists_now()))
            if contra:
                d.update(decision="rejected", testable=name in dom.directly_testable,
                         why=["contradicted by the records: " + "; ".join(str(x) for x in contra[:4])],
                         n_contradictions=len(contra))
            elif new_cx:
                d.update(decision="rejected", why=[
                    "would create counterexamples for active contracts not revised in this proposal: "
                    + "; ".join(f"{k}: {v[:3]}" for k, v in new_cx.items())])
            else:
                A = newA
                d.update(decision="accepted", testable=name in dom.directly_testable,
                         why=[] if name in dom.directly_testable else
                         ["not directly testable from the records; challenged only through contract "
                          "counterexamples"])
        dec.append(d)
    m["assumptions"] = sorted(A)

    # ---- contract operations (persistence first) -------------------------------------------------------
    def _is_persists(op):
        c = op.get("contract")
        t = _find(m, op.get("target")) if isinstance(op.get("target"), str) else None
        return (isinstance(c, dict) and c.get("kind") == "persists") or (t or {}).get("kind") == "persists"

    for op in sorted(ops, key=lambda op: 0 if _is_persists(op) else 1):
        try:
            _apply_contract_op(m, op, ev, batch, source, dom, A, dec, persists_now, counters,
                               regressions, label_fn)
        except Exception as e:  # noqa: BLE001  one malformed operation never stops the update
            dec.append({"type": "contract", "op": op.get("op"), "target": op.get("target"),
                        "decision": "rejected", "source": source,
                        "why": [f"malformed operation ({type(e).__name__}: {str(e)[:160]})"],
                        "proposed": op.get("contract")})
    return _finish(m, dec, ev, A, batch, dom, label_fn)


def _apply_contract_op(m, op, ev, batch, source, dom, A, dec, persists_now, counters, regressions,
                       label_fn):
    kind = op.get("op")
    d = {"type": "contract", "op": kind, "target": op.get("target"), "reason": op.get("reason", ""),
         "witnesses": op.get("witnesses", []), "source": source}
    if kind in ("add", "revise"):
        c = op.get("contract")
        errs = IR.validate(c, dom)
        tgt = _find(m, op.get("target")) if kind == "revise" else None
        if kind == "revise" and tgt is None:
            errs.append(f"revise target {op.get('target')!r} is not an active or refuted contract")
        if kind == "revise" and tgt is not None and isinstance(c, dict) and not _same_head(c, tgt):
            errs.append("a revision must keep the kind and the verb / scope / state / consequence atom")
        if not errs and c["kind"] == "persists" and kind == "add" and \
                any(x["state"] == c["state"] for x in IR.active(m, "persists")):
            errs.append("an active persists rule for this state exists: propose a revision instead")
        if errs:
            d.update(decision="rejected", why=errs, proposed=c)
            dec.append(d)
            return
        nc = IR.normalize(c)
        dup = [x for x in IR.active(m)
               if IR.semantic_sha(x) == IR.semantic_sha(nc) and (tgt is None or x is not tgt)]
        if dup:
            d.update(decision="no_change", why=[f"identical to active contract {dup[0]['id']}"],
                     proposed=nc)
            dec.append(d)
            return
        if nc["kind"] == "persists":
            newP = [x for x in persists_now() if x is not tgt] + [dict(nc, id="(new)", status="active")]
            sc = K.score(nc, ev, A, newP, dom, label_fn)
            new_cx = regressions(counters(A, persists_now()), counters(A, newP))
            why = K.gate(sc, "persists") + ([
                "would create counterexamples for active contracts not revised in this proposal: "
                + "; ".join(f"{k}: {v[:3]}" for k, v in new_cx.items())] if new_cx else [])
        else:
            sc = K.score(nc, ev, A, persists_now(), dom, label_fn)
            why = K.gate(sc, nc["kind"])
        d["evidence"] = summarize(sc)
        d["rendered"] = IR.render(nc, dom)
        if why:
            d.update(decision="rejected", why=why, proposed=nc)
            m.setdefault("rejected", []).append(
                dict(nc, id=op.get("target") or "(new)", batch=batch, reason=op.get("reason", ""),
                     why=why, source=source, evidence=summarize(sc)))
            dec.append(d)
            return
        if kind == "add":
            nc.update(id=IR.next_id(m, nc), version=1, parent=None, operation="add")
        else:
            tgt["status"] = "superseded"
            tgt["superseded_batch"] = batch
            nc.update(id=tgt["id"], version=tgt["version"] + 1,
                      parent=f"{tgt['id']}@v{tgt['version']}", operation="revise")
        nc.update(status="active", batch=batch, reason=op.get("reason", ""),
                  witnesses=op.get("witnesses", []), source=source, evidence=summarize(sc),
                  sha256=IR.semantic_sha(nc))
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


def _finish(m, dec, ev, A, batch, dom, label_fn):
    P = IR.active(m, "persists")
    for c in IR.active(m):
        sc = K.score(c, ev, A, P, dom, label_fn)
        c["evidence"] = summarize(sc)
        if sc["counter"]:
            c.update(status="refuted", refuted_batch=batch,
                     refute_reason=f"counterexample(s) {', '.join(sc['counter'][:6])}; no accepted repair")
            dec.append({"type": "contract", "op": "auto_refute", "target": c["id"],
                        "decision": "refuted", "why": [c["refute_reason"]], "source": "gate"})
    return m, dec
