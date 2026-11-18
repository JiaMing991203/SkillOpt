# ALFWorld context-aware contract checking: study plan

**Rule OQA/AWC-1. Recorded 2026-11-18, BEFORE any checker is changed, as §1 and §7 of the instruction
require. Offline: zero actor or optimizer API calls, zero GPUs, zero new gameplay, saved training
traces only, ≤2 requested CPU job-hours at ≤2 CPUs per job.**

The question: **does connecting permitted observations across a trace improve contract and stage
checking, while preserving uncertainty and avoiding false diagnoses?** The idea is OfficeQA proposal
**P2** — "let an already-observed scope witness a fact when it adequately supports it", which in OfficeQA
meant letting a fully observed table header row witness the absence of a nearer unit override.

---

## 1. What already exists — established BEFORE proposing anything

This is the part of the instruction that changes the shape of the study. **The behaviour this study was
asked to add is, in large part, already implemented in ALFWorld**, in the sibling worktree
`~/dev/lean_skillopt/SkillOpt-learned-contract` (branch `contract/learned-contract`, the work
**uncommitted**, 42 untracked paths). Nothing below is a new contribution of mine.

### 1.1 The shared observational fact ledger — amendment A4 §3.1–3.2, `fact_ledger.py`

`LedgerEstimator` already does what §5 of the instruction lists, by name:

| the instruction asks to track | where it already is |
|---|---|
| the specific object / receptacle a fact concerns | `lit_key(atom, args)`; `_changes()` compares `a["obj"] == args[0]`, so a fact about one instance never transfers to another of the same type |
| its observation or inference source | every value carries `OBSERVED` / `INFERRED` / `FORECAST` supports with `basis` ∈ observed / record / static / inferred(assumption) |
| the contract or assumption supporting an inference | `support = {contract, version, step, assumptions}` |
| when the fact becomes valid | "valid from the pre-state of k+1"; `inferred_at()` returns `timed="current"` at k+1 and `timed="persistence"` later |
| later observations that invalidate it | `WITHDRAWN` (an observed command's own report changes the literal) vs `CONTRADICTED` (an observation disagrees and no observed command explains it), with an event log |
| which stage requirements consume it | `queries` logs every value query with its support class, and the stage layer reads through the same estimator |

It also already separates **a new tracker state from new information**: a conclusion is `ADDITIONAL`
only if neither the observation estimator nor the ordinary tracker gives the same value at the same
prefix; otherwise it is `OVERLAP` or `OVERLAP_TRACKER`. A4 §3.1: "The baseline is never given less: no
observation is withheld from it." And `propagate=False` reproduces the frozen estimator exactly, so the
A/B comparison is a constructor argument, not a fork.

### 1.2 The stage-layer context connection — amendment A4 §3.4, `a4_reading.py::ReadingA4`

**The lamp example in this instruction is A4 §3.4, already specified and implemented on 2026-11-11.**
Its own words: the counterexample is `Q/FRESH/s3/tr:0293`, where "at s39 the actor turned the lamp on
while the tissuebox lay on the same dresser; at s41 it picked the tissuebox up", and "the old
definition, 'lamp used while the object is held', rejects this success". The revised reading discharges
INSPECT(o, l) when **either** (a) l is used while o is held, **or** (b) o is held now and a lamp of the
named type was turned on earlier with no recorded command turning it off, and at that moment either o
was already held or o was later picked up from the receptacle of that arrival with no arrival elsewhere
in between.

Already present, and each of them is a caution this instruction raises:

* **The persistence rule is stated and named**: `lamp_stays_on` = "no recorded command turns a lamp off
  (ALFWorld's command set has none)". It is not assumed silently.
* **The location rule is stated and named**: `same_place`, and a pre-freeze review *removed* a branch
  that used "o was listed at that receptacle", precisely because "an object can be listed at two
  receptacles and the actor may pick it up elsewhere" — the instruction's "a receptacle name is not
  automatically a physical-location identity".
* **Two-object identity**: rev 2 clause (c) requires both instances in the **same destination receptacle
  instance**, from counterexamples tr:3287 and tr:2785.
* **Transformations stay tied to the object and can be superseded**: clause (d), "the task's
  transformation must be the LATEST temperature transformation of that instance before its placement"
  (counterexample tr:2658).
* **Events, never properties**: `stage_common.py` header — "a heat / cool / clean of o recorded at step
  k is a REC fact, never 'o is hot / cold / clean'; 'the lamp was turned on at step k' is a REC fact,
  never 'the lamp is on now'".
* **Unknown is never absence**: `listing_complete` is an EXTENDED assumption used only by secondary
  claims; "an object missing from a listing is observational absence, not established physical absence".
* **`rev=1` reproduces the pre-revision reading.**

### 1.3 The recorded limits, also already on the record

* **The contract vocabulary has no lamp-state atom**, so lamp state is a stage-layer record event and
  can never be a contract fact (A4 §3.4, "Recorded limit"); every info-study learner listed `use` as
  uncovered.
* **No co-location relation** in the vocabulary, which is the recorded cause of several unexplained
  `go` failures.
* Both appear in the sibling track's PENDING user proposals as "co-location and lamp-state
  representation", together with "INSPECT rev-3 fresh test" after a further counterexample (tr:0281).

### 1.4 So what is actually missing

Not the mechanism. **The evidence.** The rev-2 revision's effect was established once, by hand — "a
deterministic check over all 187 records: in 81 of 84 successful episodes the goal reading first holds
exactly at the last step; in 3 it held earlier" — and reported as prose in a state note. There is:

* no tool that runs the two readings side by side over a declared set;
* no frozen, outcome-balanced manifest for such a comparison;
* no per-change classification of the kind §7 asks for;
* no regression suite for the specific soundness traps §5 lists;
* no Lean statement of the revised reading's invariants.

**This study supplies exactly those five things, and nothing else.** A checker change will be made only
if the measurement demonstrates a gap, and then only under an amendment recorded first. The already
pending proposals (rev-3, lamp-state atom, co-location) are **not** implemented here: implementing a
rule against a counterexample that is already known would be fitting the checker to a known failure,
which §2 forbids.

---

## 2. The diagnostic set, and the mechanical selection rule

**Source:** saved **training** rollouts only — `runs/*_evolve_*/skillopt/steps/step_*/rollout/` with
`results.jsonl` and `predictions/<id>/trace.json`. Every id in that pool is a `tr:` (train-split) id:
736 attempt rows, 144 distinct tasks, all six task types present with 20–27 tasks each and 2–11 saved
attempts per task. **No `seen:` or `unseen:` id and no `*_evaluate_*` run is read**, so no held-out
trajectory is opened, and the 12 held-out tasks of the frozen info-study selection
(`study/STUDY_TASKS.json`) stay untouched.

**The rule, fixed before the manifest is built:**

1. Candidate = a `tr:` task with at least one saved `trace.json` in a training rollout.
2. A task is **outcome-balanced** if the pool holds both a scored-success attempt (`hard == 1`) and a
   scored-failure attempt (`hard == 0`) of it.
3. Within each of the six types, order candidates by `(not outcome_balanced, id)` — balanced first, then
   by id, ascending and lexicographic — and take the first two.
4. Of those two, the one with the **lower id** is the **development** task; the other is the
   **checking** task. Their task directories must differ; the rule asserts it.
5. Attempts per task: at most two — the earliest-ordered success and the earliest-ordered failure; if
   only one outcome is available, the two earliest attempts of that outcome. Order is
   `(run, step, id)`, lexicographic.
6. **One recorded exception.** For `look_at_obj_in_light` the development slot is forced to **tr:0293**,
   the lamp counterexample this instruction names, so that it is a development/regression case and not a
   checking case. The checking task for that type is still rule 3's second pick.

Unavailable categories are to be reported, never manufactured. All of it is reused research material;
the checking half is reserved for this patch's assessment and is **not** an untouched benchmark.

---

## 3. The two conditions, with the information and action model matched

| | |
|---|---|
| **Current checker** | `a4_reading.ReadingA4(sp, type_of, rev=1)` — the actual existing implementation at its pre-revision reading, plus `stage_common.Reading` unchanged |
| **Context-aware checker** | `a4_reading.ReadingA4(sp, type_of, rev=2)` — the existing revision: the lamp connection (b), same-receptacle (c) and temperature-override (d) |

Both receive the same prefix (task sentence + executed commands and their observations), the same frozen
vocabulary, the same `type_of`, and the same declared assumptions. No PDDL, no engine state, no expert
plan, no actor reasoning, no later step. Nothing is relearned and no contract is proposed.

**Model lineage, to be recorded per record:** the vocabulary digest, the reading revision, the assumption
set, and whether the record was in the evidence of the revision itself. tr:0293, tr:3287, tr:2785 and
tr:2658 **are** the counterexamples the revision was built from, so any result on them is **in-sample and
retrospective** and will be labelled so. A result on a record that played no part in the revision is
out-of-sample for the revision but still reused development material.

---

## 4. Replay with explicit time boundaries

For each record, in order, for each step t:

1. **PRE(t)** — with the prefix `steps[:t]` only: the stage state, the supported facts and their
   supports, and the goal reading. Committed and written out before step t's observation is read.
2. **POST(t)** — after the observation: the observed outcome, the stage updates, and which supports were
   added, withdrawn or contradicted.
3. **RETROSPECTIVE** — only after the last step: the episode's scored outcome compared with the final
   goal reading.

The final outcome never establishes an earlier fact. It is used only to classify the *end* state
(agreement or disagreement between the goal reading and the score) and for post-hoc adjudication.

---

## 5. Classification of every changed result (§7)

Each `(record, step)` where rev 1 and rev 2 differ gets a witness and exactly one label:

`corrected_progress_recognition` · `valid_additional_execution_diagnosis` ·
`more_precise_or_earlier_existing_diagnosis` · `removal_of_a_false_diagnosis` ·
`redundant_information` · `unsupported_inference_or_unresolved_unknown`

Model refutation is kept apart from actor error. A new tracker state is not counted as new information
for the optimizer unless the saved ordinary SkillOpt analysis for the same attempt lacks it.

---

## 6. Soundness regressions (constructed, labelled separately from traces)

1. A supported fact survives irrelevant events.
2. An explicit invalidation and a conflicting observation both withdraw support, with different labels.
3. Two same-type instances keep separate identities.
4. Alternate valid action orders both discharge a stage.
5. An incomplete listing leaves an object's presence unknown, never absent.
6. Recovery after a detour restores support.
7. **The lamp traps:** a lamp-on with no later off supports "on" only under the stated rule; a lamp-on
   at a different receptacle does not support the stage; an unmodelled action leaves uncertainty.

---

## 7. Freezing, Lean, and what will not happen

The complete semantic bundle — the reading revision, the assumptions, the extraction, the stage
definitions, the renderer, the configuration and the manifest — is frozen by digest **before any
checking record is opened**. If checking reveals a bug, the original result is preserved and any
repaired recheck is labelled developmental.

Lean: the invariants of the revised reading over a finite domain, with the existing harness. What Lean
proves and what stays dependent on extraction and on the empirical assumptions is stated explicitly.

**Not in this study:** skill editing, fresh rollouts, performance evaluation, another campaign, a new
learned contract, a vocabulary change, rev 3, or any paid call.
