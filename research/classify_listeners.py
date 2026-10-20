"""Classify the TCP listeners of the job-local vLLM server.

Input is `ss -ltnpH` output restricted to vLLM's process session, plus the
HTTP API port and vllm.log (for the torch.distributed rendezvous port that
vLLM logs as ``distributed_init_method=tcp://<host>:<port>``).

Each listener is classified as:
  loopback               bound to 127.0.0.0/8 or ::1
  rendezvous_tcpstore    NOT loopback, and its port is the logged rendezvous
                         port: torch's c10d TCPStore server, which binds all
                         interfaces and has no bind-address option on the
                         tcp:// rendezvous path (torch 2.11 TCPStoreOptions)
  unexpected             anything else

Overall status and exit code:
  pass        (0)  every listener is loopback, and the HTTP API is present
  unresolved  (3)  the only non-loopback listener is the rendezvous TCPStore.
                   This is NOT treated as allowed; callers must not proceed
                   to agent execution
  fail        (1)  any unexpected listener, the HTTP API not on loopback,
                   or no listeners at all

Usage:
  classify_listeners.py --ss-file F --http-port P --vllm-log L --out J
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys

_RENDEZVOUS = re.compile(r"distributed_init_method=tcp://[^\s:]+:(\d+)")


def split_addr(local: str) -> tuple[str, int]:
    host, _, port = local.rpartition(":")
    return host.strip("[]"), int(port)


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def rendezvous_port(vllm_log_text: str) -> int | None:
    m = _RENDEZVOUS.search(vllm_log_text)
    return int(m.group(1)) if m else None


def classify(ss_lines: list[str], http_port: int, rdzv_port: int | None) -> dict:
    listeners = []
    for line in ss_lines:
        parts = line.split()
        if len(parts) < 5 or parts[0] != "LISTEN":
            continue
        host, port = split_addr(parts[3])
        proc = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if is_loopback(host):
            kind = "loopback"
        elif rdzv_port is not None and port == rdzv_port:
            kind = "rendezvous_tcpstore"
        else:
            kind = "unexpected"
        listeners.append({
            "host": host,
            "port": port,
            "process": proc.group(1) if proc else None,
            "pid": int(proc.group(2)) if proc else None,
            "class": kind,
            "is_http_api": port == http_port,
        })

    reasons = []
    http = [x for x in listeners if x["is_http_api"]]
    if not listeners:
        reasons.append("no listeners found; cannot verify binding")
    if not http:
        reasons.append("HTTP API listener not found")
    if any(x["class"] != "loopback" for x in http):
        reasons.append("HTTP API is not loopback-only")
    unexpected = [x for x in listeners if x["class"] == "unexpected"]
    if unexpected:
        reasons.append(f"{len(unexpected)} unexpected non-loopback listener(s)")
    exceptions = [x for x in listeners if x["class"] == "rendezvous_tcpstore"]

    if reasons:
        status = "fail"
    elif exceptions:
        status = "unresolved"
        reasons.append("only the torch rendezvous TCPStore listens beyond loopback; not auto-allowed")
    else:
        status = "pass"
    return {
        "status": status,
        "reasons": reasons,
        "http_port": http_port,
        "rendezvous_port": rdzv_port,
        "counts": {k: sum(x["class"] == k for x in listeners) for k in ("loopback", "rendezvous_tcpstore", "unexpected")},
        "listeners": listeners,
    }


EXIT_CODES = {"pass": 0, "unresolved": 3, "fail": 1}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ss-file", required=True)
    p.add_argument("--http-port", type=int, required=True)
    p.add_argument("--vllm-log", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with open(a.ss_file, encoding="utf-8") as f:
        ss_lines = f.read().splitlines()
    with open(a.vllm_log, encoding="utf-8", errors="replace") as f:
        rdzv = rendezvous_port(f.read())
    result = classify(ss_lines, a.http_port, rdzv)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"listener status: {result['status']} counts={result['counts']} reasons={result['reasons']}")
    for x in result["listeners"]:
        print(f"  {x['class']:<20} {x['host']}:{x['port']} ({x['process']}){' [HTTP API]' if x['is_http_api'] else ''}")
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
