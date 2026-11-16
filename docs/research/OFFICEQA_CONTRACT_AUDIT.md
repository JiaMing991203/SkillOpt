# Audit of the contracts and checks behind the OfficeQA stage + action report

**Verdict.** The rule set is **fixed and hand-written**: no learner ran, nothing was learned from traces, and
**no Lean was involved anywhere** on the report path. Only **6 of 14 rules fired** across the 11 attempts audited.
The two rules that supply the report's only distinctive content — `scale_conversion_missing` and
`operand_provenance_missing` — are also its least reliable: **5 of a 10-case sample of the former are demonstrable
false positives**, and the latter fires mostly on the actor's own arithmetic and on numbers copied out of the task
prompt. Both failures share one structural cause: **the rules inspect a value's evidence provenance and never the
actor's own computed values, so an obligation the actor later discharged is reported as outstanding.** On 5 of the
8 failures everything actionable came from the **P layer**, which a plain progress report already supplies.

Offline: zero GPU jobs, zero paid API calls. Original artifacts preserved; no implementation changed.
Answer keys and held-out data were not consulted; job 66234927 stays sealed.

---

## 1. What was actually learned: nothing

| Question | Finding |
|---|---|
| Did a learner run? | **No.** The report path is `oqa_reports` → `oqa_check` → `oqa_obligations` + `oqa_trace`. No learner module is imported, and no rule/contract artifact exists in either arm's run directory. The only "learn" in `oqa_check.py` is a docstring noting that it re-implements the *design* of the earlier learned-contract study. |
| Were rules proposed/retained/revised across epochs? | **No.** The rule set is identical in epoch 1 and epoch 2; it is Python source, not run state. |
| What did Lean check? | **Nothing.** No `.lean` file, no `lake`/`olean` invocation, no Lean import on the report path. The `lean` matches in the job script are the project directory name `lean_skillopt` and `lean_snapshot`. |
| What did the generator determine in code? | Everything: the evidence ledger (`build_ledger`), the stage bindings (`stage_state`), every finding, the P/S/C layering, the containment check (`structural_check`) and the rendering. |

### Rule and provenance table

Evidence column: **Q** = the question text only; **T** = the recorded trace (messages, tool calls, observations);
**E** = the evidence ledger built from supplied pages + tool observations. Firings are over the 8 epoch-2 failures
plus 3 successes.

| Rule (category) | Layer | Kind | What it requires | Evidence | Provenance of the rule | Fired |
|---|---|---|---|---|---|---:|
| `grep.effect.1` → `grep_effect_violated` | C | warning | every line of a grep observation contains the pattern | T | **documented tool behaviour**, verified in `tool_runtime.run_tool` | 0 |
| `grep.suffices.1` → `grep_cap_reached` | P | warning | an absence conclusion needs < 20 returned matches | T | **documented tool behaviour** (`_MAX_GREP_MATCHES = 20`) | 8 |
| `read.effect.1` | C | — | a read observation is the window `[start, start+limit)`, 4000-char truncated | T | **documented tool behaviour** (`_MAX_READ_CHARS = 4000`) | 0 |
| `table.scale.1` → `scale_conversion_missing` | C | advice | a scale in a table heading applies to rows below it, so a value must be converted to the asked scale | Q+E+T | **manual empirical hypothesis**, self-labelled "not verified against the corpus" | 29 |
| `operand_provenance_missing` | S | warning | an operand written in reasoning must match an available numeric mention | T+E | **manually written** stage rule (`locate_span` must produce operands) | 18 |
| `ungrounded_answer_value` | P | warning | the answer value must appear in the returned evidence | T+E | **manually written** | 1 |
| `asked_period_absent` | P | warning | the asked period must appear in returned evidence | Q+E | **manually written**, question-derived obligation | 1 |
| `answer_not_derivable` | P | warning | the answer must be derivable from the evidence | T+E | **manually written** | 0 |
| `operand_period_mismatch` | S | advice | an operand's period must match the asked period | Q+T+E | **manually written**, question-derived obligation | 0 |
| `unsupported_operands` | S | advice | a requested operation needs the right number of grounded operands | Q+T | **manually written** | 0 |
| `stage_skipped` | S | advice | the ordered stages must each be discharged | T | **manually written** stage procedure | 0 |
| `path_not_allowed` | P | advice | a tool path must be in the candidate-file list | T | **documented tool behaviour** (`allowed_files`) | 0 |
| `no_matches` | P | event | records a `[no matches]` observation | T | **manually written**, never scored as advice | many |
| `select_source_prefilled` | S | note | records that the harness supplied the pages | T | **manually written**, never scored as advice | 11 |
| `answer_not_numeric` | P | note | records a non-numeric answer | T | **manually written** | 0 |

Six rules produced actionable output; eight never fired. The question-derived obligations come from
`oqa_obligations.derive`, which reads **explicit cues only** and returns UNKNOWN otherwise — this matters in §3.

### Success selection (rule fixed before any success report was generated)

> Population: the arm-B epoch-2 rollout. Eligible: `hard == 1`. Order: ascending numeric uid. Take the first three.

Eligible: UID0033, UID0063, UID0065, UID0085 (4). Selected: **UID0033, UID0063, UID0065**. No shortfall.

Firing on those successes: UID0033 none, UID0065 none, **UID0063 one `grep_cap_reached`**. That one is not false —
the cap genuinely was reached — but it is non-actionable, since the attempt scored correct anyway. The checker is
therefore quiet on correct work, which is the one reliability property it clearly has.

---

## 2a. Worked case — unit conversion (UID0014)

The question asks for an OLS projection over fiscal 1929–1942 receipts **"in billions"**. The supplied table is
headed `(In millions of dollars…)`.

Following one number end to end:

| Stage | What happened | Where |
|---|---|---|
| conversion becomes necessary | the question states `in billions`; the heading states `millions` | obligation `scale=billions [STATED: 'in billions']` |
| the actor performs it | **step 1**: `For 2000: Net income taxes = 1,004,461 (in millions) = $1,004.461 billion` and again `1,004,461 million = $1,004.461 billion.` | trace step 1 |
| the system records it | `1,004.461` lands in `state["computed"]` with rule `equals`, at steps 1, 9, 17 and 19 | `stage_state` |
| the system records the raw value | `1004461.0` is recorded as an **operand** at steps **9 and 19** — later re-quotes while the actor re-derived | `stage_state` |
| the report warns anyway | `[ADVICE] scale_conversion_missing: step 9: the value 1004461.0 sits under a heading that states 'millions' … **No conversion step appears**: divide by 1,000 before answering.` | corrected report |

**Why it still warned.** The check tests
`any(abs(c["value"] * factor - o["value"]) <= … for c in o["provenance"])` — that is, *is this operand itself the
converted form of one of its own evidence matches?* `o["provenance"]` comes from `_match(value, facts)`, and `facts`
is built only from supplied pages and tool observations. **An actor-computed conversion is never an evidence fact**,
so the test can succeed only when the document itself already printed the converted figure. It is structurally
incapable of seeing the conversion the actor performed. The converted value is sitting in `state["computed"]`,
which the scale rule never reads.

**Sample before concluding.** Of the first 10 of the 29 `scale_conversion_missing` findings on this item, the
converted form is present verbatim in the trace for **5**: 1,004,461→1,004.461; 1,056,757→1,056.757;
1,105,788→1,105.788; 1,427,445→1,427.445; 1,860,040→1,860.04. So roughly half the firings on this item are false,
and the other half are consistent with the actor's real, later unit error (it used 2,331.274 where 2.331274 was
required and caught itself at step 19). The check is detecting a genuine problem in this trace while naming the
wrong values and asserting something false about them.

**Where the problem arose:** primarily **tracking whether an obligation was later resolved** — the needed fact is
collected but never consulted. Secondarily **report wording**: "No conversion step appears" is an absolute claim
the checker never had the evidence to make, having inspected only provenance.

**Does the plain report already surface it?** No — on UID0014 every actionable finding is S/C-layer, so this is the
one item where the stage/action layers carry the whole payload. That payload is about half wrong.

## 2b. Worked case — wrong-period row (UID0026)

Question: *"In the calendar year that the treasury notes of 1890 were removed from the U.S federal government
ledgers, how much paper money was added in circulation? Report your answer in millions of nominal dollars."*

The actor computed `31,336 − 29,385 = 1,951` and answered `1951`.

**Verifying the reviewer's claim against the supplied evidence.** The supplied page (1962 bulletin, page 82)
carries the header

```
| End of fiscal year or month | Total money in circulation 1/ | Paper money 2/ | …
|  |  | Total paper money | Gold certificates | Silver certificates | Treasury notes of 1890 | …
```

and these rows:

```
| 1960-December | 32,869 | 30,442 | 30 | 2,110 | 1   | 313 | 27,837 | 96 | 55 |
| 1961-January  | 31,776 | 29,385 | 30 | 1,986 | 1   | 305 | 26,913 | 95 | 55 |
| 1961-December | 33,918 | 31,336 | 30 | 2,120 | *   | 314 | 28,731 | 89 | 54 |
```

Cell 3 is **Total paper money**. So both of the actor's values are in the **correct column**; the error is the
**row**: it used `1961-January` (29,385) as the start of calendar 1961 instead of `1960-December` (30,442). Over
the supplied rows the paper-money change for 1961 is `31,336 − 30,442 = 894`. (Stated as arithmetic over the
supplied evidence; no answer key was consulted.)

This also adjudicates a disagreement between the two reviewers of §5 of the probe report: the report-guided
reviewer named **30,442**, which the header confirms is paper money; the trace-only reviewer named **32,869**,
which is *Total money in circulation*, the wrong column.

**Was the year header visible?** Yes, and better than that — the **period is in the row label** of every data row
(`1961-January`). The actor had the information; it chose the wrong row. "Wrong-year column" is therefore the wrong
name for this defect: the column was right and the row was wrong.

**Does the stage rule already require matching the requested period?** `operand_period_mismatch` exists for exactly
this, and it **cannot fire here**, for two independent reasons:

1. The question states no period. `oqa_obligations.derive` reads explicit cues only and returns
   `period(s) asked: UNKNOWN (no month-year, fiscal-year or year cue in the question)`, because the year has to be
   *inferred* ("the calendar year that the treasury notes of 1890 were removed"). Every period rule compares
   operands against *stated* periods, so an inferred-period question is unprotected by design.
2. Both values were classified as **computed**, not operands (`29,385` carries rule `equals` at step 3), and the
   period rules only examine operands. The extracted operand list for this item is just
   `[(3, '500,000'), (3, '30,222')]` — neither baseline value is in it.

**Did the system link the chosen value to the right column?** It linked the right *rows*: the ledger carries
`years` per fact — `31,336 → [1961]`, `30,442 → [1960]`, `29,385 → [1961]`. But **only the year is bound, not the
month**, so a year-level check would see `29,385` and `31,336` as both "1961" and consistent. The month is present
in the `line` text and never parsed. Nothing in the ledger identifies the *column* a value came from.

**Uncertainty to record.** The year itself is genuinely uncertain from the supplied text. The "Treasury notes of
1890" column reads `1` at 1960-December and `*` at 1961-December, and `*` is footnoted "less than $500,000" — a
balance falling below the rounding floor, which is not the same as removal from the ledgers. The actor's operand
`500,000` at step 3 shows it read that footnote. So the requested calendar year is an inference the supplied text
only weakly supports, and this audit does not resolve it.

**Where the problem arose:** **evidence extraction and binding** (month not bound; the two baseline values
classified as computed and so invisible to the period rules) **and the rule itself** (period matching is defined
only against question-stated periods).

**Does the plain report already surface it?** Partly, and it is the only finding this item got:
`[WARNING] (P) ungrounded_answer_value: the answer value 1951 appears in none of the 763 numeric mentions…`. That is
true and useful — but its justification is wrong: it continues *"No arithmetic was requested, so the answer should
have been copied from the evidence"*, because `operation` came back UNKNOWN. The question plainly requires a
difference. The rule conflates "no arithmetic **cue** detected" with "no arithmetic **requested**". The S and C
layers contributed nothing on this item.

---

## 3. Smallest changes worth making, in priority order

Each is backed by a saved example that should fire and one that should stay quiet. **No change is applied here.**

| # | Change | Should FIRE on | Should STAY QUIET / mark resolved on |
|---|---|---|---|
| 1 | **Consult `state["computed"]` before claiming a conversion is missing.** Treat the obligation as discharged if a value equal to `operand × factor` appears as a computed value at any step. | a raw millions figure with no converted counterpart anywhere in the trace (the 5 of 10 sampled on UID0014 where no converted form exists) | **UID0014 `1,004,461`** — converted at step 1 (`= $1,004.461 billion`), re-quoted raw at steps 9/19 |
| 2 | **Reword to a claim the checker can support**: "no conversion of this value appears in the returned evidence" instead of "No conversion step appears". | every current firing | — (wording only; removes an unsupportable absolute) |
| 3 | **Exclude numbers that occur in the task prompt's own text from operand extraction.** | **UID0034 `7069`**, lifted from a FRASER URL in the actor's own reasoning | **UID0017 `0.1234` / `12.34`**, which are the answer-format example in the question text, not operands |
| 4 | **Do not report provenance-missing for a value that is the result of an arithmetic step present in the trace.** | UID0034 `7069` (no arithmetic produced it) | **UID0014 `112.5`** (from `= 2 * 112.5`) and `1.1057` (the actor's own converted receipts list) |
| 5 | **Bind the month, not only the year, from a row label**, and record the column header a value sits under. | **UID0026** — a 1961-January baseline used for a calendar-1961 change, while the ledger already knows both rows | items where both operands share a row label, where no mismatch exists to report |
| 6 | **Separate "no arithmetic cue" from "no arithmetic requested"** in `ungrounded_answer_value`'s wording, and say the operation is UNKNOWN. | **UID0026** — the sentence currently asserts no arithmetic was requested for a question that requires a difference | an item whose question has an explicit single-value cue, where the claim is correct |
| 7 | **Either let period rules run against an inferred period, or state in the report that the asked period is UNKNOWN and the period checks are inactive.** | UID0026, where silence currently looks like a pass | UID0079, where the period *is* stated and `asked_period_absent` correctly fires |

Changes 1–4 are the ones the evidence most strongly supports: they target the two rules responsible for the
report's only distinctive content and for all of its measured false positives.

**Which finding could inform a reusable skill edit?** Only **UID0034's `7069` read out of a URL**, which
generalises to "never take an operand from a URL, identifier or document number". It is worth noting that arm A's
analyst derived essentially that rule — *"No Value by Inference … a paper coded 8703 is NOT thereby published in
1987"* — from the raw trace with no report at all, so even this one is not unique to the stage/action layers. The
other candidate, `grep_cap_reached` ("a capped result is not proof of absence"), is a **P-layer** finding that a
plain progress report already carries, and it is the finding that both arms' optimizers independently turned into
a termination rule.

## 4. Gaps

- `scale_conversion_missing` was sampled 10 of 29 on **one** item; `operand_provenance_missing` 3 of 18 across
  three items. Neither is a full census, and the "no conversion found" half of the scale sample rests on a textual
  pattern that could miss conversions phrased differently.
- Only epoch-2 arm-B attempts were audited, plus three successes. Eight of the fourteen rules never fired here, so
  this says nothing about their accuracy.
- The column-identification argument for UID0026 reads a two-row header by cell position; the parsed table has
  blank continuation cells, and no code binds values to columns, so that mapping is mine and not the checker's.
- Whether any of these changes would alter a real SkillOpt edit is untested. The companion probe
  (`OFFICEQA_REPORT_VALUE_PROBE.md`) found no report-attributable diagnosis reaching a reviewer's advice, and the
  replay that would settle the optimizer question is scoped in `OFFICEQA_EPOCH2_REPORT_AUDIT.md` §6.
