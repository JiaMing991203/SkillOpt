# OfficeQA handoff (latest)

One copy-pasteable block. Written 2026-11-16 22:05 EST (from `date`).

```text
OFFICEQA / LEARNED-CONTRACT PORT - HANDOFF 2026-11-16 22:05 EST
Branch officeqa-contracts, worktree ~/dev/lean_skillopt/SkillOpt-officeqa, HEAD b530b9b (tree clean, NOT pushed).

STATUS: the full learned-contract loop RAN, offline. 0 GPU jobs, 0 CPU jobs, 0 paid API calls, 0 network.
Nothing is running or queued; OfficeQA holds 0 GPUs. OQA-5 caps untouched: 6.23 of 8 A40-hours and 8 of 24
DeepSeek calls still unused. Full record: docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md.

REFERENCE DESIGN: LEARNED_CONTRACT_DESIGN.md v0.1 + amendments A1-A4, in ~/dev/lean_skillopt/SkillOpt-learned-
contract @ 4a9914a. No document labelled v0.2 exists; v0.1 as amended IS the agreed design. Digests recorded.

WHAT WAS REUSED (9 further reference files transferred, manifest entry 2; 15 total, none on an execution path)
  direct, ~90%     ir.py            -> oqa_lc_ir      Kleene logic, validation, freeze-by-digest
  direct machinery learn.py         -> oqa_lc_learn   the gate, assumption ops, auto-refutation
  direct           replay.py        -> oqa_lc_replay  commit-before-reveal with a hash chain, scoring
  semantics only   fact_ledger.py   -> oqa_lc_chain   fact kinds/statuses, obligation reopening (A4 3.1)
  semantics only   stage_tracker.py -> oqa_lc_chain   stage requirements and their discharge
  shape only       vocab.py         -> oqa_lc_vocab   OfficeQA verbs/atoms/assumptions are entirely new
  direct harness   lean_gen/a4_lean -> oqa_lc_lean    run_lean, parse_results, the mirror discipline
  NOT wired in     lc_llm.py                          no paid learner is needed for this milestone
  ALFWorld-only    parse_command, the entity-typed atom set, TASK_FORMS, compose.py, stage_replay.py

WHAT CHANGED FOR OFFICEQA
  * atoms are 0-ARY properties of ONE tool observation (there is no entity to bind, and no state persists)
  * OfficeQA tools almost never fail informatively, so condition-based prediction is UNKNOWN at PRE BY
    CONSTRUCTION and `effect` contracts carry all the content. Recorded, not worked around.
  * NEW: cells bound to a column path, a period (month included) and a unit - the link oqa_check lacked
  * NEW: obligations with a RESOLUTION (DISCHARGED / VIOLATED / OUTSTANDING / UNKNOWN) plus a witness
  * deviations, each deliberate: TEMPLATES not exhaustive enumeration; MIN_ITEMS=2; assumption_recheck added;
    mech_repair targets REFUTED contracts (a proposer looking at active ones never fires)

THE LOOP (12 shared epoch-1 attempts, in the order results.jsonl recorded them - not a curriculum I chose)
  round 0 UID0033  5 assumptions adopted; nothing in the evidence contradicted any of them
  round 3 UID0031  grep.effect.2 = not cap_reached auto-refuted (@28 hit the 20-line cap) and retired
  round 6 UID0026  frozen v5 committed "index merge will agree"; the read at s44 REFUTED it in 12 places
                   -> index_alignment DROPPED; affected bindings reopen to UNKNOWN, never to violations
  round 8 UID0028  year_inheritance dropped (Table AY-1's first row is "Jan." with no year in the table)
  final  v7        3 active contracts: read width_uniform, grep pattern_in_every_line, read sub_row_aligned
  dev replay totals 67 forecasts confirmed / 5 violated / 123 unresolved over 11 replayed attempts
  CONFIRMATION (arm B epoch 2, same 12 items, never seen by the repair): 82 confirmed / 1 violated / 71
                   unresolved; index_alignment contradicted 10 MORE times in UID0014

REGRESSION CASE - the known false warning about an already-completed conversion (UID0014, epoch-2 trace)
  old C report:       scale_conversion_missing x29, every one "No conversion step appears"
  obligation form:    13 DISCHARGED, 23 VIOLATED, 8 OUTSTANDING
  the 13 discharges:  "step 1 converts it with the correct factor: 1,004,461 (in millions) = $1,004.461 billion"
  the 23 violations:  thousands->billions applied with 1e-3 where 1e-6 is required - 14 regression inputs are
                      1,000x too large. Verified by hand. Neither P nor the old C report found this.
  plain report P on the same trace: one category, no_matches, and nothing about units.
  SKILL-EDIT CANDIDATE: write the conversion factor explicitly as a power of ten and re-check it against each
  new table's own stated unit instead of reusing the previous factor.

ALSO FIXED (each with a regression test): arithmetic intermediates reported as missing provenance (17 -> 1);
every year of "fiscal years 1929-1942" flagged as a wrong period (12 false positives -> UNKNOWN); a scaled
match now needs equal significant digits (the value 1 used to match any operand near a million).

HONEST LIMITS - do not drop these
  * adjudicating evidence is VERY sparse: 5 table pairs over 3 tool steps in 24 attempts; 126 of 128
    development tool steps leave the column atom UNKNOWN
  * the learned model is 3 contracts, and ONE of them (read width_uniform) acquires a counterexample on the
    confirmation collection at UID0028@42, so the final model is NOT fully confirmed on later data
  * nothing here shows any of this changes a skill edit that survives merge, the edit budget and the 6/12 gate

LEAN: GENERATED, NOT COMPILED. lean/OqaContracts.lean, Lean 4 core only, 64 statements, no sorry /
native_decide / axioms. 25 general ones are stated over the finite Kleene domain acceptability factors through,
so they close by EXHAUSTION rather than by a tactic script; 39 cover the exported evidence. The Python mirror
agrees on every row (0 mismatches) - a cross-implementation check, NOT a Lean proof.
  Lean verifies: the composition's properties over its finite domain; that the recorded atom values and a
  contract's polarity produce the recorded class; the unit exponent arithmetic.
  Extraction supplies and Lean does NOT check: that a row label is the period it is read as, that a flattened
  header states its column's path, that an operand is the cell it matched, that a line is the conversion parsed.
  Nothing establishes that the corpus obeys a learned hypothesis or that a violation caused a wrong answer.

TESTS: tests/test_officeqa_lc.py 38 pass (constructed inputs only - Stage A software checks, not research
findings). The existing 87 OfficeQA tests pass unchanged, which is the check that the port is additive:
oqa_check.py and oqa_obligations.py were NOT modified, so arm A stays byte-identical to upstream. Whole local
suite 2014 passed / 11 skipped (4 files cannot be collected in the scratch venv for want of openpyxl - a
pre-existing gap, unrelated).

CORRECTION to an earlier claim of mine: OFFICEQA_STATE.md section A0 said the transfer manifest was untracked
and that its source_commit and per-file source paths were null. All three were wrong - the manifest is tracked
and the fields exist as source_head and path, populated, with source_status recording that the SOURCES are
untracked in their own worktree. There was no provenance gap.

WITH THE USER (nothing will be spent without a new instruction)
  1. Compile the Lean file? Needs ONE Slurm CPU job: 1 core, 2 GB, 15 min, 0 GPU, no API key, no paid call,
     plus the LEAN_BIN path from the learned-contract worktree's private lc_site.env. The OQA-5 grant does NOT
     cover it: it is expressed in A40-hours and DeepSeek calls and names no CPU job-hours.
  2. Approve the guarded-`effect` extension? Two worked examples of where the current format bites, and the
     smallest change, are in port note 3.3. Deliberately NOT implemented - it changes contract semantics.
  3. Spend 1-2 of the 8 remaining DeepSeek calls on one learner round, to test whether a paid learner proposes
     hypotheses the deterministic template proposer does not?
  4. Still open from the comparison: re-run arm B's epoch 2 with the cache fix; restructure as one epoch of two
     steps; apply the OFFICEQA_CONTRACT_AUDIT.md changes 1-4 to oqa_check.py itself.
  The held-out score of job 66234927 remains deliberately UNREAD.

WHERE THINGS ARE
  code      research/officeqa/oqa_lc_{vocab,ir,cells,chain,learn,replay,lean,report,demo}.py
  doc       docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md   (read this, not a paraphrase)
  output    ~/dev/lean_skillopt/runs/20261116-offline-lc-port/  (420 KB; LOOP.json, 12 frozen models,
            11 + 12 committed-prediction logs, lean/OqaContracts.lean, report_UID0014.md)
  rerun     $VENV/bin/python -I research/officeqa/oqa_lc_demo.py \
              --learn  <shared>/epoch1/predictions  --order  <shared>/epoch1/results.jsonl \
              --confirm <armB>/steps/step_0002/rollout/predictions \
              --confirm-order <armB>/steps/step_0002/rollout/results.jsonl \
              --regression UID0014 --out <a NEW dir>
  ledger    coordination/reservations.jsonl, last record OQA-6:LEARNED-CONTRACT-PORT-DONE-OFFLINE
```
