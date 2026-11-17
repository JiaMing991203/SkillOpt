# Porting the learned-contract implementation to OfficeQA

**Offline. Zero GPU jobs, zero paid API calls, zero network.** Written 2026-11-16 (times from `date`). Everything
below was produced by reading saved prediction directories and writing a new output directory; no run artifact was
modified.

Reference design: `docs/LEARNED_CONTRACT_DESIGN.md` **v0.1 as amended by A1-A4**, in the sibling worktree
`~/dev/lean_skillopt/SkillOpt-learned-contract` on branch `contract/learned-contract` at commit `4a9914a`. There is
**no document labelled v0.2** anywhere in that tree; v0.1 plus the four amendments *is* the current agreed design,
and that is what "design v0.2" is read as throughout. Digests of the five documents, so a later session can tell
whether the reference moved:

| document | sha256 (first 16) | bytes |
|---|---|---|
| `LEARNED_CONTRACT_DESIGN.md` (v0.1) | `b115079e6ea7cc82` | 15,542 |
| `..._AMENDMENT_A1.md` (information access) | `f2a583610aafd8e4` | 5,925 |
| `..._AMENDMENT_A2.md` (stage-plus-action architecture) | `be7c1f80ebcaa8a5` | 10,968 |
| `..._AMENDMENT_A3.md` (corrected diagnostics) | `cc96cf76513e0f37` | 11,130 |
| `..._AMENDMENT_A4.md` (fact ledger, propagation) | `bbbb1de5827bdc75` | 24,093 |

---

## 1. Verdict

**The full loop ran.** Learn -> freeze by digest -> commit a prediction -> reveal -> real counterexample -> repair
-> recheck the earlier cases -> independent confirmation on a later collection -> generated Lean -> a report whose
diagnosis is traced into a proposed skill edit. It ran in 4 seconds on twelve development attempts and twelve later
attempts of the same training items, and the output is preserved at
`~/dev/lean_skillopt/runs/20261116-offline-lc-port/`.

What came out of it:

1. **The architecture transfers, with one structural difference that changes which contract kind does the work.**
   In ALFWorld a command's outcome is informative ("Nothing happens." is an observed failure), so `requires`
   contracts predict and get refuted. OfficeQA's document tools almost never fail that way, so condition-based
   prediction is UNKNOWN at PRE **by construction** and `effect` contracts carry the whole load. The port records
   that rather than papering over it (`oqa_lc_replay.py` docstring; `test_condition_based_prediction_is_
   undetermined_at_pre_by_construction`).
2. **A real counterexample appeared, in the data, at the step the replay reached it.** The assumption
   `index_alignment` - that a supplied page's two-row table header can be merged by cell index - was adopted at
   round 0 with nothing contradicting it, survived five rounds, and was refuted at round 6 by UID0026's own read of
   the flattened header of the same table, in 12 places. It was then refuted again, independently, by UID0014 in the
   later collection, in 10 places.
3. **The repair is an assumption drop, and it is the correct causal attribution.** The contract's claim was never
   wrong; the licence for evaluating it was. Dropping the assumption turns the affected column bindings into
   UNKNOWN rather than into violations, which is amendment A4 3.1's invariant and is now a Lean statement.
4. **The regression case is fixed and replaced by something better.** On UID0014's later attempt the old C report
   fires `scale_conversion_missing` 29 times, all saying "No conversion step appears". The obligation form
   **discharges 13** of those (the actor had converted correctly, at its first message) and reports **23 as
   VIOLATED for a different and verifiable reason**: thousands-to-billions was applied with a factor of 1e-3 where
   1e-6 is required, so fourteen regression inputs are 1,000x too large. The plain report P says nothing about any
   of it.
5. **The Lean file compiles.** Job **66259448** on Great Lakes, **Lean 4.20.0**, 1 CPU / 2 GB / 6 s elapsed
   (4.24 s in `lean`), **rc 0**, zero compiler output, zero warnings, zero `sorry`: all **64** statements check
   (23 general exhaustions + 2 `rfl` bridges + 39 over the exported evidence). The axiom set was **not** checked -
   see 5.4.

Honest limits, stated once here and not softened later: the adjudicating evidence is **very sparse** (5 adjudicated table pairs
over 3 tool steps in 24 attempts), the learned model is **small** (3 surviving contracts), one of those three **acquires a
counterexample on the confirmation collection**, and nothing here shows that any of it changes a skill edit that
survives SkillOpt's merge, budget and validation gate. That remains untested.

---

## 2. Inventory: what was reused, what needed an adapter, what is ALFWorld-only

### 2.1 Source revision and provenance

The learned-contract implementation is **untracked work in its own worktree**: `git status` reports every file
below as `??`. So there is no commit that contains them, and `4a9914a` is what HEAD pointed at when they were
copied, not a commit they are in. `research/officeqa/transfer.py` records exactly that, per file:
`source_status`, `source_sha256`, `bytes`, `source_mtime`, plus the branch and `source_head` for the transfer.

**Correction to the state note.** `OFFICEQA_STATE.md` section A0 said the manifest's `source_commit` and per-file
`source` paths were null, and that the manifest was untracked. Both are wrong, and the error was mine: the fields
exist under the names `source_head` and `path` and are populated, and `OFFICEQA_TRANSFER_MANIFEST.json` is tracked.
There was no provenance gap to fix. The state note is corrected in this commit.

Nine further files were transferred for this port (manifest entry 2, 2026-11-16), bringing
`research/officeqa/transferred/` to fifteen. They are **reference copies, not on any execution path**; nothing in
`research/officeqa/` imports them.

### 2.2 The reuse table

| ALFWorld piece | OfficeQA counterpart | Reuse |
|---|---|---|
| `ir.py` - contract schema, Kleene logic, validation, `normalize`/`semantic_sha`, model record, freeze-by-digest | `oqa_lc_ir.py` | **Direct, ~90%.** Kleene truth tables, the validation discipline, the freeze rule and the "nothing is deleted" bookkeeping are byte-equivalent in behaviour. Changed: atoms are 0-ary so the role check has no counterpart; `context.task_types` became `context.answer_kinds`; the per-verb render head map. |
| `learn.py` - `apply_proposal`, the gate, assumption ops, auto-refutation | `oqa_lc_learn.py` | **Direct for the machinery, domain adapter for the content.** The gate ("zero determinate counterexamples over ALL evidence, at least one supporting attempt") and the assumption-op semantics are unchanged. `direct_contradictions` and `TEMPLATES` are rewritten; `MIN_ITEMS` and `assumption_recheck` are additions (2.4). |
| `replay.py` - `Revealer`, predict/classify, `Evidence`, `score_contract` | `oqa_lc_replay.py` | **Direct for commit-before-reveal and the class taxonomy; adapter for prediction.** The hash-chained order log, the discrepancy classes and the direction-aware scoring transfer unchanged. Prediction changed because of the structural difference in 1.1. |
| `state.py` / `fact_ledger.py` - fact kinds, statuses, the UNKNOWN discipline, obligation reopening | `oqa_lc_chain.py` | **Semantics reused, implementation new.** A4 3.1's kinds (OBSERVED / INFERRED / FORECAST), statuses and the rule that a contradiction reopens every obligation discharged through the contradicted fact are kept; the facts themselves are table cells, not receptacle contents. |
| `stage_contracts.py` / `stage_tracker.py` - stage requirements, handoffs, withdrawal, recovery | `oqa_lc_chain.obligations` | **Semantics reused.** The five OfficeQA requirements and their four-valued resolution are the document analogue; `oqa_check.STAGES` already had the stage names. |
| `vocab.py` - the shape of a frozen domain vocabulary | `oqa_lc_vocab.py` | **Shape reused, content entirely new.** `sha`/`canon`/`vocab_digest` are the same three functions; VERBS/ATOMS/ASSUMPTIONS are OfficeQA's. |
| `lean_gen.py` / `a4_lean.py` / `LearnedContracts.lean` | `oqa_lc_lean.py` | **Direct for `run_lean`/`parse_results` and the mirror discipline; the Lean content is new.** The `Tri` Kleene type with `DecidableEq`, core-only, no `sorry`/`native_decide`, and the rule that the Python mirror must agree are kept. |
| `lc_llm.py` - the guarded DeepSeek transport | **not wired in** | A paid learner is not needed for this milestone (2.4). The transport is transferred and audited, and section 6 says what a learner round would cost. |
| `plain_v4.py` / `score_v4.py` | already present | `oqa_check.py`'s P semantics and `structural_check` came from these in the earlier transfer; unchanged here. |
| `vocab.parse_command`, the ALFWorld atom set, `TASK_FORMS`, `APPLIANCE_OF`, the PDDL goal as trusted task source | **ALFWorld-only** | Receptacles, holding, heating. OfficeQA has no machine-readable goal at all, which is why `oqa_obligations.py` reads the question sentence and labels every obligation STATED or UNKNOWN. |

### 2.3 Where the ALFWorld code assumes ALFWorld

Recorded because a later session will hit these:

- **Informative failure.** `vocab.outcome_of` reads "Nothing happens." as an observed failure. OfficeQA's
  `oqa_lc_vocab.outcome_of` can only call an error string, `[no matches]` or an empty observation a failure, so
  `support_fail` is almost always empty and `requires` contracts cannot be supported by explaining a failure.
- **Entity-typed roles.** `ATOMS` maps atoms to argument kinds `("O", "R", "TYPE", "X")` and `validate_lit`
  enforces role membership. There are no entities in an OfficeQA observation to bind, hence 0-ary atoms.
- **A state that persists between steps.** `state.Estimator` carries facts forward under `persistence`. An OfficeQA
  atom is a property of one observation and does not persist, so the ledger's persistence is about CELLS (available
  from the step that returned them onwards), not about atoms.
- **`parse_task` / `TASK_FORMS`.** Templates over ALFWorld task sentences. OfficeQA questions are free prose.
- **`compose.py` and `stage_replay.py`** are built on receptacle-instance identity. Not transferred.

### 2.4 Deviations from the ALFWorld port, and why

Each of these is a deliberate departure, not an oversight.

1. **`TEMPLATES` instead of exhaustive enumeration.** The first version of `mech_induce` enumerated three verbs x
   ten atoms x two polarities. The gate accepted `grep.effect = not has_table` ("a successful grep returns no
   table") and `read.effect = not cap_reached` (true by construction - only grep has a cap). Both are accidents of
   the first few traces. The atoms left out - `has_table`, `header_flat`, `header_split`, `scale_stated`,
   `row_period_bound` - describe what a particular window happens to contain, which is a fact about where the actor
   chose to look. This is the "8 of 14 rules never fire" disease of `docs/research/OFFICEQA_CONTRACT_AUDIT.md`, and
   the template set is the fix.
2. **`MIN_ITEMS = 2`.** A candidate needs support from two distinct items. Design v0.1 section 8, risk 5:
   "repeated observations of one task are not independent task-level confirmation."
3. **`assumption_recheck`, an addition.** `apply_proposal` tests `direct_contradictions` only when an assumption is
   **adopted**. In the ALFWorld line that sufficed, because the LLM learner saw the previous epoch's refuted
   contracts and could propose the drop itself. With a deterministic proposer nothing re-tests an assumption adopted
   before its counterexample arrived, and in the first run of this loop `index_alignment` stayed in force while the
   records contradicted it in sixteen places. A4 3.1 requires the opposite.
4. **`mech_repair` targets refuted contracts, not active ones.** `apply_proposal` auto-refutes every active contract
   that acquires a counterexample, so a repair proposer that looks at `active(model)` never fires at all - the
   first run's did not. The model keeps refuted versions with their counterexamples precisely so a repair can
   target them.
5. **No paid learner call.** The OfficeQA vocabulary is small enough that the deterministic template proposer
   enumerates it completely, so this milestone needs no DeepSeek call. Whether a learner would propose anything the
   enumeration does not is an **open question**, not an assumption; section 6 prices one round.

---

## 3. Evidence and stages for OfficeQA

### 3.1 The binding the old checker lacked

`oqa_check.build_ledger` records a number's file, page, line and the years that happen to appear on the same line.
It records neither the ROW the number sits in nor the COLUMN it belongs to, which is why
`operand_period_mismatch` can never fire on a monthly table: the ledger binds only the year, so it reads
`1961-January` and `1960-December` as the same period. `oqa_lc_cells.py` supplies the missing link. A cell fact is

```
{"value", "raw", "row_label", "period" {year, month, inherited_year}, "period_text",
 "column", "column_basis", "scale", "table_index", "row_index", "col_index", "doc_line", "source"}
```

and the chain is **cell -> operand -> conversion -> computation -> answer**, each link recorded or recorded as
missing. Worked on UID0026's own supplied page, which is also the case two blind reviewers disagreed about in
`docs/research/OFFICEQA_REPORT_VALUE_PROBE.md`:

| operand | row | period | column | scale | basis |
|---|---|---|---|---|---|
| `32,869` | `1960-December` | December 1960 | `Total money in circulation 1/` | millions | FLAT |
| `30,442` | `1960-December` | December 1960 | `Paper money 2/ > Total paper money` | millions | FLAT |
| `29,385` | `1961-January` | January 1961 | `Paper money 2/ > Total paper money` | millions | FLAT |

The question asks about **paper money**, so `30,442` is the right December-1960 figure and `32,869` is the wrong
column of the right row. The probe's reviewers split on exactly that; the binding settles it deterministically,
with the document's own column path as the witness.

### 3.2 Four sources, kept apart

Every obligation line carries its basis, and the report prints them verbatim so a reader can reject a line whose
basis they reject:

| label | meaning | example |
|---|---|---|
| **TASK** | the question sentence, via `oqa_obligations.derive`; never the skill | "requested scale: billions [STATED: 'in billions']" |
| **TOOL** | documented tool behaviour, verified against `tool_runtime.py` | the 20-line grep cap; the 4000-character read truncation |
| **OBS** | a supplied page or a returned observation | "`30,442` in row `1960-December`, column `Paper money 2/ > Total paper money`" |
| **ACTOR** | something the target wrote down - a claim, not evidence | "step 1 converts it as `2,410,259 = $2,410.259 billion`" |
| **ASSUMED(x)** | licensed by a named model assumption | `ASSUMED(index_alignment)` on a column read from a split header |
| **RULE(id@v)** | licensed by a named learned contract | `grep.effect.1@v1` |

A column binding records *how* it was obtained - `FLAT`, `INDEX_MERGE` or `UNKNOWN` - and a missing link is
UNKNOWN, never false (A4 3.1). `test_without_the_index_alignment_assumption_a_split_column_is_unknown_not_wrong`
holds that line.

### 3.3 The format limitation, with two concrete examples

**`effect` contracts take no condition in this vocabulary.** That restriction is inherited from the ALFWorld IR and
is **not changed here**. It bites in exactly two places on this evidence, and both are cases where the claim that
is true needs a guard:

**Example A - column binding.** The true claim is *"index-merging a split header is sound when the header is in
flattened form, and undetermined otherwise."* Written as an unguarded effect it becomes "a read observation
confirms the index merge", which UID0026 refutes. The repair available inside the format is to drop the assumption,
which is sound but weaker: it makes **every** split-form column UNKNOWN rather than saying where the merge is fine.
Measured cost on UID0014's later attempt: of 44 bound operands, 44 have a column with the assumption and 10
without it, so **34 lose it**.

**Example B - row periods.** The true claim is *"in a table whose first column header names a period, every data
row label yields a period."* The unguarded version is induced in both polarities and refuted in both: `not
row_period_bound` holds of the FFO receipts tables, whose rows are categories ("Individual income taxes"), and is
refuted by the money tables, whose rows are months. Neither polarity is a claim about the corpus, so the atom is
excluded from `TEMPLATES` and the loop never learns anything about row periods at all.

**The smallest extension that would lift it** (proposed, not implemented, semantics unchanged until approved):

> Allow an `effect` contract an optional `guard`, a CNF over the same 0-ary atoms, evaluated on the same
> observation at the same time as the effects. Semantics: outcome ok **and** guard true **and** some effect literal
> false -> counterexample; outcome ok and guard true and all effects true -> support; guard false or unknown ->
> **neutral**, never support and never counterexample. Everything else is untouched: the gate, the Kleene
> evaluation, `normalize`/`semantic_sha` (which gains one sorted field), the freeze rule, the Lean row export.

That is four lines in `validate_contract`, one branch in `score_contract`, one field in `normalize`, and one extra
`Tri` in the Lean row. With it, Example A becomes `effect [col_leaf_sound] guard [[header_flat]]` - a refinement
with support and no counterexample on this evidence - instead of a dropped assumption. I would want the guard's
UNKNOWN case tested hard before trusting it, because "guard unknown -> neutral" is exactly where a checker can
quietly stop checking.

---

## 4. The full loop on saved development traces

### 4.1 The replay rule, declared before the loop ran

> Attempts are processed in the order the run recorded its results (`results.jsonl`), which with four workers is
> the rollout's completion order and **not a curriculum I chose**. After each attempt the deterministic proposer
> runs on the evidence seen **so far** and the resulting model is frozen by digest, read-only. The **next** attempt
> is then replayed against that frozen model: each step's prediction is committed, with its sha256 in a hash chain,
> before that step's observation is released. A model therefore never predicts an observation it was learned from.
> A later collection is replayed afterwards against the final model as independent confirmation. Held-out items are
> never read.

Collections, both **development evidence** and both previously audited (so labelled, per design v0.1 section 7
Stage B, "Keep reused development cases distinct from independent confirmation"):

- **development**: the shared epoch-1 rollout of the 12 frozen training items, job 66209754, run
  `20261116-104926_officeqa_shared_66209754/epoch1`. Recorded order: UID0033, UID0063, UID0034, UID0031, UID0017,
  UID0079, **UID0026**, UID0014, UID0028, UID0065, UID0085, UID0030.
- **later**: arm B's epoch-2 rollout of the **same 12 items under the same S0 skill**, job 66228142,
  `arm_B/steps/step_0002/rollout`. A second sample of the same questions, not new questions.

### 4.2 What happened, round by round

| round | item | frozen model replayed | forecasts C / V / U | decision taken afterwards |
|---|---|---|---|---|
| 0 | UID0033 | - | - | 5 assumptions adopted; no record contradicted any of them |
| 1 | UID0063 | v1 | 0 / 0 / 3 | `read.effect.1 = width_uniform` added |
| 2 | UID0034 | v2 | 0 / 0 / 2 | `grep.effect.1 = pattern_in_every_line`, `grep.effect.2 = not cap_reached` added |
| 3 | UID0031 | v3 | 13 / **1** / 14 | `grep.effect.2` auto-refuted (UID0031@28 hit the cap) and retired |
| 4 | UID0017 | v3 | 6 / 2 / 10 | `read.effect.2 = sub_row_aligned` added |
| 5 | UID0079 | v5 | 0 / 0 / 0 | no tool call in this attempt |
| **6** | **UID0026** | **v5** | **12 / 2 / 46** | **`index_alignment` dropped, 12 witnesses** |
| 7 | UID0014 | v6 | 0 / 0 / 0 | no adjudicating observation |
| 8 | UID0028 | v6 | 23 / 0 / 16 | `year_inheritance` dropped, 2 witnesses |
| 9-11 | UID0065, UID0085, UID0030 | v7 | 13 / 0 / 32 | nothing further; the repaired model holds |

Totals over the development replay: **67 forecasts confirmed, 5 violated, 123 unresolved** across 11 replayed
attempts (round 0 has no model to replay against yet).

Final model **v7**: assumptions `flat_header_authoritative`, `grep_cap_not_reached`,
`table_scale_applies_to_rows`; active contracts `read.effect.1 = width_uniform`,
`grep.effect.1 = pattern_in_every_line`, `read.effect.2 = sub_row_aligned`. Every frozen version is on disk under
its own digest in `frozen/`, and every committed prediction is in `predictions/*.jsonl`, written before its
observation was revealed.

### 4.3 The counterexample, with its evidence

At round 6 the frozen model v5 asserted `index_alignment`, so for every tool step it committed the forecast
*"col_leaf_sound = true"* - that merging the supplied page's split header by cell index will agree with any
flattened header the step returns. UID0026's read at step 44 returned that flattened header. Both are renderings of
**the same table**, paired by a shared data row whose label and figures are equal once commas are stripped (the
page writes `1,858`, the transformed document writes `1858`):

```
supplied page (split form), treasury_bulletin_1963_01.txt p.88
  | End of fiscal year or month | Coin | Money in circulation per capita (in dollars) 2/ |  |  |  |
  | --- | --- | --- | --- | --- | --- |
  | Total coin | Standard silver dollars | Subsidiary silver | Minor coin |  |  |
  | 1955 | 1,858 | 223 | 1,202 | 433 | 182.90 |

the same table, flattened, in the observation at step 48
  | End of fiscal year or month > End of fiscal year or month | Coin > Total coin | Coin > Standard silver dollars
  | Coin > Subsidiary silver | Coin > Minor coin | Money in circulation per capita (in dollars) 2/ > ... |
```

The sub-label row has lost the blank that belongs under the row-label column, so index merging shifts every label
one column left:

| column | index merge says | the document's own flattened header says |
|---|---|---|
| 0 | `Total coin` | `End of fiscal year or month` |
| 1 | `Standard silver dollars` | `Coin > Total coin` |
| 2 | `Subsidiary silver` | `Coin > Standard silver dollars` |
| 3 | `Minor coin` | `Coin > Subsidiary silver` |
| 4 | (blank) | `Coin > Minor coin` |
| 5 | (blank) | `Money in circulation per capita (in dollars) 2/` |

So `1,858` is bound to "Standard silver dollars" when it is "Total coin". Twelve such columns over two tables at
steps 44 and 48.

**The rule is not simply wrong, and that is what makes the diagnosis precise.** The paper-money table on the same
page keeps its leading blanks, and there the merge gets every leaf right - it supports the assumption. I looked for
a feature of the split form that separates the two cases and there is none: whether the renderer dropped the
leading blank depends on whether the column's level-1 label repeats the level-0 one, which is visible only in the
flattened form. **The split form does not determine the alignment.** Hence the repair.

### 4.4 The repair, and rechecking the earlier cases

`assumption_recheck` proposed `{"op": "drop", "name": "index_alignment"}` with 12 witnesses; `apply_proposal`
accepted it (an assumption the records contradict may always be dropped; what is refused is *adopting* one).
Rechecks, over all evidence seen up to that round, before and after:

- `read.effect.1 = width_uniform`: 20 support / 0 counterexamples, unchanged.
- `grep.effect.1 = pattern_in_every_line`: 15 support / 0, unchanged.
- `read.effect.2 = sub_row_aligned`: 2 support / 0, unchanged.
- No contract gained or lost a counterexample: the dropped assumption licensed **bindings**, not these claims.
- **The cost is paid in obligations, not contracts.** Every obligation discharged through
  `ASSUMED(index_alignment)` is reopened to UNKNOWN by `oqa_lc_chain.reopen`, never to a violation. On UID0014's
  later attempt that is 34 of its 44 bound operands losing their column (44 have one with the assumption, 10
  without).

At round 8 `year_inheritance` was dropped too, on a different table type: Table AY-1 "Average Yields of Treasury,
Corporate and Municipal Bonds by Periods" has a header block in the same observation and its first data row label
is `Jan.` with no year anywhere in the table - the year lives in a column-group header. A row that names only a
month has nothing to inherit from, so the assumption is refuted there.

One honest note on that second drop: the **first** version of this check fired on two witnesses that were artefacts
of the 100-line `read` window - a truncated table's first visible row is not the table's first row. The check now
requires the table's header block to be in the same observation.
`test_a_truncated_window_is_not_a_counterexample_to_year_inheritance` holds that line.

### 4.5 Independent confirmation, and one result that goes the other way

Replaying the **later** collection against the final v7 model, with no further learning:

- **82 forecasts confirmed, 1 violated, 71 unresolved.**
- `index_alignment`, re-tested on data the repair never saw, is contradicted **10 more times**, all in UID0014's
  read at step 16 of `treasury_bulletin_2001_12.txt` p.17: the index merge names `Railroad retirement` where the
  flattened header names `Fiscal year or month`, and so on across eleven columns. The repair generalises beyond the
  item that forced it.
- `year_inheritance`, re-tested, is contradicted 2 more times (UID0028 steps 22 and 40).
- **The one violation is against the surviving model.** `read.effect.1 = width_uniform` is violated at UID0028@42.
  If the later collection were admitted as evidence, that contract would be auto-refuted. So the final model is
  **not** fully confirmed on later data, and I am reporting that rather than only the 82. Design v0.1 4.E:
  "Agreement after fitting and prediction on later data are separate measurements."

### 4.6 Could the traces distinguish competing rules?

Partly, and the gaps are worth recording because they bound what any amount of cleverness could get from these
traces:

- **Adjudicating evidence is rare.** `col_leaf_sound` is determined at **5 table pairs over 3 tool steps** in 24
  attempts, because it needs the actor to read a flattened header of a table it was also handed in split form. In
  the development collection **126 of 128 tool steps leave it UNKNOWN**.
- **`col_leaf_sound` versus `col_path_sound` IS distinguished**, and that was worth separating: the leaf (which
  series a figure belongs to) agrees on 2 of 5 pairs, the full path with its spanning parent on 0 of 5. Conflating
  them - which the first version of the comparison did - made the rule look uniformly wrong when half its failures
  are a lost qualifier rather than a wrong column.
- **What is missing to distinguish the guarded rule of 3.3 Example A from the unguarded one** is observations where
  a split-form page is adjudicated by a flattened header **and** the merge succeeds, from more than one item. There
  is one such pair, in one item, so `MIN_ITEMS = 2` correctly refuses it. Getting more needs either traces in which
  the actor reads header lines more often, or admitting the corpus itself as evidence - and the second changes the
  information boundary, so it is a design question, not an implementation one.

---

## 5. Lean

### 5.1 What is in the file

`lean/OqaContracts.lean`, generated by `oqa_lc_lean.py`, Lean 4 **core only** - no Mathlib, no `sorry`, no
`native_decide`, no `axiom`. 64 statements, every one closed by `by decide` or `rfl`:

- **25 general**, stated over the finite Kleene domain that acceptability factors through, with
  `acceptable_factors` as the `rfl` bridge from the operand model to that domain. They are therefore closed by
  **exhaustion**, not by a tactic script that might not go through:
  1. a violated unit requirement makes the operand unacceptable, over all 9 remaining combinations;
  2. an UNKNOWN column binding can never discharge - UNKNOWN or FALSE, never TRUE (A4 3.1's invariant);
  3. `reopen_is_unknown`: withdrawing the index-merge basis reopens the binding to UNKNOWN;
  4. reopening never manufactures a violation, over the combinations where the other premises are not already
     false;
  5. the unit exponent arithmetic on the two real cases: millions-to-billions is `10^(6-9)`, thousands-to-billions
     is `10^(3-9)`, so applying `10^-3` to a thousands figure is a violation.
- **39 over the exported evidence**: 2 per distinct operand shape (before and after a repair) over 16 shapes drawn
  from 263 operands, and 1 per distinct adjudicated forecast over 7 shapes drawn from 155 checks.

### 5.2 What Lean verifies and what evidence extraction supplies

Kept separate, because the distinction is the whole point of design v0.1 section 5:

| | |
|---|---|
| **Lean would verify** | that the Kleene composition has the stated properties over its finite domain; that the recorded atom values and the contract's polarity really do produce the class the Python side recorded, for each exported row; that the exponent arithmetic is right. |
| **Evidence extraction supplies, and Lean does not check** | that a row's label is the period it is read as; that a flattened header cell states its column's path; that an operand is the cell it was matched to; that a message line is the conversion it was parsed as. Every atom value in the EVIDENCE section is a **trusted export** of `oqa_lc_cells`. |
| **Nothing here establishes** | that the Treasury corpus obeys any learned hypothesis (a finite set of observations supports an empirical claim, it does not prove one); that a reported violation caused the item to be answered wrongly; that the readable report preserves the checked proposition's scope. |

### 5.3 COMPILED - job 66259448, Lean 4.20.0, all 64 statements, rc 0

**USER-AUTHORIZED 2026-11-16** (1 CPU core, 2 GB, up to 15 minutes, 0 GPUs, 0 paid calls) and **done**.

| | |
|---|---|
| job | **66259448**, `oqa-lean`, partition `standard`, Great Lakes |
| requested / allocated | 1 CPU, 2 GB, 15 min walltime / `cpu=1, mem=2G, node=1`, **0 GPUs** |
| state | **COMPLETED, ExitCode 0:0** |
| elapsed / TotalCPU | 00:00:06 / 00:00.907; `lean` itself ran **4.24 s** |
| Lean version | **Lean 4.20.0, x86_64-unknown-linux-gnu, commit 77cfc4d1a4f6, Release** |
| file compiled | `OqaContracts.lean`, sha256 `e40a7e62faed92ffdc53093b4937f8eb6b655ba5eabda14d7036444c0b9eecbb` |
| compiler output | **0 bytes** - Lean prints nothing when every declaration checks |
| error lines / warning lines / `sorry` lines | **0 / 0 / 0** |
| statements compiled | **23** in `section General` + **2** `rfl` bridge theorems + **39** over the exported evidence = **64** |
| paid calls | 0 |

**The identity of the compiled file is checkable at every link, not asserted.** The digest was recorded locally
before submission; the generator at `47c6ed8` reproduces it byte-for-byte (regenerated twice, same digest); it is
committed at `87b9ad6` as `research/officeqa/lean/OqaContracts.lean` with `MANIFEST.sha256` beside it; the job
verified that manifest before compiling (`manifest_check.txt`: `OqaContracts.lean: OK`) and logged the digest it
compiled; and the copy it actually fed to `lean` has the same digest. All six are
`e40a7e62faed92ffdc53093b4937f8eb6b655ba5eabda14d7036444c0b9eecbb`.

**One failed submission first, and it was not a Lean result.** Job **66259421** failed in 1 second with exit 3,
before `lean` ran: a Slurm batch script is copied into the node's spool directory, so `${BASH_SOURCE[0]}` resolved
there instead of to the bundle and the private site file could not be read
(`/var/spool/slurmd.spool/job66259421/oqa_lean_site.env: No such file or directory`). Fixed by using
`$SLURM_SUBMIT_DIR`; the `.lean` file was never touched and its digest was re-verified on the cluster before the
retry. The failed job's log is preserved beside the successful one. It consumed ~1 CPU-second.

**Not run from a git snapshot.** `research/deploy.sh` refuses a commit that is not on `origin`, and pushing was
not authorized for this task, so the job ran from a read-only **staging bundle** - the same mechanism the
learned-contract line used for its Lean jobs - carrying the `.lean` file, its manifest and a self-contained job
script. `research/cluster/oqa_lean.sbatch` is committed for the snapshot route whenever a push is authorized.
The Lean binary path came from `OQA_LEAN_BIN` in the private, untracked cluster site settings; it appears in no
tracked file and in no log, and the only thing printed about it is its `--version` string.

### 5.4 What this run does and does not establish

**Established by the compiler.** Every one of the 64 statements type-checks and every proof closes: the 23
exhaustions and 2 bridge lemmas of the general section, and the 39 `by decide` checks over the exported evidence.
With zero warnings, there is no `sorry` anywhere - Lean emits a warning for every `sorry`, and the output was
empty.

**NOT established, and I am not claiming it.** The run did **not** check which axioms the declarations use. The
job compiles the file exactly as its digest records, and adding `#print axioms` would change the file and break
that guarantee, so `oqa_lc_lean.parse_results`' axiom test had nothing to parse. Nothing in the file declares an
`axiom` and `by decide`/`rfl` introduce none, but that is an argument, not a check. One more job of the same shape
with an axiom-printing variant (a separate file, separately digested) would settle it; it is not authorized and I
have not submitted it.

### 5.5 The earlier request, now satisfied

`lean`, `lake` and `elan` are absent on the workstation **and on the Great Lakes login node** (checked, not
assumed); the learned-contract line's Lean jobs got their binary from a private `lc_site.env` inside a Slurm CPU
job. So:

- **The Python mirror**, which was the only check available before the job ran, agrees with the file on every
  row: `general_mirror()["all_ok"] == true` and **0 mismatches**. Lean has now independently confirmed the same
  statements, so the mirror's role is reduced to catching generator drift before a job is submitted.
- **The authorization.** OQA-5 covered neither a CPU job nor a learner call, so this needed its own grant, and
  the user gave one on 2026-11-16: 1 CPU core, 2 GB, up to 15 minutes, 0 GPUs, 0 paid calls. It was used once
  (plus ~1 CPU-second on the failed first submission) and is now spent. OQA-5's own caps are untouched: 6.23 of 8
  A40-hours and 8 of 24 DeepSeek calls remain unused.
- **Where the Lean toolchain actually is.** Not on the workstation and not on the Great Lakes login node. The
  installed toolchain is **Lean 4.20.0** under the project's own `elan` directory on cluster storage, registered
  as `OQA_LEAN_BIN` in the private site settings. The learned-contract state note records "Lean 4.35.0"; no such
  toolchain exists there now, and 4.20.0 is what compiled this file.

---

## 6. The regression case, the plain-report comparison, and the skill edit

### 6.1 The known false warning about an already-completed conversion

Item **UID0014**, the later collection's attempt (the one the audits documented). The question asks for figures
"in billions of nominal dollars".

| | old C report (`oqa_check.report(packet, "C")`) | obligation form |
|---|---|---|
| `scale_conversion_missing` | **29 findings**, every one "No conversion step appears: divide by ... before answering" | - |
| `unit_match` | - | **13 DISCHARGED, 23 VIOLATED, 8 OUTSTANDING** |

The 13 discharges are the false warning, resolved, with the witness the old rule never looked at:

> **DISCHARGED** [unit_match] the value used must be in the asked scale (billions)
> step 1 converts it with the correct factor: `- For 2000: Net income taxes = 1,004,461 (in millions) = $1,004.461 billion`
> basis: TASK, OBS, ACTOR

The old rule read a value's **evidence** provenance and never `state["computed"]`. The new one consults the actor's
own conversions and, separately, the scale factor of the operand's own binding - because `2331.274` matching the
cell `2,331,274` at factor `1e-3` **is** the conversion the actor applied.

### 6.2 What replaced it: a valid finding neither report had

> **VIOLATED** [unit_match] -- the same for 13 value(s)
> step 1 converts it as `- 1930: Individual = 2,410,259 = $2,410.259 billion`; thousands to billions requires a
> factor of 1e-06 and the factor used is 0.001, so the converted value is wrong by 1,000x  (the source scale on
> that line was carried from an earlier line, not restated)
> basis: TASK, OBS, ACTOR

Verified by hand: `2,331,274` thousands is `$2.33` billion, not `$2,331.274` billion. The actor converted
millions-to-billions correctly (divide by 1,000) and then reused that same divisor for a thousands table, so all
fourteen regression inputs for fiscal 1929-1942 are 1,000x too large. A second group of 9 carries the same
diagnosis reached the other way, through the binding's own factor rather than through a conversion line.

**Against the plain report P on the same trace:** P produces one category, `no_matches`, in 2,024 characters. It
says nothing about units. So on this trace the obligation form **avoids a false warning and adds a true one that
neither P nor the old C report had** - which is a different outcome from `OFFICEQA_REPORT_VALUE_PROBE.md`, where 0
of 8 cases gained a report-attributable diagnosis and 1 gained a misleading one.

Three other false-positive classes were fixed on the way, each with a regression test:

| class | old behaviour | now |
|---|---|---|
| arithmetic intermediates | 17 operands reported as having no provenance, including regression residuals | 16 of them UNKNOWN: "the message it appears in also does arithmetic, so it may be an intermediate" |
| a year inside an asked span | every year of "fiscal years 1929-1942" flagged, 12 loud false positives | UNKNOWN: whether the question names two points or a range cannot be decided from its wording |
| over-permissive scaled matching | the value `1` (a whole column of them in these tables) matched any operand near a million, reported as "converted wrong by a billion" | a scaled match additionally requires equal significant digits |

### 6.3 The skill edit this could support

From 6.2, and only from 6.2 - I am not claiming the others reach a skill edit:

> When a table's figures are in a different unit from the one the question asks for, write the conversion factor
> explicitly as a power of ten before using it (thousands to billions: divide by 1,000,000; millions to billions:
> divide by 1,000), and do not reuse a factor across tables - check it against the table's own stated unit each
> time you move to a new table.

Why this one and not the others: it is a reusable instruction, it is grounded in a verifiable arithmetic fact
rather than in a judgement about the document, and the failure it addresses is **sourced to a stage requirement
combined with an action check** - the unit obligation from the question (TASK) plus the cell's stated scale from
the observation (OBS) plus the actor's own conversion line (ACTOR). Neither the question alone nor the trace alone
is enough: you need the cell's scale to know the factor was wrong.

**What this does not show.** Whether DeepSeek, given this report over a real minibatch under the real analyst
prompt, would propose that edit; whether it would survive merge, the edit budget and the 6/12 validation gate; and
whether it would change the held-out score. Reviewers and checkers answered a different question with a different
output. `docs/research/OFFICEQA_EPOCH2_REPORT_AUDIT.md` section 6 prices the replay that would settle it.

---

## 7. Blockers and what remains

| | |
|---|---|
| **Blocked on authorization** | nothing. The Lean compile was authorized and done (job 66259448, section 5.3). An axiom check would need one further job of the same shape (section 5.4). |
| **Blocked on a design decision** | the guarded-`effect` extension of 3.3. Documented, not implemented; it changes contract semantics, so it needs approval. |
| **Open question, priced** | whether a DeepSeek learner proposes hypotheses the template proposer does not. One round over the 12 development attempts is 1-2 calls at ~$0.02 each, inside the remaining 8-call OQA-5 budget, but the OfficeQA authorization was granted for the two-arm comparison and I would want that said explicitly before spending it. |
| **Not in scope, per the instruction** | new rollouts, skill-optimization campaigns, held-out evaluation. Job 66234927's held-out score remains **unread**. |
| **Preserved** | `~/dev/lean_skillopt/runs/20261116-offline-lc-port/` - `LOOP.json`, 12 frozen models under their own digests, 11 committed-prediction logs for the development replay and 12 for the confirmation replay, the generated Lean file and the worked report. Original run artifacts untouched; `git diff` touches no tracked file other than the transfer manifest. |

### Tests

`tests/test_officeqa_lc.py`: **38 pass**, constructed inputs only (design v0.1 section 7 Stage A - they establish
software behaviour, not research findings). Three are regression tests for defects found while building this port
and say so: two tables in one pipe block must not be merged; a scaled match requires equal digits; a truncated
read window is not a counterexample to year inheritance. The existing OfficeQA suite - 87 tests across
`test_officeqa_{contracts,tool_probe,answer_key_isolation,freeze_sets,optimizer_precheck,shared_rollout_injection}`
- **still passes unchanged**, which is the check that the port is additive: `oqa_check.py` and
`oqa_obligations.py` were not modified, so arm A stays byte-identical to upstream and the two-arm run's P/S/C
semantics are untouched.
