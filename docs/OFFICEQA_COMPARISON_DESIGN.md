# The two-arm OfficeQA comparison

**Arm A — Original SkillOpt.** **Arm B — Original SkillOpt + stage and action feedback.**

Authorized by the user on 2026-11-16, with a ceiling of **8 additional A40-hours** and **24 DeepSeek calls inside a
$1 cap**. This document records the design and the frozen inputs. Measured results live in
`docs/research/EXPERIMENT_LOG.md`; the feasibility findings this builds on are in
`docs/OFFICEQA_FEASIBILITY_PROTOCOL.md`.

---

## 1. The question

Does a deterministic report about *stages and action contracts*, added to the analyst's prompt, lead the optimizer
to better skill edits than the analyst's ordinary view of the same trajectory?

### 1a. What this comparison does and does not isolate — a correction

An earlier version of this document, and an earlier message to the user, said the prior was "no effect" because
arm B's reports carried **zero advice** on the two failures available before the real run, and because the one
useful finding (that an "it is absent" conclusion drawn after `grep` hit its 20-line cap is unsound) is a
**P-layer** finding "which both arms receive". The second half of that was wrong, and it understated what this
comparison tests.

The P/S/C layers come from the earlier learned-contract studies, where **P was a control report**: condition P
was a plain progress report, and S and C added ordered stages and action contracts on top of it. "Both arms
receive a P-layer finding" was true *there*.

Here, **arm A receives no report at all** — the user's instruction is that arm A gets the ordinary SkillOpt
analyst input and arm B gets "exactly that input plus the stage + action report". So arm B's delta is the WHOLE
report, every layer of it. On the real 12-question rollout that delta is not empty: across the 7 failures the
reports flag **10 distinct problems** in 5 categories — `grep_cap_reached` (4), `operand_provenance_missing` (3),
`ungrounded_answer_value` (1), `asked_period_absent` (1) and `scale_conversion_missing` (1).

Two consequences for reading the result:

1. A difference in favour of arm B would show that *a deterministic report helps*, NOT that stages and action
   contracts specifically help. The finer question — whether the S and C layers add anything over a plain
   progress report — was answered negatively by the earlier pilot and by the ALFWorld stage studies, and this
   comparison does not re-test it.
2. The prior is therefore weaker than "no effect", but it is still not "effect": the reports mostly restate what
   is visible in the trace (that `grep` returned 20 lines, that a value is not in the evidence). Whether the
   optimizer uses a stated diagnosis better than the raw trace it is also given is exactly what is unknown.

## 2. What is held identical

Both arms run from **one config file**, `configs/research/officeqa_cmp.yaml`. The only difference between them is
the environment variable `OFFICEQA_STAGE_REPORT_DIR`, which the opt-in hook in `skillopt/gradient/reflect.py`
reads. With it unset, arm A's analyst prompt is byte-identical to upstream's. One file rather than two is the
point: matching optimizer calls, editing opportunities, edit budgets and the validation gate is then a property of
the file, not of two files that have to be kept in step.

| Held identical | Value | Where it comes from |
|---|---|---|
| starting skill | `skillopt/envs/officeqa/skills/initial.md` | released |
| actor | Qwen3.5-4B, temperature 0.7, thinking disabled | `officeqa_qwen35.yaml` |
| optimizer | DeepSeek V4.1 Flash (`deepseek-flash`) | `officeqa_qwen35.yaml` |
| epoch-1 rollouts | the SAME 12 trajectories, injected into both arms | `STAGE=shared` |
| gate baseline | the SAME 12 validation rollouts of the starting skill | `STAGE=shared` |
| steps per epoch | 1 (batch_size 40 >= 12 training questions) | upstream |
| analyst minibatch | 8 | upstream |
| edit budget | 4 edits per step, `lr_control_mode: fixed` | upstream |
| update mode | `patch` (bounded edits, not a rewrite) | upstream |
| gate | native `evaluate_gate` on `hard`, whole validation split | upstream |
| scorer | the native OfficeQA evaluator; the expected answer never leaves it | upstream |

**Why the rollouts are shared.** The actor samples at temperature 0.7, so two rollouts of one skill on one
question differ. If each arm rolled its own epoch 1, the arms would differ by the actor's sampling as well as by
the report, and with 12 questions that noise would swamp the effect being measured. `STAGE=arm` copies the shared
rollout into the trainer's `steps/step_0001/rollout/`, where `run_batch` resumes by id from `results.jsonl` and
generates nothing.

## 3. Two deviations from the released defaults, applied to BOTH arms

1. **`optimizer.use_slow_update: false`, `optimizer.use_meta_skill: false`.** Upstream turns both on. With
   `num_epochs: 2` they fire at the end of epoch 2: the slow update injects a *second* skill modification per
   epoch (a `SLOW_UPDATE` section consolidated from a longitudinal replay), which contradicts the instruction to
   give each arm **one** skill update per epoch, and between them they would add roughly 24 rollouts and 2
   optimizer calls per arm against a 24-call budget for the whole comparison. Both are symmetric, so neither can
   favour an arm; what they would change is the number of updates per epoch and the cost.
2. **`evaluation.eval_test: false`, with the held-out evaluation run afterwards as its own stage.** Upstream
   evaluates the starting skill and the best skill on the test split at the end of each training run. Run inside
   both arms, that would measure the *starting* skill's held-out score twice, and at temperature 0.7 the two
   numbers would differ, leaving no single baseline. `STAGE=heldout` evaluates one skill at a time, after both
   arms are finished, so the three numbers come from one code path and the held-out set is touched last.

Also restored to upstream after the feasibility runs: `env.max_tool_turns: 24` (the earlier 8 ended two of three
first-epoch items mid-search), `env.max_completion_tokens: 16384`, `env.workers: 4`.

## 4. The frozen question sets (rule OQA-SEL-2)

Frozen **before any of them was run**, on 2026-11-16. Digest
`fc02291540ef365e0846032bb3a8231e719befa9570c31b52938ce6baf33986b` over the three id lists
(`research/officeqa/oqa_freeze_sets.py`, record in `docs/research/OFFICEQA_FROZEN_SETS.json`).

| Set | Released split | Count | easy/single | easy/multi | hard/single | hard/multi | Shortfall |
|---|---|---:|---:|---:|---:|---:|---:|
| training | `train` | 12 | 3 | 3 | 3 | 3 | 0 |
| validation (the gate) | `val` | 12 | 3 | 3 | 3 | 3 | 0 |
| held-out | `test` | 24 | 6 | 6 | 6 | 6 | 0 |

**The rule.** The pool for a set is the released split manifest, so the three sets are disjoint by construction
and the held-out set is the one upstream reserved for testing. Items whose `source_files` and `source_docs` counts
differ are dropped, because the supplied-page builder pairs those two lists positionally and cannot identify the
page otherwise (1 of 50 in train, 1 of 24 in val, 8 of 172 in test). The remainder is stratified by `category`
(easy/hard) x source count (single/multi) and ordered by numeric uid, and each set is filled by round-robin over
the strata in the fixed order (easy,single), (easy,multi), (hard,single), (hard,multi). The training set is
OQA-SEL-1's selection, frozen on 2026-11-15 before any question text was visible.

### 4a. The evidence test, and the one substitution

Each selected item was then asked a further question: **is the value this question needs actually present in the
text the actor is given?** The test reads the question and the parsed documents, and **never the answer column** —
`oqa_freeze_sets._assert_no_key_access()` enforces that against the module's own source at import time.

A number is not evidence if the **question itself states it** (a period, a page label, a rounding instruction: the
actor is told it, so finding it establishes nothing), and not a measured value if it is a **year** or a
**day-of-month** following a month name. Verdicts:

- `AVAILABLE` — the supplied page pairs the question's subject words with a retrieved value (70 of 72 items).
- `AVAILABLE_VIA_TOOLS` — the page does not, but the document does, on a line that pairs them, and `grep`/`read`
  can reach it (1 item: `UID0030`).
- `UNCERTAIN` — values are present but nothing pairs them with the subject words. Tables keep labels in header
  rows, so this is common, and it is **not** grounds for exclusion.
- `ABSENT` — neither the supplied page nor the whole document holds any retrievable value. **The only verdict
  that excludes an item** (1 item: `UID0046`).

**A chart mention, or a short page, cannot exclude an item on its own**, and two tests pin that down, including a
158-character page that passes because it carries a figure.

**`UID0046` → `UID0063`** (next unused item of the same easy/single stratum). The first rollout prompted the
check. Its question asks, of a pie chart on the page labelled 21 of the September 1988 bulletin, what percentage
of gross federal obligations incurred outside the federal government are **not "service-related"**. The supplied
page holds the chart's three title lines and nothing else — no percentage, no figure — and the legend term
`service-related` occurs **0 times in the whole 448 KB parsed bulletin**, so the chart's data is not in the text
at any distance the tools can reach. An earlier version of the checkpoint rejected this item for having a
158-character page, which was the wrong reason: the page is correct, and the question is simply not answerable
from text.

## 5. The run, stage by stage

```
STAGE=shared    1 A40   12 training rollouts (starting skill, gated on the first 3)
                        12 validation rollouts (starting skill) = the gate's baseline
                        then free and offline: arm-B reports, and the prompt audit
                   |
                   +-- the same traces injected into both arms
                   |
STAGE=arm ARM=A   1 A40   epoch 1: reflect -> merge -> rank -> apply -> gate on val
                          epoch 2: replay the same 12 questions with the retained skill, then the same
STAGE=arm ARM=B   1 A40   identical, except OFFICEQA_STAGE_REPORT_DIR is set
                   |
STAGE=heldout     1 A40 per skill   the 24 held-out questions, once each for the starting skill,
                                    arm A's final skill and arm B's final skill
```

The arms are run **sequentially**, not in parallel, although one A40 each was authorized. The reason is the
24-call budget: a step needs 5 or 6 optimizer calls depending on whether the merged edit pool exceeds the budget,
so arm A's actual count is what decides whether arm B can run inside the cap. Running them in parallel would
spend the whole budget before either count was known.

### 5a. Before the first paid call

Three things are checked, all free:

1. **The frozen sets are recorded** (section 4) and the rollout refuses to proceed on an item the evidence test
   excluded (`oqa_rollout_checkpoint.py --evidence`).
2. **The pipeline works**, judged on the first three questions: documents supplied, tools executed when
   requested, trace and score recorded, answer key isolated. A wrong answer, a missing `<answer>` tag and
   reaching the tool-turn cap are **outcomes**, recorded and not fatal.
3. **The arms differ by the report and nothing else**, judged on the *rendered prompts*
   (`oqa_optimizer_input_audit.py`): arm B's prompt minus the exact report bytes must equal arm A's prompt, and
   neither may contain the answer key as evaluator-supplied information. The same audit answers the question of
   where the written grep-cap warning appears.

`oqa_optimizer_precheck.py` then verifies the base URL, the model id and `GET /models` with **no paid call**, and
estimates the call budget from the number of failures the rollout produced, so an overrun surfaces before the
first paid call rather than after it.

## 6. What could still make this comparison uninformative

- **12 training questions, one gate, one seed.** A single accepted or rejected candidate per arm per epoch is a
  coarse measurement; a difference of one gate decision is not evidence of a better feedback channel.
- **The reports may have nothing to say.** On the traces seen so far they produce no advice. An arm-B report that
  is empty makes the two arms' prompts nearly identical, and the comparison then measures optimizer noise.
- **The actor samples.** Sharing epoch-1 rollouts removes that noise from epoch 1 only. Epoch 2 replays and the
  gate evaluations are freshly sampled in each arm, so part of any difference is sampling.
- **A held-out set of 24 questions** resolves differences of roughly 1 question = 4 percentage points. Only a
  large effect would be visible.
