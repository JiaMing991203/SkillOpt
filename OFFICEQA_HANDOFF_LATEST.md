# OfficeQA handoff (latest)

One copy-pasteable block. Written 2026-11-17 00:35 EST (from `date`).

```text
OFFICEQA - HANDOFF 2026-11-17 00:35 EST, prepared for a compaction
Branch officeqa-contracts, worktree ~/dev/lean_skillopt/SkillOpt-officeqa.
HEAD e624c2f (clean, NOT pushed). Nothing running or queued. 0 GPUs held.
NEXT: the user will instruct on the FULL-SCALE RUN after the compaction. DO NOT START BEFORE THEY DO.

=== WHAT IS DONE ===
1. A40 two-arm comparison (Original SkillOpt vs + stage/action feedback): all four candidates REJECTED, both
   arms' final skills byte-identical to S0. Spend 1.77/8 A40-h, 16/24 DeepSeek calls, $0.154/$1.
2. Three offline audits: epoch-2 reports were stale (fixed, cache keyed to the trace); the report surfaced no
   diagnosis a trace-only review missed (0 of 8 valid, 1 of 8 misleading); the report's problem is obligation
   BOOKKEEPING, not rule content.
3. Learned-contract port from the ALFWorld branch: the full loop ran offline - learn, freeze by digest, commit a
   prediction, reveal, real counterexample, repair, recheck, independent confirmation, Lean, report, skill edit.
   docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md
4. Lean compile, job 66259448: Lean 4.20.0, ALL 64 statements, rc 0, 0 output, 0 warnings, 0 sorry, 4.24 s.
   Axiom set NOT checked (adding #print axioms would break the digest guarantee).
5. Hosted-actor switch + smoke test, job 66264849: TOOL LOOP PASS. See below.

=== SMOKE TEST RESULT (job 66264849, COMPLETED exit 0) ===
2 CPUs / 8 GB / ZERO GPUs, elapsed 00:01:12 (68.4 s in the driver), commit 07d4bb5, skill 9d377d1c2906...
  structured tool calls returned  TRUE   18 of 20 replies carried tool_calls
  arguments usable                TRUE   every argument set parsed as JSON; 0 malformed
  tools executed                  TRUE   18 tool calls run against the documents, 0 observation errors
  results fed back                TRUE   all 3 items took further turns after an observation
  attempts 20/87 | 0 errors | 0 retries | charges $0.003363/$0.50 | latency 1.63/3.41/7.04 s
  tokens 73,145 prompt + 6,251 completion | cached_prompt_tokens 0 -> NO PREFIX CACHING EXISTS
  0 run artifacts contain the key (checked on the cluster against the live value)
ACCURACY, separate and NOT the verdict: 1 of 3 answered, hard [0,1,0].
Authorization spend: 23 of 100 attempts, $0.0034 of $0.50, ~3 min of 30 min CPU, 0 GPUs.

=== THE FULL-SCALE RUN NEEDS A BUDGET (the old OQA-5 has 8 DeepSeek calls left, no CPU, no actor budget) ===
  CPU job time        8 job-hours, <=4 concurrent, each <=60 min, 2 CPUs + 8 GB
  GPUs                0
  Actor HTTP attempts 4,000
  Actor charges       $2.50     (measured $0.00112/question at 12 turns, ~$0.003 at 24 -> ~$0.80 total)
  DeepSeek calls      56
  DeepSeek charges    $1.50
RUNTIME ~2 h, and the 50 RPM ACCOUNT LIMIT is the binding constraint, not latency (3,100 requests = 62 min
floor; 44 min latency-bound at 3.41 s median with workers=4). Split into ~10 jobs of <=60 min and use resume:
measured queue behaviour was 6-72 min for <=60 min requests vs 4h23m for a 90-min request.
Full rationale per line: docs/research/OFFICEQA_API_COMPARISON_PLAN.md section 4.4.

=== USE THE JOB SHAPE THAT WORKS - DO NOT REDISCOVER IT ===
research/cluster/oqa_api_body.sh, invoked as
  sbatch --wrap="bash <bundle>/oqa_api_body.sh" --chdir=<bundle> --export=ALL,OQA_BUNDLE=<bundle>
working in a NODE-LOCAL /tmp. A script submitted directly as the sbatch batch file from /nfs/turbo was killed by
NODE_FAIL at 60 s, FOUR TIMES EACH, on EIGHT different nodes (66262999, 66263561) before Python ran, while a
control job with identical account/partition/resources COMPLETED. --no-requeue did NOT stop it.
THE ATTEMPT LEDGER IS PER-PROCESS: a requeue respends the authorized attempts with a fresh counter. Keep jobs
short and check SLURM_RESTART_COUNT in the log before trusting any ledger total.

=== DEPLOYMENT: NOTHING HAS BEEN PUSHED ===
research/deploy.sh refuses a commit not on origin, and pushing was never authorized. Both the Lean job and the
smoke test ran from a `git archive HEAD` STAGING BUNDLE with a sha256 manifest verified in-job. Either ask for a
push, or reuse the bundle route: the cluster bundle dir is $PROJECT_ROOT/oqa_api_smoke_bundle_20261116, and the
private settings.env there is already correct (keys: OQA_PY, OQA_SPLIT_DIR, OQA_DOCS_DIR, ACTOR_ENV_FILE,
OQA_API_MAX_ATTEMPTS, OQA_API_USD_CAP, OQA_API_MAX_TOOL_TURNS).

=== THREE DECISIONS STILL OPEN (plan doc 4.5) ===
1. FP8 makes this a NEW BASELINE. Same weights as the A40 (Qwen/Qwen3.5-4B), different numerics (FP8 + FP8 KV vs
   bfloat16). S0 must be re-measured on this actor; the A40's 5/12 does not transfer. Symmetric across arms.
2. Four epochs still gives NO cross-epoch memory. step_buffer is declared inside the epoch loop and
   steps_per_epoch is 1, so epoch n never sees epoch n-1's rejected edits. Four epochs = four independent
   attempts. The fix is one epoch of four steps - still open from the A40 run.
3. Arm B's frozen model v7 has a KNOWN COUNTEREXAMPLE on the confirmation collection (read.effect.1 =
   width_uniform at UID0028@42). Defensible to freeze, but record it rather than pick a cleaner-looking model.

=== READY AND VERIFIED - DO NOT REDO ===
configs/research/officeqa_api.yaml   one config for BOTH arms; records the FP8 and sampling differences that
    cannot be matched. Both roles must read "openai_compatible": target_backend "openai_chat" means AZURE.
research/officeqa/oqa_api_smoke.py   fail-closed preflight, hard attempt + USD caps with worst-case reservation
    settled against reported usage, tool-loop verdict separate from accuracy, never_sent accounting.
research/officeqa/oqa_api_probe.py   two-attempt (~$0.00005) live tool-calling probe. Run it first whenever the
    endpoint, model or tool schemas change.
research/officeqa/oqa_lc_*.py (9)    the ported learned-contract layer; research/officeqa/lean/OqaContracts.lean
    is the compiled artifact with its MANIFEST.sha256.
TREATMENT ARM provenance: arm B emits the obligation-form report under a frozen model whose digest is verified
    first; the cache key covers the MODEL VERSION as well as the trace; every report records trace_sha256 and
    model_sha256. Verified on saved A40 traces: 24 reports PASS against their own attempts; epoch-1 reports vs
    epoch-2 attempts FAIL; a report under model v5 FAILs against v7.
TESTS: whole local suite 2032 passed, 11 skipped (4 files uncollectable in the scratch venv for want of
    openpyxl - pre-existing, unrelated). Local venv:
    /private/tmp/claude-501/.../scratchpad/venv312 (workstation python is 3.9 and cannot import SkillOpt) -
    THE SCRATCHPAD IS CLEARED ON REBOOT; recreate the venv if it is gone.

=== SETTLED FACTS (do not re-derive) ===
- The actor key is at ~/.config/lean_skillopt/actor_api.env on the cluster, mode 600, 79 chars. I once reported
  it absent from both machines; that was wrong - I never looked in ~/.config.
- GET /v1/models/qwen3-5-4b is UNAUTHENTICATED and free: function_calling, json_schema, 262144 context,
  32768 max output, FP8 weights + FP8 KV, hugging_face_id Qwen/Qwen3.5-4B, 50 RPM, $0.04/$0.02/$0.07 per M.
- python -I is isolated mode and IGNORES PYTHONPATH.
- The frozen splits are CSV with quoted multi-line source_docs fields: use the adapter's own dataloader, and
  never count lines to count records (train 12, val 12, test 24 - verified with a CSV parser).
- Lean toolchain: 4.20.0 under the project's own elan dir on cluster storage, as OQA_LEAN_BIN in the private
  site env. The learned-contract note's "Lean 4.35.0" does not exist there.
- Frozen question sets: rule OQA-SEL-2, digest fc02291540ef..., 12/12/24, UID0046 -> UID0063.
- Never tune on held-out. Never derive contracts from the skill. No secrets, cluster paths or run dirs in
  tracked files. Record every submission in docs/research/jobs.tsv and the coordination ledger. Timestamps
  come from `date`.

=== SEALED ===
The held-out score of job 66234927 remains deliberately UNREAD.

=== ARTIFACTS ===
~/dev/lean_skillopt/runs/20261116-offline-lc-port/            the port's loop output + lean_job_66259448/
~/dev/lean_skillopt/runs/20261117-officeqa-api-smoke-66264849/ SMOKE.json, job.log, slurm.out, 3 predictions
~/dev/lean_skillopt/runs/20261116-160952_officeqa_arm_66228142/audit_evidence/  44 files, the three audits
docs/research/: OFFICEQA_API_COMPARISON_PLAN.md, OFFICEQA_LEARNED_CONTRACT_PORT.md, OFFICEQA_CONTRACT_AUDIT.md,
    OFFICEQA_REPORT_VALUE_PROBE.md, OFFICEQA_EPOCH2_REPORT_AUDIT.md, EXPERIMENT_LOG.md, DAILY_LOG.md, jobs.tsv
Coordination ledger last record: OQA-9:API-ACTOR-SMOKE-PASSED-66264849
```
