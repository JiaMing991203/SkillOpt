# OfficeQA learned action-and-stage contract — milestone report
**Rule OQA-LN-1, amendment D-1. 2026-11-21/22. Branch `officeqa-contracts`, not pushed.**

This is one self-contained read. Abbreviations are explained where they first appear. Four kinds of
claim are kept apart throughout and never traded for one another:

* **SOFTWARE CORRECTNESS** — code does what it says.
* **EMPIRICAL LEARNING** — a hypothesis was learned from records and tested on records it had not seen.
* **DIAGNOSTIC USEFULNESS** — a report told an optimizer something it would not otherwise have had.
* **SKILL PERFORMANCE** — a skill scored better. **Nothing here establishes this, and nothing here
  was allowed to touch it.**

**The completed two-baseline comparison is untouched.** The frozen actor, document rendering, tools,
response handling, scorer, and the 12 training / 12 validation / 30 held-out split are unchanged; the
three endpoints (`9d377d1c2906` S0, `e35673d87cb7` Original SkillOpt, `03d6564f1d10` Trace2Skill) and
their evaluations are preserved; **held-out scores and trajectories remain sealed and were not opened.**
No baseline was rerun, validation was not enlarged, and no previously unobserved document structure was
added.

---

## 1. What actually ran, and what it cost

Thirteen Slurm jobs, bundles `oqa_ln_20261121` … `oqa_ln_20261121j`, output root
`runs/20261121-officeqa-learner/`.

| job | phase | state | elapsed | what it established |
|---|---|---|---|---|
| 66580310 | ledger | COMPLETED | 2:23 | ledger PASS on NFS; suite 2,529 passed / **1 failed** (a stale pin) |
| 66580311 | freeze | COMPLETED | 0:02 | first evidence freeze |
| 66580469 | ledger | COMPLETED | 5:55 | suite **2,531 passed / 0 failed**; the stage-group probe |
| 66580714 | learn | FAILED | 0:02 | **fail-closed**: ledger probe could not import; refused to load a key |
| 66580801 | learn | FAILED | 0:05 | **fail-closed**: wrong credential variable; refused to proceed |
| 66580895 | learn | FAILED | 0:10 | `retries=0` sent **nothing**; route and key both verified correct |
| 66581070 | freeze | COMPLETED | 0:09 | evidence + vocabulary + occurrence audit frozen |
| 66581071 | **learn (paid)** | COMPLETED | 0:46 | two learner repetitions, **4 paid calls** |
| 66587986 | lean | FAILED | 0:06 | **Lean refuted the Python side** — see §9 |
| 66588253 | restage | COMPLETED | 0:03 | stage reports rebuilt free; **2 false accusations withdrawn** |
| 66588304 | lean | COMPLETED | 0:03 | Lean 4.20.0, return code 0 |
| 66588323 | **rehearse (paid)** | COMPLETED | 1:55 | the paired analyst rehearsal, **2 paid calls** |
| 66588746 | ledger | COMPLETED | 3:22 | final verification of the finished code: **2,557 passed / 0 failed**, ledger PASS |

**Resources.** Sublimits for this follow-up were 8 requested CPU job-hours, 24 DeepSeek HTTP attempts
AND $3, zero actor calls, zero GPUs.

| resource | authorized for this follow-up | used | remaining |
|---|---|---|---|
| CPU, requested job-hours | 8 | **4.25** | 3.75 |
| CPU, elapsed hours | — | 0.19 | — |
| DeepSeek attempts | 24 | **6** (4 learner + 2 rehearsal) | **18** |
| DeepSeek USD | $3.00 | **$0.029476** | **$2.970524** |
| Actor calls | 0 | **0** | — |
| GPUs | 0 | **0** | — |

Three of the four failed `learn` submissions cost CPU and nothing else: each stopped before any request
left the machine, and the ledger shows it.

**Read straight off the shared ledger, which separates this follow-up from the campaign by job id:**

```
FOLLOW-UP  jobs 66581071, 66588323   optimizer attempts 6   $0.029476   refusals 0
campaign   jobs 66553248, 66553250, 66553251, 66553363   attempts 35   $0.312134   refusals 0
```

So this follow-up consumed **6 of its 24 authorized attempts and $0.029476 of its $3.00**, inside phase
ceilings against which the campaign had already drawn 206 calls and $1.9788. The lower of the two
limits governs at every point, and it was the follow-up's own sublimit throughout.

---

## 2. Accounting, reconstructed

The live spend ledger was inactive for most of the two-baseline campaign, and activating it does not
incorporate spending that already happened. The phase's consumption is therefore rebuilt from the
records that exist, with the overlaps **named rather than summed**.
`research/officeqa/oqa_phase_acct{,_run}.py` → `docs/research/OFFICEQA_PHASE_ACCOUNTING.json`.

**The sources, and what each can answer.**

| source | covers | carries |
|---|---|---|
| per-job backend **call logs** (`actor_calls.jsonl`, `optimizer_calls.jsonl`) | every stage whose body exported the path | one line per HTTP **attempt**, retries included, with the provider's own `cost_usd` |
| the shared **ledger** (`<run>/ledger/{claims,release,spend}`) | jobs from 66553248 onward **only** | one `attempt` record before each request and one `usage` record after each reply |
| a **provider statement** | nothing — not reachable offline | — |

**Overlap and disjointness.** 17 (job, role) rows: **13 call-log only, 3 ledger only, 1 both**.
Where both exist the call log is counted and the ledger is used only to check it. The single
overlapping pair, job 66553248's optimizer role, **agrees exactly** — 35 attempts and $0.312133 from
both. That is the only cross-check this reconstruction has, and it passes.

**Missing coverage: none, and the reason the held-out jobs are ledger-only is read, not inferred.**
The three held-out jobs have no actor call log because the body deployed at commit `c35df7ef6ba6`
exports `TARGET_OPENAI_COMPATIBLE_CALL_LOG` at the s0 and Trace2Skill branches only — read from the
deployed script on the cluster, not concluded from the file's absence. **0 (job, role) pairs are
uncovered by every source, and 0 spending appears that the job catalogue did not expect**, so no
conservative allowance had to be reserved for a gap.

**The reconciled figures.**

| role | attempts | retries | unresolved | charged (project price table) | provider's own figure |
|---|---|---|---|---|---|
| target (actor) | **3,462** | 0 | 0 | **$1.4378** | **$1.1151** (ratio 0.78) |
| optimizer (DeepSeek) | **206** | 0 | 0 | **$1.9788** | **unavailable** — no record carries one |

`unavailable` is reported as such and never as `$0.00`: the provider returns no cost for this role, so
there is no independent figure to check the computed charge against.

**Opening-balance adjustment** (a new document; no historical record was modified, and nothing is
counted twice):

| role | cap | consumed | remaining |
|---|---|---|---|
| actor | 20,000 attempts AND $15.00 | 3,462 / $1.4378 | **16,538 / $13.5622** |
| optimizer | 400 attempts AND $20.00 | 206 / $1.9788 | **194 / $18.0212** |

**Against the previously reported figures:** the attempt counts are **identical** (3,462 and 206),
independently rebuilt. The dollar figures were **overstated** — $1.87 → $1.4378 and $2.06 → $1.9788.

**Outstanding reservations, and one false alarm of my own.** In the live ledger, claims minus releases
equals what was spent, to the micro-dollar, for all four (role, resource) pairs; the invariant
`spent ≤ claimed ≤ cap` **holds everywhere**. My first pass reported `spent` twelve micro-dollars
**above** `claimed` — a cap violation that existed only in my arithmetic, because I summed rounded
dollar figures where the ledger charges in integer micro-dollars. The reconstruction now uses integers.

---

## 3. The intermittent cap failure: mechanism, fix, and verification

"Four processes once spent 24 against a cap of 23" had a mechanism, and later passing runs did not
resolve it.

**The mechanism.** A claim is created with `O_CREAT|O_EXCL` under a sequence number, and the old
docstring argued that this stops two processes being granted the same block. It does not, because the
file **name carries the amount**:

```
{seq:06d}-{role}-{resource}-{amount}.json
```

Two callers reaching one sequence number with **different amounts** write **different names**, so the
exclusive create never fires and both claims count. They reach different amounts because
`give = min(block, cap − granted)` and `granted` came from **two separate directory listings** —
claims, then releases — so a release landing between them lowers one caller's figure and not the
other's. `_write_release` had the identical defect, in the more dangerous direction: two releases both
subtracted lower `granted`, which is what lets the cap be exceeded.

**Reproduced deterministically**, no threads and no forks: eleven claims of 2 against a cap of 23, one
ordinary grant taking the last unit, then one caller with a stale claims view and a fresh release view:

```
grant A gave : 1 -> ['000012-target-attempts-1.json']
grant B gave : 2 -> ['000012-target-attempts-1.json', '000012-target-attempts-2.json']
cap 23, net granted 24   <- the reported failure, on demand
```

**The fix — three guards, each sufficient for a different failure shape.**
1. Granting **and** releasing run under one exclusive lock, so the read-decide-create sequence cannot
   interleave with another process's at all.
2. The sequence number is reserved by an **amount-free token** (`{seq}-{role}-{resource}.seq`) created
   with `O_CREAT|O_EXCL`, so exclusivity no longer depends on two callers choosing the same amount.
3. `granted` reads **releases first and claims second**, so a listing that is out of date can only
   overstate what has been granted, never understate it.

A listing that stays stale is treated as a ledger that cannot be trusted: the grant **fails closed**.

**Verified on the production filesystem** (`/nfs/turbo`, job 66580469), not only on a temporary ledger:

| check | result |
|---|---|
| the deterministic over-grant interleaving | **refused, fail-closed**; net 22 against a cap of 23 |
| four-process race, **12 repeats** | **12 of 12 pass**, every run totalling **exactly 23** |
| restart | a fresh process finds the authorization already spent |
| fail-closed | an unusable ledger stops the run |

Twelve runs each reaching exactly 23 is the second thing worth noting: the cap is reachable **to its
last unit** and never exceeded, so the fix did not buy safety by wasting authorization.

`tests/test_officeqa_ledger_race.py` pins the **mechanism** in nine deterministic tests, because a race
that usually passes is the worst possible evidence that it is fixed.

**And a phase-wide single payment coordinator as well.** A defect verified once is not a reason to run
four payers at a 24-attempt allowance. Both paid phases take an exclusive **payer token** — a `mkdir`
on one directory, which is atomic on NFS — holding the job id; a second paid job finds it and refuses.
Limiting each job to one request would not be enough, because several jobs could still spend
independently. The token is released on exit and broken only if the scheduler no longer knows its
holder.

**The production path, verified rather than assumed.** Every paid request this phase made went through
the active ledger (`verdict: PASS` recorded before the key was loaded), the correct provider and model
(`deepseek-flash` at `api.deepseek.com`, max_tokens 16384, recorded as the **effective** route read back
from the live backend config), with attempt and spend reservations taken before each request and
released at exit. Four separate fail-closed stops fired during this phase and **each cost nothing**:

| stop | what it caught |
|---|---|
| 66580714 | the ledger liveness probe ran under `python -I`, which ignores `PYTHONPATH`, so `import skillopt` failed. The body read that as an inactive ledger and refused to load a key — correct behaviour from a wrong premise. |
| 66580801 | the credential file defines `DEEPSEEK_API_KEY`; the body read the backend's own variable name and found it empty. |
| 66580895 | `retries=0` — the backend's loop is `for attempt in range(retries)`, so this sent **no request at all**. Confirmed spent nothing: the ledger reads 35 optimizer attempts before and after, and no call log was written. |
| 66553061 (earlier) | a phase submitted against a bundle built before that phase existed. |

---

## 4. The expected-to-change test, and a claim withdrawn

**The problem.** `test_regenerating_a_real_historical_report_reproduces_its_bytes` carried two
expected-to-change observations as **literals in its own body**. Every amendment that legitimately
moved one made a successful new stage exit 1, and because a summary line names only the test, telling a
stale pin from a real regression meant reading the traceback. I had already once read such a failure as
evidence that a repair had restored a report's bytes; the assertion that actually failed was a different
one, and three claims built on that inference were withdrawn.

**The correction — not a disabled test.** The pins moved to
`tests/data/officeqa_report_expectations.json`, where each value names the amendment that set it and
why. A mismatch still fails, and the message names the field, both values, and where to re-pin
deliberately. What stayed in the test body got **stronger**: the model digest, the trace digest, the
action forecasts, the `"unavailable"` provenance fields, and a **new** test asserting the frozen
campaign's own records still say what they said and its prediction files are still there. A pin may be
re-set deliberately; those may not be re-set at all.

**Suite: 2,531 passed, 0 failed, 9 skipped, 368 subtests** (job 66580469 — the run that first had the
suite green). Re-run against the **final** code of this phase, job 66588746: **2,557 passed, 0 failed,
9 skipped, 368 subtests**, the 26 extra being this phase's own new tests. The ledger verification
passed again in that same job: all five checks PASS, 12 of 12 races at exactly 23, the over-grant
interleaving refused fail-closed.

**A claim of mine is withdrawn.** The C-3 amendment said C-3 had returned the stage-diagnosis group
count to its historical 14 and called that "independent support for C-3 being the right repair". It
reads **17**. The sentence and the inference are withdrawn.

**And 17 is correct, measured rather than argued** (`oqa_groups_probe.py`, job 66580469): 140 ledger
entries, 17 groups — 2 VIOLATED, 3 OUTSTANDING, **9 UNKNOWN**, 3 DISCHARGED. The three largest UNKNOWN
groups are `cell_bound` n=19, `period_match` n=19 and `unit_match` n=19 — the identical triple over the
identical 19 subjects that C-2's defect-A repair emits for **one** withdrawn binding. The +3 **is**
those three groups. The historical 14 is what a silently vanishing requirement looked like; the count
rose because requirements stopped vanishing.

---

## 5. The evidence set, frozen before any learner call

`runs/20261121-officeqa-learner/v1_freeze/LN_FREEZE.json`.

**Records.** The 48 fixed-S0 trajectories of job 66552421 — four passes over the twelve frozen
OQA-SEL-4 **training** questions, actor frozen, explicit thinking disabled. **676 binary calculations,
1,352 operands**; 82 chained statements counted and declared **out of scope** rather than parsed badly.

**Roles, task-separated.** The twelve questions are ordered by `sha256("OQA-LN-1|" + uid)` and split
6/6, so no checking record is an attempt at a question the learner saw:

* **learn** — UID0028, UID0026, UID0079, UID0014, UID0034, UID0033 → 1,214 operands, **28** with a
  corroborated source
* **check** — UID0085, UID0031, UID0063, UID0065, UID0017, UID0030 → 138 operands, **26** sourced

**Exposure, recorded.** These are **REUSED DEVELOPMENT RECORDS, not fresh confirmation data.** The same
48 trajectories are Trace2Skill's entire input, so a baseline method has already consumed them, and I
have read UID0026 in all four passes and historically UID0014, UID0019, UID0028 and UID0044 in detail.
They can show that a learned rule transfers across questions; they cannot confirm anything.

**Not read:** `ground_truth`, `soft`, the provider's alternate reasoning field, and `hard` (the score).
An AST walk in the test suite enforces this structurally rather than by grep.

**Recovery controls, selected by SHAPE and not by any outcome** — two calculations with different
results, the later equal to the committed answer: **p2/UID0026** (learn side), **p4/UID0063** and
**p4/UID0085** (check side, so the recovery control is **prospective**).

**Observational assumptions, frozen with the set:** an occurrence exists only where an observation line
carries a line number and pipe-delimited fields; only `a OP b = c` is parsed; numeric equality is a
match and not an identity; features come from `items[0:k]`, the verdict from `items[0:k+1]` because the
resolver needs the writing message's own words.

**One defect of my own, found by re-freezing.** The freeze digest hashed its own `written_utc`, so the
same record set froze as `63c10e469f45930e` and then `ba3ab77882193c84` four minutes apart. A digest
that moves with the clock cannot witness "this set was fixed before any learner call", which is the
only thing it is for. It is now a content digest, and two freezes of one set verifiably agree. This was
the second instance of that defect; the first was in the prediction digest and a test caught it.

---

## 6. The occurrence-binding audit — and what it says about the 64

`oqa_ln_audit.py`, two cases of each of the four situations, chosen in attempt-id order, each quoting
the observation line as the actor received it.

**(a) An unambiguous source — and it is wrong.** p1/UID0014, statement `1937 - 1935.5 = 1.5`, operand
**1937**: one observed position, corroborated by `ROW_LABEL`, reported **SUPPORTED** — the resolver's
strongest verdict. The position is **field 2 of a header line**:

```
910: | Fiscal year or month | Receipts Carriers' Taxing Act of 1937 | Total | Expenditures ...
```

It is a year inside a column heading, not a value the actor looked up. This is exactly the failure the
instruction warned about: a unique number with a matching row token does **not** establish semantic
source identity.

**(b) Competing numeric matches.** p1/UID0014, operand **2** in `0.25 * 2 = 84.5`: **12** observed
positions. `SEVERAL_POSITIONS → UNKNOWN`. A numeric match cannot choose, and the resolver does not.

**(c) A derived value that equals an observed cell.** p4/UID0063, operand **86,000** in
`86,000 - 81,774 = 4,226`: two positions, both on line 5456 (`| Polish zlotys | 0 | 86000 | 86000 |`),
fields 3 and 4. UNKNOWN. The operand is also the result of the actor's own earlier statement, which is
why `DERIVED_FROM_EARLIER_WORK` exists as a separate class from `SOURCED`.

**(d) A replaced calculation.** p2/UID0026: committed **217** from `29,943 - 29,726 = 217`, with **16**
superseded statements still in the record, including `31,336 - 30,442 = 894` and
`30,442 - 29,726 = 716`. They are reported as replaced and they do not bear on the obligation.

**How far (a) generalises, measured instead of assumed.** Across all **54** supported bindings in the
frozen set: **49 match a bare numeral, 5 (9.3%) match a numeral inside a label**, 0 could not be
re-read. Four of the five are the operand `14` matching `Unnamed: 14_level_2`, a column-index artifact
of the rendering. So the defect is real and it is **not** a collapse.

**Nothing reads the new field.** The evidence set is frozen and the learner has run against this
resolver; changing it now would make every earlier number incomparable. The repair — require the
matched field to be a bare numeral before a position counts as a cell — belongs to its own amendment,
with this measurement as its reason.

**So: the 64 supported candidates and the 14 remaining violations are not treated as ground truth.**
They are measurements of this resolver. The audit's own output says so, and case (a) shows why.

---

## 7. The learned contract: what was learned, what refuted it, and what the learner would not do

**The vocabulary** (amendment D-1, bundle label **v10**, digest `464b1cae6c65aa36`) lives in its own
namespace, because the 2026-11-17 prototype enumerates its own predicate dict into its prompt and
extending that dict in place would have silently changed the frozen learner. `oqa_cc_ir` gained an
optional vocabulary argument and defaults to the old one, so every existing caller is unchanged — 59
connected-contract and occurrence tests confirm it.

It adds two entities (`operand`, `answer`), thirteen predicates and one stage obligation
(`source_traced`). **Exactly one predicate is learnable: `operand_sourced`.** Everything else is
labelled `TASK` (read from the question), `GIVEN` (supplied by the parse), `OBS` (read from an
observation or the actor's own message) or `MATH` (a definition), and the validator rejects a contract
that makes a definition or a supplied structure its conclusion, or that puts a consequence in its
antecedent. **The model bank began at its declared initial state: empty.** The 2026-11-17 unit
hypothesis was not carried over or renamed.

**What makes `operand_sourced` empirical rather than definitional, and the limit of that.** The
resolver decides SUPPORTED from two corroborations — a token shared between the matched line and the
actor's own message, or a grep pattern whose words appear on that line. **Both are withheld from the
learner.** It sees behavioural correlates instead: how many positions matched, which tool returned
them, how the number was written, whether the message quoted a question term. A rule over those
predicts the resolver's verdict and can be wrong. **The honest limit:** which tool returned a value is
not fully independent of the query corroboration, since only a grep carries a pattern at all. The claim
is a correlate of the verdict, not a restatement of it, and that is weaker than independence.

**Two independent repetitions, 4 paid calls, job 66581071.** Both received the identical prompt
(digest `47064dac`) and returned different text (812 and 783 characters), so these are two samples, not
one cached reply. **Both proposed the same contract:**

```
operand.sourced@v1 [active]
    operand_sourced(operand)  [LEARNED]
    IF  operand_positions(operand, 1)  [OBS]
      AND operand_source_tool(operand, grep)  [OBS]
    WITNESSES stage obligation source_traced
```

> *Repetition 1, verbatim:* "The table shows operands with exactly one position in the prefix and
> source tool grep are almost always corroborated as sourced (13/13, 10/10, and 4/5), while operands
> with no positions or non-grep tools are never sourced."

Model digests `1231f2817c476aae` (r1) and `d81e2383f0a502f2` (r2).

**Measured, with the predictions committed before any verdict was read** (`r*_predictions_check.json`
is written and digested first; `r*_verdicts_check.json` records the prediction digest it was built
against, and the digests are over content with the clock excluded):

| | operands | the contract applies | confirmations | counterexamples | silent | precision where it applies |
|---|---|---|---|---|---|---|
| **learning records** (6 questions) | 1,214 | 28 | **27** | **1** | 1,186 | 0.964 |
| **checking questions** (6 unseen) | 138 | 26 | **26** | **0** | 112 | **1.000** |

**0 prospective refutations.** On questions the learner had never seen, every operand whose conditions
held was confirmed by the resolver. The rule transferred.

**The one counterexample is natural and in-sample.** In `14 * 1935.5 = 27,097` (p1/UID0014) the operand
`14` has a unique observed position and **no** corroboration, so the resolver calls it unsourced while
the contract predicted sourced. It is labelled `in_sample_on_the_learning_records` everywhere it
appears, because repairing from it is fitting the same batch rather than responding to new evidence.
*(The repair trigger originally fired only on prospective counterexamples and would have reported "no
contradiction" while this one sat in the learning records; the dry run caught that.)*

**Both repetitions were asked to repair it, and both declined.**

> *Repetition 1, verbatim:* `{"extra_condition": null, "separable": false, "reason": "No available
> predicate can exclude the counterexample without also excluding a correct-prediction group: the
> counterexample has the exact same feature vector as 4 operands where the contract predicted sourced
> and was right."}`

**The decline is correct, and the test suite checks it rather than taking the learner's word.** The
counterexample's feature vector is **identical** to that of operands the resolver did call sourced, so
a test enumerates every single-condition narrowing over every available feature and asserts each one
treats the two the same. The hypothesis class is **expressively insufficient** for this contradiction —
by construction, because the resolver's own corroborations are deliberately withheld. **No condition
was invented to cover it.**

**And the learner predicted its own refutation before it was shown one.**

> *Repetition 1's `expected_failures`, written with the proposal:* "… will wrongly source the one
> unsourced operand in the n_positions=1, grep, non-round, no-separator group, **likely because an
> independent corroboration feature not listed is absent**."

That is the right diagnosis, stated prospectively.

**So no empirical learner repair is claimed.** The repair machinery is exercised by two **CONSTRUCTED**
fixtures, under a block comment saying in as many words that they establish software capability and
nothing empirical: a separable counterexample produces a version 2 with the extra condition, version 1
is kept marked superseded with its reason and parent link, and a narrowing that removes a
counterexample while losing two confirmations is **reported as a regression**, not as an improvement.

---

## 8. The stage connection, and the recovery control

The chain, with each link's provenance kept separate:

```
TASK requirement (read from the question sentence alone)
  -> LOOKUP   a line-numbered observation exists                         [OBS]
  -> SOURCE   every operand of the supporting calculation is sourced,
              derived from earlier written work, a conversion factor,
              or taken from the question                        [LEARNED + OBS]
  -> CALC     the supporting calculation's arithmetic holds              [MATH]
  -> ANSWER   the committed answer is the result of written work         [OBS]
```

Across the 25 checked attempts (24 checking-side plus the learning-side recovery control):

| obligation | DISCHARGED | OUTSTANDING | VIOLATED | UNKNOWN |
|---|---|---|---|---|
| lookup_observed | 16 | — | — | 9 |
| answer_traced | 9 | — | 7 | 9 |
| arith_recorded | 9 | — | — | 16 |
| source_traced | 3 | 6 | — | 16 |

**p4/UID0063 — the prospective recovery control, all four links discharged.** The committed answer
4,226 comes from `86,000 - 81,774 = 4,226`. One operand is **SOURCED** (81,774, at a corroborated
position) and the other is **DERIVED_FROM_EARLIER_WORK** (86,000, the result of the actor's own earlier
statement) — a **legitimate multihop**: an intermediate quantity need not be the quantity the question
asks for. Two superseded statements (`86,000 - 0 = 86,000` and `5,541,720 - 5,459,946 = 81,774`) stay
in the record, each marked `supports_the_answer: false`. **0 execution diagnoses, 0 model refutations.**

**p4/UID0085 — missing knowledge stays unknown.** No line-numbered observation exists in the attempt,
so `lookup_observed` is **UNKNOWN**, not discharged and not violated: an absent observation does not
mean nothing changed. `answer_traced` and `arith_recorded` are discharged from written work, and
`source_traced` is discharged through derivation.

**The six properties the instruction asks for:**

| property | where it is shown |
|---|---|
| historical events remain recorded | 16 superseded statements kept in p2/UID0026; 2 in each check-side control |
| superseded calculations stop supporting the active answer | every one carries `supports_the_answer: false`, and the obligation reads only the supporting calculation's operands |
| corrected work can discharge the obligation | p4/UID0063, all four links discharged after two replacements |
| a model refutation is not an actor mistake | a refutation sets `source_traced` to **UNKNOWN**; a structural test asserts the branch contains no VIOLATED and no OUTSTANDING |
| missing knowledge remains unknown | p4/UID0085's `lookup_observed`; **16 of 25** `source_traced` states are UNKNOWN, and 9 of 25 lookups |
| a missing action effect does not imply nothing changed | `resolveEffect none = unknown`, proved in Lean over `Option Bool`, so the absent case is a real case |

**Prospective versus retrospective, stated per claim.** The 26 checking-side confirmations are
**prospective** — committed before the verdict was read, on questions the learner never saw. The single
counterexample and the whole stage picture are **retrospective**: a model fitted on the learning
records, applied to completed messages.

**A limitation I am not glossing.** `DERIVED_FROM_EARLIER_WORK` identifies an operand's **origin**, not
its soundness: only the *supporting* calculation's arithmetic is checked, so an operand inherited from
an earlier statement carries no arithmetic guarantee. In p4/UID0085 one such earlier statement
(`3564583961 * 100 = 13.24213093`) does not hold as written. Checking the deriving calculation
recursively is a bounded next step, not something this chain does.

---

## 9. Lean: what was compiled, and the error that mattered

`research/officeqa/oqa_ln_lean.py` → `v3_lean/OqaConnected.lean`.

| | |
|---|---|
| compiler | **Lean (version 4.20.0, x86_64-unknown-linux-gnu, commit 77cfc4d1a4f6, Release)** |
| source digest | **`a1cc5340807f652b`**, 17,777 bytes |
| return code | **0**, 0 errors, 0 warnings, all statements accepted |
| exported examples | **86** = 36 operand classifications + 50 stage obligations |
| job | 66588304 |

**What is checked.** General theorems over the finite domain the resolution factors through:
a refutation of the learned rule resolves to UNKNOWN and never to an accusation; an unavailable
observation channel likewise; nothing-to-trace is UNKNOWN; a discharge implies a supporting
calculation, no refutation and no unresolved operand; an unknown action effect is not "nothing
changed"; replaced work can still discharge; and an uncorroborated or multi-position match can never
be `sourced`. Then one `by decide` example per exported operand and per exported obligation, asserting
that the state the Python side recorded is what the Lean definitions produce from the same inputs.

**What it does not establish:** that the extraction found the right cell, line or operand — that is a
trusted input; that the learned rule is true of the corpus — that is empirical and measured on 48 saved
attempts; or that any reported state caused a task outcome.

### The first compile FAILED, and that is the most useful thing in this section

Job 66587986 returned rc 1 with about twenty copies of one error:

```
error: tactic 'decide' proved that the proposition
  sourceTraced false false 0 = ObState.unknown
is false
```

Lean computed **DISCHARGED** where the Python side had recorded **UNKNOWN**. The Lean definition took
an attempt with no supporting calculation — no operands, so zero unresolved — to a discharge. *Nothing
to trace* is not *nothing left to trace*, and the model had no way to say so. It now takes
`hasSupporting`, with a theorem `nothingToTraceIsUnknown` stating exactly the failing case.

**And following that disagreement exposed a false accusation in the Python side.**
`recovery_shape` returned early when an attempt had fewer than two calculations, so an attempt whose
**single** written calculation yields the committed answer reported no supporting calculation at all —
and the stage layer reads that as "the committed answer is the result of no written calculation" and
marks `answer_traced` **VIOLATED**. Two checking-side attempts were accused that way for doing exactly
the right thing: **p2/UID0063** and **p3/UID0063** each wrote `86,000 - 81,774 = 4,226` once and
committed 4,226.

Rebuilt free from the already-frozen models (phase `restage`, job 66588253, **0 paid calls**), per
repetition: **6 obligations move, 2 accusations withdrawn** (`answer_traced` VIOLATED → DISCHARGED on
both), `arith_recorded` UNKNOWN → DISCHARGED on both because the calculation can now be checked at all,
and `source_traced` UNKNOWN → OUTSTANDING on both because once a supporting calculation is identified
its operands can be judged. The last pair reads as an *added* accusation and is one — the checker became
able to see something it had been silent about. **The corrected reports were written to a new
directory; the superseded ones are preserved unmodified**, because a correction that overwrites what it
corrects leaves nothing to check.

*(A smaller fix in the same pass: `supersededIsIrrelevant` was stated as `X = X`, which is vacuous —
Lean's linter flagged the unused variable and was right. It is now `replacedWorkCanStillDischarge`,
which says something. And the receipt required stdout to be empty for `all_statements_accepted`, so one
linter warning produced `returncode: 0` beside `all_statements_accepted: false` — two fields of one
record disagreeing about one compile.)*

---

## 10. The paired rehearsal through the real analyst

Job 66588323, **2 paid calls**, through `skillopt.gradient.reflect.run_error_analyst_minibatch` — the
entry point the campaign itself uses.

| held identical | value |
|---|---|
| skill | S0, `9d377d1c2906981b` |
| item | p1/UID0063, trace digest recorded |
| edit budget | L = 4 |
| model, sampling, call count | DeepSeek `deepseek-flash`, one call each, upstream's own retry setting |
| trajectory text | **16,179 chars ordinary vs 19,338 assisted — a difference of 3,159, which is the 3,129-byte report plus its 30-character heading, exactly** |

The report was delivered **through the production hook**: written to the cache path that
`reflect._officeqa_report_cache_key` computes and injected by `fmt_minibatch_trajectories`, not pasted
into a prompt by the rehearsal script. **Linked by digest:** report `afd740186c0395e0` ← model
`d81e2383f0a502f2` ← Lean source `a1cc5340807f652b` (rc 0), all three recorded in
`v4_rehearse/LN_REHEARSAL.json` beside the outbound request.

**One deviation from the production path, recorded rather than hidden:** the production analyst
receives `fail_reason`, which states that the scorer marked the attempt incorrect. It is omitted from
**both** conditions so that no score information reaches either analyst. Identical in both, so the
comparison is unaffected.

**The report that was delivered** (3,129 bytes, generated from the checked structure, qualifications
intact) said, among other things:

```
  [source_traced] OUTSTANDING  (OBS)
      1 operand(s) of the supporting calculation have no identified origin (86,000): not a
      corroborated observed cell, not the result of earlier written work, not a conversion
      factor and not in the question

THE OPERANDS OF THE CALCULATION THAT SUPPORTS THE COMMITTED ANSWER
          86,000  UNRESOLVED
          81,774  SOURCED  at treasury_bulletin_1990_06.txt line 5450 field 3
                  corroborated by ROW_LABEL, QUERY, SOLE_IN_ROW
```

**Both conditions proposed 2 edits.** Verbatim reasoning:

> **Ordinary:** "The trajectory eventually reached a self-consistent value, but the core risk in this
> class of question is conflating an 'as-of' snapshot column with the intervening period-change/flow
> column … A second, targeted retrieval rule addresses the wasted effort when the first opened page is
> off-topic."
>
> **Contract-assisted:** "The single trajectory reached the correct numeric answer (4,226) but was
> flagged because **one operand (86,000) was not uniquely traceable**: the value sits in more than one
> column of its row and was never anchored to a specific line/field, so its origin could not be
> corroborated."

**The judgment, against the five questions asked — with the evidence, so you and Codex can disagree:**

| question | verdict | basis |
|---|---|---|
| **adds a supported diagnosis?** | **Yes** | the assisted analyst names the specific operand (86,000) and the specific defect (no single checkable origin). The ordinary analyst identifies no operand at all. The localisation came from the report, and it is correct — the audit independently shows 86,000 at line 5456 fields 3 **and** 4 of `\| Polish zlotys \| 0 \| 86000 \| 86000 \|`. |
| **makes an existing diagnosis more precise?** | **Partly** | both arms say "resolve the column by its header". The assisted arm adds the rule for the case actually observed: *when a row shows the same number in more than one column, state which column and field you used*. |
| **prevents a false accusation?** | **Not demonstrated** | the report's `answer_traced HOLDS` kept the assisted analyst from blaming the answer — but the ordinary analyst did not blame it either ("eventually reached a self-consistent value"). There is no false accusation here for the report to have prevented. |
| **produces only redundant advice?** | **No** | the operand-citation rule and the final-check restatement appear only in the assisted arm. |
| **causes an unsupported edit?** | **One unsupported inference, in the reasoning rather than an edit** | the assisted analyst wrote "reached the **correct** numeric answer". The report never says correct — it says *traced*, and its own header says it reads no score. The ordinary arm was more careful ("self-consistent"). **This is a risk in the report's wording**: `answer_traced HOLDS` can be read as a correctness claim. It did not reach either edit, and both edits are generalisable rather than task-specific. |

**What this is not.** Two edits against two edits, on **one** trajectory, is not evidence about skill
performance and was never going to be. **Neither patch was applied to any skill, no validation ran, and
no held-out record was touched.** Different edits alone establish nothing; the edits and both requests
are saved so the judgment can be made on them rather than on a count.

---

## 11. Readiness: what passed, what failed, what is untested

| # | requirement | verdict |
|---|---|---|
| 1 | reconstruct consumption from call logs, ledger, provider accounting; name overlap and disjointness | **PASSED** — 17 rows, 13/3/1, one cross-check and it agrees exactly |
| 2 | calls, retries, spending, outstanding reservations | **PASSED** — 3,462 and 206 attempts, 0 retries, 0 unresolved, reservations settle to the micro-dollar |
| 3 | missing coverage and its conservative upper bound | **PASSED, and none was needed** — 0 uncovered pairs; the held-out gap was read from the deployed script |
| 4 | auditable opening-balance adjustment, no history modified, nothing double-counted | **PASSED** |
| 5 | fix the cap failure and verify the mechanism | **PASSED** — reproduced deterministically, three guards, 12/12 on NFS at exactly 23 |
| 6 | a proven phase-wide single payment coordinator | **PASSED** — exclusive payer token, used by both paid phases |
| 7 | every paid request on the active ledger, correct provider/model, reservations, retries, release | **PASSED** — effective route read back from the live backend; four fail-closed stops, each costing nothing |
| 8 | correct the expected-to-change test without disabling tests | **PASSED** — 2,557 passed / 0 failed on the final code; pins are data, immutability assertions are stronger |
| 9 | freeze records, roles, ids, chronology, exposure, vocabulary, extraction, stages, assumptions | **PASSED** — and the freeze digest defect was found and fixed |
| 10 | task-separated checking where the evidence permits | **PASSED** — 6/6 by digest, disjoint questions |
| 11 | audit a small set of occurrence bindings against the observations | **PASSED — and it refuted the resolver's strongest verdict** |
| 12 | real API learner, separate model bank from a declared initial state | **PASSED** — DeepSeek, bank declared empty, nothing renamed |
| 13 | two independent learner repetitions | **PASSED** — 4 paid calls, convergent proposals, different replies |
| 14 | learn → freeze → prefix-only check → predictions before reveal → classify | **PASSED** — content digests, two files in order |
| 15 | repair from a genuine contradiction without supplying the target | **ATTEMPTED; THE LEARNER DECLINED, CORRECTLY** — no empirical repair is claimed |
| 16 | recheck earlier evidence and affected stage connections after a repair | **UNTESTED on natural data** — there was no accepted repair to recheck; the regression check is exercised by a constructed fixture only |
| 17 | distinguish task-defined, supplied, learned, assumptions | **PASSED** — provenance labels, and a validator that refuses a definition as a conclusion |
| 18 | connect the learned rule to a task-progress obligation, multihop allowed | **PASSED** — p4/UID0063 |
| 19 | the successful-recovery control | **PASSED** — two prospective ones, selected by trajectory shape |
| 20 | compile the model applications, stage connections and uncertainty/recovery checks | **PASSED** — rc 0, 86 examples; **and the first compile found a real disagreement** |
| 21 | report generated from the checked structure, linked by digest to model and outbound request | **PASSED** |
| 22 | paired rehearsal through the real analyst entry point, matched | **PASSED** — inputs identical to the byte except the report |
| 23 | show what the report adds | **PASSED, with a mixed verdict** — §10 |
| 24 | skill-performance evidence | **NOT ATTEMPTED, BY INSTRUCTION** — no edit applied, no validation, held-out sealed |

**Failed outright: none. The two that are not green are 15 and 16**, and both are the same fact: the
natural contradiction available in this evidence is not separable by the features the learner is
allowed to use, so no learner repair happened and nothing was rechecked after one.

---

## 12. Limitations

1. **The resolver is not ground truth.** 9.3% of its supported bindings match a numeral inside a label
   rather than a cell, and its single strongest case in the audit is a year in a column heading.
2. **`operand_source_tool` is not independent of the query corroboration** — only a grep carries a
   pattern. The learned rule is a correlate of the resolver's verdict, not a restatement, but it is not
   cleanly independent either.
3. **One feature carries no information:** `message_quotes_question_term` is true for every operand in
   the frozen set.
4. **The records are reused development data**, already consumed by Trace2Skill and already read by me.
   They can show transfer across questions; they cannot confirm.
5. **676 calculations is what a binary-statement parser sees**; 82 chained statements are out of scope
   and counted as such.
6. **`DERIVED_FROM_EARLIER_WORK` establishes origin, not soundness** (§8).
7. **One trajectory, two edits against two edits.** Nothing here speaks to skill performance.
8. **The report's `answer_traced HOLDS` can be misread as a correctness claim**, and in the rehearsal it
   was (§10).

---

## 13. A bounded next step

**Recommended, and nothing else:** one free job (~0.3 requested CPU job-hours, $0.00) that requires the
matched field to be a **bare numeral** before a position counts as a cell — amendment D-2, with §6's
measurement as its reason — then replays the occurrence binding, the learner's scores and the stage
reports over the **same frozen record set**, writing to a new versioned directory. The prediction to
record before it runs: the supported-binding count drops from 54 by about 5, the rule's learning-record
precision rises above 0.964, and the single counterexample `14` may disappear, which would make the
hypothesis class separable where it currently is not.

**Not recommended now:** a broader parser change, an actor-interface change, a third learner repetition,
or any contract performance arm. **No held-out unsealing, no baseline rerun and no interface overhaul
follows from this work.**
