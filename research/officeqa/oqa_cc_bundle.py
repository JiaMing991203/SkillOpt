#!/usr/bin/env python3
"""The COMPLETE semantic bundle of a connected-contract hypothesis. Amendment B-2, rule OQA-CC-2.

WHY THE MODEL DIGEST IS NOT ENOUGH. `CC_MODEL_v1.json` fixes the hypothesis as a sentence. It does not
fix what the sentence MEANS. B-1 recorded that deviation honestly and it is the reason this file exists:
`nearer_scale_between` had no evaluator when the model was frozen, so one was written afterwards from the
proposer's stated reading - and the frozen model's digest did not change, while its meaning did.

So a check is only reproducible if all of this is frozen together, BEFORE the later records are read:

    hypothesis            the model JSON and its digest, parent and freeze epoch
    vocabulary            every predicate, its arity, the entities it binds and the one provenance
                          label it may carry; the truth values; the stage obligations
    extractor + evaluator the SOURCE of the code that decides a clause, by digest - including the
                          reused `oqa_lc_cells` / `oqa_lc_chain` readers, because a change there
                          changes what a cell IS
    stage definitions     the obligation names, the states the stage link can return, and the source of
                          the function that returns them
    rendering             the source of the renderer, because a report is what a reader sees
    learner prompt        the prompt the proposer was given, by digest
    learning manifest     which records the hypothesis was induced from, by item, epoch and trace digest
    configuration         the assumptions in force, the interpreter, and the deployed commit

`verify` recomputes every component and names the ones that moved. A bundle that no longer verifies does
not invalidate the recorded result; it says precisely which part of the meaning changed since.

usage:
  python3 -I research/officeqa/oqa_cc_bundle.py freeze --model <CC_MODEL_v1.json> \
      --records <CC_RECORDS_v2.json> --prompt <CC_PROMPT.txt> --out <dir> [--label v2]
  python3 -I research/officeqa/oqa_cc_bundle.py verify --bundle <dir>/CC_BUNDLE_v2.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import inspect
import json
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oqa_cc_check as CHK  # noqa: E402
import oqa_cc_extract as X  # noqa: E402
import oqa_cc_ir as IR  # noqa: E402

RULE = "OQA-CC-2"
HERE = os.path.dirname(os.path.abspath(__file__))

# Every module whose source decides what a clause MEANS. `oqa_lc_cells` and `oqa_lc_chain` are in the
# list although they are frozen: being frozen is a claim to check, not a reason to omit them.
SEMANTIC_MODULES = ("oqa_cc_ir.py", "oqa_cc_check.py", "oqa_cc_extract.py", "oqa_cc_run.py",
                    "oqa_cc_lean.py", "oqa_lc_cells.py", "oqa_lc_chain.py", "oqa_obligations.py")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(s) -> str:
    if isinstance(s, (dict, list)):
        s = json.dumps(s, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def sha_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def components(model_path: str, records_path: str, prompt_path: str | None) -> dict:
    m = IR.load_frozen(model_path)
    contracts = IR.active(m)
    rec = json.load(open(records_path, encoding="utf-8"))
    vocab = {
        "predicates": {p: {"args": list(v["args"]), "provenance": v["provenance"]}
                       for p, v in sorted(IR.PREDICATES.items())},
        "not_learnable": sorted(IR.NOT_LEARNABLE),
        "entities": list(IR.ENTITIES),
        "truths": list(IR.TRUTHS),
        "provenance_labels": list(IR.PROVENANCE),
        "stage_obligations": list(IR.STAGE_OBLIGATIONS),
        "evaluable_predicates": sorted(CHK.EVALUABLE),
        "scale_exponents": dict(X.SCALE_EXP),
        "nearer_scale_states": [X.PRESENT, X.ABSENT, X.UNKNOWN],
        "operand_verdicts": [X.MONETARY, X.AMBIGUOUS, X.REJECTED],
    }
    code = {name: sha_file(os.path.join(HERE, name)) for name in SEMANTIC_MODULES
            if os.path.isfile(os.path.join(HERE, name))}
    functions = {
        # The three functions that ARE the semantics, by source digest, so a change inside a module
        # that leaves the rest alone is still visible.
        "evaluate_binding": sha(inspect.getsource(CHK.evaluate_binding)),
        "stage_link": sha(inspect.getsource(CHK.stage_link)),
        "check_attempt": sha(inspect.getsource(CHK.check_attempt)),
        "render": sha(inspect.getsource(IR.render)),
        "nearer_scale": sha(inspect.getsource(X.nearer_scale)),
        "classify_number": sha(inspect.getsource(X.classify_number)),
        "data_cells": sha(inspect.getsource(X.data_cells)),
        "conversion": sha(inspect.getsource(X.conversion)),
        "match_operand": sha(inspect.getsource(X.match_operand)),
    }
    stage = {
        "obligation": sorted({c.get("witnesses_obligation") for c in contracts
                              if c.get("witnesses_obligation")}),
        "states": ["PREDICTED", "DISCHARGED", "VIOLATED", "OUTSTANDING", "REOPENED", "UNKNOWN"],
        "outcomes": [CHK.ACTOR_ERROR, CHK.REFUTED, CHK.EXTRACTION_ERROR, CHK.UNKNOWN_OUT,
                     "discharged"],
        "withdrawal_rule": ("a CONFLICT withdraws the binding and REOPENS the obligation; it never "
                            "produces an actor error"),
    }
    manifest = {
        "records_sha256": rec.get("records_sha256"),
        "rule": rec.get("rule"),
        "learning": [{"item": r["item"], "epoch": r["epoch"], "trace_sha256": r.get("trace_sha256"),
                      "arm": r.get("arm")} for r in rec.get("learning") or []],
        "checking": [{"item": r["item"], "epoch": r["epoch"], "trace_sha256": r.get("trace_sha256"),
                      "arm": r.get("arm")} for r in rec.get("checking") or []],
        "all_records_are_reused_development_material": True,
        "held_out_material_used": False,
    }
    out = {
        "hypothesis": {
            "model_path": os.path.abspath(model_path),
            "model_sha256": m.get("sha256"),
            "parent_sha256": m.get("parent_sha256"),
            "frozen_at_epoch": m.get("frozen_at_epoch"),
            "version": m.get("version"),
            "evidence_scope": m.get("evidence_scope"),
            "contracts": [{"id": c["id"], "version": c["version"], "status": c["status"],
                           "rendered": IR.render(c),
                           "unevaluable_predicates": CHK.unevaluable(c)} for c in contracts],
        },
        "vocabulary": vocab,
        "code_sha256": code,
        "function_sha256": functions,
        "stage_definitions": stage,
        "learner_prompt_sha256": sha_file(prompt_path) if prompt_path and os.path.isfile(prompt_path)
        else "unavailable",
        "learner_prompt_path": os.path.abspath(prompt_path) if prompt_path else None,
        "learning_manifest": manifest,
        "configuration": {
            "assumptions": ["flat_header_authoritative"],
            "python": platform.python_version(),
            "commit": os.environ.get("OQA_COMMIT", "unavailable"),
            "slurm_job": os.environ.get("SLURM_JOB_ID", ""),
        },
    }
    return out


def cmd_freeze(model: str, records: str, prompt: str | None, out_dir: str, label: str) -> dict:
    comp = components(model, records, prompt)
    doc = {"rule": RULE, "schema": "oqa.cc.bundle.v1", "label": label, "frozen_utc": _utc(),
           **comp}
    doc["bundle_sha256"] = sha({k: v for k, v in doc.items()
                                if k not in ("frozen_utc", "bundle_sha256")})
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"CC_BUNDLE_{label}.json")
    if os.path.exists(path):
        old = json.load(open(path, encoding="utf-8"))
        if old.get("bundle_sha256") != doc["bundle_sha256"]:
            raise RuntimeError(f"{path} is already frozen with a DIFFERENT meaning "
                               f"({old.get('bundle_sha256', '')[:16]} vs "
                               f"{doc['bundle_sha256'][:16]}); refusing to overwrite it")
        return old
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.chmod(path, 0o444)
    doc["written_to"] = path
    return doc


def cmd_verify(bundle_path: str) -> dict:
    doc = json.load(open(bundle_path, encoding="utf-8"))
    now = components(doc["hypothesis"]["model_path"],
                     # the manifest records the records' digest, not their path; re-derive from the
                     # bundle itself so verification needs nothing but the bundle and the tree
                     doc.get("records_path") or _records_path_from(doc),
                     doc.get("learner_prompt_path"))
    moved = []
    for section in ("vocabulary", "code_sha256", "function_sha256", "stage_definitions",
                    "learning_manifest", "hypothesis"):
        a, b = doc.get(section), now.get(section)
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    moved.append(f"{section}.{k}")
        elif a != b:
            moved.append(section)
    return {"rule": RULE, "utc": _utc(), "bundle": os.path.abspath(bundle_path),
            "bundle_sha256": doc.get("bundle_sha256"),
            "verifies": not moved, "changed_components": moved}


def _records_path_from(doc: dict) -> str:
    """The records file beside the model, which is where `oqa_cc_learn` writes it."""
    d = os.path.dirname(doc["hypothesis"]["model_path"])
    for name in ("CC_RECORDS_v2.json", "CC_RECORDS.json"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    raise RuntimeError(f"no records file beside {d}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--model", required=True)
    f.add_argument("--records", required=True)
    f.add_argument("--prompt")
    f.add_argument("--out", required=True)
    f.add_argument("--label", default="v2")
    v = sub.add_parser("verify")
    v.add_argument("--bundle", required=True)
    args = ap.parse_args()
    if args.cmd == "freeze":
        doc = cmd_freeze(args.model, args.records, args.prompt, args.out, args.label)
        print(f"rule {RULE}  bundle {doc['bundle_sha256'][:16]}  label {doc['label']}")
        print(f"  hypothesis  {str(doc['hypothesis']['model_sha256'])[:16]} "
              f"(frozen_at_epoch {doc['hypothesis']['frozen_at_epoch']}, "
              f"parent {str(doc['hypothesis']['parent_sha256'])[:16]})")
        for c in doc["hypothesis"]["contracts"]:
            print(f"    {c['id']}@v{c['version']} [{c['status']}] unevaluable="
                  f"{c['unevaluable_predicates'] or 'none'}")
        print(f"  vocabulary  {sha(doc['vocabulary'])[:16]}  "
              f"{len(doc['vocabulary']['predicates'])} predicates, "
              f"{len(doc['vocabulary']['evaluable_predicates'])} evaluable")
        print(f"  code        {len(doc['code_sha256'])} modules, "
              f"{len(doc['function_sha256'])} semantic functions")
        print(f"  prompt      {str(doc['learner_prompt_sha256'])[:16]}")
        print(f"  manifest    learning {len(doc['learning_manifest']['learning'])}, "
              f"checking {len(doc['learning_manifest']['checking'])}, "
              f"records {str(doc['learning_manifest']['records_sha256'])[:16]}")
        print(f"wrote {doc.get('written_to', 'already frozen, unchanged')}")
    else:
        doc = cmd_verify(args.bundle)
        print(f"rule {RULE}  verifies={doc['verifies']}")
        for c in doc["changed_components"]:
            print(f"  CHANGED {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
