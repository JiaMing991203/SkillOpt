"""Read a campaign spend ledger from OUTSIDE the job, with no dependency on the deployed tree.

WHY THIS EXISTS. The job body's end-of-job ledger summary runs `python -I -m skillopt.model.spend_ledger`, and
`-I` implies `-E`, so PYTHONPATH is ignored and `-m` cannot find the extracted tree: job 66272737 recorded
"No module named 'skillopt'" and wrote no LEDGER_AFTER.json. The body is fixed, but the campaign's deployed
bundle carries the old version and MUST NOT be changed mid-run - its manifest is verified in-job, so editing it
would kill every queued job. This script reads the same files with nothing but the standard library, so the
ledger can be audited at any time from anywhere.

It also reports the two things a cap is for: that observed spend never exceeded what was claimed, and that what
was claimed never exceeded the authorization. And it reports per-question cost, which is the measurement the
budget's constants should be taken from.

usage: python3 -I research/officeqa/oqa_ledger_read.py <ledger dir> [--questions N] [--out FILE]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re

CLAIM = re.compile(r"^(\d{6})-(target|optimizer)-(attempts|usdmicro)-(\d+)\.json$")
CAPS = {"target": {"attempts": 15000, "usd": 8.00}, "optimizer": {"attempts": 130, "usd": 4.00}}


def read(ledger: str) -> dict:
    claimed: dict[tuple[str, str], int] = {}
    for name in os.listdir(os.path.join(ledger, "claims")):
        m = CLAIM.match(name)
        if m:
            key = (m.group(2), m.group(3))
            claimed[key] = claimed.get(key, 0) + int(m.group(4))
    # Releases: what processes handed back when they exited cleanly. The cap is enforced on the NET claim, so
    # an audit that ignored these would overstate how much of the authorization is gone - which is exactly the
    # confusion that made the three-arm campaign's starvation hard to see from the totals.
    released: dict[tuple[str, str], int] = {}
    try:
        release_names = os.listdir(os.path.join(ledger, "release"))
    except OSError:
        release_names = []
    for name in release_names:
        m = CLAIM.match(name)
        if m:
            key = (m.group(2), m.group(3))
            released[key] = released.get(key, 0) + int(m.group(4))
    for key, amount in released.items():
        claimed[key] = claimed.get(key, 0) - amount

    roles: dict[str, dict] = {}
    owners: dict[str, dict] = {}
    times: dict[str, list] = {}
    spend_dir = os.path.join(ledger, "spend")
    for name in sorted(os.listdir(spend_dir)):
        if not name.endswith(".jsonl"):
            continue
        for line in open(os.path.join(spend_dir, name), encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = rec.get("role")
            if not role:
                continue
            r = roles.setdefault(role, {"attempts": 0, "usd": 0.0, "usdmicro": 0, "refusals": 0,
                                        "prompt_tokens": 0, "completion_tokens": 0,
                                        "cached_prompt_tokens": 0, "reserve_exceeded": 0,
                                        "usage_records": 0})
            o = owners.setdefault(name[:-6], {"role": role, "attempts": 0, "usd": 0.0,
                                              "restart": rec.get("slurm_restart_count", "")})
            kind = rec.get("kind")
            if kind == "attempt":
                r["attempts"] += 1
                o["attempts"] += 1
                times.setdefault(role, []).append(rec.get("utc"))
            elif kind == "usage":
                r["usage_records"] += 1
                # TWO SUMS, because they are not the same number and the difference once made this tool
                # report a failure that was not one. `usdmicro` adds the SAME per-record rounded integer the
                # ledger itself adds to `used_usdmicro`, so it is the quantity the cap is actually enforced
                # on; `usd` adds the raw floats, which over 7,313 records drifted 26 microdollars ABOVE it
                # and so appeared to exceed a claim that exactly covered the real spend. The invariant is
                # checked in micros; the float sum is kept for reference and reported when it differs.
                usd = float(rec.get("usd") or 0.0)
                r["usd"] += usd
                r["usdmicro"] += int(round(usd * 1_000_000))
                o["usd"] += usd
                for field in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens"):
                    r[field] += int(rec.get(field) or 0)
                if rec.get("reserve_exceeded"):
                    r["reserve_exceeded"] += 1
            elif kind == "refused":
                r["refusals"] += 1
    for r in roles.values():
        r["usd_float_sum"] = round(r["usd"], 9)
        r["usd"] = round(r["usdmicro"] / 1e6, 6)
        r["usd_float_minus_micro"] = round(r["usd_float_sum"] - r["usd"], 9)
        r["failed_attempts"] = r["attempts"] - r["usage_records"]
        # An attempt with no usage record is an UNRESOLVED request: it was sent and its cost was never
        # learned. The ledger retains one reserve for each, and the reconciler does too.
        r["unresolved_requests"] = r["failed_attempts"]
    for o in owners.values():
        o["usd"] = round(o["usd"], 6)

    rates = {}
    for role, stamps in times.items():
        marks = sorted(datetime.datetime.fromisoformat(s) for s in stamps if s)
        if len(marks) > 1:
            minutes = (marks[-1] - marks[0]).total_seconds() / 60.0
            rates[role] = {"first": marks[0].isoformat(), "last": marks[-1].isoformat(),
                           "span_minutes": round(minutes, 1),
                           "attempts_per_minute": round(len(marks) / minutes, 2) if minutes else None}

    problems = []
    for role, r in roles.items():
        cap = CAPS.get(role, {})
        got_a = claimed.get((role, "attempts"), 0)
        got_u = claimed.get((role, "usdmicro"), 0) / 1e6
        if r["attempts"] > got_a:
            problems.append(f"{role}: {r['attempts']} attempts observed but only {got_a} claimed")
        if cap and r["attempts"] > cap["attempts"]:
            problems.append(f"{role}: {r['attempts']} attempts exceeds the authorized {cap['attempts']}")
        if cap and r["usd"] > cap["usd"]:
            problems.append(f"{role}: ${r['usd']:.4f} exceeds the authorized ${cap['usd']:.2f}")
        if r["usdmicro"] > int(round(got_u * 1_000_000)):
            problems.append(f"{role}: ${r['usd']:.6f} spent against ${got_u:.6f} claimed net of releases")
        # The failure mode that starved four arm-epochs: the NET claim reaches the cap while real spend is far
        # below it, so every further attempt is refused with money still authorized.
        if cap and got_u >= cap["usd"] - 1e-9 and r["usd"] < cap["usd"] * 0.9:
            problems.append(
                f"{role}: the net claim has reached the ${cap['usd']:.2f} cap while only ${r['usd']:.4f} has "
                f"been spent - ${got_u - r['usd']:.4f} is claimed-but-unspent and unreachable. Further "
                f"attempts will be REFUSED with money still authorized.")
    return {
        "ledger": os.path.abspath(ledger),
        "read_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "claimed_net_of_releases": {f"{k[0]}/{k[1]}": (v / 1e6 if k[1] == "usdmicro" else v)
                                    for k, v in sorted(claimed.items())},
        "released": {f"{k[0]}/{k[1]}": (v / 1e6 if k[1] == "usdmicro" else v)
                     for k, v in sorted(released.items())},
        "authorized": CAPS,
        "observed": roles,
        "rates": rates,
        "processes": owners,
        "problems": problems,
        "verdict": "PASS" if not problems else "FAIL",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger")
    ap.add_argument("--questions", type=int, default=0,
                    help="question-rollouts completed so far, to report cost per question")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = read(args.ledger)
    if args.questions:
        t = out["observed"].get("target") or {}
        if t:
            out["per_question"] = {
                "questions": args.questions,
                "attempts": round(t["attempts"] / args.questions, 2),
                "prompt_tokens": round(t["prompt_tokens"] / args.questions),
                "completion_tokens": round(t["completion_tokens"] / args.questions),
                "usd": round(t["usd"] / args.questions, 6),
            }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
