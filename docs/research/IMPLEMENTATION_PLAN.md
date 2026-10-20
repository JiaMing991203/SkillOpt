# Implementation Plan

Current milestone: **operational, reproducible original SkillOpt + ALFWorld
baseline on the cluster** (CLAUDE.md §9). No Lean work until the baseline and
representative traces have been inspected.

## Milestones

| # | Milestone | Status | Evidence / blocker |
|---|---|---|---|
| 0 | Repository + paper inspection | **Done** (2026-10-19) | `BASELINE_ARCHITECTURE.md` |
| 1 | Cluster environment inspection (login node, read-only) | **Done** except compute-node API check | `BASELINE_SETUP.md` §2 |
| 2 | Deployment workflow (deploy / submit / fetch, snapshots, manifests) | **Written; readiness check run; not yet deployed** | `research/`, `research/cluster/` |
| 2a | Commit + push branch to research fork | Done (see EXPERIMENT_LOG) | — |
| 2b | `deploy.sh` + `setup_env` job (new envs, model, ALFWorld data, data census) | **Done** | job 64323201 FAILED (PATH; fixed in `70de16f`); job 64323909 COMPLETED — see EXPERIMENT_LOG |
| 2c | `run_tests` job | **Done** | job 64326614: 104 passed |
| 2d | DeepSeek key on cluster + `check_compute` job | Needs user (key) + approval | — |
| 3a | `STAGE=vllm` smoke (Qwen endpoint, loopback check) | After 2b | — |
| 3b | `STAGE=rollout` smoke, 1 episode: validity + **throughput measurement** | After 2d, 3a | — |
| 3c | Estimate time/compute for training smoke from 3b; approve walltime | After 3b | — |
| 3d | `STAGE=train` smoke (one SkillOpt iteration + gate) | After 3c | — |
| 4 | Reproducibility review of a fetched run | After 3d | — |
| 5 | Decide paper-aligned full-baseline config (selection size, slow-update gating, reflection rounds, seeds, budget) | Not started | needs 2b census + 3d traces |

## Decisions

| Date | Decision | Why | Alternatives considered |
|---|---|---|---|
| 2026-10-19 | Slurm account chosen by user; stored only in private `site.env` | Fork is public; keep machine details out of git | — |
| 2026-10-19 | Split settings: private `site.env` (untracked) + `site.env.example` (tracked placeholders) + `experiment.env` (tracked model/serving settings); each run copies only `experiment.env` plus a sanitized manifest with a `site.env` SHA-256; a final guard fails the job if settings files or key values reach the run dir | Experiment-defining values must be versioned; machine details must not be public | single tracked file |
| 2026-10-19 | Two new envs (controller; vLLM with vllm==0.25.1) | Isolation from other projects' envs; 0.25.1 is known to support Qwen3.5 | reuse an existing env read-only |
| 2026-10-19 | Model cache = existing HF cache on project storage | User asked for models under project storage; home quota nearly full | per-project cache dir |
| 2026-10-19 | Controller + vLLM in one single-node GPU job; HTTP on 127.0.0.1, `VLLM_HOST_IP=127.0.0.1`, per-job key, listener check fails closed | CLAUDE.md §4; nodes are shared; vLLM's default rendezvous uses the node IP | separate server job |
| 2026-10-19 | Run SkillOpt from the snapshot via `PYTHONPATH`, not `pip install -e` | Env stays commit-independent; code identity = snapshot commit | editable install per snapshot |
| 2026-10-19 | Pin Qwen `thinking_mode: disabled`, temperature 0.7, max_tokens 8000 | Make target behaviour explicit; repo notes published Qwen numbers were non-thinking; prompt asks for `<think>` in content | server default (thinking on) |
| 2026-10-19 | `optimizer_openai_compatible_max_tokens: 16384` | Backend default 8000 silently caps SkillOpt's 16384 requests | keep 8000 |
| 2026-10-19 | Smoke iteration: 1 epoch × 1 step, 4 train tasks, full 18-task val gate, no test eval; **slow update / meta skill / reflection at upstream defaults** | Exercise every stage and the real gate without changing upstream behaviour; with `eval_test: false` the epoch-1 slow-update placeholder costs nothing | disable slow/meta (earlier draft) |
| 2026-10-19 | Measure throughput on 1 episode before submitting the training smoke | 2,000 target calls is the upper bound; walltime should come from data | submit with a guessed 2 h |
| 2026-10-19 | No source changes to SkillOpt; all additions are new files (+ 2 `.gitignore` lines) | Preserve baseline semantics | — |

## Open questions (decide before larger experiments)

1. **Selection split size** (18 shipped vs. 140 in paper): provenance traced in
   `BASELINE_ARCHITECTURE.md` §2a; the `setup_env` data census closes the
   remaining factual gaps. Options: keep 18 / materialize 140 from
   `valid_seen` / ask upstream.
2. **Slow-update gating:** paper says gated; code default force-accepts.
3. **Reflection rounds:** paper says up to 3; code does 1 (multi-round was
   never wired in). A paper-aligned mode would be new code.
4. **DeepSeek reasoning mode:** per provider docs, `deepseek-flash` thinks by
   default at effort `high`; the backend cannot send `extra_body`, so runs use
   that default, whereas the paper's optimizer used `medium`. Confirm with
   `check_compute`, then decide whether a small, flagged backend option is
   justified (it would apply equally to all comparison arms).
5. **Batch size:** default 40 > 39 train tasks.
6. **Seeds/repeats:** an 18-task gate is noisy; plan repeats.
7. **Fallback diagnostics:** for the Lean work, add opt-in per-step
   instrumentation (raw response, exception, retries, executed action), off
   for baseline runs.

## Next actions

1. User: create `$SECRETS_FILE` on the cluster (`BASELINE_SETUP.md` §4).
2. With approval: `deploy.sh`, then `setup_env` (CPU, ≤2 h), then `run_tests`, then `check_compute`.
3. With approval: `STAGE=vllm`, then `STAGE=rollout` (1 episode); then estimate the training-smoke budget.
