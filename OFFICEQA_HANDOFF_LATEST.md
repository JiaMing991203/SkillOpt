# OfficeQA handoff (latest) — the THREE-ARM launch packet

One copy-pasteable block. Written 2026-11-17 01:17 EST (from `date`).

```text
OFFICEQA - THREE-ARM LAUNCH PACKET, 2026-11-17 01:17 EST
Branch officeqa-contracts, worktree ~/dev/lean_skillopt/SkillOpt-officeqa.
OFFLINE PREP COMPLETE AND CHECKED. NOTHING SUBMITTED. Nothing running or queued. 0 GPUs held.
NEXT: the campaign needs a BUDGET (section 5) and two DECISIONS (section 8). Do not submit before both.
Full plan: docs/research/OFFICEQA_THREE_ARM_LAUNCH.md     Evidence: runs/20261117-officeqa-3arm-prelaunch/

=== 1. THE THREE ARMS (the AC-1 structure) ===
  A  Original SkillOpt           the ordinary analyst inputs, byte-identical to upstream
  B  Action Contracts Only       + feedback from the evolving action contracts
  C  Action Contracts + Stages   + the action feedback, the stage obligations, and the action-to-stage checks
ONE config for all three: configs/research/officeqa_api3.yaml. The arms differ in two env vars only:
  A: OFFICEQA_STAGE_REPORT_DIR unset
  B: ...DIR=<dir>  ...CONDITION=LC_ACTION  OFFICEQA_LC_MODEL=<frozen model>
  C: ...DIR=<dir>  ...CONDITION=LC_STAGE   OFFICEQA_LC_MODEL=<frozen model>

=== 2. THE THREE SPLITS - rule OQA-SEL-3, digest 117836ff4139..., supersedes OQA-SEL-2 (fc02291540ef...) ===
docs/research/OFFICEQA_FROZEN_SETS_V3.json. Built from the tracked ID manifest only: no question text, no
document, no score, no trace. DISJOINT, and all three OQA-SEL-2 sets are SUBSETS, so every saved development
trace is still a trace of a current item.
  train  24 of the released 50   strata 6/6/6/6     10 of 24 availability-untested   12 carried over
  val    24 = THE WHOLE RELEASED VAL SPLIT, unfiltered and unstratified   4 of 24 untested
  test   60 of the released 172  strata 15/15/15/15  34 of 60 untested    24 carried over
  train: UID0033 0034 0063 0163 0151 0087 0014 0026 0079 0141 0166 0115 0017 0030 0031 0169 0165 0044
         0028 0065 0085 0150 0019 0228
  val:   UID0001 0027 0039 0041 0052 0070 0072 0086 0091 0109 0142 0154 0159 0161 0170 0190 0213 0217
         0220 0233 0234 0235 0239 0240
  test:  UID0006 0011 0016 0020 0021 0023 0095 0158 0146 0232 0080 0047 0061 0206 0048 0008 0076 0078
         0082 0088 0089 0198 0099 0160 0171 0116 0137 0126 0131 0064 0003 0009 0010 0012 0013 0015
         0187 0245 0214 0050 0180 0084 0175 0118 0093 0004 0005 0022 0025 0062 0068 0147 0112 0223
         0148 0096 0117 0140 0216 0100
  UID0046 stays EXCLUDED from the train pool (OQA-SEL-2: no retrievable value in its page or document).
  NOT ESTABLISHED: 48 of the 108 items have no availability verdict - that needs the documents, which are on
  the cluster. A short CPU preflight settles it and spends nothing.

=== 3. REPORT SEPARATION - CHECKED ON THE RENDERED TEXT, 24 of 24 ATTEMPTS PASS ===
Arm C's report = arm B's report + the stage half, so the arms differ by the stage material and nothing else.
  audit_action_only   FAILS arm B on a STAGE_MARKER: the stage headings, the obligation kinds (cell_bound,
                      period_match, unit_match, arith_recorded, answer_traced), the states DISCHARGED /
                      OUTSTANDING. build_one_lc then raises StageLeak instead of handing it over. Generic words
                      ("stage", "conversion", ...) are ADVISORY only: the report quotes bulletin column
                      headings, and one such word in a scanned table must not abort an arm.
  audit_containment   FAILS if arm C's report does not contain arm B's LINE FOR LINE.
  measured: arm B 2,965 chars mean, arm C 6,134. Headings differ too, because the optimizer reads them:
  "#### Action Contract Report" vs "#### Action Contract and Stage Report".

=== 4. PRE-LAUNCH: TWO SAVED EPOCHS THROUGH scripts/train.py:main() - VERDICT PASS, NO PROBLEMS ===
research/officeqa/oqa_prelaunch.py, one run, one verdict -> runs/20261117-officeqa-3arm-prelaunch/PRELAUNCH.json
The real entry point and the real trainer. Exactly two things replaced, because no authorization covers them:
  ACTOR      chat_target_messages RAISES. Every rollout dir pre-seeded from recorded predictions; run_batch
             resumes from results.jsonl, so a correct run sends NOTHING and an unseeded dir fails loudly.
  OPTIMIZER  chat_optimizer replaced in all 7 importing modules by a recorder; every prompt written to disk.
             Its OUTPUT is not realistic - no conclusion is drawn from the skills produced.
Each arm ran as TWO PHASES, one per epoch, resuming from runtime_state.json - the shape the campaign must use,
because OFFICEQA_LC_MODEL is read from the environment and names a different model each epoch.
  arm A gets the ordinary input          PASS  no report, no heading, in any of 4 analyst prompts
  arm B gets the Action-Only report      PASS  2/2 prompts per phase; 12/12 reports free of stage material
  arm C gets both halves                 PASS  its own heading, no other
  audited == sent                        PASS  12/12 reports found VERBATIM in an analyst prompt
  trace + model digests to the optimizer PASS  24 reports, 2 collections, 24 distinct trace digests, each
                                               matching the attempt on disk and the model the arm ran under
  an evolved model reaches the optimizer PASS  new digest in the records; the report drops the retired
                                               contract and names the three new ones
  arm C contains arm B line for line     PASS  24/24

MODEL EVOLUTION (research/officeqa/oqa_lc_arm.py). Both contract arms start from ONE model learned on the
shared rollout, then each evolves from its OWN traces with the same learner, evidence rules and repair schedule.
  epoch 1 -> M0   epoch 2 -> M0   epoch 3 -> M2   epoch 4 -> M3      (evolve runs at each epoch END)
  order inside an epoch: rollout -> reports under the model frozen BEFORE it -> analyst -> gate -> evolve
  evidence ACCUMULATES; evolve replays the new traces FIRST, hash-chaining each prediction before reveal.
  M0 on the shared rollout: adopted flat_header_authoritative, table_scale_applies_to_rows,
      grep_cap_not_reached; REJECTED index_alignment (contradicted at UID0026/e1@44) and year_inheritance
      (UID0028/e1@8); induced grep.effect.1, read.effect.1, read.effect.2.
  evolved on a later epoch: 82 forecasts confirmed / 1 violated / 71 unsettled OUT OF SAMPLE; the one
      counterexample UID0028/e2@42 (width_uniform forecast true, observed false) auto-refuted and RETIRED
      read.effect.1; grep.effect.2, read.effect.3, read.effect.4 induced; grep.effect.1 (60 steps/11 items) and
      read.effect.2 (23/6) retained with their evidence.
  IN-SAMPLE CAVEAT, recorded not hidden: M0 is learned on the rollout epoch 1's reports describe, so epoch 1's
      forecasts are NOT predictions. The frozen model carries its evidence item list and the report SAYS SO in
      its own text. Epochs 2-4 are out-of-sample.

FOUR DEFECTS FOUND AND FIXED BY BUILDING THIS, each with a regression test:
  1. Evidence keyed its atom cache on the ITEM id, so epoch 2's attempt of a training question reused epoch 1's
     atom values. On the real traces this turned ONE genuine counterexample into FIVE phantom ones. Attempts now
     carry trace_key "<item>/e<epoch>".
  2. A counterexample found at replay never triggered the refutation: re-scoring lives in apply_proposal, which
     was only called when the learner proposed something new - not the case from epoch 3 on.
  3. A RETIRED contract was invisible to the optimizer (only `refuted` was listed), and retiring is what the
     repair schedule does most often.
  4. A contract kept with a counterexample was printed as a retention saying "no counterexample".
TESTS: tests/test_officeqa_three_arm.py 25 passed. Whole local suite 2,057 passed, 11 skipped (4 files
  uncollectable in the scratch venv for want of openpyxl - pre-existing, unrelated).

=== 5. THE BUDGET TO AUTHORIZE (computed from measured constants: research/officeqa/oqa_budget.py) ===
  wall time             6 hours, one campaign
  CPU job time          20 job-hours, <=4 concurrent, each <=60 min, 2 CPUs + 8 GB  (= 40 CPU-core-hours)
  GPUs                  0
  actor HTTP requests   15,000        (11,756 computed at the measured rate, + retries)
  actor charges         $8.00         ($5.73 computed)
  DeepSeek calls        130           (102 computed: 34/arm = 7/step x 4 epochs + slow update & meta skill)
  DeepSeek charges      $4.00         ($1.36 off-peak, $2.72 IF EVERY CALL LANDS AT PEAK)
QUESTION-ROLLOUTS: 48 shared + 3 arms x 264 = 840 training, + 420 held-out worst case = 1,260.
  per arm: epoch 1 = 24 (gate only); each later epoch = 80 (24 train + 24 gate + 8 slow update + 24 slow gate).
THE BINDING CONSTRAINT IS THE 50 RPM *ACCOUNT* LIMIT, NOT LATENCY: 11,756 / 50 = 235 min floor, against 168 min
  latency-bound at the measured 3.41 s median with workers=4. Account-wide, so running the arms concurrently
  divides the quota rather than raising it.
MEASURED BASIS (not extrapolated): 9.33 actor requests and 106,300 prompt + 4,221 completion tokens per
  question at a 24-turn cap, from arm B's step 2 (224 calls / 24 question-rollouts); EmpirioLabs $0.04/$0.07
  per M. DeepSeek priced at the CACHE-MISS rate because the measured cache hits were 256-2,048 tokens against
  up to 172,548 missed.
vs THE OLD TWO-ARM ASK (8 job-h, 4,000 requests, $2.50, 56 calls, $1.50): three arms not two, 24 training
  questions not 12, 60 held-out not 24, and the gated slow update + meta skill switched on.

=== 6. EFFECTIVE SETTINGS, READ BACK FROM THE PINNED TRAINER (scripts/train.py:load_config) ===
  num_epochs 4   batch_size 40   accumulation 1  -> steps_per_epoch = ceil(24/40) = 1, ONE ordinary update/epoch
  minibatch_size 8   merge_batch_size 8   analyst_workers 2   failure_only false   skill_update_mode patch
  edit_budget 4, min 2, lr_scheduler COSINE -> the per-epoch budget is 4, 3, 2, 2. NOT constant at 4. Same in
     every arm.
  use_gate true, sel_env_num 0 (all 24 val), gate on `hard` with STRICT improvement (a tie is a REJECT)
  use_slow_update true   slow_update_gate_with_selection TRUE (paper-style: the candidate goes through the SAME
     validation gate, not force-injected)   slow_update_samples 4   longitudinal_pair_policy mixed (no top-ups)
  use_meta_skill true - it REUSES the slow update's comparison pairs: 1 optimizer call, 0 extra rollouts
  eval_test FALSE - the held-out split is a SEPARATE job (section 7)
  seed 42   workers 4   max_tool_turns 24   max_completion_tokens 16384   target qwen3-5-4b (EmpirioLabs)

=== 7. THE HELD-OUT EVALUATION - ONCE PER DISTINCT SKILL ===
research/officeqa/oqa_heldout.py --plan.  S0 + each arm's validation-selected skill (best_skill.md) + each
arm's last skill (skills/skill_v<step>.md), DEDUPLICATED BY DIGEST: at most 7 rollouts of the 60-question split.
Fewer in practice - in the A40 two-arm run all four candidates were rejected and both final skills were
byte-identical to S0. This is why eval_test is false: with it true the trainer re-scores S0 once per arm.

=== 8. DECISIONS STILL OPEN ===
  1. slow_update_samples 4, AGAINST UPSTREAM'S 20. This repo disables per-trajectory truncation in the slow
     update on purpose, so the longitudinal prompt carries both trajectories of every pair in full. MEASURED:
     12 pairs = 846,563 chars = 262,257 tokens, 8 of them in trajectory-showing categories -> ~32,800 tokens
     per showing pair. Against the 173,060-token envelope (the largest optimizer prompt this project has sent
     successfully, arm B's 2nd analyst call of step 2):
         4 samples  ->  87k typical / 131k worst case   INSIDE even in the worst case
         6 samples  -> 131k / 197k                      inside on the measured mix, OVER at worst
        20 samples  -> 437k / 656k                      far over
     Too large is not fatal - run_slow_update catches it and the trainer records action "no_content" - but the
     run would then report a gated slow update that produced no guidance. To raise it, confirm the optimizer's
     real context window first (a free GET /models settles it; the published DeepSeek figures disagree, 128k for
     the V3 era vs 1M for the current ones, with no official source among them). AFTER THE RUN, check every
     arm's slow_update/epoch_NN/slow_result.json for "no_content" before reporting the slow update ran.
  2. Epoch 1's reports are IN-SAMPLE (see section 4). Leave-one-out for epoch 1 would fix it at no cost -
     24 models instead of one. Small change to oqa_lc_arm.seed; say the word.
  3. Four epochs still gives NO cross-epoch memory in the ORDINARY update: step_buffer is declared inside the
     epoch loop and steps_per_epoch is 1. The gated slow update and the meta skill now carry some memory, which
     is new since the A40 run, but the ordinary update does not. One epoch of four steps remains the fix.
  4. FP8 makes this a NEW BASELINE: same weights as the A40 (Qwen/Qwen3.5-4B), different numerics (FP8 + FP8 KV
     vs bfloat16). S0 must be re-measured here; the A40's 5/12 does not transfer. Symmetric across arms.
  5. Availability untested for 48 of 108 items (section 2).
  6. A PUSH HAS NOT BEEN AUTHORIZED (section 9).

=== 9. THE JOB SHAPE - DO NOT REDISCOVER IT ===
13 jobs, each <=60 min: one per ARM-EPOCH (12) + the held-out evaluation (1, split if it exceeds an hour). One
job per arm-epoch is required, not a convenience: OFFICEQA_LC_MODEL is read from the environment. Between jobs
`oqa_lc_arm.py evolve` runs offline in seconds at no cost and freezes the next model.
  research/cluster/oqa_api_body.sh, invoked as
    sbatch --wrap="bash <bundle>/oqa_api_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle>
  working in a NODE-LOCAL /tmp. A script submitted directly as the sbatch batch file from /nfs/turbo was killed
  by NODE_FAIL at 60 s, FOUR TIMES EACH, on EIGHT nodes, before Python ran, while a control job with identical
  account/partition/resources COMPLETED. --no-requeue did NOT stop it.
  THE ATTEMPT LEDGER IS PER-PROCESS: a requeue respends the authorization with a fresh counter. Keep jobs short
  and check SLURM_RESTART_COUNT in the log before trusting any ledger total.
  DEPLOYMENT: research/deploy.sh refuses a commit not on origin and pushing was never authorized. Both previous
  cluster jobs ran from a `git archive HEAD` staging bundle with a sha256 manifest verified in-job.

=== 10. WHAT THIS PREP DOES NOT ESTABLISH ===
  Nothing about which arm is better - no arm has run on the hosted actor. Nothing about accuracy on the new
  splits - S0 is unmeasured here. Nothing about whether the reports help: the earlier offline probe on the
  two-arm traces found 0 of 8 cases gained a valid diagnosis attributable to the report and 1 of 8 gained a
  MISLEADING one (OFFICEQA_REPORT_VALUE_PROBE.md); that stands, and this campaign is what would test it at
  scale. Nothing Lean verified - the compiled artifact (job 66259448, Lean 4.20.0, all 64 statements) is
  unchanged, and these reports rest on the Python extractor. The dry run's skills and scores are MEANINGLESS:
  the optimizer was a stub and the rollouts were recorded. What it establishes is the WIRING.

=== 11. SETTLED FACTS (do not re-derive) ===
  - The actor key is at ~/.config/lean_skillopt/actor_api.env on the cluster, mode 600, 79 chars.
  - GET /v1/models/qwen3-5-4b is UNAUTHENTICATED and free: function_calling, json_schema, 262144 context,
    32768 max output, FP8 weights + FP8 KV, hugging_face_id Qwen/Qwen3.5-4B, 50 RPM, $0.04/$0.02/$0.07 per M.
  - NO PREFIX CACHING on the actor (job 66264849: cached_prompt_tokens 0 on every reply).
  - python -I is isolated mode and IGNORES PYTHONPATH.
  - The frozen splits are CSV with quoted multi-line source_docs fields: use the adapter's own dataloader and
    never count lines to count records.
  - target_backend "openai_chat" means AZURE. Both roles must read "openai_compatible".
  - Lean toolchain 4.20.0 under the project's own elan dir on cluster storage (OQA_LEAN_BIN in the private site
    env). The learned-contract note's "Lean 4.35.0" does not exist there.
  - Local venv: /private/tmp/claude-501/.../scratchpad/venv312 (the workstation python is 3.9 and cannot import
    SkillOpt). THE SCRATCHPAD IS CLEARED ON REBOOT; recreate the venv if it is gone.
  - Never tune on held-out. Never derive contracts from the skill. No secrets, cluster paths or run dirs in
    tracked files. Record every submission in docs/research/jobs.tsv and the coordination ledger. Timestamps
    come from `date`.

=== 12. SEALED ===
  The held-out score of job 66234927 remains deliberately UNREAD.

=== 13. ARTIFACTS ===
  runs/20261117-officeqa-3arm-prelaunch/   PRELAUNCH.json, BUDGET.json, HELDOUT_PLAN.json,
      OFFICEQA_FROZEN_SETS_V3.json, models/ (M0 + each arm's evolved model + MODEL_EVOLUTION.jsonl),
      arm_A|B|C/ (full trainer out_roots with _dryrun/ holding every optimizer prompt), arm_B|C_evolved/
  runs/20261116-offline-lc-port/           the port's loop output + lean_job_66259448/
  runs/20261117-officeqa-api-smoke-66264849/  SMOKE.json, job.log, slurm.out, 3 predictions
  runs/20261116-160952_officeqa_arm_66228142/audit_evidence/  44 files, the three audits
  docs/research/: OFFICEQA_THREE_ARM_LAUNCH.md (the full plan), OFFICEQA_FROZEN_SETS_V3.json,
      OFFICEQA_API_COMPARISON_PLAN.md, OFFICEQA_LEARNED_CONTRACT_PORT.md, OFFICEQA_CONTRACT_AUDIT.md,
      OFFICEQA_REPORT_VALUE_PROBE.md, OFFICEQA_EPOCH2_REPORT_AUDIT.md, EXPERIMENT_LOG.md, DAILY_LOG.md, jobs.tsv
  Coordination ledger last record: OQA-11:THREE-ARM-PREP-COMPLETE-AWAITING-BUDGET
```
