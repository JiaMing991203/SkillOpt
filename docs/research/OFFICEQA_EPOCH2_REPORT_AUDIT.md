# Arm B epoch 2: would the corrected stage reports have changed the advice?

**Conclusion: no — not the advice, although 5 of the 8 individual diagnoses change.** The per-item reports
differ substantially, but the report *set* the analyst saw carries the same diagnosis categories in the same
quantities, and both edits the optimizer actually applied rest on categories that are present either way. No
replay is warranted on this evidence. Offline only: zero GPU jobs, zero paid calls.

Date 2026-11-16. Arm B run `20261116-160952_officeqa_arm_66228142` (job 66228142), epoch-2 step
`arm_B/steps/step_0002`. Original artifacts untouched; the 8 reports that were sent are preserved and verified
byte-identical throughout this investigation.

## 1. Method, and a check on the baseline

The 8 epoch-2 failures are UID0014, UID0017, UID0026, UID0028, UID0030, UID0031, UID0034, UID0079. For each,
`oqa_reports.build_one` was run twice with the same logic and the same permitted inputs (prediction directory +
question + results file, condition C): once on the **epoch-1 shared trace**, which is what the sent report was
built from, and once on the **saved epoch-2 trace**, which is what it should have been built from.

Baseline check: all 8 reports regenerated from the epoch-1 traces are **byte-identical to the files actually
sent**. So the "old" column below is the real sent text, not an approximation, and nothing here is reconstructed.

## 2. All eight cases

| Item | diagnosis in the report SENT | diagnosis in the CORRECTED report | class |
|---|---|---|---|
| UID0014 | operand_provenance_missing, scale_conversion_missing | the same two | counts/wording only |
| UID0017 | answer_not_derivable, grep_cap_reached | grep_cap_reached, operand_provenance_missing | **different diagnosis** |
| UID0026 | grep_cap_reached | ungrounded_answer_value | **different diagnosis** |
| UID0028 | grep_cap_reached | grep_cap_reached | same diagnosis, different step |
| UID0030 | operand_provenance_missing | *(none actionable)* | **different diagnosis** |
| UID0031 | grep_cap_reached | grep_cap_reached | same diagnosis, different step |
| UID0034 | *(none actionable)* | grep_cap_reached, operand_provenance_missing | **different diagnosis** |
| UID0079 | asked_period_absent, operand_provenance_missing, ungrounded_answer_value | asked_period_absent | **different diagnosis** |

5 different diagnosis, 2 same diagnosis at a different step, 1 counts/wording only.

**But the set composition is unchanged**, and the analyst reads the set, not one report:

| Diagnosis | items in the reports SENT | items in the CORRECTED reports |
|---|---|---|
| `grep_cap_reached` | 4: UID0017, 0026, 0028, 0031 | 4: UID0017, 0028, 0031, **0034** |
| `operand_provenance_missing` | 3: UID0014, 0030, 0079 | 3: UID0014, **0017**, **0034** |
| `ungrounded_answer_value` | 1: UID0079 | 1: **UID0026** |
| `scale_conversion_missing` | 1: UID0014 | 1: UID0014 |
| `asked_period_absent` | 1: UID0079 | 1: UID0079 |

Total report payload: 18,082 chars sent vs 18,271 corrected, **+1.0%**.

## 3. Two concrete examples

**UID0026 — the diagnosis inverts.** Epoch-1 trace: 24 tool calls, 49 turns, no answer. Epoch-2 trace: **1 tool
call, 4 turns, answered `1951`**.

- sent: *"step 8: grep returned 20 lines, which is the tool's cap; the evidence for this pattern is incomplete and
  no conclusion that something is absent is supported by it"* (and the same at step 30).
- corrected: *"the answer value 1951 appears in none of the 763 numeric mentions that the supplied pages and the
  returned tool observations contain. No arithmetic was requested, so the answer should have been copied from the
  evidence."*

These are opposite advice: "you searched exhaustively and your evidence is incomplete" versus "you answered
without looking". The trace evidence for the difference is the tool-call count, 24 against 1.

**UID0034 — silence becomes three warnings.** Epoch-1 trace: 11 tool calls, answered `1987`. Epoch-2 trace: 14
tool calls, 30 turns, **no answer**.

- sent: no ADVICE or WARNING findings at all.
- corrected: two `grep_cap_reached` warnings (steps 18 and 24) plus `operand_provenance_missing` at step 21 —
  *"the operand 7069 is stated in the reasoning but matches no numeric mention available at that point ... Line:
  'Looking at the source URL provided in the pre-parsed content: https://fraser.stlouisfed.org/title/
  treasury-bulletin-407/'"*, i.e. the model read a number out of a FRASER URL.

This is the clearest information loss: an item with three diagnosable problems was reported as clean.

## 4. Does any changed diagnosis connect to an edit the optimizer proposed?

The saved optimizer output was searched for every finding category name, for "Stage and Action Report", for
"[WARNING]" and "[ADVICE]", and for report-specific phrases such as "stage locate_span" and "contract
table.scale". **Zero occurrences.** Every connection below is therefore *plausible*, never demonstrated.

Arm B's epoch-2 applied edits, and where their support stands:

1. **"## Termination Discipline — Local tools are capped (grep returns ~20 lines ...). A capped or empty result
   is not proof of absence — do not re-run the same query idea."** This plausibly tracks `grep_cap_reached`,
   which appears on **4 items in both** report sets. Support unchanged.
2. **"Normalize units before computing or answering ... thousands→billions ÷1,000,000"** plausibly tracks
   `scale_conversion_missing`, which appears on **UID0014 in both** sets, where the only change was counts and
   wording. Support unchanged.

The categories that *did* change map onto no applied edit. The nearest case is UID0026's new
`ungrounded_answer_value`, which relates to a **proposed** edit — *"Treat the pre-parsed oracle page(s) ... as
primary evidence: read them fully before re-searching"* — that the ranking stage dropped (3 proposed, budget 2).

One further point against materiality: the analyst's own reasoning names *"UID0017, UID0034, UID0014,
UID0079 ... looped in long reasoning, repeated the same searches, or called an undefined 'answer' tool and never
wrote `<answer>`"*. It identified UID0034's failure mode **with no report saying anything about UID0034**, from
the raw trace. The corrected report would have corroborated that reading rather than redirected it.

## 5. The fix, and the regression result

Cause: the hook cached at `<report dir>/<id>.md` and reused that file whenever it existed, so the key was the
item id. The key now covers everything the report derives from — the conversation, the user prompt the actor was
given, the question string and the condition — as `<id>.<12-hex>.md`. The arm stage no longer pre-seeds reports
from the shared run, since copying plainly-named files is exactly what enabled the reuse; the hook regenerates
the same bytes deterministically. `oqa_optimizer_input_audit.find_report` accepts both namings so preserved
artifacts still read.

**Regression test result: PASS** (`tests/test_officeqa_answer_key_isolation.py`, 19 tests; whole suite 1976
passed, 11 skipped).

- `test_two_traces_of_one_question_get_their_own_reports` renders one question from two different traces and
  asserts each **rendered optimizer request** carries its own trace's report: trace A hit the 20-line grep cap
  and its embedded report says `1 tool call(s)` and *"which is the tool's cap"*; trace B made no tool call, so
  its report says `0 tool call(s)` and cannot contain the cap warning. Two cached files exist, no `UID0033.md`,
  and re-rendering trace A reuses its own cache without writing a third file.
- `test_the_cache_key_covers_every_input_that_changes_the_report` asserts the key changes with the conversation,
  the user prompt, the question and the condition, and is stable otherwise.
- **End-to-end on the real data:** pointed at a directory holding all 8 stale reports under their original
  names, the fixed hook ignored them and generated 8 keyed reports that match the independently-corrected
  reports byte-for-byte, while the 8 stale files stayed byte-identical.

## 6. Replay decision: not warranted

Under the stated criterion — replay if the corrected reports *materially change the advice* — the answer is no.
The diagnosis for 5 of 8 items changes, but the set the analyst works from carries the same five categories in
the same counts, the payload differs by 1%, both applied edits rest on categories that are unchanged, and the one
changed category with a matching proposed edit had that edit dropped by the ranking stage anyway.

Were it run anyway, the replay **is covered by the existing authorization** (8 of 24 DeepSeek calls and 6.23 of
8 A40-hours remain) and would need:

- 1 A40, `--time=00:45:00`;
- 4 optimizer calls (2 analyst + 1 merge + 1 ranking), ≈$0.02;
- 12 validation rollouts for the gate, ≈9 min; total ≈25 min including vLLM startup;
- S0 as the starting skill, the saved epoch-2 training traces injected as the step rollout, the shared validation
  baseline injected, `train.num_epochs=1`, and the optimizer settings, edit budget (2 at this step) and gate
  unchanged; scored against S0's 5/12;
- a pre-flight check that the rendered input differs from the original only inside the report blocks.

Any such result would be exploratory: one optimizer draw, and the gate needs 6/12 to accept.

## 7. Gaps

- No content-level discriminator exists for UID0028, UID0031 and UID0079's `grep_cap`/period findings where both
  traces give the same counts; byte-identity and mtime carried the provenance argument there.
- "The advice would be the same" is an inference from set composition and from the absence of any report citation
  in the optimizer's output. It is not a demonstration; only the replay would demonstrate it.
- A historical run's exact sent prompt can no longer be reproduced by re-running the hook, because the hook now
  regenerates rather than honouring a plainly-named cached file. The sent bytes remain available as artifacts,
  and `find_report` still reads them.
- Held-out scores were not consulted. Job 66234927 stands only as the shared starting-skill baseline and its
  outcome remains sealed.
