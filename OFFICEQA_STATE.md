# OfficeQA: state note (resume from here)

**Last update: 2026-11-16 21:14 EST (from `date`).** Branch `officeqa-contracts`, worktree `SkillOpt-officeqa`.

**Nothing is running or queued. 0 GPUs held.** Both arms and the held-out evaluation are COMPLETE.
**The report-cache defect is FIXED (`c353b99`) and the follow-up audit is done**: see
`docs/research/OFFICEQA_EPOCH2_REPORT_AUDIT.md`. Correcting the eight stale reports changes the diagnosis for
5 of 8 items but NOT the advice, so **no replay was run**; its exact cost is scoped in section 6 of that
document should the user want it anyway.
**A further offline probe (0 GPU, 0 paid calls) answers the research question directly** ->
`docs/research/OFFICEQA_REPORT_VALUE_PROBE.md`: paired blind review of the same eight attempts, with and without
the corrected report. **0 of 8 cases gained a valid diagnosis attributable to the report; 1 of 8 gained a
misleading one.** See section C5.
**A contract/check audit (offline) says what to change to make the report accurate** ->
`docs/research/OFFICEQA_CONTRACT_AUDIT.md`. Rule set is FIXED and hand-written; no learner, **no Lean anywhere**.
See section C6. Implementation changes deliberately NOT applied.
**Still with the USER:** whether to restructure as one epoch of two steps so the optimizer sees its rejected
edits (section C3), and whether to apply the C6 changes 1-4. **The held-out outcome of job 66234927 remains
deliberately UNREAD/SEALED.**

**This session owns** the OfficeQA track only. Write only `from: "officeqa"` records in
`/Users/yaojiaming/dev/lean_skillopt/coordination/reservations.jsonl`. Standing authorization since
2026-11-15 ~23:00 replaced per-job ACKs; a peer's ACK coordinates capacity, never authorizes work.

---

## A0. NEXT TASK (user, 2026-11-16 21:14): import design from the ALFWorld branch

The user will give the instruction after this compaction. **Do not start importing before they do.** What the
next session needs to know:

**Where the sources are** (sibling worktrees of the same repo, both at `4a9914a` on their own branches):
| Path | Branch | Holds |
|---|---|---|
| `~/dev/lean_skillopt/SkillOpt-learned-contract` | `contract/learned-contract` | the learned-contract line: `docs/LEARNED_CONTRACT_DESIGN.md` + amendments **A1-A4**, `LEARNED_CONTRACT_PILOT_REPORT.md`, `LEARNED_CONTRACT_INFO_STUDY_REPORT.md`, and its own `docs/research/STATE.md` |
| `~/dev/lean_skillopt/SkillOpt` | `research/alfworld-baseline` | the ALFWorld baseline line |
| `~/dev/lean_skillopt/SkillOpt-officeqa` | `officeqa-contracts` | **this** worktree |

**What was already imported** (2026-11-15), into `research/officeqa/transferred/` (tracked, reference only - NOT
imported by the report path): `fu_budget.py`, `ir.py`, `lc_llm.py`, `plain_v4.py`, `score_v4.py`,
`stage_contracts.py`. Provenance is in `OFFICEQA_TRANSFER_MANIFEST.json` (untracked).

**PROVENANCE GAP to fix on the next import:** that manifest records `source_branch` and `created` but its
`source_commit` and per-file `source` paths are **null**. Do not invent them retroactively. Any new transfer must
record the source branch, the exact source commit, the source path and a digest per file, and
`research/officeqa/transfer.py` is the script that does it - check it writes those fields before relying on it.

**What an import should be aimed at.** This session's audits found the report's problem is NOT the rules'
content but the bookkeeping around them (section C6): the checker collects the fact that discharges an obligation
(`state["computed"]`, a row's month) and never consults it when deciding whether to complain. So when reading the
ALFWorld design, the questions worth answering are:
1. Does the learned-contract design have an **obligation-resolution** notion (discharged / outstanding / unknown)
   that OfficeQA's `oqa_check.py` lacks? That is defect #1 and #5 in section C6.
2. How does it keep **assumption bookkeeping** (`score_v4.py`, `plain_v4.py` are already transferred and carry the
   P-vs-C scoring and the containment invariant)?
3. Is the **learner** (`lc_llm.py`, `ir.py`) worth wiring in at all? OfficeQA's rule set is currently fixed and
   hand-written, and section C6 shows 8 of 14 rules never fire while 2 produce most of the false positives.
4. `fu_budget.py` - the follow-up budget notion, relevant because the OfficeQA actor's dominant failure is
   non-termination (5 of 7 epoch-1 failures produced no `<answer>`).

**Hard constraints that carry over:** keep any import additive and opt-in; never derive contracts from the skill;
arm A must stay byte-identical to upstream; no secrets or cluster paths in tracked files; the fork is public.

## A. The authorized task

The two-epoch comparison **Arm A = Original SkillOpt** vs **Arm B = + stage and action feedback**, inside
**8 additional A40-hours** and **24 DeepSeek calls / $1**. Surface any integrity failure or budget overrun
*before* proceeding past it. Full design, including everything the user specified, is in
**`docs/OFFICEQA_COMPARISON_DESIGN.md`** — read that, not a paraphrase.

Final deliverable: one copy-pasteable report with frozen set counts, the prompt audit, per-epoch gate results,
held-out scores, examples of report findings that became skill edits, and actual time and spend.

## B. DONE: the shared rollout and all three pre-paid-call gates (job 66209754)

COMPLETED 2026-11-16 11:10:47, elapsed 00:21:23 after a 48-minute queue wait. Charged **0.36 A40-h (~$0.039)**,
zero paid calls. Local copy: `~/dev/lean_skillopt/runs/20261116-104926_officeqa_shared_66209754` (3.1 MB).
Cluster run dir (= `OQA_SHARED_DIR`):
`/nfs/turbo/umms-csriram/yaojm/lean_skillopt/runs/20261116-104926_officeqa_shared_66209754`.

| | Result |
|---|---|
| training rollout (12, starting skill, 24 turns) | **5/12 hard = 0.4167**, 128 tool calls, 0 tool errors |
| validation baseline (12, starting skill) | **5/12 hard = 0.4167** -> the gate needs **>= 6/12** to accept |
| first-3 gate | PASS (2/3 correct, 17 tool calls, 0 errors) |
| all-12 gate | PASS, answer-key isolation 12/12 |
| prompt audit | PASS both arms; arm A 472,711 chars / ~131,308 est tokens, arm B 488,838 / ~135,788; **arm B == arm A + reports only** |
| grep-cap warning | **0x in arm A, 4x in arm B**; the capped grep output it is drawn from appears **4x in BOTH** |

Rollout outcomes (none fatal, per the user's rule): 5 correct; 2 wrong answers (UID0034 `1987` vs `August 1986`,
UID0079 `11.45` vs `11.73%`); 3 reached the 24-turn cap with no answer (UID0031, UID0026, UID0028); 2 requested no
tool and produced no answer (UID0014, UID0030).

Arm B's reports over the 7 failures flag **10 distinct problems** in 5 categories: `grep_cap_reached` 4,
`operand_provenance_missing` 3, `ungrounded_answer_value` 1, `asked_period_absent` 1, `scale_conversion_missing` 1.

**CORRECTION recorded in the design doc section 1a.** An earlier claim that arm B's grep-cap finding "is a P-layer
finding both arms receive" was wrong for THIS comparison: P was a control *report* in the earlier learned-contract
studies, whereas here **arm A receives no report at all**, so arm B's delta is the whole report. A win for arm B
would show that a deterministic report helps, NOT that stages and action contracts specifically help.

## C. RESULT: both arms retained the starting skill

| | arm A (job 66224637) | arm B (job 66228142) |
|---|---|---|
| epoch-1 candidate on validation | 0.3333 (4/12) -> **REJECT** | 0.4167 (5/12, tie) -> **REJECT** |
| epoch-2 candidate on validation | 0.4167 (5/12, tie) -> **REJECT** | 0.2500 (3/12) -> **REJECT** |
| final skill | starting skill, byte-identical `9d377d1c2906...` | the same |
| optimizer attempts | 8 (4 analyst, 2 merge, 2 ranking), all ok | 8, all ok |
| cost | $0.0721 | $0.0815 |
| elapsed | 00:32:57 | 00:32:27 |

The native gate needs STRICT improvement over 0.4167, so a tie is a reject. Baseline 0.4167 throughout.

**Why arm A's edits were rejected (verified, and independent of the arm comparison):** its epoch-1 edits were
well-targeted - "never end a turn without an `<answer>`", and "a paper coded 8703 is NOT thereby published in
1987", which is exactly UID0034's error. The edit did what it was designed to do: empty answers on validation fell
from 5 to 2. But under `hard` (exact match) a blank and a wrong answer score the same, so converting one into the
other gains nothing, and the added text flipped UID0070 and UID0091 from correct to wrong. Net -1.

Held-out: job **66234927** COMPLETED (19:27). Because all three final skills are the same bytes, that ONE run is
all three evaluations the user asked for. Jobs 66234928 / 66234929 were cancelled as exact duplicates, both
charged 0. **Its outcome has not been read.**

## C2. INTEGRITY FAILURE: arm B's epoch-2 reports were stale (recorded in the ledger)

**All 8** of arm B's epoch-2 failures received stage reports describing their **epoch-1** traces; epoch 2
generated nothing. The hook caches at `<report dir>/<id>.md` and reuses that file whenever it exists, so the key
is the ITEM ID, not the (item, trace) pair. Evidence: 7 of 8 report files are byte-identical to the shared
epoch-1 reports with mtime 16:14:08 (the `cp` at job start); UID0017's was written at 16:14:12 during epoch 1's
*success* minibatch; and for 5 of 8 the tool-call count the report states matches the epoch-1 trace and differs
from the epoch-2 one (UID0026: report says 24, epoch-2 trace made 1). The stale reports did reach DeepSeek - arm
B's epoch-2 failure prompt embedded 8 report blocks.

My earlier claim of "5 reports generated during epoch 2" was wrong: those 5 are exactly epoch 1's five successes.

**Consequence:** arm B's epoch 1 is valid (its reports came from the shared epoch-1 rollout, which is the trace in
the prompt). Arm B's **epoch 2 is invalid as a test of the intervention**. Arm A is unaffected throughout.

**Test gap:** `test_a_report_is_generated_when_none_is_cached` asserted determinism by re-rendering from cache,
which locked the stale-cache behaviour in. No test rendered one item against a second, different trace.

**FIXED in `c353b99`.** The cache key now covers the conversation, the user prompt the actor was given, the
question string and the condition, as `<id>.<12-hex>.md`; the arm stage no longer pre-seeds reports (that copy of
plainly-named files is what enabled the reuse); `oqa_optimizer_input_audit.find_report` accepts both namings so
preserved artifacts still read. Two regression tests, including one that renders the same question from two
different traces and asserts each rendered optimizer request carries its own trace's report. Verified on the real
data: pointed at the 8 stale reports, the fixed hook ignores them and regenerates 8 keyed reports matching the
independently-corrected ones byte-for-byte.

**Follow-up audit (offline, 0 GPU, 0 paid calls) -> `docs/research/OFFICEQA_EPOCH2_REPORT_AUDIT.md`:** all 8 sent
reports reproduce byte-identically from the epoch-1 traces, confirming provenance. Correcting them changes the
diagnosis for 5 of 8 items (UID0017, 0026, 0030, 0034, 0079), but the set carries the same five categories in the
same counts (grep_cap 4<->4, operand_provenance 3<->3, ungrounded_answer 1<->1, scale_conversion 1<->1,
asked_period 1<->1), the payload differs by +1.0%, and BOTH applied edits rest on unchanged categories. The saved
optimizer output contains ZERO references to report content, so every report->edit link is plausible only.
**Decision: no replay.**

## C3. Second audit finding: no cross-epoch memory in either arm

Neither arm's epoch-2 prompt contained the rejected epoch-1 edits, their scores, or a meta-skill summary - verified
against the logged prompts in `optimizer_calls.jsonl`, not reconstructed. `step_buffer` is declared INSIDE the
epoch loop (`trainer.py:1187`) so it resets each epoch, and with `steps_per_epoch = 1` it is always empty, so
`_format_step_buffer`'s "Rejected edits (score X -> Y)" block never appears. `active_meta_skill` is "" because
`use_meta_skill` is off by my deviation. So "two epochs" here is two independent attempts, not cumulative learning.

## C5. Does the report surface diagnoses a trace-only review would miss? NO

`docs/research/OFFICEQA_REPORT_VALUE_PROBE.md`. Eight attempts, two views each in separate contexts (trace only vs
trace + corrected report), identical instructions, then one blind adjudicator per case with the two reviews
anonymised in randomised order.

- The two views agree on the **primary failure in 7 of 8** cases.
- The blind adjudicator judged the report-guided review **more precise in 7 of 8** and its instruction better in
  5 of 8 - **but the attribution test kills that credit**: of the 12 issues it credited only to the report-guided
  reviews, **not one appears in the corresponding report**. The praised specifics (`30,442`, the lost year
  headers, column mis-mapping, glob counts) are absent from the reports, which say only `ungrounded_answer_value`,
  `grep_cap_reached` x2 and `asked_period_absent` respectively. With one draw per cell, "the report prompts closer
  reading", "reviewer variance" and "the adjudicator prefers specifics" cannot be separated.
- **Misleading claims: 5 of 8 cases on each side** - the report does not make reviews cleaner.
- **The one report-attributable effect is a false claim.** UID0014's report asserts `scale_conversion_missing` for
  1004461.0 ("No conversion step appears"), but the trace contains `1,004,461 (in millions) = $1,004.461 billion`.
  The report-guided reviewer quoted it as evidence; the blind adjudicator caught it. The finding fires 29x for
  that item and is demonstrably wrong at least once. Signal source: a stage requirement combined with an action
  check - the operand ledger saw the raw figure at step 9 and the stage rule fired before the conversion line.
- In 5 of 8 cases the **trace-only** review caught real issues the report-guided one missed (no stopping rule,
  the Exhibit 3 contradiction, table-of-contents reads, duplicated spans).
- A methodological note: I first "verified" the UID0014 claim by searching for the report's literal `1004461.0`,
  found no conversion, and was wrong - the trace writes `1,004,461`. The adjudicator was right.

**Still not established:** whether DeepSeek, over a real eight-trajectory minibatch under the real analyst prompt,
would propose different EDITS that survive merge, budget and the 6/12 gate. Reviewers answered a different
question with a different output. The replay that would settle it is scoped in
`OFFICEQA_EPOCH2_REPORT_AUDIT.md` section 6 (1 A40, 4 calls, ~25 min, inside the remaining caps).

## C6. Contract/check audit: what to change in the report generator

`docs/research/OFFICEQA_CONTRACT_AUDIT.md`. Offline, 0 GPU, 0 paid calls. Nothing implemented.

**Provenance:** the rule set is **fixed, hand-written Python**. No learner ran, no rule was proposed/retained/
revised, and **no Lean was involved** (the `lean` matches are the `lean_skillopt` path). Of 14 rules, 3 rest on
documented tool behaviour, 1 on a self-labelled unverified empirical hypothesis (`table.scale.1`), the rest are
manually written. **Only 6 of 14 fired** across 8 failures + 3 successes.

**Reliability:** the two rules carrying the report's only distinctive content are its least reliable.
`scale_conversion_missing`: **5 of a 10-case sample are demonstrable false positives**.
`operand_provenance_missing`: fires on arithmetic intermediates, on the actor's own converted values, and on the
answer-format example inside the question text; 1 of 18 sampled firings looked genuinely useful.

**One structural cause:** the rules read a value's EVIDENCE provenance (`_match` over supplied pages + tool
observations) and never `state["computed"]`, so a conversion the actor performed is invisible and the obligation is
reported as outstanding. Verified: UID0014 converted `1,004,461 -> $1,004.461 billion` at **step 1**; the raw value
is recorded as an operand at steps 9/19 and the report says "No conversion step appears".

**UID0026 (wrong-period row, not wrong column):** the actor used the `1961-January` paper-money level (29,385)
instead of `1960-December` (30,442) as the calendar-1961 baseline; the column was CORRECT (header cell 3 = "Total
paper money"), which also settles a disagreement between the two probe reviewers (30,442 right, 32,869 wrong
column). `operand_period_mismatch` could not fire because (a) the question states no period - it must be inferred -
so the obligation is UNKNOWN, and (b) both values were classified `computed`, not operands. The ledger binds only
the YEAR, never the month, so a year-level check would call both rows "1961" and consistent. The requested year is
itself uncertain from the supplied text (the "Treasury notes of 1890" column goes `1` -> `*` = "less than
$500,000", which is a rounding floor, not removal) - recorded as unresolved.

**Prioritised changes (1-4 best supported), each with a fire/stay-quiet example in the doc:** consult
`state["computed"]` before claiming a missing conversion; reword "No conversion step appears" to a claim the
checker can support; exclude numbers occurring in the task prompt's own text from operand extraction; do not report
provenance-missing for arithmetic results. Then: bind month + column; separate "no arithmetic cue" from "no
arithmetic requested"; make period-rule inactivity explicit.

**Skill-edit candidate:** only UID0034's `7069` read out of a FRASER URL generalises usefully - and arm A's
analyst derived that rule from the raw trace with no report at all.

## C4. Earlier work (all verified)

1. **Question sets frozen** (rule OQA-SEL-2, digest `fc02291540ef...`): 12 train / 12 val / 24 held-out, each
   balanced 3-3-3-3 or 6-6-6-6 across easy|hard x single|multi, disjoint, **no shortfall**. Record:
   `docs/research/OFFICEQA_FROZEN_SETS.json`; per-item evidence verdicts:
   `docs/research/OFFICEQA_EVIDENCE_CHECK.json`.
2. **The evidence test is key-free** and excluded exactly one item: **UID0046 -> UID0063** (same easy/single
   stratum). Its pie-chart legend term `service-related` occurs 0 times in the whole 448 KB bulletin and its
   supplied page carries no figure at all. 70 of 72 assessed items AVAILABLE, 1 AVAILABLE_VIA_TOOLS (UID0030).
3. **Corpus**: 68 of 68 needed bulletins present (`--verify-docs` PASS), 50 MB,
   `$PROJECT_ROOT/officeqa_data/treasury_bulletins_parsed`.
4. **Split materialized**: `$PROJECT_ROOT/officeqa_data/split_cmp` loads **train=12 val=12 test=24**; the
   trainer's `valid_seen`/`valid_unseen` resolve to val/ and test/.
5. **24 tool turns restored** plus upstream `max_completion_tokens: 16384` and `workers: 4`.
6. **Checkpoint fixed**: wrong answers, a missing `<answer>` and reaching the turn cap are **outcomes**, not
   fatal. Verified against the preserved job 66196051: the same traces that FAILED before now PASS, and with
   `--evidence` it refuses a run containing an excluded item.
7. **Prompt audit works, previewed on real traces**: arm B = arm A + the report bytes only (TRUE), both arms
   PASS the leakage audit, and the grep-cap warning sentence appears **0x in arm A, 1x in arm B** while the
   capped `grep` output it is derived from appears in **both**. To be re-run on the 24-turn traces as the
   official gate before the first paid call.
8. **70 local tests pass** in the python3.12 venv (see section E).

## D. Spend (final for this comparison unless a re-run is authorized)

| | Used | Cap |
|---|---|---|
| A40-hours (this comparison) | **1.77** (0.36 shared + 0.55 arm A + 0.54 arm B + 0.32 held-out) | 8 |
| A40-hours (all OfficeQA to date) | 3.77 | - |
| DeepSeek calls | **16** (8 per arm), all `ok`, no 429, no 5xx | 24 within $1 |
| DeepSeek USD | **$0.1536** | $1 |

Cancelled without charge: 66219432 (90-min arm A, PENDING 2h03m), 66234928, 66234929.

`deepseek-flash` has a **1M-token** context, so the 131k-token analyst prompt is within limits (checked against
api-docs.deepseek.com, not assumed). Off-peak rates now: $0.15/M cache-miss input, $0.60/M output, so ~$0.021 per
analyst call and ~$0.15-0.25 for the whole comparison. The $1 cap is not the binding constraint; the 24-call cap is.

A step needs 5 or 6 optimizer calls (5 + the ranking call when the merged pool exceeds the budget of 4), so four
steps need 20-24. **That is why the arms run sequentially**: arm A's actual count decides whether arm B fits.
`oqa_optimizer_precheck.py` estimates it from the rollout and refuses to start if the estimate exceeds the cap.

## E. Next actions

**Blocked on the USER's decision** (both raised in the final report):
1. Re-run arm B's epoch 2 with the report-cache fix? One A40, ~8 more calls; the budget covers it
   (8 of 24 calls and 6.2 of 8 A40-h remain).
2. Restructure as one epoch of two steps, so `step_buffer` actually carries the rejected edits forward?

If neither is taken, the comparison is finished and the only remaining act is to read job 66234927's held-out
score (currently unread by instruction) and state it for all three skills at once.

### The exact arm submit command (paths resolved)
```
D=/nfs/turbo/umms-csriram/yaojm/lean_skillopt/officeqa_data
SHARED=/nfs/turbo/umms-csriram/yaojm/lean_skillopt/runs/20261116-104926_officeqa_shared_66209754
ARM=B OQA_SHARED_DIR="$SHARED" OFFICEQA_SPLIT_DIR="$D/split_cmp" \
  OFFICEQA_DOCS_DIR="$D/treasury_bulletins_parsed" OQA_SELECTION_RULE=OQA-SEL-2 \
  OQA_CALL_BUDGET=24 OQA_CALL_STEPS=4 STAGE=arm \
  research/submit.sh officeqa <deployed commit> -- --time=01:30:00
```

## F. Where things are

| | |
|---|---|
| Local HEAD | pushed to `origin/officeqa-contracts`; deployed snapshot `6939c29a72ea` |
| Split / docs / frozen record | `$PROJECT_ROOT/officeqa_data/{split_cmp,treasury_bulletins_parsed,oqa_frozen_sets.json,oqa_evidence.json}` |
| Config (BOTH arms) | `configs/research/officeqa_cmp.yaml` - the only difference between arms is `OFFICEQA_STAGE_REPORT_DIR` |
| Job script | `research/cluster/officeqa.sbatch`, `STAGE=tools|check3|pilot12|epoch1|shared|arm|heldout` |
| This session's audit evidence | `~/dev/lean_skillopt/runs/20261116-160952_officeqa_arm_66228142/audit_evidence/` - 44 files, 208 KB: the 16 paired reviews, the 8 blind adjudications + the de-anonymisation key, the corrected epoch-2 reports, the 3 success reports, the success-selection rule, the old-vs-corrected classification and the rule-firing tally. Copied out of the session scratchpad, which is cleared on reboot |
| Local test venv | `/private/tmp/claude-501/-Users-yaojiaming-dev-lean-skillopt-SkillOpt-officeqa/82390561-79e4-43e9-b247-03526392e651/scratchpad/venv312` (workstation python is 3.9 and cannot import SkillOpt) |
| Tests | `tests/test_officeqa_{contracts,tool_probe,answer_key_isolation,freeze_sets,optimizer_precheck}.py` - 70 pass |

### Settled facts (do not re-derive)
- Tool calling needs `--enable-auto-tool-choice --tool-call-parser qwen3_coder`; `hermes` is wrong for Qwen3.5-4B.
  `--max-model-len 65536` fits one A40 (peak 38.7 of 46.1 GB).
- The actor is handed the gold parsed pages and its tools are restricted to the gold files.
- The answer-key leak is fixed (`c07fefb`) and audited both ways.
- The arm-B hook now **generates** a missing report instead of skipping it; skipping would have turned arm B back
  into arm A from epoch 2 onward, since epoch 2's traces do not exist until the run makes them.
- `use_slow_update` / `use_meta_skill` are off for both arms: at epoch 2 they would add a second skill update per
  epoch, ~24 rollouts and ~2 calls per arm. Symmetric, documented in the config and the design doc.
- **Negative so far:** after the false-positive fix (`f819011`) arm B produced zero advice on the two real
  failures, and its one useful finding is a P-layer finding both arms receive.

## G. Standing rules
Never tune on held-out. Never derive contracts from the skill. Keep the scorer, gate, analyst prompts and
skill-update rules unchanged apart from the approved answer-key fix. No secrets, cluster paths or run dirs in
tracked files. Record every submission in `docs/research/jobs.tsv` and SUBMITTED/SETTLE lines in the coordination
ledger. Timestamps come from `date`.
