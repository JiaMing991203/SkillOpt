# Research Development State

## 2026-11-18 — ALFWORLD CONTEXT-CHECKING STUDY: DONE -> `ALFWORLD_CONTEXT_PACKET.md`

**Rule AWC-1. Offline, 0 API calls, 0 GPUs, 0 gameplay, 0.6 of 2 authorized CPU job-hours. Jobs
66385774 (tests, 2,353 passed), 66385916 (Lean 4.20.0 rc 0). Artifacts
`$RUNS/20261118-alfworld-context/`; frozen manifest `5517922e569456b2`, bundle `db73dae18d5acf41`.**

* **The proposed context connection was ALREADY IMPLEMENTED** — amendment A4 §3.1–3.2 (the fact ledger,
  with supports, validity, withdrawal, contradiction, persistence as a named assumption and an
  ADDITIONAL-vs-OVERLAP label) and A4 §3.4 (`a4_reading.ReadingA4`, the lamp connection under
  `lamp_stays_on` and `same_place`, plus same-receptacle and temperature-override). Nothing was claimed
  as new and **no checker was changed**.
* **What was missing was the evidence**, and this study supplies it. **CORRECTED by the ALFWorld
  session's ORD-1 audit and re-verified here:** 736 is attempt ROWS; deduplicated by trace digest they
  are **432 distinct episodes** (304 rows are copies of an episode into another arm's directory). The
  context-aware reading changes **9 of 432 distinct episodes (2.1%)** - the 19 changed rows are 9
  episodes, tr:3287 being one episode copied eleven times. **8 of 9 make it STRICTER**, 1 more permissive
  (the lamp). **False completion claims introduced: 0. Removed: 1** (tr:2986, out-of-sample). 4 in-sample
  episodes, 5 out-of-sample.
* **ALSO CORRECTED: "current" is a PRE-A4 baseline.** AC-1 froze `a4_reading.py` at the identical digest
  `70b707f4f10a434f` and rendered the revised reading in BOTH arms (`lc_epoch.P_INSPECT`), so the 9
  changed episodes are a historical tracker revision, **not** something the live campaign lacked. That
  strengthens "do not study this again".
* **ALSO CORRECTED: the LOCAL Lean artifacts were the FAILED job's.** The compiled pair (job 66385916,
  rc 0) is now in the local run dir; the failing pair is kept as `*.FAILED_*`. The §8 compile result was
  always the cluster one and is unchanged.
* **The frozen 24-attempt diagnostic set shows ZERO differences** — a 24-attempt set cannot see a 2.6%
  phenomenon. That is the methodological result.
* **Two defects found while building the baseline:** `ReadingA4(rev=1)` does NOT reproduce the
  pre-revision reading for `look_at_obj_in_light` (clause (b) is ungated), so the honest baseline is
  `stage_common.Reading`; and my own ordinary-analysis lookup was one directory too shallow.
* **The one result worth acting on:** on tr:2986 the saved ordinary analysis blames a navigation loop in
  which the agent "never find[s] the goal object"; it had found both cups and put them in **cabinet 3
  and cabinet 6**. The stage reading contradicts the attribution. A cheap offline count of how often
  that happens is **proposed and not run**.
* **NOT MINE, read only:** the live ALFWorld implementation is uncommitted in
  `~/dev/lean_skillopt/SkillOpt-learned-contract` (branch `contract/learned-contract`). Its own state
  note `CONTRACT_IMPLEMENTATION_STATE.md` was **not modified**; that track is closed and owns its notes.
  Pending USER decisions that predate this study and are untouched: INSPECT rev 3, a lamp-state atom,
  a co-location relation.

---

**Last verified:** 2026-10-20 18:02 EDT (git, `squeue`; planning only, no jobs)
**Current milestone (user priority change, 2026-10-20):** the Lean-guided
contract-feedback prototype (Option B), milestones L0–L7 in
`IMPLEMENTATION_PLAN.md`. Planning is done; **L0 (design note) is next**. E1
is supporting work; E3 is deferred until genuine reports exist.
**Status (verified):** the first training smoke (condition A, strict parser +
traces, job 64359024) **completed**: every loop stage ran; the candidate tied
the baseline at 12/18 and the gate **rejected** it. The user accepted it as a
successful engineering milestone (2026-10-20). Nothing is running and **no GPU
job or paid API call is authorized**.

> Read this file first after a new session or any compaction, then verify it
> against git and Slurm (section B and section F) before acting. History lives
> in `DAILY_LOG.md`; measurements in `EXPERIMENT_LOG.md`.

## A. Current objective

Build the smallest end-to-end contract-checking pipeline (Proposal, not yet
implemented; details in `IMPLEMENTATION_PLAN.md`, "Lean-guided prototype
roadmap"):

1. Task requirements from each game's trusted PDDL `(:goal ...)`, never from
   the skill.
2. Trusted execution facts by deterministic offline replay of recorded
   actions through TextWorld's PDDL engine, checked against the recorded
   feedback, admissible commands and `won`.
3. A Lean 4 checker (core Lean, no Mathlib) with proven correctness, and an
   independent Python checker as the control.
4. PASS / FAIL / UNKNOWN reports with evidence, attached to SkillOpt's
   reflection input behind an opt-in option (scorer and gate unchanged).

First family: `look_at_obj_in_light`; design generic over all six ALFWorld
goal types. Test data: 48 traced episodes (all six types) from 64337548 and
64359024.

Baseline result for reference (job 64359024, verified): baseline 12/18,
candidate 12/18, gate rejected; ≈ $0.28 GPU, ≈ $0.008 DeepSeek.

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
| Main effort shifts to the Lean-guided prototype; E1 supporting (scorer and gate unchanged, must not block); E3 deferred until the pipeline produces genuine reports (then: original vs spec-only vs Python vs Lean) | user decision 2026-10-20 |
| Lean4Agent is reused only for patterns (JSON trace → Lean, subprocess bridge, digest format); not its Mathlib stack, LLM-generated specs, workflow mapping or trust axioms | proposal (from code review), pending user review |
| Run condition A (strict) + `trace_log` as the first training smoke | user-approved 2026-10-20; **done** (64359024) |
| `trace_log` and the optimizer call log may be on in any condition (diagnostic only) | verified in code and tests (64358982); per-run choice |

## F. Active operations and approvals

- **Active Slurm jobs:** none (`squeue` empty, 18:02).
- **Authorized but not executed:** none.
- **Pending user decisions:** (1) push the local docs commits (`cfeb2b8` and
  this planning update); (2) start implementation of L0–L2 (local code plus
  one small CPU job: replay + oracle tests, no GPU/API); (3) install a pinned
  Lean toolchain on the cluster (CPU setup job, user space, no Mathlib);
  (4) when to submit E1 (3 rollout jobs, ≈ $0.33–0.46); (5) call-log
  redaction hardening.
- **Not authorized:** any GPU job, paid API call, new optimization run,
  E3, change to scorer/gate/experimental semantics, Lean toolchain install.

## G. Next actions

1. User review of the roadmap (`IMPLEMENTATION_PLAN.md`) and the open
   research uncertainty "C vs D may be indistinguishable by construction".
2. On approval: L0 design note; L1 replay + evidence JSON; L2 goal
   extraction; oracle tests on the 48 traced episodes (CPU job).
3. Then L3 Python checker, L4 Lean package (after toolchain approval).
4. Supporting: E1 when the user asks for it. Later: record pre-implementation
   research decisions (Option B rationale).

## H. Evidence and references

`EXPERIMENT_LOG.md` (all jobs and measurements) · `DAILY_LOG.md` (history and
reasons) · `IMPLEMENTATION_PLAN.md` (milestones, next experiment, deferred
work) · `BASELINE_ARCHITECTURE.md` (code paths, conditions A/B/C) ·
`BASELINE_SETUP.md` (workflow, billing, networking) · `jobs.tsv` (submissions)
· fetched runs in `$LOCAL_RUNS` (outside git).

## I. Handoff warnings

- Lean-guided work is now the main track, but implementation (L0–L2) awaits
  the user's go-ahead and the Lean toolchain install needs approval. Never
  derive contracts from the skill; keep the condition-A path byte-identical
  when contract feedback is off; never change the scorer or the gate.
- Do not resubmit failed or finished GPU jobs automatically; check `jobs.tsv`
  and `sacct` first.
- Do not change model, sampling, token cap, workers, evaluator, skill-update
  rules or splits without an explicitly approved new condition.
- Do not confuse source-code edits (ours) with SkillOpt's skill edits (DeepSeek's).
- `research/cluster/site.env`, secrets and run directories (which contain
  absolute cluster paths) never go into git; the fork is public.
- Earlier approvals cover only their original scope; a compaction summary
  never authorizes jobs, paid API calls or pushes.
