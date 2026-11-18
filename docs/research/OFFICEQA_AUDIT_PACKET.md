# OfficeQA: offline audit of the recovery report

**2026-11-18. Rules OQA-AUD-1 (Workstream A) and OQA-AUD-2 (Workstream B). Amendments A-3 and B-3.
Zero API calls, zero GPUs, zero new gameplay. Three CPU jobs, 0.87 of 2 authorized requested
job-hours. Saved artifacts only.**

Artifacts: `$CAMP/audit_20261118/` on the cluster. Nothing under `followup_20261118/` or
`followup_20261119/` was written, and the frozen campaign was not touched.

---

## The five answers, first

**1. Is the eight-pass measurement complete and trustworthy?** **Yes — the records are complete and the
summaries were wrong.** All 192 intended `(pass, question)` records exist exactly once: eight passes,
each with the frozen split's 24 validation ids, no duplicate, no extra row, no missing row, and 35 of 35
consistency assertions pass. Every score, emission count and recovery count the packet published is
confirmed from the rows. What was wrong was one block: the termination table, which was computed over
**179** rollouts instead of 192, and one sentence derived from the same truncated source.

**2. What are the corrected baseline numbers?** The headline numbers do not move: **9.75 of 24 mean
(range 7–12, sd 1.753), 21.25 of 24 answers emitted, 14.6 of 24 recovered from `message.reasoning`, 0
provider errors in 1,476 recorded attempts.** The corrected termination table is over 192 rollouts:
**117 recovered, 53 from ordinary content, 18 out of tool turns, 3 truncated, 1 declined half-answer** —
and 22 of 192 rollouts produced no answer, not 21 of 179. Questions answered in **every** pass:
**17 of 24**, not 20.

**3. Which earlier statements must be withdrawn or qualified?** Five, listed in the discrepancy table of
§2.3. The substantive ones: "the 179 completed question-rollouts" (there are 192), the five termination
counts, "47 answers did arrive in ordinary `content`" (53), and "answered in all 8 passes — 20 of 24"
(17). Also `OFFICEQA_STATE.md`'s rounding of the sample-size arithmetic ("sd ≈ 1.5 … below about 6 … 3
… 2") understates the packet's own figures (sd 1.753 → 6.9 / 3.5 / 2.5), and those thresholds need the
qualifications in §5.

**4. What primarily causes missing header bindings?** **Not the corpus, and not mostly the header.** Of
the 196 operands the audit followed across 20 records, **141 never reach a header question at all**
because the number the actor wrote is in no data cell of the prefix — it is a sum, an average or a
converted figure the actor computed. **19 more match several cells.** Only **29 bind a single cell**,
and all 29 of those are blocked by the same clause, `nearer_scale_between` being UNKNOWN for want of a
bound column. In **every one of those 29** the table's header row **was observed** and names **no money
scale at all**. So "no bound header" never meant "header information absent": 105 of the 155 tables in
these prefixes have an observed header row, and the binding is refused because an oracle page's SPLIT
rendering cannot be merged by column index soundly — not because the text is missing.

Behind that, three concrete defects, each against the extractor's own stated semantics, each now
repaired with a regression on the real witness: a list comma read as a thousands separator (26 years
admitted as money, now 0), the record's own flat headers never offered as references (UID0014's
8,006,880 appeared as an "ambiguous match" between a bound and an unbound copy of one cell), and one
step read by nobody. After the repairs the reserved checking records go from **0 holding bindings to 4**.

**5. What is the smallest justified next step?** **Decide proposal P-1 (§7) and nothing else.** The four
new findings are all on UID0014 and all resolve to OUTSTANDING ("no later step states a factor"),
because this actor writes the operand, the factor and the answer in **one final message** — on UID0014
the operand's message is step 45 of 45. Reading that message as well turns all four into
`incorrect_factor_stated`, VIOLATED, **wrong by 10^3**: the campaign's actual 1,000× error. That is a
change to what "committed before the later steps are read" means, so it is yours to accept or refuse.
And it is worth knowing before you spend anything on it that the comparison's verdict for UID0014 is
**`repeats_what_was_available`** — the campaign's own stage report for that attempt already carries
`**VIOLATED** [unit_match] the value used must be in the asked scale (billions)`. **The repairs bought a
sound finding where there was none; they did not buy a diagnosis the project did not already have.**

---

## 1. Scope, and what was read

| | |
|---|---|
| Authorized | ≤ 2 requested CPU job-hours, ≤ 2 CPUs per job, ≤ 2 concurrent jobs, **0 API calls, 0 GPUs** |
| Used | **0.87 requested job-hours**, 3 jobs, 2 CPUs each, **0 API calls, 0 GPUs, $0.00** |
| Deployed | bundle `$PROJECT_ROOT/oqa_aud_20261118`, commits `ba53007` (jobs 66356410/66356411) and `3974433` (job 66356437) |
| Written | `$CAMP/audit_20261118/` only |
| Read | `followup_20261119/s0_repeat/p1..p8`, `followup_20261118/connected_contract/`, `shared/val_baseline/`, `heldout/S0.md`, `arm_A/skills/skill_v0000.md`, the campaign's `arm_B`/`arm_C` step traces and stage reports, `ledger/`, and one campaign call log |
| Keys | every credential is unset by the job body before Python starts; the body logs it |

The validation set was used only as the subject of the audit, which is the measurement already taken on
it. No held-out question, answer or trajectory was read.

---

## 2. Workstream A — one authoritative manifest

### 2.1 The manifest

`research/officeqa/oqa_audit_s0.py manifest` builds one record per intended `(pass, question)`. The
intended grid is **declared**, not derived: the 24 ids of `sets.val` in
`docs/research/OFFICEQA_FROZEN_SETS_V3.json` (rule OQA-SEL-3, digest `117836ff4139…`) crossed with the
eight pass labels. A grid derived from the rows cannot notice a missing row.

```
rule OQA-AUD-1  intended 192 records (8 passes x 24 questions)
  records by status: {'complete': 192}
  duplicates 0  extra rows 0  abandoned-attempt groups 2
    p1: rows 24 jobs ['66346539'] attempts 154      p5: rows 24 jobs ['66346540'] attempts 190
    p2: rows 24 jobs ['66346539'] attempts 215      p6: rows 24 jobs ['66346540'] attempts 178
    p3: rows 24 jobs ['66346539'] attempts 202      p7: rows 24 jobs ['66346540'] attempts 212
    p4: rows 24 jobs ['66346539'] attempts 148      p8: rows 24 jobs ['66346540', '66351468'] attempts 177
  unresolved requests: 4  owners without a release: ['66346540r0p1430962']
```

Each record carries: the source file and 1-based row index, a content digest of the row, the job that
produced it, the pass's original and continuation job, the final provider response id, the recorded HTTP
attempts for that question split by job, the sha256 of the actor-visible system prompt and user prompt,
the rollout settings fingerprint, correctness, emission, recovery source and outcome, termination cause,
and the trace and call-log paths. **No record carries an uncertainty flag.**

**How the continuation joins the initial output, and why the clock is not enough.** `run_batch` resumes
by question id into the *same* directory, so pass 8's single `results.jsonl` holds 11 rows from job
66346540 and 13 from 66351468. Jobs 66346539 and 66346540 were submitted together and **ran
concurrently** (both started 22:52:35), so any instant before 00:02:19 lies inside both windows and wall
clock alone cannot attribute a pass. Attribution therefore uses two independent facts: each job's own log
line declaring the passes it ran ("S0 reproducibility: passes 1..4 …"), and each row's **own** final
`response_metadata.response_id` matched against the pass call log to get that call's `started_utc`. The
11/13 split this yields agrees exactly with the `predictions/<UID>/` directory mtimes (11 dirs at
00:06:48–00:12:28, 13 at 00:15:18–00:32:32), which were not used to derive it.

Duplicates are never resolved by score: there are none, and the assertion that says so is checked.

### 2.2 The corrected eight-pass table

| pass | records | correct /24 | soft | emitted | from `reasoning` | from `content` | recorded attempts | provider errors | job(s) |
|---|---|---|---|---|---|---|---|---|---|
| p1 | 24 | 11 | 0.458 | 22 | 16 | 6 | 154 | 0 | 66346539 |
| p2 | 24 | 9 | 0.375 | 19 | 13 | 6 | 215 | 0 | 66346539 |
| p3 | 24 | **12** | 0.500 | 21 | 17 | 4 | 202 | 0 | 66346539 |
| p4 | 24 | 11 | 0.458 | 22 | 17 | 5 | 148 | 0 | 66346539 |
| p5 | 24 | 9 | 0.396 | 21 | 15 | 6 | 190 | 0 | 66346540 |
| p6 | 24 | 11 | 0.458 | 22 | 15 | 7 | 178 | 0 | 66346540 |
| p7 | 24 | 8 | 0.333 | 20 | 12 | 8 | 212 | 0 | 66346540 |
| p8 | 24 | **7** | 0.292 | 23 | 12 | 11 | 177 | 0 | 66346540 **+ 66351468** |
| **total** | **192** | **78** | | **170** | **117** | **53** | **1,476** | **0** | |
| **mean** | 24 | **9.75** | 0.409 | **21.25** | 14.6 | 6.6 | 184.5 | 0 | |
| sd / range | | 1.753 / 7–12 | | 19–23 | 12–17 | 4–11 | | | |

### Termination causes, reconciled over all 192 rollouts

```
  117  answer_recovered_from_reasoning
   53  answer_emitted                                  (ordinary content)
   18  tool_turn_budget_exhausted_after_tool_call
    3  completion_truncated_at_cap
    1  empty_reply_content_null+recovery_incomplete_answer_tag
  ---
  192  = the episode count            117 + 53 = 170  = the emitted count
```

Both sums are asserted, per pass and in total. 22 of 192 rollouts produced no answer, and every one has
a named cause.

### Paired, question by question (eight observations each, none missing)

| | count | questions |
|---|---|---|
| answered in **all eight** passes | **17** | UID0001, 0039, 0041, 0072, 0091, 0109, 0142, 0154, 0159, 0170, 0190, 0217, 0220, 0233, 0234, 0235, 0239 |
| answered **sometimes** | 6 | UID0027, 0052, 0070, 0086, 0213, 0240 |
| **never** answered | 1 | UID0161 |
| correct in all eight | 5 | UID0072, 0142, 0190, 0233, 0235 |
| correct **sometimes** (flips) | **10** | UID0001, 0039, 0070, 0086, 0091, 0159, 0170, 0217, 0234, 0239 |
| never correct | 9 | UID0027, 0041, 0052, 0109, 0154, 0161, 0213, 0220, 0240 |

Per-pass totals equal the question-level totals in all three columns (emitted 170, correct 78, recovered
117), which is one of the assertions. `always` / `never` / `sometimes` are asserted only over a complete
row of eight observations; anything short of that would be reported as `undetermined`, and nothing is.

### 2.3 The discrepancy table

16 published quantities were transcribed from `OFFICEQA_RECOVERY_PACKET.md` §7 and recomputed. **Eleven
agree exactly** — every per-pass score, emission and recovery count, the mean, the sd, the attempt total,
the provider-error count, and all four paired-question lists. Five disagree:

| published claim | where | corrected | cause | consequence |
|---|---|---|---|---|
| "the **179** completed question-rollouts" | §7 termination block | **192** | an aggregation over p1–p7 complete **plus only the 11 rows of pass 8's killed first attempt** | the denominator of the termination block only |
| `answer_recovered_from_reasoning` **111** | §7 | **117** | same | +6 |
| `answer_emitted` **47**; "47 answers did arrive in ordinary `content`" | §7 | **53** | same | +6; the share of answers arriving in `content` is 31%, not 28% |
| `tool_turn_budget_exhausted_after_tool_call` **17** | §7 | **18** | same | +1 |
| "**21** of 179 rollouts produced no answer" | §7 | **22 of 192** | same | +1 |
| "answered in all 8 passes — **20** of 24" | §7 paired table | **17 of 24** | the same truncated source, under an "answered at least 7 times" reading mislabelled as "in every pass" | the claim is false under every reading; see below |

`completion_truncated_at_cap` (3) and `empty_reply…incomplete_answer_tag` (1) were already right.

**The cause is demonstrated, not inferred.** Re-running the same aggregation with pass 8 cut to its first
11 rows reproduces the published block exactly:

```
truncation demonstration {'p8': 11}: 179 rollouts, 158 answers,
  causes {'answer_recovered_from_reasoning': 111, 'answer_emitted': 47,
          'tool_turn_budget_exhausted_after_tool_call': 17, 'completion_truncated_at_cap': 3,
          'empty_reply_content_null+recovery_incomplete_answer_tag': 1}
  answered_in_every_pass_seen 17   answered_at_least_seven_times 20   answered_in_all_8_passes 7
```

All five counts, the rollout total and the answer total match the published figures. 179 = 7×24 + 11, and
the 11 ids are exactly the first 11 rows of `p8/results.jsonl` — the ones job 66346540 wrote before its
wall limit — of which 6 recovered and 5 came from content, which is precisely the +6/+5 deficit. The
"20" is reproducible from the same truncated source only as "answered at least seven times"; no
computation over the complete records produces it, and it exceeds the hard ceiling (the smallest per-pass
emission is 19), which is why that ceiling is now an assertion.

**Which input, and which operation.** The saved tooling is not at fault: `oqa_s0repeat.py`'s own output,
preserved in both cc jobs' `s0_summary.txt` (jobs 66352197 and 66352245), reports the complete eight-pass
figures correctly, and `S0_REPEAT.json` holds 192 rows with per-pass termination causes summing to 24
each. The tool never prints a termination table at all. The published block came from an **ad-hoc
aggregation run between 00:12:35 (the timeout) and 00:32:32 (the top-up's completion)**, while
`p8/results.jsonl` held 11 rows. That command was not saved and **cannot be recovered**; what is
established is the arithmetic identity above. Everything else in §7 came from the complete source.

**One latent defect in the summariser, now recorded (amendment A-3).** `oqa_s0repeat.summarise` computes
`questions_always_correct` as `times_correct == passes_seen`, so a question observed in seven passes and
correct in all seven would count as "always correct". That is harmless in the published artifact, because
all eight passes are complete — but it was live in the intermediate aggregation, and a missing
observation must never be evidence. The audit's classifier refuses to label an incomplete row.

### 2.4 The four unresolved requests, and six abandoned attempts

Kept apart from episode outcomes, and reconciled from the ledger alone.

```
owner                 attempt  usage  unresolved  released  claimed                         given back
66346539r0p9717           154    154           0  True      200 att / $0.150000             46 att / $0.060262
66346539r0p21345          215    215           0  True      250 att / $0.200000             35 att / $0.060020
66346539r0p38210          202    202           0  True      250 att / $0.150000             48 att / $0.018058
66346539r0p61090          148    148           0  True      150 att / $0.150000              2 att / $0.068929
66346540r0p1377672        190    190           0  True      200 att / $0.150000             10 att / $0.035066
66346540r0p1394573        178    178           0  True      200 att / $0.150000             22 att / $0.035725
66346540r0p1412829        212    212           0  True      250 att / $0.200000             38 att / $0.051892
66346540r0p1430962         61     57           4  FALSE     100 att / $0.100000             -- claim RETAINED
66351468r0p1437795        120    120           0  True      150 att / $0.150000             30 att / $0.060749
```

* **The four are exactly the gap** between the `attempt` records (61) and the `usage` records (57) of the
  one process that was SIGKILLed at its wall limit. A request whose reply never arrived writes no
  call-log record, so it is invisible in the 1,476 and in every per-pass attempt count.
* **A request sent is not a provider error.** "0 provider errors in 1,476 recorded attempts" stands as
  stated; the honest full sentence is **"0 errors in 1,476 recorded attempts, and 4 further requests were
  sent whose outcome is unrecorded"** — 1,480 requests in total.
* **The accounting is conservative, not short.** That process ran no `atexit` hook, so its whole claim
  (100 attempts / $0.10) is retained against a measured 57 attempts. The ledger therefore overcharges
  this measurement by 43 attempts and about $0.07, which is the safe direction for a cap.
* **Six attempts back no record.** Job 66346540 had begun two pass-8 rollouts when it was killed —
  UID0159 (5 recorded attempts) and UID0161 (1) — and the resuming job re-ran both questions from
  scratch. Those six attempts are real spend attached to no manifest record. They are reported as
  `abandoned_attempts`; they are **not** duplicate records, and no row was ever chosen between.

---

## 3. Verifying the claimed historical/new input match

The brief flags two S0 digests: the development manifest's `9d377d1c2906` and the campaign audit's
injected S0 `b2f8624c8da8` at 886 bytes.

**They are one document.** `heldout/S0.md` is 887 bytes, sha256 `9d377d1c2906…`, and ends in a single
`\n`. With that newline stripped it is 886 bytes and sha256 **`b2f8624c8da8…`**. `arm_A/skills/skill_v0000.md`
is byte-identical to it (887 B, `9d377d1c2906`). So the 886-byte figure is the skill text as extracted
from the rendered prompt, where the renderer does not carry the file's final newline; nothing substantive
differs, and no normalization beyond that newline is involved. No file anywhere under the campaign has
digest `b2f8624c8da8` or length 886.

**And the file digests are not what decides it — the rendered prompt is.** Comparing the campaign's own
S0 validation baseline (`shared/val_baseline/results.jsonl`) with each of the eight new passes, question
by question:

| check | result |
|---|---|
| actor-visible **system prompt** identical | **24 of 24 questions, in all 8 passes** (digest `b41c9dbeec8d43c5` everywhere) |
| actor-visible **user prompt** identical (question, oracle pages, candidate files, turn budget) | **24 of 24, in all 8 passes** |
| `resolved_source_paths` identical | yes, every question |
| `oracle_parsed_pages_included` | true on both sides, every question |
| `use_local_tools` | true on both sides, every question |
| task ids | the frozen `sets.val` 24, both sides |
| **generation payload as sent** | identical: `{model: qwen3-5-4b, temperature: 0.7, max_tokens: 8000, tools_sent: true, extra_body: null}` |
| `rollout_settings_fingerprint` | `d58cf3b59bbc2928` in all 8 passes; **absent in the campaign's baseline rows** |
| recovery policy | `committed_answer` in all 8 passes; absent (off) in the campaign |

Two differences, both **intended and explained**:

1. **The fingerprint is missing on the campaign side** because the field did not exist when those rows
   were written (it enters at commit `4b60132`). It is not a changed setting; it is a field added to the
   record. The settings it covers — search mode, local tools, turn budget, queries per turn, completion
   tokens — are fixed for both runs by one config file, and the rendered prompts being byte-identical is
   the stronger evidence, since the turn budget appears inside the user prompt in this mode.
2. **The recovery policy is the intervention under test.** It changes only which field of the reply the
   client reads; the transmitted request is unchanged, which is what `extra_body: null` on both sides
   shows.

**One limit, stated.** The gate's own baseline rollout (job 66272737) has **no call log** — the variable
that writes one was introduced in the campaign's second chain — so "the payload as sent" for the campaign
side is taken from a later job of the same config and code path (`run_66273856_arm_A_e2`, 808 records,
one distinct payload). For the baseline stage itself the evidence is the rendered prompts, the shared
config and the shared code revision, not a recorded request.

### The body script that was replaced while a job was running

**Established.** The bundle `oqa_fu2_20261118` was rebuilt six times during the follow-up, and each job
verified the manifest at start and logged its commit:

| job | phase | commit it verified |
|---|---|---|
| 66346074 | tests | `0119b4f` |
| 66346177, 66346233, 66346256, 66346322 | tests, prep, **episodes**, cc | `241f8e8` |
| **66346539, 66346540** | **S0 passes 1–7 + p8 prefix** | **`7e4a69a`** |
| **66351468** | **S0 pass 8 remainder** | **`7e4a69a`** |
| 66352197 | cc | `0e04284` |
| 66352245 | cc (the published CC artifacts) | `fa8f1c3` |
| 66352312 | tests | `4572c61` |

So `OFFICEQA_STATE.md`'s claim that the bundle's commit `4572c61` "is the revision every result below was
produced under" is **wrong and is withdrawn**: only the final test job ran under it. The S0 measurement
ran under `7e4a69a`, the twelve episodes under `241f8e8`, the published connected-contract artifacts
under `fa8f1c3`.

**It does not change any result, and that is checked rather than assumed:** `git diff 241f8e8 4572c61 --
skillopt/` is **empty**, so the client, the adapter, the recovery policy and the rollout loop are
byte-identical across every one of those commits. The differences are documentation, the `research/`
tooling and the cluster scripts.

**What remains unverifiable.** Job 66351468 read its body script from NFS lazily and the file was
replaced between 00:32:32 and 00:33:55; it died on a syntax error at the summarisation line immediately
after logging "pass 8: 24 rows, hard=7". What the job executed *up to that point* is pinned: the body
extracted the `7e4a69a` tarball to node-local storage at 00:14:06 and every Python call ran from that
copy, so pass 8's rows were produced by `7e4a69a` code. What cannot be reconstructed is the exact text of
the body lines the shell read **after** 00:32:32 — only their effect, which was to abort before any
further artifact was written. Nothing was overwritten and no result depends on them. The published
`S0_REPEAT.json` was written later by the cc jobs, whose own logs are preserved.

---

## 4. Workstream B — where the header binding is lost

The prefix is exactly the one the checker uses: the steps up to and including the first message in which
the actor writes a monetary operand. Later observations were never read while a cause was decided.

### 4.1 Cause counts

Primary cause per operand the checker would use, over the four reserved checking records, the four
learning records and the twelve new episode traces (post-repair extraction):

| primary cause | checking | learning | descriptive (12 episodes) | total |
|---|---|---|---|---|
| `operand_is_in_no_cell` | 31 | 5 | 105 | **141** |
| `several_plausible_bindings` | 11 | 3 | 5 | **19** |
| `header_observed_structure_lost` | 3 | 21 | 5 | **29** |
| `bound_no_failure` (the binding holds) | **4** | 0 | **3** | **7** |
| `header_never_observed` | 0 | 0 | 0 | 0 |
| `header_observed_not_connected` | 0 | 0 | 0 | 0 (it was the cause **before** repair B-3.2; see §4.3) |
| `identity_matched_wrongly` | 0 | 0 | 0 | 0 |
| `records_cannot_distinguish` | 0 | 0 | 0 | 0 |
| records with **no monetary operand at all** | 2 of 4 | 2 of 4 | 6 of 12 | 10 of 20 |

Secondary cause `condition_needs_more_than_the_hypothesis` on 8 of the 29 — those are the cells where
`nearer_scale_between` is the **only** unsatisfied clause. The other 21 are additionally blocked by
`table_states_scale` being false: their table states no money scale anywhere, which is genuinely absent
evidence, not a parsing loss.

### 4.2 Representative raw evidence

**(a) A bound cell whose binding is UNKNOWN although its header was observed — UID0115, operand `132,483`:**

```json
"cell":   {"raw": "132,483", "row_label": "Dec.", "col_index": 9, "scale": "millions",
           "column": null, "column_basis": "UNKNOWN", "header_known": false,
           "period_text": "December 1981", "table_key": "supplied_page:None:0"}
"header": {"header_form": "split", "header_rows_observed": true, "n_header_cells_with_text": 12,
           "money_scales_named_anywhere_in_the_header": [],
           "no_money_scale_anywhere_in_the_observed_header": true, "table_scale": "millions",
           "header_cells_sample": ["End of fiscal year or month", "Currencies no longer issued",
                                   "Currencies presently being issued 1/", "Total currencies",
                                   "Federal Reserve bank notes", "National bank notes",
                                   "Gold certificates 2/", "Silver certificates"]}
"nearer_scale": {"state": "UNKNOWN",
                 "why": "not observed for this cell: column header; absence cannot be asserted"}
"binding": UNKNOWN, blocked only by  nearer_scale_between (observed=None, want=False)
```

The header row is in the prefix, has twelve labelled cells, and names no money scale at all. The
hypothesis asks only that no nearer scope state **another** money scale. The evaluator asks for a bound
column before it will say so.

**(b) The same table in two renderings — the real witness for repair B-3.2, UID0014.** The oracle page
the harness supplies gives the SPLIT form; the actor's own `read` at step 5 returns the FLAT form:

```
page  (split):  | Fiscal year or month | Total | Income and profit taxes | Employment taxes | ... |
                | Total | Individual | Corporation, income and excess profits 2/ | ... |
                | 1930 | 3,040,146 | 2,410,259 | 1,146,845 | 1,263,414 | - | ... |

step 5 (flat):  | Fiscal year or month > Fiscal year or month | Total > Total |
                  Income and profit taxes > Total | Income and profit taxes > Individual | ... |
                | 1930 | 3040146 | 2410259 | 1146845 | 1263414 | - | ... |
```

Equal width, shared data rows. Before the repair the operand matched two cells and was reported ambiguous
because one copy had a column and the other did not:

```
before B-3.2:  hits=2  ->  ambiguous_cell_match
   {"raw": "8,006,880", "column": null,  "column_basis": "UNKNOWN", "table_key": "supplied_page:None:0"}
   {"raw": "8006880",   "column": "Income and profit taxes > Total", "column_basis": "FLAT",
    "table_key": "observation:5:0"}
after  B-3.2:  hits=2  ->  repeated_identical_cell_match   (both carry the FLAT path)
   binding HOLDS   required_exp -6  (thousands -> billions)
```

**(c) The years that were money — the real witness for repair B-3.1, UID0079:**

```
"I need to get the budget outlay data from the FFO-4 tables in the December Treasury Bulletins
 from odd years (2011, 2013, 2015, 2017, 2019)."
before B-3.1:  token "2011," -> grouped -> MONETARY, reason "thousands_grouping"   (x4)
after  B-3.1:  token "2011"  -> not an operand; UID0079 has NO monetary operand at all
```

**(d) The resolution, and why it stops at OUTSTANDING — UID0014, all four findings:**

```
operand 8006880  cell "8,006,880"  column "Income and profit taxes > Total"  scale thousands
asked billions -> required 10^-6
resolved from steps strictly after the operand's message:  no_factor_stated   -> OUTSTANDING (unknown)
the operand's message is step 45 of 45; there are no later messages at all
if that message may be read (PROPOSAL P-1):  incorrect_factor_stated -> VIOLATED (actor_error)
   "the binding gives this cell scale thousands and the question asks billions, which requires 10^-6;
    the actor states a factor of 10^-3; wrong by 10^3"
```

### 4.3 The three repairs, measured

Recorded as amendment **B-3** before the change, each against the extractor's own stated semantics, each
with a regression test on the real witness, and the pre-repair behaviour kept in the audit tool
(`data_cells_isolated`) so the before/after is a test rather than a claim.

| | defect | witness | measured effect |
|---|---|---|---|
| **B-3.1** | `_NUM` allowed a match to end in a comma, so a list comma made a year "grouped" and `classify_number` returned MONETARY before any year test | UID0079 `2011,` | year-shaped numbers used as monetary operands: **26 → 0** |
| **B-3.2** | `data_cells` never passed `references` to `cells_of`, whose docstring defines them as "flat-form tables from elsewhere in the same record"; each text was bound in isolation | UID0014 `8,006,880` | data cells with no bound header: **5,791 → 5,184** (−607); UID0014 alone **1,484 → 1,107**; the checking records' holding bindings **0 → 4** |
| **B-3.3** | the prefix ended at the operand's message and the later window began one step later, so one step was read by nobody | none on these records | no change here (every record's operand message is its last step); the regression is synthetic and asserts the window |

`oqa_lc_cells` and `oqa_lc_chain` are byte-identical; every change is in the B-2 layer above them.

### 4.4 The repaired check, beside the published one

| | | v3 as published | v4 repaired |
|---|---|---|---|
| **checking** (4 reserved records) | match status | 3 unique, 9 ambiguous, 11 no-cell | 3 unique, **4 repeated-identical**, 5 ambiguous, 7 no-cell |
| | bindings | 0 holding | **4 holding** |
| | resolutions | 3 UNKNOWN, 20 none | **4 OUTSTANDING**, 3 UNKNOWN, 12 none |
| **learning** (4 records) | match status | 8 unique, 4 ambiguous, 7 no-cell | **12 unique**, 3 ambiguous, 2 no-cell |
| | resolutions | 8 UNKNOWN | 12 UNKNOWN |
| **descriptive** (12 new episodes) | match status | 2 unique, 3 repeated, 8 ambiguous, 25 no-cell | 2 unique, **6 repeated**, 5 ambiguous, 32 no-cell |
| | resolutions | 5 UNKNOWN | **3 DISCHARGED**, 5 UNKNOWN |

The three DISCHARGED are UID0163's `3,550` and `3,475` in the new episode traces: the cell is already in
the asked scale (millions), so the required factor is 10^0 and none was applied. They are
**confirmations, not diagnoses**, and they come from descriptive records inspected after the bundle was
frozen.

Counts in that table are **predictions committed**, not operands in the message: `check_attempt` caps at
the first 12 operands per record (`ops[:12]`) and commits one prediction per occurrence, so a number the
actor repeats is counted repeatedly. The audit's uncapped pass is the one in §4.1.

### 4.5 The comparison: a sound finding, not a new one

`oqa_cc_run.py compare`, each record against **its own arm's** historical report:

```
verdict_counts: {"remains_unknown": 3, "repeats_what_was_available": 1}

UID0044 B -> remains_unknown              no prediction was committed for this attempt
UID0079 B -> remains_unknown              no prediction was committed for this attempt
UID0115 B -> remains_unknown              7 predictions, 0 findings; every resolution is UNKNOWN
UID0014 C -> repeats_what_was_available   12 predictions, 4 findings, 4 WELL BOUND, 0 weakly bound
   the historical report for this very attempt already carries:
     - **VIOLATED**    [unit_match] the value used must be in the asked scale (billions)  -- the same for 10 value(s)
     - **VIOLATED**    [unit_match] the value used must be in the asked scale (billions)
     - **OUTSTANDING** [unit_match] the value used must be in the asked scale (billions)  -- the same for 13 value(s)
     - **OUTSTANDING** [unit_match] the value used must be in the asked scale (billions)  -- the same for 2 value(s)
```

All four new findings pass the mechanised inspection `_well_bound` requires — a MONETARY operand, an
unambiguous cell match, a data cell at column index > 0 with a bound header, and a holding binding — so
this is not a false alarm. But the campaign's own stage layer already said it, and said it more strongly
(VIOLATED where the repaired contract says OUTSTANDING). **Valid additional diagnosis: 0 of 4. More
precise existing diagnosis: 0. Redundancy: 1. False alarm: 0. Unknown: 3.**

`repair` found **no counterexample**: no checked cell had its conditions met and its conclusion
contradicted. An actor error is not a counterexample to the hypothesis, which is the distinction the
condition exists to draw.

### 4.6 Lean

`oqa_cc_lean.py` over the v4 evidence: **return code 0, all statements accepted**, 7 evidence examples
encoded (v3 had 3), 0 skipped. Lean 4.20.0 core, `by decide` over finite domains.

```
unknownBindingNeverViolates            withdrawalReopensTheObligation
dischargeNeedsTheBinding               violationNeedsTheBinding
unknownNeverAccusesWhateverTheConversion
withdrawalNeverAccusesWhateverTheConversion
requiredExp billions->billions = 0 and needs no factor
applying a factor to an already-compatible value is VIOLATED
a missing factor is OUTSTANDING, never VIOLATED
a correct factor not carried through is OUTSTANDING
an already-compatible unit needs no factor at every money scale
requiredExp thousands->billions = -6        requiredExp thousands->billions != -3
"the 1,000x difference"                     7 evidence example(s)
```

**What Lean is trusted for here, and what it is not.** It re-decides the stage resolution over the finite
domain of binding truths, conversion verdicts and carry states, and it checks the scale arithmetic
(thousands → billions is 10^-6 and not 10^-3). **The facts it takes as given are extraction inputs:** the
bound scale, the asked scale, the stated factor and whether the converted value appears. Those come from
`oqa_cc_extract` — which this audit has just repaired three times — so Lean's acceptance says the
consequences are sound given the extraction, not that the extraction is right.

### 4.7 The five interpretation checks

| question | answer, with numbers |
|---|---|
| Does "no bound header" mean "header information absent"? | **No.** 105 of 155 tables in these prefixes have an observed header row (50 split, 44 single, 11 flat); 50 have none. Every one of the 29 bound cells whose binding stayed UNKNOWN is in a table **with** an observed header row |
| Can a table-wide scale be used under the existing hypothesis without inventing an unobserved absence of overrides? | **Yes for 29 of 29 of these cells, and the evaluator does not.** In all 29 the observed header row names **no money scale anywhere**, so "no nearer scope states another money scale" follows from the observed scope without assuming any index alignment. Closing that gap is **proposal P-2**, not a repair: it changes what the checker decides |
| Does `nearer_scale_between` preserve UNKNOWN when the scope was not observed? | **Yes, on the real records.** 29 UNKNOWN and 7 ABSENT over the bound cells; all 29 UNKNOWN have no observed column, and `False` is never returned from a missing field. The B-2 repair holds |
| Do dates, row labels or prose digits still become monetary operands? | **Years did, and no longer do: 26 → 0.** Row labels: 0 — the row-label column is dropped by index, 77 such cells on UID0014 alone. Prose digits: rejected by name (`bare_prose_digit`, `list_number`, `footnote_marker`, `month_fragment`, `date_fragment`), and an unsettled four-digit number is AMBIGUOUS and unused (581 of 714 candidate numbers in the checking records) |
| Are no-conversion-needed, correct, wrong and converted-value-use distinguished correctly? | **Yes, and all four paths are exercised on real records.** From later steps: `requirement_unknown` 29, `no_factor_stated` 4, `no_conversion_required` 3. Under P-1: `requirement_unknown` 29, `incorrect_factor_stated` 4, `no_conversion_required` 3. `converted_value_used` is reported apart from the factor's correctness, and `requirement_unknown` exists so that no factor is called wrong with nothing behind it |

**No condition was weakened to produce a finding.** The two gaps that would produce more — P-1 and P-2 —
are left as proposals with their measured effect.

### 4.8 Re-assessing "contracts cannot be evaluated with thinking enabled"

The packet's sentence conflates four different interventions. Separated:

| intervention | what it needs | does the unit-binding checker need it? |
|---|---|---|
| **Use the tool observations and the committed answer already in the trace** | nothing new; both are already in every saved trace | **This is all the unit-binding checker needs.** Its evidence is table text from observations plus a number. The one thing it cannot get this way is the *operand* — the number the actor wrote — which with thinking on is only the final answer block |
| **Record additional provider fields for audit** | `TARGET_OPENAI_COMPATIBLE_RAW_CAPTURE`, already built and off by default | No. It is an audit facility |
| **Give those fields to the analyst or learner** | a decision about learner information; **not authorized** | No |
| **Change actor generation by disabling thinking** | a different actor condition, needing its own comparison | No, but it is what currently puts the actor's arithmetic into the trace |

**The corrected statement.** A trace-based checker whose evidence is tool observations and the final
answer can be evaluated on thinking-on traces today. What cannot be evaluated on them is a checker that
needs the actor's *intermediate* numbers, because with thinking on the trace carries 20–36 characters of
actor text (the recovered answer block alone) against 575–8,864 with `enable_thinking: false`. The
unit-binding contract is of the second kind only because it binds the operand the actor wrote; a contract
that bound the *answer* against the asked scale would be of the first kind. **That is a design choice
about the contract, not a limitation of the trace** — and the audit's own evidence supports it: the three
DISCHARGED resolutions in §4.4 come from condition-B (thinking-off) episode traces, and the four
well-bound findings come from a campaign trace recorded with thinking on, through the broken client.

The §6 claim that "1,484 to 2,022 of the data cells have no bound column header, and the applicability
condition needs one" is **withdrawn in both halves**: the count is now 1,107–2,022 after B-3.2, and the
condition does **not** need a bound column — it needs the absence of a nearer override, which the
observed header row settles in all 29 cases.

---

## 5. The sample-size table, reviewed separately

The packet's table is arithmetically right and under-qualified. It is reproduced with its assumptions
named:

> Δ_min(n) = (z₀.₉₇₅ + z₀.₈₀) · σ · √(2/n) = 2.802 · σ · √(2/n), with σ = 1.753 of 24.

| draws per arm | published Δ_min | with σ at its 95% lower bound (1.16) | with σ at its upper bound (3.57) |
|---|---|---|---|
| 1 | 6.9 | 4.6 | 14.1 |
| 2 | 4.9 | 3.3 | 10.0 |
| 4 | 3.5 | 2.3 | 7.1 |
| 8 | 2.5 | 1.6 | 5.0 |

**Assumptions, each of which can move the answer:**

1. **An approximation, not a limit.** This is the normal two-sample formula for a count out of 24 at 80%
   power and a two-sided 5% test. "Undetectable below 6.9" means "a design with one draw per arm has less
   than 80% power against a true difference of 6.9 questions", not that a smaller difference cannot
   appear or cannot be real.
2. **σ is itself estimated from 8 draws.** Its 95% interval is [1.16, 3.57], so every threshold carries a
   band of roughly ×0.66 to ×2.0 — the right-hand columns above. The single most useful thing a future
   measurement could do is narrow σ.
3. **It is an UNPAIRED calculation, and the design is paired.** Both arms are scored on the same 24
   questions, and 14 of those 24 never change outcome across draws (5 always correct, 9 never). A paired
   analysis is therefore strictly more sensitive than this table implies — but its noise is the sd of the
   *difference*, which cannot be estimated from repeats of one document. Estimating it needs repeats of
   **two** documents.
4. **Repeated evaluations of one fixed document are not independent training runs.** These eight draws
   hold the candidate fixed and measure scoring noise only. A training run also varies which candidate the
   optimizer proposes, which is a further and probably larger source of variation; the campaign's 21 gate
   observations are evidence about that, not these eight. Treating draws as runs would understate the
   pipeline's variance.
5. **It is about this actor on these 24 validation questions.** The held-out split has 60 questions where
   one question is 1.7 points; nothing here transfers to it.

`OFFICEQA_STATE.md`'s rounded version ("sd ≈ 1.5 … below about 6 … 3 … 2") understates the measured sd and
is replaced by the table above.

---

## 6. Tests, fingerprints, costs

**Tests: 2,326 passed, 9 skipped, 368 subtests, exit 0** — job 66356411, commit `ba53007`, from the
pinned bundle on the cluster. Five are new (the B-3 regressions on the real witnesses). The local
workstation runs Python 3.9, where `tests/test_officeqa_answer_recovery.py` cannot even be collected
(`dataclass(slots=True)`), so the authoritative run is the cluster's 3.11.17.

**Semantic fingerprints.**

| | |
|---|---|
| frozen semantic bundle, repaired semantics | **`CC_BUNDLE_v4`, bundle sha256 `1c165508ed8d7aae…`** |
| hypothesis (unchanged from B-1) | `96c3d1bd9aad6f4d`, `scale.binds@v1`, frozen at epoch 2 |
| vocabulary | `58b4d330139032e5`, 11 predicates, 5 evaluable |
| learner prompt | `41c1069535862b69` |
| learning manifest | learning 4, checking 4, records `4e34cfc16c8ebd7f`, all reused development material |
| configuration recorded in v4 | commit `ba53007`, python 3.11.17, slurm job 66356410, assumptions `["flat_header_authoritative"]` |
| what v3 reports as changed | `code_sha256.oqa_cc_check.py`, `code_sha256.oqa_cc_extract.py`, `function_sha256.check_attempt`, `function_sha256.classify_number`, `function_sha256.data_cells` — exactly the three repairs |
| S0 measurement identity | system prompt `b41c9dbeec8d43c5`, settings fingerprint `d58cf3b59bbc2928`, one of each across all 8 passes |
| S0 document | 887 B `9d377d1c2906…`; stripped of its trailing newline 886 B `b2f8624c8da8…` |

**The v4 bundle records job 66356410 and commit `ba53007`** because it was frozen in that job; the rerun
(66356437, commit `3974433`) found every semantic component identical and kept the file, recording the
difference as provenance-only. That is the behaviour fixed in `4572c61` and it is working.

**Cost.**

| resource | this audit | authorized for it | cumulative after it |
|---|---|---|---|
| EmpirioLabs attempts | **0** | 0 | 11,756 of 30,024 |
| EmpirioLabs charge | **$0.00** | $0 | $7.8344 of $17.00 |
| DeepSeek calls | **0** | 0 | 106 of 266 |
| CPU requested job-hours | **0.87** (0:20 + 0:12 + 0:20) | 2 | **39.37 of 44 — 89%** |
| GPUs | **0** | 0 | 0 |

Jobs: **66356410** audit (FAILED, exit 1 — a `NameError` in the audit tool's own interpretation step,
after every other step had written its artifact), **66356411** tests (COMPLETED), **66356437** audit
(COMPLETED, exit 0, 7 s) after the one-line fix. Two jobs at once at most; 2 CPUs each.

**CPU is the binding constraint on the whole authorization**, now at 89% of 44 requested job-hours. The
held-out re-measurement this project has been weighing needs about 1.5 of the 4.63 that remain.

---

## 7. The proposals, measured and not applied

**P-1 — resolve the conversion from the operand's own message as well as from later steps.** This actor
writes the operand, the factor and the answer in one final message; on UID0014 that message is step 45
of 45, so the strictly-later window is empty and the contract reports "no later step states a factor".
Measured effect: the four UID0014 findings move from `no_factor_stated`/OUTSTANDING to
`incorrect_factor_stated`/**VIOLATED**, wrong by 10^3. **Why it is not applied:** it changes what
"committed before the later steps are read" means. The prediction itself would not change — the binding
and the required factor are fixed by the table, the cell and the question before any factor text is read
— but the separation between prediction and observation would sit inside one message instead of between
steps, and that is a weaker guarantee that should be labelled (`same_message` vs `later_steps`) and
accepted deliberately.

**P-2 — let an observed header row witness the absence of a nearer scale.** When no cell of a table's
observed header row names any money scale, no column of that table can state a nearer money scale, so
`nearer_scale_between` is ABSENT by an inference from observed evidence rather than from an assumed index
alignment. Measured effect: 29 cells across these records move from UNKNOWN to decidable; 8 of them have
no other blocking clause and would reach a binding. **Why it is not applied:** it is a change to what the
checker decides, needs its own amendment and re-freeze, and the instruction is explicit that conditions
must not be weakened to produce findings. It is also not obviously a weakening — it is a stronger
evaluator for the same condition — which is exactly why it should be your call and not mine.

Neither proposal involves a new learned hypothesis, a learner call, an optimizer run or any change to
actor or learner information.

---

## 8. Limitations

1. **The command that produced the published 179-row table was not saved.** Its arithmetic is reproduced
   exactly, which identifies the source; the exact invocation cannot be recovered.
2. **The campaign's gate baseline has no call log**, so its transmitted payload is evidenced indirectly
   (§3). The rendered prompts are direct evidence and they are byte-identical.
3. **All connected-contract records are reused development material.** The four checking records were
   reserved before the B-1 check, but they have been checked three times now (B-1, B-2, this audit), and
   the twelve episode traces did not exist when any bundle was frozen. **Which bundle was frozen before
   which records were inspected:** v2 before the B-2 check of the reserved records; v3 before the
   descriptive check of the episodes was *run*, but after those episodes had been collected and after
   their traces had been read for amendment A-2's reporting; **v4 after this audit's diagnosis, which is
   what motivated the repairs it freezes.** The v4 re-check is therefore a *re-measurement under repaired
   software*, not a fresh prediction, and the comparison verdict (`repeats_what_was_available`) is the one
   number in it that does not depend on that ordering.
4. **The four findings' row identity is not verified.** The operand is matched to a cell by value, and
   two of UID0014's four land on rows 1930 and 1931 while the actor's own text discusses 1942. The scale
   requirement is table-wide, so the 10^3 conclusion is unaffected; nothing here is evidence about which
   series or period the actor meant.
5. **One record, one error.** The four new findings all come from UID0014. The sample is four reserved
   records of which two contain no monetary operand at all.
6. **Constructed tests demonstrate software behaviour only.** B-3.3's regression is synthetic because no
   audited record has a step after its operand message.
7. **The audit changed no measurement.** The 192 rows, the campaign, the gate and the scorer are
   untouched. The corrected figures in §2 are recomputations of the same rows.

---

## 9. What I would do next, in order

1. **Decide P-1** (and, separately, P-2). Both are free, offline and deterministic. P-1 is the one that
   changes a conclusion.
2. **Correct the record**: the five withdrawn claims in §2.3, the deployed-commit claim in §3, and the
   §6 "silent by construction" claim in §4.8. Done in this file; `OFFICEQA_STATE.md` and the logs now
   point at it.
3. **Then, and only on your word, the held-out re-measurement** — the two distinct documents of the
   primary comparison on the 60 held-out questions through the corrected client, ~1,240 attempts, ~$0.79,
   ~1.5 requested job-hours of the 4.63 left. It is still the only number that was ever the primary
   result, and nothing in this audit changes that.
4. **Not another campaign**, and not a contract experiment, until §5's σ is narrowed by a design that
   repeats **two** documents rather than one.
