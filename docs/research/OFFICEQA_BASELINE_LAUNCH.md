# OfficeQA two-baseline campaign — launched, plus contract-arm progress
**2026-11-21 03:27 EST · branch `officeqa-contracts` · worktree `SkillOpt-officeqa` · commit `88acd75` · not pushed**
Rules **OQA-SEL-4** (splits) · **OQA-BASE-1** (campaign) · **OQA-T2S-1** (Trace2Skill) · **OQA-OCC-1** / amendment **C-1** (contract arm)

Abbreviations on first use: **S0** = the unchanged starting skill document. **Actor** = the frozen
target agent (Qwen3.5-4B, hosted). **Optimizer / teacher** = DeepSeek V4.1 Flash. **Episode** = one
question attempt. **Attempt** = one HTTP request to the actor (an episode makes many).

---

## 1. What ran, and what came out

**Both baselines completed end to end.** Three held-out evaluations are running or done.

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
| S0 | `9d377d1c2906` | 887 | 66553250 — **COMPLETE, 30/30 × 2** |
| Original SkillOpt | `e35673d87cb7` | 4,518 | 66553251 |
| Trace2Skill | `03d6564f1d10` | 24,121 | 66553363 |

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

## 3. Reserved and remaining resources

Fresh ledger at zero: `runs/20261121-officeqa-two-baselines/ledger`. The three-arm campaign's ledger is
deliberately **not** reused, so this allowance and the historical spending are never summed into one
cap. This does not retrospectively approve the old overrun or settle the accounting convention.

| Resource | Authorized | Reserved by submitted jobs | Remaining |
|---|---:|---:|---:|
| Requested CPU job-hours | 50 | **11.33** | 38.67 |
| GPUs | 0 | 0 | 0 |
| Actor attempts | 20,000 | ~3,360 projected | ~16,640 |
| Actor USD | $15.00 | ~$2.12 projected | ~$12.88 |
| DeepSeek calls | 400 | ~117 projected | ~283 |
| DeepSeek USD | $20.00 | low units projected | — |

CPU reserved = prep 0:20 + S0 1:30 + epochs (1:00 + 3×1:45) + t2s 2:30 + contract 0:45 = **11:20**. The
three held-out endpoints add 3 × 1:30 = 4:30 → **15:50 of 50**, leaving **34:10 of recovery headroom**.

**Whole-campaign projection, computed and checked against the ceilings by the preflight:** 396
episodes, **5,544 actor attempts**, **$3.49**, **117 optimizer calls** — all inside the caps.
Trace2Skill contributes **0 rollouts**: it reuses the S0 trajectories.

> **A correction to my own first projection.** It said 56 Trace2Skill optimizer calls and omitted the
> method's entire MAP phase. The real count is 105 (48 analysis + 48 map + 7 merges + apply/verify);
> campaign-wide 68 → **117**, still well inside 400. The t2s walltime rose 1:00 → 2:30 for the same
> reason: 105 calls at two requests in flight is ~53 sequential slots.

Concurrency as instructed: ≤4 CPU jobs (at most 2 ran at once), 4 actor requests, 2 DeepSeek requests.
**Resume:** `eval_only.py` and `run_batch` resume by id; every Trace2Skill call is cached by prompt
digest, so a TIMEOUT costs only the calls that had not happened. The body refuses to run under
`SLURM_RESTART_COUNT != 0`, so a requeue can never re-spend — recovery is always deliberate.

**Historical, separate and unchanged:** EmpirioLabs 12,042 of 30,024 attempts and $8.0147 of $17.00;
DeepSeek 105 of 266 and $3.4689 of $10.00; CPU **disputed** — 35.649 of 44 core-hours or 66.483 of 44
requested walltime. **Which convention governs that 44-hour ceiling is still your decision.**

---

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

## 6. Five defects of mine, each caught by machinery, none of which spent anything

**(a) The preflight asserted the backend's defaults and called them the campaign's settings.** Job
66552338 (free) failed with 36 of 38 checks passing and `outbound/model` = `gpt-4o-mini`,
`outbound/base_url` = `api.openai.com`. The config was correct; my preflight read
`_initial_config("target")`, which reads **only** role environment variables, in a process that had
never applied the config. The config reaches the backend only via `configure_openai_compatible`
(`trainer.py:843`), which the preflight now calls before asserting, reading the **live** config. Added
`outbound/not_the_backend_fallback` in the opposite direction — an unset base URL silently becoming
OpenAI's should be caught by a preflight, not by a bill. The five paid dependents were correctly held by
`afterok` and cancelled.

**(b) Trace2Skill's boundary guard conflated a correct answer with a leaked key.** Job 66552508 aborted
after 2 seconds with "1 ground-truth string(s) appear in an outbound prompt", before any paid call. The
hits were real and were not leaks: UID0017's `[10102000000, 4.73]` and UID0033's `March 3, 1977`, both
in an **actor message**, both the question's own key, both on episodes the actor **answered correctly**.
On a correct episode the actor's own answer **is** the ground-truth string — so a string scan refuses
exactly the episodes the success analyst needs, and the same conflation would fire on any value the
actor legitimately *read*. The guard is now about **provenance**: a key in a prompt is EXPLAINED if the
permitted corpus also contains it, and a VIOLATION only if it does not, since then it can only have come
from the evaluator. An empty corpus explains nothing, so it still fails closed. Verified on the real 48:
**0 violations, 7 explained hits.**

**(c) Trace2Skill never configured the optimizer route.** Job 66552741 made **zero** paid calls; all 48
analysis calls raised `ValueError: Azure OpenAI endpoint is not configured for optimizer`.
`chat_optimizer` is a **router whose default is Azure**, and a config file does not reach it without
`set_optimizer_backend` + `configure_openai_compatible`. This was the **same lesson as (a), hours
apart** — and the campaign body's own header already documents the hazard from job 66271190. I fixed the
preflight for it and failed to carry it across. `configure_models` now runs before anything is built,
reads the live config back, records it, and **refuses a route that is not DeepSeek**.

**(d) The dry run reported healthy both times** because it short-circuited before configuring anything —
a self-check that skipped the thing most likely to be wrong. It now configures too, and the stage
refuses to proceed if the guard fires.

**(e) A missing `collections` import**, caught by that dry run (job 66552763). The real run reaches the
line only after its analysis calls, so it spends then crashes — but every reply is cached by prompt
digest on durable storage, so the resubmission reuses them and nothing is wasted. The local dry run that
would have caught it can no longer run, because `configure_models` imports skillopt (Python 3.10+). Two
guards replace it: pyflakes over these modules, and a test that walks the AST for `<stdlib>.<attr>` uses
and asserts each module is imported.

**And one false alarm of mine, corrected rather than dismissed.** A job reported "v2_bind CHANGED". It
had not — all four digests are byte-identical to the previous job's record. I had moved the
before-snapshot inside one phase's branch while the after-check stayed outside, so the replay phase
compared an empty before against a populated after. The snapshot now runs in every phase. A preservation
check that cries wolf is worse than none.

**A historical report moved, and that is recorded rather than re-pinned silently.** A-1 promised
metadata-only changes and a test guarded it by regenerating two real campaign reports byte-for-byte. C-1
supersedes that for the source-binding section: **UID0014's report changes, UID0019's does not.** The
test now pins that blast radius *per case* and still asserts the model digest, trace digest and action
forecasts are unchanged — a change in any of those would mean C-1 reached further than its amendment
claims.

---

## 7. Evaluation and reporting, as it will run

- S0 and each **distinct** endpoint on the **same frozen 30** held-out questions, **twice** each.
  Endpoints: S0, Original SkillOpt's validation-selected skill (**including S0 itself if no candidate
  wins the gate** — a real outcome, not a missing result), and Trace2Skill's consolidated skill.
- An evaluation is reused only when the **complete effective endpoint inputs** match — skill digest,
  config digest, split, turn budget, passes — never because two documents look semantically equivalent.
  Each held-out run writes a `PROVENANCE.json`.
- **Held-out scores and trajectories stay sealed** while the contract arm is developed, and will not be
  used to choose contract features, prompts or repairs.

**Records:** `OFFICEQA_STATE.md` (one current state note, opening with the live jobs, the bundles that
must not be rebuilt, and the resume procedure), the frozen config and split manifests,
`docs/research/jobs.tsv` and `OFFICEQA_AUTHORIZATION.md`, separate baseline and contract records, and
resume instructions that survive compaction. **Bundles must never be rebuilt while their jobs are live**
— bash reads a script body lazily; each change went to a new bundle directory.

---

## 8. Limitations, stated rather than left to inference

1. **Not a reproduction of the SkillOpt paper**, and the config header says so: 12 training questions,
   a **hosted FP8** actor rather than a local bfloat16 server, and an **offline** setting (parsed text
   through local document tools, no web search). The algorithm is upstream's; the scale and environment
   are ours.
2. **6 of 30 held-out questions are UNTESTED for document availability** — selected mechanically and
   reported as unverified rather than filtered, since filtering on retrievability would be a selection
   the benchmark did not make.
3. **One shared document between training and held-out.** Small, real, recorded.
4. **The three-arm campaign's numbers are not a usable baseline** — broken actor client.
5. **12 of the 48 S0 trajectories could have been reused and were not**, deliberately: one job, one
   settings fingerprint, for about $0.01.
6. **The occurrence channel is thinner on the 12 campaign training questions than on the 24-question
   superset** (9/27/22 unique positions per fresh pass against 201), because four high-yield records —
   UID0044, UID0228, UID0150, UID0165 — are in the *other* 12. The per-pass supported rates look higher
   (0.78/1.00/0.82) but rest on 9–27 observations each. **No rate is claimed from them.**
7. **Trace2Skill's analyst cannot validate its diagnoses** by implementing a fix — a direct consequence
   of keeping answer keys out of analysts. Its causal claims rest on the trajectory alone.
8. **The historical 44-hour CPU convention is still unsettled** and is untouched by this phase.

### Next actions
1. Monitor epochs 2–4 and Trace2Skill. On a TIMEOUT, resubmit the same stage with the same output
   directory; completed episodes and cached replies are reused.
2. Repair C-1 defects **A** and **B**, each with its own prediction committed before its own replay.
3. Compile Lean against the C-1 facts — `unknownBindingNeverViolates` is already the right theorem —
   and state precisely that it verifies the resolution algebra, not extraction correctness.
4. Then the learner (a genuinely learned rule), a supported diagnosis on a later record, and the
   recovered control on UID0026.
5. On completion: export the validation-selected endpoint, then the three held-out evaluations.
