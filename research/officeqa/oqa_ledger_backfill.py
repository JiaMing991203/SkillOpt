#!/usr/bin/env python3
"""Record the OQA-HTTP-1 probe's 23 sends in the shared spend ledger, exactly once. Rule OQA-REC-2.

WHY THIS IS NEEDED. The probe calls the OpenAI SDK directly - it must, to capture the raw HTTP body from
the same exchange the SDK parsed - so it bypasses the backend where the ledger lives. Its 23 sends were
accounted BY HAND in `docs/research/OFFICEQA_AUTHORIZATION.md`. The user's instruction for this follow-up
is that every send goes through the central accounting mechanism, including raw-response probes, and that
the earlier 23 are reconciled WITHOUT being charged twice.

WHAT IT DOES. It replays each saved exchange's recorded token usage through the real
`spend_ledger.reserve` and `record_usage`, under stage `oqa_http_probe_backfill`, so the ledger's own
audit view now contains them and the hand-kept figure and the ledger agree. It charges at THIS PROJECT's
price table, not at the provider's reported cost: the provider's figure is recorded beside it, and the
cap stays enforced on the computed charge, exactly as for a live call.

HOW DOUBLE CHARGING IS PREVENTED. The ledger's spend records are append-only and carry the stage name.
Before writing anything this tool counts the usage records already present under that stage; if any
exist it refuses and reports. So running it twice is a no-op with an explanation, not a second charge.

IT SENDS NOTHING. No network, no key. The attempts it records already happened.

usage:
  python3 -I research/officeqa/oqa_ledger_backfill.py --exchanges <dir> --ledger <dir> --out <file> [--apply]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

RULE = "OQA-REC-2"
STAGE = "oqa_http_probe_backfill"


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sends(exchanges_dir: str) -> list:
    """Every exchange in which a request was actually sent, with the usage the provider reported."""
    out = []
    for path in sorted(glob.glob(os.path.join(exchanges_dir, "EXCHANGES_*.json"))):
        doc = json.load(open(path, encoding="utf-8"))
        for r in doc.get("records") or []:
            if r.get("skipped") or not r.get("http"):
                continue
            body = (r.get("http") or {}).get("body")
            usage = (body or {}).get("usage") if isinstance(body, dict) else None
            usage = usage or {}
            details = usage.get("prompt_tokens_details") or {}
            out.append({
                "file": os.path.basename(path),
                "label": r.get("label"), "rep": r.get("rep"), "setting": r.get("setting"),
                "response_id": (body or {}).get("id") if isinstance(body, dict) else None,
                "utc": r.get("utc"),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "cached_tokens": int(details.get("cached_tokens") or 0),
                "provider_cost_usd": usage.get("cost_usd"),
            })
    return out


def already_present(ledger_dir: str) -> int:
    """Usage records already in the ledger under this stage. Non-zero means the backfill has run."""
    n = 0
    root = os.path.join(ledger_dir, "spend")
    for name in sorted(glob.glob(os.path.join(root, "*.jsonl"))):
        with open(name, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("stage") == STAGE and rec.get("kind") == "usage":
                    n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exchanges", required=True, help="the http_probe directory with EXCHANGES_*.json")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--apply", action="store_true", help="write to the ledger (otherwise report only)")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    os.environ["SKILLOPT_SPEND_LEDGER_DIR"] = args.ledger
    from skillopt.model import spend_ledger as L

    rows = sends(args.exchanges)
    present = already_present(args.ledger)
    doc = {"rule": RULE, "utc": _utc(), "stage": STAGE, "ledger": os.path.abspath(args.ledger),
           "exchanges_dir": os.path.abspath(args.exchanges),
           "n_sends_found": len(rows),
           "already_in_ledger": present,
           "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
           "completion_tokens": sum(r["completion_tokens"] for r in rows),
           "cached_prompt_tokens_the_provider_reports": sum(r["cached_tokens"] for r in rows),
           "provider_cost_usd": round(sum(float(r["provider_cost_usd"] or 0.0) for r in rows), 8),
           # WHAT THE BACKFILL CHARGES, and why it is the larger of two figures.
           #
           # The backend computes its charge with `cached_tokens=prompt_cache_hit_tokens`, which is
           # DEEPSEEK's field name. EmpirioLabs reports its cache hits as
           # `usage.prompt_tokens_details.cached_tokens` (and `cache_read_input_tokens`), so the backend
           # sees 0 and bills every prompt token at the full price. That is why the ledger charged
           # $5.3159 across the campaign where the provider charged $3.2768: the ledger is conservative
           # by about 38% on the actor side, which is the safe direction for a cap.
           #
           # The backfill therefore charges AS THE BACKEND WOULD HAVE - cache ignored - so that a
           # back-filled attempt and a live one are charged identically and the recorded cumulative
           # figure stays an upper bound. The cache-aware figure is reported beside it, never used.
           "project_price_usd": round(sum(
               L.cost_usd("target", r["prompt_tokens"], r["completion_tokens"], 0) for r in rows), 8),
           "project_price_usd_if_cache_were_accounted": round(sum(
               L.cost_usd("target", r["prompt_tokens"], r["completion_tokens"], r["cached_tokens"])
               for r in rows), 8),
           "charge_basis": ("this project's price table with the cache IGNORED, which is how the "
                            "backend charges a live call for this provider"),
           "sends": rows, "applied": False}
    if present:
        doc["refused"] = (f"{present} usage record(s) already exist under stage {STAGE}: the backfill has "
                          f"run. Refusing, so the same sends cannot be charged twice.")
    elif args.apply:
        charged = 0.0
        for r in rows:
            L.reserve("target", STAGE)
            # cached_tokens=0 on purpose: see `charge_basis` above.
            charged += L.record_usage("target", STAGE, r["prompt_tokens"], r["completion_tokens"],
                                      cached_tokens=0,
                                      provider_cost_usd=r["provider_cost_usd"])
        released = L.release(reason="probe backfill complete")
        doc["applied"] = True
        doc["charged_usd"] = round(charged, 8)
        doc["released"] = released
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(f"rule {RULE}  {doc['n_sends_found']} probe send(s); already in ledger {present}")
    print(f"  provider ${doc['provider_cost_usd']:.6f}; this project's price table "
          f"${doc['project_price_usd']:.6f} (cache ignored, as the backend charges) / "
          f"${doc['project_price_usd_if_cache_were_accounted']:.6f} if cache were accounted")
    print(f"  the provider reports {doc['cached_prompt_tokens_the_provider_reports']} cached prompt "
          f"token(s) over {doc['prompt_tokens']} prompt token(s)")
    if doc.get("refused"):
        print(f"  REFUSED: {doc['refused']}")
    elif doc["applied"]:
        print(f"  applied: charged ${doc['charged_usd']:.6f} over {doc['n_sends_found']} attempts")
    else:
        print("  report only (pass --apply to write)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
