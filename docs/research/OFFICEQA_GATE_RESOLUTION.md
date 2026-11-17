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
