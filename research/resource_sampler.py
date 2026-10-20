"""Sample the job's own cgroup memory and CPU time into a CSV.

Reads /proc/self/cgroup to find this process's cgroup (the Slurm job/step
cgroup, which contains every process the batch script starts) and samples:
  mem_bytes      current memory charged to the cgroup (RSS + page cache)
  mem_peak_bytes cgroup's recorded peak, if the kernel exposes it
  anon_bytes     process (non-reclaimable) memory from memory.stat
  file_bytes     file page cache (e.g. model weights read from disk; reclaimable)
  cpu_seconds    cumulative CPU time of all processes in the cgroup
Supports cgroup v1 (memory / cpuacct controllers) and v2 (unified).

Usage: python research/resource_sampler.py --out FILE [--interval 5]
Stop it with SIGTERM; every row is flushed as it is written.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time


def cgroup_files() -> dict:
    paths = {}
    with open("/proc/self/cgroup", encoding="utf-8") as f:
        for line in f:
            _, controllers, path = line.strip().split(":", 2)
            if controllers == "":  # v2 unified
                base = os.path.join("/sys/fs/cgroup", path.lstrip("/"))
                paths.setdefault("mem", os.path.join(base, "memory.current"))
                paths.setdefault("mem_peak", os.path.join(base, "memory.peak"))
                paths.setdefault("cpu_v2", os.path.join(base, "cpu.stat"))
                paths.setdefault("stat", os.path.join(base, "memory.stat"))
                paths.setdefault("stat_keys", "anon,file")
            for c in controllers.split(","):
                if c == "memory":
                    base = os.path.join("/sys/fs/cgroup/memory", path.lstrip("/"))
                    paths["mem"] = os.path.join(base, "memory.usage_in_bytes")
                    paths["mem_peak"] = os.path.join(base, "memory.max_usage_in_bytes")
                    paths["stat"] = os.path.join(base, "memory.stat")
                    paths["stat_keys"] = "total_rss,total_cache"
                elif c == "cpuacct":
                    paths["cpu_v1"] = os.path.join("/sys/fs/cgroup/cpu,cpuacct", path.lstrip("/"), "cpuacct.usage")
    return paths


def read_int(path: str | None) -> int | None:
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip().split()[0])
    except (OSError, ValueError, TypeError, IndexError):
        return None


def read_mem_stat(paths: dict) -> tuple[int | None, int | None]:
    anon_key, file_key = paths.get("stat_keys", "anon,file").split(",")
    vals = {}
    try:
        with open(paths["stat"], encoding="utf-8") as f:
            for line in f:
                k, v = line.split()
                vals[k] = int(v)
    except (OSError, KeyError, ValueError):
        return None, None
    return vals.get(anon_key, vals.get("rss")), vals.get(file_key, vals.get("cache"))


def read_cpu_seconds(paths: dict) -> float | None:
    if "cpu_v1" in paths:
        ns = read_int(paths["cpu_v1"])
        if ns is None and os.path.exists("/sys/fs/cgroup/cpuacct"):
            ns = read_int(paths["cpu_v1"].replace("/cpu,cpuacct/", "/cpuacct/"))
        if ns is not None:
            return ns / 1e9
    if "cpu_v2" in paths:
        try:
            with open(paths["cpu_v2"], encoding="utf-8") as f:
                for line in f:
                    k, v = line.split()
                    if k == "usage_usec":
                        return int(v) / 1e6
        except OSError:
            return None
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=5.0)
    a = p.parse_args()
    paths = cgroup_files()
    running = {"on": True}
    signal.signal(signal.SIGTERM, lambda *_: running.update(on=False))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(f"# cgroup files: {sorted(paths.items())}\n")
        f.write("unix_time,mem_bytes,mem_peak_bytes,anon_bytes,file_bytes,cpu_seconds\n")
        f.flush()
        while running["on"]:
            anon, cache = read_mem_stat(paths)
            row = (time.time(), read_int(paths.get("mem")), read_int(paths.get("mem_peak")), anon, cache,
                   read_cpu_seconds(paths))
            f.write(",".join("" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v)) for v in row) + "\n")
            f.flush()
            time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
