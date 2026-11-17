# OfficeQA: the three-arm comparison on the hosted actor — launch plan

**Status: offline preparation is COMPLETE and checked. Nothing has been submitted. The campaign needs a budget
(section 8) and two decisions (section 11).** Written 2026-11-17, superseding the two-arm plan in
`OFFICEQA_API_COMPARISON_PLAN.md` section 4, which remains the record of the hosted-actor switch and the smoke
test.

Everything below either ran offline on this workstation or is a measurement from a recorded run on this project.
No actor request, no paid call, no GPU and no cluster job was used to prepare it.

---

## 1. The three arms

Following the comparison structure of the ALFWorld AC-1 campaign:

| arm | name | what the analyst receives |
|---|---|---|
| **A** | Original SkillOpt | the ordinary analyst inputs, byte-identical to upstream |
| **B** | Action Contracts Only | the ordinary inputs **plus** feedback from the evolving action contracts |
| **C** | Action Contracts + Stages | the ordinary inputs **plus** the action feedback, the stage obligations, and the checks that link actions to stage progress |

The arms differ in exactly two environment variables, which the opt-in hook in `skillopt/gradient/reflect.py`
reads. Everything that defines the experiment — splits, scoring, gate, edit budget, optimizer call schedule,
actor settings — comes from one config file, `configs/research/officeqa_api3.yaml`, for all three arms.

```
arm A:  OFFICEQA_STAGE_REPORT_DIR unset
arm B:  OFFICEQA_STAGE_REPORT_DIR=<dir>  OFFICEQA_STAGE_REPORT_CONDITION=LC_ACTION  OFFICEQA_LC_MODEL=<frozen>
arm C:  OFFICEQA_STAGE_REPORT_DIR=<dir>  OFFICEQA_STAGE_REPORT_CONDITION=LC_STAGE   OFFICEQA_LC_MODEL=<frozen>
```

---

## 2. The three question sets — rule OQA-SEL-3

`docs/research/OFFICEQA_FROZEN_SETS_V3.json`, digest `117836ff413934b3…`, built by
`research/officeqa/oqa_sel3.py` from the tracked ID manifest of the released OfficeQA splits. No question text,
no document, no score and no trace was read, so the sets cannot have been chosen with hindsight. Supersedes
OQA-SEL-2 (`fc02291540ef…`).

| set | n | drawn from | strata (easy/hard × single/multi doc) | availability untested |
|---|---|---|---|---|
| train | 24 | the released train split, 50 available | 6 / 6 / 6 / 6 | 10 of 24 |
| validation | 24 | **the whole released val split** | 5 / 6 / 7 / 6, whatever the benchmark released | 4 of 24 |
| held-out | 60 | the released test split, 172 available | 15 / 15 / 15 / 15 | 34 of 60 |

**train (24)**
```
UID0033 UID0034 UID0063 UID0163 UID0151 UID0087 UID0014 UID0026 UID0079 UID0141 UID0166 UID0115
UID0017 UID0030 UID0031 UID0169 UID0165 UID0044 UID0028 UID0065 UID0085 UID0150 UID0019 UID0228
```
**validation (24, the released set in full)**
```
UID0001 UID0027 UID0039 UID0041 UID0052 UID0070 UID0072 UID0086 UID0091 UID0109 UID0142 UID0154
UID0159 UID0161 UID0170 UID0190 UID0213 UID0217 UID0220 UID0233 UID0234 UID0235 UID0239 UID0240
```
**held-out (60)**
```
UID0006 UID0011 UID0016 UID0020 UID0021 UID0023 UID0095 UID0158 UID0146 UID0232 UID0080 UID0047
UID0061 UID0206 UID0048 UID0008 UID0076 UID0078 UID0082 UID0088 UID0089 UID0198 UID0099 UID0160
UID0171 UID0116 UID0137 UID0126 UID0131 UID0064 UID0003 UID0009 UID0010 UID0012 UID0013 UID0015
UID0187 UID0245 UID0214 UID0050 UID0180 UID0084 UID0175 UID0118 UID0093 UID0004 UID0005 UID0022
UID0025 UID0062 UID0068 UID0147 UID0112 UID0223 UID0148 UID0096 UID0117 UID0140 UID0216 UID0100
```

The rule, stated before it was run:

* **validation** is every item of the released val split. The user asked for the released 24-question set, so
  nothing is selected or balanced; the stratum mix is whatever the benchmark released. Nothing is filtered out
  either, including items whose evidence may not be retrievable — filtering the gate would be a selection the
  benchmark did not make.
* **train** and **held-out** are balanced over category × document count. **OQA-SEL-2's items are retained
  first**, so all three previous sets are subsets of the new ones and every saved development trace remains a
  trace of a current item. Verified: `previous_sets_are_subsets` is true for all three.
* the remaining draws follow `sha256("OQA-SEL-3" + uid)` ascending — a fixed permutation of the pool that
  depends on the rule name and the item id and on nothing else.
* **UID0046 stays excluded** from the train pool. OQA-SEL-2 removed it because the evidence test found no
  retrievable value for that question in its supplied page or anywhere in the parsed document. The exclusion is
  carried forward and recorded, not applied silently, and it is not applied to validation.
* the three sets are **disjoint** (they come from disjoint released splits; checked anyway).

**What is not established.** 48 of the 108 items have no availability verdict: whether the asked value is
actually retrievable needs the documents, which exist only on the cluster. The counts above say exactly how many
are untested per set. A short CPU preflight can test them before the first rollout; it reads local files only and
spends nothing. Unanswerable items depress every arm's score equally, so they do not bias the comparison — they
lower the ceiling.

---

## 3. How arm B's report differs from arm C's, and how that is checked

Arm C's report is arm B's report **plus** the stage half:

* **the action half** (`oqa_lc_report.render_action`) — the learned action contracts in force with the evidence
  that justified each one; the ones that have been withdrawn, retired or superseded, with the reason; what each
  contract forecast for this attempt's tool calls and what the observation showed, as CONFIRMED / VIOLATED /
  UNRESOLVED; and the assumption checks. Nothing about a task requirement, a value binding or an answer.
* **the stage half** — what the question requires, the recorded value chain, and the obligation ledger with
  each obligation's resolution and witness.

Two checks, run on the **rendered text** rather than on the intention:

* `audit_action_only` fails an Action-Only report that contains any STAGE_MARKER — the stage section headings,
  the obligation kind names (`cell_bound`, `period_match`, `unit_match`, `arith_recorded`, `answer_traced`) and
  the obligation states `DISCHARGED` / `OUTSTANDING`. Generic English words that stage material tends to use
  ("stage", "obligation", "conversion", …) are reported as advisories and do **not** fail, because the report
  quotes column headings out of the bulletins and one such word in a scanned table must not abort an arm.
  `oqa_reports.build_one_lc` raises `StageLeak` on a failure rather than handing the report over.
* `audit_containment` fails if arm C's report does not contain arm B's line for line. Without it, a change to
  the action renderer could leave the arms differing in the action half too, and the comparison would no longer
  isolate the stage contribution.

Measured on the 24 saved attempts of the shared epoch-1 rollout and a later epoch: **24 of 24 pass both.** Arm
B's report averages 2,965 characters, arm C's 6,134.

The heading the analyst prompt puts the report under is part of what the optimizer reads, so it differs too:
`#### Action Contract Report` for arm B, `#### Action Contract and Stage Report` for arm C.

---

## 4. How each arm's contract model evolves

`research/officeqa/oqa_lc_arm.py`. Both contract arms start from **one** model learned on the shared rollout;
after that each evolves from its own training traces using the same learner, the same evidence rules and the same
repair schedule.

| epoch | model in force for that epoch's reports | learned from | reports are |
|---|---|---|---|
| 1 | **M0** | the shared epoch-1 rollout | **in-sample** (see below) |
| 2 | M0 | the shared epoch-1 rollout | out-of-sample |
| 3 | M2 | + that arm's epoch-2 traces | out-of-sample |
| 4 | M3 | + that arm's epoch-3 traces | out-of-sample |
| — | M4 | + that arm's epoch-4 traces | recorded, used for nothing further |

The order inside an epoch is what keeps a prediction a prediction:

```
rollout → reports built under the model frozen BEFORE this epoch → analyst → gate → END OF EPOCH → evolve
```

`evolve` replays the new traces against the model in force **first**, committing each prediction's sha256 to a
hash chain before the observation is released, and only then admits them as evidence. Evidence **accumulates**:
the collections the arm has already admitted are reloaded, so "recheck the earlier cases" means what it says.

**The one exception, recorded rather than hidden.** M0 is learned on the shared epoch-1 rollout, which is also
the rollout epoch 1's reports describe. Epoch 1's forecasts were induced to hold of those very observations and
are not predictions. The frozen model carries its evidence item list, so a report on one of those items **says
so in its own text**. From epoch 2 on every report is out-of-sample. A leave-one-out variant would remove even
this, at the cost of 24 models instead of one for epoch 1; it is not implemented and is listed in section 11.

### What the pre-launch run showed about the evolution

M0, learned on the shared epoch-1 rollout (12 items, 128 tool steps):

```
assumptions adopted:  flat_header_authoritative, table_scale_applies_to_rows, grep_cap_not_reached
assumptions REJECTED: index_alignment    contradicted at UID0026/e1@44 - merging the supplied page's split
                                         header by index names 'Total paper money' where the flattened header
                                         the step returned names 'End of fiscal year or month'
                      year_inheritance   contradicted at UID0028/e1@8 - the table's first labelled row 'Jan.'
                                         names a month with no earlier row to inherit a year from, and the
                                         table's header is in the same observation
contracts adopted:    grep.effect.1  pattern_in_every_line   27 steps / 9 items, 0 counterexamples
                      read.effect.1  width_uniform           36 steps / 8 items, 0 counterexamples
                      read.effect.2  sub_row_aligned         10 steps / 4 items, 0 counterexamples
```

Evolving M0 with a later epoch's traces (12 items, 122 tool steps), with epoch 1's evidence carried forward:

```
out-of-sample before learning:  82 forecasts confirmed, 1 violated, 71 the records cannot settle
the counterexample:             UID0028/e2@42, read, width_uniform forecast true, observed false
what it justified:              read.effect.1 auto-refuted, then RETIRED (no accepted repair)
what else changed:              grep.effect.2, read.effect.3, read.effect.4 induced from the new evidence
retained, with the evidence:    grep.effect.1  60 steps / 11 items, 0 counterexamples
                                read.effect.2  23 steps /  6 items, 0 counterexamples
```

Both arms were given the same traces in the dry run, so their models came out identical in content and differ
only by the arm label recorded in the frozen file (`evidence_scope`) and hence by digest. In the campaign the
traces differ and the models will diverge on content.

---

## 5. Effective settings, read back from the pinned trainer

Resolved through `scripts/train.py:load_config()` on `configs/research/officeqa_api3.yaml` — not read off the
YAML, because the trainer's own resolution is what runs.

```
env officeqa              num_epochs 4             batch_size 40      accumulation 1
  -> steps_per_epoch = ceil(24 / (40 x 1)) = 1, i.e. ONE ordinary skill update per epoch
minibatch_size 8          merge_batch_size 8       analyst_workers 2  failure_only false
skill_update_mode patch   lr_control_mode fixed    lr_scheduler cosine
edit_budget 4             min_edit_budget 2
  -> the per-epoch edit budget is 4, 3, 2, 2 (cosine from 4 to 2 over 4 steps). NOT constant at 4, and
     identical in every arm.
use_gate true             sel_env_num 0 (the whole 24-question val split)     gate on `hard`, strict improvement
use_slow_update true      slow_update_gate_with_selection true   slow_update_samples 4
longitudinal_pair_policy mixed  -> no per-item top-up rollouts
use_meta_skill true       meta skill reuses the slow update's comparison pairs: 1 optimizer call, 0 rollouts
eval_test false           -> the held-out split is evaluated by a separate job (section 10)
seed 42                   workers 4                max_tool_turns 24  max_completion_tokens 16384
target qwen3-5-4b on openai_compatible (EmpirioLabs)   optimizer openai_compatible (DeepSeek)
```

`use_slow_update` and `use_meta_skill` were **off** in the two-arm A40 run and are **on** here, which is the
change the user asked for. At epoch 1 the slow update only injects an empty placeholder and makes no optimizer
call; from epoch 2 it replays the previous epoch's skill against the current one, asks the optimizer for
consolidated guidance, and — because `slow_update_gate_with_selection` is true — submits the result as a
candidate through the **same validation gate** as an ordinary step rather than force-injecting it.

---

## 6. The pre-launch check: two saved epochs through the actual training entry point

`research/officeqa/oqa_prelaunch.py` drives the whole thing; one run, one verdict. Output:
`runs/20261117-officeqa-3arm-prelaunch/PRELAUNCH.json`. **Verdict: PASS, with no problems.**

`scripts/train.py:main()` is the real entry point and the real `ReflACTTrainer` runs. Exactly two things are
replaced, and both because no authorization covers them:

* **the actor** — `chat_target_messages` is replaced by a function that RAISES. Every rollout directory the
  trainer uses is pre-seeded from recorded predictions, and `run_batch` resumes from `results.jsonl`, so a
  correctly seeded run sends nothing. An unseeded directory fails the run loudly instead of spending money.
* **the optimizer** — `chat_optimizer` is replaced in all seven modules that import it by a recorder that
  returns a fixed recorded-shape response per stage and writes every prompt to `optimizer_calls_*.jsonl`. The
  stub's *output* is not realistic and no conclusion is drawn from the skills it produces; what the dry run
  establishes is which *input* each arm's analyst received.

Each arm ran as **two phases, one per epoch**, because `OFFICEQA_LC_MODEL` is read from the environment and must
name a different frozen model in each epoch. The second phase resumed from `runtime_state.json` at step 2, which
is the shape the campaign itself must use.

What it established:

| check | result |
|---|---|
| the training entry point completes for all three arms | COMPLETED ×3, 2 epochs each, slow update and meta skill firing, the gate rejecting |
| **arm A gets the ordinary SkillOpt input** | PASS — no report and no report heading in any of its 4 analyst prompts |
| **arm B gets the Action-Only report** | PASS — 2 of 2 analyst prompts per phase carry `#### Action Contract Report`, no other heading; 12 of 12 reports free of stage material |
| **arm C gets both halves** | PASS — `#### Action Contract and Stage Report`, no other heading |
| every report the optimizer read was audited, and every audited report was sent | PASS — 12 of 12 reports per phase found **verbatim** in an analyst prompt |
| **which trace and model digest reached the optimizer** | PASS — 24 reports over 2 collections, 24 distinct trace digests, every one matching the attempt on disk, every one under the model the arm was run with |
| a changed model reaches the optimizer | PASS — after the evolution the report records carry the new digest, and the report text drops the retired contract and names the three new ones |
| arm C's report contains arm B's, line for line | PASS — 24 of 24 |

Four defects were found and fixed by building this, and each has a regression test in
`tests/test_officeqa_three_arm.py`:

1. **`Evidence` keyed its atom cache on the item id, not the attempt.** With evidence accumulating across epochs,
   the second epoch's attempt of a training question silently reused the first epoch's atom values. On the real
   traces this turned one genuine counterexample into five phantom ones. Attempts now carry a `trace_key`
   (`<item>/e<epoch>`) that the cache and every counterexample id use.
2. **A counterexample found at replay never triggered the refutation.** Re-scoring and refuting a surviving
   contract happens inside `apply_proposal`, which was only called when the learner proposed something new — not
   the case from epoch 3 on. An empty proposal is now applied whenever the evidence contradicts an active
   contract, so the repair has something to target.
3. **A retired contract was invisible to the optimizer.** The report listed only `refuted` contracts, but
   `mech_repair` *retires* a contract it cannot revise, which is the commonest outcome of the repair schedule.
4. **A contract kept with a counterexample was reported as a retention** with the words "no counterexample"
   beside a non-zero count.

Two mistakes in the checking code itself were also corrected, and are worth recording because both would have
produced a false alarm: slicing the report out of the analyst prompt by looking for the next `####` heading
swallowed the trajectory text that follows it (and with it words like "outstanding"), and the per-phase report
check initially counted the previous phase's report files.

---

## 7. Tests

`tests/test_officeqa_three_arm.py` — 25 tests, all passing: the split rule and its determinism, the report
separation and containment, the heading map, the condition vocabulary, the four regressions above, the held-out
deduplication, and the budget's own constants. Whole local suite: **2,057 passed, 11 skipped** (4 files remain
uncollectable in the scratch venv for want of `openpyxl`; pre-existing and unrelated).

---

## 8. The budget to authorize

`research/officeqa/oqa_budget.py` computes this from measured constants, each carrying the record it came from;
`runs/20261117-officeqa-3arm-prelaunch/BUDGET.json` holds the full output.

### Question-rollouts (one question attempted once by one skill)

```
shared across all arms           48   the fresh epoch-1 rollout (24) and S0 on the gate (24), reused by all arms
per arm, epoch 1                 24   the candidate's gate pass; the slow update only injects a placeholder
per arm, each later epoch        80   24 train + 24 gate + 8 slow update (2 x 4 samples) + 24 slow-update gate
per arm                         264   = 24 + 3 x 80
three arms                      792
+ shared                        840
held-out, worst case            420   at most 7 distinct skills x 60; fewer whenever skills coincide
TOTAL, worst case             1,260
```

### What to authorize

| resource | request | measured basis |
|---|---|---|
| **wall time** | **6 hours**, one campaign | 235 min is the floor set by the account rate limit; the optimizer adds ~20 s per call over 102 calls, and queue waits are on top |
| **CPU job time** | **20 job-hours**, ≤4 concurrent, each ≤60 min, 2 CPUs + 8 GB | 13 jobs (3 arms × 4 epochs + 1 held-out), ≤60 min each, plus one retry per job. 2 CPUs × 20 h = 40 CPU-core-hours |
| **GPUs** | **0** | the actor is remote and the document tools are local |
| **actor HTTP requests** | **15,000** | 11,756 measured-rate requests + headroom for retries |
| **actor charges** | **$8.00** | $5.73 at the measured 106,300 prompt + 4,221 completion tokens per question and EmpirioLabs' published prices |
| **DeepSeek calls** | **130** | 102 computed (34 per arm: 7 per step × 4 epochs, plus slow update and meta skill from epoch 2) + retries |
| **DeepSeek charges** | **$4.00** | $1.36 off-peak, **$2.72 if every call lands at peak**; prompts are priced at the cache-miss rate because the measured cache hits were 256–2,048 tokens against up to 172,548 missed |

**The binding constraint on wall time is the 50 requests-per-minute ACCOUNT limit, not latency.** 11,756 requests
÷ 50 = 235 minutes; latency-bound at the measured 3.41 s median with `workers: 4` would be 168 minutes. The limit
is account-wide, so running the three arms concurrently does not reduce the floor — it only divides the quota.

### Against the previous ask

The two-arm plan asked for 8 CPU job-hours, 4,000 actor requests, $2.50 actor, 56 DeepSeek calls, $1.50 DeepSeek.
Three arms instead of two, 24 training questions instead of 12, 60 held-out instead of 24, and the gated slow
update and meta-skill switched on together account for the increase. The per-question actor cost is now a
measurement from the A40 run's own token counts ($0.0046) rather than an extrapolation from the 12-turn smoke
test, and it is **higher** than the smoke-test extrapolation, not lower.

---

## 9. The job plan and the submission shape

Thirteen jobs, each ≤60 min: **one per arm-epoch** (12) plus the held-out evaluation (1, split further if the
rate limit makes it exceed an hour). One job per arm-epoch is not a convenience — `OFFICEQA_LC_MODEL` is read
from the environment, so a different frozen model per epoch needs a different process. Between jobs,
`oqa_lc_arm.py evolve` runs offline (seconds, no cost) and freezes the next model.

```
for epoch in 1..4:
    for arm in A, B, C:
        submit one job: trainer with --cfg-options train.num_epochs=<epoch>, resuming the arm's out_root
    for arm in B, C:
        offline: oqa_lc_arm.py evolve --prev <model> --pred <arm>/steps/step_000<epoch>/rollout/predictions \
                 --epoch <epoch> --arm <arm> --history <arm history> --out <arm models>
then: the held-out evaluation over the distinct skills (section 10)
```

**Use the job shape that works, and do not rediscover it.** `research/cluster/oqa_api_body.sh`, invoked as
`sbatch --wrap="bash <bundle>/oqa_api_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle>`, working in a
**node-local `/tmp`**. A script submitted directly as the sbatch batch file from `/nfs/turbo` was killed by
NODE_FAIL at 60 s, four times each, on eight different nodes, before Python ran, while a control job with
identical account, partition and resources completed. `--no-requeue` did not stop it.

**A job that spends actor requests must not be requeuable.** The attempt ledger lives in the Python process, so a
Slurm restart begins with a fresh counter and respends the authorization. Keep jobs short and check
`SLURM_RESTART_COUNT` in the log before trusting any ledger total.

**Deployment.** `research/deploy.sh` refuses a commit that is not on `origin`, and pushing has not been
authorized. Both previous cluster jobs ran from a `git archive HEAD` staging bundle with a sha256 manifest
verified in-job. Either authorize a push or reuse the bundle route.

---

## 10. The held-out evaluation

`research/officeqa/oqa_heldout.py --plan`. The held-out split is scored **once per distinct skill** and never
during training:

```
S0 (the starting skill)  +  each arm's validation-selected skill (best_skill.md)  +  each arm's last skill
```

deduplicated by skill digest — at most 1 + 6 = 7 rollouts of the 60-question split, and fewer whenever skills
coincide. They do coincide in practice: in the A40 two-arm run all four candidates were rejected and both arms'
final skills were byte-identical to S0, which would have made 5 of 5 requested skills a single distinct one.

This is why the three training jobs run with `eval_test: false`. With it true the trainer evaluates the held-out
split itself, once per arm, and each of those passes re-scores S0 — three identical 60-question rollouts for one
number.

Both skills per arm are saved by the trainer already: `best_skill.md` is the validation-selected one and
`skills/skill_v<step>.md` the last. The planner reports a missing one as a problem rather than guessing.

---

## 11. Decisions still open

1. **`slow_update_samples: 4`, against upstream's 20.** The measured reason is in the config file and section 8.
   Four is the largest value that stays inside the measured prompt envelope even when every comparison pair lands
   in a trajectory-showing category. Six works on the measured category mix but not in the worst case; 20 is far
   out of reach. Raising it means confirming the optimizer's real context window first — a free `GET /models`
   against the configured base URL settles it, and the published figures for the DeepSeek chat family disagree
   (128k for the V3 era against 1M for the current ones, with no official source among them).
2. **Epoch 1's reports are in-sample**, because M0 is learned on the rollout they describe, which is what
   "the same contract model learned on the shared rollout" implies. The report says so in its own text. The
   alternative is leave-one-out for epoch 1 only: 24 models instead of one, no extra cost, and epoch 1's
   forecasts become predictions. Say the word and it is a small change to `oqa_lc_arm.seed`.
3. **Four epochs still gives no cross-epoch memory in the ordinary update.** `step_buffer` is declared inside the
   epoch loop and `steps_per_epoch` is 1, so epoch *n*'s analyst never sees epoch *n−1*'s rejected edits. The
   gated slow update and the meta skill now carry some memory across epochs, which is new since the A40 run, but
   the ordinary update does not. Restructuring as one epoch of four steps remains the fix, and remains open.
4. **FP8 makes this a new baseline.** Same weights as the A40 pilot (`Qwen/Qwen3.5-4B`), different numerics
   (FP8 weights and FP8 KV cache against bfloat16). S0 must be re-measured on this actor; the A40's 5/12 does not
   transfer. Symmetric across arms.
5. **Availability is untested for 48 of the 108 items** (section 2). A short CPU preflight settles it and spends
   nothing.
6. **A push has not been authorized** (section 9).

---

## 12. What this preparation does **not** establish

* Nothing about which arm is better. No arm has run on the hosted actor.
* Nothing about the actor's accuracy on the new splits. S0 has not been measured on this endpoint.
* Nothing about whether the contract reports help the optimizer. The earlier offline probe on the two-arm run's
  traces found **0 of 8 cases gained a valid diagnosis attributable to the report and 1 of 8 gained a misleading
  one** (`OFFICEQA_REPORT_VALUE_PROBE.md`); that finding stands and this campaign is what would test it at scale.
* Nothing Lean verified. The Lean artifact compiled (job 66259448, all 64 statements, Lean 4.20.0) and is
  unchanged by this work; the three-arm reports rest on the Python extractor, not on Lean.
* The dry run's skills and scores are meaningless — the optimizer was a stub and the rollouts were recorded.
  What it establishes is the wiring: which input reached which arm, and under which trace and model digest.

**The held-out outcome of job 66234927 remains deliberately UNREAD and SEALED.**
