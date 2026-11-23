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

---

## A-3 — 2026-11-18. The eight-pass aggregation, and a latent defect in the summariser

**Recorded before the audit's analysis was run.** Rule **OQA-AUD-1**, tool
`research/officeqa/oqa_audit_s0.py`, artifacts under `<campaign>/audit_20261118/`. **No measurement
changes.** The 192 rows of the corrected-client S0 measurement are untouched, and this amendment changes
only how they are aggregated and what the aggregation refuses to do.

**Why.** `OFFICEQA_RECOVERY_PACKET.md` section 7 published four aggregates that cannot all describe one
192-episode measurement: a per-pass table summing to 170 emitted answers, a per-question table summing
to the same 170, a termination table over "179 completed question-rollouts" counting 158 answers, and a
sentence claiming 20 of 24 questions were answered in every pass although one pass emitted 19.

**Added — one authoritative manifest, and summaries that are functions of it.** The intended grid is
DECLARED (the frozen split record's 24 validation ids x the eight pass labels), never derived from the
rows, because a grid derived from the rows cannot notice a missing row. Each record carries its source
file and row index, a content digest, the job that produced it — resolved from the row's own final
response id matched against the call log, since the two S0 jobs ran concurrently and wall clock alone
cannot separate them — the actor-visible prompt digests, the settings fingerprint, and an explicit
uncertainty list. A duplicate is never resolved by score.

**Added — consistency assertions, so a disagreement fails instead of being narrated.** Every pass has
the intended 24 unique ids; every question has one observation per pass; termination categories sum to
the episode count; answer-source categories sum to emitted answers; per-pass totals equal
question-level totals; and "always answered" may not exceed the smallest per-pass emission count.

**Changed — a missing observation is never evidence.** `oqa_s0repeat.summarise` computes
`questions_always_correct` as `times_correct == passes_seen`, so a question observed in seven passes and
correct in all seven counts as "always correct". That is latent in the published artifact, because all
eight passes are in fact complete; it was live in the intermediate aggregation that produced the
published termination table. The audit's classifier reports `always` / `never` / `sometimes` only over a
COMPLETE row of observations and lists anything short of that as `undetermined`.

**Added — abandoned attempts and unresolved requests, kept apart from episode outcomes.** A killed job's
in-flight rollouts are re-run from scratch by the resuming job, so their HTTP attempts back no record; a
request whose reply never arrived has no call-log record at all and exists only as the gap between the
ledger's `attempt` and `usage` counts for that process. Neither is a provider error and neither is an
episode outcome.

**Not changed.** `oqa_s0repeat.py` itself, the measurement's artifacts, the frozen campaign, the gate
and the scorer. The published figures stand as published and this file is why the corrected ones differ.

---

## B-3 — 2026-11-18. Three extraction repairs against the extractor's own stated semantics

**Recorded before the change.** Rule **OQA-AUD-2**, tool `research/officeqa/oqa_audit_bind.py`. Each
repair is a case where the code contradicts what its own docstring says it does, each has a witness from
a real record, and each is reported with a before/after. The B-2 artifacts (`CC_BUNDLE_v3`, the v3 check
documents, `CC_COMPARE`, `CC_LEAN`) are preserved; the repaired semantics are frozen under a NEW label
and re-run beside them.

**B-3.1 — a comma between list items was read as a thousands separator.** `_NUM` allowed a match to end
in a comma, so in "from odd years (2011, 2013, 2015, 2017, 2019)" the token is `2011,`, `grouped` is
true, and `classify_number` returns MONETARY with reason `thousands_grouping` — before the year tests
are ever reached. Amendment B-2 claims years are "rejected BY NAME"; four of UID0079's operands and
eleven of UID0014's are years admitted this way, 26 across all audited records. The repair: a number may
not end in a comma, and grouping means a real `d,ddd` group.

**B-3.2 — the record's own flat headers were never offered as references.**
`oqa_lc_cells.cells_of` takes a `references` argument documented as "flat-form tables from elsewhere in
the same record (for example the tool observations) that may pair with a split-form table here", and
`oqa_cc_extract.data_cells` calls it once per text without that argument. So the oracle page's SPLIT
rendering of a table could not be adjudicated by the FLAT rendering of the same table that the actor's
own `read` had already returned. Witness: UID0014's `8,006,880` matched two cells, identical in value,
row and scale, differing only in that the page's copy had no bound column — reported as an ambiguous
match, which silenced the one record that contains the campaign's real 1,000x error. The repair passes
the prefix's own flat tables as references, using the frozen `pair_tables` rule unchanged.

**B-3.3 — the step after the operand's message was read by nobody.** `check_attempt` takes the prefix as
`steps[:i]` and the later steps as `rev.beyond(i + 1)`, which is `steps[i+1:]`; the step at index `i` is
in neither. The repair is `rev.beyond(i)`. Measured effect on these records: none, because in each one
the operand's message is the last step.

**NOT changed, and recorded as a proposal instead (P-1).** The conversion is resolved only from steps
STRICTLY AFTER the operand's message. This actor writes the operand, the factor and the answer in one
final message — on UID0014 the operand message is step 45 of 45 — so the resolution has nothing to read
and reports OUTSTANDING ("no later step states a factor"). Reading the operand's own message as well
turns that into `incorrect_factor_stated`, VIOLATED, "wrong by 10^3": the actual error. That changes
what "committed before the later steps are read" means and so stays a proposal, measured and labelled,
for the user to accept or refuse.

**Also NOT changed.** `oqa_lc_cells` and `oqa_lc_chain` stay byte-identical; `match_operand`'s treatment
of an unbound duplicate as a disagreeing candidate is left in place because B-3.2 removes the case that
mattered; `nearer_scale`'s refusal to infer an absence from an unobserved column stays as it is, and the
measured cost of that refusal is reported rather than legislated away.

---

## B-4 — 2026-11-19. The column LEAF is recoverable from a split header; the column PATH is not

**Recorded BEFORE the code change, rule OQA-BIND-1.** Baseline preserved: the 24 collected episodes,
their freeze `2083b4587253`, bundle v5 `162b580923e2e0b9`, the learned hypothesis `96c3d1bd9aad6f4d`, the
checker, stage and learner semantics, and every existing analysis file. All new output goes to a
versioned directory `collect_20261118/v2_bind/`; nothing is overwritten.

**Reason, measured on the 24 corrected-client training records before any code was written.** The
2026-11-18 collection reported `header_observed_structure_lost` for 166 of 166 bindings. Inspecting the
code and the raw observations shows that cause is made of three different situations, and only one of
them is a defect:

| situation | measured |
|---|---|
| a flat rendering of the same table exists and pairs by a shared data row | `pair_tables` already finds **9 pairs** over 306 split/single tables, and 4,117 of 26,437 data cells (15.6%) already have a bound header. "166 of 166" was true of the bindings that reached a cell, not of the corpus |
| a flat rendering exists, widths match, but no data row is shared | 227 width-equal combinations; **2** have full leaf agreement at every index (both UID0026), 1 partial, the rest are different tables that merely share a width. A `grep` that matched the header line returns the header ALONE, with `rows = 0`, so the shared-data-row identity test can never fire |
| no flat rendering of the table exists anywhere in the permitted view | the common case: UID0019, UID0063, UID0163 have **zero** flat tables in the whole permitted prefix |

**And the span reading is testable, and where it was testable it was WRONG.** Reading the split header
with the parent spanning forward over empty cells agrees with an independently observed flat header at
**9 of 10** indices on UID0026 and disagrees at index 9 (`Paper money 2/ > National bank notes` against
the flat form's top-level `National bank notes`). Admitting it as `index_alignment` would raise bound-header
coverage from 15.6% to **76.8%** on an assumption the model does not hold and that the one available test
refutes. That is forcing a binding to increase coverage, and it is not done.

**The one thing that IS recoverable.** The split header's CHILD row is index-aligned with the data cells
— both come from the same pipe rendering and have the same width — and its labels agree with the
independently observed flat header's leaves at **10 of 10** indices on UID0026. The PARENT path does not
(9 of 10). The decisive witness is UID0019, whose supplied page renders

```
| Report date | Spot, forward and future contracts | Net options positions | Exchange rate (Japanese yen per U.S. dollar) |  |
|  | Purchased (1) | Sold (2) | (3) | (4) |
```

over the observed data row `| 03/16/2016 | 528979 | 533325 | -74 | 114.0 |`. The parent row's labels are
positionally shifted because a two-column span was rendered as one cell, so the parent path at index 2 is
wrong; the child row's labels are aligned, so the leaf at index 1 is `Purchased (1)` and at index 2 is
`Sold (2)` — which is how one can see that the actor's operands are the spot/forward columns and not the
asked `Net options positions`.

**Changed — `research/officeqa/oqa_cc_extract.py` only, in the layer ABOVE the frozen modules.**
`oqa_lc_cells` and `oqa_lc_chain` are NOT modified; the frozen campaign's behaviour stays byte-identical,
exactly as in B-2 and B-3.

* A new basis **`LEAF_ONLY`** and a new field `column_leaf`. When a cell's `column_basis` is `UNKNOWN`,
  its table's header form is `split` or `single`, and the table's child row has a non-empty label at the
  cell's own column index that is not a bare ordinal marker (`(3)`), the leaf is recorded.
* **`column` and `header_known` are left untouched.** This is the distinction the repair turns on: a leaf
  gives **column/series identity**, not **unit scope**. `nearer_scale_between` asks whether a nearer
  scope states another money scale, which needs the path and the parent; a leaf cannot settle it. So no
  binding changes state, and `_well_bound` still requires a bound header.
* Table identity is required, not assumed: the leaf is read from the cell's OWN table, re-bound by its
  own `(source, table index)` key, never from another table of the same width.
* Uncertainty is preserved: where the child row is empty at that index, or the label is a bare ordinal,
  or the header form is `flat`/`none`, the cell keeps `UNKNOWN` and nothing is recorded.

**Not changed, deliberately.** The learned model and its assumption set; `nearer_scale_between`'s
evaluator; the stage link; the prefix/later window; the vocabulary; the report renderer; the gate; the
scorer. The two standing proposals — same-message postcondition checking and supported table-unit scope
inference — are NOT implemented here, so the comparison isolates extraction.

**What this amendment does NOT claim.** It does not claim the repair lets the frozen contract produce
findings. The frozen hypothesis `scale_binds` is about the unit, and the leaf does not bear on the unit;
the expected effect on bindings is therefore **zero**, and section 6 of the packet reports the measured
outcome rather than this expectation. The finding the leaf WOULD support — an operand whose column is not
the asked measure — needs a predicate the vocabulary does not have, and that extension stays unimplemented.

## C-1 — 2026-11-21. A value match may not select a source cell; an occurrence must corroborate it

**Recorded BEFORE the code change, rule OQA-OCC-1.** Baseline preserved: `collect_20261118/v2_bind/`
(digest-checked before and after by the job, which fails if anything there changed), the 24 collected
episodes and their freeze `2083b4587253`, the learned hypothesis, and every existing analysis file. New
output goes to `runs/20261121-officeqa-contract/v3_occ/`. The mixed-version bundle is corrected in the
same job by freezing the current semantic state as **v7** in that new directory rather than relabelling
v6 in place.

**The defect, in one sentence.** `oqa_lc_chain._best_cell` is handed every cell whose value matches an
operand and **silently returns one of them**, ranked by exact-match, then FLAT column, then earliest
step. When several cells hold the same number the chain therefore reports a source binding that rests on
nothing but numeric equality and a tie-break — exactly what "numeric equality alone must not establish
cell, series or period identity" forbids. The ledger then discharges or violates an obligation against a
cell the actor may never have read.

**Reason, measured on the 24 corrected-client TRAINING records before any code was written**
(`research/officeqa/oqa_occ.py`, reused development evidence; no validation or held-out record takes
part). A `grep` observation prefixes each matched line with its **line number**, so a cell has an
observable position — (document, line, field index) — independent of what its column means:

| | measured |
|---|---:|
| records where the occurrence channel exists at all | **20 of 24** |
| observed numeric occurrences | 4,261 |
| distinct numbers the actor wrote | 1,117 |
| of those, at a **unique** observed position | **201** |
| at several positions → correctly UNKNOWN | 122 |
| at no observed position (actor-derived results) | 794 |
| unique position **and independently corroborated** | **64** |

**A unique position is not a binding, and the module says so.** Corroboration must be independent of the
number: `ROW_LABEL` (the actor's own message naming a distinctive token from the line the value sits on,
stopwords removed) or `QUERY` (the line was returned by a grep whose pattern the actor chose). 64 of 201
reach that bar.

**A third candidate was rejected, and the rejection is the useful part.** `SOLE_IN_ROW` — the value
occurs once on its line — looked like a corroboration and lifted the supported rate to **100% of unique
positions**. That attractive number was the tell: it is very nearly a tautology, because two occurrences
of one value on a single line would occupy two field indices and the position would not be unique.
Measured, **200 of 201** unique positions were sole-in-row, an entailed share of 0.995. It is computed
and reported for transparency and excluded from support. Honest support is 64, not 201.

**The change.** `_best_cell` is replaced by a selection that refuses to choose without evidence:

| candidates | occurrence evidence | result |
|---|---|---|
| exactly one | not required | selected, basis `SOLE_CANDIDATE` |
| several | a SUPPORTED occurrence binding for that value | selected, basis `OCCURRENCE_CORROBORATED`, position recorded |
| several | UNKNOWN or absent | **no selection.** The rivals are recorded and the obligation becomes UNKNOWN |

Nothing else changes: the obligation vocabulary, the stage states, the `reopen` semantics on a dropped
assumption, the learned hypothesis and the report format are all untouched. The corroboration is computed
ABOVE `oqa_lc_cells`, which stays byte-identical, keeping B-2's promise.

**What is NOT claimed.**
* **Not the column's meaning.** A corroborated occurrence says *which cell*, not what the cell measures.
  Both witnessed error classes of OQA-BIND-1 are column *selection* errors and this does not settle
  them; it supplies the cell identity a later stage contract would need.
* **Not that an unbound number is wrong.** Most numbers the actor writes are its own intermediate
  results, so `NO_OCCURRENCE` is a category and not a fault.
* **Not that a record without numbered observations failed.** Four of the 24 read documents without
  producing numbered lines. The channel is absent there — genuinely missing information, reported as its
  own outcome rather than folded into UNKNOWN.

**Prediction, committed before the replay runs.** Fewer reported source bindings, not more, because the
change only ever *withholds* a selection. Specifically: the count of operands carrying a selected cell
will **fall**; some obligations currently DISCHARGED or VIOLATED will become **UNKNOWN**; and the number
of *supported* bindings will not rise above the 64 the occurrence measurement found. If the replay shows
*more* selections, the implementation is wrong rather than the prediction. An increase in coverage would
be a defect here, not a result.

### C-1, measured: the replay, and two defects it found in C-1 itself

**Job 66552721 (free, 1:20).** The post-C-1 semantic state is frozen as label **v8**, bundle
`cfd8318c752db11e`, in `runs/20261121-officeqa-contract/v4_c1/`; v7 and `v3_occ/` are untouched.

**The committed prediction HELD in direction.** Over the same 24 training records, 659 operands:

| | old tie-break | under C-1 |
|---|---:|---:|
| operands with a selected source cell | **566** | **102** |
| of which sole candidate | — | 67 |
| of which occurrence-corroborated | — | 35 |
| selections withdrawn | — | **464** |
| ledger DISCHARGED | 254 | 81 |
| ledger VIOLATED | **186** | **30** |
| ledger OUTSTANDING | 19 | 30 |
| ledger UNKNOWN | 1,380 | 770 |

**The headline result.** The old tie-break was selecting a source cell on numeric equality alone in
**464 of its 566 selections (82%)**, and those selections supported **156 of its 186 VIOLATED
obligations** — accusations against the actor resting on a cell it may never have read. C-1 removes
them. Fewer findings, and the ones that remain rest on something.

**Defect A in C-1, found by the replay and not by reading it.** The amendment predicted that withdrawn
bindings would make obligations **UNKNOWN**. They do not: the ledger shrinks from **1,839 obligations to
911**, because the obligations *downstream* of a cell — the unit question, the period question — are
not generated at all when no cell was selected. On UID0044 the ledger goes from 406 records (132
DISCHARGED) to 142 (0 DISCHARGED, 141 UNKNOWN): 264 requirements simply vanish. That is "an unknown
action effect silently meaning nothing changed", which this project's own chain docstring exists to
prevent. **The direction of the prediction held; this sub-claim failed, and the failure is a defect in
the implementation.** The repair is for `obligations()` to emit the downstream requirements as UNKNOWN
with a `why` naming the refusal, rather than omit them.

**Defect B in C-1, measured separately.** `_match_cells` treats the same underlying cell observed
through different routes as distinct candidates. Of 499 operands with several value-matched candidates,
**199 (39.9%) collapse to a single cell** once candidates are keyed by (row label, column, value, scale
factor); the mean candidate count falls from 8.61 to 3.54 distinct cells. The UID0044 witness is
typical: three "rivals" for `-3044.0` are the supplied page, the grep at step 2 and the grep at step 12
**of the same document**. So a share of the 464 withdrawals are refusals of a tie that does not exist.
The repair is to deduplicate candidates by cell identity *before* deciding a tie needs corroboration.
(Measured with a stand-in model locally, so the exact share is indicative; the cell-identity collapse
itself is not model-dependent.)

**A historical report moved, and that is recorded rather than re-pinned silently.** A-1 promised
metadata-only changes and a test guarded it by regenerating two real campaign reports byte-for-byte.
C-1 supersedes that promise for the source-binding section: **UID0014's report changes, UID0019's does
not.** The test now pins that blast radius per case and still asserts that the model digest, the trace
digest and the action forecasts are unchanged — a change in any of those would mean C-1 reached further
than this amendment claims. 2,479 of 2,480 tests passed; that one failure was this.

**And one false alarm of my own, corrected.** The job reported "v2_bind CHANGED". It had not: all four
digests are byte-identical to the previous job's record. The before-snapshot had been moved inside the
`bundle7` branch while the after-check stayed outside it, so the replay phase compared an empty
before against a populated after. The snapshot now runs in every phase. A preservation check that
cries wolf is worse than none.

## C-2 — 2026-11-21. Two repairs to C-1, from the counterexamples its own replay produced

**Recorded BEFORE the code change, rule OQA-OCC-1.** Baseline preserved: `v2_bind/`, `v3_occ/` (v7) and
`v4_c1/` (v8) are all left alone; new output goes to `runs/20261121-officeqa-contract/v5_c2/`, and the
post-C-2 semantic state will be frozen as **v9**. The learned hypothesis `96c3d1bd9aad6f4d` stays fixed,
so the comparison isolates the selection and obligation logic.

### Defect A — a withdrawn binding made requirements VANISH, not UNKNOWN

**The mechanism, now exact.** In `obligations()` the operand loop emits `cell_bound` and then
`continue`s when `cell is None`, skipping obligation 2 (`period_match`) and obligation 3
(`unit_match`). So each refused operand loses exactly two records: **464 refusals × 2 = 928**, which is
precisely the observed ledger gap of 1,839 → 911. Nothing else contributes.

**Why it matters more than its size.** The chain module exists because `oqa_check` "has no object for a
requirement that is still open". A requirement that disappears when its source becomes unidentifiable
is the same failure in a new place: the reader cannot tell a requirement nobody addressed from one the
representation quietly dropped. "An unknown action effect must not silently mean nothing changed."

**The change.** When no cell was selected, `period_match` and `unit_match` are emitted as **UNKNOWN**
with a `why` that names the reason — no candidate at all, or a refusal for want of corroboration — and
the refusal's rival list is carried on the record. This applies to **both** reasons, not only to C-1's
new one, because the principle is the same; the replay passes both arms through the same
`obligations()`, so the comparison stays like-for-like.

**PREDICTION, committed before the replay.**
1. **The two arms will have the SAME ledger size.** Every operand now yields exactly three obligations
   whatever happens to its cell, so the gate changes obligation *states* and never their existence.
   That is the property worth having, and it is falsifiable: if the sizes still differ, the repair is
   incomplete.
2. **DISCHARGED and VIOLATED will not rise** above C-1's 81 and 30. UNKNOWN absorbs every recovered
   record. If an accusation appears where a requirement had vanished, the repair has manufactured a
   finding and is wrong.

### Defect B — a "tie" is often one cell seen through several observations

**The evidence.** `_match_cells` collects candidates per observation, so the same underlying cell
reached by the supplied page and by two different greps becomes three candidates. Measured over the 24
training records: of **499** operands with several value-matched candidates, **199 (39.9%) collapse to a
single cell** once keyed by content rather than route; mean candidates **8.61 → 3.54**. The UID0044
witness is typical — three "rivals" for `-3044.0` are the supplied page, the grep at step 2 and the grep
at step 12 **of the same document**. (Measured locally with a stand-in model, so the exact share is
indicative; the collapse itself is not model-dependent.)

**The change.** Candidates are deduplicated by a **route-independent cell identity** before a tie is
declared: `(source file, row label, column index, value, scale factor)`. Two candidates agreeing on all
five are the same measurement in the same document, so selecting "one of them" asserts nothing extra.
The observation route — which step returned it — is deliberately excluded from the key, because the
route is exactly what differs when one cell is seen twice. The earliest-available candidate is kept, so
`available_from` still reflects when the actor could first have read it.

**PREDICTION, committed before the replay.**
3. **Selections will RISE above C-1's 102 and stay far below the old tie-break's 566.** A rise here is
   the intended effect, unlike in C-1 where any rise would have been a defect: C-1 removed selections
   that rested on numeric equality, and C-2 restores only those where the "rivals" were never rivals.
4. **The rise will be in `SOLE_CANDIDATE`**, not in `OCCURRENCE_CORROBORATED`, because a collapsed tie
   has one candidate and needs no corroboration. If `OCCURRENCE_CORROBORATED` rises instead, the dedupe
   is changing which cell is corroborated rather than how many candidates there are, and is wrong.
5. **`VIOLATED` may rise above 30**, and that is the honest risk of this repair: a restored binding can
   support an accusation. Any increase must be inspected witness by witness before it is believed, and
   an increase without an inspectable witness is a reason to withdraw the repair, not to report it.

**What is still NOT claimed by either repair.** The column's meaning; that an unbound number is wrong;
that a record without numbered observations failed. And neither repair touches the learned hypothesis,
the stage vocabulary, the `reopen` semantics or `oqa_lc_cells`, which stays byte-identical.

### C-2, measured: four predictions held, one was mis-stated, and one is still being discharged

**Job 66552984 (free, 1:14).** Post-C-2 state frozen as label **v9**, bundle `3e82a6390357ccac`, in
`v5_c2/`; v7, v8, `v3_occ/` and `v4_c1/` untouched, and `v2_bind` digest-verified unchanged (the
preservation check, repaired after its false alarm, now reports VERIFIED in every phase).

| over the same 24 records, 659 operands | old tie-break | under C-1 | under C-2 |
|---|---:|---:|---:|
| operands with a selected source cell | 566 | 102 | **257** |
| …of which SOLE_CANDIDATE | — | 67 | **241** |
| …of which OCCURRENCE_CORROBORATED | — | 35 | **16** |
| refused for want of corroboration | — | 464 | **309** |
| ledger obligations | 1,839 → **2,025** | 911 → **2,025** | **2,025** |
| DISCHARGED | 254 | 81 | 192 |
| **VIOLATED** | **186** | **30** | **69** |
| OUTSTANDING | 19 | 30 | 19 |
| UNKNOWN | 1,566 | 1,745 | 1,745 |

**Prediction 1 HELD, and sharply.** Both arms now carry **exactly 2,025 obligations** — the state
deltas sum to zero (−117 VIOLATED, −62 DISCHARGED, +179 UNKNOWN). The gate changes obligation *states*
and never their existence, which is the property worth having and was the falsifiable part.

**Prediction 3 HELD.** Selections rose from 102 to 257 and stayed far below the old 566.

**Prediction 4 HELD for its claim and was INCOMPLETE.** The rise is in `SOLE_CANDIDATE` (67 → 241), as
predicted. But `OCCURRENCE_CORROBORATED` **fell** (35 → 16), which I did not anticipate. The mechanism
is benign and follows from the repair: 19 operands whose ties collapsed to one cell no longer need
corroboration, so they moved from corroborated to sole-candidate. Explained, but not predicted.

**Prediction 2 is VIOLATED AS WRITTEN, and the fault is in how I wrote it.** It said DISCHARGED and
VIOLATED "will not rise above C-1's 81 and 30"; they rose to 192 and 69. The prediction was stated as
though only defect A's repair were applied, when C-2 applies both: defect B restores 155 selections,
and a restored binding legitimately produces discharges and accusations. The two sources ARE
distinguishable — `test_the_recovered_requirements_are_never_accusations` pins that every record
recovered by defect A is UNKNOWN — but the prediction conflated them and should have been split.

**Prediction 5 is live and not yet discharged.** VIOLATED rose from 30 to 69. My own words: "any
increase must be inspected witness by witness before it is believed, and an increase without an
inspectable witness is a reason to withdraw the repair, not to report it." A count cannot be
inspected, so the replay now carries **every accusation with its witness** and the inspection is the
next step. **Until that inspection is done, C-2's accusations are NOT claimed as correct.** Note the
direction: 69 is still well below the old tie-break's 186.

**An independent validation of defect B, from a test that failed in the opposite direction.** The
byte-reproduction guard on two real campaign reports expected UID0014's text to have CHANGED under C-1.
Under C-2 it is **byte-identical to the historical report again**. So C-1's entire effect on that
report was caused by SPURIOUS TIES rather than genuine ambiguity, and the blast radius of C-1+C-2 on
historical report text is **zero**. The expectation is re-pinned to unchanged with that reason
recorded, not quietly flipped.

**An unrelated failure found by the same test run, reported not buried.**
`test_four_concurrent_processes_cannot_spend_more_than_the_one_shared_cap` failed: *"four processes
spent 24 against a cap of 23: [2, 18, 2, 2]"* — a real one-unit overspend in the shared spend ledger,
which none of these amendments touch. It passed in three earlier jobs the same night, so it is either
a race that sometimes loses or a load-dependent one. A ten-repeat diagnostic establishes which before
anyone proposes a repair; it changes nothing. **Impact on the live campaign is immaterial** — one claim
block against caps of 20,000 attempts and $15, with about $2 spent — but the invariant `spent <= cap`
is the thing that protects the authorization, so it is recorded as an open defect rather than rounded
away.

## C-3 — 2026-11-21. An unknown year became an accusation, through a sentinel in an inequality

**Recorded BEFORE the code change, rule OQA-OCC-1.** This amendment exists because C-2's prediction 5
was discharged honestly and the answer was unfavourable. Output goes to `v7_c3/`; v7, v8, v9 and the
directories `v3_occ/`, `v4_c1/`, `v5_c2/`, `v6_lean/` are all left alone.

### What the inspection found, and what it costs C-2

C-2's prediction 5 said: *"any increase must be inspected witness by witness before it is believed, and
an increase without an inspectable witness is a reason to withdraw the repair, not to report it."*
VIOLATED had risen from C-1's 30 to C-2's 69, so the replay was made to carry every accusation with its
witness. Inspected:

| | |
|---|---:|
| accusations under the gate | **69**, every one of kind `period_match` |
| …resting on a period whose **YEAR IS UNKNOWN** | **55 (80%)** |
| …with a known year, so potentially real | 14 |
| concentrated in | 2 records — UID0028 (39), UID0228 (30) |
| the same class in the old tie-break arm | **186** |

The witnesses are unambiguous. A row labelled `Aug.` yields period *"August (year unknown)"*, and the
obligation reports *"row period August (year unknown) is not among the asked June 1970, 1960, 1969 and
is not inside their span"* — **VIOLATED**. A period whose year is undetermined cannot be known to be
outside the asked periods. This is a false accusation, and it is made against the actor.

**THE MECHANISM, exactly.** In the `period_match` branch:

```python
elif len(asked_years) >= 2 and min(asked_years) < (cp.get("year") or -1) < max(asked_years):
```

`cp["period"]` is not None here — it is a dict whose `year` is None — so the earlier UNKNOWN branch
does not fire. `(None or -1)` substitutes **−1** for "unknown", −1 lies outside any span of real years,
the guard is False, and control falls through to VIOLATED. **A sentinel standing in for "unknown",
used in an inequality, is precisely how an unknown becomes an accusation.**

**SO C-2's ACCUSATIONS ARE WITHDRAWN AS A CLASS**, per prediction 5. To be clear about what is and is
not affected:
* C-2's **repairs stand** — predictions 1, 3 and 4 held, the ledger sizes are identical, and the
  byte-identical historical report is independent evidence that defect B was real.
* C-2's **accusations are not claimed**. 80% are false by construction; the remaining 14 rest on
  bindings whose correctness I have not established.
* **This defect PREDATES the gate.** The old tie-break arm carries 186 VIOLATED of the same kind, so
  every previous VIOLATED count in this project — including the three-arm campaign's delivered reports
  — is inflated by it. That is a finding about the historical record, not only about tonight's work.

### The change

An explicit branch: if the cell's period has **no year**, the obligation is **UNKNOWN**, with a `why`
naming the row label and saying that the year is undetermined so the period cannot be compared. The
sentinel is removed from the inequality, which now runs only when the year is known.

**Why not decide it from the month alone.** Tempting, and unsound here: UID0028's asked periods are
"June 1970, 1960, 1969" — two of them BARE YEARS with no month — so an August row could be August 1960
or August 1969 and might well be within what the question asks. The asked-period representation is
already known to be loose (`oqa_obligations._periods` reads "fiscal years 1929-1942" as two bare
years). With that representation, an unknown year is genuinely undecidable, and UNKNOWN is the honest
state rather than a conservative one.

**PREDICTION, committed before the replay.**
1. **Gated-arm VIOLATED falls from 69 to 14** — exactly the unknown-year class moves to UNKNOWN.
2. **Old-arm VIOLATED falls substantially from 186**, because the class predates the gate and the
   replay runs both arms through the same `obligations()`.
3. **Ledger size is unchanged at 2,025 in both arms.** This change moves states, never counts.
4. **DISCHARGED does not rise.** An unknown year cannot discharge a period requirement any more than
   it can violate one. If DISCHARGED rises, the branch is granting a witness it does not have.

### C-3, measured — and a claim of mine withdrawn, with the inference error named

**Report generation is DETERMINISTIC** (job 66553105, `oqa_report_det.py`: 8 regenerations within one
process and 8 across processes with fresh hash seeds, for two reports).

| report | digest now | historical | matches |
|---|---|---|---|
| UID0019 (arm B, LC_ACTION) | `338b6a2d7b6f` | `338b6a2d7b6f` | **16 of 16** |
| UID0014 (arm C, LC_STAGE) | `51f046e063a7` | `8b90446cecdc` | **0 of 16** |

**I WITHDRAW the claim that C-2 restored UID0014's report byte-identically**, and the two conclusions I
drew from it — that C-1's whole effect on that report came from spurious ties, and that the blast
radius of C-1+C-2 on historical report text was zero. All three were wrong.

**The inference error, named plainly.** Job 66552984's pytest summary showed this test failing with the
parametrisation `-False`, whose digest assertion is `now != historical`. I read that failure as "the
bytes must therefore be identical". The assertion that actually failed was a **later one in the same
test**: *"the stage group count keeps its historical value" — `assert 17 == 14`*. I inferred a fact
from a test NAME without reading its assertion message, and then built two conclusions on it. The
digest has differed from the historical one continuously since C-1.

**What the stage-group count actually shows, and it supports C-3.**

| | stage diagnosis groups in UID0014's report |
|---|---:|
| historical (old tie-break) | 14 |
| under C-2 | **17** |
| under C-3 | **14** again |

C-2 restored bindings that then produced false `period_match` accusations, and each added a stage
diagnosis group. C-3 made an undetermined year UNKNOWN instead of VIOLATED, and the count returned to
its historical value — the assertion passes again. That is independent support for C-3 being the right
repair, arrived at by reading the failure instead of guessing at it.

**Under C-3 the whole suite is otherwise green: 2,506 passed, 9 skipped, 368 subtests, 1 failure** —
the UID0014 digest, which is now pinned as *expected to change* with its measured digest recorded. The
earlier `True` pin is reverted.

**Lean, run in a job (66553083).** `OqaConnected.lean` over the delivered facts: **50 evidence examples
encoded, 0 skipped, lean returncode 0, "all statements accepted"** (Lean 4.20.0). The standing
theorems in `OqaContracts.lean` compiled clean. Two notes kept deliberately narrow: the first two
attempts failed on the BINARY, not the proofs — `elan/bin/lean` is a shim with no configured toolchain,
and the real binary is under `elan/toolchains/leanprover--lean4---v4.20.0/bin/` — and what is verified
is the RESOLUTION ALGEBRA, not that the extraction is correct or that the occurrence binding picks the
right cell.

**The ledger overspend is INTERMITTENT, not deterministic:** ten repeats, ten passes. It remains a real
violation of `spent <= cap` that happened once, now characterised rather than explained away.

---

## D-1 — 2026-11-21. A source-to-calculation vocabulary, in its own namespace

**Reason.** C-1, C-2 and C-3 were *software* repairs: they fixed arbitrary source selection, obligations
that vanished instead of becoming unknown, and a sentinel year that turned an unknown into an accusation.
None of them is a learner repairing its own hypothesis, and the instruction says so plainly. What is
missing is a learned contract over the connection

    question requirement -> relevant lookup -> supported value -> calculation -> final answer

and the existing vocabulary cannot express it. `oqa.cc.model.v1` has `table`, `header`, `cell`, `value`
and `calculation`, and its only LEARNED predicate is `scale_binds` — a claim about units. There is no
term for an *operand of a written calculation*, none for the committed *answer*, and none for how the
actor came by a number. The 2026-11-19 binding investigation also established that the column's MEANING
is not recoverable from the permitted rendering, so a smaller justified scope is required: **occurrence
identity** rather than column semantics, which is what `oqa_occ` already provides.

**Added, in a NEW namespace `research/officeqa/oqa_ln_vocab.py`, leaving `oqa.cc.model.v1` untouched.**
The frozen prototype's own prompt enumerates `IR.PREDICATES`, so extending that dict in place would
silently change the 2026-11-17 learner's prompt. The extension therefore lives beside it and
`oqa_cc_ir.validate`, `clause` and `contract` take an optional vocabulary argument that defaults to the
old one. With no argument, every existing caller behaves exactly as before.

| new entity | what it denotes |
|---|---|
| `operand` | one number of a written binary arithmetic statement, at the position it was written |
| `answer` | the value committed under the narrow `committed_answer` policy |

| new predicate | args | provenance | meaning |
|---|---|---|---|
| `operand_of` | operand, calculation | GIVEN | the parse put this operand in this statement |
| `calculation_result` | calculation, value | GIVEN | the result as written |
| `arithmetic_consistent` | calculation | MATH | `a OP b = c` really holds; a definition, never a discovery |
| `calculation_supports_answer` | calculation, answer | OBS | the committed answer equals this result |
| `calculation_superseded` | calculation, calculation | OBS | a later statement in the same attempt yields the committed answer and this one does not |
| `operand_positions` | operand, count | OBS | how many observed occurrences match it: 0, 1 or many |
| `operand_source_tool` | operand, tool | OBS | `grep` or `read`, from whether the occurrence carries a query pattern |
| `operand_written_with_separator` | operand | OBS | written as `1,234,567` |
| `operand_round_power_of_ten` | operand | OBS | 10, 100, 1000 … — the shape of a conversion factor |
| `operand_in_question` | operand | TASK | the number appears in the question sentence |
| `operand_equals_earlier_result` | operand, calculation | OBS | equal to an earlier statement's result |
| `message_quotes_question_term` | operand | OBS | the message that wrote it quotes a distinctive word of the question |
| `operand_sourced` | operand | **LEARNED** | the only learnable conclusion: the occurrence resolver will find one corroborated source |

New stage obligation: **`source_traced`** — the operands of the calculation that supports the committed
answer have identified sources. It joins `unit_match`, `cell_bound`, `period_match`, `arith_recorded`
and `answer_traced`; the existing five are unchanged.

**What makes `operand_sourced` an empirical claim rather than a definition, and the limit of that.**
The resolver `oqa_occ` decides SUPPORTED from two corroborations: a token shared between the matched
line and the actor's own message (`ROW_LABEL`), or a grep pattern whose words appear on that line
(`QUERY`). **Those two are withheld from the learner.** It sees behavioural correlates instead — how
many positions matched, which tool returned them, how the number was written, whether the message
quoted a question term. A rule over those predicts the resolver's verdict and can be wrong: the
2026-11-19 measurement found 201 unique positions of which only 64 were corroborated, so "a unique
position is a source" is false about two thirds of the time. **The honest limit:** `operand_source_tool`
is not fully independent of `QUERY`, because only a grep carries a pattern at all. The claim is a
correlate of the verdict, not a restatement of it, and that is weaker than independence.

**Not changed.** `oqa.cc.model.v1`, every `oqa_cc_*` and `oqa_lc_*` behaviour, the frozen campaign, the
C-1/C-2/C-3 chain, and the semantic bundle v9 `3e82a6390357ccac`. The extended vocabulary is digested
as its own bundle label **v10**, because the guard must keep refusing to let one label mean two digests.

**Predicted before implementation, so the measurement can contradict it.**
1. A first learned rule will over-claim: it will condition on `operand_positions = 1` and little else,
   and will therefore be refuted on the checking questions.
2. The refutations will be concentrated on operands whose only occurrence came from a `read`, because a
   `read` observation in this corpus often carries no line numbers and never carries a query pattern.
3. The repair will narrow the condition rather than retire the rule — the operation the restricted
   20-effect learner could not perform.
4. The recovery control will NOT be accused: `p4/UID0063` and `p4/UID0085` both replace a calculation
   and then commit an answer, and `calculation_superseded` must stop the earlier statement supporting
   the answer without the later one being blamed for it.

---

## C-3, CORRECTED — 2026-11-21. A claim of mine in the C-3 section is withdrawn

**What I wrote.** The C-3 section, and the comment in
`tests/test_officeqa_followup_observability.py`, said: "The stage-group count is the part that tells a
story: C-2 took it 14 -> 17 by restoring bindings that then produced FALSE period accusations, and C-3
returned it to 14 by making an undetermined year UNKNOWN instead of VIOLATED. **That assertion passes
again, which is independent support for C-3 being the right repair.**"

**What is true.** It does not pass. Job 66580310 ran the whole suite on the cluster: **2,529 passed, 1
failed**, and the one failure is that assertion — `assert 17 == 14`. The count was never returned to
14, so the sentence claiming it was, and the inference drawn from it, are both withdrawn. This is the
second time I have built a claim on this one test without reading its assertion, and the first time is
recorded in the same section.

**What 17 is, now MEASURED rather than argued.** C-2's defect-A repair made a WITHDRAWN binding emit
`cell_bound`, `period_match` and `unit_match` as UNKNOWN instead of letting those requirements vanish.
`group()` buckets by diagnosis CLASS, so three classes that previously produced no entry now produce
one each. `research/officeqa/oqa_groups_probe.py`, job 66580469, prints every group from the same
ledger:

| state | groups |
|---|---|
| VIOLATED | 2 |
| OUTSTANDING | 3 |
| UNKNOWN | **9** |
| DISCHARGED | 3 |
| **total** | **17** |

140 ledger entries, 5 accusing groups, 9 UNKNOWN. And the three largest UNKNOWN groups are exactly
`cell_bound` n=19, `period_match` n=19 and `unit_match` n=19 — the identical triple, over the identical
19 subjects, that defect A's repair emits for one withdrawn binding. **So the +3 is those three groups,
and 17 is correct**: the historical 14 is what a silently-vanishing requirement looked like, and the
count rose because requirements stopped vanishing. The pin in
`tests/data/officeqa_report_expectations.json` is set to 17 on that basis. What is still withdrawn is
the claim that C-3 restored 14 and that doing so supported C-3; C-3's support is its own measured
section, not this count.

**What changed in the test, and why it is not a disabled test.** The expected-to-change observations —
whether the regenerated bytes should match, and the stage-group count — now live in
`tests/data/officeqa_report_expectations.json`, each with the amendment that set it and the reason.
The test still fails on a mismatch, and its message names the field, both values and where to re-pin.
What moved out of the file is only the *pins*; the baseline-immutability assertions stayed in the test
body and are stronger than before: the model digest, the trace digest, the action forecasts, the
"unavailable" provenance fields, and a new test asserting the frozen campaign's own records and
prediction files are still there and still say what they said. A pin may be re-set deliberately; those
may not be re-set at all.

---

## D-2 — 2026-11-22. A numeral inside label text is not a numeric data cell

**Recorded before implementation.** Nothing in this section had been coded when it was written; the
measurements quoted are read-only probes over the already-frozen record set.

**The defect.** `oqa_occ.observed_occurrences` splits a line-numbered, pipe-delimited observation into
fields and emits an occurrence for **every** numeral it finds **anywhere in any field**. A column
heading that contains a year, and a pandas rendering artifact such as `Unnamed: 14_level_2`, therefore
occupy a `(document, line, field)` position exactly as a data cell does, and can be matched, counted,
corroborated and reported as the source of a number the actor wrote.

**It is much larger than the five cases already reported.** The earlier measurement
(`oqa_ln_audit.bare_number_measurement`, milestone §6) counted **5 of 54 SUPPORTED bindings** whose
matched field was a numeral inside a label. That is the tail of the distribution, not its size. Over
the same 48 frozen attempts, counting every numeral match that produces an occurrence at all:

| field class | matches | example |
|---|---|---|
| a numeric cell | 2,002 | `81774`, `-74`, `684 $`, `1,089 6/ 7/` |
| **label text** | **1,464** | `Receipts Carriers' Taxing Act of 1937`, `Unnamed: 14_level_2` |
| several numerals, not a value plus footnotes | 78 | `8/21/91`, `2.861 2.807 2.860 2.893`, `2 -148,086` |
| decoration only | 4 | `5/`, `16/` |
| **total before the correction** | **3,548** | |

So **1,546 of 3,548 occurrence-producing matches (43.6%) are not numeric cells.** Position counts,
not only supported bindings, are affected.

**The correction, and its limit.** A field counts as a numeric cell only when the whole field is one
numeral in a form the parser already supports, optionally followed by footnote references:

```
^ \(? <numeral> \)? (currency or percent marker)? ( <digits>/ )* $
```

with `<numeral>` exactly `oqa_occ._NUM`'s own form, so every sign, separator, decimal, parenthesised
negative, leading `$` and trailing `%` the parser already reads is retained. A cell yields its
**leading** numeral only, so `1,089 6/ 7/` yields 1,089 and not 6 or 7. Everything else — any field
containing a letter, a date, a collapsed multi-value rendering, a bare footnote marker — yields no
occurrence and therefore stays **unknown**; it is not asserted to be wrong.

**Label text is still read.** `line_tokens` is computed from the whole line body, so the `ROW_LABEL`
corroboration and the report's context are unchanged. What the correction removes is label text's
ability to be a *position*, not its ability to be *evidence*.

**What is deliberately NOT fixed, and stays ambiguous.** A year standing alone in its own heading cell
— `| 1987 | 1988 |` — is a bare numeral and remains indistinguishable from a data cell in this
rendering. Deciding it would need a header-row detector, which is the broad table-parser rewrite this
amendment is forbidden to attempt. It is reported as a residual, named limitation.

**Scope.** `oqa_occ` gains the mode and **defaults to the superseded behaviour**, so every existing
caller — including `oqa_lc_chain` and `oqa_report_det`, whose historical reports are pinned by digest —
is byte-identical. The learned-contract pipeline (`oqa_ln_evidence`, `oqa_ln_check`, `oqa_ln_audit`)
defaults to the **corrected** mode and can be asked for the superseded one, which is how the replay
produces both sides of one comparison from one code path.

**Predicted before implementation, so the measurement can contradict it.**
1. Among the 54 supported bindings, about 5 disappear — the already-measured label matches.
2. **The net change in supported bindings will not be −5**, because removing label matches also
   *reduces* position counts: operands that were `SEVERAL_POSITIONS` because one of their positions was
   a label can become `UNIQUE_POSITION` and then acquire a corroboration. The net may be smaller than
   5 and may even be positive.
3. Far more operands change their `n_positions_in_prefix` **feature** than change their resolver
   verdict, so the learned rule's **applicability** (28 operands on the learning records, 26 on the
   checking records) will change by more than the binding counts do.
4. The single counterexample — operand `14` in `14 * 1935.5 = 27,097`, p1/UID0014 — matches
   `Unnamed: 14_level_2`. Under the correction it has no occurrence at all, so the rule becomes
   **silent** on it rather than refuted by it, and precision-where-it-applies on the learning records
   rises from 0.964 to 1.000. **That is an extraction correction and not learner repair.** The
   hypothesis class is no more separable than before; one unseparable case stopped being presented to
   it.
5. `source_traced` will move in **both** directions: an operand that loses its only position becomes
   `UNRESOLVED` (more OUTSTANDING), and an operand that gains a unique corroborated position becomes
   `SOURCED` (more DISCHARGED).

**What this does not establish.** A bare-numeral match still says only *which cell*, never that the
cell carries the requested measure, period or column. That question is untouched.

---

## D-3 — 2026-11-22. Four meanings the report conflated, and a count that accused an actor

**Recorded before implementation**, from an audit of `oqa_ln_check` against the report it actually
delivered in the paired rehearsal (`v4_rehearse/DELIVERED_REPORT.md`).

**D-3a. `source_traced OUTSTANDING` was counted as an actor execution diagnosis.** The branch that
reports unresolved operands passes `actor_execution_diagnosis=True`, and `_pack` turns that into the
report's `COUNTS actionable execution diagnoses 1`. Missing provenance is **not** an actor error: the
records may simply not settle where a number came from. The flag is removed from that branch, the
count is renamed to separate *actionable execution diagnoses* from *unresolved provenance*, and
`OUTSTANDING` is stated in the report as "not settled by the records", never as a demonstrated
violation.

**D-3b. A direct lookup was required to have a calculation.** When no written calculation yields the
committed answer, `answer_traced` is set **VIOLATED** with an execution diagnosis. For a question whose
task requirement is a single cited value — no arithmetic at all — committing an answer with no
calculation is correct behaviour, and the accusation is false. The state becomes **UNKNOWN** with the
reason stated when the derived task requirement needs no arithmetic, and stays VIOLATED only when the
question requires an operation over two or more quantities.

**D-3c. `answer_traced HOLDS` reads as a correctness claim, and in the rehearsal it was read as one.**
The assisted analyst wrote "reached the **correct** numeric answer". The obligation means only that the
committed answer is the result of a statement the actor wrote. Each obligation now carries a one-line
statement of what it does and does not establish, and the report's header separates four things that
were previously run together: **provenance traced**, **arithmetic checked**, **task requirements
satisfied**, and **benchmark correctness** — the last of which this report never reads.

**D-3d. `SOLE_IN_ROW` was printed in the corroboration list after being rejected as support.**
`oqa_occ.corroborations` returns it, `bind` excludes it from `supporting`, and nothing in any decision
reads it — verified by code path, not by assertion: `binding` is `SUPPORTED if supporting else
UNKNOWN`, and `supporting` filters on `SUPPORTING = ("ROW_LABEL", "QUERY")`. So its appearance in
`corroborated by ROW_LABEL, QUERY, SOLE_IN_ROW` is **display only**, and the display is misleading
because it presents a rejected criterion beside two accepted ones. The report prints the supporting
corroborations, and names the non-supporting ones separately as *observed, not counted as support*.

**D-3e. "Superseded" conflated an abandoned alternative with a live ancestor.**
`oqa_ln_evidence.recovery_shape` marks **every** earlier calculation whose result differs from the
committed answer as superseded, and the report prints them under "WORK THAT WAS REPLACED". In
p4/UID0063 one of the two so-marked statements is `5,541,720 - 5,459,946 = 81,774`, and 81,774 is an
**operand of the supporting calculation** — the final answer consumes its result. It was not replaced;
it is an ancestor. Occurrence-level dependency classes are introduced for this
(`research/officeqa/oqa_d2_ancestry.py`), and the report distinguishes work that was abandoned from
work that is still consumed.

**Predicted before implementation.**
1. The rehearsal report regenerated after D-3 loses its `actionable execution diagnoses 1` line in that
   form; the same fact appears as unresolved provenance.
2. At least one of the seven `answer_traced VIOLATED` states in the post-restage table is a question
   that needs no arithmetic, and becomes UNKNOWN.
3. At least one calculation currently printed as replaced is an ancestor of the committed answer.
4. No decision anywhere changes because of D-3d; only text changes.

---

## D-4 — 2026-11-22. A binary match inside a larger expression was reported as false arithmetic

**Order of events, recorded honestly.** D-2 and D-3 were written before their code. This one was not:
I found the defect while inspecting the "false ancestor" §5 of the follow-up instruction asked me to
check, implemented the detector to see how large it was, and am recording the amendment now — before
any result that uses it is reported, but after the change. The inspection that motivated it is quoted
below and was read-only.

**The defect.** `oqa_ln_evidence._CHAINED` counts `a OP b OP c =` and declares it out of scope.
It does not fire when the larger expression contains a parenthesis. In p4/UID0085 the actor wrote,
verbatim:

```
- California percentage = 472,027,235 / 3,564,583,961 × 100
- California percentage = 0.1324213... × 100 = 13.24213...%
```

and `_CALC` extracted from it the binary statement `3564583961 * 100 = 13.24213093` — the **divisor**
read as a left operand and the numerator dropped — which of course does not hold. The milestone report
(§8) repeated that as a fact about the actor: "one such earlier statement … does not hold as written."
**The actor's arithmetic is correct. The parse is wrong.**

**How large it is.** Over the 48 frozen attempts: 676 binary statements, **38 are fragments of a
larger expression**, and those 38 are **38 of the 50** statements that are arithmetically inconsistent
as parsed — **76% of every "the arithmetic does not hold" finding in this evidence set is an extraction
artifact.**

**The correction.** A match is a fragment when the first non-space character before it is an operator
or a closing parenthesis that is itself preceded by a digit, a closing parenthesis or a percent sign —
that is, when the match continues an expression that began earlier. A markdown bullet such as
`- 100 - 50 = 50` is not a fragment, because the `-` is preceded by a line break rather than by a
value.

**A fragment is flagged, not deleted.** Deleting it would change the frozen 676 calculations and 1,352
operands and make every earlier number incomparable. What changes is how it is READ: `arith_recorded`
is **UNKNOWN** for a fragment, never VIOLATED, and the ancestry audit reports it as out of scope rather
than as an arithmetic error. This is what the instruction asks for in as many words — "keep chained or
unsupported expressions outside scope rather than treating them as false".

**What it does not fix.** The fragment's operands still exist in the record and still carry features
and verdicts, so the learner's row counts are unchanged by design. A fragment is evidence that the
parser saw something it cannot represent, not evidence about the actor either way.

---

## D-5 — 2026-11-22. Seven accusations, and neither of them was about the actor

**Recorded during implementation, before any result that uses it is reported**, like D-4 and for the
same reason: the defect was found by inspecting the attempts the checker accused, which §6 of the
follow-up instruction asks for in as many words — "a direct lookup need not have a calculation stage;
absence of a parsed calculation must not automatically accuse an actor".

**The finding.** Of the 25 attempts in the stage set, **seven** carried `answer_traced VIOLATED` with
`actor_execution_diagnosis=True` — the checker's strongest accusation. **All seven are false, and
they fall into exactly two extraction classes.**

**(a) A committed answer that is not one number.** `<answer>[10102000000, 4.73]</answer>` (UID0017, a
two-part question) was pushed through `num(re.sub(r"[^\d.,-]", "", raw))` and became
**101,020,000,004.73**; `<answer>March 3, 1977</answer>` (UID0033) became **31,977.0**. No calculation
can yield a value like that, so the chain reported "the committed answer is the result of no written
calculation" and blamed the actor. **Eight of the 48 frozen attempts commit an answer of this kind.**
The committed answer is now read only when it is a single numeral; otherwise it is UNKNOWN with the
raw text quoted, because a value this parser cannot represent supports no claim in either direction.

**(b) Arithmetic written with absolute-value bars.** All four UID0065 attempts wrote, verbatim:

```
- |2.0% - 0.2%| = 1.8%
```

The binary-statement regex cannot match across the bars, so the attempt reported **no written
calculation at all** and the actor was accused of committing 1.8 with no work behind it. The work is
on the page. When no calculation the parser can read yields the committed answer **and** the attempt
contains arithmetic the parser cannot represent — absolute-value bars, a chained expression, or a
fragment of one (D-4) — `answer_traced` is **UNKNOWN**, with the count of unrepresented expressions
stated.

**Measured effect on the stage set**, same 25 attempts, same two frozen models, nothing retrained:

| | before D-3/D-4/D-5 | after |
|---|---|---|
| `answer_traced` VIOLATED | **7** | **0** |
| `answer_traced` UNKNOWN | 9 | 16 |
| actionable execution diagnoses, pooled | **7** | **0** |
| unresolved provenance (a new, separate count) | — | 7 |

**What this does not establish.** Nothing here says the actor was right. Six of the seven withdrawn
accusations become UNKNOWN because the records cannot settle the question, which is the honest state,
not a clearance. Whether UID0017 read the right cell, and whether 1.8 is the right difference, are
untouched — and this checker never reads a score, so it cannot say.

**A conclusion that follows for the whole method.** Every accusation this checker made on 25 attempts
was an artifact of its own parser. That is the strongest available evidence that a contract report's
value depends far more on extraction fidelity than on the learned layer above it.

---

## D-6 — 2026-11-22. An implementation-parity audit module, additive and read-only

**Recorded before the module was used for any conclusion.** The follow-up instruction of 2026-11-22
asks whether the OfficeQA and ALFWorld learned-contract implementations share a core, whether their
active behaviours are equivalent, and where OfficeQA diverges from the accepted design. That question
is answered by measurement, not by reading source, so this amendment records one new module.

**What is added.** `research/officeqa/oqa_pa_parity.py`, with two probes and no side effects on any
existing artifact:

* `callpath(freeze, model)` runs every frozen attempt's stage report **twice** — once with the learned
  contract and once with it absent — and counts the calls into `oqa_ln_learn.applies` and
  `oqa_ln_check.classify_operand`, recording which top-level fields of the report differ. The absent
  condition is the checker's **own** `None` path (`oqa_ln_check._applies`), not a second
  implementation, so the two sides cannot drift apart.
* `target(freeze)` measures whether the learned rule's antecedent entails its consequent through
  supplied code: for every actor-written value with exactly one observed numeric cell, whether the
  occurrence came from a `grep` and whether `oqa_occ.corroborations` set `QUERY`.

**What is NOT changed.** No existing module's behaviour, no vocabulary, no stage semantics, no
extraction rule, no Lean definition, no report text, no ALFWorld file. The probes read
`LN_FREEZE.json`, the saved `conversation.json` records and the two saved models, and write only into
a new directory, `runs/20261122-officeqa-parity/`. `tests/test_officeqa_pa.py` asserts the module
names no answer key, no held-out record and no provider field, and imports no `skillopt` module.

**Why a probe instead of a reading of the source.** `oqa_ln_check.stage_report` receives the contract
and passes it to `_applies`, so the source alone does not say whether a conclusion depends on it. The
measurement does: over all 48 frozen attempts the contract is consulted 18 times, the origin
classifier is called 36 times and sees the contract 0 times, and every obligation state and every
operand class is identical with and without it. That extends the D-2 2x2 — which ran on 25 attempts —
to the learning side as well.

---

## E-1 (2026-11-22, 19:50-20:38 EST) — the ALFWorld framework ported into OfficeQA, rule `OQA-FW-1`

**Why.** PA-1 established that the two branches shared no code and that OfficeQA's milestone used the
narrowest of its three generations: one kind, two statuses, no assumptions, and a repair that could
only append a condition. The instruction that follows PA-1 asks for one learned action-and-stage
framework across benchmarks with benchmark-specific adapters, and for an end-to-end slice rather than
another audit.

**What is added.**

* `research/officeqa/framework/upstream/` — a one-time byte copy of 32 ALFWorld sources (v1 core, its
  vocabulary, VX-1, RP-1, EB-1) with `MANIFEST.json`: per-file sha256, byte count, source mtime and
  git status, plus the source commit `4a9914a9005a`. All 32 are untracked in the source, so their
  hashes are their only identity. The copy was read-only on that worktree: no file written, no module
  imported, no byte-compilation cache created there, verified afterwards.
* `research/officeqa/framework/fw_{kleene,domain,ir,state,check,learn,repair}.py` — those mechanisms
  with the ALFWorld vocabulary lifted out into a `Domain` object. Function-level provenance is in each
  module docstring.
* `research/officeqa/oqa_fw_{extract,domain,learn,stage,report,run,repair,lean,edit}.py` — the
  OfficeQA adapter: document entities, the first slice, the report, the drivers.
* `research/cluster/oqa_fw_lean.sbatch`, registered in `research/submit.sh`. Deliberately separate
  from `oqa_lean.sbatch`, which the frozen D-1 receipt points at.
* `tests/test_officeqa_fw.py` — 22 tests, all passing.

**Three additions to the framework beyond a copy**, each available to every domain:

* the kind `concludes` — `completes` generalised from "the task is complete" to a named consequence,
  with `subject` variables it is checked POINTWISE over. A document-interpretation rule is a claim
  about every cell, not about some cell.
* `subject_scope` — temporal scope, `all` / `introduced` / `once`. `once` checks each entity at the
  first state where the record settles both sides of the rule, which matters here because the
  rendering that adjudicates a column can arrive thirty steps after the column did.
* typed atom arguments — an atom position naming an entity type admits only a variable declared with
  that type. This is a schema check, and it cut the first slice's hypothesis space from 3,358
  candidates to 378.

**What is NOT changed.** No ALFWorld file. No actor, tool, document rendering, scorer or frozen
baseline artifact. `oqa_lc_cells`, `oqa_cc_extract`, `oqa_ln_evidence`, `oqa_cc_ir` and `oqa_ln_check`
keep their behaviour byte-for-byte: the pairing guard, the precision-aware numeric comparison and the
new report all live in the layer above, under a new version. `oqa_reports.py` gains ONE additive
branch for condition `FW` that returns before the three existing LC conditions' path, with a test
asserting that order and their unchanged headings. Nothing new enters `skillopt`.

**The two extraction corrections**, both under `oqa.fw.extract.v1` and both measured:

* stable observation references, so a cell from an unnumbered `read` can be cited. Every `read`
  observation in the 48 frozen attempts is unnumbered and reads carry **571 of the 757 observed
  tables (75.4%)**; the LN generation could cite only line-numbered grep output.
* comparison at the written precision. `oqa_ln_evidence._near`'s absolute floor of 0.5 makes 1.8 equal
  to 2.0; `near_legacy` is kept beside the correction so the difference is measurable.

**Three defects of mine, recorded because each was invisible until something re-derived a number.**
The unnamed-entity token was missing, so an existential was FALSE before its entities appeared instead
of UNKNOWN — which turned a rule with 6 supporting and 2 refuting columns into one with 0 and 0. The
operand's written text was read as `text` where `oqa_cc_extract` writes `raw`, silently disabling the
precision correction. And the conversion obligation was raised for every number the actor typed: 70 of
one attempt's 227 were reported OUTSTANDING and **none survived the audit**, so the obligation is now
scoped to numbers inside a written calculation, a stated factor is sought across the whole prefix, and
a differing factor resolves to UNKNOWN rather than to a violation.

**Full report:** `docs/research/OFFICEQA_FRAMEWORK_PORT.md`, mirrored at `HANDOFF_LATEST.md`.
Artifacts: `runs/20261122-officeqa-framework/`.
