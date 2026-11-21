# OfficeQA: state note (resume from here)

**Last update: 2026-11-21 03:25 EST. Branch `officeqa-contracts`, worktree `SkillOpt-officeqa`,
commit `5497ee9`. NOT pushed. Tracked tree clean. JOBS LIVE — check `squeue` before doing anything.**

## START HERE -> `docs/research/OFFICEQA_BASELINE_LAUNCH.md` (rules OQA-SEL-4, OQA-BASE-1, OQA-T2S-1)
## CONTRACT ARM -> amendment **C-1** in `docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md`

**BOTH BASELINES ARE RUNNING. Do NOT resubmit them.** Live state:
`squeue -u $USER | grep oqa-` and
`sacct -X -j 66552420,66552421,66552422,66552423,66552424,66552425,66552763,66552633,66552721`.

| job | stage | state at 03:25 |
|---|---|---|
| 66552420 | prep (free) | **COMPLETED** — preflight READY 39/0/0, 2,428 tests |
| 66552421 | s0 reference | **COMPLETED** 23:16 — 48 training + 12 validation episodes, all 12/12 |
| 66552422 | Original SkillOpt epoch 1 | **COMPLETED** 3:40 — wrote `best_skill.md`, `meta_skill/`, `slow_update/` |
| 66552423 | Original SkillOpt epoch 2 | **RUNNING** — the first gated slow update |
| 66552424 / 66552425 | epochs 3 / 4 | PENDING on the chain |
| **66552763** | **Trace2Skill** | the LIVE attempt, from bundle `oqa_base_20261121e` |
| 66552633 | contract: v7 + occurrence measurement (free) | **COMPLETED** |
| 66552721 | contract: v8 + C-1 replay (free) | COMPLETED its work; exited 1 on a test and a false alarm, both since fixed |

**THREE FAILED t2s ATTEMPTS, each a different defect of MINE, none of which spent anything:**
* **66552508** — the boundary guard conflated a CORRECT ANSWER with a leaked key. On a correct episode
  the actor's own answer IS the ground-truth string, so a string scan refuses exactly the episodes the
  success analyst needs. Now a PROVENANCE guard: a key is a violation only if the permitted corpus
  cannot explain it. Verified on the real 48: **0 violations, 7 explained hits.**
* **66552741** — `chat_optimizer` is a ROUTER whose default is **Azure**; the config does not reach it
  without `set_optimizer_backend` + `configure_openai_compatible`. 48 calls raised, 0 paid. The
  preflight had the same defect hours earlier and I failed to carry the fix across.
* The dry run reported healthy both times because it short-circuited before configuring. It now
  configures too, and the stage refuses to proceed if the guard fires.

**Bundles — NEVER rebuild one while its jobs are queued or running (bash reads the body lazily):**
* `oqa_base_20261121b` commit `a8f80fa` — the LIVE bundle for s0 and the four epochs.
* `oqa_base_20261121c` commit `3765139` — the LIVE bundle for Trace2Skill.
* `oqa_base_20261121` commit `ad65252` — the FAILED first prep. Kept as the record; do not reuse.
Run root `runs/20261121-officeqa-two-baselines/`; FRESH ledger at its `ledger/`, starting at zero.

**Resume after a TIMEOUT: resubmit the SAME stage with the SAME output directory.** `eval_only.py` and
`run_batch` resume by id; every Trace2Skill call is cached by prompt digest. The body refuses to run
under `SLURM_RESTART_COUNT != 0`, so a requeue can never re-spend — recovery is always deliberate.
Submit with `OQA_BUNDLE=<bundle> bash <bundle>/oqa_base_submit.sh prep|s0|arm|t2s|heldout|chain`.

### THE FROZEN SETUP, in one place
Rule **OQA-SEL-4**, `docs/research/OFFICEQA_FROZEN_SETS_V4.json`, digest `b0dbbd4be97fc245`:
**train 12 / val 12 / held-out 30**, disjoint, 0 problems, train and val 3/3/3/3 and held-out 8/8/7/7
over released `category` x document count. Train and val are OQA-SEL-2's carried forward unchanged;
held-out retains OQA-SEL-2's 24 and draws 6 more from OQA-SEL-3's 60. Every set is a SUBSET of the
freeze it subsets and **no item changed the role an earlier freeze gave it**. 6 of 30 held-out are
UNTESTED for availability. Train and held-out share **exactly one document** (UID0014 / UID0013).

Config `configs/research/officeqa_base2.yaml`: four epochs, one ordinary update per epoch
(batch_size 40 > 12), gated slow updates from epoch 2 with **slow_update_samples 6**, meta-skill on,
upstream's strict-improvement rule, gate on the separate 12 val questions, `eval_test: false`.

**EFFECTIVE actor settings, verified in outbound requests — not read off the config:** `qwen3-5-4b` on
EmpirioLabs, temperature 0.7, **output cap 8,000**, `enable_thinking: false` transmitted AND effective
(0 alternate-reasoning characters in 285 responses), narrow `committed_answer` recovery, 24 tool turns,
S0 unchanged at `9d377d1c2906`. Document/tool rendering is FROZEN for this campaign.

> **Do not mistake the two token fields again.** `env.max_completion_tokens: 16384` reaches NO prompt
> and NO API call — its only consumer is `rollout_settings_fingerprint`. The transmitted cap is
> `model.target_openai_compatible_max_tokens` = **8,000**. The inert 16384 is left in place on purpose:
> changing it would invalidate the fingerprint of every saved S0 trajectory.

### TRACE2SKILL IS PINNED, AND ITS ADAPTATIONS ARE DECLARED
Upstream `Qwen-Applications/Trace2Skill` at **`3d0b52a140f002a512930252b613c49048f7d5ac`**, Apache 2.0,
arXiv:2603.25158. Structure is upstream's `ParallelSkillEvolver`: analysis per trajectory -> MAP per
record -> **hierarchical** REDUCE (48 -> 6 batch merges at size 8 -> 1 final) -> apply -> verify.
Four declared adaptations in `research/officeqa/oqa_t2s_prompts.py`: OfficeQA domain; upstream's
**single-call** analyst rather than its agentic one (the agentic analyst is handed the ground truth);
a single markdown skill document rather than a skill directory; DeepSeek Flash as the analyst.
It borrows NOTHING from SkillOpt — a test fails if `valid_seen`, `selection_eval`, `slow_update`,
`use_gate` or `num_epochs` appears in the module.

### RESOURCES — this phase has its OWN allowance and ledger
Authorized 2026-11-21: **50 requested CPU job-hours, 0 GPUs, 20,000 actor attempts AND $15, 400
DeepSeek calls AND $20.** Reserved by submitted jobs: **10.58 CPU hours**; three held-out endpoints will
add 4:30 for **15:05 of 50**. Whole-campaign projection, computed and checked by the preflight: 396
episodes, 5,544 attempts, $3.49, **117** optimizer calls.

**This allowance is SEPARATE from the historical accounting and does not settle it.** Historical and
unchanged: EmpirioLabs 12,042 of 30,024 attempts and $8.0147 of $17.00; DeepSeek 105 of 266 and $3.4689
of $10.00; CPU **disputed** — 35.649 of 44 core-hours or 66.483 of 44 requested walltime. **Which
convention governs the 44-hour ceiling is still the user's decision.**

### SEALED
**Held-out scores and trajectories stay sealed while the contract arm is developed.** Do not use them
to choose contract features, prompts or repairs. Contract development uses the 48 new S0 training
trajectories and the saved 24-question collection only.

### THE CONTRACT ARM: WHAT IS BUILT AND WHAT IS NOT -> amendment C-1
**Occurrence-level source binding works where column semantics did not.** `research/officeqa/oqa_occ.py`.
A grep observation prefixes each line with its LINE NUMBER, so a cell has an observable position -
(document, line, field). Over the 24 saved TRAINING records: 4,261 observed occurrences, 201 at a
unique position, 122 at several (UNKNOWN), 794 with none (actor-derived), **64 SUPPORTED** against the
frozen prototype's 0. A unique position is NOT a binding: support needs ROW_LABEL or QUERY
corroboration. `SOLE_IN_ROW` was rejected as a near-tautology (entailed 0.995) after it lifted the rate
to a suspicious 100%.

**Amendment C-1 is implemented and replayed.** `oqa_lc_chain.select_cell` refuses to pick a source cell
by numeric equality. Over 659 operands: selections **566 -> 102**, VIOLATED **186 -> 30**. The old
tie-break was choosing on numeric equality alone in 82% of its selections.

**TWO DEFECTS IN C-1 REMAIN OPEN, both measured, both specified in the amendment, deliberately NOT
rushed:**
* **A** — withdrawn bindings make downstream obligations VANISH rather than UNKNOWN (ledger 1,839 ->
  911). Repair: `obligations()` must emit them as UNKNOWN naming the refusal.
* **B** — 199 of 499 ties (39.9%) are the SAME cell observed through different routes. Repair:
  deduplicate candidates by cell identity before declaring a tie.

**Still missing from the §4 demonstration:** a genuinely learned rule with its evidence, a supported
diagnosis on a later record, a successful/recovered control, and Lean compiled against the C-1 facts.
Lean is at `/nfs/turbo/umms-csriram/yaojm/lean/elan/bin/lean`; `OqaContracts.lean` ALREADY contains
`unknownBindingNeverViolates`, which is exactly C-1's soundness condition.

### NEXT ACTIONS
1. Monitor the chains. On a TIMEOUT, resubmit the same stage (see resume note above).
2. **Correct the mixed-version bundle before deploying contract code**:
   `collect_20261118/v2_bind/CC_BUNDLE_v6.json` is run 1's bundle (`36c37a397eddaa65`) beside run 2's
   artifacts; the honest label for run 2 is **v7**. Re-freeze into a NEW versioned directory and
   PRESERVE `v2_bind`.
3. Develop the contract arm in an ISOLATED snapshot: question requirement -> lookup stage -> source
   column/value -> calculation -> final answer. Report readiness and gaps SEPARATELY.
4. After the epochs: export the validation-selected endpoint (**S0 itself if no candidate wins the
   gate** — that is a real outcome), then the three held-out evaluations, two passes of 30 each.

### TWO DEFECTS FOUND 2026-11-21, both in my own safety machinery
* **The preflight asserted the backend's DEFAULTS** (`gpt-4o-mini` on `api.openai.com`) because it read
  `_initial_config("target")` in a process that had never applied the config. The config reaches the
  backend only via `configure_openai_compatible` (`skillopt/engine/trainer.py:843`). Job 66552338
  failed on this and was right to; it cost nothing. Fixed, plus a new check that the fallbacks are NOT
  in force.
* **Trace2Skill's FINAL consolidation merge was unprotected**, so a provider error there would have
  discarded every proposal the run had paid for. It now degrades to a local coalesce that keeps every
  item and records `conflicts_unresolved: true`.

---

# EARLIER WORK (2026-11-19 and before) — still valid, read after the above

## START HERE -> `docs/research/OFFICEQA_BIND_PACKET.md` (rule OQA-BIND-1, amendment B-4)

**THE ANSWER IS NO: the permitted observations do NOT support reliable source-column bindings.** Grant 8,
0.500 of 0.5 requested CPU job-hours over jobs 66414726 / 66414832, $0.00, 2,406 tests pass. All new
output in `collect_20261118/v2_bind/`; the baseline was digest-verified unchanged in both jobs.

**`header_observed_structure_lost` was three situations, not one.** 4,117 of 26,437 data cells (15.6%)
ALREADY have a bound header and `pair_tables` already finds 9 pairs, so "166 of 166" was true of the
bindings that reached a cell, not of the corpus. The real defect is narrow: a `grep` that matched a header
line returns it ALONE with `rows = 0`, so the shared-data-row identity test cannot fire. And for most
tables no flat rendering exists at all - UID0019, UID0063, UID0163 have ZERO flat tables in their prefix.

**The span reading is refuted, and admitting it would have been the convenient mistake.** It agrees with
the one observable flat header at 9 of 10 indices and fails at the tenth. It would have lifted
bound-header coverage from 15.6% to **76.8%** on an assumption the model does not hold. Not done.

**B-4 was implemented and it earns nothing, exactly as the amendment predicted.** The split header's
child row gives the column LEAF (10/10 where testable); the PATH does not (9/10). So `oqa_cc_extract`
records `column_leaf` at a `LEAF_ONLY` basis and leaves `column`/`column_basis`/`header_known` alone -
a leaf is column/series identity, not unit scope. Replayed over all 24: 113 predictions, 566 operands,
166 bindings, **0 supported bindings, 0 findings - identical to baseline on every contract measure**;
43 operands and 12 predictions now carry a leaf.

**AND THE LEAF ITSELF IS UNRELIABLE - inspect section 6a before trusting it.** Correct on UID0019
(`528,979 -> Purchased (1)`, `533,325 -> Sold (2)`), WRONG on three: UID0026 gives two different leaves
at the same column index (two renderings with different offsets), UID0163 attaches `Mar.` to the actor's
own computed result, UID0014 reports `Gross (5)` where the actor read `Income taxes > Net (4)`. The
10/10 agreement was one table and did not generalise.

## THREE OF FOUR DISPUTED DIAGNOSES ARE CONTRADICTED -> packet section 4

* **UID0019** - NOT a unit error. The conversions are correct and both land in millions of USD. The
  error is COLUMN SELECTION: `Net options positions` is column (3), observed as **-74** (yen) and **369**
  (pound); the actor differenced `Purchased (1)`/`Sold (2)` from `Spot, forward and future contracts`.
  `|-74000/114.0 - 369x1.41| = 1169.41` = the ground truth EXACTLY. A third reason the contract is silent:
  both tables state a FOREIGN-CURRENCY scale, and the scale reader requires "of dollars".
* **UID0028** - NOT a measure-identity error. The FULL question is explicitly two-hop ("In that
  corresponding calendar month and year, what were the railroad retirement account trust receipts..."),
  so reading the second table is the TASK. My earlier reading came from a truncated question.
* **UID0044** - NOT a genuinely ambiguous operation. The window is ONE reporting week, so a single cited
  value is asked and `operation: UNKNOWN` is correct, not a missing cue. The real error is the same
  column class: the actor read the **Canada** column instead of **Latin America** in **6 of 8** reads;
  the right column gives `1,323 + 155 - 229 + 212 = 1,461` = the ground truth. Its header row is rendered
  with 9 cells for a 14-cell data row, so the needed column names are ABSENT from the observation.
* **UID0026** - a CORRECTED/REPLACED attempt, not an error in the answer. The superseded
  `30,442 - 29,726 = 716` used a year-offset pair; the committed `31,336 - 30,442 = 894` is right.
  Report it as recovered work; do not accuse a correct answer because an earlier calculation was replaced.

**The two witnessed error classes are both COLUMN SELECTION, which the learned hypothesis cannot express
at all** - it is about units.

## THE CPU DISCREPANCY IS REBUILT, AND THE CEILING'S STATUS DEPENDS ON THE UNIT -> packet section 1

`docs/research/OFFICEQA_CPU_LEDGER.json`, 0 problems. Started jobs at full requested limit, never-started
excluded, OfficeQA-only, pre-campaign phase separated.

| inside the 44-hour ceiling | requested **66.483** | elapsed **17.833** | core-hours **35.649** |
|---|---|---|---|

**Under requested walltime the ceiling was exceeded during the CAMPAIGN itself: 53.583 against its own
40-hour authorization.** Under core-hours the cumulative figure is 35.649 of 44 (81%). The reported
41.69 was a MIXED total (core-hours for the campaign, requested walltime after), rebuilt as 40.585; 79.65
and 43.92 were over-counts including the pre-campaign phase. **Which convention governs is your decision
and is NOT settled.** Grant 8's 0.5 h is recorded separately and does not depend on it.

## NEXT STEP: a TOOL-INTERFACE change, not more parsing -> packet section 7

Both witnessed errors failed for one reason: the pipe rendering the document tools return has already
destroyed the column spans, and the span is what identifies a column. The proposed change is to have the
tools return the table's own column structure beside the text (a `columns` field, or repeating a spanning
parent in each column it covers). Then ask a learner for a hypothesis over column/series identity - in
that order, because asking first produces a predicate with no evidence path. ~0.2 CPU job-hours, $0.00.
NOT launched.

## THE COLLECTION -> `docs/research/OFFICEQA_COLLECT_PACKET.md` (rule OQA-COL-1) — read section 4 of the BIND packet first; three of its case diagnoses are contradicted

**THE EVIDENCE GAP IS CLOSED, AND IT REVEALED A DIFFERENT OBSTACLE THAN EITHER PENDING PROPOSAL.**
24 frozen TRAINING questions, one attempt each, S0 unchanged, `enable_thinking: false`, narrow
`committed_answer` policy. Freeze `2083b4587253` written BEFORE the first request; prototype semantics
frozen as bundle v5 `162b580923e2e0b9` before any new record existed. Jobs 66407516 / 66407607 /
66410230 / 66412182 / 66412183. **286 attempts, $0.180, 1.418 of 1.5 CPU job-hours, 0 optimizer calls.**

**12 correct, 12 failures** — three equal kinds: 4 committed a wrong answer (UID0019, 0030, 0034, 0228),
4 truncated at the completion cap (UID0014, 0044, 0063, 0079), 4 exhausted the tool-turn budget
(UID0028, 0031, 0150, 0165). **11 of 12 failures carry written arithmetic.** Settings verification:
all 9 checks pass, and `enable_thinking: false` was **effective**, not just transmitted — 0 alternate-field
characters in all 285 calls, so 0 recoveries were needed.

**THE FROZEN PROTOTYPE FINDS NOTHING ON ANY OF THE 24.** 50 predictions, **every resolution UNKNOWN**;
the wider audit reaches 166 bindings, **all UNKNOWN**. Records with a holding binding: **0 of 24**,
controls included. The single blocking cause is **`header_observed_structure_lost`, 166 of 166**: these
richer traces `read` their tables, which returns a split header whose column spans are gone, so the
column binding is correctly refused.

**NEITHER PENDING REPAIR HELPS.** Same-message postcondition checking changes **0 of 166** (it only
matters once a binding reaches HOLDS, and none does). Supported table-unit scope inference cannot help
either: the scope here is not unobserved, it is observed and unparseable.

**THE HISTORICAL 1,000x ERROR DID NOT RECUR.** On a fresh UID0014 attempt the actor wrote "convert
thousands to billions by dividing by 1,000,000" and `2,331,274 / 1,000,000 = 2.331274 billion` - the
CORRECT factor. It failed by truncating at the 8,000-token cap while recomputing regression sums. The
error the connected contract was built for is not a standing property of this actor on this question.

**AND ONE CORRECT EPISODE SHOWS WHY A NEW REPRESENTATION IS NEEDED FOR PRECISION, NOT YIELD.** UID0026
is scored correct and its one final message holds BOTH `30,442 - 29,726 = 716` (superseded, wrong) and
`31,336 - 30,442 = 894` (committed, right). The frozen vocabulary cannot tell them apart, so a finding
bound to `716` would be a false alarm on a correct episode.

**NEXT STEP (free): fix column binding from split headers, then re-run the same analysis over the same
24 records.** `BIND_AUDIT.json` before and after is the comparison. ~0.2 CPU job-hours, $0.00. Every
vocabulary proposal in the packet's section 6 is gated behind it, and the packet says so rather than
proposing machinery its evidence cannot reach.

## PROVENANCE: TWO OF MY OWN CLAIMS ARE WITHDRAWN -> packet section 7

**The campaign DID record every optimizer request.** `oqa_campaign_body.sh` writes
`OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG` into the JOB's output directory inside the deployment bundle, not
the campaign run tree, so my search of the run tree found nothing and I wrongly concluded nothing was
written. **13 logs, 102 calls, complete prompt text and response.** The minibatch composition is
DERIVABLE from the recorded prompt (which items it names, cross-checked against the saved patch record's
reasoning prefix), so "missing" is withdrawn there too.

For UID0014: job 66323202 arm C epoch 3 **analyst call 1**, user prompt 388,789 chars, digest
`2328fd810d37`, 8 report headings, **its delivered report present VERBATIM**, matching
`minibatch_fail_001.json`. And the control is recorded: arm A had **0 report headings in every call at
every epoch**, yet at job 66273856 call 1 returned "conflated units/columns (net vs gross income taxes,
thousands vs billions)" unaided.

**"The gate, not the contract layer, is where it died" is OVERSTATED and withdrawn.** Recorded: the
instruction was proposed, ranked, applied, and the candidate was rejected at 0.0833 against 0.1667, and
no retained skill contains it. That the gate CAUSED the absence of improvement does not follow - a
candidate carrying one correct instruction can be worse overall. **Unavailable: any per-instruction
attribution of a candidate's gate score.**

The launch doc's "12 of 12 reports verbatim in an analyst prompt" is true **of the pre-launch dry run**,
whose per-call records hold only the stage, char count and prompt DIGEST - the dry run's prompt text was
never persisted either.

## CPU ACCOUNTING NEEDS YOUR DECISION -> packet section 8

The running total mixes conventions: core-hours for the campaign phase, requested walltime since.
Uniformly over all 122 OfficeQA jobs: **requested walltime 79.65 h, elapsed 19.90 h, core-hours
43.92 h**. The reported mixed figure is **41.69 of 44 (95%), 2.31 left**. Under uniform requested
walltime the ceiling is already exceeded; under uniform core-hours it is at 99.8%.

## THE STATUS PACKET -> `docs/research/OFFICEQA_STATUS_PACKET.md` (rule OQA-ST-1) — read section 7 of the COLLECT packet first; two of its claims are withdrawn

**One self-contained read of where OfficeQA stands, what is implemented versus opt-in versus proposed,
every usable trace, three worked examples and one complete feedback path.** Jobs 66395263 / 66395275 /
66395597 / 66395598, 0.90 of 1 authorized CPU job-hour, $0.00.

**The six status claims were each decided against the records, and ALL SIX HOLD** — twelve complete
training episodes on three questions; eight passes of the unchanged skill on 24 validation questions;
9.75/24 correct and 21.25/24 emitted; no corrected-client training comparison; no corrected held-out
comparison; P-1 and P-2 still proposals. An absent artifact would have been reported UNDECIDED, never as
a pass; none was. One figure needed correcting and it is the packet's own: the actor-visible trace with
thinking on is **20-36 characters**, which the tool now reproduces after a counting defect in it was
fixed (it had been joining the conversation's entries and counting the harness's separators).

**THE BINDING EVIDENCE GAP: there are ZERO corrected-client training failures.** All twelve corrected
episodes are correct (`hard = 1`). The contract layer can only be judged on records where the actor wrote
something down and got something wrong, and no such record exists. **Validation failures do not
substitute** — different split, out of bounds for development.

**And the thinking setting decides whether a trace contains anything to check at all.** With the
historical payload (thinking on) the permitted trace is **20-36 characters** — the recovered answer block
alone, 0 operands, 0 arithmetic, contract silent by construction on 6 of the 12 episodes. With
`enable_thinking: false` it is **575-8,864 characters** with 2-66 monetary operands; exactly ONE of the
twelve records (B2/UID0163) reaches a holding binding, and it DISCHARGES correctly.

**The feedback path for UID0014 is now traced end to end, and the gate is where it died.** The scale
diagnosis reached the optimizer, the proposed instruction was correct ("thousands->billions is x1e-6 ...
a 1000x slip is the most common scale bug"), it was ranked, merged and applied to the candidate — and the
candidate scored 0.0833 against the incumbent's 0.1667 and was **rejected**. No skill arm C kept contains
it: v0000-v0004 are 887/940 bytes and the probe is false on every one. **The report cannot be credited
with the diagnosis:** arm A, which received no report in any analyst prompt, produced "conflated
units/columns (net vs gross income taxes, thousands vs billions)" on its own.

**MISSING ARTIFACTS, named rather than inferred.** The campaign saved **no optimizer request** (no
`optimizer_calls.jsonl`, no analyst prompt) and **no minibatch composition**; the launch's
report-injection checks ran on a dress rehearsal and only their verdicts were kept. The packet re-renders
the analyst view with SkillOpt's own formatter and labels it a RECONSTRUCTION. And **zero of the 192
delivered reports carried a Lean certificate** — checked mechanically; Lean ran only as a standalone
compiled demonstration (rc 0, 15 checks, 7 evidence examples), which is a different claim.

**THE SMALLEST NEXT STEP, costed and NOT launched:** one pass over the 24 frozen TRAINING questions, one
attempt each, S0 unchanged, with `enable_thinking: false` — **~248 attempts, $0.16, ~0.4 job-hours** — to
find out whether a corrected-client failure carries a bindable operand at all. Expected yield about 14
failures. Everything else is blocked on that.

## THE AUDIT -> `docs/research/OFFICEQA_AUDIT_PACKET.md` (rules OQA-AUD-1 / OQA-AUD-2, amendments A-3 / B-3)

**The eight-pass S0 measurement is COMPLETE and its records are sound; one published block was not.**
All 192 intended `(pass, question)` records exist exactly once, 0 duplicates, 0 missing, **35 of 35
consistency assertions pass**. Every per-pass score, emission, recovery count, the mean 9.75, the sd
1.753 and all four paired-question lists are confirmed from the rows. **Withdrawn:** the termination
table's "179 completed question-rollouts" (there are **192**) and its five cause counts (corrected:
**117** recovered / **53** content / **18** turn-budget / 3 truncated / 1 declined), "47 answers arrived
in ordinary content" (**53**), "21 of 179 produced no answer" (**22 of 192**), and "answered in all 8
passes — 20 of 24" (**17 of 24**). Cause **demonstrated**: an ad-hoc aggregation run while
`p8/results.jsonl` still held the 11 rows job 66346540 wrote before its wall limit — cutting p8 to 11
rows reproduces all five published counts exactly. Jobs 66356410 / 66356411 / 66356437.

**Deployed vs local — CORRECTED.** The earlier claim that bundle `oqa_fu2_20261118`'s commit `4572c61`
"is the revision every result was produced under" was **wrong**. The bundle was rebuilt six times and
each job logged what it verified: **episodes `241f8e8`**, **S0 passes `7e4a69a`**, published CC artifacts
**`fa8f1c3`**, tests `4572c61`. It changes nothing, and that is checked: `git diff 241f8e8 4572c61 --
skillopt/` is **empty**, so the client, the adapter, the recovery policy and the rollout loop are
byte-identical across all of them. **Do not rebuild a bundle while a job is queued OR RUNNING:** bash
reads a body script lazily, and replacing it under a running job killed 66351468 after its work was done.

**The connected contract: three extraction repairs, a sound finding, and no new diagnosis.** Amendment
B-3 fixes three defects against the extractor's own stated semantics — a list comma read as a thousands
separator (**26 years admitted as money → 0**), the record's own flat headers never passed as
`references` (**UID0014's 8,006,880 was an "ambiguous match" between a bound and an unbound copy of one
cell**; unbound data cells 5,791 → 5,184), and one step read by nobody. The reserved checking records go
from **0 holding bindings to 4**, all on UID0014, all `OUTSTANDING`. **But the comparison verdict is
`repeats_what_was_available`:** the campaign's own stage report for that attempt already carries
`**VIOLATED** [unit_match] … in the asked scale (billions)`. Valid additional diagnosis **0 of 4**.
`CC_BUNDLE_v4` = `1c165508ed8d7aae`; **Lean 4.20.0 return code 0**, 15 statements, 7 evidence examples.

**"No bound header" never meant "header absent."** 105 of 155 tables in these prefixes have an observed
header row; all 29 cells whose binding stayed UNKNOWN sit in a table whose header row **was** observed
and names **no money scale at all**. The dominant reason the contract is silent is earlier than the
header: **141 of 196 operands are in no data cell** — they are sums, averages and converted figures the
actor computed.

**TWO PROPOSALS, measured and NOT applied — the decision is yours.**
* **P-1:** resolve the conversion from the operand's own message as well. This actor writes operand,
  factor and answer in ONE final message (UID0014's is step 45 of 45), so the strictly-later window is
  empty. Applying it turns the four findings into `incorrect_factor_stated`, **VIOLATED, wrong by 10^3** —
  the campaign's real 1,000x error. It weakens what "committed before the later steps are read" means.
* **P-2:** let a fully observed header row that names no money scale witness the absence of a nearer
  scale. 29 cells become decidable, 8 of them reach a binding. A stronger evaluator for the same
  condition, not a weaker condition — which is why it is yours and not mine.

## THE HELD-OUT RESULT IS RELEASED -> `docs/research/OFFICEQA_RELEASE_PROTOCOL.md` (end of file)

All three jobs COMPLETED. Read ONCE, after both evidence packets' proposed tests were already written, so
nothing in them is tuned on it.

```
66332133  oqa-ho-S0                   3 of 60   hard 0.0500  S0            9d377d1c2906   887 B  PRIMARY
66332134  oqa-ho-primary_B_selected   6 of 60   hard 0.1000  arm B best    95989ce5ebad  3603 B  PRIMARY
66332135  oqa-ho-exploratory_A_last   8 of 60   hard 0.1333  arm A/C last  5bd17ea109bc   940 B  exploratory
```

One question on this split is **1.7 points**. Arms A and C selected S0, so the primary comparison has two
distinct documents, not four.

**The table refutes itself, and that is the point.** The exploratory document differs from S0 by two HTML
comment lines and carries the **identical advice digest** `3e70328ffb65`. Two documents with the same
advice scored **3/60 and 8/60** — five questions, 8.3 points apart. Arm B's +3 over S0 is smaller than
that. **Verified:** the scores. **Interpretation:** nothing here separates a skill effect from a draw, and
the design could not have separated the arms at all, because only arm B has an edited selected skill.

## THE ANSWER RECOVERY -> `docs/research/OFFICEQA_RECOVERY_PACKET.md`

**The `reasoning`-field defect is FIXED, VERIFIED ON COMPLETE EPISODES, and its effect on a score is now
MEASURED.** Rules OQA-REC-2 and OQA-CC-2; amendments A-2 and B-2. Everything new is under
`<campaign>/followup_20261119/`; the frozen campaign and `followup_20261118/` are untouched.

### The three results that matter

1. **Twelve complete episodes, twelve answers, twelve correct.** Three frozen TRAINING questions
   (UID0017, UID0063, UID0163 — membership checked against the frozen split record), two reps, two
   conditions. Under condition A (the historical generation payload) **all six answers came out of
   `message.reasoning`**, and under the old client all six episodes would have ended with no answer.
   Job 66346256.
2. **S0 on the 24 validation questions, through the corrected client: 9.75 of 24 on average (range 7–12:
   11, 9, 12, 11, 9, 11, 8, 7) with 21.25 of 24 answers emitted, against the campaign's recorded
   4 of 24 with 6 emitted.** Eight passes, jobs 66346539 / 66346540 / 66351468, zero provider errors in
   1,476 attempts. **The campaign's entire gate ran at a quarter of the actor's real answer rate and
   under half its real score**, and its one acceptance was on a margin of ONE question against a
   draw-to-draw sd of **1.753** with **10 of 24 questions flipping between draws**.
3. **The connected contract's extraction was repaired and its one apparent gain disappeared with it.**
   The repaired reader rejects the years B-1 bound (`1942`, `07`) and finds the real money operands
   (`8006880`, `1211749`, `1004461` on UID0014) — and then refuses to bind them, because each matches
   several cells that disagree. All four comparison verdicts are `remains_unknown`, 0 findings, 0
   well-bound findings; no counterexample; **Lean 4.20.0 return code 0, every statement accepted**. The
   obstacle was reported as **1,484 to 2,022 of the data cells having no bound column header**, with the
   applicability condition needing one, so the contract was "silent by construction on this corpus".
   **BOTH HALVES OF THAT ARE WITHDRAWN by the 2026-11-18 audit:** the count is 1,107-2,022 once the
   extractor passes the references its own machinery documents, the condition does NOT need a bound
   column (it needs the absence of a nearer override, which the observed header row settles in all 29
   cases), and the ambiguity on UID0014 was an artifact of comparing a bound against an unbound copy of
   ONE cell. Repaired, the same records give 4 holding bindings and 4 findings - which the campaign's own
   stage report already had. See `docs/research/OFFICEQA_AUDIT_PACKET.md`.

### What is in force now (all OFF by default, so the frozen campaign still reproduces)

* **The narrow recovery policy.** `SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=committed_answer` (also
  `1/true/yes/on/answer`) recovers only an unambiguous, complete, **terminal** `<answer>…</answer>` block
  from `reasoning` / `reasoning_content` / `thinking`, and recovers **the block alone**. `reasoning` /
  `reasoning_text` still selects yesterday's permissive promotion, kept only so the OQA-HTTP-1 probe's
  recorded outcomes reproduce. Every declined case has its own name and it reaches `termination_cause`.
* **`TARGET_OPENAI_COMPATIBLE_EXTRA_BODY`** (JSON) transmits serving controls — this is how
  `{"enable_thinking": false}` is sent. Invalid JSON refuses the request.
* **`TARGET_OPENAI_COMPATIBLE_RAW_CAPTURE`** (a JSONL path) records four layers of one exchange: the
  transmitted request (with the digests that name the episode), the complete HTTP body, the SDK view and
  the compatibility message. Headers are filtered to an allow-list; no credential can reach it.
* **The repaired `oqa_cc_extract`** beside the untouched `oqa_lc_*`, the stage link's REOPENED / "no
  conversion required" / "stated but not carried through", the `_well_bound` verdict rule, and
  `oqa_cc_bundle.py`, which freezes what a hypothesis MEANS (vocabulary, extractor and evaluator source
  digests, stage definitions, renderer, learner prompt, learning manifest) rather than only what it says.

### The decision that is yours, and the one thing I would not do without it

**The frozen campaign's numbers were all taken at a quarter of the actor's real answer rate.** Nothing in
this follow-up changed them, and nothing should without your word. Three options, cheapest first:

1. **Leave the campaign as a recorded negative result** with the defect documented. $0.
2. **Re-measure the held-out comparison only** — the two distinct documents of the primary comparison on
   the 60 held-out questions, through the corrected client. 120 question-rollouts, about 1,240 attempts,
   **$0.79**, ~1.5 core-hours. This is the only number that was ever the primary result.
3. **Re-run the three-arm campaign.** I would advise against it until the sample-size question below is
   settled: the client defect is fixed, but the design's other problem is not.

**The sample-size arithmetic, from this measurement — and read the audit's §5 before quoting it.**
Draw-to-draw sd is **1.753** of 24 (not the 1.5 this note used to say). Δ_min = 2.802·σ·√(2/n): one draw
per arm **6.9** questions of 24, two 4.9, four **3.5**, eight **2.5**. **The campaign accepted a
candidate on a margin of one question with one draw.** Four qualifications, all in the audit packet §5:
σ itself has a 95% interval of [1.16, 3.57] from 8 draws, so every threshold carries a band of roughly
x0.66 to x2.0; the formula is an unpaired approximation while the design is paired (14 of 24 questions
never change outcome, so a paired test is more sensitive, and its sd needs repeats of TWO documents);
these are repeated evaluations of ONE FIXED document, which is not a training run and excludes the
optimizer's proposal variance; and it is about these 24 validation questions, not the 60 held-out ones.

### And the one finding that constrains any future contract experiment

A trace-based checker can read **20–36 characters** of actor text per episode with thinking ON (the
recovered answer block, and nothing else — the policy deliberately does not promote reasoning prose),
against **575–8,864** with `enable_thinking: false`. **So the contract layer cannot be evaluated on
traces recorded with thinking on.** Restoring that text means changing what the actor does, which is a
condition that needs its own comparison. That is a design decision, not a defect.

### Standing prohibitions, unchanged

Pushing this branch; GPUs; weakening the validation gate; unsealing job 66234927; using held-out
questions, answers or trajectories outside the released protocol; empty-response nudges or episode
retries; another training campaign; altering the frozen campaign's artifacts.

### Tests

**2,326 passed, 9 skipped, 368 subtests** (job 66356411, commit `ba53007`) - the five new ones are the
B-3 regressions on the real witnesses. The earlier 2,321 stand for job 66352312, commit `4572c61`.
NOTE: the local workstation runs Python 3.9 and cannot even COLLECT
`tests/test_officeqa_answer_recovery.py` (`dataclass(slots=True)`); the cluster's 3.11.17 is
authoritative.

### Ready to run

```
R=/nfs/turbo/umms-csriram/yaojm/lean_skillopt ; CAMP=$R/runs/20261117-officeqa-3arm-campaign
AUD=$CAMP/audit_20261118 ; A=$R/oqa_aud_20261118          # the AUDIT bundle, commit 3974433
ST=$CAMP/status_20261118 ; S=$R/oqa_st_20261118           # the STATUS bundle, commit 855612c
OQA_BUNDLE=$S bash $S/oqa_st_submit.sh status             # 0:15, free, rewrites $ST in place
OQA_BUNDLE=$S bash $S/oqa_st_submit.sh tests              # 0:12, free
OQA_BUNDLE=$A bash $A/oqa_aud_submit.sh audit             # 0:20, free, rewrites $AUD in place
OQA_BUNDLE=$A bash $A/oqa_aud_submit.sh tests             # 0:12, free
FU=$CAMP/followup_20261119 ; D=$R/oqa_fu2_20261118        # bundle, commit in $D/COMMIT
# every phase, free unless marked:
OQA_BUNDLE=$D bash $D/oqa_fu2_submit.sh tests             # 0:12
OQA_BUNDLE=$D OQA_BACKFILL_APPLY=1 bash $D/oqa_fu2_submit.sh prep
OQA_BUNDLE=$D bash $D/oqa_fu2_submit.sh episodes          # PAID, 12 episodes, ~$0.02
OQA_BUNDLE=$D bash $D/oqa_fu2_submit.sh cc                # free; OQA_CC_PROPOSE=1 allows 2 DeepSeek calls
OQA_BUNDLE=$D bash $D/oqa_fu2_submit.sh s0                # PAID, 8 passes; top up with OQA_S0_FIRST=<n>
# offline, no key, no cost:
python3 -I research/officeqa/oqa_recovery.py replay --exchanges <dir|file> --out <dir>
python3 -I research/officeqa/oqa_cc_bundle.py verify --bundle $FU/connected_contract/CC_BUNDLE_v3.json
```

## THE FOLLOW-UP IS DONE -> `docs/research/OFFICEQA_FOLLOWUP_PACKET.md`

**THE HEADLINE: the empty actor replies were never empty.** Replaying two of the campaign's own recorded
requests (rule OQA-HTTP-1, job 66340531) returned HTTP 200, `content: null`, no tool calls — and
`choices[0].message.reasoning` holding 944 and 1,550 characters, each ending in a complete
`<answer>[10102000000, 4.73]</answer>`. The provider's field is **`reasoning`**; the client read `content`
and `reasoning_content`. So a large share of the 331 empty terminal replies (62.7% of all 528 selection
observations) were answers the client discarded. **This corrects OQA-FOR-1**, which reported
`reasoning_content` null in all 7,909 logged attempts and treated the hidden-reasoning hypothesis as
refuted — it was refuted only for that spelling.

**Every score in the campaign was taken through that client.** The gate, the noise floor and the held-out
comparison all sit downstream of it. Nothing in the frozen campaign was changed.

### What is in force now

* **Amendment A-1** (`docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md`): metadata through the
  adapter, `last_response_metadata` in the local-tools loop, named termination causes, the provider cost
  beside the ledger's, attempt/model provenance, and the Action-Only diagnosis counter redefined
  (confirmations / model refutations / execution diagnoses / unknowns reported separately). Two tests
  regenerate real historical reports byte-for-byte, so "metadata-only" is checked, not hoped.
* **The client correction**: `SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK=reasoning` uses the reasoning
  text when `content` is empty and there is no tool call. **OFF by default**, so the frozen campaign's
  behaviour is still what runs unless asked for. Not a nudge and not a retry.
* **`enable_thinking: false`** is documented for qwen3-5-4b (default **true**, `thinking_budget` 4096)
  and was verified on the wire: transmitted, accepted (200), effective — the `reasoning` key disappears
  and completion tokens fall ~5x. 0 of 9 empty replies under it against 2 of 9 historical. **Not a
  performance finding.**
* **Amendment B-1**, the connected-contract prototype (`oqa_cc_*.py`): bound entities, an applicability
  condition, holds/violated/unknown/CONFLICT, versioned repair, a stage link, enforced provenance labels.
  Frozen model `96c3d1bd9aad6f4d` decides 180 of 240 learning cells; 24 prefix-only predictions committed
  before the later steps were read; **Lean 4.20.0 return code 0, all statements accepted**.
  **No natural counterexample in the permitted records, and no actionable diagnosis on them** — all 24
  checking outcomes are `unknown`, and its one apparent gain is an artifact of a weak operand match that
  bound YEARS. Its value here was nil; the machinery works.

### The next action the evidence supports (NOT another campaign)

1. Repeat the existing S0 measurement on the 24 selection questions **with the client correction in
   force** — 8 draws, 1,984 attempts, $1.26, ~2.9 core-hours — because every earlier score came through
   the broken client.
2. Give the prototype `oqa_lc_chain`'s operand and cell matching instead of its own. Code only, no data.
3. Only then ask whether a contract layer adds anything, on traces recorded through the corrected client.

**Allowance left of Grant 3:** 1 EmpirioLabs attempt, 3 DeepSeek attempts, **0 CPU job-hours** (4.00 of 4
requested, exhausted). A further step needs a new allocation.

## THE FORENSICS AUDIT IS DONE -> `docs/research/OFFICEQA_FORENSICS_PACKET.md`

Rule OQA-FOR-1, tool `research/officeqa/oqa_forensics.py` (`noanswer`, `contracts`), job **66338091**
commit `4cf50fc`, bundle `oqa_for_20261117`, outputs `$CAMP/diagnostic/FOR_{NOANSWER,CONTRACTS}.json`.
Zero API calls, zero GPUs, 0.13 CPU core-hours. Tests **2,223 passed, 9 skipped, 368 subtests** (66338092).

**The source nobody had opened:** the campaign body set `TARGET_OPENAI_COMPATIBLE_CALL_LOG`, so
`$PROJECT_ROOT/oqa_campaign_20261117{,b,c}/run_*/actor_calls.jsonl` holds the RAW provider reply for every
attempt in 16 jobs — 7,909 records with finish_reason, content, reasoning_content and usage. Its record
count and completion-token total match the ledger's for the same jobs exactly.

1. **The dominant cause of a missing answer is a reply with no content at all.** 331 of the 528 selection
   observations (62.7%) ended on one reply with finish_reason `stop`, no `content`, no `reasoning_content`,
   and a median of **920 completion tokens** (min 96, max 4,076) generated and billed. None had zero
   tokens. 533 such replies campaign-wide: 672,104 tokens, $0.2004 by the provider's own `cost_usd`.
2. **"Prose but no answer" never happens and extraction missed nothing.** Zero terminal replies had text
   without a tag. Running the real extractor's fallback over the 7 truncated replies and the real scorer:
   **0 of 7** would have scored.
3. **The unsupported `answer` tool is not the cause.** 54 of 528 episodes contain one (109 calls); the loop
   continues past it. 8 of the 54 still answered (3 correct).
4. **Three row fields carry nothing:** `last_finish_reason` is `""` in all 528 rows, because the
   local-tools branch of `rollout.py` never assigns `last_response_metadata` and
   `compat_message_from_chat_message` drops the finish reason and the reasoning channel.
5. **The contract representation cannot express the dominant failure.** 20 single-atom `effect` contracts
   are expressible in total; 5 were learned, 1 retired. Every atom is a property of one tool observation's
   text — nothing about the actor's output, a period, a unit or an answer.
6. **Arm B's "zero distinct diagnoses" is a hard-coded constant** (`oqa_reports.py`: `... else 0` for
   LC_ACTION). It measures nothing. **This corrects the previous packet.**
7. **In six evolve rounds the checker found ONE counterexample** (arm B e3, UID0019@10) and it retired
   `read.effect.2` — the model correcting its own rule, not a diagnosis of the agent.
8. **Nothing was lost in supply:** all 24 of arm C's epoch-3 reports appear byte-for-byte in that job's
   own `optimizer_calls.jsonl`.
9. **The in-sample warning is a false alarm on 72 of each arm's 96 reports.** The renderer tests the ITEM
   ID against the model's `evidence_items`, and the same 24 training ids were drawn in all four epochs.
   Epoch N's reports do cite the model frozen at the end of epoch N−1, as intended. By trace digest:
   epoch 1 is genuinely in-sample (24/24), epochs 2–4 are fresh (0/24 identical in every epoch pair) —
   except arm C's UID0019, which is a digest COLLISION because the saved record keeps only the empty
   content strings and the tool commands.

## THE DIAGNOSTIC READ IS DONE -> `docs/research/OFFICEQA_DIAGNOSTIC_PACKET.md`

Rule OQA-DIAG-1, tool `research/officeqa/oqa_diagnose.py` (`gates`, `questions`, `replicates`,
`contracts`, `chains`), job **66337036** commit `66b2b66`, bundle `oqa_diag_20261117`, outputs in
`$CAMP/diagnostic/DIAG_{GATES,QUESTIONS,REPLICATES,CONTRACTS,CHAINS}.json`. **It spends nothing** -- every
API key is dropped before Python starts. Whole phase: 0 attempts, $0.00, 0.13 CPU core-hours, 15 jobs.

Four findings, all VERIFIED from files (the packet has the evidence and the caveats):

1. **One identical skill document was measured SEVEN times on the same 24 training questions and scored
   1, 1, 2, 2, 3, 5, 6** (sd 1.95, range 5). Arms A and C accepted nothing, so their in-force skill never
   changed and every epoch re-ran it; the actor is at temperature 0.7 with **no sampling seed sent**. The
   spread of one FIXED document covers nearly the whole range of the 21 candidates' scores (0 to 5), and
   the gate accepted on a margin of ONE question.
2. **The gate was largely measuring termination, not advice.** Of 504 candidate observations: 33 correct,
   105 wrong answers, 363 no answer at all, 3 out of tool turns. Score vs commitment rate r = 0.78; score
   vs skill length r = -0.08. 109 calls to a nonexistent `answer` tool on the selection evals (211
   campaign-wide); S0's own traces have none.
3. **The campaign's only acceptance sits inside the scorer's formatting tolerance.** S0 answered UID0159
   "39.31%" against ground truth "39.31" and was scored wrong. Count it right and S0 is 5/24, the
   threshold is 5/24, and arm B's 5/24 candidate TIES -- zero acceptances, all arms keep S0. (A
   counterfactual about the scorer. The native exact-match scorer was required to stay unchanged and did.)
4. **Nothing from either contract layer is traceable into an edit.** 0 of 101 proposed-edit entries name
   any contract vocabulary. Arm B: 0 distinct diagnoses in all 96 reports, 1 violated forecast in 2,676.
   Arm C: 11 violated obligations, all in one report. All three arms' analysts independently reported the
   same dominant failure.

All 21 gates pass every integrity check: candidate file verbatim in its own 24 system prompts, score and
decision recomputed, question ids / user prompts / oracle pages / sources / system scaffold identical to
the shared S0 baseline, arms' configs differing only in `out_root`.

Bundle `oqa_heldout_20261117`, commit `251e9514beaefb88ed1ded997102c4769a2e23f1`. **Do not rebuild that
bundle while 66332134/66332135 are queued** — the manifest is verified in-job. The recovery bundle
`oqa_campaign_20261117c` (commit `37c78ec`) has no queued jobs and may be left alone.

**Check state before acting:** `squeue -u $USER`, then
`sacct -X -j 66332133,66332134,66332135 --format=JobID,JobName%26,State,Elapsed`.

### WHAT TO DO WHEN THE HELD-OUT CHAIN FINISHES

```
D=$PROJECT_ROOT/oqa_heldout_20261117 ; CAMP=$PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign
# 1. the three scores and their provenance
for d in $CAMP/heldout/*/; do cat $d/eval_summary.json; cat $d/PROVENANCE.json; done
# 2. release the held-out jobs' unspent claims, then read the caps from outside the job
oqa_ledger_reconcile.py $CAMP/ledger --exited-jobs 66332133,66332134,66332135 --apply
oqa_ledger_read.py $CAMP/ledger --questions N
# 3. refresh the measured spend in oqa_budget.py CEILINGS and OFFICEQA_AUTHORIZATION.md section 3
```

## THE CAMPAIGN IS COMPLETE: four epochs, three arms, all twelve arm-epochs sound

`oqa_recover.py audit` after the recovery: **still starved: none. recovered: A.4, B.4, C.3, C.4.**
`oqa_recover.py verify`: **PASS** — every recovered epoch has 4 analyst calls, an ordinary gate decision, a
slow-update gate decision and a meta skill.

| arm.epoch | train hard | candidate | ordinary gate | pairs sent | slow gate | guidance | meta skill |
|---|---|---|---|---|---|---|---|
| A.1 | 0.1250 | 1/24 | reject | — | placeholder | — | skip (epoch 1) |
| A.2 | 0.0417 | 2/24 | reject | 10/10 | reject 2/24 | yes | written |
| A.3 | 0.0417 | 1/24 | reject | 10/10 | reject 1/24 | yes | written |
| A.4 | 0.2500 | 1/24 | reject | **8/10** | reject 0/24 | yes | written |
| B.1 | 0.1250 | 3/24 | reject | — | placeholder | — | skip (epoch 1) |
| B.2 | 0.0833 | **5/24** | **accept_new_best** | 10/10 | reject 1/24 | yes | written |
| B.3 | 0.0000 | 1/24 | reject | 10/10 | reject 1/24 | yes | written |
| B.4 | 0.1250 | 0/24 | reject | 10/10 | reject 1/24 | yes | written |
| C.1 | 0.1250 | 2/24 | reject | — | placeholder | — | skip (epoch 1) |
| C.2 | 0.2083 | 2/24 | reject | 10/10 | reject 1/24 | yes | written |
| C.3 | 0.0833 | 2/24 | reject | **7/10** | reject **4/24** | yes | written |
| C.4 | 0.1250 | 0/24 | reject | **9/10** | reject 2/24 | yes | written |

**ONLY ARM B UPDATED ITS SKILL.** Its epoch-2 candidate measured 5/24 against the incumbent's 4/24 — a
one-question margin — and every candidate after it was rejected, so arm B's selected skill and its last skill
are the same document.

| arm | validation-selected | size | last skill | note |
|---|---|---|---|---|
| A | **S0** `9d377d1c2906` | 887 B | `5bd17ea109bc` 940 B | the last skill is S0 + an EMPTY placeholder |
| B | **step 2** `95989ce5ebad` | 3,603 B | `95989ce5ebad` | selected and last are the same document |
| C | **S0** `9d377d1c2906` | 887 B | `5bd17ea109bc` 940 B | same as arm A |

**7 skills requested, 3 distinct by digest, 2 BY ADVICE.** Arms A's and C's last skills carry advice digest
`3e70328ffb65`, identical to S0's: they differ only by two HTML comment lines.
`$CAMP/heldout/SKILLS_FROZEN.json`, record digest `f26ba852b9e59dc4`.

**C.3's slow update scored exactly 4/24 — tying S0's baseline** and rejected only because the gate requires
STRICT improvement. That is the concrete case behind the "accept on a tie" option, which is NOT authorized.

### Pair counts and the prompt-size fallback: 6 of 9 sent 10/10

Rule OQA-PB-1, limit 225,000 tokens. Trimmed: A.4 10→8 (261,376→205,051), C.3 10→7 (252,996→207,927),
C.4 10→9 (235,924→221,772). Every one of the 9 slow updates from epoch 2 on produced usable guidance, and
every one was rejected by the gate.

**Proof that the four `no_content` results were the LEDGER and not the prompt size:** the original B.e4 sent
10 pairs at 181,849 tokens — far inside the limit — and got no guidance, while B.e2 sent 10 at 223,815 and
did. The recovery reproduced **identical** pair and token counts for every affected epoch and got guidance
every time, which also shows the rollout reuse was faithful.

## THE STATISTICS over all 21 gates (`$CAMP/GATE_STATS.json`, `oqa_gate_stats.py`)

**21 gate evaluations — 12 ordinary + 9 slow-update — and 1 acceptance.** (Earlier I reported 9 gates and an
expected 2.99; both were wrong, and they did not even agree with each other. See
`OFFICEQA_GATE_RESOLUTION.md` §CORRECTION.)

```
candidate scores /24   0 0 0 1 1 1 1 1 1 1 1 1 2 2 2 2 2 2 3 4 5
mean candidate 1.571/24   against mean incumbent 4.238/24
uniform null  (21 x 0.371)      expected 7.78 acceptances
per-gate null (own incumbent)   expected 7.85,  P(at most 1) = 0.001
pooled over 21 x 24 = 504 question-level observations
    observed correct  33      null predicts  89      P(total <= 33) = 2.1e-13
```

**The null that every candidate was as good as its incumbent is strongly disfavoured, in the direction that
the optimizer's edits made this actor WORSE on the selection set — in all three arms alike, Original SkillOpt
included.** It does NOT separate the arms, and it does NOT turn arm B's acceptance into noise.

Caveat applied to both tails: all gates in an arm share the same 24 questions, so 0.001 could be several times
larger; a 56-question pooled deficit is far too large for within-arm correlation to explain.

## THE AUTHORIZATION, AS AMENDED, and what is left after Grant 6

TOTALS including everything already spent. `docs/research/OFFICEQA_AUTHORIZATION.md` is authoritative and
records all six grants; the figures below are the ledger's own reading plus the two paid calls that ran
outside it (both accounted: the 23 probe sends are now back-filled INTO the ledger, and the one
pre-ledger DeepSeek probe of job 66271290 is subtracted from the enforced cap).

| resource | cumulative ceiling | spent, measured | left | used |
|---|---|---|---|---|
| EmpirioLabs attempts | 30,024 | 11,756 (ledger, incl. the 23 back-filled) | 18,268 | 39% |
| EmpirioLabs charge | $17.00 | $7.8344 | $9.1656 | 46% |
| DeepSeek calls | 266 | 106 | 160 | 40% |
| DeepSeek charge | $10.00 | $3.5100 | $6.4900 | 35% |
| CPU requested job-hours | 44 | **40.27** (31.6 + 6.88 follow-up + 0.87 audit + 0.90 status) | **3.73** | **92%** |
| GPUs | 0 | 0 | — | — |

**Grant 4's own suballocation** (5,000 attempts / $4.00 actor, 4 / $1.00 optimizer, 8 requested CPU
job-hours, 0 GPUs) was drawn from that headroom and verified against it before anything was submitted.
**Used: 1,598 attempts, $0.9789, 0 DeepSeek calls, 6.88 requested job-hours, 0 GPUs.** Left in it: 3,402
attempts, $3.02, 4 DeepSeek calls, 1.12 job-hours. **CPU is now the binding constraint on the whole
authorization at 92% of 44 requested job-hours, with 3.73 left.**
The caps the jobs enforce are ABSOLUTE and were computed once: **target 15,181 / $10.8626, optimizer
109 / $4.4688**. A cap derived from "what has been spent so far" would hand every job a fresh allowance.

**Two field-name defects, same class, one corrected and one deliberately not.** The client read `content`
and `reasoning_content` where this provider uses `reasoning` — corrected (amendment A-2). The spend
ledger reads `prompt_cache_hit_tokens`, which is DeepSeek's name, where EmpirioLabs reports
`prompt_tokens_details.cached_tokens` — **left alone on purpose**, because it makes the ledger
conservative by about 38% on the actor side ($5.3159 charged against $3.2768 billed over the campaign),
and the safe direction for a cap is to overcharge.

## THE ACCOUNTING FIX IS FINISHED AND PROVEN IN PRODUCTION

Ledger verdict **PASS**: after reconciliation net claims equal recorded spend exactly in both roles
(optimizer $3.460519 / 102 attempts, target $5.624077 / 8,394), **zero unresolved requests**, **zero reserve
breaches**. Released across the campaign: optimizer 46 attempts / $2.2395, target 456 / $2.2259 — and the four
recovery jobs released their own unspent claims on clean exit, which is the fix working for real rather than
in a test.

Three parts: release on clean exit with $0.05 blocks (`771b3f0`); **retain one `reserve_usd` per UNRESOLVED
request** — one that was sent and whose reply never arrived, which may still have been billed; and
`oqa_ledger_reconcile.py` (rule OQA-REC-1) for a ledger whose processes have already exited, which refuses any
owner it cannot show has finished and consults `squeue` every time.

**Verified across all four conditions the user named:** concurrent jobs at one cap, clean exits, interruption
(a SIGKILLed process runs no atexit hook and keeps its whole claim — the safe direction), and restarts.

The 24 refusal records remain in the ledger as history; the audit now reports those stages as **repaired**.

## WHERE THE TRACES ARE (for an audit of existing traces)

Campaign root `$PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign`, **252 MB**. 18 `predictions/` directories
per arm plus 2 shared:

```
shared/epoch1/predictions/<UID>/          the 24 training questions all three arms reflected on (epoch 1)
shared/val_baseline/predictions/<UID>/    S0 on the 24 gate questions
arm_<X>/steps/step_000<N>/rollout/predictions/<UID>/          24 training questions, epoch N
arm_<X>/steps/step_000<N>/selection_eval/predictions/<UID>/   24 gate questions for that step's candidate
arm_<X>/slow_update/epoch_0<N>/rollout_prev|rollout_curr/predictions/<UID>/   10 paired questions each
arm_<X>/slow_update/epoch_0<N>/selection_eval/predictions/<UID>/              24 gate questions, slow candidate
arm_<X>/selection_eval_baseline/predictions/<UID>/            injected copy of the shared gate baseline
arm_<B|C>/stage_reports/UID<id>.<trace digest>.md             96+ contract reports, content-addressed
arm_<B|C>/stage_reports/GENERATED.jsonl                       one record per report, append-only
contracts/{model_M0.json, arm_B/, arm_C/, MODEL_EVOLUTION.jsonl}
heldout/{S0.md, SKILLS_FROZEN.json, S0/, primary_B_selected/, exploratory_A_last/}
recovery/{RECOVERY_AUDIT.json, RECOVERY_AUDIT_AFTER.json, RECOVERY_PLAN.json, RECOVERY_RECORD.json,
          RECOVERY_VERIFY.json, LEDGER_RECONCILE.json, LEDGER_RECONCILE_AFTER.json, original/20261117-recovery/}
ledger/{claims/, spend/, release/}        spend/<job>r<restart>p<pid>.jsonl is the per-process record
```

Each `predictions/<UID>/` holds `conversation.json` and `target_user_prompt.txt` (which is what
`oqa_reports.trace_digest` hashes), plus `target_system_prompt.txt`. Each `results.jsonl` row carries
`target_system_prompt`, `target_user_prompt`, `response`, `predicted_answer`, `ground_truth`, `hard`, `soft`,
`n_turns`, `last_finish_reason`, `fail_reason`, `oracle_parsed_pages_*`, `resolved_source_paths` and (on rows
written from commit `4b60132` on) `rollout_settings_fingerprint`.

**Tools already written for reading traces** (all `python3 -I`, run from an extracted bundle tree):

| tool | what it does |
|---|---|
| `oqa_trace.py` | renders one trace: turns, tool calls, what the actor saw |
| `oqa_inventory.py` | per-item inventory of a rollout directory: turns, tools, outcomes |
| `oqa_reports.py` | builds a contract report from a trace; `trace_digest`, `verify_report_provenance` |
| `oqa_report_audit.py` | audits stage reports from the FILES: marker leakage, boundary audit, provenance |
| `oqa_optimizer_input_audit.py` | what the optimizer was actually shown |
| `oqa_rollout_checkpoint.py` | sanity-checks a rollout directory mid-run |
| `oqa_campaign_summary.py` | the whole-campaign readout, including arm-B-in-arm-C containment |
| `oqa_gate_stats.py` | enumerates every gate and does the binomial arithmetic |
| `oqa_cases.py` | hand-picked case material |
| `oqa_parser_replay.py` | replays the answer parser over saved responses |

## STILL NOT AUTHORIZED

1. **Pushing** this branch. Deployment is by pinned `git archive` bundle with an in-job manifest check
   (`research/cluster/oqa_bundle.sh`).
2. **GPUs**, in any quantity.
3. **Weakening the validation gate** — accepting on a tie would have changed C.3's slow update from reject to
   accept. `CLAUDE.md` §7 forbids it without explicit approval; `OFFICEQA_GATE_RESOLUTION.md` lists it as an
   option, not a recommendation.
4. **Unsealing** the held-out outcome of job 66234927, which remains deliberately UNREAD.
5. The one remaining paid DeepSeek attempt from the original probe grant stays unspent.

## Tests

**2,190 passed, 9 skipped, 368 subtests** on the cluster from the pinned bundle (job 66331956, commit
`251e951`). Four of my own tests failed along the way and each was my error, not the code's: the provenance
guard does not see a changed `max_tool_turns` (offline mode puts the turn budget in the user prompt); `prepare`
deliberately removes the resume markers it archives; I dropped an `import re`; and I wrote a second
advice-stripper with guessed markers that stripped nothing.

---

## A0-RUN. THE THREE-ARM CAMPAIGN, SUBMITTED 2026-11-17 03:26 EST (user-authorized)

**Commit `91b249449a48fb850b82a3d0d62457bfe1ca291a`, deployed as a `git archive` bundle at
`$PROJECT_ROOT/oqa_campaign_20261117` (pushing is still not authorized). Campaign root
`$PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign`.**

ONE `afterok` chain, so a single failure stops everything downstream instead of spending the authorization on a
chain whose earlier step is broken. It did exactly that, which is why there are two chains:

```
CHAIN 1, commit 91b2494, bundle oqa_campaign_20261117
  66272737 shared  COMPLETED   ->  66272738 A.e1  COMPLETED  ->  66272739 B.e1  COMPLETED
  ->  66272740 C.e1  FAILED (the import race)  ->  66272741..66272749  CANCELLED by the dependency

CHAIN 2, commit 797846e, bundle oqa_campaign_20261117b, resumed with `--from C.1`
  66273855 C.e1 -> 66273856 A.e2 -> 66273857 B.e2 -> 66273858 C.e2
               -> 66273859 A.e3 -> 66273860 B.e3 -> 66273861 C.e3
               -> 66273862 A.e4 -> 66273863 B.e4 -> 66273864 C.e4
```

**The shared stage was NOT re-run and arms A and B epoch 1 were NOT re-run.** Re-rolling the 24 training
questions at temperature 0.7 would hand the later arms DIFFERENT traces from the ones arms A and B already
reflected on, which would break the comparison rather than repair it. The epoch-1 jobs keep their 60-minute
limit; the later epochs now ask for 120 (see the measured throughput below).

**Why a chain and not concurrent arms.** The 50 RPM limit is ACCOUNT-WIDE, and one job with `workers: 4` at the
measured 3.41 s median latency already offers about 70 RPM - so a single job is rate-limited on its own.
Concurrency cannot raise throughput; it divides the same quota and roughly triples the CPU job-hours for the
same wall time. The chain also gives a checkpoint between every job. Original SkillOpt has first launch
priority after the shared rollout, which is what the user asked for.

**Which contract model each epoch runs under** (epochs 1 and 2 both use M0, so the first evolution follows
epoch 2; the arm job evolves the model at the END of each epoch from 2 on):

| epoch | arms B and C run under | reports are |
|---|---|---|
| 1 | `contracts/model_M0.json` | **IN-SAMPLE** - M0 was learned on this very rollout, and each report says so in its own text |
| 2 | `contracts/model_M0.json` | out-of-sample |
| 3 | `contracts/arm_<X>/model_arm<X>_epoch02.json` | out-of-sample |
| 4 | `contracts/arm_<X>/model_arm<X>_epoch03.json` | out-of-sample |

**Verified offline before launch:** all 24 reports whose item is in M0's evidence carry "The forecasts below are
in-sample: they were induced to hold of these very observations, so a confirmation here is not a successful
prediction." That is in the report the optimizer reads, not only in a record beside it.

**The budget, computed at `slow_update_samples: 10`** (`research/officeqa/oqa_budget.py --slow-samples 10`):

| resource | computed need | authorized | used of the ceiling |
|---|---|---|---|
| actor HTTP requests | 12,763 | 15,000 | 85% |
| actor charges | $6.22 | $8.00 | 78% |
| DeepSeek calls | 102 | 130 | 78% |
| DeepSeek charges | $3.49 **if every call lands at peak** ($1.75 off-peak) | $4.00 | 87% |
| CPU job time | ~5 job-hours | 20 | 25% |
| GPUs | 0 | 0 | - |

Wall time floor 255 min from the account rate limit. **Nothing exceeds a ceiling.**

### VERIFIED LIVE, on the running campaign (not the dry run)

| check | result |
|---|---|
| S0 on this endpoint | training `hard` **0.125** (3/24); the gate's baseline, validation `hard` **0.1667** (4/24) |
| M0 | frozen `8d8ecceecd0b77533913ab5996cfa57740d6eb4e6782d7146e74fbcb7e7d1a69`, 24 items, 339 tool steps, 0 assumptions rejected |
| the route, both roles, inside the trainer | **PASS** — optimizer `openai_compatible` -> `api.deepseek.com` (`deepseek-flash`), target -> `api.empiriolabs.ai/v1` (`qwen3-5-4b`), dispatch proven at zero HTTP cost (66272738) |
| the shared traces are reused, not re-rolled | 24 shared rollouts injected into each arm; arm A's epoch-1 rollout spent nothing |
| **arm A gets the ordinary input** | `OFFICEQA_STAGE_REPORT_DIR` unset, no report heading in its analyst prompts |
| **arm B carries NO stage material** | **24 of 24 reports, 0 stage markers**, audited independently from the files (mean 4,438 chars) |
| **epoch 1's reports are labelled in-sample** | **24 of 24** carry the notice; it is in the report the optimizer reads |
| both contract arms start from ONE model | 24 distinct trace digests, all under the single model digest `8d8ecceecd0b` |
| answer-key isolation | `boundary_audit: PASS` on every report |
| the caps | audit at 48 rollouts **PASS**: target 511 attempts / $0.3177 / 4 failed / 0 refusals; optimizer 7 calls / $0.1776 / 0 refusals; claims ahead of spend in both roles |

**Arm A epoch 1 result: the candidate was REJECTED**, `hard` 0.0417 against the current 0.1667 — the edit made
things markedly worse and the strict-improvement gate refused it, so `best_skill.md` is S0. Epoch wall time
629 s. Read this as one data point, not a trend.

**A defect in the job body, contained and NOT patched mid-run.** The end-of-job ledger summary writes nothing:
it runs `python -I -m skillopt.model.spend_ledger`, and `-I` implies `-E`, so PYTHONPATH is ignored and `-m`
cannot find the extracted tree. Only this diagnostic is affected — the ledger that enforces the caps is imported
inside the trainer's own process. The deployed bundle is deliberately untouched, because its manifest is
verified in-job and editing it would fail every queued job. **Read the ledger with
`research/officeqa/oqa_ledger_read.py <ledger dir> --questions N`**, which needs nothing but the standard
library. The repo copy of the body is fixed for anything deployed later.

### CORRECTED FROM THE CAMPAIGN'S OWN FIRST 47 ROLLOUTS (job 66272737) — read this, not the table above

The table above was computed from the A40 pilot's per-question tokens. The campaign measured itself and three of
those constants were wrong, two of them badly. From the spend ledger's own usage records over the first 47
question-rollouts (24 training + 23 validation under S0):

| constant | A40 estimate | **MEASURED here** | effect |
|---|---|---|---|
| attempts per question | 9.33 | **10.32** | +11% |
| prompt tokens per question | 106,300 | **145,532** | +37% |
| completion tokens per question | 4,221 | **10,525** | **+149%** |
| throughput | 50 RPM (the account limit) | **15.7 requests/min** | wall time x3.2 |

`cached_prompt_tokens` was 0 on every one of the 483 usage records, which confirms the endpoint does no prefix
caching. 1 of 485 attempts failed and no 429 appeared, so **15.7 RPM is LATENCY, not throttling**: throughput is
about `workers / mean request latency`, and with `workers: 4` only four questions are in flight while each
question's tool loop is SEQUENTIAL - a question that reaches the 24-turn cap issues 24 requests one after
another. **The plan's claim that "the binding constraint on wall time is the 50 RPM ACCOUNT limit" was wrong in
the optimistic direction by a factor of 3.2.**

**Cost per question-rollout: $0.00656** (budgeted $0.0046).

| what | attempts | charge | against the authorization |
|---|---|---|---|
| **the 13 queued training jobs** (948 rollouts) | 9,783 | **$6.22** | inside $8.00, with $1.78 left |
| held-out, per distinct skill (60 questions) | 619 | **$0.39** | |
| everything at 7 distinct skills | 14,118 | **$8.97** | **OVER the $8.00 ceiling** |

**THE ONE CEILING AT RISK IS THE ACTOR CHARGE, and only in the held-out stage - and the PRIMARY comparison
always fits.** Refined after running `oqa_heldout.py --plan` against arm A's real output:

| what is scored | distinct skills | total with the $6.22 training chain |
|---|---|---|
| **the PRIMARY comparison** - S0 plus each arm's validation-selected skill | at most **4** | **$7.79, inside $8.00 at its worst** |
| + 1 exploratory last skill | 5 | $8.19, over by $0.19 |
| + 2 | 6 | $8.58, over by $0.58 |
| + 3 (every arm's last skill distinct) | 7 | $8.97, over by $0.97 |

So **no decision is needed to get the primary result**. What may not fit is the EXPLORATORY last-skill
evaluation, and only if every arm's last skill is distinct. If candidates keep being rejected as arm A's epoch-1
candidate was, every arm's selected skill IS S0 - 1 distinct for the primary - and the three distinct last
skills then also fit, at $7.79 in total. Verified on arm A's real output: 3 skills requested, **2 distinct**,
because its selected skill is S0 and its last skill is the rejected candidate.

**The held-out jobs are NOT submitted.** When the chain finishes, run
`oqa_heldout.py --plan --arm A=... --arm B=... --arm C=... --usd-per-question 0.00656 --requests-per-question 10.32`,
read the distinct-skill count, and submit the primary skills first. If the exploratory extras would cross $8.00,
that is the point to ask the user - not before.

Attempts and CPU job-hours are both fine: 14,118 of 15,000 even at 7 skills, and about 11 job-hours for the
chain plus 0.66 per held-out skill, against 20 authorized.

**ACTION TAKEN, 2026-11-17 ~04:00 EST:** the nine later-epoch jobs (66272741-66272749) had their time limit
raised from 60 to **120 minutes** with `scontrol update`, because at the measured rate one of them needs about
65 minutes (92 rollouts x 10.32 attempts / 15.7 per minute = 60.5 min of actor time, plus the optimizer calls)
and would have hit a 60-minute wall and stopped the chain. Nothing else was changed: the config, the splits, the
seeds and the per-job spend ceilings are untouched. The "each <= 60 min" job shape came from my own launch plan,
not from the authorization, which caps CPU job-hours at 20.

**FOUR GUARDS ARE IN FORCE, each because of a recorded failure. All four are inert unless their environment
variables are set, so no other track's behaviour changes.**

1. **A durable shared spend ledger** (`skillopt/model/spend_ledger.py`) on `/nfs/turbo`. Attempts are CLAIMED in
   blocks before the request is sent, with `O_CREAT|O_EXCL` for atomicity, so `spent <= claimed <= cap` holds
   across concurrent processes AND across restarts. Per-job ceilings sit beside the account caps so one runaway
   arm-epoch cannot eat what the other arms need. **Verified on NFS** by job 66272499.
2. **A pre-send prompt size check** (`skillopt/optimizer/prompt_budget.py`), rule **OQA-PB-1**, limit **225,000**
   estimated tokens. A draw over it drops comparison pairs - `stable_success` -> `improved` ->
   `persistent_fail` -> `regressed`, later-sampled first - until it fits. **MEASURED, and it does not need to
   trim:** arm A's epoch-2 slow update (job 66273856) drew 10 pairs of which ALL TEN were in
   trajectory-showing categories - the worst case for size - and the prompt came to **185,332 tokens against
   the 225,000 limit**, sent whole, guidance produced. That is 18,533 tokens per showing pair against the
   34,569 the plan assumed, because this campaign's trajectories are about half the size of the saved
   collection the constant came from (which held a 171,394-character outlier). My earlier prediction that
   "some slow updates will send 9 pairs rather than 10" was wrong in the cautious direction. The log
   (`prompt_budget.jsonl` in each job's run dir) records the pair count actually sent and whether guidance came
   back, for every arm.
3. **An in-process route guard** (`skillopt/model/route_guard.py`), called by the trainer and by `eval_only`. It
   reads back each role's live backend, base URL and model and proves the dispatch path at zero HTTP cost. The
   actor-only stages additionally require the optimizer role to be UNUSABLE (no key loaded).
4. **The body refuses to run on a Slurm restart** (`SLURM_RESTART_COUNT != 0`, exit 6), and every job is
   `--no-requeue`.

**WHAT TO CHECK WHEN IT FINISHES, before reporting any result:**

* `LEDGER_AFTER.json` in the last job's run dir - `verdict` must be PASS and `observed` must be inside the caps.
* `prompt_budget.jsonl` in every arm-epoch run dir - the actual pair count per slow update and per meta skill,
  and `guidance_produced`.
* `slow_update/epoch_NN/slow_result.json` in every arm for `action: "no_content"`.
* `route_guard.json` in every run dir - one line per role, with the resolved base URL and model.
* The **validation-selected** skill (`best_skill.md`) is the PRIMARY comparison. A last-skill evaluation is
  **EXPLORATORY** and must be labelled so.
* The held-out evaluation is NOT in the chain. Run `research/officeqa/oqa_heldout.py --plan` over the three arm
  directories afterwards: it deduplicates S0 plus each arm's selected and last skill BY DIGEST, so at most 7
  rollouts of the 60 questions and fewer whenever skills coincide. 420 of the 12,763 budgeted requests cover it.

---

## A0-FAIL-1. Arm C epoch 1 died on a concurrent-import race (job 66272740), and why A and B stand

**The failure.** Arm C epoch 1 wrote 16 of its 24 reports and then died:

```
File "research/officeqa/oqa_reports.py", line 72, in _lc_modules
  spec.loader.exec_module(mod)
File "research/officeqa/oqa_lc_report.py", line 43, in <module>
  _ORDER = {CHAIN.VIOLATED: 0, CHAIN.OUTSTANDING: 1, CHAIN.UNKNOWN: 2, CHAIN.DISCHARGED: 3}
AttributeError: module 'oqa_lc_chain' has no attribute 'VIOLATED'
```

Arms A and B ran the same code through the same two analyst threads and both completed. That asymmetry is the
character of the bug: it is a race, and arm C lost it.

**The mechanism, REPRODUCED rather than inferred.** `_lc_modules()` ran on every report, from a
ThreadPoolExecutor sized by `gradient.analyst_workers`, and its loop registered an EMPTY module in `sys.modules`
and only then executed it. Thread 1 registers an empty `oqa_lc_chain` and begins a slow exec; thread 2 arrives,
finds the name present so its own `setdefault` is a no-op, execs its OWN copy from the warm page cache,
overtakes thread 1, and reaches `oqa_lc_report`, whose module-level `import oqa_lc_chain as CHAIN` returns
thread 1's still-empty object. Hand-written loading bypasses the import system's per-module lock, which exists
precisely to prevent this.

**My first reproduction attempt FAILED**, and that was informative: a uniform delay keeps the threads in
lockstep and neither reads the other's half-built module. Making only the FIRST exec slow lets thread 2 overtake,
and the job's error then appears verbatim. Against the fixed loader, 24 callers over six stagger patterns gave
zero exceptions and zero identity failures.

**The fix (`df9d922`)**: load once per process under a lock, and reuse anything already in `sys.modules`. That
also removes a quieter hazard the old loop carried - `oqa_lc_report` imports `oqa_lc_replay` and
`oqa_obligations`, which come after it in the load order, so the old code executed those twice and returned a
DIFFERENT instance from the one `oqa_lc_report` holds.

**WHY ARMS A AND B EPOCH 1 STAND.** Two independent reasons, both checked rather than argued:

1. Every module-level assignment in the five learned-contract modules is an immutable constant - regexes,
   literal dicts, cue lists. There is no module-level mutable cache, so two instances behave identically.
2. **All 24 of arm B's stored epoch-1 reports rebuild BYTE-IDENTICAL under the fixed loader.** Checked against
   the files the job wrote, not against a fixture.

So the fix is a loader fix and nothing about report content changes. Arm A has no reports at all.

**What the failure cost:** 3 optimizer calls (~$0.07), sunk. Arm C's `runtime_state.json` says
`last_completed_step: 0`, so the rerun redoes step 1 from scratch; its injected rollout and the shared gate
baseline are on disk, so no actor request is re-spent for those, and its 17 cached reports are keyed on
(trace, model) and will be correctly reused. The ledger at resubmission: target 804 attempts / $0.4925, optimizer
17 calls / $0.4669, zero refusals, verdict PASS.

**The revision changes mid-campaign and that is recorded, not hidden:** arms A and B epoch 1 ran under
`91b2494`, everything from arm C epoch 1 onward under `797846e`. The old bundle is kept intact on the cluster as
the record of what A and B ran, and its manifest still verifies.

---

## A0-PREP. DONE: the preflight the user required before any paid rollout (jobs 66272358, 66272499 - both FREE)

**No actor key and no optimizer key is loaded in this stage, so it cannot spend a cent.** Artifacts:
`$PROJECT_ROOT/oqa_campaign_20261117/run_66272499_prep/`.

**1. Documents - this was a REAL BLOCKER, not a formality.** The 108 questions of rule OQA-SEL-3 reference
**153** distinct parsed bulletins; the sparse corpus held **78**. So **33 of the 108 items had no document at
all** - 8 of the 24 training questions, 2 of the 24 validation questions and 23 of the 60 held-out questions.
An item with no parsed file cannot be answered by any arm: the oracle page comes from `jsons/<stem>.json` and
the grep and read tools from `transformed/<stem>.txt`. **150 files (121.1 MB) were fetched from the pinned
dataset revision `8ecbf18d` with zero errors**, and the corpus now verifies **PASS**: 153 of 153 present, 0
missing `.txt`, 0 missing `.json`.

**2. Availability, all 108 items, UID0019 included: PASS.** 107 **AVAILABLE** from the supplied page, 1
**AVAILABLE_VIA_TOOLS**; **0 ABSENT, 0 NO_DOCUMENT, 0 UNTESTED**. No verdict changed for any of the 60 items the
previous check had already tested, which is the consistency check on the new code.

**3. UID0019 and the two items like it.** UID0019 (train), UID0039 (val) and UID0064 (held-out) are the only
`source_files`/`source_docs` count mismatches among the 108, and all three are **one file with two page
references**. `tool_runtime._iter_oracle_refs` has an explicit branch for that shape, both references resolved
for all three, and all three are AVAILABLE. **The OQA-SEL-1 filter that dropped UID0019 was over-cautious.**
Recorded rather than hidden: `oqa_sel3.stratum` calls these items "multi" (it counts `max(files, docs)`) while
`oqa_freeze_sets.stratum_of` calls them "single" (it counts `files`). Both labels are in `AVAILABILITY.json`.
Nothing depends on it - the stratification is a balancing device whose rule was fixed in advance.

**4. The caps, raced on the REAL filesystem** (`research/officeqa/oqa_ledger_check.py`, `LEDGER_CHECK.json`):
concurrency **PASS** (four processes split exactly 23 of a 23 cap, `[4, 8, 8, 3]`, and the ledger's own record
agrees), restart **PASS** (the first process spent the whole cap of 9, a fresh process with
`SLURM_RESTART_COUNT=1` spent **0**), fail-closed **PASS**.

**The first run, job 66272358, FAILED - and the failure was my check, not the ledger.** It made the claim
directory `chmod 0500` and expected the write to be denied. `/nfs/turbo` does not deny it: the share carries
POSIX ACLs whose mask grants the owner write access whatever the mode bits say, so the condition was never
created and the check reported the ledger failing when its own premise had failed. The job refused to continue,
which is right for an unproven cap, but the diagnosis was wrong. `fail_closed` now PROBES the premise and runs a
portable case that works on every filesystem (the ledger path's parent is a regular file, so `makedirs` raises),
reporting the chmod case as NOT APPLICABLE where the filesystem does not enforce it.

**5. The effective settings, read back through the trainer's own resolution** of
`configs/research/officeqa_api3.yaml`: `num_epochs 4`, `batch_size 40`, `accumulation 1` ->
**`steps_per_epoch = ceil(24 / 40) = 1`**, one ordinary skill update per epoch; `edit_budget 4`,
`min_edit_budget 2`, `lr_scheduler cosine` -> **4, 3, 2, 2** over the four steps; `use_gate true`,
`sel_env_num 0` (the whole 24-question val split); `use_slow_update true`,
`slow_update_gate_with_selection true`, `use_meta_skill true`, **`slow_update_samples 10`**,
`longitudinal_pair_policy mixed` (no top-up rollouts); `eval_test false`; `seed 42`, `workers 4`,
`max_tool_turns 24`; target `qwen3-5-4b` on `openai_compatible` at `https://api.empiriolabs.ai/v1`, optimizer
`openai_compatible` at `https://api.deepseek.com`. **The route strings the jobs assert agree with this config**,
checked offline at zero cost.

---

## A0-NEXT. SUPERSEDED BY A0-RUN - the offline preparation, kept for its evidence (user, 2026-11-17)

**The campaign described here is now RUNNING; read A0-RUN for the live state. Two things in this section
are out of date: the budget table is at 4 pairs per slow update (the campaign runs at 10), and the
"173,060-token envelope" was retired by job 66271290. The split manifests, the report-separation checks
and the model-evolution evidence below all still stand.**

**Read `docs/research/OFFICEQA_THREE_ARM_LAUNCH.md`, not a paraphrase. The copy-pasteable packet is
`OFFICEQA_HANDOFF_LATEST.md`. Evidence: `runs/20261117-officeqa-3arm-prelaunch/`.**

**NOTHING HAS BEEN SUBMITTED.** The user expanded the comparison to three arms (A Original SkillOpt / B Action
Contracts Only / C Action Contracts + Stages), 24 training questions x 4 epochs, the released 24-question val
split, 60 held-out, gated slow updates and meta-skill on from epoch 2. The offline preparation they asked for is
done and its verdict is **PASS with no problems**; what is missing is the budget, which they asked to see before
the campaign is submitted.

| resource | request | basis |
|---|---|---|
| wall time | **6 h** | 235 min floor from the rate limit + optimizer latency + queue |
| CPU job time | **20 job-hours**, <=4 concurrent, each <=60 min, 2 CPUs + 8 GB | 13 jobs + one retry each |
| GPUs | **0** | remote actor, local document tools |
| actor requests | **15,000** | 11,756 computed at the measured 9.33/question |
| actor charges | **$8.00** | $5.73 at measured tokens x published prices |
| DeepSeek calls | **130** | 102 computed, 34/arm |
| DeepSeek charges | **$4.00** | $1.36 off-peak, $2.72 if all peak |

**The 50 RPM ACCOUNT limit is the binding constraint on wall time, not latency** (235 min against 168).
Account-wide, so concurrency divides the quota rather than raising it.

**VERIFIED (do not redo):** rule **OQA-SEL-3** 24/24/60, digest `117836ff4139...`, disjoint, and all three
OQA-SEL-2 sets are subsets so the saved development traces stay usable; the report separation checked on the
RENDERED text (24/24 attempts: arm B carries no stage marker, arm C contains arm B line for line); two saved
epochs through `scripts/train.py:main()` for all three arms with ZERO actor calls and a recording optimizer stub
(arm A gets the ordinary input, arm B the Action-Only report, arm C both halves, every report found verbatim in
a prompt, 24 distinct trace digests each matching its attempt and its model); the per-arm model evolution with
the prior model, the new evidence, the counterexample and the repair recorded; 25 new tests and 2,057 passing.

**FOUR DEFECTS were found and fixed while building this** (each with a regression test) - the most consequential:
`Evidence` keyed its atom cache on the ITEM id, so epoch 2's attempt of a training question silently reused
epoch 1's atom values, which on the real traces turned ONE genuine counterexample into FIVE phantom ones.

**TWO DECISIONS for the user, both recorded with their measurements:**
1. **`slow_update_samples: 4` against upstream's 20** - MEASURED: this repo disables per-trajectory truncation in
   the slow update, so 12 comparison pairs come to 262,257 tokens against a 173,060-token envelope (the largest
   optimizer prompt this project has sent successfully). 4 samples stay inside even in the worst case; 6 are
   inside on the measured category mix but over at worst; 20 are far over. Too large is not fatal (the trainer
   records `no_content`) but would mean reporting a gated slow update that produced no guidance.
2. **Epoch 1's reports are in-sample** because M0 is learned on the shared rollout they describe, which is what
   "the same model learned on the shared rollout" implies. The report says so in its own text. Leave-one-out for
   epoch 1 would remove it at no cost; not implemented.

Still open from before and unchanged: **four epochs give no cross-epoch memory in the ORDINARY update** (one
epoch of four steps is the fix); **FP8 makes this a new baseline** so S0 must be re-measured; **availability is
untested for 48 of the 108 items**; **a push has not been authorized** (both previous jobs ran from a
`git archive HEAD` staging bundle).

**The job shape is 13 jobs, one per ARM-EPOCH plus the held-out evaluation** - required, not a convenience,
because `OFFICEQA_LC_MODEL` is read from the environment and names a different frozen model each epoch. Reuse
`research/cluster/oqa_api_body.sh` via `--wrap` with a node-local `/tmp`; a requeue respends the authorization
because the attempt ledger is per-process.

## A0-PROBE. DONE: is 10 paired questions per slow update practical? YES at the measured mix (user, 2026-11-17)

**Job 66271290, COMPLETED, 49 s. One paid DeepSeek attempt of the two authorized; ONE STILL UNSPENT.**
Artifacts: `runs/20261117-officeqa-slow-probe-66271190/` (RENDER.json, GROWTH.json, SEND_66271290.json, job log).
**The campaign config is still at `slow_update_samples: 4`,** as the user asked, pending their review.

**THE DECISIVE MEASUREMENT.** A realistic 10-pair slow-update request - rendered by the campaign's own
`run_slow_update` from saved training traces - was **accepted**: `prompt_tokens` **243,986**, `finish_reason`
`stop`, 7,562 completion tokens, 43.7 s, and `run_slow_update` returned **3,235 characters of parsed guidance**,
so the trainer would record an accepted candidate and gate it on the selection set. Charge **$0.041 off-peak**
($0.082 if peak). Prompt cache hits 512 of 243,986, so caching stays negligible.

**THIS RETIRES THE "173,060-TOKEN ENVELOPE" FRAMING of section A0-NEXT.** That number was never a limit, only
the largest prompt this project had tried. DeepSeek accepted 1.41x it. The real ceiling is still unknown.

| pairs | slow prompt, measured mix | worst case (every pair showing) | against the 243,986 now proven |
|---|---|---|---|
| 4 | 92,230 | 138,276 | inside always |
| **6** | 138,345 | **207,414** | **inside even in the worst mix** |
| **10** | **230,575 (MEASURED 243,986)** | 345,690 | measured mix proven; worst case unproven |
| 20 | 461,150 | 691,380 | far beyond anything tried |

**Cost of 10 over 4, inside the budget already requested:** +108 question-rollouts (2x10 instead of 2x4 training
rollouts per slow update x 9), +1,007 actor requests, +$0.49 actor, +$0.74 DeepSeek at peak, +20 min of
rate-limited wall clock. Totals at 10 pairs: 1,368 question-rollouts, 12,763 actor requests, $6.22 actor, 102
DeepSeek calls, $3.49 at peak, 255 min at the 50 RPM account limit.

**RECOMMENDATION: go to 10, keep the `no_content` check.** An over-large prompt is NOT fatal -
`run_slow_update` catches the provider error and the trainer records `action: "no_content"`, symmetrically across
arms and at no cost - so the downside is a slow update that silently produces nothing, which the post-run check
catches. If the user wants no unproven region at all, **6** is the largest value whose worst case stays under the
proven 243,986 while still giving 1.5x the sample of 4.

**NO DEMONSTRATED HEADROOM BEYOND THIS.** At 34,569 tokens per trajectory-showing pair, the measured 10-pair mix
sits ~13,000 tokens under the proven point - less than half a pair. Longer epochs, longer trajectories as the
skill grows, or a worse category mix all push into territory nothing has tested. A free `GET /models` or one
deliberately oversized request would settle the real ceiling.

**META-SKILL NOT TESTED, deliberately.** The user's condition was to test it only if its rendered request were
larger. It is **smaller**: 785,649 characters against the slow update's 786,843, because its system prompt is
1,229 characters shorter. The size question is therefore settled by the larger of the two. What stays untested is
that `meta_skill_content` parses back, which the remaining authorized attempt could close.

**INFRASTRUCTURE LESSON, cost 0.** Job **66271190** configured the DeepSeek endpoint perfectly and sent nothing:
`skillopt.model.chat_optimizer` is a router and `OPTIMIZER_BACKEND` defaults to `openai_chat`, which means
**AZURE**. The Azure path raised, `run_slow_update` swallowed it, and the probe recorded `no_content` after zero
HTTP attempts - a result that looked like a DeepSeek failure and was not one. This is the trap already recorded
for the TARGET role in the smoke test, hit on the OPTIMIZER role. The probe's preflight now calls
`set_optimizer_backend`, reads it back, resets the cached per-role client, and proves the dispatch path with a
check that costs no HTTP attempt. **Any campaign job that calls the optimizer must pin the backend the same way.**

## A-API. DONE: OfficeQA on a HOSTED ACTOR - smoke test PASSED (user, 2026-11-16) -> `docs/research/OFFICEQA_API_COMPARISON_PLAN.md`

**SMOKE TEST RAN AND PASSED. The API is viable.** Job **66264849**, COMPLETED exit 0, 68.4 s, 20 attempts,
**0 errors, 0 malformed replies, 0 retries**, **$0.0034 of the authorized $0.50**, **zero GPUs**.
All four tool-loop checks PASS: 18 of 20 replies carried `tool_calls`, every argument set parsed as JSON,
**18 tool calls executed against the documents with 0 observation errors**, and all 3 items took further turns
after an observation. Latency min 1.63 / median 3.41 / max 7.04 s. 73,145 prompt + 6,251 completion tokens.
**cached_prompt_tokens = 0, so NO prefix caching was observed** - do not assume the $0.02/M cached rate.
Accuracy, separate and not the verdict: 1 of 3 answered, hard [0,1,0].

**THE KEY IS AT `/home/yaojm/.config/lean_skillopt/actor_api.env`** on the cluster (mode 600, 79 chars).
**CORRECTION: I previously reported it existed on neither machine, and that was wrong** - I searched $HOME
dotfiles, guessed filenames and the turbo tree but never `~/.config`. The user supplied the path. The job reads
it there, exports it for the ACTOR role only, drops the optimizer keys, and greps the run dir against the live
value afterwards (0 artifacts contain it).

**VERIFIED FREE OF CHARGE.** `GET /v1/models/qwen3-5-4b` (unauthenticated, 200 in 0.67 s): id `qwen3-5-4b`,
alias `qwen3.5-4b`, `hugging_face_id` **Qwen/Qwen3.5-4B** (the A40's weights), features include
**`function_calling`**, `structured_output: json_schema`, context 262144, max output 32768, **FP8 weights + FP8
KV**, 50 RPM, $0.04/$0.02/$0.07 per M in/cached/out. Login-node reachability 200 in 0.245 s; the sbatch re-checks
from the COMPUTE node.

**THE TOOL LOOP IS PROVED END TO END** against a local OpenAI-compatible server (`tests/test_officeqa_api_smoke.py`,
16 pass): unmodified SkillOpt advertised all three schemas, parsed a `tool_calls` reply, resolved the candidate
path, ran `run_tool` against a real parsed bulletin, fed the observation back as `role: "tool"`, and the actor
took a further turn quoting it. **The only untested variable left is the provider's own behaviour.**

**TWO WIRING FACTS found offline (both would have cost a long run to discover):**
1. `target_backend: openai_chat` means **AZURE** OpenAI. `skillopt/model` dispatches on the COMBINED backend name
   and separates roles by env prefix, so BOTH `optimizer_backend` and `target_backend` must be `openai_compatible`.
2. The backend keeps a module-level config per role, so env vars alone are not enough once anything has called
   `configure_openai_compatible`. Configure the target role explicitly.

**TREATMENT ARM WIRED to the compiled prototype, with provenance.** Arm B now emits the obligation-form report
(`oqa_lc_report.render`) under a frozen model whose digest is verified first; the report-cache key covers the
MODEL VERSION as well as the trace, and every report records `trace_sha256` + `model_sha256`.
**Verified on the saved A40 traces:** 12 epoch-1 and 12 epoch-2 reports PASS against their own attempts; epoch-1
reports checked against epoch-2 attempts **FAIL** (the A40 integrity failure, now caught); a report built under
the early model v5 checked against the final v7 **FAIL** (`607f3a138090` vs `059c110ae6c7`).

**MEASURED ESTIMATE for the 4-epoch comparison, now from the smoke test's own numbers:** 264 question-rollouts,
~3,100 actor requests, **~2 h wall** - and the **50 RPM account limit is the binding constraint, not latency**
(3,100/50 = 62 min floor; 44 min latency-bound at 3.41 s median with workers=4). **Allowances it NEEDS, as a
SEPARATE request** (the old allowance has 8 DeepSeek calls left and no CPU or actor budget): **8 CPU job-hours**
(<=4 concurrent, each <=60 min, 2 CPUs + 8 GB), **0 GPUs**, **4,000 actor HTTP attempts**, **$2.50 actor
charges**, **56 DeepSeek calls**, **$1.50 DeepSeek charges**. CPU down from 12 and actor down from $4.00 because
the measured cost is $0.00112/question at a 12-turn cap (~$0.003 at 24 turns, ~$0.80 for the whole comparison).

**FIVE SUBMISSIONS, FOUR FAILURES, NO WASTED API ATTEMPTS.** 66262999 and 66263561: **NODE_FAIL at 60 s, four
times each, on eight different nodes**, before Python ran. A control job with identical account/partition/
resources COMPLETED, and one-factor controls showed reading the key from `~/.config`, extracting the 576-file
bundle and curling the endpoint from a compute node are each fine. Fixed by moving work to a **node-local /tmp**
and submitting via `--wrap` (the shape five control jobs used) - the same NFS-temp-dir lesson the
learned-contract line recorded in job 65818719. Then 66264572 hit **`python -I` ignoring PYTHONPATH**, and
66264701 hit the **Azure default** (3 ledger attempts, 0 HTTP sent; the ledger now reports `never_sent`).
Total actual HTTP: 3 probe + 20 smoke = **23 of 100**, $0.0034.

**THREE THINGS TO DECIDE BEFORE IT RUNS** (plan doc 4.5): the FP8 confound means S0 must be re-measured on the API
actor and this is a NEW baseline, not a continuation; four epochs still gives NO cross-epoch memory
(`step_buffer` is epoch-scoped with one step per epoch), so it is four independent attempts; and arm B's frozen
model (v7) has a known counterexample on the confirmation collection, which must be recorded if it is used.

Local suite after all of this: **2032 passed, 11 skipped**.

## A0. DONE: the learned-contract port (user, 2026-11-16) -> `docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md`

**The full loop ran, offline, 0 GPU jobs and 0 paid calls.** Learn -> freeze by digest -> commit a prediction ->
reveal -> real counterexample -> repair -> recheck -> independent confirmation -> generated Lean -> report -> a
proposed skill edit. Read the port note, not this summary. Output preserved at
`~/dev/lean_skillopt/runs/20261116-offline-lc-port/` (420 KB: LOOP.json, 12 frozen models, 23 prediction logs,
`lean/OqaContracts.lean`, `report_UID0014.md`).

**Reference design** = `docs/LEARNED_CONTRACT_DESIGN.md` **v0.1 + amendments A1-A4** in
`~/dev/lean_skillopt/SkillOpt-learned-contract` @ `4a9914a`. **No document labelled v0.2 exists**; v0.1 as amended
is the current agreed design. The five digests are in the port note's header table.

**New code (all additive and opt-in; `oqa_check.py` and `oqa_obligations.py` were NOT touched, so arm A stays
byte-identical to upstream and the two-arm run's P/S/C semantics are unchanged):**
`research/officeqa/oqa_lc_{vocab,ir,cells,chain,learn,replay,lean,report,demo}.py`, `tests/test_officeqa_lc.py`
(38 pass; the existing 87 OfficeQA tests still pass unchanged).

**VERIFIED results:**
1. **Counterexample found in the data, not invented.** `index_alignment` (merge a supplied page's two-row table
   header by cell index) was adopted at round 0 with nothing contradicting it and refuted at round 6 by UID0026's
   own read of the flattened header of the same table, in 12 places; refuted again by UID0014 in the later
   collection, in 10 places. The repair drops the assumption; affected column bindings become UNKNOWN, never
   violations.
2. **Regression case fixed and improved.** On UID0014's epoch-2 trace the old C report fires
   `scale_conversion_missing` **29x** ("No conversion step appears"). The obligation form **DISCHARGES 13** of them
   (the actor converted correctly at step 1) and reports **23 VIOLATED** for a verifiable new reason: thousands ->
   billions applied with factor 1e-3 where 1e-6 is required, so 14 regression inputs are 1,000x too large. The
   plain report P says only `no_matches`. Skill-edit candidate in port note section 6.3.
3. **Column + period + unit binding now exists** (`oqa_lc_cells.py`), which `oqa_check` lacked. It settles the
   probe's UID0026 disagreement deterministically: `30,442` = `Paper money 2/ > Total paper money`, December 1960;
   `32,869` = `Total money in circulation 1/`, same row. Paper money was asked, so 30,442.
4. **Three false-positive classes fixed**, each with a regression test: arithmetic intermediates, a year inside an
   asked span, over-permissive scaled matching.

**HONEST LIMITS (do not drop these when summarising):** adjudicating evidence is very sparse (5 table pairs over 3
tool steps in 24 attempts; 126 of 128 development tool steps leave the column atom UNKNOWN); the learned model is 3
contracts; **one of those 3 acquires a counterexample on the confirmation collection** (`read.effect.1 =
width_uniform` at UID0028@42); and nothing here shows any of it changes a skill edit that survives merge, budget
and the 6/12 gate.

**Lean: COMPILED. Job 66259448, COMPLETED, ExitCode 0:0.** USER-AUTHORIZED 2026-11-16 (1 CPU, 2 GB, <=15 min,
0 GPU, 0 paid calls); the grant is now spent and OQA-5's caps are untouched (6.23 of 8 A40-h, 8 of 24 calls).
- **Lean 4.20.0** (x86_64-unknown-linux-gnu, commit 77cfc4d1a4f6, Release), from `OQA_LEAN_BIN` in the PRIVATE
  cluster site settings. The toolchain is NOT on the workstation and NOT on the login node. The learned-contract
  state note's "Lean 4.35.0" does not exist there any more; 4.20.0 is what compiled this.
- 1 CPU / 2 GB allocated, **0 GPUs**; elapsed 00:00:06, TotalCPU 00:00.907, `lean` itself **4.24 s**.
- **All 64 statements compiled: 23 in `section General` + 2 `rfl` bridge theorems + 39 over the exported
  evidence.** Compiler output **0 bytes** (Lean is silent when everything checks); **0 errors, 0 warnings, 0
  `sorry`**.
- **Digest identical at all six links** - recorded locally before submission, reproduced twice by the generator at
  `47c6ed8`, committed at `87b9ad6`, verified by the job's manifest check, logged by the job, and the copy fed to
  `lean`: `e40a7e62faed92ffdc53093b4937f8eb6b655ba5eabda14d7036444c0b9eecbb`.
- **NOT established: the axiom set.** `#print axioms` was deliberately not added, because changing the file would
  break the digest guarantee. 0 warnings does rule out `sorry` (Lean always warns on one). An axiom check needs one
  further job of the same shape; NOT authorized, NOT submitted.
- First submission **66259421 FAILED in 1 s, exit 3, before `lean` ran**: a Slurm batch script is copied to the
  node spool, so `${BASH_SOURCE[0]}` pointed there instead of at the bundle and the private site file was
  unreadable. Fixed with `$SLURM_SUBMIT_DIR`. The `.lean` file was never touched; its digest was re-verified on the
  cluster before the retry. Both logs preserved.
- Ran from a read-only **staging bundle**, not a git snapshot: `research/deploy.sh` refuses a commit that is not on
  `origin` and pushing was not authorized, so nothing was pushed. `research/cluster/oqa_lean.sbatch` is committed
  for the snapshot route whenever a push is authorized.
- Artifacts: `~/dev/lean_skillopt/runs/20261116-offline-lc-port/lean_job_66259448/` (job.log, results.tsv,
  lean_version.txt, manifest_check.txt, the compiled copy, the 0-byte compiler output, and the failed job's log).
  Note the `job.log` is missing its final line - a `tee` flush race at exit; `results.tsv` carries the full record.

**CORRECTION to the old A0:** it claimed `OFFICEQA_TRANSFER_MANIFEST.json` was untracked and that its
`source_commit` and per-file `source` paths were null. All three claims were wrong - the manifest is tracked and
the fields exist as `source_head` and `path`, populated, with `source_status` recording that the sources are
untracked in their own worktree. There was no provenance gap. Nine further files were transferred for this port
(manifest entry 2), bringing `research/officeqa/transferred/` to 15 reference files, none on an execution path.

### STILL WITH THE USER after this port
1. **Approve the guarded-`effect` extension?** Documented with two worked examples and the smallest change in port
   note 3.3; deliberately NOT implemented, because it changes contract semantics. **PENDING by instruction.**
2. **Spend 1-2 of the 8 remaining DeepSeek calls on one learner round**, to test whether a paid learner proposes
   hypotheses the deterministic template proposer does not? Priced in port note section 7. **PENDING by
   instruction.**
3. **One more CPU job to check the axiom set?** Same shape as 66259448 but compiling a separately digested
   axiom-printing variant. Not authorized, not submitted.

## A. The authorized task

The two-epoch comparison **Arm A = Original SkillOpt** vs **Arm B = + stage and action feedback**, inside
**8 additional A40-hours** and **24 DeepSeek calls / $1**. Surface any integrity failure or budget overrun
*before* proceeding past it. Full design, including everything the user specified, is in
**`docs/OFFICEQA_COMPARISON_DESIGN.md`** — read that, not a paraphrase.

Final deliverable: one copy-pasteable report with frozen set counts, the prompt audit, per-epoch gate results,
held-out scores, examples of report findings that became skill edits, and actual time and spend.

## B. DONE: the shared rollout and all three pre-paid-call gates (job 66209754)

COMPLETED 2026-11-16 11:10:47, elapsed 00:21:23 after a 48-minute queue wait. Charged **0.36 A40-h (~$0.039)**,
zero paid calls. Local copy: `~/dev/lean_skillopt/runs/20261116-104926_officeqa_shared_66209754` (3.1 MB).
Cluster run dir (= `OQA_SHARED_DIR`):
`/nfs/turbo/umms-csriram/yaojm/lean_skillopt/runs/20261116-104926_officeqa_shared_66209754`.

| | Result |
|---|---|
| training rollout (12, starting skill, 24 turns) | **5/12 hard = 0.4167**, 128 tool calls, 0 tool errors |
| validation baseline (12, starting skill) | **5/12 hard = 0.4167** -> the gate needs **>= 6/12** to accept |
| first-3 gate | PASS (2/3 correct, 17 tool calls, 0 errors) |
| all-12 gate | PASS, answer-key isolation 12/12 |
| prompt audit | PASS both arms; arm A 472,711 chars / ~131,308 est tokens, arm B 488,838 / ~135,788; **arm B == arm A + reports only** |
| grep-cap warning | **0x in arm A, 4x in arm B**; the capped grep output it is drawn from appears **4x in BOTH** |

Rollout outcomes (none fatal, per the user's rule): 5 correct; 2 wrong answers (UID0034 `1987` vs `August 1986`,
UID0079 `11.45` vs `11.73%`); 3 reached the 24-turn cap with no answer (UID0031, UID0026, UID0028); 2 requested no
tool and produced no answer (UID0014, UID0030).

Arm B's reports over the 7 failures flag **10 distinct problems** in 5 categories: `grep_cap_reached` 4,
`operand_provenance_missing` 3, `ungrounded_answer_value` 1, `asked_period_absent` 1, `scale_conversion_missing` 1.

**CORRECTION recorded in the design doc section 1a.** An earlier claim that arm B's grep-cap finding "is a P-layer
finding both arms receive" was wrong for THIS comparison: P was a control *report* in the earlier learned-contract
studies, whereas here **arm A receives no report at all**, so arm B's delta is the whole report. A win for arm B
would show that a deterministic report helps, NOT that stages and action contracts specifically help.

## C. RESULT: both arms retained the starting skill

| | arm A (job 66224637) | arm B (job 66228142) |
|---|---|---|
| epoch-1 candidate on validation | 0.3333 (4/12) -> **REJECT** | 0.4167 (5/12, tie) -> **REJECT** |
| epoch-2 candidate on validation | 0.4167 (5/12, tie) -> **REJECT** | 0.2500 (3/12) -> **REJECT** |
| final skill | starting skill, byte-identical `9d377d1c2906...` | the same |
| optimizer attempts | 8 (4 analyst, 2 merge, 2 ranking), all ok | 8, all ok |
| cost | $0.0721 | $0.0815 |
| elapsed | 00:32:57 | 00:32:27 |

The native gate needs STRICT improvement over 0.4167, so a tie is a reject. Baseline 0.4167 throughout.

**Why arm A's edits were rejected (verified, and independent of the arm comparison):** its epoch-1 edits were
well-targeted - "never end a turn without an `<answer>`", and "a paper coded 8703 is NOT thereby published in
1987", which is exactly UID0034's error. The edit did what it was designed to do: empty answers on validation fell
from 5 to 2. But under `hard` (exact match) a blank and a wrong answer score the same, so converting one into the
other gains nothing, and the added text flipped UID0070 and UID0091 from correct to wrong. Net -1.

Held-out: job **66234927** COMPLETED (19:27). Because all three final skills are the same bytes, that ONE run is
all three evaluations the user asked for. Jobs 66234928 / 66234929 were cancelled as exact duplicates, both
charged 0. **Its outcome has not been read.**

## C2. INTEGRITY FAILURE: arm B's epoch-2 reports were stale (recorded in the ledger)

**All 8** of arm B's epoch-2 failures received stage reports describing their **epoch-1** traces; epoch 2
generated nothing. The hook caches at `<report dir>/<id>.md` and reuses that file whenever it exists, so the key
is the ITEM ID, not the (item, trace) pair. Evidence: 7 of 8 report files are byte-identical to the shared
epoch-1 reports with mtime 16:14:08 (the `cp` at job start); UID0017's was written at 16:14:12 during epoch 1's
*success* minibatch; and for 5 of 8 the tool-call count the report states matches the epoch-1 trace and differs
from the epoch-2 one (UID0026: report says 24, epoch-2 trace made 1). The stale reports did reach DeepSeek - arm
B's epoch-2 failure prompt embedded 8 report blocks.

My earlier claim of "5 reports generated during epoch 2" was wrong: those 5 are exactly epoch 1's five successes.

**Consequence:** arm B's epoch 1 is valid (its reports came from the shared epoch-1 rollout, which is the trace in
the prompt). Arm B's **epoch 2 is invalid as a test of the intervention**. Arm A is unaffected throughout.

**Test gap:** `test_a_report_is_generated_when_none_is_cached` asserted determinism by re-rendering from cache,
which locked the stale-cache behaviour in. No test rendered one item against a second, different trace.

**FIXED in `c353b99`.** The cache key now covers the conversation, the user prompt the actor was given, the
question string and the condition, as `<id>.<12-hex>.md`; the arm stage no longer pre-seeds reports (that copy of
plainly-named files is what enabled the reuse); `oqa_optimizer_input_audit.find_report` accepts both namings so
preserved artifacts still read. Two regression tests, including one that renders the same question from two
different traces and asserts each rendered optimizer request carries its own trace's report. Verified on the real
data: pointed at the 8 stale reports, the fixed hook ignores them and regenerates 8 keyed reports matching the
independently-corrected ones byte-for-byte.

**Follow-up audit (offline, 0 GPU, 0 paid calls) -> `docs/research/OFFICEQA_EPOCH2_REPORT_AUDIT.md`:** all 8 sent
reports reproduce byte-identically from the epoch-1 traces, confirming provenance. Correcting them changes the
diagnosis for 5 of 8 items (UID0017, 0026, 0030, 0034, 0079), but the set carries the same five categories in the
same counts (grep_cap 4<->4, operand_provenance 3<->3, ungrounded_answer 1<->1, scale_conversion 1<->1,
asked_period 1<->1), the payload differs by +1.0%, and BOTH applied edits rest on unchanged categories. The saved
optimizer output contains ZERO references to report content, so every report->edit link is plausible only.
**Decision: no replay.**

## C3. Second audit finding: no cross-epoch memory in either arm

Neither arm's epoch-2 prompt contained the rejected epoch-1 edits, their scores, or a meta-skill summary - verified
against the logged prompts in `optimizer_calls.jsonl`, not reconstructed. `step_buffer` is declared INSIDE the
epoch loop (`trainer.py:1187`) so it resets each epoch, and with `steps_per_epoch = 1` it is always empty, so
`_format_step_buffer`'s "Rejected edits (score X -> Y)" block never appears. `active_meta_skill` is "" because
`use_meta_skill` is off by my deviation. So "two epochs" here is two independent attempts, not cumulative learning.

## C5. Does the report surface diagnoses a trace-only review would miss? NO

`docs/research/OFFICEQA_REPORT_VALUE_PROBE.md`. Eight attempts, two views each in separate contexts (trace only vs
trace + corrected report), identical instructions, then one blind adjudicator per case with the two reviews
anonymised in randomised order.

- The two views agree on the **primary failure in 7 of 8** cases.
- The blind adjudicator judged the report-guided review **more precise in 7 of 8** and its instruction better in
  5 of 8 - **but the attribution test kills that credit**: of the 12 issues it credited only to the report-guided
  reviews, **not one appears in the corresponding report**. The praised specifics (`30,442`, the lost year
  headers, column mis-mapping, glob counts) are absent from the reports, which say only `ungrounded_answer_value`,
  `grep_cap_reached` x2 and `asked_period_absent` respectively. With one draw per cell, "the report prompts closer
  reading", "reviewer variance" and "the adjudicator prefers specifics" cannot be separated.
- **Misleading claims: 5 of 8 cases on each side** - the report does not make reviews cleaner.
- **The one report-attributable effect is a false claim.** UID0014's report asserts `scale_conversion_missing` for
  1004461.0 ("No conversion step appears"), but the trace contains `1,004,461 (in millions) = $1,004.461 billion`.
  The report-guided reviewer quoted it as evidence; the blind adjudicator caught it. The finding fires 29x for
  that item and is demonstrably wrong at least once. Signal source: a stage requirement combined with an action
  check - the operand ledger saw the raw figure at step 9 and the stage rule fired before the conversion line.
- In 5 of 8 cases the **trace-only** review caught real issues the report-guided one missed (no stopping rule,
  the Exhibit 3 contradiction, table-of-contents reads, duplicated spans).
- A methodological note: I first "verified" the UID0014 claim by searching for the report's literal `1004461.0`,
  found no conversion, and was wrong - the trace writes `1,004,461`. The adjudicator was right.

**Still not established:** whether DeepSeek, over a real eight-trajectory minibatch under the real analyst prompt,
would propose different EDITS that survive merge, budget and the 6/12 gate. Reviewers answered a different
question with a different output. The replay that would settle it is scoped in
`OFFICEQA_EPOCH2_REPORT_AUDIT.md` section 6 (1 A40, 4 calls, ~25 min, inside the remaining caps).

## C6. Contract/check audit: what to change in the report generator

`docs/research/OFFICEQA_CONTRACT_AUDIT.md`. Offline, 0 GPU, 0 paid calls. Nothing implemented.

**Provenance:** the rule set is **fixed, hand-written Python**. No learner ran, no rule was proposed/retained/
revised, and **no Lean was involved** (the `lean` matches are the `lean_skillopt` path). Of 14 rules, 3 rest on
documented tool behaviour, 1 on a self-labelled unverified empirical hypothesis (`table.scale.1`), the rest are
manually written. **Only 6 of 14 fired** across 8 failures + 3 successes.

**Reliability:** the two rules carrying the report's only distinctive content are its least reliable.
`scale_conversion_missing`: **5 of a 10-case sample are demonstrable false positives**.
`operand_provenance_missing`: fires on arithmetic intermediates, on the actor's own converted values, and on the
answer-format example inside the question text; 1 of 18 sampled firings looked genuinely useful.

**One structural cause:** the rules read a value's EVIDENCE provenance (`_match` over supplied pages + tool
observations) and never `state["computed"]`, so a conversion the actor performed is invisible and the obligation is
reported as outstanding. Verified: UID0014 converted `1,004,461 -> $1,004.461 billion` at **step 1**; the raw value
is recorded as an operand at steps 9/19 and the report says "No conversion step appears".

**UID0026 (wrong-period row, not wrong column):** the actor used the `1961-January` paper-money level (29,385)
instead of `1960-December` (30,442) as the calendar-1961 baseline; the column was CORRECT (header cell 3 = "Total
paper money"), which also settles a disagreement between the two probe reviewers (30,442 right, 32,869 wrong
column). `operand_period_mismatch` could not fire because (a) the question states no period - it must be inferred -
so the obligation is UNKNOWN, and (b) both values were classified `computed`, not operands. The ledger binds only
the YEAR, never the month, so a year-level check would call both rows "1961" and consistent. The requested year is
itself uncertain from the supplied text (the "Treasury notes of 1890" column goes `1` -> `*` = "less than
$500,000", which is a rounding floor, not removal) - recorded as unresolved.

**Prioritised changes (1-4 best supported), each with a fire/stay-quiet example in the doc:** consult
`state["computed"]` before claiming a missing conversion; reword "No conversion step appears" to a claim the
checker can support; exclude numbers occurring in the task prompt's own text from operand extraction; do not report
provenance-missing for arithmetic results. Then: bind month + column; separate "no arithmetic cue" from "no
arithmetic requested"; make period-rule inactivity explicit.

**Skill-edit candidate:** only UID0034's `7069` read out of a FRASER URL generalises usefully - and arm A's
analyst derived that rule from the raw trace with no report at all.

## C4. Earlier work (all verified)

1. **Question sets frozen** (rule OQA-SEL-2, digest `fc02291540ef...`): 12 train / 12 val / 24 held-out, each
   balanced 3-3-3-3 or 6-6-6-6 across easy|hard x single|multi, disjoint, **no shortfall**. Record:
   `docs/research/OFFICEQA_FROZEN_SETS.json`; per-item evidence verdicts:
   `docs/research/OFFICEQA_EVIDENCE_CHECK.json`.
2. **The evidence test is key-free** and excluded exactly one item: **UID0046 -> UID0063** (same easy/single
   stratum). Its pie-chart legend term `service-related` occurs 0 times in the whole 448 KB bulletin and its
   supplied page carries no figure at all. 70 of 72 assessed items AVAILABLE, 1 AVAILABLE_VIA_TOOLS (UID0030).
3. **Corpus**: 68 of 68 needed bulletins present (`--verify-docs` PASS), 50 MB,
   `$PROJECT_ROOT/officeqa_data/treasury_bulletins_parsed`.
4. **Split materialized**: `$PROJECT_ROOT/officeqa_data/split_cmp` loads **train=12 val=12 test=24**; the
   trainer's `valid_seen`/`valid_unseen` resolve to val/ and test/.
5. **24 tool turns restored** plus upstream `max_completion_tokens: 16384` and `workers: 4`.
6. **Checkpoint fixed**: wrong answers, a missing `<answer>` and reaching the turn cap are **outcomes**, not
   fatal. Verified against the preserved job 66196051: the same traces that FAILED before now PASS, and with
   `--evidence` it refuses a run containing an excluded item.
7. **Prompt audit works, previewed on real traces**: arm B = arm A + the report bytes only (TRUE), both arms
   PASS the leakage audit, and the grep-cap warning sentence appears **0x in arm A, 1x in arm B** while the
   capped `grep` output it is derived from appears in **both**. To be re-run on the 24-turn traces as the
   official gate before the first paid call.
8. **70 local tests pass** in the python3.12 venv (see section E).

## D. Spend (final for this comparison unless a re-run is authorized)

| | Used | Cap |
|---|---|---|
| A40-hours (this comparison) | **1.77** (0.36 shared + 0.55 arm A + 0.54 arm B + 0.32 held-out) | 8 |
| A40-hours (all OfficeQA to date) | 3.77 | - |
| DeepSeek calls | **16** (8 per arm), all `ok`, no 429, no 5xx | 24 within $1 |
| DeepSeek USD | **$0.1536** | $1 |

Cancelled without charge: 66219432 (90-min arm A, PENDING 2h03m), 66234928, 66234929.

`deepseek-flash` has a **1M-token** context, so the 131k-token analyst prompt is within limits (checked against
api-docs.deepseek.com, not assumed). Off-peak rates now: $0.15/M cache-miss input, $0.60/M output, so ~$0.021 per
analyst call and ~$0.15-0.25 for the whole comparison. The $1 cap is not the binding constraint; the 24-call cap is.

A step needs 5 or 6 optimizer calls (5 + the ranking call when the merged pool exceeds the budget of 4), so four
steps need 20-24. **That is why the arms run sequentially**: arm A's actual count decides whether arm B fits.
`oqa_optimizer_precheck.py` estimates it from the rollout and refuses to start if the estimate exceeds the cap.

## E. Next actions

**Blocked on the USER's decision.** The three items from the port are in section A0. From the earlier comparison:
1. Re-run arm B's epoch 2 with the report-cache fix? One A40, ~8 more calls; the budget covers it
   (8 of 24 calls and 6.2 of 8 A40-h remain).
2. Restructure as one epoch of two steps, so `step_buffer` actually carries the rejected edits forward?
3. Apply the `OFFICEQA_CONTRACT_AUDIT.md` changes 1-4 to `oqa_check.py` itself? The port implements the same
   semantics in a separate, opt-in module and leaves `oqa_check.py` alone, so this is still open.

If neither is taken, the comparison is finished and the only remaining act is to read job 66234927's held-out
score (currently unread by instruction) and state it for all three skills at once.

### The exact arm submit command (paths resolved)
```
D=/nfs/turbo/umms-csriram/yaojm/lean_skillopt/officeqa_data
SHARED=/nfs/turbo/umms-csriram/yaojm/lean_skillopt/runs/20261116-104926_officeqa_shared_66209754
ARM=B OQA_SHARED_DIR="$SHARED" OFFICEQA_SPLIT_DIR="$D/split_cmp" \
  OFFICEQA_DOCS_DIR="$D/treasury_bulletins_parsed" OQA_SELECTION_RULE=OQA-SEL-2 \
  OQA_CALL_BUDGET=24 OQA_CALL_STEPS=4 STAGE=arm \
  research/submit.sh officeqa <deployed commit> -- --time=01:30:00
```

## F. Where things are

| | |
|---|---|
| Local HEAD | pushed to `origin/officeqa-contracts`; deployed snapshot `6939c29a72ea` |
| Split / docs / frozen record | `$PROJECT_ROOT/officeqa_data/{split_cmp,treasury_bulletins_parsed,oqa_frozen_sets.json,oqa_evidence.json}` |
| Config (BOTH arms) | `configs/research/officeqa_cmp.yaml` - the only difference between arms is `OFFICEQA_STAGE_REPORT_DIR` |
| Job script | `research/cluster/officeqa.sbatch`, `STAGE=tools|check3|pilot12|epoch1|shared|arm|heldout` |
| This session's audit evidence | `~/dev/lean_skillopt/runs/20261116-160952_officeqa_arm_66228142/audit_evidence/` - 44 files, 208 KB: the 16 paired reviews, the 8 blind adjudications + the de-anonymisation key, the corrected epoch-2 reports, the 3 success reports, the success-selection rule, the old-vs-corrected classification and the rule-firing tally. Copied out of the session scratchpad, which is cleared on reboot |
| Local test venv | `/private/tmp/claude-501/-Users-yaojiaming-dev-lean-skillopt-SkillOpt-officeqa/82390561-79e4-43e9-b247-03526392e651/scratchpad/venv312` (workstation python is 3.9 and cannot import SkillOpt) |
| Tests | `tests/test_officeqa_{contracts,tool_probe,answer_key_isolation,freeze_sets,optimizer_precheck,shared_rollout_injection}.py` - 87 pass; `test_officeqa_lc.py` - 38 pass |
| Learned-contract port | `research/officeqa/oqa_lc_*.py` (9 modules), docs in `docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md`, output in `~/dev/lean_skillopt/runs/20261116-offline-lc-port/` |

### Settled facts (do not re-derive)
- Tool calling needs `--enable-auto-tool-choice --tool-call-parser qwen3_coder`; `hermes` is wrong for Qwen3.5-4B.
  `--max-model-len 65536` fits one A40 (peak 38.7 of 46.1 GB).
- The actor is handed the gold parsed pages and its tools are restricted to the gold files.
- The answer-key leak is fixed (`c07fefb`) and audited both ways.
- The arm-B hook now **generates** a missing report instead of skipping it; skipping would have turned arm B back
  into arm A from epoch 2 onward, since epoch 2's traces do not exist until the run makes them.
- `use_slow_update` / `use_meta_skill` are off for both arms: at epoch 2 they would add a second skill update per
  epoch, ~24 rollouts and ~2 calls per arm. Symmetric, documented in the config and the design doc.
- **Negative so far:** after the false-positive fix (`f819011`) arm B produced zero advice on the two real
  failures, and its one useful finding is a P-layer finding both arms receive.

## G. Standing rules
Never tune on held-out. Never derive contracts from the skill. Keep the scorer, gate, analyst prompts and
skill-update rules unchanged apart from the approved answer-key fix. No secrets, cluster paths or run dirs in
tracked files. Record every submission in `docs/research/jobs.tsv` and SUBMITTED/SETTLE lines in the coordination
ledger. Timestamps come from `date`.
