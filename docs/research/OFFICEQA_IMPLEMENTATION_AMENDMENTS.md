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
