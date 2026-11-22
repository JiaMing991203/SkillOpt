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
