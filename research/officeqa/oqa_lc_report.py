"""The obligation-resolution report for one OfficeQA attempt, and its comparison with the plain report.

Two things distinguish this from `oqa_check.report`:

  1. It reports a REQUIREMENT with a RESOLUTION, not a finding. An obligation that the record discharges is printed
     as discharged, with the witness that discharged it, so a reader can see that the checker looked. The old
     report could only stay silent or complain, which is why it complained about a conversion the actor had
     already performed.
  2. Every line states what it rests on: TASK (the question sentence), TOOL (documented tool behaviour), OBS (a
     supplied page or a returned observation), ACTOR (something the target wrote, which is a claim and not
     evidence), RULE(id@v) (a named learned contract) or ASSUMED(name) (a named model assumption). A line that
     rests on an ASSUMED label can be rejected by a reader who rejects the assumption.

Grouping. The same requirement recurring over many values is ONE line plus a count, following the fix that cut the
arm-B reports from 27,570 to 15,910 characters (docs/OFFICEQA_COMPARISON_DESIGN.md). Fourteen operands converted
with the same wrong factor are one diagnosis, not fourteen.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oqa_check as CHK  # noqa: E402
import oqa_lc_chain as CHAIN  # noqa: E402
import oqa_obligations as OB  # noqa: E402

_ORDER = {CHAIN.VIOLATED: 0, CHAIN.OUTSTANDING: 1, CHAIN.UNKNOWN: 2, CHAIN.DISCHARGED: 3}
_MAX_LISTED = 12


def _class_of(item: dict) -> tuple:
    """What makes two obligation records "the same diagnosis": kind, state, and the reason with values removed."""
    skeleton = re.sub(r"-?[\d,]*\.?\d+", "#", item["why"])
    return (item["kind"], item["state"], skeleton)


def group(ledger: list) -> list:
    """One record per distinct diagnosis, carrying the subjects it applies to, most serious first."""
    buckets: dict = {}
    for item in ledger:
        key = _class_of(item)
        b = buckets.setdefault(key, {**item, "subjects": [], "n": 0})
        b["subjects"].append(item["subject"])
        b["n"] += 1
        if _ORDER[item["state"]] < _ORDER[b["state"]]:
            b.update({k: item[k] for k in ("state", "why", "witness", "supports")})
    out = list(buckets.values())
    out.sort(key=lambda b: (_ORDER[b["state"]], -b["n"], b["kind"]))
    return out


def render(chain: dict, model: dict) -> str:
    """The report an optimizer would receive for this attempt."""
    task = chain["task"]
    lines: list[str] = []
    lines.append(f"# Stage and action report (obligation form) - item {chain['id']}")
    lines.append("")
    lines.append(f"Model: v{model.get('version')} sha256 {str(model.get('sha256'))[:12]}, assumptions in force: "
                 f"{', '.join(model.get('assumptions') or []) or '(none)'}")
    lines.append("Every line below states its basis. TASK = the question sentence. TOOL = documented tool "
                 "behaviour. OBS = a supplied page or a returned observation. ACTOR = something the target wrote "
                 "down, which is a claim and not evidence. ASSUMED(x) = licensed by the named model assumption, so "
                 "a reader who rejects x should reject the line.")
    lines.append("")
    lines.append("## What the task requires (read from the question text only)")
    for line in OB.describe(task):
        lines.append(f"- {line}")
    lines.append("")

    bound = sum(1 for o in chain["operands"] if o["cell"] is not None)
    with_col = sum(1 for o in chain["operands"] if (o["cell"] or {}).get("column"))
    lines.append("## The value chain that was recorded")
    lines.append(f"- {len(chain['cells'])} numeric cells were available across the supplied pages and the returned "
                 f"observations.")
    lines.append(f"- the target wrote down {len(chain['operands'])} operand(s); {bound} matched an available cell "
                 f"and {with_col} of those have a determined column.")
    lines.append(f"- {len(chain['conversions'])} unit conversion(s) and {len(chain['computations'])} arithmetic "
                 f"step(s) appear in its messages.")
    ans = (chain["answer"] or "").strip()
    lines.append(f"- final answer: {ans[:120]!r}" if ans else "- final answer: none was produced.")
    lines.append("")

    groups = group(chain["ledger"])
    counts = CHAIN.summary(chain["ledger"])
    lines.append("## Requirements and how the record resolves them")
    lines.append(f"({', '.join(f'{k} {v}' for k, v in counts.items())})")
    lines.append("")
    for g in groups:
        head = f"- **{g['state']}** [{g['kind']}] {g['requires']}"
        if g["n"] > 1:
            head += f"  -- the same for {g['n']} value(s)"
        lines.append(head)
        lines.append(f"    {g['why']}")
        w = g.get("witness") or {}
        if w.get("column") or w.get("row_label"):
            lines.append(f"    witness: {w.get('raw')} in row {w.get('row_label')!r}, period {w.get('period')}, "
                         f"column {w.get('column')!r} [{w.get('column_basis')}], scale {w.get('scale')}")
        elif w.get("line"):
            lines.append(f"    witness: step {w.get('step')}: {w.get('line')}")
        lines.append(f"    basis: {', '.join(g['supports']) or '(none)'}")
        if g["n"] > 1:
            lines.append(f"    values: {', '.join(g['subjects'][:_MAX_LISTED])}"
                         + (f" and {g['n'] - _MAX_LISTED} more" if g["n"] > _MAX_LISTED else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── comparison with the plain report ────────────────────────────────────────────

def compare_with_plain(packet: dict, chain: dict) -> dict:
    """What the obligation report says that the plain report P does not, and the other way round.

    The plain report is `oqa_check.report(packet, "P")` unchanged, so the comparison is against the same control
    the two-arm run used.
    """
    plain = CHK.report(packet, "P")
    plain_cats = sorted({f["cat"] for f in plain["findings"]})
    groups = group(chain["ledger"])
    actionable = [g for g in groups if g["state"] in (CHAIN.VIOLATED, CHAIN.OUTSTANDING)]
    discharged = [g for g in groups if g["state"] == CHAIN.DISCHARGED]
    unknown = [g for g in groups if g["state"] == CHAIN.UNKNOWN]
    return {
        "item": chain["id"],
        "plain_report_categories": plain_cats,
        "plain_report_chars": len(CHK.render(plain, packet)),
        "obligation_report_chars": len(render(chain, {"version": chain["model"]["version"],
                                                      "assumptions": chain["model"]["assumptions"]})),
        "n_requirements": len(chain["ledger"]),
        "n_distinct_diagnoses": len(groups),
        "actionable": [{"kind": g["kind"], "state": g["state"], "n": g["n"], "why": g["why"][:240]}
                       for g in actionable],
        "discharged": [{"kind": g["kind"], "n": g["n"], "why": g["why"][:200]} for g in discharged],
        "unknown": [{"kind": g["kind"], "n": g["n"], "why": g["why"][:200]} for g in unknown],
    }
