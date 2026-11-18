# OfficeQA: answer recovery verified end to end, the extraction repaired, and S0 re-measured

**Rules OQA-REC-2 (Workstream A) and OQA-CC-2 (Workstream B). Amendments A-2 and B-2 in
`docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md`, both written before the behaviour changed.
Branch `officeqa-contracts`, commits `0119b4f` → `7e4a69a`. Every new artifact is under
`<campaign>/followup_20261119/`; the frozen campaign and `followup_20261118/` are untouched.**

---

## 1. The big picture

**(a) Is answer handling verified end to end? YES — and the verification is not a replay.** Twelve
complete episodes ran on three frozen TRAINING questions under two conditions. **All twelve emitted an
answer. All twelve were scored correct by the existing scorer. Under condition A (the historical
generation payload) all six answers came from `message.reasoning` — the field the old client discarded —
and under the old client all six episodes would have ended with "Model neither produced a tool request
nor a final answer".** Re-deciding all 95 captured exchanges offline shows exactly 6 decisions differ:
the 6 terminal replies. Nothing else changed: 68 tool-calling turns were declined by the policy, as they
must be, and 21 ordinary-content turns were untouched.

**(b) What is still unresolved.** Four things, stated plainly:

1. **How often this happened in the campaign is still unknowable.** The historical `reasoning` text was
   never recorded. The corrected-client measurement in §7 gives a rate going FORWARD, not a
   reconstruction of what the campaign's scores would have been.
2. **One new fact makes the damage wider than "lost answers".** In condition A, **0 of 45 captured
   replies had non-null `content`** — not just the terminal ones. The client lost the actor's *entire*
   written reasoning, every turn, which is why `conversation.json` holds empty assistant messages and why
   Workstream B's operand extraction had almost nothing to read (B-1: "only 7 of 24 traces contain any
   number the actor wrote in a message"). The two workstreams' findings are the same defect.
3. **Thinking-off is a different actor, not a fix to the client.** With `enable_thinking: false` the
   `reasoning` key disappears and `content` is populated, so no recovery is needed — but completion
   tokens fall about fivefold and unsupported tool calls rose from 0 to 12. It is not the primary
   configuration and was not made one.
4. **The scorer's formatting tolerance is still unaddressed** (diagnostic packet §3, S0's "39.31%" vs
   "39.31"). Unchanged on purpose: changing the scorer is not authorized here.

**(c) Did the extraction repairs produce a valid diagnostic improvement? NO — and the repair's value is
that it removed a false one.** See §6. The repaired extraction rejects the years the B-1 prototype bound,
and what remains on these records is `extraction_error` and `unknown`, with **zero** findings that
survive inspection. The B-1 report's single "earlier_or_more_precise" verdict is now classified
correctly. The machinery is sounder and its yield on these records is still nil.

**(d) The corrected baseline measurement.** _(§7)_

**(e) What the evidence justifies next.** _(§9)_

---

## 2. State, allocation and accounting

### What was read first

`OFFICEQA_STATE.md` (its RESUME HERE section), `OFFICEQA_IMPLEMENTATION_AMENDMENTS.md` (A-1, B-1),
`OFFICEQA_AUTHORIZATION.md` §1–§6, `OFFICEQA_FOLLOWUP_PACKET.md` §3–§5, the live ledger, and `squeue` /
`sacct`. Nothing was running; the six `e3m-*` jobs are Experiment 3's and were left alone.

### Headroom verified BEFORE anything was submitted

This is a suballocation of the remaining approved ceilings, not an increase (`OFFICEQA_AUTHORIZATION.md`
Grant 4):

| resource | ceiling | spent before | headroom | suballocation | fits |
|---|---|---|---|---|---|
| EmpirioLabs attempts | 30,024 | 10,181 | 19,843 | 5,000 | yes |
| EmpirioLabs charge | $17.00 | $6.8627 | $10.1373 | $4.00 | yes |
| DeepSeek attempts | 266 | 106 | 160 | 4 | yes |
| DeepSeek charge | $10.00 | $3.5100 | $6.4900 | $1.00 | yes |
| CPU requested job-hours | 44 | 31.6 | 12.4 | 8 | yes |
| GPUs | 0 | 0 | — | 0 | — |

The enforced caps are **absolute and computed once** — `target 15,181 / $10.8626`,
`optimizer 109 / $4.4688` — because a cap derived from "what has been spent so far" would hand every job
a fresh allowance. They are passed to every job by `oqa_fu2_submit.sh` and enforced by the same durable
ledger the campaign used.

### The 23 hand-accounted probe sends are now in the ledger, once

`oqa_ledger_backfill.py` replays each saved exchange's recorded usage through the real `reserve` /
`record_usage` under stage `oqa_http_probe_backfill`, and **refuses if any usage record already exists
under that stage**, so a second run is a no-op with an explanation. Applied in job 66346233:

```
rule OQA-REC-2  23 probe send(s); already in ledger 0
  provider $0.004679; this project's price table $0.007213 (cache ignored, as the backend charges)
                                              / $0.004679 if cache were accounted
  the provider reports 126720 cached prompt token(s) over 164662 prompt token(s)
  applied: charged $0.007213 over 23 attempts
```

**A finding fell out of that arithmetic.** The backend computes its charge with
`cached_tokens = prompt_cache_hit_tokens`, which is **DeepSeek's** field name; EmpirioLabs reports cache
hits as `usage.prompt_tokens_details.cached_tokens` (and `cache_read_input_tokens`). So the ledger sees
zero cache for this provider and bills every prompt token. **That is the whole of the campaign's 38%
conservatism** ($5.3159 charged against $3.2768 billed). It is the same class of defect as the
`reasoning` field — a field name read for the wrong provider — and it is left uncorrected on purpose,
because fixing it would *lower* the computed charge and weaken the guard. The backfill charges the same
way so a back-filled attempt and a live one cost the same.

`oqa_http_probe.py` now calls `reserve` before each send and `record_usage` after, so the accounting hole
is closed for any future raw-response probe as well.

---

## 3. Workstream A — the recovery policy, defined narrowly

The policy shipped with the previous follow-up promoted the **whole** reasoning text into `content`.
Amendment A-2 replaces it. `SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK` now names a policy:

| value | policy |
|---|---|
| unset / `0` / `off` / `false` / `no` / `none` | **off, the default** — the frozen campaign's behaviour |
| `1` / `true` / `yes` / `on` / `answer` / `committed_answer` | the **narrow** policy |
| `reasoning` / `reasoning_text` | the superseded permissive promotion, kept only so the OQA-HTTP-1 probe's recorded outcomes stay reproducible |

The narrow policy, in order:

1. **Ordinary content wins.** A reply with text in `content` is handled exactly as before.
2. **A tool call wins.** The loop continues on a tool call and never reads that turn's answer; recovering
   here would invent a final answer out of a turn that asked for evidence.
3. Otherwise the alternate fields (`reasoning`, `reasoning_content`, `thinking`, including through the
   SDK's `model_extra`) must contain **one unambiguous, complete, terminal `<answer>…</answer>` block**.
   *Complete*, because an unclosed tag is what a truncation looks like. *Unambiguous*, because two blocks
   with different payloads are two answers and picking one would be the client answering. *Terminal*,
   because nothing but whitespace may follow the closing tag — which is what separates a commitment from
   a worked example quoted mid-thought.
4. **What is recovered is the block alone.** Not the prose, not the working.
5. Everything else is **named**, and the name reaches the row's `termination_cause`.

### What the ordinary trace and the learner receive — declared, and tested

| consumer | what it gets |
|---|---|
| the episode (`response`) and `conversation.json` | the recovered `<answer>…</answer>` block **only** |
| the contract learner / checker (`oqa_trace.build_packet` reads `conversation.json`) | the same block only — **no reasoning prose is exposed to the learner** |
| the row (`results.jsonl`) | the block, plus `answer_recovered`, `answer_recovery_source`, `answer_recovery_outcome`, `answer_recovery_policy`, `answer_recovery_turns` |
| the audit log (`actor_calls.jsonl`) | the reasoning field's name, length, answer-tag flag, and a 400-character prefix and suffix |
| the raw capture (`raw_capture.jsonl`, new) | the **complete** HTTP body, the SDK view, the compat message — audit only |

`test_the_row_records_where_the_answer_came_from` asserts the saved trace contains exactly
`["<answer>[10102000000, 4.73]</answer>"]` and nothing else, so "the learner does not see the prose" is
checked rather than asserted.

### Generation settings are a separate variable, on purpose

`TARGET_OPENAI_COMPATIBLE_EXTRA_BODY` (JSON) carries serving controls such as
`{"enable_thinking": false}`; invalid JSON **refuses the request** rather than sending one whose controls
cannot be recorded. One variable changes what the model does; the other changes only what the client
reads. Conflating them would make "thinking off" and "read the field we were dropping"
indistinguishable in a result.

### Tested against the real responses and the constructed cases

`tests/test_officeqa_answer_recovery.py`, built on a body with this provider's real key set
(`annotations, audio, content, function_call, reasoning, refusal, role`):

| case | outcome | recovered |
|---|---|---|
| ordinary answer in `content` | `ordinary_content_present` | no |
| alternate-field answer (the real captured reply) | `recovered_committed_answer` | **yes** |
| tool call present, answer in `reasoning` | `tool_call_present_no_recovery` | no |
| two blocks, different payloads | `ambiguous_competing_answers` | no |
| two blocks, identical payloads | `recovered_committed_answer` | yes |
| a block quoted mid-thought | `answer_tag_not_terminal` | no |
| blank reply, nothing in any field | `no_alternate_field_text` | no |
| reasoning prose, no tag | `no_answer_tag_in_alternate_field` | no |
| truncated mid-block (`finish_reason: length`) | `incomplete_answer_tag` | no |
| complete block, completion truncated after it | `recovered_committed_answer_despite_truncation` | yes |
| `<answer></answer>` | `empty_answer_payload` | no |

**Test suite: 2,318 passed, 9 skipped, 368 subtests** (job 66346177), up from 2,259.

### Two defects the tests caught before any paid run

1. **The A-1 row initialisation had landed in the wrong function.** `termination`, `turns_used` and
   `recoveries` were inserted after the first matching block in `rollout.py`, which is inside
   `_run_custom_search_process` — so *every* local-tools rollout raised `NameError` on `recoveries` when
   it built its row. Found by the regression test written for the no-tools path (job 66346074).
2. **The adapter must stay silent without a response.** `compat_message_from_chat_message(message)` with
   no response object must return empty metadata — the compatibility guarantee for every other backend.
   The recovery decision is now attached only when there is a metadata block already, or when a recovery
   changed the content.

---

## 4. Workstream A — the twelve development episodes

### Frozen before any request (job 66346233, plan digest `a93d9eb6f4e58f40`)

The same three questions the OQA-HTTP-1 probe selected, with their **training membership verified
against the frozen split record** rather than assumed:

```
UID0163  frozen sets: ['train']     treasury_bulletin_1981_12.txt  58b64f32
UID0017  frozen sets: ['train']     treasury_bulletin_1982_08.txt  352cb596
UID0063  frozen sets: ['train']     treasury_bulletin_1990_06.txt  2db05ce7
skill 9d377d1c2906 (S0)   config 9ff823b2043a   settings fingerprint d58cf3b59bbc2928
tools glob, read, grep    max_tool_turns 24   max_completion_tokens 16384 (the backend caps at 8000)
```

None of the three is in the validation or held-out set. The plan records the documents by digest, the
question digests (not the answers), both conditions' exact serving controls, and the tool schemas'
digest.

### The two conditions differ in one documented control and nothing else

| | A | B |
|---|---|---|
| generation payload | the historical one; thinking controls **omitted**, so their documented defaults are in force (`enable_thinking` true, `thinking_budget` 4096, `reasoning_effort` "medium") | identical **plus** `{"enable_thinking": false}` |
| answer recovery | narrow policy on | narrow policy on |
| skill, temperature (0.7, no seed), token limit, tools, corpus, turn budget, timeout | identical | identical |
| contract feedback | none | none |

### Results — 12 episodes (job 66346256)

```
run  exch  prompt  compl   provider$   recovered  unsupported  truncated  non-null content  elapsed
A1     21  100613  15770  $0.003629        3           0           0            0 of 21     2:36
A2     24  148185  26937  $0.005321        3           0           0            0 of 24     4:16
B1     22  116515   5015  $0.003111        0           1           0            9 of 22     1:02
B2     28  176270   4252  $0.004286        0          11           0           12 of 28     1:00
```

| run | item | answer? | where from | turns | exit | scorer |
|---|---|---|---|---|---|---|
| A1 | UID0017 | yes | **recovered: `reasoning`** | 12 | `answer_recovered_from_reasoning` | correct |
| A1 | UID0063 | yes | **recovered: `reasoning`** | 4 | `answer_recovered_from_reasoning` | correct |
| A1 | UID0163 | yes | **recovered: `reasoning`** | 5 | `answer_recovered_from_reasoning` | correct |
| A2 | UID0017 | yes | **recovered: `reasoning`** | 11 | `answer_recovered_from_reasoning` | correct |
| A2 | UID0063 | yes | **recovered: `reasoning`** | 2 | `answer_recovered_from_reasoning` | correct |
| A2 | UID0163 | yes | **recovered: `reasoning`** | 11 | `answer_recovered_from_reasoning` | correct |
| B1 | UID0017 | yes | `content` | 11 | `answer_emitted` | correct |
| B1 | UID0063 | yes | `content` | 4 | `answer_emitted` | correct |
| B1 | UID0163 | yes | `content` | 7 | `answer_emitted` | correct |
| B2 | UID0017 | yes | `content` | 15 | `answer_emitted` | correct |
| B2 | UID0063 | yes | `content` | 7 | `answer_emitted` | correct |
| B2 | UID0163 | yes | `content` | 6 | `answer_emitted` | correct |

* **Usable committed answers lost: 0.** The policy declined nothing that had an answer tag
  (`declined_though_a_tag_was_present = 0` in all four runs), and every exchange was attributed to an
  episode by its user-prompt digest (`exchanges_not_attributed_to_an_episode = 0`).
* **Provider failures: 0. Truncations at the cap: 0. Turn-budget exhaustions: 0.**
* **Unsupported tool calls: 0 under A, 12 under B.** The loop continues past them, as before.
* Cost: **$0.016347** provider-reported over **95 attempts**; elapsed 8:55 for all twelve.

**These are functional development checks, not a comparison of thinking modes**, and the preferred
condition was not chosen from these scores. Two of the three questions were selected *because* they had
produced empty replies, so 12-for-12 says the handling works — it does not say the actor answers 100% of
questions. §7 is the measurement.

### One exchange, all four layers (A1 / UID0017, turn 12)

```
REQUEST (transmitted)
  model qwen3-5-4b   max_tokens 8000   temperature 0.7   tool_choice auto
  tools [glob, read, grep]        extra_body None          24 messages
  first_user_sha256 094f74efb2f14556   serialized_sha256 8cfbdc2923c99d77

RAW HTTP  200, 9,367 bytes, body sha256 c5edebd5ab686aad
  headers  content-type: application/json | server: railway-hikari |
           x-ratelimit-limit-requests: 50 | x-ratelimit-remaining-requests: 46
  id chatcmpl-89d28e790dda8e39   model qwen3-5-4b   finish_reason "stop"
  message keys  [annotations, audio, content, function_call, reasoning, refusal, role]
  content       null
  reasoning     8,580 chars, ending:
      "...non-domestic investors is $478,000,000.
       Percent = (478 / 10102) * 100 = 4.7316...% = 4.73%
       I'm confident in this answer.
       <answer>[10102000000, 4.73]</answer>"
  usage  prompt 8,671 (7,392 cached)  completion 2,333  cost_usd 0.00036231

SDK VIEW
  content_is_null True   content_chars 0   reasoning_content_is_null True
  model_extra_keys ['reasoning']          <- the SDK exposes it ONLY here
  metadata  reasoning_field_name "reasoning"  reasoning_has_answer_tag True
            reasoning_content_chars 8580      recovery_policy "committed_answer"

COMPATIBILITY MESSAGE
  content  '<answer>[10102000000, 4.73]</answer>'   (36 chars; the block ALONE)
  tool_calls []
  answer_recovery  outcome recovered_committed_answer, source_field reasoning,
                   blocks 1, open 1, close 1, terminal True, distinct_payloads 1

EPISODE DECISION (the row, joined by response_id)
  termination_cause  answer_recovered_from_reasoning
  predicted_answer   [10102000000, 4.73]      hard 1   soft 1.0
```

### The old adapter on the same bodies, offline

`oqa_recovery.py replay` re-decides every captured body under the old client and under the policy. Over
the episodes' 95 exchanges:

```
rule OQA-REC-2  95 saved exchange(s); 6 client decision(s) differ;
                6 committed answer(s) the old client discarded
    21  ordinary_content_present
     6  recovered_committed_answer
    68  tool_call_present_no_recovery
  reasoning: 8580ch -> '<answer>[10102000000, 4.73]</answer>'
  reasoning:10349ch -> '<answer>4226</answer>'
  reasoning:  794ch -> '<answer>-75</answer>'
  reasoning: 4252ch -> '<answer>[10102000000, 4.73]</answer>'
  reasoning: 3646ch -> '<answer>4226</answer>'
  reasoning:  667ch -> '<answer>-75</answer>'
```

And over the 23 bodies saved by the earlier probe (job 66346233): 3 ordinary content, **2** recovered,
18 tool-call turns — the same two the probe found, and nothing more. **Each record carries the caveat
that it does not reconstruct the rest of an episode after an earlier termination**, and a test asserts
that the caveat is present.

### Condition B on the wire

```
transmitted extra_body  {"enable_thinking": false}      http 200
message keys  [annotations, audio, content, function_call, refusal, role]   <- no `reasoning`
content       "Now I have the data I need from Table UST-1: For **October 1981** (Oct. row): ..."
completion_tokens 238          recovery outcome ordinary_content_present
```

Transmitted, accepted, **and effective**: the key disappears and completion tokens fall from 15,770 /
26,937 (A) to 5,015 / 4,252 (B) over comparable work.

---

## 5. Workstream B — what was repaired in the extraction, and why

The B-1 prototype's own report recorded that its single apparent gain was an artifact: on arm C's
UID0014 — the campaign's real 1,000× unit error — it bound the **years** `1942` and `07` while the
historical contract report bound the real figures (2,410,259 and 3,471,122r thousands). It had the
requirement right and the operand wrong, which is the worst combination, because the report then looks
like a finding.

The cause was not the representation. It was that the prototype carried **its own** operand reader and
cell matcher, both weaker than the ones `oqa_lc_chain` / `oqa_lc_cells` already had. Amendment B-2
therefore adds `research/officeqa/oqa_cc_extract.py`, which **reuses those modules and audits them**.
The reused modules are not modified — the frozen campaign's `oqa_lc_*` behaviour must stay
byte-identical — so every correction lives in the layer above, and two tests pin that boundary.

### The seven audit findings, each with a test

| # | what was wrong | where | correction |
|---|---|---|---|
| 1 | `cells_of` yields column 0, which is the row **label**, so a year used as a label is offered as a data cell | the reused reader | the labelled column is dropped, with the reason recorded and whether the label parses as a period |
| 2 | any number in a message was an operand | the prototype | years, dates, month fragments, list numbers, footnote markers, percentages and bare prose digits are rejected **by name** |
| 2b | — | — | **four digits is not a reason on its own**: with an adjacent currency or scale marker it is money, with date context it is a date, and with neither it is **AMBIGUOUS** and not used |
| 3 | the first value match won | the prototype | several matches that disagree about scale, column or row are an **ambiguous match**, and the contract stays silent |
| 4 | the table was any table sharing the cell's scale | the prototype | a cell names its own table by `(source, table index)` and is bound to that one |
| 5 | `nearer_scale_between` was **False** whenever the column field was empty | the prototype | tri-state PRESENT / ABSENT / **UNKNOWN**; absence is asserted only when the scope was observed |
| 6 | `cell_in_table` was `(A and B) or True` — true for every input, so the clause asserted nothing | the prototype | it compares the cell's table key with the table being evaluated |
| 7 | one question: "was there a factor?" | the prototype | three: was a factor stated, was it the right one, and **was the converted value actually used** |

Finding 5 is the one the instruction names, and the test that pins it is parameterised over all four ways
a scope can fail to be observed (no column, empty column, unbound column basis, empty row label):

```python
def test_a_missing_or_unbound_scope_makes_the_nearer_unit_unknown_not_false(cell):
    got = X.nearer_scale(dict(cell, table_key="k"), {"scale": "thousands", "key": "k"})
    assert got["state"] == X.UNKNOWN
    assert "absence cannot be asserted" in got["why"]
```

And the operand reader is tested on the **actual text** of the trace that produced the false finding:

```
"**From treasury_bulletin_1942_07.txt (in thousands of dollars):**
 - Income and profit taxes, Individual column for year 1942: 2,410,259
 - The 1929 figure is 3,471,122r"

  1942       REJECTED  date_fragment
  07         REJECTED  date_fragment
  1942       REJECTED  year_in_date_context
  2,410,259  MONETARY  thousands_grouping          <- kept
  1929       REJECTED  year_in_date_context
  3,471,122  MONETARY  thousands_grouping          <- kept
```

A first version of that classifier marked the trailing `1942` as money, because the line mentions
"dollars" within 60 characters. The rule now distinguishes a **tight** marker (a `$`, or a scale word or
"dollars" within 14 characters) from a **loose** one anywhere in the window, and a year-shaped number is
never money on a loose marker alone.

### The stage link gained three states it could not express

* **REOPENED** — a conflict withdraws the binding and reopens the obligation, and the text says so:
  "nothing here says the actor did anything wrong". B-1 flattened this into UNKNOWN.
* **No conversion required** — when the bound scale already equals the asked scale the required factor is
  10⁰. B-1 reported that as OUTSTANDING, which is exactly the false alarm it printed on UID0044
  ("requires a factor of 10^0 and no step states one"). Applying a factor to an already-compatible value
  is still reported as VIOLATED.
* **Stated but not carried through** — a correct factor whose result appears in no later step is
  OUTSTANDING, not DISCHARGED.

Only a **holding** binding may witness an obligation, spelled out explicitly so the Python and the Lean
encoding agree even on the unreachable cases.

### The comparison verdict can no longer say "more precise" on its own

`_verdict` now requires `_well_bound`: a MONETARY operand, an unambiguous cell match, a data cell with a
bound header, and a holding binding. A finding that fails any of those is `false_alarm`, and every
comparison carries the witnesses — operand, verdict, reason, cell, column, table key, binding, conversion
— so the label can be checked rather than taken. Each checking record is compared against **its own
arm's** historical report (the v2 split spans arms B and C).

### The complete semantic bundle, frozen before any record was re-read

The model digest fixes the hypothesis as a sentence, not what the sentence **means** — B-1 recorded that
`nearer_scale_between` got its evaluator *after* the model was frozen. `oqa_cc_bundle.py` therefore
freezes, together:

```
rule OQA-CC-2  bundle 7809287366d50598  label v2          (job 66346233, 22:41:28)
  hypothesis  96c3d1bd9aad6f4d  frozen_at_epoch 2  parent None
    scale.binds@v1 [active]  unevaluable=none
  vocabulary  58b4d330139032e5   11 predicates, 5 evaluable
  code        8 modules, 9 semantic functions (evaluate_binding, stage_link, check_attempt, render,
              nearer_scale, classify_number, data_cells, conversion, match_operand)
  prompt      41c1069535862b69
  manifest    learning 4, checking 4, records 4e34cfc16c8ebd7f
```

It includes `oqa_lc_cells` and `oqa_lc_chain` by digest although they are frozen: being frozen is a claim
to check, not a reason to omit them. `verify` recomputes every component and names the ones that moved.
**The bundle was frozen at 22:41 — before the checking records were re-read (22:44) and before the new
episode traces existed (22:42–22:51).**

---

## 6. Workstream B — re-scoring, re-checking, and what it actually produced

_(filled in from job 66346xxx below)_

---

## 7. The corrected-client S0 reproducibility measurement

_(filled in from jobs 66346539 / 66346540 below)_

---

## 8. Costs, allowance and deviations

_(filled in below)_

---

## 9. What the evidence justifies next

_(filled in below)_
