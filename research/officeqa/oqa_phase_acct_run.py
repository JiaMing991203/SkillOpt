"""Driver for the phase accounting reconstruction. Writes ONE new adjustment document; changes nothing.

The job catalogue below is the claim this reconstruction makes about which jobs could have spent money.
It is built from `docs/research/jobs.tsv` and each job's own `job.log`, and a job listed with an empty
`paid_roles` is a claim that it spent nothing - checked against the records, never assumed.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oqa_phase_acct as A  # noqa: E402

# job -> what it is and which roles it could have paid for.
# `bound` is the conservative UPPER bound used when a job is uncovered by every source.
CATALOGUE = {
    "66552338": {"stage": "prep", "paid_roles": [], "why": "bounded preflight; it reported the backend "
                 "fallback model and sent no request to a provider"},
    "66552420": {"stage": "prep", "paid_roles": [], "why": "bounded preflight, no paid request"},
    "66552421": {"stage": "s0", "paid_roles": ["target"]},
    "66552422": {"stage": "arm_e1", "paid_roles": ["target", "optimizer"]},
    "66552423": {"stage": "arm_e2", "paid_roles": ["target", "optimizer"]},
    "66552424": {"stage": "arm_e3", "paid_roles": ["target", "optimizer"]},
    "66552425": {"stage": "arm_e4", "paid_roles": ["target", "optimizer"]},
    "66552508": {"stage": "t2s", "paid_roles": [], "why": "the information-boundary guard stopped the "
                 "run before any optimizer request; no actor key was loaded"},
    "66552741": {"stage": "t2s", "paid_roles": [], "why": "the optimizer route was never configured, so "
                 "all 48 calls raised at the Azure dispatcher; zero reached a provider"},
    "66552763": {"stage": "t2s", "paid_roles": ["optimizer"]},
    "66552979": {"stage": "t2s", "paid_roles": ["optimizer"]},
    "66553058": {"stage": "t2s", "paid_roles": ["optimizer"]},
    "66553156": {"stage": "t2s", "paid_roles": ["optimizer"]},
    "66553248": {"stage": "t2s", "paid_roles": ["optimizer"]},
    "66553250": {"stage": "heldout_S0", "paid_roles": ["target"]},
    "66553251": {"stage": "heldout_original_skillopt", "paid_roles": ["target"]},
    "66553363": {"stage": "heldout_trace2skill", "paid_roles": ["target"]},
    "66552633": {"stage": "ct_bundle7", "paid_roles": [], "why": "contract arm, all credentials dropped"},
    "66552721": {"stage": "ct_replay", "paid_roles": [], "why": "contract arm, all credentials dropped"},
    "66552984": {"stage": "ct_c2", "paid_roles": [], "why": "contract arm, all credentials dropped"},
    "66553067": {"stage": "ct_lean", "paid_roles": [], "why": "contract arm, all credentials dropped"},
    "66553083": {"stage": "ct_lean", "paid_roles": [], "why": "contract arm, all credentials dropped"},
    "66553105": {"stage": "ct_det", "paid_roles": [], "why": "contract arm, all credentials dropped"},
}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobdirs", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prior-estimate-actor-attempts", type=int, default=3462)
    ap.add_argument("--prior-estimate-actor-usd", type=float, default=1.87)
    ap.add_argument("--prior-estimate-opt-calls", type=int, default=206)
    ap.add_argument("--prior-estimate-opt-usd", type=float, default=2.06)
    a = ap.parse_args(argv)

    call_logs = A.read_call_logs(a.jobdirs)
    ledger = A.read_ledger(a.ledger)
    rec = A.reconcile(call_logs, ledger, CATALOGUE)
    uncovered = A.gaps(rec["rows"], CATALOGUE)

    # Jobs the records show spending for that the catalogue did NOT expect: the opposite error, and the
    # one that would quietly understate the phase.
    unexpected = []
    for r in rec["rows"]:
        meta = CATALOGUE.get(r["job"])
        if meta is None:
            unexpected.append({**{k: r[k] for k in ("job", "role", "attempts")},
                               "why": "this job is not in the catalogue at all"})
        elif r["role"] not in meta.get("paid_roles", []) and r["attempts"] > 0:
            unexpected.append({**{k: r[k] for k in ("job", "role", "attempts")},
                               "why": "the catalogue says this role spent nothing here"})

    # Outstanding reservations in the live ledger: claimed - released, against what was actually spent.
    outstanding = {}
    for (role, resource), st in sorted(rec["claim_state"].items()):
        net = st["claimed"] - st["released"]
        spent = (rec["totals"][role]["attempts"] if resource == "attempts"
                 else int(round(rec["totals"][role]["computed_usd"] * 1_000_000)))
        led_spent = None
        if resource == "attempts":
            led_spent = sum(p["attempts"] for (o, rl), p in ledger["processes"].items() if rl == role)
        else:
            led_spent = sum(p["computed_micro"] for (o, rl), p in ledger["processes"].items()
                            if rl == role)
        outstanding[f"{role}/{resource}"] = {
            "claimed": st["claimed"], "released": st["released"], "net_claimed": net,
            "n_claims": st["n_claims"], "n_releases": st["n_releases"],
            "spent_within_ledger_coverage": led_spent,
            "still_reserved_but_unspent": net - led_spent,
            "invariant_spent_le_claimed": "HOLDS" if led_spent <= net else "VIOLATED",
            "units": ("attempts" if resource == "attempts"
                      else "micro-USD, integer, as the ledger itself charges"),
            "note": "the ledger only ever saw the jobs from 66553248 on, so `net_claimed` describes "
                    "those jobs alone and is NOT this phase's consumption",
        }

    opening = {}
    for role in ("target", "optimizer"):
        t = rec["totals"][role]
        cap = A.PHASE_CAPS[role]
        bound_attempts = sum(int(g["conservative_upper_bound"].get("attempts", 0))
                             for g in uncovered if g["role"] == role)
        bound_usd = sum(float(g["conservative_upper_bound"].get("usd", 0.0))
                        for g in uncovered if g["role"] == role)
        opening[role] = {
            "cap_attempts": cap["attempts"], "cap_usd": cap["usd"],
            "reconstructed_attempts": t["attempts"],
            "reconstructed_charged_usd": t["computed_usd"],
            "provider_reported_usd": t["provider_cost_usd"],
            "unresolved_requests": t["unresolved_requests"],
            "unresolved_reserve_usd": t["unresolved_reserve_usd"],
            "uncovered_allowance_attempts": bound_attempts,
            "uncovered_allowance_usd": round(bound_usd, 6),
            "opening_balance_attempts_consumed": t["attempts"] + bound_attempts,
            "opening_balance_usd_consumed": round(t["charged_usd_upper"] + bound_usd, 6),
            "remaining_attempts": cap["attempts"] - (t["attempts"] + bound_attempts),
            "remaining_usd": round(cap["usd"] - (t["charged_usd_upper"] + bound_usd), 6),
        }

    prior = {
        "target": {"attempts": a.prior_estimate_actor_attempts, "usd": a.prior_estimate_actor_usd},
        "optimizer": {"attempts": a.prior_estimate_opt_calls, "usd": a.prior_estimate_opt_usd},
    }
    versus_prior = {}
    for role in ("target", "optimizer"):
        versus_prior[role] = {
            "previously_reported_attempts": prior[role]["attempts"],
            "now_reconstructed_attempts": opening[role]["opening_balance_attempts_consumed"],
            "attempts_delta": opening[role]["opening_balance_attempts_consumed"] - prior[role]["attempts"],
            "previously_reported_usd": prior[role]["usd"],
            "now_reconstructed_usd": opening[role]["opening_balance_usd_consumed"],
            "usd_delta": round(opening[role]["opening_balance_usd_consumed"] - prior[role]["usd"], 6),
        }

    doc = {
        "schema": A.SCHEMA,
        "written_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "what_this_is": "a reconstruction of the 2026-11-21 phase's paid consumption and an opening-"
                        "balance adjustment for the work that follows it. No historical record is "
                        "modified by this document or by the code that produced it.",
        "sources": {
            "call_logs": {"root": a.jobdirs, "n_log_files": sum(len(v["paths"]) for v in call_logs.values()),
                          "covers": "one line per HTTP attempt incl. retries, with the provider's own "
                                    "per-call cost_usd"},
            "ledger": {"root": a.ledger, "n_processes": len(ledger["processes"]),
                       "covers": "jobs from 66553248 onward only; before that SKILLOPT_SPEND_LEDGER_DIR "
                                 "was unset and spend_ledger.py was inert"},
            "provider_statement": {"available": False,
                                   "note": "no account statement is reachable offline; 'provider "
                                           "accounting' here means the per-call cost_usd the provider "
                                           "returned inside the call log and the ledger usage records"},
        },
        "per_job": rec["rows"],
        "totals": rec["totals"],
        "overlap": {
            "both_sources": sorted({(r["job"], r["role"]) for r in rec["rows"]
                                    if r["coverage"] == "both"}),
            "call_log_only": sorted({(r["job"], r["role"]) for r in rec["rows"]
                                     if r["coverage"] == "call_log_only"}),
            "ledger_only": sorted({(r["job"], r["role"]) for r in rec["rows"]
                                   if r["coverage"] == "ledger_only"}),
            "rule": "where both exist the call log is counted and the ledger is used only to check it",
        },
        "uncovered_by_every_source": uncovered,
        "spending_the_catalogue_did_not_expect": unexpected,
        "outstanding_reservations": outstanding,
        "opening_balance": opening,
        "versus_the_previously_reported_figures": versus_prior,
    }
    body = json.dumps(doc, indent=1, sort_keys=True, default=str)
    doc["self_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(f"wrote {a.out}  digest {doc['self_sha256'][:16]}")
    for role in ("target", "optimizer"):
        o = opening[role]
        prov = o['provider_reported_usd']
        prov_s = f"${prov:.4f}" if isinstance(prov, (int, float)) else str(prov)
        print(f"  {role:9s} attempts {o['reconstructed_attempts']:>6d} of {o['cap_attempts']:<6d}"
              f"  charged ${o['reconstructed_charged_usd']:.4f} of ${o['cap_usd']:.2f}"
              f"  provider {prov_s}"
              f"  remaining {o['remaining_attempts']} / ${o['remaining_usd']:.4f}")
    print(f"  uncovered (job,role): {len(uncovered)}   unexpected: {len(unexpected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
