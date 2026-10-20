#!/usr/bin/env bash
# Probe a running STAGE=vllm job's listeners from OUTSIDE its compute node.
#
#   research/probe_listeners.sh <job-id>
#
# Waits until the job writes probe_window.json, then, from a login node, tries
# a plain TCP connect (no payload) to every listener port on each of the
# node's addresses. Loopback-bound ports serve as controls (expected: refused).
# Results go to probe_from_login.json in the job's run dir. Only our own job's
# ports are contacted. Changes nothing else.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/cluster/site.env"
J="${1:?usage: probe_listeners.sh <job-id>}"

# shellcheck disable=SC2087
ssh "$SSH_HOST" bash -s <<EOF
set -euo pipefail
for _ in \$(seq 1 240); do
    R=\$(ls -d "$RUN_ROOT"/*_"$J" 2>/dev/null | head -1 || true)
    if [ -n "\$R" ] && [ -f "\$R/probe_window.json" ]; then break; fi
    state=\$(squeue -h -j "$J" -o %T 2>/dev/null || true)
    if [ -z "\$state" ] && [ -n "\$R" ]; then echo "job $J ended without opening a probe window"; exit 2; fi
    sleep 15
done
[ -f "\$R/probe_window.json" ] || { echo "timed out waiting for probe window"; exit 2; }
python3 - "\$R" <<'PY'
import json, os, socket, sys, time
run = sys.argv[1]
w = json.load(open(os.path.join(run, "probe_window.json")))
results = []
for ip in w["node_ips"]:
    for l in w["listeners"]:
        s = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET)
        s.settimeout(3)
        try:
            s.connect((ip, l["port"]))
            outcome = "OPEN (connected)"
        except ConnectionRefusedError:
            outcome = "refused"
        except socket.timeout:
            outcome = "timeout (filtered?)"
        except OSError as e:
            outcome = "error: %s" % e.strerror
        finally:
            s.close()
        results.append({"target_ip": ip, "port": l["port"], "bound_to": l["host"],
                        "class": l["class"], "is_http_api": l["is_http_api"], "outcome": outcome})
out = {"probed_from": socket.gethostname(), "probed_at": time.time(),
       "window_closes_at": w["closes_at"], "results": results}
json.dump(out, open(os.path.join(run, "probe_from_login.json"), "w"), indent=2)
for r in results:
    print("%-16s port %-6s bound %-12s %-20s -> %s%s" % (r["target_ip"], r["port"], r["bound_to"], r["class"],
          r["outcome"], " [HTTP API]" if r["is_http_api"] else ""))
PY
EOF
