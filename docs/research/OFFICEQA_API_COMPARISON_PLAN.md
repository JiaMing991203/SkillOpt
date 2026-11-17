# OfficeQA on a hosted actor: smoke test and the four-epoch comparison

Written 2026-11-16 (times from `date`). Branch `officeqa-contracts`.

**Status: the smoke test is BLOCKED on one missing credential and everything else is built, tested and ready.**
The endpoint and the model are verified, the tool loop is proved end to end against an OpenAI-compatible endpoint,
the treatment arm is wired to the compiled prototype with per-attempt and per-model-version provenance, and the
comparison's budget is estimated from measured numbers. Nothing was spent: **0 API calls, 0 GPU, 0 CPU jobs.**

---

## 1. The blocker, stated first

`EMPIRIOLABS_API_KEY` **does not exist on either machine.** Every occurrence of that name in either worktree is
code *reading* the variable; there is no assignment anywhere, the cluster secrets file named by `SECRETS_FILE`
contains `DEEPSEEK_API_KEY` only, and the `lc_site.env` that the ALFWorld actor-API pilot read its
`ACTOR_PILOT_SECRETS` path from is gone from both the workstation and Great Lakes.

**To unblock, add one line to the cluster secrets file** (the path is in the private
`research/cluster/site.env`; it must stay mode 600):

```
EMPIRIOLABS_API_KEY=<the key>
```

Then the smoke test is one command, and `research/cluster/oqa_api_smoke.sbatch` picks the key up, exports it as
`TARGET_OPENAI_COMPATIBLE_API_KEY` for the actor role only, drops the optimizer key so no DeepSeek call can be
made by accident, and greps the run directory afterwards to make sure the key did not land in an artifact.

Until then the job exits **4** with that message after running the preflight, which needs no key.

---

## 2. What is already verified, free of charge

### 2.1 The endpoint and the model (`GET /v1/models/qwen3-5-4b`, unauthenticated, 0.67 s)

| | |
|---|---|
| `id` / `aliases` | `qwen3-5-4b` / `qwen3.5-4b` |
| `hugging_face_id` | **`Qwen/Qwen3.5-4B`** - the same weights the A40 pilot served |
| `features` | reasoning, vision, video, **`function_calling`**, cache, multimodal, json_mode, logprobs |
| `structured_output` | `json_schema` |
| `weight_precision` | **FP8 weights + FP8 KV** |
| `context_window` / `max_output_tokens` | 262,144 / 32,768 |
| documented limits | 50 RPM, 2M TPM |
| price (input / cached input / output) | $0.04 / $0.02 / $0.07 per M tokens |

So **the provider advertises function calling**, which is the precondition for the OfficeQA tool loop. Whether it
actually emits well-formed `tool_calls` for these three schemas is what the smoke test settles.

Reachability from the Great Lakes **login node**: `http=200` in 0.245 s. The sbatch re-checks it **from the
compute node**, because login-node reachability proves nothing about where the job runs.

### 2.2 The tool loop, proved end to end against an OpenAI-compatible endpoint

`tests/test_officeqa_api_smoke.py` stands up a local HTTP server that speaks `/chat/completions`, returns a
`tool_calls` reply, and then answers once it receives the tool result. Against it, unmodified SkillOpt:

- advertised all three OfficeQA tool schemas (`glob`, `read`, `grep` - 835 bytes);
- parsed the `tool_calls` reply and read the arguments as JSON;
- resolved the candidate file path and **executed `run_tool` against a real parsed bulletin on disk**;
- sent the observation back as a `role: "tool"` message;
- took a further turn, in which the actor quoted the observation it had received and produced `<answer>`.

16 tests pass. A reply whose tool-call arguments are not JSON fails the verdict rather than passing silently -
and the test records what actually happens, which is one level earlier than I first assumed: the client library
rejects the malformed response before the driver sees a message, so it arrives as an attempt **error**, not as a
parsed-but-bad call. Either way no tool is executed, which is what the verdict keys on.

**So the only untested variable left is the provider's own behaviour.** That is exactly what the authorized smoke
test is for, and it is now a small question rather than an open-ended one.

### 2.3 Two wiring facts found offline, for free

1. **`target_backend: openai_chat` means AZURE OpenAI**, not a generic OpenAI-compatible endpoint. The first
   version of the test failed with "Azure OpenAI endpoint is not configured for target". `skillopt/model`
   dispatches on the *combined* backend name, and the two roles are separated by their env prefixes
   (`TARGET_*` vs `OPTIMIZER_*`), not by the module - so **both** keys must read `openai_compatible`.
   `configs/research/officeqa_api.yaml` sets both.
2. The backend keeps a **module-level config object per role**, so env vars alone are not enough once anything
   else in the process has called `configure_openai_compatible`. The smoke test configures the target role
   explicitly. Worth knowing before a 4-hour comparison silently talks to the wrong endpoint.

---

## 3. The smoke test as it will run

`research/officeqa/oqa_api_smoke.py`, submitted by `research/cluster/oqa_api_smoke.sbatch` as a **CPU job: 2 CPUs,
8 GB, 30 min, zero GPUs**. With the actor behind an API a rollout is HTTP plus local file I/O, so no GPU is
requested and none can be consumed by accident.

**Questions, by a rule fixed before the run** (`OQA-SMOKE-1`): the first three items of the frozen **training**
set, in the order the A40 shared epoch-1 rollout recorded its results, that requested at least one document tool
under the starting skill → **UID0033, UID0063, UID0034**. Requiring a tool call is the point; an item answered
from the supplied pages alone would exercise none of the loop. Validation and held-out items are never read, and
`select_items` refuses an id outside `train/`.

**Matched to the A40 pilot where the schema allows:** same starting skill (digest recorded), same supplied oracle
pages, same local document tools, `temperature 0.7`, `top_p 1.0`, output cap 8,000, offline search mode.
**Not matchable, recorded as the confound it is:** FP8 vs bfloat16 numerics; `top_k` / `min_p` /
`repetition_penalty` cannot be sent through this chat schema. Both differences apply identically to both arms.

**Caps, enforced in code and not by hope:**

| cap | value | how |
|---|---|---|
| actor HTTP attempts | **90** of the authorized 100 | every attempt is counted *before* it is made; `CapReached` at 90, leaving 10 in reserve |
| actor charges | **$0.50** | worst case (measured prompt + full output cap) reserved before each attempt, settled against reported usage afterwards |
| CPU job time | 30 min walltime | sbatch |
| GPUs | **0** | sbatch requests none |
| DeepSeek calls | **0** | the optimizer key is unset inside the job, so a stray call fails closed |

A retry storm cannot walk past the attempt cap: the counter wraps the rollout's own
`chat_target_messages`, so the rollout's `retries=5` is counted, not hidden. The wrapper lives in the test
driver, so shipped code stays byte-identical to what the comparison will run.

**The verdict is about the loop, and accuracy is reported separately.** PASS requires all four of:
structured tool calls returned; arguments usable; tools executed against the documents; results fed back so the
actor took a further turn. Answers are recorded (`hard`, `soft`, answered-count) and are explicitly not part of
the verdict - three questions cannot measure accuracy, and the A40 pilot already put S0 at 5/12 on training.

Also recorded: elapsed time, attempts, per-attempt latency (min/median/max), prompt and completion tokens, cached
prompt tokens, reserved vs settled USD, every malformed reply, every retry with its reason, and any attempt whose
outcome is unknown (flagged `unreconciled` and left charged at the worst case).

---

## 4. The four-epoch matched comparison

### 4.1 Design

| | |
|---|---|
| arms | **A = Original SkillOpt**; **B = SkillOpt + Learned Contracts and Stages** |
| training | the frozen **12** training questions, repeated across **4 epochs**, one skill update per epoch |
| gate | the separate fixed **12**-question validation set, native gate on `hard`, strict improvement |
| held out | the **24** disjoint held-out questions, scored once at the end for S0, A-final and B-final |
| epoch 1 | a **fresh shared API rollout**, used by both arms, so epoch 1 differs only in the report |
| S0 | measured on the API actor (12 validation questions) before any update - the A40's 5/12 does not transfer across FP8 |
| config | **one file for both arms**, `configs/research/officeqa_api.yaml`; the only difference is `OFFICEQA_STAGE_REPORT_DIR` + `OFFICEQA_LC_MODEL` for arm B |
| matched | optimizer call schedule, edit budget (`learning_rate 4`, patch mode), minibatch size, merge fan-in, gate, splits, seeds, `use_slow_update`/`use_meta_skill` off for both |

### 4.2 The treatment arm is wired to the compiled prototype, with provenance

Arm B's report is now the **obligation-form report** of the ported learned-contract layer
(`oqa_lc_report.render`), produced under a **frozen model** whose digest is verified before use, rather than the
old `oqa_check` C report. Two things follow, and both are checked:

- **The report-cache key now includes the model version.** It already covered the conversation, the user prompt,
  the question and the condition (the fix for the A40 stale-report failure). The same trace yields *different*
  bindings under a model that assumes `index_alignment` and one that has dropped it, so keying on the trace alone
  would let a report survive a model repair and be attributed to a version that did not produce it.
- **Every report records `trace_sha256` and `model_sha256`**, and `verify_report_provenance` rejects either
  mismatch.

**Verified on the saved A40 traces** (offline, 0 cost):

| check | result |
|---|---|
| 12 epoch-1 reports against their own attempts, final model | **PASS** |
| 12 epoch-2 reports against their own attempts, final model | **PASS** |
| 12 epoch-2 reports under the *early* model (v5, `index_alignment` in force) | **PASS** - a different, correctly-attributed model digest |
| epoch-1 reports checked against the **epoch-2** attempts | **FAIL**, 4 problems, e.g. `UID0014: report was built from trace cc1ff7b7cda6 but the attempt on disk is db43015ae874` |
| a report built under the early model, checked against the final one | **FAIL**: `UID0026: report was built under model 607f3a138090 but the frozen model in force is 059c110ae6c7` |

The two FAILs are the point: the first is exactly the A40 integrity failure, and it is now caught; the second is
the new one the user asked about, and it is caught too. Two tests hold both lines, plus one that a model file
edited after freezing is refused outright.

### 4.3 Measured runtime estimate

Derived from the A40 run, not guessed: the shared epoch-1 rollout made **128 tool calls over 12 questions**, so
~**11.7 actor requests per question** (one request per turn).

| | questions | actor requests |
|---|---|---|
| shared epoch-1 training rollout | 12 | ~140 |
| S0 on the API actor (validation) | 12 | ~140 |
| per arm: epochs 2-4 training rollouts | 36 | ~420 |
| per arm: validation gate x 4 epochs | 48 | ~560 |
| held out: S0 + A-final + B-final | 72 | ~840 |
| **total** | **264** | **~3,100** |

Wall time per 12-question rollout: ~140 requests at `workers: 4`. The 50 RPM account limit alone puts a floor of
**2.8 min**; at a plausible 8 s per request the parallel time is **~5 min**. So a rollout is **5-10 min**, which
is comparable to or faster than the A40's 21 min, and the per-request latency is one of the numbers the smoke
test pins down. Summing: shared + S0 ~15 min, each arm ~95 min, held out ~45 min → **~4.2 hours of wall time**.

**Recommended job shape, from tonight's queue measurement.** `spgpu` is irrelevant here (no GPU), but the same
walltime effect applies on CPU partitions, and my own measurements today were stark: requests of ≤60 min waited
6-72 min while a 90-min request waited 4h23m. So **split the work into ~10 jobs of ≤60 min** (one per arm-epoch)
rather than two long ones, relying on SkillOpt's resume to reuse completed minibatch patches.

### 4.4 The exact allowances the comparison needs

**This is a separate request; the old OfficeQA allowance has 8 DeepSeek calls left and no CPU or actor budget.**
Each figure is a worst case, with the basis stated.

| resource | request | basis |
|---|---|---|
| **CPU job time** | **12 job-hours**, ≤4 concurrent jobs, each ≤60 min walltime, 2 CPUs + 8 GB | ~4.2 h wall at 2 CPUs ≈ 8.5 CPU-hours; 12 covers a retry of any single job |
| **GPUs** | **0** | the actor is remote and the tools are local |
| **Actor HTTP attempts** | **4,000** | ~3,100 measured-basis requests + retries and cap-hitting questions |
| **Actor charges** | **$4.00** | ~35M input tokens at $0.04/M = $1.40 if questions average 12 turns; $2.48 if a third hit the 24-turn cap; $4 covers that plus output and retries. Cached input at $0.02/M would roughly halve it |
| **DeepSeek calls** | **56** | the A40 run used 8 calls per arm for 2 epochs → 4 per epoch; 4 epochs x 2 arms = 32, plus a ranking call per step when the merged pool exceeds the budget of 4 → up to 48; 56 leaves a margin |
| **DeepSeek charges** | **$1.50** | measured $0.0721 for 8 calls ≈ $0.009/call → ~$0.50 at 56 calls; prompts grow over 4 epochs, so $1.50 |

A pre-run check refuses to start if the estimate exceeds the cap (`oqa_optimizer_precheck.py` already does this
for DeepSeek; the actor ledger does it for the API).

### 4.5 What I would want decided before it runs

- **The FP8 confound.** Same weights, different numerics, so S0 on the API actor is a different number from the
  A40's 5/12 and must be measured, not inherited. It is symmetric across arms, so it does not decide the
  comparison - but it does mean this is not a continuation of the A40 run, it is a new baseline.
- **Four epochs still gives no cross-epoch memory.** `step_buffer` is declared inside the epoch loop and
  `steps_per_epoch` is 1, so epoch *n* never sees epoch *n-1*'s rejected edits. Four epochs is therefore four
  independent attempts, not cumulative learning, exactly as the A40 run's two were. If cumulative learning is
  wanted, the restructure is one epoch of four steps - still an open decision from the A40 report.
- **The learned-contract model to freeze for arm B.** The port's final model (v7, 3 contracts, `index_alignment`
  and `year_inheritance` dropped) is the obvious choice, but one of its three contracts acquires a counterexample
  on the confirmation collection. Freezing a model with a known counterexample is defensible only if it is
  recorded; I would record it.

---

## 5. Files

| | |
|---|---|
| actor config, both arms | `configs/research/officeqa_api.yaml` |
| smoke driver | `research/officeqa/oqa_api_smoke.py` (preflight, ledger, verdict) |
| smoke job | `research/cluster/oqa_api_smoke.sbatch` (`STAGE=preflight` is free) |
| LC report + provenance | `research/officeqa/oqa_reports.py` (`build_one_lc`, `trace_digest`, `verify_report_provenance`, `lc_model_digest`) |
| arm-B hook | `skillopt/gradient/reflect.py` (`OFFICEQA_LC_MODEL`; cache key now covers the model digest) |
| tests | `tests/test_officeqa_api_smoke.py` (16), `tests/test_officeqa_lc.py` (40, incl. 2 provenance) |
| whole local suite | **2032 passed, 11 skipped** |
