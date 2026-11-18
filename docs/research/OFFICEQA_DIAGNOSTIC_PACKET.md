# OfficeQA diagnostic evidence packet — why the proposed skill edits scored badly

Prepared 2026-11-17 for review. Everything here is read from the saved training and validation records of
the 2026-11-17 three-arm campaign. **No paid request was made:** every API key is dropped before the
diagnostic job starts Python, and the whole phase cost 0 attempts, $0.00 and 0.10 CPU core-hours over 13
Slurm jobs. The held-out evaluation kept running throughout and **its numbers are deliberately not read
into this packet** — the proposals in §9 were written before any held-out result was opened, so nothing
here is tuned on it.

Tool: `research/officeqa/oqa_diagnose.py` (rule OQA-DIAG-1), subcommands `gates`, `questions`,
`replicates`, `contracts`, `chains`. Job 66337036, commit `66b2b66`, bundle `oqa_diag_20261117`.
Outputs: `<campaign>/diagnostic/DIAG_{GATES,QUESTIONS,REPLICATES,CONTRACTS,CHAINS}.json`.
Tests: 2,215 passed, 9 skipped, 368 subtests (job 66337037).

```text
════════════════════════════════════════════════════════════════════════════════════════════════════
0. THE SHORT VERSION
════════════════════════════════════════════════════════════════════════════════════════════════════

Four findings, each from the files, each checkable:

(a) THE MEASUREMENT IS WIDER THAN EVERY EFFECT BEING CHASED. One identical skill document was evaluated
    SEVEN times on the same 24 questions during this campaign and scored 1, 1, 2, 2, 3, 5, 6 — sd 1.95
    questions, range 5. The gate accepted arm B's candidate on a margin of ONE question. The spread of a
    FIXED document (1 to 6) covers almost the whole range of the 21 candidates' scores (0 to 5).

(b) THE GATE WAS LARGELY MEASURING WHETHER THE ACTOR TERMINATED, NOT WHETHER THE ADVICE WAS GOOD. Of 504
    candidate observations, 363 produced no answer at all, 3 ran out of tool turns, 105 were wrong
    answers and 33 were correct. A candidate's score correlates with how often it committed an answer
    (Pearson r = 0.78) and not at all with the length of its skill document (r = -0.08).

(c) THE ONE ACCEPTANCE IN THE CAMPAIGN SITS INSIDE THE SCORER'S FORMATTING TOLERANCE. S0 answered UID0159
    "39.31%" against a ground truth of "39.31" and was marked wrong. Count that as right and S0 is 5/24,
    the gate threshold is 5/24, and arm B's 5/24 candidate ties instead of winning — the campaign would
    have had ZERO acceptances. (This is a counterfactual about the scorer, not a claim that exact match
    is the wrong rule. The native scorer was required to stay unchanged and did.)

(d) THE CONTRACT LAYERS CONTRIBUTED NOTHING TRACEABLE. Across 101 proposed-edit entries in 24 merge and
    rank rounds (71 merged edits, 30 ranked), not one names anything from the contract vocabulary. Arm B's 96 action-contract reports contain ZERO
    distinct diagnoses and one violated forecast in 2,676. Arm C's 96 stage reports contain 11 violated
    obligations, all 11 in a single report.

What this packet does NOT do: it does not explain the method's performance. The arms are not separated by
anything here, and the measurement is too wide to attribute the differences that were observed. §9 states
what would resolve each open question and what it would cost.

════════════════════════════════════════════════════════════════════════════════════════════════════
1. ALL 21 CANDIDATE GATES  (VERIFIED — read from the files, every score and decision recomputed)
════════════════════════════════════════════════════════════════════════════════════════════════════

S0 baseline: 4/24 = 0.1667, document sha256 b2f8624c8da8…, 886 bytes.
  `shared/val_baseline/results.jsonl` md5 b16ccc9c… — the three arms' injected copies are BYTE-IDENTICAL
  to it, so "S0 = 4/24" is one measurement, not three.
  S0's own 24 outcomes: 4 correct, 2 wrong answers, 18 no answer (16 silent turns + 2 empty parses),
  0 execution failures, 0 turn-budget hits, 0 truncations.

Candidate documents: the retained `skills/skill_v*.md` hold only ACCEPTED skills, so 20 of the 21
candidates exist only as `steps/step_000N/candidate_skill.md` and `slow_update/epoch_0N/candidate_skill.md`.
Each of those 21 files was proved to be the document the actor ran, by finding it verbatim inside the
`target_system_prompt` of all 24 of its own selection rollouts. All 21 are distinct by digest.

 #  arm ep kind  candidate      bytes  input skill   thr    score  decision          corr wrong no-ans exec  optimizer calls
 1  A   1  step  b03b949cf454    4174  b2f8624c8da8  4/24   1/24   reject               1    6    17     0   analyst 4, merge 2, rank 1
 2  A   2  step  098556c2db91    2179  84fb9da5dc93  4/24   2/24   reject               2    5    16     1   analyst 4, merge 2, rank 1
 3  A   2  slow  26e01c695425    4730  84fb9da5dc93  4/24   2/24   reject               2    6    16     0   slow 1
 4  A   3  step  8347e900a6b1    2437  84fb9da5dc93  4/24   1/24   reject               1    4    19     0   analyst 4, merge 2, rank 1
 5  A   3  slow  abf7ca5ee20e    4044  84fb9da5dc93  4/24   1/24   reject               1    2    20     1   slow 1
 6  A   4  step  b56b30e7c13e    2861  84fb9da5dc93  4/24   1/24   reject               1    6    17     0   analyst 4, merge 2, rank 1   [recovered]
 7  A   4  slow  839c6a1ab661    4436  84fb9da5dc93  4/24   0/24   reject               0    3    21     0   slow 1                       [recovered]
 8  B   1  step  26861706c8da    4652  b2f8624c8da8  4/24   3/24   reject               3    4    17     0   analyst 4, merge 2, rank 1
 9  B   2  step  95989ce5ebad    3603  84fb9da5dc93  4/24   5/24   ACCEPT_NEW_BEST      5    6    13     0   analyst 4, merge 2, rank 1
10  B   2  slow  ef6878e2a495    8139  086659d5d596  5/24   1/24   reject               1    6    17     0   slow 1
11  B   3  step  0499b59ba840    4887  086659d5d596  5/24   1/24   reject               1    7    16     0   analyst 3, merge 1, rank 1
12  B   3  slow  5316d1360073    7178  086659d5d596  5/24   1/24   reject               1    4    19     0   slow 1
13  B   4  step  3c013c4bbfc7    4236  086659d5d596  5/24   0/24   reject               0    3    21     0   analyst 4, merge 2, rank 1   [recovered]
14  B   4  slow  1ad45f22bb9a    7602  086659d5d596  5/24   1/24   reject               1    5    18     0   slow 1                       [recovered]
15  C   1  step  c1d79c371e14    4420  b2f8624c8da8  4/24   2/24   reject               2    2    19     1   analyst 4, merge 2, rank 0
16  C   2  step  349e7ff54088    4324  84fb9da5dc93  4/24   2/24   reject               2    6    16     0   analyst 4, merge 2, rank 1
17  C   2  slow  387ca2517dca    4632  84fb9da5dc93  4/24   1/24   reject               1    3    20     0   slow 1
18  C   3  step  3ab865d3bada    3335  84fb9da5dc93  4/24   2/24   reject               2    9    13     0   analyst 4, merge 2, rank 1   [recovered]
19  C   3  slow  cb1a3f5891a6    4723  84fb9da5dc93  4/24   4/24   reject (TIE)         4    8    12     0   slow 1                       [recovered]
20  C   4  step  43ef66eee797    2295  84fb9da5dc93  4/24   0/24   reject               0    3    21     0   analyst 4, merge 2, rank 1   [recovered]
21  C   4  slow  14e8067fb3c0    4528  84fb9da5dc93  4/24   2/24   reject               2    7    15     0   slow 1                       [recovered]

Input skills: b2f8624c8da8 = S0 (886 B); 84fb9da5dc93 = S0 + the empty slow-update placeholder (939 B);
086659d5d596 = arm B's accepted skill (3,603 B). The input skill is taken from the producing rollout's own
system prompt, not inferred: an ordinary step's candidate is built on the skill its own training rollout
ran under, a slow update's on the skill `rollout_curr` ran under.

THRESHOLDS MOVE. Arm B's acceptance raised its own bar from 4/24 to 5/24 for its last four gates. A table
that prints one threshold for all of an arm's seven gates is wrong about four of arm B's.

INTEGRITY CHECKS — all PASS:
  every gate has a candidate file ............................... 21/21
  every candidate appears verbatim in its own 24 system prompts .. 21/21
  every recorded score recomputes from the rows .................. 21/21
  every recorded decision recomputes from the gate rule .......... 21/21   (strict improvement, cand > current)
  every question-id set matches the S0 baseline's ................ 21/21
  every user prompt is byte-identical to the baseline's .......... 504/504
  every non-skill system scaffold matches the baseline's ......... 21/21
  the input skill is uniform across a producing rollout .......... 21/21
  resolved source paths identical to the baseline's .............. 504/504
  oracle parsed-page sizes identical to the baseline's ........... 504/504
  no two candidates are the same document ....................... 0 duplicates

RECOVERED STAGES COMPLETED THEIR INTENDED OPTIMIZER CALLS. A.4, B.4, C.3 and C.4 each show 4 analyst
calls, 2 merge calls, 1 ranking call and 1 slow-update call, which is the full later-epoch shape. Two
rounds in the whole campaign have fewer, and neither is a recovered one:
  B.3 step  3 analyst / 1 merge — arm B scored 0/24 on epoch 3's training rollout, so there was no
            success minibatch to analyse. One fewer analyst call is the expected consequence.
  C.1 step  0 ranking calls — 4 merged edits against an edit budget of 4, so there was nothing to rank.
All twelve arm-epochs also wrote a meta skill (2,129–2,929 chars) except the three epoch-1 stages, which
record the documented `skip_first_epoch`.

════════════════════════════════════════════════════════════════════════════════════════════════════
2. SETTINGS PARITY, AND THE ONE SETTING THAT MATTERS MOST  (VERIFIED)
════════════════════════════════════════════════════════════════════════════════════════════════════

The three arms' `config.json` differ in ONE key, `out_root`. Digests A ebdaf2170026, B 3ef26047973a,
C 21c281174478; the only differing key between any pair is the output directory.

Shared by every evaluation in the campaign:
  actor            qwen3-5-4b at api.empiriolabs.ai/v1, temperature 0.7, max_tokens 8000
  tools            use_local_tools true, search_mode offline, max_tool_turns 24, max_queries_per_turn 4
  splits           split_sel3, seed 42, split_seed 42 — train 24 / val 24 / test 60, pairwise DISJOINT
                   (verified: train ∩ val = ∅)
  optimizer        deepseek-flash, reasoning_effort medium, edit_budget 4, min_edit_budget 2
  gate             metric hard (exact match), use_gate true, strict improvement
  questions        the same 24 selection ids in every one of the 22 evaluations
  documents        byte-identical user prompts, identical resolved source paths, identical oracle page sizes

SAMPLING SEEDS. **No per-request sampling seed is sent to the actor API.** `seed` and `split_seed` fix
which items are drawn and in what order; they do not fix what the actor says. There is no `seed` key
anywhere in `skillopt/model/openai_compatible_backend.py`, and `target_openai_compatible_temperature` is
0.7, which is forwarded on every request. Re-running the same skill on the same questions therefore does
not reproduce the same answers — and §3 measures how far apart two such runs come out.

The training question set is also identical in every epoch and every arm (24 ids, one id-set digest,
batch seeds 1043 / 2043 / 3043 / 4043 shared across arms), so each epoch reflected on the same questions
under a fresh sample.

TRUNCATION, MALFORMED ANSWERS AND EXECUTION ERRORS, before any score difference is read as a skill
effect (VERIFIED over all 528 observations):
  completion truncation (finish_reason "length") ............ 0
  tool-turn budget exceeded ................................. 3   (A.2 step, A.3 slow, C.1 step, 1 each)
  exceptions ................................................ 0
  bundle manifest / provenance failures ..................... 0
  answers that parsed empty with no execution failure ....... 27
  calls to tools that do not exist .......................... 211  (see §5)
A note on one field: `n_turns` counts conversation MESSAGES, not tool turns — it reaches 55 against a
budget of 24, at roughly 2 messages per tool call. Comparing it to the budget suggests 5 of S0's
questions hit the cap; the true number is 0, and only the three rows above really ran out.

════════════════════════════════════════════════════════════════════════════════════════════════════
3. THE NOISE FLOOR THE CAMPAIGN MEASURED FOR FREE  (VERIFIED — the central finding)
════════════════════════════════════════════════════════════════════════════════════════════════════

Arms A and C rejected every candidate, so their in-force skill never changed. Every epoch therefore
re-ran THE SAME DOCUMENT over THE SAME 24 training questions, and the actor is sampled at temperature 0.7
with no seed. Those runs are independent draws of one skill's score. Grouping every saved rollout by the
skill digest taken from its own system prompt plus the exact question-id set:

  DOCUMENT 84fb9da5dc93 (S0 + placeholder, 939 B), 7 draws over the same 24 training questions
      A.2 train  1/24     B.2 train  2/24     C.2 train  5/24
      A.3 train  1/24                         C.3 train  2/24
      A.4 train  6/24                         C.4 train  3/24
      scores 1, 1, 2, 2, 3, 5, 6    mean 2.86   sd 1.95   min 1   max 6   RANGE 5 QUESTIONS
      0 of 24 questions right in every draw, 16 never right, 8 flipped:
         UID0017 1/7   UID0026 1/7   UID0033 3/7   UID0044 1/7
         UID0063 4/7   UID0065 4/7   UID0151 3/7   UID0163 3/7

  DOCUMENT 086659d5d596 (arm B's accepted skill), 2 draws over the same 24 training questions
      B.3 train 0/24,  B.4 train 3/24      range 3 questions

  Smaller groups on the 10-question slow-update pairs (prev and curr are the same document whenever the
  arm had accepted nothing): 5 draws → 1,1,1,1,1; 4 draws → 1,2,2,2; 4 draws → 1,1,1,1; 2 draws → 1,1;
  2 draws → 0,1. Flips occur inside every one of them.

  Excluded, and why: epoch 1's training rollout was run ONCE and injected into all three arms — the three
  `results.jsonl` files share one md5 — so it is one measurement, not three. Counting it would have
  reported "3 draws, sd 0.00" and implied a deterministic actor. The arms' copies of the S0 baseline are
  excluded for the same reason.

WHAT THIS MEANS FOR THE 21 GATES (INTERPRETATION, but arithmetic on verified numbers):
  * The 21 candidate scores span 0 to 5 of 24. One fixed document re-measured spans 1 to 6 of 24.
  * sd 1.95 questions is 8.1 percentage points. The gate's decision margin is one question, 4.2 points.
  * Two of the seven fixed-document draws (5 and 6) would have cleared a 4/24 threshold outright. A gate
    comparing two single draws at this noise level accepts a candidate of IDENTICAL quality a substantial
    fraction of the time — consistent with the 0.371 figure already recorded in
    `OFFICEQA_GATE_RESOLUTION.md`, now with an empirical spread behind it.
  * The observed sd is close to the independent-binomial prediction for this rate
    (sqrt(24 × 0.119 × 0.881) = 1.59), so the earlier binomial analysis was not using the wrong noise
    model; it was using the right model without a measurement of it.
  CAVEAT: these 7 draws are on the TRAINING split, and the gates ran on the SELECTION split. Same actor,
  same question style, same n = 24, but not the same questions, so the spread transfers only
  approximately. §9's first proposal measures it on the gate's own split.

════════════════════════════════════════════════════════════════════════════════════════════════════
4. WHAT CHANGED ON EACH VALIDATION QUESTION  (VERIFIED)
════════════════════════════════════════════════════════════════════════════════════════════════════

Legend:  OK = exact match   wrng = committed a wrong answer   ---- = no answer produced   EXEC = ran out
of tool turns.  Columns are the 21 candidates in trainer order, grouped by arm.

question    gt              S0        A: 1s 2s 2S 3s 3S 4s 4S   B: 1s 2s 2S 3s 3S 4s 4S   C: 1s 2s 2S 3s 3S 4s 4S
UID0001  2,602            OK          ---- ---- OK   ---- ---- wrng ----   ---- wrng ---- wrng wrng ---- ----   ---- ---- ---- wrng ---- ---- ----
UID0027  3069             OK          wrng wrng ---- wrng ---- ---- ----   ---- wrng wrng ---- ---- ---- wrng   wrng wrng ---- wrng wrng ---- wrng
UID0039  180681           wrng        ---- ---- ---- ---- ---- wrng ----   wrng OK   ---- wrng ---- ---- ----   wrng wrng wrng ---- wrng wrng wrng
UID0041  0.011            ----        wrng wrng wrng ---- ---- ---- ----   ---- wrng wrng wrng wrng wrng wrng   ---- ---- ---- wrng wrng ---- wrng
UID0052  2.23%            ----        wrng ---- wrng ---- ---- wrng wrng   ---- wrng ---- wrng wrng ---- ----   ---- ---- ---- wrng ---- ---- wrng
UID0070  182              ----        ---- OK   ---- ---- ---- OK   ----   ---- ---- ---- ---- ---- ---- ----   ---- ---- ---- ---- ---- ---- ----
UID0072  0.42             ----        ---- ---- ---- ---- ---- ---- ----   ---- ---- ---- ---- ---- ---- ----   ---- ---- ---- ---- ---- ---- ----
UID0086  4.815            ----        ---- ---- ---- ---- ---- ---- ----   ---- ---- ---- OK   ---- ---- ----   ---- ---- ---- ---- OK   ---- ----
UID0091  0.105            ----        ---- ---- ---- wrng ---- ---- ----   ---- OK   ---- ---- ---- ---- ----   ---- ---- ---- OK   ---- ---- ----
UID0109  678077.00        ----        ---- ---- ---- ---- ---- ---- ----   ---- ---- wrng ---- ---- ---- wrng   ---- ---- ---- wrng wrng wrng ----
UID0142  1007599          ----        ---- ---- ---- ---- OK   ---- ----   OK   ---- ---- ---- ---- ---- OK     ---- ---- ---- ---- ---- ---- OK
UID0154  12               ----        wrng wrng wrng ---- ---- ---- wrng   wrng ---- ---- ---- ---- wrng ----   ---- wrng ---- wrng wrng ---- wrng
UID0159  39.31            wrng*       ---- ---- ---- ---- ---- ---- ----   wrng* OK   ---- ---- ---- ---- ----   ---- ---- ---- ---- ---- ---- ----
UID0161  0.0003           ----        ---- ---- ---- wrng EXEC ---- ----   wrng ---- wrng wrng ---- ---- wrng   ---- wrng ---- ---- wrng ---- ----
UID0170  [0.611, 1.635]   OK          ---- wrng ---- ---- ---- ---- ----   ---- ---- ---- ---- ---- ---- ----   ---- ---- ---- wrng ---- ---- ----
UID0190  -11              OK          OK   ---- ---- OK   ---- ---- ----   OK   ---- ---- ---- OK   ---- ----   ---- ---- ---- ---- OK   ---- ----
UID0213  -550.3           ----        ---- EXEC ---- ---- wrng wrng ----   ---- wrng ---- ---- ---- wrng ----   ---- wrng ---- wrng ---- ---- wrng
UID0217  303683           ----        ---- ---- wrng ---- ---- ---- ----   ---- ---- ---- ---- ---- ---- ----   ---- OK   ---- ---- ---- ---- ----
UID0220  27               ----        wrng ---- wrng ---- ---- wrng* wrng*  ---- ---- ---- wrng ---- ---- ----  ---- ---- ---- ---- wrng* ---- ----
UID0233  0.0013           ----        ---- ---- ---- ---- ---- ---- ----   ---- ---- OK   ---- ---- ---- ----   OK   ---- ---- ---- OK   ---- ----
UID0234  501              ----        ---- OK   OK   ---- ---- ---- ----   ---- OK   ---- ---- ---- ---- ----   EXEC OK   OK   OK   ---- ---- ----
UID0235  0.0063           ----        ---- ---- ---- ---- ---- ---- ----   OK   OK   ---- ---- ---- ---- ----   ---- ---- ---- ---- OK   ---- OK
UID0239  0.045            ----        ---- ---- ---- ---- ---- ---- ----   ---- ---- wrng ---- ---- ---- ----   OK   ---- wrng ---- ---- ---- ----
UID0240  0.9999           ----        wrng wrng wrng wrng wrng wrng ----   ---- wrng wrng wrng wrng ---- wrng   ---- wrng wrng wrng wrng wrng wrng
   (* = numerically equal to the ground truth but scored wrong; see §6)

gate                  correct  gained  lost  kept-correct  kept-incorrect
A.1.step                 1       0       3        1             20
A.2.step                 2       2       4        0             18
A.2.slow                 2       1       3        1             19
A.3.step                 1       0       3        1             20
A.3.slow                 1       1       4        0             19
A.4.step                 1       1       4        0             19
A.4.slow                 0       0       4        0             20
B.1.step                 3       2       3        1             18
B.2.step                 5       5       4        0             15
B.2.slow                 1       1       4        0             19
B.3.step                 1       1       4        0             19
B.3.slow                 1       0       3        1             20
B.4.step                 0       0       4        0             20
B.4.slow                 1       1       4        0             19
C.1.step                 2       2       4        0             18
C.2.step                 2       2       4        0             18
C.2.slow                 1       1       4        0             19
C.3.step                 2       2       4        0             18
C.3.slow                 4       3       3        1             17
C.4.step                 0       0       4        0             20
C.4.slow                 2       2       4        0             18
TOTALS                  33      27      78        6            438      (over 21 x 24 = 504)

S0's four correct questions, and how many candidates kept them:
  UID0001 kept by  1 of 21     UID0027 kept by 0 of 21
  UID0170 kept by  0 of 21     UID0190 kept by 5 of 21
There were 84 chances to retain an S0-correct answer and 6 were taken.

Nine of the 24 questions were never answered correctly by ANY of the 22 documents:
  UID0041 UID0052 UID0072 UID0109 UID0154 UID0161 UID0213 UID0220 UID0240
Fifteen were answered correctly at least once. Seven of those fifteen are "right or silent" — the actor
either gets them exactly right or produces nothing, and never commits a wrong answer:
  UID0070 (2/21 correct, 19 silent)   UID0086 (2/21, 19)   UID0142 (4/21, 17)   UID0190 (5/21, 16)
  UID0233 (3/21, 18)                  UID0234 (6/21, 14)   UID0235 (4/21, 17)
Those seven questions supply 26 of the 33 correct candidate answers in the whole campaign (79%).

INTERPRETATION. On this split the gate metric has an effective sample much smaller than 24: nine
questions carry no information at all, and most of the score comes from seven questions on which the only
thing that varies is whether the actor finished.

════════════════════════════════════════════════════════════════════════════════════════════════════
5. WHAT ACTUALLY CHANGED IN THE ACTOR'S BEHAVIOUR  (VERIFIED)
════════════════════════════════════════════════════════════════════════════════════════════════════

Outcome shares, S0 against the 21 candidates pooled:
                            S0 (24)        candidates (504)
  correct                    4  (16.7%)     33  ( 6.5%)
  wrong answer committed     2  ( 8.3%)    105  (20.8%)
  no answer at all          18  (75.0%)    363  (72.0%)
      of which silent turn  16              338
      of which empty parse   2               25
  ran out of tool turns      0                3  ( 0.6%)

  answer committed, of any kind     6/24 = 25.0%      138/504 = 27.4%
  correct GIVEN an answer           4/6               33/138  = 23.9%

So the edits did NOT mainly reduce silence: pooled commitment rose 2.4 points while accuracy given an
answer fell. Per candidate the commitment rate ranges from 12.5% to 50.0%, and it tracks the score:

  candidate   committed   score     candidate   committed   score     candidate   committed   score
  A.4.slow     3/24       0/24      B.4.step     3/24       0/24      C.4.step     3/24       0/24
  A.3.slow     3/24       1/24      B.3.slow     5/24       1/24      C.1.step     4/24       2/24
  A.3.step     5/24       1/24      B.4.slow     6/24       1/24      C.2.slow     4/24       1/24
  A.1.step     7/24       1/24      B.1.step     7/24       3/24      C.2.step     8/24       2/24
  A.2.step     7/24       2/24      B.2.slow     7/24       1/24      C.4.slow     9/24       2/24
  A.4.step     7/24       1/24      B.3.step     8/24       1/24      C.3.step    11/24       2/24
  A.2.slow     8/24       2/24      B.2.step    11/24       5/24      C.3.slow    12/24       4/24

  score vs commitment rate   Pearson r = 0.775,  Spearman 0.766
  score vs candidate bytes   Pearson r = -0.075, Spearman 0.043
  commitment vs bytes        Pearson r = 0.004

TOOL USE FELL ALMOST EVERYWHERE. Tool calls per question, candidate minus S0:
  A.1 -1.2  A.2 -0.7  A.2S -2.3  A.3 -2.0  A.3S +0.2  A.4 +0.1  A.4S -2.0
  B.1 -0.1  B.2 -2.5  B.2S -2.8  B.3 -1.2  B.3S -2.2  B.4 -3.4  B.4S -3.0
  C.1 +2.0  C.2 -1.7  C.2S -0.6  C.3 -1.3  C.3S -4.0  C.4 -2.2  C.4S -0.4
Totals over the 24 questions: S0 169 tool calls; C.3.slow 74 (-56%); B.4.step 87; C.1.step 217 (+28%).
Several candidates answer some questions with ZERO tool calls, straight from the oracle pages in the
prompt — C.3.slow does this on UID0041, UID0109, UID0220, UID0233, UID0235, UID0240, getting two right.

A TOOL THAT DOES NOT EXIST. The actor has exactly three tools (`glob`, `read`, `grep`). Over the whole
campaign it issued 10,814 tool calls, of which 221 named something else and were refused by the harness:
  211 × `answer`      7 × `calculate`      3 × `tool`     observation: "[tool error: unknown tool answer]"
S0's own 24 selection traces contain NONE. On the candidates' selection evaluations, 109 in total:
  A.1 3  A.2 1  A.2S 3  A.3 14  A.3S 2  A.4 4  A.4S 10
  B.1 1  B.2 2  B.2S 2  B.3  2  B.3S 7  B.4 3  B.4S 0
  C.1 10 C.2 7  C.2S 19 C.3  0  C.3S 0  C.4 5  C.4S 14
This is the behaviour behind a large part of the 338 silent turns, and it is a behaviour the edited skills
induced. Arm B's accepted skill says, in so many words, "There is no `answer` tool — never call one", and
arm B's later candidates still did it (2, 2, 7, 3, 0 across its five later gates).

════════════════════════════════════════════════════════════════════════════════════════════════════
6. THE SCORER  (VERIFIED, with one counterfactual clearly marked)
════════════════════════════════════════════════════════════════════════════════════════════════════

Five of the 528 observations are answers that are numerically EQUAL to the ground truth and were scored
wrong, all on formatting:

  S0        UID0159   ground truth "39.31"   predicted "39.31%"
  B.1.step  UID0159   ground truth "39.31"   predicted "39.31%"
  A.4.step  UID0220   ground truth "27"      predicted "27.0"
  A.4.slow  UID0220   ground truth "27"      predicted "27.0%"
  C.3.slow  UID0220   ground truth "27"      predicted "27.0"

UID0159 asks for a PERCENT ("what percent of total U.S. assets… rounded to the nearest hundredths place")
and the ground truth carries no percent sign. UID0220 asks for a figure "rounded to the nearest tenths
place" and the ground truth is the bare integer "27".

COUNTERFACTUAL (clearly marked as such; the native exact-match scorer was required to stay unchanged and
did). If numeric equality were the rule, the scores would move like this:
  S0        4/24 → 5/24        A.4.step  1/24 → 2/24        A.4.slow  0/24 → 1/24
  B.1.step  3/24 → 4/24        C.3.slow  4/24 → 5/24        B.2.step  5/24 → 5/24 (unchanged)
S0 at 5/24 raises every arm's starting threshold to 5/24. Arm B's accepted candidate then TIES at 5/24 and
is rejected under the strict-improvement rule, and C.3's slow candidate ties too. **The campaign would
have had zero acceptances and all three arms would have kept S0.** The single acceptance in the whole
campaign turns on whether "39.31%" counts as 39.31 in S0's own baseline.

One further observation is a non-answer the scorer treated as a wrong value: C.1.step on UID0027 predicted
the prose "The document does not contain the required historical monthly yield data…".

════════════════════════════════════════════════════════════════════════════════════════════════════
7. FROM THE TRAINING OBSERVATION TO THE ACTOR'S BEHAVIOUR  (paired excerpts)
════════════════════════════════════════════════════════════════════════════════════════════════════

Selection rule, applied identically to all three arms: the largest regression in an arm is the candidate
with the most negative (gains − losses); ties broken by the lower score, then the earlier epoch. That
picks A.4.slow, B.4.step and C.4.step — each −4 and each 0/24. Added by name because the user asked for
them: arm B's accepted epoch-2 candidate, and arm C's epoch-3 slow candidate that tied S0.

── 7a. ARM B, EPOCH 2 — THE ONE ACCEPTED CANDIDATE (5/24 against 4/24) ─────────────────────────────

TRAINING OBSERVATION (`steps/step_0002/trajectory_digest.json`), 22 of 24 training questions failed:
   [14] "Model accumulated or partially accumulated evidence but never committed a final <answer>,
         looping or stopping mid-analysis (empty/blank prediction); two of these looped searching the
         bulletins for an operand that lives outside the documents (an exchange rate, a journal
         publication date)."
         UID0169 UID0019 UID0017 UID0087 UID0044 UID0085 UID0028 UID0115 UID0033 UID0163 UID0141
         UID0151 UID0166 UID0079
   [ 8] "operand_error: Misidentified the correct period from the table layout (wrong repeating year
         column block; wrong 'week ended' row for the requested date window)."
         UID0228 UID0014 UID0150 UID0034 UID0031 UID0030 UID0165 UID0026

MERGE AND RANKING: 8 edits merged, 2 ranked within an edit budget of 2, both applied (one `replace`, one
`append_before_protected_region`), support counts 22 and 14.

DEPLOYED WORDING — edit 1, support 22, replacing the last bullet of "Final Answer Discipline":
   "- Copy the final answer from a checked value, not from an unverified intermediate guess.
    - Commit once you hold the operands and computed result: stop searching and emit the answer
      immediately as plain text `<answer>...</answer>`. There is no `answer` tool — never call one.
    - Do not re-read the same line range or re-issue the same query after the deciding row is already
      captured; proceed to compute and answer to avoid non-terminating loops.
    - A blank prediction scores as wrong, and never end a turn with neither a tool call nor an
      `<answer>`: if some evidence remains missing after a strategy change, still emit the best-grounded
      answer or state the limit explicitly."

DEPLOYED WORDING — edit 2, support 14, appended as three new sections: "## External or Non-Document
Operands" ("Never leave the answer empty because one operand is external"), "## Table Layout and
Repeating Blocks" ("Treasury tables often repeat an identical column group… do not assume the leftmost
block is the requested period"), "## Multi-Hop Questions" ("Decompose into ordered sub-tasks… Record the
intermediate result explicitly before continuing").

THE ACTOR FOLLOWED IT — and that is how three of the four losses happened:
   UID0001  gt 2,602   S0 18 messages, 8 tool calls, answered "2,602"  → CORRECT
                       B.2 8 messages, 3 tool calls, answered "1580"   → WRONG
                       (stopped searching sooner and committed, exactly as instructed)
   UID0041  gt 0.011   S0 6 messages, 2 tool calls, silent             → no answer
                       B.2 4 messages, 1 tool call, answered "458.070" → WRONG
   UID0240  gt 0.9999  S0 4 messages, 1 tool call, silent              → no answer
                       B.2 6 messages, 2 tool calls, answered "0.3937" → WRONG
   Across all 21 candidates UID0240 was answered 17 times and never correctly.

THE ACTOR IGNORED IT — on the two questions it lost outright, with the same amount of work as S0:
   UID0170  gt [0.611, 1.635]   S0 4 messages, 1 tool call → "[0.611, 1.635]" CORRECT
                                B.2 4 messages, 1 tool call → "" silent turn
   UID0190  gt -11              S0 6 messages, 2 tool calls → "-11" CORRECT
                                B.2 4 messages, 1 tool call → "" silent turn
   The skill it was running says "never end a turn with neither a tool call nor an `<answer>`".

AND THE GAIN THAT DECIDED THE CAMPAIGN'S ONLY ACCEPTANCE IS A FORMATTING CHANGE:
   UID0159  gt 39.31   S0  answered "39.31%" → scored WRONG
                       B.2 answered "39.31"  → scored CORRECT
   The same number. Without this one question arm B's candidate is 4/24, ties, and is rejected.
   One more gain came with no tool use at all: UID0091, candidate answered "0.105" correctly in 2
   messages with zero tool calls, from the oracle pages alone; S0 made 5 tool calls across 2 files and
   went silent.

── 7b. ARM C, EPOCH 3 SLOW UPDATE — 4/24, TIED S0, REJECTED BY STRICT IMPROVEMENT ─────────────────

7 pairs sent (trimmed from 10 by rule OQA-PB-1 at 207,927 tokens), guidance 3,772 chars, and its
highest-priority section is:
   "ANSWER COMMITMENT (highest priority — most failures were empty outputs, not wrong math):
    - You must ALWAYS finish with a final <answer> holding your best computed value. Never end a turn,
      stop, or fall into a search loop without producing an answer…
    - Before you search the file at all, read the oracle-parsed pages already given in the question. They
      almost always contain the exact rows and columns needed. Extract the numbers from those pages
      directly, compute, and answer. Reserve grep/read for verification…
    - If a grep returns '[no matches],' that is not a reason to stop… Do not repeat the same failing
      search more than twice."

THE ACTOR FOLLOWED IT, MEASURABLY: tool calls fell from 169 to 74 (−56%, the largest change in the
campaign), commitment rose to 12 of 24 (the highest), and six questions were answered with zero tool
calls. Three gains (UID0086, UID0233, UID0235) and three losses (UID0001, UID0159, UID0170), all three
losses being S0-correct-or-answered questions that went silent or wrong.
   UID0233  gt 0.0013   S0 10 messages, 4 tool calls, silent  → C.3S 2 messages, 0 tool calls, "0.0013" CORRECT
   UID0109  gt 678077   S0 12 messages, 5 tool calls, silent  → C.3S 2 messages, 0 tool calls, "682397.05" WRONG
   UID0220  gt 27       S0 16 messages, 7 tool calls, silent  → C.3S 2 messages, 0 tool calls, "27.0" WRONG*
   (*numerically equal — with numeric equality this candidate is 5/24; see §6.)

── 7c. THE LARGEST REGRESSION IN EACH ARM — A.4.slow, B.4.step, C.4.step, all 0/24 ────────────────

All three lose the same S0-correct questions and almost always in the same way: S0 answered, the candidate
went silent, with FEWER tool calls.
   A.4.slow  UID0001 S0 "2,602" (8 tool calls) → silent (3 tool calls)
             UID0027 S0 "3069"  (6)            → silent (3)
             UID0170 S0 correct                → silent
   B.4.step  UID0001 S0 "2,602" (8)            → silent (2)
             UID0027 S0 "3069"  (6)            → silent (3)
             UID0039 S0 "176,688" wrong (3)    → silent (3)
   C.4.step  UID0001, UID0027, UID0170, UID0190 all S0-correct-or-answered → all silent
             UID0109 S0 silent → "1023198.00" wrong (13 tool calls over 2 files)

A.4.slow's guidance is near-identical IN INTENT to C.3.slow's — "Always commit to a final answer — never
end a turn empty… Read the Oracle Parsed Pages first; they are primary evidence… Do not spend your whole
step budget re-grepping" — and the outcome was the opposite: commitment 3/24 and 0/24 correct, against
C.3.slow's 12/24 and 4/24. (INTERPRETATION: the two documents differ in other respects too, so this is
not a controlled contrast; but it is the same headline instruction with opposite measured effect in two
arms.)

The clearest "ignored the instruction" trace in the campaign is A.4.slow on UID0039: 34 messages, 16 tool
calls, 7 of them REPEATS of an earlier call, and SEVEN calls to the nonexistent `answer` tool — then the
turn ended with no answer. The guidance it was running told it to stop repeating failing searches and to
commit.

════════════════════════════════════════════════════════════════════════════════════════════════════
8. WHAT THE CONTRACT LAYERS CONTRIBUTED  (VERIFIED)
════════════════════════════════════════════════════════════════════════════════════════════════════

ARM B — learned ACTION contracts, 96 reports, one per training trace:
   forecasts        561 confirmed, 1 VIOLATED, 2,114 the records cannot settle   (violated rate 0.00037)
   obligations      none (not this condition)
   distinct diagnoses per report   0 in all 96 reports
   boundary audit   PASS in all 96;  model versions v2 × 72, v4 × 24
   the one violated forecast:  UID0019, epoch 3, report 338b6a2d7b6f

ARM C — action contracts AND stage obligations, 96 reports:
   forecasts        636 confirmed, 0 violated, 2,450 unsettled
   obligations      69 DISCHARGED, 130 OUTSTANDING, 252 UNKNOWN, 11 VIOLATED
   the 11 violated obligations are ALL IN ONE REPORT: UID0014, epoch 3
   distinct diagnoses per report   2 in 83 reports, 3 in 5, 5 in 3, 6 in 1, 7 in 2, 9 in 1, 14 in 1

The reports themselves say a confirmation is not a successful prediction: "this item's own trace is part
of the evidence this model was learned from… the forecasts below are in-sample".

DID ANY CONTRACT FINDING REACH AN EDIT? Scanning all 101 proposed-edit entries in the 24 merge and rank
rounds (71 merged, 30 ranked — a ranked edit is a re-scan of a merged one that survived selection) for
anything only a contract report could have supplied — `RULE(`, `ASSUMED(`, the atom names
`col_path_sound`, `col_leaf_sound`, `sub_row_aligned`, `pattern_in_every_line`, and the obligation
vocabulary `DISCHARGED` / `OUTSTANDING` / `VIOLATED`:
   arm A  0 of 37 (27 merged + 10 ranked)
   arm B  0 of 34 (24 + 10)
   arm C  0 of 30 (20 + 10)
Not one edit in the campaign names any of it. (A hit would have been suggestive, not conclusive; a MISS
is the stronger direction — an edit that names nothing from the report could have been written from the
trace alone.)

WHAT THE ORDINARY ANALYST HAD ALREADY FOUND. All three arms' epoch-3 analysts independently reported the
same dominant failure, 13 or 14 of 24 each:
   arm A  "No final <answer> emitted; agents stalled after retrieving evidence or after repeated no-match
           searches."                                                        13 items, incl. UID0019
   arm B  "Agent ended the turn with neither a tool call nor an <answer> after looping on the same
           reads/greps or invoking an unknown 'answer' tool."                14 items, incl. UID0019
   arm C  "Agent stalled with no tool call and no <answer> block even when the needed rows were already
           read or present in the oracle page."                              14 items, incl. UID0014
Arm B's wording names the unknown-`answer`-tool mechanism, which arm A's does not — but that mechanism is
written in the raw trace as "[tool error: unknown tool answer]", which every arm's analyst sees, and the
contract report does not supply it. UID0019 (arm B's one violated forecast) and UID0014 (arm C's one
report with violated obligations) both appear in arm A's own diagnosis for the same epoch.

INTERPRETATION: on these records the contract layers added no diagnosis the ordinary analyst had not
already produced, and nothing from either layer is traceable into a proposed edit. This is a statement
about this campaign's four epochs and this actor, not about contract-guided optimization in general — and
in particular the actor failed so often by not finishing at all that there was little well-formed
execution for a contract to be informative about.

════════════════════════════════════════════════════════════════════════════════════════════════════
9. WHAT WOULD RESOLVE WHICH QUESTION, AND WHAT IT COSTS  (PROPOSALS — none of this has been run)
════════════════════════════════════════════════════════════════════════════════════════════════════

Measured unit costs (`research/officeqa/oqa_budget.py`, all MEASURED on this campaign):
   one 24-question evaluation = 248 actor attempts, $0.1574, 649 s median wall, 0.36 CPU core-hours
   at the measured 15.7 requests/min the account limit is not what binds; 4 evaluations fit one 90-min job

Headroom, projected after the held-out chain finishes (ledger reconciliation pending):
   actor attempts  ~19,700 of 30,000 left        actor charge  ~$9.19 of $16.00 left
   optimizer       157 calls / $4.50 left (none of the proposals below needs the optimizer)
   CPU             ~12.9 of 40 core-hours left   GPUs 0, and none wanted

T1 — REPEAT THE EXACT S0 EVALUATION.  **Recommended first, and on its own.**
   What: the baseline document b2f8624c8da8 (886 B), the same 24 selection questions, 8 independent draws.
   Resolves: how wide the gate's own measurement is ON THE GATE'S SPLIT (§3 measured it on the training
   split); whether S0's 4/24 was a high or a low draw; the per-question flip rate on the gate items, which
   says which of the 78 recorded "losses" carry any information; and an empirical P(a draw beats 4/24) to
   put beside the 0.371 already on record.
   Cost: 8 evaluations = 1,984 attempts, $1.26, 2.9 CPU core-hours, ~2.1 h wall serial (4 per job → 2 jobs).

T2 — ONE EDIT AT A TIME, on the only candidate that was ever accepted.
   What: four documents — the incumbent 84fb9da5dc93 (939 B), incumbent + edit 1 (the commit-discipline
   replace), incumbent + edit 2 (the three appended sections), and the full candidate 95989ce5ebad — each
   on the same 24 selection questions.
   Resolves: whether either edit has an effect distinguishable from the noise T1 measures, and in
   particular whether the commit-discipline edit buys answers at the price of correctness, which §5 and
   §7a suggest but cannot establish from one draw each.
   Cost: 3 draws each = 12 evaluations = 2,976 attempts, $1.89, 4.3 core-hours.
         5 draws each = 20 evaluations = 4,960 attempts, $3.15, 7.2 core-hours.

   POWER, which is why T1 should decide whether T2 is worth running. Taking sd = 1.95 questions from §3,
   the smallest difference detectable at 80% power and α = 0.05 two-sided is
         3 draws per document → 4.5 questions      5 draws → 3.4 questions      8 draws → 2.7 questions
   The accepted candidate's measured margin was ONE question. Detecting a one-question effect at this
   noise level needs roughly 60 draws per document — about $9.4 per document, more than the whole
   remaining allowance for even two documents. So if T1 confirms the §3 spread on the selection split,
   the useful next move is not more ablation draws at n = 24: it is a better measurement. Options, in
   rising order of cost: evaluate on train+val together (48 questions, sd ≈ 5.7 pp instead of 8.1 pp);
   compare documents PAIRED on the same questions and test the discordant questions rather than the
   totals; or report the commitment rate and correctness separately, since §5 shows they move in
   opposite directions and the single `hard` score hides it.

T3 — RE-SCORE WHAT IS ALREADY SAVED UNDER NUMERIC EQUALITY. Cost: ZERO, no new rollout.
   §6 already carries the result for the five cases the numeric relation finds, including the
   consequence that the campaign's only acceptance does not survive it. `oqa_parser_replay.py` can
   confirm it over all 528 saved responses with no paid call.

T4 — THE TERMINATION BUG, WHICH IS A SKILL-INDEPENDENT FINDING. Cost: ZERO to establish, unknown to fix.
   338 silent turns and 211 calls to a tool that does not exist are already in the records. If the harness
   answered an unknown-tool call with a message telling the actor to emit `<answer>` instead of just
   "[tool error: unknown tool answer]", or if a final round forced an answer the way the custom-search
   mode already does, the dominant failure mode would change — but that is a HARNESS change, not a skill
   edit, and it would alter the comparison's semantics. It needs explicit approval and it should not be
   mixed into a frozen comparison.

NOT PROPOSED, and why: accepting a candidate on a tie (it would have changed C.3's slow update from
reject to accept, and `CLAUDE.md` §7 forbids weakening a validation gate without explicit approval); any
use of the 60-question held-out split for anything but the pre-registered comparison; GPUs.

════════════════════════════════════════════════════════════════════════════════════════════════════
10. WHAT I DID NOT DO
════════════════════════════════════════════════════════════════════════════════════════════════════

* Nothing in this packet was tuned on held-out results. The held-out chain ran in the background
  throughout; §9's proposals were written before any held-out number beyond the already-recorded
  S0 = 3/60 was opened.
* No scorer, gate, prompt, split, seed or actor setting was changed. The diagnostic job only reads.
* No paid request was made in this phase: every API key is dropped before Python starts.
* I have not concluded why the method performed as it did. §0(a)–(d) are findings; the explanation is
  left open for review.
```
