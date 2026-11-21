# OfficeQA two-baseline campaign — launched, plus contract-arm progress
**2026-11-21 05:00 EST · branch `officeqa-contracts` · worktree `SkillOpt-officeqa` · commit `HEAD` · not pushed**
Rules **OQA-SEL-4** (splits) · **OQA-BASE-1** (campaign) · **OQA-T2S-1** (Trace2Skill) · **OQA-OCC-1** / amendment **C-1** (contract arm)

Abbreviations on first use: **S0** = the unchanged starting skill document. **Actor** = the frozen
target agent (Qwen3.5-4B, hosted). **Optimizer / teacher** = DeepSeek V4.1 Flash. **Episode** = one
question attempt. **Attempt** = one HTTP request to the actor (an episode makes many).

---

## 1. What ran, and what came out

**THE CAMPAIGN IS COMPLETE.** Both baselines ran end to end and all three held-out evaluations
finished — 180 held-out episodes, 30/30 in both passes for every endpoint. **Scores remain sealed.**

| Job | Stage | State |
|---|---|---|
| 66552420 | prep (free) | **COMPLETED** — preflight READY 39/0/0, 2,428 tests |
| 66552421 | S0 reference, 48 training + 12 validation | **COMPLETED** 23:16, all 12/12 |
| 66552422–425 | **Original SkillOpt** epochs 1–4 | **ALL COMPLETED** |
| 66553248 | **Trace2Skill**, third attempt | **COMPLETED** 13:33 — the faithful run |
| 66553250 / 66553251 / 66553363 | held-out: S0 / Original SkillOpt / Trace2Skill | running or done, 2 passes × 30 each |
| 66552633, 66552721, 66552984 | contract: v7, v8 + C-1 replay, v9 + C-2 replay | all did their work |
| 66553083 | contract: **Lean compiled** | work COMPLETED |
| 66553105 | contract: determinism probe | work COMPLETED |

**Six jobs are marked FAILED whose substantive steps all succeeded**, exiting 1 only because the body
runs the test suite last and one byte-reproduction test is pinned as expected-to-change. Read
`job.log` before concluding a stage did not run. The genuinely failed jobs were: one free prep
(66552338, a defect in my preflight), one free Lean submission against a stale bundle (66553061), and
**four Trace2Skill attempts** (§6).

### 1a. Original SkillOpt — the result

Four epochs. **0 accepts and 4 rejects on ordinary bounded-edit steps.** The only change that survived
the gate came from the **gated slow update at epoch 2**; epochs 3 and 4 changed nothing (v3 and v4 are
byte-identical to v2).

| | |
|---|---|
| skill lineage | S0 `9d377d1c2906` (887 B) → v1 `5bd17ea109bc` (940 B) → **v2 `e35673d87cb7` (4,518 B) = the selected endpoint** |
| validation | S0 **4/12** → selected **5/12** (`best_selection_hard` 0.4167, `best_origin: slow_update_epoch_02`) |
| S0's own spread over four independent training passes | **5, 4, 4, 5** of 12 |

**The gain is one question, and S0's own sampling spread is ±1 question.** The acceptance rests on a
1/12 margin; twelve validation questions cannot resolve finer. Upstream's strict-improvement rule was
preserved, so this is the framework's own behaviour, not a weakened gate. Whether it transfers is what
the held-out measurement is for.

### 1b. Trace2Skill — the result, and two degraded runs preserved as evidence

| | first run (66553058) | second (66553156) | **third (66553248)** |
|---|---:|---:|---:|
| truncated calls | 3, undetected | 3 | **0** |
| hierarchy | 60 → 8, level 2 failed | 27 → 9 → 3, level 4 failed | **60 → 22 → 11 → 6 → 2 → 1** |
| consolidated items | 77 | 47 | **18** |
| `conflicts_unresolved` | true | true | **none** |
| verification | unparseable | — | **complete, all 18 APPLIED** |
| endpoint | 18,590 B | — | **24,121 B** |

The third run is the faithful one: the final merge succeeded, so there is no mechanical fallback and
the method's defining property — hierarchical, conflict-free consolidation — is actually achieved. The
item count falling **77 → 47 → 18** is the deduplication the method exists for, visible only once the
replies stopped being cut off. Individual merges still failed at three levels; those keep their inputs
rather than losing proposals, which is why the hierarchy still converged. The two degraded runs are
preserved as `trace2skill_degraded_66553058/` and `trace2skill_partial_66553156/`.

### 1c. The three held-out endpoints, all distinct

| endpoint | digest | bytes | job |
|---|---|---:|---|
| S0 | `9d377d1c2906` | 887 | 66553250 — **COMPLETE**, 30/30 × 2, 29:39 |
| Original SkillOpt | `e35673d87cb7` | 4,518 | 66553251 — **COMPLETE**, 30/30 × 2, 32:45 |
| Trace2Skill | `03d6564f1d10` | 24,121 | 66553363 — **COMPLETE**, 30/30 × 2, 22:57 |

All three wrote a `PROVENANCE.json` under one config digest `671eb1d42293` and one commit
`c35df7ef6ba6`, rule OQA-SEL-4, split `valid_unseen`, n=30, passes=2. **180 held-out episodes, every
one of 30/30 in both passes for all three endpoints — no partial pass, no dropped question.**

Each writes a `PROVENANCE.json` carrying the skill digest, config digest, commit, frozen-set rule,
split, question count and pass count — the complete effective inputs the reuse rule needs.
**Scores and trajectories stay sealed.**

## 2. The frozen setup

### 2a. Splits — OQA-SEL-4, digest `b0dbbd4be97fc245`, **disjoint, 0 problems**

| Set | n | Released split | Strata (category × document count) | Availability |
|---|---:|---|---|---|
| Training | 12 | `train` (50 available) | 3/3/3/3 | 12 AVAILABLE |
| Validation (all gates) | 12 | `val` (24 available) | 3/3/3/3 | 12 AVAILABLE |
| Held-out | 30 | `test` (172 available) | 8/8/7/7 | 24 AVAILABLE, **6 UNTESTED** |

Training and validation are OQA-SEL-2's 12 and 12 **carried forward unchanged**. The held-out 30
retains OQA-SEL-2's 24 and draws 6 more from OQA-SEL-3's 60 by `sha256("OQA-SEL-4"+uid)` — a fixed
permutation of the rule name and item id and nothing else. Every set is a **subset** of the freeze it
claims to subset, so the new measurement overlaps the two historical held-out numbers on 24 of its 30
questions. Strata come from **released metadata**; no OfficeQA task category is invented.

**Prior research exposure, recorded per item.** All 54 questions were used before, and **no item
changed the role an earlier freeze gave it** — a test asserts this, because a question that was once
training and is now held-out would be contaminated by construction.

**Source-document overlap, measured and not empty.** Training rests on 21 documents, validation 28,
held-out 46. Shared: **train↔held-out 1 document** (UID0014 trains on the July 1942 bulletin p. 76,
UID0013 is held out on it); train↔val 1; val↔held-out 2. A real leakage channel for a
document-specific edit, counted and named rather than assumed absent — and small.

### 2b. Actor, tools, response handling — **effective**, read back from outbound requests

| Setting | Value | How it is known |
|---|---|---|
| Actor / endpoint | `qwen3-5-4b` (FP8) on `https://api.empiriolabs.ai/v1` | preflight read the **live** backend config |
| Temperature | 0.7 | preflight; and 285/285 saved requests |
| **Output cap** | **8,000 tokens** | `model.target_openai_compatible_max_tokens`; 285/285 requests |
| Thinking | `extra_body {"enable_thinking": false}` | transmitted **and effective** — 0 alternate-reasoning characters in 285 responses |
| Recovery | narrow `committed_answer` | pinned in the job, not defaulted |
| Tools | `glob`, `read`, `grep`, offline, local files | frozen; schema digest `7e4fe0e714eb` |
| Turn budget | 24 tool turns, ≤4 queries/turn | config, resolved |
| Starting skill | **S0 unchanged**, 887 bytes, `9d377d1c2906` | `skillopt/envs/officeqa/skills/initial.md` |
| Scorer / corpus | existing / `treasury_bulletins_parsed` | unchanged; **document rendering frozen** |

> **A correction to the record.** Two config fields look like the output cap and **only one is**.
> `env.max_completion_tokens: 16384` reaches **no prompt and no API call** — its only consumer is
> `rollout_settings_fingerprint`, the digest the provenance guard compares. The transmitted cap is
> **8,000**. The two have differed by 2× all along. I left the inert 16384 in place **on purpose**:
> changing it would invalidate the fingerprint of every saved S0 trajectory and force Trace2Skill to
> re-pay for 12 rollouts. Documented rather than silently corrected at that price.

The split-header column-span problem is **not** a launch prerequisite and has not been touched.

### 2c. Original SkillOpt — "Original SkillOpt framework baseline"

`configs/research/officeqa_base2.yaml`. Arm A runs upstream `scripts/train.py` with
`OFFICEQA_STAGE_REPORT_DIR` **unset**, so the analyst prompt is byte-identical to upstream's. Resolved
and asserted by the preflight:

- **Four epochs** over the same 12 questions; `batch_size 40 > 12` → `ceil(12/40) = 1`, so **one
  ordinary update per epoch**.
- **Gated slow updates from epoch 2**, `slow_update_samples: 6` — six training-subset questions per
  scheduled slow update, by specification (against the three-arm campaign's 10 and upstream's 20).
  `slow_update_gate_with_selection: true`, so the slow-update candidate faces the **same** 12-question
  gate as an ordinary step rather than being force-injected.
- **Meta-skill on** (optimizer-side memory; it does not edit the skill).
- **Upstream's strict-improvement acceptance rule preserved.** Ties are not accepted; no update is
  forced to manufacture a changed skill.
- The separate 12-question validation set serves every gate. `eval_test: false` — the trainer never
  touches the held-out set. Both previous-skill and current-skill slow-update rollouts are budgeted.
- Native handling of prior edits, rejected edits and memory is upstream's, unmodified.

**Adaptation, declared:** epoch 1's training rollout and gate baseline are the **injected S0 traces**
(`run_batch` resumes by id, so nothing new is generated). Not a shortcut — it is what makes both
methods reason about the *same* S0 evidence rather than two independent draws at temperature 0.7.

### 2d. Trace2Skill — pinned, then adapted

**Pinned:** `Qwen-Applications/Trace2Skill` at **`3d0b52a140f002a512930252b613c49048f7d5ac`**
(2026-05-02), Apache 2.0, arXiv:2603.25158. Inspected before adapting. Structure reproduced is
upstream's `ParallelSkillEvolver`, **not** a one-shot summary:

```
ANALYSIS  one call per trajectory   error analyst if it failed, success analyst if it passed
MAP       one call per record       -> a semantic patch, in parallel
REDUCE    HIERARCHICAL              48 patches -> 6 batch merges (size 8) -> 1 final merge
APPLY     the merged patch becomes the new skill document
VERIFY    every item checked: APPLIED / PARTIAL / MISSING, plus UNREQUESTED content
```

`chunk_list` reproduces upstream's balancing exactly, including round-robin distribution of a remainder
smaller than half a batch; a property test checks no item is lost for any n in 1..59. Input: **48
fixed-S0 trajectories** (4 passes × 12 questions) from one frozen configuration.

**Four adaptations, each with its reason** (`research/officeqa/oqa_t2s_prompts.py`):

1. **Domain** — document QA over parsed Treasury Bulletin text, not spreadsheets.
2. **Analysis mode** — upstream's **single-call** analyst, not its agentic one. The agentic analyst is
   handed `gold.xlsx`, and our rule keeps answer keys in scoring. Both are released upstream modes, so
   this is a choice between two, not an invention. **Consequence, stated not hidden:** our analyst
   cannot validate a diagnosis by implementing a fix and re-evaluating, so upstream's causality
   requirement is weakened to what the log supports, and the prompt asks for that rather than demanding
   a guarantee it cannot check.
3. **Skill interface** — the single markdown document SkillOpt optimizes, not a skill *directory*.
   That document is the artifact under study. Upstream's own markdown path already targets `SKILL.md`.
4. **Model** — DeepSeek V4.1 Flash, matching our SkillOpt teacher, through
   `skillopt.model.chat_optimizer`, so both methods use the same route-pinned, ledger-enforced path.

**Nothing is borrowed from SkillOpt.** A test greps the module for `valid_seen`, `selection_eval`,
`slow_update`, `use_gate`, `num_epochs` and fails if any appears. The consolidated skill **is** the
endpoint; validation may measure it but is not allowed to become an undeclared tuning loop.

### 2e. The information boundary, enforced mechanically

| Class | Contents |
|---|---|
| **Permitted** | `conversation.json` — actor messages, executed tool calls and their observations (which for OfficeQA *are* the document text the actor read) — and the saved target prompts |
| **Triage only** | `results.jsonl` `hard`, the binary verdict, used *only* to choose which analyst runs — upstream triages the same way |
| **Forbidden** | `ground_truth`, `soft`, and the provider's alternate reasoning field |

`render_permitted` emits only the three known entry shapes, so a field added to a trace later cannot
leak. The answer-key guard is a **provenance** predicate (see §6b) and was verified on the real 48
trajectories: **0 violations, 7 explained hits.**

---

## 3. Resources — measured, not projected

### CPU, under the convention that charges a started job its FULL REQUESTED LIMIT

| group | jobs | **requested h** | elapsed h |
|---|---:|---:|---:|
| **Trace2Skill (7 submissions)** | 7 | **17.50** | 0.94 |
| Original SkillOpt epochs | 4 | 6.25 | 1.02 |
| held-out | 3 | 4.50 | 1.42 |
| contract arm | 7 | 3.00 | 0.14 |
| S0 reference / prep | 3 | 2.17 | 0.44 |
| **total** | **24** | **33.42 of 50** | **3.96** |

**Requested is 8.4x elapsed, and 17.5 of it is one stage's retries** (§6j). **16.58 requested hours
remain** of the 50 authorized.

### API spend — assembled from two incomplete sources, which is itself a finding

Because the ledger was inert for most of the campaign (§6f) **and** the held-out stage did not set an
actor call log, neither source covers everything. Stated as what each one actually knows:

| source | covers | actor | optimizer |
|---|---|---|---|
| per-job **call logs** | prep, s0, Original SkillOpt epochs, Trace2Skill | 2,059 calls, $1.0569 | 206 calls, $2.0632 |
| **live ledger** (post-fix) | Trace2Skill run 3, the three held-out jobs | 1,403 attempts, $0.8081 | 35 attempts, $0.3121 |

**Actor total ≈ 3,462 attempts of 20,000 (17%) and ≈$1.87 of $15.00 (12%)** — the two sources are
disjoint for the actor, because the stages that logged are exactly the stages the ledger missed.
**Optimizer: 206 calls of 400 (52%) and $2.06 of $20.00 (10%)** from the call logs, which do cover every
optimizer-calling stage; the ledger's 35 is a subset of those, not an addition.

The optimizer's **call count is the tightest figure in the campaign at 52%**, driven by five Trace2Skill
attempts; its dollar cost is only 10%. The held-out stage makes no optimizer calls. **GPUs: 0 of 0.**

**Both gaps are now closed** — the ledger is wired and every paid stage refuses to start without it, and
the held-out stage logs its actor calls. One stage logging and another not is how an accounting becomes
a reconstruction, and this phase's spend record will remain a reconstruction however carefully it is
assembled.

**Concurrency** held to the instruction throughout: at most 4 CPU jobs (the peak was 3), 4 actor
requests, 2 DeepSeek requests. **Resume** worked as designed — the Trace2Skill cache carried 94 to 97
paid replies across each resubmission, so five retries of a 48-trajectory analysis cost the analysis
once.

**Historical accounting, separate and untouched:** EmpirioLabs 12,042 of 30,024 attempts and $8.0147 of
$17.00; DeepSeek 105 of 266 and $3.4689 of $10.00; CPU **disputed** — 35.649 of 44 core-hours or 66.483
of 44 requested walltime. **Which convention governs that 44-hour ceiling remains your decision**, and
nothing in this phase bears on it.

## 4. Evidence: what ran and what it showed

**Prep (free, 1:18).** 2,428 tests passed, 11 skipped. The 12/12/30 split materialized and was compared
to the manifest **id by id**, not by count — exact match in all three roles, pairwise disjoint. The
ledger's cap mechanism was proven to hold **on this filesystem** against concurrency, restarts and an
unwritable ledger (it rests on `O_CREAT|O_EXCL` atomicity, which is exactly what can differ between
local disk and NFS). **Preflight: READY — 39 pass, 0 warn, 0 fail.**

**S0 stage (23:16).** 48 training + 12 validation episodes, all 12/12 per pass. The gated first-three
checkpoint passed before the remaining 45 were run.

**Original SkillOpt epoch 1 (3:40).** Wrote `best_skill.md`, `meta_skill/`, `slow_update/`, `steps/`,
`summary.json`. Epoch 2 — the first *gated* slow update — is running.

**The saved 2026-11-18 collection is configuration-identical** and reusable in principle: recorded
skill digest `9d377d1c2906` equals the current S0 digest, recovery policy `committed_answer`,
`enable_thinking: false`, 24 tool turns. Verdict `REUSABLE_SUBJECT_TO_PER_ROW_GUARD`. I am **not**
relying on it — the S0 stage collected all four passes fresh in one job so all 48 carry one settings
fingerprint. The check is reported because it was asked for, and because it independently shows the
frozen condition is stable across runs.

---

## 5. The contract arm — what is built, and what is not

Developed in a **separate snapshot** (`oqa_ct_20261121*` bundles, `runs/20261121-officeqa-contract/`)
so contract changes cannot reach a running baseline job.

### 5a. The prerequisite you named, done
**The mixed-version bundle is corrected.** Job 66414726 froze label v6 = `36c37a397eddaa65` and wrote
its analyses into `collect_20261118/v2_bind/`; job 66414832 then ran after two semantic-function
sources had changed, the guard **correctly** refused to let v6 mean a second thing and exited 1, but the
later steps still rewrote that directory's analyses. File timestamps confirm it: bundle 23:46, analyses
23:49. The current state is now frozen as **v7** (`fd0645517a50e991`) in a **new** directory, and
`v2_bind` is **preserved and verified** — all four digests byte-identical before and after, with the job
set to fail if any changed. Nothing was relabelled in place or overwritten.

### 5b. Occurrence-level source binding works where column semantics did not
The source-binding investigation refuted column **semantics** from the pipe rendering. It never tested
**occurrence identity**, and a `grep` observation prefixes each line with its **line number**, so a cell
has an observable position — (document, line, field index) — independent of what its column means.

Measured over the 24 saved **training** records (reused development evidence; no validation or held-out
record takes part):

| | |
|---|---:|
| records where the occurrence channel exists | **20 of 24** |
| observed numeric occurrences | 4,261 |
| distinct numbers the actor wrote | 1,117 |
| at a **unique** observed position | **201** |
| at several positions → correctly UNKNOWN | 122 |
| at no position (actor-derived results) | 794 |
| **SUPPORTED** bindings | **64** (prototype: 0) |

**A unique position is not a binding.** Support requires corroboration independent of the number:
`ROW_LABEL` (the actor's own message names a distinctive token from the line, stopwords removed) or
`QUERY` (the line came from a grep whose pattern the actor chose). 64 of 201 reach that bar.

> **I nearly reported 201 of 201.** A third candidate, `SOLE_IN_ROW`, lifted the rate to **100%** — and
> that attractive number was the tell. It is nearly a **tautology**: two occurrences of one value on a
> line would occupy two field indices, so the position would not be unique. Measured, **200 of 201**
> were sole-in-row, entailed share **0.995**. It is computed and reported for transparency and excluded
> from support. Honest support is 64, not 201.

Reproduced **exactly** under cluster Python 3.11 as under local 3.9.

### 5c. The amendment chain C-1 → C-2 → C-3, each measured against a prediction written first

**C-1.** `oqa_lc_chain._best_cell` was handed every cell whose value matched an operand and **silently
returned one**, ranked by exact match then FLAT column then earliest step — so the ledger could discharge
or violate a requirement against a cell the actor may never have read. C-1 refuses: one candidate →
selected; several + a SUPPORTED occurrence → selected with the position recorded; several + UNKNOWN →
**no selection**, rivals recorded.

**C-2** repaired the two defects C-1's own replay found: withdrawn bindings made downstream obligations
*vanish* rather than become UNKNOWN (464 refusals × 2 = 928, the whole ledger gap), and 39.9% of "ties"
were one cell seen through several observations.

**C-3** — an unknown year was becoming an **accusation**. The span guard read `(cp.get("year") or -1)`,
so "unknown" became −1, which lies outside any span of real years, and fell through to VIOLATED. **A
sentinel standing in for unknown, used in an inequality, is how an undetermined fact becomes an
accusation.**

| over the same 24 records, 659 operands | old tie-break | C-1 | C-2 | C-3 |
|---|---:|---:|---:|---:|
| operands with a selected source cell | 566 | 102 | 257 | 257 |
| ledger obligations | 1,839 → 2,025 | 911 → 2,025 | **2,025** | **2,025** |
| DISCHARGED | 254 | 81 | 192 | 192 |
| **VIOLATED** | **186** | 30 | 69 | **14** |

**What each prediction did.** C-1's held in direction (selections can only fall). C-2's sharpest held
*exactly*: both arms carry **2,025 obligations**, so the gate changes obligation states and never their
existence. C-2's prediction that DISCHARGED and VIOLATED would not rise was **violated as written, and
the fault was mine** — I wrote it as though only one of its two repairs were applied. C-3 moved the 55
false accusations to UNKNOWN and returned the stage-diagnosis-group count to its historical 14 from
C-2's 17, which is independent support for it being the right repair.

**C-2's accusations are not claimed**, per its own prediction 5: 55 of 69 were false by construction and
the remaining 14 rest on bindings whose correctness I have not established. **And the defect predates the
gate** — the old tie-break arm carries 186 of the same class, so every prior VIOLATED count in this
project, including the three-arm campaign's delivered reports, is inflated by it. That is a finding about
the historical record, not only about tonight.

### 5d. Lean, compiled in a job — and what it does not cover

Job 66553083, Lean 4.20.0: `OqaConnected.lean` over the delivered facts — **50 evidence examples
encoded, 0 skipped, returncode 0, "all statements accepted"**. The standing theorems in
`OqaContracts.lean` compiled clean.

It verifies the **resolution algebra**: that an UNKNOWN or CONFLICT binding can never yield VIOLATED,
that a discharge requires its binding, that a withdrawal reopens rather than silently satisfies. That is
exactly the soundness condition C-1, C-2 and C-3 rely on, since all three make *more* bindings UNKNOWN —
the theorem says doing so can withdraw a discharge but can never manufacture an accusation. **It does not
establish that the extraction is correct, that the occurrence binding picks the right cell, or that the
specification is complete.** No Python mirror was substituted: the first two attempts failed on the
*binary* (`elan/bin/lean` is a shim with no configured toolchain; the real one is under
`elan/toolchains/leanprover--lean4---v4.20.0/bin/`).

### 5e. Still missing from the §4 demonstration — the honest gap list

1. **A genuinely learned rule with its evidence.** Not done. The current hypothesis
   (`96c3d1bd9aad6f4d`) is about *units*, and both witnessed error classes are *column selection*, which
   it cannot express. Deliberately not started: tonight repeatedly showed that semantic work done in a
   hurry produces half-right results, and a learner needs evidence selection, a freeze, a proposal, a
   compilation into the vocabulary and a check on later records.
2. **A supported diagnosis on a later record.** Not done — needs (1).
3. **A successful/recovered control.** Not done. UID0026 is the natural witness: one message holds both
   a superseded wrong calculation and the committed right one.
4. **Lean verification** — **DONE** (§5d), with its scope stated narrowly.
5. **A counterexample-repair path with regressions** — **DONE**: C-1 → C-2 → C-3, each with a prediction
   committed before its replay, each repairing a counterexample the previous one's measurement produced,
   with 146+ tests as the regression net. The "isolated optimizer rehearsal" of delivering a report is
   not done.

## 6. Nine defects, all mine, each caught by machinery rather than by a bill

Listed because the campaign's credibility rests on them having been found, and because several were
caught by checks that only existed because an earlier one had been found.

**(a) The preflight asserted the backend's defaults and called them the campaign's settings.** Job
66552338 (free): `outbound/model` = `gpt-4o-mini`, `outbound/base_url` = `api.openai.com`. It read
`_initial_config("target")`, which reads only role env vars, in a process that had never applied the
config. The config reaches the backend only via `configure_openai_compatible`
(`trainer.py:843`). Fixed, plus a new check that the fallbacks are **not** in force.

**(b) Trace2Skill's boundary guard conflated a correct answer with a leaked key.** Job 66552508, 2
seconds, no paid call. The hits were real and not leaks: UID0017's `[10102000000, 4.73]` and UID0033's
`March 3, 1977`, both in an **actor message**, both on episodes the actor **answered correctly**. On a
correct episode the actor's own answer **is** the ground-truth string. Now a provenance predicate.

**(c) Trace2Skill never configured the optimizer route.** Job 66552741: all 48 calls raised *"Azure
OpenAI endpoint is not configured"*, zero paid. `chat_optimizer` is a router whose default is Azure.
**This was the same lesson as (a), hours apart**, and a standing memory had already warned that it
"applies to scripts that run outside SkillOpt's trainer too".

**(d) The dry run reported healthy for both (b) and (c)** because it short-circuited before configuring
anything — a self-check that skipped the thing most likely to be wrong. It now configures too.

**(e) A missing `collections` import**, caught by that dry run. pyflakes and an AST test now cover the
class.

**(f) THE SPEND LEDGER WAS INERT FOR THE WHOLE CAMPAIGN.** `spend_ledger.py` is *"INERT BY DEFAULT —
with `SKILLOPT_SPEND_LEDGER_DIR` unset this module does nothing at all"*. My body set every cap and
never set that variable: **0 claims, 0 spend records**, caps configured and unenforced throughout. The
prep stage's own `LEDGER_BEFORE.json` said `{"active": false}` and I quoted it in a status check without
following it up; the prep stage's ledger self-test passed, but it exercises the mechanism in its own
temp directory and says nothing about whether this campaign is wired to it. **Measured consequence:
none** — reconstructed from the call logs, nothing overran. Fixed, and every paid stage now calls
`require_live_ledger` before loading a key, asking the *module* whether it is active. Confirmed live:
`{"active": true, ...}` for all four post-fix stages.

**(g) Trace2Skill's consolidation degraded, and I reported one truncation cause as four.** All three
failures in job 66553058 were output truncation at exactly `completion_tokens=16384`. Fixed by smaller
batches, chunked verification, truncation detected *at the call*, and a cache key that covers
`max_tokens` — it did not, so raising the cap would have served the truncated reply back from cache.
Raising the cap also had to go through the **config**, because the backend sends
`min(per_call, config.max_tokens)` — the same silent-clamp pattern as the actor's output cap.

**(h) I inferred a fact from a test NAME without reading its assertion.** I reported that C-2 had
restored a historical report byte-identically, and drew two further conclusions from it. The assertion
that actually failed was a later one in the same test — *"the stage group count keeps its historical
value", `assert 17 == 14`*. A determinism probe (16 regenerations) showed generation is deterministic and
the digest had differed continuously since C-1. **All three claims withdrawn.**

**(i) I submitted a phase against a stale bundle** (job 66553061, `unknown PHASE=lean`). Both submit
scripts now check the deployed body first — and **the first version of that guard would have rejected
every valid stage of the campaign script**, because it matched only `$PHASE` while that body uses
`$STAGE`. Caught by self-testing the guard against both bodies before deploying it.

**(j) The walltime, not the retries, was the CPU expense.** Seven Trace2Skill submissions at 2:30 each
consumed **17.50 of the 50 authorized requested job-hours for 44 minutes of compute** — 64% of the
allowance, under the convention that charges a started job its full request. The stage is now requested
at 0:45, its measured runtime plus margin. The retries were necessary; the walltime was not.

**And one false alarm, corrected rather than dismissed.** A job reported "v2_bind CHANGED". It had not —
all four digests matched the previous job's record. I had moved the before-snapshot inside one phase's
branch while the after-check stayed outside. A preservation check that cries wolf is worse than none.

**One defect that is NOT mine and remains open.**
`test_four_concurrent_processes_cannot_spend_more_than_the_one_shared_cap` failed once with *"four
processes spent 24 against a cap of 23: [2, 18, 2, 2]"* — a real violation of `spent <= cap` in the
shared spend ledger. **Intermittent, not deterministic: ten repeats, ten passes.** Immaterial here
(~$1.20 against $15) but it is the invariant the authorization rests on.

## 7. Evaluation and reporting, as it ran

* S0 and each **distinct** endpoint on the **same frozen 30** held-out questions, **twice** each. All
  three endpoints are distinct by digest (§1c), so all three are evaluated; Original SkillOpt's
  endpoint is its validation-selected skill, which happens not to be S0 because the epoch-2 gated slow
  update was accepted.
* Each run writes a `PROVENANCE.json` carrying the skill digest, config digest, commit, frozen-set
  rule, split, question count and pass count — the **complete effective inputs**, so a later reuse
  decision rests on identity rather than on two documents looking equivalent.
* **Held-out scores and trajectories stay sealed.** I have checked episode counts and provenance for
  operational integrity and have not read the scores; nothing in the contract arm was chosen from them.

**Records:** `OFFICEQA_STATE.md` is the one current state note and opens with the live jobs, the
bundles that must not be rebuilt and the resume procedure. Frozen config and split manifests,
`docs/research/jobs.tsv`, `OFFICEQA_AUTHORIZATION.md`, `OFFICEQA_IMPLEMENTATION_AMENDMENTS.md`
(A-1…B-4, C-1, C-2, C-3), separate baseline and contract records, and resume instructions that survive
compaction. **A bundle is never rebuilt while its jobs are live** — each change went to a new bundle
directory, and the submit scripts now refuse a stage the deployed body does not contain.

---

## 8. Limitations, stated rather than left to inference

1. **Not a reproduction of the SkillOpt paper**, and the config header says so: 12 training questions, a
   **hosted FP8** actor rather than a local bfloat16 server, an **offline** setting with no web search.
   The algorithm is upstream's; the scale and environment are ours.
2. **The headline comparison may not be resolvable at this size.** Original SkillOpt's accepted gain is
   **one validation question**, and S0's own spread across four independent passes is ±1 question. A
   30-question held-out set at two passes gives 60 attempts per endpoint; differences of one or two
   questions should not be read as method differences.
3. **6 of 30 held-out questions are UNTESTED for document availability** — selected mechanically and
   reported as unverified rather than filtered, since filtering on retrievability would be a selection
   the benchmark did not make.
4. **One shared document between training and held-out** (UID0014 trains on the July 1942 bulletin p. 76,
   UID0013 is held out on it). Small, real, recorded.
5. **Trace2Skill needed three attempts** and its first two produced degraded consolidations; both are
   preserved. The faithful run required raising the optimizer's output cap to 32,768, which is a
   **departure from the released configuration** and is declared as one.
6. **Trace2Skill's endpoint is 24,121 bytes against S0's 887** — a 27× expansion read at the start of
   every task. Upstream's released skills are also large, so this is not anomalous, but it is a
   confound for any comparison that cares about prompt length.
7. **Trace2Skill's analyst cannot validate its diagnoses** by implementing a fix, a direct consequence
   of keeping answer keys out of analysts. Its causal claims rest on the trajectory alone.
8. **The spend ledger was inert for most of the campaign**, so this phase's spend record is the call-log
   reconstruction and the caps are now enforced against an undercount of ~1,800 attempts (§3, §6f).
9. **The occurrence channel is thinner on the 12 campaign training questions** than on the 24-question
   superset (9/27/22 unique positions per pass against 201), because four high-yield records are in the
   other 12. No rate is claimed from the small per-pass samples.
10. **An intermittent violation of `spent <= cap`** in the shared spend ledger remains open, in code I
    did not write (§6).
11. **The historical 44-hour CPU convention is still unsettled** and untouched by this phase.

### Next actions
1. Collect the three held-out results and report completion and provenance; **keep the scores sealed**
   until you say otherwise.
2. The contract arm's real gap is the **learner** (§5e items 1–3): a genuinely learned rule over
   column/series identity with its evidence, a supported diagnosis on a later record, and the
   recovered-work control on UID0026. Deliberately not started tonight.
3. Investigate the intermittent ledger overspend, or decide it is acceptable and record that.
4. Decide whether the one-question validation margin makes a larger validation set worth the budget
   before any further comparison is run on this setup.
