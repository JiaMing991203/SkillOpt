# Research Development State

**Last verified:** 2026-10-20 13:33 EDT (git, cluster snapshots, `squeue`, `sacct`)
**Current milestone:** first end-to-end original SkillOpt optimization step on
ALFWorld (CLAUDE.md §9, item 6).
**Status:** the first training smoke (condition A, strict parser + traces) is
**submitted as job 64359024** (user-authorized 2026-10-20, one submission).
Do not resubmit; check `sacct -j 64359024` first.

> Read this file first after a new session or any compaction, then verify it
> against git and Slurm (section B and section F) before acting. History lives
> in `DAILY_LOG.md`; measurements in `EXPERIMENT_LOG.md`.

## A. Current objective

Run **one** SkillOpt optimization step (rollout → reflection → edit → gate) to
verify the complete loop. A skill improvement is **not** required for the
smoke test to count as operational.

Configuration (user-approved 2026-10-20 for exactly one submission):

| Item | Planned value |
|---|---|
| Target | Qwen/Qwen3.5-4B @ `851bf6e8…`, vLLM 0.28.0, thinking disabled, temperature 0.7, max_tokens 8000, native sampler, prefix caching off |
| Optimizer | DeepSeek `deepseek-flash` (pinned in `research/cluster/experiment.env`) |
| Reasoning parser | **strict** (condition A, upstream). `qwen_compat` (condition B) is prepared as a separate condition, not authorized |
| Config | `configs/research/alfworld_smoke_strict_traced.yaml` = `alfworld_smoke.yaml` + `trace_log: true` (resolved diff verified, job 64358982): 1 epoch × 1 step, 4 train tasks (`train:0017, 0036, 0004, 0013` at seed 42), full 18-task selection gate (`val:0000`–`val:0017`), workers 8, patch mode, edit budget 4, slow update / meta at upstream defaults, `eval_test: false` |
| Diagnostics | `optimizer_calls.jsonl` (opt-in backend call log: prompts, replies, usage, errors), `OPENAI_LOG=debug` SDK HTTP lines in `job.log`, `trace.json` per episode. None affect requests, actions or scores (tests in 64358982) |
| Resources | 1 A40, 4 CPUs, 48 GB (one spgpu billing unit), 5 h limit |
| No Lean | Lean / contract work has not started and must not start yet |

The run plan, budgets and retry/cost bounds are in `IMPLEMENTATION_PLAN.md`
("Next experiment").

## B. Repository and environment

| Item | Value (verified 2026-10-20 13:11) |
|---|---|
| Local checkout | `~/dev/lean_skillopt/SkillOpt`, branch `research/alfworld-baseline` |
| Upstream base | `microsoft/SkillOpt` @ `1a44050` |
| Remote | `origin` = public research fork; `origin/research/alfworld-baseline` = local HEAD |
| Latest code commit | `12a153e` (strict+trace config, opt-in optimizer call log, config resolver); pushed |
| Latest deployed snapshot | `12a153e` (`$SNAPSHOT_ROOT/12a153e5ffa3`), used by tests 64358982 and training job 64359024. Do not modify it while the job runs |
| Cluster envs | controller `skillopt`; vLLM `skillopt-vllm-0.28.0` (in use); `skillopt-vllm` (0.25.1, preserved, unused) |
| Data / model | ALFWorld `json_2.1.1` in `$ALFWORLD_DATA`; Qwen weights in `$HF_HOME` (2/2 shards verified) |
| Private settings | `research/cluster/site.env` (git-ignored; template `site.env.example`); DeepSeek key only in `$SECRETS_FILE` on the cluster |

Workflow: edit locally → commit → (approved) push → `research/deploy.sh` →
`research/submit.sh <job>` → `research/fetch_run.sh <jobid>`
(`BASELINE_SETUP.md` §3). Every submission is appended to `docs/research/jobs.tsv`.

## C. Verified accomplishments (evidence: `EXPERIMENT_LOG.md`)

- Upstream code and paper compared; differences documented (`BASELINE_ARCHITECTURE.md` §8).
- Cluster envs installed (`setup_env` 64323909, `setup_vllm_env` 64332646); CPU test suite **195 passed** (64358982, commit `12a153e`).
- DeepSeek works **through SkillOpt's backend** from a compute node (64328962). Thinking is on by default; reasoning is excluded from SkillOpt's text.
- Qwen serving on 1 A40 with vLLM 0.28.0: file-based rendezvous, every vLLM listener on loopback, external probe refused (64332934).
- First ALFWorld episode solved in 3 actions (64333791); 8 fixed train tasks: **4/8 successes**, traces consistent (64337548).
- Opt-in `env.reasoning_parser` (strict | qwen_compat) and `env.trace_log` implemented and tested; defaults equal upstream (`8f739f7`; `BASELINE_ARCHITECTURE.md` §2e).
- Offline analysis: runaway 8000-token generations under the lockstep rollout caused most of the 31-minute rollout (64351263).
- Run manifests sanitized; `billing.json` and `resource_usage.csv` recorded per run.

Cumulative compute so far: GPU ≈ 1.03 physical GPU-h, ≈ 1.47 billable
GPU-equivalent h (≈ $0.16); DeepSeek: 2 paid calls (≈ $0.00007).

## D. Current issues and uncertainties

- **Reasoning extraction:** strict parsing drops Qwen's untagged explanations,
  so the optimizer sees actions and observations only. `qwen_compat` recovers
  them but makes analyst prompts ~10× larger (4-failure minibatch ~8–11k →
  ~84–112k tokens). Effect on optimization quality: **untested**.
- **Long generations:** 7/277 replies hit the 8000-token cap (5 became
  `look` fallbacks). Together with lockstep stepping they dominate runtime.
  Changing the cap or `env.workers` would be a new, separately labeled condition.
- **Runtime estimate for the smoke:** ~1.5–3.3 h (a planning estimate); a 2 h
  allocation is likely too short.
- **SkillOpt saves no optimizer prompts or raw replies** and drops unparsable
  replies silently (verified in code). The opt-in call log in `12a153e` closes
  this for our runs; upstream behavior is unchanged when it is off.
- **Methodology:** the repo's selection split has 18 tasks; the paper reports
  140 (all of `valid_seen`, an **inference**). Slow-update gating, reflection
  rounds (paper 3 vs code 1) and DeepSeek reasoning effort (provider default
  "high" vs paper "medium") are open (`IMPLEMENTATION_PLAN.md`).

## E. Standing decisions (current; full rationale in `DAILY_LOG.md`)

| Decision | Status |
|---|---|
| vLLM **0.28.0** in its own env (file:// rendezvous); prefix caching pinned off; native sampler; `GLOO_SOCKET_IFNAME=lo`; private 0700 `TMPDIR` | approved; applies to all arms |
| Network guard: HTTP API and all vLLM listeners must be loopback; a wildcard or node-IP listener stops agent stages; no whitelisting of unknown listeners | approved (user) |
| GPU requests: **1 A40 = 4 CPUs = 48 GB** (spgpu bills the max of weighted resources) | approved (user) |
| Keep Qwen checkpoint, generation settings, 8000-token cap, `env.workers` 8, evaluator, skill-update rules, selection split unchanged for the smoke | approved (user) |
| Conditions kept separate: **A** strict upstream, **B** + `qwen_compat`, **C** future Python/Lean contract feedback. B's gains are never credited to C | approved (user) |
| Run condition A (strict) + `trace_log` as the first training smoke | **user-approved 2026-10-20** (one submission; 64359024) |
| `trace_log` and the optimizer call log may be on in any condition (diagnostic only) | verified in code and tests (64358982); per-run choice |

## F. Active operations and approvals

- **Active Slurm job:** **64359024** (`smoke`, STAGE=train, commit `12a153e`,
  `TRAIN_CONFIG=configs/research/alfworld_smoke_strict_traced.yaml`,
  1 A40 / 4 CPUs / 48 GB / 5 h, submitted 13:31 EDT; PENDING at last check).
  Run dir: `$RUN_ROOT/<timestamp>_smoke_train_64359024`.
  Status: `ssh gl sacct -j 64359024 -X -o State,Elapsed`; log: `<run dir>/job.log`.
  Expected outputs: `skillopt/{selection_eval_baseline,steps/step_0001,skills,history.json,summary.json}`,
  `optimizer_calls.jsonl`, `optimizer_calls_summary.json`, `trace_check_*.json`, `run_summary.json`, `billing.json`.
- **User authorization (2026-10-20, from the user's message):** exactly
  **one** strict-parser training smoke as configured in section A. On failure:
  preserve evidence and stop; no automatic retry, no setting changes.
- **Not authorized:** any resubmission, condition B (`qwen_compat`) training,
  additional iterations, held-out test evaluation, 8000-token-cap or workers
  experiments, two-replica serving, Lean/contract work.

## G. Next actions

1. Monitor 64359024 to a terminal state (`sacct`), then `research/fetch_run.sh 64359024`.
2. Inspect: baseline and candidate selection scores, `patches/`, `merged_patch.json`,
   `ranked_edits.json` (if any), `candidate_skill.md`, `edit_apply_report.json`,
   `step_record.json` (gate action), `optimizer_calls_summary.json` (attempts,
   parse outcomes, tokens, cost), `trace_check_*.json`, `billing.json`.
3. Update `EXPERIMENT_LOG.md`, `DAILY_LOG.md`, this file; report to the user.
4. Review results with the user before any further experiment.
5. Later (non-blocking, user request): record pre-implementation research
   decisions, incl. the rationale for independently specified task contracts (Option B).

## H. Evidence and references

`EXPERIMENT_LOG.md` (all jobs and measurements) · `DAILY_LOG.md` (history and
reasons) · `IMPLEMENTATION_PLAN.md` (milestones, next experiment, deferred
work) · `BASELINE_ARCHITECTURE.md` (code paths, conditions A/B/C) ·
`BASELINE_SETUP.md` (workflow, billing, networking) · `jobs.tsv` (submissions)
· fetched runs in `$LOCAL_RUNS` (outside git).

## I. Handoff warnings

- Do not start Lean/contract integration before the baseline step has been
  inspected with the user.
- Do not resubmit failed or finished GPU jobs automatically; check `jobs.tsv`
  and `sacct` first.
- Do not change model, sampling, token cap, workers, evaluator, skill-update
  rules or splits without an explicitly approved new condition.
- Do not confuse source-code edits (ours) with SkillOpt's skill edits (DeepSeek's).
- `research/cluster/site.env`, secrets and run directories (which contain
  absolute cluster paths) never go into git; the fork is public.
- Earlier approvals cover only their original scope; a compaction summary
  never authorizes jobs, paid API calls or pushes.
