"""OfficeQA execution checking: evidence ledger, stage model, and the three report conditions P / S / C.

Lineage. This re-implements, over documents instead of receptacles, the semantics of the learned-contract
prototype. Those reference files are kept locally and are NOT committed here (they are another branch's
uncommitted work); their hashes and provenance are in the local OFFICEQA_TRANSFER_MANIFEST.json:
  * plain_v4.py  - P is assessed at PRE of the SELECTED step, with the same opportunity as S (amendment A3 3.3).
                   NOTE (neutral information) is never scored as ADVICE (A3 3.2).
  * stage_contracts.py - S = P plus an ordered procedure: which stage is open, what it requires, what is bound to
                   it, which later stage consumes its result, and the claim that a step cannot discharge it.
  * score_v4.py  - the comparison unit is the FIRST correct and appropriately timed message per problem, and an
                   extended condition must CONTAIN its base with identical first prefixes (structural_check).

Conditions (frozen here; see docs/OFFICEQA_FEASIBILITY_PROTOCOL.md section 5):
  P  plain progress report: obligations from the question, per-step checks of the selected tool call, and whether the
     final answer is grounded in / derivable from the evidence that was actually returned. Order-free: P never binds
     an operand to a source, never states which stage is open, and never carries an obligation across steps.
  S  P + the ordered stage procedure and its handoffs (locate_span -> extract_operands -> compute -> format_answer),
     with per-operand source bindings.
  C  S + action contracts over the document tools, whose named assumptions license inferences that S must leave
     UNKNOWN (for example: a table's stated scale applies to the numbers in its rows).

Trust boundary. Nothing here reads the answer key, `fail_reason`, the evaluator, or the skill. Every finding is
derived from the question text, the candidate-file list, the oracle pages the harness supplied, and the tool calls
and observations actually recorded. PASS / FAIL / UNKNOWN are kept distinct: a check whose inputs are undetermined
returns UNKNOWN and issues no warning.
"""
from __future__ import annotations

import re

import oqa_obligations as OB

# ── evidence ────────────────────────────────────────────────────────────────────

_NUM = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+)(?![\w])")
_SCALE_CTX = re.compile(r"\(?\s*in\s+(millions?|billions?|thousands?)\s+of\s+dollars", re.I)
_SCALE_WORD = re.compile(r"\b(millions?|billions?|thousands?)\b", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_FILE_PERIOD = re.compile(r"_(\d{4})_(\d{2})")

_SCALE_FACTOR = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}


def _canon_scale(word: str) -> str:
    w = word.lower().rstrip("s")
    return {"million": "millions", "billion": "billions", "thousand": "thousands"}.get(w, w)


def file_period(name: str) -> dict | None:
    """Period a bulletin file covers, read from its name (treasury_bulletin_YYYY_MM.txt)."""
    m = _FILE_PERIOD.search(name or "")
    if not m:
        return None
    return {"year": int(m.group(1)), "month": int(m.group(2))}


def numbers_in(text: str) -> list[dict]:
    """Numeric mentions with their line, the years on that line, and the nearest stated scale above the line."""
    out: list[dict] = []
    lines = str(text or "").splitlines()
    scale = None
    for idx, line in enumerate(lines):
        m = _SCALE_CTX.search(line)
        if m:
            scale = _canon_scale(m.group(1))
        for nm in _NUM.finditer(line):
            raw = nm.group(1)
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            out.append({
                "raw": raw,
                "value": value,
                "line": line.strip()[:300],
                "line_no": idx + 1,
                "years": sorted({int(y) for y in _YEAR.findall(line)}),
                "scale_context": scale,
            })
    return out


def build_ledger(packet: dict) -> list[dict]:
    """Every numeric fact the actor could have read, with its source and the step from which it was available.

    Oracle pages are available from step 0 (the harness puts them in the first user message). A tool observation is
    available from its own step onwards. Facts are OBSERVED only; no value is inferred here.
    """
    facts: list[dict] = []
    for page in packet.get("oracle_pages") or []:
        for num in numbers_in(page.get("text", "")):
            facts.append({**num, "basis": "OBSERVED", "available_from": 0,
                          "source": {"kind": "oracle_page", "file": page.get("file"), "page": page.get("page")}})
    for step in packet.get("steps") or []:
        if step.get("kind") != "tool":
            continue
        obs = step.get("obs") or ""
        if obs.startswith("[") and obs.rstrip().endswith("]"):
            continue  # "[no matches]", "[read error: ...]" - no numeric evidence
        path = (step.get("args") or {}).get("path") or ""
        for num in numbers_in(obs):
            facts.append({**num, "basis": "OBSERVED", "available_from": step["i"],
                          "source": {"kind": step.get("tool"), "file": path.rsplit("/", 1)[-1], "page": None,
                                     "step": step["i"]}})
    return facts


def available(facts: list[dict], upto: int) -> list[dict]:
    return [f for f in facts if f["available_from"] <= upto]


def _match(value: float, facts: list[dict], rel: float = 1e-9) -> list[dict]:
    """Facts whose value equals `value`, exactly or up to a scale factor of 1000 / 1e6 / 1e9."""
    hits = []
    for f in facts:
        for factor in (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9):
            a, b = f["value"] * factor, value
            if abs(a - b) <= max(abs(b), 1.0) * max(rel, 1e-9):
                hits.append({**f, "scale_factor": factor})
                break
    return hits


def _apply(op: str, a: float, b: float) -> list[float]:
    if op == "difference":
        return [a - b, b - a]
    if op == "sum":
        return [a + b]
    if op == "average":
        return [(a + b) / 2.0]
    if op == "ratio":
        return [a / b if b else float("inf"), b / a if a else float("inf"),
                100.0 * a / b if b else float("inf"), 100.0 * b / a if a else float("inf")]
    if op == "percent_change":
        return [100.0 * (a - b) / b if b else float("inf"), 100.0 * (b - a) / a if a else float("inf")]
    return []


def answer_number(text: str) -> float | None:
    nums = numbers_in(text)
    return nums[0]["value"] if nums else None


# ── findings ────────────────────────────────────────────────────────────────────

def _finding(bag: dict, problem: str, key: str, cat: str, kind: str, layer: str, step: int, text: str,
             **extra) -> dict:
    """Record one occurrence.

    `first` is the earliest prefix at which this message was issued, and `problem` names what the message is about.
    score_v4's comparison unit is the first correct and appropriately timed message per PROBLEM, not per category:
    two conditions that say different things about the same problem must not both be credited as discoveries.
    """
    f = bag.get(key)
    if f is None:
        f = bag[key] = {"key": key, "cat": cat, "kind": kind, "layer": layer, "first": step, "problem": problem,
                        "tier": extra.pop("tier", "primary"), "text": text, "occ": []}
    f["occ"].append({"step": step, **extra})
    return f


# ── stage model ─────────────────────────────────────────────────────────────────

STAGES = ["select_source", "locate_span", "extract_operands", "compute", "format_answer"]


def _tool_arg_numbers(packet: dict) -> set[float]:
    vals: set[float] = set()
    for step in packet.get("steps") or []:
        for v in (step.get("args") or {}).values():
            if isinstance(v, (int, float)):
                vals.add(float(v))
    return vals


_LOCATION_WORD = re.compile(r"\b(line|lines|page|pages|row|rows|column|columns|col|section)\b\s*(?:no\.?|number|#)?\s*$", re.I)


def looks_like_location_reference(num: dict, body: str) -> bool:
    """Is this number a navigation reference - a line, page, row or column - rather than a figure?

    The number must follow the location word IMMEDIATELY ("line 976", "pages 860"), or be the right-hand side of
    a range whose left side is one ("line 976-980"). An earlier, looser version of this test looked anywhere in
    the preceding 24 characters and swallowed real figures: in "The June 1970 page gives 81,243" it threw away
    81,243 because "page" appeared earlier in the sentence.
    """
    idx = body.find(num["raw"])
    if idx < 0:
        return False
    before = body[:idx]
    if _LOCATION_WORD.search(before):
        return True
    m = re.search(r"(\d[\d,]*)\s*[-\u2013]\s*$", before)
    return bool(m and _LOCATION_WORD.search(before[:m.start()]))


def looks_like_figure(num: dict) -> bool:
    """Could this number be a Treasury bulletin figure at all?

    This is the positive filter that does the real work, and it replaced a list of negative contexts that was
    never going to be complete. Figures in these tables are comma-grouped ("646,379"), decimal ("5.2"), or large.
    Bare small integers in prose are overwhelmingly line numbers, page numbers, table numbers and counts - the
    class that produced arm B's false finding in job 66196051.

    It errs towards dropping: a genuine small figure written without a comma is a check not made (silent) rather
    than a warning invented (loud).
    """
    raw = num["raw"]
    return ("," in raw) or ("." in raw) or abs(num["value"]) >= 1000


def looks_like_year(num: dict) -> bool:
    """A bare four-digit integer in 1800-2100 is a period label in this corpus, not a figure.

    Treasury bulletin figures are comma-grouped or decimal, so this rule errs on the safe side: a genuine figure of
    exactly 1970 would be skipped, which loses a check (silent) rather than inventing one (loud).
    """
    return "," not in num["raw"] and "." not in num["raw"] and 1800 <= num["value"] <= 2100


def _number_role(num: dict, mentions: list[dict], body: str) -> tuple[str, str]:
    """Is a number the agent wrote down an OPERAND it read, or a value it COMPUTED in the same message?

    Deterministic and asymmetric. In "a - b = c" all three numbers satisfy "equals some operation on the other
    two", so arithmetic alone cannot say which one is the result; position can. A value the agent derived must not
    be reported as an operand with missing provenance - that is exactly the class of false alarm amendment A3
    removed from the v3 plain report. The rule that fired is recorded:
      "equals"          the number follows an '=' sign
      "derived_result"  the number is the message's LAST numeric mention and equals one of the supported operations
                        applied to two EARLIER mentions
    """
    raw_escaped = re.escape(num["raw"])
    if re.search(r"=\s*[^\d\-]{0,12}" + raw_escaped, body):
        return "computed", "equals"
    if mentions and mentions[-1] is num and len(mentions) >= 3:
        earlier = mentions[:-1]
        for a_i, fa in enumerate(earlier):
            for fb in earlier[a_i + 1:]:
                for op in ("difference", "sum", "average", "ratio", "percent_change"):
                    for cand in _apply(op, fa["value"], fb["value"]):
                        if cand in (float("inf"), float("-inf")):
                            continue
                        if abs(cand - num["value"]) <= max(abs(num["value"]), 1.0) * 1e-9:
                            return "computed", f"derived_result:{op}({fa['raw']},{fb['raw']})"
    return "operand", ""


def stage_state(packet: dict, facts: list[dict], obligations: dict) -> dict:
    """Bindings the ordered procedure needs: supplied sources, read sources, operands, computation steps.

    An OPERAND is a number the actor itself wrote down in a message (not inside <answer>, not a tool argument).
    Its provenance is the set of available facts it matches at the step where it was written.
    """
    supplied = [{"file": p.get("file"), "page": p.get("page")} for p in packet.get("oracle_pages") or []]
    arg_nums = _tool_arg_numbers(packet)
    read_files: list[tuple[int, str]] = []
    operands: list[dict] = []
    computed: list[dict] = []
    computations: list[dict] = []
    for step in packet.get("steps") or []:
        if step.get("kind") == "tool":
            obs = step.get("obs") or ""
            path = ((step.get("args") or {}).get("path") or "").rsplit("/", 1)[-1]
            if path and not obs.startswith("[no matches]") and not obs.startswith("[read error"):
                read_files.append((step["i"], path))
            continue
        text = step.get("text") or ""
        body = re.sub(r"(?is)<answer>.*?</answer>", " ", text)
        mentions = [n for n in numbers_in(body)
                    if n["value"] not in arg_nums and looks_like_figure(n) and not looks_like_year(n)
                    and not looks_like_location_reference(n, body)]
        for num in mentions:
            role, rule = _number_role(num, mentions, body)
            if role == "computed":
                computed.append({"value": num["value"], "raw": num["raw"], "step": step["i"], "rule": rule})
                continue
            if any(o["raw"] == num["raw"] and o["step"] == step["i"] for o in operands):
                continue  # a value repeated inside one message is one operand, not two
            prov = _match(num["value"], available(facts, step["i"]))
            operands.append({"value": num["value"], "raw": num["raw"], "step": step["i"],
                             "line": num["line"], "provenance": prov})
        if re.search(r"[-+*/=]|\bminus\b|\bdivided by\b|\btimes\b|\bdifference\b", body, re.I) \
                and len(mentions) >= 2:
            computations.append({"step": step["i"], "text": body.strip()[:400]})
    return {
        "supplied_sources": supplied,
        "read_files": read_files,
        "operands": operands,
        "computed": computed,
        "computations": computations,
        "answer": packet.get("final_answer") or "",
        "answer_value": answer_number(packet.get("final_answer") or ""),
        "obligations": obligations,
    }


def open_stage(state: dict, upto: int) -> str:
    """The first stage whose own result is not yet bound, assessed on the prefix only."""
    if not state["supplied_sources"] and not [f for i, f in state["read_files"] if i <= upto]:
        return "select_source"
    if not [o for o in state["operands"] if o["step"] <= upto]:
        return "locate_span"
    need = state["obligations"]["operation"]["arity"]
    bound = [o for o in state["operands"] if o["step"] <= upto and o["provenance"]]
    if need >= 2 and len(bound) < need:
        return "extract_operands"
    if need >= 2 and not [c for c in state["computations"] if c["step"] <= upto]:
        return "compute"
    return "format_answer"


# ── action contracts (condition C) ──────────────────────────────────────────────

CONTRACTS = [
    {"id": "grep.effect.1", "verb": "grep", "kind": "effect", "version": 1,
     "statement": "every line in a grep observation contains the requested pattern (case-insensitively)",
     "assumptions": [], "basis": "tool implementation, verified in skillopt/envs/officeqa/tool_runtime.py run_tool"},
    {"id": "grep.suffices.1", "verb": "grep", "kind": "suffices", "version": 1,
     "statement": "if the pattern occurs in the file and fewer than 20 matches were returned, grep returned all of "
                  "its occurrences, so an absence conclusion about that pattern in that file is licensed",
     "assumptions": ["grep_cap_not_reached"],
     "basis": "tool implementation (_MAX_GREP_MATCHES = 20)"},
    {"id": "read.effect.1", "verb": "read", "kind": "effect", "version": 1,
     "statement": "a read observation is the contiguous line window [start, start+limit) of the named file, "
                  "truncated at 4000 characters",
     "assumptions": [], "basis": "tool implementation (_MAX_READ_CHARS = 4000)"},
    {"id": "table.scale.1", "verb": "locate_span", "kind": "effect", "version": 1,
     "statement": "a scale stated in a table heading applies to the numbers in the rows below it, until another "
                  "scale is stated",
     "assumptions": ["table_scale_applies_to_rows"],
     "basis": "EMPIRICAL hypothesis about Treasury bulletin tables; not verified against the corpus"},
]


# ── the three reports ───────────────────────────────────────────────────────────

def report(packet: dict, condition: str) -> dict:
    """Run condition "P", "S" or "C" over one recorded trace. S contains P; C contains S."""
    if condition not in {"P", "S", "C"}:
        raise ValueError(f"unknown condition {condition!r}")
    obligations = OB.derive(packet.get("question", ""))
    facts = build_ledger(packet)
    state = stage_state(packet, facts, obligations)
    bag: dict = {}
    candidates = [c.rsplit("/", 1)[-1] for c in (packet.get("candidate_files") or [])]
    steps = packet.get("steps") or []

    # ---- P: per-step checks of the selected tool call, at PRE ----
    for step in steps:
        if step.get("kind") != "tool":
            continue
        i, tool, args = step["i"], step.get("tool"), step.get("args") or {}
        path = (args.get("path") or "").rsplit("/", 1)[-1]
        if tool in {"read", "grep"} and path and candidates and path not in candidates:
            _finding(bag, f"tool_path:{path}", f"path_not_allowed:{path}", "path_not_allowed", "advice", "P", i,
                     f"step {i}: {tool} targets {path!r}, which is not in the candidate-file list; the tool will "
                     f"refuse it. Allowed: {', '.join(candidates)}.", path=path)
        obs = step.get("obs") or ""
        if tool == "grep" and len([ln for ln in obs.splitlines() if ln.strip()]) >= 20:
            _finding(bag, f"grep_cap:{i}", f"grep_cap_reached:{i}", "grep_cap_reached", "warning", "P", i,
                     f"step {i}: grep returned 20 lines, which is the tool's cap; the evidence for this pattern is "
                     f"incomplete and no conclusion that something is absent is supported by it.")
        if obs.startswith("[no matches]"):
            _finding(bag, f"no_matches:{i}", f"no_matches:{i}", "no_matches", "event", "P", i,
                     f"step {i}: no matches returned for {args.get('pattern')!r} in {path or '(unknown file)'}.")

    answer_step = steps[-1]["i"] if steps else 0
    op = obligations["operation"]
    av = available(facts, answer_step)
    ans = state["answer_value"]

    # ---- P: is the answer grounded in, or derivable from, the returned evidence? ----
    if ans is None:
        if state["answer"].strip():
            _finding(bag, "answer_grounding", "answer_not_numeric", "answer_not_numeric", "note", "P", answer_step,
                     f"the final answer {state['answer'].strip()[:80]!r} contains no number; the numeric checks "
                     f"below are UNKNOWN for it.", tier="conditional")
    elif op["op"] == "none":
        hits = _match(ans, av)
        if not hits:
            _finding(bag, "answer_grounding", "ungrounded_answer_value", "ungrounded_answer_value", "warning", "P", answer_step,
                     f"the answer value {ans:g} appears in none of the {len(av)} numeric mentions that the supplied "
                     f"pages and the returned tool observations contain. No arithmetic was requested, so the answer "
                     f"should have been copied from the evidence.")
    else:
        pairs = []
        for a_i, fa in enumerate(av):
            for fb in av[a_i + 1:]:
                for cand in _apply(op["op"], fa["value"], fb["value"]):
                    if cand not in (float("inf"), float("-inf")) and abs(cand - ans) <= max(abs(ans), 1.0) * 1e-6:
                        pairs.append((fa, fb))
                        break
            if len(pairs) >= 3:
                break
        if not pairs:
            _finding(bag, "answer_grounding", "answer_not_derivable", "answer_not_derivable", "warning", "P", answer_step,
                     f"the question asks for a {op['op']} and the answer is {ans:g}, but no pair of numbers in the "
                     f"available evidence yields it. Either an operand or the arithmetic is wrong.")

    # ---- P: are the asked periods present in the evidence at all? (order-free) ----
    for period in obligations["periods"]:
        if not any(period["year"] in f["years"] for f in av) and \
           not any(((file_period(f["source"].get("file") or "") or {}).get("year")) == period["year"] for f in av):
            _finding(bag, f"period:{period['year']}", f"asked_period_absent:{period['year']}", "asked_period_absent", "warning", "P",
                     answer_step,
                     f"the question asks about {period['text']}, but no returned evidence mentions {period['year']} "
                     f"and no source document covers it.")

    p_keys = set(bag)
    if condition == "P":
        return _finalise(bag, obligations, state, facts, condition, p_keys)

    # ---- S: the ordered procedure and its handoffs ----
    asked_years = [p["year"] for p in obligations["periods"]]
    supplied_files = [s["file"] for s in state["supplied_sources"]]

    # select_source: in the released offline setup the harness supplies the gold pages, so this stage is
    # discharged before the actor acts. Record it as such rather than crediting the actor with it.
    if supplied_files:
        supplied_text = ", ".join("{} p.{}".format(s["file"], s["page"]) for s in state["supplied_sources"])
        _finding(bag, "select_source", "select_source_prefilled", "select_source_prefilled", "note", "S", 0,
                 f"stage select_source was discharged by the harness: the first user message already contains the "
                 f"parsed page(s) {supplied_text}. Source-selection errors can therefore only be errors of "
                 f"choosing among the supplied pages.",
                 tier="conditional")

    # locate_span -> extract_operands handoff
    for o in state["operands"]:
        if not o["provenance"]:
            _finding(bag, "answer_grounding", f"operand_provenance_missing:{o['raw']}", "operand_provenance_missing", "warning", "S",
                     o["step"],
                     f"step {o['step']}: the operand {o['raw']} is stated in the reasoning but matches no numeric "
                     f"mention available at that point, so stage locate_span never produced it. Line: "
                     f"{o['line'][:120]!r}", operand=o["raw"])

    # operand period binding: with two asked periods, the bound operands must come from the two asked periods
    if op["arity"] >= 2 and len(asked_years) >= 2 and obligations["operation_varies"] == "period":
        bound = [o for o in state["operands"] if o["provenance"]]
        for o in bound:
            years = set()
            for p in o["provenance"]:
                years |= set(p["years"])
                fp = file_period(p["source"].get("file") or "")
                if fp:
                    years.add(fp["year"])
            if years and not (years & set(asked_years)):
                _finding(bag, "operand_period_binding", f"operand_period_mismatch:{o['raw']}", "operand_period_mismatch", "advice", "S",
                         o["step"],
                         f"step {o['step']}: operand {o['raw']} comes from evidence about "
                         f"{', '.join(str(y) for y in sorted(years))}, and the question asks about "
                         f"{', '.join(str(y) for y in asked_years)}. Take this operand from the page for "
                         f"{asked_years[1] if len(asked_years) > 1 else asked_years[0]} instead.",
                         operand=o["raw"])
        covered: dict[int, list[str]] = {}
        for o in bound:
            for p in o["provenance"]:
                fp = file_period(p["source"].get("file") or "")
                for y in set(p["years"]) | ({fp["year"]} if fp else set()):
                    if y in asked_years:
                        covered.setdefault(y, []).append(o["raw"])
        missing = [y for y in asked_years if y not in covered]
        if len(bound) >= op["arity"] and missing:
            step_at = max(o["step"] for o in bound)
            _finding(bag, "operand_period_binding", "unsupported_operands", "unsupported_operands", "advice", "S", step_at,
                     f"the question varies the period over {', '.join(str(y) for y in asked_years)}, but every bound "
                     f"operand traces to {', '.join(str(y) for y in sorted(covered)) or 'no asked period'}; "
                     f"{', '.join(str(y) for y in missing)} is never read. A {op['op']} over these operands answers "
                     f"a different question. Read the page for {missing[0]} and take the second operand from it.")

    # compute -> format_answer handoff
    if ans is not None and op["arity"] >= 2 and not state["computations"]:
        _finding(bag, "stage_handoff", "stage_skipped:compute", "stage_skipped", "advice", "S", answer_step,
                 f"the question asks for a {op['op']}, but no step states the arithmetic; stage format_answer was "
                 f"reached while stage compute was still open.")
    if ans is not None and not state["operands"]:
        _finding(bag, "stage_handoff", "stage_skipped:operands", "stage_skipped", "advice", "S", answer_step,
                 "the answer was produced without any operand being written down, so no locate_span -> "
                 "extract_operands handoff exists to check it against.")

    s_keys = set(bag)
    if condition == "S":
        return _finalise(bag, obligations, state, facts, condition, p_keys, s_keys)

    # ---- C: action contracts whose named assumptions license what S left UNKNOWN ----
    for step in steps:
        if step.get("kind") != "tool" or step.get("tool") != "grep":
            continue
        pattern = str((step.get("args") or {}).get("pattern") or "").lower()
        lines = [ln for ln in (step.get("obs") or "").splitlines() if ln.strip()]
        offenders = [ln for ln in lines if pattern and pattern not in ln.lower()]
        if offenders and not (step.get("obs") or "").startswith("["):
            _finding(bag, f"grep_effect:{step['i']}", f"grep_effect_violated:{step['i']}", "grep_effect_violated", "warning", "C", step["i"],
                     f"step {step['i']}: contract grep.effect.1 says every returned line contains the pattern "
                     f"{pattern!r}; {len(offenders)} returned line(s) do not. Either the extraction of this "
                     f"observation or the contract is wrong.")

    # scale: a stated table scale lets C type the operands, which S has to leave UNKNOWN
    asked_scale = obligations["scale"]["scale"]
    if asked_scale and asked_scale in _SCALE_FACTOR:
        answer_raw = (numbers_in(state["answer"]) or [{"raw": ""}])[0]["raw"]
        for o in state["operands"] + ([{"value": ans, "raw": answer_raw, "step": answer_step,
                                        "provenance": _match(ans, av), "line": ""}] if ans is not None else []):
            scales = {p["scale_context"] for p in o["provenance"] if p.get("scale_context")}
            if len(scales) == 1:
                found = scales.pop()
                if found != asked_scale:
                    factor = _SCALE_FACTOR[found] / _SCALE_FACTOR[asked_scale]
                    converted = any(abs(c["value"] * factor - o["value"]) <= max(abs(o["value"]), 1.0) * 1e-6
                                    for c in o["provenance"])
                    if not converted:
                        how = (f"multiply by {factor:g}" if factor >= 1
                               else f"divide by {1 / factor:,.0f}")
                        _finding(bag, "scale", f"scale_conversion_missing:{o['raw']}", "scale_conversion_missing", "advice",
                                 "C", o["step"],
                                 f"step {o['step']}: the value {o['raw']} sits under a heading that states "
                                 f"{found!r} (contract table.scale.1, assumption "
                                 f"table_scale_applies_to_rows), and the question asks for {asked_scale!r}. "
                                 f"No conversion step appears: {how} before answering.",
                                 operand=o["raw"])
    return _finalise(bag, obligations, state, facts, condition, p_keys, s_keys)


def _finalise(bag, obligations, state, facts, condition, p_keys, s_keys=None) -> dict:
    findings = sorted(bag.values(), key=lambda f: (f["first"], f["key"]))
    return {
        "condition": condition,
        "obligations": obligations,
        "obligation_lines": OB.describe(obligations),
        "n_facts": len(facts),
        "open_stage_at_answer": open_stage(state, max([s["step"] for f in findings for s in f["occ"]] or [0])),
        "operands": [{"raw": o["raw"], "step": o["step"], "grounded": bool(o["provenance"])}
                     for o in state["operands"]],
        "findings": findings,
        "layers": {"P": sorted(p_keys), "S": sorted((s_keys or set()) - p_keys),
                   "C": sorted(set(bag) - (s_keys or p_keys))},
    }


# ── rendering and comparison ────────────────────────────────────────────────────

_KIND_TAG = {"advice": "ADVICE", "warning": "WARNING", "note": "NOTE", "event": "EVENT", "positive": "POSITIVE"}


def render(result: dict, packet: dict) -> str:
    lines = [f"# OfficeQA {result['condition']} report - item {packet.get('id', '?')}",
             "", "## Task obligations (from the question text only)"]
    lines += [f"- {line}" for line in result["obligation_lines"]]
    lines += ["", f"## Evidence available: {result['n_facts']} numeric mentions "
                  f"({len(packet.get('oracle_pages') or [])} supplied page(s), "
                  f"{sum(1 for s in packet.get('steps') or [] if s.get('kind') == 'tool')} tool call(s))"]
    if result["operands"]:
        lines.append("## Operands written down by the agent")
        lines += [f"- step {o['step']}: {o['raw']} "
                  f"({'grounded in returned evidence' if o['grounded'] else 'NOT found in returned evidence'})"
                  for o in result["operands"]]
    lines += ["", "## Findings"]
    if not result["findings"]:
        lines.append("- none: every check this condition can make either passed or is UNKNOWN.")
    for f in result["findings"]:
        tier = "" if f["tier"] == "primary" else f" [{f['tier']}]"
        lines.append(f"- [{_KIND_TAG[f['kind']]}{tier}] ({f['layer']}, first at step {f['first']}) "
                     f"{f['cat']}: {f['text']}")
    return "\n".join(lines)


def structural_check(base: dict, top: dict, label: str) -> dict:
    """score_v4 invariant: the extended condition contains the base with identical first prefixes."""
    tmap = {f["key"]: f for f in top["findings"]}
    bad = [f["key"] for f in base["findings"]
           if f["key"] not in tmap or tmap[f["key"]]["first"] != f["first"]]
    if bad:
        raise AssertionError(f"{label}: base findings changed in the extended report: {bad}")
    return {"shared": len(base["findings"]), "added": len(top["findings"]) - len(base["findings"])}


def _first_warning(findings: list[dict]) -> dict:
    """The first WARNING or ADVICE occurrence (score_v4: NOTE and EVENT are never problem warnings)."""
    warnings = [f for f in findings if f["kind"] in {"advice", "warning"}]
    return min(warnings, key=lambda f: f["first"]) if warnings else {}


def compare(base: dict, top: dict) -> dict:
    """Per-PROBLEM comparison classes of score_v4.

    The unit is a problem, not a category: when the extended condition says something different about a problem the
    base condition already warns about, that is EARLIER / MORE_SPECIFIC / MORE_ACTIONABLE, never ADDITIONAL. Only a
    problem on which the base is silent counts as ADDITIONAL.
    """
    def by_problem(result):
        groups: dict[str, list[dict]] = {}
        for f in result["findings"]:
            groups.setdefault(f["problem"], []).append(f)
        return groups

    bg, tg = by_problem(base), by_problem(top)
    out = {"ADDITIONAL": [], "EARLIER": [], "TIE": [], "MORE_ACTIONABLE": [], "MORE_SPECIFIC": [],
           "NOTE_ONLY": [], "REDUNDANT": {}}
    for problem, findings in tg.items():
        t_first = _first_warning(findings)
        b_first = _first_warning(bg.get(problem, []))
        if not t_first:
            if problem not in bg:
                out["NOTE_ONLY"].append(problem)
            continue
        if not b_first:
            out["ADDITIONAL"].append(problem)
            if t_first["kind"] == "advice":
                out["MORE_ACTIONABLE"].append(problem)
            continue
        out["EARLIER" if t_first["first"] < b_first["first"] else "TIE"].append(problem)
        if t_first["kind"] == "advice" and b_first["kind"] != "advice":
            out["MORE_ACTIONABLE"].append(problem)
        base_cats = {f["cat"] for f in bg.get(problem, [])}
        new_cats = sorted({f["cat"] for f in findings if f["kind"] in {"advice", "warning"}} - base_cats)
        if new_cats:
            out["MORE_SPECIFIC"].append({"problem": problem, "categories": new_cats})
        repeats = len({occ["step"] for f in findings for occ in f["occ"]}) - 1
        if repeats > 0:
            out["REDUNDANT"][problem] = repeats
    return out
