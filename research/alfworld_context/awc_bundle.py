#!/usr/bin/env python3
"""Freeze the complete semantic bundle of this study, and classify every changed result. Rule AWC-1.

TWO COMMANDS, in this order:

  freeze   Digest everything that decides what a check MEANS, before the checking records are replayed:
           the reading implementations and their source digests, the per-function digests of the three
           functions the comparison turns on, the declared assumptions, the stage definitions, the
           vocabulary, the conditions and the frozen manifest. A model JSON digest alone would not do:
           in this study there is no learned model at all, and the whole semantics lives in the code.

  compare  Classify every `(record, condition)` difference with its raw witness, into exactly one of:
             corrected_progress_recognition          current missed a completion the context reading sees
             removal_of_a_false_diagnosis            current claimed a completion the context reading withdraws
             more_precise_or_earlier_existing        both recognise it, the context reading at a better step
             valid_additional_execution_diagnosis    a NEW execution fault, not merely a progress state
             redundant_information                   the saved ordinary analysis already said it
             unsupported_inference_or_unresolved     the context reading asserts what the prefix cannot support
           Model refutation is kept apart from actor error, and a new tracker state is NOT counted as
           new information unless the saved ordinary SkillOpt analysis for that attempt lacks it.

usage:
  awc_bundle.py freeze  --manifest <AWC_MANIFEST.json> --out <dir>
  awc_bundle.py compare --replay <AWC_REPLAY_*.json> [--replay ...] [--case <AWC_CASE_*.json> ...]
                        [--scan <AWC_SCAN_*.json> ...] --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import inspect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = os.path.join(HERE, "frozen", "learned_contracts")
for _p in (FROZEN, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import a4_reading as A4            # noqa: E402
import stage_common as SC          # noqa: E402
import vocab as V                  # noqa: E402

RULE = "AWC-1"
SCHEMA = "awc.bundle.v1"

# The functions the comparison turns on. If any of these moves, the study's results describe other code.
SEMANTIC_FUNCTIONS = {
    "stage_common.Reading.goal_now": SC.Reading.goal_now,
    "stage_common.Reading.apply": SC.Reading.apply,
    "stage_common.Reading.supported": SC.Reading.supported,
    "stage_common.Reading.available": SC.Reading.available,
    "stage_common.Reading.transformed_before": SC.Reading.transformed_before,
    "stage_common.spec_of": SC.spec_of,
    "a4_reading.ReadingA4.goal_now": A4.ReadingA4.goal_now,
    "a4_reading.ReadingA4.apply": A4.ReadingA4.apply,
    "a4_reading.ReadingA4.transformed_before": A4.ReadingA4.transformed_before,
    "a4_reading.parse_steps": A4.parse_steps,
    "a4_reading.classify_command": A4.classify_command,
    "vocab.parse_task": V.parse_task,
    "vocab.type_of": V.type_of,
}

CLS = ("corrected_progress_recognition", "removal_of_a_false_diagnosis",
       "more_precise_or_earlier_existing", "valid_additional_execution_diagnosis",
       "redundant_information", "unsupported_inference_or_unresolved")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def components(manifest_path: str) -> dict:
    snap = json.load(open(os.path.join(HERE, "frozen", "FROZEN_SNAPSHOT.json"), encoding="utf-8"))
    man = json.load(open(manifest_path, encoding="utf-8"))
    return {
        "hypothesis": {
            "note": ("there is NO learned model in this study: no contract is learned, proposed or "
                     "repaired. What is under test is a stage-layer READING of observations, so the "
                     "semantics is entirely in the code digested below"),
            "readings": {
                "current": "stage_common.Reading (clause a only)",
                "a4_rev1": "a4_reading.ReadingA4(rev=1)",
                "context_aware": "a4_reading.ReadingA4(rev=2)",
            },
        },
        "code_sha256": {f["path"]: f["sha256"] for f in snap["files"]},
        "function_sha256": {k: sha_text(inspect.getsource(f)) for k, f in SEMANTIC_FUNCTIONS.items()},
        "assumptions": {
            "shared": SC.ASSUMPTIONS,
            "extended_used_only_by_secondary_claims": SC.EXTENDED_ASSUMPTIONS,
            "inspect_revision": A4.INSPECT_ASSUMPTIONS,
            "revision_assumptions_not_in_a_named_set": [
                "same_receptacle (rev 2 clause c, named in the amendment's text only)",
                "temperature_overrides (rev 2 clause d, named in the amendment's text only)",
            ],
        },
        "stage_definitions": {
            "spec_fields": ["form", "otype", "dest", "lamp", "transform", "count"],
            "forms": ["simple", "two", "heat", "cool", "clean", "light"],
            "transform_of": SC.TRANSFORM_OF,
            "problem_families": sorted({v[0] for v in SC.FAMILY.values()}),
            "finding_categories": sorted(SC.FAMILY),
        },
        "rendering": {
            "note": ("this study renders no analyst report: it compares stage readings, so the "
                     "renderer (reports_a4.py) is NOT part of the bundle and no optimizer input is "
                     "produced"),
        },
        "vocabulary": {"version": V.VOCAB_VERSION, "digest": V.vocab_digest()},
        "configuration": {
            "conditions": ["current", "a4_rev1", "context_aware"],
            "manifest": os.path.abspath(manifest_path),
            "manifest_sha256": man.get("manifest_sha256"),
            "python": ".".join(str(x) for x in sys.version_info[:3]),
            "slurm_job": os.environ.get("SLURM_JOB_ID"),
            "commit": os.environ.get("AWC_COMMIT") or os.environ.get("OQA_COMMIT"),
        },
    }


SEMANTIC_SECTIONS = ("hypothesis", "code_sha256", "function_sha256", "assumptions",
                     "stage_definitions", "rendering", "vocabulary")


def cmd_freeze(manifest_path: str, out_dir: str, label: str = "v1") -> dict:
    comp = components(manifest_path)
    doc = {"schema": SCHEMA, "rule": RULE, "label": label, "frozen_utc": _utc(), **comp}
    doc["bundle_sha256"] = sha({k: v for k, v in doc.items()
                                if k not in ("frozen_utc", "bundle_sha256")})
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"AWC_BUNDLE_{label}.json")
    if os.path.exists(path):
        old = json.load(open(path, encoding="utf-8"))
        if old.get("bundle_sha256") == doc["bundle_sha256"]:
            return old
        moved = [k for k in SEMANTIC_SECTIONS if old.get(k) != doc.get(k)]
        if not moved:
            out = dict(old)
            out["provenance_only_difference"] = {"recorded": old.get("configuration"),
                                                 "now": doc.get("configuration")}
            return out
        raise SystemExit(f"{path} is frozen with a DIFFERENT meaning; moved: {', '.join(moved)}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.chmod(path, 0o444)
    doc["written_to"] = path
    return doc


# ───────────────────────── classification ─────────────────────────

def classify(rec: dict) -> list:
    """Every difference between `current` and `context_aware` on one attempt, with its witness."""
    out = []
    cur = rec["by_rev"]["current"]
    ctx = rec["by_rev"]["context_aware"]
    r = rec["retrospective"]
    hard = int(r["scored_hard"])
    gc, gx = cur.get("first_goal_step"), ctx.get("first_goal_step")
    if gc == gx and bool(cur["final"]["goal_reached"]) == bool(ctx["final"]["goal_reached"]):
        return out
    ordinary = (rec.get("ordinary_analysis") or {}).get("patterns_naming_this_attempt") or []

    witness = {
        "task": rec.get("task_description"), "id": rec.get("id") or rec.get("task_id"),
        "scored_hard": hard, "n_steps": rec.get("n_visible_steps"),
        "current_goal_step": gc, "context_goal_step": gx,
        "current_reached_at_end": bool(cur["final"]["goal_reached"]),
        "context_reached_at_end": bool(ctx["final"]["goal_reached"]),
        "current_goal_objs": cur["final"]["goal_support"],
        "context_goal_objs": ctx["final"]["goal_support"],
        "placements_at_end": ctx["final"]["placed"],
        "placement_events": ctx["final"]["placement_events"],
        "lamp_on": ctx["final"]["lamp_on"], "lamp_uses": ctx["final"]["lamp_uses"],
        "transforms": ctx["final"]["transforms"],
        "inspect_basis": ctx.get("inspect_basis"),
        "assumptions_used_by_the_context_reading": ctx["assumptions"]["inspect_revision"],
        "in_sample_for_the_revision": rec.get("in_sample_for_the_revision"),
        "saved_ordinary_analysis_patterns": ordinary,
    }

    if gc is None and gx is not None:
        label = ("corrected_progress_recognition" if hard == 1
                 else "unsupported_inference_or_unresolved")
        why = ("the current reading never recognises a completion the episode in fact achieved; the "
               "context reading recognises it at step %s on basis %s"
               % (gx, ctx.get("inspect_basis")) if hard == 1 else
               "the context reading claims a completion on an episode that scored 0")
    elif gx is None and gc is not None:
        label = ("removal_of_a_false_diagnosis" if hard == 0
                 else "unsupported_inference_or_unresolved")
        why = ("the current reading claims a completion at step %s on an episode that scored 0; the "
               "context reading withdraws it" % gc if hard == 0 else
               "the context reading withdraws a completion the episode in fact achieved")
    elif gc is not None and gx is not None and gx > gc:
        label = "more_precise_or_earlier_existing"
        why = ("both recognise the completion, the current reading %d step(s) EARLIER than the "
               "evidence supports; the context reading moves it to step %s" % (gx - gc, gx))
    elif gc is not None and gx is not None and gx < gc:
        label = "more_precise_or_earlier_existing"
        why = ("the context reading recognises the completion %d step(s) earlier, at step %s"
               % (gc - gx, gx))
    else:
        label = "unsupported_inference_or_unresolved"
        why = "the two readings differ in a way this classifier does not cover"

    # a progress state is not new INFORMATION if the saved ordinary analysis already named the attempt
    if label in ("corrected_progress_recognition", "more_precise_or_earlier_existing") and ordinary:
        witness["note_on_novelty"] = (
            "the saved ordinary SkillOpt analysis already names this attempt: %s. The changed stage "
            "state is a better progress reading, not necessarily new information for the optimizer."
            % ordinary)
    out.append({"classification": label, "why": why, "witness": witness,
                "kind": "actor_error" if label == "valid_additional_execution_diagnosis"
                        else "progress_reading",
                "model_refutation": False,
                "note": ("no contract is under test here, so nothing in this study can refute a model; "
                         "every change is a change to a stage-layer READING")})
    return out


def cmd_compare(replays: list, cases: list, scans: list, out_dir: str) -> dict:
    changes, totals = [], {"records": 0, "changed": 0}
    for p in replays or []:
        d = json.load(open(p, encoding="utf-8"))
        for rec in d["records"]:
            totals["records"] += 1
            cs = classify(rec)
            for c in cs:
                c["source"] = {"file": os.path.basename(p), "role": rec.get("role"),
                               "type": rec.get("type_label"), "attempt": rec.get("attempt"),
                               "run": rec.get("run"), "step": rec.get("step")}
            changes += cs
            totals["changed"] += 1 if cs else 0
    for p in cases or []:
        d = json.load(open(p, encoding="utf-8"))
        rec = d["record"]
        totals["records"] += 1
        cs = classify(rec)
        for c in cs:
            c["source"] = {"file": os.path.basename(p), "role": "labelled_case",
                           "label": d.get("label")}
        changes += cs
        totals["changed"] += 1 if cs else 0
    by_cls = {}
    for c in changes:
        by_cls[c["classification"]] = by_cls.get(c["classification"], 0) + 1
    scan_agg, scan_changes = {}, []
    for p in scans or []:
        d = json.load(open(p, encoding="utf-8"))
        scan_agg[d["task_type"]] = d["aggregate"]
        # EVERY changed attempt the scan found is replayed in full here and classified with its witness,
        # so the supplementary measurement is not left as a bare count.
        import awc_replay as RP
        for row in d.get("changed") or []:
            ep = RP.load_episode(row["trace"])
            runs = {c: RP.replay_one(ep, c) for c in RP.CONDITIONS}
            rec = {"id": row["id"], "task_id": row["id"], "task_type": d["task_type"],
                   "task_description": ep["task_description"],
                   "n_visible_steps": runs["current"].get("n_steps"),
                   "in_sample_for_the_revision": row["id"] in RP.REVISION_EVIDENCE,
                   "by_rev": dict(runs), "retrospective": RP.retrospective(ep, runs),
                   "ordinary_analysis": RP.ordinary_analysis(row["trace"], row["id"])}
            for c in classify(rec):
                c["source"] = {"file": os.path.basename(p), "role": "supplementary_scan",
                               "type": d["task_type"], "run": row["run"], "step": row["step"]}
                scan_changes.append(c)
    for c in scan_changes:
        by_cls[c["classification"]] = by_cls.get(c["classification"], 0) + 1
    doc = {"schema": SCHEMA, "rule": RULE, "utc": _utc(),
           "totals": totals, "by_classification": {k: by_cls.get(k, 0) for k in CLS},
           "changes": changes, "supplementary_scan_changes": scan_changes,
           "supplementary_scans": scan_agg,
           "soundness": {
               "false_completion_claims_introduced": by_cls.get(
                   "unsupported_inference_or_unresolved", 0),
               "false_completion_claims_removed": by_cls.get("removal_of_a_false_diagnosis", 0),
               "note": ("a claim of completion on an episode that scored 0 is the false-certainty "
                        "failure this study was asked to watch for"),
           },
           "caveat": ("the frozen diagnostic set is 24 attempts; the supplementary scans cover every "
                      "saved training attempt and are where a 2-3% phenomenon is visible at all")}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "AWC_COMPARE.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    doc["written_to"] = path
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--manifest", required=True)
    f.add_argument("--label", default="v1")
    f.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--replay", action="append")
    c.add_argument("--case", action="append")
    c.add_argument("--scan", action="append")
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "freeze":
        d = cmd_freeze(a.manifest, a.out, a.label)
        print(f"rule {RULE}  bundle {d['bundle_sha256'][:16]}  label {d['label']}")
        print(f"  code      {len(d['code_sha256'])} modules")
        print(f"  functions {len(d['function_sha256'])} semantic functions")
        print(f"  vocabulary {d['vocabulary']['version']} {d['vocabulary']['digest'][:16]}")
        print(f"  manifest  {str(d['configuration']['manifest_sha256'])[:16]}")
        if d.get("provenance_only_difference"):
            print("  (kept: every semantic component identical, only the provenance differs)")
        print(f"wrote {d.get('written_to', '(already frozen)')}")
        return 0
    d = cmd_compare(a.replay, a.case, a.scan, a.out)
    print(f"rule {RULE}  {d['totals']['changed']} of {d['totals']['records']} replayed records changed")
    for k, v in d["by_classification"].items():
        if v:
            print(f"    {k:40s} {v}")
    for ch in d["changes"] + d.get("supplementary_scan_changes", []):
        w = ch["witness"]
        print(f"      {str(w['id']):8s} hard={w['scored_hard']} steps={str(w['n_steps']):3s} "
              f"current@{str(w['current_goal_step']):5s} -> context@{str(w['context_goal_step']):5s} "
              f"{ch['classification']:34s} in_sample={w['in_sample_for_the_revision']} "
              f"[{ch['source'].get('role')}]")
    print(f"  false completion claims introduced: "
          f"{d['soundness']['false_completion_claims_introduced']}, removed: "
          f"{d['soundness']['false_completion_claims_removed']}")
    print(f"wrote {d['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
