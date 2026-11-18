# ALFWorld context-aware contract checking: offline study

**Rule AWC-1. 2026-11-18. Offline: zero actor or optimizer API calls, zero GPUs, zero new gameplay,
saved training traces only. 0.6 of 2 authorized requested CPU job-hours. Plan recorded before any
checker was touched: `docs/research/ALFWORLD_CONTEXT_STUDY_PLAN.md`. Artifacts:
`$RUNS/20261118-alfworld-context/`.**

The question: **does connecting permitted observations across a trace improve contract and stage
checking, while preserving uncertainty and avoiding false diagnoses?** The idea came from OfficeQA
proposal **P2** — "let an already-observed scope witness a fact when the observed evidence supports
it", which in OfficeQA meant letting a fully observed table header row witness the absence of a nearer
unit override.

**Abbreviations used once and then freely.** *Stage reading* = the deterministic reader that turns one
trace's observations into task progress. *Clause (a)/(b)/(c)/(d)* = the four stage rules compared below.
*A4* = the fourth amendment to the learned-contract design (`LEARNED_CONTRACT_DESIGN_AMENDMENT_A4.md`,
2026-11-11), which specified the context connection. *rev 1 / rev 2* = that amendment's two reading
revisions. *tr:NNNN* = an ALFWorld training-split task id. *hard* = the benchmark's own binary score.

---

## The five answers, first

**1. What did the current checker already do?** **Almost all of it.** The behaviour this study was
asked to add is already implemented and was specified seven days ago. A4 §3.1–3.2's fact ledger already
attaches to every fact its object, its source, the contract and assumptions behind it, the step from
which it is valid, the later observation that withdraws or contradicts it, and — the part that matters
most — a label saying whether the inference *added* anything (`ADDITIONAL`) or merely agreed with what
the ordinary tracker already knew (`OVERLAP`, `OVERLAP_TRACKER`). A4 §3.4 already connects an earlier
lamp-on to a later holding under two named rules, `lamp_stays_on` and `same_place`, and a pre-freeze
review had already removed an unsound branch of it for exactly the reason this instruction gives — that
a receptacle listing is not a location identity. I claim none of that as new.

**2. What did the extension change?** I wrote **no extension**, because §1 says not to claim existing
functionality and not to build a weakened baseline. What I built is the evidence that did not exist: a
frozen outcome-balanced manifest, a replay that runs the readings side by side with explicit time
boundaries, a per-change classification with witnesses, 27 constructed soundness regressions, and the
Lean invariants. Two defects turned up in the course of building it, both reported below: `rev=1` does
**not** reproduce the pre-revision reading for Examine-in-Light, and my own first comparison of saved
ordinary analyses was looking one directory too shallow.

**3. Did the transfer produce useful diagnostic improvement?** **A little, and not in the expected
direction.** Over **736 saved training attempts** it changes **19** of them (2.6%). **Eighteen of the
nineteen make the reading STRICTER** — they withdraw or delay a completion claim the old reading made
too early — and only one makes it more permissive (the lamp case). The single most useful change is on
a scored failure: on tr:2986 the old reading claims the task complete at step 45; the context-aware
reading withdraws that, because the two cups are in **cabinet 3 and cabinet 6**, and the saved ordinary
SkillOpt analysis for that step attributes the failure to a navigation loop in which the agent "never
find[s] the goal object" — which is **false of that attempt**. So one change both removes a false
progress claim and corrects an attribution.

**4. Did it introduce false certainty?** **No, in 736 attempts.** There is **not one case** where the
context-aware reading claims a completion on an episode that scored 0, and the one case where the old
reading did claim one is the case the context reading withdraws. Lean states the corresponding
invariants: no discharge without the object held, a lamp-on elsewhere never discharges, no observed
lamp-on means silence, and clauses (c) and (d) can only ever withdraw.

**5. What remains unsupported?** The lamp connection's yield is **1 in 736**, and that one attempt is
the counterexample the rule was written from — so the out-of-sample yield of clause (b) is **0 of 735**.
The contract vocabulary still has **no lamp-state atom** and no co-location relation, both recorded
limits, so lamp state can never be a contract fact and stays a stage-layer record. And the frozen
24-attempt diagnostic set this instruction specifies shows **zero** differences: a 24-attempt set cannot
see a 2.6% phenomenon, which is a fact about the study design worth more than the result it produced.

---

## 1. What already existed, with citations

### 1.1 The fact ledger — A4 §3.1–3.2, `fact_ledger.py` (12,613 bytes, sha `c805634fc0e0`)

| the instruction's §5 list | where it already is |
|---|---|
| the object / receptacle a fact concerns | `lit_key(atom, args)`; `_changes()` tests `a["obj"] == args[0]`, so a fact about one instance never transfers to another of the same type |
| its observation or inference source | every value carries its supports: `OBSERVED` / `INFERRED` / `FORECAST`, with basis observed / record / static / inferred(assumption) |
| the contract or assumption behind an inference | `{contract, version, step, assumptions}` on each inferred support |
| when the fact becomes valid | "valid from the pre-state of k+1"; `inferred_at()` returns `timed="current"` at k+1 and `timed="persistence"` later, and **only if `persistence` is in the model's assumption set** |
| what invalidates it | `WITHDRAWN` (the observed command's own report changed the literal) vs `CONTRADICTED` (an observation disagrees and no observed command explains it), each logged as an event |
| which stage requirements consume it | every value query is logged with its support class |

And the distinction the instruction insists on — "showing a new tracker state does not automatically
establish new information" — is already a label: a conclusion is `ADDITIONAL` only when neither the
observation estimator nor the ordinary tracker gives the same value at the same prefix. A4 §3.1: *"The
baseline is never given less: no observation is withheld from it."*

### 1.2 The stage-layer context connection — A4 §3.4, `a4_reading.py` (sha `70b707f4f10a434f`)

The lamp example in this instruction **is** A4 §3.4. Quoting the amendment: the counterexample is
`Q/FRESH/s3/tr:0293`, where *"at s39 the actor turned the lamp on while the tissuebox lay on the same
dresser; at s41 it picked the tissuebox up"*, and *"the old definition, 'lamp used while the object is
held', rejects this success"*. The revised reading discharges the lighting stage when **either**

* **(a)** the lamp is used while the object is held (unchanged), **or**
* **(b)** the object is held now, a lamp of the named type was turned on earlier with no recorded
  command turning it off, and at that moment either the object was already held or it was later picked
  up at the receptacle of that arrival with no arrival elsewhere in between.

Two further rules came with the same revision:

* **(c)** a two-object task needs both instances in the **same destination receptacle instance**
  (counterexamples tr:3287, tr:2785);
* **(d)** the task's transformation must be the **latest** temperature event of that instance before its
  placement, so a later cool supersedes a heat (counterexample tr:2658).

Every caution in this instruction was already honoured there: the persistence rule is **named and
justified** (`lamp_stays_on` = "no recorded command turns a lamp off (ALFWorld's command set has
none)"); the location rule is **named** (`same_place`) and a pre-freeze review deleted a branch that
used "the object was listed at that receptacle" *"because an object can be listed at two receptacles and
the actor may pick it up elsewhere"*; a transformation is a **recorded event**, never a property —
`stage_common.py`'s own header says *"'the lamp was turned on at step k' is a REC fact, never 'the lamp
is on now'"*; and listing completeness is an **extended** assumption that no primary finding may use,
so an object missing from a listing is observational absence, not established absence.

### 1.3 What did not exist

The **evidence**. The revision's effect had been established once, by hand — *"a deterministic check
over all 187 records: in 81 of 84 successful episodes the goal reading first holds exactly at the last
step; in 3 it held earlier"* — and reported as prose in a state note. There was no tool that ran the two
readings side by side, no frozen manifest for such a comparison, no per-change classification, no
regression suite for these specific traps, and no Lean statement of the revised reading's invariants.
This study supplies those five things and changes no checker.

---

## 2. Two defects found while building the comparison

### 2.1 `rev=1` is not the pre-revision reading (in the existing code)

`a4_reading.py`'s docstring says *"rev=1 reproduces the reading used for Study A's frozen reports"*. For
`look_at_obj_in_light` **it does not.** `ReadingA4.goal_now` mentions `self.rev` exactly once, gating
clause (c); the lamp clause (b) is applied **unconditionally**:

```python
def goal_now(self):
    if sp["form"] == "two" and self.rev >= 2:      # clause (c) IS gated
        ...
    if sp["form"] != "light":
        return super().goal_now()
    a = set(super().goal_now())                     # clause (a)
    b = set()                                       # clause (b) - no rev test anywhere below
```

Measured consequence on the named counterexample: `rev=1` and `rev=2` both recognise the completion at
step 41 on basis (b). **An A/B over rev 1 vs rev 2 would therefore have reported "no change" for the one
task type the revision was written for.** The honest baseline is `stage_common.Reading` itself — the
frozen shared reading that conditions P, S and C all used — and that is what "current" means everywhere
below. `tests/test_alfworld_context.py::test_rev1_does_not_reproduce_the_pre_revision_lamp_reading`
records the behaviour that is actually in force so that a later change to it is deliberate.

This is reported, not repaired: `a4_reading.py` belongs to another track, is uncommitted in its own
worktree, and changing it would alter frozen Study A/B/C provenance.

### 2.2 My own ordinary-analysis lookup was one directory too shallow

`awc_replay.ordinary_analysis` derived the step directory with two `dirname` calls where the path needs
four, so every lookup reported "not found" and the comparison with the saved SkillOpt analysis came back
empty for the wrong reason. Fixed, re-run, and it changed a conclusion — see §6.

---

## 3. The frozen diagnostic set

**Mechanical rule, recorded in the plan before it ran** (plan §2, rules 1–6): candidates are `tr:`
tasks with a saved trace in a **training** rollout (`*_evolve_*` only — no `*_evaluate_*` run and no
`seen:`/`unseen:` id is ever opened, so no held-out trajectory is read); outcome-balanced tasks first,
then by id; the lower id develops and the other checks; two attempts per task, the earliest success and
the earliest failure; and one recorded exception forcing the lamp task into the development slot.

Pool: **736 attempt rows, 144 tasks**, all six types present (20–27 tasks each, 4–9 outcome-balanced).
Manifest digest **`5517922e569456b2`**, written read-only before any replay.

| role | type | task | task directory | attempts (hard) |
|---|---|---|---|---|
| development | Pick & Place | tr:0392 | `pick_and_place_simple-Box-None-DiningTable-223` | 2 (1, 0) |
| checking | Pick & Place | tr:0561 | `pick_and_place_simple-CreditCard-None-Desk-307` | 2 (1, 0) |
| development | Pick Two & Place | tr:2744 | `pick_two_obj_and_place-AlarmClock-None-Desk-30…` | 2 (1, 0) |
| checking | Pick Two & Place | tr:2785 | `pick_two_obj_and_place-Book-None-Drawer-304` | 2 (1, 0) |
| development | Clean & Place | tr:1148 | `pick_clean_then_place_in_recep-Bowl-None-Shelf…` | 2 (1, 0) |
| checking | Clean & Place | tr:1158 | `pick_clean_then_place_in_recep-ButterKnife-Non…` | 2 (1, 0) |
| development | Heat & Place | tr:2332 | `pick_heat_then_place_in_recep-Apple-None-SideT…` | 2 (1, 0) |
| checking | Heat & Place | tr:2449 | `pick_heat_then_place_in_recep-Egg-None-Fridge-…` | 2 (1, 0) |
| development | Cool & Place | tr:1869 | `pick_cool_then_place_in_recep-Bread-None-Dinin…` | 2 (1, 0) |
| checking | Cool & Place | tr:1928 | `pick_cool_then_place_in_recep-Egg-None-Garbage…` | 2 (1, 0) |
| **development** | **Examine in Light** | **tr:0293** | `look_at_obj_in_light-TissueBox-None-DeskLamp-301` | 2 (1, 0) |
| checking | Examine in Light | tr:0010 | `look_at_obj_in_light-AlarmClock-None-DeskLamp-304` | 2 (1, 0) |

All twelve are outcome-balanced, each pair's directories differ, no category was unavailable. All of it
is **reused research material**; the checking half is reserved for this patch's assessment and is not an
untouched benchmark; the balance supports diagnosis and estimates nothing about population performance.

**Deviation, recorded.** Rule 6 forced the lamp *task* into the development slot, but rule 5's
earliest-success / earliest-failure ordering then chose two **other** attempts of it — a 6-step success
and a 50-step failure — neither of which contains the pattern. The attempt the amendment names (the
exp2 FRESH arm, 42 steps) is in the pool but was not selected. Rather than re-freeze the manifest I kept
it and added that attempt as an **explicitly labelled supplementary record**
(`AWC_CASE_lamp_tr0293_fresh.json`). It is in-sample for the revision, so it is a retrospective
regression check, not a fresh test.

**Second deviation, recorded.** The supplementary scans (§5) aggregate over every saved training attempt
of each type and therefore include the checking tasks' attempts; they ran **before** the semantic bundle
was frozen, and one of their changed rows is a (different) attempt of the checking task tr:2785. So the
checking half was not perfectly blind. What the freeze protects against is tuning, and **no checker was
changed at all**, so nothing could be tuned; the loss of blindness is nevertheless labelled here.

---

## 4. The before/after table on the frozen set

| | development (12 attempts) | checking (12 attempts) |
|---|---|---|
| completions recognised by the **current** reading | 6 of 6 successes, at steps 41, 44, 45, 24, 11, 5 | 6 of 6 successes, at steps 13, 35, 25, 21, 9, 15 |
| completions claimed on a **failure** by the current reading | 0 of 6 | 0 of 6 |
| **differences from the context-aware reading** | **0** | **0** |
| of which false certainty introduced | 0 | 0 |

**Zero changes on 24 attempts.** Every success is recognised at the same step by both readings and no
failure is claimed by either. The frozen set, built exactly as instructed, has no power to see the
phenomenon — which is the most useful methodological result here and is the reason for §5.

---

## 5. The supplementary scans: every saved training attempt

**Labelled supplementary, and not a performance evaluation.** Each row is every `tr:` attempt of that
type with a saved trace in a training rollout, replayed under both readings.

| task type | attempts | changed | on a scored success | on a scored **failure** |
|---|---|---|---|---|
| Pick & Place | 155 | 0 | 0 | 0 |
| Pick Two & Place | 157 | **15** | 14 | **1** |
| Clean & Place | 132 | 0 | 0 | 0 |
| Heat & Place | 100 | **3** | 3 | 0 |
| Cool & Place | 112 | 0 | 0 | 0 |
| Examine in Light | 80 | **1** | 1 | 0 |
| **total** | **736** | **19** | **18** | **1** |

Within Examine in Light, the lighting stage was discharged 29 times: **28 on clause (a)** and **1 on
clause (b)**. So the context connection carries one recognition in twenty-nine.

### Classification of all 19, with the in-sample flag

| classification | n | which |
|---|---|---|
| `corrected_progress_recognition` | **1** | tr:0293 (in-sample) — the lamp case |
| `more_precise_or_earlier_existing` | **17** | tr:3287 ×11 and tr:2785 ×1 (in-sample), tr:3042 ×2, tr:2395, tr:2456 (out-of-sample), tr:2658 (in-sample) — every one of them *later*, not earlier |
| `removal_of_a_false_diagnosis` | **1** | tr:2986 (out-of-sample) |
| `valid_additional_execution_diagnosis` | 0 | |
| `redundant_information` | 0 | |
| `unsupported_inference_or_unresolved_unknown` | **0** | |

**Out-of-sample yield** — records that played no part in writing the rules: **5 changes on 4 distinct
tasks** (tr:3042 ×2, tr:2395, tr:2456, tr:2986), every one of them a correction in the stricter
direction. **Clause (b), the lamp connection, has an out-of-sample yield of zero.**

Model refutation versus actor error: **no contract is under test in this study**, so nothing here can
refute a model. Every change is a change to a stage-layer *reading*, and none of the 19 is an accusation
against the actor.

---

## 6. One successful context connection, in plain language

**tr:0293, "examine the tissuebox with the desklamp", 42 steps, the engine scored it a success.**

What the actor did:

| step | action | observation |
|---|---|---|
| 0 | go to desk 1 | *"You arrive at desk 1. On the desk 1, you see … a tissuebox 1 …"* |
| 1 | take tissuebox 1 from desk 1 | *"You pick up the tissuebox 1 from the desk 1."* |
| 2–36 | thirty-five steps of searching | — |
| 37 | go to dresser 1 | *"You arrive at dresser 1. On the dresser 1, you see a alarmclock 2, a cellphone 2, and a desklamp 1."* |
| 38 | move tissuebox 1 to dresser 1 | *"You move the tissuebox 1 to the dresser 1."* (hands now empty) |
| 39 | **use desklamp 1** | *"You turn on the desklamp 1."* |
| 40 | examine dresser 1 | *"On the dresser 1, you see … a desklamp 1, and a tissuebox 1."* |
| 41 | **take tissuebox 1 from dresser 1** | *"You pick up the tissuebox 1 from the dresser 1."* |

* **What was known about the target object:** `tissuebox 1` — the one instance, by name — was picked up
  at step 1, put down on `dresser 1` at step 38 and picked up again at step 41. The reading tracks the
  instance, not the type.
* **What was known about the lamp:** `desklamp 1` was listed on `dresser 1` at step 37 and turned on at
  step 39. That is all: a **recorded event at a step**, never "the lamp is on now".
* **Which assumptions were required:** exactly two, both named and both reported on the finding —
  `lamp_stays_on` ("no recorded command turns a lamp off", admissible because ALFWorld's command set has
  no switch-off) and `same_place` (the receptacle of the arrival at which the lamp was turned on is the
  object's place, because the object was picked up there with no arrival elsewhere in between).
* **Why the current checker misses it:** its rule is "a lamp was used while the object was held". At
  step 39 the actor's hands were empty, and after step 39 it never used the lamp again. So the lighting
  stage is never discharged, although the engine scored the episode a success — a **missed completion**.
* **What the extension changes, and on what evidence:** at step 41 the object is held; the lamp-on at
  step 39 is still supported; the pick-up at step 41 happened at `dresser 1`, the arrival receptacle of
  step 37, with no arrival in between. The stage is discharged at step 41 on basis (b), agreeing with
  the score.

```
PRE(41)   current  goal=[]  held=[]          POST(41) current  goal=[]
PRE(41)   context  goal=[]  held=[]          POST(41) context  goal=['tissuebox 1']
lamp_on   [{step: 39, lamp: "desklamp 1", arrival_recep: "dresser 1", arrival_step: 37,
            held_then: [], requested_listed_there: []}]
```

Note `requested_listed_there: []` — at the arrival of step 37 the tissuebox was still in the actor's
hands, so it was not in the listing. The deleted "listed there" branch would not have helped; the
pick-up branch is what carries the connection. That is the review fix doing its job.

---

## 7. One invalidation, and one withdrawal that matters

### 7.1 Invalidation on real data — tr:2785 attempt 2, "put two book in drawer.", scored 0

The existing machinery, not the extension. Placement support for one instance is established,
confirmed, withdrawn and restored five times, and ends withdrawn:

```
step  3  book 4 -> drawer 1   placed      (the move report establishes it)
step  5  book 4 in drawer 1   confirmed   (a listing of drawer 1 names it)
step  9  book 4               withdrawn   (the actor picks it up again)
step 10  book 4 -> drawer 1   restored    (keyed by instance, never double counted)
step 12                       confirmed
step 13                       withdrawn
step 30                       restored
step 33                       withdrawn   <- the episode ends with the book in hand
```

Neither reading claims a completion. The withdrawal is an **observed** event, not a staleness timeout,
and `book 3` — the other instance of the same type — keeps its own separate record.

### 7.2 The withdrawal that matters — tr:2986, "find two cup and put them in cabinet.", scored 0

```
step 29  move cup 1 to cabinet 3   "You move the cup 1 to the cabinet 3."
step 39  (listing of cabinet 3 names cup 1)        -> confirmed
step 45  move cup 4 to cabinet 6   "You move the cup 4 to the cabinet 6."
step 46  (listing of cabinet 6 names cup 4)        -> confirmed
step 49  (listing of cabinet 3 names cup 1)        -> confirmed again
```

* **Current reading:** two instances of the requested type have supported placements in receptacles of
  the destination type → **goal reached at step 45**. The episode scored **0**.
* **Context-aware reading (clause c):** the two placements must share **one cabinet instance**;
  cabinet 3 ≠ cabinet 6 → **goal never reached**, matching the score.

This is the instruction's own caution as a measured case: *a receptacle name is not automatically a
physical-location identity* — here "cabinet" as a **type** is not "cabinet 6" as an **instance**.

**And its relationship to the saved ordinary analysis.** The `trajectory_digest.json` for that step
reports exactly one failure pattern, and it names this attempt:

> `navigation_loop`: *"Agents re-walk the same 2-4 receptacles/rooms for dozens of steps … instead of
> expanding to unseen locations, so they never find the goal object."*

That is **not true of this attempt**: it found both cups and placed both of them. So the one change on a
scored failure removes a false progress claim *and* contradicts the ordinary analysis's attribution of
the failure. **Whether an optimizer would ever see that depends on a renderer this study does not run**,
and the obligation-level rendering is not part of the frozen bundle.

**For the other 18 changes the comparison is empty, and for a structural reason.** A
`failure_patterns[].task_ids` list names the **failing** attempts of a step; 18 of the 19 changes are on
scored **successes**, so the ordinary analysis does not mention them by construction. Every digest at
every affected step reports the same single pattern, `navigation_loop`. The honest summary: **the
context-aware reading improves when a success is recognised; it does not produce failure diagnoses the
ordinary analysis lacked.** For an optimizer whose input is failure patterns, 18 of the 19 changes add
nothing actionable.

---

## 8. Tests, Lean, resources

**Tests: 2,353 passed, 9 skipped, 368 subtests, exit 0** — job **66385774**, commit `d98fd6a`, from the
pinned bundle on Great Lakes. 27 of those are this study's, and **all 27 are constructed inputs**,
labelled as such in the module docstring; they establish what the code does, never anything about a
trace. They cover: a placement surviving four irrelevant steps; an observed pick-up withdrawing it; a
listing that omits the object marking a **conflict** rather than silently dropping it; a listing that
names a *different instance of the same type* not confirming it; both lamp orders discharging under the
context reading and only the coincident order under the current one; an object never observed being
neither available nor declared absent; a failed command and an unmodelled `examine` changing nothing; a
second placement after a detour **restoring** support; a lamp-on at another receptacle not discharging;
an arrival in between breaking the connection; a lamp-on being a record and not a state; two instances
in different receptacle instances; and a heat superseded by a later cool.

**Lean: 4.20.0, return code 0, all statements accepted** — job **66385916**, 46 statements of which 20
are evidence examples, 0 skipped. `$RUNS/20261118-alfworld-context/AwcContext.lean`.

What Lean establishes:

| statement | what it means |
|---|---|
| `inspectCurrentImpliesContext` | the extension is **monotone on clause (a)**: it never withdraws a completion the old reading recognised |
| `inspectNeedsTheObjectHeld` | no discharge without the requested object held — a lamp-on alone is never a completion |
| `lampElsewhereNeverDischarges` | `same_place` as an invariant |
| `noLampObservedNoDischarge` | with no lamp-on observed, the reading is **silent**; an unknown is not turned into a completion |
| `twoContextImpliesCurrent`, `twoNeedsOneReceptacleInstance` | clause (c) is strictly stronger, and cabinet 3 ≠ cabinet 6 as arithmetic |
| `heatContextEmptyIsSilent` + 7 exhaustive cases | clause (d) is strictly stronger **over temperature-event lists of length ≤ 2**, which is what the records contain. The unrestricted statement is *not* proved, and the Lean file says so in a comment |
| `strictOnlyWithdraws` + 20 examples | the direction recorded for each measured change is the direction the definitions give — the Python and the Lean agree on the real rows |

What Lean does **not** establish: that the observation grammar extracted the right object, receptacle or
lamp — that is a trusted input, and two of this study's own findings are about that layer; that
`lamp_stays_on` is true of ALFWorld — an empirical claim about the command set, stated in the amendment;
that the revised reading matches the engine's success condition — the evidence for that is 736 saved
attempts, not a proof.

**Semantic bundle, frozen before the checking records were replayed:** `AWC_BUNDLE_v1`, digest
**`db73dae18d5acf41`** — 4 module digests, 13 semantic function digests, the assumption sets, the stage
definitions, the vocabulary (`lc.vocab.v1`, `5005c74b9dbd409b`) and the manifest digest. There is no
learned model in this study, so the bundle digests code rather than a hypothesis, and it records that
explicitly. The renderer is deliberately **not** in the bundle: no analyst report is produced.

**Frozen module snapshot.** `research/alfworld_context/frozen/` holds read-only copies of
`stage_common.py` (`570f2049205c7c99`), `a4_reading.py` (`70b707f4f10a434f`), `vocab.py`
(`e857150a39a166bd`) and `trace_only/tor_record.py` (`b40c448ebaa07f2e`), each byte-identical to the
live file in the sibling worktree when taken, with the source path and mtime recorded.
`awc_manifest.py verify` refuses to proceed if a snapshot file drifts from its source while the source
is reachable. **Nothing in the sibling worktree was written.**

**Resources.**

| resource | this study | authorized |
|---|---|---|
| actor / optimizer API calls | **0** | 0 |
| GPUs | **0** | 0 |
| new gameplay | **0** | 0 |
| CPU requested job-hours | **0.6** (3 jobs × 0:12, 2 CPUs each) | 2 |

Jobs: **66385774** tests (COMPLETED), **66385775** lean (FAILED — `heatContextEmptyIsSilent` needed
`rfl`, not `simp`, because `latest []` has to unfold; its requested time is counted anyway),
**66385916** lean (COMPLETED, 6 s). The 2 job-hours come from the learned-contract track's own recorded
remaining headroom (2.0 CPU job-hours at the A4 close), not from the OfficeQA ceiling. Everything else
ran on the workstation over saved traces, which is what "prefer lightweight local processing" asked for.

---

## 9. Deviations and limits

1. **No extension was written**, because the proposed behaviour already exists. The study is a
   measurement and a verification, and §1 asked for exactly that outcome if it were the case.
2. **The mechanical attempt rule missed the named lamp attempt**; it was added as a labelled
   supplementary record (§3).
3. **The checking half was not perfectly blind** (§3): the supplementary scans, run before the bundle
   freeze, aggregate over all attempts of each type. No checker was changed, so nothing was tuned.
4. **Two in-sample tasks dominate the count.** 12 of the 19 changes are tr:3287 (eleven runs) and
   tr:2785 (one) — the same two episodes re-rolled in different arms, because every arm rolls out the
   same task set. By distinct **task** the 19 changes are **8 tasks**: tr:0293, tr:3287, tr:2785 and
   tr:2658 (**in-sample** for the revision) and tr:3042, tr:2986, tr:2395, tr:2456 (**out-of-sample**).
   So the honest denominator for "did the rule generalise" is four tasks, not nineteen attempts.
5. **`rev=1` is not the pre-revision reading** (§2.1). Reported, not repaired.
6. **The engine's success condition is not available**, so "agrees with the score" is the only check on
   a reading's correctness, and the score is binary and end-of-episode. A reading could be wrong at step
   30 and right at step 50 and this study would not see it.
7. **Clause (d)'s Lean statement is exhaustive only over event lists of length ≤ 2.**
8. **No held-out material was opened**: no `*_evaluate_*` run, no `seen:`/`unseen:` id, and the 12
   held-out tasks of the frozen info-study selection are untouched.
9. **The lamp-state and co-location vocabulary gaps are unchanged**, and both were already pending user
   proposals before this study. So was a further INSPECT counterexample (tr:0281) with a rev-3 candidate
   that is post-hoc 28/28. **None of those is implemented here**: fitting a rule to a counterexample that
   is already known would be fitting the checker to a known failure.

---

## 10. Does the evidence justify a next step?

**For the context connection itself: no.** It is already implemented, it is sound on 736 attempts, and
its measured yield is 19 changes of which 18 only correct *when* a success is recognised and 1 corrects
a false claim on a failure. Out of sample, clause (b) yields nothing at all. Another study of the same
kind would cost two more job-hours and change no decision.

**The one thing the evidence does support, and it is cheap:** tr:2986 shows that the stage layer can
*contradict* the ordinary analysis's attribution of a failure — the analysis said the agent never found
the goal object; it had found both and misplaced one. If a contract layer is ever going to add something
an optimizer can use, that is the shape of it: not a better progress state, but a **correction of a
failure attribution**. The smallest experiment that would test it is to count, over the saved training
attempts, how often the stage reading's final state contradicts the saved `navigation_loop`-style
attribution — no gameplay, no API calls, and it reuses everything built here. **I have not run it**; it
is a proposal, and it is a different question from the one this study was asked.

**What I would not do:** implement rev 3, add a lamp-state atom or a co-location relation, re-run any
campaign, or evaluate performance. The first two are pending user decisions, and the evidence for them
is a single known counterexample each.

---

## 11. Restartable handoff

```
STUDY      rule AWC-1, plan docs/research/ALFWORLD_CONTEXT_STUDY_PLAN.md, report (this file)
BRANCH     officeqa-contracts, worktree SkillOpt-officeqa. NOT pushed; pushing is not authorized.
ARTIFACTS  local  ~/dev/lean_skillopt/runs/20261118-alfworld-context/
           cluster $PROJECT_ROOT/runs/20261118-alfworld-context/  (AWC_COMPARE, AwcContext.lean, AWC_LEAN)
BUNDLE     $PROJECT_ROOT/awc_20261118  (commit 7abbb91; the tests job ran d98fd6a)
JOBS       66385774 tests COMPLETED 2353 passed | 66385775 lean FAILED (rfl) | 66385916 lean COMPLETED rc 0
FROZEN     manifest 5517922e569456b2 (read-only) | bundle db73dae18d5acf41 | vocab 5005c74b9dbd409b
NOT MINE   the live ALFWorld implementation is UNCOMMITTED in ~/dev/lean_skillopt/SkillOpt-learned-contract
           (branch contract/learned-contract). It was READ ONLY. Its own state note,
           CONTRACT_IMPLEMENTATION_STATE.md, was NOT modified - that track is closed and owns its notes.
REPRODUCE  python3 -I research/alfworld_context/awc_manifest.py verify
           python3 -I research/alfworld_context/awc_manifest.py freeze --runs ~/dev/lean_skillopt/runs --out $O
           python3 -I research/alfworld_context/awc_replay.py --manifest $O/AWC_MANIFEST.json --role all --out $O
           python3 -I research/alfworld_context/awc_replay.py --scan-type <type> --runs ~/dev/lean_skillopt/runs --out $O
           python3 -I research/alfworld_context/awc_bundle.py freeze --manifest $O/AWC_MANIFEST.json --out $O
           python3 -I research/alfworld_context/awc_bundle.py compare --replay ... --case ... --scan ... --out $O
           OQA_BUNDLE=$D bash  # PHASE=tests|lean via sbatch, see research/cluster/awc_body.sh
LEFT       1.4 of 2 requested CPU job-hours; 0 API calls and 0 GPUs were used and none are authorized
OPEN       (a) the failure-attribution count proposed in section 10 - offline, not run;
           (b) pending USER decisions that predate this study: INSPECT rev 3, lamp-state atom,
               co-location relation. None implemented here.
```
