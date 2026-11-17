"""Plan and record the PRIMARY held-out evaluation of the three-arm comparison: one rollout per DISTINCT skill.

Offline when run with `--plan`: it only reads the arms' output directories and reports which skills exist, which
are distinct, and what the held-out evaluation would therefore cost.

WHY THIS IS A SEPARATE STEP. With `evaluation.eval_test: true` the trainer evaluates the held-out split itself,
once per arm, and each of those passes re-scores the STARTING skill. Three arms would roll out S0 on the held-out
split three times and would also re-roll any skill two arms happen to share - the A40 run ended with both arms'
final skills byte-identical to S0, so that is not a hypothetical. The campaign therefore runs with
`eval_test: false` and this script does the held-out evaluation ONCE over the deduplicated set:

    S0  +  each arm's validation-selected skill (best_skill.md)  +  each arm's last skill

which is at most 1 + 6 = 7 rollouts of the 60-question held-out split, and fewer whenever skills coincide.

The held-out split is read by nothing else in the campaign. `--plan` does not read any score.

usage:
  python3 -I research/officeqa/oqa_heldout.py --plan --arm A=<out_root> --arm B=<out_root> --arm C=<out_root> \
      --skill-init skillopt/envs/officeqa/skills/initial.md --heldout-size 60 --out <dir>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys


def skill_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def last_skill_path(out_root: str) -> str | None:
    """The last skill the trainer wrote, which is not in general the validation-selected one."""
    d = os.path.join(out_root, "skills")
    if not os.path.isdir(d):
        return None
    best, best_n = None, -1
    for name in os.listdir(d):
        m = re.fullmatch(r"skill_v(\d+)\.md", name)
        if m and int(m.group(1)) > best_n:
            best, best_n = os.path.join(d, name), int(m.group(1))
    return best


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None



# The marker pairs the trainer injects into a skill document. A skill that differs from S0 ONLY by these is
# the starting skill with an annotation, not a new skill - see `_substantive`.
_MARKER_PAIRS = (("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->"),
                 ("<!-- META_SKILL_START -->", "<!-- META_SKILL_END -->"),
                 ("<!-- APPENDIX_START -->", "<!-- APPENDIX_END -->"))


def _substantive(text: str) -> str:
    """The skill with every injected marker block removed, so two skills can be compared on their ADVICE.

    WHY THIS MATTERS. When a candidate is rejected the trainer still writes a skill snapshot for the step, and
    with `use_slow_update` on that snapshot is the retained skill PLUS an empty
    `<!-- SLOW_UPDATE_START --><!-- SLOW_UPDATE_END -->` placeholder. Its digest therefore differs from S0's
    while its advice is identical - which makes a held-out evaluation of the "last skill" an expensive way to
    re-measure S0. Epoch 1 of the three-arm campaign is exactly that case: all three arms rejected their
    candidates, and all three `skill_v0001.md` files are byte-identical to each other and differ from S0 by
    three lines of placeholder.
    """
    out = text
    for start, end in _MARKER_PAIRS:
        while start in out and end in out:
            a = out.find(start)
            b = out.find(end, a)
            if b < 0:
                break
            out = out[:a] + out[b + len(end):]
    return "\n".join(l.rstrip() for l in out.splitlines() if l.strip())

def collect(arms: dict, skill_init: str) -> dict:
    """Every skill the held-out evaluation should score, deduplicated by digest."""
    entries: list = []

    s0 = _read(skill_init)
    if s0 is None:
        raise FileNotFoundError(f"the starting skill is not readable at {skill_init}")
    entries.append({"role": "S0", "arm": None, "path": os.path.abspath(skill_init),
                    "digest": skill_digest(s0), "chars": len(s0)})

    for arm, root in sorted(arms.items()):
        best = os.path.join(root, "best_skill.md")
        bt = _read(best)
        if bt is None:
            entries.append({"role": "validation_selected", "arm": arm, "path": best, "digest": None,
                            "problem": "best_skill.md is absent: the arm has not finished"})
        else:
            entries.append({"role": "validation_selected", "arm": arm, "path": os.path.abspath(best),
                            "digest": skill_digest(bt), "chars": len(bt)})
        lastp = last_skill_path(root)
        lt = _read(lastp) if lastp else None
        if lt is None:
            entries.append({"role": "last", "arm": arm, "path": lastp, "digest": None,
                            "problem": "no skill snapshot was written"})
        else:
            entries.append({"role": "last", "arm": arm, "path": os.path.abspath(lastp),
                            "digest": skill_digest(lt), "chars": len(lt),
                            "same_as_validation_selected": skill_digest(lt) == skill_digest(bt or ""),
                            # True when this snapshot is the starting skill with only injected marker blocks
                            # added. Scoring it on the held-out split would re-measure S0 at full price.
                            "same_advice_as_s0": _substantive(lt) == _substantive(s0)})

    by_digest: dict = {}
    for e in entries:
        if not e.get("digest"):
            continue
        by_digest.setdefault(e["digest"], []).append(
            f"{e['role']}" + (f"[{e['arm']}]" if e["arm"] else ""))
    notes = []
    hollow = sorted(e["arm"] for e in entries
                    if e.get("role") == "last" and e.get("same_advice_as_s0"))
    if hollow:
        notes.append(
            f"the LAST skill of arm(s) {', '.join(hollow)} differs from the starting skill only by injected "
            f"marker blocks, so its advice is S0's. Scoring it on the held-out split would re-measure S0 at "
            f"full price; the number would not be a result about that arm.")
    return {"entries": entries, "distinct": by_digest,
            "n_distinct": len(by_digest),
            "n_requested": sum(1 for e in entries if e.get("digest")),
            "n_distinct_by_advice": len({_substantive(_read(e["path"]) or "") for e in entries
                                         if e.get("digest")}),
            "last_skills_with_s0_advice": hollow,
            "notes": notes,
            "problems": [e["problem"] for e in entries if e.get("problem")]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", default=[], metavar="LETTER=OUT_ROOT",
                    help="repeatable, e.g. --arm A=/path/to/arm_A")
    ap.add_argument("--skill-init", default="skillopt/envs/officeqa/skills/initial.md")
    ap.add_argument("--heldout-size", type=int, default=60)
    ap.add_argument("--requests-per-question", type=float, default=11.7,
                    help="measured actor requests per question at a 24-turn cap (the A40 pilot's mean)")
    ap.add_argument("--usd-per-question", type=float, default=0.003,
                    help="measured actor charge per question at a 24-turn cap")
    ap.add_argument("--rpm", type=int, default=50, help="the account-wide request limit")
    ap.add_argument("--plan", action="store_true", help="report the plan and its cost; roll nothing out")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    arms: dict = {}
    for spec in args.arm:
        if "=" not in spec:
            print(f"--arm expects LETTER=OUT_ROOT, got {spec!r}", file=sys.stderr)
            return 2
        letter, root = spec.split("=", 1)
        arms[letter.strip()] = os.path.abspath(root.strip())

    if not args.plan:
        print("only --plan is implemented offline. The rollout itself belongs to an authorized CPU job: it "
              "spends actor requests and must run under the campaign's ledger, not from this script.",
              file=sys.stderr)
        return 2

    worst_case = 1 + 2 * max(len(arms), 1)
    found = collect(arms, args.skill_init) if arms else {"entries": [], "distinct": {}, "n_distinct": 0,
                                                         "n_requested": 0, "problems":
                                                         ["no --arm was given: reporting the worst case only"]}
    n = found["n_distinct"] or worst_case
    questions = n * args.heldout_size
    requests = questions * args.requests_per_question
    plan = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "heldout_size": args.heldout_size,
        "skills_requested": found["n_requested"], "skills_distinct": found["n_distinct"],
        "worst_case_distinct_skills": worst_case,
        "worst_case_note": "S0 plus a validation-selected and a last skill for each arm. Fewer whenever two "
                           "arms select the same skill, or an arm's last skill is its selected one, which is "
                           "what happened in the A40 two-arm run (all four candidates were rejected).",
        "entries": found["entries"], "distinct_skills": found["distinct"], "problems": found["problems"],
        "skills_distinct_by_advice": found["n_distinct_by_advice"],
        "last_skills_whose_advice_is_s0": found["last_skills_with_s0_advice"],
        "notes": found["notes"],
        "cost_at_n_distinct": {
            "n_skills": n, "question_rollouts": questions,
            "actor_requests": round(requests),
            "actor_usd": round(questions * args.usd_per_question, 3),
            "minutes_at_rate_limit": round(requests / args.rpm, 1),
            "basis": f"{args.requests_per_question} requests and ${args.usd_per_question} per question at a "
                     f"24-turn cap, {args.rpm} requests per minute account-wide",
        },
        "cost_worst_case": {
            "n_skills": worst_case, "question_rollouts": worst_case * args.heldout_size,
            "actor_requests": round(worst_case * args.heldout_size * args.requests_per_question),
            "actor_usd": round(worst_case * args.heldout_size * args.usd_per_question, 3),
            "minutes_at_rate_limit": round(worst_case * args.heldout_size * args.requests_per_question
                                           / args.rpm, 1),
        },
        "rules": ["the held-out split is scored ONCE per distinct skill and never during training",
                  "no skill, contract or configuration is chosen using a held-out score",
                  "the three arms' training jobs run with evaluation.eval_test false"],
    }
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "HELDOUT_PLAN.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1)
    print(json.dumps({k: v for k, v in plan.items() if k not in ("entries", "distinct_skills")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
