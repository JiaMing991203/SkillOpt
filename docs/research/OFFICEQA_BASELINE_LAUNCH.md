# OfficeQA two-baseline campaign — launched, plus contract-arm progress
**2026-11-21 03:27 EST · branch `officeqa-contracts` · worktree `SkillOpt-officeqa` · commit `88acd75` · not pushed**
Rules **OQA-SEL-4** (splits) · **OQA-BASE-1** (campaign) · **OQA-T2S-1** (Trace2Skill) · **OQA-OCC-1** / amendment **C-1** (contract arm)

Abbreviations on first use: **S0** = the unchanged starting skill document. **Actor** = the frozen
target agent (Qwen3.5-4B, hosted). **Optimizer / teacher** = DeepSeek V4.1 Flash. **Episode** = one
question attempt. **Attempt** = one HTTP request to the actor (an episode makes many).

---

## 1. What has actually been submitted

| Job | Stage | Walltime | State at 03:27 |
|---|---|---:|---|
| 66552420 | prep (free) | 0:20 | **COMPLETED** 1:18 |
| 66552421 | S0 reference: 4×12 training + 12 validation | 1:30 | **COMPLETED** 23:16 — all 12/12 |
| 66552422 | **Original SkillOpt** epoch 1 | 1:00 | **COMPLETED** 3:40 |
| 66552423 | Original SkillOpt epoch 2 (first gated slow update) | 1:45 | **RUNNING** |
| 66552424 / 66552425 | epochs 3 / 4 | 1:45 each | PENDING on the chain |
| **66552763** | **Trace2Skill** | 2:30 | **RUNNING**, paid calls flowing |
| 66552633 | contract: v7 + occurrence measurement (free) | 0:20 | **COMPLETED** 1:04 |
| 66552721 | contract: v8 + C-1 replay (free) | 0:25 | work COMPLETED; exited 1 (see §6) |

Every baseline link is `afterok`. Both methods hang off the S0 stage and run **in parallel**, so one
method's adapter problem cannot hold the other. There was no in-flight OfficeQA job to continue — the
previous one ended 2026-11-18 — so this is a fresh submission, not a duplicate.

**Why a new campaign exists at all.** The three-arm campaign of 2026-11-17 ran through the **broken**
actor client, under which S0 scored 4/24 against 9.75/24 corrected. Its numbers measure a defect and
sampling together and cannot serve as a baseline.

---

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

### 5c. Amendment C-1: a value match may not select a source cell
`oqa_lc_chain._best_cell` was handed every cell whose value matched an operand and **silently returned
one**, ranked by exact match, then FLAT column, then earliest step. The ledger would then discharge or
violate a requirement against a cell the actor may never have read. C-1 replaces that with a refusal:
one candidate → selected; several + a SUPPORTED occurrence → selected and the position recorded;
several + UNKNOWN → **no selection**, rivals recorded, obligation UNKNOWN.

**Replayed (job 66552721), against a prediction written in the amendment first.** 659 operands:

| | old tie-break | under C-1 |
|---|---:|---:|
| operands with a selected source cell | **566** | **102** (67 sole, 35 corroborated) |
| ledger DISCHARGED | 254 | 81 |
| ledger **VIOLATED** | **186** | **30** |
| ledger OUTSTANDING | 19 | 30 |

**The headline.** The old tie-break was selecting a source cell on numeric equality alone in **464 of
566 selections (82%)**, and those supported **156 of its 186 VIOLATED obligations** — accusations
resting on cells the actor may never have read. Fewer findings, and the ones left rest on something.

### 5d. Two defects in C-1, found by its own replay — open, and deliberately not rushed
- **A.** The amendment predicted withdrawn bindings would make obligations **UNKNOWN**. They do not: the
  ledger shrinks **1,839 → 911**, because obligations *downstream* of a cell aren't generated at all
  when no cell was selected. On UID0044, 406 records (132 DISCHARGED) become 142 (0 DISCHARGED, 141
  UNKNOWN) — **264 requirements vanish**. That is an unknown effect silently meaning "nothing changed",
  which the chain module's own docstring exists to prevent. The prediction's *direction* held; this
  sub-claim **failed**, and the failure is a defect in my implementation. Repair: `obligations()` must
  emit them as UNKNOWN naming the refusal.
- **B.** `_match_cells` treats one cell observed through different routes as distinct candidates. Of 499
  operands with several candidates, **199 (39.9%) collapse to a single cell** once keyed by (row label,
  column, value, scale factor); mean candidates **8.61 → 3.54**. The UID0044 witness is typical: three
  "rivals" for `-3044.0` are the supplied page and two greps **of the same document**. So some of the
  464 withdrawals refuse a tie that does not exist. Repair: deduplicate by cell identity before
  declaring a tie. (Measured with a stand-in model locally, so the share is indicative.)

Both repairs are specified in the amendment and left for their own committed prediction and replay. I
already learnt once tonight that a semantic change made in a hurry produces a half-right result.

### 5e. Still missing from the §4 demonstration — the honest gap list
1. **A genuinely learned rule with its evidence.** Not done. The current hypothesis
   (`96c3d1bd9aad6f4d`) is about *units*, and both witnessed error classes are *column selection*,
   which it cannot express. A learner call needs the optimizer; by your priority ordering it waits
   until Trace2Skill has finished.
2. **A supported diagnosis on a later record.** Not done — needs (1).
3. **A successful/recovered control.** Not done. UID0026 is the natural witness: one message holds both
   a superseded wrong calculation and the committed right one.
4. **Lean compiled against the C-1 facts.** Not done, and it is now *warranted*, unlike for B-4 where
   no encoded fact changed. `OqaContracts.lean` already contains **`unknownBindingNeverViolates`** —
   "withdrawing a binding may withdraw a discharge, it may never manufacture an accusation" — which is
   **exactly** C-1's soundness condition. Lean is at `/nfs/…/lean/elan/bin/lean`. Note precisely what
   that would establish: the **resolution algebra**, not that the occurrence extraction is correct.
5. **A counterexample-repair path with regressions.** Partially: C-1 *is* a repair of a counterexample
   class with 121 tests as regression, but defects A and B are the live counterexamples and are unrepaired.

**On the source-binding result:** it demonstrated a failing *extraction method*, not that inference from
permitted observations is impossible — §5b is the small supported connection tested before proposing any
parser overhaul, and it found a channel that works at a modest, measured rate.

---

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
