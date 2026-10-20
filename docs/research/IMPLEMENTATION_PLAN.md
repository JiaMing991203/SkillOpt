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
| 2d | DeepSeek key on cluster + `check_compute` job | **Done** | job 64328962 PASS via SkillOpt backend |
| 3a | `STAGE=vllm` smoke (Qwen endpoint, loopback check) | **Done (vLLM 0.28.0, job 64332934)**; history: | 64329002 FAILED (FlashInfer needs `nvcc`; fixed with native sampler). 64330104: server ready in 230 s with native sampler, then FAILED the fail-closed listener check (torch Gloo/TCPStore sockets on node IP / 0.0.0.0). 64331018: Gloo moved to loopback; only torch's rendezvous TCPStore (0.0.0.0) remains, reachable from a login node → status unresolved, rollout not run. Decision needed (see EXPERIMENT_LOG) |
| 3b | `STAGE=rollout` smoke, 1 episode: validity + **throughput measurement** | **Done** | job 64333791: success in 3 actions, 4.1 s/request; see EXPERIMENT_LOG |
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
| 2026-10-20 | vLLM serves with `VLLM_USE_FLASHINFER_SAMPLER=0` (native sampler), verified at startup, recorded in manifests, shared by all arms | FlashInfer sampler JIT needs `nvcc` (job 64329002); user-approved | `module load cuda/<ver>` (keeps FlashInfer, needs CUDA version choice) |
| 2026-10-20 | Opt-in `env.reasoning_parser` (strict \| qwen_compat) and `env.trace_log` in the ALFWorld adapter; defaults = upstream; conditions A/B/C kept separate (BASELINE_ARCHITECTURE §2e–2f) | Recover the lost reasoning channel as a labeled condition; capture the observations and admissible actions needed for independent contracts | change the parser for everyone (rejected: would silently alter the baseline) |
| 2026-10-20 | Move serving to **vLLM 0.28.0** in a new env (0.25.1 env preserved); prefix caching pinned off; private 0700 `TMPDIR` for vLLM; startup checks for rendezvous method, prefix caching and sampling defaults | 0.28.0 removes the wildcard TCPStore (PR #50999); keep generation settings at parity with 0.25.1 | patch vLLM to use file:// (rejected: no custom patches); accept the TCPStore exception (rejected) |
| 2026-10-20 | vLLM gets `GLOO_SOCKET_IFNAME=lo`; listener policy pass/unresolved/fail; rendezvous TCPStore wildcard is *not* auto-allowed | Keep the HTTP API and all other sockets strictly loopback; investigate TCPStore before any exception (user decision) | relax to HTTP-only check (rejected) |
| 2026-10-20 | Project variables renamed `LSO_VLLM_*` | `VLLM_*` is vLLM's namespace; `VLLM_PORT` collided with a real vLLM setting | — |
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
7. **DeepSeek spend bound:** the training-smoke cost (~$0.50 worst case) is a
   planning estimate until per-call token limits and all retry layers are
   verified in code and in a real run's token log.
8. **Fallback diagnostics:** for the Lean work, add opt-in per-step
   instrumentation (raw response, exception, retries, executed action), off
   for baseline runs.

## Overnight run 2026-10-20 (autonomous, user-authorized)

Authorized sequence, each step gated on the previous one passing both Slurm
completion and application-level checks; at most one submission per step:

| Step | Job | Gate to proceed |
|---|---|---|
| check_compute | 64328962 (done, PASS) | preflight PASS via SkillOpt backend |
| STAGE=vllm | 64329002 — **FAILED** (FlashInfer sampler JIT needs `nvcc`; see EXPERIMENT_LOG) | preflight target PASS (`<action>` returned), loopback-only listeners, model loaded |
| STAGE=rollout, 1 episode | not submitted (blocked) | episode ran 1+ steps; 0 call_error / empty fallbacks; HTTP 200s only |
| STAGE=rollout, 8 episodes (concurrency) | not submitted (blocked) | same checks; throughput measured |
| STAGE=train, smoke config | not submitted (blocked; one submission still unused) | estimate fits a 2 h allocation with margin; DeepSeek cost bounded |

**Recovery if the session ends:** submitted job ids are in
`docs/research/jobs.tsv`; check `ssh <host> 'sacct -j <id>'`, then
`research/fetch_run.sh <id>` and read `run_summary.json` / `preflight.json`.
Do not resubmit a stage that already has a job id; continue only from the
first stage without a COMPLETED + PASS result. All runs use snapshot
`d129260` (no code changes deployed during the sequence).

## Overnight run 2 — vLLM 0.28.0 (2026-10-20, autonomous, user-authorized)

All jobs from snapshot `3dfaff9` unless noted. Each step requires Slurm
COMPLETED **and** its application-level checks; at most one GPU job per step,
no automatic retries.

| Step | Job | Gate to proceed |
|---|---|---|
| setup_vllm_env (CPU) | 64332646 — **COMPLETED, checks true** | `vllm_env_check.json` required checks true; serve flags accepted |
| run_tests (CPU) | 64332647 — **114 passed** | all pass |
| STAGE=vllm (1 A40, ≤1 h) | 64332934 — **PASS** (file rendezvous; all listeners loopback; external probe all refused) | rendezvous `file`, network `pass`, native sampler, prefix caching off, no sampling override, preflight `<action>` PASS |
| STAGE=rollout, 1 episode (1 A40, ≤1 h) | 64333791 — **COMPLETED**; `val:0000` solved in 3 actions; 0 fallbacks; reasoning null on all steps | serving/parser checks; task success is not required |

**Recovery:** job ids are in `docs/research/jobs.tsv`. For each, run
`ssh <host> 'sacct -j <id>'`, then `research/fetch_run.sh <id>` and read the
run dir JSON files named above. Never resubmit a step that already has a job
id. Continue only from the first step without COMPLETED + checks passed.

## Open questions added 2026-10-20 (from the first episode)

9. **Reasoning channel:** Qwen (non-thinking) writes reasoning without
   `<think>` tags, so SkillOpt's reflection receives no agent reasoning.
   Decide before training: keep as the upstream-faithful condition and
   document it, or test a variant (prompt/parser/thinking mode) as a
   separate, explicitly labeled condition applied to all arms.
10. **Trace fields for verification:** add opt-in logging of the initial
    observation, per-step prompt and admissible actions, executed action and
    validity flag (default off).
11. **Throughput for the training budget:** 1 episode gives 4.1 s/request at
    concurrency 1 with ~9% mean GPU use. An 8-episode test (needs approval)
    would measure concurrent throughput before planning training.

## Prepared, not submitted: 8-episode throughput test (needs approval)

- Command: `STAGE=rollout ROLLOUT_EPISODES=8 ROLLOUT_SPLIT=valid_seen
  ROLLOUT_CFG_OPTIONS="env.trace_log=true" research/submit.sh smoke`
- Tasks: `val:0000`–`val:0007` (first 8 of the selection split in file order:
  3 look_at_obj_in_light, 3 pick_and_place_simple, 2
  pick_clean_then_place_in_recep). Evaluation only, no optimization, held-out
  test untouched.
- Model/serving: as in job 64333791 (Qwen3.5-4B @ 851bf6e, vLLM 0.28.0,
  native sampler, prefix caching off, thinking disabled, temperature 0.7,
  max_tokens 8000, initial skill, max_steps 50); `reasoning_parser: strict`
  (compat comparison done offline from the saved raw replies);
  `trace_log: true`.
- Concurrency: `env.workers` 8 (one chunk of 8 simulators), `max_api_workers`
  8 (up to 8 concurrent requests, lockstep per step).
- Resources: 1 A40, 8 CPUs, 64 GB, 1 h limit; expected 10–15 min.
- Cost: ≤ ~0.25 A40-hours; no DeepSeek calls.

## Prepared, not submitted: first training smoke (condition B, Qwen-compatible baseline)

- Command (from the next deployed commit):
  `STAGE=train TRAIN_CONFIG=configs/research/alfworld_smoke_qwen_compat.yaml research/submit.sh smoke HEAD -- --time=05:00:00`
- Config: `alfworld_smoke_qwen_compat.yaml` = strict smoke + `reasoning_parser:
  qwen_compat` + `trace_log: true` (test asserts no other difference).
  Qwen3.5-4B @ 851bf6e, thinking off, temperature 0.7, max_tokens 8000;
  optimizer `deepseek-flash` (pinned in experiment.env); 1 epoch × 1 step, 4
  train tasks (`train:0017, 0036, 0004, 0013`), full 18-task selection gate,
  upstream workers (8), edit budget 4, patch mode, slow update / meta at
  upstream defaults (no calls at epoch 1), `eval_test: false`.
- Resources: 1 A40, 4 CPUs, 48 GB (1 billing unit), 5 h limit.
- GPU-time **estimate**: vLLM startup ~4 min; selection baseline 18 episodes
  (chunks 8/8/2) ~35–80 min; train 4 ~10–30 min; optimizer ~2–6 min;
  candidate selection 18 ~35–80 min (0 on a skill-hash cache hit or with no
  patches). Total **~1.5–3.3 h**; billable ≤ 5 h × $0.108 = **≤ $0.54**,
  expected ~$0.20–0.36.
- DeepSeek **estimate** (not a cap): at most 4 optimizer call sites in one
  step (failure analyst, success analyst, final merge if both produce edits,
  ranking if > 4 merged edits; one patch per group, so no group merges).
  Inputs: failure analyst up to ~112k tok (observed worst-like), success
  ~35k, merge/ranking ~5k. Output ≤ 16,384 per call (thinking counts).
  Per attempt at peak ≈ $0.054 / $0.030 / $0.022 / $0.022, so **≈ $0.13 for
  one full step if every output hit the cap**; expected ~$0.03–0.08. Plus 2
  tiny preflight calls (~$0.0001).
- Retry layers (verified in code): call sites pass `retries=3` to the
  backend loop, and the OpenAI SDK client (no `max_retries` set) adds up to 2
  retries for connection, 429 and 5xx errors. That is ≤ 9 HTTP requests per
  call site and ≤ 36 per step. Failed requests are normally not billed. A
  parse failure is **not** retried (the step gets no patch). Worst case if
  every request were billed at full size and peak price: 36 × $0.054 ≈ $1.9
  (not expected). There is no API-side spend cap; after the run we check
  `summary.json → token_summary` and `step_record.json → tokens`.
- Context: largest observed-like analyst prompt ~112k tok ≪ 1M (DeepSeek).
  The theoretical compat worst case (many 8000-token replies with an action)
  could exceed the context and fail the call (no patch, not billed).

## Training-smoke budget (planning estimate from job 64337548; needs a decision)

With upstream `env.workers: 8`, an 8-episode chunk took ~31 min, driven by
runaway 8000-token generations under the lockstep rollout. The smoke step
runs 18 selection (3 chunks) + 4 train (1 chunk) + up to 18 selection
episodes for the candidate (3 chunks, skipped on a cache hit). That is
roughly **2.5–3.5 h**, plus ~4 min vLLM startup, ~90 s env build per chunk
and the optimizer calls. **A 2 h allocation is likely insufficient.**
Options:
1. Longer walltime (e.g. 5 h at 1 billing unit ≈ $0.54 worst case). No
   semantic change.
2. Raise `env.workers` so all 18 selection episodes share one chunk. This
   changes upstream parallelism and per-chunk env seeds; the effect on
   outcomes is unverified.
3. Lower the target `max_tokens` (now 8000 from the qwen_chat backend cap).
   This is a generation-setting change and would be a new condition.
4. Two vLLM replicas (below).

## Possible optimization (not implemented): two vLLM replicas

When a job already pays for two GPU billing units (for example because it
needs more than 4 CPUs or 48 GB), a second A40 costs nothing extra per hour.
Two independent single-GPU vLLM servers, each with its own private temp dir,
loopback port and key, could roughly double rollout throughput. Open points:
- Distributing requests: SkillOpt's `qwen_chat` backend takes one base URL.
  It would need a local round-robin proxy (an extra loopback listener that the
  network guard must allow explicitly) or a backend change (not
  upstream-faithful).
- vLLM's built-in data parallelism is not an alternative here: its
  data-parallel path still uses TCP rendezvous, which brings back the
  listener problem.
- Benefit depends on GPU saturation. The first episode used ~9% mean GPU
  utilization at concurrency 1, so first measure 8-way concurrency on one GPU.

## Next actions

1. User: create `$SECRETS_FILE` on the cluster (`BASELINE_SETUP.md` §4).
2. With approval: `deploy.sh`, then `setup_env` (CPU, ≤2 h), then `run_tests`, then `check_compute`.
3. With approval: `STAGE=vllm`, then `STAGE=rollout` (1 episode); then estimate the training-smoke budget.
