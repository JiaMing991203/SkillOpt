# OfficeQA contract-arm campaign — final report (rule OQA-FW-1, 2026-11-23)

Abbreviations on first use: **S0** = the unchanged starting skill. **Original SkillOpt** = the upstream
framework with no report (the control). **Trace2Skill** = the one-shot per-trajectory method. **FW** =
the ported learned action-and-stage framework, this campaign's arm. **Actor** = the frozen target model
answering questions (`qwen3-5-4b`). **Optimizer** = the model that reads trajectories and proposes skill
edits (`deepseek-flash`). **Learner** = the same provider in a different role, proposing contracts.
**Obligation** = one checkable requirement about one written number. **Gate** = the validation test a
candidate skill must pass to be accepted. **Hard / soft** = the benchmark's two scorers; they agreed on
every evaluation here, so one number is quoted.

## 1. Performance, and the comparison released together

30 held-out questions, two passes each, same actor, tools, document rendering and scorer for all four.

| endpoint | pass 1 | pass 2 | total | mean | bytes |
|---|---|---|---|---|---|
| S0 (starting skill) | 4/30 | 7/30 | 11/60 | 0.1833 | 887 |
| Original SkillOpt | 8/30 | 7/30 | 15/60 | 0.2500 | 4,510 |
| Trace2Skill | 9/30 | 6/30 | 15/60 | 0.2500 | 24,121 |
| **Contract arm (FW)** | 11/30 | 6/30 | **17/60** | **0.2833** | 5,691 |

The contract arm is nominally highest. **It is not a supported difference.** Paired per-question,
per-pass sign tests over the 60 cells:

| comparison | contract better | worse | tied | two-sided p |
|---|---|---|---|---|
| vs Original SkillOpt | 6 | 4 | 50 | **0.754** |
| vs Trace2Skill | 9 | 7 | 44 | **0.804** |
| vs S0 | 9 | 3 | 48 | 0.146 |

And the arm's own instability is larger than any gap between arms: its two passes scored **11 then 6**,
with **11 of 30 questions flipping between its own passes** — against 3 for Original SkillOpt and 7 for
Trace2Skill. A 2-of-60 difference between arms sits inside a 5-of-30 swing within one arm. The honest
reading is **no demonstrated effect on held-out performance**, in either direction.

**Selected skill.** `6ca636f141523c81`, 5,691 bytes, the candidate accepted at epoch 3. Its six merged
edits: prefer a chart's underlying data table; commit an answer rather than ending empty; multi-block
tables repeat a header and each block is a different period; sanity-check an intermediate's order of
magnitude; locate a table by grepping a distinctive label rather than a guessed line offset; render the
value in the exact surface format requested.

## 2. The central result: the learned layer changed no conclusion

A four-cell ablation over the campaign's own rollouts, each cell rebuilding every report from the same
records through the same code with only the model argument changed. Cell A adopts nothing; cell B adopts
the seed assumptions and no contract; cell C is what ran.

| epoch | operands | cell | `column_identified` | `unit_resolved` | `conversion_accounted` | report chars |
|---|---|---|---|---|---|---|
| 1 | 526 | A / B / **C** | UNKNOWN 526 in all three | NOT_REQ 8, UNK 518 in all three | NOT_REQ 79, UNK 447 in all three | 32,891 / 33,347 / **38,411** |
| 3 | 385 | A / B / **C** | UNKNOWN 385 in all three | NOT_REQ 18, UNK 367 | NOT_REQ 46, UNK 339 | 32,900 / 33,356 / **41,792** |
| 4 | 435 | A / B / **C** | UNKNOWN 435 in all three | NOT_REQ 18, UNK 417 | NOT_REQ 73, UNK 362 | 33,163 / 33,619 / **42,079** |

**1,346 operands, three epochs, every state identical in every cell.** The learned model's entire
footprint is the report text that names it. Cell B equalling cell A matters too: the researcher-supplied
assumptions added nothing either, so the zero belongs to the learner's output and not to a setting
chosen for it.

Every judgement the reports delivered, counted:

| state | count | share |
|---|---|---|
| UNKNOWN | 2,639 | 94.6% |
| NOT_REQUIRED | 151 | 5.4% |
| DISCHARGED | **0** | 0% |
| OUTSTANDING | **0** | 0% |

Not one positive finding and not one accusation. Both halves are results: the soundness property holds —
the checker never accuses on evidence it lacks, which is what the 2026-11-22 audit was for — and so does
the uselessness, because a report that only ever says "I cannot tell" carries no diagnosis.

**Why it is zero, traced not guessed.** `column_identified` discharges only when `col_bound` is TRUE;
`col_bound` is TRUE only under the `index_alignment` assumption AND an active learned rule whose
consequence is a label-determining atom with value `true`. The learner accepted three rules in four
passes and none qualifies — two conclude `col_label_present` and `child_aligned`, which license nothing,
and the third concludes `col_child_label_present` **false**. With that obligation UNKNOWN everywhere,
`unit_resolved` cannot find a nearer scope and `conversion_accounted` cannot reach a required factor.

## 3. Learned-rule coverage, unknowns and repairs

| pass | job | attempts | accepted | rejected by the gate |
|---|---|---|---|---|
| seed e1 | 66667004 | 2 | `col_label_present(?c)` whenever `col_child_label_present(?c)` (265 support, 0 counter); `child_aligned(?t)` whenever `header_split(?t)` (57, 0) | a `col_label_agrees_flat` form: 0 support, 171 undetermined |
| evolve e3 | 66667007 | 2 | `¬col_child_label_present(?c)` whenever `¬col_label_present(?c)` (934, 0) | a `cell_row_labelled` form: 0 support, 8,140 undetermined |
| evolve e4 | 66667512 | 1 | none — **abstained**, `keep` on all three | — |

Support grows as the gate re-scores over all evidence: 265 → 452 → 667 and 57 → 115 → 166.

**Three findings here matter more than the counts.**

**(a) The gate accepted the exact contrapositive of an active rule.** Rule 1 is `A → B`; rule 3 is
`¬B → ¬A`. Logically equivalent, so it carries nothing the model did not already have — and it scored
934 supporting cases because the contrapositive is determinate on the far more numerous negative
columns. The learner's own note says so: *"added the contrapositive column rule, which has 934
determinate supports."* **The gate tests evidence, not logical independence from the active model, and
this framework has no check for the latter.** That is a framework defect this campaign exposed.

**(b) The learner diagnosed the evidence gap itself**, unprompted, from the atom census: *"flat_pair_observed
is false for every table and col_label_agrees_flat is unknown everywhere, so flat_header_authoritative
cannot be tested; col_bound stays unknown for all 1066 columns."* It also noted that charts and
prose-only answers introduce no entities the vocabulary can relate.

**(c) Repairs: none fired, and that is recorded rather than fabricated.** 0 contracts refuted, 0 retired,
2 proposals rejected. `fw_repair.attempt` is reached only for a refuted contract and no evidence ever
contradicted an accepted rule. Deletion repair, earlier-evidence regression checking and retirement are
implemented and tested; this campaign's evidence exercised none of them.

## 4. Report-to-edit uptake, and survival through consolidation

Measured from the prompts actually sent (`optimizer_calls.jsonl`), matching each report by its unique
`Attempt …` line — the reports share a long preamble, so a prefix match would count all twelve whatever
the prompt holds.

| epoch | stage | prompt chars | report chars | share |
|---|---|---|---|---|
| 1 | analyst (the 5 successes) | 144,783 | 16,169 | 11.2% |
| 1 | analyst (the 7 failures) | 507,845 | 22,230 | 4.4% |
| 1 | merge | 7,572 | **0** | 0% |
| 2 | analyst x2 | 170,202 / 459,827 | 15,986 / 22,429 | 9.4% / 4.9% |
| 2 | merge / ranking / slow_update / meta_skill | 13,377 / 10,902 / 511,218 / 510,024 | **0 each** | 0% |

**All twelve items get a report, successes included.** And **the report reaches the analyst stage and
nowhere else** — zero report characters in merge, ranking, slow-update or meta-skill. The contract layer
cannot influence consolidation, ranking or the optimizer's longitudinal memory except through whatever
the analyst chooses to write.

Matched against the executed baseline's epoch 1, the analyst input goes 613,653 → 652,628 characters,
**+38,975 (+6.35%)** — the reports plus their headings and nothing else, which is the condition the
comparison needs.

**Did anything survive into an edit?** The two ranked edits of the accepted epoch-3 candidate both carry
`source_type: "failure"` and `support_count: 8`: they come from the analyst reading failures, and none
cites a contract. The sharpest check is topical. Phrase hits in each endpoint over the four topics the
report is about:

| topic | baseline endpoint | contract endpoint |
|---|---|---|
| **column header identity** | **0** | **0** |
| money scale / unit | 2 | 8 |
| citing a position | 0 | 1 |
| period / row identity | 5 | 19 |

The contract endpoint says more about scales and periods, but the report contained no finding about
either, so nothing traces. And on **column header identity** — the one subject the contract layer exists
to adjudicate, about which the report said UNKNOWN 1,346 times — **both endpoints say nothing at all.**

**Wording, counted not judged.** S0: 0 obligation words, 4 optional, 1 line leaving an alternative.
Baseline endpoint: 5 / 12 / 3. Contract endpoint: 10 / 17 / 7. More imperative *and* more hedged, with
more recovery paths open, so no sign of the distortion pattern. One confound checked and ruled out as a
differentiator: both optimized endpoints instruct the actor to always commit an answer (the baseline's
is the blunter) and S0 says nothing of the kind — which explains why both beat S0 on commitment but does
not separate the arms.

## 5. Reused versus newly collected trajectories

**Reused, by provenance and never by outcome** (`oqa_fw_reuse.py`, `FW_REUSE.json`): epoch 1's rollout is
byte-identical to the one the completed Original SkillOpt arm consumed — `results.jsonl`
`d5c0ced1c70c9d21…`, predictions tree `04887721282a2a78…`. Its 12 question ids are exactly the frozen
OQA-SEL-4 training set (`b0dbbd4be97fc245`), 0 missing and 0 extra. The starting skill
`9d377d1c2906981b…` appears inside **every** row's own recorded system prompt. All 12 rows share the
actor/tool settings fingerprint `d58cf3b59bbc2928`, which is the value this config produces. The 12-row
validation baseline is the baselines' own. The check reads `predicted_answer` for completeness and never
reads `ground_truth`, `hard` or `soft`. Kept rather than filtered: 1 attempt made no tool call and 7
committed no answer — evidence about the actor.

**Newly collected:** epochs 2, 3 and 4 under the contract arm's own then-current skill (1,724 actor
attempts in total), the four learner passes, all reports, and the held-out evaluation of the one new
endpoint. The starting skill's held-out evaluation was **reused and not re-paid**; the held-out stage
compares the endpoint digest against the three sealed provenance records and would have skipped itself
had they matched.

**Model schedule**, so every report rests on a model frozen before the traces it describes. At the start
of epoch 2 the only traces in existence are epoch 1's, which M0 was learned on, so M0 serves both and
the first evolution follows epoch 2: epoch 1 → M0 (**in-sample, declared in every report**), epoch 2 →
M0 (out of sample), epoch 3 → M_e2, epoch 4 → M_e3.

## 6. Limitations, deviations and spending

**Limitations.**
1. No demonstrated effect on held-out performance (§1), and the contract layer demonstrably contributed
   no findings (§2), so even the nominal +2/60 cannot be attributed to a diagnosis. What differed in
   training was the *presence* of ~38,000 characters of report, which is the information-access confound
   this project has flagged before.
2. The gate has no logical-independence check (§3a).
3. The contract layer never reaches consolidation, ranking or the meta-skill (§4).
4. One slice, one benchmark, 12 training and 30 held-out questions, two passes. Lean compiling
   establishes that the encoded model's application is internally consistent with the checker's own
   logic; it does not establish that extraction is correct or that an empirical rule is true.

**Deviations from the plan, each declared before the run.**
* **Amendment F-1a:** the campaign learner is DeepSeek, as instructed; the finite enumeration ran free
  and **unapplied** beside it as a labelled diagnostic (378 candidates, 0 acceptable, every one "no
  supporting case").
* **Amendment F-1b:** the licence set was widened from `col_label_agrees_flat` alone to that atom plus
  `col_child_label_present`, because the first was measured to be unevaluable in a campaign epoch and no
  hypothesis could then have moved any obligation. **It produced nothing** — the learner used that atom
  as a condition and its one rule over it concludes it false. A researcher chose which *kind* of
  conclusion licenses; no researcher chose the conditions. Recorded as a decision that did not pay off.
* The uptake audit tool predates this condition and injects a condition-C report into its simulated
  prompt, so its figure (15,306 characters) measures the wrong thing and is not used; §4 is measured
  from the sent prompts instead.
* Actor concurrency was lowered from 4 workers to 2 for epochs 3-4 and the held-out stage after the
  shared-account conflict below. Nothing else about the protocol changed.

**Three defects found and fixed during submission**, all before any result was produced.
1. **Lean refuted the Python export.** The first compile this file ever had (job 66666627) disproved
   **24 `decide` goals**. The Lean conversion rule predated the 2026-11-22 audit, and the exported tuple
   could not determine the state recorded beside it — `in_calculation` was absent, a missing required
   factor was written as `0`, and whether the operand matched a cell was absent. Fixed under
   `oqa.fw.lean.v2`; `self_check` now re-derives every state with a second implementation (12 state and
   3 total mismatches on the first regeneration under it, then clean). Final compile: Lean 4.20.0, rc 0,
   0 errors, 0 warnings, **12 theorems and 114 examples**, source `d45ec19540ca6588`.
2. **The learner's route was configured and inert.** `chat_optimizer*` is a router whose backend comes
   from a call, and `SKILLOPT_REQUIRE_ROUTE`'s only caller is the trainer, which the learner does not
   run. It now pins and proves the route in the calling process; the dispatch check costs nothing.
3. **The learner reasoned past its completion budget** (job 66666916): `finish_reason` length, empty
   `content`, 31,393 characters of `reasoning_content`. Cap 8,000 → 16,000, the prompt now demands the
   JSON block first, and an unparseable reply is retried inside the round budget. After the fix the same
   task used 2,487 and 930 completion tokens.

**Spending**, reserved inside the 2026-11-21 phase grant with no new funds.

| resource | consumed by this campaign | phase position |
|---|---|---|
| CPU | **14.833 requested job-hours** (1.392 h elapsed) | 4.750 of 19.583 left |
| actor | 1,724 attempts, 21.1M prompt / 0.84M completion tokens | within 16,538 / $13.56 |
| optimizer | 27 attempts, 2.0M prompt / 0.11M completion tokens | within 194 / $18.02 |
| total | **$1.6451 at peak prices** | ledger verdict PASS |
| GPU | none | — |

**5.67 of those CPU hours went to re-running the two gates five times** as defects were found and
recommitted — the honest cost of gating every commit, under a convention that charges requested
walltime. Cancelled-before-start jobs cost nothing (66666642-66666650, 66667008-66667011). The separate
demonstration sublimit — 3.08 CPU job-hours and 18 teacher attempts — is **untouched**.

**Shared-account conflict, and my part in it.** Two ALFWorld AC-2 jobs were stopped by account-wide 429s
two minutes after this campaign's first arm job started. Measured from my own logs: 4 workers runs at a
mean of 24.8-28.3 requests/minute with a rolling-60-second peak of 46-57, against a ~50/minute
account-wide limit, while AC-2 was at 42. **I told that session my own jobs were throttled too; that was
false and is withdrawn** — I had grepped the literal "429", which matches Treasury figures inside the
logged payloads. All 1,724 of my actor records are `outcome: ok` on attempt 1: none of my requests was
ever refused, and AC-2's two lanes absorbed the whole cost. I held my remaining actor jobs at zero cost,
dropped to 2 workers (mean 15.1/min, peak 43) and logged three lines in
`coordination/reservations.jsonl`. **The split between the two campaigns remains the user's decision.**

## 7. Submission record

Gates, re-run per commit: `run_tests` (2,646 passed / 16 skipped / 0 failed) and `oqa_fw_lean`.
Chain: 66667003 preflight → 66667004 learn e1 → 66667005 arm e1 → 66667006 arm e2 → 66667007 learn e3 →
66667511 arm e3 → 66667512 learn e4 → 66667513 arm e4 → 66667514 held-out, all COMPLETED.
Bundles `oqa_fw_20261123d` (commit `4b69200`) and `oqa_fw_20261123e` (commit `f3777a0`).
Artifacts in `runs/20261123-officeqa-fw-campaign/`: `FW_REUSE.json`, `models/FW_MODEL_e{1,3,4}.json` with
their `FW_ARM_FW_e*.json` records and learner call logs, `reports_fw/e{1,2,3,4}/`,
`FW_CAMPAIGN_ABLATION.json`, `heldout/contract_arm/` with `PROVENANCE.json`.
