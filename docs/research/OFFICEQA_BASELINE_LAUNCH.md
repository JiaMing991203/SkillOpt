# OfficeQA: two-baseline campaign launched — first update
**2026-11-21, 03:00 EST. Branch `officeqa-contracts`, worktree `SkillOpt-officeqa`, commit `3765139`. Not pushed.**
Rules **OQA-SEL-4** (splits), **OQA-BASE-1** (campaign), **OQA-T2S-1** (Trace2Skill).

---

## 1. What has actually been submitted

Both baselines are submitted. Nothing is waiting on contract readiness, document rendering or the old
budget dispute.

| Job | Stage | Walltime | State at 03:00 | Depends on |
|---|---|---:|---|---|
| **66552420** | prep (free) | 0:20 | **COMPLETED** in 1:18 | — |
| **66552421** | s0 reference, 48+12 episodes | 1:30 | **RUNNING** | 66552420 |
| **66552422** | Original SkillOpt epoch 1 | 1:00 | PENDING | 66552421 |
| **66552423** | Original SkillOpt epoch 2 | 1:45 | PENDING | 66552422 |
| **66552424** | Original SkillOpt epoch 3 | 1:45 | PENDING | 66552423 |
| **66552425** | Original SkillOpt epoch 4 | 1:45 | PENDING | 66552424 |
| **66552508** | **Trace2Skill** | 2:30 | PENDING | 66552421 |

Every link is `afterok`, so a failed stage stops its chain instead of paying for a stage whose input
does not exist. Trace2Skill and Original SkillOpt both hang off s0 and run **in parallel** once it
finishes, which is why one method's adapter problem cannot hold the other.

**Verified, not assumed:** s0's gated checkpoint passed at 02:54 — the body runs the first three
questions of pass 1, checks them, and refuses the remaining 45 if they fail. The actor path is live.

**One failure, already diagnosed and corrected.** The first chain (66552338–343) was submitted at 02:44
and its free prep stage **failed**, correctly holding the five paid jobs behind it, which were then
cancelled rather than left to rot. Details in §5. No money was spent.

### Not a duplicate
There was no in-flight OfficeQA job to continue: `squeue` showed only the six held `e3m-*` Experiment 3
GPU jobs, and `sacct` showed the last OfficeQA job ending 2026-11-18. This is a fresh submission under a
new setup, and it is **not** a resubmission of the three-arm campaign — that campaign ran through the
**broken** actor client, under which S0 scored 4/24 against 9.75/24 corrected, so its numbers cannot
serve as a baseline.

---

## 2. The frozen setup

### 2a. Splits — rule OQA-SEL-4, digest `b0dbbd4be97fc245`

`docs/research/OFFICEQA_FROZEN_SETS_V4.json`. **Disjoint, 0 problems.**

| Set | n | Released split | Strata (category × document count) | Availability |
|---|---:|---|---|---|
| Training | 12 | `train` (50 available) | 3/3/3/3 | 12 AVAILABLE |
| Validation | 12 | `val` (24 available) | 3/3/3/3 | 12 AVAILABLE |
| Held-out | 30 | `test` (172 available) | 8/8/7/7 | 24 AVAILABLE, **6 UNTESTED** |

- **Training** and **validation** are OQA-SEL-2's 12 and 12 **carried forward unchanged**, no new draw.
- **Held-out** retains OQA-SEL-2's 24 first, then draws 6 more from OQA-SEL-3's 60 by
  `sha256("OQA-SEL-4" + uid)` ascending — a fixed permutation depending on the rule name and the item
  id and nothing else.
- Every set is a **subset** of the freeze it claims to subset, so the new held-out measurement overlaps
  the two historical ones on 24 of its 30 questions and the series stays comparable.
- Strata come from **released metadata** (`category` is "easy"/"hard"; single/multi is the source-document
  count). **No OfficeQA task category is invented.**
- UID0046's exclusion is carried forward from OQA-SEL-2 and recorded; it is not among the 12 training
  items, so it changes nothing here.

**Prior research exposure, recorded per item.** All 54 selected questions have been used before, and
**no item changed the role an earlier freeze gave it** — a test asserts this, because a question that
was once training and is now held-out would be contaminated by construction. 24 of the 30 held-out
questions were evaluated during the three-arm campaign (under the broken client); the other 6 were
frozen into the held-out role by OQA-SEL-3 but never run.

**Source-document overlap, measured and not empty.** Training rests on 21 documents, validation 28,
held-out 46.

| Pair | Shared documents | Items involved |
|---|---:|---|
| train ↔ held-out | **1** | UID0014 (train) and UID0013 (held-out), both on the July 1942 bulletin p. 76 |
| train ↔ val | 1 | UID0028 (train), UID0027 (val) |
| val ↔ held-out | 2 | UID0001, UID0109 (val); UID0004, UID0005, UID0089 (held-out) |

This is a real leakage channel for a document-specific edit, so it is counted and named rather than
assumed absent. It is **small**: one of 30 held-out questions shares a document with one training
question.

### 2b. Actor, tools and response handling — **effective**, verified in outbound requests

| Setting | Value | How it is known |
|---|---|---|
| Actor | `qwen3-5-4b` (Qwen3.5-4B), EmpirioLabs hosted, FP8 | preflight read the **live** backend config |
| Base URL | `https://api.empiriolabs.ai/v1` | same |
| Temperature | 0.7 | same; and 285/285 transmitted requests of the 2026-11-18 collection |
| **Output cap** | **8,000 tokens** | `model.target_openai_compatible_max_tokens`; 285/285 requests |
| Thinking | `extra_body {"enable_thinking": false}` | transmitted **and effective** — 0 alternate-reasoning characters in 285 responses |
| Recovery | narrow `committed_answer` | pinned in the job, not defaulted |
| Tool interface | `glob`, `read`, `grep`, offline, local files | frozen; schema digest `7e4fe0e714eb` |
| Turn budget | 24 tool turns, ≤4 queries per turn | config, resolved |
| Workers | 4 | config |
| Starting skill | **S0 unchanged**, 887 bytes, `9d377d1c2906` | `skillopt/envs/officeqa/skills/initial.md` |
| Corpus | `treasury_bulletins_parsed`, frozen | document rendering **frozen** for this campaign |
| Scorer | existing | unchanged |

> **A correction to the record, worth stating plainly.** Two config fields look like the actor's output
> cap and **only one is**. `env.max_completion_tokens: 16384` reaches **no prompt and no API call** — its
> only consumer is `rollout_settings_fingerprint`, the digest the provenance guard compares. The cap
> that is transmitted is `model.target_openai_compatible_max_tokens`, which is **8,000**. The two have
> differed by 2× all along. I left the inert 16384 in place **on purpose**: changing it would invalidate
> the fingerprint of every saved S0 trajectory and force Trace2Skill to re-pay for 12 rollouts. The
> misleading number is documented rather than silently corrected at that price.

**Document/tool rendering is frozen**, as instructed. The split-header column-span problem that the
source-binding investigation identified is **not** a launch prerequisite and has not been touched.

### 2c. Original SkillOpt — "Original SkillOpt framework baseline"

`configs/research/officeqa_base2.yaml`. Arm A runs `scripts/train.py` with
`OFFICEQA_STAGE_REPORT_DIR` **unset**, so the analyst prompt is byte-identical to upstream's. Resolved
and asserted by the preflight:

- **Four epochs** over the same 12 training questions; `batch_size 40 > 12` so `ceil(12/40) = 1` —
  **one ordinary update per epoch**.
- **Gated slow updates from epoch 2**, `slow_update_samples: 6` — six training-subset questions per
  scheduled slow update, by specification, against the three-arm campaign's 10 and upstream's 20.
  `slow_update_gate_with_selection: true`, so the slow-update candidate faces the **same** 12-question
  validation gate as an ordinary step rather than being force-injected.
- **Meta-skill on** (optimizer-side memory; it does not edit the skill).
- **Upstream's strict-improvement acceptance rule preserved.** Ties are not accepted and no update is
  forced to manufacture a changed skill.
- The **separate** 12-question validation set serves every selection gate. `eval_test: false`, so the
  trainer never touches the held-out set.
- Both previous-skill and current-skill rollouts required by the slow-update comparison are budgeted.
- Native handling of prior edits, rejected edits and memory is upstream's, unmodified.

**Adaptation, declared:** epoch 1's training rollout and gate baseline are the **injected S0 traces**
from stage s0 (`run_batch` resumes by id, so the trainer generates nothing new). This is not a
shortcut — it is what makes Original SkillOpt and Trace2Skill reason about the *same* S0 evidence
rather than two independent draws at temperature 0.7.

### 2d. Trace2Skill — pinned, then adapted

**Pinned:** `github.com/Qwen-Applications/Trace2Skill` at **`3d0b52a140f002a512930252b613c49048f7d5ac`**
(2026-05-02), Apache 2.0, arXiv:2603.25158. Inspected before adapting.

The structure reproduced is upstream's `ParallelSkillEvolver`, **not** a generic one-shot summary:

```
ANALYSIS  one call per trajectory   error analyst if it failed, success analyst if it passed
MAP       one call per record       -> a semantic patch, in parallel
REDUCE    HIERARCHICAL              48 patches -> 6 batch merges (size 8) -> 1 final merge
APPLY     the merged patch becomes the new skill document
VERIFY    every item checked: APPLIED / PARTIAL / MISSING, plus UNREQUESTED content
```

`chunk_list` reproduces upstream's balancing exactly, including the round-robin distribution of a
remainder smaller than half a batch; a property test checks no item is lost at any n from 1 to 59.

**Input:** 48 fixed-S0 trajectories — four independent passes over the 12 training questions, collected
by stage s0 under one frozen configuration.

**Four adaptations, each with its reason** (`research/officeqa/oqa_t2s_prompts.py`):

1. **Domain** — document QA over parsed Treasury Bulletin text, not spreadsheets.
2. **Analysis mode** — upstream's **single-call** analyst, not its agentic one. The agentic analyst is
   handed `gold.xlsx`, and our rule is that answer keys stay in scoring. Both are released upstream
   modes, so this is a choice between two, not an invention. **Consequence, stated rather than hidden:**
   our analyst cannot validate a diagnosis by implementing a fix and re-evaluating, so upstream's
   causality requirement is weakened to what the log supports, and the prompt asks for that instead of
   demanding a guarantee it cannot check.
3. **Skill interface** — the single markdown document SkillOpt optimizes, not a skill *directory*. That
   document is the artifact under study; a directory would change what the two baselines are compared
   on. Upstream's own markdown path already targets `SKILL.md`, and `references/*.md` creation is
   disallowed.
4. **Model** — DeepSeek V4.1 Flash, matching our SkillOpt teacher, through
   `skillopt.model.chat_optimizer` so the calls use the same route-pinned, ledger-enforced path.

**Nothing is borrowed from SkillOpt.** A test greps the module for `valid_seen`, `selection_eval`,
`slow_update`, `use_gate` and `num_epochs` and fails if any appears. The consolidated skill **is** the
endpoint; validation may measure it but is not allowed to become an undeclared tuning loop.

### 2e. The information boundary, enforced mechanically

| Class | Contents |
|---|---|
| **Permitted** | `conversation.json` — the actor's messages, the executed tool calls and their observations (which for OfficeQA *are* the document text the actor read) — and the saved target prompts |
| **Triage only** | `results.jsonl` `hard`, the binary verdict, used *only* to choose which analyst runs — upstream triages the same way from its official evaluation |
| **Forbidden** | `ground_truth`, `soft`, and the provider's alternate reasoning field |

Every outbound Trace2Skill prompt is scanned for **every ground-truth string in the campaign** and the
run aborts on a hit. Keys shorter than 6 characters are excluded because they would match by
coincidence. `render_permitted` emits only the three known entry shapes, so a field added to the trace
later cannot leak silently. Answer keys stay in scoring; no analyst or learner receives one.

---

## 3. Reserved and remaining resources

This phase has a **fresh ledger at zero**
(`runs/20261121-officeqa-two-baselines/ledger`). The three-arm campaign's ledger is deliberately
**not** reused, so the new allowance and the historical spending are never summed into one cap. This
does not retrospectively approve the old overrun or settle the accounting convention.

| Resource | Authorized | Reserved by submitted jobs | Remaining |
|---|---:|---:|---:|
| Requested CPU job-hours | 50 | **10.58** | 39.42 |
| GPUs | 0 | 0 | 0 |
| Actor attempts | 20,000 | ~3,360 projected | ~16,640 |
| Actor USD | $15.00 | ~$2.12 projected | ~$12.88 |
| DeepSeek calls | 400 | ~117 projected | ~283 |
| DeepSeek USD | $20.00 | low units projected | — |

CPU reserved = prep 0:20 + s0 1:30 + epochs (1:00 + 3×1:45) + t2s 2:30 = **10:35**. The three held-out
endpoints will add 3 × 1:30 = 4:30, bringing the campaign to **15:05 of 50** and leaving **34:55 of
recovery headroom**.

**The whole-campaign projection the preflight computed and checked against the ceilings:**
396 episodes, **5,544 actor attempts**, **$3.49**, **117 optimizer calls** — all inside the caps.
Episode breakdown: s0 48 + 12, Original SkillOpt 12 + 3×48, Trace2Skill **0 rollouts** (it reuses s0's
trajectories), held-out 3 endpoints × 2 passes × 30 = 180.

> **A correction to my own first projection.** It said 56 Trace2Skill optimizer calls and omitted the
> method's entire MAP phase. The real count is 105 — one analysis call per trajectory *and* one map call
> per analysis record, plus seven hierarchical merges and apply/verify. Campaign-wide 68 → **117**,
> still well inside 400. The t2s walltime was raised 1:00 → 2:30 for the same reason: 105 calls at two
> requests in flight is ~53 sequential slots.

**Concurrency**, as instructed: at most 4 CPU jobs (the chains allow at most 2 at once), 4 actor
requests, 2 DeepSeek requests. **Resume:** `eval_only.py` and `run_batch` resume by id, and every
Trace2Skill call is cached by prompt digest, so a TIMEOUT is recovered by resubmitting the same stage
and costs only the calls that had not yet happened. The body refuses to run under
`SLURM_RESTART_COUNT != 0`, so an automatic requeue can never re-spend; recovery is always deliberate.

**Historical, separate and unchanged:** EmpirioLabs 12,042 of 30,024 attempts and $8.0147 of $17.00;
DeepSeek 105 of 266 and $3.4689 of $10.00; and the disputed CPU figure — 35.649 of 44 core-hours, or
66.483 of 44 requested walltime. **Which convention governs that 44-hour ceiling is still your
decision and is untouched by this phase.**

---

## 4. Evidence: what ran, and what it found

**Prep (free, 1:18):**
- **2,428 tests passed**, 11 skipped, 368 subtests — run *before* any paid stage, because a campaign
  deployed from a tree whose tests fail is a campaign nobody can interpret.
- The 12/12/30 split materialized and the preflight compared it to the manifest **id by id**, not by
  count. Exact match in all three roles, pairwise disjoint.
- The ledger's cap mechanism was proven to hold **on this filesystem** against concurrency, restarts and
  an unwritable ledger — it rests on `O_CREAT|O_EXCL` atomicity, which is exactly what can differ
  between local disk and NFS.
- **Preflight: READY — 39 pass, 0 warn, 0 fail.**

**The saved 2026-11-18 collection is configuration-identical** and therefore reusable in principle:
recorded skill digest `9d377d1c2906` equals the current S0 digest, recovery policy `committed_answer`,
`enable_thinking: false`, 24 tool turns. Verdict `REUSABLE_SUBJECT_TO_PER_ROW_GUARD`. I am **not**
relying on it: stage s0 collects all four passes fresh in one job, so all 48 trajectories carry one
settings fingerprint. The compatibility check is reported because it was asked for, and because it is
independent evidence that the frozen condition is stable across runs.

**Trace2Skill, validated without spending anything:** a local dry run over the saved collection
completes the whole orchestration, triages error/success correctly, and reads the S0 digest as
`9d377d1c2906`.

**50 new tests** for this work. Two of them found real defects, described next.

---

## 5. Two defects found, both mine, both in safety machinery

**(a) The preflight was asserting the backend's defaults and calling them the campaign's settings.**
Job 66552338 (prep, free) failed with 36 of 38 checks passing and these two failing:

```
outbound/model      effective gpt-4o-mini                expected qwen3-5-4b
outbound/base_url   effective https://api.openai.com/v1   expected https://api.empiriolabs.ai/v1
```

The config was correct throughout. My preflight read `_initial_config("target")`, which reads **only**
role environment variables, in a process that had never applied the config — so the backend returned
its own fallbacks. The config does not reach the backend by itself: `skillopt/engine/trainer.py:843`
calls `configure_openai_compatible(...)`, which writes the resolved values into both the module config
and the environment, so the **config wins and the environment is its record**. The preflight now
performs the same application with the same fields before asserting anything, and reads the **live**
config. That is what makes the assertion a claim about the paid stages rather than about an
unconfigured process.

I added a check in the opposite direction — `outbound/not_the_backend_fallback` — because an unset base
URL silently becoming OpenAI's is a live hazard, not a hypothetical, and it should be caught by a
preflight rather than by a bill.

**The failure cost nothing and the machinery behaved correctly:** prep loads no actor or optimizer key,
and the five dependent jobs were held by `afterok` rather than running against a broken setup.

**(b) Trace2Skill's final consolidation merge could destroy the whole run's work.** The batch merges
are shielded by the parallel runner, but the **final** coalescing merge was not, so a provider error
there propagated and would have discarded every proposal the run had paid for. It now degrades to a
local coalesce that preserves every item and records `conflicts_unresolved: true` plus the provider
error — because losing the model's conflict resolution is not the same as losing the work. A test
smuggles an exception into that call to pin it.

---

## 6. What I am doing while the jobs run — the contract arm

Developed in a **separate snapshot** (its own bundle directory and output tree) so contract changes
cannot reach a running baseline job. Progress and gaps will be reported **separately**; this prompt does
not launch a contract performance arm, and I am not treating it as one.

**The prerequisite named in the instruction, and the first task:** correct the **mixed-version bundle**.
`collect_20261118/v2_bind/CC_BUNDLE_v6.json` is run 1's bundle (`36c37a397eddaa65`) sitting beside run
2's analysis artifacts; the honest label for run 2's state is **v7**. It will be re-frozen into a new
versioned directory with `v2_bind` **preserved**, not overwritten.

**The target remains genuinely learned, evolving action contracts connected to explicit stage
contracts.** A fixed Python report or an all-UNKNOWN checker does not satisfy it, and the current
prototype is all-UNKNOWN on all 24 records, so there is real work here rather than a relabelling.

**The narrow connection to build first:**
`question requirement → relevant lookup stage → source column/value → calculation or intermediate
result → final answer`.

**What the source-binding result does and does not say.** It demonstrated that *one extraction method*
fails — the pipe rendering has already destroyed the column spans, and the leaf recovered from a split
header was correct on one record and wrong on three. It did **not** show that every inference from
permitted observations is impossible. So the next step is a **small supported connection tested on a
few witnesses**, not a parser or corpus overhaul. If a check cannot work without additional source
structure, I will document the exact missing information and propose a separate interface variant
rather than quietly widening what the contract system may see.

Development evidence is the **training/development** side only: the 48 new S0 trajectories and the
saved 24-question collection, successes and failures alike. Campaign validation and held-out records
stay outside contract development.

---

## 7. Evaluation and reporting, as it will run

- S0 and each **distinct** baseline endpoint on the **same frozen 30** held-out questions, **twice**
  each. Endpoints: S0, Original SkillOpt's validation-selected skill (**including S0 itself if no
  candidate wins the gate** — that is a real outcome, not a missing result), and Trace2Skill's
  consolidated skill.
- An evaluation is reused only when the **complete effective endpoint inputs** match — skill digest,
  config digest, split, turn budget and passes — never because two documents look semantically
  equivalent. Each held-out run writes a `PROVENANCE.json` recording all of it.
- **Held-out scores and trajectories stay sealed** while the contract arm is developed. They will not be
  used to choose contract features, prompts or repairs.

**Records maintained:** one current state note (`OFFICEQA_STATE.md`), the frozen campaign configuration
and split manifests, job and budget records (`docs/research/jobs.tsv`,
`docs/research/OFFICEQA_AUTHORIZATION.md`), separate baseline and contract-development records, and
resume instructions that survive compaction.

---

## 8. Open items and honest limitations

1. **This is not a reproduction of the SkillOpt paper, and the config says so in its own header.** Our
   dataset is far smaller (12 training questions), the actor is a **hosted FP8** endpoint rather than a
   local bfloat16 server, and the setting is **offline** — parsed text through local document tools, no
   web search. The framework's algorithm is upstream's; the scale and environment are ours.
2. **6 of the 30 held-out questions are UNTESTED for document availability.** They were selected
   mechanically and are reported as unverified rather than filtered out, since filtering on
   retrievability would be a selection the benchmark did not make.
3. **One shared document between training and held-out** (UID0014 / UID0013). Small, real, recorded.
4. **The three-arm campaign's numbers are not a usable baseline** — they ran through the broken actor
   client. That is why this campaign exists.
5. **12 of the 48 S0 trajectories could have been reused** from the 2026-11-18 collection and are not,
   deliberately: one job, one settings fingerprint, for about $0.01.
6. **The CPU accounting convention for the historical 44-hour ceiling is still unsettled** and is not
   touched by this phase's separate allowance.
7. **Trace2Skill's analyst cannot validate its diagnoses** by implementing a fix, a direct consequence
   of keeping answer keys out of analysts. Its causal claims rest on the trajectory alone.

### Next actions
1. Monitor s0 → the four Original SkillOpt epochs and Trace2Skill (both hang off s0, both run in
   parallel).
2. Correct the mixed-version bundle, then develop the contract arm in its isolated snapshot.
3. On completion: export the validation-selected endpoint, then the three held-out evaluations.
4. Report the contract arm's readiness and remaining gaps **separately**.
