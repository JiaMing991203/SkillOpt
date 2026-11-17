"""Model revision with explicit counterexamples for OfficeQA (design v0.1 section 4.E).

Port of `transferred/learn.py` (source sha256 recorded in OFFICEQA_TRANSFER_MANIFEST.json). The gate, the
assumption-operation semantics, the "nothing is ever deleted" bookkeeping and the deterministic proposer are the
ALFWorld behaviour; the domain-specific parts - which assumptions the records can contradict, and which single-atom
contracts the inducer tries - are rewritten for OfficeQA. No network.

`apply_proposal(prev, proposal, ev, epoch, source)` turns a proposer's operations into the next model version:
  1. assumption_ops  {"op": "adopt"|"drop", "name", "reason"}: adopting is refused if the records directly
                     contradict the assumption, or if it would create counterexamples for contracts the proposal
                     does not also revise or retire.
  2. contract_ops    add / revise / retire / keep, with
     GATE (the same for every source): schema and vocabulary valid, ZERO determinate counterexamples over ALL
     evidence so far (every earlier permitted example, not only the current batch), at least one supporting step.
  3. Every contract still active but with a counterexample under the final assumption set becomes `refuted`.
     Nothing is deleted: superseded, retired, refuted and rejected versions stay in the model with their reasons.

The proposer never changes the gate. An unchanged model is a valid result.

Why the deterministic proposer is enough for this milestone. In the ALFWorld line the hypotheses came from a
DeepSeek learner (`transferred/lc_llm.py` + its prompts) and `mech_induce` was the no-model comparator. The
OfficeQA vocabulary is small enough that the comparator enumerates it exhaustively: every single-atom effect
contract over three verbs and ten atoms is 30 candidates, which the gate scores in milliseconds. So this milestone
needs no paid learner call, and whether a paid learner would propose anything the enumeration does not is an open
question recorded in the port note rather than an assumption made here.
"""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oqa_lc_ir import active, next_id, normalize, render, semantic_sha, validate_contract  # noqa: E402
from oqa_lc_replay import Evidence, score_contract
from oqa_lc_replay import trace_key as REPLAY_trace_key  # noqa: E402
from oqa_lc_vocab import ASSUMPTIONS, ATOMS, DIRECTLY_TESTABLE, VERBS, outcome_of  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402

MAX_IDS = 12


def summarize(sc: dict) -> dict:
    return {"applicable": sc["applicable"], "counter": sc["counter"][:MAX_IDS], "n_counter": len(sc["counter"]),
            "support_fail": sc["support_fail"][:MAX_IDS], "n_support_fail": len(sc["support_fail"]),
            "support_ok": sc["support_ok"][:MAX_IDS], "n_support_ok": len(sc["support_ok"]),
            "neutral": sc.get("neutral", 0), "undetermined": sc.get("undetermined", 0),
            "items_supporting": sc["items_supporting"]}


def gate(sc: dict) -> list:
    """Why a contract is refused, or [] when it passes. Identical in form to the ALFWorld gate."""
    why = []
    if sc["counter"]:
        why.append(f"{len(sc['counter'])} counterexample(s) in the evidence so far: {', '.join(sc['counter'][:6])}")
    if sc["support"] == 0:
        why.append("no supporting step in the evidence so far")
    return why


# ---- direct contradictions of an assumption by the records -----------------------------------------------------
def direct_contradictions(name: str, ev: Evidence) -> list:
    """Witnesses in the saved records that contradict a named assumption, each as "<item>@<step>: <evidence>".

    Only the three OfficeQA assumptions that the records can settle on their own are testable here
    (`oqa_lc_vocab.DIRECTLY_TESTABLE`); `table_scale_applies_to_rows` and `grep_cap_not_reached` can be challenged
    only through a contract counterexample, which `apply_proposal` records as such.
    """
    out: list[str] = []
    for attempt, step in ev.steps():
        if outcome_of(step) != "ok":
            continue
        aid = f"{REPLAY_trace_key(attempt)}@{step['i']}"
        supplied = ev._supplied(attempt)  # noqa: SLF001  (Evidence's own cache helper)
        if name == "index_alignment":
            for d in CELLS.disagreement_witnesses(step, supplied, "leaf"):
                out.append(f"{aid}: column {d['index']} of a {d['width']}-wide table - merging the supplied page's "
                           f"split header by index names {d['merged_leaf']!r}, the flattened header this step "
                           f"returned names {d['flat_leaf']!r}")
        elif name == "flat_header_authoritative":
            # Contradicted only if two flattened headers of the SAME table disagree with each other.
            ts = [t for t in CELLS.tables(step.get("obs") or "") if t["header_form"] == "flat"]
            for a_i, ta in enumerate(ts):
                for tb in ts[a_i + 1:]:
                    if ta["width"] == tb["width"] and set(ta["fingerprints"]) & set(tb["fingerprints"]):
                        for d in CELLS.binding_disagreements(ta, tb, "leaf"):
                            out.append(f"{aid}: two flattened headers of the same table disagree at column "
                                       f"{d['index']}: {d['merged_leaf']!r} vs {d['flat_leaf']!r}")
        elif name == "year_inheritance":
            for t in CELLS.tables(step.get("obs") or ""):
                # Only a table whose header block is in the SAME observation can contradict the inheritance rule.
                # A `read` returns a 100-line window, so a table is usually truncated and the window's first
                # labelled row is not the table's first row: "Nov." at the top of a window has an earlier row to
                # inherit from, just not one the window shows. Counting those as contradictions dropped the
                # assumption on two witnesses that were artefacts of the window, not of the corpus.
                if not t["header_lines"]:
                    continue
                first = next((r for r in t["rows"] if r["label"]), None)
                if first and first["period"] and first["period"].get("month") \
                        and first["period"].get("year") is None:
                    out.append(f"{aid}: the table's first labelled row {first['label']!r} names a month with no "
                               f"earlier row to inherit a year from, and the table's header is in this same "
                               f"observation so the row is not a truncated window's first row")
    return out


# ---- applying a proposal ---------------------------------------------------------------------------------------
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
                d.update(decision="rejected", why=["contradicted by the records: " + "; ".join(contra[:4])],
                         n_contradictions=len(contra))
            elif new_cx:
                d.update(decision="rejected",
                         why=["would create counterexamples for active contracts not revised in this proposal: "
                              + "; ".join(f"{k}: {v[:3]}" for k, v in new_cx.items())])
            else:
                A = newA
                d.update(decision="accepted", why=[] if name in DIRECTLY_TESTABLE else
                         ["not directly testable from the records; can be challenged only through contract "
                          "counterexamples"])
                if kind == "drop":
                    d["witnesses"] = (op.get("witnesses") or [])[:MAX_IDS]
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
                                                         reason=op.get("reason", ""), why=why, source=source,
                                                         evidence=summarize(sc)))
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

    # Re-score every surviving contract over ALL evidence under the final assumption set, and refute what no
    # longer holds. This is the "recheck earlier cases" step of design v0.1 4.E.
    for c in active(m):
        sc = score_contract(c, ev, A)
        c["evidence"] = summarize(sc)
        if sc["counter"]:
            c.update(status="refuted", refuted_epoch=epoch,
                     refute_reason=f"counterexample(s) {', '.join(sc['counter'][:6])}; no accepted repair")
            dec.append({"type": "contract", "op": "auto_refute", "target": c["id"], "decision": "refuted",
                        "why": [c["refute_reason"]], "source": "gate"})
    return m, dec


# ---- deterministic proposer (no model, no network) --------------------------------------------------------------
def L(atom, value=True):
    return {"atom": atom, "args": [], "value": value}


# Which atoms are worth hypothesising about, per verb. This is a TEMPLATE set, matching `transferred/learn.py`'s
# per-verb TEMPLATES, and not an exhaustive product over the vocabulary. An earlier version of this module did
# enumerate the product, and the result was the disease the contract audit named: it accepted
# `grep.effect = not has_table` ("a successful grep returns no table") and `read.effect = not cap_reached` (true by
# construction, since only grep has a cap), both accidents of the first few traces rather than hypotheses about the
# corpus. The atoms left out below - has_table, header_flat, header_split, scale_stated - describe what a
# particular window happens to contain, which is a fact about where the actor chose to look, not a claim about the
# documents. The atoms kept are claims about document STRUCTURE that a later observation can refute.
TEMPLATES = {
    "grep": ("pattern_in_every_line", "cap_reached", "width_uniform", "sub_row_aligned",
             "col_leaf_sound", "col_path_sound"),
    "read": ("width_uniform", "sub_row_aligned", "col_leaf_sound", "col_path_sound"),
    "glob": (),   # a glob observation is a file list; no document-structure atom applies to it
}
# `row_period_bound` is deliberately NOT in the template set, and the reason is worth recording because it is the
# format limitation of section 5 of the port note showing up a second time. Both of its polarities are induced and
# then refuted on this evidence: "not row_period_bound" holds of the FFO receipts tables, whose rows are indexed by
# category ("Individual income taxes"), and is refuted by the money tables, whose rows are months. Neither polarity
# is a claim about the corpus; the claim that IS true needs a guard - "in a table whose first column header names a
# period, every data row label yields a period" - and an `effect` contract takes no condition in this vocabulary.
# Until that guard can be expressed, hypothesising about it only manufactures a rule that the trace order decides.
MIN_ITEMS = 2


def mech_induce(ev: Evidence, assumptions, verbs=None, model=None, min_items: int = MIN_ITEMS) -> dict:
    """Every templated single-atom effect contract with support and no counterexample in the evidence so far.

    A candidate is proposed only when the gate would accept it AND its support comes from at least `min_items`
    distinct items. The second condition is design v0.1 section 8's fifth risk: "repeated observations of one task
    are not independent task-level confirmation". Candidates that fail either test are proposed anyway when they
    fail only for want of support, so that the rejection and its reason are recorded in the model.
    """
    ops = []
    have = {semantic_sha(c) for c in active(model)} if model else set()
    for verb in verbs or sorted(VERBS):
        for atom in TEMPLATES.get(verb, ()):
            for value in (True, False):
                c = {"verb": verb, "kind": "effect", "effects": [L(atom, value)]}
                if semantic_sha(c) in have:
                    continue
                sc = score_contract(c, ev, assumptions)
                if sc["counter"] or not sc["support"]:
                    continue
                if len(sc["items_supporting"]) < min_items:
                    continue
                ops.append({"op": "add", "contract": c,
                            "reason": f"holds of {sc['support']} observation(s) across "
                                      f"{len(sc['items_supporting'])} item(s) with no counterexample in the "
                                      f"evidence so far (deterministic template proposer)",
                            "witnesses": (sc["support_ok"] + sc["support_fail"])[:5]})
    return {"assumption_ops": [], "contract_ops": ops, "source": "mech"}


def assumption_recheck(model: dict, ev: Evidence) -> dict:
    """Drop proposals for adopted assumptions that the evidence NOW contradicts.

    This is an addition to the ALFWorld port, and the port note records it as one. `apply_proposal` tests
    `direct_contradictions` only when an assumption is ADOPTED, which in the ALFWorld line was enough because the
    LLM learner saw the refuted contracts of the previous epoch and could propose the drop itself. With a
    deterministic proposer nothing re-tests an assumption adopted before its counterexample arrived, so an
    assumption could stay in force while the records contradicted it in sixteen places. Amendment A4 3.1 requires
    the opposite: a contradicting observation must reopen every obligation discharged through the contradicted
    fact, which cannot happen while the assumption stands.
    """
    ops = []
    for name in sorted(set(model.get("assumptions") or []) & set(DIRECTLY_TESTABLE)):
        wit = direct_contradictions(name, ev)
        if wit:
            ops.append({"op": "drop", "name": name,
                        "reason": f"the records now contradict it in {len(wit)} place(s); it was adopted when no "
                                  f"observation had yet adjudicated it",
                        "witnesses": wit[:MAX_IDS], "n_witnesses": len(wit)})
    return {"assumption_ops": ops, "contract_ops": [], "source": "mech-assumption-recheck"}


def mech_repair(model: dict, ev: Evidence) -> dict:
    """Repairs for whatever the current evidence refutes.

    Two kinds, both in the existing format:
      * a refuted contract whose counterexamples are all attributable to ONE model assumption is repaired by
        DROPPING that assumption, which is the correct causal attribution when the contract's claim is right and
        the licence for evaluating it is not;
      * otherwise the contract is retired with its counterexamples recorded.
    """
    ops_a, ops_c = [], []
    A = list(model.get("assumptions") or [])
    # REFUTED contracts, not active ones. `apply_proposal` auto-refutes every active contract that has acquired a
    # counterexample, so by the time a repair is proposed there is nothing refuted left among the active ones; the
    # model keeps refuted versions with their counterexamples precisely so that a repair can target them. An
    # earlier version of this function looked at `active(model)` and therefore never fired at all.
    for c in [x for x in model["contracts"] if x.get("status") == "refuted"]:
        sc = score_contract(c, ev, A)
        if not sc["counter"]:
            continue
        blamed = [a for a in A if a in DIRECTLY_TESTABLE and direct_contradictions(a, ev)]
        if blamed:
            for a in blamed:
                wit = direct_contradictions(a, ev)
                if not any(o.get("name") == a for o in ops_a):
                    ops_a.append({"op": "drop", "name": a,
                                  "reason": f"the records contradict it in {len(wit)} place(s); the contract "
                                            f"{c['id']} it licensed is refuted at {', '.join(sc['counter'][:3])}, "
                                            f"and the claim is sound where the licence holds",
                                  "witnesses": wit[:MAX_IDS]})
        else:
            ops_c.append({"op": "retire", "target": c["id"],
                          "reason": f"counterexample(s) {sc['counter'][:3]} with no assumption to attribute them to",
                          "witnesses": sc["counter"][:MAX_IDS]})
    return {"assumption_ops": ops_a, "contract_ops": ops_c, "source": "mech"}


def recheck(model: dict, ev: Evidence) -> dict:
    """Re-score every contract of a model over all evidence, without changing the model. For the repair report."""
    A = list(model.get("assumptions") or [])
    out = {}
    for c in model["contracts"]:
        out[f"{c.get('id')}@v{c.get('version')}"] = {"status": c.get("status"),
                                                     **summarize(score_contract(c, ev, A))}
    return out


def uncovered_tools(attempts: list) -> dict:
    out: dict = {}
    for a in attempts:
        for s in a.get("steps") or []:
            if s.get("kind") == "tool" and s.get("tool") not in VERBS:
                key = s.get("tool") or "(empty)"
                out[key] = out.get(key, 0) + 1
    return out
