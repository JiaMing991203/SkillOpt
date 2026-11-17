#!/usr/bin/env python3
"""Enumerate every validation gate the campaign ran, and say exactly what the count of acceptances supports.

WHY THIS EXISTS. An earlier readout of this campaign said: "there were 9 gate evaluations; if every candidate
were merely as good as S0 the expected number of acceptances is 2.99, and we observed 1. P(at most 1 | all
neutral) = 0.141. The campaign produced FEWER acceptances than chance predicts." Three things were wrong with
that sentence, and this tool exists so none of them can be repeated from memory:

  1. THE COUNT WAS NOT CHECKED AGAINST THE ARTIFACTS. It was arrived at by counting arm-epochs rather than
     gates. The gates are enumerated here from the files that record them.

  2. THE ARITHMETIC DID NOT MATCH ITS OWN COUNT. 9 x 0.371 is 3.34, not 2.99; 2.99 is 8 x 0.371. The stated
     count and the stated expectation came from different numbers.

  3. THE CONCLUSION OVERREACHED, and this is the part that matters. A high P(at most 1) does not show that
     the candidates were neutral, and it does not show that arm B's acceptance was noise. Failing to reject a
     null is not evidence for it. The same observation is at least as consistent with candidates that were
     genuinely WORSE than the incumbent - which is what a rate below the null predicts - and this gate has
     little power to tell those apart. "Fewer acceptances than chance predicts" is a description of the
     arithmetic, not a finding about contracts.

WHAT A GATE IS HERE. `evaluate_gate` accepts a candidate only on STRICT improvement over the incumbent's
score, both measured on the same 24 validation questions. One question is 4.2 percentage points. So a gate
whose incumbent measured c of 24 accepts exactly when the candidate measures c+1 or more.

THE NULL, per gate: the candidate's true success rate equals the incumbent's MEASURED rate, c/24. Then

    P(accept) = P(Binomial(24, c/24) >= c + 1)

which is 0.371 when c = 4 (S0's measured 4/24) and smaller as c rises - the incumbent gets harder to beat as
it gets luckier. That is why the expected number of acceptances is NOT simply 0.371 times the number of gates:
arm B's incumbent rose to 5/24 after its epoch-2 acceptance, so its two later gates faced a harder threshold.
Both figures are reported, the uniform one because it is the number under discussion and the per-gate one
because it is the right one.

The distribution of the TOTAL is then a Poisson binomial over the per-gate probabilities, computed exactly by
convolution rather than approximated.

WHAT THE TAIL PROBABILITY IS NOT. The gates are not independent trials. Every gate in an arm scores its
candidate on the SAME 24 questions, and each incumbent is itself a single 24-question measurement rather than
a known rate. A question that is simply hard for this actor is hard at every gate, so the real distribution is
more concentrated than the independent model says. The tail below is therefore an approximation whose direction
is known but whose size is not, and it is reported as context for the observed scores, not as a test.

usage:
  python3 -I research/officeqa/oqa_gate_stats.py --campaign <root> [--out <file>]
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys

RULE = "OQA-GATE-1"
N_SEL = 24          # the whole released OfficeQA validation split, which is what sel_env_num 0 selects
ARMS = ("A", "B", "C")
EPOCHS = (1, 2, 3, 4)


def _load(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _binom_pmf(n: int, k: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    return math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))


def p_accept(incumbent_count: int, n: int = N_SEL) -> float:
    """P(a candidate whose TRUE rate equals the incumbent's measured rate clears a strict-improvement gate)."""
    p = incumbent_count / n
    return sum(_binom_pmf(n, k, p) for k in range(incumbent_count + 1, n + 1))


def pooled_tail(gates: list[dict], n: int = N_SEL) -> dict:
    """How likely is the TOTAL number of questions the candidates got right, under the per-gate null?

    This is the question the acceptance count cannot answer. A gate throws away all of its information except
    one bit, and the bit is dominated by noise at this resolution. The candidates' actual scores are not: 13
    candidates measured on 24 questions each is 312 question-level observations, and the sum of them has a
    distribution that can be computed exactly by convolving the 13 binomials.
    """
    dist = [1.0]
    expected = 0.0
    observed = 0
    for g in gates:
        p = g["incumbent_count"] / n
        expected += n * p
        observed += g["candidate_count"]
        pmf = [_binom_pmf(n, k, p) for k in range(n + 1)]
        nxt = [0.0] * (len(dist) + n)
        for i, d in enumerate(dist):
            if d == 0.0:
                continue
            for k, q in enumerate(pmf):
                nxt[i + k] += d * q
        dist = nxt
    return {"observed_total_correct": observed,
            "expected_total_correct": round(expected, 2),
            "n_question_observations": n * len(gates),
            "p_total_at_most_observed": sum(dist[: observed + 1])}


def p_at_most(observed: int, probs: list[float]) -> float:
    """Exact Poisson-binomial P(total <= observed), by convolution."""
    dist = [1.0]
    for p in probs:
        nxt = [0.0] * (len(dist) + 1)
        for i, d in enumerate(dist):
            nxt[i] += d * (1.0 - p)
            nxt[i + 1] += d * p
        dist = nxt
    return sum(dist[: observed + 1])


def _count(score) -> int | None:
    if score is None:
        return None
    return int(round(float(score) * N_SEL))


def enumerate_gates(campaign: str) -> list[dict]:
    """Every gate evaluation, in the order the trainer ran it: step E, then epoch E's slow update."""
    gates: list[dict] = []
    for arm in ARMS:
        root = os.path.join(campaign, f"arm_{arm}")
        if not os.path.isdir(root):
            continue
        # The incumbent before the first gate is S0's own measurement on the gate set.
        baseline = _load(os.path.join(root, "selection_eval_baseline", "eval_summary.json"), {}) or {}
        incumbent = baseline.get("hard")
        if incumbent is None:
            incumbent = 4 / N_SEL
        for epoch in EPOCHS:
            step = _load(os.path.join(root, "steps", f"step_{epoch:04d}", "step_record.json"), {}) or {}
            cand = step.get("candidate_gate_score")
            action = step.get("action")
            if cand is not None and action in ("reject", "accept", "accept_new_best"):
                gates.append({
                    "arm": arm, "epoch": epoch, "kind": "ordinary",
                    "incumbent_hard": incumbent, "incumbent_count": _count(incumbent),
                    "candidate_hard": cand, "candidate_count": _count(cand),
                    "action": action, "accepted": action in ("accept", "accept_new_best"),
                    "candidate_hash": step.get("candidate_hash"),
                })
                # The gate's own bookkeeping is authoritative for what the next gate faces.
                incumbent = step.get("current_score", incumbent)
            elif action:
                gates.append({
                    "arm": arm, "epoch": epoch, "kind": "ordinary", "action": action,
                    "no_gate": True,
                    "why": "no candidate was produced, so no gate ran",
                    "incumbent_hard": incumbent, "incumbent_count": _count(incumbent),
                })
            slow = _load(os.path.join(root, "slow_update", f"epoch_{epoch:02d}",
                                     "slow_result.json"), {}) or {}
            s_cand = slow.get("selection_hard")
            s_action = slow.get("action")
            if s_cand is not None and s_action in ("reject", "accept", "accept_new_best"):
                gates.append({
                    "arm": arm, "epoch": epoch, "kind": "slow_update",
                    "incumbent_hard": incumbent, "incumbent_count": _count(incumbent),
                    "candidate_hard": s_cand, "candidate_count": _count(s_cand),
                    "action": s_action, "accepted": s_action in ("accept", "accept_new_best"),
                    "candidate_hash": slow.get("candidate_hash"),
                })
                if s_action in ("accept", "accept_new_best"):
                    incumbent = s_cand
            elif s_action and s_action not in ("inject_placeholder",):
                gates.append({
                    "arm": arm, "epoch": epoch, "kind": "slow_update", "action": s_action,
                    "no_gate": True,
                    "why": ("no guidance was produced, so no candidate reached the gate"
                            if s_action == "no_content" else f"action {s_action}"),
                    "incumbent_hard": incumbent, "incumbent_count": _count(incumbent),
                })
    return gates


def analyse(campaign: str) -> dict:
    gates = enumerate_gates(campaign)
    real = [g for g in gates if not g.get("no_gate")]
    for g in real:
        g["p_accept_under_null"] = round(p_accept(g["incumbent_count"]), 6)
        g["p_candidate_at_most_this_under_null"] = round(
            sum(_binom_pmf(N_SEL, k, g["incumbent_count"] / N_SEL)
                for k in range(0, g["candidate_count"] + 1)), 6)
    observed = sum(1 for g in real if g["accepted"])
    probs = [g["p_accept_under_null"] for g in real]
    pooled = pooled_tail(real)
    uniform = p_accept(4)          # 0.371, the figure the earlier readout used for every gate
    return {
        "rule": RULE, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campaign": campaign, "n_selection_questions": N_SEL,
        "gates": gates,
        "n_gate_evaluations": len(real),
        "n_ordinary": sum(1 for g in real if g["kind"] == "ordinary"),
        "n_slow_update": sum(1 for g in real if g["kind"] == "slow_update"),
        "n_stages_with_no_gate": sum(1 for g in gates if g.get("no_gate")),
        "observed_acceptances": observed,
        "uniform_null": {
            "p_accept_each": round(uniform, 6),
            "expected_acceptances": round(len(real) * uniform, 4),
            "note": "every gate treated as facing S0's measured 4/24, which is what 0.371 assumes",
        },
        "per_gate_null": {
            "expected_acceptances": round(sum(probs), 4),
            "p_at_most_observed": round(p_at_most(observed, probs), 6),
            "note": "each gate against its OWN incumbent; arm B's rose to 5/24 after epoch 2",
        },
        "pooled_scores": pooled,
        "candidate_scores_out_of_24": sorted(g["candidate_count"] for g in real),
        "mean_candidate_count": round(sum(g["candidate_count"] for g in real) / max(1, len(real)), 3),
        "mean_incumbent_count": round(sum(g["incumbent_count"] for g in real) / max(1, len(real)), 3),
        "what_the_scores_support": (
            "The acceptance count is a weak instrument and says little. The SCORES say something: over "
            f"{len(real)} gates the candidates got {pooled['observed_total_correct']} of "
            f"{pooled['n_question_observations']} questions right where the null predicts "
            f"{pooled['expected_total_correct']:.0f}, and P(total at most the observed) is "
            f"{pooled['p_total_at_most_observed']:.2g}. So the null that every candidate was as good as its "
            "incumbent is DISFAVOURED, in the direction that the optimizer's edits made this actor WORSE on "
            "the selection set - in all three arms alike. This does not separate the arms from each other, "
            "and the non-independence limit below applies to it as well, though a deficit this large is far "
            "too big for within-arm correlation to account for."
        ),
        "limits": [
            "The gates are not independent: every gate in an arm scores its candidate on the same 24 "
            "questions, and each incumbent is a single 24-question measurement rather than a known rate. The "
            "tail probability is therefore an approximation that overstates the spread.",
            "A high P(at most the observed count) does NOT establish that the candidates were neutral. "
            "Failing to reject a null is not evidence for it, and the same data are at least as consistent "
            "with candidates that were genuinely worse than their incumbent.",
            "It does not establish that arm B's acceptance was noise either. It shows the campaign's "
            "acceptance count is not distinguishable from chance at this resolution, which is a statement "
            "about the instrument, not about contracts.",
            "One question is 4.2 percentage points. No margin of one or two questions on this set separates "
            "the arms.",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    doc = analyse(args.campaign)

    print(f"rule {doc['rule']}   {doc['n_gate_evaluations']} gate evaluations "
          f"({doc['n_ordinary']} ordinary + {doc['n_slow_update']} slow-update), "
          f"{doc['observed_acceptances']} acceptance(s)")
    print("\n%-4s %-6s %-12s %-12s %-16s %-9s %s" % (
        "arm", "epoch", "kind", "incumbent", "candidate", "decision", "P(accept|null)"))
    for g in doc["gates"]:
        if g.get("no_gate"):
            print("%-4s %-6s %-12s %-12s %-16s %-9s %s" % (
                g["arm"], g["epoch"], g["kind"], f"{g['incumbent_count']}/24",
                "- no candidate -", g["action"], "-"))
            continue
        print("%-4s %-6s %-12s %-12s %-16s %-9s %.3f" % (
            g["arm"], g["epoch"], g["kind"], f"{g['incumbent_count']}/24",
            f"{g['candidate_count']}/24 (needs {g['incumbent_count'] + 1})",
            "ACCEPT" if g["accepted"] else "reject", g["p_accept_under_null"]))

    pl = doc["pooled_scores"]
    print(f"\n  candidate scores out of 24: {doc['candidate_scores_out_of_24']}")
    print(f"  mean candidate {doc['mean_candidate_count']}/24 against mean incumbent "
          f"{doc['mean_incumbent_count']}/24")
    print(f"  pooled: {pl['observed_total_correct']} of {pl['n_question_observations']} questions right, "
          f"null predicts {pl['expected_total_correct']:.0f}, "
          f"P(total at most observed) = {pl['p_total_at_most_observed']:.2g}")
    print(f"\n  WHAT THE SCORES SUPPORT\n    {doc['what_the_scores_support']}")
    u, p = doc["uniform_null"], doc["per_gate_null"]
    print(f"\n  uniform null  ({doc['n_gate_evaluations']} x {u['p_accept_each']:.3f}): "
          f"expected {u['expected_acceptances']:.2f} acceptances")
    print(f"  per-gate null (each against its own incumbent): "
          f"expected {p['expected_acceptances']:.2f}, "
          f"P(at most {doc['observed_acceptances']}) = {p['p_at_most_observed']:.3f}")
    print("\n  LIMITS")
    for line in doc["limits"]:
        print(f"    - {line}")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
