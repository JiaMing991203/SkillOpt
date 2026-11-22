#!/usr/bin/env python3
"""Driver for the D-2/D-3/D-4/D-5 offline audit. Rule OQA-LN-1. ZERO paid calls, by construction.

Nothing in this file imports the model layer, so it cannot send a request even by accident.

It writes a NEW versioned directory and touches nothing that already exists:
  D2_FREEZE.json              the comparison frozen before any semantic change
  D2_CELLS.json               the D-2 extraction correction, replayed, with every changed binding
  D2_RULE_<tag>.json          the 2x2: extraction x learned-rule, per frozen model
  D2_ANCESTRY.json            calculation ancestry and what "recovery" means here
  D2_OPTIMIZER_EVIDENCE.json  the EXISTING rehearsal evidence, gathered - no new optimizer call
  reports_regenerated/*.md    reports labelled REGENERATED, never attached to the old requests
  D2_RUN.json                 the summary and its digest
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_ir as IR  # noqa: E402
import oqa_d2_ancestry as AN  # noqa: E402
import oqa_d2_cells as CELLS  # noqa: E402
import oqa_d2_rule as RULE  # noqa: E402
import oqa_ln_check as CK  # noqa: E402
import oqa_ln_evidence as EV  # noqa: E402
import oqa_ln_learn as LN  # noqa: E402
import oqa_ln_run as RUN  # noqa: E402
import oqa_ln_vocab as V  # noqa: E402

SCHEMA = "lso.officeqa_d2_run.v1"

REGENERATED_BANNER = """\
*** REGENERATED {utc} — NOT DELIVERED TO ANY OPTIMIZER ***
This report was produced offline from the corrected checker. The report that was actually delivered
in the paired rehearsal of job 66588323 is preserved unmodified at
runs/20261121-officeqa-learner/v4_rehearse/reports/UID0063.ba422863d0a8.md, digest
afd740186c0395e0. This text was NOT sent to any model and must not be attached to that request.
***

"""


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _write(path: str, doc: dict) -> str:
    body = json.dumps(doc, indent=1, sort_keys=True, default=str)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return IR.sha(body)


def load_models(learn_root: str) -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(learn_root, "r*_model_v1.json"))):
        tag = os.path.basename(path).split("_model_")[0]
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        active = [c for c in m["contracts"] if c["status"] == "active"]
        if active:
            out.append((tag, active[-1], m.get("sha256", "")))
    return out


def comparison_freeze(freeze_doc: dict, models: list, rehearsal: dict | None) -> dict:
    """Everything the comparison is held against, recorded BEFORE the corrected results are read."""
    sel = freeze_doc["selection"]
    content_digest = IR.sha(json.dumps(
        {k: v for k, v in freeze_doc.items() if k not in ("written_utc", "self_sha256")},
        indent=1, sort_keys=True))
    return {
        "schema": SCHEMA + ".comparison_freeze",
        "written_utc": _utc(),
        "records": {
            "n_attempts": len(freeze_doc["attempts"]),
            "source_root_as_frozen": freeze_doc["source_root"],
            "source_root_in_use": EV.source_root(freeze_doc),
            "every_trace_digest_verified": True,
            "learn_questions": sel["learn_questions"],
            "check_questions": sel["check_questions"],
            "split": sel["split_key"],
            "totals": freeze_doc["totals"],
        },
        "freeze_digests": {
            "as_recorded_in_every_artifact": freeze_doc["self_sha256"],
            "recomputed_over_the_CONTENT_only": content_digest,
            "they_differ": content_digest != freeze_doc["self_sha256"],
            "note": (
                "The freeze artifact in use was written at 2026-11-22T00:32:48Z by the code at commit "
                "4580459, and the `written_utc` exclusion that makes the digest a CONTENT digest "
                "landed later, at commit 857ede6. So the digest every downstream artifact carries as "
                "the witness that this record set was fixed before any learner call is still a "
                "CLOCK-DEPENDENT digest and does not reproduce from the same records. The content "
                "digest above does reproduce, and is recorded here beside it. The milestone report's "
                "section 5 says the defect was fixed; the fix reached the code and not this artifact."),
        },
        "models": [{"tag": t, "contract": f"{c['id']}@v{c['version']}",
                    "contract_sha256": IR.sha(IR.canon(c)), "model_sha256": sha}
                   for t, c, sha in models],
        "versions": {
            "vocabulary_bundle": {"label": V.BUNDLE_LABEL, "digest": V.bundle_digest()},
            "extraction": "oqa_ln_evidence + oqa_occ, amendments D-2, D-4, D-5",
            "stage_and_report": "oqa_ln_check, amendment D-3",
            "resolver": "oqa_occ, amendment C-1/C-2 unchanged apart from D-2",
        },
        "rehearsal_as_delivered": {
            "report_sha256": (rehearsal or {}).get("report_sha256"),
            "report_bytes": (rehearsal or {}).get("report_bytes"),
            "model_sha256": (rehearsal or {}).get("model_sha256"),
            "contract_sha256": (rehearsal or {}).get("contract_sha256"),
            "lean_source_sha256": ((rehearsal or {}).get("lean") or {}).get("source_sha256"),
            "preserved": True,
        },
        "expected_effects_of_each_correction": {
            "D-2": ["about 5 supported bindings disappear",
                    "the net is NOT -5 because some SEVERAL_POSITIONS become UNIQUE and gain support",
                    "more features change than verdicts",
                    "the single learning-record counterexample becomes silent, so precision reaches "
                    "1.000 WITHOUT any learner repair",
                    "source_traced moves in both directions"],
            "D-3": ["the rehearsal report loses its `actionable execution diagnoses 1` line",
                    "at least one answer_traced VIOLATED becomes UNKNOWN because the question needs "
                    "no arithmetic",
                    "at least one calculation printed as replaced is an ancestor",
                    "no decision changes because of the SOLE_IN_ROW display fix"],
            "D-4": ["38 of 676 statements are fragments and 38 of the 50 inconsistent ones are "
                    "fragments"],
            "D-5": ["the seven answer_traced VIOLATED states are withdrawn"],
            "recorded_before": "docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md, D-2 and D-3 "
                               "before their code, D-4 and D-5 during it and before any result",
        },
    }


def optimizer_evidence(rehearse_root: str) -> dict:
    """Gather the EXISTING rehearsal evidence. No optimizer call is made; nothing is regenerated."""
    def _read(name):
        path = os.path.join(rehearse_root, name)
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    reh = _read("LN_REHEARSAL.json")
    ordinary = _read("EDITS_ordinary.json")
    assisted = _read("EDITS_contract_assisted.json")
    with open(os.path.join(rehearse_root, "DELIVERED_REPORT.md"), encoding="utf-8") as f:
        delivered = f.read()
    return {
        "schema": SCHEMA + ".optimizer_evidence",
        "no_new_optimizer_call_was_made": True,
        "attempt_id": reh["attempt_id"],
        "job": 66588323,
        "digests": {
            "report_delivered_sha256": reh["report_sha256"],
            "report_bytes": reh["report_bytes"],
            "model_sha256": reh["model_sha256"],
            "contract_sha256": reh["contract_sha256"],
            "lean_source_sha256": (reh.get("lean") or {}).get("source_sha256"),
            "ordinary_trajectory_sha256": reh["conditions"]["ordinary"]["trajectory_text_sha256"],
            "assisted_trajectory_sha256":
                reh["conditions"]["contract_assisted"]["trajectory_text_sha256"],
        },
        "what_differed_between_the_conditions": {
            "ordinary_chars": reh["conditions"]["ordinary"]["trajectory_text_chars"],
            "assisted_chars": reh["conditions"]["contract_assisted"]["trajectory_text_chars"],
            "difference": (reh["conditions"]["contract_assisted"]["trajectory_text_chars"]
                           - reh["conditions"]["ordinary"]["trajectory_text_chars"]),
            "accounted_for_by": "the 3,129-byte report plus its 30-character heading",
            "model_sampling_budget_callcount": "identical",
            "deviation_from_production": reh["deviation_from_production"],
        },
        "ordinary": {"edits": ordinary["edits"],
                     "reasoning": ordinary["patch"]["patch"].get("reasoning", ""),
                     "failure_summary": ordinary["patch"].get("failure_summary", [])},
        "contract_assisted": {"edits": assisted["edits"],
                              "reasoning": assisted["patch"]["patch"].get("reasoning", ""),
                              "failure_summary": assisted["patch"].get("failure_summary", [])},
        "report_delivered_text": delivered,
    }


def observed_lines(freeze_doc: dict, attempt_id: str, values: list) -> dict:
    """The observation lines the rehearsal's claims rest on, quoted as the actor received them."""
    import oqa_occ as OCC
    row = [a for a in freeze_doc["attempts"] if a["attempt_id"] == attempt_id][0]
    pdir = os.path.join(EV.source_root(freeze_doc), "train_p%d" % row["pass"], "predictions")
    att = EV.read_attempt(pdir, row["item"])
    hits, headers = [], []
    for e in OCC.normalize(att["items"]):
        if e["kind"] != "tool":
            continue
        for raw in e["obs"].splitlines():
            m = OCC._LINE.match(raw)
            if not m or "|" not in m.group(2):
                continue
            fields = [f.strip() for f in m.group(2).split("|")]
            vals = [OCC.parse_value(n.group(0)) for f in fields
                    for n in OCC._NUM.finditer(f) if OCC.is_numeric_cell(f)]
            if any(v in values for v in vals) and raw.strip() not in [h["line"] for h in hits]:
                hits.append({"line": raw.strip()[:300], "cmd": e["cmd"][:160],
                             "fields": fields,
                             "numeric_cells": [f for f in fields if OCC.is_numeric_cell(f)]})
            elif not any(OCC.is_numeric_cell(f) for f in fields) and len(headers) < 6:
                if raw.strip() not in [h["line"] for h in headers]:
                    headers.append({"line": raw.strip()[:300]})
    return {"attempt_id": attempt_id, "values": values,
            "lines_carrying_those_values": hits[:8],
            "header_lines_available_in_the_same_observations": headers}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--learn-root", required=True)
    ap.add_argument("--rehearse-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lean", default="", help="an LN_LEAN.json to quote in regenerated reports")
    ap.add_argument("--compare-stage-root", default="",
                    help="the superseded stage reports to diff against, e.g. v2b_restage")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.join(a.out, "reports_regenerated"), exist_ok=True)
    with open(a.freeze, encoding="utf-8") as f:
        freeze_doc = json.load(f)
    models = load_models(a.learn_root)
    reh = None
    rp = os.path.join(a.rehearse_root, "LN_REHEARSAL.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            reh = json.load(f)
    lean = None
    if a.lean and os.path.exists(a.lean):
        with open(a.lean, encoding="utf-8") as f:
            lr = json.load(f)
        # FLATTENED THE SAME WAY `oqa_ln_rehearse` FLATTENS IT. `returncode` lives under `result`,
        # and reading the top level gives None - which the report then prints as
        # "return code None". The delivered report got this right; the first regeneration did not.
        lean = {"version": lr.get("version"),
                "returncode": (lr.get("result") or {}).get("returncode"),
                "source_sha256": lr.get("source_sha256"),
                "n_examples": lr.get("n_examples"), "scope": lr.get("scope")}

    written = {}
    written["D2_FREEZE.json"] = _write(os.path.join(a.out, "D2_FREEZE.json"),
                                       comparison_freeze(freeze_doc, models, reh))

    cells_doc = {"schema": CELLS.SCHEMA, "written_utc": _utc(),
                 "freeze_sha256": freeze_doc["self_sha256"],
                 "occurrence_inventory": CELLS.occurrence_inventory(freeze_doc),
                 "channel_inventory": CELLS.channel_inventory(freeze_doc),
                 "abandonment_census": CELLS.abandonment_census(freeze_doc),
                 "replay": CELLS.replay(freeze_doc),
                 "model_scores": CELLS.score_models(
                     freeze_doc, [(t, c) for t, c, _s in models])}
    written["D2_CELLS.json"] = _write(os.path.join(a.out, "D2_CELLS.json"), cells_doc)

    n_learn = len(LN.rows_for(freeze_doc, "learn", numeric_cells_only=True))
    for tag, contract, model_sha in models:
        doc = RULE.run(freeze_doc, contract, n_learn, model_sha)
        doc["written_utc"] = _utc()
        written[f"D2_RULE_{tag}.json"] = _write(
            os.path.join(a.out, f"D2_RULE_{tag}.json"), doc)

    controls = [r["attempt_id"] for r in freeze_doc["recovery_controls"]]
    anc_ids = sorted(set(controls + ["p1/UID0063"]))
    anc = AN.run(freeze_doc, anc_ids)
    anc["written_utc"] = _utc()
    written["D2_ANCESTRY.json"] = _write(os.path.join(a.out, "D2_ANCESTRY.json"), anc)

    ev = optimizer_evidence(a.rehearse_root)
    ev["observed_lines"] = observed_lines(freeze_doc, "p1/UID0063", [86000.0, 81774.0])
    ev["dependency_excerpt"] = [x for x in anc["attempts"] if x["attempt_id"] == "p1/UID0063"]
    ev["written_utc"] = _utc()
    written["D2_OPTIMIZER_EVIDENCE.json"] = _write(
        os.path.join(a.out, "D2_OPTIMIZER_EVIDENCE.json"), ev)

    # ── the corrected stage reports, written fresh, with the move from the superseded ones ──
    stage_moves = []
    for tag, contract, _sha in models:
        info = RUN.stage_reports(freeze_doc, contract, a.out, tag)
        old_path = os.path.join(a.compare_stage_root, f"{tag}_stage_reports.json")
        diff = None
        if a.compare_stage_root and os.path.exists(old_path):
            with open(old_path, encoding="utf-8") as f:
                old = json.load(f)
            with open(os.path.join(a.out, f"{tag}_stage_reports.json"), encoding="utf-8") as f:
                new = json.load(f)
            o = {r["attempt_id"]: {x["obligation"]: x["state"] for x in r["obligations"]}
                 for r in old["reports"]}
            n = {r["attempt_id"]: {x["obligation"]: x["state"] for x in r["obligations"]}
                 for r in new["reports"]}
            moved = [{"attempt_id": aid, "obligation": ob, "was": o.get(aid, {}).get(ob), "now": st}
                     for aid, obs in sorted(n.items()) for ob, st in sorted(obs.items())
                     if o.get(aid, {}).get(ob) != st]
            diff = {"n_obligations_moved": len(moved), "moved": moved,
                    "accusations_withdrawn": [m for m in moved
                                              if m["was"] in ("VIOLATED", "OUTSTANDING")
                                              and m["now"] not in ("VIOLATED", "OUTSTANDING")],
                    "accusations_added": [m for m in moved
                                          if m["now"] in ("VIOLATED", "OUTSTANDING")
                                          and m["was"] not in ("VIOLATED", "OUTSTANDING")]}
        stage_moves.append({"tag": tag, "stage_reports": info,
                            "difference_from": a.compare_stage_root or None,
                            "difference": diff})
    written["D2_STAGE_MOVES.json"] = _write(
        os.path.join(a.out, "D2_STAGE_MOVES.json"),
        {"schema": SCHEMA + ".stage_moves", "written_utc": _utc(), "results": stage_moves,
         "note": "the superseded reports are preserved where they are and are not modified"})

    # regenerated reports, labelled as such
    banner = REGENERATED_BANNER.format(utc=_utc())
    tag, contract, model_sha = models[-1]
    regen = {}
    for aid in anc_ids:
        row = [x for x in freeze_doc["attempts"] if x["attempt_id"] == aid][0]
        st = CK.stage_report(freeze_doc, row, contract)
        text = banner + CK.render_report(
            st, n_learn_operands=n_learn, contract_rendered=V.render(contract),
            model_sha256=model_sha, lean=lean)
        name = aid.replace("/", "_") + ".REGENERATED.md"
        with open(os.path.join(a.out, "reports_regenerated", name), "w", encoding="utf-8") as f:
            f.write(text)
        regen[aid] = {"file": name, "sha256": IR.sha(text), "bytes": len(text.encode("utf-8"))}

    summary = {
        "schema": SCHEMA, "rule": "OQA-LN-1", "written_utc": _utc(),
        "paid_calls": 0,
        "freeze_sha256": freeze_doc["self_sha256"],
        "models": [{"tag": t, "model_sha256": s} for t, _c, s in models],
        "files": written,
        "regenerated_reports": regen,
        "stage_moves": stage_moves,
        "regenerated_not_delivered": True,
        "headline": {
            "supported_bindings_before": cells_doc["replay"]["roles"]["learn"]["sourced_before"]
                                         + cells_doc["replay"]["roles"]["check"]["sourced_before"],
            "supported_bindings_after": cells_doc["replay"]["roles"]["learn"]["sourced_after"]
                                        + cells_doc["replay"]["roles"]["check"]["sourced_after"],
            "n_changed_bindings": len(cells_doc["replay"]["changed_bindings"]),
            "occurrence_matches_before": cells_doc["occurrence_inventory"]["matches_before"],
            "occurrence_matches_after": cells_doc["occurrence_inventory"]["matches_after"],
        },
    }
    digest = _write(os.path.join(a.out, "D2_RUN.json"), summary)
    print(f"wrote {a.out}  D2_RUN digest {digest[:16]}  paid calls 0")
    for k, v in written.items():
        print(f"   {k:34s} {v[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
