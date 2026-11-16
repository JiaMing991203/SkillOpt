# Does the corrected stage + action report surface diagnoses a trace-only review would miss?

**Answer: no. 0 of 8 cases gained a valid, actionable diagnosis attributable to the report, and 1 of 8 gained a
misleading one that came from the report and was propagated into the review's evidence.**

Offline, zero GPU jobs, zero paid API calls, 2026-11-16. Reviewers are a proxy for the optimizer, not the
optimizer. Held-out scores were not consulted and remain sealed; job 66234927 stands only as the shared
starting-skill baseline.

## 1. Design

The eight saved arm-B epoch-2 failure traces (UID0014, 0017, 0026, 0028, 0030, 0031, 0034, 0079), each reviewed
twice in **separate contexts**, with identical instructions and output limits:

- **Trace only** — the skill document, the task, the system and user prompts the actor received (including the
  supplied parsed pages) and the full step-by-step trace. Assembled with SkillOpt's own
  `fmt_minibatch_trajectories`, hook off, so it is what the ordinary analyst receives.
- **Trace + report** — byte-identical input plus that attempt's **corrected** stage-and-action report, injected by
  the same opt-in hook, keyed to the epoch-2 trace.

Each reviewer named the main failure, cited trace evidence, and proposed at most one reusable skill instruction.
Then one **blind adjudicator per case** received the trace record and the two reviews anonymised as "Review 1" and
"Review 2" in randomised order (seed 20261116), and checked every quoted piece of evidence against the record.
No answer key appeared in any view: verified per case with the per-item leak check.

Report payload added per case: 1,230–3,351 characters.

## 2. All eight cases

TO = trace-only review, TR = trace+report review. "More precise" and "better instruction" are the blind
adjudicator's verdicts.

| Case | Same primary failure? | More precise | Better instruction | Misleading claims TO / TR | Report's effect |
|---|---|---|---|---|---|
| UID0014 | yes | TR | TO | 2 / 3 | **MISLEADING** — TR quoted a false report finding as evidence |
| UID0017 | yes | TR | TR | 1 / 1 | repeated TO's diagnosis; extra precision not in the report |
| UID0026 | yes | TR | TO | 0 / 0 | repeated; the named baseline figure is not in the report |
| UID0028 | yes | TR | TO | 2 / 0 | repeated; the year-panel mechanism is not in the report |
| UID0030 | yes | TR | TR | 1 / 0 | repeated; the glob/step counts are not in the report |
| UID0031 | yes | TR | TR | 2 / 2 | repeated; re-read counts are not in the report |
| UID0034 | yes | TO | TR | 0 / 3 | repeated, with three TR miscounts of its own |
| UID0079 | **no** | TR | TR | 0 / 3 | new primary diagnosis, but **not** in the report |

Totals: judged more precise — **TR 7, TO 1**. Better instruction — **TR 5, TO 3**. Cases carrying at least one
misleading claim — **TO 5, TR 5** (equal).

## 3. The attribution test, which is the decisive result

The adjudicators credited the report-guided reviews with 12 distinct issues the trace-only reviews missed. For
each, the corrected report was searched for the issue's content words. **Not one is present in the report.**

| Report-only issue the adjudicator credited | In the report? |
|---|---|
| UID0026: the unused `30,442` prior-December baseline | **no** (0 occurrences of "30,442") |
| UID0028: parsing dropped the table's year headers, so no literal `196x` label existed | **no** (1/9 words) |
| UID0079: the FY2011 < FY2010 anomaly came from column mis-mapping | **no** (0/6 words) |
| UID0079: a series-breaking value should trigger re-checking column alignment | **no** (1/8) |
| UID0017: the loop is specifically about output units and format | **no** (0/11) |
| UID0017: retrieval and arithmetic explicitly ruled out | **no** (0/6) |
| UID0030: four fruitless glob patterns; exact step localisation | **no** (2/10, 1/5) |
| UID0031: re-read the same contents region three times; 24 tool calls | **no** (1/6, 2/7) |
| UID0034: no-match results already settled absence; turn ended with neither call nor answer | **no** / partly (generic) |
| UID0014: missing scale conversion across ~29 values | **partly — and false (see §4)** |

For comparison, the three corrected reports behind the most-praised cases say only:

- UID0026: one warning, `ungrounded_answer_value` — *"the answer value 1951 appears in none of the 763 numeric
  mentions…"*
- UID0028: two warnings, both `grep_cap_reached` at steps 38 and 48.
- UID0079: one warning, `asked_period_absent` — the question asks about 2010, which no source covers.

None mentions column groups, year headers, General funds, `155,304`, `30,442`, globs or formatting.

So the report-guided reviews were judged more precise in 7 of 8 cases **while containing no report-sourced
content that explains the gain**. With one review per cell, three explanations cannot be separated: the report may
prompt closer reading of the trace; it may be reviewer variance; or the adjudicator may prefer whichever review
cites more specifics. This probe cannot tell them apart, and the measured gain must not be credited to the report.

## 4. The strongest case against the report (there is no strongest case for it)

**UID0014 — the report asserts something the trace contradicts, and the reviewer repeated it.**

The corrected report states:

> `[ADVICE] (C, first at step 9) scale_conversion_missing: step 9: the value 1004461.0 sits under a heading that
> states 'millions' (contract table.scale.1, assumption table_scale_applies_to_rows), and the question asks for
> 'billions'. No conversion step appears: divide by 1,000 before answering.`

The trace shows the conversion happening, twice:

> `1,004,461 (in millions) = $1,004.461 billion`
> `1,004,461 million = $1,004.461 billion.`

The report-guided reviewer cited that finding as its third piece of evidence. The blind adjudicator — which never
saw the report and judged only against the trace — caught it: *"That quoted finding says no conversion step
appears for 1004461.0, but the record shows the agent converting it."*

Scope: the finding fires 29 times for this item. The trace shows a conversion for **1** of those values; for the
other 28 no conversion context was found, so the finding is mostly right and demonstrably wrong at least once —
and the reviewer happened to quote the wrong instance. Signal source: a **combination** of a stage requirement
(`extract_operands` must bind a value to its table's scale heading) and an action check (the operand ledger built
from the trace). The combination is what failed: the ledger saw the raw figure at step 9 and the stage rule fired
before the later conversion line was reached.

An honest note on my own method: I first "checked" this claim by searching the trace for the report's literal
`1004461.0` and concluded there was no conversion. That was wrong — the trace writes `1,004,461`. The adjudicator
was right and I was not; the number above is from the corrected, normalised check.

## 5. What the trace-only review caught that the report-guided review missed

In 5 of 8 cases the adjudicator credited the trace-only review with issues the report-guided one missed:

- **UID0028** — no stopping rule, and never falling back to the oracle evidence already in the prompt; the yield
  table re-read three times. The adjudicator preferred the trace-only instruction, noting that the report-guided
  one *"addresses only table layout"*.
- **UID0030** — the agent wrongly concluded page 5 continued Exhibit 3, contradicting the supplied page.
- **UID0014** — two reads returning only the table of contents; the budget consumed by in-context arithmetic;
  repeated re-litigation of column semantics.
- **UID0034** — duplicated reads of the same span (start=790, start=780).
- **UID0017** — the self-second-guessing spans the whole deliberation, not only the output format.

Notably the **one** case where the report's own finding was most specific and correct — UID0079's
`asked_period_absent`, that no supplied source covers 2010 — produced **no** instruction about period coverage
from either reviewer. The report-guided reviewer diagnosed column mis-mapping instead.

## 6. What the reviewers found vs. what a real SkillOpt update would still have to show

**Found here (reviewer proxy, n=8, one draw per cell):** the two views agree on the primary failure in 7 of 8
cases; the report adds no diagnosis traceable to its own content; it introduced one false claim that a reviewer
repeated as evidence; and both views produce misleading claims at the same rate.

**Not established, and only a real update could show it:** whether DeepSeek, reading all eight trajectories in one
minibatch under the real analyst prompt, would propose different *edits*; whether any such edit would survive the
merge and the edit budget; and whether the resulting candidate would pass the native gate at 6/12. Reviewers here
answered a different question (diagnose one attempt) with a different output (one instruction, not a ranked patch),
and a single reviewer draw per cell cannot separate a real effect from variance. The companion audit
(`OFFICEQA_EPOCH2_REPORT_AUDIT.md` §6) already scoped that replay at 1 A40, 4 optimizer calls and ~25 minutes.

## 7. Gaps

- **The adjudicators were given the trace-only record**, so they could not verify any report-sourced quote. One
  flagged UID0014's report quotation as "absent from the record" partly for that reason. This was a deliberate
  blinding trade-off — showing the report would have revealed which review came from which view — and it means
  report-sourced claims were checkable only against the trace, which is exactly how the one false claim surfaced.
- One review and one adjudication per case; no repeats, so variance is unmeasured.
- The attribution test is lexical. A report could in principle convey an idea whose words it does not share; the
  three worked examples in §3 were read in full to guard against that, the other nine were not.
- "28 of 29 unconverted" for UID0014 is an upper bound on that finding's correctness: my conversion-context
  pattern may miss conversions phrased differently.
- Reviewers are not the optimizer: different model, different prompt, different output format, one trajectory each
  rather than a minibatch of eight.
