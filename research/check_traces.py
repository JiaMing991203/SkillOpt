"""Check real trace.json files against the unchanged SkillOpt outputs.

For every episode in <out_root>/results.jsonl with a trace.json:
  consistency  trace steps == conversation steps; per step raw_response ==
               model_response, parsed_action == action, env_feedback,
               reward and done equal; final.hard == results hard
  projection   executed_action == lowercase(parsed_action) for parsed steps
               (the vendored projection lowercases the first <action> span);
               env_is_action_valid == projection_valid
  evidence     initial observation present; admissible lists present and
               non-empty; observation_before present for every step
Also counts executed actions that were not in the admissible list.

Writes a JSON report; exit 0 if every check passes, 1 otherwise.
Usage: python research/check_traces.py <out_root> --out trace_check.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def check_episode(result: dict, conv: list[dict], trace: dict) -> dict:
    problems = []
    steps = trace.get("steps", [])
    if len(steps) != len(conv):
        problems.append(f"step count trace={len(steps)} conversation={len(conv)}")
    for t, c in zip(steps, conv):
        for tk, ck in (("raw_response", "model_response"), ("parsed_action", "action"),
                       ("env_feedback", "env_feedback"), ("reward", "reward"), ("done", "done")):
            if t.get(tk) != c.get(ck):
                problems.append(f"step {c.get('step')}: {tk} != conversation {ck}")
        parsed = t.get("parsed_action")
        if parsed is not None and t.get("executed_action") != parsed.strip().lower():
            problems.append(f"step {c.get('step')}: executed {t.get('executed_action')!r} != lower(parsed)")
        if t.get("env_is_action_valid") is not None and bool(t["env_is_action_valid"]) != bool(t.get("projection_valid")):
            problems.append(f"step {c.get('step')}: env validity != projection validity")
        if not t.get("observation_before"):
            problems.append(f"step {c.get('step')}: no observation_before")
        if not t.get("admissible_actions"):
            problems.append(f"step {c.get('step')}: no admissible actions")
    final = trace.get("final", {})
    if final.get("hard") != result.get("hard"):
        problems.append(f"final.hard {final.get('hard')} != results hard {result.get('hard')}")
    if not trace.get("initial_observation"):
        problems.append("no initial_observation")
    if not trace.get("initial_admissible_actions"):
        problems.append("no initial_admissible_actions")
    return {
        "id": result.get("id"),
        "hard": result.get("hard"),
        "n_steps": len(steps),
        "executed_not_admissible": sum(1 for t in steps if t.get("executed_action_admissible") is False),
        "admissible_list_sizes": [len(t.get("admissible_actions") or []) for t in steps],
        "ok": not problems,
        "problems": problems,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("out_root")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with open(os.path.join(a.out_root, "results.jsonl"), encoding="utf-8") as f:
        results = [json.loads(line) for line in f if line.strip()]
    episodes = []
    for r in results:
        d = os.path.join(a.out_root, "predictions", str(r["id"]))
        tpath = os.path.join(d, "trace.json")
        if not os.path.exists(tpath):
            episodes.append({"id": r["id"], "ok": False, "problems": ["trace.json missing"]})
            continue
        with open(os.path.join(d, "conversation.json"), encoding="utf-8") as f:
            conv = json.load(f)
        with open(tpath, encoding="utf-8") as f:
            trace = json.load(f)
        episodes.append(check_episode(r, conv, trace))
    report = {"n_episodes": len(episodes), "all_ok": all(e["ok"] for e in episodes), "episodes": episodes}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"trace check: all_ok={report['all_ok']} episodes={len(episodes)}")
    for e in episodes:
        print(f"  {e['id']}: ok={e['ok']} steps={e.get('n_steps')} not_admissible={e.get('executed_not_admissible')} "
              f"{'; '.join(e['problems'][:3])}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
