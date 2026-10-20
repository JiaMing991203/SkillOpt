# Research Development State

**Last verified:** 2026-10-20 16:46 EDT (git, `squeue`, fetched run, offline analyses)
**Current milestone:** decide the full-baseline protocol, starting with
evaluation noise (`IMPLEMENTATION_PLAN.md` milestone 9). Milestone 8 (review of
the training smoke) is done offline.
**Status (verified):** the first training smoke (condition A, strict parser +
traces, job 64359024) **completed**: every loop stage ran; the candidate tied
the baseline at 12/18 and the gate **rejected** it. The user accepted it as a
successful engineering milestone (2026-10-20). Nothing is running and **no GPU
job or paid API call is authorized**.

> Read this file first after a new session or any compaction, then verify it
> against git and Slurm (section B and section F) before acting. History lives
> in `DAILY_LOG.md`; measurements in `EXPERIMENT_LOG.md`.

## A. Current objective

Decide how to measure and handle evaluation noise before any further
optimization run (proposal E1 in `IMPLEMENTATION_PLAN.md`), then the rest of
the baseline protocol. No experiment is authorized.

Result of job 64359024 (verified; details in `EXPERIMENT_LOG.md`):

| Item | Value |
|---|---|
| Config | `configs/research/alfworld_smoke_strict_traced.yaml` (= strict smoke + `trace_log: true`), commit `12a153e` |
| Baseline selection (initial skill) | 12/18 (0.667) |
| Train batch `train:0017, 0036, 0004, 0013` | 1/4 |
| DeepSeek | 4 calls (2 analysts, merge, ranking), 0 retries, 0 parse failures; 6 edits proposed → 4 kept |
| Candidate selection | 12/18 (0.667); +`val:0012`, −`val:0014` |
| Gate | **reject** (tie; strict `>` rule) — `best_skill.md` = initial skill |
| Cost | 2.57 h on one A40 unit ≈ $0.28; DeepSeek ≈ $0.008 |

## B. Repository and environment

| Item | Value (verified 2026-10-20 16:46) |
|---|---|
| Local checkout | `~/dev/lean_skillopt/SkillOpt`, branch `research/alfworld-baseline` |
| Upstream base | `microsoft/SkillOpt` @ `1a44050` |
| Remote | `origin` = public research fork; `origin/research/alfworld-baseline` = local HEAD |
| Latest code commit | `12a153e` (strict+trace config, opt-in optimizer call log, config resolver); pushed |
| Latest deployed snapshot | `12a153e` (`$SNAPSHOT_ROOT/12a153e5ffa3`), used by tests 64358982 and training job 64359024 |
| Docs commits | `79d7a59`, `8db42fa` pushed (2026-10-20, approved); offline-review docs commit is local only (push needs approval) |
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
- **One complete original SkillOpt step** incl. the gate (64359024); optimizer call log, SDK request lines and traces all consistent.
- Run manifests sanitized; `billing.json` and `resource_usage.csv` recorded per run.

Cumulative compute so far: GPU ≈ 3.60 physical GPU-h, ≈ 4.04 billable
GPU-equivalent h (≈ $0.44); DeepSeek: 8 paid calls (≈ $0.008).

## D. Current issues and uncertainties

- **Reasoning extraction:** strict parsing drops Qwen's untagged explanations,
  so the optimizer sees actions and observations only. `qwen_compat` recovers
  them but makes analyst prompts ~10× larger (4-failure minibatch ~8–11k →
  ~84–112k tokens). Effect on optimization quality: **untested**.
- **Long generations:** 7/277 replies hit the 8000-token cap (5 became
  `look` fallbacks). Together with lockstep stepping they dominate runtime.
  Changing the cap or `env.workers` would be a new, separately labeled condition.
- **Gate noise:** verified that baseline and candidate share tasks, order,
  env seeds and deterministic games; only target sampling varies (global vLLM
  generator, no per-request seed), and the gate compares single runs with a
  strict `>` (`BASELINE_ARCHITECTURE.md` §2g). Same skill + same game already
  produced different trajectories. Flip rates are **unmeasured** (E1).
  For identical skills P(accept) = (1 − P(tie))/2, so even one coin-flip task
  makes false accepts common (interpretation).
- **Credit assignment (verified from the PDDL goal and DeepSeek's reply):**
  `train:0004` required lamp toggled + agent at the lamp's receptacle +
  holding a statue, in one state; no `examine`, no order. DeepSeek claimed
  examining was sufficient and taking optional; the kept edit omits holding.
  The prompt gave no goal definition and no success step
  (`EXPERIMENT_LOG.md`, offline analyses).
- **Trace field caveat (verified):** `env_is_action_valid` is a `<think>`-tag
  format flag (0 on nearly all steps), not action validity; checkers must use
  `executed_action_admissible`.
- **Call-log residual risk (interpretation):** provider error messages are
  stored on failed attempts and might echo a masked key fragment; proposed
  redaction not implemented.
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
| Conditions kept separate: **A** strict-parser baseline, **B** Qwen-compatible parser, **C** independently specified Python-contract feedback, **D** equivalent Lean-contract feedback. B–D are not validated conditions; parser or explanation-text gains are never credited to verification | user decision 2026-10-20 |
| Contracts are derived from the benchmark's trusted specification (PDDL goal), not from the skill | user direction 2026-10-20 |
| Run condition A (strict) + `trace_log` as the first training smoke | user-approved 2026-10-20; **done** (64359024) |
| `trace_log` and the optimizer call log may be on in any condition (diagnostic only) | verified in code and tests (64358982); per-run choice |

## F. Active operations and approvals

- **Active Slurm jobs:** none (`squeue` empty, 16:46). Last jobs: 64358982
  (tests), 64359024 (training smoke, COMPLETED).
- **Authorized but not executed:** none.
- **Done with approval (2026-10-20):** pushed docs commits `79d7a59`,
  `8db42fa` after a scope and secrets review.
- **Pending user decisions:** (1) push the local docs commit(s) from the
  offline review; (2) E1 evaluation-noise jobs (3 × 18-episode rollouts of the
  initial skill, ≈ $0.33–0.46, no API); (3) E3 optimizer replay pilot (paid,
  ≈ $0.05); (4) call-log redaction hardening (code change, needs CPU tests).
- **Not authorized:** any GPU job or paid API call, new optimization runs,
  changes to experimental semantics, condition B/C/D runs, Lean work.

## G. Next actions

1. Discuss the offline findings and proposals E1–E3 with the user.
2. If E1 is approved: submit the three rollout jobs exactly as written in
   `IMPLEMENTATION_PLAN.md`, then analyze per-task flip rates.
3. Use E1 to decide the acceptance/assessment procedure (E2) for the
   C-versus-D comparison.
4. Later (non-blocking): record pre-implementation research decisions,
   incl. the rationale for independent task contracts (Option B).

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
