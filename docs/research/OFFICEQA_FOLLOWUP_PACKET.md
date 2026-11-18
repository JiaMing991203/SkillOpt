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
