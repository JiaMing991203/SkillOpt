"""Trace2Skill on OfficeQA: the method's structure, its parsers, and its information boundary.

WHY EACH TEST IS HERE. Trace2Skill is a BASELINE, so its value depends entirely on being run
faithfully. These tests pin the three ways a faithful run could quietly become an unfaithful one:

  * the hierarchy could degrade into a flat merge, so `chunk_list` is checked against upstream's
    documented balancing including the round-robin remainder;
  * a half-parsed patch could be applied as if whole, so a block whose items do not parse must be
    dropped and a failed batch merge must keep its inputs rather than lose proposals;
  * the answer key could reach an analyst, so the guard is tested by actually trying to smuggle one.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research", "officeqa")


def _mod(name):
    for p in (REPO, RESEARCH):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"{name}_t2s_test", os.path.join(RESEARCH, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


T = _mod("oqa_t2s")
P = _mod("oqa_t2s_prompts")


# ───────────────────────── the upstream is pinned, not merely cited ─────────────────────────

def test_the_upstream_is_pinned_to_an_exact_commit():
    assert P.UPSTREAM["commit"] == "3d0b52a140f002a512930252b613c49048f7d5ac"
    assert P.UPSTREAM["repo"] == "https://github.com/Qwen-Applications/Trace2Skill"
    assert P.UPSTREAM["paper"] == "arXiv:2603.25158"
    assert "single-call" in P.UPSTREAM["analysis_mode"]


def test_the_method_does_not_borrow_skillopts_machinery():
    """Trace2Skill's consolidated skill IS its endpoint. An epoch loop or a selection gate here would
    make it a different method wearing this one's name."""
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    code = "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))
    for forbidden in ("valid_seen", "selection_eval", "slow_update", "use_gate", "num_epochs"):
        assert forbidden not in code, forbidden


# ───────────────────────── the hierarchy ─────────────────────────

def test_chunk_list_reproduces_upstreams_balancing():
    assert T.chunk_list([], 8) == []
    assert T.chunk_list([1, 2, 3], 8) == [[1, 2, 3]]
    # exact multiple
    assert T.chunk_list(list(range(16)), 8) == [list(range(8)), list(range(8, 16))]
    # remainder >= half the batch: its own batch
    got = T.chunk_list(list(range(21)), 8)
    assert [len(b) for b in got] == [8, 8, 5]
    # remainder < half the batch: distributed round-robin into earlier batches, NOT left as a stub
    got = T.chunk_list(list(range(19)), 8)
    assert [len(b) for b in got] == [10, 9]
    assert sorted(x for b in got for x in b) == list(range(19))


def test_the_48_trajectory_reduce_is_genuinely_hierarchical():
    """48 patches at batch 8 is six merges, then one final merge: two levels, not one flat call."""
    first = T.chunk_list(list(range(48)), T.MERGE_BATCH_SIZE)
    assert len(first) == 6 and all(len(b) == 8 for b in first)
    assert T.MAX_MERGE_LEVELS >= 2


def test_every_item_survives_every_level_of_batching():
    for n in range(1, 60):
        batches = T.chunk_list(list(range(n)), 8)
        assert sorted(x for b in batches for x in b) == list(range(n)), n


# ───────────────────────── the permitted view ─────────────────────────

def test_the_permitted_view_renders_only_the_three_known_entry_shapes():
    conv = [
        {"role": "user", "content": "the question"},
        {"type": "message", "content": "my reasoning"},
        {"type": "tool_call", "cmd": "grep(pattern='x')", "obs": "3507: | a | b |"},
        # anything else must not appear, so a field added to the trace later cannot leak silently
        {"type": "provider_debug", "content": "SECRET-ALTERNATE-REASONING"},
        {"reasoning": "SECRET-ALTERNATE-REASONING"},
    ]
    view = T.render_permitted(conv)
    assert "the question" in view and "my reasoning" in view
    assert "grep(pattern='x')" in view and "3507: | a | b |" in view
    assert "SECRET-ALTERNATE-REASONING" not in view


def test_the_observation_text_is_kept_because_it_is_the_evidence():
    """For OfficeQA the tool observation IS the document text, so the analyst sees what the actor saw."""
    view = T.render_permitted([{"type": "tool_call", "cmd": "read(p)", "obs": "| 1942 | 528979 |"}])
    assert "528979" in view
    assert "[OBSERVATION]" in view


# ───────────────────────── the boundary guard ─────────────────────────

def test_an_UNEXPLAINED_answer_key_in_an_outbound_prompt_aborts_the_run(tmp_path):
    """A key the permitted corpus cannot account for can only have come from the evaluator."""
    call = T.Caller(str(tmp_path), keys=["1,169.41 million dollars"],
                    permitted_corpus="a trajectory that never mentions the answer", dry_run=True)
    call("analysis", "system", "an innocent trajectory")        # clean prompt goes through
    with pytest.raises(SystemExit) as exc:
        call("analysis", "system", "the agent should have said 1,169.41 million dollars")
    assert "BOUNDARY VIOLATION" in str(exc.value)
    assert "permitted corpus does NOT contain them" in str(exc.value)
    assert call.leaks and call.leaks[0]["phase"] == "analysis"


def test_a_correct_episode_is_not_refused_because_the_actor_got_the_answer_right(tmp_path):
    """THE defect that failed job 66552508. On a correct episode the actor's own answer IS the key, so
    a plain string scan refuses exactly the episodes the success analyst needs.

    The two real hits were UID0017's `[10102000000, 4.73]` and UID0033's `March 3, 1977`, both found in
    an ACTOR MESSAGE, both the question's own key, both on an episode the actor answered correctly."""
    for key in ("[10102000000, 4.73]", "March 3, 1977"):
        trajectory = f"[AGENT]\nAfter checking the table I conclude {key}."
        call = T.Caller(str(tmp_path), keys=[key], permitted_corpus=trajectory, dry_run=True)
        call("analysis", "system", "# Agent chat log\n\n" + trajectory)   # must NOT raise
        assert call.leaks == []
        assert len(call.explained) == 1
        assert "did not come from the evaluator" in call.explained[0]["why"]


def test_a_value_the_actor_merely_READ_is_also_explained(tmp_path):
    """The answer to a document question is a number printed in the document, so an observation
    containing it is unavoidable and harmless."""
    trajectory = "[TOOL CALL]\ngrep(...)\n[OBSERVATION]\n3507: | Receipts | 1,169.41 |"
    call = T.Caller(str(tmp_path), keys=["1,169.41"], permitted_corpus=trajectory, dry_run=True)
    call("analysis", "system", trajectory)
    assert call.leaks == [] and len(call.explained) == 1


def test_the_guard_still_fails_closed_when_there_is_no_permitted_corpus(tmp_path):
    """An empty corpus explains nothing, so the guard refuses rather than waving a key through."""
    call = T.Caller(str(tmp_path), keys=["a distinctive answer string"], permitted_corpus="",
                    dry_run=True)
    with pytest.raises(SystemExit):
        call("map", "system", "a distinctive answer string")


def test_short_keys_are_not_used_as_a_leak_test(tmp_path):
    """A two-character ground truth would match by coincidence in any prompt, so it is not a signal."""
    rows = "uid,question,ground_truth\nUID1,q,42\nUID2,q,a distinctive long answer string\n"
    for sub in ("train", "val", "test"):
        d = tmp_path / sub
        d.mkdir()
        (d / f"officeqa_{sub}.csv").write_text(rows, encoding="utf-8")
    keys = T.answer_keys(str(tmp_path))
    assert "a distinctive long answer string" in keys
    assert "42" not in keys


def test_a_cached_reply_is_reused_and_not_paid_for_again(tmp_path):
    """Resume must cost only the calls that had not happened."""
    call = T.Caller(str(tmp_path), keys=[], dry_run=True)
    os.makedirs(os.path.join(str(tmp_path), "cache", "map"), exist_ok=True)
    import hashlib as _h
    digest = _h.sha256(json.dumps({"p": "map", "s": "sys", "u": "usr"}, sort_keys=True).encode()).hexdigest()
    with open(os.path.join(str(tmp_path), "cache", "map", f"{digest}.json"), "w", encoding="utf-8") as f:
        json.dump({"text": "the saved reply", "usage": {"prompt_tokens": 7}}, f)
    assert call("map", "sys", "usr") == "the saved reply"
    assert call.calls[-1]["cached"] is True


# ───────────────────────── the analysis parser ─────────────────────────

ANALYSIS = """Some preamble the model wrote.

# Failure Cause Item 1

## Title
Read the wrong column

## Description
The agent took the value from the Canada column.

## Content
At step 4 the agent grepped the row and took field 3 without checking the header.

# Failure Memory Item 1

## Title
Confirm the column before reporting

## Description
Always name the column a value came from.

## Content
State the column label beside the number before committing an answer.
"""


def test_the_analysis_sections_parse_into_titled_items():
    got = T.parse_analysis(ANALYSIS)
    assert got["parsed"] is True
    assert got["n_cause"] == 1 and got["n_memory"] == 1
    cause = next(i for i in got["items"] if i["section"] == "cause")
    assert cause["title"] == "Read the wrong column"
    assert "field 3" in cause["content"]
    memory = next(i for i in got["items"] if i["section"] == "memory")
    assert memory["kind"] == "Failure Memory Item"


def test_success_items_parse_under_their_own_headings():
    got = T.parse_analysis("# Success Cause Item 1\n\n## Title\nT\n\n## Description\nD\n\n## Content\nC\n")
    assert got["n_cause"] == 1 and got["items"][0]["kind"] == "Success Cause Item"


def test_an_unparseable_analysis_is_reported_not_invented():
    got = T.parse_analysis("I could not determine the cause.")
    assert got["parsed"] is False and got["items"] == []


# ───────────────────────── the patch parser ─────────────────────────

PATCH = """===== PATCH START =====
Reasoning:
The agent misread a column.

Changelog:
- Add a column-confirmation step

Items:

[ITEM_1_START]
Target File: SKILL.md
Edit Intent: Add a column-confirmation step before committing a number
Location Hint: near the table-reading section
Change Instruction:
Tell the agent to quote the column label beside the value it read.

[ITEM_2_START]
Target File: SKILL.md
Edit Intent: Add a unit check
Location Hint: near the arithmetic section
Change Instruction:
Require the scale line to be quoted before converting.
===== PATCH END =====
"""


def test_a_semantic_patch_parses_into_its_four_fields():
    got = T.parse_patches(PATCH)
    assert len(got) == 1
    patch = got[0]
    assert patch["reasoning"] == "The agent misread a column."
    assert "column-confirmation step" in patch["changelog"]
    assert len(patch["items"]) == 2
    first = patch["items"][0]
    assert first["target_file"] == "SKILL.md"
    assert first["edit_intent"].startswith("Add a column-confirmation step")
    assert first["location_hint"] == "near the table-reading section"
    assert "quote the column label" in first["change_instruction"]
    # the Change Instruction must not swallow the next item's fields
    assert "Target File" not in first["change_instruction"]


def test_a_patch_block_with_no_usable_item_is_dropped():
    """A half-parsed patch applied as if whole is worse than no patch at all."""
    assert T.parse_patches("===== PATCH START =====\nReasoning:\nx\nItems:\n\n===== PATCH END =====") == []
    assert T.parse_patches("no patch block here at all") == []


def test_a_patch_round_trips_through_the_upstream_schema():
    """render_patch feeds the merge and apply phases, so it must re-parse to the same items."""
    once = T.parse_patches(PATCH)[0]
    twice = T.parse_patches(T.render_patch(once))
    assert len(twice) == 1
    assert [i["edit_intent"] for i in twice[0]["items"]] == [i["edit_intent"] for i in once["items"]]
    assert [i["change_instruction"] for i in twice[0]["items"]] == \
           [i["change_instruction"] for i in once["items"]]


def test_several_patch_blocks_in_one_response_are_all_kept():
    got = T.parse_patches(PATCH + "\n" + PATCH)
    assert len(got) == 2


# ───────────────────────── reduce behaviour under failure ─────────────────────────

def test_a_failed_batch_merge_keeps_its_inputs(tmp_path, monkeypatch):
    """A provider error during consolidation must lose no proposal."""
    patches = T.parse_patches(PATCH) * 9          # 9 patches -> 2 batches at size 8
    calls = {"n": 0}

    def fake_call(phase, system, user, **kw):
        calls["n"] += 1
        raise RuntimeError("provider exploded")

    merged, levels = T.phase_reduce(fake_call, patches, "SKILL", P, batch_size=8, max_levels=2)
    assert calls["n"] >= 1
    assert merged is not None, "a total merge failure must still yield a patch to apply"
    assert levels and levels[0]["merge_failed"] >= 1
    assert levels[0]["patches_out"] == levels[0]["patches_in"]
    # every item of every proposal survives, and the report admits no model resolved the conflicts
    assert len(merged["items"]) == sum(len(p["items"]) for p in patches)
    assert merged.get("conflicts_unresolved") is True
    assert any(lv.get("error") for lv in levels if isinstance(lv, dict)), \
        "the final merge's provider error must be recorded, not swallowed"


def test_a_reduce_that_stops_making_progress_does_not_keep_paying():
    """If a level returns as many patches as it was given, another identical level is pure cost."""
    patches = T.parse_patches(PATCH) * 9
    seen = {"n": 0}

    def fake_call(phase, system, user, **kw):
        seen["n"] += 1
        return PATCH * 5          # always returns MORE patches than it was given

    merged, levels = T.phase_reduce(fake_call, patches, "SKILL", P, batch_size=8, max_levels=4)
    assert any("stopped" in lv for lv in levels if isinstance(lv, dict))
    assert merged is not None


def test_a_single_patch_needs_no_merge_call():
    one = T.parse_patches(PATCH)

    def fake_call(*a, **k):
        raise AssertionError("no merge call should be made for a single patch")

    merged, levels = T.phase_reduce(fake_call, one, "SKILL", P)
    assert merged == one[0] and levels == []


# ───────────────────────── the prompts carry the method's constraints ─────────────────────────

def test_the_analyst_is_forbidden_to_imply_a_ground_truth():
    for prompt in (P.ERROR_ANALYSIS_SYSTEM, P.SUCCESS_ANALYSIS_SYSTEM):
        assert "NEVER mention or imply the existence of ground truth" in prompt
        assert "no more than 3" in prompt
        assert "Do NOT fabricate evidence" in prompt


def test_the_single_document_skill_interface_is_stated_in_the_prompts():
    """Upstream evolves a skill DIRECTORY; ours is one document, and the prompts must say so."""
    assert "SINGLE document" in P.MAP_SYSTEM
    assert "references/*.md" in P.MAP_SYSTEM
    assert "single document" in P.MERGE_SYSTEM


def test_the_merge_prompt_requires_conflicts_to_be_resolved_not_accumulated():
    assert "Deduplicate" in P.MERGE_SYSTEM
    assert "conflict" in P.MERGE_SYSTEM.lower()
    assert "non-overlapping" in P.MERGE_SYSTEM


def test_the_success_analyst_separates_a_good_method_from_a_lucky_outcome():
    assert "luck" in P.SUCCESS_ANALYSIS_SYSTEM.lower()
    assert "correct outcome does NOT establish a good method" in P.SUCCESS_ANALYSIS_SYSTEM


def test_the_optimizer_route_is_configured_before_anything_is_sent():
    """Job 66552741's defect: `chat_optimizer` is a ROUTER whose default is Azure, and a config file
    does not reach it by itself. Every call raised "Azure OpenAI endpoint is not configured", 48 times,
    AFTER a dry run had reported everything healthy."""
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    body = src[src.index("def run(args)"):src.index("def _write(")]
    assert "configure_models(" in body
    # it must happen BEFORE the trajectories are gathered, so nothing is built against a bad route
    assert body.index("configure_models(") < body.index("collect_trajectories(")
    # and it must NOT be skipped in dry run: the dry run's purpose is to exercise the real path
    line = next(l for l in body.splitlines() if "configure_models(" in l)
    assert "dry_run" not in line and "dry-run" not in line


def test_configure_models_sets_the_backend_selector_not_only_the_url():
    """Pinning the base URL alone leaves the ROUTER pointed elsewhere."""
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    fn = src[src.index("def configure_models("):src.index("def _parallel(")]
    assert "set_optimizer_backend" in fn
    assert "configure_openai_compatible" in fn
    assert "_config_for(\"optimizer\")" in fn, "the LIVE config must be read back, not assumed"
    assert "deepseek" in fn, "it must refuse a route that is not DeepSeek"


def test_analysis_errors_are_surfaced_in_the_summary_not_buried_per_record():
    """The first version printed only "parsed: 0" while 48 identical exceptions sat in a per-record
    field, so the summary said nothing about the cause."""
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    assert '"errors": dict(analysis_errors)' in src
    printed = src[src.index('print(json.dumps({"verdict": report["verdict"]'):]
    assert '"errors"' in printed


def test_every_stdlib_module_the_research_code_uses_is_actually_imported():
    """The bug this pins: `collections.Counter` was used in oqa_t2s.run() and `collections` was never
    imported, so the module raised NameError at runtime. Syntax checks and the unit tests above both
    passed, because none of them calls run() - and run() cannot be called locally any more, since
    configure_models imports skillopt, which needs Python 3.10+.

    So this walks the AST for `<module>.<attr>` uses of known stdlib modules and asserts each one is
    imported in that file. It is a narrow linter, but it is exactly the class of mistake that reached
    the cluster twice tonight.
    """
    import ast as _ast
    stdlib = {"collections", "json", "os", "re", "sys", "glob", "hashlib", "datetime", "argparse",
              "threading", "itertools", "math", "csv", "time", "subprocess", "statistics",
              "functools", "textwrap", "shutil", "random", "copy"}
    checked = 0
    for name in ("oqa_t2s.py", "oqa_occ.py", "oqa_occ_replay.py", "oqa_base_preflight.py",
                 "oqa_base_verify.py", "oqa_sel4.py", "oqa_t2s_prompts.py"):
        path = os.path.join(RESEARCH, name)
        tree = _ast.parse(open(path, encoding="utf-8").read())
        imported, aliases = set(), set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for a in node.names:
                    imported.add(a.asname or a.name.split(".")[0])
                    aliases.add(a.name.split(".")[0])
            elif isinstance(node, _ast.ImportFrom) and node.module:
                for a in node.names:
                    imported.add(a.asname or a.name)
        used = set()
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Attribute) and isinstance(node.value, _ast.Name)
                    and node.value.id in stdlib):
                used.add(node.value.id)
        missing = sorted(used - imported - aliases)
        assert not missing, f"{name} uses {missing} without importing them"
        checked += 1
    assert checked == 7


def test_THE_STRUCTURAL_GUARANTEE_no_evaluator_field_is_ever_read():
    """This is the protection worth relying on; the string scan is only a tripwire on top of it.

    No CODE in the module may read `ground_truth` or `soft`. `hard` may be read, and only to triage
    which analyst runs, which is how upstream triages from its official evaluation. The check walks the
    AST rather than the text, so prose describing the boundary is not mistaken for code crossing it -
    the module docstring names the forbidden fields precisely in order to forbid them.
    """
    import ast as _ast
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    tree = _ast.parse(src)

    # every string literal that is NOT a docstring
    docstrings = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    # answer_keys is the ONE permitted reader: it builds the list the scan compares against
    permitted_reader = next(n for n in _ast.walk(tree)
                            if isinstance(n, _ast.FunctionDef) and n.name == "answer_keys")
    inside_reader = {id(n) for n in _ast.walk(permitted_reader)}

    offenders = []
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Constant) and isinstance(node.value, str)
                and node.value in ("ground_truth", "soft")
                and id(node) not in docstrings and id(node) not in inside_reader):
            offenders.append((node.value, getattr(node, "lineno", "?")))
    assert not offenders, f"evaluator-only fields read in code: {offenders}"

    reads_key_in_reader = [n.value for n in _ast.walk(permitted_reader)
                           if isinstance(n, _ast.Constant) and n.value == "ground_truth"]
    assert reads_key_in_reader, "answer_keys must read the key to build the scan list"

    hard_reads = [getattr(n, "lineno", "?") for n in _ast.walk(tree)
                  if isinstance(n, _ast.Constant) and n.value == "hard" and id(n) not in docstrings]
    assert len(hard_reads) == 1, f"`hard` must be read exactly once, for triage; found {hard_reads}"
    collect = src[src.index("def collect_trajectories"):src.index("def answer_keys")]
    assert "TRIAGE ONLY" in collect


def test_derived_text_joins_the_permitted_corpus_so_the_scan_stays_usable(tmp_path):
    """Job 66552979: the MAP phase quoted an analyst's own words back and the guard refused, because
    the corpus held the trajectories and the skill but not the analysts' output."""
    key = "1,169.41 million dollars"
    call = T.Caller(str(tmp_path), keys=[key], permitted_corpus="a trajectory", dry_run=True)
    # before: an analyst's reformatted value is not in the corpus, so it is refused
    with pytest.raises(SystemExit):
        call("map", "sys", f"the analyst wrote: the value should be {key}")
    # after admitting the analyst's output, the same prompt is accepted
    call.extend_permitted(f"Failure cause: the agent needed {key} and read something else")
    call("map", "sys", f"the analyst wrote: the value should be {key}")
    assert len(call.explained) == 1


def test_each_phases_output_is_admitted_before_the_next_phase_quotes_it():
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    body = src[src.index("def run(args)"):src.index("def _write(")]
    # the analysts' items before MAP, the patches before REDUCE, the merged patch before APPLY
    assert body.index("extend_permitted") < body.index("phase_map(")
    assert body.count("extend_permitted") >= 3


def test_the_guard_admits_that_it_is_permissive_downstream():
    """A guard that overstates itself is worse than one that states its limit."""
    src = open(os.path.join(RESEARCH, "oqa_t2s.py"), encoding="utf-8").read()
    fn = src[src.index("def extend_permitted"):src.index("def _guard")]
    assert "PERMISSIVE" in fn
    assert "not the main protection" in fn or "MAIN PROTECTION IS STRUCTURAL" in fn
