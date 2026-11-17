# OfficeQA — handoff, 2026-11-17 (the three-arm campaign is FINISHED; a recovery decision is with the user)

```
=====================================================================================================
OFFICEQA THREE-ARM CAMPAIGN — FINISHED. NOTHING IS RUNNING. A DECISION IS PENDING.
=====================================================================================================

0. READ THIS FIRST

   The campaign ran to completion, and a defect in MY OWN spend ledger cost four arm-epochs their optimizer
   calls. The sound comparison is EPOCHS 1-2, symmetric across all three arms. Arm B is the only arm whose
   gate accepted a candidate, and that acceptance is NOT evidence that action contracts help - the campaign
   produced FEWER acceptances than chance predicts at this gate's resolution. The recovery is cheap and fits
   the existing authorization, but it needs the user's word. Details in 5, 6 and 10.

1. WHERE IT IS

   FINISHED. Nothing running, nothing queued, 0 GPUs held. HEAD 771b3f0 + the containment-scope fix.

     CHAIN 1  commit 91b2494, bundle $PROJECT_ROOT/oqa_campaign_20261117
       66272737 shared COMPLETED   66272738 A.e1 COMPLETED   66272739 B.e1 COMPLETED
       66272740 C.e1 FAILED (concurrent-import race, fixed in df9d922) -> 66272741..49 CANCELLED

     CHAIN 2  commit 797846e, bundle $PROJECT_ROOT/oqa_campaign_20261117b, resumed with `--from C.1`
       66273855 C.e1 -> 66273856 A.e2 -> 66273857 B.e2 -> 66273858 C.e2 -> 66273859 A.e3
       -> 66273860 B.e3 -> 66273861 C.e3 -> 66273862 A.e4 -> 66273863 B.e4 -> 66273864 C.e4
       ALL COMPLETED.

   Campaign root  $PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign
   Readout        <campaign root>/CAMPAIGN_SUMMARY.json  (regenerate with oqa_campaign_summary.py)
   Ledger         <campaign root>/ledger                 (read with oqa_ledger_read.py; it will report FAIL,
                                                          correctly, because this ledger predates the fix)

2. THE AUTHORIZATION AND WHAT IS LEFT OF IT

   User, 2026-11-17: 20 CPU job-hours, zero GPUs, 15,000 EmpirioLabs attempts and $8.00, 130 DeepSeek
   calls and $4.00, inside the shared 50 RPM account limit.

   Projected on the campaign's OWN measurements: 14,118 attempts and $8.97 for everything including a
   7-skill held-out stage. The TRAINING chain alone is 9,783 attempts and $6.22, inside. The held-out
   stage costs $0.39 per DISTINCT skill, so 4 fit and 5 do not — but see 5: as of epoch 1 there is only
   ONE distinct skill by advice, which would make the whole held-out stage $0.39.

3. THE FOUR GUARDS, all inert unless their environment variables are set

   a. skillopt/model/spend_ledger.py — a durable shared authorization ledger on /nfs/turbo. Attempts are
      CLAIMED in blocks before the request is sent, O_CREAT|O_EXCL for atomicity, so
      spent <= claimed <= cap holds across concurrent processes AND restarts. Per-job ceilings sit beside
      the account caps. VERIFIED ON NFS (job 66272499): four processes split exactly 23 of a 23 cap, a
      restart respent 0 of 9, an unusable ledger refused.
   b. skillopt/optimizer/prompt_budget.py — rule OQA-PB-1, limit 225,000 estimated tokens, drop order
      stable_success -> improved -> persistent_fail -> regressed, later-sampled first. Logs the pair
      count actually sent and whether guidance came back, per arm, to prompt_budget.jsonl.
   c. skillopt/model/route_guard.py — called inside the trainer and eval_only. Reads back each role's
      LIVE backend, base URL and model and proves the dispatch path at zero HTTP cost. The actor-only
      stages require the optimizer role to be UNUSABLE (no key loaded).
   d. The job body refuses to run on a Slurm restart (SLURM_RESTART_COUNT != 0, exit 6); every job is
      --no-requeue.

4. WHAT IS VERIFIED, on the live run and not the dry run

   S0 on this endpoint        training hard 0.125 (3/24); gate baseline, validation hard 0.1667 (4/24)
   M0                         8d8ecceecd0b77533913ab5996cfa57740d6eb4e6782d7146e74fbcb7e7d1a69
                              24 items, 339 tool steps, 0 assumptions rejected
   route, both roles          PASS inside the trainer, dispatch proven at zero cost
   shared traces reused       24 injected per arm; no arm re-rolled the training set
   arm A gets upstream input  OFFICEQA_STAGE_REPORT_DIR unset, no report heading in any analyst prompt
   arm B carries NO stage     24 of 24 reports, ZERO stage markers (mean 4,438 chars)
   arm C contains arm B       24 of 24, line for line (mean 6,095 chars)
   ONE starting model         24 distinct trace digests per arm, all under the single M0 digest
   epoch 1 labelled in-sample 24 of 24 reports per arm carry the notice, in the text the optimizer reads
   answer-key isolation       boundary_audit PASS on every report
   documents                  153 of 153 present after fetching 150 files (121.1 MB); 108 of 108 items
                              answerable (107 AVAILABLE, 1 AVAILABLE_VIA_TOOLS, 0 ABSENT, 0 UNTESTED)
   UID0019                    AVAILABLE; 1 file, 2 page refs, BOTH resolved. Same for UID0039, UID0064.

5. THE RESULT

     step/epoch |  arm A Original   |  arm B Action Contracts  |  arm C Action + Stages
        1       |  reject 0.0417    |  reject 0.1250           |  reject 0.0833
        2       |  reject 0.0833    |  ACCEPT_NEW_BEST 0.2083  |  reject 0.0833
        3       |  reject 0.0417    |  reject 0.0417           |  skip_no_patches  <- starved
        4       |  skip_no_patches  |  skip_no_patches         |  skip_no_patches  <- starved
       best     |  S0, 0.1667       |  step 2, 0.2083          |  S0, 0.1667

   S0 is 4/24 on the gate. Arm B's accepted skill is 3,601 chars from S0's 887.

   ARM B'S ACCEPTANCE IS NOT EVIDENCE THAT CONTRACTS HELP. The margin was 5/24 against 4/24 - ONE question.
   P(a candidate exactly as good as S0 is accepted) = 0.371. In the sound window there were 9 gate
   evaluations; expected acceptances if every candidate were neutral = 2.99; observed = 1;
   P(at most 1 | all neutral) = 0.141. The campaign produced FEWER acceptances than chance predicts.
   docs/research/OFFICEQA_GATE_RESOLUTION.md has the working.

   WHAT DID DIFFER: the arms' edits are different documents (epoch 1: 4,174 / 4,652 / 4,420 chars from 887).
   The channels change what the optimizer writes, not whether the gate takes it.

   SCOPE: epochs 1-2 are complete and symmetric in all three arms - that is the comparison. Epoch 3 is
   ASYMMETRIC (A and B ran it, C did not). Epoch 4 is empty everywhere.

6. FOUR CORRECTIONS TO FIGURES OR CLAIMS I GAVE THE USER

   a. "The binding constraint on wall time is the 50 RPM ACCOUNT limit" — FALSE. Measured 15.7
      requests/min from the ledger's timestamps; 1 of 485 attempts failed and no 429 appeared, so it is
      per-request latency and the SEQUENTIAL tool loop, not throttling. 255 min became ~15 h. The nine
      later-epoch jobs were raised from 60 to 120 minutes with scontrol as a result.
   b. Three per-question constants came from the A40 pilot and were wrong: attempts 9.33 -> 10.32,
      prompt tokens 106,300 -> 145,532, completion tokens 4,221 -> 10,525 (+149%, the largest error).
      Cost per question-rollout $0.0046 -> $0.00656, which is what puts the $8.00 ceiling in play.
   c. "Expect some slow updates to send 9 pairs rather than 10" — FALSE. The first one drew the WORST
      case (all ten pairs showing trajectories) and came to 185,332 tokens against the 225,000 limit.
      The per-showing-pair constant was 34,569 and is 18,533 here; the old figure came from a saved
      collection holding a 171,394-character outlier.
   d. "The optimizer's first edit is harmful in every condition" — NOT SUPPORTED. See
      docs/research/OFFICEQA_GATE_RESOLUTION.md. A strict gate rejects an equally good candidate 63% of
      the time at n=24, and P(all four observed candidates land <= 4/24 | all are as good as S0) = 0.157.
      The supported claim is only that no candidate has measured above S0.

7. WHAT TO READ BEFORE REPORTING ANY RESULT

   * LEDGER: research/officeqa/oqa_ledger_read.py <ledger> --questions N. Verdict must be PASS and
     observed must be inside the caps. (The job body's own ledger summary writes nothing — `python -I`
     implies -E so `-m skillopt.model.spend_ledger` cannot find the deployed tree. Fixed in the repo,
     deliberately NOT patched into the live bundle, whose manifest is verified in-job.)
   * PROMPT BUDGET: prompt_budget.jsonl per arm-epoch — pairs_sent, dropped, guidance_produced.
   * SLOW UPDATE: slow_update/epoch_NN/slow_result.json per arm, for action "no_content".
   * REPORTS: research/officeqa/oqa_report_audit.py --expect 24 --arm B=... --arm C=...
   * ROUTE: route_guard.json per run dir.
   * HELD-OUT: research/cluster/oqa_heldout_submit.sh --dry-run first. PRIMARY = S0 and each arm's
     best_skill.md. A last-skill number is EXPLORATORY and must be labelled so. The script refuses to
     submit a set it computes to exceed the authorized charge.

8. THE DEFECT THAT COST FOUR ARM-EPOCHS, AND THE FIX

   The ledger enforced its cap on CLAIMED money and never released what a process did not spend:

       optimizer  spent $2.1913  CLAIMED $4.0000 (the whole cap)  wasted $1.8087 = 45%
       target     spent $4.9441  CLAIMED $7.0000 of $8.00         wasted $2.0559

   Once claims reached $4.00 every further optimizer attempt was refused - 24 refusals - while DeepSeek had
   been paid $2.19 of an authorized $4.00. The cap did its job on the wrong quantity. Four arm-epochs made
   ZERO optimizer calls and $1.4976 went on rollouts whose analyst never ran.

   FIXED in 771b3f0: a clean exit RELEASES unused claims into release/, which _granted subtracts; a process
   that dies keeps its claim (the conservative direction); the default USD block drops $0.25 -> $0.05, because
   waste is bounded by one block per process and a block must be small AGAINST THE CAP; oqa_ledger_read.py
   nets out releases and flags the exact condition. Four tests, including the counterfactual.

9. OPEN FOR THE USER

   1. THE RECOVERY DECISION. Every starved arm-epoch still holds its 24-question training rollout and both
      10-question slow-update rollouts. Only the refused optimizer calls and the gate passes are missing.

          complete all four starved arm-epochs   $1.26 actor  +  $1.32 optimizer
          remaining authorization                $3.06 actor  +  $1.81 optimizer
          left afterwards                        $1.80 actor  +  $0.49 optimizer
          held-out, per distinct skill           $0.39 actor

      It fits what was already authorized. It needs the user's word because it spends ~$2.60 more and means
      resetting each arm's runtime_state.json to re-run a step recorded as skip_no_patches. The alternative
      is to write up the two-epoch result and run held-out on what exists.
   2. The gate's resolution (5). Options recorded without recommendation: a larger selection set, a repeated
      S0 baseline, or accepting on a tie - the last is WEAKENING a validation gate and needs explicit approval.
   3. Four epochs still give no cross-epoch memory in the ORDINARY update; one epoch of four steps is the fix.
   4. The C6 contract/check changes are still not applied.
   5. A push has not been authorized.
   6. ONE PAID DEEPSEEK ATTEMPT from the probe grant remains UNSPENT.
   7. The held-out outcome of job 66234927 remains deliberately UNREAD and SEALED.

10. WHAT THIS CAMPAIGN CANNOT ESTABLISH

   The earlier offline probe on the two-arm run's traces found 0 of 8 cases gained a valid diagnosis
   attributable to the report and 1 of 8 gained a misleading one
   (docs/research/OFFICEQA_REPORT_VALUE_PROBE.md). That stands; this campaign tests it at scale. Nothing
   here is Lean-verified — the Lean artifact compiled (job 66259448, 64 statements, Lean 4.20.0) and the
   reports rest on the Python extractor. FP8 makes this a new baseline, so the A40 pilot's S0 does not
   transfer.
=====================================================================================================
```
