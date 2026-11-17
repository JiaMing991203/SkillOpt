# OfficeQA — handoff, 2026-11-17 (the three-arm campaign is RUNNING)

```
=====================================================================================================
OFFICEQA THREE-ARM CAMPAIGN — STATUS AT 2026-11-17 ~05:20 EST
=====================================================================================================

1. WHERE IT IS

   RUNNING, on its second chain. 0 GPUs held, 0 requested.

     CHAIN 1  commit 91b2494, bundle $PROJECT_ROOT/oqa_campaign_20261117
       66272737 shared  COMPLETED
       66272738 A.e1    COMPLETED      66272739 B.e1  COMPLETED
       66272740 C.e1    FAILED  (concurrent-import race; see 6)
       66272741..66272749             CANCELLED by the afterok dependency

     CHAIN 2  commit 797846e, bundle $PROJECT_ROOT/oqa_campaign_20261117b, resumed with `--from C.1`
       66273855 C.e1 COMPLETED -> 66273856 A.e2 RUNNING -> 66273857 B.e2 -> 66273858 C.e2
                     -> 66273859 A.e3 -> 66273860 B.e3 -> 66273861 C.e3
                     -> 66273862 A.e4 -> 66273863 B.e4 -> 66273864 C.e4

   Campaign root  $PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign
   Ledger         <campaign root>/ledger     read it with research/officeqa/oqa_ledger_read.py
   Pushing is still NOT authorized; both bundles are `git archive` trees with in-job manifest checks.

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

5. RESULTS SO FAR — every candidate rejected

     arm A epoch 1   candidate 0.0417 (1/24)   REJECT    629 s
     arm B epoch 1   candidate 0.1250 (3/24)   REJECT    733 s
     arm C epoch 1   candidate 0.0833 (2/24)   REJECT    962 s
     arm A epoch 2   candidate 0.0833 (2/24)   REJECT   2142 s   (ordinary update)
     arm A epoch 2   slow update: 10/10 pairs sent, ~185,332 tokens, guidance produced; gate running

   All arms' best_skill.md is S0 at 0.1667. The arms' CANDIDATES do differ — epoch 1 gave three
   different documents (4,174 / 4,652 / 4,420 bytes from S0's 887) — so the feedback channels change
   what the optimizer writes. What none has produced is an edit the gate accepts.

   skills/skill_v0001.md is NOT a candidate. When a candidate is rejected the trainer still writes the
   step's snapshot, which with use_slow_update on is the retained skill plus an empty SLOW_UPDATE
   placeholder. All three arms' copies are byte-identical for that reason alone. oqa_heldout.py now
   reports `skills_distinct_by_advice` and names any arm whose last skill carries S0's advice; after
   epoch 1 that is all three, so 7 requested skills are 2 distinct by digest and ONE by advice.

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

8. OPEN FOR THE USER

   1. If the held-out set turns out to need 5+ distinct skills, raise the actor ceiling to about $10 or
      drop the exploratory last-skill passes. As of epoch 1 this is moot (one distinct skill by advice).
   2. The gate's resolution (6d). Options on the record, none recommended: a larger selection set, a
      repeated S0 baseline, or accepting on a tie — the last is WEAKENING a validation gate and needs
      explicit approval.
   3. Four epochs still give no cross-epoch memory in the ORDINARY update; one epoch of four steps is
      the fix. The gated slow update and meta skill now carry some memory, which is new.
   4. The C6 contract/check changes (docs/research/OFFICEQA_CONTRACT_AUDIT.md) are still not applied.
   5. A push has not been authorized.
   6. ONE PAID DEEPSEEK ATTEMPT from the probe grant remains UNSPENT.
   7. The held-out outcome of job 66234927 remains deliberately UNREAD and SEALED.

9. WHAT THIS CAMPAIGN CANNOT ESTABLISH

   The earlier offline probe on the two-arm run's traces found 0 of 8 cases gained a valid diagnosis
   attributable to the report and 1 of 8 gained a misleading one
   (docs/research/OFFICEQA_REPORT_VALUE_PROBE.md). That stands; this campaign tests it at scale. Nothing
   here is Lean-verified — the Lean artifact compiled (job 66259448, 64 statements, Lean 4.20.0) and the
   reports rest on the Python extractor. FP8 makes this a new baseline, so the A40 pilot's S0 does not
   transfer.
=====================================================================================================
```
