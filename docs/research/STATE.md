# Research Development State

**Last verified:** 2026-10-20 13:11 EDT (git, cluster snapshots, `squeue`, `sacct`), before a manual `/compact`
**Current milestone:** first end-to-end original SkillOpt optimization step on
ALFWorld (CLAUDE.md §9, item 6).
**Status:** inference and benchmark-rollout infrastructure verified on the
cluster. The first training smoke is prepared but **not submitted** and **not
yet authorized**.

> Read this file first after a new session or any compaction, then verify it
> against git and Slurm (section B and section F) before acting. History lives
> in `DAILY_LOG.md`; measurements in `EXPERIMENT_LOG.md`.

## A. Current objective

Run **one** SkillOpt optimization step (rollout → reflection → edit → gate) to
verify the complete loop. A skill improvement is **not** required for the
smoke test to count as operational.

Planned configuration (direction from the user, 2026-10-20; submission still
needs explicit approval):

| Item | Planned value |
|---|---|
| Target | Qwen/Qwen3.5-4B @ `851bf6e8…`, vLLM 0.28.0, thinking disabled, temperature 0.7, max_tokens 8000, native sampler, prefix caching off |
| Optimizer | DeepSeek `deepseek-flash` (pinned in `research/cluster/experiment.env`) |
| Reasoning parser | **strict** (condition A, upstream). `qwen_compat` (condition B) is prepared as a separate condition |
| Config | `configs/research/alfworld_smoke.yaml`: 1 epoch × 1 step, 4 train tasks (`train:0017, 0036, 0004, 0013` at seed 42), full 18-task selection gate, upstream workers (8), patch mode, edit budget 4, slow update / meta at upstream defaults, `eval_test: false` |
| Resources | 1 A40, 4 CPUs, 48 GB (one spgpu billing unit), proposed 5 h limit |
| No Lean | Lean / contract work has not started and must not start yet |

The run plan, budgets and retry/cost bounds are in `IMPLEMENTATION_PLAN.md`
("Next experiment").

## B. Repository and environment

| Item | Value (verified 2026-10-20 13:11) |
|---|---|
| Local checkout | `~/dev/lean_skillopt/SkillOpt`, branch `research/alfworld-baseline` |
| Upstream base | `microsoft/SkillOpt` @ `1a44050` |
| Remote | `origin` = public research fork; `origin/research/alfworld-baseline` = local HEAD |
| Latest code commit | `7e78fb3` (analysis tooling, Qwen-compat smoke config) |
| Latest commits | `e998b9f` (pins `OPTIMIZER_MODEL=deepseek-flash` in `experiment.env`); `d319636` (STATE/DAILY_LOG docs system); this pre-compact refresh. Working tree clean; HEAD pushed |
| Latest deployed snapshot | `7e78fb3` (`$SNAPSHOT_ROOT/7e78fb3321b7`). **The next GPU job must deploy the current HEAD first** (`research/deploy.sh`), because `experiment.env` changed after `7e78fb3` |
| Cluster envs | controller `skillopt`; vLLM `skillopt-vllm-0.28.0` (in use); `skillopt-vllm` (0.25.1, preserved, unused) |
| Data / model | ALFWorld `json_2.1.1` in `$ALFWORLD_DATA`; Qwen weights in `$HF_HOME` (2/2 shards verified) |
| Private settings | `research/cluster/site.env` (git-ignored; template `site.env.example`); DeepSeek key only in `$SECRETS_FILE` on the cluster |

Workflow: edit locally → commit → (approved) push → `research/deploy.sh` →
`research/submit.sh <job>` → `research/fetch_run.sh <jobid>`
(`BASELINE_SETUP.md` §3). Every submission is appended to `docs/research/jobs.tsv`.

## C. Verified accomplishments (evidence: `EXPERIMENT_LOG.md`)

- Upstream code and paper compared; differences documented (`BASELINE_ARCHITECTURE.md` §8).
- Cluster envs installed (`setup_env` 64323909, `setup_vllm_env` 64332646); CPU test suite **185 passed** (64351262).
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
- **Trace logging for the strict smoke:** `alfworld_smoke.yaml` has
  `trace_log: false`. Traces are verified not to affect optimization, but
  enabling them for condition A needs a small strict+trace config (not created; user decision).
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
| Run condition A (strict) as the first training smoke | user direction 2026-10-20; **not yet authorized to submit** |
| `trace_log` may be on in any condition (diagnostic only) | verified in code and tests; per-run choice |

## F. Active operations and approvals

- **Active Slurm jobs:** none (`squeue` empty). All 20 jobs in
  `docs/research/jobs.tsv` have finished; outcomes are in `EXPERIMENT_LOG.md`.
- **Pending user decisions / approvals:**
  1. Authorization to submit the strict training smoke (1 A40, 4 CPUs, 48 GB, ≤ 5 h, DeepSeek estimate in `IMPLEMENTATION_PLAN.md`).
  2. Whether condition A runs with `trace_log: true` (needs a strict+trace config).
- **Authorized but not executed:** none.
- **Not authorized:** condition B training run, 8000-token-cap or workers
  experiments, two-replica serving, Lean/contract work.

## Session handoff (pre-compact, 2026-10-20 13:11)

- No operation is in progress; nothing is running on the cluster; nothing
  is authorized but unexecuted.
- The user said they will send a new prompt about the **training smoke**
  after this compaction. Treat that prompt as the source of authorization
  and scope. Nothing in this file authorizes a submission.
- Open questions to settle from that prompt: submit or not, the walltime
  (proposed 5 h), and strict + `trace_log` (needs a small new config) versus
  strict without traces.

## G. Next actions

1. On resumption: run `git status`, `git log -3`, `squeue -u $USER` and
   `tail docs/research/jobs.tsv`, and compare with sections B and F.
2. Obtain the user's decision on items F.1–F.2.
3. If authorized: `research/deploy.sh` (HEAD) →
   `STAGE=train research/submit.sh smoke HEAD -- --time=05:00:00` (add
   `TRAIN_CONFIG=…` only for an approved non-default config) → monitor →
   `research/fetch_run.sh <id>`.
4. Preserve and inspect: trajectories, reflection inputs, `patches/`,
   `merged_patch.json`, `ranked_edits.json`, candidate skill, gate decision
   (`step_record.json`), token usage (`summary.json`), `billing.json`.
5. Review results with the user before any further experiment.

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
