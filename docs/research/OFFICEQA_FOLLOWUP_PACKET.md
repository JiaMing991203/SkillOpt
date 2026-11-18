# OfficeQA follow-up: the empty replies, and one richer connected contract

Prepared 2026-11-18. Two bounded investigations under Grant 3, which added **24 EmpirioLabs attempts /
$1, 6 DeepSeek attempts / $2, 4 requested CPU job-hours at 2 CPUs per job, zero GPUs** to the cumulative
ceilings without resetting prior usage. The frozen 2026-11-17 campaign is unchanged: its snapshots,
selected skills, gate decisions, reports and evaluation artifacts are untouched, and everything new is
written under `<campaign>/followup_20261118/`. No held-out question, answer or trajectory was read.

Rules: **OQA-HTTP-1** (the HTTP probe), **OQA-CC-1** (the connected-contract prototype), amendments
**A-1** and **B-1** in `docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md`, recorded before either was
implemented.

---

## 1. In plain language

**What we now know about the empty replies.** They were never empty. Replaying two of the campaign's own
recorded requests reproduced the failure, and the HTTP response came back with status 200, a complete
answer, and `content: null`. The model's text was in a field called **`reasoning`**, which the client
never read: `skillopt/model/common.py` looked at `content` and at `reasoning_content`, found nothing, and
the OfficeQA loop recorded "Model neither produced a tool request nor a final answer". One of the two
replayed replies ends `<answer>[10102000000, 4.73]</answer>`. So a large share of the campaign's 331
empty terminal replies — 62.7% of all 528 selection observations — were answers the client threw away.

This also corrects OQA-FOR-1. That audit reported `reasoning_content` null in all 7,909 logged attempts
and treated the hidden-reasoning hypothesis as refuted. It was refuted only for that spelling.

**What remains unresolved.** How many of the 331 historical empty replies carried a usable answer cannot
be recovered: the campaign's call log stored `content` and `reasoning_content`, not `reasoning`, so the
text is gone for those attempts. The replay rate is suggestive, not a measurement — 2 of 9 historical
replays ended that way and both carried an answer, but the actor samples at temperature 0.7 and nine
replays of two contexts cannot estimate a campaign-wide share. We also do not know what the corrected
client would do to a whole episode, because nothing here ran an episode to completion.

**What the richer contract could express and diagnose.** It can state a claim about *this cell of that
table* under an explicit condition, and withdraw it when the cell's own column header states a different
unit — which the restricted 20-effect learner cannot say at all, since every one of its atoms is a
property of one observation's whole text. Connected to the calculation stage, the binding either
discharges the unit requirement, violates it with a named arithmetic mismatch, or leaves it unknown, and
an unknown binding can never produce an accusation against the actor. Lean verifies that last property
and the conversion arithmetic.

**Which next experiment the evidence supports.** Not another campaign. The order the evidence implies is:
(1) re-run the existing held-back measurement — repeat S0 on the 24 selection questions — **with the
client correction in force**, because every score in the campaign was taken through a client that
discarded answers, and the noise floor measured in `OFFICEQA_DIAGNOSTIC_PACKET.md` §9 was measured through
it too; (2) only then consider whether a contract layer adds anything, because the 24-trace survey here
found the actor's written evidence use largely absent from the records — 7 of 24 traces contain any
number the actor wrote and none states a conversion factor.

---

## 2. Workstream A — repair observability offline (amendment A-1)

All changes are additive except one recorded semantic correction. `skillopt/` and `research/officeqa/`
were changed in place; the regenerated diagnostics live under the follow-up directory and the campaign's
own `stage_reports/` and `GENERATED.jsonl` are untouched.

| what | where | effect |
|---|---|---|
| finish reason, usage, tool calls, provider cost and every unmodelled message field carried through | `model/common.py::compat_message_from_chat_message(message, response, choice)` | fills the existing-but-always-empty `CompatAssistantMessage.metadata`; called without a response it behaves exactly as before, so other backends are untouched |
| response metadata populated in the local-tools loop | `envs/officeqa/rollout.py` | `last_response_metadata` is now assigned every turn, as the three other branches already did |
| explicit termination causes | same | `answer_emitted`, `empty_reply_content_null`, `empty_reply_content_blank`, `completion_truncated_at_cap`, `prose_without_answer_tag`, `tool_turn_budget_exhausted_after_tool_call`, `exception` — including the `for/else` exit the campaign left with no reason at all |
| provider-reported cost beside the conservative estimate | `model/spend_ledger.py::record_usage(..., provider_cost_usd=)` | recorded, never substituted: the charge, the cap and every refusal stay on the computed figure |
| tool calls and unmodelled fields in the call log | `model/openai_compatible_backend.py::_log_call` | the previous shape omitted both |
| **the Action-Only diagnosis counter (semantic)** | `research/officeqa/oqa_reports.py` | was `len(group(ledger)) if condition != LC_ACTION else 0`. Now `n_distinct_diagnoses` means distinct **actionable** diagnoses for both conditions, the stage group count moves to `n_stage_diagnosis_groups`, and `diagnosis_counts` reports confirmations / model refutations / execution diagnoses / unknowns separately |
| attempt and model provenance | `oqa_reports.py`, `oqa_lc_ir.freeze`, `oqa_lc_arm.py`, `oqa_lc_replay.py`, `oqa_trace.py` | an attempt is `(prediction_dir, item_id, trace_sha256, row_sha256)` with its epoch; a model records `parent_sha256` and `frozen_at_epoch`, both covered by its digest; the learner records `evidence_attempts` (trace key, content digest, collection); `trace_digest` now has ONE definition, in the shared module |
| the in-sample warning | `oqa_lc_report.py::render_action` | tests the ATTEMPT when the model records them; keeps the old id test and its exact wording when it does not, so no historical report's text moves |

**Nothing historical is back-filled.** A field that does not exist on a historical record reads
`"unavailable"`; one that is derived rather than read is labelled (`epoch_source: "parsed from the
prediction directory"`). A digest collision is reported as a collision.

### Why the diagnosis counter needed a definition, not a constant

| kind | what it is | actionable |
|---|---|---|
| `confirmations` | a forecast the observation bore out, or a DISCHARGED obligation | no — the report itself says an in-sample confirmation is not a successful prediction |
| `model_refutations` | an observation that refuted a learned rule | no — it corrects the checker, not the agent |
| `execution_diagnoses` | a VIOLATED or OUTSTANDING obligation: something the actor did or failed to do | **yes** |
| `unknowns` | the records cannot settle it | no |

An Action-Only report still reports 0 actionable diagnoses — but now because the action vocabulary cannot
express one, which is a measured fact about the representation rather than a literal in the code. Arm B's
one violated forecast is now counted as a `model_refutation`, which is what it is.

### The regression that makes "metadata-only" a claim rather than a hope

Two tests regenerate REAL historical reports with the amended code and require the rendered text's sha256
to equal the frozen record's: arm B epoch 3 UID0019 (the campaign's only violated action forecast) and
arm C epoch 3 UID0014 (its only report with violated stage obligations). Both pass. They also assert that
the historical record's `n_distinct_diagnoses` was 0 while the new `diagnosis_counts.model_refutations`
is 1, and that `model_parent_sha256`, `model_frozen_at_epoch` and
`attempt.response_metadata_availability` all read `"unavailable"` for a historical attempt.

---

## 3. Workstream A — the actual HTTP response (rule OQA-HTTP-1)

### The selection, frozen before any request

`oqa_http_probe.py select` read the 16 saved actor call logs (held-out logs excluded by path, and the
exclusion reported), indexed 396 terminal calls of TRAINING episodes — eligible only when the sha256 of a
call's first user message appears in a training `results.jsonl`, so validation and held-out material are
excluded by construction — and applied this rule:

* **EMPTY_1, EMPTY_2**: the two terminal calls with `finish_reason` "stop" and null content, ordered by
  completion tokens descending; ties by (job, user digest, started_utc) ascending.
* **ANSWERED_1**: the terminal call with "stop" whose content contains `<answer>`, by prompt tokens
  ascending.

| label | item | job | finish | completion tokens | prompt tokens | calls in episode |
|---|---|---|---|---|---|---|
| EMPTY_1 | UID0163 | 66273856 (arm A e2) | stop | 4,043 | 9,072 | 5 |
| EMPTY_2 | UID0017 | 66273857 (arm B e2) | stop | 4,028 | 8,336 | 16 |
| ANSWERED_1 | UID0063 | 66273859 (arm A e3) | stop | 729 | 3,086 | 4 |

199 of the 396 terminal training calls were empty; 83 carried an answer tag. Frozen as
`SELECTION.json`, selection digest `25718e4ed2f16c8c`; the tool refuses to overwrite a different one.

### What the provider documents, which is what decided the second setting

`docs.empiriolabs.ai/models/qwen3-5-4b` (read 2026-11-18) lists for this model: **`enable_thinking`
boolean, default TRUE**, "Enables reasoning before answering"; **`thinking_budget` integer, default
4096**, "Max reasoning tokens when thinking is on"; `reasoning_effort` enum default `"medium"`, where
`none` disables thinking; and the warning that "a very low `max_tokens` can be spent entirely on
reasoning". The campaign omitted all three, so it ran with thinking ON at a 4,096-token reasoning budget
inside an 8,000-token `max_tokens` — and the two empty replies the rule picked sit at 4,043 and 4,028
completion tokens, just under that budget.

`skillopt/model/qwen_backend.py` uses `chat_template_kwargs: {"enable_thinking": ...}`, the vLLM/SGLang
extension. That is **not** what this provider documents, so the probe sends the documented top-level
field instead, and verifies on the wire what was transmitted rather than assuming the flag works.

### The request-results table — 23 of 24 attempts

| setting | sends | transmitted extra | `message` keys returned | reasoning chars | completion tokens | finish | outcomes |
|---|---|---|---|---|---|---|---|
| historical (replay) | 9 | *none* | include **`reasoning`** | 105–3,030, every reply | 119–845 | 7 tool_calls, **2 stop** | 2 × content in the body lost in client handling (field `reasoning`); 7 × tool call returned with unread reasoning |
| `enable_thinking: false` | 9 | `{"enable_thinking": false}` | **no `reasoning` key** | 0 | 82–180 | 9 tool_calls | 9 × tool call returned |
| historical + client fallback on | 3 | *none* | include `reasoning` | present | — | 3 tool_calls | 3 × tool call returned with unread reasoning |
| historical + client fallback on | 2 | *none* | include `reasoning` | present | — | 2 tool_calls | 2 × tool call returned with unread reasoning |
| **total** | **23** | | | | 8,956 | | provider charge **$0.004679** |

Every request returned HTTP 200; no request failed. Our ledger's price-table charge for the same 23 calls
is $0.007213, against the provider's $0.004679 — the same cache-read conservatism recorded in the
authorization.

**Verified on the wire, not assumed:** the documented flag was transmitted (`extra_body:
{"enable_thinking": false}`), accepted (200, not a 400), and effective — the `reasoning` key disappears
from the response entirely and completion tokens fall about five-fold.

**What this does not establish.** 9 against 9 is a small sample; the two settings differ in how much the
model generates as well as in the field it uses; a tool call is not an answer, and nothing here ran an
episode to completion. The historical empty terminal reply reproduced 2 of 9 times, so it is real and
intermittent, and that rate is not an estimate of anything campaign-wide.

### One exchange, end to end — EMPTY_2 rep 2, arm B epoch 2's recorded request for UID0017

```
REQUEST  (replayed; omitted settings left omitted)
  model qwen3-5-4b   max_tokens 8000   temperature 0.7   timeout 300
  32 messages: system, user, then 15 x (assistant, tool)
  tools [glob, read, grep]          provenance: reconstructed from the pinned source
  tool_choice "auto"                provenance: reconstructed from the loop
  extra_body null
  omitted, with the documented defaults therefore in force:
      enable_thinking=true, thinking_budget=4096, reasoning_effort="medium", top_p=0.95, top_k=20, seed

HTTP RESPONSE  200, 1,540 bytes
  headers content-type: application/json | server: railway-hikari
          x-ratelimit-limit-requests: 50 | x-ratelimit-remaining-requests: 45
  choices[0].finish_reason = "stop"
  choices[0].message keys  = annotations, audio, content, function_call, reasoning, refusal, role
  choices[0].message.content   = null
  choices[0].message.reasoning = 944 chars:
      "Based on my analysis of the Treasury Bulletin August 1982 document, I found: ...
       - Total bids = $10,102 million
       - Noncash rollover tenders = $478 million
       - Percentage = (478 / 10,102) x 100 = 4.7319...% = 4.73%
       <answer>[10102000000, 4.73]</answer>"
  usage completion_tokens 285, prompt_tokens 8336, cached_tokens 7392,
        billable_prompt_tokens 944, cost_usd 0.00020555

SDK REPRESENTATION
  content_is_none           True
  reasoning_content_is_none True      <- the field the client looked for does not exist
  model_extra keys          ["reasoning"]   <- where the SDK actually surfaces it
  n_tool_calls              0

COMPATIBILITY MESSAGE  (before this follow-up)
  content ""   tool_calls []
  metadata {}                          <- nothing was carried at all
COMPATIBILITY MESSAGE  (after amendment A-1, fallback OFF - the default)
  content ""   tool_calls []
  metadata.finish_reason "stop"   metadata.content_is_null True
  metadata.reasoning_field_name "reasoning"   metadata.reasoning_has_answer_tag True
  metadata.unmodelled_message_fields {"reasoning": {"type": "str", "chars": 944, ...}}
COMPATIBILITY MESSAGE  (fallback ON)
  content = the 944 characters      metadata.content_taken_from "reasoning"

LOOP DECISION
  response = message.content or ""  ->  ""
  no tool_calls, no "<answer>" in ""  ->  branch "neither -> break"
  fail_reason "Model neither produced a tool request nor a final answer"
  WITH THE FALLBACK: "<answer>" is present, _extract_answer returns "[10102000000, 4.73]"
```

### The correction, and what it is not

`reasoning_text()` looks for `reasoning`, `reasoning_content` and `thinking`, including through the SDK's
`model_extra`; the metadata always reports which field was used and whether an answer tag was in it; and
a content fallback uses that text when `content` is empty **and there is no tool call**. It is **off**
unless `SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK` is set, so the frozen campaign's behaviour remains
the default and is not retroactively changed. It is not an empty-response nudge and not an episode retry:
it reads a field the provider already sent.

Proved offline against the real captured shape: with the fallback off the content is `""` and the
termination cause is `empty_reply_content_null`; with it on, `_extract_answer` returns
`[10102000000, 4.73]`; and the fallback never overrides real content or a tool call. The live check spent
5 attempts with the fallback enabled and drew a tool call every time, so it neither confirms nor
contradicts — the offline proof is the deterministic one.

---

## 4. Workstream B — one richer connected contract (amendment B-1, rule OQA-CC-1)

### What was added, and nothing more

Four modules beside the restricted learner, which is untouched: `oqa_cc_ir.py` (the representation),
`oqa_cc_check.py` (extraction, evaluation, the stage link, the prefix guard), `oqa_cc_learn.py` (the
record split, the DeepSeek proposal, deterministic validate/score/compile) and `oqa_cc_lean.py`.

* **Bound entities** `table, header, cell, value, calculation`, each clause evaluated with those names
  bound to extracted objects.
* **An applicability condition**: `conclusion IF conditions`. A cell the conditions do not cover is
  silence, not a counterexample — the distinction the unconditional form could not draw, and the reason
  `row_period_bound` had to be struck from the restricted template set.
* **Three truth values plus CONFLICT**, with an evidence pointer per clause.
* **Versioned repair**: a counterexample narrows the condition and the superseded version is kept. The
  restricted form's only repair was to retire a rule, which is what happened to its single counterexample
  in six rounds.
* **A stage link**: the conclusion witnesses `unit_match`, so withdrawing the binding withdraws the
  witness instead of inventing a violation.
* **Provenance on every clause**, enforced: `TASK`, `GIVEN`, `MATH`, `LEARNED`, `OBS`. `validate` refuses
  a MATH or GIVEN predicate as the conclusion of a learned contract and refuses a LEARNED label on a
  definition. Thousands-to-billions is `MATH`; the learner does not get to have discovered it.

### The permitted records, and a finding in the way of getting them

**Rule v1** — arm C only, epoch-2 traces learn and epoch-3 traces of four other items check — produced
**three usable traces of 24 and an EMPTY checking set.** The binding constraint is not tables (14 of 24
have a table with a stated money scale) but **operands: only 7 of 24 traces contain any number the actor
wrote in a message, and NONE contains a stated conversion factor.** That is Workstream A's finding
arriving here: the actor's visible text never reached the record, so the "evidence use" this relationship
is about is largely unobservable in these traces. Rule v1 and its outcome are kept and recorded.

**Rule v2**, used below, applies the same usability test over arms C and B at epochs 2 and 3 — still all
saved TRAINING material — and gives 8 traces:

| | arm | item | epoch |
|---|---|---|---|
| learning | B | UID0026, UID0028, UID0063, UID0163 | 2 |
| checking | B | UID0044, UID0079, UID0115 | 3 |
| checking | C | UID0014 | 3 |

Four question ids are reserved for checking (UID0014, UID0044, UID0079, UID0115) — more than the two
required — and no checking record comes from an epoch the learner saw. **All of it is reused development
material**: these are the campaign's own training traces, already seen by its analyst and its restricted
learner. UID0014, the 1,000× unit case, landed in the checking set by the rule, not by hand.

### The learned rule, its proposal, and what the deterministic side did with it

One DeepSeek call, 13,484 prompt tokens and 1,489 completion tokens, shown extracted structure only — no
ground truth, no skill, no score. It returned:

```
scale.binds@v1 [active]
    scale_binds(table, cell, scale)                       [LEARNED]
    IF  table_states_scale(table, scale)                  [OBS]
      AND cell_in_table(cell, table)                      [GIVEN]
      AND NOT nearer_scale_between(table, cell)           [OBS]
    WITNESSES stage obligation unit_match

reason: "Across the records, cells inside a table whose stated scale is observed carry that table-level
         scale unless a closer column or row scale intervenes."
withdrawal_reason: "A nearer_scale_between observation, such as a column header or row label with its own
         scale annotation closer to the cell, withdraws the table-level binding."
```

`IR.validate` passed it — well formed, correct provenance labels, a stated withdrawal condition — **and
it then scored UNKNOWN on all 240 learning cells, because `nearer_scale_between` had no evaluator.** A
well-formed but unevaluable hypothesis is worse than a refused one: it looks like a result. Two changes
followed, both recorded: `oqa_cc_check.unevaluable()` is now a second gate wired into the compile step,
and `nearer_scale_between` is implemented from the proposer's **own stated reading** — the cell's own
column header or row label stating a different money scale — with that reading labelled `GIVEN`, because
choosing what a predicate means is an implementation decision and only "its absence implies the binding"
is the learned part.

### Learning, freezing, prefix-only checking, repair, recheck

1. **Learn and freeze.** `CC_MODEL_v1.json`, digest `96c3d1bd9aad6f4d`, `frozen_at_epoch 2`,
   `evidence_attempts` naming the four learning attempts by trace key and content digest. Score
   recomputed at freeze time over the learning records: **180 HOLDS, 60 UNKNOWN, 0 CONFLICT of 240
   cells.** (The score stored with the proposal was 240 UNKNOWN, taken before `nearer_scale_between` had
   an evaluator; the freeze recomputes rather than echoing it.)
2. **Check the later records in order, prefix only.** 4 attempts, **24 committed predictions**. Each
   prediction and its stage requirement are written out before any later step is read, and
   `oqa_cc_check.Revealer` raises if that order is broken.
3. **Repair.** `repaired=False`: **no counterexample exists in the permitted checking records** — no
   checked cell had its conditions met and its conclusion contradicted. Reported, not manufactured. The
   repair machinery is exercised by two clearly-labelled CONSTRUCTED tests
   (`test_a_repair_narrows_the_condition_and_keeps_the_old_version`,
   `test_a_cell_the_condition_does_not_cover_is_silence_not_a_counterexample`), which demonstrate
   software behaviour and nothing about the corpus.
4. **Recheck.** Not reached, because no repair occurred. The code path that would do it re-runs the
   repaired version over the LEARNING records, so a narrowed condition that broke what the hypothesis
   was induced from would show.

### One committed prediction in full — arm C epoch 3, UID0014

```
contract            scale.binds@v1
committed_at_step   45        prefix_steps 45   (nothing past step 45 was read to make this)
operand             "1942"  value 1942.0
  context           "I have the data needed. Let me extract the values:
                     **From treasury_bulletin_1942_07.txt (in thousands of dollars):**
                     - Income and profit taxes, Individual column for year ..."
cell                raw "1942"  row_label "1942"  column null  column_basis UNKNOWN
                    scale "thousands"  source {supplied_page}
binding clauses
  table_states_scale(table,scale)    want True  observed True   [OBS]
      evidence {table_scale: "thousands", origin: {kind: "supplied_page"}}
  cell_in_table(cell,table)          want True  observed True   [GIVEN]
  nearer_scale_between(table,cell)   want False observed False  [OBS]
      evidence {column_unit: null, differing: [],
                reading: "the cell's own column header or row label states a different money scale",
                reading_provenance: "GIVEN (the proposer's stated withdrawal_reason)"}
  -> binding HOLDS, scale thousands
REQUIREMENT committed before reading on
  obligation unit_match, required_exp -6   ("thousands -> billions requires 10^-6")   [MATH]
RESOLUTION after reading the later steps
  state OUTSTANDING, outcome unknown: "the binding requires a factor of 10^-6 and no step states one"
```

### The action-to-stage connection, and the actual Lean result

The binding witnesses `unit_match`. Resolved through it: an UNKNOWN or CONFLICT binding gives UNKNOWN; a
holding binding with no stated factor gives OUTSTANDING; with the required factor, DISCHARGED; with
another factor, VIOLATED and the reason names the mismatch ("wrong by 10^3").

`OqaConnected.lean`, Lean 4.20.0, core only, no Mathlib. **Return code 0 — all statements accepted**, 23
evidence examples encoded, 1 skipped and reported (a holding binding whose asked scale was not a money
scale, so it cannot be encoded in the finite domain).

| statement | what it establishes |
|---|---|
| `unknownBindingNeverViolates` | an UNKNOWN or CONFLICT binding never resolves to VIOLATED — withdrawing a binding may withdraw a discharge, it may never manufacture an accusation |
| `dischargeNeedsTheBinding` | DISCHARGED implies the binding HOLDS; contrapositively, withdraw the binding and the witness goes |
| `violationNeedsAMismatch` | a VIOLATED obligation implies the used exponent differs from the required one |
| `requiredExp thousands billions = -6`, `!= -3`, and the difference is 3 | the definitional arithmetic behind arm C UID0014's "wrong by 1,000x" — labelled MATH, not a discovery |
| 23 × `example ... := by decide` | the Python side's recorded state is what the Lean definitions produce from the same inputs — the two implementations checked against each other over the actual records |

**What Lean does NOT establish**, and this is the whole of it: that the extraction bound the right table,
cell, column or operand (a trusted input); that the learned applicability condition is true of the
Treasury corpus (empirical, and the checking set is four attempts); that a reported violation caused a
wrong answer; and nothing whatever about the actor's reasoning.

### The comparison — saved analysis, historical contract report, new prototype report

The automated verdicts, from a deliberately narrow rule, were: `earlier_or_more_precise` 2 (UID0044,
UID0115), `repeats_what_was_available` 1 (UID0014), `remains_unknown` 1 (UID0079). **On inspection those
verdicts overstate the prototype, and the excerpts are why.**

```
UID0044  (arm B, epoch 3)                                     automated verdict: earlier_or_more_precise
  ORDINARY SkillOpt analysis  "Agent stalled and produced no final <answer>, even when the needed figure
                               was already on the supplied oracle page or an early read."
  HISTORICAL contract report  obligations {DISCHARGED 1, UNKNOWN 1}; no unit_match line
  PROTOTYPE                   operand "1" binding HOLDS -> OUTSTANDING: "the binding requires a factor
                              of 10^0 and no step states one"   (x3, all committed at step 3)
  MY READING: this is not a better diagnosis. A required factor of 10^0 is "no conversion needed", and
  the operands are "1" and "3" - digits the operand reader picked out of prose. The verdict is
  "earlier_or_more_precise" only because the historical report happened to have no unit_match line.

UID0014  (arm C, epoch 3 - the 1,000x case)              automated verdict: repeats_what_was_available
  ORDINARY SkillOpt analysis  "Agent stalled with no tool call and no <answer> block even when the
                               needed rows were already read or present in the oracle page."
  HISTORICAL contract report  obligations {DISCHARGED 32, OUTSTANDING 16, UNKNOWN 43, VIOLATED 11},
                              14 distinct diagnoses, and these lines:
      - **VIOLATED** [unit_match] the value used must be in the asked scale (billions) -- the same for
        10 value(s)
      - **OUTSTANDING** [unit_match] ... -- the same for 13 value(s)
  PROTOTYPE                   operand "1942" binding HOLDS -> OUTSTANDING, required 10^-6
                              operand "07"   binding HOLDS -> OUTSTANDING, required 10^-3
  MY READING: the HISTORICAL report is better here. It bound the real money operands (2,410,259
  thousands, 3,471,122r thousands) and named the 1,000x error with witnesses. The prototype matched
  YEARS - "1942", "1929", "07" - because its operand reader takes any number out of a message and its
  cell matcher accepts a row label read as a numeric cell. It got the REQUIREMENT right (10^-6 for
  thousands -> billions) and the OPERAND wrong.
```

**So: on these records the prototype produced no actionable diagnosis.** All 24 checking outcomes are
`unknown`; no VIOLATED; no counterexample; and its one apparent gain is an artifact of a narrow verdict
rule over a weak operand match. What it did demonstrate is the machinery: a frozen model, a condition
that decides 180 of 240 learning cells, a prediction committed before the observation, a stage link that
cannot turn an unknown into an accusation, versioned repair, and a Lean file that compiles.

---

## 5. Changes, tests, costs, allowance, deviations

### Changed

| file | change |
|---|---|
| `skillopt/model/common.py` | metadata through the adapter; `reasoning_text`; the off-by-default content fallback |
| `skillopt/model/openai_compatible_backend.py` | hands the response to the adapter; logs tool calls, unmodelled fields and the reasoning field; passes the provider cost |
| `skillopt/model/spend_ledger.py` | records `provider_cost_usd`; the guard is unchanged |
| `skillopt/envs/officeqa/rollout.py` | `last_response_metadata` in the local-tools loop; `_no_answer_cause`; the named exits and the new row fields |
| `research/officeqa/oqa_reports.py` | the diagnosis counter's definition; `diagnosis_counts`; `attempt_provenance`; `trace_digest` delegates |
| `research/officeqa/oqa_trace.py` | the one definition of `trace_digest` |
| `research/officeqa/oqa_lc_ir.py`, `oqa_lc_arm.py` | `parent_sha256`, `frozen_at_epoch`, `evidence_attempts` |
| `research/officeqa/oqa_lc_report.py`, `oqa_lc_replay.py` | the in-sample note tests the attempt; the replay carries its identity |
| new | `oqa_http_probe.py`, `oqa_cc_ir.py`, `oqa_cc_check.py`, `oqa_cc_learn.py`, `oqa_cc_run.py`, `oqa_cc_lean.py`, `oqa_probe_body.sh`, `oqa_cc_body.sh`, two test files, two docs |

### Tests — **2,259 passed, 9 skipped, 368 subtests** (job 66341945)

Up from 2,225 before this follow-up. The ones that carry the argument: the two that regenerate real
historical reports byte-for-byte; the four built from the real captured `reasoning` reply; the
unevaluable-condition gate named after the real proposal that exposed it; and the stage-link invariant
that mirrors the first Lean theorem.

### Costs, against Grant 3

| resource | allowance | used | left |
|---|---|---|---|
| EmpirioLabs attempts | 24 | **23** | 1 |
| EmpirioLabs charge | $1.00 | **$0.004679** provider-reported ($0.007213 at our price table) | ~$0.995 |
| DeepSeek attempts | 6 | **3** (one 400 on a wrong model name, one truncated at a 2,000-token cap, one that worked) | 3 |
| DeepSeek charge | $2.00 | **$0.008363** (ledger, peak prices) | ~$1.99 |
| CPU requested | 4 job-hours | **4.00** over 17 jobs, 2 CPUs each | 0 |
| GPUs | 0 | 0 | — |

### Operational deviations, all recorded

1. **The 23 probe sends are NOT in the spend ledger.** The probe calls the SDK directly — it must, to
   capture the raw body from the same exchange — so it bypasses the backend where the ledger lives. The
   allowance was enforced instead by an in-process send cap, a per-job `--max-sends`, and the frozen
   selection; the accounting above comes from the provider's own `cost_usd` in each saved exchange, so
   nothing is unaccounted, but the cumulative actor figures in the authorization must be updated by hand.
2. **The CPU allowance is exactly exhausted** at 4.00 of 4 requested job-hours, so no further job was
   submitted. Two phases were combined into one job to fit.
3. **Two DeepSeek attempts were spent on my own errors**: the backend reads
   `OPTIMIZER_OPENAI_COMPATIBLE_MODEL`, which I had not set, so it defaulted to `gpt-4o-mini` and
   DeepSeek answered 400; and a 2,000-token completion cap was spent entirely on reasoning
   (`finish_reason: "length"`, 8,788 characters of `reasoning_content`, no JSON). Both are counted.
4. **Record-selection rule v1 produced an empty checking set** and was replaced by rule v2 after the
   outcome was seen. Both rules and both outcomes are recorded; v1's outcome is itself a finding.
5. **The evaluator for `nearer_scale_between` was written after `CC_MODEL_v1` was frozen**, from the
   proposer's own stated reading. The frozen model is unchanged, but what it MEANS depends on a checker
   commit written later, and that is recorded here rather than left implicit.
6. **The live check of the client correction never reproduced an empty terminal reply** in 5
   fallback-enabled sends, so it is proved offline against the real captured bodies and not live.
7. The probe's outcome taxonomy was wrong on first use (it labelled a tool-calling turn's unread
   reasoning "content lost in client handling"); 12 saved outcomes were re-derived offline, which cost
   no requests.

---

## 6. Interpretation, kept separate from the evidence above

* **I think the empty-reply finding changes how every number in the campaign should be read.** Scores
  were taken through a client that discarded answers whenever the model put them in `reasoning`, and the
  rate at which that happened is unknown and was not constant. That is a *measurement* defect, not a
  skill effect, and it sits upstream of the gate, the noise floor and the held-out comparison alike.
* **I do not think the `enable_thinking: false` result is a performance finding.** It is a wire-level
  verification that the documented flag is accepted and effective. Whether an actor run with thinking off
  answers more questions is untested here.
* **I think the connected representation is the right shape and the wrong size.** A condition that can be
  narrowed, a conflict that can be reported, and a stage link that cannot invent an accusation are all
  things the restricted form lacks and this prototype has. But its value on these records was nil, and
  the reason is not the representation: it is that the actor's written evidence use is mostly absent from
  the traces, and that the operand-to-cell matching is far weaker than `oqa_lc_chain`'s.
* **What I would do next, in order.** (1) Repeat the existing S0 selection-set measurement with the
  client correction in force — 8 draws, 1,984 attempts, $1.26, ~2.9 core-hours by the measured
  constants — because without it every earlier score is through the broken client. (2) Reuse
  `oqa_lc_chain`'s operand and cell matching in the prototype instead of its own, which is a code change
  with no new data. (3) Only then ask whether a contract layer adds anything, and ask it on traces
  recorded through the corrected client.
* **What I would not do.** Another training campaign, an empty-response nudge, or an episode retry —
  each changes the actor harness and needs its own specified comparison.

### Artifacts

```
<campaign>/followup_20261118/http_probe/SELECTION.json                 frozen selection 25718e4ed2f16c8c
<campaign>/followup_20261118/http_probe/EXCHANGES_*.json               4 files, 23 exchanges, five layers each
<campaign>/followup_20261118/connected_contract/CC_RECORDS.json        rule v1 and its empty checking set
<campaign>/followup_20261118/connected_contract/CC_RECORDS_v2.json     the frozen 4+4 split, 4e34cfc16c8ebd7f
<campaign>/followup_20261118/connected_contract/CC_PROMPT.txt          what the proposer was shown
<campaign>/followup_20261118/connected_contract/PROPOSAL.json          the reply, parsed and compiled
<campaign>/followup_20261118/connected_contract/CC_MODEL_v1.json       frozen 96c3d1bd9aad6f4d
<campaign>/followup_20261118/connected_contract/CC_CHECK_{learning,checking}.json
<campaign>/followup_20261118/connected_contract/CC_REPAIR.json         repaired=False, and why
<campaign>/followup_20261118/connected_contract/CC_COMPARE.json        the three reports side by side
<campaign>/followup_20261118/connected_contract/OqaConnected.lean      + CC_LEAN.json (return code 0)
docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md                    A-1 and B-1
bundle oqa_fu_20261118, jobs 66340342-66341945
```
