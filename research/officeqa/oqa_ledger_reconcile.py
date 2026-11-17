#!/usr/bin/env python3
"""Reconcile a spend ledger left behind by processes that exited WITHOUT releasing their claims.

WHY THIS EXISTS. `skillopt/model/spend_ledger.py` enforces the authorization on money that has been CLAIMED,
not on money that has been spent. Claiming before sending is the whole point - it is what makes
`spent <= claimed <= cap` hold across concurrent processes and across restarts - but until 2026-11-17 nothing
ever gave a claim back. The three-arm OfficeQA campaign therefore ran out of authorization with 45 per cent of
the optimizer's money unspent, and four arm-epochs made zero optimizer calls while DeepSeek had been paid
$2.19 of an authorized $4.00.

`release()` now hands back the unspent remainder when a process exits cleanly. That does nothing for a ledger
whose processes are already gone. This tool is the post-hoc equivalent: it reads the per-process spend records,
works out what each exited process claimed and never spent, and writes the same `release/` entries that process
would have written itself.

RULE OQA-REC-1, which is the same rule `release()` applies in-process:

  * ATTEMPTS. An attempt is either sent, and then it is recorded, or it is not sent at all. So the whole of
    `claimed_attempts - attempts_recorded` is released.

  * MONEY. `claimed_usd - usd_recorded` is released MINUS one `reserve_usd` for every UNRESOLVED request:
    a request the process sent (an `attempt` record) whose reply it never accounted for (no matching `usage`
    record). The provider may well have billed it. Treating it as spent is the conservative direction; the
    alternative lets a later process spend money that is already gone.

  * ACTUAL CHARGES ARE NEVER RELEASED. By construction the release can only reach down to
    `usd_recorded + unresolved * reserve_usd`, so every dollar the ledger has seen charged stays claimed.

SAFETY. Releasing the claim of a process that is still running would break the invariant the ledger exists to
hold. So an owner is reconciled only when it is demonstrably finished:

  * `--exited-jobs` lists the Slurm job ids known to have terminated (from `sacct`), and every owner whose job
    is not on that list is left alone, reported as `skipped_possibly_live`; or
  * `--assume-all-exited` for a ledger with no live jobs at all, which still refuses if `squeue` is reachable
    and shows one of the owners running.

Stdlib only, and it is an independent reader: it never imports `skillopt`, so it audits the ledger as a third
party rather than through the code that wrote it. `--dry-run` is the default; nothing is written without
`--apply`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

SCHEMA = "lso.spend_ledger.v1"
RULE = "OQA-REC-1"
ROLES = ("target", "optimizer")
_NAME = re.compile(r"^(\d{6})-(target|optimizer)-(attempts|usdmicro)-(\d+)\.json$")
# The reserve a process sets aside per in-flight request, by role. These are spend_ledger's defaults; a ledger
# whose `open` record carries a different `reserve_usd` is read from that record instead, which is why the
# `open` record is parsed at all.
_DEFAULT_RESERVE_USD = {"target": 0.02, "optimizer": 0.15}


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _owner_job(owner: str) -> str:
    """`66273862r0p1988298` -> `66273862`. Returns '' when the token has no job id."""
    m = re.match(r"^(\d+)r", owner)
    return m.group(1) if m else ""


def read_ledger(ledger: str) -> dict:
    """Parse claims, releases and per-process spend records. Opens the claim files, unlike `_granted()`.

    `_granted()` in spend_ledger reads amounts out of the claim FILE NAMES, which is what makes the hot path
    cheap. Reconciliation needs to know WHICH PROCESS made each claim, and that is only in the file body.
    """
    claims_dir = os.path.join(ledger, "claims")
    release_dir = os.path.join(ledger, "release")
    spend_dir = os.path.join(ledger, "spend")

    claimed_by_owner: dict[tuple[str, str, str], int] = {}
    claimed_total: dict[tuple[str, str], int] = {}
    unattributed: dict[tuple[str, str], int] = {}
    for name in sorted(os.listdir(claims_dir)):
        m = _NAME.match(name)
        if not m:
            continue
        role, resource, amount = m.group(2), m.group(3), int(m.group(4))
        claimed_total[(role, resource)] = claimed_total.get((role, resource), 0) + amount
        owner = ""
        try:
            with open(os.path.join(claims_dir, name), encoding="utf-8") as f:
                owner = str(json.load(f).get("owner") or "")
        except (OSError, json.JSONDecodeError):
            owner = ""
        if owner:
            key = (owner, role, resource)
            claimed_by_owner[key] = claimed_by_owner.get(key, 0) + amount
        else:
            unattributed[(role, resource)] = unattributed.get((role, resource), 0) + amount

    released_total: dict[tuple[str, str], int] = {}
    released_by_owner: dict[tuple[str, str, str], int] = {}
    try:
        release_names = sorted(os.listdir(release_dir))
    except OSError:
        release_names = []
    for name in release_names:
        m = _NAME.match(name)
        if not m:
            continue
        role, resource, amount = m.group(2), m.group(3), int(m.group(4))
        released_total[(role, resource)] = released_total.get((role, resource), 0) + amount
        try:
            with open(os.path.join(release_dir, name), encoding="utf-8") as f:
                owner = str(json.load(f).get("owner") or "")
        except (OSError, json.JSONDecodeError):
            owner = ""
        if owner:
            key = (owner, role, resource)
            released_by_owner[key] = released_by_owner.get(key, 0) + amount

    owners: dict[str, dict] = {}
    for name in sorted(os.listdir(spend_dir)):
        if not name.endswith(".jsonl"):
            continue
        owner = name[:-len(".jsonl")]
        rec = owners.setdefault(owner, {"owner": owner, "job": _owner_job(owner), "roles": {}})
        with open(os.path.join(spend_dir, name), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = str(row.get("role") or "")
                if role not in ROLES:
                    continue
                r = rec["roles"].setdefault(role, {
                    "attempts": 0, "resolved": 0, "usdmicro": 0, "refusals": 0,
                    "reserve_usd": _DEFAULT_RESERVE_USD[role], "released_already": False})
                kind = row.get("kind")
                if kind == "open":
                    cfg = row.get("cfg") or {}
                    if cfg.get("reserve_usd") is not None:
                        r["reserve_usd"] = float(cfg["reserve_usd"])
                elif kind == "attempt":
                    r["attempts"] += 1
                elif kind == "usage":
                    r["resolved"] += 1
                    r["usdmicro"] += int(round(float(row.get("usd") or 0.0) * 1_000_000))
                elif kind == "refused":
                    r["refusals"] += 1
                elif kind == "release":
                    r["released_already"] = True

    return {"ledger": ledger, "claims_dir": claims_dir, "release_dir": release_dir,
            "claimed_by_owner": claimed_by_owner, "claimed_total": claimed_total,
            "claimed_unattributed": unattributed, "released_total": released_total,
            "released_by_owner": released_by_owner, "owners": owners}


def _live_jobs() -> set[str] | None:
    """Job ids this user currently has in the queue, or None when squeue cannot be reached."""
    try:
        out = subprocess.run(["squeue", "-h", "-u", os.environ.get("USER", ""), "-o", "%i"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    jobs = set()
    for line in out.stdout.split():
        jobs.add(line.split("_")[0].split(".")[0])
    return jobs


def plan(ledger: str, *, exited_jobs: set[str] | None, assume_all_exited: bool) -> dict:
    st = read_ledger(ledger)
    live = _live_jobs()
    entries: list[dict] = []
    skipped: list[dict] = []

    for owner in sorted(st["owners"]):
        rec = st["owners"][owner]
        job = rec["job"]
        if live is not None and job and job in live:
            skipped.append({"owner": owner, "job": job, "reason": "its Slurm job is in the queue now"})
            continue
        if not assume_all_exited:
            if exited_jobs is None or (job and job not in exited_jobs):
                skipped.append({"owner": owner, "job": job,
                                "reason": "not listed as exited; pass --exited-jobs or --assume-all-exited"})
                continue
        for role, r in sorted(rec["roles"].items()):
            unresolved = max(0, r["attempts"] - r["resolved"])
            hold_usdmicro = unresolved * int(round(r["reserve_usd"] * 1_000_000))
            for resource, used, hold in (("attempts", r["attempts"], 0),
                                         ("usdmicro", r["usdmicro"], hold_usdmicro)):
                claimed = st["claimed_by_owner"].get((owner, role, resource), 0)
                already = st["released_by_owner"].get((owner, role, resource), 0)
                amount = claimed - already - used - hold
                if amount <= 0:
                    continue
                entries.append({
                    "owner": owner, "job": job, "role": role, "resource": resource,
                    "claimed": claimed, "already_released": already, "recorded_use": used,
                    "retained_for_unresolved": hold, "release": amount,
                    "unresolved_requests": unresolved,
                })

    totals: dict[str, dict] = {}
    for e in entries:
        t = totals.setdefault(e["role"], {"attempts": 0, "usdmicro": 0, "retained_usdmicro": 0,
                                          "unresolved_requests": 0})
        t[e["resource"]] += e["release"]
        if e["resource"] == "usdmicro":
            t["retained_usdmicro"] += e["retained_for_unresolved"]
            t["unresolved_requests"] += e["unresolved_requests"]

    before: dict[str, dict] = {}
    for role in ROLES:
        ca = st["claimed_total"].get((role, "attempts"), 0) - st["released_total"].get((role, "attempts"), 0)
        cu = st["claimed_total"].get((role, "usdmicro"), 0) - st["released_total"].get((role, "usdmicro"), 0)
        used_a = sum(r["roles"].get(role, {}).get("attempts", 0) for r in st["owners"].values())
        used_u = sum(r["roles"].get(role, {}).get("usdmicro", 0) for r in st["owners"].values())
        rel = totals.get(role, {})
        before[role] = {
            "net_claimed_attempts": ca, "net_claimed_usd": round(cu / 1e6, 6),
            "recorded_attempts": used_a, "recorded_usd": round(used_u / 1e6, 6),
            "would_release_attempts": rel.get("attempts", 0),
            "would_release_usd": round(rel.get("usdmicro", 0) / 1e6, 6),
            "net_claimed_attempts_after": ca - rel.get("attempts", 0),
            "net_claimed_usd_after": round((cu - rel.get("usdmicro", 0)) / 1e6, 6),
            "unresolved_requests": rel.get("unresolved_requests", 0),
            "retained_usd_for_unresolved": round(rel.get("retained_usdmicro", 0) / 1e6, 6),
        }

    problems = []
    for role, b in before.items():
        if b["net_claimed_usd"] + 1e-9 < b["recorded_usd"]:
            problems.append(f"{role}: recorded spend ${b['recorded_usd']:.4f} exceeds net claims "
                            f"${b['net_claimed_usd']:.4f} - the invariant spent <= claimed is BROKEN")
        if b["net_claimed_usd_after"] + 1e-9 < b["recorded_usd"]:
            problems.append(f"{role}: the plan would release actual charges, which rule {RULE} forbids")
    if st["claimed_unattributed"]:
        problems.append(f"claims with no owner recorded: {st['claimed_unattributed']} - these are never "
                        f"released, because the process that made them cannot be shown to have exited")

    return {"rule": RULE, "ledger": ledger, "utc": _utc(), "entries": entries, "skipped": skipped,
            "by_role": before, "problems": problems,
            "squeue_consulted": live is not None,
            "live_jobs": sorted(live) if live else []}


def apply_plan(p: dict, *, reason: str) -> dict:
    """Write the release records. Same atomic O_CREAT|O_EXCL sequence scheme as a claim."""
    rel_dir = os.path.join(p["ledger"], "release")
    os.makedirs(rel_dir, exist_ok=True)
    written = []
    for e in p["entries"]:
        for _ in range(500):
            names = os.listdir(rel_dir)
            seq = 0
            for name in names:
                m = _NAME.match(name)
                if m:
                    seq = max(seq, int(m.group(1)))
            seq += 1
            path = os.path.join(rel_dir, f"{seq:06d}-{e['role']}-{e['resource']}-{e['release']}.json")
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"schema": SCHEMA, "kind": "release", "seq": seq, "role": e["role"],
                           "resource": e["resource"], "amount": e["release"], "owner": e["owner"],
                           "reason": reason, "rule": RULE,
                           "recorded_use": e["recorded_use"],
                           "retained_for_unresolved": e["retained_for_unresolved"],
                           "utc": _utc()}, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            written.append(os.path.basename(path))
            break
        else:
            raise RuntimeError(f"could not allocate a release sequence in {rel_dir}")
    return {"written": written, "n": len(written)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ledger", help="the ledger directory (the one holding claims/ spend/ release/)")
    ap.add_argument("--exited-jobs", default="",
                    help="comma-separated Slurm job ids known to have terminated")
    ap.add_argument("--assume-all-exited", action="store_true",
                    help="treat every owner as finished; still refuses any owner squeue shows running")
    ap.add_argument("--apply", action="store_true", help="write the release records (default is a dry run)")
    ap.add_argument("--reason", default="post-hoc reconciliation of an exited process (OQA-REC-1)")
    ap.add_argument("--out", default="", help="write the plan as JSON here")
    args = ap.parse_args()

    exited = {s.strip() for s in args.exited_jobs.split(",") if s.strip()} or None
    p = plan(args.ledger, exited_jobs=exited, assume_all_exited=args.assume_all_exited)

    print(f"rule {RULE}   ledger {args.ledger}")
    print(f"squeue consulted: {p['squeue_consulted']}   live jobs: {p['live_jobs'] or 'none'}")
    for role, b in sorted(p["by_role"].items()):
        print(f"\n  {role}")
        print(f"    recorded   {b['recorded_attempts']:>7} attempts   ${b['recorded_usd']:.4f}")
        print(f"    claimed    {b['net_claimed_attempts']:>7} attempts   ${b['net_claimed_usd']:.4f}  (net of releases)")
        print(f"    release    {b['would_release_attempts']:>7} attempts   ${b['would_release_usd']:.4f}")
        print(f"    after      {b['net_claimed_attempts_after']:>7} attempts   ${b['net_claimed_usd_after']:.4f}")
        if b["unresolved_requests"]:
            print(f"    RETAINED for {b['unresolved_requests']} unresolved request(s): "
                  f"${b['retained_usd_for_unresolved']:.4f}")
    if p["skipped"]:
        print(f"\n  {len(p['skipped'])} owner(s) left alone:")
        for s in p["skipped"][:10]:
            print(f"    {s['owner']}  {s['reason']}")
    if p["problems"]:
        print("\n  PROBLEMS")
        for s in p["problems"]:
            print(f"    - {s}")

    if args.apply:
        if p["problems"]:
            print("\nrefusing to apply: fix the problems above first")
            return 3
        res = apply_plan(p, reason=args.reason)
        print(f"\nwrote {res['n']} release record(s)")
        p["applied"] = res
    else:
        print("\ndry run - nothing written. Pass --apply to write the release records.")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=2, sort_keys=True)
        print(f"plan: {args.out}")
    return 2 if p["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
