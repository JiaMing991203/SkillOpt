#!/usr/bin/env python3
"""Proposers for the ported framework: a vocabulary SEARCH proposer, and the provider request builder.

TWO PROPOSERS, and the difference is declared everywhere it matters.

  `SearchProposer` enumerates candidate hypotheses inside the declared vocabulary and keeps the ones the
  gate accepts. It makes NO provider call. It is a real learner over the hypothesis space - it is given
  the atoms and the kinds, not the answer, and it finds the condition by being refuted first - but it is
  not the DeepSeek learner the ALFWorld studies used, and no result from it is reported as one.

  `build_request` builds the same shape of request the provider learner receives in
  `upstream/vx1/vx_prompt.py`: the current model, the new records, the committed predictions and the
  gate's own reasons for every rejected or refuted operation. It is prepared so the authorised teacher
  attempts can run as a cluster job; this module never opens a socket.

WHAT THE PROPOSER MAY SEE: the permitted analyst view only - the saved conversation and the saved
prompts. Not `ground_truth`, not `soft`, not `hard`, not a score, not a skill, not held-out material,
and not the validation split. The search proposer gets the gate's scores over that evidence, which is
what a learner is entitled to; its acceptance is then checked on a RESERVED record under the frozen
model, which is what makes the acceptance mean anything.
"""
from __future__ import annotations

import itertools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import fw_check as K  # noqa: E402
import fw_ir as IR  # noqa: E402

MAX_CONJUNCTS = 3


def _unary(atom: str, var: str, value: bool = True) -> dict:
    return {"atom": atom, "args": [var], "value": value}


def _binary(atom: str, a: str, b: str, value: bool = True) -> dict:
    return {"atom": atom, "args": [a, b], "value": value}


class SearchProposer:
    """Enumerate `concludes` hypotheses over a domain's atoms, smallest condition set first.

    The order matters and is the point: the most general hypothesis is offered FIRST, so when it is
    wrong the gate refuses it and names the counterexamples, and only then is a narrower one tried. The
    narrowing is therefore a response to recorded evidence rather than a researcher's choice.
    """

    def __init__(self, dom, subject_type: str, consequence: str, *, scope: str = "any",
                 subject_scope: str = "once", max_conjuncts: int = MAX_CONJUNCTS,
                 link_types=("table",)):
        self.dom = dom
        self.subject_type = subject_type
        self.consequence = consequence
        self.scope = scope
        self.subject_scope = subject_scope
        self.max_conjuncts = max_conjuncts
        self.link_types = tuple(link_types)
        self.sv = "?s"

    # ---- the hypothesis space ------------------------------------------------------------------------
    def conjuncts(self) -> list:
        """Every clause the vocabulary allows over the subject and its linked entity.

        Arguments are TYPED, so the space is only the atoms that can actually be said about a column and
        about the table a column belongs to: 2 column atoms in 2 polarities, 5 table atoms in 2
        polarities, and the link itself. A rule may use neither its own consequence nor the atom its
        acceptance would license (`col_bound`), which is the rule against circularity.
        """
        out = []
        for atom, (kinds, cat, _why) in sorted(self.dom.atoms.items()):
            if atom == self.consequence or cat == "LEARNED-DEPENDENT":
                continue
            if len(kinds) == 1 and kinds[0] == self.subject_type:
                for v in (True, False):
                    out.append({"clause": [_unary(atom, self.sv, v)], "exists": {}, "link": False})
            elif len(kinds) == 1 and kinds[0] in self.link_types:
                for v in (True, False):
                    out.append({"clause": [_unary(atom, "?t", v)], "exists": {"?t": kinds[0]},
                                "link": True})
        for atom, (kinds, cat, _why) in sorted(self.dom.atoms.items()):
            if len(kinds) == 2 and kinds[0] == self.subject_type and kinds[1] in self.link_types:
                self.link_clause = {"clause": [_binary(atom, self.sv, "?t")],
                                    "exists": {"?t": kinds[1]}, "link": False}
        return out

    def candidates(self) -> list:
        """Candidate contracts, by increasing number of conjuncts, then by a stable order."""
        self.link_clause = None
        pool = self.conjuncts()
        seen, out = set(), []
        for n in range(1, self.max_conjuncts + 1):
            for combo in itertools.combinations(range(len(pool)), n):
                ex, clauses = {}, []
                if any(pool[i]["link"] for i in combo):
                    if self.link_clause is None:
                        continue
                    clauses.append(self.link_clause["clause"])
                    ex.update(self.link_clause["exists"])
                for idx in combo:
                    clauses.append(pool[idx]["clause"])
                    ex.update(pool[idx]["exists"])
                atoms = tuple(sorted((l["atom"], l["value"]) for cl in clauses for l in cl))
                if len(set(a for a, _ in atoms)) != len(atoms):
                    continue        # the same atom asserted twice, or both polarities
                if atoms in seen:
                    continue
                seen.add(atoms)
                c = {"kind": "concludes", "scope": self.scope,
                     "subject": {self.sv: self.subject_type}, "subject_scope": self.subject_scope,
                     "conditions": clauses,
                     "consequence": _unary(self.consequence, self.sv, True),
                     "context": {"task_types": []}}
                if ex:
                    c["exists"] = ex
                if not IR.validate(c, self.dom):
                    out.append(c)
        return out

    # ---- the search ------------------------------------------------------------------------------------
    def propose(self, model: dict, ev, *, want: int = 1, label_fn=None) -> dict:
        """Score every candidate and return the operations for the best ones, with the full trail.

        A candidate is ACCEPTABLE when the ordinary gate has nothing against it. Among acceptable ones
        the most general wins: fewest conjuncts, then most support. Everything scored is reported,
        including the rejected general forms and the gate's own reason, because the refutation of the
        general form is the finding that justifies the narrower rule.
        """
        A, P = model["assumptions"], IR.active(model, "persists")
        trail, ok = [], []
        for c in self.candidates():
            sc = K.score(c, ev, A, P, self.dom, label_fn)
            why = K.gate(sc, "concludes")
            row = {"rendered": IR.render(c, self.dom), "n_conjuncts": len(c["conditions"]),
                   "n_support": len(sc["support"]), "n_counter": len(sc["counter"]),
                   "n_consistent": len(sc["consistent"]), "undetermined": sc["undetermined"],
                   "applicable": sc["applicable"], "gate": why,
                   "counter": sc["counter"][:6],
                   "counter_witness": {k: sc["counter_witness"][k] for k in sc["counter"][:3]
                                       if k in sc["counter_witness"]},
                   "acceptable": not why, "contract": c}
            trail.append(row)
            if not why:
                ok.append(row)
        ok.sort(key=lambda r: (r["n_conjuncts"], -r["n_support"], r["rendered"]))
        chosen = ok[:want]
        return {
            "proposer": "search", "n_candidates": len(trail),
            "n_acceptable": len(ok),
            "trail": sorted(trail, key=lambda r: (r["n_conjuncts"], -r["n_support"], r["rendered"])),
            "contract_ops": [{"op": "add", "contract": r["contract"],
                              "reason": ("the most general form the recorded evidence does not refute: "
                                         f"{r['n_support']} supporting column(s), {r['n_counter']} "
                                         f"counterexample(s), {r['undetermined']} undetermined"),
                              "witnesses": []} for r in chosen],
            "assumption_ops": [],
        }


# ---- the provider request (prepared, never sent from here) ------------------------------------------
def build_request(model: dict, dom, new_records: list, decisions: list, batch: int, n_batches: int):
    """The learner request, in `upstream/vx1/vx_prompt.build_request`'s shape, as plain data.

    Returned, not sent. `oqa_fw_run.py --prepare-teacher` writes it to the run directory so the
    authorised teacher attempts can be made by a cluster job under the existing ledger.
    """
    return {
        "batch": f"Batch {batch} of at most {n_batches}",
        "vocabulary": {"atoms": {a: list(t) for a, t in dom.atoms.items()},
                       "kinds": list(dom.kinds),
                       "assumptions": {k: list(v) for k, v in dom.assumptions.items()},
                       "types": list(dom.types), "verbs": {v: list(s["roles"])
                                                           for v, s in dom.verbs.items()}},
        "model": {"version": model["version"], "assumptions": model["assumptions"],
                  "contracts": [{"id": c["id"], "status": c.get("status"),
                                 "rendered": IR.render(c, dom), "evidence": c.get("evidence")}
                                for c in model["contracts"]]},
        "new_records": new_records,
        "gate_feedback": [{"op": d.get("op"), "target": d.get("target"),
                           "decision": d.get("decision"), "why": d.get("why"),
                           "evidence": d.get("evidence")}
                          for d in decisions
                          if d.get("decision") in ("rejected", "refuted")],
        "instructions": ("Propose contract operations and assumption operations. Every operation is "
                         "gated by code over all permitted evidence so far: zero counterexamples and at "
                         "least one supporting case. Abstaining is a legitimate answer."),
    }
