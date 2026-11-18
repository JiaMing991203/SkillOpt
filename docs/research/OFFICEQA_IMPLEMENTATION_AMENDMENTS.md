# OfficeQA implementation amendments

The accepted design is **learned action conditions and effects connected to explicit stage requirements
and results, with prefix-only checking, uncertainty, counterexample-driven repair, and Lean verification
of the encoded consequences.** The deterministic 20-effect learner shipped in the 2026-11-17 campaign is a
**restricted implementation** of that design, not the design itself. This file records every amendment to
the implementation's representation or checker semantics, with its reason, before the change is made, so
that the agreed design and the historical implementation stay distinguishable from whatever runs next.

Nothing here changes the frozen 2026-11-17 campaign. Its snapshots, selected skills, gate decisions,
reports and evaluation artifacts are unchanged; regenerated diagnostics are written to a separate
follow-up directory and are labelled as regenerated.

---

## A-1 — 2026-11-18. Observability, and one reported field whose meaning changes

**Reason.** OQA-FOR-1 established that three recorded fields cannot support a conclusion: the row's
`last_finish_reason` is empty for every rollout in the campaign, the compatibility adapter drops
`finish_reason`, the usage block and any unmodelled message field before the environment can see them,
and the Action-Only reports' `n_distinct_diagnoses` is a hard-coded `0`.

**Changed — additive, no behaviour change.**
* `skillopt/model/common.py::compat_message_from_chat_message` accepts the response object and fills
  `CompatAssistantMessage.metadata` with the finish reason, the response and model identifiers, the full
  usage block, the provider's own cost when it reports one, the tool calls it already models, and any
  message field the adapter does NOT model. Called without a response it behaves exactly as before, so
  the other backends are untouched.
* `skillopt/envs/officeqa/rollout.py`'s local-tools branch assigns `last_response_metadata` on every
  turn, as the three other branches already did, and records an explicit termination cause.
* `skillopt/model/spend_ledger.py::record_usage` accepts and stores the provider's reported cost beside
  the computed charge. **The guard still enforces the computed charge**; the provider's figure is
  recorded, never substituted.
* The backend's call log additionally records the reply's tool calls and unmodelled message fields.

**Changed — SEMANTICS, and this is the amendment proper.** `research/officeqa/oqa_reports.py` wrote
`"n_distinct_diagnoses": len(REPORT.group(chain["ledger"])) if condition != LC_ACTION else 0`. That field
now means **the number of distinct ACTIONABLE diagnoses, defined for both conditions**, and the stage
group count moves to its own field `n_stage_diagnosis_groups`. A new `diagnosis_counts` block reports
four kinds separately, because a confirmed forecast is not a diagnosis:

| kind | what it is | counted as actionable |
|---|---|---|
| `confirmations` | a forecast the observation bore out, or a DISCHARGED obligation | no |
| `model_refutations` | an observation that refuted a learned rule — the checker's own model was wrong | no |
| `execution_diagnoses` | something the ACTOR did or failed to do: a VIOLATED or OUTSTANDING obligation | yes |
| `unknowns` | the records cannot settle it | no |

`n_distinct_diagnoses` is therefore `execution_diagnoses`. Under the old constant every Action-Only report
read 0; under the new definition an Action-Only report still reads 0 **because the action vocabulary
cannot express an execution diagnosis**, which is now a measured fact about the representation rather than
a literal in the code, and its refutations and confirmations are reported separately.

**Not changed.** The rendered report TEXT, the obligation states, the grouping rule, the gate, the scorer,
the analyst prompts and every historical artifact. A regression test asserts the rendered text is
byte-identical before and after.

**Provenance of an attempt.** A question id alone is insufficient (the same 24 ids recur in all four
epochs) and a digest of a lossy trace alone is insufficient (two different interactions saved one
identical trace — arm C's UID0019). An attempt is now identified by the tuple
`(prediction_dir, item_id, trace_sha256, row_sha256)` with `epoch`, and a model by
`(sha256, parent_sha256, frozen_at_epoch, version)`. Where a historical field does not exist it is
recorded as `"unavailable"`, and where it is derived rather than read it is recorded as
`"reconstructed"`. Nothing is back-filled with a guess.

---

## B-1 — 2026-11-18. A connected action-to-stage contract, as a prototype beside the restricted learner

**Reason.** The restricted learner can express exactly 20 unconditional single-atom `effect` contracts
over one tool observation's text. It cannot express the relationship the campaign most needed: *under what
observable conditions does a table header or unit annotation apply to a particular cell, and when must
that binding be withdrawn or revised?* — nor connect such a binding to the calculation stage that consumes
the value.

**Added, as a SEPARATE module set (`research/officeqa/oqa_cc_*.py`), leaving `oqa_lc_*` untouched.**
* Bound entities: `table`, `header`, `cell`, `value`, `calculation`, each with an evidence pointer.
* A contract form with an explicit **applicability condition** over bound entities and an **effect**, so
  that a claim can be true of some cells and silent about others instead of being refuted by them.
* Three truth values — holds / violated / unknown — with evidence pointers for each, and an explicit
  record of **conflicting** evidence rather than a silent majority vote.
* Versioned repairs: a counterexample produces a new version with a narrowed condition and a recorded
  reason; the old version is kept and marked superseded.
* A declared connection from an action-level binding to a stage obligation, so that a withdrawn binding
  withdraws the obligation's witness.

**Provenance labels are part of the representation,** because the audit's central complaint was that
ordinary arithmetic was presented as a discovery. Every clause carries one of: `TASK` (read from the
question sentence), `GIVEN` (manually supplied structure or representation), `MATH` (a definition, e.g.
thousands→billions is 1e-6), `LEARNED` (an empirical hypothesis from the permitted records), `OBS` (a
returned observation). A `MATH` or `GIVEN` clause may never be reported as learned.

**Boundaries.** Prefix-only checking: a prediction is committed before the observation it concerns is
read. Learning records and checking records are disjoint and fixed before any learner call. DeepSeek
proposes structured hypotheses; deterministic code validates, scores and compiles them, and no proposal
is accepted on the proposer's word. Lean verifies the encoded consequences only — what it checks and what
remains dependent on extraction, assumptions or empirical validity is stated with the result.

**Not changed.** The `oqa_lc_*` modules, the campaign's reports, and the frozen vocabulary
`oqa.lc.vocab.v1`. The prototype is not wired into any arm and does not run in any training loop.

---

## A-2 — 2026-11-18. A narrow answer-recovery policy, and two things recorded about every request

**Reason.** A-1 and rule OQA-HTTP-1 established that this provider returns the model's text in
`choices[0].message.reasoning` and that the client read `content`, so 331 of 528 selection observations
ended as "no answer". The correction shipped with the follow-up promoted the WHOLE reasoning text into
`content`. That is too broad: reasoning prose, a worked example quoted mid-thought, or one of two
competing answer blocks would all become the episode's final answer. A measurement defect would be
replaced by a fabrication.

**Changed — SEMANTICS of the recovery, and this is the amendment proper.**
`SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK` now selects a POLICY rather than a boolean:

| value | policy |
|---|---|
| unset, `0`, `off`, `false`, `no`, `none` | **off — the default.** The frozen campaign's behaviour. |
| `1`, `true`, `yes`, `on`, `answer`, `committed_answer` | **the narrow policy** (below) |
| `reasoning`, `reasoning_text` | the superseded permissive promotion, kept only so the OQA-HTTP-1 probe's recorded outcomes stay reproducible |

The narrow policy, in `skillopt/model/common.py` (`committed_answer`, `recover_from_alternate_field`):

* Ordinary `content` wins, and a tool call wins. Neither is ever overridden.
* Otherwise the alternate fields (`reasoning`, `reasoning_content`, `thinking`, including through the
  SDK's `model_extra`) are inspected for **one unambiguous, complete, terminal `<answer>...</answer>`
  block**: complete because an unclosed tag is what a truncation looks like; unambiguous because two
  blocks with different payloads are two answers and choosing one would be the client answering;
  terminal because nothing but whitespace may follow the closing tag, which is what separates a
  commitment from a quoted example.
* **What is recovered is the block alone** — `<answer>…</answer>`, not the prose around it. That is
  exactly what the environment sees, what `conversation.json` stores, and therefore all the contract
  learner receives. The full provider fields stay in the audit artifacts (the call log's reasoning
  prefix/suffix and the raw capture's complete body) and nowhere else.
* Every other case is **named**: `no_answer_tag_in_alternate_field`, `no_alternate_field_text`,
  `incomplete_answer_tag`, `ambiguous_competing_answers`, `answer_tag_not_terminal`,
  `empty_answer_payload`, `tool_call_present_no_recovery`, `ordinary_content_present`,
  `recovery_disabled`, and `recovered_committed_answer_despite_truncation` when the block is intact but
  the completion stopped at the cap. A declined reply cannot be mistaken for a recovered one, and the
  name reaches `termination_cause`.

**Changed — additive, off unless set.**
* `<ROLE>_OPENAI_COMPATIBLE_EXTRA_BODY` (JSON object) is forwarded as `extra_body`, which is how a
  documented serving control such as `enable_thinking: false` is transmitted. Invalid JSON or a
  non-object **refuses the request** rather than sending one whose controls cannot be recorded.
  **Generation settings and answer-field handling are deliberately separate variables**: one changes what
  the model does, the other only what the client reads.
* `<ROLE>_OPENAI_COMPATIBLE_RAW_CAPTURE` names a JSONL file. When set, the request goes through
  `with_raw_response`, so the complete HTTP body and the object the SDK parsed come from the SAME
  exchange, and one record carries four layers: the transmitted request (with the digests of the system
  and first user message, which identify the episode), the raw body, the SDK view, and the compatibility
  message. The episode's own decision is NOT written there — it is in the rollout row, joined by
  `response_id` — because writing it there would mean guessing it. Response headers are filtered to the
  same allow-list the probe uses, so no credential can reach an artifact.
* The row carries `answer_recovered`, `answer_recovery_source`, `answer_recovery_outcome`,
  `answer_recovery_policy` and `answer_recovery_turns`. `answer_recovered` is about PROVENANCE only;
  `hard` and `soft` remain the scorer's, computed from the same answer either way.
* The call log records the recovery decision and the transmitted `extra_body`.
* `research/officeqa/oqa_recovery.py` re-decides any saved body offline under the old adapter and under
  the policy, and `oqa_ledger_backfill.py` records the probe's 23 earlier sends in the shared ledger
  exactly once.

**Fixed — a defect A-1 introduced.** `rollout.py` read `termination` when building every row and assigned
it only in the local-tools branch, so a custom-search, azure-search, no-tools or codex OfficeQA rollout
raised `NameError` after finishing its work. It is initialised to `"unrecorded"`, which is what those
branches honestly report. A regression test covers the no-tools path.

**Not changed.** The scorer, the gate, the loop's control flow, the analyst prompts, the frozen
campaign's artifacts, and the plain-text backend path — `chat_optimizer` still returns `message.content`,
so no optimizer call is affected. **No empty-response nudge and no episode retry**: the policy reads a
reply the provider already sent.

---

## B-2 — 2026-11-18. The prototype reuses the stronger extraction, and uncertainty replaces a false negative

**Reason.** B-1's own report recorded that its one apparent gain was an artifact: on arm C's UID0014, the
campaign's real 1,000× unit error, the prototype bound the YEARS `1942` and `07` while the historical
contract report bound the real figures (2,410,259 and 3,471,122r thousands). It got the requirement right
and the operand wrong. The cause was not the representation but the extraction: the prototype carried its
own operand reader and cell matcher, both weaker than `oqa_lc_chain` / `oqa_lc_cells`.

**Added — `research/officeqa/oqa_cc_extract.py`, which reuses those modules and corrects seven things
above them.** The reused modules are **not modified**: the frozen campaign's `oqa_lc_*` behaviour must
stay byte-identical, so every correction lives in the layer above, and two tests pin that boundary.

| # | audit finding against the reused or local machinery | correction |
|---|---|---|
| 1 | `cells_of` yields column 0, which is the row LABEL, so a year used as a label is offered as a data cell | labelled column dropped, with the reason and whether the label is a period |
| 2 | any number in a message was an operand | years, dates, month fragments, list numbers, footnote markers, percentages and bare prose digits are rejected **by name**; four digits is not a reason on its own — with an adjacent currency or scale marker it is money, with date context it is a date, and with neither it is **AMBIGUOUS** and not used |
| 3 | the first value match won | several matches that disagree about scale, column or row are an **ambiguous match** and the contract stays silent |
| 4 | the table was any table sharing the cell's scale | a cell names its own table by `(source, table index)` and is bound to that one |
| 5 | `nearer_scale_between` was **False** whenever the column field was empty | tri-state: PRESENT / ABSENT / **UNKNOWN**, and absence is asserted only when the scope was observed. An unobserved scope cannot witness an absence |
| 6 | `cell_in_table` was `(A and B) or True`, which is True for every input | it compares the cell's table key with the table being evaluated |
| 7 | one question — "was there a factor?" | three: was a factor stated, was it the right one, and **was the converted value actually used** |

**Changed — SEMANTICS of the stage link** (`oqa_cc_check.stage_link`), with three states B-1 could not
express:

* **REOPENED.** A conflict withdraws the binding and reopens the obligation, and the text says so:
  "nothing here says the actor did anything wrong". B-1 flattened this into UNKNOWN.
* **No conversion required.** When the bound scale already equals the asked scale the required factor is
  10^0: no factor is needed and an explicit multiplication by one is not required. B-1 reported that as
  OUTSTANDING, which is the false alarm it printed on UID0044. Applying a factor to an
  already-compatible value is still reported as VIOLATED.
* **Stated but not carried through.** A correct factor whose result appears in no later step is
  OUTSTANDING, not DISCHARGED.

Only a HOLDING binding may witness an obligation, spelled out explicitly so that the Python and the Lean
encoding agree on the unreachable cases too.

**Changed — the comparison verdict.** `oqa_cc_run._verdict` could return `earlier_or_more_precise` from a
finding resting on a year. A verdict now requires an inspection of the actual operand and evidence
binding (`_well_bound`: a MONETARY operand, an unambiguous cell match, a data cell with a bound header,
and a holding binding); a finding that fails it is `false_alarm`, and every comparison carries the
witnesses — operand, verdict, reason, cell, column, table key, binding, conversion — so the label can be
checked rather than taken. Each checking record is compared against **its own arm's** historical report.

**Added — the complete semantic bundle** (`research/officeqa/oqa_cc_bundle.py`). The model digest fixes
the hypothesis as a sentence, not what the sentence MEANS: B-1 recorded that `nearer_scale_between` got
its evaluator after the model was frozen. A bundle therefore freezes, together and before any later
record is read: the hypothesis, the vocabulary and predicate provenance, the source digests of every
module and of the nine functions that are the semantics, the stage definitions, the renderer, the
learner prompt, the learning manifest (item, epoch, trace digest) and the configuration. `verify`
recomputes each component and names the ones that moved.

**Changed — the Lean file** (`oqa_cc_lean.py`, `OqaConnected.lean`): `ObState` gains `reopened`, the
resolution takes the conversion verdict and whether the converted value was carried through, and the new
consequences are stated — a withdrawal reopens, a missing factor never accuses, an already-compatible
unit needs no factor, a correct factor not carried through is outstanding, and two statements quantified
over every conversion verdict and carry state.

**Not changed.** `oqa_lc_*`, the campaign's reports, the frozen vocabulary `oqa.lc.vocab.v1`, the gate
and the scorer. The prototype is still wired into no arm and runs in no training loop. **Earlier recorded
results stand as recorded**: the B-1 numbers were taken under the B-1 semantics, and this file is why the
two sets of numbers differ.
