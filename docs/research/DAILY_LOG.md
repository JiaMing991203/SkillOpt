# Research Development Log

Chronological journal of meaningful development: what we tried, why, what the
evidence showed, what went wrong, what we decided and what changed in our
understanding. Detailed measurements live in `EXPERIMENT_LOG.md`; the current
state lives in `STATE.md`. Entries are appended. Later corrections are new
entries that point back, never silent rewrites.

Labels: **Finding** (verified evidence) · **Interpretation** · **Recommendation**
(agent) · **User decision** (approved by the researcher) · **Proposal** (not yet
approved) · **Open question**.

History before 2026-10-19 (e.g. when the research idea and CLAUDE.md were
first drafted) is not recorded in the repository. The entries below are
reconstructed from git commits, `docs/research/jobs.tsv`, `EXPERIMENT_LOG.md`
and fetched run artifacts. Times are local (EDT) unless marked UTC.

---

## 2026-10-19 — Repository inspection, cluster inspection, workflow design

### Objective
Understand the checked-out SkillOpt (upstream `1a44050`, v0.2.0) and the
paper, inspect Great Lakes without changing anything, and design a
local-development / cluster-execution workflow.

### Work completed
- Read the training loop, ALFWorld adapter and rollout, backends and gate,
  and compared them with the paper (`BASELINE_ARCHITECTURE.md`; two
  read-only subagents helped map the trainer and the paper).
- Read-only cluster inspection: Slurm accounts and partitions, A40 nodes,
  conda, CUDA modules, existing envs, storage, outbound API reachability.
- Wrote the deployment tooling (`research/deploy.sh`, `submit.sh`,
  `fetch_run.sh`, Slurm jobs, manifests) and the research configs. First
  commit `258a19d`.

### Key findings
- **Finding:** DeepSeek can run through the existing `openai_compatible`
  backend and Qwen through `qwen_chat`, with backends chosen per role. No
  SkillOpt code change is needed.
- **Finding:** the gate accepts only a strictly higher selection score; ties
  are rejected.
- **Finding:** failed or untagged target replies are silently replaced by the
  action `look`, which hides endpoint failures. This led to
  `summarize_run.py` fallback counting.
- **Finding:** the repo's selection split has 18 games where the paper
  reports 140. The paper also describes three reflection rounds (the code
  does one) and a gated slow update (the code force-accepts).
- **Finding:** the `openai_compatible` backend caps output at 8000 tokens by
  default and drops `reasoning_effort`.

### Decisions
- **User decision:** use one of the two available Slurm accounts (recorded
  only in the private `site.env`). Model weights go under the project's
  persistent storage (`~/projects`), not home (home quota nearly full).
- **User decision:** create new pinned envs rather than reuse another
  project's env (isolation; CLAUDE.md forbids altering existing envs).
- **Recommendation → adopted:** controller and vLLM in one single-node GPU
  job over loopback; run SkillOpt from a pinned git worktree via
  `PYTHONPATH`; pin Qwen thinking off, temperature 0.7, max_tokens 8000;
  raise the optimizer cap to 16384; smoke = 1 epoch × 1 step with the full
  18-task gate.
- **User decision:** the fork is public, so machine-specific settings move
  to an untracked `site.env` (tracked `site.env.example` with placeholders)
  and manifests are sanitized. No `site.env` or secrets copy ever goes into a
  run dir, and a leak guard checks every run (`8c19f7b`).

### Remaining issues
Selection split size, slow-update gating and reflection rounds are open
methodology questions.

---

## 2026-10-19 (evening) — Environment installation and the first failure

### Objective
Create the controller and vLLM envs, download Qwen3.5-4B and ALFWorld data,
run the CPU tests.

### What happened
- `setup_env` **64323201 failed** after 2 min. **Finding:** ALFWorld depends
  on `fast-downward-textworld`, which builds from source and calls a bare
  `python`. pip was run without activating the env, so no `python` was on
  PATH.
- Fix `70de16f`: installs run with the env's `bin` first on PATH. Envs
  created by the script get marker files, and the script never modifies an
  env it did not create. **User decision:** remove the empty partial env
  left by the failed job (its conda history held only our create command).
- Rerun **64323909 completed** in 20.5 min. Peak memory reached the 16 GB
  request (no OOM). Tests **64326614: 104 passed**.

### Key findings
- **Finding (data census):** ALFWorld `valid_seen` has 140 games,
  `valid_unseen` 134. The `split_*.json` files cited by the repo's manifest
  do not exist, and the 18 selection games are a scattered subset.
- **Interpretation (kept as inference at the user's request):** the paper's
  "140 selection" is probably all of `valid_seen`.
- **Finding:** the controller env resolved `openai==3.35.1`, a newer major
  version than other envs. The backend tests passed.

### Lessons
Building from source can depend on an activated environment; scripted
installs must activate explicitly.

---

## 2026-10-19 → 10-20 — DeepSeek integration through SkillOpt

### Objective
Verify the optimizer from a compute node through SkillOpt's own code path.

### What happened
- The first preflight called the OpenAI SDK directly. **User decision:**
  verify through SkillOpt's backend instead. We deployed `d129260` (two
  calls via `openai_compatible_backend.chat_optimizer`, raw response
  recorded) rather than the previously requested `d11e70b`, and removed
  DeepSeek calls from the rollout stage.
- `check_compute` **64328962 passed**.

### Key findings
- **Finding:** the API echoes only the alias `deepseek-flash`; provider docs
  map it to V4.1-Flash-1017. Thinking is on by default (effort "high");
  reasoning arrives in `reasoning_content` and SkillOpt's returned text
  excludes it. `extract_json` parses the replies. Cost ≈ $0.00007.
- **Finding:** the leak guard's final log line went missing.
  **Interpretation:** the background `tee` was killed at job end. Verified
  independently that no key reached the artifacts; later fixed by waiting
  for `tee` in a single EXIT handler (`c740c97`).

### Open question
The paper's optimizer used reasoning effort "medium". SkillOpt's backend
cannot send DeepSeek's thinking controls, so we run at the provider default.

---

## 2026-10-20 (overnight) — vLLM startup failure: FlashInfer needs a compiler

### Objective
Run the first vLLM smoke (Qwen on one A40) during an authorized overnight sequence.

### What happened
- `STAGE=vllm` **64329002 failed** after 3.7 min. **Finding:** weights loaded
  and the CUDA 13.0 torch build worked, but during memory profiling vLLM's
  FlashInfer top-k/top-p sampler JIT-compiled a kernel and found no `nvcc`.
- Per the failure policy: no retry, dependent stages not submitted.
- While diagnosing: our variables named `VLLM_*` collided with vLLM's
  namespace (`VLLM_PORT` is a real vLLM setting), and the manifest was
  written too late to survive a failed start. Both fixed in `c740c97`.
- **Recommendation:** use vLLM's native sampler rather than load a CUDA
  module. **User decision:** adopt `VLLM_USE_FLASHINFER_SAMPLER=0`, verified
  at startup and recorded in manifests (`a945bb2`).

### Alternatives considered
`module load cuda/<ver>` (keeps FlashInfer; needs a CUDA version choice and
first-start compilation). Deferred.

---

## 2026-10-20 — Network exposure of torch's rendezvous socket

### Objective
Keep the job-local model server unreachable from other cluster users.

### What happened and how our understanding changed
- `STAGE=vllm` **64330104**: the server started (native sampler confirmed)
  and then **failed our fail-closed listener check**. Besides the loopback
  HTTP API, vLLM's engine opened 7 listeners on the node IP and 1 on `0.0.0.0`.
- *Earlier belief:* `VLLM_HOST_IP=127.0.0.1` would keep the rendezvous on
  loopback. *Corrected:* it only sets the address clients use. torch's
  TCPStore server still binds all interfaces, and Gloo binds the
  hostname's interface.
- **Recommendation:** Gloo on loopback plus a documented exception for the
  TCPStore port. **User decision (modified):** set `GLOO_SOCKET_IFNAME=lo`,
  but do **not** auto-allow the wildcard listener; investigate it first;
  keep strict checks (`0724e27`: listener classifier, external probe from
  a login node).
- **64331018: Finding:** the Gloo sockets moved to loopback. The TCPStore on
  `0.0.0.0` was **reachable from a login node**. Code reading (torch 2.11):
  `TCPStoreOptions` has no bind address, and vLLM 0.25.1's single-GPU
  executor always uses `tcp://`. Exit 3 ("unresolved") by design; no rollout.
- First reasoning-format observation (one reply): `</think>` present, no
  opening `<think>`. **Interpretation at the time:** Qwen's non-thinking
  template pre-fills an empty think block. (Revised later, see below.)
- **Recommendation:** override vLLM's rendezvous to `file://` (a patch).
  **User decision:** no custom patches. The user pointed to vLLM 0.28.0
  (PR #50999), which adopts file-based rendezvous upstream.

---

## 2026-10-20 — Migration to vLLM 0.28.0 and the first ALFWorld episode

### What happened
- **Finding:** PR #50999's merge commit is in v0.28.0 (not in 0.25.1). The
  single-GPU executor uses a `FileStore` (verified in the tag and the
  installed copy).
- **Finding (release review by subagent):** 0.28.0 turns prefix caching on
  for hybrid models such as Qwen3.5. Pinned off for parity with 0.25.1.
  Startup checks were added for rendezvous method, prefix caching and
  sampling defaults; vLLM also gets a private 0700 `TMPDIR` (`3dfaff9`).
- New env `skillopt-vllm-0.28.0` (**64332646**). The 0.25.1 env was
  preserved untouched. Tests 114 passed.
- **64332934 passed:** file rendezvous, all 7 listeners on loopback, external
  probe refused, inference produced a valid action.
- **64333791: first complete ALFWorld episode**, `val:0000` solved in 3
  actions with 0 fallbacks.

### Change in understanding: missing reasoning
- **Finding:** all 3 replies in the episode had **no think tags at all**, so
  the dominant pattern is "no tags", not "missing opening tag". Every step's
  `reasoning` was null.
- **Finding (code trace):** SkillOpt keeps the raw reply in `model_response`
  but the reflection formatter only uses `reasoning`. The optimizer would
  see actions and observations only.
- **Interpretation:** a collision between Qwen's documented non-thinking
  template and the ALFWorld prompt's request for `<think>` tags, not a
  SkillOpt crash.

---

## 2026-10-20 — Opt-in reasoning parser and trace instrumentation

### Objective
Make the lost reasoning recoverable and capture evidence for future
independent contracts, without changing the baseline.

### Work completed (`8f739f7`)
- `env.reasoning_parser`: `strict` (default, identical to upstream) or
  `qwen_compat` (text before the first `<action>`; never invents text; one
  line so it cannot forge prompt structure).
- `env.trace_log`: `trace.json` with initial observation, per-step
  observation and admissible actions, raw reply, parsed and executed action,
  validity, feedback, and call latency, usage and errors.
- These are the **first changes to upstream SkillOpt files**: 4 upstream
  lines changed, the rest additive and off by default. Tests 179 passed.
  Fake-env tests show traces and parser leave actions, outcomes and scores
  unchanged.
- **Finding (offline demo 64336627):** on the saved episode, strict gives 0
  reasoning lines and compat 3 (451–1,728 chars each).

### Decision
- **User decision:** keep three conditions separate. **A** strict upstream,
  **B** + compat (a format fix, never credited to verification), **C**
  future Python/Lean contract feedback compared against B, with Python as
  the control for Lean.

---

## 2026-10-20 — Billing correction and the 8-episode rollout

### What happened and how our understanding changed
- *Earlier assumption:* requesting 8 CPUs / 64 GB with one A40 was harmless.
  *Corrected by the user:* spgpu bills the maximum of weighted resources, and
  one A40 unit = 4 CPUs = 48 GB. **Finding:** all five earlier GPU jobs were
  billed as 2 GPUs (36157 units; `my_job_estimate` $0.217/h vs $0.108/h).
- **Finding (measure_envs 64337522, CPU only):** 8 ALFWorld simulators need
  0.7 GiB and ~0.8 cores; building them takes ~90 s.
- **User decision:** GPU jobs request 4 CPUs / 48 GB per A40. Billing
  units, GPU-equivalents and cgroup CPU/RAM are recorded per run (`73890c3`).
- **64337548 (approved):** 8 fixed **train** tasks (one per type, plus two),
  strict parser, traces on. **4/8 successes**; all failures were 50-step
  timeouts (task failures, not infrastructure). Billed **1.0
  GPU-equivalent**. Traces were consistent with SkillOpt's outputs.
- *Earlier estimate:* 10–15 min. *Measured:* 35 min (episodes 31 min).
  **Finding:** 7 replies ran to the 8000-token cap (5 had no action → `look`
  fallbacks), and the lockstep rollout waits for the slowest env each step.

### Lessons
Mean latency is a poor guide to cost. A few runaway generations dominate
under lockstep stepping, so the long tail must be measured before budgeting.

### Deferred (user request to note, not build)
Two independent vLLM replicas when a job already pays for two GPU units.

---

## 2026-10-20 — Offline analysis and training-smoke preparation

### Work completed
- **Offline analysis 64351263** (no inference): output tokens median 171,
  p90 969; the action tag sits at a median 95% of each reply.
- **Finding (walltime):** the 7 capped replies each stalled a different step
  (989 s); ordinary stragglers added 755 s. That reconciles the 29-minute
  critical path.
- **Finding:** 6/7 capped replies and 13/14 replies ≥ 2000 tokens came from
  failed episodes.
- **Finding (unexpected):** upstream deliberately never truncates trajectory
  text, so `qwen_compat` makes analyst prompts ~10× larger (4-failure
  minibatch ~8–11k → ~84–112k tokens), much of it runaway deliberation.
- **Finding (code):** the planned train batch at seed 42 is `train:0017,
  0036, 0004, 0013`. qwen_compat applies to every trainer rollout through
  the same adapter. Retry layers are backend 3 × SDK up to 3 attempts per
  call site. A parse failure is not retried.
- Prepared `configs/research/alfworld_smoke_qwen_compat.yaml` (condition B;
  a test asserts it differs only in parser and trace) and pinned
  `OPTIMIZER_MODEL=deepseek-flash` (`7e78fb3`, `e998b9f`).

### Decisions
- **User decision:** keep 1 A40 / 4 CPUs / 48 GB. For the first smoke,
  change neither checkpoint, token cap, workers, evaluator, skill-update
  rules nor selection split.
- **User direction:** run the **strict-parser** smoke first (condition A),
  to verify the original loop before studying extra reasoning text.
  Submission is not yet authorized.
- **Proposal (deferred):** a controlled lower-token-cap condition; a
  per-step cap on recovered reasoning; more `env.workers`. Each would be a
  separately labeled condition for all arms.

### Remaining issues
The full optimization loop is untested. The effect of recovered reasoning is
unknown. Selection-split methodology is open. Long generations make runtime
unpredictable (smoke estimate 1.5–3.3 h).

---

## 2026-10-20 — Persistent project state

### Objective
Make the project resumable across sessions and context compaction, and
readable as a development history.

### Work completed
- Added `STATE.md` (current handoff) and this `DAILY_LOG.md`.
- Restructured `IMPLEMENTATION_PLAN.md` to milestones and planned work only;
  past checklists and the decision table now live in this log.
- Added three rules to CLAUDE.md alongside the user's new continuity
  section: check `jobs.tsv`/`squeue`/`sacct` before costly actions, keep the
  statement labels in `STATE.md`, and the spgpu resource rule.

### Next actions
See `STATE.md` §G. The strict training smoke awaits authorization.

---

## 2026-10-20 — Training smoke authorized; pre-submission checks

### Objective
Submit the user-approved strict-parser training smoke with diagnostic traces,
after verifying the configuration and the evidence capture.

### Work completed
- **User decision:** approve one strict-parser training smoke with a
  separate strict + `trace_log` config (original strict config unchanged),
  1 A40 / 4 CPUs / 48 GB, ≤ 5 h, original prompts, edit logic and gate.
- Added `configs/research/alfworld_smoke_strict_traced.yaml` and tests.
- **Finding (code):** SkillOpt writes only *parsed* optimizer results
  (`patches/*.json`, `merged_patch.json`, `ranked_edits.json`); it saves no
  prompts or raw replies, and drops unparsable analyst replies and ranking
  failures without any file record (`reflect.py:353-355`, `clip.py:101-102`;
  merge leaves only a "fallback" reasoning string). The backend retries
  silently. So the user's request to preserve optimizer inputs/outputs and
  parse outcomes could not be met without instrumentation.
- Added an **opt-in** call log to the `openai_compatible` backend (one JSON
  line per attempt: messages, reply, DeepSeek reasoning text, finish reason,
  full usage incl. cache/reasoning tokens, or the error). It is off unless
  `OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG` is set and is written so it cannot
  alter a request, a return value or the retry loop (tested). The OpenAI
  SDK in our env (3.35.1) logs no bodies, headers or URLs at debug level, so
  `OPENAI_LOG=debug` adds content-free per-HTTP-request lines, including
  the SDK's own internal retries.
- Added `research/summarize_optimizer_calls.py` (parse outcome per call
  using SkillOpt's own rule, tokens, cost by price tier) and
  `research/resolve_config.py` (resolved configs, planned batch, selection
  ids), both run automatically.
- Tests 64358982: 195 passed; resolved diff confirmed. Deployed `12a153e`;
  submitted training job **64359024**.

### Decisions and reasons
- **Interpretation:** the call log is a SkillOpt source change, but only in
  the transport layer and off by default; it does not touch optimizer
  prompts, edit rules or the gate. Keeping it in the backend (rather than a
  runtime monkey-patch from a wrapper) makes it reviewable and testable.

### Next actions
Monitor 64359024; fetch and analyze; update logs; report. Later: write up the
pre-implementation research decisions (incl. Option B contracts).

