"""Verify on the REAL filesystem that the attempt and spend caps hold across concurrency and restarts.

The unit tests in `tests/test_officeqa_campaign_guards.py` establish this on a local temporary directory. The
campaign's ledger lives on `/nfs/turbo`, and the mechanism that makes it safe is the atomicity of
`O_CREAT|O_EXCL` file creation - which is exactly the kind of guarantee that differs between a local filesystem
and a network one. This script runs the same races where the campaign will run them.

Three checks, all free (no HTTP, no GPU, no model request):

  concurrency   N processes race for one cap that is deliberately not a multiple of the block size. Their total
                must not exceed the cap, and the ledger's own record must agree with what they report.
  restart       a fresh process, with a Slurm restart count set, must find the authorization already spent.
  fail_closed   a ledger that cannot be used must stop the run. Two ways of making it unusable, because
                only one of them is portable - see `fail_closed` below.

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


def _spend_in_child(ledger: str, cap: int = 9) -> dict:
    env = {**os.environ, **_env(ledger, cap), "SLURM_JOB_ID": "ledgercheck"}
    run = subprocess.run([sys.executable, "-I", "-c", _SPEND_ALL], capture_output=True, text=True, env=env)
    try:
        spent = json.loads(run.stdout.strip().splitlines()[-1])["spent"]
    except Exception:  # noqa: BLE001 - a crash IS a refusal, and is the expected outcome here
        spent = None
    return {"returncode": run.returncode, "spent": spent,
            "refused": run.returncode != 0 or spent in (0, None),
            "error_tail": run.stderr.strip().splitlines()[-1][:200] if run.stderr.strip() else ""}


def _chmod_is_enforced(path: str) -> bool:
    """Does this filesystem actually deny the OWNER write access to a directory they chmod 0500?

    On the campaign's `/nfs/turbo` it does NOT: job 66272358 chmod-ed the claim directory to 0500 and then
    created a file in it without error, because the share carries POSIX ACLs whose mask grants the owner
    write access regardless of the mode bits. The first version of this check read that as the LEDGER failing
    when what had failed was the check's own premise. So the premise is now probed instead of assumed.
    """
    os.chmod(path, 0o500)
    probe = os.path.join(path, ".chmod-probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
    except OSError:
        return True
    else:
        os.remove(probe)
        return False


def fail_closed(root: str) -> dict:
    """A ledger that cannot be used must stop the run, not be ignored.

    Two ways of making it unusable, because only one of them is portable:

      cannot_create   the ledger path's parent is a regular FILE, so `os.makedirs` raises on every filesystem.
                      This case always runs and always decides the verdict.
      unwritable      the claim directory is chmod 0500. Only meaningful where the filesystem enforces that;
                      where it does not, the case is reported as not applicable rather than as a failure.
    """
    checks: dict = {}

    # cannot_create: the parent of the ledger directory is a file.
    blocker = os.path.join(root, "not-a-directory")
    shutil.rmtree(blocker, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("this is a regular file, so a directory cannot be created underneath it\n")
    checks["cannot_create"] = _spend_in_child(os.path.join(blocker, "ledger"))
    checks["cannot_create"]["applicable"] = True

    # unwritable: only where chmod is enforced for the owner.
    ledger = os.path.join(root, "failclosed")
    shutil.rmtree(ledger, ignore_errors=True)
    claims = os.path.join(ledger, "claims")
    os.makedirs(claims, exist_ok=True)
    os.makedirs(os.path.join(ledger, "spend"), exist_ok=True)
    try:
        enforced = _chmod_is_enforced(claims)
        if enforced:
            checks["unwritable"] = {**_spend_in_child(ledger), "applicable": True}
        else:
            checks["unwritable"] = {
                "applicable": False,
                "why": "this filesystem does not deny the owner write access to a directory chmod-ed 0500 "
                       "(POSIX ACLs on /nfs/turbo), so the condition cannot be created here and the case "
                       "proves nothing either way",
            }
    finally:
        os.chmod(claims, 0o700)

    problems = []
    for name, c in checks.items():
        if c.get("applicable") and not c.get("refused"):
            problems.append(f"{name}: an unusable ledger still allowed {c.get('spent')} attempts")
    return {"checks": checks, "problems": problems, "verdict": "PASS" if not problems else "FAIL"}


# ── the MECHANISM, on this filesystem ────────────────────────────────────────

_MECHANISM = r"""
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("sl", %r)
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
d = os.environ["SKILLOPT_SPEND_LEDGER_DIR"]
CAP, BLOCK = 23, 2
claims = os.path.join(d, "claims")
for i in range(1, 12):
    with open(os.path.join(claims, "%%06d-target-attempts-2.json" %% i), "w") as f:
        json.dump({"amount": 2}, f)
stale = sorted(os.listdir(claims))
got_a = L._grant("target", "attempts", BLOCK, CAP, note="A")
with open(os.path.join(d, "release", "000001-target-attempts-1.json"), "w") as f:
    json.dump({"amount": 1}, f)
real = os.listdir
def staged(path):
    return list(stale) if os.path.abspath(path) == os.path.abspath(claims) else real(path)
os.listdir = staged
outcome, got_b = "granted", None
try:
    got_b = L._grant("target", "attempts", BLOCK, CAP, note="B")
except L.LedgerUnusable as exc:
    outcome, got_b = "refused_fail_closed", str(exc)[:200]
except L.BudgetExhausted:
    outcome = "refused_budget"
finally:
    os.listdir = real
net = L._granted().get(("target", "attempts"), 0)
print(json.dumps({"grant_a": got_a, "outcome": outcome, "grant_b": got_b, "net_granted": net, "cap": CAP}))
""" % (os.path.join(REPO, "skillopt", "model", "spend_ledger.py"),)


def mechanism(root: str) -> dict:
    """Reproduce the exact over-grant interleaving ON THIS FILESYSTEM, and require it to be refused.

    The four-process race is the wrong evidence on its own: it failed once (24 against a cap of 23) and
    then passed ten times running. This case is single-threaded and deterministic - it constructs the
    stale-claims-view-plus-fresh-release-view state directly - so it answers the question the race
    cannot: is the defect still reachable at all?
    """
    ledger = os.path.join(root, "mechanism")
    shutil.rmtree(ledger, ignore_errors=True)
    for sub in ("claims", "spend", "release"):
        os.makedirs(os.path.join(ledger, sub), exist_ok=True)
    env = {**os.environ, **_env(ledger, CAP), "SLURM_JOB_ID": "ledgercheck"}
    run = subprocess.run([sys.executable, "-I", "-c", _MECHANISM], capture_output=True, text=True, env=env)
    problems = []
    data = {}
    try:
        data = json.loads(run.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not read the mechanism probe: {exc}; stderr={run.stderr[-300:]}")
        return {"problems": problems, "verdict": "FAIL", "stdout": run.stdout[-500:],
                "stderr": run.stderr[-500:]}
    if data.get("net_granted", 0) > data.get("cap", CAP):
        problems.append(f"THE OVER-GRANT IS STILL REACHABLE: net {data['net_granted']} against a cap "
                        f"of {data['cap']}")
    if data.get("outcome") == "granted":
        problems.append("the racer with a stale claims view was GRANTED a block; it must fail closed")
    return {**data, "filesystem": _fstype(ledger), "problems": problems,
            "verdict": "PASS" if not problems else "FAIL",
            "what_this_shows": "the interleaving that produced '24 against a cap of 23' is constructed "
                               "directly on this filesystem and must be refused"}


def concurrency_repeated(root: str, n: int) -> dict:
    """The four-process race, n times. One pass means nothing; n passes bound the flake rate."""
    runs = []
    for i in range(n):
        r = concurrency(os.path.join(root, "rep%02d" % i))
        runs.append({"i": i, "total": r["total"], "per_process": r["per_process"],
                     "verdict": r["verdict"], "problems": r["problems"]})
    bad = [r for r in runs if r["verdict"] != "PASS"]
    return {"repeats": n, "failures": len(bad), "runs": runs,
            "problems": [f"run {r['i']}: {p}" for r in bad for p in r["problems"]],
            "verdict": "PASS" if not bad else "FAIL"}


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
    ap.add_argument("--repeats", type=int, default=12,
                    help="how many times to re-run the four-process race; one pass proves little")
    args = ap.parse_args()
    os.makedirs(args.root, exist_ok=True)
    checks = {"mechanism": mechanism(args.root),
              "concurrency": concurrency(args.root),
              "concurrency_repeated": concurrency_repeated(args.root, args.repeats),
              "restart": restart(args.root),
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
