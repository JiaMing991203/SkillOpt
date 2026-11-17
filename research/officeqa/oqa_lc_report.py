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

THE THREE ARMS (added for the three-arm API comparison; see docs/research/OFFICEQA_THREE_ARM_LAUNCH.md).

  arm A  "Original SkillOpt"           no report at all: the analyst prompt is byte-identical to upstream.
  arm B  "Action Contracts Only"       `render_action` alone - the learned action contracts in force, what each
                                       one forecast for this attempt's tool calls, what the observations showed,
                                       and the assumption checks. Nothing about stages: no task requirement, no
                                       value chain, no obligation, no action-to-stage deduction.
  arm C  "Action Contracts + Stages"   `render_action` followed by the stage sections. Arm C's report CONTAINS
                                       arm B's, line for line, so the only difference between the two arms is the
                                       stage material. `audit_action_only` and `audit_containment` check both
                                       halves of that claim on the rendered text, not on the intention.

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
import oqa_lc_ir as IR  # noqa: E402
import oqa_lc_replay as REPLAY  # noqa: E402
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


# ── arm B: the action-contract half ─────────────────────────────────────────────

# What arm B must never print. Two lists, because they do different jobs.
#
# STAGE_MARKERS are strings that can only have come from the stage renderer: its section headings and the
# snake_case names of the obligation kinds and states. Any of these in an Action-Only report means the wrong
# report was built, so `audit_action_only` FAILS and `oqa_reports.build_one_lc` refuses to hand it over.
#
# ADVISORY_WORDS are ordinary English words that stage material tends to use. They are REPORTED but do not fail
# the audit, because the report quotes column headings and row labels out of the documents and a Treasury
# Bulletin may legitimately contain the word "stage" or "conversion". Failing on those would let one word in a
# scanned table abort a whole arm of the comparison, which is a worse outcome than a reviewer reading an
# advisory line.
STAGE_MARKERS = (
    "## What the task requires", "## The value chain that was recorded",
    "## Requirements and how the record resolves them", "# Stage report", "# Stage and action report",
    "cell_bound", "period_match", "unit_match", "arith_recorded", "answer_traced",
    "DISCHARGED", "OUTSTANDING",
)
ADVISORY_WORDS = ("stage", "obligation", "value chain", "requirement", "operand", "provenance", "final answer")


def _forecast_rows(replay: dict) -> list:
    """One row per (contract, atom, prediction, observation, verdict), with the steps it happened at."""
    buckets: dict = {}
    for rec in replay.get("attempts") or []:
        for f in rec.get("forecast_checks") or []:
            key = (f["contract"], f.get("version"), f["atom"], bool(f["predicted"]), f["observed"], f["class"])
            b = buckets.setdefault(key, {"contract": f["contract"], "version": f.get("version"),
                                         "atom": f["atom"], "predicted": bool(f["predicted"]),
                                         "observed": f["observed"], "class": f["class"], "steps": [],
                                         "tools": set(), "witnesses": []})
            b["steps"].append(rec["step"])
            b["tools"].add(rec["tool"])
            if f["class"] == REPLAY.FORECAST_VIOLATED:
                b["witnesses"] += rec.get("witnesses") or []
    order = {REPLAY.FORECAST_VIOLATED: 0, REPLAY.FORECAST_UNRESOLVED: 1, REPLAY.FORECAST_CONFIRMED: 2}
    rows = list(buckets.values())
    rows.sort(key=lambda r: (order.get(r["class"], 3), -len(r["steps"]), r["atom"]))
    return rows


def _basis_of(contract_id: str) -> str:
    """A forecast made by a named contract rests on that rule; one made by an assumption rests on the assumption."""
    return contract_id if contract_id.startswith("ASSUMED(") else f"RULE({contract_id})"


def render_action(replay: dict, model: dict) -> str:
    """ARM B's report: the learned action contracts, their forecasts for this attempt, and the observations.

    Reads nothing but the frozen model and the replay of one recorded attempt. It never mentions a task
    requirement, a value binding or an answer, so an edit it supports can only be an edit about how to call the
    tools - which is what separates this arm from arm C.
    """
    lines: list[str] = []
    lines.append(f"# Action contract report - item {replay.get('item')}")
    lines.append("")
    lines.append(f"Model: v{model.get('version')} sha256 {str(model.get('sha256'))[:12]}, assumptions in force: "
                 f"{', '.join(model.get('assumptions') or []) or '(none)'}")
    lines.append("Every line states what it rests on. TOOL = documented tool behaviour. OBS = an observation a "
                 "tool call returned. RULE(id@v) = a named learned contract, with the version that made the "
                 "claim. ASSUMED(x) = licensed by the named model assumption, so a reader who rejects x should "
                 "reject the line. A forecast is a claim recorded BEFORE the observation was read; it never "
                 "becomes a fact by itself.")
    # An item whose own trace is part of the evidence this model was learned from cannot be predicted by it. The
    # arms' epoch-1 model is learned on the shared epoch-1 rollout, so epoch 1 is exactly this case, and saying so
    # in the report is cheaper than hoping a later reader remembers.
    if str(replay.get("item")) in set(model.get("evidence_items") or []):
        lines.append("")
        lines.append("NOTE: this item's own trace is part of the evidence this model was learned from "
                     f"({model.get('evidence_scope') or 'see the model record'}). The forecasts below are "
                     "in-sample: they were induced to hold of these very observations, so a confirmation here is "
                     "not a successful prediction.")
    lines.append("")

    act = [c for c in IR.active(model)]
    # Every way a contract can leave the active set, not just "refuted". `mech_repair` RETIRES a contract it
    # cannot revise, and `apply_proposal` marks the old version SUPERSEDED when a revision replaces it. Listing
    # only the refuted ones hid exactly the case the repair schedule produces most often.
    _WITHDRAWN = {"refuted": "withdrawn", "retired": "retired", "superseded": "superseded by a later version"}
    ref = [c for c in model.get("contracts") or [] if c.get("status") in _WITHDRAWN]
    lines.append("## The learned action contracts in force")
    if not act:
        lines.append("- none: no contract has survived the evidence so far, so nothing below was forecast by a "
                     "rule.")
    for c in act:
        ev = c.get("evidence") or {}
        lines.append(f"- {_basis_of(str(c.get('id')) + '@v' + str(c.get('version')))}: {IR.render(c)}")
        lines.append(f"    evidence when it was frozen: {ev.get('n_support_ok', 0)} supporting step(s) over "
                     f"{len(ev.get('items_supporting') or [])} item(s), {ev.get('n_counter', 0)} "
                     f"counterexample(s), {ev.get('undetermined', 0)} step(s) the records cannot settle")
        if c.get("reason"):
            lines.append(f"    why it was proposed: {c['reason']}")
    if ref:
        lines.append("")
        lines.append("Retracted, and shown so that an edit is not written from a rule that no longer holds:")
        for c in ref:
            lines.append(f"- {_basis_of(str(c.get('id')) + '@v' + str(c.get('version')))}: {IR.render(c)}")
            why = (c.get("refute_reason") or c.get("retire_reason")
                   or (f"version {c.get('version')} was replaced" if c.get("status") == "superseded" else "")
                   or "(no reason recorded)")
            lines.append(f"    {_WITHDRAWN[c['status']]}: {why}")
    lines.append("")

    rows = _forecast_rows(replay)
    n_steps = len(replay.get("attempts") or [])
    covered = sum(1 for r in (replay.get("attempts") or []) if r.get("forecast_checks"))
    lines.append("## What those contracts forecast for this attempt's tool calls, and what came back")
    lines.append(f"({n_steps} tool call(s) recorded; {covered} of them had at least one forecast)")
    lines.append("")
    if not rows:
        lines.append("- no forecast applied to any tool call in this attempt.")
    for r in rows:
        verdict = {REPLAY.FORECAST_CONFIRMED: "CONFIRMED", REPLAY.FORECAST_VIOLATED: "VIOLATED",
                   REPLAY.FORECAST_UNRESOLVED: "UNRESOLVED"}[r["class"]]
        steps = ", ".join(str(s) for s in r["steps"][:_MAX_LISTED])
        more = f" and {len(r['steps']) - _MAX_LISTED} more" if len(r["steps"]) > _MAX_LISTED else ""
        obs = "(the records cannot settle it)" if r["observed"] is None else str(bool(r["observed"])).lower()
        lines.append(f"- **{verdict}** {r['atom']}: forecast {str(r['predicted']).lower()}, observed {obs}"
                     + (f"  -- at {len(r['steps'])} tool call(s)" if len(r["steps"]) > 1 else ""))
        lines.append(f"    at step(s) {steps}{more}, tool {'/'.join(sorted(r['tools']))}")
        # Name the exact contract VERSION that made the claim: a report is only attributable if the rule it
        # rests on can be looked up at the version in force when the forecast was committed.
        named = (r["contract"] if r["contract"].startswith("ASSUMED(")
                 else f"{r['contract']}@v{r['version']}" if r.get("version") else r["contract"])
        lines.append(f"    basis: {_basis_of(named)}, OBS")
        for w in r["witnesses"][:4]:
            lines.append(f"    witness: column {w.get('index')} of a {w.get('width')}-wide table - merging the "
                         f"supplied page's split header by index names {w.get('merged_leaf')!r}, the flattened "
                         f"header this call returned names {w.get('flat_leaf')!r}")
    lines.append("")

    sc = REPLAY.score(replay.get("attempts") or [])
    lines.append("## How the model did on this attempt")
    lines.append(f"- forecasts: {sc['forecasts']['confirmed']} confirmed, {sc['forecasts']['violated']} violated, "
                 f"{sc['forecasts']['unresolved']} the records cannot settle.")
    lines.append(f"- condition-based predictions that the records could determine: "
                 f"{sc['determinate_condition_predictions']} of {sc['steps']} tool call(s). Every atom in this "
                 f"vocabulary is a property of the observation a call has not returned yet, so a condition "
                 f"cannot be valued before the call; this number is expected to be low and is not a defect.")
    viol = [r for r in rows if r["class"] == REPLAY.FORECAST_VIOLATED]
    if viol:
        assumed = sorted({r["contract"][len("ASSUMED("):-1] for r in viol if r["contract"].startswith("ASSUMED(")})
        if assumed:
            lines.append(f"- the assumption(s) {', '.join(assumed)} were contradicted by this attempt's own "
                         f"observations. Any line resting on them should be read as unsettled.")
    return "\n".join(lines).rstrip() + "\n"


def audit_action_only(text: str) -> dict:
    """Refuse an Action-Only report that carries stage material. Checked on the RENDERED text, not the intention.

    `stage_words_found` lists the STAGE_MARKERS, which fail the audit. `advisory` lists the generic words, which
    do not: see the note on the two lists above.
    """
    low = text.lower()
    hits = sorted({m for m in STAGE_MARKERS if m.lower() in low})
    advisory = sorted({w for w in ADVISORY_WORDS if w in low})
    return {"verdict": "FAIL" if hits else "PASS", "stage_words_found": hits, "advisory": advisory,
            "chars": len(text)}


def audit_containment(action_text: str, full_text: str) -> dict:
    """Arm C's report must contain arm B's line for line, so the arms differ by the stage material and nothing else.

    Without this, a change to `render_action` could leave the two arms differing in the action half as well, and
    the comparison would no longer isolate the stage contribution.
    """
    a = [l for l in action_text.splitlines() if l.strip()]
    f = set(l for l in full_text.splitlines() if l.strip())
    missing = [l for l in a if l not in f]
    return {"verdict": "FAIL" if missing else "PASS", "action_lines": len(a),
            "lines_missing_from_full": missing[:8], "n_missing": len(missing)}


def render(chain: dict, model: dict, replay: dict | None = None) -> str:
    """The report an optimizer would receive for this attempt.

    With `replay` supplied this is ARM C: `render_action`'s text verbatim, then the stage half. Without it, the
    stage half alone, which is what the earlier offline work and the tests already compare against.
    """
    task = chain["task"]
    lines: list[str] = []
    if replay is not None:
        lines.append(render_action(replay, model).rstrip())
        lines.append("")
        lines.append("# Stage report (obligation form) - item " + str(chain["id"]))
        lines.append("")
        lines.append("The section above is about the tool calls alone. This one links a value to where it came "
                     "from, to the period and unit it carries, and to the answer that was produced from it.")
        lines.append("")
    else:
        lines.append(f"# Stage and action report (obligation form) - item {chain['id']}")
        lines.append("")
    if replay is None:
        lines.append(f"Model: v{model.get('version')} sha256 {str(model.get('sha256'))[:12]}, assumptions in "
                     f"force: {', '.join(model.get('assumptions') or []) or '(none)'}")
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
