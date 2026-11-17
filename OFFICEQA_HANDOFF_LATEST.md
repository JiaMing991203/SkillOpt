# OfficeQA handoff (latest) — the THREE-ARM launch packet, with the slow-update size question settled

One copy-pasteable block. Written 2026-11-17 02:48 EST (from `date`).

```text
OFFICEQA - THREE-ARM LAUNCH PACKET, 2026-11-17 02:48 EST
Branch officeqa-contracts, worktree ~/dev/lean_skillopt/SkillOpt-officeqa.  HEAD ca150a9, clean, NOT pushed.
OFFLINE PREP COMPLETE AND CHECKED. NOTHING SUBMITTED for the campaign. Nothing running or queued. 0 GPUs held.
NEXT: the user will instruct on the FULL CAMPAIGN after this compaction. It needs a BUDGET (section 5) and the
      DECISIONS in section 8. DO NOT SUBMIT BEFORE THEY DO.
Full plan: docs/research/OFFICEQA_THREE_ARM_LAUNCH.md     State note: OFFICEQA_STATE.md sections A0-NEXT, A0-PROBE
Evidence:  runs/20261117-officeqa-3arm-prelaunch/   runs/20261117-officeqa-slow-probe-66271190/

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

=== 5. THE BUDGET TO AUTHORIZE (research/officeqa/oqa_budget.py; every constant carries its source record) ===
  wall time             6 hours, one campaign
  CPU job time          20 job-hours, <=4 concurrent, each <=60 min, 2 CPUs + 8 GB  (= 40 CPU-core-hours)
  GPUs                  0
  actor HTTP requests   15,000        (11,756 at 4 pairs / 12,763 at 10 pairs, + retries)
  actor charges         $8.00         ($5.73 at 4 pairs / $6.22 at 10)
  DeepSeek calls        130           (102 computed: 34/arm = 7/step x 4 epochs + slow update & meta skill)
  DeepSeek charges      $4.00         (4 pairs: $1.38 off-peak / $2.75 peak.  10 pairs: $1.75 / $3.49)
THE REQUEST ABOVE COVERS EITHER 4 OR 10 PAIRS - the difference is inside the headroom, so one grant serves both.
QUESTION-ROLLOUTS: 4 pairs -> 48 shared + 3 x 264 = 840 training + 420 held-out worst case = 1,260.
                  10 pairs -> 48 shared + 3 x 300 = 948 training + 420 held-out worst case = 1,368.
  per arm: epoch 1 = 24 (gate only); each later epoch = 24 train + 24 gate + 2xS slow update + 24 slow gate.
THE BINDING CONSTRAINT IS THE 50 RPM *ACCOUNT* LIMIT, NOT LATENCY: 235 min (4 pairs) / 255 min (10 pairs) floor,
  against 168 min latency-bound at the measured 3.41 s median with workers=4. Account-wide, so running the arms
  concurrently divides the quota rather than raising it.
MEASURED BASIS (not extrapolated): 9.33 actor requests and 106,300 prompt + 4,221 completion tokens per
  question at a 24-turn cap, from arm B's step 2 (224 calls / 24 question-rollouts); EmpirioLabs $0.04/$0.07
  per M. DeepSeek priced at the CACHE-MISS rate: measured cache hits were 256-2,048 tokens, and the 10-pair
  probe saw 512 hits against 243,474 missed.

=== 6. EFFECTIVE SETTINGS, READ BACK FROM THE PINNED TRAINER (scripts/train.py:load_config) ===
  num_epochs 4   batch_size 40   accumulation 1  -> steps_per_epoch = ceil(24/40) = 1, ONE ordinary update/epoch
  minibatch_size 8   merge_batch_size 8   analyst_workers 2   failure_only false   skill_update_mode patch
  edit_budget 4, min 2, lr_scheduler COSINE -> the per-epoch budget is 4, 3, 2, 2. NOT constant at 4. Same in
     every arm.
  use_gate true, sel_env_num 0 (all 24 val), gate on `hard` with STRICT improvement (a tie is a REJECT)
  use_slow_update true   slow_update_gate_with_selection TRUE (paper-style: the candidate goes through the SAME
     validation gate, not force-injected)   slow_update_samples 4 (SEE SECTION 8.1)   policy mixed (no top-ups)
  use_meta_skill true - it REUSES the slow update's comparison pairs: 1 optimizer call, 0 extra rollouts
  eval_test FALSE - the held-out split is a SEPARATE job (section 7)
  seed 42   workers 4   max_tool_turns 24   max_completion_tokens 16384   target qwen3-5-4b (EmpirioLabs)

=== 7. THE HELD-OUT EVALUATION - ONCE PER DISTINCT SKILL ===
research/officeqa/oqa_heldout.py --plan.  S0 + each arm's validation-selected skill (best_skill.md) + each
arm's last skill (skills/skill_v<step>.md), DEDUPLICATED BY DIGEST: at most 7 rollouts of the 60-question split.
Fewer in practice - in the A40 two-arm run all four candidates were rejected and both final skills were
byte-identical to S0. This is why eval_test is false: with it true the trainer re-scores S0 once per arm.

=== 8. DECISIONS FOR THE USER ===
8.1 slow_update_samples: 4 (as configured) or 10?  *** MEASURED, job 66271290, NOT estimated ***
    A realistic 10-pair slow-update request - rendered by the campaign's OWN run_slow_update from saved training
    traces - WAS ACCEPTED BY DEEPSEEK: prompt_tokens 243,986, finish_reason "stop", 7,562 completion tokens,
    43.7 s, and run_slow_update returned 3,235 chars of PARSED guidance, so the trainer would record an accepted
    candidate and gate it on the selection set. Charge $0.041 off-peak / $0.082 peak.
      pairs   prompt, measured mix   worst case (every pair showing)   against the 243,986 now PROVEN
        4            92,230                  138,276                   inside always
        6           138,345                  207,414                   inside EVEN IN THE WORST MIX
       10    230,575 (MEASURED 243,986)      345,690                   measured mix PROVEN, worst case unproven
       20           461,150                  691,380                   far beyond anything tried
    Cost of 10 over 4: +108 question-rollouts, +1,007 actor requests, +$0.49 actor, +$0.74 DeepSeek at peak,
    +20 min of rate-limited wall clock. All inside the section-5 request.
    MY RECOMMENDATION: GO TO 10 and keep the post-run no_content check. An over-large prompt is NOT fatal -
    run_slow_update catches the provider error and the trainer records action "no_content", symmetrically across
    arms and at no cost - so the downside is a slow update that silently produces nothing, which the check
    catches. If you want nothing in unproven territory, 6 is the largest worst-case-safe value and still 1.5x
    the sample of 4.  TO CHANGE IT: one line in configs/research/officeqa_api3.yaml + rerun oqa_budget.py.
    NO DEMONSTRATED HEADROOM BEYOND 10: at 34,569 tokens per trajectory-showing pair the measured 10-pair mix
    sits ~13,000 tokens under the proven point, less than half a pair.
    AFTER THE RUN, check every arm's slow_update/epoch_NN/slow_result.json for "no_content" before reporting
    that the slow update ran.
8.2 Epoch 1's reports are IN-SAMPLE (section 4). Leave-one-out for epoch 1 would fix it at no cost - 24 models
    instead of one. Small change to oqa_lc_arm.seed; say the word.
8.3 Four epochs still gives NO cross-epoch memory in the ORDINARY update: step_buffer is declared inside the
    epoch loop and steps_per_epoch is 1. The gated slow update and the meta skill now carry some memory, which
    is new since the A40 run, but the ordinary update does not. One epoch of four steps remains the fix.
8.4 FP8 makes this a NEW BASELINE: same weights as the A40 (Qwen/Qwen3.5-4B), different numerics (FP8 + FP8 KV
    vs bfloat16). S0 must be re-measured here; the A40's 5/12 does not transfer. Symmetric across arms.
8.5 Availability untested for 48 of 108 items (section 2).
8.6 A PUSH HAS NOT BEEN AUTHORIZED (section 9).
8.7 ONE PAID DEEPSEEK ATTEMPT REMAINS UNSPENT from the probe authorization (2 granted, 1 used). It could close
    either residual: that meta_skill_content parses back, or the real context ceiling via one deliberately
    oversized request. Leave it unspent unless asked.

=== 9. THE JOB SHAPE - DO NOT REDISCOVER IT ===
13 jobs, each <=60 min: one per ARM-EPOCH (12) + the held-out evaluation (1, split if it exceeds an hour). One
job per arm-epoch is required, not a convenience: OFFICEQA_LC_MODEL is read from the environment. Between jobs
`oqa_lc_arm.py evolve` runs offline in seconds at no cost and freezes the next model.
  research/cluster/oqa_bundle.sh <name> <body> [commit]   builds and uploads a PINNED staging bundle: refuses a
    dirty tree, clears any stale tree from the bundle dir, writes a sha256 manifest the job verifies, and leaves
    the private settings.env alone. Verified twice tonight.
  research/cluster/oqa_api_body.sh (rollouts) / oqa_slow_probe_body.sh (optimizer-only) are invoked as
    sbatch --wrap="bash <bundle>/<body>.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle>
  working in a NODE-LOCAL /tmp. A script submitted directly as the sbatch batch file from /nfs/turbo was killed
  by NODE_FAIL at 60 s, FOUR TIMES EACH, on EIGHT nodes, before Python ran, while a control job with identical
  account/partition/resources COMPLETED. --no-requeue did NOT stop it.
  THE ATTEMPT LEDGER IS PER-PROCESS: a requeue respends the authorization with a fresh counter. oqa_slow_probe_
  body.sh now REFUSES TO RUN when SLURM_RESTART_COUNT is non-zero - copy that guard into every spending job.
  *** ANY JOB THAT CALLS THE OPTIMIZER MUST PIN THE BACKEND. *** skillopt.model.chat_optimizer is a ROUTER and
  OPTIMIZER_BACKEND defaults to "openai_chat", which means AZURE. Job 66271190 configured the DeepSeek endpoint
  perfectly, dispatched to Azure, had the exception swallowed by run_slow_update, and recorded "no_content"
  after ZERO HTTP attempts - a result that reads like a DeepSeek failure and is not one. Call
  set_optimizer_backend("openai_compatible"), read it back, reset the cached per-role client, and prove the
  dispatch path (oqa_slow_probe.dispatch_check does it at zero HTTP cost). The trainer does this itself via its
  config; a standalone driver does NOT.
  DEPLOYMENT: research/deploy.sh refuses a commit not on origin and pushing was never authorized. Every cluster
  job so far ran from a `git archive` staging bundle with a sha256 manifest verified in-job.

=== 10. WHAT THIS PREP DOES NOT ESTABLISH ===
  Nothing about which arm is better - no arm has run on the hosted actor. Nothing about accuracy on the new
  splits - S0 is unmeasured here. Nothing about whether the reports help: the earlier offline probe on the
  two-arm traces found 0 of 8 cases gained a valid diagnosis attributable to the report and 1 of 8 gained a
  MISLEADING one (OFFICEQA_REPORT_VALUE_PROBE.md); that stands, and this campaign is what would test it at
  scale. Nothing Lean verified - the compiled artifact (job 66259448, Lean 4.20.0, all 64 statements) is
  unchanged, and these reports rest on the Python extractor. The pre-launch dry run's skills and scores are
  MEANINGLESS: the optimizer was a stub and the rollouts were recorded. What it establishes is the WIRING.
  The DeepSeek context CEILING is still unknown: 243,986 tokens is a demonstrated acceptance, not a limit.

=== 11. SETTLED FACTS (do not re-derive) ===
  - DeepSeek deepseek-flash at https://api.deepseek.com ACCEPTED a 243,986-token prompt and returned parsed
    JSON (job 66271290). Published figures for the chat family disagree (128k V3-era vs 1M current) and none of
    the sources is DeepSeek's own documentation, so the ceiling is unknown above 243,986.
  - This project's prompts run at 3.228 characters per token: the offline estimate was 243,756 against 243,986
    reported, 0.09% low. Reliable enough to plan with.
  - DeepSeek prices (optimizer_calls_summary.json): off-peak cache-hit $0.003 / miss $0.15 / output $0.60 per M;
    peak is exactly double. _chat_messages_impl treats retries=N as N TOTAL attempts.
  - The DeepSeek key is ONLY on the cluster: SECRETS_FILE -> DEEPSEEK_API_KEY, mapped by lib.sh load_secrets to
    OPTIMIZER_OPENAI_COMPATIBLE_API_KEY. The actor key is at ~/.config/lean_skillopt/actor_api.env, mode 600.
    A local OPENAI_API_KEY exists in this harness's own environment - it is NOT the user's and must not be used.
  - The slow update DISABLES per-trajectory truncation on purpose, so its prompt carries both trajectories of
    every comparison pair in full. Only regressed / persistent_fail / improved print a trajectory;
    stable_success does not and costs almost nothing. ~34,569 tokens per showing pair.
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
    SkillOpt). THE SCRATCHPAD IS CLEARED ON REBOOT; recreate the venv if it is gone. Run the suite with
    --ignore on the 4 openpyxl files.
  - Never tune on held-out. Never derive contracts from the skill. No secrets in tracked files. Record every
    submission in docs/research/jobs.tsv and the coordination ledger. Timestamps come from `date`.
  - PRE-EXISTING HYGIENE DEBT: OFFICEQA_STATE.md and DAILY_LOG.md already contain cluster paths and the key's
    location from earlier sessions, against the project's own rule. Nothing is pushed, so there is no exposure
    today - but this needs cleaning before any push is authorized.

=== 12. SEALED ===
  The held-out score of job 66234927 remains deliberately UNREAD.

=== 13. ARTIFACTS ===
  runs/20261117-officeqa-slow-probe-66271190/  RENDER.json (both stages, 3 batch sizes), GROWTH.json (the
      per-showing-pair curve + the computed 10-pair figures), SEND_66271290.json (the paid attempt),
      SEND_66271190_azure_misdispatch.json (the router lesson's evidence), BUDGET.json at 10 pairs, job log
  runs/20261117-officeqa-3arm-prelaunch/       PRELAUNCH.json, BUDGET.json at 4 pairs, HELDOUT_PLAN.json,
      OFFICEQA_FROZEN_SETS_V3.json, models/ (M0 + each arm's evolved model + MODEL_EVOLUTION.jsonl),
      arm_A|B|C/ (full trainer out_roots; _dryrun/ holds every optimizer prompt), arm_B|C_evolved/
  runs/20261116-offline-lc-port/               the port's loop output + lean_job_66259448/
  runs/20261117-officeqa-api-smoke-66264849/   SMOKE.json, job.log, slurm.out, 3 predictions
  runs/20261116-160952_officeqa_arm_66228142/audit_evidence/  44 files, the three audits
  cluster bundle (reusable): $PROJECT_ROOT/oqa_slow_probe_bundle_20261117, settings.env already correct
  docs/research/: OFFICEQA_THREE_ARM_LAUNCH.md (the full plan), OFFICEQA_FROZEN_SETS_V3.json,
      OFFICEQA_API_COMPARISON_PLAN.md, OFFICEQA_LEARNED_CONTRACT_PORT.md, OFFICEQA_CONTRACT_AUDIT.md,
      OFFICEQA_REPORT_VALUE_PROBE.md, OFFICEQA_EPOCH2_REPORT_AUDIT.md, EXPERIMENT_LOG.md, DAILY_LOG.md, jobs.tsv
  Coordination ledger last record: OQA-12:PAUSED-FOR-COMPACTION-AWAITING-CAMPAIGN-BUDGET
```
