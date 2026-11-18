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

---

## 2026-10-20 — First SkillOpt optimization step completed (job 64359024)

### Objective
Verify the complete original SkillOpt loop on ALFWorld (condition A, strict
parser, traces on).

### Work completed
- Job 64359024 completed in 2 h 34 min on one A40 billing unit (≈ $0.28);
  DeepSeek cost ≈ $0.008 for 4 calls. Details: `EXPERIMENT_LOG.md`.
- **Finding:** every stage ran end to end. Baseline selection 12/18; train
  batch 1/4; two analyst calls (2 + 4 edits), a merge (6 edits) and a ranking
  call (kept 4) all parsed on the first attempt; the candidate (2,966 →
  4,752 chars) scored 12/18; the gate **rejected** it (ties are rejected).
- **Finding:** the new diagnostics worked: the optimizer call log matched the
  trainer's token counts exactly; the SDK lines confirmed 4 HTTP requests and
  no hidden retries; all 40 traced episodes passed the consistency check.
- **Finding:** the per-task results differ on 2 tasks in opposite directions
  (+`val:0012` cool, −`val:0014` heat). The candidate also had fewer
  fallbacks (17 → 6), fewer 8000-token replies (20 → 11) and shorter
  pick-two episodes.

### Interpretations (not established)
- A single 18-task evaluation at temperature 0.7 cannot separate a small
  skill effect from sampling noise; we have no repeat runs to estimate that
  noise. The lower fallback/cap counts are one-run observations.
- The success analyst generalized from one trajectory and misattributed the
  success (the episode ended on *taking* the statue under the lit lamp, not
  on examining). Its kept edit omits that the object must be held. This is a
  concrete example of the failure-localization problem the research question
  is about; whether independent task contracts would catch it is a
  hypothesis, not a result.
- **Finding (clarification):** live responses (64328962, 64359024) report
  only the alias `deepseek-flash` in `model`; the dated version in
  `BASELINE_ARCHITECTURE.md` remains a provider-documentation claim that the
  API does not confirm.

### Remaining issues
Noise level of the gate; the paper-aligned protocol (selection size,
reflection rounds, reasoning effort, seeds); whether condition B changes
the optimizer's edits. None of these were run.

### Next actions
Review with the user (milestone 8). No further jobs are authorized.

---

## 2026-10-20 — Review of the first optimization step (offline)

### Objective
Close out job 64359024, audit the new logging, and study two issues relevant
to the research question: the `train:0004` success-credit error and
evaluation noise. No jobs, no API calls.

### Work completed
- **User decision:** the training smoke counts as a successful engineering
  milestone; the rejected candidate is expected gate behavior. Pushed the
  two docs commits after a scope and secrets scan.
- **Logger audit (findings):** call log off unless the variable is set (only
  the train stage sets it); behavior unchanged (tests + exact token match);
  no key, header, URL, path or host in the 4 records; the SDK debug log has
  4 content-free message types. Residual risk: provider error messages are
  stored on failure (could echo a masked key fragment); redaction proposed.
- **`train:0004` (findings):** the PDDL goal requires, in one state, a
  toggled desklamp, the agent at its receptacle, and holding a statue; no
  `examine`, no order. Step 4 (take) completed the conjunction after the lamp
  (step 2) and location (step 1). DeepSeek's reply and reasoning claim
  examining was sufficient and taking optional; the kept edit omits holding.
  DeepSeek got only actions and observations: no goal, no success step, no
  statement that episodes end on success.
- **Randomness (findings):** tasks, order, env seeds and games are identical
  between baseline and candidate; target sampling is the only stochastic part
  (global vLLM generator, no per-request seed; the hybrid Qwen3.5 attention
  backend is not batch-invariant). The baseline is evaluated once; acceptance
  is a strict `>` between single runs. The same skill already produced
  different trajectories on the same game.
- **Other findings:** the trace's `env_is_action_valid` is a `<think>`-tag
  format flag, not validity. SkillOpt's ALFWorld "hidden reference" channel
  exists but is never wired in (`attach_reference_context` has no caller). A
  second lamp toggle still prints "You turn on", and the PDDL goal checks
  `isToggled`, not `isOn`.

### Decisions and reasons
- **User decision:** keep four conditions distinct: A strict, B Qwen-compatible
  parser, C Python contracts, D Lean contracts; B–D are not validated, and
  parser/explanation gains are never credited to verification.
- **Interpretation:** DeepSeek's error was a real misstatement of the success
  conditions, not merely an incomplete strategy: it asserted a non-goal
  action as sufficient and a required action as optional. The trajectory held
  partial evidence against this (lamp-on + examine did not end the episode),
  but the prompt did not explain termination, and one trajectory cannot show
  that `examine` or the order is unnecessary.
- **Interpretation:** contract reports would add task-specification
  information *and* checking. A specification-text-only control is needed to
  tell these apart; for a goal this simple, a Python checker gives the same
  information as Lean.
- **Proposal:** E1 (repeat the initial skill's 18-task evaluation 3 times),
  E2 (robust acceptance/assessment, after E1), E3 (cheap optimizer replay
  pilot). See `IMPLEMENTATION_PLAN.md`.

### Next actions
Await the user's decision on E1–E3 and on pushing the docs commit.

---

## 2026-10-20 — Priority change: Lean-guided contract feedback

### Objective
Move the main effort from baseline investigation to the research
contribution: SkillOpt guided by independently specified, Lean-checked task
contracts (Option B). Planning only; no jobs, no API calls.

### Work completed
- **User decision:** E1 stays as supporting work (scorer and gate unchanged,
  must not block); E3 is deferred until the pipeline produces genuine reports.
- **Lean4Agent review (findings, commit `e6a78cb`):** it verifies that an
  agent followed an LLM-specified *workflow*; specs are LLM-generated from the
  workflow's own step instructions; Lean 4.20 + all of Mathlib (52 files
  import it); no temporal operators; its `trustExternalVerification` axiom
  concludes any predicate from any tool result (the result's `passed` flag is
  not used), which makes the corresponding proofs vacuous (our inference;
  not compiled). Its SWE "Lean" feedback largely relays benchmark test
  results. Reusable only as patterns (JSON trace → Lean, subprocess bridge,
  per-step digest).
- **Trusted-state investigation (findings):** TextWorld's PDDL engine
  computes the full ground fact set every step (`pddl.py:79`) and `won` with
  Fast Downward's `check_goal`; requesting facts changes nothing else; no RNG
  in the engine. ALFWorld goals across all six types use only `and`,
  `exists`, `not`, `=`; pick-two uses inequality, not counting. The goal
  checks `isToggled`, not `isOn`. Text feedback hides `isOn`, closed
  receptacles' contents and co-location.
- Wrote the roadmap (L0–L7) with trust boundary, Lean guarantees, components,
  Python control, acceptance tests and uncertainties
  (`IMPLEMENTATION_PLAN.md`).

### Decisions and reasons
- **Proposal:** trusted evidence by deterministic offline replay of recorded
  actions through the same engine, validated by reproducing recorded feedback,
  admissible commands and `won`, and by checking that our goal evaluation
  equals `won` at every step. Reason: no change to the experiment code path
  and the strongest available trust anchor.
- **Proposal:** a fresh, small Lean package (core Lean, no Mathlib) with
  proven checker correctness and `#print axioms` limited to Lean's standard
  axioms, instead of reusing Lean4Agent's library.
- **Interpretation (important):** if the Python and Lean checkers are both
  correct, their reports are identical, so C and D cannot differ in
  optimization outcome by construction. Lean's value must be demonstrated as
  assurance (bug detection, sound UNKNOWN handling, proven decomposition),
  while the optimization comparison is contract feedback vs reflection vs
  specification-only text.

### Next actions
User review of the roadmap; on approval, L0 design note and L1–L2 (replay,
evidence, goal extraction, oracle tests on 48 episodes).


## 2026-11-16 (OfficeQA worktree, branch `officeqa-contracts`)

### Ported the learned-contract implementation to OfficeQA (offline; 0 GPU jobs, 0 paid calls)
Full record: `docs/research/OFFICEQA_LEARNED_CONTRACT_PORT.md`. Output preserved at
`~/dev/lean_skillopt/runs/20261116-offline-lc-port/`.

Reference design: `LEARNED_CONTRACT_DESIGN.md` **v0.1 + amendments A1-A4** in the sibling worktree
`SkillOpt-learned-contract` @ `4a9914a`. There is no document labelled v0.2; v0.1 as amended is the agreed design,
and the five digests are recorded in the port note so a later session can tell whether the reference moved.

New, additive, opt-in: `research/officeqa/oqa_lc_{vocab,ir,cells,chain,learn,replay,lean,report,demo}.py` and
`tests/test_officeqa_lc.py`. `oqa_check.py` and `oqa_obligations.py` were deliberately not modified, so arm A
stays byte-identical to upstream and the two-arm run's P/S/C semantics are unchanged; the existing 87 OfficeQA
tests still pass, which is the check on that.

**The loop ran end to end** on the 12 shared epoch-1 training attempts, in the order the run recorded them, with
each model frozen by digest before the next attempt was replayed and each prediction committed (hash-chained)
before its observation was released. `index_alignment` was adopted at round 0 with nothing contradicting it and
refuted at round 6 by UID0026's own read of the flattened header of a table it had been handed in split form, in
12 places; the repair drops the assumption and reopens the affected bindings to UNKNOWN. Arm B's epoch-2 rollout
of the same items served as independent confirmation: 82 forecasts confirmed, 1 violated, and the dropped
assumption contradicted 10 more times in UID0014.

### Decisions and reasons
- **Decision:** the repair is an **assumption drop**, not a contract revision. Reason: the contract's claim was
  never wrong; the licence for evaluating it was. I looked for a feature of the split header form that separates
  the cases where index merging works from those where it does not, and there is none - whether the renderer kept
  the leading blank depends on information visible only in the flattened form.
- **Decision:** `TEMPLATES` + `MIN_ITEMS = 2` instead of exhaustive enumeration over the vocabulary. Reason: the
  first version enumerated three verbs x ten atoms x two polarities and the gate accepted
  `grep.effect = not has_table` and `read.effect = not cap_reached` (true by construction). That is the
  "8 of 14 rules never fire" disease of `OFFICEQA_CONTRACT_AUDIT.md`.
- **Addition to the ported design (recorded as a deviation):** `assumption_recheck`. `apply_proposal` tests
  `direct_contradictions` only at adoption time, which sufficed in the ALFWorld line because the LLM learner saw
  the previous epoch's refutations. With a deterministic proposer nothing re-tested it, and in the first run
  `index_alignment` stayed in force while the records contradicted it in 16 places.
- **Correction:** `mech_repair` must target **refuted** contracts, not active ones. `apply_proposal` auto-refutes
  every active contract that acquires a counterexample, so a proposer looking at `active(model)` never fires at
  all - the first run's did not.
- **Correction to an earlier conclusion of mine.** `OFFICEQA_STATE.md` section A0 claimed
  `OFFICEQA_TRANSFER_MANIFEST.json` was untracked and that its `source_commit` and per-file `source` paths were
  null. All three were wrong: the manifest is tracked, and the fields exist as `source_head` and `path`, populated,
  with `source_status` recording that the sources are untracked in their own worktree. There was no provenance gap.
- **Verified result, regression case:** on UID0014's epoch-2 trace the old C report fires
  `scale_conversion_missing` 29x ("No conversion step appears"). The obligation form discharges 13 of those - the
  actor converted `1,004,461 (in millions)` to `$1,004.461 billion` correctly at step 1 - and reports 23 as
  VIOLATED for a different, checkable reason: thousands to billions applied with factor 1e-3 where 1e-6 is
  required, so 14 regression inputs are 1,000x too large. The plain report P reports only `no_matches`.
- **Negative results kept:** adjudicating evidence is very sparse (5 table pairs over 3 tool steps in 24 attempts;
  126 of 128 development tool steps leave the column atom UNKNOWN); the learned model is 3 contracts; and one of
  those 3 acquires a counterexample on the confirmation collection (`read.effect.1 = width_uniform` at
  UID0028@42), so the final model is not fully confirmed on later data.
- **Lean: generated, NOT compiled.** 64 core-only statements, every one closed by `by decide` or `rfl`; the general
  results are stated over the finite Kleene domain acceptability factors through, so they are closed by exhaustion
  rather than by a tactic script. The Python mirror agrees with the file on every row (0 mismatches), which is a
  cross-implementation check and not a Lean proof. `lean`/`lake`/`elan` are absent on the workstation and on the
  Great Lakes login node (checked, not assumed), and the OQA-5 authorization is expressed in A40-hours and
  DeepSeek calls and names no CPU job-hours, so compiling it was left as a request: 1 CPU, 2 GB, 15 min, 0 GPU,
  no key, no paid call.
- **Proposal, not implemented:** a `guard` field on `effect` contracts, with "guard false or unknown -> neutral".
  It changes contract semantics, so it waits for approval. Two worked examples of where the current format bites
  are in port note section 3.3.

### Next actions
User decisions: compile the Lean file (needs the CPU job above); approve the guarded-`effect` extension; spend
1-2 of the 8 remaining DeepSeek calls on one learner round to test whether a paid learner proposes anything the
deterministic proposer does not.

## 2026-11-16/17 (OfficeQA worktree, branch `officeqa-contracts`)

### Compiled the ported Lean file on the cluster (job 66259448)
USER-AUTHORIZED: 1 CPU core, 2 GB, <=15 min, 0 GPUs, 0 paid calls. **Lean 4.20.0 compiled all 64 statements,
rc 0, zero compiler output, zero warnings, zero `sorry`** (23 in `section General` + 2 `rfl` bridges + 39 over
the exported evidence), 4.24 s in `lean`. The file's digest is identical at six links - recorded locally before
submission, reproduced twice by the generator at `47c6ed8`, committed at `87b9ad6` with a manifest, verified by
the job's own manifest check, logged by the job, and in the copy fed to `lean`:
`e40a7e62faed92ffdc53093b4937f8eb6b655ba5eabda14d7036444c0b9eecbb`.

**NOT established: the axiom set.** `#print axioms` was deliberately not added, because changing the file would
break the digest guarantee. Zero warnings does rule out `sorry`, since Lean warns on every one.

An earlier submission (66259421) failed in 1 s before `lean` ran: Slurm copies a batch script into the node
spool, so `${BASH_SOURCE[0]}` pointed there instead of at the bundle. Fixed with `$SLURM_SUBMIT_DIR`.

The toolchain is **Lean 4.20.0** under the project's own `elan` directory on cluster storage, registered as
`OQA_LEAN_BIN` in the private site settings. The learned-contract state note records "Lean 4.35.0"; no such
toolchain exists there now. Nothing was pushed - `deploy.sh` refuses an unpushed commit, so the job ran from a
read-only staging bundle.

### Switched the planned comparison to a hosted actor, and ran the authorized smoke test (job 66264849)
Full record: `docs/research/OFFICEQA_API_COMPARISON_PLAN.md`. Artifacts:
`~/dev/lean_skillopt/runs/20261117-officeqa-api-smoke-66264849/`.

USER-AUTHORIZED: <=30 min CPU job time, 0 GPUs, <=100 EmpirioLabs HTTP attempts, <=$0.50 actor charges.
**Spent: 23 of 100 attempts, $0.0034 of $0.50, ~3 min of CPU, 0 GPUs.**

**Verified free, before spending anything:** `GET /v1/models/qwen3-5-4b` is unauthenticated and declares
`function_calling`, `json_schema` structured output, 262144 context, 32768 max output, **FP8 weights + FP8 KV**,
and `hugging_face_id` `Qwen/Qwen3.5-4B` - the same weights the A40 pilot served, different numerics.

**Smoke test PASS.** 20 attempts, 0 errors, 0 malformed replies, 0 retries; 18 of 20 replies carried
`tool_calls`; every argument set parsed as JSON; **18 tool calls executed against the documents with 0
observation errors**; all 3 items took further turns after an observation. Latency min 1.63 / median 3.41 /
max 7.04 s. 73,145 prompt + 6,251 completion tokens. **`cached_prompt_tokens` was 0 on every call**, so no
prefix caching happens even though each turn re-sends an identical system prompt and oracle-page prefix.
Accuracy, recorded separately and not the verdict: 1 of 3 answered.

### Decisions and reasons
- **Decision:** judge the smoke test on the tool loop and report accuracy separately, as instructed. Three
  questions cannot measure accuracy, and the A40 run already put this skill at 5/12 on training.
- **Decision:** select the three questions by a rule fixed before the run (`OQA-SMOKE-1`) - the first three
  training items in the A40 shared-rollout order that requested at least one tool under S0. Requiring a tool
  call is the point; an item answered from the supplied pages alone exercises none of the loop.
- **Correction to an earlier claim of mine.** I reported that `EMPIRIOLABS_API_KEY` existed on neither machine.
  It is at `/home/yaojm/.config/lean_skillopt/actor_api.env` on the cluster (mode 600, 79 characters). I had
  searched `$HOME` dotfiles, guessed filenames and the turbo tree, and never looked in `~/.config`. The user
  supplied the path.
- **My own error cost four of five submissions.** I documented the "target_backend: openai_chat means AZURE"
  trap in the plan and then did not guard it in the driver, which called `process_one` without configuring a
  backend (job 66264701). The driver now configures both roles itself, and the ledger reports `never_sent` so a
  client-side failure is not mistaken for spend.
- **Infrastructure finding, recorded for the comparison:** a batch script submitted directly as the sbatch file
  from `/nfs/turbo`, doing its work there, was killed by **NODE_FAIL at 60 s, four times each, on eight
  different nodes** (66262999, 66263561) before Python ran. A control job with the same account, partition and
  resources COMPLETED; one-factor controls (66264099-101, 66264256) showed reading the key from `~/.config`,
  extracting the 576-file bundle and curling the endpoint from a compute node are each fine. Submitting via
  `--wrap` with a **node-local /tmp** work dir fixed it - the same lesson the learned-contract line recorded in
  job 65818719. **`--no-requeue` did not stop the requeue loop**, which matters because the attempt ledger is
  per-process: a requeue after work begins would respend the authorized attempts with a fresh counter.
- **`python -I` is isolated mode and ignores `PYTHONPATH`** (job 66264572). The driver now derives the repository
  root from `__file__`.
- **Treatment arm wired to the compiled prototype with provenance.** Arm B emits the obligation-form report under
  a frozen model whose digest is verified first; the report-cache key now covers the **model version** as well as
  the trace, because the same trace yields different bindings under a model that assumes `index_alignment` and one
  that has dropped it. Verified on the saved A40 traces: 24 reports pass against their own attempts; epoch-1
  reports checked against epoch-2 attempts FAIL; a report built under model v5 FAILs against v7.
- **Budget revised DOWN from measured numbers:** 8 CPU job-hours (was 12) and $2.50 actor charges (was $4.00).
  The **50 RPM account limit is the binding constraint on runtime, not latency**: 3,100 requests is a 62-minute
  floor against 44 minutes latency-bound.

### Next actions
The user will instruct on the full-scale run after a compaction. The comparison needs its own budget (section 4.4
of the plan); the old OfficeQA allowance has 8 DeepSeek calls left and no CPU or actor budget.

## 2026-11-17 — the OfficeQA comparison expanded to three arms; offline prep complete, nothing submitted

The user expanded the planned hosted-actor comparison from two arms to three, following the ALFWorld AC-1
structure: **A** Original SkillOpt, **B** Action Contracts Only, **C** Action Contracts + Stages. Larger splits
(24 training questions × 4 epochs, the released 24-question validation set, 60 held-out), the gated slow update
and the meta-skill on from epoch 2, and the two contract arms starting from one model learned on the shared
rollout and then evolving from their own traces. They asked for the offline preparation and the revised budget
**before** anything is submitted. Full plan: `OFFICEQA_THREE_ARM_LAUNCH.md`. Packet:
`OFFICEQA_HANDOFF_LATEST.md`. Evidence: `runs/20261117-officeqa-3arm-prelaunch/`.

**Nothing was submitted and nothing was spent.** All of it ran on the workstation.

### What was built, and why each piece exists

**Rule OQA-SEL-3** (`research/officeqa/oqa_sel3.py`, digest `117836ff4139…`). The validation set is the whole
released val split, unfiltered — the user asked for the released set, and filtering the gate would be a
selection the benchmark did not make. Train and held-out are balanced over category × document count, and
**OQA-SEL-2's items are retained first**, so all three previous sets are subsets of the new ones and every saved
development trace is still a trace of a current item. The draw order is `sha256("OQA-SEL-3" + uid)`, which
depends on the rule name and the item id and on nothing else. UID0046 stays excluded with its reason recorded.
48 of the 108 items have no availability verdict, because that needs the documents, which exist only on the
cluster; the counts say so per set rather than leaving it implicit.

**The report split.** The existing learned-contract report was entirely stage-oriented — the action contracts
appeared only as a basis label. Arm B needs a report *about the tool calls*, so `oqa_lc_report.render_action`
was added: the contracts in force with the evidence behind each, the ones withdrawn and why, what each forecast
for this attempt's calls against what the observation showed, and the assumption checks. Arm C is that text
**plus** the stage half, so arm C's report contains arm B's line for line and the arms differ by the stage
material and nothing else. Both halves of that claim are checked on the rendered text, not on the intention.

A design decision worth recording: the first version of `audit_action_only` banned a list of generic words
("stage", "obligation", "conversion", "final answer"). That is too aggressive — the report quotes column
headings out of the bulletins, and a Treasury table may legitimately contain such a word, so one word in a
scanned page could have aborted an arm mid-campaign. The audit now **fails** only on markers that can only come
from the stage renderer (its section headings, the obligation kind names, the states `DISCHARGED`/`OUTSTANDING`)
and reports the generic words as advisories.

**Per-arm model evolution** (`research/officeqa/oqa_lc_arm.py`). `seed` learns M0 on the shared rollout once, so
both contract arms start byte-identical; `evolve` takes the model frozen before an epoch and that arm's own
traces, replays them out-of-sample first (hash-chaining each prediction before the observation is released),
then admits them and runs the same learner schedule. Evidence accumulates across epochs, so "recheck the earlier
cases" means what it says.

**One honest limitation, recorded rather than hidden.** M0 is learned on the shared epoch-1 rollout, which is
also the rollout epoch 1's reports describe, so epoch 1's forecasts are in-sample and are not predictions. The
frozen model now carries its evidence item list and a report on one of those items **says so in its own text**.
Leave-one-out for epoch 1 would remove even this at no cost; it is offered as a decision, not done unilaterally.

### The pre-launch check, and the four defects it found

`research/officeqa/oqa_prelaunch.py` runs two saved epochs through `scripts/train.py:main()` — the real entry
point and the real trainer — for each arm, replacing exactly two things because no authorization covers them:
the actor raises (every rollout directory is pre-seeded from recorded predictions, so a correct run sends
nothing and an unseeded one fails loudly) and the optimizer is a recorder that writes every prompt to disk. Each
arm ran as two phases, one per epoch, resuming from `runtime_state.json`, which is the shape the campaign itself
must use because `OFFICEQA_LC_MODEL` is read from the environment. Verdict **PASS, no problems**.

Four real defects came out of building it, each now with a regression test:

1. **`Evidence` keyed its atom cache on the item id, not the attempt.** The same training question is attempted
   again every epoch, so with evidence accumulating the second epoch's attempt silently reused the first
   epoch's atom values. On the real traces this reported **five** counterexamples for `read.effect.1` where
   there is **one**. Attempts now carry a `trace_key` of `<item>/e<epoch>` that the cache and every
   counterexample id use. This is the one that would have quietly corrupted the campaign's conclusions.
2. **A counterexample found at replay never triggered the refutation.** Re-scoring and refuting a surviving
   contract happens inside `apply_proposal`, which only ran when the learner proposed something new — not the
   case from epoch 3 on. An empty proposal is now applied whenever the evidence contradicts an active contract.
3. **A retired contract was invisible to the optimizer**, and retiring is what the repair schedule does most
   often; only `refuted` was listed.
4. **A contract kept with a counterexample printed "no counterexample"** beside a non-zero count.

Two errors in my own checking code are worth recording because both produced a false alarm before being fixed:
slicing the report out of an analyst prompt by looking for the next `####` heading swallowed the trajectory text
that follows it, and the per-phase report check counted the previous phase's files.

### The budget, and the one measurement that changed the plan

`research/officeqa/oqa_budget.py` computes the budget from constants that each carry the record they came from.
The ask is 6 h wall, 20 CPU job-hours, 0 GPUs, 15,000 actor requests, $8.00 actor, 130 DeepSeek calls, $4.00
DeepSeek. The **50 RPM account limit is the binding constraint on wall time, not latency** (235 min against
168), and being account-wide it means concurrency divides the quota rather than raising it.

The measurement that changed the plan: **this repo deliberately disables per-trajectory truncation in the slow
update**, so the longitudinal prompt carries both trajectories of every comparison pair at full length. Measured
on the dry run, 12 pairs come to 846,563 characters — 262,257 tokens — against a **173,060-token** envelope,
which is the largest optimizer prompt this project has ever sent successfully. At upstream's
`slow_update_samples: 20` that extrapolates to 437,000–656,000 tokens. Four samples stay inside the envelope
even if every pair lands in a trajectory-showing category, so the config is set to 4 and the deviation is
documented in the file itself with the table of measurements. Exceeding the limit would not be fatal —
`run_slow_update` catches the error and the trainer records `no_content` — but the run would then report a gated
slow update that produced no guidance, which is worse than a small sample.

**A correction to my own arithmetic.** I first put that envelope at ~104,000 tokens, by dividing a step's total
analyst tokens by the number of calls in it. The per-call records in `optimizer_calls_summary.json` give
173,060 for a single successful call. The conclusion about `slow_update_samples` is unchanged; the constant and
its source are now right, and the budget script says which mistake it replaced.

### The held-out evaluation, separated out

The trainer's own `eval_test` evaluates the held-out split once per arm, and each pass re-scores the starting
skill — three identical 60-question rollouts for one number, and more waste whenever two arms select the same
skill, which is exactly what the A40 run produced (all four candidates rejected, both final skills identical to
S0). So the campaign runs with `eval_test: false` and `research/officeqa/oqa_heldout.py` scores the held-out
split **once per distinct skill**: S0 plus each arm's validation-selected and last skill, deduplicated by
digest, at most 7 rollouts.

The held-out outcome of job 66234927 remains deliberately unread.

## 2026-11-17 (later) — 10 paired questions per slow update: measured, and it works

The user asked for a quick preflight on whether 10 comparison pairs per slow update is practical, with at most
two paid DeepSeek attempts, and asked that the campaign configuration stay at four pairs pending review. Detail
and every number: `EXPERIMENT_LOG.md` section OQA-SLOW-PROBE; state note section **A0-PROBE**.

**Result: a realistic 10-pair request of 243,986 prompt tokens was accepted and returned 3,235 characters of
parsed, usable guidance in 43.7 s for $0.041.** One of the two authorized attempts was used; one is unspent. The
config is untouched at 4.

**The measurement retires a framing I had been using.** The three-arm packet treated 173,060 tokens as an
"envelope" because it was the largest optimizer prompt the project had successfully sent. That was never a limit,
and saying so as if it bounded anything was the weak part of that analysis. DeepSeek accepted 1.41× it without
complaint. What is established now is a demonstrated acceptance at 243,986 tokens; the real ceiling is still
unknown, because the published figures for the DeepSeek chat family disagree and none of the sources is
DeepSeek's own documentation.

The offline half is worth keeping separate from the paid half. The prompts were rendered by calling the
campaign's own `run_slow_update` and `run_meta_skill` with `chat_optimizer` replaced by a capture stub, so the
rendering and the trace-inclusion rules are the planned run's rather than a reimplementation. That also settled
a question the user had made conditional: **the meta-skill request is smaller, not larger** — the two user
prompts are within 35 characters and the slow update's system prompt is 1,229 characters longer. So the larger
of the two was the one tested and the second attempt stayed unspent, which is what their condition asked for.

**The offline estimate was good to 0.09%.** 243,756 estimated against 243,986 reported, using the 3.228
characters-per-token ratio taken from the A40 run's per-call records. Worth remembering: for this project's
prompts that calibration is reliable enough to plan with, which is not something I could claim before a provider
had confirmed a number at this size.

**The first attempt cost nothing and taught something.** Job 66271190 configured the DeepSeek endpoint perfectly
and sent nothing: `skillopt.model.chat_optimizer` is a router and `OPTIMIZER_BACKEND` defaults to `openai_chat`,
which means Azure. The Azure path raised, `run_slow_update` swallowed the exception as it is written to do, and
the probe dutifully recorded `no_content` — a result that reads like a DeepSeek failure and is nothing of the
kind. This is the same trap I had already documented for the *target* role in the hosted-actor smoke test, and I
walked into it on the *optimizer* role because the preflight checked the openai-compatible config without
checking which backend the router would pick. The fix proves the dispatch path before anything is spent, by
replacing `_chat_messages_impl` with a marker-raiser and making one call through the same wrapper chain — zero
HTTP attempts, and it refuses under the default backend. Every campaign job that calls the optimizer needs the
same pinning.

**Where this leaves 10 versus 4.** Ten pairs costs +108 question-rollouts, +1,007 actor requests, +$0.49 actor
and +$0.74 DeepSeek at peak over four — all inside the budget already requested. The honest limit is that at ten
pairs the measured mix sits about 13,000 tokens under the proven point, less than half a pair, so there is no
demonstrated room for longer epochs or for a draw in which every pair carries a trajectory (computed at
~347,000 tokens). Overflow is graceful: `run_slow_update` catches it and the trainer records `no_content`,
symmetrically across arms and at no cost. So I recommend ten with the post-run `no_content` check kept, and name
six as the choice if the user wants nothing in unproven territory — six is the largest value whose worst case
stays under the proven 243,986.

Two small pieces of infrastructure came out of this and will be reused: `research/cluster/oqa_bundle.sh` builds
and uploads a pinned staging bundle (the campaign needs 13 of them, and the steps had been living in a shell
history), and the probe's job body **refuses to run on a requeue**, which is the safeguard the earlier
API-spending jobs only documented.

## 2026-11-17 (later) — the three-arm campaign is submitted, after a preflight that found a real blocker

The user authorized the campaign: 20 CPU job-hours, zero GPUs, 15,000 EmpirioLabs attempts and $8.00, 130
DeepSeek calls and $4.00, Original SkillOpt first after the shared rollout, ten paired questions per gated slow
update, and four specific things to settle before any paid rollout. All four are settled; one of them was not a
formality.

**The document blocker.** The corpus on the cluster was fetched for the twelve development questions of the
earlier study — 86 files, 63 MB of a 1.25 GB dataset. Rule OQA-SEL-3's 108 questions reference **153** distinct
parsed bulletins, of which **78** were present. So **33 of the 108 items had no document at all**, including 8
of the 24 training questions and 23 of the 60 held-out ones. An item with no parsed file cannot be answered by
any arm, and we would have spent roughly a third of the actor budget discovering that. 150 files (121.1 MB) are
now fetched from the pinned revision; the corpus verifies 153 of 153.

I had listed "availability is untested for 48 of the 108 items" as an open item for two days without noticing
that the untested items were untested *because their documents were absent*. The launch plan said a short CPU
preflight "reads local files only and spends nothing" — true of the check, false of the precondition it needed.

**Availability is now PASS for all 108**: 107 AVAILABLE from the supplied page, 1 AVAILABLE_VIA_TOOLS, zero
ABSENT, zero untested, and no verdict changed for any of the 60 items the previous check had already seen.

**UID0019 turned out to be fine, and so did the two items like it.** It was dropped by OQA-SEL-1 because its
`source_files` and `source_docs` counts differ, on the ground that the supplied-page builder pairs those lists
positionally. It is one file with two page references, and `_iter_oracle_refs` has an explicit branch for that
shape. Both pages resolve; the item is AVAILABLE. The same holds for UID0039 (val) and UID0064 (held-out). I
recorded the stratum disagreement this exposes rather than smoothing it over: `oqa_sel3.stratum` counts
`max(files, docs)` and calls these items multi-document, while `oqa_freeze_sets.stratum_of` counts `files` and
calls them single. Nothing depends on it — the stratification is a balancing device fixed in advance — but the
two scripts do disagree and a later reader should not have to discover that.

**The caps needed building, not verifying.** The user asked me to verify that the attempt and spend caps hold
across concurrent jobs and restarts. There were no such caps on the trainer's path: `oqa_api_smoke.py` had its
own per-process counters, and the campaign runs `scripts/train.py`. So I built a durable shared ledger that
claims attempts in blocks before each request, with `O_CREAT|O_EXCL` for atomicity, giving
`spent <= claimed <= cap` under concurrency and across restarts, plus per-job ceilings so one runaway arm-epoch
cannot eat what the other arms need.

**And then the verification of it failed on the cluster — because of my test, not the ledger.** Job 66272358's
fail-closed case made the claim directory `chmod 0500` and expected the write to be denied. `/nfs/turbo` does
not deny it: POSIX ACLs on the share grant the owner write access whatever the mode bits say. The condition was
never created, and my check reported the *ledger* failing when its own premise had failed. The job refused to
continue, which is the right behaviour for an unproven cap, and I would rather have this failure mode than the
opposite — but the diagnosis in the artifact was wrong until I fixed it. `fail_closed` now probes its premise
and runs a portable case that works on any filesystem. On the re-run the three checks pass on NFS: four
concurrent processes split exactly 23 of a 23 cap, a restarted process respent 0 of 9, and an unusable ledger
refused.

**The route is now proven in the process that spends.** The guard I wrote for the slow-update probe checked the
optimizer's config in its own process, which cannot establish where the trainer's router will dispatch. The
guard now runs inside `trainer.train()` and `eval_only`, reads back each role's live backend, base URL and
model, and proves the dispatch path at zero HTTP cost. The actor-only stages go further and require the
optimizer role to be *unusable* — no key loaded — which is a stronger statement than "we did not intend to call
it". Job 66272737's record shows both: the target pinned to EmpirioLabs `qwen3-5-4b` with the dispatch proven,
the optimizer with no key.

**Ten pairs, with a check before the send.** The user asked for ten paired questions per slow update and for the
rendered request to be checked against a conservative limit inside the tested prompt size, falling back to a
smaller subset in a predetermined order. The limit is 225,000 estimated tokens, chosen so that the prompt plus
the whole 16,384-token completion allowance (241,384) stays inside the 251,548 tokens job 66271290 demonstrably
processed. Rule OQA-PB-1 drops pairs from the least informative category first — `stable_success`, then
`improved`, then `persistent_fail`, then `regressed`, which is SkillOpt's own stated order of interest — and the
log records the pair count actually sent and whether guidance came back, per arm.

I should be plain about what that means in practice: at ten pairs the typical mix estimates to about 230,000
tokens, **above** the limit, so some slow updates will send nine pairs rather than ten. That is the mechanism
working as specified rather than a surprise, and it is visible in the log rather than inferred. The honest
summary of the size question is unchanged from last night: ten pairs sits *at* the proven point, not inside it,
and there is no demonstrated headroom for longer epochs or longer trajectories.

**Submitted as one chain, not three concurrent arms.** The 50 RPM limit is account-wide and one job with
`workers: 4` already offers about 70 RPM, so concurrency cannot raise throughput — it divides the same quota and
roughly triples the CPU job-hours for the same wall time. 66272737 (shared) through 66272749 (C.e4), every link
`afterok`. Computed need: 12,763 actor requests and $6.22, 102 DeepSeek calls and $3.49 at peak, about 5 CPU
job-hours. Nothing exceeds a ceiling.

One observation from the first three questions of the shared rollout, recorded now so it is not mistaken for a
late discovery: all three produced no `<answer>` tag and one reached the tool-turn cap. The checkpoint treats
those as rollout outcomes, which they are, but if the starting skill answers rarely then every arm's score sits
near the floor and the comparison has little to separate. The A40 pilot's 5 of 12 is not a guide here — FP8
makes this a new baseline.

## 2026-11-17 (end of day) — the campaign finished, and the instrument I built to protect it broke it

The three-arm comparison ran to completion. The headline is that arm B, Action Contracts Only, is the only arm
whose gate accepted a candidate — and the honest reading of that is **nothing**, which took some arithmetic to
establish and is the part worth writing down.

Arm B's accepted candidate scored 5 of 24 against S0's 4 of 24. One question. At that resolution a strict gate
accepts a candidate that is merely as good as S0 **37% of the time**. Nine gate evaluations happened in the
window where all three arms had full optimizer access, so if every single candidate were neutral we would expect
about **three** acceptances. We saw **one**. P(at most one | all neutral) = 0.141. So the campaign produced
fewer acceptances than chance predicts, and arm B's one is on the low side of noise rather than a signal. Had I
reported "the arm with action contracts was the only one to improve the skill" — which is literally true — it
would have been badly misleading.

What the campaign does show is that the three feedback channels produce genuinely different edits: epoch 1's
candidates were 4,174, 4,652 and 4,420 characters from the same 887-character starting skill, with different
content. The channels change what the optimizer writes. At 24 validation questions they do not change whether
the gate takes it, and they could not be shown to.

**And then the part I am least pleased about.** The spend ledger I built specifically to stop this campaign
overrunning its authorization is what truncated it. It enforces the cap on *claimed* money, and I never wrote
the other half: a process that exits never gave back the headroom it was still holding. Thirteen processes each
kept about one call's reserve plus block rounding, which over a $4.00 optimizer cap in $0.25 blocks came to
$1.81 — **45% of the authorization claimed and unspendable**. Once the claims reached the cap, the ledger
refused every further optimizer attempt, exactly as designed, while DeepSeek had been paid $2.19 of an
authorized $4.00. Four arm-epochs made no optimizer call at all. The trainer recorded `skip_no_patches` and
carried on, which is the right behaviour and also why it was invisible until I read the per-process records.

I had documented the waste when I wrote the thing: "the unused remainder of a dead process's block is LOST
rather than recycled — the conservative direction, and the reason blocks are small." The blocks *were* small in
absolute terms. They were 6% of the cap each, with thirteen processes to serve. "Small" was the wrong
comparison, and I wrote the sentence that should have caught it.

Worse, the damage is asymmetric: arm C lost two epochs, arms A and B one each. So the symmetric comparison is
epochs 1–2, not the four the user asked for.

The fix is a release record written at exit, netted out of the claim total, plus a block an order of magnitude
smaller. The counterfactual test is the one that matters: six short-lived processes spending $0.03 of a $0.20
cap all get through with the release, and the last is starved without it.

**Three other things I got wrong today, for the record.** I predicted some slow updates would send nine pairs
rather than ten; the first one sent ten at the worst-case category mix, because my per-pair size constant came
from a saved collection with a 171,394-character outlier and was twice the real figure. I said "the binding
constraint on wall time is the 50 RPM account limit"; it is per-request latency and the sequential tool loop,
and I was out by a factor of 3.2. And I wrote that "the optimizer's first edit is harmful in every condition"
after epoch 1, which the gate cannot support — it was the same resolution problem, noticed three hours too late.

The containment check took me three attempts to scope correctly, which is worth recording because the first two
attempts each produced a confident false alarm. Comparing the arms' latest reports flags the epoch-2-onward
divergence as a failure, but that divergence is the design. Restricting to M0 is not enough either, because
epochs 1 and 2 both run under M0 while their traces differ per arm. The comparable reports are the ones sharing
both the model and the trace, and intersecting on that key finds them without being told which epoch to look
at: 22 of 22 pass.

Everything expensive from the starved epochs survived — each still holds its 24-question training rollout and
both 10-question slow-update rollouts, and the contract-model evolution ran regardless because it is offline. So
completing them costs $1.26 of actor and $1.32 of optimizer against $3.06 and $1.81 remaining, which fits. That
is the user's call and I have not submitted it.

## 2026-11-17 (later) — the recovery: amendment, accounting fix, and four corrections to my own readout

The user raised the campaign's **total** ceilings to EmpirioLabs $16 / 30,000 attempts, DeepSeek $8 / 260
attempts, CPU 40 job-hours, 0 GPUs, *including everything already spent*, and authorized the recovery and the
planned evaluations without further approval. `docs/research/OFFICEQA_AUTHORIZATION.md` is now the single
record of the grants, the inclusion rule and the measured spend.

**The enforced optimizer cap is deliberately $7.95 / 259, not $8.00 / 260.** The slow-update size probe (job
66271290) spent 1 DeepSeek request and $0.0411 before the campaign ledger existed. A ceiling that covers the
whole campaign has to be enforced net of what did not pass through the ledger, or the cap enforces a subtotal.
There is no matching subtraction for the actor: the preflight that ran outside the ledger was key-free by
construction and `oqa_ledger_check.py` has no HTTP client at all.

### The accounting fix, finished

Releasing on clean exit was not enough. A request that was **sent** and whose reply never arrived has no usage
record, so it looks unspent — but the provider may have billed it, and handing that money back lets the next
process spend money that is already gone. The ledger now counts *resolved* attempts and retains one
`reserve_usd` per unresolved request. `oqa_ledger_reconcile.py` applies the same rule (OQA-REC-1) to a ledger
whose processes have already exited, which is the state the campaign's own ledger was in; it refuses any owner
it cannot show has finished, and `squeue` is consulted every time.

Applied: optimizer net claims **$4.0000 → $2.1913** and 100 → 66 attempts, target **$7.0000 → $4.9441** and
7,700 → 7,313. 52 release records. **Zero unresolved requests** in this campaign — every attempt has a
matching usage record — so nothing was retained on that account. A second run finds nothing left.

**And the reconciliation immediately exposed a disagreement I had not seen.** `oqa_ledger_read.py` then
reported FAIL: "target: $4.9441 spent against $4.9441 claimed". The two are not the same number. The ledger
adds `int(round(usd * 1e6))` per usage record, and that integer is what `reserve()` compares against the
claims; the audit summed raw floats, which over 7,313 records came out **26 microdollars higher**. So a
reconciliation that released down to the ledger's own figure looked like an overspend. The audit now checks the
invariant in micros and reports the float sum beside it; the reconciler retains the larger of the two, so no
reader can see spent > claimed whichever way it sums.

### Confirmed remaining, measured rather than assumed

`oqa_budget.py --headroom`: actor **22,687 attempts / $11.0559** left, optimizer **193 calls / $5.7676**,
**20.05 CPU core-hours of 40**. CPU has two readings and they differ by 2x because every job asked for 2 CPUs
— 9.98 hours of wall time summed over jobs, 19.95 core-hours. **Planning uses the larger one.**

### The audit: exactly four arm-epochs, and two things that only looked wrong

`oqa_recover.py audit` reads the artifacts *and* the ledger's per-process refusal records, because neither
alone can tell a starved stage from a quiet one. All 24 refusals sit in four jobs — 66273861 (C.e3), 66273862
(A.e4), 66273863 (B.e4), 66273864 (C.e4) — six each: 4 analyst, 1 slow update, 1 meta skill. **No other stage
was masked.**

Two shapes I checked by hand before writing the classifier, and the classifier now explains rather than flags:

* **arm B epoch 3 made 3 analyst calls, not 4.** No refusal in that job. It scored **0/24**, so there was no
  success group to reflect on. Legitimate control flow.
* **arm C epoch 1 made no ranking call.** Its 4 merged edits already fit the edit budget of 4, so there was
  nothing to rank. Legitimate control flow.

### Four hazards found by reading the code paths the recovery would take

Each would have gone unnoticed until the readout, and none was visible from the artifacts:

1. **`run_batch` resumes by id and never consults the skill for a row it already has.** That is what makes a
   saved rollout reusable — and it is how a recovery could reflect on traces produced by a superseded skill.
   `SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1` now reuses a row only when its recorded system prompt is the one this
   call would build, re-rolls the rest, preserves the superseded `results.jsonl` and keeps the live one
   canonical so no id appears twice. This is what lets "epoch 4's paired rollout is stale if and only if epoch
   3's gate accepted" be settled at run time instead of guessed.
2. **The slow update and the meta skill resume on the EXISTENCE of their result file.** A `no_content` left in
   place would have skipped the work a second time, silently, and charged nothing.
3. **`history.json` is loaded whole and appended to, with no trimming on resume.** `summary.json` counts
   accepts, rejects and skips straight out of it, so the readout would have double-counted exactly the epochs
   under repair.
4. **`oqa_lc_ir.freeze()` refuses to overwrite a frozen model with different content, and the body only
   warns.** A model file for an epoch whose traces can change would have stopped the evolution and left a file
   standing under a name that no longer described its epoch.

**And one I corrected in my own tool.** I first wrote `prepare` to take the rewind state from the previous
step's record. An epoch does not end with its ordinary step: the gated slow update runs after it and can
accept, and then that record's `current_score` is stale — which would hand the recovered gate the wrong
incumbent to beat. Every slow gate here rejected, so no number moved; the reconstruction is in because a tool
that is accidentally right is not right.

**Also corrected in my own test.** I asserted that the provenance guard would catch a changed
`max_tool_turns`. It failed, correctly: in offline mode the turn budget is in the per-round and user prompts,
not the system prompt. New rows now carry a settings fingerprint and the guard checks it when present; the
test states the real coverage instead of the coverage I assumed.

### The statistics: three corrections, one of which reverses a conclusion

`oqa_gate_stats.py` enumerates every gate from the artifacts so these figures cannot come from memory again.

* **13 gate evaluations, not 9** — 8 ordinary plus 5 gated slow updates. My count came from counting
  arm-epochs. My stated expectation of 2.99 did not even match my stated count: that is 8 x 0.371, and
  9 x 0.371 is **3.34**, as the user pointed out.
* **The threshold moved.** After arm B accepted at epoch 2 its incumbent rose to 5/24, so its two later gates
  needed 6/24 and faced P(accept | null) = 0.383, not 0.371. Expected acceptances against each gate's own
  incumbent: **4.86**. P(at most 1) = **0.020**.
* **"Nothing separates harmful from neutral" was right about four candidates and wrong about thirteen.** The
  acceptance count throws away all but one bit per gate. The scores do not: the candidates got **23 of 312**
  questions right where the null predicts **55**, P(total ≤ 23) = **1.3e-07**. The null is strongly
  disfavoured, in the direction that **the optimizer's edits made this actor worse on the selection set** — in
  all three arms alike, Original SkillOpt included. It does not separate the arms, and it does not turn arm
  B's acceptance into noise: 5/24 against 4/24 is a one-question margin this set cannot resolve.

The non-independence caveat applies to both tails and is stated once: all gates in an arm share the same 24
questions, so 0.020 could plausibly be several times larger, while a 32-question pooled deficit is far too
large for within-arm correlation to explain.

## 2026-11-17 (end of day) — the recovery finished; four epochs in all three arms

**Jobs 66323200–66323203, all COMPLETED**, 26:20 / 20:14 / 24:52 / 27:01 of wall time, **3.28 CPU
core-hours** between them. `oqa_recover.py verify`: **PASS**. The post-recovery audit reports **still starved:
none; recovered: A.4, B.4, C.3, C.4.**

### What the recovery cost, and why it was cheaper than planned

$0.68 of actor against the $1.26 the plan called certain, and 1,081 attempts against 1,981. The gate passes
used about **5.6 attempts per question** rather than the campaign average of 10.32, because a candidate that
answers nothing ends its tool loop early — which is itself a symptom of the finding below. Optimizer: 36 calls
and $1.2692, close to the $1.44 estimate.

**Every saved rollout was reused and nothing was re-rolled.** All four recovered epochs rejected their
candidates, so the skill in force at each epoch's end was unchanged and `rollout_curr`'s provenance held. The
guard reported, for each of the three directories per job, that the rows predate the settings fingerprint and
so their skill and search mode were checked but their turn budget could not be — which is honest and sound
here, because one config file fixes it.

**The reuse was faithful, and the prompt-budget log proves it.** Each recovered epoch's slow update rebuilt
*identical* pair and token counts: A.4 10→8 pairs at 261,376→205,051 tokens both times, C.3 10→7 at
252,996→207,927, B.4 10→10 at 181,849. Same inputs, same trimming decisions, same sizes.

**And it settles what the four `no_content` results were.** The original B.e4 sent 10 pairs at 181,849 tokens
— far inside the 225,000 limit — and got no guidance; B.e2 sent 10 at 223,815 and did. The size was never the
problem; the ledger was.

### The result: only arm B updated its skill

| arm | selected skill | size | what happened |
|---|---|---|---|
| A (Original SkillOpt) | **S0** | 887 B | 4 ordinary + 3 slow gates, all rejected |
| B (Action Contracts Only) | **step 2's skill** | 3,603 B | **accepted at epoch 2**, 5/24 vs 4/24 |
| C (Action Contracts + Stages) | **S0** | 887 B | 4 ordinary + 3 slow gates, all rejected |

7 skills requested, **3 distinct by digest, 2 by advice**: arms A's and C's last skills are S0 plus an empty
placeholder, advice digest identical to S0's.

**Arm C's epoch-3 slow update scored exactly 4/24, tying S0's baseline**, and was rejected only because
upstream's gate requires strict improvement. That is the concrete case behind the "accept on a tie" option,
which remains unauthorized and unapplied.

### The statistics over all 21 gates — the direction is now unmistakable

12 ordinary + 9 slow-update gates, **1 acceptance**. Expected under each gate's own incumbent: **7.85**;
P(at most 1) = **0.001**. Pooled over 504 question-level observations the candidates got **33** right where
the null predicts **89**, P(total ≤ 33) = **2.1e-13**.

So the optimizer's edits made this actor **worse** on the selection set, in all three arms including Original
SkillOpt. It does not separate the arms, and it does not make arm B's one-question acceptance noise. At
thirteen gates I recorded this finding as reversing my earlier "harmful versus neutral is indistinguishable";
at twenty-one it is not close.

### The accounting fix, proven in production

Ledger **PASS**: net claims equal recorded spend exactly in both roles, zero unresolved requests, zero reserve
breaches. The four recovery jobs **released their own unspent claims on clean exit** — the first time the fix
has run outside a test. Across the campaign, 46 optimizer attempts / $2.2395 and 456 target attempts / $2.2259
went back to the authorization instead of being stranded.

### Held-out, submitted and partly in

`66332133` S0 → `66332134` arm B's selected → `66332135` the exploratory placeholder variant, `afterok`,
frozen by digest first (`SKILLS_FROZEN.json`, record `f26ba852b9e59dc4`).

**S0 scores 3/60 = 0.0500 on the 60 held-out questions**, against 4/24 = 0.1667 on the gate set. Different
question sets, and the intervals overlap, so that gap is not yet a finding.

The third job is worth naming carefully: the 940-byte skill differs from S0 only by two HTML comment lines, so
its advice is identical. Its held-out number is **not a third arm's result** — it is an inadvertent replicate
of S0 under a slightly different system prompt, which makes it a read on the instrument's run-to-run
variability and nothing more.

### Four more of my own mistakes, all caught by running things

1. **The held-out submitter would have handed every job a path it had just deleted.** S0's path pointed into
   the temp tree the script extracts and removes on exit. Found by smoke-testing against a throwaway campaign
   root instead of waiting to run it for real. S0 is now a durable campaign artifact.
2. **I wrote a second advice-stripper with guessed markers** (`<!-- SLOW_UPDATE -->`) when the trainer writes
   `<!-- SLOW_UPDATE_START -->`. It stripped nothing, and the tool reported 3 skills distinct by advice where
   there are 2. `oqa_heldout.py` had the correct definition all along; the freezer now loads it rather than
   duplicating the concept, and raises if it cannot.
3. **The audit called the repaired epochs starved**, because a refusal record is permanent history. Left
   as it was, the record would have claimed the arms were still broken while `verify` said PASS.
4. **`verify` guessed how to resolve an archived file's path** and got every contracts entry wrong.

Plus two test errors of mine: asserting every archived file still exists (`prepare` removes the markers it
archives, deliberately), and dropping an `import re` while replacing the regexes.

## 2026-11-17 (late) — the diagnostic read of the finished three-arm campaign (rule OQA-DIAG-1)

Asked to prepare the evidence for a review of why the optimizer's edits scored badly, using only the saved
training and validation records while the held-out chain finished. Built `research/officeqa/oqa_diagnose.py`
(`gates`, `questions`, `replicates`, `contracts`, `chains`) and `research/cluster/oqa_diag_body.sh`, which
drops every API key before Python starts — the phase cost 0 attempts, $0.00 and 0.13 CPU core-hours over 15
jobs. Packet: `docs/research/OFFICEQA_DIAGNOSTIC_PACKET.md`. Final run job 66337036, commit `66b2b66`;
tests 2,215 passed / 9 skipped / 368 subtests (job 66337037).

**The finding that reframes the campaign.** Arms A and C rejected every candidate, so their in-force skill
never changed and every epoch re-ran the SAME document over the SAME 24 training questions. That makes
seven independent draws of one skill's score, because the actor runs at temperature 0.7 and **no sampling
seed is sent to the API** (there is no `seed` key in `openai_compatible_backend.py`). The seven scores are
1, 1, 2, 2, 3, 5, 6 — sd 1.95 questions, range 5. The gate accepted arm B's candidate on a margin of one
question. The spread of a fixed document covers nearly the whole range of the 21 candidates' scores. The
campaign measured its own noise floor for free and nobody had looked.

**Three more, each from the files.** (a) The gate was largely scoring termination: of 504 candidate
observations, 363 produced no answer at all and only 105 were wrong answers; a candidate's score correlates
with its commitment rate at r = 0.78 and with its document length at r = -0.08. There are 211 calls to a
nonexistent `answer` tool across the campaign and none in S0's own traces. (b) The only acceptance sits
inside the scorer's formatting tolerance: S0 answered "39.31%" where the ground truth was "39.31" and was
marked wrong; count it right and S0 is 5/24, the threshold is 5/24, arm B's candidate ties, and the campaign
has zero acceptances. (c) Not one of 101 proposed-edit entries names anything from the contract vocabulary;
arm B's 96 action-contract reports hold zero distinct diagnoses and one violated forecast in 2,676, and arm
C's 11 violated obligations are all in a single report.

**Deliberately left unread:** job 66332134's held-out number, although the job had finished, so that §9's
proposed next tests were written without it. Nothing here was tuned on held-out.

**Five mistakes of mine, each caught by a test or a smoke run before it reached the packet.**
1. `score or -1` treats a score of exactly 0.0 as missing, and two candidates measured 0/24 — so the
   recomputation check reported FAIL on exactly the two most interesting rows. Caught by a local smoke run
   before any job.
2. I read `agent_ok` as an execution-health flag. `rollout.py` sets `agent_ok = not fail_reason` on the
   EXECUTION reason and then writes a SCORER reason into the `fail_reason` field, so `agent_ok` is False for
   the commonest outcome in the campaign. That filed 354 of 528 observations as crashes. The classifier now
   reads the reason text, whose four shapes are the only four present.
3. My new test fixtures shadowed an existing `_row` helper and broke three passing tests (job 66336123).
   Renamed with a `_dg_` prefix — and my own rename then matched inside `skill_from_system(`.
4. The replicate scan counted epoch 1's training rollout three times and reported "3 draws, sd 0.00",
   which reads as a deterministic actor. It is one rollout injected into all three arms; the three files
   share one md5. Members are now identified by what the actor said.
5. `n_turns` counts conversation messages, not tool turns — it reaches 55 against a budget of 24. Comparing
   it to the budget said 5 of S0's questions hit the cap; the true number is 0, and only three observations
   in the whole campaign ran out.
Two further test failures were my expectations being wrong rather than the code: a miniature campaign's S0
baseline legitimately joins a replicate group when the fixture gives it the same question ids, and a
deterministic row builder makes every all-wrong fixture rollout the same measurement.

**Proposed next, costed, nothing run** (packet §9): repeat the exact S0 evaluation 8 times on the 24
selection questions (1,984 attempts, $1.26, 2.9 core-hours) to measure the spread on the gate's own split;
then, only if that spread allows it, one-edit-at-a-time on arm B's accepted candidate (12 or 20 evaluations,
$1.89 or $3.15). The power arithmetic says 5 draws per document resolves a 3.4-question difference and the
accepted candidate's margin was one question, so the honest recommendation is that more ablation draws at
n = 24 are not the useful next move — a better measurement is.

## 2026-11-17 (night) — forensics: the empty replies and the contract machinery (rule OQA-FOR-1)

Asked two questions about the frozen campaign: why attempts ended without an answer, and why the contract
reports produced so little corrective feedback. Built `research/officeqa/oqa_forensics.py`
(`noanswer`, `contracts`) and `research/cluster/oqa_forensics_body.sh`. Zero API calls, zero GPUs, 0.13 CPU
core-hours. Packet: `docs/research/OFFICEQA_FORENSICS_PACKET.md`. Job 66338091, commit `4cf50fc`; tests
2,223 passed / 9 skipped / 368 subtests (job 66338092).

**The thing I should have found a day earlier.** `research/cluster/oqa_campaign_body.sh` sets
`TARGET_OPENAI_COMPATIBLE_CALL_LOG`, so every job wrote `actor_calls.jsonl` with the raw provider reply —
finish_reason, content, reasoning_content, usage — for all 7,909 attempts across 16 jobs. I had been
reading the SAVED representation and treating its gaps as facts. The raw log's record count and
completion-token total match the spend ledger's for the same jobs exactly, so the two corroborate.

**What it says.** 331 of the 528 selection observations ended because one reply came back with
finish_reason `stop`, no content in any field the backend reads, and a median of 920 completion tokens
(min 96, max 4,076) generated and billed. Not one had zero tokens. `reasoning_content` is null in all
7,909 records, so "the answer went to the reasoning channel" is refuted for this provider — the tokens go
somewhere it does not expose at all. 533 such replies campaign-wide cost $0.2004 by the provider's own
`cost_usd`. "Prose but no answer" never occurs among terminal replies, and running the real extractor's
fallback plus the real scorer over the 7 truncated replies scores 0 of 7, so extraction missed nothing.
The unsupported `answer` tool (211 calls campaign-wide, 109 in 54 of the 528 episodes) costs a turn and
does not end an episode: 8 of those 54 still answered.

**Two corrections to yesterday's packet, both mine, both from misreading a field.** `last_finish_reason`
is the empty string in all 528 rows — the local-tools branch of `rollout.py` never assigns
`last_response_metadata`, and `compat_message_from_chat_message` drops the finish reason and the reasoning
channel — so my "0 truncations" was a count of nothing; the raw log shows 12, 7 inside the 528. And arm B's
"zero distinct diagnoses" is `... if condition != LC_ACTION else 0` in `oqa_reports.py`: a hard-coded
constant that says nothing about arm B.

**On the contracts.** The whole expressible space is 20 single-atom `effect` contracts; 5 were learned,
all in the first round, and 1 was later retired. Every atom is a 0-ary property of one tool observation's
text, so the failure behind 384 of 528 observations is not expressible as an action contract at all. In six
evolve rounds the checker found one counterexample (arm B epoch 3, UID0019 step 10), which retired
`read.effect.2` — the contract model correcting its own rule, not a diagnosis of the agent. Arm C's stage
layer produced exactly one actionable finding, a witnessed 1,000x unit error on UID0014, which reached the
optimizer byte-for-byte (verified against that job's own optimizer_calls.jsonl) — but arm A, with no
contract layer, wrote unit-and-scale edits in every epoch, so it is not a finding the ordinary analyst
lacked.

**The in-sample warning.** It fires on 96 of 96 reports per arm because `render_action` tests the item ID
against the model's `evidence_items`, which is a list of item ids, and the same 24 training questions were
drawn in all four epochs. The schedule itself was followed: epoch N's reports cite the model frozen at the
end of epoch N−1. By trace digest, epoch 1 is genuinely in-sample and epochs 2–4 are fresh — 0 of 24
identical in every epoch pair — except arm C's UID0019, whose epoch-3 record is byte-identical to the
shared epoch-1 record because both hold three EMPTY assistant messages and two identical tool calls. A
digest over a lossy record cannot establish that two attempts were the same attempt.

**My own mistakes this round.** (a) I derived each model's learning corpus from its `epoch` field; that
field records the last model-CHANGING decision, so every arm-C model says 1 and the corpus was shrunk to
epoch 1. Fixed to take it from the file name and backed by a model-independent cross-epoch identity
matrix, which gave the same answer. (b) My first `_dg_`-prefixed rename matched inside
`skill_from_system(`; caught by the test job.

### 2026-11-17, same evening — the held-out result, released under the protocol

The chain finished and was read once, after both evidence packets' proposed next tests had been written,
so nothing in them is tuned on it. S0 **3 of 60** (0.0500), arm B's validation-selected skill **6 of 60**
(0.1000), arm A's and C's last skill **8 of 60** (0.1333, exploratory). One question is 1.7 points.

The table carries its own refutation. The exploratory document differs from S0 by exactly two HTML comment
lines — the empty slow-update placeholder — so its advice digest is identical to S0's. **Two documents
that give the actor the same advice scored 3/60 and 8/60.** Arm B's three-question margin over S0 is
smaller than the five-question gap between two copies of the same advice. That is the held-out echo of the
training-split replicates (one fixed document, seven draws, 1 to 6 of 24, sd 1.95), and it is why the
primary comparison is reported as a number and not as a finding.

Spend reconciled: target 10,158 attempts / $6.8554 of 30,000 / $16.00; optimizer 103 / $3.5016 of
260 / $8.00; CPU 27.61 of 40 core-hours over 57 jobs; zero unresolved requests, zero reserve breaches,
ledger verdict PASS. Recorded alongside: the provider's own `cost_usd` is $3.2768 where the ledger charged
$5.3159 for the same 7,909 attempts, because the provider does not bill cache reads — the ledger is
conservative by about 38% on the actor side, and the ceilings are left as they are.

**One defect of mine, found by trying to use it.** `oqa_ledger_reconcile.py` refused to release the
held-out jobs' unused claims, reporting "recorded spend $6.8555 exceeds net claims $6.8554 — the invariant
spent <= claimed is BROKEN" on a ledger whose own read says PASS. I had taken the spend to be
max(per-record integer sum, ceil(float sum)) so that no reader could see spend exceed claims; once claims
have been reconciled down to equal the spend there is no slack, and the inflated figure exceeds the net
claim. It declared a sound ledger broken over 2.14 microdollars across 10,158 records. The spend is now
the per-record integer sum — what the ledger adds and the cap enforces — with the float excess held back
as a margin. My first replacement guard and its test were both wrong too: a flat 1,000-microdollar
tolerance (unreachable at small record counts, exceedable by rounding at large ones) and a test that
forged a one-cent gap by adding 0.01 to each stored `usd`, which moves both sums equally and proves
nothing. The bound is now half a microdollar per record, which is what the arithmetic allows.

## 2026-11-18 — the follow-up: the empty replies had answers in them, and one connected contract

Two bounded investigations under Grant 3 (24 EmpirioLabs attempts / $1, 6 DeepSeek / $2, 4 requested CPU
job-hours, zero GPUs, added to the cumulative ceilings). Report:
`docs/research/OFFICEQA_FOLLOWUP_PACKET.md`. Amendments A-1 and B-1 were recorded BEFORE either was
implemented, in `docs/research/OFFICEQA_IMPLEMENTATION_AMENDMENTS.md`.

**The finding.** Replaying two of the campaign's own recorded requests reproduced the empty terminal
reply and settled it: HTTP 200, `choices[0].message.content = null`, no tool calls, and
`choices[0].message.reasoning` holding 944 and 1,550 characters, each ending in a complete
`<answer>[10102000000, 4.73]</answer>`. The provider's field is `reasoning`. The client read `content`
and `reasoning_content`, found nothing, and the loop recorded "Model neither produced a tool request nor
a final answer" — 331 of 528 selection observations. **This corrects yesterday's packet**, which reported
`reasoning_content` null in all 7,909 logged attempts and took the hidden-reasoning hypothesis as
refuted; it was refuted only for that spelling. Every score in the campaign was taken through this
client, which puts a measurement defect upstream of the gate, the noise floor and the held-out result
alike.

The smallest correction reads the field — OFF by default so the frozen campaign's behaviour is unchanged
— and it is proved offline against the real captured bodies: with it off the content is `""` and the
cause is `empty_reply_content_null`; with it on, `_extract_answer` returns `[10102000000, 4.73]`.

**What the provider documents decided the second setting.** For qwen3-5-4b, `enable_thinking` defaults to
TRUE and `thinking_budget` to 4096, and the docs warn that "a very low max_tokens can be spent entirely
on reasoning" — the campaign omitted all of it and the two empty replies the selection rule picked sit at
4,043 and 4,028 completion tokens. Sending the documented top-level `enable_thinking: false` (not the
`chat_template_kwargs` extension our qwen backend uses, which this provider does not document) was
verified on the wire: transmitted, accepted, effective — the `reasoning` key vanishes and completion
tokens fall about five-fold. 0 of 9 empty replies against 2 of 9 historical, which is not a performance
finding.

**The connected contract.** DeepSeek proposed a conditional binding rule with a stated withdrawal; it
validated, then scored UNKNOWN on all 240 learning cells because one condition had no evaluator — a
defect in my own split between well-formedness and evaluability, now a second gate. With the predicate
implemented from the proposer's own stated reading (labelled GIVEN, not learned), the frozen model
decides 180 of 240 cells, 24 predictions were committed prefix-only, and Lean 4.20.0 accepted every
statement including that an unknown binding can never produce a violation. **No natural counterexample
and no actionable diagnosis on these records**: all 24 checking outcomes are unknown, and the one
apparent gain is an artifact of an operand reader that bound YEARS where the historical stage report had
bound the real money operands and named the 1,000x error. The representation is the right shape; its
value on this corpus was nil, and the reason is that the actor's written evidence use is mostly absent
from the traces — which is the same finding again.

**My own mistakes, each counted.** Two DeepSeek attempts went on an unset model name (the backend reads
`OPTIMIZER_OPENAI_COMPATIBLE_MODEL`; it defaulted to gpt-4o-mini and DeepSeek answered 400) and on a
2,000-token completion cap spent entirely on reasoning. Record-selection rule v1 produced an empty
checking set and was replaced by v2 with both recorded. My first conflict ordering flattened every
withdrawal into silence so a counterexample could never be seen, caught by a test. The probe's first
outcome taxonomy called a tool-calling turn's unread reasoning "content lost", corrected and 12 saved
outcomes re-derived offline. And the 23 probe sends are outside the spend ledger because the probe must
call the SDK directly to capture the raw body — enforced by an in-process cap instead, and accounted from
the provider's own cost_usd.

Tests 2,259 passed, 9 skipped, 368 subtests (job 66341945). CPU exactly 4.00 of 4 requested job-hours over
17 jobs, so no further job was submitted.

## 2026-11-18 (later) — the recovery is verified on complete episodes, and the extraction repair removes a false finding

**The headline: twelve complete episodes, twelve answers, twelve correct, and under the historical
generation payload every one of those answers came out of `message.reasoning`.** Rule OQA-REC-2, jobs
66346177 (tests), 66346233 (prep), 66346256 (episodes), 66346539/66346540 (S0), amendments A-2 and B-2.
Everything new is under `<campaign>/followup_20261119/`.

**The policy was narrowed before it was used.** Yesterday's fallback promoted the whole reasoning text
into `content`, which would make reasoning prose, a quoted example or one of two competing answers into
an episode's final answer. It now recovers only an unambiguous, complete, **terminal**
`<answer>…</answer>` block, recovers the block **alone**, and gives every other case its own name that
reaches the row's `termination_cause`. Eleven reply shapes are tested, including the real captured body
and the truncation cases. What the contract learner receives is the block and nothing else, and a test
asserts the saved trace contains exactly that one string.

**Two defects the tests caught before anything was paid for.** A-1's row initialisation had been inserted
after the first matching block in `rollout.py`, which is inside `_run_custom_search_process` — so every
local-tools rollout raised `NameError` when it built its row. And the adapter must return empty metadata
when it is called with the message alone, which is the compatibility guarantee for every other backend.
Both were found by job 66346074 and fixed before the episodes ran.

**A finding that connects the two workstreams.** In condition A, **0 of 45 captured replies had non-null
`content`** — not just the terminal ones. The client was losing the actor's entire written reasoning on
every turn, which is exactly why `conversation.json` holds empty assistant messages and why the
connected contract had almost nothing to read ("only 7 of 24 traces contain any number the actor wrote
in a message"). Counted in the new traces: a trace-based checker can read 20–36 characters of actor text
per episode under thinking-on, and 575–8,864 under `enable_thinking: false`. **So the contract layer
cannot be evaluated on traces recorded with thinking on**, and restoring that text means changing what
the actor does — a decision about the next experiment's design, not a defect.

**Workstream B removed a false finding rather than adding a true one.** The prototype now reuses
`oqa_lc_cells` / `oqa_lc_chain` (unmodified) and corrects seven things above them, each with a test: the
row-label column is not a data cell; years, dates, month fragments, list numbers, footnote markers and
prose digits are rejected by name while a four-digit number with an adjacent marker is still money; an
ambiguous cell match is silence; a cell binds to its own table; `cell_in_table` no longer reads
`(A and B) or True`, which was true for every input; and `nearer_scale_between` is UNKNOWN rather than
False when the scope was never observed. The stage link gained REOPENED, "no conversion required" (a
factor of 10^0 needs no explicit multiplication by one — the false alarm B-1 printed on UID0044) and
"stated but not carried through". A complete semantic bundle now freezes what the hypothesis *means* —
vocabulary, extractor and evaluator source digests, stage definitions, renderer, learner prompt,
learning manifest — because the model digest fixes only what it *says*, and B-1's evaluator was written
after the model was frozen.

**The accounting hole is closed.** The 23 probe sends are in the ledger, once, under a stage name the
backfill tool refuses to write twice. The arithmetic turned up a second wrong-field-name defect: the
backend reads `prompt_cache_hit_tokens`, which is DeepSeek's name, so for EmpirioLabs it sees no cache
and bills every prompt token — that is the whole of the campaign's 38% conservatism. Left uncorrected on
purpose, because fixing it would lower the computed charge and weaken the guard.
