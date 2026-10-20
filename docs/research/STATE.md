# Research Development State

**Last verified:** 2026-10-20 16:10 EDT (git, `squeue`, `sacct`, fetched run)
**Current milestone:** review of the first original SkillOpt optimization
step (CLAUDE.md §9 items 6–7; `IMPLEMENTATION_PLAN.md` milestone 8).
**Status (verified):** the first training smoke (condition A, strict parser +
traces, job 64359024) **completed**: every loop stage ran; the candidate tied
the baseline at 12/18 and the gate **rejected** it. The one authorized
submission is used up. Nothing is running.

> Read this file first after a new session or any compaction, then verify it
> against git and Slurm (section B and section F) before acting. History lives
> in `DAILY_LOG.md`; measurements in `EXPERIMENT_LOG.md`.

## A. Current objective

Review the completed training smoke with the user, then decide the
paper-aligned baseline protocol (milestone 9). No experiment is authorized.

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

| Item | Value (verified 2026-10-20 13:11) |
|---|---|
| Local checkout | `~/dev/lean_skillopt/SkillOpt`, branch `research/alfworld-baseline` |
| Upstream base | `microsoft/SkillOpt` @ `1a44050` |
| Remote | `origin` = public research fork; `origin/research/alfworld-baseline` = local HEAD |
| Latest code commit | `12a153e` (strict+trace config, opt-in optimizer call log, config resolver); pushed |
| Latest deployed snapshot | `12a153e` (`$SNAPSHOT_ROOT/12a153e5ffa3`), used by tests 64358982 and training job 64359024 |
| Local docs | results recorded in local commits after `12a153e` (docs only; not pushed, push needs approval) |
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
- **Gate noise (interpretation):** one 18-task evaluation at temperature 0.7;
  a 1-task swap is 5.6 points. Run-to-run noise is unmeasured, so the tie
  (and the lower fallback/cap counts) say little about skill quality.
- **Credit assignment (interpretation):** the success analyst misattributed
  `train:0004`'s success (it ended on *taking* the statue under the lit lamp);
  the kept "Light before examine" edit omits holding the object.
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
| Run condition A (strict) + `trace_log` as the first training smoke | user-approved 2026-10-20; **done** (64359024) |
| `trace_log` and the optimizer call log may be on in any condition (diagnostic only) | verified in code and tests (64358982); per-run choice |

## F. Active operations and approvals

- **Active Slurm jobs:** none (`squeue` empty, 16:10). Last jobs: 64358982
  (tests, 195 passed), 64359024 (training smoke, COMPLETED 0:0, 2:34:00).
- **Authorized but not executed:** none. The one training submission
  approved on 2026-10-20 was used by 64359024.
- **Pending user decisions:** (1) push the local results/docs commits;
  (2) next experiment, if any (e.g. repeat runs to measure gate noise,
  condition B, the paper-aligned protocol questions in `IMPLEMENTATION_PLAN.md`).
- **Not authorized:** any new GPU job or paid API call, condition B
  training, additional iterations, held-out test evaluation, token-cap or
  workers experiments, two-replica serving, Lean/contract work.

## G. Next actions

1. Review job 64359024 with the user (milestone 8): edits, gate, traces,
   optimizer call log (`$LOCAL_RUNS/20261020-133347_smoke_train_64359024`).
2. Decide milestone 9 questions (selection size, slow-update gating,
   reflection rounds, reasoning effort, seeds/repeats, budget).
3. Later (non-blocking, user request): record pre-implementation research
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
