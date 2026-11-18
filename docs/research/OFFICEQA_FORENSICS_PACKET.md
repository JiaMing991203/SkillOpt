# OfficeQA forensics: why attempts ended without an answer, and why the contract reports said so little

Prepared 2026-11-17 for review. Rule **OQA-FOR-1**. Everything is read from the frozen campaign.
**Zero new actor or optimizer calls, zero GPUs:** every API key is dropped before the job starts Python.
Cost of this phase: 0 attempts, $0.00, 0.13 CPU core-hours over 3 Slurm jobs. The campaign directory was
not modified except for new files under `<campaign>/diagnostic/`. The held-out logs are excluded by path,
twice, and the exclusion is reported in the output.

Tool `research/officeqa/oqa_forensics.py` (`noanswer`, `contracts`), job **66338091**, commit `4cf50fc`,
bundle `oqa_for_20261117`. Outputs `<campaign>/diagnostic/FOR_NOANSWER.json`, `FOR_CONTRACTS.json`.
Tests **2,223 passed, 9 skipped, 368 subtests** (job 66338092).

```text
════════════════════════════════════════════════════════════════════════════════════════════════════
0. THE SHORT VERSION
════════════════════════════════════════════════════════════════════════════════════════════════════

ON THE MISSING ANSWERS (verified)

(1) A SOURCE NOBODY HAD OPENED. The campaign body set TARGET_OPENAI_COMPATIBLE_CALL_LOG, so
    `<run>/actor_calls.jsonl` holds the RAW provider reply for every attempt in 16 of the campaign's
    jobs: 7,909 records with finish_reason, content, reasoning_content and usage. Its record count and
    completion-token total match the spend ledger's for the same jobs EXACTLY (7,909 records,
    8,503,592 completion tokens), so the two independent records corroborate each other.

(2) THE DOMINANT CAUSE IS A REPLY WITH NO CONTENT AT ALL, AFTER THE MODEL GENERATED PLENTY.
    Of the 528 saved selection observations, 331 (62.7%) ended because one reply came back with
    finish_reason "stop", no `content`, no `reasoning_content` -- and a median of 920 completion tokens
    (min 96, mean 1,261, max 4,076). NOT ONE of the 331 had zero completion tokens. The text was
    generated and billed and never arrived in any field the backend reads.

(3) "PROSE BUT NO ANSWER" DOES NOT HAPPEN, AND EXTRACTION MISSED NOTHING. Among terminal replies the
    count of "content present, no `<answer>`, no tool call" is ZERO. Running the extractor's own
    fallback over the 7 replies the loop never handed it (all truncations) and scoring them with the
    real scorer: 0 of 7 would have scored. The actor either emits `<answer>` or emits nothing.

(4) THE UNSUPPORTED `answer` TOOL IS NOT THE CAUSE OF MOST MISSING ANSWERS. 54 of the 528 episodes
    contain at least one call to a tool that does not exist (109 calls). The loop treats it as any other
    tool call and continues, so 8 of those 54 still produced an answer (3 correct). 46 did not, against
    a base rate of 384 of 528, so the association is real and modest -- not a cause of the 331.

(5) THREE FIELDS CANNOT SUPPORT ANY CONCLUSION, AND ONE EARLIER FIGURE OF MINE WAS WRONG BECAUSE OF IT.
    `last_finish_reason` is the empty string in ALL 528 saved rows -- `rollout.py` never assigns
    `last_response_metadata` in the local-tools branch, and `compat_message_from_chat_message` drops the
    finish reason and the reasoning channel before the loop could record them. My earlier packet reported
    "completion truncation: 0" from that field. The raw log shows 12 truncations at the 8,000-token cap
    across the campaign, 7 of them inside the 528.

ON THE CONTRACT REPORTS (verified)

(6) THE REPRESENTATION CANNOT EXPRESS THE DOMINANT FAILURE. The learner's whole expressible space is 20
    single-atom `effect` contracts (3 verbs x the per-verb template set x two polarities), every atom a
    0-ary property of ONE tool observation's text, and an `effect` contract takes no condition. Nothing
    about the actor's own output -- whether it answered, what period or unit it used, what arithmetic it
    did -- is expressible at all. It learned 5 of the 20 and retired 1.

(7) ACTION ONLY'S "ZERO DISTINCT DIAGNOSES" IS A HARD-CODED CONSTANT, NOT A MEASUREMENT.
    `oqa_reports.py` writes `len(REPORT.group(chain["ledger"])) if condition != LC_ACTION else 0`. The
    field is the literal 0 for every Action-Only report whatever it contains. My earlier packet reported
    it as a finding about arm B. It says nothing about arm B.

(8) IN SIX EVOLVE ROUNDS THE CHECKER FOUND ONE COUNTEREXAMPLE, AND IT WAS ABOUT ITSELF. Arm B epoch 3,
    item UID0019, step 10: the observation refuted `read.effect.2` ("a successful read returns: not
    col_leaf_sound"), which was retired. That is the contract model correcting its own error, not a
    diagnosis of the agent, and no skill edit follows from it. Arm C never found a counterexample to an
    action contract in any epoch.

(9) NOTHING WAS LOST BETWEEN THE RENDERER AND THE OPTIMIZER. All 24 of arm C's epoch-3 reports appear
    BYTE-FOR-BYTE inside an optimizer request recorded in that job's own `optimizer_calls.jsonl`.

(10) THE IN-SAMPLE WARNING IS A FALSE ALARM ON 72 OF EACH ARM'S 96 REPORTS. The renderer fires it when
     the item's ID is in the model's `evidence_items`, which is a list of ITEM IDS -- and the training
     question set is identical in all four epochs. Epoch N's reports do use the model frozen at the end
     of epoch N-1, as intended. Checked by trace digest instead: epoch 1 is genuinely in-sample (24 of
     24 in both arms); epochs 2-4 are fresh (0 of 24 identical in every epoch pair in arm B, and in arm
     C except one case, which is a digest COLLISION caused by the record dropping the model's output).
```

```text
════════════════════════════════════════════════════════════════════════════════════════════════════
1. WHY ATTEMPTS ENDED WITHOUT AN ANSWER
════════════════════════════════════════════════════════════════════════════════════════════════════

1a. THE TWO LAYERS OF RECORD, AND WHAT EACH ONE CAN SAY  (verified by reading both)

  THE RAW PROVIDER REPLY -- `<bundle>/run_<job>/actor_calls.jsonl`, written by
  `skillopt/model/openai_compatible_backend.py::_log_call` when TARGET_OPENAI_COMPATIBLE_CALL_LOG is set,
  which `research/cluster/oqa_campaign_body.sh` does. One line per attempt:
      request:  model, max_tokens, temperature, timeout, tools (a bool), the full messages
      response: id, model, system_fingerprint, finish_reason, content, reasoning_content, usage
  It has NO `tool_calls` field, so which tools a reply asked for is recovered from the saved conversation.

  WHAT SKILLOPT SAVED -- `results.jsonl` and `predictions/<id>/conversation.json`. The conversation keeps
  the assistant's CONTENT and each tool call's `cmd` and `obs`. It does not keep the finish reason, the
  token counts, or anything the provider returned outside `content`.

  WHERE THE INFORMATION IS LOST, exactly:
    * `skillopt/model/common.py::compat_message_from_chat_message` builds the message the loop sees from
      `content` and `tool_calls` ONLY. finish_reason, reasoning_content and usage stop there.
    * `skillopt/envs/officeqa/rollout.py` sets `last_response_metadata` in the custom-search, the
      azure-search and the no-local-tools branches. The local-tools branch this campaign used never
      assigns it, so `last_finish_reason` in the row is `""` for all 528 observations (verified: the
      field's value set across all 528 rows is exactly {""}).
    * `_message_debug_metadata(message)` reads `message.metadata`, and the compat message's `metadata`
      is always the empty dict, so even calling it in the local-tools branch would record nothing until
      the conversion carries the fields.

1b. THE LOOP'S CONTROL FLOW -- FIVE WAYS OUT, AND THE RECORD NAMES THREE
    (`skillopt/envs/officeqa/rollout.py`, the local-tools branch, quoted)

      for turn in range(1, max_tool_turns + 1):
          message, _ = chat_target_messages(..., tools=_TOOL_SCHEMAS, tool_choice="auto",
                                            return_message=True)
          response = message.content or ""                 # (A) a lost reasoning channel is already gone
          ...
          if getattr(message, "tool_calls", None):
              for tool_call in message.tool_calls:
                  cmd, obs = run_tool(tool_name, arguments, ...)   # (B) an UNKNOWN tool is just a tool
                  ...
              continue                                     # (C) an <answer> in THIS turn is never read
          if "<answer>" in response.lower():
              final_answer = _extract_answer(response); break
          if turn == max_tool_turns:
              fail_reason = f"Exceeded tool-turn budget ({max_tool_turns})"
          else:
              fail_reason = "Model neither produced a tool request nor a final answer"; break

    (A) `message.content or ""` is the ONLY channel consulted. A reply whose content is null is
        indistinguishable from one that never existed.
    (B) `run_tool` returns "[tool error: unknown tool answer]" and the loop CONTINUES. An unsupported
        tool call costs a turn; it does not end the episode.
    (C) the tool-call branch `continue`s BEFORE the `<answer>` check, so a turn that does both has its
        answer discarded. Measured: this never happened in the 528 (0 turns with a tag and a tool call).
    (D) one turn with no tool call and no literal `<answer>` ends the episode AT ONCE, with the same
        fail_reason whatever the content was. No retry, no nudge, no second chance.
    (E) an episode still calling tools on the last allowed turn falls out of the `for` with NO
        fail_reason, and the record then fills the field with the SCORER's message. That exit is
        invisible in the saved reason.

1c. AGGREGATE COUNTS over the 528 saved selection observations (S0 + the 21 candidates x 24)

    category                              n     what it means
    ------------------------------------  ---   -------------------------------------------------------
    empty_reply_after_generating          331   finish_reason "stop", content null, no tool call,
                                                completion_tokens > 0. Exit (D).
    answered_wrong                        107   an answer was committed and the scorer rejected it
    answered_correct                       37   exact match
    turn_budget_exhausted_unrecorded       27   24 tool turns used, still calling tools. Exit (E).
    silent_turn_raw_unavailable            16   exit (D), but S0's job wrote no raw log
    completion_truncated_at_cap             7   finish_reason "length" at 8,000 tokens. Exit (D).
    turn_budget_exhausted_recorded          3   reached turn 24 with no tool call. Exit (D) at the cap.
    prose_without_answer_tag                0   content present, no tag, no tool call -- NEVER OCCURRED
    prose_with_tag_but_unmatched            0   a tag the regex could not close -- never occurred
    empty_reply_no_tokens                   0   a truly empty generation -- never occurred
                                          ---
                                          528

    384 of 528 produced no answer at all. Of those, 331 (86%) are one empty reply; 30 exhausted the turn
    budget; 7 were cut off at the token cap; 16 are S0's, unclassifiable for want of a raw log.

    per evaluation (columns: correct / wrong / empty reply / truncated / budget-unrecorded / budget-recorded)
      S0.baseline  4 / 2 /  - / - / 2 / -   (+16 silent, raw unavailable)
      A.1.step     1 / 6 / 16 / 0 / 1 / 0      B.1.step  3 / 4 / 14 / 0 / 3 / 0
      A.2.step     2 / 5 / 15 / 1 / 0 / 1      B.2.step  5 / 6 / 13 / 0 / 0 / 0
      A.2.slow     2 / 6 / 12 / 2 / 2 / 0      B.2.slow  1 / 6 / 16 / 0 / 1 / 0
      A.3.step     1 / 4 / 17 / 0 / 2 / 0      B.3.step  1 / 7 / 14 / 0 / 2 / 0
      A.3.slow     1 / 2 / 19 / 1 / 0 / 1      B.3.slow  1 / 4 / 18 / 0 / 1 / 0
      A.4.step     1 / 6 / 14 / 0 / 3 / 0      B.4.step  0 / 3 / 19 / 1 / 1 / 0
      A.4.slow     0 / 3 / 18 / 2 / 1 / 0      B.4.slow  1 / 5 / 17 / 0 / 1 / 0
      C.1.step     2 / 2 / 17 / 0 / 2 / 1      C.3.step  2 / 9 / 12 / 0 / 1 / 0
      C.2.step     2 / 6 / 16 / 0 / 0 / 0      C.3.slow  4 / 8 / 11 / 0 / 1 / 0
      C.2.slow     1 / 3 / 19 / 0 / 1 / 0      C.4.step  0 / 3 / 20 / 0 / 1 / 0
                                               C.4.slow  2 / 7 / 14 / 0 / 1 / 0

1d. THE RAW PROVIDER PICTURE -- all 7,909 logged attempts, across training and selection alike

    finish_reason  content   calls   completion tokens min/median/p90/max   provider cost_usd
    tool_calls     null       7,056       16 /   358 /  4,182 / 8,000             2.9177
    stop           null         533       96 /   896 /  2,880 / 4,076             0.2004
    stop           text         221       15 / 4,359 /  5,144 / 7,710             0.1128
    tool_calls     text          87       89 /   241 /  4,887 / 6,288             0.0337
    length         text          12    8,000 / 8,000 /  8,000 / 8,000             0.0122
    TOTAL                     7,909                                               3.2768

    `reasoning_content` is null in ALL 7,909 records, so the "the answer went into the reasoning channel
    and the backend dropped it" hypothesis is REFUTED for this provider. The tokens went somewhere the
    provider does not expose at all: `completion_tokens_details` is null in every usage record too.

    Of the 320 replies that did carry text, 221 contained `<answer>` and 99 did not; those 99 are exactly
    the 87 tool-calling turns plus the 12 truncations. Every single `stop`+text reply had an `<answer>`.

    A discriminator worth noting: when the model speaks it uses MORE tokens (median 4,359) than when it
    returns nothing (median 896). The empty replies are not hitting a budget; they stop early.

1e. WHAT THE EMPTY REPLIES COST  (the provider's own accounting, in the log)

    533 replies with finish_reason "stop" and no content: 672,104 completion tokens generated and billed,
    $0.2004 by the provider's `cost_usd`, which is 6.1% of the $3.2768 it charged across the 16 logged
    jobs.

    A cross-check that fell out of this: the ledger holds exactly 7,909 target usage records and exactly
    8,503,592 completion tokens for the same 16 jobs -- an exact match with the raw log -- and charges
    $5.3159 where the provider charged $3.2768. The difference is cache reads: the provider bills
    `billable_prompt_tokens` (= prompt_tokens - cached_tokens) and our price table bills all of them. The
    ledger is therefore CONSERVATIVE by about 38% on the actor side. That is a safe direction for a cap
    and is NOT a correction to the authorization, but it is worth reconciling deliberately rather than
    discovering later.

1f. SHORT EXAMPLES  (selection rule, stated and uniform: for each category, the first observation in
    alphabetical evaluation order, then by item id -- plus the two named cases below)

  (i) THE MODAL CASE -- arm B epoch 2 (the one accepted candidate) on UID0170, a question S0 answered:
        saved row  hard=0  predicted=''  n_turns=4  last_finish_reason=''
                   fail_reason='Model neither produced a tool request nor a final answer'
        raw        call 1: msgs=2  finish=tool_calls  content=null   completion_tokens=771
                   call 2: msgs=4  finish=stop        content=null   completion_tokens=1502
        The second reply generated 1,502 tokens and returned none of them. S0, on the same question,
        answered "[0.611, 1.635]" correctly. The skill arm B was running says, verbatim, "never end a
        turn with neither a tool call nor an `<answer>`".

  (ii) WHAT SUCCESS LOOKS LIKE -- arm C epoch-3 slow candidate on UID0233, correct with no tool use:
        saved row  hard=1  predicted='0.0013'  n_turns=2  fail_reason=''
        raw        one call: msgs=2  finish=stop  completion_tokens=810
                   content='\n\nI need to find the Total Interest-Bearing Public Marketabl...'
        When content comes back, the actor answers. The difference between (i) and (ii) is not more
        reasoning; it is whether anything was returned.

  (iii) A TRUNCATION RECORDED AS A SILENT TURN -- arm A epoch 2 on UID0161:
        saved row  n_turns=43  last_finish_reason=''
                   fail_reason='Model neither produced a tool request nor a final answer'
        raw        21 calls; the last is finish=length, content present, completion_tokens=8000
                   (the preceding calls include six at 4,187-4,192 tokens, all content=null)
        The row's reason says "neither produced a tool request nor a final answer". The provider says it
        ran out of room mid-sentence. These are different problems with different fixes.

  (iv) AN EPISODE THAT SPENT ITS WHOLE BUDGET -- arm A epoch 3 on UID0041, 12 calls to `answer`:
        saved row  n_turns=49  fail_reason="predicted '' but the scorer marked it incorrect"
        raw        24 calls, every one finish=tool_calls; two carried text, one of which begins
                   "I notice I've been stuck in a loop trying to figure out the ..."
        The episode used all 24 tool turns and fell out of the loop, so the only reason on record is the
        SCORER's. Exit (E): the record never says it ran out.

1g. UNSUPPORTED TOOL CALLS -- episodes, not calls  (verified)

    The actor has exactly three tools: `glob`, `read`, `grep`. Over the whole campaign it issued 10,814
    tool calls, of which 221 named something else and the harness refused:
        211 x `answer`      7 x `calculate`      3 x `tool`      obs "[tool error: unknown tool X]"
    Inside the 528 selection observations: 109 such calls in 54 distinct episodes. What happened next:

        outcome of the episode                     episodes
        empty_reply_after_generating                   40
        answered_wrong                                  5
        turn_budget_exhausted_unrecorded                5
        answered_correct                                3
        turn_budget_exhausted_recorded                  1
                                                      ---
                                                       54      (8 of them, 15%, still answered)

    So 46 of 54 ended with no answer, against a base rate of 384 of 528 (73%). An unsupported tool call
    is associated with a worse outcome and costs a turn, and it is not what caused the 331 empty replies.
    Examples, with the turn the first one happened at and the turns that followed it:
        A.1.step UID0161   2 calls, first at turn 18, 5 turns after -> budget exhausted (unrecorded)
        A.3.step UID0041  12 calls, first at turn  8, 15 turns after -> budget exhausted (unrecorded)
        A.2.slow UID0220   2 calls, first at turn  1, 2 turns after -> answered (wrong)
        A.2.step UID0233   1 call,  first at turn  1, 1 turn  after -> empty reply

1h. DID EXTRACTION MISS AN ANSWER THAT WAS THERE?  NO -- measured, not assumed

    `_extract_answer` is only ever CALLED when `"<answer>" in response.lower()`; its fallback (the last
    non-empty line) therefore never runs on a reply without the tag. The experiment: take every terminal
    reply with text and no usable tag -- the 7 truncations -- run the extractor's own fallback over it,
    and score the result with `skillopt/envs/officeqa/evaluator.evaluate`.
        0 of 7 would have scored (exact match), and all 7 have f1 = 0.000.
    What the fallback would have produced, in full:
        A.2.step UID0161  gt 0.0003  -> 'Mean of x = (8.14 + 8.22'
        A.2.slow UID0027  gt 3069    -> '| Nov. | 4.28 | 6.11 |'
        A.2.slow UID0161  gt 0.0003  -> '= 2.1003 + 0.6504 + 0.7712 + 0.1243 + 0.0203 + 0.2648 + ...'
        A.3.slow UID0027  gt 3069    -> 'Given the uncertainty, let me proceed with the assumption'
        A.4.slow UID0027  gt 3069    -> '- Jul'
        A.4.slow UID0240  gt 0.9999  -> 'y = ln(P2/P1)'
        B.4.step UID0240  gt 0.9999  -> 'Actually, I think the question is asking for the duration as ...'
    These are mid-sentence fragments of a cut-off working. The answer was not there to be missed.
    (The quoted fallback is compared against `rollout._extract_answer` at run time and the run FAILS if
    the quote has drifted; the comparison passed.)

1i. WHAT THE RECORDS CANNOT SETTLE

  * WHY the provider returns a reply with no content. Nothing in the log distinguishes a model that
    emitted an end-of-turn token with nothing before it from a server-side template that consumed the
    output. `reasoning_content` is null and `completion_tokens_details` is null in all 7,909 records, so
    the provider exposes no breakdown. PLAUSIBLE, and consistent with everything above: the served
    chat template runs a thinking phase whose tokens are billed but not returned, and the visible turn
    comes back empty. AGAINST that reading: the empty replies use FEWER tokens (median 896) than the
    speaking ones (median 4,359), which is not what exhausting a thinking budget would look like.
    Settling it needs one paid request, which is proposed in section 3 and was not made.
  * S0's 24 observations. Job 66272737 -- the shared epoch-1 rollout and the S0 selection baseline --
    wrote no `actor_calls.jsonl`, so for 16 of S0's 18 no-answer observations the reason cannot be
    separated into an empty reply, prose or a truncation. S0's figures in the baseline comparison stand;
    their MECHANISM is unverifiable from what was kept.
  * Which tools a reply asked for, from the raw side. The log's response record has no `tool_calls`
    field, so tool names come from the saved conversation. That is exact for what ran, but it means the
    raw log alone cannot tell an unsupported tool call from a supported one.
```

```text
════════════════════════════════════════════════════════════════════════════════════════════════════
2. WHY THE CONTRACT REPORTS PRODUCED SO LITTLE CORRECTIVE FEEDBACK
════════════════════════════════════════════════════════════════════════════════════════════════════

2a. WHAT THE LEARNER COULD EXPRESS, AND WHAT IT LEARNED  (verified, enumerated from the definitions)

  The vocabulary is frozen in `research/officeqa/oqa_lc_vocab.py` (`oqa.lc.vocab.v1`, digest
  6cc11aaaf016): 3 verbs (`glob`, `read`, `grep`), 11 atoms, 5 named assumptions. EVERY ATOM IS 0-ARY
  AND IS EVALUATED ON ONE TOOL OBSERVATION'S TEXT. The only contract kind the proposer builds is
  `effect` -- a claim about what a successful call returns -- and in this vocabulary an `effect` contract
  TAKES NO CONDITION.

  The template set (`oqa_lc_learn.TEMPLATES`) restricts which atoms may be hypothesised per verb:
      grep: pattern_in_every_line, cap_reached, width_uniform, sub_row_aligned, col_leaf_sound,
            col_path_sound                                                             -> 6 atoms
      read: width_uniform, sub_row_aligned, col_leaf_sound, col_path_sound             -> 4 atoms
      glob: (none -- a glob observation is a file list)                                -> 0 atoms
  Excluded on purpose, with the reason recorded in the module: has_table, header_flat, header_split,
  scale_stated (they describe where the actor chose to look, not the documents) and row_period_bound
  (both polarities are refutable on this corpus; the true claim needs a guard, which `effect` cannot
  carry).

  SO THE WHOLE EXPRESSIBLE SPACE IS 10 ATOM-SLOTS x 2 POLARITIES = 20 CONTRACTS. The campaign learned 5:
      grep.effect.1@v1   a successful grep(...) returns: pattern_in_every_line      113 obs / 21 items
      grep.effect.2@v1   a successful grep(...) returns: sub_row_aligned              6 obs /  4 items
      read.effect.1@v1   a successful read(...) returns: sub_row_aligned             22 obs /  8 items
      read.effect.2@v1   a successful read(...) returns: not col_leaf_sound          10 obs /  2 items
      read.effect.3@v1   a successful read(...) returns: not col_path_sound          10 obs /  2 items
  and 15 were never learned (every other verb-atom-polarity combination). `read.effect.2` was later
  retired. All five were proposed in the FIRST round, from the shared epoch-1 rollout; no later round
  added one.

  WHAT IS NOT IN THE SPACE, and this is the heart of the matter: no atom mentions the actor's output.
  There is no atom for "an answer was produced", for a period, a unit, an arithmetic step, or a
  termination. The failure that accounts for 384 of 528 observations is not expressible as an action
  contract at all, in either arm.

2b. HOW PROPOSALS ORIGINATED, WHAT SUPPORTED THEM, AND WHAT REPAIRS HAPPENED  (verified)

  Origin: `oqa_lc_learn.mech_induce` -- a DETERMINISTIC enumeration, no model call and no network. For
  every (verb, atom, polarity) in the template set it scores the candidate against the evidence and
  proposes it when the gate would accept AND the support spans at least MIN_ITEMS = 2 distinct items. The
  module records why that is enough here: "the OfficeQA vocabulary is small enough that the comparator
  enumerates it exhaustively", and whether a paid learner would propose anything more is left open.

  Evidence recorded per contract: supporting steps, supporting items, counterexamples, and the steps the
  records cannot settle, with up to five witnesses as `UID<item>/e<epoch>@<step>`.

  Repairs available: `mech_repair` (revise, else RETIRE a contract with a counterexample it cannot
  attribute to an assumption) and `assumption_recheck` (drop an adopted assumption the evidence now
  contradicts). Over SIX evolve rounds:

      arm epoch  model changed  counterexamples  active after  what happened
      B   2      no             0                5             nothing
      C   2      no             0                5             nothing
      B   3      YES            1                4             read.effect.2 RETIRED
      C   3      no             0                5             nothing
      B   4      no             0                4             nothing
      C   4      no             0                5             nothing
  The three assumptions in force (flat_header_authoritative, grep_cap_not_reached,
  table_scale_applies_to_rows) were never changed and never contradicted.

2c. WHY ACTION ONLY PRODUCED "ZERO DISTINCT DIAGNOSES" DESPITE A VIOLATED FORECAST  (verified)

  Because the field is a constant. `research/officeqa/oqa_reports.py`, in the record built by
  `build_one_lc`:

      "n_distinct_diagnoses": len(REPORT.group(chain["ledger"])) if condition != LC_ACTION else 0,

  `REPORT.group` groups STAGE obligations, and an Action-Only report must contain no stage material
  (`audit_action_only` raises `StageLeak` if it does), so the field is written as the literal 0 for all
  96 of arm B's reports whatever they say. The violated forecast IS in arm B's report, in the text and in
  `action_forecasts.violated`. "Arm B produced zero distinct diagnoses" is a schema artifact. I reported
  it as a finding in the previous packet; it is not one.

  What IS true about arm B's yield, from the fields that do measure something: 561 confirmed forecasts,
  1 violated, 2,114 the records cannot settle, over 96 reports -- and the report itself says a
  confirmation is not a successful prediction.

2d. HOW THE STAGE SYSTEM TREATED A MISSING ANSWER, AN OUTSTANDING REQUIREMENT, AND AN UNKNOWN FACT
    (verified; counts are GROUP LINES parsed out of the 96 rendered arm-C reports)

  A missing final answer has its own obligation. `oqa_lc_chain.obligations`, requirement 5:

      ans = chain["answer_value"]
      if not (chain["answer"] or "").strip():
          out.append(_ob("answer_traced", "the answer", "an answer must be produced", OUTSTANDING,
                         "the attempt ended with no <answer> block", ["TASK"]))

  so it is OUTSTANDING -- one step less serious than VIOLATED -- with the reason "the attempt ended with
  no <answer> block" and basis TASK. No witness, because there is nothing to witness.

      obligation kind    DISCHARGED  OUTSTANDING  UNKNOWN  VIOLATED
      answer_traced          10          85           1        0
      arith_recorded          6          30          60        0
      cell_bound              7           -          13        0
      period_match            4           -          11        0
      unit_match              4           2           4        2

  Read that table and the shape of the feedback is plain:
    * 85 of 96 arm-C reports say the attempt produced no answer. That is a correct restatement of the
      fact and carries no mechanism: the stage layer cannot say the reply was empty, because the saved
      trace does not record it either (section 1a).
    * UNKNOWN dominates every kind that could have been informative -- 60 of 96 for arithmetic, 13 for
      cell binding, 11 for periods. The code is deliberate about this: when an operand has no matching
      cell and the message it appears in also does arithmetic, "the honest state here is UNKNOWN, not
      OUTSTANDING", because the value may be an intermediate the record does not spell out.
    * VIOLATED appears twice in 96 reports, both `unit_match`, both in ONE report (section 2g).

2e. WAS ANY FINDING OMITTED, WEAKENED OR LOST IN RENDERING OR IN SUPPLY?  (verified)

  SUPPLY: nothing. `skillopt/gradient/reflect.py` appends the report text verbatim under its heading into
  the analyst prompt header. Checked against the optimizer's own request log rather than the code alone:
  all 24 of arm C's epoch-3 reports appear BYTE-FOR-BYTE inside a request in
  `run_66323202_arm_C_e3/optimizer_calls.jsonl`. Analyst prompts in that job ran 11,403 to 666,811
  characters; 4 of the 9 logged requests carry a contract report, under the heading "Action Contract and
  Stage Report" (arm B's is "Action Contract Report", which is what keeps the arms distinguishable).

  RENDERING: compression with an explicit count, not loss.
    * `oqa_lc_report.render` prints EVERY group; there is no cap on groups.
    * `group()` merges records whose (kind, state, reason-with-numbers-replaced-by-#) agree, so N
      records of one kind and reason become ONE line reading "the same for N value(s)".
    * the per-group list of subjects is capped at _MAX_LISTED = 12, then "and N more".
    * `n_distinct_diagnoses` counts GROUPS, so it understates volume by design: UID0014's 11 violated
      obligations are 2 group lines.
  The one real loss is metadata, not text: the hard-coded 0 of section 2c.

2f. WORKED EXAMPLE 1 -- THE SINGLE VIOLATED ACTION FORECAST
    arm B, epoch 3, UID0019. Report 338b6a2d7b6f, model 4174e893c3fb v2 (arm B's epoch-2 model).
    Record: action_forecasts {confirmed 2, violated 1, unresolved 8}, obligations null,
    n_distinct_diagnoses 0 (the constant).

    The report says, in full:

        - **VIOLATED** col_leaf_sound: forecast false, observed true
            at step(s) 10, tool read
            basis: RULE(read.effect.2@v1), OBS

    and closes with

        ## How the model did on this attempt
        - forecasts: 2 confirmed, 1 violated, 8 the records cannot settle.
        - condition-based predictions that the records could determine: 0 of 5 tool call(s). Every atom
          in this vocabulary is a property of the observation a call has not returned yet, so a condition
          cannot be valued before the call; this number is expected to be low and is not a defect.

    At the epoch-3 evolve this became a repair, recorded in the model:

        {"decision": "accepted", "op": "retire", "target": "read.effect.2", "source": "mech-repair",
         "reason": "counterexample(s) ['UID0019/e3@10'] with no assumption to attribute them to"}

    INTERPRETATION. The content of the campaign's one refutation is "a read observation turned out to
    have soundly-bound column leaves, contrary to a rule induced from two items in epoch 1". It is a
    statement about the CHECKER'S OWN MODEL being wrong. No edit to a skill follows from it, and none was
    made: 0 of 101 proposed-edit entries in the campaign name any contract vocabulary.

2g. WORKED EXAMPLE 2 -- THE 11 VIOLATED STAGE OBLIGATIONS
    arm C, epoch 3, UID0014. Report 8b90446cecdc, model bae1e21c2ee3 v2 (arm C's epoch-2 model).
    Record: forecasts {confirmed 14, violated 0, unresolved 43},
    obligations {DISCHARGED 32, OUTSTANDING 16, UNKNOWN 43, VIOLATED 11}, n_distinct_diagnoses 14.

    The two VIOLATED group lines, verbatim:

        - **VIOLATED** [unit_match] the value used must be in the asked scale (billions)
                                                             -- the same for 10 value(s)
            the operand is the cell value 2,410,259 (thousands) scaled by 0.001, but thousands to
            billions requires 1e-06; the value is wrong by 1,000x
            witness: 2,410,259 in row '1930', period 1930, column 'Income and profit taxes > Total'
                     [FLAT], scale thousands
            basis: TASK, OBS, ACTOR
            values: operand 2410.259 (step 45), operand 1056.757 (step 45), ... (10 listed)

        - **VIOLATED** [unit_match] the value used must be in the asked scale (billions)
            the operand is the cell value 3,471,122r (thousands) scaled by 0.001, but thousands to
            billions requires 1e-06; the value is wrong by 1,000x
            witness: 3,471,122r in row '1941', period 1941, column 'Income and profit taxes > Total'

    plus, in the same report:

        - **OUTSTANDING** [unit_match] ... -- the same for 13 value(s)
            the operand is the raw figure from a table that states 'thousands', the question asks for
            'billions', and no conversion of this value to billions is recorded in any message
            (divide by 1,000,000)

    This is the best feedback the contract layer produced anywhere in the campaign: exact, witnessed,
    and actionable. VERIFIED that it reached the optimizer byte-for-byte (section 2e). What the optimizer
    did with it, from the artifacts: arm C epoch 3's second ranked edit is "## Derived Quantities,
    Windows, and Scale ... for 'difference', subtract the two converted values", support count 3.

    BUT -- and this is why it does not separate the arms -- the same diagnosis was available WITHOUT any
    contract, and arm A produced it. Scanning each arm-epoch's MERGED edits for scale/unit vocabulary:
        A.1 1/7   A.2 4/7   A.3 3/8   A.4 3/5      B.1 1/5   B.2 3/8   B.3 0/5   B.4 0/6
        C.1 3/4   C.2 3/4   C.3 3/6   C.4 1/6
    Arm A, which has no contract layer at all, wrote unit-and-scale edits in every epoch, including an
    A.2 edit carrying the full set of thousand/million/billion/convert. A 1,000x unit error is visible in
    the trace, and the ordinary analyst reads the trace.

2h. FAILURES THE ANALYST DIAGNOSED AND THE CONTRACT REPORT DID NOT -- AND WHETHER THEY WERE EXPRESSIBLE

  All three arms' epoch-3 analysts independently named the same dominant failure, 13 or 14 items each:
      arm A  "No final <answer> emitted; agents stalled after retrieving evidence or after repeated
              no-match searches."                                            13 items, incl. UID0019
      arm B  "Agent ended the turn with neither a tool call nor an <answer> after looping on the same
              reads/greps or invoking an unknown 'answer' tool."              14 items, incl. UID0019
      arm C  "Agent stalled with no tool call and no <answer> block even when the needed rows were
              already read or present in the oracle page."                    14 items, incl. UID0014

  Three concrete cases, with the expressibility verdict:

   (i) THE EMPTY REPLY (331 of 528). Arm B's analyst named it and even named the unknown-`answer`-tool
       mechanism -- which it read off the trace, where the observation "[tool error: unknown tool answer]"
       sits in plain text. The ACTION report cannot express it: no atom mentions the actor's output.
       NOT EXPRESSIBLE in arm B's representation. In arm C it IS expressible, as `answer_traced`
       OUTSTANDING, and 85 of 96 reports say exactly that -- a restatement without a mechanism, because
       the trace does not record that the reply was empty either.

   (ii) WRONG PERIOD / WRONG YEAR-BLOCK (8 items in arm B's epoch-2 diagnosis alone: UID0228, UID0014,
        UID0150, UID0034, UID0031, UID0030, UID0165, UID0026). NOT EXPRESSIBLE as an action contract --
        `row_period_bound` is deliberately excluded from the template set because the claim that is true
        needs a guard ("in a table whose first column header names a period...") and an `effect` contract
        takes no condition. EXPRESSIBLE in the stage layer as `period_match`, which resolved 4 DISCHARGED
        and 11 UNKNOWN over 96 reports and VIOLATED nothing.

   (iii) A UNIT ERROR. EXPRESSIBLE in the stage layer and expressed exactly once, well (section 2g). NOT
         EXPRESSIBLE as an action contract: `scale_stated` is in the vocabulary but excluded from the
         template set, and in any case it describes the observation, not the actor's arithmetic.

2i. THE MODEL TIMELINE, AND THE IN-SAMPLE WARNING  (verified)

  Every frozen model, in order, with the corpus it was learned from:

    frozen (UTC)          file                      sha           ver  `epoch` field  corpus epochs
    2026-11-17T08:59:30   model_M0.json             8d8ecceecd0b  v2   1              [1]
    2026-11-17T11:48:25   arm_B/..._epoch02.json    4174e893c3fb  v2   1              [1, 2]
    2026-11-17T12:58:12   arm_C/..._epoch02.json    bae1e21c2ee3  v2   1              [1, 2]
    2026-11-17T15:26:47   arm_B/..._epoch03.json    8424f45c45e5  v4   3              [1, 2, 3]
    2026-11-17T16:15:53   arm_C/..._epoch03.json    5176dac5ad00  v2   1              [1, 2, 3]
    2026-11-17T17:47:07   arm_B/..._epoch04.json    dd708dc96846  v4   3              [1, 2, 3, 4]
    2026-11-17T23:00:05   arm_C/..._epoch04.json    4153b65695e9  v2   1              [1, 2, 3, 4]

  A BOOKKEEPING DEFECT FOUND ON THE WAY: the record's `epoch` field is the epoch of the last
  model-CHANGING decision, not the epoch the model was frozen at -- every arm-C model says 1 because
  nothing in them ever changed. `parent_sha256` is null in all seven, so the chain of models is only
  reconstructible from MODEL_EVOLUTION.jsonl (which also holds three duplicate rounds, from the recovery
  re-running B.4, C.3 and C.4). Taking the corpus from that field, as the first version of this audit
  did, shrinks it to epoch 1 and can only understate in-sample contamination.

  WHICH MODEL CHECKED WHICH ATTEMPTS, from each report's own `model_sha256`:

    arm  epoch  reports  model cited    version  in-sample by ID  in-sample by TRACE  fresh
    B    1      24       8d8ecceecd0b   v2       24               24                  0
    B    2      24       8d8ecceecd0b   v2       24                0                 24
    B    3      24       4174e893c3fb   v2       24                0                 24
    B    4      24       8424f45c45e5   v4       24                0                 24
    C    1      24       8d8ecceecd0b   v2       24               24                  0
    C    2      24       8d8ecceecd0b   v2       24                0                 24
    C    3      24       bae1e21c2ee3   v2       24                1                 23
    C    4      24       5176dac5ad00   v2       24                0                 24

  THE INTENDED SCHEDULE WAS FOLLOWED. Epoch N's reports cite the model frozen at the END of epoch N-1
  (epoch 2 cites M0; epoch 3 cites the epoch-2 model; epoch 4 cites the epoch-3 model, which for arm B is
  the v4 one), and each model's freeze time matches the end of that epoch's job -- M0 at 08:59:30 UTC
  against the shared job's 03:59 EST finish, arm B's epoch-2 model at 11:48:25 UTC against
  skill_v0002.md written at 06:47 EST. So within an epoch's job the order is: rollout -> render the
  reports against the PRIOR model -> analyst, merge, rank, gate, slow update, meta skill -> evolve and
  freeze the next model.

  SO WHY DOES EVERY REPORT CARRY THE IN-SAMPLE WARNING? Because of what the renderer tests
  (`oqa_lc_report.render_action`):

      if str(replay.get("item")) in set(model.get("evidence_items") or []):
          ... "NOTE: this item's own trace is part of the evidence this model was learned from"

  `evidence_items` is a list of ITEM IDS, 24 of them in every model, and the campaign drew the SAME 24
  training question ids in all four epochs (one id-set digest, batch seeds 1043/2043/3043/4043). So the
  test is true for every item in every epoch, and the note printed on 96 of 96 reports in each arm.

  CHECKED PROPERLY, by trace digest and then model-independently, for each arm, how many of epoch N's
  saved traces are byte-identical to an earlier epoch's trace of the same item:

      arm B   e1 vs e2: 0/24   e1 vs e3: 0/24   e1 vs e4: 0/24
              e2 vs e3: 0/24   e2 vs e4: 0/24   e3 vs e4: 0/24
      arm C   e1 vs e2: 0/24   e1 vs e3: 1/24 (UID0019)   e1 vs e4: 0/24
              e2 vs e3: 0/24   e2 vs e4: 0/24   e3 vs e4: 0/24

  VERDICT: the warning is accurate on the 24 epoch-1 reports of each arm, where M0 was learned on exactly
  the traces it then reported on. It is a FALSE ALARM on the other 72 in each arm, caused by reused
  question IDs. The forecasts in epochs 2-4 are genuine out-of-sample predictions, and the campaign has
  been understating them.

  AND THE ONE EXCEPTION IS NEITHER. Arm C's epoch-3 trace for UID0019 is byte-identical to the shared
  epoch-1 trace, so the digest test calls it in-sample. It is not the same attempt. Both saved files hold
  three assistant messages whose content is EMPTY and two tool calls with identical arguments:

      shared_e1  entries=6 messages=3 tool_calls=2 nonempty_contents=0
      armC_e3    entries=6 messages=3 tool_calls=2 nonempty_contents=0
          cmd: grep(pattern='03/16/2016', path='.../treasury_bulletins_parsed/...')
          cmd: read(path='.../treasury_bulletins_parsed/transformed/tre...')
      (arm C's epoch-2 attempt on the same item has 10 entries, so it differs)

  Two different provider interactions -- each generating ~1,000 billed tokens that were never returned --
  produce ONE identical record, because the record keeps only the empty content strings and the tool
  commands. This is section 1's finding arriving in section 2: a digest over a lossy record cannot
  establish that two attempts were the same attempt.
```

```text
════════════════════════════════════════════════════════════════════════════════════════════════════
3. THE SMALLEST CHANGE AND TEST THAT WOULD RESOLVE EACH REMAINING UNCERTAINTY
════════════════════════════════════════════════════════════════════════════════════════════════════

Each item is the smallest thing that settles its question, with what it costs and what it would change.
None of this has been run. The first three cost nothing and change no experimental semantics.

U1. WHY DOES THE PROVIDER RETURN A REPLY WITH NO CONTENT?  (the 331, 86% of the missing answers)
    SMALLEST CHANGE: three lines in `skillopt/model/common.py` -- carry `finish_reason`, the raw usage
    and any unrecognised message fields into `CompatAssistantMessage.metadata`, which already exists and
    is already read by `rollout._message_debug_metadata`; and one line in the local-tools branch of
    `rollout.py` to assign `last_response_metadata = _message_debug_metadata(message)` as the other three
    branches do.
    SMALLEST TEST, FREE: a unit test that a stub message carrying finish_reason "length" and a usage dict
    reaches the saved row's `last_finish_reason` and a new `last_completion_tokens`. No API call.
    SMALLEST TEST, PAID, AND NOT YET AUTHORIZED: re-send ONE recorded prompt that produced an empty
    reply, with the raw HTTP response body dumped verbatim rather than through the SDK object, to see
    whether the provider returns a field the client discards. 1 attempt, ~$0.0007, 1 minute. Worth asking
    for, because the answer decides whether this is a client bug or a server behaviour.

U2. IS THE 8,000-TOKEN CAP COSTING ANSWERS?  (12 truncations campaign-wide, 7 in the 528; and 1,019 of
    the 7,909 replies used 4,100-4,200 completion tokens, a suspiciously tight band)
    SMALLEST CHANGE: none to the pipeline. Record the number, which U1 already does.
    SMALLEST TEST, FREE: tabulate completion_tokens per turn against the turn index from the call log --
    already loaded by `oqa_forensics noanswer` -- and report what share of episodes spend more than half
    their budget on a single turn. Zero cost, and it would say whether the cap or the turn budget binds
    first.

U3. IS THE EMPTY TURN RECOVERABLE WITHIN THE EXISTING SEMANTICS?
    The loop ends the episode on the FIRST turn with no tool call and no tag (exit D), with no retry.
    SMALLEST CHANGE: on that exit, when `turn < max_tool_turns`, append one tool-result-shaped message
    saying no output was received and asking for either a tool call or `<answer>`, and continue -- and
    count how often it is used. This CHANGES THE EXPERIMENT'S SEMANTICS and must not be mixed into a
    frozen comparison; it needs explicit approval and a fresh arm.
    SMALLEST TEST, FREE: a unit test with a stubbed backend returning empty content once and then an
    `<answer>`, asserting the episode now answers and that the nudge is recorded. The paid version is one
    24-question evaluation at 248 attempts and $0.16, which only makes sense alongside the noise-floor
    measurement already proposed in the previous packet.

U4. ARM B'S REPORT YIELD, WHICH IS CURRENTLY UNMEASURED
    SMALLEST CHANGE: one line in `oqa_reports.py` -- replace the `else 0` with a count defined for the
    Action-Only condition, e.g. the number of distinct (contract, atom, polarity, verdict) rows with a
    verdict of VIOLATED or UNRESOLVED-but-determinable. It is metadata only; it does not touch the report
    text the optimizer reads, so it does not change any arm's behaviour.
    SMALLEST TEST, FREE: a unit test that an Action-Only record whose report contains one violated
    forecast reports a non-zero count, and that the report text is unchanged. Then re-derive the counts
    over the 96 saved arm-B reports from the files, which costs nothing.

U5. THE IN-SAMPLE WARNING, WHICH IS WRONG ON 144 OF THE 192 REPORTS
    SMALLEST CHANGE: store `evidence_traces` -- the trace digests `oqa_reports.trace_digest` already
    computes -- beside `evidence_items` in the frozen model, and have `render_action` test the trace
    digest, falling back to the id test only when no digests are stored (so old models still render).
    SMALLEST TEST, FREE: a unit test that a model carrying the digest of attempt X warns on X and not on
    a different attempt of the same item id. Plus the regression this audit already provides: the
    cross-epoch identity matrix must stay at 0.

U6. THE MODEL RECORD'S OWN BOOKKEEPING
    SMALLEST CHANGE: set `parent_sha256` when a model is frozen from a prior one, and record
    `frozen_at_epoch` beside the existing `epoch` (which is the last change). Both are one assignment in
    `oqa_lc_ir.freeze`.
    SMALLEST TEST, FREE: a unit test that freezing a model from a parent records the parent's digest and
    the freeze epoch, and that `epoch` keeps its current meaning.

U7. THE LEDGER IS CONSERVATIVE AGAINST THE PROVIDER'S OWN FIGURE ($5.3159 vs $3.2768 over 16 jobs)
    SMALLEST CHANGE: record the provider's `cost_usd` in the ledger's usage record beside our computed
    figure. One field; the cap still enforces our own conservative number.
    SMALLEST TEST, FREE: a unit test that a usage payload carrying `cost_usd` has it preserved in the
    record and that the enforced charge does NOT change. Then re-read the authorization's section 3 with
    both figures side by side.

NOT PROPOSED, and why: anything that touches the held-out split beyond its own procedure; relaxing the
exact-match scorer; accepting a gate candidate on a tie; GPUs; and any change to the frozen campaign's
artifacts (this audit only added files under <campaign>/diagnostic/).
```

**What I did not do**

* No actor or optimizer call was made. The job drops every API key before Python starts, and the held-out
  call logs are excluded by path with the exclusion reported in the output.
* No GPU was requested. CPU for this phase: 0.13 core-hours over 3 jobs.
* The frozen campaign was not modified: the only writes are `<campaign>/diagnostic/FOR_*.json`.
* The held-out evaluation stayed under its own release procedure and no held-out score was read into
  this work.
* Two statements in my previous packet are corrected here, in §0(5) and §0(7). Both were readings of
  fields that do not mean what I took them to mean.
