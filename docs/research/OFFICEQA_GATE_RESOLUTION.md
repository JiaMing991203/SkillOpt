# What a 24-question gate can and cannot decide

Written 2026-11-17, while the three-arm campaign was running, because it changes how every accept/reject in it
should be read — including one claim of mine that it corrects.

## The claim this corrects

After epoch 1 I wrote that "the optimizer's first edit is harmful in every condition". **That is not supported
by the data.** What the data support is the weaker statement that **no candidate has yet measured above S0 on
its 24-question gate**. The arithmetic below is why the difference matters.

## The resolution of the instrument

The gate scores a candidate on 24 validation questions and compares it to a baseline that is itself a single
24-question measurement. One question is **4.2 percentage points**. S0 measured 4 of 24.

```
S0 measured rate                     0.1667  (4/24)
binomial standard error at n=24      0.0761  = 1.8 questions
95% interval on S0's TRUE rate       0.018 to 0.316  =  0.4 to 7.6 questions
```

A skill whose true rate is exactly S0's, measured once on 24 questions, lands anywhere from 0 to 9 correct with
non-trivial probability:

```
 0/24  p=0.013      3/24  p=0.204      6/24  p=0.108
 1/24  p=0.060      4/24  p=0.214      7/24  p=0.056
 2/24  p=0.139      5/24  p=0.171      8/24  p=0.024
```

## Two consequences

**A strict-improvement gate rejects an equally good candidate about 63% of the time.** P(a candidate whose true
rate equals S0's measures ≤ 4/24) = 0.629. Rejection is therefore the *expected* outcome for a neutral edit, not
evidence against it.

**The four candidates measured so far are unremarkable under the null** that each is exactly as good as S0:

| candidate | measured | P(≤ this \| true rate = S0's) |
|---|---|---|
| A epoch 1 | 1/24 | 0.073 |
| C epoch 1 | 2/24 | 0.212 |
| A epoch 2 | 2/24 | 0.212 |
| B epoch 1 | 3/24 | 0.416 |

P(all four land at or below 4/24 \| all four are exactly as good as S0) = **0.157**. Nothing here separates
"the edits are harmful" from "the edits are neutral and the gate is rejecting them by noise".

## Power, which is the harder problem

How often does this gate *accept* a candidate that really is better?

| candidate's true rate | | P(gate accepts) |
|---|---|---|
| 0.1667 | exactly as good as S0 | 0.371 |
| 0.2100 | +1 question of true skill | 0.588 |
| 0.2500 | +2 questions | 0.753 |
| 0.3333 | double S0's rate | 0.941 |

So the gate is not blind — it would usually accept a genuinely much better skill. What it cannot do is
distinguish small true differences, and it accepts a neutral candidate 37% of the time, which is a false-accept
rate, not a margin.

Detecting a true doubling (0.167 → 0.333) with 80% power at a one-sided 5% level needs about **37 questions**
against a *fixed* baseline. The campaign has 24, and its baseline is a single 24-question measurement, which
widens the requirement further.

## What this does and does not change about the campaign

**It does not invalidate the comparison.** The gate is upstream SkillOpt's, preserved deliberately, and it is
identical in all three arms — so it cannot favour one arm over another. Every arm faces the same noise.

**It does change what a result can say.** Three legitimate conclusions remain available:

1. *Did any arm produce a skill the gate accepted?* A clean yes/no, whatever the noise.
2. *Do the arms' edits differ?* Already answered: epoch 1's three candidates are three different documents
   (4,174 / 4,652 / 4,420 bytes from S0's 887). The feedback channels change what the optimizer writes.
3. *The held-out comparison over distinct selected skills*, which is the pre-registered primary result.

**What a result cannot say** is that one arm's edits are better than another's on the strength of selection-set
margins of one or two questions. If the campaign ends with every candidate rejected in every arm, the honest
report is "no arm produced an accepted edit, and the gate could not have reliably detected a small improvement
if one existed" — not "contracts did not help".

## What would fix it, if the user wants it fixed

Not available inside this campaign's authorization, and listed only so the option is on the record:

* **A larger selection set.** 37+ questions for a doubling; more for smaller effects. OfficeQA's released val
  split has exactly 24, so this would mean borrowing from the test split — which would cost the held-out set.
* **Repeat the baseline.** Scoring S0 two or three times on the gate set would at least put an error bar on the
  number every candidate is compared against. At $0.39 per 60-question pass, a 24-question repeat is about
  $0.16 each.
* **Accept on a tie.** Upstream requires strict improvement; accepting ties would halve the false-reject rate
  for neutral edits. That is a change to validation semantics and would need explicit approval
  (`CLAUDE.md` §7: never weaken a gate to make a run pass — this would be weakening it, and is recorded here as
  an option, not a recommendation).

---

## CORRECTION, 2026-11-17 (added after the campaign finished, with all 13 gates in hand)

Everything above was written while the campaign was running, from **four** candidates. The campaign ran
**thirteen** gates. Three things above are now wrong or misleading, and `research/officeqa/oqa_gate_stats.py`
is the tool that computes the replacements from the artifacts so they cannot drift again.

### 1. The count of gates was wrong, and so was the expectation built on it

A later readout of mine said "in the sound window there were 9 gate evaluations ... the expected number of
acceptances is 2.99". Both numbers were wrong, and they were wrong in ways that did not even agree with each
other: 2.99 is 8 x 0.371, not 9 x 0.371, which is **3.34**. The real count is neither:

| | gates |
|---|---|
| ordinary updates that reached the gate | **8** |
| gated slow updates that reached the gate | **5** |
| **total gate evaluations** | **13** |
| stages where no candidate reached a gate (the starved epochs) | 8 |
| **acceptances** | **1** (arm B, epoch 2, ordinary) |

The per-gate expectation is not 13 x 0.371 either, because **the threshold moved**: after arm B accepted at
epoch 2 its incumbent rose to 5/24, so its two later gates needed 6/24 rather than 5/24 and faced
P(accept | null) = 0.383. Expected acceptances, each gate against its own incumbent: **4.86**. P(at most 1) =
**0.020** under an independence assumption discussed below.

### 2. "Nothing separates harmful from neutral" was true of four candidates and is NOT true of thirteen

This is the substantive correction. The acceptance count is a poor instrument — a gate discards all of its
information but one bit. The **scores** do not:

```
candidate scores, out of 24     1 1 1 1 1 1 1 2 2 2 2 3 5
mean candidate                  1.77 / 24   (0.074)
mean incumbent                  4.23 / 24   (0.176)
pooled over 13 x 24 = 312 question-level observations
    observed correct            23
    predicted by the null       55
    P(total <= 23 | every candidate as good as its incumbent)   1.3e-07
```

So the null this document set up — every candidate is exactly as good as S0 — is **strongly disfavoured**, in
the direction that **the optimizer's edits made this actor worse on the selection set**. The section above
saying "nothing here separates 'the edits are harmful' from 'the edits are neutral'" was a correct reading of
four candidates and is the wrong reading of thirteen.

Two things this does **not** say:

* It does not separate the arms. The deficit appears in all three, including Original SkillOpt. It is a
  statement about this optimizer, on this benchmark, with this actor and an 887-character starting skill — not
  about contracts.
* It does not make arm B's single acceptance noise. "Not distinguishable from chance at this resolution" is a
  statement about the instrument. The honest description of that one acceptance is: 5/24 against 4/24, a
  one-question margin, which this gate set cannot resolve.

### 3. The independence caveat, stated once and applied to both numbers

Every gate within an arm scores its candidate on the **same** 24 validation questions, and each incumbent is
itself a single 24-question measurement rather than a known rate. The real joint distribution is therefore more
concentrated than the independent model used above, so both tail probabilities overstate the evidence. For
P(at most 1 acceptance) = 0.020 that matters: it could plausibly be several times larger. For the pooled
deficit — 23 observed against 55 predicted — it does not: no within-arm correlation among 24 shared questions
can account for a shortfall of that size.

### 4. What was confirmed rather than corrected

The resolution arithmetic at the top of this document stands: one question is 4.2 percentage points, a
strict-improvement gate rejects an equally good candidate about 63% of the time, and it accepts a neutral one
37% of the time. So does the conclusion that the gate cannot adjudicate between the arms on margins of one or
two questions, and that the pre-registered primary result is the held-out comparison over distinct
validation-selected skills.
