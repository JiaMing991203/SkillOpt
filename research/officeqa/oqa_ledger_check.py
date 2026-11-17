"""Verify on the REAL filesystem that the attempt and spend caps hold across concurrency and restarts.

The unit tests in `tests/test_officeqa_campaign_guards.py` establish this on a local temporary directory. The
campaign's ledger lives on `/nfs/turbo`, and the mechanism that makes it safe is the atomicity of
`O_CREAT|O_EXCL` file creation - which is exactly the kind of guarantee that differs between a local filesystem
and a network one. This script runs the same races where the campaign will run them.

Three checks, all free (no HTTP, no GPU, no model request):

  concurrency   N processes race for one cap that is deliberately not a multiple of the block size. Their total
                must not exceed the cap, and the ledger's own record must agree with what they report.
  restart       a fresh process, with a Slurm restart count set, must find the authorization already spent.
  fail_closed   with the claim directory read-only, a process must refuse to proceed rather than spend.

usage: python3 -I research/officeqa/oqa_ledger_check.py --root <dir on the shared filesystem> --out LEDGER_CHECK.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

CAP = 23
BLOCK = 2
NPROC = 4
PRICE = "0.04,0.07,0.02"


def _env(ledger: str, cap: int) -> dict:
    return {
        "SKILLOPT_SPEND_LEDGER_DIR": ledger,
        "SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET": str(cap),
        "SKILLOPT_LEDGER_MAX_USD_TARGET": "100.0",
        "SKILLOPT_LEDGER_PRICE_TARGET": PRICE,
        "SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET": str(BLOCK),
        "SKILLOPT_LEDGER_BLOCK_USD_TARGET": "1.0",
        "SKILLOPT_LEDGER_RESERVE_USD_TARGET": "0.01",
        "SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER": "4",
        "SKILLOPT_LEDGER_MAX_USD_OPTIMIZER": "1.0",
        "SKILLOPT_LEDGER_PRICE_OPTIMIZER": "0.15,0.60,0.003",
    }


def _child(ledger: str, cap: int, queue) -> None:
    os.environ.update(_env(ledger, cap))
    from skillopt.model import spend_ledger as L
    L._state.clear()
    L._owner = ""
    spent = 0
    for _ in range(cap * 5):
        try:
            L.reserve("target", "ledger-check")
        except L.BudgetExhausted:
            break
        spent += 1
        L.record_usage("target", "ledger-check", 1000, 100)
    queue.put(spent)


_SPEND_ALL = (
    "import json, os, sys; sys.path.insert(0, %r)\n"
    "from skillopt.model import spend_ledger as L\n"
    "n = 0\n"
    "for _ in range(500):\n"
    "    try:\n"
    "        L.reserve('target', 'restart-check')\n"
    "    except L.BudgetExhausted:\n"
    "        break\n"
    "    n += 1\n"
    "print(json.dumps({'spent': n}))\n"
) % REPO


def concurrency(root: str) -> dict:
    ledger = os.path.join(root, "concurrency")
    shutil.rmtree(ledger, ignore_errors=True)
    os.makedirs(os.path.join(ledger, "claims"), exist_ok=True)
    os.makedirs(os.path.join(ledger, "spend"), exist_ok=True)
    ctx = mp.get_context("fork")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_child, args=(ledger, CAP, queue)) for _ in range(NPROC)]
    for p in procs:
        p.start()
    reported = []
    for p in procs:
        p.join(300)
        reported.append(p.exitcode)
    totals = [queue.get() for _ in procs]
    os.environ.update(_env(ledger, CAP))
    from skillopt.model import spend_ledger as L
    L._state.clear()
    report = L.report()
    observed = report["observed"]["by_role"]["target"]["attempts"]
    problems = []
    if any(code != 0 for code in reported):
        problems.append(f"a child process failed: exit codes {reported}")
    if sum(totals) > CAP:
        problems.append(f"{NPROC} processes spent {sum(totals)} against a cap of {CAP}")
    if observed != sum(totals):
        problems.append(f"the ledger records {observed} attempts but the processes report {sum(totals)}")
    if sum(totals) == 0:
        problems.append("no process managed to spend anything, so nothing was tested")
    return {"cap": CAP, "processes": NPROC, "per_process": totals, "total": sum(totals),
            "ledger_observed": observed, "ledger_verdict": report["verdict"],
            "filesystem": _fstype(ledger), "problems": problems,
            "verdict": "PASS" if not problems else "FAIL"}


def restart(root: str) -> dict:
    ledger = os.path.join(root, "restart")
    shutil.rmtree(ledger, ignore_errors=True)
    os.makedirs(os.path.join(ledger, "claims"), exist_ok=True)
    os.makedirs(os.path.join(ledger, "spend"), exist_ok=True)
    env = {**os.environ, **_env(ledger, 9), "SLURM_JOB_ID": "ledgercheck"}
    first = subprocess.run([sys.executable, "-I", "-c", _SPEND_ALL], capture_output=True, text=True,
                           env={**env, "SLURM_RESTART_COUNT": "0"})
    second = subprocess.run([sys.executable, "-I", "-c", _SPEND_ALL], capture_output=True, text=True,
                            env={**env, "SLURM_RESTART_COUNT": "1"})
    problems = []
    got_first = got_second = -1
    try:
        got_first = json.loads(first.stdout.strip().splitlines()[-1])["spent"]
        got_second = json.loads(second.stdout.strip().splitlines()[-1])["spent"]
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not read a child's output: {exc}; stderr={second.stderr[:300]}")
    if got_first != 9:
        problems.append(f"the first process spent {got_first}, not the whole cap of 9")
    if got_second != 0:
        problems.append(f"THE RESTART RESPENT {got_second} attempts; the cap did not survive it")
    return {"cap": 9, "first_process_spent": got_first, "after_restart_spent": got_second,
            "problems": problems, "verdict": "PASS" if not problems else "FAIL"}


def fail_closed(root: str) -> dict:
    ledger = os.path.join(root, "failclosed")
    shutil.rmtree(ledger, ignore_errors=True)
    claims = os.path.join(ledger, "claims")
    os.makedirs(claims, exist_ok=True)
    os.makedirs(os.path.join(ledger, "spend"), exist_ok=True)
    os.chmod(claims, 0o500)
    env = {**os.environ, **_env(ledger, 9), "SLURM_JOB_ID": "ledgercheck"}
    try:
        run = subprocess.run([sys.executable, "-I", "-c", _SPEND_ALL], capture_output=True, text=True, env=env)
        spent = -1
        try:
            spent = json.loads(run.stdout.strip().splitlines()[-1])["spent"]
        except Exception:  # noqa: BLE001 - a crash is the expected outcome
            spent = None
        refused = run.returncode != 0 or spent == 0
        problems = [] if refused else [f"an unwritable ledger still allowed {spent} attempts"]
        return {"returncode": run.returncode, "spent": spent,
                "error_tail": run.stderr.strip().splitlines()[-1][:200] if run.stderr.strip() else "",
                "problems": problems, "verdict": "PASS" if not problems else "FAIL"}
    finally:
        os.chmod(claims, 0o700)


def _fstype(path: str) -> str:
    try:
        out = subprocess.run(["stat", "-f", "-c", "%T", path], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="a directory on the filesystem the campaign ledger will use")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.root, exist_ok=True)
    checks = {"concurrency": concurrency(args.root), "restart": restart(args.root),
              "fail_closed": fail_closed(args.root)}
    problems = [f"{name}: {p}" for name, c in checks.items() for p in c["problems"]]
    out = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": os.path.abspath(args.root), "filesystem": _fstype(args.root),
        "host": os.uname().nodename, "slurm_job": os.environ.get("SLURM_JOB_ID", ""),
        "checks": checks, "problems": problems,
        "verdict": "PASS" if not problems else "FAIL",
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
