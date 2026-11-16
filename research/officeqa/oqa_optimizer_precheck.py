"""Verify the optimizer endpoint before a paid run, without making a paid call.

Why this exists. A misrouted role is the failure that already cost this study a GPU hour: the tool probe called
the target without selecting a backend and the request went to Azure instead of the job-local vLLM server (job
66171008). The rule that came out of it is to check the base URL and the model id before every paid request, and
never to let a library default decide. `research/preflight_endpoints.py --optimizer` does that and then sends two
tiny probe calls; those probes are real DeepSeek calls, and this comparison is capped at 24 of them, so here the
same checks are made with the paid part removed:

  * the base URL and model id come from the config the run will actually use, not from a default;
  * the model id must appear in GET /models, which is free and unauthenticated-by-key-only;
  * SkillOpt's own backend is then configured from those values and its resolved settings are printed, so the
    recorded manifest shows what the run will send.

It also prints the call budget the run is expected to need, from the number of failures a rollout produced, so a
budget overrun can be surfaced BEFORE the first paid call rather than discovered afterwards.

usage: python3 oqa_optimizer_precheck.py --config configs/research/officeqa_cmp.yaml --out precheck.json
                                         [--rollout <shared rollout dir>] [--budget 24] [--steps 4]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

EXPECTED_BASE_URL = "https://api.deepseek.com"


def list_models(base_url: str, api_key: str, timeout: int = 20) -> list[str]:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models",
                                     headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https endpoint
        payload = json.loads(response.read().decode())
    return sorted(str(m.get("id")) for m in payload.get("data", []) if m.get("id"))


def expected_calls(n_failures: int, n_successes: int, minibatch_size: int, steps: int) -> dict:
    """How many optimizer calls one step needs, and the whole run.

    Per step, from skillopt/gradient/reflect.py and skillopt/gradient/aggregate.py:
      analyst      ceil(failures / M) + ceil(successes / M)        one call per minibatch
      merge        0 if a group has one patch (it is returned as is), else ceil(groups / merge_batch) per level
      final merge  1 when both groups produced edits, else 0
      ranking      1 only when the merged pool exceeds the edit budget (optimizer/clip.py returns early otherwise)
    The upper bound assumes every optional call happens.
    """
    fail_batches = math.ceil(n_failures / minibatch_size) if n_failures else 0
    succ_batches = math.ceil(n_successes / minibatch_size) if n_successes else 0
    analyst = fail_batches + succ_batches
    merge = (1 if fail_batches > 1 else 0) + (1 if succ_batches > 1 else 0)
    final_merge = 1 if (fail_batches and succ_batches) else 0
    per_step_min = analyst + merge + final_merge
    per_step_max = per_step_min + 1  # the ranking call
    return {"failures": n_failures, "successes": n_successes, "minibatch_size": minibatch_size,
            "analyst_calls": analyst, "merge_calls": merge, "final_merge_calls": final_merge,
            "per_step_min": per_step_min, "per_step_max": per_step_max, "steps": steps,
            "run_min": per_step_min * steps, "run_max": per_step_max * steps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rollout", help="a rollout directory, to count the failures the first step will reflect on")
    ap.add_argument("--budget", type=int, default=24, help="the approved number of optimizer calls")
    ap.add_argument("--steps", type=int, default=4, help="steps in the whole comparison (arms x epochs)")
    args = ap.parse_args()

    from skillopt.config import flatten_config, load_config
    from skillopt.model import openai_compatible_backend as oc

    cfg = flatten_config(load_config(args.config))
    base = str(cfg.get("optimizer_openai_compatible_base_url") or "").rstrip("/")
    requested = os.environ.get("OPTIMIZER_MODEL", "").strip()
    key = os.environ.get("OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", "")
    report: dict = {"config": os.path.abspath(args.config), "base_url": base, "requested_model": requested,
                    "paid_calls_made": 0, "problems": []}

    if base != EXPECTED_BASE_URL:
        report["problems"].append(f"base URL is {base!r}, expected {EXPECTED_BASE_URL!r}")
    if not requested:
        report["problems"].append("OPTIMIZER_MODEL is empty; the backend would fall back to a library default")
    if str(cfg.get("optimizer_backend")) != "openai_compatible":
        report["problems"].append(f"optimizer_backend is {cfg.get('optimizer_backend')!r}, expected "
                                  f"'openai_compatible'")
    if not key:
        report["problems"].append("OPTIMIZER_OPENAI_COMPATIBLE_API_KEY is not set")

    if not report["problems"]:
        try:
            report["available_models"] = list_models(base, key)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            report["problems"].append(f"GET {base}/models failed: {type(exc).__name__}: {exc}")
        else:
            if requested not in report["available_models"]:
                report["problems"].append(f"model {requested!r} is not listed by the endpoint")

    if not report["problems"]:
        oc.configure_openai_compatible(
            optimizer_base_url=base,
            optimizer_model=requested,
            optimizer_max_tokens=cfg.get("optimizer_openai_compatible_max_tokens"),
        )
        report["backend_settings"] = {"model": oc.OPTIMIZER_CONFIG.deployment,
                                      "base_url": oc.OPTIMIZER_CONFIG.base_url,
                                      "max_tokens_cap": oc.OPTIMIZER_CONFIG.max_tokens}

    if args.rollout:
        rows = []
        path = os.path.join(args.rollout, "results.jsonl")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        failures = sum(1 for r in rows if not float(r.get("hard") or 0))
        budget = expected_calls(failures, len(rows) - failures, int(cfg.get("minibatch_size", 8) or 8), args.steps)
        budget["approved_budget"] = args.budget
        budget["fits"] = budget["run_max"] <= args.budget
        report["call_budget"] = budget
        if not budget["fits"]:
            report["problems"].append(
                f"the comparison could need up to {budget['run_max']} optimizer calls, above the approved "
                f"{args.budget}: surface this before the first paid call")

    report["verdict"] = "FAIL" if report["problems"] else "PASS"
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    printable = dict(report)
    printable.pop("available_models", None)
    printable["n_available_models"] = len(report.get("available_models", []))
    print(json.dumps(printable, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
