"""VX-1 freezes (amendment A5). python3 -I -B vx_bundle.py freeze-plan|freeze-code|verify --out RUNROOT [--manifest M]

  plan  (before ANY VX-1 episode, including the dry run): the amendment, the selection / partition / batches, the
        evaluation spec, vocabulary and prompt digests, reference identities.
  code  (after the development dry run, before the main collection): every study source, test and the staging
        manifest used for the main collection and learning.
Each file is written once, read-only; `verify` recomputes both.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import vx_eval as E  # noqa: E402
import vx_prompt as P  # noqa: E402
import vx_vocab as XV  # noqa: E402


def sf(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def plan(out):
    sel = os.path.join(out, "selection", "VX1_SELECTION.json")
    fr = os.path.join(WT, "research/api_campaign/overlay/research/api_campaign_lc/frozen")
    return {"rule": "VX-1", "schema": "vx.plan_bundle.v1",
            "amendment": {"path": "docs/research/VX1_AMENDMENT_A5.md", "sha256": sf(os.path.join(WT, "docs/research/VX1_AMENDMENT_A5.md"))},
            "selection": {"file_sha256": sf(sel), "content_sha256": json.load(open(sel))["sha256"]},
            "spec": E.SPEC, "vocab": {v: XV.vocab_digest(v) for v in ("OLD", "EXT")}, "prompts": P.prompt_diff(),
            "references": {"a4_reading.py": sf(os.path.join(fr, "a4_reading.py")), "frozen vocab.py": sf(os.path.join(fr, "vocab.py")),
                           "frozen state.py": sf(os.path.join(fr, "state.py")), "ord2_bundle": "323747323ec8f8b863ef874684230651a8a235adce81b553e0526095105352a7"}}


def code(out, manifest):
    files = sorted(glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "cluster", "*")))
    return {"rule": "VX-1", "schema": "vx.code_bundle.v1",
            "plan_bundle_sha256": json.load(open(os.path.join(out, "VX1_PLAN_BUNDLE.json")))["bundle_sha256"],
            "code": {os.path.relpath(f, WT): sf(f) for f in files},
            "tests": {t: sf(os.path.join(WT, "tests", t)) for t in ("test_vx1.py", "vx1_fake_env.py")},
            "staging_manifest_sha256": sf(manifest) if manifest else None}


def write_once(p, b):
    if os.path.exists(p):
        raise SystemExit(f"{p} already frozen")
    b["bundle_sha256"] = hashlib.sha256(canon(b).encode()).hexdigest()
    b["frozen_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump(b, open(p, "w"), indent=1)
    os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    print("FROZEN", os.path.basename(p), b["bundle_sha256"], b["frozen_utc"])


def check(p, now):
    b = json.load(open(p))
    keep = {k: v for k, v in b.items() if k not in ("bundle_sha256", "frozen_utc")}
    bad = sorted(k for k in now if now[k] != keep.get(k))
    if bad or hashlib.sha256(canon(keep).encode()).hexdigest() != b["bundle_sha256"]:
        raise SystemExit(f"MISMATCH {os.path.basename(p)} {bad}")
    print("verified", os.path.basename(p), b["bundle_sha256"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("freeze-plan", "freeze-code", "verify"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest")
    a = ap.parse_args()
    pp, cp = os.path.join(a.out, "VX1_PLAN_BUNDLE.json"), os.path.join(a.out, "VX1_CODE_BUNDLE.json")
    if a.cmd == "freeze-plan":
        write_once(pp, plan(a.out))
    elif a.cmd == "freeze-code":
        write_once(cp, code(a.out, a.manifest))
    else:
        check(pp, plan(a.out))
        if os.path.exists(cp):
            c = json.load(open(cp))
            check(cp, code(a.out, a.manifest or None) if a.manifest else {k: v for k, v in code(a.out, None).items()
                                                                          if k != "staging_manifest_sha256"} | {"staging_manifest_sha256": c["staging_manifest_sha256"]})


if __name__ == "__main__":
    main()
