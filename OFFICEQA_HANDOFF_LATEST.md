# OfficeQA handoff — latest

```text
================================================================================
OFFICEQA THREE-ARM CAMPAIGN — RECOVERY COMPLETE, HELD-OUT IN FLIGHT
prepared 2026-11-17 for a compaction   HEAD aadf991 (NOT pushed)
branch officeqa-contracts   worktree SkillOpt-officeqa
================================================================================

0. READ THIS FIRST
------------------
a) THREE HELD-OUT JOBS ARE IN THE QUEUE. Do not resubmit them, and do NOT rebuild
   the bundle oqa_heldout_20261117 while they are queued - its manifest is
   verified in-job.
       66332133  oqa-ho-S0                  COMPLETED  hard 0.0500 (3/60)
       66332134  oqa-ho-primary_B_selected  RUNNING
       66332135  oqa-ho-exploratory_A_last  PENDING (afterok 66332134)
   Bundle commit 251e9514beaefb88ed1ded997102c4769a2e23f1.
b) ONLY ARM B UPDATED ITS SKILL. One acceptance in twenty-one gate evaluations,
   by a one-question margin. That is not evidence that contracts help.
c) THE REAL FINDING IS ABOUT ALL THREE ARMS: the optimizer's edits made this
   actor WORSE on the selection set. 33 of 504 questions right where the null
   predicts 89, P = 2.1e-13. It does not separate the arms.
d) Four numbers I previously reported were wrong and are corrected below. The
   tools now recompute them from the artifacts so they cannot come from memory.

1. THE AUTHORIZATION, AS AMENDED 2026-11-17 (user)
--------------------------------------------------
TOTALS for the campaign, including everything already spent. The user also
authorized the recovery and the planned evaluations without further approval
inside them, and asked for a specific resource gap to be reported rather than a
ceiling crossed.  docs/research/OFFICEQA_AUTHORIZATION.md is authoritative.

  resource               ceiling     spent      left      used
  EmpirioLabs attempts    30,000     8,394    21,606       28%
  EmpirioLabs charge      $16.00   $5.6241  $10.3759       35%
  DeepSeek calls             260       103       157       40%
  DeepSeek charge          $8.00   $3.5016   $4.4984       44%
  CPU core-hours              40     23.47     16.53       59%
  GPUs                         0         0         -        -

  NOT yet included: the three held-out jobs, about $1.18 and 1,900 attempts.
  The ENFORCED optimizer cap is $7.95 / 259, one request BELOW the grant,
  because the slow-update size probe (job 66271290) spent 1 request and $0.0411
  outside the ledger. A ceiling that covers the whole campaign has to be set net
  of what did not pass through the ledger.
  CPU reads two ways and they differ 2x because every job asked for 2 CPUs:
  23.47 core-hours, or about 11.7 wall-clock job-hours. PLANNING USES THE LARGER.

2. THE RESULT: four epochs, three arms, all twelve arm-epochs sound
-------------------------------------------------------------------
  arm.ep  train   cand   ordinary gate    pairs  slow gate   guid  meta
  A.1     0.1250  1/24   reject             -    placeholder  -    skip
  A.2     0.0417  2/24   reject           10/10  reject 2/24  yes  written
  A.3     0.0417  1/24   reject           10/10  reject 1/24  yes  written
  A.4     0.2500  1/24   reject            8/10  reject 0/24  yes  written
  B.1     0.1250  3/24   reject             -    placeholder  -    skip
  B.2     0.0833  5/24   ACCEPT_NEW_BEST  10/10  reject 1/24  yes  written
  B.3     0.0000  1/24   reject           10/10  reject 1/24  yes  written
  B.4     0.1250  0/24   reject           10/10  reject 1/24  yes  written
  C.1     0.1250  2/24   reject             -    placeholder  -    skip
  C.2     0.2083  2/24   reject           10/10  reject 1/24  yes  written
  C.3     0.0833  2/24   reject            7/10  reject 4/24  yes  written
  C.4     0.1250  0/24   reject            9/10  reject 2/24  yes  written

  arm  condition                 selected skill        size   last skill
  A    Original SkillOpt         S0 9d377d1c2906       887B   5bd17ea109bc 940B
  B    Action Contracts Only     step2 95989ce5ebad  3,603B   95989ce5ebad (same)
  C    Action Contracts+Stages   S0 9d377d1c2906       887B   5bd17ea109bc 940B

  7 skills requested, 3 DISTINCT BY DIGEST, 2 BY ADVICE. Arms A's and C's last
  skills are S0 plus an EMPTY <!-- SLOW_UPDATE_START --><!-- SLOW_UPDATE_END -->
  placeholder: advice digest 3e70328ffb65, identical to S0's.

  ARM C EPOCH 3's SLOW UPDATE SCORED EXACTLY 4/24 - TYING S0's BASELINE - and was
  rejected only because upstream's gate requires STRICT improvement. That is the
  concrete case behind the "accept on a tie" option, which is NOT authorized and
  has NOT been applied.

3. PAIR COUNTS AND THE PROMPT-SIZE FALLBACK (rule OQA-PB-1, limit 225,000)
--------------------------------------------------------------------------
  6 of 9 slow updates sent 10/10. Trimmed: A.4 10->8 (261,376->205,051 tokens),
  C.3 10->7 (252,996->207,927), C.4 10->9 (235,924->221,772). Dropped in the
  fixed order stable_success -> improved -> persistent_fail -> regressed.
  ALL NINE produced usable guidance, and ALL NINE were rejected by the gate.

  THE FOUR no_content RESULTS WERE THE LEDGER, NOT THE SIZE. The original B.e4
  sent 10 pairs at 181,849 tokens - far inside the limit - and got no guidance,
  while B.e2 sent 10 at 223,815 and did.

4. THE STATISTICS, CORRECTED (research/officeqa/oqa_gate_stats.py)
------------------------------------------------------------------
  21 gate evaluations: 12 ordinary + 9 slow-update. ONE acceptance.
  candidate scores /24: 0 0 0 1 1 1 1 1 1 1 1 1 2 2 2 2 2 2 3 4 5
  mean candidate 1.571/24 against mean incumbent 4.238/24
  uniform null (21 x 0.371)       expected 7.78 acceptances
  per-gate null (own incumbent)   expected 7.85,  P(at most 1) = 0.001
  pooled over 21 x 24 = 504 question-level observations
      observed 33   null predicts 89   P(total <= 33) = 2.1e-13

  WHAT I GOT WRONG BEFORE, and what replaces it:
   - "9 gate evaluations"            -> 21. I counted arm-epochs, not gates.
   - "expected 2.99 acceptances"     -> that is 8 x 0.371; 9 x 0.371 = 3.34, as
                                        the user pointed out; the real figure
                                        against each gate's own incumbent is 7.85.
   - a uniform P(accept)=0.371       -> the threshold MOVED: arm B's incumbent
                                        rose to 5/24, so its later gates faced
                                        0.383.
   - "nothing separates harmful from -> right about 4 candidates, WRONG about 21.
      neutral"                          The null is strongly disfavoured.

  WHAT THE SCORES DO AND DO NOT SUPPORT. The optimizer's edits made this actor
  worse on the selection set, in all three arms, Original SkillOpt included. It
  does NOT separate the arms. It does NOT turn arm B's acceptance into noise:
  5/24 against 4/24 is a one-question margin this set cannot resolve. Caveat
  applied to both tails: all gates in an arm share the same 24 questions, so
  0.001 could be several times larger; a 56-question pooled deficit is far too
  large for within-arm correlation to explain.

5. WHAT WAS RECOVERED, AND HOW REUSE WAS DECIDED
------------------------------------------------
  THE DEFECT: the spend ledger capped CLAIMED money and never released what a
  process did not spend. Optimizer claims reached $4.00 with $2.19 paid, and from
  then on every optimizer request was refused. The trainer records that as
  skip_no_patches (ordinary) and no_content (slow, meta) - indistinguishable in
  the artifacts from a quiet epoch.

  THE AUDIT read the artifacts AND the ledger's per-process refusal records,
  because neither alone tells a starved stage from a quiet one. All 24 refusals
  sat in four jobs, six each (4 analyst, 1 slow, 1 meta): 66273861 C.e3,
  66273862 A.e4, 66273863 B.e4, 66273864 C.e4. NO OTHER STAGE WAS MASKED.
  Two shapes that look wrong and are not:
    - arm B epoch 3 made 3 analyst calls, not 4: it scored 0/24, so there was no
      SUCCESS group to reflect on.
    - arm C epoch 1 made no ranking call: its 4 merged edits already fit the
      edit budget of 4.

  RECOVERY CHAIN, all COMPLETED, 3.28 CPU core-hours in total:
    66323200 A.e4 -> 66323201 B.e4 -> 66323202 C.e3 -> 66323203 C.e4
  Bundle oqa_campaign_20261117c, commit 37c78ec683d53f656c0aceed9fde413b4ce1f941.
  git diff 797846e..37c78ec over skillopt/ configs/ scripts/ deletes NO line of
  the actor path: prompts, tool loop and scoring are identical to the first run.

  REUSE WAS DECIDED BY PROVENANCE, NOT POSITION. Every trace records the system
  prompt it ran under and that prompt embeds the skill verbatim, so the producing
  skill is recovered from the trace and matched against the skill the recovered
  state puts in force. SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1 settles it per row at
  run time, which is what made the conditional dependency safe:
    "epoch 4's paired rollout is stale if and only if epoch 3's gate accepted".
  OUTCOME: every saved rollout reused, NOTHING re-rolled, because all four
  recovered epochs rejected. Cost $0.68 actor / 1,081 attempts (planned $1.26 /
  1,981 - the gate passes used 5.6 attempts per question, not 10.32, because a
  candidate that answers nothing ends its tool loop early) and 36 optimizer
  calls / $1.2692.

  THE REUSE WAS FAITHFUL: each recovered slow update rebuilt IDENTICAL pair and
  token counts (A.4 10->8 at 261,376->205,051 both times; C.3 10->7 at
  252,996->207,927; B.4 10->10 at 181,849).

  THE CHECKPOINT REWOUND TO (what a wrong recovery would get wrong):
    arm A  step 3  incumbent 0.1667  best S0 step 0
    arm B  step 3  incumbent 0.2083  best step 2   <- acceptance preserved
    arm C  step 2  incumbent 0.1667  best S0 step 0
  The incumbent is reconstructed from the previous step's record AND the previous
  epoch's slow-update result, because an epoch does not end with its ordinary
  step. Every slow gate rejected, so no number moved.

  51 ORIGINAL RECORDS ARCHIVED to recovery/original/20261117-recovery (4.2 MB):
  each arm's runtime_state, history, summary, best_skill and skills/, the step
  records and slow/meta results from the first recovered epoch on, and both
  contract arms' model and EVOLVE files. Arm C's epoch-4 model, EVOLVE record and
  predictions were MOVED ASIDE (not deleted) and regenerated; each arm's
  first-recovered-epoch model was KEPT, and verify confirms it byte-identical.

  VERDICTS: oqa_recover.py verify PASS. Post-recovery audit: still starved NONE,
  recovered A.4 B.4 C.3 C.4.
  Records: recovery/{RECOVERY_AUDIT,RECOVERY_AUDIT_AFTER,RECOVERY_PLAN,
  RECOVERY_RECORD,RECOVERY_VERIFY,LEDGER_RECONCILE,LEDGER_RECONCILE_AFTER}.json

6. THE ACCOUNTING FIX, FINISHED AND PROVEN IN PRODUCTION
--------------------------------------------------------
  LEDGER VERDICT: PASS. After reconciliation net claims equal recorded spend
  EXACTLY in both roles - optimizer $3.460519 / 102 attempts, target $5.624077 /
  8,394 - with ZERO unresolved requests and ZERO reserve breaches.
  Released across the campaign: optimizer 46 attempts / $2.2395, target 456 /
  $2.2259. THE FOUR RECOVERY JOBS RELEASED THEIR OWN UNSPENT CLAIMS ON CLEAN
  EXIT - the fix working for real, not in a test.

  Three parts:
   1. release on clean exit, with USD claim blocks cut $0.25 -> $0.05 (at $0.25
      against a $4.00 cap each process stranded 6 per cent).
   2. RETAIN one reserve_usd per UNRESOLVED request - one that was sent and whose
      reply never arrived, which the provider may still have billed. Releasing it
      would let the next process spend money that is already gone.
   3. oqa_ledger_reconcile.py (rule OQA-REC-1) for a ledger whose processes have
      already exited. Refuses any owner it cannot show has finished; consults
      squeue every time; dry run by default.

  VERIFIED ACROSS ALL FOUR CONDITIONS THE USER NAMED: concurrent jobs at one cap
  (forked processes), clean exits, INTERRUPTION (a SIGKILLed process runs no
  atexit hook and keeps its whole claim - the safe direction, with the reconciler
  as the remedy), and RESTARTS (a requeued process does not get the authorization
  again; the body also refuses SLURM_RESTART_COUNT != 0 with exit 6).

  A ROUNDING DISAGREEMENT THE RECONCILIATION EXPOSED. oqa_ledger_read.py reported
  FAIL: "$4.9441 spent against $4.9441 claimed". The ledger adds
  int(round(usd*1e6)) per usage record and compares THAT against the claims; the
  audit summed raw floats, which over 7,313 records came out 26 MICRODOLLARS
  higher. The audit now checks the invariant in micros and reports the float sum
  beside it; the reconciler retains the larger of the two.

  The 24 refusal records REMAIN in the ledger as history. Every stage they refused
  has been recovered, and the audit reports those stages as "repaired", not
  "starved".

7. THE RELEASE PROTOCOL (docs/research/OFFICEQA_RELEASE_PROTOCOL.md)
--------------------------------------------------------------------
  Written down because it had been spread across documents and partly carried in
  conversation. Six rules, with a compliance table:
   1. the held-out split is read ONCE and nothing is tuned on it
      (evaluation.eval_test: false throughout the chain AND the recovery)
   2. PRIMARY = S0 against each arm's validation-selected skill, pre-registered
   3. a LAST skill is EXPLORATORY and labelled so, in the job name and the
      output directory
   4. the set is FROZEN BY DIGEST before submission (oqa_freeze_skills.py; the
      submitter verifies and REFUSES on a mismatch, which matters because the
      recovery rewrote exactly those files)
   5. each distinct skill scored once; an identical evaluation is reused when the
      skill digest AND the settings fingerprint match (PROVENANCE.json per result)
   6. JOB 66234927 STAYS SEALED AND UNREAD

  FROZEN SET: heldout/SKILLS_FROZEN.json, record digest f26ba852b9e59dc4.
  S0 is copied to heldout/S0.md so the exact skill scored is a campaign artifact.
  THE THIRD JOB IS NOT A THIRD ARM'S RESULT: the 940-byte skill differs from S0
  only by two HTML comment lines, so its advice is identical. Its number is an
  inadvertent replicate of S0 under a slightly different system prompt - a read
  on the instrument's run-to-run variability, nothing more.
  FIRST RESULT IN: S0 scores 3/60 = 0.0500 on the held-out split, against
  4/24 = 0.1667 on the gate set. Different question sets, intervals overlap; that
  gap is not yet a finding.

8. WHERE EVERYTHING IS
----------------------
  campaign root  $PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign  (252 MB)
  bundles        oqa_campaign_20261117  (commit 91b2494, chain 1)
                 oqa_campaign_20261117b (commit 797846e, chain 2)
                 oqa_campaign_20261117c (commit 37c78ec, the recovery)
                 oqa_heldout_20261117   (commit 251e951, the held-out - IN USE)
  traces         shared/epoch1/predictions/<UID>/
                 shared/val_baseline/predictions/<UID>/
                 arm_<X>/steps/step_000<N>/rollout/predictions/<UID>/
                 arm_<X>/steps/step_000<N>/selection_eval/predictions/<UID>/
                 arm_<X>/slow_update/epoch_0<N>/rollout_prev|rollout_curr/predictions/
                 arm_<X>/slow_update/epoch_0<N>/selection_eval/predictions/
                 arm_<X>/selection_eval_baseline/predictions/<UID>/
                 18 predictions dirs per arm, 2 shared
  reports        arm_<B|C>/stage_reports/UID<id>.<trace digest>.md  (content-addressed)
                 arm_<B|C>/stage_reports/GENERATED.jsonl            (append-only)
  contracts      contracts/{model_M0.json, arm_B/, arm_C/, MODEL_EVOLUTION.jsonl}
  ledger         ledger/{claims,spend,release}/  spend/<job>r<n>p<pid>.jsonl
  each predictions/<UID>/ holds conversation.json, target_user_prompt.txt (these
  two are what oqa_reports.trace_digest hashes) and target_system_prompt.txt

  TOOLS ALREADY WRITTEN FOR READING TRACES (python3 -I, from an extracted tree):
    oqa_trace.py                 render one trace: turns, tool calls, what it saw
    oqa_inventory.py             per-item inventory of a rollout directory
    oqa_reports.py               build a contract report; trace_digest;
                                 verify_report_provenance
    oqa_report_audit.py          audit stage reports FROM THE FILES: marker
                                 leakage, boundary audit, provenance
    oqa_optimizer_input_audit.py what the optimizer was actually shown
    oqa_rollout_checkpoint.py    sanity-check a rollout directory
    oqa_campaign_summary.py      whole-campaign readout + B-in-C containment
    oqa_gate_stats.py            enumerate every gate, do the arithmetic
    oqa_parser_replay.py         replay the answer parser over saved responses
    oqa_cases.py                 hand-picked case material
    oqa_recover.py               audit / plan / prepare / verify a recovery
    oqa_ledger_read.py           external ledger audit (stdlib only)
    oqa_ledger_reconcile.py      post-hoc release of exited processes' claims
    oqa_freeze_skills.py         freeze / verify the evaluation set by digest
    oqa_budget.py --headroom     what is left of each amended ceiling

9. TESTS
--------
  2,190 passed, 9 skipped, 368 subtests, on the cluster from the pinned bundle
  (job 66331956, commit 251e951). Run with:
    sbatch ... --wrap="bash $BUNDLE/run_tests_bundle.sh"
  FOUR of my own tests failed along the way; each was my error, not the code's:
   - I asserted the provenance guard catches a changed max_tool_turns. It does
     not: in offline mode the turn budget is in the per-round and USER prompts,
     not the system prompt. New rows now carry a settings fingerprint and the
     guard checks it when present.
   - I asserted every archived file still exists afterwards. `prepare` removes
     the resume markers it archives, deliberately.
   - I dropped an "import re" while replacing regexes with a module load.
   - I wrote a second advice-stripper with GUESSED markers (<!-- SLOW_UPDATE -->)
     when the trainer writes <!-- SLOW_UPDATE_START -->. It stripped nothing and
     reported 3 skills distinct by advice where there are 2.

10. STILL NOT AUTHORIZED
------------------------
  1. PUSHING this branch. Deployment is by pinned git archive bundle with an
     in-job MANIFEST.sha256 check (research/cluster/oqa_bundle.sh).
  2. GPUs, in any quantity.
  3. WEAKENING THE VALIDATION GATE. Accepting on a tie would have turned arm C's
     epoch-3 slow update from reject into accept. CLAUDE.md section 7 forbids it
     without explicit approval; OFFICEQA_GATE_RESOLUTION.md lists it as an
     option, not a recommendation.
  4. UNSEALING the held-out outcome of job 66234927, which remains UNREAD.
  5. The one remaining paid DeepSeek attempt from the original probe grant stays
     unspent.

11. WHAT TO DO NEXT
-------------------
  a) WAIT for 66332134 and 66332135. Then:
       for d in $CAMP/heldout/*/; do cat $d/eval_summary.json $d/PROVENANCE.json; done
       oqa_ledger_reconcile.py $CAMP/ledger --exited-jobs 66332133,66332134,66332135 --apply
       oqa_ledger_read.py $CAMP/ledger --questions N
     then refresh oqa_budget.py CEILINGS and OFFICEQA_AUTHORIZATION.md section 3.
  b) REPORT the primary comparison as S0 against arm B's skill - TWO distinct
     skills by advice - with the third labelled an inadvertent replicate of S0.
     One question on the 60-question held-out split is 1.7 percentage points.
  c) DO NOT read a held-out difference of one or two questions as a finding, and
     do not present the exploratory number as a comparison result.
================================================================================
```
