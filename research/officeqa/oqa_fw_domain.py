#!/usr/bin/env python3
"""The OfficeQA domain for the ported framework: entities, atoms, assumptions and the prefix estimator.

WHAT IS CARRIED OVER AND WHAT IS NOT. The MECHANISMS come from ALFWorld (see
`framework/upstream/MANIFEST.json`): the kinds, typed entity variables, guards, Kleene values with
provenance, the gate, repair, regression and the frozen-model/prefix discipline. The CONTENT is
OfficeQA's own and nothing physical is imported: no object vocabulary, no receptacles, no lamp rule, no
learned ALFWorld model, no task fact.

THE ENTITIES are document structure, named by STABLE OBSERVATION REFERENCES (`oqa_fw_extract`):
  table   tb:s12:1     the 2nd table of the observation at conversation item 12
  column  co:s12:1:3   its 4th column
  cell    ce:s12:1:4:3 the data cell at row 5, column 4
  operand op:s13:2     the 3rd number the actor wrote in message 13
  scale   sc:thousands one of units / thousands / millions / billions

WHAT EACH ATOM IS, and the categories matter more than the list: only LEARNED and ASSUMPTION belong to
the learner. `nearer_unit_stated` is the hinge of the whole slice - it is a SUPPLIED tri-state, and its
UNKNOWN case is not a defect to be removed but the honest answer when the scope that would carry an
overriding annotation was never observed. What the learned model supplies is not the answer: it is the
licence to read the split header's own child row, which turns that UNKNOWN into an observation.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "framework"))

import oqa_cc_extract as X  # noqa: E402
import oqa_fw_extract as E  # noqa: E402
import oqa_lc_cells as CELLS  # noqa: E402
import oqa_lc_vocab as LCV  # noqa: E402  (the LC generation's assumption readings, reused verbatim)
from fw_domain import Domain  # noqa: E402
from fw_kleene import fact, is_unnamed, k_and, k_or, merge, unnamed, unnamed_type  # noqa: E402
from fw_state import Estimator  # noqa: E402

VERSION = "oqa.fw.domain.v1"
SCALES = ("units", "thousands", "millions", "billions")
_TOOL = re.compile(r"^\s*(read|grep|glob|answer)\b", re.I)
_PATH = re.compile(r"path\s*=\s*'([^']*)'")
_PAT = re.compile(r"pattern\s*=\s*'([^']*)'")

ATOMS = {
    # ---- tables -------------------------------------------------------------------------------------
    "table_observed":    (("table",), "OBS", "this table appeared in an observation in the prefix"),
    "header_split":      (("table",), "OBS", "its header is a split or single form carrying a sub-label row"),
    "child_aligned":     (("table",), "OBS", "that sub-label row has exactly one cell per column"),
    "scale_stated":      (("table",), "OBS", "a money scale is stated in the table's heading"),
    "flat_pair_observed": (("table",), "OBS", "a flattened rendering of the SAME table (a shared data row) "
                                          "was observed somewhere in the prefix"),
    # ---- columns ------------------------------------------------------------------------------------
    "col_of":            (("column", "table"), "MACHINERY", "this column belongs to this table"),
    "col_label_present": (("column",), "OBS", "the sub-label at this column index is a real column name - not "
                                         "empty, not the text `nan`, not an \"Unnamed: n\" placeholder "
                                         "and not a bare ordinal such as \"(3)\""),
    "col_child_label_present": (("column",), "OBS", "the SUB-LABEL ROW's own cell at this index is a real "
                                               "column name. Different from `col_label_present`, which "
                                               "is satisfied by the spanning parent the merge falls "
                                               "back to; a spanning parent is printed once at its "
                                               "first column, so the fallback is shifted"),
    "col_label_agrees_flat": (("column",), "OBS", "the paired flat rendering names the same innermost label "
                                             "at this index; UNKNOWN when no flat rendering of this "
                                             "table was observed, and UNKNOWN when two of them "
                                             "disagree with each other here"),
    "col_bound":         (("column",), "LEARNED-DEPENDENT", "the index-merged sub-label may be read as this "
                                                       "column's name: true only under the adopted "
                                                       "`index_alignment` assumption AND an active "
                                                       "learned rule that covers this column"),
    # ---- cells --------------------------------------------------------------------------------------
    "cell_of":           (("cell", "table"), "MACHINERY", "this data cell came from this table"),
    "cell_col":          (("cell", "column"), "MACHINERY", "this data cell sits in this column"),
    "cell_row_labelled": (("cell",), "OBS", "the cell's row carries a row label"),
    "nearer_unit_stated": (("cell",), "MACHINERY", "a nearer scope (the column header or the row label) "
                                                "states a money scale other than the table's; UNKNOWN "
                                                "when a nearer scope was not observed, because an "
                                                "unobserved scope cannot witness an absence"),
    "cell_scale_resolved": (("cell",), "MACHINERY", "the money scale that applies to this cell is "
                                                 "identified: the table states one and no nearer scope "
                                                 "overrides it (under `table_scale_applies_to_rows`)"),
    # ---- operands and the question ------------------------------------------------------------------
    "operand_matches":   (("operand", "cell"), "MACHINERY", "this written operand matches this data cell "
                                                   "uniquely, compared at the written precision"),
    "conversion_needed": (("operand",), "MATH", "the operand's resolved scale differs from the scale the "
                                          "question asks for, so a factor of ten is required"),
    "conversion_stated": (("operand",), "OBS", "the actor wrote a factor of ten for this operand"),
}

# The LC generation's assumption readings, reused verbatim so the provenance is one place.
ASSUMPTIONS = {k: LCV.ASSUMPTIONS[k] for k in
               ("index_alignment", "flat_header_authoritative", "table_scale_applies_to_rows",
                "grep_cap_not_reached")}
DIRECTLY_TESTABLE = ("index_alignment", "flat_header_authoritative")

VERBS = {
    "read": {"roles": ("F",), "head": "read F"},
    "grep": {"roles": ("P",), "head": "grep P"},
    "glob": {"roles": ("P",), "head": "glob P"},
    "answer": {"roles": (), "head": "answer"},
}


class OfficeQADomain(Domain):
    name = "officeqa"
    version = VERSION
    verbs = VERBS
    atoms = ATOMS
    kinds = ("concludes", "requires", "suffices", "effect")
    assumptions = ASSUMPTIONS
    directly_testable = DIRECTLY_TESTABLE
    placeholders = ()
    types = ("table", "column", "cell", "operand", "scale")
    variables = True
    guards = True
    subjects = True
    persist_states = {}
    invalidators = {}
    completion_atom = None

    # The licence atom `col_bound` is true only through an active learned rule whose consequence is
    # this atom; naming it here keeps the dependency in the vocabulary rather than in a driver.
    LICENCE_CONSEQUENCE = "col_label_agrees_flat"

    def __init__(self, entities=None, asked_scale=None, question_numbers=(), model=None):
        self.entities = entities or {"tables": {}, "columns": {}, "cells": {}, "operands": {},
                                     "introduced": {}}
        self.asked_scale = asked_scale
        self.question_numbers = set(question_numbers)
        self.model = model          # the model whose ACTIVE rules license `col_bound`; None = absent

    def licence_rules(self) -> list:
        """Active `concludes` rules whose consequence is the licence atom. An empty list is the ABSENT
        condition of the ablation: no rule, so no licence, so `col_bound` is UNKNOWN."""
        import fw_ir as IR
        if not self.model:
            return []
        return [c for c in IR.active(self.model, "concludes")
                if (c.get("consequence") or {}).get("atom") == self.LICENCE_CONSEQUENCE
                and (c.get("consequence") or {}).get("value") is True]

    # ---- record reading --------------------------------------------------------------------------
    def parse_command(self, step) -> dict:
        cmd = (step or {}).get("cmd") if isinstance(step, dict) else str(step or "")
        cmd = str(cmd or "")
        m = _TOOL.match(cmd)
        if not m:
            return {"kind": "other", "verb": None, "args": {}}
        verb = m.group(1).lower()
        args = {}
        if verb == "read":
            p = _PATH.search(cmd)
            args["F"] = os.path.basename(p.group(1)) if p else None
        elif verb in ("grep", "glob"):
            p = _PAT.search(cmd)
            args["P"] = p.group(1) if p else None
        return {"kind": "covered", "verb": verb, "args": {k: v for k, v in args.items() if v}}

    def outcome_of(self, step) -> str:
        return LCV.outcome_of(step)

    def task_info(self, task) -> dict:
        return {}

    def type_of(self, entity):
        return E.kind_of(entity) if not is_unnamed(entity) else unnamed_type(entity)

    def estimator(self, task_type, steps, assumptions=(), persists=()):
        est = OfficeQAEstimator.of_steps(self, task_type, steps, assumptions, persists)
        est.model_rules = self.licence_rules()
        return est

    # ---- the one directly testable assumption ------------------------------------------------------
    def direct_contradictions(self, name: str, ev) -> list:
        """Records that contradict an assumption outright.

        `index_alignment` is contradicted where a split table's sub-label row does NOT have one cell per
        column: merging by index is then not even well defined. It is NOT contradicted merely because a
        merged label disagrees with a flat rendering - that is a counterexample to a CONTRACT, and it is
        the learner's job to find the condition that excludes it.
        `flat_header_authoritative` is contradicted where two flat renderings of the same table (a
        shared data row, the same width) name different labels at the same index.
        """
        out = []
        for ep in ev.episodes:
            for s in ep["steps"]:
                if s.get("kind") != "tool":
                    continue
                ts = CELLS.tables(str(s.get("obs") or ""))
                if name == "index_alignment":
                    for k, t in enumerate(ts):
                        if t["header_form"] in ("split", "single") and \
                                len(t.get("child_row") or []) != t["width"]:
                            out.append(f"{ep['episode_id']}@{s['i']} table {k + 1}: the sub-label row "
                                       f"has {len(t.get('child_row') or [])} cells for {t['width']} "
                                       f"columns, so merging by index is undefined")
                elif name == "flat_header_authoritative":
                    flats = [t for t in ts if t["header_form"] == "flat"]
                    for a in range(len(flats)):
                        for b in range(a + 1, len(flats)):
                            ta, tb = flats[a], flats[b]
                            if ta["width"] != tb["width"] or not (set(ta["fingerprints"])
                                                                  & set(tb["fingerprints"])):
                                continue
                            for j in range(ta["width"]):
                                la = ((ta.get("flat") or [{}])[j] or {}).get("child") or ""
                                lb = ((tb.get("flat") or [{}])[j] or {}).get("child") or ""
                                if CELLS._norm(la) != CELLS._norm(lb):
                                    out.append(f"{ep['episode_id']}@{s['i']}: two flat renderings of "
                                               f"the same table name {la!r} and {lb!r} at index {j}")
        return out


class OfficeQAEstimator(Estimator):
    """Prefix-only values for the OfficeQA atoms. Every method reads entities whose `step` is < t."""

    def __init__(self, dom, assumptions=(), task_type=None, persists=()):
        super().__init__(dom, assumptions, task_type, persists)
        self.model_rules: list = []     # active `concludes` rules whose consequence licenses col_bound
        self.ents = dom.entities

    # ---- entities -----------------------------------------------------------------------------------
    def _visible(self, kind: str, t: int) -> list:
        key = {"table": "tables", "column": "columns", "cell": "cells", "operand": "operands"}[kind]
        return [r for r, e in (self.ents.get(key) or {}).items() if int(e.get("step", -1)) < t]

    def names(self, t: int) -> set:
        out = set()
        for k in ("table", "column", "cell", "operand"):
            out |= set(self._visible(k, t))
        return out

    def entities_of_type(self, t: int, ty: str) -> list:
        """Named instances visible in the prefix, PLUS one unnamed instance.

        The unnamed token is ALFWorld's (`fw_kleene.unnamed`) and leaving it out is a real mistake, not
        a simplification: with an empty pool `fw_ir.evaluate` has nothing to OR and returns False, so
        every rule with an existential would be scored against a condition that is FALSE before its
        entities appear instead of UNKNOWN. Every atom below returns UNKNOWN for an unnamed argument,
        because `_t`/`_c`/`_x` cannot find it.
        """
        if ty == "scale":
            return [E.scale_ref(s) for s in SCALES]
        if ty not in ("table", "column", "cell", "operand"):
            return []
        return sorted(self._visible(ty, t)) + [unnamed(ty)]

    def introduced_at(self, t: int, ty: str) -> list:
        """Only what the observation of step t-1 introduced: the claim is about this returned window."""
        if ty == "scale":
            return [E.scale_ref(s) for s in SCALES]
        if t - 1 < 0 or t - 1 >= len(self.steps):
            return [unnamed(ty)]
        i = int(self.steps[t - 1].get("i", t - 1))
        return sorted((self.ents.get("introduced") or {}).get(i, {}).get(ty, [])) + [unnamed(ty)]

    # ---- helpers ------------------------------------------------------------------------------------
    def _t(self, ref):
        return (self.ents.get("tables") or {}).get(ref)

    def _c(self, ref):
        return (self.ents.get("columns") or {}).get(ref)

    def _x(self, ref):
        return (self.ents.get("cells") or {}).get(ref)

    def _o(self, ref):
        return (self.ents.get("operands") or {}).get(ref)

    @staticmethod
    def _seen(e, t) -> bool:
        return e is not None and int(e.get("step", 1 << 30)) < t

    def _unknown(self):
        return fact(None, "none")

    # ---- table atoms ---------------------------------------------------------------------------------
    def _v_table_observed(self, t, tref):
        e = self._t(tref)
        if e is None:
            return self._unknown()      # including the unnamed token: not seen is not "does not exist"
        return fact(True, "observed", [e["step"]]) if self._seen(e, t) else fact(None, "none")

    def _v_header_split(self, t, tref):
        e = self._t(tref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(e["header_form"] in ("split", "single"), "observed", [e["step"]])

    def _v_child_aligned(self, t, tref):
        e = self._t(tref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(len(e["child_row"]) == e["width"], "observed", [e["step"]])

    def _v_scale_stated(self, t, tref):
        e = self._t(tref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(bool(e.get("scale")), "observed", [e["step"]])

    def _v_flat_pair_observed(self, t, tref):
        e = self._t(tref)
        if not self._seen(e, t):
            return self._unknown()
        fp = e.get("flat_pair_step")
        return fact(fp is not None and fp < t, "observed", [e["step"]] + ([fp] if fp is not None else []))

    # ---- column atoms --------------------------------------------------------------------------------
    def _v_col_of(self, t, cref, tref):
        e = self._c(cref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(e["table"] == tref, "record", [e["step"]])

    def _v_col_label_present(self, t, cref):
        e = self._c(cref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(bool(e["label_present"]), "observed", [e["step"]])

    def _v_col_child_label_present(self, t, cref):
        e = self._c(cref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(bool(e.get("child_label_present")), "observed", [e["step"]])

    def _v_col_label_agrees_flat(self, t, cref):
        """UNKNOWN unless a flat rendering of this very table was observed in the prefix."""
        e = self._c(cref)
        if not self._seen(e, t):
            return self._unknown()
        if e.get("agrees_flat") is None or (e.get("pair_step") is not None and e["pair_step"] >= t):
            return fact(None, "none")
        return fact(bool(e["agrees_flat"]), "observed", [e["step"], e["pair_step"]])

    def _v_col_bound(self, t, cref):
        """LEARNED-DEPENDENT, and this is the hinge.

        True only when `index_alignment` is adopted AND an active learned rule covers this column -
        i.e. the rule's own conditions hold here. With no model, or with the covering rule refuted or
        retired, the value is UNKNOWN: the sub-label was printed, but nothing licenses reading it as
        this column's name. Never False from absence.
        """
        e = self._c(cref)
        if not self._seen(e, t):
            return self._unknown()
        if "index_alignment" not in self.A:
            return fact(None, "none")
        for r in self.model_rules:
            if self._rule_covers(r, t, cref) is True:
                f = fact(True, "inferred", [e["step"]], ("index_alignment",))
                f["learned"] = [r["id"]]
                return f
        return fact(None, "stale", [e["step"]])

    def _rule_covers(self, rule: dict, t: int, cref: str):
        """Kleene value of the rule's conditions at this column. Imported lazily to avoid a cycle."""
        import fw_ir as IR
        return IR.evaluate(rule, rule["conditions"], self, t, {list(rule["subject"])[0]: cref}, {},
                           self.dom)["v"]

    # ---- cell atoms ----------------------------------------------------------------------------------
    def _v_cell_of(self, t, xref, tref):
        e = self._x(xref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(e["table"] == tref, "record", [e["step"]])

    def _v_cell_col(self, t, xref, cref):
        e = self._x(xref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(e["column"] == cref, "record", [e["step"]])

    def _v_cell_row_labelled(self, t, xref):
        e = self._x(xref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(bool(str(e.get("row_label") or "").strip()), "observed", [e["step"]])

    def _v_nearer_unit_stated(self, t, xref):
        """The tri-state of `oqa_cc_extract.nearer_scale`, with the column scope gated on `col_bound`.

        PRESENT  a nearer scope states another money scale  -> True
        ABSENT   both nearer scopes were observed and neither does -> False
        UNKNOWN  a nearer scope was not observed, or the column is not bound -> None
        """
        e = self._x(xref)
        if not self._seen(e, t):
            return self._unknown()
        tbl = self._t(e["table"]) or {}
        tscale = CELLS.canon_scale(str(tbl.get("scale") or "")) or None
        row_ok = bool(str(e.get("row_label") or "").strip())
        cb = self.value(t, "col_bound", [e["column"]])
        col_text = (self._c(e["column"]) or {}).get("merged_label")
        cu = X.scale_word(col_text) if cb["v"] is True else None
        ru = X.scale_word(e.get("row_label")) if row_ok else None
        if (cu is not None and cu != tscale) or (ru is not None and ru != tscale):
            return merge(True, [cb, fact(True, "observed", [e["step"]])])
        if cb["v"] is True and row_ok:
            return merge(False, [cb, fact(False, "observed", [e["step"]])])
        return fact(None, "none", [e["step"]])

    def _v_cell_scale_resolved(self, t, xref):
        e = self._x(xref)
        if not self._seen(e, t):
            return self._unknown()
        tbl = self._t(e["table"]) or {}
        if not tbl.get("scale"):
            return fact(False, "observed", [e["step"]])
        if "table_scale_applies_to_rows" not in self.A:
            return fact(None, "none", [e["step"]])
        nu = self.value(t, "nearer_unit_stated", [xref])
        if nu["v"] is None:
            return merge(None, [nu])
        if nu["v"] is True:
            return merge(False, [nu])
        return merge(True, [nu, fact(True, "inferred", [e["step"]], ("table_scale_applies_to_rows",))])

    # ---- operand atoms --------------------------------------------------------------------------------
    def _v_operand_matches(self, t, oref, xref):
        e = self._o(oref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(bool(e.get("unique")) and e.get("cell") == xref, "record", [e["step"]])

    def _v_conversion_needed(self, t, oref):
        e = self._o(oref)
        if not self._seen(e, t) or not e.get("cell"):
            return self._unknown()
        if not self.dom.asked_scale:
            return self._unknown()
        res = self.value(t, "cell_scale_resolved", [e["cell"]])
        if res["v"] is not True:
            return merge(None, [res])
        x = self._x(e["cell"]) or {}
        req = X.required_exp(CELLS.canon_scale(str(x.get("table_scale") or "")), self.dom.asked_scale)
        if req is None:
            return merge(None, [res])
        return merge(req != 0, [res, fact(req != 0, "static", [])])

    def _v_conversion_stated(self, t, oref):
        e = self._o(oref)
        if not self._seen(e, t):
            return self._unknown()
        return fact(bool(e.get("conversion_stated")), "observed", [e["step"]])
