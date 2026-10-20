# Experiment Log

Every cluster job gets an entry: purpose, commit, job id, run directory,
resources, task/seed selection, outcome, and observations. Labels:
**Verified** (seen in outputs), **Interpretation**, **Speculation**.
Machine-readable submission records: `docs/research/jobs.tsv`.
Fetched run directories: `$LOCAL_RUNS` (outside git). Machine-specific
details (account, paths) stay in the private `site.env`, not here; runs
record only its SHA-256 (`manifest.json → site_env_sha256`).

---

## 2026-10-19 — Environment inspection (no jobs)

- **Purpose:** Phase 1 discovery on the login node, read-only.
- **Commands:** `sacctmgr`, `sinfo`, `module avail`, `conda env list`, `ls`,
  `curl` to `api.deepseek.com` and `huggingface.co`, reads of the installed
  vLLM 0.25.1 source (network binding).
- **Verified:** GPU partition with 8 × A40 nodes available to our account; no
  Qwen3.5-4B in cache; no ALFWorld data; DeepSeek reachable from the login
  node (401 without key); home quota nearly full; vLLM's single-GPU executor
  uses the node IP for its rendezvous unless `VLLM_HOST_IP` is set.
- **Changes on cluster:** none.
- **Compute used:** none. **API calls:** none.

## 2026-10-19 — Deployment of `8c19f7b` (no job)

- **Purpose:** first deployment of the research branch to the cluster.
- **Commit:** `8c19f7b45a11bec5597d33bd229c7c8566f3a02e`   **Snapshot:** `snapshots/8c19f7b45a11`
- **Verified:** `deploy.sh --check` READY (fork readable over HTTPS without
  credentials; partitions and account visible; secrets file present, mode
  600); clone + detached worktree created; snapshot HEAD equals the commit;
  no modified tracked files. Private `site.env` uploaded to the cluster config dir.

## 2026-10-20 — setup_env (job 64323201)

- **Purpose:** create the controller and vLLM envs, download Qwen3.5-4B at the
  pinned revision, download ALFWorld data, run the data census.
- **Commit / snapshot:** `8c19f7b` / `snapshots/8c19f7b45a11`
- **Resources requested:** CPU partition, 4 cores, 16 GB, 2 h.
- **Run dir:** `runs/20261019-230827_setup_env_64323201` (fetched locally)
- **Resources used:** 2 min 3 s elapsed, 53 s CPU, MaxRSS 1.8 GB (sacct).
- **Outcome:** **FAILED** (exit 1) during the controller `pip install`.
- **Verified cause:** `alfworld` → `textworld[pddl]` → `fast-downward-textworld`
  (sdist only). Its `setup.py` runs `subprocess.check_call(['python', 'build.py', ...])`.
  The job called `$CTL_ENV/bin/python -m pip` without activating the env, so
  no `python` was on `PATH`: `FileNotFoundError: [Errno 2] No such file or
  directory: 'python'`. The build also compiles Fast Downward (cmake ≥ 3.12,
  make, C++11). The login node has cmake 3.26.5 and gcc 8.5 (RHEL 8.10);
  compute nodes are assumed to match, not verified.
- **State left behind:** env `skillopt` created by this job (Python 3.11 + pip
  only; conda history 23:10:09); nothing else installed; no vLLM env, model,
  or ALFWorld data. The sanitized manifest was correct (no account, node,
  or paths).
- **Fix (commit after 8c19f7b):** installs run with the env's `bin` first on
  `PATH`. Envs created by `setup_env` get a marker file; only marked,
  incomplete envs are resumed, and unmarked envs are never modified. The
  partial env predates the marker and must be removed before rerunning.

## 2026-10-20 — setup_env rerun (job 64323909)

- **Purpose:** rerun of job 64323201 after the PATH fix.
- **Commit / snapshot:** `ffb1d27` (fix `70de16f` + jobs.tsv) / `snapshots/ffb1d27f5c65`
- **Pre-step (approved):** removed the partial controller env left by 64323201
  (its conda history held only the create command; base packages only).
- **Resources requested / used:** CPU, 4 cores, 16 GB, 2 h / **20 min 30 s**
  elapsed, 10 min 47 s CPU, **MaxRSS 16.0 GiB (at the request limit; no OOM
  kill)**. Peak step unknown; candidates are the vLLM/torch wheel install and
  the HF download. Use 32 GB if setup is ever rerun from scratch.
- **Run dir:** `runs/20261019-231519_setup_env_64323909` (fetched locally)
- **Outcome:** **COMPLETED** (exit 0). Step times: controller env 3 m 12 s;
  vLLM env 14 m 1 s; Qwen download 1 m 15 s; ALFWorld download 1 m 37 s.
- **Verified:**
  - Controller: `controller imports ok` (alfworld, textworld, gymnasium,
    omegaconf, `skillopt.envs.alfworld.adapter`). Versions: alfworld 0.4.2,
    textworld 1.7.0, fast_downward_textworld 20.6.4 (built from source),
    gymnasium 1.4.0, numpy 2.4.6, omegaconf 2.4.0, json_repair 0.64.1,
    **openai 3.35.1** (a newer major version than the 2.x in other envs; see tests).
  - vLLM env: vllm 0.25.1, torch 2.11.0 (**CUDA 13.0 build**), transformers
    5.20.2, flashinfer 0.6.13.
  - Qwen3.5-4B at `851bf6e8…`: 2/2 safetensors shards present, 11 GB.
  - ALFWorld: all 39 / 18 / 134 split gamefiles exist under `$ALFWORLD_DATA`.
  - Data census: games per split train 3553, **valid_seen 140**, **valid_unseen
    134**, valid_train 200. The `split_{train,val,test}.json` files cited by
    the manifest are **not present**. The 18 val items are at positions
    0, 1, 10, 27, 28, 30, 51, 52, 69, 71, 77, 92, 98, 100, 113, 114, 118, 122
    of sorted `valid_seen`: a scattered subset, not a prefix.
  - Both envs carry the created/complete markers; leak guard passed.
- **Untested risk:** the CUDA 13.0 torch build needs a recent NVIDIA driver on
  the A40 nodes. The other project's env uses the same build, which suggests
  compatibility. `STAGE=vllm` will verify it.

## 2026-10-20 — run_tests (job 64326614)

- **Purpose:** focused CPU tests in the new controller env.
- **Commit / snapshot:** `ffb1d27` / `snapshots/ffb1d27f5c65`
- **Resources requested / used:** CPU, 2 cores, 8 GB, 30 min / 6 s elapsed, MaxRSS 106 MB.
- **Run dir:** `runs/*_64326614` (fetched locally; `pytest.xml`)
- **Outcome:** **COMPLETED**: **104 passed**, 0 failed, 0 errors, 0 skipped
  (2.5 s). Per file: research_configs 4, research_summarize_run 5, gate 22,
  gate_no_regression 19, alfworld_paths 4, openai_compatible_backend 4,
  openai_compatible_config 17, qwen_backend 29. 8 warnings, all the expected
  "thinking mode left to server" warning from tests of the unpinned mode.
- **Interpretation:** the research configs change only wiring/scale; the
  fallback classifier matches rollout.py's sentinels; the backend unit tests
  pass under openai 3.35.1. Those tests mock the HTTP client, so a real
  DeepSeek call through openai 3.x is still unverified (`check_compute`).

## 2026-10-20 — check_compute (job 64328962)

- **Purpose:** DeepSeek from a CPU compute node, through SkillOpt's backend.
- **Commit / snapshot:** `d129260` / `snapshots/d129260cd916`; `OPTIMIZER_MODEL=deepseek-flash` (passed at submit).
- **Resources requested / used:** CPU, 1 core, 4 GB, 15 min / 10 s elapsed, MaxRSS 86 MB.
- **Run dir:** `runs/20261020-001540_check_compute_64328962` (fetched locally)
- **Outcome:** **COMPLETED, preflight PASS.**
- **Verified, connectivity:** compute node reaches `api.deepseek.com` (401
  unauthenticated, as expected) and `huggingface.co` (200). `GET /models` with
  the key lists `deepseek-flash` and `deepseek-v4-pro`.
- **Verified, SkillOpt integration** (2 paid calls, both via
  `openai_compatible_backend.chat_optimizer` configured from the research YAML):
  - Request fields sent by SkillOpt: `model`, `max_tokens` (16384), `messages`.
    No temperature, `reasoning_effort` or `extra_body`.
  - `resp.model` = `deepseek-flash` (the alias is echoed back; the underlying
    version is **not** exposed in responses). `system_fingerprint`
    `e72d3c7f663d3e1383b00b3884cb4501`.
  - **Thinking active** in both calls (`reasoning_content` present; 9 and 35
    reasoning tokens). Reasoning tokens are included in `completion_tokens`
    (11 and 66), so they are billed as output.
  - SkillOpt's returned text equals `message.content` exactly and contains no
    reasoning text: reasoning is excluded from what the optimizer pipeline sees.
  - JSON probe: SkillOpt's `extract_json` parsed `{"edits": [{"op": "append", ...}]}`.
  - Usage: 125 prompt + 77 completion tokens in total; latency 0.9–1.0 s.
    Estimated cost **$0.000065** (off-peak rates; 04:15 UTC is off-peak).
- **Leak guard:** passed (exit 0). Its final log line is missing from
  `job.log` and the Slurm `.out`. **Interpretation:** the background `tee`
  is killed at job end before it flushes. An independent check found 0 files
  with the key and 0 settings files. To fix later: wait for `tee` before exit.
- **Note:** `job.log` prints the compute node's hostname (curl check line).
  Run dirs are private artifacts.

## 2026-10-20 — smoke STAGE=vllm (job 64329002) — FAILED; sequence stopped

- **Purpose:** start vLLM with Qwen3.5-4B on one A40; verify CUDA, model
  loading, loopback networking, and an ALFWorld-format `<action>` response.
- **Commit / snapshot:** `d129260` / `snapshots/d129260cd916`
- **Resources requested / used:** spgpu, 1 A40, 8 cores, 64 GB, 1 h / queued
  18 min, ran **3 min 43 s**, MaxRSS 13.4 GB, 2 min 46 s CPU. Peak GPU memory
  9.8 GiB of 46 GiB (45 samples).
- **Run dir:** `runs/20261020-003503_smoke_vllm_64329002` (fetched locally:
  `vllm.log`, `job.log`, `gpu_usage.csv`, Slurm `.out`, `sacct.txt`)
- **Outcome:** **FAILED** (exit 1). `vLLM exited during startup`.
- **Verified (from `vllm.log`):**
  - vLLM 0.25.1 started the V1 engine. The CUDA 13.0 torch build runs on the
    A40 node: weights loaded (2/2 shards, **7.99 GiB**, 14 s) and torch.compile
    finished (45 s; cached under the project vLLM cache).
  - Then, during memory profiling (`determine_available_memory` →
    `_dummy_sampler_run`), vLLM's top-k/top-p sampler ("Using FlashInfer for
    top-p & top-k sampling") called `flashinfer.sampling`. That
    **JIT-compiles** its sampling module, which needs `nvcc`:
    `RuntimeError: Could not find nvcc and default cuda_home='/usr/local/cuda'
    doesn't exist`. The job loads no CUDA module. The `flashinfer-cubin`
    package did not provide this kernel precompiled.
  - Not reached: KV-cache allocation, CUDA-graph capture, first inference,
    listener (loopback) check, `<action>` preflight.
- **Also observed:**
  - vLLM warned about six unknown `VLLM_*` variables: our own names, exported
    via `set -a`. One, `VLLM_PORT`, is a **real** vLLM setting (base port
    for internal communication). We set it to the HTTP port, and torch's
    rendezvous then used port+1 (39824). Not the cause of this failure, but a
    port-collision risk.
  - c10d warning: IPv6 `localhost` socket not supported (errno 97). It fell
    back without error; probably harmless.
  - `manifest.json` / `gpus.txt` / pip freezes were not written, because they
    ran after vLLM startup. The driver version is therefore unrecorded.
- **Context (read-only):** the other project's vLLM 0.25.1 Slurm scripts run
  `module load cuda/12.6.3` before starting vLLM, which provides `nvcc`.
- **Not done, per failure policy:** no GPU retry; STAGE=rollout, the
  concurrency rollout and the training smoke were **not submitted**. No
  DeepSeek calls were made by this job.
- **Fix options (need review; both affect how the target is served):**
  1. `VLLM_USE_FLASHINFER_SAMPLER=0`: vLLM's PyTorch top-k/top-p sampler.
     Same sampling semantics, different kernel/RNG path, no compiler needed.
  2. `module load cuda/<ver>` in `smoke.sbatch` so FlashInfer can JIT-compile.
     This keeps vLLM's default sampler. It needs a version choice (torch is
     cu130; the cluster offers 12.6.3 / 12.8.x / 13.1.0 / 13.2.1), adds
     first-start compile time, and needs FlashInfer's JIT cache moved off home.
- **Local, undeployed infra fixes (commit after this entry; for review):**
  our variables renamed to `LSO_VLLM_*` (removes the `VLLM_PORT` collision);
  manifest/freezes written before vLLM starts; a single EXIT handler that
  stops vLLM/GPU sampler and waits for `tee`. None change model behavior.

## 2026-10-20 — run_tests (job 64330068)

- Commit `a945bb2` (native sampler + LSO_VLLM_* infra). **105 passed**. The
  manifest records `target.vllm_env = {VLLM_HOST_IP: 127.0.0.1,
  VLLM_USE_FLASHINFER_SAMPLER: 0}`. The leak-guard final line now reaches
  `job.log`, so the exit-flush fix works.

## 2026-10-20 — smoke STAGE=vllm rerun (job 64330104) — FAILED at network check; sequence stopped

- **Purpose:** rerun of 64329002 with the native sampler.
- **Commit / snapshot:** `a945bb2` / `snapshots/a945bb2b6198`
- **Resources requested / used:** spgpu, 1 A40, 8 cores, 64 GB, 1 h / queued
  <1 min, ran **3 min 58 s**, MaxRSS 13.9 GB, 2 min 59 s CPU.
- **Run dir:** `runs/20261020-010644_smoke_vllm_64330104` (fetched locally)
- **Outcome:** **FAILED** (exit 1) by our fail-closed listener check.
  `vLLM has a non-loopback TCP listener`. vLLM was then stopped by the EXIT handler.
- **Verified: model loading and server initialization succeeded.**
  - GPU: NVIDIA A40, driver **595.71.05**, 46,068 MiB.
  - **Native sampler selected:** `FlashInfer top-p/top-k sampling disabled
    via VLLM_USE_FLASHINFER_SAMPLER=0.` The 64329002 failure is resolved.
  - Weights 13.9 s (7.99 GiB). torch.compile 46.4 s (recompiled, not cached).
    Engine init (profiling, KV cache, warm-up) 145.8 s. CUDA graphs captured
    in 6 s (0.55 GiB).
  - KV cache 27.92 GiB = **859,911 tokens**, max concurrency 26× at 32k-token
    requests.
  - **Startup to ready: ~230 s.** `/v1/models` answered with the per-job key.
  - Peak GPU memory **38.97 GiB of 46 GiB**, as expected with
    `--gpu-memory-utilization 0.85` (vLLM pre-allocates).
  - Benign warning: optional `deep_gemm` failed to import (no CUDA toolkit);
    it is not needed on A40.
- **Not reached:** inference (target preflight), `check_vllm_sampler` (the
  log line above confirms the sampler anyway), any ALFWorld interaction. No
  DeepSeek calls.
- **Listener check result (`vllm_listeners.txt`):**
  - HTTP API: `127.0.0.1:<port>` ✓ (vLLM API server process).
  - Engine-core process: **7 listeners on the node's network IP** and **1 on
    `0.0.0.0`** (the port that matches `distributed_init_method=tcp://127.0.0.1:36883`).
  - **Interpretation** (consistent with vLLM's `parallel_state.py`, not proven
    per socket): the 7 are Gloo listeners, from the CPU process groups vLLM
    creates alongside NCCL. Gloo binds to the hostname's interface unless
    `GLOO_SOCKET_IFNAME` is set, and `VLLM_HOST_IP` does not affect it. The
    `0.0.0.0` socket is torch's c10d TCPStore (rendezvous) server, which
    listens on all interfaces even when the init address is 127.0.0.1.
  - These sockets are torch.distributed internals, not the model API. The
    model API is loopback-only and requires the per-job key.
- **Options (need review):**
  1. Set `GLOO_SOCKET_IFNAME=lo` for the vLLM process (expected to move the 7
     Gloo listeners to loopback; untested). Accept the single TCPStore socket
     on `0.0.0.0` (port taken from the logged `distributed_init_method`) as a
     documented exception, and keep failing on anything else.
  2. Narrow the check to "the HTTP API must be loopback". Record all other
     listeners as known torch internals. This is simplest but accepts 8
     unauthenticated internal sockets reachable from the cluster network for
     the job's lifetime (typical of PyTorch jobs; main risk is disruption,
     not model access).
  3. Investigate binding TCPStore to loopback as well (may not be configurable
     by environment in torch 2.11).
- **Not done, per instructions:** no retry; STAGE=rollout not submitted.

## 2026-10-20 — run_tests (job 64330988)

- Commit `0724e27` (Gloo loopback, listener classifier, probe window).
  **113 passed** (includes 8 classifier tests built from 64330104's socket layout).

## 2026-10-20 — smoke STAGE=vllm (job 64331018) — network UNRESOLVED (exit 3); rollout not run

- **Purpose:** validate `GLOO_SOCKET_IFNAME=lo`, inspect the remaining
  sockets, and test their reachability from outside the node.
- **Commit / snapshot:** `0724e27` / `snapshots/0724e27fd50c`
- **Resources requested / used:** spgpu, 1 A40, 8 cores, 64 GB, 1 h / ran
  **7 min 34 s** (includes the 180 s probe window), 2 min 52 s CPU. Peak GPU
  memory 38.98 / 46 GiB.
- **Run dir:** `runs/20261020-014508_smoke_vllm_64331018` (fetched locally,
  including `vllm_listeners.json`, `probe_window.json`, `probe_from_login.json`)
- **Outcome:** exit 3 by design. `network status: unresolved`; no agent stage ran.
- **Verified, model loading:** weights 15.8 s; torch.compile 54.9 s (again not
  served from cache); engine init 165.3 s; KV cache 27.92 GiB. **Ready after
  ~250 s.** Native sampler confirmed (`vllm_sampler.txt` = native). 0 ERROR
  lines in `vllm.log`.
- **Verified, inference (target preflight PASS):** one ALFWorld-format prompt
  (initial skill + template) through SkillOpt's `qwen_chat` backend (thinking
  disabled, temperature 0.7, max_tokens 8000). 973 prompt / 317 completion
  tokens in 5.78 s (about 55 completion tokens/s for one request, prefill
  included). HTTP 200. Extracted action `go to countertop 1` is admissible.
- **Verified, formatting finding:** the response contains `</think>` and a
  well-formed `<action>…</action>`, but **no opening `<think>`**. Qwen's chat
  template pre-fills `<think>\n\n</think>\n\n` when `enable_thinking=false`
  (`chat_template.jinja` line 150). Consequences (from code): the action parses
  fine, but `rollout._extract_think` (`<think>(.*?)</think>`) would return None,
  so trajectory steps would have `reasoning: null`. The analyst's
  `fmt_trajectory` uses only `reasoning`/`action`/`env_feedback`, so the
  optimizer would not see the agent's reasoning. The vendored validity flag
  would also be 0 (it is unused for scoring). One sample only; an episode is
  needed to see whether this is systematic.
- **Verified, sockets** (`vllm_listeners.json`; probe from a login node to
  each of the node's 3 addresses, 24 connects):

  | Socket | Created by | Bound to | From login node |
  |---|---|---|---|
  | HTTP API | vLLM API server (`--host 127.0.0.1`) | 127.0.0.1 | refused |
  | 6 Gloo CPU-group listeners | torch ProcessGroupGloo (vLLM `parallel_state`) | **127.0.0.1** (was node IP in 64330104) | refused |
  | rendezvous store | torch c10d `TCPStore` server (`rendezvous._create_c10d_store`, rank 0) | **0.0.0.0** | **OPEN on all 3 addresses** |

  The rendezvous port matches `distributed_init_method=tcp://127.0.0.1:<port>`
  in `vllm.log`. The 64330104 layout had 7 Gloo listeners and this run had 6.
  The count difference is unexplained, but all are loopback now.
- **Code findings (torch 2.11 / vLLM 0.25.1, installed copies):** vLLM's
  single-GPU executor always uses `tcp://<VLLM_HOST_IP>:<free port>`, with no
  setting to change it. torch's `tcp://` handler creates a `TCPStore` server on
  rank 0. `TCPStoreOptions` has `port` but **no bind address**. The only
  supported restriction is `masterListenFd` (a pre-bound socket), which the
  `tcp://` path does not expose. There is therefore no supported environment
  or config setting that restricts this listener without changing vLLM's code
  path.
- **Great Lakes isolation:** ARC's public documentation describes the node
  networks (Ethernet + InfiniBand) but no per-job or per-user firewalling. The
  probe shows the store is reachable from a login node, which all cluster
  users share.

## 2026-10-20 — vLLM 0.28.0 compatibility review (no job)

- **Verified (GitHub, tag source):** PR #50999 ("Use file:// rendezvous for
  single-node executors") merged as `11ba93f`, which is an ancestor of
  v0.28.0 (0 behind) and not in v0.25.1. At v0.28.0,
  `UniProcExecutor._distributed_args` uses `get_file_store_init_method()`,
  giving `file://{tempfile.gettempdir()}/vllm_dist_<uuid>`, unless ROCm AITER
  custom all-reduce is on. `parallel_state` builds a `torch.distributed.FileStore`.
- **Verified (PyPI):** vllm 0.28.0 pins torch==2.13.0 (cuda-toolkit 13.0.3),
  torchvision 0.28.0, torchaudio 2.11.0 (declares no torch pin),
  flashinfer-python 0.6.16.post3, transformers>=5.5.3; Python 3.10–3.14.
- **Release-notes and source review** (subagent, code reading only): one
  relevant engine default changed. Prefix caching is now on for hybrid models
  such as Qwen3.5 (PR #50991), so it is pinned off for parity (0.25.1 logged
  `enable_prefix_caching=False`). Unchanged: sampler and default sampling
  parameters, generation-config logic, no auto reasoning parser, chat-template
  logic, our CLI flags and env vars, sm_86 in the CUDA 13 arch list.
- Decision: new env for 0.28.0, 0.25.1 env preserved; see BASELINE_SETUP §6b.

## 2026-10-20 — setup_vllm_env 0.28.0 (job 64332646) and run_tests (job 64332647)

- **Commit / snapshot:** `3dfaff9` / `snapshots/3dfaff9becb1`
- **setup_vllm_env:** CPU, 4 cores, 32 GB, 2 h requested / **17 min 11 s**,
  2 min 51 s CPU. COMPLETED. New env `skillopt-vllm-0.28.0` (marked created
  and complete). `vllm_env_check.json` (installed files): vllm 0.28.0, torch
  2.13.0 (CUDA 13.0), transformers 5.20.2, torchaudio 2.11.0, torchvision
  0.28.0, flashinfer-python 0.6.16.post3. `uniproc_uses_file_store`,
  `file_store_uses_tempdir`, `qwen3_5_registered`,
  `native_sampler_message_present` are all **true**.
- `vllm serve --help=all` exited 1 on the CPU node with "Failed to infer
  device type". Building the help needs a GPU, so the job skipped the flag
  check as designed. The flags were then verified in the installed source
  (`engine/arg_utils.py` fields; `BooleanOptionalAction` provides
  `--no-enable-prefix-caching`; `host`/`port` in `cli_args.py`).
- **0.25.1 env untouched:** `vllm-0.25.1.dist-info` present; conda history
  unchanged since 2026-10-19 23:19.
- **run_tests:** **114 passed** (8 s).

## 2026-10-20 — smoke STAGE=vllm on vLLM 0.28.0 (job 64332934) — PASS

- **Commit / snapshot:** `3dfaff9` / `snapshots/3dfaff9becb1`; env `skillopt-vllm-0.28.0`
- **Resources:** spgpu, 1 A40 (driver 595.71.05), 8 cores, 64 GB, 1 h /
  **7 min 15 s** (includes the 180 s probe window), 3 min 1 s CPU. COMPLETED, exit 0.
- **Run dir:** `runs/20261020-024456_smoke_vllm_64332934` (fetched locally)
- **Verified, model loading:** weights 16.0 s (7.99 GiB); torch.compile 27.3 s;
  engine init 132.3 s. **Ready after ~240 s.** KV cache 27.94 GiB = 860,904
  tokens (0.25.1: 859,911). Peak GPU memory **37.8 GiB / 46 GiB**. 0 ERROR
  lines. Warnings: the known optional `deep_gemm` import, and the
  force-kill of EngineCore at our shutdown.
- **Verified, serving settings:** `distributed_init_method=file://<private
  tmpdir>/vllm_dist_<uuid>` (`vllm_rendezvous.json`: method file, store in the
  private dir, mode 700). Native sampler. `enable_prefix_caching=False`. No
  sampling-default override warning. FLASH_ATTN backend.
- **Verified, network:** `vllm_listeners.json` status **pass**: 7 listeners,
  all `127.0.0.1` (HTTP API + 6 Gloo), 0 rendezvous store, 0 unexpected.
  External probe from a login node: all 21 connects (7 ports × 3 node
  addresses) **refused**.
- **Verified, inference:** preflight PASS. 973 prompt / 332 completion
  tokens in **5.94 s** (~56 tokens/s single request). HTTP 200. Action
  `go to countertop 1` is admissible. This reply had **no think tags at all**
  (0 `<think>`, 0 `</think>`). The 0.25.1 reply to the same prompt had
  `</think>` only. Sampling at temperature 0.7 varies per reply.

## 2026-10-20 — smoke STAGE=rollout, 1 ALFWorld episode (job 64333791) — first complete trajectory

- **Commit / snapshot:** `3dfaff9` / `snapshots/3dfaff9becb1`; vLLM 0.28.0,
  native sampler, prefix caching off, file rendezvous, network pass (7
  loopback listeners, 0 other). No DeepSeek calls.
- **Config:** `configs/research/alfworld_qwen35_deepseek.yaml` (upstream
  ALFWorld settings; max_steps 50) via upstream `scripts/eval_only.py
  --split valid_seen --test_env_num 1`; initial skill
  `skillopt/envs/alfworld/skills/initial.md`; Qwen thinking disabled,
  temperature 0.7, max_tokens 8000.
- **Resources:** spgpu, 1 A40, 8 cores, 64 GB, 1 h / queued 23 min, ran
  **4 min 3 s** (vLLM ready after 210 s; episode + preflight ~25 s), 2 min 43 s
  CPU. Peak GPU memory 37.8 GiB / 46 GiB (pre-allocation); mean utilization
  9% over the job.
- **Run dir:** `runs/20261020-031601_smoke_rollout_64333791` (fetched
  locally, including `skillopt/results.jsonl`,
  `skillopt/predictions/val:0000/conversation.json`, `run_summary.json`,
  `reflection_input_preview.txt`).
- **Episode (verified):** task **`val:0000`**, `look_at_obj_in_light`,
  "look at alarmclock under the desklamp." **Success** (hard 1, reward 10 on
  the last step) in **3 actions**: `go to desk 1` → `use desklamp 1` →
  `take alarmclock 2 from desk 1`.
- **Serving / parser (verified):** 0 fallback actions, 0 "Nothing happens."
  (all actions valid), 0 genuine `look`. vLLM: 3 requests, all HTTP 200 (plus
  1 preflight), 1,327 prompt / 231 completion tokens per request on average,
  **mean latency 4.1 s per request** (1 in flight).
- **Reasoning format (verified):** **all 3 replies contain no think tags at
  all**: reasoning prose (482 / 362 / 1,777 chars), then `<action>…</action>`.
  `reasoning` is `null` on every step (`reasoning_null` = 3,
  `think_close_only` = 0). The full replies are preserved in
  `model_response`. `reflection_input_preview.txt` (SkillOpt's own
  `fmt_minibatch_trajectories`) shows the optimizer would receive the task
  header, 3 actions and 3 observations, and **no reasoning lines**. Step 2's
  lost reasoning explicitly cites the skill ("According to the 'Examine in
  Light' task type…").
- **Interpretation:** model serving and the action parser work; the task
  succeeded. The reasoning channel to the optimizer is empty for this model
  configuration, so SkillOpt's reflection would reason from actions and
  observations only. One easy episode is not a success-rate estimate.

<!-- Template for job entries:

## YYYY-MM-DD — <job name> (job <id>)

- **Purpose:**
- **Commit:** <sha>   **Snapshot:** snapshots/<sha12>
- **Run dir:** runs/<...>
- **Resources requested / used:** (from sacct)
- **Config:** configs/research/<file>.yaml + CLI overrides
- **Tasks / seeds:**
- **Outcome:** COMPLETED/FAILED/TIMEOUT/CANCELLED — what the outputs show
- **Scores:** native hard score(s)
- **Trajectory validity:** fallback actions by kind, genuine look, "Nothing happens.", format_invalid
- **Throughput:** requests, mean latency, gen tokens/s (vllm_throughput)
- **Gate:** action, selection scores
- **Tokens / est. cost:**
- **Deviations from plan:**
- **Observations / interpretation:**
-->
