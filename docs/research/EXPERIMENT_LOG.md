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

## 2026-10-20 — offline: run_tests (job 64336623) and parser demo (job 64336627)

- **Commit / snapshot:** `8f739f7` / `snapshots/8f739f76a794`. CPU only, no
  model or API calls.
- **run_tests:** **179 passed**, 0 failed/skipped (61 parser cases, 4
  fake-env equivalence tests: trace_log leaves results, conversations,
  executed actions and `compute_score` identical; compat changes only
  `reasoning`/`reasoning_source`).
- **Parser demo** on the saved episode `runs/20261020-031601_smoke_rollout_64333791`
  (`reasoning_parser_demo.json`, `reflection_input_{strict,qwen_compat}.txt`):

  | Step | Action | Raw reply chars | Strict reasoning | Compat reasoning |
  |---|---|---|---|---|
  | 0 | go to desk 1 | 482 | 0 | 451 (untagged_prefix) |
  | 1 | use desklamp 1 | 362 | 0 | 329 (untagged_prefix) |
  | 2 | take alarmclock 2 from desk 1 | 1,777 | 0 | 1,728 (untagged_prefix) |

  The reflection input has 0 think lines / 488 chars under strict and 3 think
  lines / 3,044 chars under compat. Actions, observations and outcome are
  identical by construction. The difference is the action tag and
  whitespace.

## 2026-10-20 — billing check (no job), run_tests (64337521), measure_envs (64337522)

- **Billing (verified):** spgpu `TRESBillingWeights=cpu=4519.68,
  mem=376.64G, GRES/gpu=18078.70`; billing = max of weighted TRES (earlier
  8 CPU / 64 GB / 1 A40 jobs billed 36157 = 8 × 4519.68). `my_job_estimate`
  per hour: 8 CPU / 64 GB / 1 A40 **$0.217** (2.0 GPU-eq); 4 CPU / 48 GB /
  1 A40 **$0.108** (1.0 GPU-eq); 4 CPU / 32 GB same as 48 GB.
- **Earlier GPU jobs (sacct):** batch MaxRSS 14.0–14.1 GiB; TotalCPU 2.7–3.0
  min over 4–7.5 min (0.4–0.75 cores on average).
- **run_tests (64337521, commit `73890c3`):** **184 passed**.
- **measure_envs (64337522, commit `73890c3`; CPU, 4 cores, 16 GB):** 8
  ALFWorld simulators for the fixed train subset (`train:0000, 0005, 0006,
  0012, 0013, 0021, 0027, 0034`), no model. Env build **89.8 s**, reset
  4.5 s, lockstep `step` of all 8: mean **0.20 s** (max 0.30 s) over 30 random
  admissible steps. Job cgroup: peak process memory **0.70 GiB**, page cache
  1.51 GiB, total 2.27 GiB; mean **0.8 cores** (20% of 4). The cluster uses
  cgroup v2 (`slurmstepd.scope/job_<id>/step_batch/...`). Billed 10018
  units (CPU partition), est. $0.02.
- **Decision:** the 8-episode rollout uses **1 A40, 4 CPUs, 48 GB** (one
  billing unit). Expected peak is ~15–16 GiB process memory plus reclaimable
  cache, and ~1–2 cores during generation. Env build is single-threaded or
  I/O-bound, so more CPUs would not help it.

## 2026-10-20 — 8-episode traced rollout on fixed train tasks (job 64337548)

- **Commit / snapshot:** `73890c3` / `snapshots/73890c3ab901`; vLLM 0.28.0
  (file rendezvous, network pass, native sampler, prefix caching off);
  `reasoning_parser: strict`, `trace_log: true`; initial skill; tasks
  `research/task_subsets/throughput_train8.txt` (train split only; val/test
  empty in the split_dir). No DeepSeek calls.
- **Resources:** spgpu **1 A40, 4 CPUs, 48 GB**; billed **18078 units = 1.0
  GPU-equivalent** (`billing.json`). Ran **35 min 8 s** (vLLM ready after 230 s,
  episodes 31 min). Physical GPU time 0.585 GPU-h; billable 0.585
  GPU-equivalent-h; about $0.063 at $0.108/h. At the previous 8 CPU / 64 GB
  request it would have billed 2.0 GPU-eq (~$0.13).
- **CPU / RAM (job cgroup, `resource_usage.csv`):** mean **0.32 cores (8% of
  4)**; peak process memory 4.07 GiB, page cache 11.83 GiB (model weights),
  cgroup total 16.07 GiB (sacct MaxRSS 16.1 GiB, which counts mapped model
  pages). **4 CPUs / 48 GB are sufficient.** Peak GPU memory 37.8 / 46 GiB,
  mean GPU utilization 84%.
- **Outcomes (native score, `eval_summary.json` hard = 0.5):**

  | Task | Type | Result | Steps | Fallbacks | "Nothing happens." |
  |---|---|---|---|---|---|
  | train:0000 | look_at_obj_in_light | fail (50-step timeout) | 50 | 3 missing tag | 0 |
  | train:0005 | pick_and_place | success | 16 | 0 | 0 |
  | train:0006 | pick_and_place | success | 9 | 0 | 0 |
  | train:0012 | clean | success | 13 | 0 | 0 |
  | train:0013 | clean | fail (timeout) | 50 | 0 | 1 |
  | train:0021 | cool | fail (timeout) | 50 | 1 missing tag | 1 |
  | train:0027 | heat | success | 39 | 0 | 0 |
  | train:0034 | pick_two | fail (timeout) | 50 | 1 missing tag | 2 |

  277 actions; 5 fallback `look` (all `missing_action_tag`), 6 genuine `look`,
  4 non-admissible executed actions. Reasoning: `reasoning_null` on 272/272
  non-fallback steps; 28 replies had `</think>` only, the rest no tags.
- **Throughput (vLLM /metrics + traces):** 277 requests, all HTTP 200;
  1,263 prompt / 543 completion tokens per request on average. Latency:
  median **3.9 s**, p90 17.5 s, max 142 s. Aggregate generation 81 tokens/s.
  **7 requests hit `max_tokens` = 8000** (~141 s each); 5 of them contained no
  `<action>` and became the 5 fallbacks (raw text preserved in
  `trace.json → call.unparsed_response`, 24–28k chars). Sample: an
  indecision loop over which action to take ("Let's try `examine alarmclock
  2`." ×9), not token repetition.
- **Bottleneck (interpretation):** the rollout is lockstep. Each step waits
  for the slowest of the active envs, and the sum of per-step maximum
  latency is 1,744 s of the 1,857 s. Runaway 8000-token generations dominate.
  CPU, RAM and simulators are not the limit (env step 0.2 s; env build ~90 s
  per chunk).
- **Traces (`trace_check.json`): all_ok for 8/8 episodes.** Raw responses,
  actions, feedback, rewards, termination and final `hard` match
  `conversation.json` / `results.jsonl`. Executed action = lowercased parsed
  action. Admissible lists of 14–48 actions on every step; initial
  observations present. The env's `is_action_valid` is a *format* flag
  (needs `<think>` tags) and is 0 almost everywhere; admissibility is in
  `executed_action_admissible`.
- **Scores/actions unchanged:** checked here for internal consistency. The
  causal invariance (trace on vs off) is established by the deterministic
  fake-env tests, since a real on/off pair is not comparable under sampling.

## 2026-10-20 — offline analysis of job 64337548 (jobs 64351262 tests, 64351263 analysis)

- **Commit / snapshot:** `7e78fb3` / `snapshots/7e78fb3321b7`. CPU only; no
  inference, no API. **185 tests passed.** `rollout_analysis.json` fetched locally.
- **Output tokens (277 replies):** median 171, p75 383, p90 969, max 8000
  (cap). Histogram: <100: 36, 100–199: 121, 200–499: 67, 500–999: 27,
  1000–1999: 12, 2000–3999: 7, 8000: 7.
- **Action-tag position (272 non-fallback replies):** the first `<action>`
  sits at a median **95%** of the reply (p25 93%, min 45%). Nothing follows
  `</action>` in ≥ 90% of replies. 2 replies have several action tags.
- **The 7 capped replies:** 5 contain **no `<action>` at all** within 8000
  tokens, so the rollout substituted `look` (`missing_action_tag`). 2 contain
  tags, so they parsed: `train:0000` step 45 (5 tags; first at 75%, executed
  `examine alarmclock 2`) and `train:0027` step 14 (**174** tags; first at
  45%, executed `go to cabinet 1`, then 14k chars more). In both, the
  **first** tag was executed although generation continued.
- **Walltime reconciliation:** each capped reply was the slowest request of
  a *different* lockstep step: 7 × ~141 s = **989 s (16.5 min)**. The other
  43 steps' slowest requests sum to **755 s (12.6 min)** (mean 17.6 s ≈ the
  p90 latency). The critical path is 1,744 s. Measured wall 1,857 s minus
  that leaves ~113 s of env build (~90 s) and stepping.
- **By outcome:** failed episodes 6/7 capped and 13/14 ≥ 2000-token replies
  (200 requests, mean 614 tokens). Successful episodes 1 capped (the heat
  success) and 1 ≥ 2000 (77 requests, mean 359).
- **Exact analyst prompts** (SkillOpt's own builders with ALFWorld prompts,
  chat_optimizer replaced by a recorder; tokens ≈ chars/4–chars/3):

  | Group | strict | qwen_compat |
  |---|---|---|
  | all 4 failures (one failure minibatch) | 31,772 chars (~8–11k tok) | **336,709 chars (~84–112k tok)** |
  | all 4 successes | 14,776 (~3.7–4.9k) | 103,047 (~26–34k) |
  | single worst episode (train:0000) | 12,276 (~3–4k) | 162,122 (~41–54k) |

  The cause: upstream `_clip_text` deliberately never truncates ("the
  optimizer is given the full content"). Compat therefore passes all
  recovered reasoning: median 661 chars per step, p90 3,186, max 21,430; 16
  steps > 5k chars hold 35% of it.
- **Planned training batch** (smoke config, seed 42, epoch 1): `train:0017`
  (clean), `train:0036` (pick_two), `train:0004` (look), `train:0013`
  (clean; a 50-step failure in 64337548).

## 2026-10-20 — pre-training checks: run_tests + resolved configs (job 64358982)

- **Purpose:** verify the strict+trace config and the optimizer call log
  before the training smoke.
- **Commit:** `12a153e`   **Snapshot:** snapshots/12a153e5ffa3
- **Resources:** CPU partition, 2 CPUs / 8 GB, ~10 s.
- **Outcome:** COMPLETED. **195 passed** (185 earlier + 10 new). New tests:
  - strict+trace smoke differs from the strict smoke only in `trace_log`,
    and `scripts/train.py`'s `get_adapter` builds it as strict + traced with
    all other adapter settings equal;
  - the failure and success analyst prompts SkillOpt builds are
    byte-identical with and without traces (trace-only evidence such as
    backend error text never appears);
  - only `skillopt/envs/alfworld/{rollout,adapter}.py` mention `trace.json`;
  - call log: off by default; same `create()` kwargs, return values, token
    totals and attempt count with it on; failed attempts logged; API key
    never written; an unwritable log path changes nothing;
  - `summarize_optimizer_calls.py` parse rules, costs, SDK HTTP lines.
- **Resolved configs** (`resolved_configs.json`, as `scripts/train.py` reads
  them): diff vs `alfworld_smoke.yaml` = `{trace_log: false → true}` for the
  strict+trace config; `{reasoning_parser, trace_log}` for condition B. Train
  batch (all three) `train:0017, 0036, 0004, 0013`; selection = `val:0000`–
  `val:0017` (18). Key values: Qwen/Qwen3.5-4B, qwen_chat, thinking disabled,
  temperature 0.7, max_tokens 8000; optimizer openai_compatible at
  api.deepseek.com, max_tokens 16384 (model id from `OPTIMIZER_MODEL` =
  `deepseek-flash`); 1 epoch, batch 4, edit budget 4, patch mode, gate on,
  workers 8, max_api_workers 8, analyst_workers 2, minibatch 8, max_steps 50,
  lr fixed, `eval_test: false`, slow update / meta on (upstream defaults).

## 2026-10-20 — training smoke, condition A strict + traces (job 64359024) — COMPLETED; candidate rejected (tie 12/18)

- **Purpose:** one original SkillOpt optimization step (rollout → reflect →
  merge → clip → apply → gate) end to end (CLAUDE.md §9 item 6).
- **Commit:** `12a153e`   **Snapshot:** snapshots/12a153e5ffa3
- **Command:** `STAGE=train TRAIN_CONFIG=configs/research/alfworld_smoke_strict_traced.yaml research/submit.sh smoke 12a153e -- --time=05:00:00`
- **Resources requested:** 1 A40, 4 CPUs, 48 GB, 5 h (one spgpu billing unit).
- **Budget (estimate, recorded before the run):** GPU ~1.5–3.3 h, billable
  ≤ 5 h × $0.108 ≈ $0.54 (expected $0.16–0.36). DeepSeek: ≤ 4 call sites
  (failure analyst, success analyst, final merge if both groups produce
  edits, ranking if > 4 merged edits); expected ~$0.01–0.03, ≈ $0.09 if every
  output hit 16,384 tokens; + 2 tiny preflight calls. Submitted 17:31 UTC
  (DeepSeek off-peak). No API-side spend cap; ≤ 9 HTTP requests per call site.
- **Diagnostics enabled:** `trace.json` per episode, `optimizer_calls.jsonl`,
  `OPENAI_LOG=debug` (content-free SDK request lines in `job.log`).
- **Run dir:** runs/20261020-133347_smoke_train_64359024 (fetched to `$LOCAL_RUNS`).
- **Outcome:** COMPLETED, exit 0, 2 h 34 min (13:33–16:07 EDT). Every stage
  ran: vLLM ready after 210 s, network check **pass**, preflight PASS
  (optimizer via SkillOpt's backend, thinking active; target OK), baseline
  selection, train rollout, reflection, merge, ranking, apply, candidate
  selection, gate, epoch-end slow-update placeholder and meta-skill skip.
- **Resources used (sacct / samplers):** 1 A40, 4 CPUs, 48 GB; billable 1.0
  GPU-equivalent × 2.567 h ≈ **$0.28** (rate $0.1085/h). GPU memory peak
  38.7 / 46.1 GB (vLLM reserves 0.85), mean GPU util 95.8%. Mean CPU use 0.18
  cores (4.5% of 4); peak cgroup memory 16.1 GiB (process 4.1 GiB, page cache
  11.9 GiB). The one-unit request was ample.
- **Time split:** vLLM + preflight ~4 min; baseline selection (18 episodes,
  chunks 8/8/2) ≈ 78 min (derived); step 1 = 71 min: train rollout 782 s,
  reflect 18 s, aggregate 11 s, ranking 14 s, candidate selection 3,431 s.
- **Train rollout** (`train:0017, 0036, 0004, 0013`, batch seed 1043, as
  predicted by the dataloader preview): **1/4** — only `train:0004`
  (look_at_obj_in_light, 5 steps) succeeded; the other three used all 50
  steps.
- **Optimizer (DeepSeek, `optimizer_calls_summary.json`):** 4 calls, 4 HTTP
  requests (all 200), **0 retries, 0 errors, 0 parse failures**, all
  `finish_reason=stop`; `resp.model` = `deepseek-flash` (alias; the response
  does not expose a version). Tokens: 13,762 in (0 cache hits), 9,631 out
  (7,193 reasoning). Cost ≈ **$0.0078** at the off-peak rate in effect ($0.0157
  if peak). Call-log totals match the trainer's `token_summary` exactly.

  | Call | Input → output tokens | Result |
  |---|---|---|
  | success analyst (1 trajectory) | 1,445 → 1,306 | 2 edits |
  | failure analyst (3 trajectories) | 8,548 → 3,398 | 4 edits |
  | final merge | 2,100 → 2,451 | 6 edits (4 failure + 2 success) |
  | ranking (6 > budget 4) | 1,669 → 2,476 | kept indices [0, 1, 2, 5] |

- **Kept edits (candidate, 2,966 → 4,752 chars; all 4 applied):**
  1. append "Room-Level Exploration (Breaking Navigation Loops)": never
     revisit checked receptacles; move to unvisited areas (support 3).
  2. insert after principle 3: take only the goal object; cleaning/heating/
     cooling need no extra tool (support 3).
  3. append "Pick-Two Progress Rule": never re-take a placed object; track
     `placed_count` (support 3).
  4. insert principle 9 "Light before examine": turn on the desklamp, then
     examine; if lamp and object share a receptacle "do everything in place"
     (support 1, from the single success).
  Dropped by ranking: "Avoid Redundant Inspection" (failure analyst) and a
  task-table rewrite for examine-in-light that called taking the object
  "optional" (success analyst).
- **Gate:** baseline selection hard **0.6667 (12/18)**; candidate **0.6667
  (12/18)** → `action: reject` ("hard=0.6667 <= current=0.6667"; the rule is
  strictly greater). `best_skill.md` = initial skill; `skills/skill_v0001.md`
  = initial skill + the empty SLOW_UPDATE placeholder (upstream epoch-1
  behavior). Rejected edits recorded in `trajectory_digest.json`.
- **Per task (baseline → candidate):** identical on 16/18; candidate gained
  `val:0012` (cool; 50 → 23 steps) and lost `val:0014` (heat; 15 → 50
  steps). Steps 493 → 474; `look` fallbacks 17 → 6; replies at the 8000-token
  cap 20 → 11; the two pick-two tasks finished faster (40 → 11, 50 → 28).
- **Trajectory validity:** trace checks all consistent (18 + 4 + 18
  episodes). 1,122 target requests, all HTTP 200; mean 580 generated tokens per
  request, 72 generated tokens/s aggregate. As before, almost all replies lack
  `<think>` tags (strict parser → `reasoning` null on 474/476 baseline and
  468/468 candidate non-fallback steps), so the analysts saw actions and observations only.
- **Deviations from plan:** none. Settings as approved; no retries.
- **Interpretation (not established):** the tie is consistent with no effect
  or a small effect in either direction: with one sample per task at
  temperature 0.7, a 1-task swap (5.6 points) is within plausible run-to-run
  noise, which we have not measured. The step, fallback and cap reductions
  are single-run observations, not evidence of improvement. The kept
  principle 9 generalizes from one success and misattributes it: the trace
  shows `train:0004` ended when the agent *picked up* the statue under the
  lit lamp (step 4), not when it examined the dresser; the edit never says
  the object must be held.

## 2026-10-20 — offline analyses of job 64359024 (no job, no API calls)

### (a) Audit of the optimizer call log and SDK debug log

- **Default off (verified):** the log is written only when
  `OPTIMIZER_OPENAI_COMPATIBLE_CALL_LOG` or `OPENAI_COMPATIBLE_CALL_LOG` is
  set. The only place that sets it, and `OPENAI_LOG`, is the `train` stage of
  `research/cluster/smoke.sbatch` (not preflight, not rollout).
- **Behavior preserved (verified):** inside the retry loop the only changes
  are a timestamp and two guarded `_log_call` calls that swallow every
  exception. Tests (64358982) show identical `create()` arguments, return
  values, token totals and attempt counts with the log on; in 64359024 the
  call-log token totals equal the trainer's `token_summary` exactly.
- **Contents (verified, 4 records):** request = model, max_tokens,
  temperature, timeout, tools flag, messages; response = id, model,
  system_fingerprint, finish_reason, content, reasoning_content, usage. No
  API key, `Authorization`, header, URL, absolute path, host name, e-mail or
  IP address occurs (pattern scan; the only "sk-" hits are words such as
  "task-specific"). The job's leak check passed.
- **SDK debug log (verified):** exactly 4 message templates appear, 4 times
  each: "Building HTTP request: method=POST retries_taken=0", "Sending HTTP
  Request: POST", "HTTP Response: POST 200", "request_id: None". No bodies,
  headers or URLs; no other logger produced formatted lines.
- **Residual risks (interpretation):** (1) on a failed attempt the log stores
  the provider's error message (truncated to 4,000 chars); some providers
  echo a masked key fragment in 401 messages (not verified for DeepSeek; no
  error occurred). (2) The shared `OPENAI_COMPATIBLE_CALL_LOG` variable would
  also log target calls if a target ever used this backend. (3) Prompts and
  replies contain benchmark text and DeepSeek's full reasoning; run
  directories stay out of git. Proposed hardening (not implemented): redact
  the configured key and key-like tokens from error messages, and honor only
  the role-specific variable.

### (b) Success-credit case study: `train:0004`

- **Task (verified):** "look at statue under the desklamp"
  (`look_at_obj_in_light`, object Statue, toggle target DeskLamp; game
  `json_2.1.1/train/look_at_obj_in_light-Statue-None-DeskLamp-319/...`).
- **Trusted goal (verified, `game.tw-pddl` `:goal`):** in one state,
  (G1) some DeskLamp is `isToggled`, (G2) the agent is at the location of the
  receptacle containing that lamp, and (G3) the agent `holds` a Statue. No
  order, no `examine`. Domain effects: `examineReceptacle` only sets
  `checked`; `ToggleObject` sets `isToggled` permanently (and flips `isOn`);
  the text feedback after any toggle reads "You turn on ..." (grammar).
  TextWorld walkthrough: go to dresser 1 → use desklamp 1 → take statue 1.
  ALFRED expert plan: pick up statue → go to lamp → toggle.
- **Trajectory (verified from trace):** 0 go to bed 1; 1 go to dresser 1
  (desklamp 1 and statue 1 observed on it); 2 use desklamp 1 ("You turn on");
  3 examine dresser 1; 4 take statue 1 → reward 10, won. `won` was false
  after steps 0-3.
- **Which conditions the final action met (inference from observation text,
  consistent with the won flag):** G2 first held at step 1, G1 at step 2, G3
  only at step 4. Step 4 completed the conjunction; steps 1-2 were also
  necessary; steps 0 and 3 changed no goal predicate.
- **What DeepSeek saw (verified, `optimizer_calls.jsonl`):** the one success,
  as actions and observations, under "Successful Trajectories", with "Steps:
  5"; no per-step reward, no statement that episodes end on success, no goal
  definition, no hidden reference (the adapter's reference channel exists but
  `attach_reference_context` is never called).
- **What DeepSeek concluded (verified, reply + reasoning):** listed as success
  patterns that the lamp "must be lit when the examine action fires" and that
  examining the receptacle "is sufficient to satisfy" the task; called taking
  the object "optional"; claimed the original skill row ("Find X -> take X ->
  find desklamp -> use desklamp") "omits the lamp-on requirement" (it does
  not: `use` is the toggle) and has the wrong order (the goal has no order).
  The kept edit ("Light before examine ... do everything in place") states G1,
  adds an unrequired step and ordering, and omits G3. The explicit
  "optionally take object" edit was dropped by ranking for unrelated reasons.
- **Evidence available to DeepSeek (interpretation):** the trajectory itself
  shows that lamp-on + examine was not enough (the episode continued after
  step 3) and that the episode ended right after taking, but only if one
  knows episodes stop on success, which the prompt does not say. One
  trajectory cannot show that `examine` or the order is unnecessary; our own
  run 64333791 solved the same task type (`val:0000`) without `examine`.
- **Effect on the candidate (observation, not causal):** lamp-task outcomes
  were unchanged (`val:0000-0002`); in failed `val:0001` (lamp not on the
  desk, never found by either skill) the candidate tried the inadmissible
  `use desklamp` three times.

### (c) Evaluation randomness (code inspection; details `BASELINE_ARCHITECTURE.md` §2g)

- Same 18 tasks, order, env seeds and deterministic games for baseline and
  candidate; the only stochastic element is target sampling (temperature
  0.7, global vLLM generator, no per-request seed), plus batch-composition
  effects. The baseline is evaluated once; acceptance is a strict `>` on one
  run each.
- Same skill, same game, different runs → different action sequences
  (`val:0000`: 3 vs 6 actions; `train:0013`: diverges at step 2). Per-task
  flip rates are unmeasured.

<!-- Template for job entries:

## 2026-11-16 — OfficeQA learned-contract Lean compile (job 66259448) — COMPLETED, all 64 statements

- **Authorization:** USER 2026-11-16: 1 CPU core, 2 GB, <=15 min walltime, 0 GPUs, 0 paid API calls. Spent once
  (plus ~1 CPU-second on a failed first submission); the grant is now exhausted. OQA-5's own caps untouched.
- **Deployed revision:** generator `47c6ed8`; the frozen artifact committed at `87b9ad6` as
  `research/officeqa/lean/OqaContracts.lean` with `MANIFEST.sha256`. Ran from a read-only **staging bundle**, not
  a git snapshot: `research/deploy.sh` refuses a commit that is not on `origin` and pushing was not authorized.
- **Resources:** partition `standard`, AllocTRES `cpu=1, mem=2G, node=1`, **0 GPUs**. Elapsed 00:00:06, TotalCPU
  00:00.907, `lean` itself 4.24 s.
- **Toolchain:** Lean 4.20.0 (x86_64-unknown-linux-gnu, commit 77cfc4d1a4f6, Release), from `OQA_LEAN_BIN` in the
  private cluster site settings. Not present on the workstation or the login node.
- **Result:** rc 0. **0 bytes of compiler output, 0 errors, 0 warnings, 0 `sorry`.** 64 statements: 23 in
  `section General` + 2 `rfl` bridge theorems + 39 over the exported evidence.
- **File identity:** `e40a7e62faed92ffdc53093b4937f8eb6b655ba5eabda14d7036444c0b9eecbb`, identical at six links
  (recorded before submission; reproduced twice by the generator; committed with a manifest; verified by the
  job's manifest check; logged by the job; and in the copy fed to `lean`).
- **Deviations:** none beyond the staging-bundle deployment noted above.
- **NOT established:** the axiom set. `#print axioms` was deliberately omitted because changing the file would
  break the digest guarantee. Zero warnings does rule out `sorry`.
- **Earlier submission:** 66259421 FAILED in 1 s, exit 3, before `lean` ran (`${BASH_SOURCE[0]}` resolves to the
  node spool copy of a batch script; fixed with `$SLURM_SUBMIT_DIR`). Both logs preserved.
- **Artifacts:** `~/dev/lean_skillopt/runs/20261116-offline-lc-port/lean_job_66259448/`.

## 2026-11-17 — OfficeQA hosted-actor tool-loop smoke test (job 66264849) — COMPLETED, tool loop PASS

- **Question:** does a hosted OpenAI-compatible actor (EmpirioLabs Qwen3.5-4B) return structured tool calls that
  SkillOpt can execute against OfficeQA's local document tools, with results fed back? Judged on the LOOP;
  accuracy recorded separately, as instructed.
- **Authorization:** USER 2026-11-16: <=30 min CPU job time, 0 GPUs, <=100 EmpirioLabs HTTP attempts including
  retries, <=$0.50 actor charges. **Spent: 23 of 100 attempts, $0.003363 + ~$0.00005 probe, ~3 min CPU, 0 GPUs.**
- **Deployed revision:** `07d4bb5` via `git archive` staging bundle (digest verified in-job). Starting skill
  `9d377d1c2906...` — the same skill the A40 two-arm comparison used.
- **Model wiring:** `configs/research/officeqa_api.yaml`; both roles `openai_compatible`; target
  `https://api.empiriolabs.ai/v1`, model `qwen3-5-4b`, temperature 0.7, top_p 1.0, max_tokens 8000, timeout 300.
  Driver-set `max_tool_turns` 12 for the smoke test (the comparison uses 24).
- **Endpoint facts (unauthenticated `GET /v1/models/qwen3-5-4b`, free):** `function_calling` declared,
  `structured_output: json_schema`, context 262144, max output 32768, **FP8 weights + FP8 KV**,
  `hugging_face_id: Qwen/Qwen3.5-4B`, 50 RPM, $0.04/$0.02/$0.07 per M in/cached/out.
- **Items (rule OQA-SMOKE-1, fixed before the run):** UID0033, UID0063, UID0034 — the first three TRAINING items
  in the A40 shared-rollout order that requested at least one tool under S0. Validation and held-out untouched.
- **Result — tool loop PASS (all four checks):** 18 of 20 replies carried `tool_calls`; every argument set parsed
  as JSON (0 malformed); **18 tool calls executed, 0 observation errors**; all 3 items took further turns after
  an observation.
- **Measured:** 20 attempts / 0 errors / 0 retries; elapsed 68.4 s for 3 sequential questions; latency min 1.63,
  median 3.41, max 7.04 s; 73,145 prompt + 6,251 completion tokens; settled **$0.003363** against a $0.0133
  worst-case reservation; **`cached_prompt_tokens` = 0 on every call** (no prefix caching, although each turn
  re-sends an identical system prompt and oracle-page prefix); compute-node reachability `http=200` in 0.124 s;
  0 run artifacts contain the key (checked on the cluster against the live value).
- **Accuracy (recorded, NOT the verdict):** 1 of 3 answered; `hard` = [0, 1, 0]. UID0033 stopped without a tool
  request or an answer after 2 calls; UID0034 used all 12 turns of the smoke cap and produced nothing.
- **Deviations:** staging bundle rather than a git snapshot (pushing not authorized); `max_tool_turns` 12 rather
  than the comparison's 24, to keep the worst case inside the attempt cap; `top_k` / `min_p` /
  `repetition_penalty` could not be sent through the chat schema and were left at the provider's defaults.
- **Four failed submissions, none of which spent an API attempt.** 66262999 and 66263561: **NODE_FAIL at 60 s,
  four times each, on eight different nodes**, before Python ran; control job 66263988 (same account, partition,
  resources) COMPLETED, and one-factor controls 66264099 (read the key from `~/.config`), 66264100 (extract the
  576-file bundle) and 66264101 (curl the endpoint from a compute node) all COMPLETED. Fixed by submitting via
  `--wrap` with a **node-local /tmp** work dir. **`--no-requeue` did not stop the requeue loop.** 66264572:
  `python -I` ignores `PYTHONPATH`. 66264701: the target backend defaulted to Azure OpenAI (3 ledger attempts,
  **0 HTTP sent** — the ledger now reports `never_sent`).
- **Interpretation (not measured here):** the API is viable for the comparison, and the **50 RPM account limit,
  not latency, is the binding constraint** — 3,100 estimated requests is a 62-minute floor against 44 minutes
  latency-bound at the measured median with `workers: 4`. FP8 numerics mean S0 must be re-measured on this actor
  rather than inherited from the A40 run's 5/12.
- **Artifacts:** `~/dev/lean_skillopt/runs/20261117-officeqa-api-smoke-66264849/` (SMOKE.json, job.log,
  slurm.out, manifest_check.txt, three predictions directories). Plan and budget:
  `docs/research/OFFICEQA_API_COMPARISON_PLAN.md`.

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

---

## OQA-3ARM-PREP — the three-arm comparison: offline pre-launch check (2026-11-17, no job, no spend)

**Authorization.** None needed and none used: no cluster job, no actor request, no paid call, no GPU, no network.
The user asked for the offline preparation and the revised budget before the campaign is submitted.

**Deployed revision.** Workstation only, branch `officeqa-contracts`. Entry point `scripts/train.py:main()` at
the working-tree revision; the pre-launch driver records the repo path and every child command in
`PRELAUNCH.json`.

**Resources.** One workstation process tree, ~2 minutes of CPU across 12 child runs. Python 3.12.11 in the
session scratch venv (the workstation python is 3.9 and cannot import SkillOpt).

### Configuration under test

`configs/research/officeqa_api3.yaml`, one file for all three arms. Effective settings read back through the
pinned trainer's own `load_config`:

```
num_epochs 4   batch_size 40   accumulation 1   -> steps_per_epoch 1 (one ordinary update per epoch)
minibatch_size 8   merge_batch_size 8   analyst_workers 2   failure_only false   skill_update_mode patch
edit_budget 4, min 2, lr_scheduler cosine  -> per-epoch budget 4, 3, 2, 2 (identical in every arm)
use_gate true   sel_env_num 0 (all 24 val)   gate on `hard`, strict improvement
use_slow_update true   slow_update_gate_with_selection true   slow_update_samples 4
longitudinal_pair_policy mixed   use_meta_skill true   eval_test false
seed 42   workers 4   max_tool_turns 24   max_completion_tokens 16384   target qwen3-5-4b (EmpirioLabs)
```

Splits: rule **OQA-SEL-3**, digest `117836ff413934b307d282109675d10febec442c6431f7064cadef42fad098e5`,
train 24 / val 24 / held-out 60, disjoint, with all three OQA-SEL-2 sets as subsets.

### What ran

Two saved epochs through the real training entry point, for each of the three arms, as two phases each (one per
epoch, the second resuming from `runtime_state.json`). Collections used: the shared epoch-1 rollout of job
66209754 as epoch 1, arm B's epoch-2 rollout of job 66228142 as epoch 2, and arm B's `selection_eval_baseline`
to seed every gate. Two substitutions, both because no authorization covers them:

* the actor (`chat_target_messages`) raises; every rollout directory is pre-seeded from recorded predictions and
  `run_batch` resumes from `results.jsonl`, so **zero** actor requests were sent;
* the optimizer (`chat_optimizer`, in all seven importing modules) is a recorder returning a fixed
  recorded-shape response per stage. **Zero** paid calls. Its output is not realistic and no conclusion is drawn
  from the skills produced.

### Results

| check | result |
|---|---|
| entry point completes | COMPLETED ×3 arms × 2 epochs, slow update and meta skill firing, gate rejecting |
| arm A receives the ordinary SkillOpt input | PASS — no report, no report heading, in any of 4 analyst prompts |
| arm B receives the Action-Only report | PASS — `#### Action Contract Report` in 2 of 2 prompts per phase, no other heading; 12 of 12 reports carry no stage marker |
| arm C receives both halves | PASS — `#### Action Contract and Stage Report`, no other heading |
| audited == sent | PASS — 12 of 12 reports per phase found **verbatim** in an analyst prompt |
| trace and model digests reaching the optimizer | PASS — 24 reports over 2 collections, 24 distinct trace digests, each matching the attempt on disk, each under the model the arm ran with |
| an evolved model reaches the optimizer | PASS — new digest in the report records; the text drops the retired contract and names the three new ones |
| arm C's report contains arm B's line for line | PASS — 24 of 24 |
| report sizes | arm B 2,965 chars mean (2,353–3,411), arm C 6,134 (4,450–15,370) |

Optimizer calls recorded per arm, per phase: epoch 1 → 2 analyst + 1 merge; epoch 2 → 2 analyst + 1 merge +
1 slow update + 1 meta skill. (No `ranking` call: `rank_and_select` short-circuits when the merged edit count is
within budget, which the stub's two edits always are.)

Model evolution, both contract arms, M0 → M2 with epoch 1's evidence carried forward:

```
M0 (shared epoch-1 rollout, 12 items / 128 tool steps)
  adopted   flat_header_authoritative, table_scale_applies_to_rows, grep_cap_not_reached
  REJECTED  index_alignment   contradicted at UID0026/e1@44
            year_inheritance  contradicted at UID0028/e1@8
  induced   grep.effect.1 (27 steps / 9 items), read.effect.1 (36/8), read.effect.2 (10/4), 0 counterexamples
evolve, epoch 2 (12 items / 122 tool steps new, 12/128 carried forward)
  out-of-sample before learning   82 forecasts confirmed, 1 violated, 71 unsettled
  counterexample                  UID0028/e2@42, read, width_uniform forecast true, observed false
  justified                       read.effect.1 auto-refuted, then RETIRED (no accepted repair)
  induced                         grep.effect.2, read.effect.3, read.effect.4
  retained with evidence          grep.effect.1 (60/11), read.effect.2 (23/6), 0 counterexamples
```

Both arms' evolved models came out identical in content (they were given the same traces) and differ only in the
arm label recorded in the frozen file and hence in digest: `ef295b0e241e…` for B, `ffd09864455f…` for C.

### Deviations from upstream, applied identically to all three arms

* `use_slow_update` and `use_meta_skill` **on** (off in the A40 two-arm run), with
  `slow_update_gate_with_selection: true` so the slow-update candidate passes the same validation gate rather
  than being force-injected.
* `slow_update_samples: 4` against upstream's 20. Measured reason: this repo disables per-trajectory truncation
  in the slow update, so 12 comparison pairs come to 846,563 characters = 262,257 tokens against the
  173,060-token largest prompt this project has sent successfully. 4 stays inside even when every pair shows a
  trajectory; 6 is inside on the measured mix but over at worst; 20 reaches 437,000–656,000.
* `eval_test: false`, with the held-out evaluation moved to a separate pass over the deduplicated skills.

### What is **not** established

Nothing about which arm is better, nothing about accuracy on the new splits, nothing about whether the reports
help the optimizer, and nothing Lean verified. The dry run's skills and scores are meaningless: the optimizer was
a stub and the rollouts were recorded. What it establishes is the wiring — which input reached which arm, under
which trace and model digest. Availability is untested for 48 of the 108 items. Epoch 1's reports are in-sample.

### Artifacts

```
runs/20261117-officeqa-3arm-prelaunch/
  PRELAUNCH.json          the one verdict and every child command's exit status and output tail
  BUDGET.json             the budget with every constant and the record it came from
  HELDOUT_PLAN.json       the deduplicated held-out plan
  OFFICEQA_FROZEN_SETS_V3.json
  models/                 M0, each arm's evolved model, SEED.json, EVOLVE_*.json, MODEL_EVOLUTION.jsonl
  arm_A|B|C/              full trainer out_roots; _dryrun/ holds every optimizer prompt and the DRYRUN_*.json
  arm_B|C_evolved/        the single-epoch pass under the evolved model
```

Code: `research/officeqa/oqa_sel3.py`, `oqa_lc_arm.py`, `oqa_dryrun.py`, `oqa_prelaunch.py`, `oqa_heldout.py`,
`oqa_budget.py`; `oqa_lc_report.py` (action renderer and the two audits), `oqa_reports.py` (the three
conditions), `skillopt/gradient/reflect.py` (condition-dependent heading and mode).
Tests: `tests/test_officeqa_three_arm.py`, 25 passed; whole local suite 2,057 passed, 11 skipped.

---

## OQA-SLOW-PROBE — is 10 paired questions per slow update practical? (jobs 66271190, 66271290, 2026-11-17)

**Authorization.** The user authorized **at most two paid HTTP attempts in total, including retries**, with saved
training artifacts only, zero new actor rollouts, zero GPU jobs and no held-out data, and asked that the campaign
configuration stay at four pairs pending review. **One attempt was used. One remains unspent. The config is
unchanged at `slow_update_samples: 4`.**

**Deployed revision.** `aa4aae91f2b05ec3b4b43f131ce443499180cea1`, via a `git archive` staging bundle whose
sha256 manifest the job verifies before running (`research/cluster/oqa_bundle.sh`).

**Resources.** 1 CPU, 4 GB, `standard` partition, 49 s elapsed, zero GPUs. The paid attempt had to run on the
cluster because the DeepSeek key exists only there, in the private secrets file.

### Part 1 — the prompts, rendered offline

`research/officeqa/oqa_slow_probe.py --mode render`. The prompts are produced by calling the campaign's own
`run_slow_update` and `run_meta_skill` with `chat_optimizer` replaced by a capture stub, so the rendering and the
trace-inclusion rules are the planned run's rather than a reimplementation of them. Pairs are built by the
campaign's own `build_comparison_pairs` against the saved rollout directories, which is what reads the
trajectories.

Sources: the shared epoch-1 rollout of job 66209754 as `prev`; arm B's and arm A's epoch-2 rollouts (jobs
66228142 and 66224637) as `curr`, keeping the heavier recorded attempt per item; arm B's `skill_v0001.md` and
`skill_v0002.md` as the two skills. Pool: 12 distinct training items, of which **7** fall in a
trajectory-showing category (`regressed` / `persistent_fail` / `improved`) and 5 are `stable_success`, which
prints no trajectory and so costs almost nothing.

| batch | pairs | showing | slow update | meta skill |
|---|---|---|---|---|
| representative (recorded order) | 10 | 6 | 641,406 chars ≈ 198,701 tok | 640,212 chars ≈ 198,331 tok |
| **largest assemblable** | 10 | 7 | **786,843 chars ≈ 243,756 tok** | 785,649 chars ≈ 243,386 tok |
| reference: representative | 4 | — | 183,679 chars ≈ 56,902 tok | — |
| reference: largest | 4 | — | 539,455 chars ≈ 167,117 tok | — |

**The slow update is the larger of the two requests**, by 1,194 characters: the user prompts are within 35
characters of each other and the slow update's system prompt is 1,229 characters longer (2,984 against 1,755).

Growth per trajectory-showing pair, measured (`GROWTH.json`): mean 111,483 characters, max 172,530, min 70,550 —
about **34,569 tokens per showing pair**. The saved collections share only 12 training items and only 7 showing
pairs, so a 10-showing-pair prompt cannot be assembled from these records; computed, it would be **~347,000
tokens** at mean weight and **~536,000** if every pair were as heavy as the heaviest one seen.

### Part 2 — the real DeepSeek path

One guarded attempt, largest batch, through the campaign's endpoint, model and generation settings.

```
preflight      optimizer_backend openai_compatible | base_url https://api.deepseek.com | model deepseek-flash
               max_tokens 16384 | temperature not sent | dispatch check PASS
request        prompt_tokens 243,986   (offline estimate was 243,756 — 0.09% low)
               cache hit 512 / miss 243,474        completion_tokens 7,562
response       finish_reason "stop"    elapsed 43.7 s (stage 44.0 s)    content 5,160 chars
parsed         run_slow_update returned slow_update_content of 3,235 chars -> USABLE
trainer would  record an accepted candidate and gate it on the 24-question selection set
charge         $0.04106 at off-peak prices, $0.08212 if peak
authorization  1 of 2 HTTP attempts used, 1 unspent
```

The returned guidance is substantive, not a stub: it opens by addressing the dominant failure in the saved
traces ("Always finish with an answer — do not run out of budget reasoning"), which matches the pool's 7
persistent failures, 4 of them ending with no answer produced.

**Success was judged on parsed output, not on HTTP 200**, which is the test the trainer itself applies: the stage
must return a non-empty content field or the epoch records `action: "no_content"`.

### The first attempt, and why it cost nothing

**Job 66271190 FAILED, 6 s, zero HTTP attempts, zero charge.** It configured the openai-compatible endpoint
correctly and then dispatched to **Azure**: `skillopt.model.chat_optimizer` is a router and `OPTIMIZER_BACKEND`
defaults to `openai_chat`. The Azure path raised for want of an endpoint, `run_slow_update` swallowed the
exception as it is written to do, and the probe recorded `no_content` — a result that looks like a DeepSeek
failure and is not one. This is the trap already recorded for the TARGET role in the hosted-actor smoke test,
hit on the OPTIMIZER role because the preflight checked the openai-compatible config without checking which
backend the router would choose.

Fixed at `aa4aae9`: the preflight calls `set_optimizer_backend`, reads it back, resets the cached per-role client
so it is rebuilt from the config just set, and proves the dispatch path with a check that costs no HTTP attempt —
`_chat_messages_impl` is temporarily replaced by one that raises a unique marker and a call is made through the
same wrapper chain the probe will use. Verified locally that the check refuses under the default backend and
passes once the backend is set. **Any campaign job that calls the optimizer must pin the backend the same way.**

### Safeguards that were in force

* the HTTP cap counts **actual sends** at the lowest level — a proxy around `client.chat.completions.create` —
  because `run_slow_update` hardcodes `retries=3` and capping by argument would depend on every caller honouring
  it. `retries` was additionally forced to 1, which `_chat_messages_impl` treats as one total attempt.
* the job body **refuses to run when `SLURM_RESTART_COUNT` is non-zero**, because the cap is per-process and a
  requeue would respend the authorization. This is the safeguard earlier runs only documented.
* the actor keys were dropped inside the job, so no rollout request was possible; the optimizer key never
  reached a log or an artifact (checked after the run).

### What this changes, and what it does not

The **173,060-token "envelope"** of the three-arm launch packet is retired: it was the largest prompt the project
had tried, never a limit, and DeepSeek accepted 1.41× it. What is now established is that **243,986 tokens are
accepted and parse**. The real ceiling remains unknown — the published figures for the DeepSeek chat family
disagree (128k for the V3 era, 1M for the current ones) and none of the sources is DeepSeek's own documentation.

At 10 pairs the measured mix sits about 13,000 tokens under the proven point, which is less than half a pair, so
**there is no demonstrated headroom for longer epochs**, longer trajectories as the skill grows, or a worse
category mix. The failure mode is graceful — `no_content`, symmetric across arms, at no cost — so the risk is a
slow update that silently produces nothing rather than a broken campaign, and the post-run check for
`no_content` in every arm's `slow_update/epoch_NN/slow_result.json` is what catches it.

**The meta-skill path was deliberately not tested**: the user's condition was to test it only if its rendered
request were larger, and it is smaller. What stays untested is that `meta_skill_content` parses back; the
remaining authorized attempt could close that.

### Artifacts

```
runs/20261117-officeqa-slow-probe-66271190/
  RENDER.json                          both stages' rendered sizes for representative, largest and 4-pair batches
  GROWTH.json                          the per-showing-pair growth curve and the computed 10-pair figures
  SEND_66271290.json                   the paid attempt: preflight, HTTP record, parsed result, charge
  SEND_66271190_azure_misdispatch.json the first attempt's record, kept as the evidence for the router lesson
  job_66271290.log
```

Code: `research/officeqa/oqa_slow_probe.py`, `research/cluster/oqa_slow_probe_body.sh`,
`research/cluster/oqa_bundle.sh`; `research/officeqa/oqa_budget.py` (constants revised to the measured values).

---

## OQA-3ARM — the three-arm OfficeQA campaign on the hosted actor (submitted 2026-11-17 03:26 EST)

**Status: RUNNING.** Commit `91b249449a48fb850b82a3d0d62457bfe1ca291a`, deployed as a `git archive` bundle at
`$PROJECT_ROOT/oqa_campaign_20261117` (pushing is not authorized). Campaign root
`$PROJECT_ROOT/runs/20261117-officeqa-3arm-campaign`.

### Design

| arm | name | what the analyst receives |
|---|---|---|
| A | Original SkillOpt | the ordinary analyst inputs, byte-identical to upstream |
| B | Action Contracts Only | the ordinary inputs plus feedback from the evolving action contracts |
| C | Action Contracts + Stages | the ordinary inputs plus the action feedback, the stage obligations, and the checks linking actions to stage progress |

One config file for all three arms, `configs/research/officeqa_api3.yaml`. The arms differ in exactly two
environment variables read by the opt-in hook in `skillopt/gradient/reflect.py`
(`OFFICEQA_STAGE_REPORT_CONDITION` = unset / `LC_ACTION` / `LC_STAGE`) plus `OFFICEQA_LC_MODEL`.

Splits: rule **OQA-SEL-3**, digest `117836ff413934b3…`, train 24 / val 24 (the whole released val split) /
held-out 60, disjoint, with all three OQA-SEL-2 sets as subsets. Materialized at
`$PROJECT_ROOT/officeqa_data/split_sel3`; row counts verified 24 / 24 / 60, every row carrying a question and a
ground truth.

Effective settings, read back through the trainer's own resolution: `num_epochs 4`, one ordinary update per
epoch (`ceil(24/40) = 1`), edit budget 4 / 3 / 2 / 2 (cosine), gate on the whole 24-question val split with
strict improvement, `use_slow_update` + `slow_update_gate_with_selection` + `use_meta_skill` all true from
epoch 2, **`slow_update_samples 10`**, `longitudinal_pair_policy mixed`, `eval_test false`, seed 42,
`workers 4`, `max_tool_turns 24`. Target `qwen3-5-4b` on EmpirioLabs (FP8 weights, FP8 KV, 262144 context),
optimizer `deepseek-flash`.

### The job chain

```
66272737 shared  ->  66272738 A.e1 -> 66272739 B.e1 -> 66272740 C.e1
                 ->  66272741 A.e2 -> 66272742 B.e2 -> 66272743 C.e2
                 ->  66272744 A.e3 -> 66272745 B.e3 -> 66272746 C.e3
                 ->  66272747 A.e4 -> 66272748 B.e4 -> 66272749 C.e4
```

Every link `afterok`, `--no-requeue`, partition `standard`, 2 CPUs, 8 GB, 60 min each, 0 GPUs. A chain rather
than concurrent arms because the 50 RPM limit is ACCOUNT-WIDE and one job with `workers: 4` already offers about
70 RPM: concurrency divides the same quota and roughly triples the CPU job-hours for the same wall time.

Contract models: epochs 1 and 2 both run under **M0** (learned on the shared rollout, so epoch 1's reports are
**in-sample** and say so in their own text); epoch 3 under `model_arm<X>_epoch02.json` and epoch 4 under
`model_arm<X>_epoch03.json`, each evolved by the arm job at the END of the preceding epoch from that arm's own
traces.

### Authorization and computed need

User, 2026-11-17: 20 CPU job-hours, zero GPUs, 15,000 EmpirioLabs attempts and $8.00, 130 DeepSeek calls and
$4.00, inside the shared 50 RPM account limit.

| resource | computed | authorized |
|---|---|---|
| actor HTTP requests | 12,763 | 15,000 |
| actor charges | $6.22 | $8.00 |
| DeepSeek calls | 102 | 130 |
| DeepSeek charges | $3.49 at peak / $1.75 off-peak | $4.00 |
| CPU job time | ~5 job-hours | 20 |
| GPUs | 0 | 0 |

### Preflight (jobs 66272358 and 66272499, both free — no actor or optimizer key loaded)

| check | result |
|---|---|
| documents the 108 questions need | 153 required, **78 present**, so 33 items had NO document. **150 files (121.1 MB) fetched** from pinned revision `8ecbf18d`, 0 errors; corpus now 153 of 153, verdict PASS |
| availability, all 108 items | **PASS** — 107 AVAILABLE, 1 AVAILABLE_VIA_TOOLS, 0 ABSENT, 0 NO_DOCUMENT, 0 UNTESTED; no verdict changed for the 60 previously tested |
| **UID0019** | **AVAILABLE.** 1 source file, 2 page references, **both resolved** (pages 54 and 58 of `treasury_bulletin_2016_09.txt`); supplied page 3,490 chars, 313 candidate values. Same for UID0039 (val) and UID0064 (held-out) |
| caps under concurrency, on NFS | **PASS** — 4 processes split exactly 23 of a 23 cap (`[4, 8, 8, 3]`), ledger record agrees |
| caps across a restart, on NFS | **PASS** — first process spent 9 of 9, a fresh process with `SLURM_RESTART_COUNT=1` spent **0** |
| caps fail closed | **PASS** (portable case). The chmod case is NOT APPLICABLE on `/nfs/turbo`: POSIX ACLs grant the owner write access whatever the mode bits say |
| route pinned and proven | **PASS** — job 66272737: target `openai_compatible` -> `https://api.empiriolabs.ai/v1` (`qwen3-5-4b`), dispatch proven at zero HTTP cost; optimizer required UNUSABLE and confirmed to have no key |
| route string vs the pinned config | **agrees**, checked offline at zero cost through the trainer's own config resolution |

**Job 66272358 FAILED and the failure was the check, not the ledger** — see DAILY_LOG for the full account.

### What must be read before any result is reported

* `LEDGER_AFTER.json` — `verdict` PASS and `observed` inside the caps.
* `prompt_budget.jsonl` per arm-epoch — the pair count actually sent per slow update and per meta skill, and
  `guidance_produced`. **Expect some slow updates at 9 pairs rather than 10**: the typical 10-pair mix estimates
  to ~230,000 tokens against the 225,000-token limit, so rule OQA-PB-1 trims.
* `slow_update/epoch_NN/slow_result.json` per arm for `action: "no_content"`.
* `route_guard.json` per run dir.
* The **validation-selected** skill (`best_skill.md`) is the PRIMARY comparison; a last-skill evaluation is
  **EXPLORATORY** and must be labelled so.
* The held-out evaluation is NOT in the chain: run `research/officeqa/oqa_heldout.py --plan` afterwards, which
  deduplicates S0 plus each arm's selected and last skill by digest.

### What this campaign cannot establish

The earlier offline probe on the two-arm run's traces found **0 of 8 cases gained a valid diagnosis attributable
to the report and 1 of 8 gained a misleading one** (`docs/research/OFFICEQA_REPORT_VALUE_PROBE.md`). That finding
stands; this campaign tests it at scale and does not presuppose the answer. Nothing here is Lean-verified: the
Lean artifact compiled (job 66259448, all 64 statements, Lean 4.20.0) and the reports rest on the Python
extractor. FP8 makes this a new baseline, so the A40 pilot's S0 does not transfer.

### Epoch 1 result (all three arms), 2026-11-17

S0 on this endpoint: training `hard` **0.125** (3/24); the gate's baseline on the 24 validation questions,
`hard` **0.1667** (4/24). The baseline is SHARED - all three arms were given the same 24 training traces and the
same validation baseline, so none of them re-measured it.

| arm | candidate `hard` on the selection set | gate | epoch wall |
|---|---|---|---|
| A Original SkillOpt | 0.0417 (1/24) | **REJECT** | 629 s |
| B Action Contracts Only | 0.1250 (3/24) | **REJECT** | 733 s |
| C Action Contracts + Stages | 0.0833 (2/24) | **REJECT** | 962 s |

**All three candidates were rejected, so all three arms' `best_skill.md` is S0 at 0.1667.** The gate requires
STRICT improvement, so a candidate needed 5 of 24. Every arm's edit made the skill worse than the starting
skill, not better.

Read with care. The candidate scores do differ (A 0.0417 < C 0.0833 < B 0.1250) but all three are BELOW the
baseline, so this is not evidence that one feedback channel produces better edits - it is one epoch, and the
ordering among three sub-baseline candidates on a 24-question set where one question is 4.2 percentage points
is not a signal. What it does say is that on OfficeQA, with this actor, the optimizer's first edit is harmful in
every condition.

**If that pattern holds through all four epochs**, the primary comparison will be three arms that all retained
S0 - a legitimate negative result about SkillOpt on this benchmark, but one that cannot distinguish the arms,
because an arm that never accepts an edit never exercises the difference between the feedback channels. The A40
two-arm run ended the same way (all four candidates rejected, both final skills byte-identical to S0).

**Report checks on the live run, all 24 items per arm:** arm B carries ZERO stage markers; arm C carries stage
material on all 24 and **contains arm B's report line for line on 24 of 24**; both arms show 24 distinct trace
digests under the single model digest `8d8ecceecd0b7753` (M0); all 24 reports per arm carry the IN-SAMPLE notice;
`boundary_audit` PASS on every one. Arm B's reports average 4,438 characters and arm C's 6,095.

### Arm A epoch 2 — the first gated slow update and meta skill (job 66273856)

Elapsed **65:41**, which settles the time-limit question with evidence: the 60-minute limit the launch plan
proposed would have killed this job, and the chain with it. The nine later-epoch jobs were raised to 120 minutes
before any of them ran.

| what the epoch did | result |
|---|---|
| ordinary update | candidate 0.0833 (2/24) — **REJECT** against 0.1667 |
| slow update, pairs sent | **10 of 10**, ~185,332 tokens against the 225,000 limit, nothing dropped |
| slow update, guidance | **produced**, 3,783 characters, 1,194.7 s |
| slow update, gate | candidate 0.0833 (2/24) — **REJECT**; `action: "reject"`, not `no_content` |
| meta skill, pairs sent | **10 of 10**, ~184,959 tokens, nothing dropped |
| meta skill, memory | **produced**, 2,141 characters, 16.6 s |
| epoch outcome | accept 0, reject 1, best 0.1667 (S0) |

The comparison the slow update worked from: 1 regressed, 1 improved, 8 persistent failures, 0 stable successes
over its 10 sampled training items, with `prev epoch hard=0.1000` and `curr epoch hard=0.1000` — the two skill
versions scored identically on the sample, as expected when the ordinary candidate was rejected and the two
documents differ only by a placeholder.

**The guidance is a sensible diagnosis, for the record:** "Always finish with a final answer (highest priority)
— Your single biggest failure is ending the episode without emitting `<answer>`." That is the same failure mode
the offline probe's guidance identified, and it matches the rollout checkpoints, where items were recorded as
producing no `<answer>` tag.

The meta skill took 16.6 s against the slow update's 1,194.7 s, because the slow update includes its two
10-question rollouts while the meta skill REUSES those comparison pairs — one optimizer call, no rollouts, which
is what `use_meta_skill` beside `use_slow_update` is supposed to cost.

Ledger after this job, verdict PASS: optimizer 32 of 130 calls (25%) and $0.8638 of $4.00 (22%); target 1,864 of
15,000 attempts (12%) and $1.2061 of $8.00 (15%); 4 failed attempts, **zero refusals**.

---

## OQA-3ARM: THE RESULT, and a defect in my own instrumentation that cost two epochs

**The chain completed: 66273855 through 66273864, all COMPLETED.** The result below is what the artifacts say,
with the part that is sound separated from the part that is not.

### The gate decisions, per arm per step

| step / epoch | arm A Original | arm B Action Contracts | arm C Action + Stages |
|---|---|---|---|
| 1 | reject 0.0417 | reject 0.1250 | reject 0.0833 |
| 2 | reject 0.0833 | **accept_new_best 0.2083** | reject 0.0833 |
| 3 | reject 0.0417 | reject 0.0417 | **skip_no_patches** |
| 4 | **skip_no_patches** | **skip_no_patches** | **skip_no_patches** |
| **best** | **S0, 0.1667** | **step 2, 0.2083** | **S0, 0.1667** |

S0 is 4/24 = 0.1667 on the gate. Arm B's accepted skill is 3,601 characters from S0's 887, adding sections on
committing to an answer once the operands are held, operands that are not in the local bulletins, repeating
column blocks and year-only-on-first-row tables, and decomposing multi-hop questions.

### `skip_no_patches` is a DEFECT IN MY LEDGER, not a property of the arms

Four arm-epochs — **arm C epoch 3, and epoch 4 in all three arms** — made **ZERO optimizer calls**. 24 attempts
were refused. The cause is mine:

**The ledger enforces the cap on CLAIMED money and never released what a process did not spend.** Each process
kept roughly one `per_call_reserve_usd` plus block rounding at exit. Across 13 processes:

```
optimizer   spent $2.1913   CLAIMED $4.0000 (the whole cap, 16 blocks of $0.25)   wasted $1.8087 = 45%
target      spent $4.9441   CLAIMED $7.0000 of $8.00                              wasted $2.0559
```

Once the claims reached $4.00 every further optimizer attempt was refused — while the provider had been paid
$2.19 of an authorized $4.00. The cap did its job on the wrong quantity. The actor was four blocks from the same
fate.

It also cost money for nothing: those four jobs ran their actor rollouts before the analyst was refused —
**2,161 attempts and $1.4976 spent on rollouts whose analyst never ran.**

### Therefore: what is sound and what is not

* **Epochs 1 and 2 are complete and symmetric in all three arms**, with full optimizer access. This is the
  comparison.
* **Epoch 3 is asymmetric**: arms A and B ran it (both rejected), arm C did not.
* **Epoch 4 is empty in every arm** — symmetric, and contributes nothing anywhere.

### What the sound window supports, and it is not what the headline looks like

Arm B is the only arm whose gate accepted anything. **That is not evidence that action contracts help.** The
margin was 5/24 against 4/24 — ONE question — and `docs/research/OFFICEQA_GATE_RESOLUTION.md` works out what
that instrument can decide:

```
P(a candidate exactly as good as S0 measures >= 5/24, i.e. is ACCEPTED)   = 0.371
gate evaluations in the sound window (ordinary + slow update, all arms)   = 9
expected acceptances IF EVERY CANDIDATE WERE MERELY AS GOOD AS S0         = 2.99
observed acceptances                                                      = 1
P(at most 1 acceptance | every candidate neutral)                         = 0.141
```

**The campaign produced FEWER acceptances than the null predicts.** One acceptance at a one-question margin out
of nine evaluations is the modal kind of noise outcome, and the total is on the low side of chance. The
supported conclusion is that **no feedback channel in this campaign produced edits the gate could distinguish
from neutral**, and arm B's single acceptance does not separate it from arms A and C.

### What DID differ between the arms

The arms' edits are genuinely different documents, which is the thing the design was meant to produce:
epoch 1's three candidates were 4,174 / 4,652 / 4,420 characters from S0's 887, with different content. The
feedback channels change what the optimizer writes. What they do not change, at this resolution, is whether the
gate accepts it.

### Everything that was supposed to be checked, checked

| check | result |
|---|---|
| arm B carries no stage material | 24 of 24 reports, zero stage markers |
| arm C contains arm B, epoch 1 | 24 of 24, line for line (both arms under M0) |
| epoch 1 reports labelled in-sample | 24 of 24 per arm |
| one starting contract model | 24 distinct trace digests per arm under the single M0 digest |
| answer-key isolation | `boundary_audit` PASS on every report |
| slow update pair counts | 10/10 sent in 6 of 9; trimmed to 8, 7 and 9 in the three draws that exceeded the limit; those three plus one more produced no guidance BECAUSE of the ledger, not the size |
| guidance produced | epochs 2 and 3 where the optimizer was reachable: arm A 3,783 and 3,089 chars; arm B 4,523 and 3,566; arm C 3,683 |
| contract evolution | ran for every epoch in both arms — it is offline, so the starvation did not touch it |

**Arm C's reports no longer contain arm B's from epoch 2 onward, and that is correct**: from epoch 2 each
contract arm evolves its OWN model from its OWN traces, so the action halves legitimately diverge. The
containment invariant is an epoch-1 property, where both arms run under M0 on identical traces. My first audit
flagged the divergence as a failure; the audit was wrong, not the campaign.

### What is recoverable, and what it would cost

Every expensive thing the starved epochs needed is already on disk: the 24-question training rollout and both
10-question slow-update rollouts for all four. What is missing is only the optimizer calls that were refused
and the gate passes for the candidates they would have produced.

```
per starved arm-epoch   48 question-rollouts (two gate passes) = $0.31 actor  +  ~9 calls = $0.33 optimizer
four of them                                                     $1.26 actor  +  $1.32 optimizer
remaining authorization                                           $3.06 actor  +  $1.81 optimizer
after completing them                                             $1.80 actor  +  $0.49 optimizer
held-out, per distinct skill                                      $0.39 actor  +  $0    optimizer
```

So completing all four AND the held-out evaluation fits inside the authorization already granted. It needs the
user's decision, because it spends about $2.60 more of their money and it means resetting each arm's
`runtime_state.json` to re-run a step the record currently shows as `skip_no_patches`.

---

## OQA-3ARM-RECOVERY — recovering the four starved arm-epochs (jobs 66323200–66323203, 2026-11-17)

**Authorization.** The amendment of 2026-11-17: EmpirioLabs **$16 / 30,000 attempts**, DeepSeek **$8 / 260
attempts**, CPU **40 job-hours**, GPUs **zero**, as TOTALS including everything already spent, with the
recovery and the planned evaluations authorized without further approval inside them.
`docs/research/OFFICEQA_AUTHORIZATION.md` holds the grant's wording and the arithmetic.

**Deployed revision.** Bundle `oqa_campaign_20261117c`, commit
`37c78ec683d53f656c0aceed9fde413b4ce1f941`, pinned `git archive` with an in-job `MANIFEST.sha256` check.
`git diff --stat 797846e..37c78ec -- skillopt/ configs/ scripts/` touches two files and **deletes no line of
`skillopt/envs/officeqa/rollout.py`**: the actor's prompt construction, tool loop and scoring are identical to
the original run's, and the only new behaviour is the provenance guard (inert unless its environment variable
is set) plus one extra field on each result row. That field cannot move a trace digest, because
`trace_digest()` hashes `conversation.json` and `target_user_prompt.txt` from the per-item prediction
directory, not the `results.jsonl` row.

### 1. The ledger, reconciled (rule OQA-REC-1)

| role | net claimed before | recorded spend | net claimed after | released |
|---|---|---|---|---|
| optimizer | $4.0000 / 100 attempts | $2.1913 / 66 | $2.1913 / 66 | $1.8087 / 34 |
| target | $7.0000 / 7,700 attempts | $4.9441 / 7,313 | $4.9441 / 7,313 | $2.0559 / 387 |

52 release records. **Zero unresolved requests** — every attempt in this campaign has a matching usage record,
so nothing was retained on that account. A second reconciliation finds nothing left to release. Record:
`recovery/LEDGER_RECONCILE.json`.

**A rounding disagreement the reconciliation exposed.** `oqa_ledger_read.py` then reported FAIL: "$4.9441
spent against $4.9441 claimed". The ledger adds `int(round(usd * 1e6))` per usage record and compares *that*
against the claims; the audit summed raw floats, which over 7,313 records came out **26 microdollars higher**
($4.944136 against $4.944110). Fixed on both sides: the audit checks the invariant in micros and reports the
float sum beside it, and the reconciler retains the larger of the two sums.

### 2. The stage audit (rule OQA-REC-2) — exactly four arm-epochs

Read from the artifacts **and** the ledger's per-process refusal records. All **24 refusals** sit in four
jobs, six each (4 analyst, 1 slow update, 1 meta skill):

| job | arm-epoch | refusals |
|---|---|---|
| 66273861 | C.e3 | analyst 4, slow_update 1, meta_skill 1 |
| 66273862 | A.e4 | analyst 4, slow_update 1, meta_skill 1 |
| 66273863 | B.e4 | analyst 4, slow_update 1, meta_skill 1 |
| 66273864 | C.e4 | analyst 4, slow_update 1, meta_skill 1 |

**No other stage was masked.** Two shapes that look like failures and are not, both checked by hand before
the classifier was written and now explained by it:

* **arm B epoch 3 made 3 analyst calls, not 4.** No refusal in that job. It scored **0/24**, so there was no
  success group to reflect on — one analyst call fewer is the correct behaviour.
* **arm C epoch 1 made no ranking call.** Its 4 merged edits already fit the edit budget of 4, so there was
  nothing to rank; all four were applied.

Record: `recovery/RECOVERY_AUDIT.json`.

### 3. What was reused, and what decided it

Every starved arm-epoch still held its paid 24-question training rollout and both 10-question slow-update
rollouts. `run_batch` resumes by id from `results.jsonl`, so leaving those directories in place returns the
identical trajectories at no cost.

Reuse is decided by **provenance, not position**. Each stored row records the system prompt it ran under, and
the prompt embeds the skill verbatim, so the producing skill is recovered from the trace itself and matched
against the skill the recovered state puts in force (longest match wins; ambiguity is reported, and a rollout
that cannot be attributed is never reused). Measured on the live artifacts:

| artifact | per arm-epoch | decision |
|---|---|---|
| training rollout | 24 items | reuse (A.e4, B.e4, C.e3); **conditional** for C.e4 |
| slow-update `rollout_prev` | 10 items | reuse (A.e4, B.e4, C.e3); **conditional** for C.e4 |
| slow-update `rollout_curr` | 10 items | **conditional** in every arm — it runs the skill the recovered gate leaves in force |
| `comparison_pairs.json` | 10 pairs | rebuilt from whichever trajectories survive the check (free) |

"Conditional" is settled at run time by `SKILLOPT_ROLLOUT_PROVENANCE_GUARD=1` rather than guessed: on a reject
the rows are reused, on an accept they are re-rolled, the superseded `results.jsonl` is preserved beside the
new one and the live one is kept canonical so no id appears twice.

Plan (`recovery/RECOVERY_PLAN.json`): **192 question-rollouts certain** ($1.2595, 1,981 attempts), **266 worst
case** ($1.7450, 2,745 attempts), plus ~36 optimizer calls and ~$1.44.

### 4. The checkpoint the recovery rewound to

| arm | last_completed_step | current_skill | incumbent the recovered gate must beat | best |
|---|---|---|---|---|
| A | 3 | `skill_v0003.md` | 0.1667 (4/24) | S0, step 0 |
| B | 3 | `skill_v0003.md` | **0.2083 (5/24)** | step 2 |
| C | 2 | `skill_v0002.md` | 0.1667 (4/24) | S0, step 0 |

The incumbent is reconstructed from the previous step's record **and** the previous epoch's slow-update result,
because an epoch does not end with its ordinary step — a slow update that accepted would make the step record
stale. Every slow gate here rejected, so no number moved.

**51 original records archived** to `recovery/original/20261117-recovery` (4.2 MB): each arm's
`runtime_state.json`, `history.json`, `summary.json`, `best_skill.md` and `skills/`, the step records and
slow/meta results from the first recovered epoch on, and both contract arms' model and EVOLVE files.

**Moved aside, not deleted:** arm C's `model_armC_epoch04.json`, `EVOLVE_armC_epoch04.json` and
`predictions_epoch04.jsonl`, because epoch 4's traces can change and `oqa_lc_ir.freeze()` refuses to overwrite
a frozen model with different content. **Kept:** each arm's first-recovered-epoch model
(`model_armC_epoch03.json`, `model_armB_epoch04.json`) — its rollout is reused unchanged, so re-evolving it is
a no-op and the file holds its original bytes.

### 5. The chain

```
66323200  oqa-rec-A-e4   arm A epoch 4   head of the chain
66323201  oqa-rec-B-e4   arm B epoch 4   afterok 66323200   model model_armB_epoch03.json
66323202  oqa-rec-C-e3   arm C epoch 3   afterok 66323201   model model_armC_epoch02.json
66323203  oqa-rec-C-e4   arm C epoch 4   afterok 66323202   model model_armC_epoch03.json
```

`standard`, 2 CPUs, 8 GB, 90 min, `--no-requeue`, `afterok` throughout. Per-job ceilings 1,600 attempts /
$1.20 actor and 16 calls / $0.70 optimizer. Arm C epoch 4 follows epoch 3 because it begins from the skill
epoch 3 leaves in force.

**First evidence that the design works** (job 66323200, 6 minutes in): `[resume] from step 4 current=0.1667
best=0.1667`; the provenance guard checked all 24 saved training rows, reused them, and printed that their
turn budget could not be checked because they predate the settings fingerprint; the analyst ran and produced
3 failure and 1 success patch, 5 edits merged, 2 selected at budget 2, a candidate of 2,847 characters from a
940-character skill; **zero** actor attempts for the training rollout and 48 so far, all from the gate pass in
progress.

## OQA-3ARM-DIAGNOSTIC — reading the finished campaign's own records (rule OQA-DIAG-1)

**Date** 2026-11-17. **Cost** 0 actor attempts, 0 optimizer calls, $0.00, 0.13 CPU core-hours, 15 Slurm
jobs. No API key is in scope in the job. **Commit** `66b2b66`, **bundle** `oqa_diag_20261117`,
**final jobs** 66337036 (diagnostic) and 66337037 (tests: 2,215 passed, 9 skipped, 368 subtests).

**Tool** `research/officeqa/oqa_diagnose.py`: `gates` (the 21-gate ledger, with every candidate document
proved verbatim in its own 24 system prompts and every score and decision recomputed), `questions` (the
24 × 22 outcome matrix with wrong / no-answer / execution-failure separated), `replicates` (groups every
rollout by skill digest and question set and reports the spread), `contracts`, `chains`.
**Outputs** `$CAMP/diagnostic/DIAG_{GATES,QUESTIONS,REPLICATES,CONTRACTS,CHAINS}.json`.

| result | value |
|---|---|
| gates enumerated | 21 (12 ordinary + 9 slow), all integrity checks PASS |
| S0 baseline | 4/24; the three arms' copies byte-identical (md5 b16ccc9c…) |
| candidate outcomes, pooled 21 × 24 | 33 correct, 105 wrong answers, 363 no answer, 3 out of tool turns |
| gains / losses vs S0 | 27 / 78; 6 of 84 chances to retain an S0-correct answer were taken |
| **fixed document, 7 draws, same 24 training questions** | **1, 1, 2, 2, 3, 5, 6 — sd 1.95, range 5** |
| arm B's accepted skill, 2 draws | 0/24 and 3/24 |
| score vs commitment rate (21 candidates) | Pearson r = 0.775, Spearman 0.766 |
| score vs candidate length | Pearson r = −0.075 |
| calls to a nonexistent tool | 211 campaign-wide (`answer` 211, `calculate` 7, `tool` 3 of 10,814 calls); 109 on selection evals; 0 under S0 |
| numerically-correct answers scored wrong | 5 of 528 — and under numeric equality the campaign has ZERO acceptances |
| contract vocabulary in proposed edits | 0 of 101 entries (71 merged + 30 ranked) |
| arm B action contracts | 96 reports, 0 distinct diagnoses, 1 violated forecast in 2,676 |
| arm C stage obligations | 69 discharged / 130 outstanding / 252 unknown / 11 violated, the 11 all in one report |
| sampling seed sent to the actor API | **none**; temperature 0.7 on every request |
| truncation / exceptions | 0 / 0; 3 tool-turn-budget hits in the whole campaign |

**Recovered stages verified**: A.4, B.4, C.3 and C.4 each made 4 analyst + 2 merge + 1 ranking + 1
slow-update call. The only two rounds with fewer are B.3 (3 analyst / 1 merge — arm B scored 0/24 on that
epoch's training rollout, so there was no success minibatch) and C.1 (0 ranking calls — 4 merged edits
against a budget of 4). All twelve arm-epochs wrote a meta skill except the three documented epoch-1 skips.

**Evidence packet** `docs/research/OFFICEQA_DIAGNOSTIC_PACKET.md`. Held-out job 66332134's number was left
unread while the packet's proposals were written, so none of them is tuned on held-out.

## OQA-3ARM-FORENSICS — the empty replies and the contract machinery (rule OQA-FOR-1)

**Date** 2026-11-17. **Cost** 0 actor attempts, 0 optimizer calls, $0.00, 0.13 CPU core-hours, 3 jobs; no
API key in scope. **Commit** `4cf50fc`, **bundle** `oqa_for_20261117`, **jobs** 66338091 (forensics),
66338092 (tests: 2,223 passed, 9 skipped, 368 subtests). **Outputs**
`$CAMP/diagnostic/FOR_{NOANSWER,CONTRACTS}.json`. **Packet** `docs/research/OFFICEQA_FORENSICS_PACKET.md`.

**New source.** `oqa_campaign_body.sh` set `TARGET_OPENAI_COMPATIBLE_CALL_LOG`; 16 jobs wrote
`actor_calls.jsonl` with the raw provider reply per attempt. 7,909 records, cross-validated against the
ledger (same 7,909 usage records, same 8,503,592 completion tokens).

| result | value |
|---|---|
| raw replies, finish_reason × content | tool_calls/null 7,056; stop/null 533; stop/text 221; tool_calls/text 87; length/text 12 |
| `reasoning_content` non-null | 0 of 7,909 |
| saved `last_finish_reason` | `""` in all 528 rows (the field is never assigned in this code path) |
| the 528 selection observations | 331 empty reply after generating; 107 wrong answer; 37 correct; 27 turn budget (unrecorded); 16 S0 silent (no raw log); 7 truncated at 8,000; 3 turn budget (recorded) |
| prose without a tag, terminal | **0** |
| completion tokens on the 331 | min 96, median 920, mean 1,261, max 4,076; none zero |
| the 533 empty replies, campaign-wide | 672,104 completion tokens, $0.2004 by the provider's `cost_usd` |
| provider cost vs ledger, same 16 jobs | $3.2768 vs $5.3159 — the ledger is conservative (it bills cache reads, the provider does not) |
| unsupported tool calls | 221 campaign-wide (`answer` 211, `calculate` 7, `tool` 3); 109 in 54 of the 528 episodes; 8 of those 54 still answered |
| missed-extraction experiment | 0 of 7 would have scored (real extractor fallback + real scorer) |
| expressible contracts / learned / retired | **20 / 5 / 1** |
| evolve rounds with a counterexample | 1 of 6 (arm B epoch 3, `UID0019/e3@10`, retired `read.effect.2`) |
| arm C obligation group lines, by kind | answer_traced 10 D / 85 O / 1 U; arith_recorded 6 D / 30 O / 60 U; cell_bound 7 D / 13 U; period_match 4 D / 11 U; unit_match 4 D / 2 O / 4 U / **2 V** |
| arm B `n_distinct_diagnoses` | a hard-coded `0` for LC_ACTION in `oqa_reports.py` — measures nothing |
| report → optimizer | 24 of 24 arm-C epoch-3 reports byte-for-byte inside `run_66323202_arm_C_e3/optimizer_calls.jsonl` |
| model schedule | epoch N's reports cite the model frozen at the end of epoch N−1, verified by digest and freeze time |
| in-sample warning | printed on 96/96 per arm; genuine on the 24 epoch-1 reports; by trace digest epochs 2–4 are fresh (0/24 identical in every epoch pair) except arm C UID0019, a collision from the lossy record |

**Defects found in the records themselves:** `last_response_metadata` unassigned in the local-tools
branch; `compat_message_from_chat_message` drops finish_reason/reasoning_content/usage;
`n_distinct_diagnoses` hard-coded for LC_ACTION; the model record's `epoch` field is the last change, not
the freeze; `parent_sha256` null in all seven models; the in-sample test compares item ids.

## OQA-FOLLOWUP — the HTTP probe (OQA-HTTP-1) and the connected-contract prototype (OQA-CC-1)

**Date** 2026-11-18. **Grant** 3. **Spend** 23 of 24 EmpirioLabs attempts ($0.004679 provider-reported,
$0.007213 at our price table), 3 of 6 DeepSeek attempts ($0.008363 ledger), 4.00 of 4 requested CPU
job-hours over 17 jobs, zero GPUs. **Bundle** `oqa_fu_20261118`. **Tests** 2,259 passed, 9 skipped, 368
subtests (job 66341945). **Report** `docs/research/OFFICEQA_FOLLOWUP_PACKET.md`.

### OQA-HTTP-1

Selection frozen before any request (`SELECTION.json`, digest `25718e4ed2f16c8c`): 396 terminal TRAINING
calls indexed, 199 empty and 83 answered; EMPTY_1 = UID0163 arm A e2 (4,043 completion tokens), EMPTY_2 =
UID0017 arm B e2 (4,028), ANSWERED_1 = UID0063 arm A e3 (729).

| setting | sends | transmitted | `reasoning` returned | completion tokens | finish | empty terminal replies |
|---|---|---|---|---|---|---|
| historical replay | 9 | nothing added | 105–3,030 chars, every reply | 119–845 | 7 tool_calls, 2 stop | **2 of 9** |
| `enable_thinking: false` | 9 | `{"enable_thinking": false}` | key absent | 82–180 | 9 tool_calls | 0 of 9 |
| historical + fallback | 5 | nothing added | present | — | 5 tool_calls | 0 of 5 |

**The defect:** `choices[0].message.reasoning` carried the model's text while `content` was null; the SDK
surfaces it only through `model_extra`; the client read `content` and `reasoning_content`. One reproduced
reply ends `<answer>[10102000000, 4.73]</answer>`. Outcome class: *content in the HTTP body lost in client
handling*. Correction: `reasoning_text()` plus an off-by-default content fallback.

### OQA-CC-1

Record split rule v1 → 3 usable of 24, EMPTY checking set (binding constraint: 7 of 24 traces contain any
number the actor wrote, 0 contain a stated conversion factor). Rule v2 → 8 traces: learning arm B e2
UID0026/0028/0063/0163, checking arm B e3 UID0044/0079/0115 and arm C e3 UID0014; 4 ids reserved.

| step | result |
|---|---|
| proposal | 1 DeepSeek call, 13,484 prompt / 1,489 completion tokens; a conditional `scale_binds` with a stated withdrawal; validated |
| first compile | UNKNOWN on all 240 learning cells — `nearer_scale_between` had no evaluator; `unevaluable()` is now a second gate |
| frozen model | `CC_MODEL_v1.json` `96c3d1bd9aad6f4d`, 180 HOLDS / 60 UNKNOWN / 0 CONFLICT of 240 cells |
| prefix-only checking | 4 attempts, 24 committed predictions, all outcomes `unknown` (no trace states a factor) |
| repair | **none: no counterexample in the permitted records.** Repair exercised by labelled constructed tests |
| Lean | `OqaConnected.lean`, Lean 4.20.0, core only, **return code 0**, 23 evidence examples, 1 skipped |
| comparison | automated verdicts 2 earlier_or_more_precise / 1 repeats / 1 unknown — and on inspection they overstate: the prototype bound YEARS where the historical stage report bound the real money operands and named the 1,000x error |

---

## 2026-11-18 — OfficeQA answer recovery, verified end to end, and S0 re-measured (rules OQA-REC-2, OQA-CC-2)

Full evidence: `docs/research/OFFICEQA_RECOVERY_PACKET.md`. Amendments A-2 and B-2. Bundle
`oqa_fu2_20261118`, commits `0119b4f` → `4572c61`. All output under
`$CAMP/followup_20261119/`; the frozen campaign and `followup_20261118/` unchanged.

| job | phase | state | elapsed | what it produced |
|---|---|---|---|---|
| 66346074 | tests | FAILED | 0:56 | caught the misplaced row init and the adapter metadata guarantee |
| 66346177 | tests | COMPLETED | 1:04 | 2,318 passed |
| 66346233 | prep | COMPLETED | 0:05 | ledger backfill (23 / $0.007213), episode plan `a93d9eb6f4e58f40`, replay of the 23 saved bodies, bundle v2 `7809287366d50598` |
| 66346256 | episodes | COMPLETED | 8:58 | **12 episodes, 12 answers, 12 correct**; 95 exchanges captured; 6 recovered from `reasoning` |
| 66346322 | cc | FAILED | 0:09 | caught the prediction shape and the two undecidable Lean statements |
| 66346539 | s0 | COMPLETED | 1:09:44 | passes 1–4: 11, 9, 12, 11 |
| 66346540 | s0 | TIMEOUT | 1:20:00 | passes 5–7: 9, 11, 8; pass 8 at 11 of 24 rows |
| 66351468 | s0 top-up | FAILED | 18:28 | pass 8 finished (24 rows, hard 7); died afterwards on a body script replaced under it |
| 66352197 | cc | COMPLETED | 0:08 | re-score, recheck, compare, repair, Lean rc 0, S0 summary |
| 66352245 | cc | FAILED | 0:12 | all work done; non-zero only because the v3 bundle freeze refused a provenance-only difference |
| 66352312 | tests | COMPLETED | 0:59 | **2,321 passed, 9 skipped, 368 subtests** |

**S0, 24 validation questions, eight passes, corrected client (condition A):** 11, 9, 12, 11, 9, 11, 8,
7 — **mean 9.75, sd 1.753, range 7–12**; answers emitted 22, 19, 21, 22, 21, 22, 20, 23 (**mean 21.25**);
recovered from the discarded field 16, 13, 17, 17, 15, 15, 12, 12; **zero provider errors in 1,476
attempts**; one skill and one settings fingerprint across all passes. **The campaign recorded 4/24 with 6
emitted for the same skill on the same questions.** Paired: 5 questions always correct, 9 never, **10
flip**, 1 never answered.

**Connected contract after the extraction repair:** 19 learning and 23 checking predictions, **0
VIOLATED / 0 OUTSTANDING / 0 DISCHARGED**, all four comparison verdicts `remains_unknown`, no
counterexample, **Lean 4.20.0 return code 0**. The measured obstacle: 1,484–2,022 of the data cells in
these records have no bound column header, which the applicability condition requires.

**Cost:** 1,598 actor attempts, $0.9789 at our price table ($0.6257 provider-reported), **0 DeepSeek
calls**, 6.88 requested CPU job-hours, 0 GPUs.

---

## 2026-11-18 — AUDIT of the recovery report (rules OQA-AUD-1 / OQA-AUD-2)

**Jobs 66356410 (FAILED, exit 1, all artifacts written), 66356411 (tests, COMPLETED), 66356437
(COMPLETED, 7 s).** Bundle `$PROJECT_ROOT/oqa_aud_20261118`, commits `ba53007` then `3974433`.
Artifacts `$CAMP/audit_20261118/`. **0 API calls, 0 GPUs, $0.00, 0.87 requested CPU job-hours of 2.**
Tests **2,326 passed, 9 skipped, 368 subtests**.

**Workstream A.** 192 of 192 intended `(pass, question)` records present exactly once; 0 duplicates, 0
extra rows, 0 missing; **35 of 35 assertions pass**. Per pass (records / correct / emitted / recovered /
content / attempts / errors): p1 24/11/22/16/6/154/0, p2 24/9/19/13/6/215/0, p3 24/12/21/17/4/202/0,
p4 24/11/22/17/5/148/0, p5 24/9/21/15/6/190/0, p6 24/11/22/15/7/178/0, p7 24/8/20/12/8/212/0,
p8 24/7/23/12/11/177/0. Totals 78 correct, 170 emitted, 117 recovered, 53 content, 1,476 recorded
attempts, 0 errors. Mean 9.75, sd 1.753, range 7–12; emitted mean 21.25, range 19–23. Terminations over
192: 117 recovered / 53 emitted / 18 turn-budget / 3 truncated / 1 declined. Answered in all eight
passes 17, sometimes 6, never 1; correct always 5, sometimes 10, never 9. **Corrected against the
published §7:** 179 → 192 rollouts, 111 → 117, 47 → 53, 17 → 18, 21 → 22 unanswered, 20 → 17 always
answered. Cause: an aggregation over p1–p7 plus the 11 pass-8 rows that existed before the top-up;
reproducing that cut gives the published numbers exactly. **4 unresolved requests** (one process's 61
attempt vs 57 usage records, claim retained, no release) and **6 abandoned attempts** (UID0159 ×5,
UID0161 ×1, re-run from scratch by job 66351468).

**Input match.** System prompt and user prompt byte-identical to the campaign's S0 validation baseline
for 24 of 24 questions in all 8 passes (digest `b41c9dbeec8d43c5`); transmitted payload identical
(`qwen3-5-4b`, temp 0.7, max_tokens 8000, tools on, `extra_body: null`); `heldout/S0.md` 887 B
`9d377d1c2906` = 886 B `b2f8624c8da8` with its trailing newline stripped; `arm_A/skills/skill_v0000.md`
byte-identical to it. Explained differences: the settings fingerprint is absent from the campaign's rows
(the field postdates them) and the recovery policy is the intervention under test.

**Workstream B.** Primary causes over 196 operands in 20 records: `operand_is_in_no_cell` 141,
`several_plausible_bindings` 19, `header_observed_structure_lost` 29, `bound_no_failure` 7;
`header_never_observed` 0. Table header forms: 50 split, 44 single, 11 flat, 50 none. After amendment
B-3: year-shaped monetary operands 26 → 0, unbound data cells 5,791 → 5,184, checking records' holding
bindings **0 → 4** (resolutions 4 OUTSTANDING, 3 UNKNOWN), learning 8 → 12 unique matches, descriptive
**3 DISCHARGED**. `CC_BUNDLE_v4` = `1c165508ed8d7aae`; v3 reports 5 changed components, all three
repairs. Comparison: `remains_unknown` 3, **`repeats_what_was_available` 1** (UID0014, 4 findings, 4
well bound, against a historical report that already states VIOLATED). No counterexample. **Lean 4.20.0
return code 0**, 15 statements, 7 evidence examples.

**Proposals, measured and not applied.** P-1 (read the operand's own message when resolving): the four
UID0014 findings become `incorrect_factor_stated`, VIOLATED, wrong by 10^3. P-2 (a fully observed header
row naming no money scale witnesses the absence of a nearer scale): 29 cells decidable, 8 reach a
binding.

---

## 2026-11-18 — ALFWorld context-aware stage checking (rule AWC-1)

**Jobs 66385774 (tests, COMPLETED), 66385775 (lean, FAILED rc1), 66385916 (lean, COMPLETED rc0).**
Bundle `$PROJECT_ROOT/awc_20261118`, commits `d98fd6a` then `7abbb91`. Artifacts
`$RUNS/20261118-alfworld-context/` (local and cluster). **0 API calls, 0 GPUs, 0 new gameplay, 0.6 of 2
requested CPU job-hours.** Tests **2,353 passed, 9 skipped, 368 subtests**; **Lean 4.20.0 rc 0**, 46
statements, 20 evidence examples, 0 skipped.

**Conditions.** `current` = `stage_common.Reading` (the frozen shared reading; INSPECT clause (a) only).
`context_aware` = `a4_reading.ReadingA4(rev=2)` (A4 §3.4: lamp clause (b), same-receptacle (c),
temperature-override (d)). `a4_rev1` recorded as a third condition because it does NOT reproduce the
pre-revision reading for `look_at_obj_in_light`. Frozen snapshot digests: `stage_common.py`
`570f2049205c7c99`, `a4_reading.py` `70b707f4f10a434f`, `vocab.py` `e857150a39a166bd`, `tor_record.py`
`b40c448ebaa07f2e`. Manifest `5517922e569456b2`; semantic bundle `db73dae18d5acf41`; vocabulary
`lc.vocab.v1` `5005c74b9dbd409b`.

**Frozen set (24 attempts, 12 tasks, two per type, all outcome-balanced): 0 differences** between the
two readings. Every success recognised at the same step; no failure claimed by either.

**Supplementary scans, every saved `tr:` training attempt with a trace in a `*_evolve_*` rollout:**

| type | attempts | changed | success | failure |
|---|---|---|---|---|
| pick_and_place | 155 | 0 | 0 | 0 |
| pick_two_obj_and_place | 157 | 15 | 14 | 1 |
| pick_clean_then_place_in_recep | 132 | 0 | 0 | 0 |
| pick_heat_then_place_in_recep | 100 | 3 | 3 | 0 |
| pick_cool_then_place_in_recep | 112 | 0 | 0 | 0 |
| look_at_obj_in_light | 80 | 1 | 1 | 0 |
| **total** | **736** | **19** | **18** | **1** |

Examine-in-Light discharges: 28 on clause (a), 1 on clause (b).

**Classification of the 19:** `corrected_progress_recognition` 1 (tr:0293, in-sample);
`more_precise_or_earlier_existing` 17 (every one *later*, not earlier);
`removal_of_a_false_diagnosis` 1 (tr:2986, out-of-sample);
`valid_additional_execution_diagnosis` 0; `redundant_information` 0;
**`unsupported_inference_or_unresolved_unknown` 0.** By distinct task: 8 tasks, 4 in-sample
(tr:0293, tr:3287, tr:2785, tr:2658), 4 out-of-sample (tr:3042, tr:2986, tr:2395, tr:2456).

**The lamp witness (tr:0293, 42 steps, scored 1).** take tissuebox 1 (t=1) → 35 steps of search →
arrive dresser 1 (t=37) → move tissuebox 1 to dresser 1 (t=38) → **use desklamp 1** (t=39, hands empty)
→ examine dresser 1 (t=40) → **take tissuebox 1 from dresser 1** (t=41). Current: never discharged
(missed completion). Context: discharged at t=41 on basis (b), assumptions `lamp_stays_on` + `same_place`.

**The failure witness (tr:2986, 50 steps, scored 0).** cup 1 → cabinet 3 (t=29, confirmed t=39, t=49);
cup 4 → cabinet 6 (t=45, confirmed t=46). Current: goal reached at t=45. Context (clause c): never
reached. The saved ordinary analysis names this attempt under `navigation_loop` — "they never find the
goal object" — which is false of it.

**Relationship to the saved ordinary analysis:** `failure_patterns[].task_ids` lists only the failing
attempts of a step, so 18 of the 19 changes (all on successes) are not named there by construction; every
affected step's digest reports the single pattern `navigation_loop`. The context-aware reading improves
*when* a success is recognised and produces no failure diagnosis the ordinary analysis lacked.

## OQA-ST-1 — the status-and-evidence packet, 2026-11-18

**Purpose.** Not an experiment: an evidence read, so the next step can be chosen from the records. Report:
`docs/research/OFFICEQA_STATUS_PACKET.md`. Artifacts: `<campaign>/status_20261118/`.

**Authorization.** Grant 6: 1 requested CPU job-hour, ≤2 CPUs per job, zero model API calls, zero provider
probes, zero GPUs, zero gameplay/training/evaluation, saved artifacts only. **Used 0.90 job-hours, 0
attempts, $0.00, 0 GPUs.**

| job | phase | requested | elapsed | commit | outcome |
|---|---|---|---|---|---|
| 66395263 | status | 0:15 | 0:33 | `d777d2d` | inventory, statements, feedback chain written |
| 66395275 | tests | 0:12 | 0:59 | `d777d2d` | 2,366 passed, 9 skipped, 368 subtests |
| 66395597 | status | 0:15 | 0:04 | `855612c` | **authoritative** |
| 66395598 | tests | 0:12 | 0:57 | `855612c` | **2,367 passed**, 9 skipped, 368 subtests |

**Method.** `research/officeqa/oqa_status.py`, three subcommands. `inventory` establishes which record
sets came through the corrected client from each run's own call log — the transmitted model, sampling
controls and `extra_body`, and the recovery policy the client applied — never from a directory date, and
takes training/development membership from the frozen split record. `feedback` joins one attempt's trace,
extracted facts, delivered report, ordinary SkillOpt analysis, optimizer proposal, merge, gate decision
and skill survival. `statements` decides six status claims by assertion, reporting an absent artifact as
UNDECIDED rather than as a pass. The 2026-11-18 audit's `BIND_AUDIT.json` and `S0_AUDIT_MANIFEST.json`
are joined, not recomputed.

**Results — recorded.**

| | |
|---|---|
| Six status statements | **6 of 6 HOLD**, 0 undecided, 0 failing |
| Corrected-client record sets | 4 development runs (12 records, all `train`) + S0 × 8 passes (192 records, `val`) |
| Campaign arm rollout files carrying any recovery policy | **0 of 12** — every one predates amendment A-2 |
| Held-out runs carrying any recovery policy | **0 of 3** (S0 3/60, arm-B selected 6/60, exploratory 8/60) |
| **Corrected-client training failures** | **0** — all twelve episodes `hard = 1` |
| Actor-visible trace, thinking on (A1/A2) | **20–36 characters**, 0 monetary operands, no arithmetic, 6 of 6 records |
| Actor-visible trace, `enable_thinking: false` (B1/B2) | **575–8,864 characters**, 2–66 monetary operands, arithmetic present, 6 of 6 records |
| Records reaching a holding binding | **1 of 12** (B2/UID0163, 3 operands, resolution `DISCHARGED`, required factor 10⁰) |
| Provider alternate field, A-condition episodes | 667–10,349 characters, kept only in `actor_calls.jsonl` / `raw_capture.jsonl` |
| UID0014 final call | `finish_reason: "length"` — truncated at the 8,000-token cap, so no answer; **not** the empty-content defect |
| UID0014 operands | 693 candidates → 42 monetary → 12 predictions → **4 holding bindings** (3 distinct values), required factor 10⁻⁶ |
| UID0014 deployed resolution | **OUTSTANDING ×4** — the operand's message is step 45 of 45, so the later window is empty |
| UID0014 under proposal P-1 (diagnostic only) | **VIOLATED ×4**, `incorrect_factor_stated`, wrong by 10³ |
| UID0014 gate | candidate 0.0833 vs incumbent 0.1667 → **reject**; instruction present in `candidate_skill.md`, absent from every retained skill (887/940 bytes) |
| Reports delivered to the optimizer carrying a Lean certificate | **0 of 192** (96 arm B + 96 arm C) |
| Lean, standalone demonstration | return code **0**, 15 checks, 7 evidence examples |

**Deviations and defects.**

1. **A counting defect in this packet's own tool, found and fixed.** `actor_written` joined the
   conversation's entries, so the harness's separators between empty turns were counted as actor text
   (A1/UID0017: 47 instead of 36). Fixed in `855612c`; the corrected measurement reproduces the recovery
   packet's published 20–36 / 575–8,864 ranges exactly. A regression with a several-message fixture pins
   the definition. Cost: the second pair of jobs, 0.45 requested job-hours, both counted.
2. **The exact optimizer request is unavailable** for every arm and epoch: no `optimizer_calls.jsonl`, no
   saved analyst prompt, no minibatch composition. The analyst views in `status_20261118/` are a
   RECONSTRUCTION from SkillOpt's own `fmt_minibatch_trajectories`, and only the block contributed by the
   named item is certainly what the optimizer saw.
3. **The two item-identity views disagree for UID0014.** `trajectory_digest.json` names it under a stall
   pattern; `minibatch_fail_001.json` names a thousands-vs-billions slip without naming any item. The
   slip is attributable to UID0014 because UID0014 is the only item among the 24 in that rollout whose
   actor-written text mentions both scales — established by scanning all 24, not inferred from wording.

**What was NOT done.** No checker semantics changed (P-1 and P-2 measured only). No historical artifact
written. No sealed score opened. No validation or held-out trajectory used for development. Nothing
launched.

## OQA-COL-1 — the corrected-client training collection, 2026-11-18

**Purpose.** Collect the corrected-client training failures the status packet identified as missing, then
use them to decide the next representation change. Report: `docs/research/OFFICEQA_COLLECT_PACKET.md`.
Selection rule recorded in advance: `docs/research/OFFICEQA_COLLECT_SELECTION_RULE.md`. Artifacts:
`<campaign>/collect_20261118/`.

**Authorization (Grant 7).** 1.5 requested CPU job-hours; 800 actor attempts and $0.75 whichever first;
zero GPUs; zero optimizer calls. **Used: 286 attempts, $0.180329 ledger / $0.104993 provider, 0
optimizer calls, 1.418 job-hours over 5 jobs, 0 GPUs.** Zero refusals, zero non-ok calls in 285, ledger
verdict PASS.

**Frozen before the first request.** `COLLECT_FREEZE.json` digest `2083b4587253`: 24 training-only ids
(membership checked, 0 problems), S0 `9d377d1c2906` unchanged, config `9ff823b2043a`, four code digests,
`qwen3-5-4b` / temp 0.7 / `max_tokens` 8000 / tools on / `extra_body {"enable_thinking": false}`, tools
`glob,read,grep` schema `7e4fe0e714eb`, limits unchanged, settings fingerprint `d58cf3b59bbc2928`, policy
`committed_answer`, and the permitted/audit-only/evaluator-only boundary. Prototype semantics frozen as
`CC_BUNDLE_v5` `162b580923e2e0b9` in the same job.

| job | phase | requested | elapsed | commit | outcome |
|---|---|---|---|---|---|
| 66407516 | freeze | 0:10 | 0:05 | `6a3d01b` | freeze + bundle v5 written |
| 66407607 | collect | 0:30 | 0:30 | `6a3d01b` | TIMEOUT at 16 of 24 |
| 66410230 | collect | 0:25 | 0:15:49 | `455e267` | resumed 16, collected 8, 24 of 24, verify all_ok |
| 66412182 | analyze | 0:10 | 0:08 | `455e267` | prototype, bind audit, provenance, inventory |
| 66412183 | tests | 0:10 | 0:56 | `455e267` | 2,390 passed, 9 skipped, 368 subtests |

**Results — recorded.**

| | |
|---|---|
| Episodes | 24 of 24 complete, 0 missing |
| Correct / failures / answers emitted | **12 / 12 / 16** |
| Failure kinds | 4 `answer_emitted` but wrong; 4 `completion_truncated_at_cap`; 4 `tool_turn_budget_exhausted` |
| Failures with written arithmetic | **11 of 12** |
| Failures with a monetary operand | 8 of 12 |
| **Failures with a holding binding** | **0** |
| **Records with a holding binding** | **0 of 24**, controls included |
| Settings verification | **9 of 9 checks pass** |
| Thinking control | **effective**, not just transmitted: 0 alternate-field chars in all 285 calls |
| Recoveries | **0** (190 `ordinary_content_present`, 95 `tool_call_present_no_recovery`) |
| Prototype | 113 monetary operands, 50 predictions, **all resolutions UNKNOWN** |
| Bind audit | 2,087 candidates, 566 monetary, **166 bindings all UNKNOWN** |
| **Primary causes** | **`header_observed_structure_lost` 166**, `operand_is_in_no_cell` 290, `several_plausible_bindings` 110 |
| Same-message postcondition checking (diagnostic column) | **UNKNOWN 166 → UNKNOWN 166, no change** |
| Actor-written trace size | 158 (UID0031) to 30,436 (UID0150) characters |

**Three findings that changed the conclusion.**

1. **The blocking obstacle is split-header column binding, not either pending repair.** 166 of 166
   bindings die on `header_observed_structure_lost`. Same-message checking moves 0; supported
   table-unit scope inference cannot apply because the scope is observed and unparseable rather than
   unobserved. The packet's vocabulary proposal is therefore explicitly **gated** behind an extraction
   fix, and says that adding it today would produce more UNKNOWNs, not more findings.
2. **The historical 1,000× unit error did not recur.** Fresh UID0014 attempt: "convert thousands to
   billions by dividing by 1,000,000", `2,331,274 / 1,000,000 = 2.331274 billion` — correct. It failed by
   truncating at the completion cap. The prototype's record of it notes `used 10^-6`.
3. **A correct episode carries a superseded wrong calculation.** UID0026 (`hard = 1`) holds both
   `30,442 - 29,726 = 716` and `31,336 - 30,442 = 894` in one message, with `894` committed. The frozen
   vocabulary cannot distinguish them, so the extension's primary justification is avoiding false alarms
   on correct episodes.

**Provenance reconciliation — two earlier claims of mine withdrawn.** 13 optimizer call logs exist with
the complete prompt text of 102 calls; `oqa_campaign_body.sh` writes them to the job's output directory
inside the deployment bundle, not the campaign run tree. Minibatch composition is DERIVED from the
recorded prompt. UID0014: job 66323202, arm C epoch 3, analyst call 1, 388,789 chars, digest
`2328fd810d37`, delivered report **verbatim**, matched to `minibatch_fail_001.json`. Arm A: **0 report
headings in every call at every epoch**, and at job 66273856 call 1 it returned the same units diagnosis
unaided. "The gate, not the contract layer, is where it died" withdrawn as OVERSTATED; no per-instruction
attribution of a gate score exists. The launch doc's verbatim claim is true of the **pre-launch dry run**,
whose records hold only stage, char count and prompt digest.

**Deviations and defects.**

1. The first collect job hit its 0:30 wall at 16 of 24. `results.jsonl` is written incrementally, so the
   resume reused those 16 and never recollected. Both jobs' requested time is counted.
2. The freeze's `resolved_documents` is empty for the 9 multi-document questions: this split separates
   source files with a newline and the parser split on commas; `task_type` was empty because the column
   is `category`. **The collection is unaffected** — the dataloader reads the CSV. The manifest was NOT
   re-written (that would change the digest the collect job logged); the parser is fixed with three
   regressions.
3. My `diagnostic_opportunity` field emits both `incorrect_answer` and `no_answer_emitted` for an episode
   that scored 0 without answering — the same fact twice. The termination cause is the reliable
   classifier.
4. The exchange rate `114.0` (UID0019) and the line number `2255` are classified MONETARY — two
   extractor mis-classifications visible in the new records.
5. CPU accounting mixes core-hours (campaign) with requested walltime (grants since). Uniform figures
   over 122 jobs: requested 79.65 h, elapsed 19.90 h, core-hours 43.92 h, ceiling 44. Reported mixed
   figure 41.69. **Raised for the user's decision, not resolved.**

**What was NOT done.** No skill update, no optimizer call, no learning run, no performance campaign, no
checker semantics changed, neither pending repair deployed, no rescoring of the frozen prototype under
changed semantics, no validation or held-out record used or re-run, no sealed score opened, and the new
vocabulary not implemented.

## OQA-BIND-1 — the source-binding investigation, 2026-11-19

**Purpose.** Can the permitted observations support reliable source-column bindings, and does repairing
extraction let the frozen contract produce useful, correct findings? Report:
`docs/research/OFFICEQA_BIND_PACKET.md`. Amendment B-4 recorded before any code change. Artifacts:
`<campaign>/collect_20261118/v2_bind/`.

**Authorization (Grant 8).** A separate 0.5 requested CPU job-hours including verification and failed
jobs; zero paid API/provider probes, zero actor/optimizer/learner calls, zero GPUs, zero new collection.
**Used 0.500 requested job-hours (2 jobs), elapsed 0.036 h, core-hours 0.072 h, $0.00.**

| job | requested | elapsed | commit | outcome |
|---|---|---|---|---|
| 66414726 | 0:20 | 1:08 | `13ca17c` | 2,404 tests; bundle v6 `36c37a397eddaa65`; replay identical to baseline |
| 66414832 | 0:10 | 1:00 | `f80ec8d` | 2,406 tests; leaf carried into the witnesses; **exit 1 = the v6 bundle guard refusing to redefine a label** |

**Measurements — deterministic, over all 24 saved records.**

| | |
|---|---|
| data cells in the permitted prefixes | 26,437 |
| cells already carrying a bound header | **4,117 (15.6%)** |
| split/single tables / flat tables | 306 / 65 |
| pairs `pair_tables` already finds | **9** |
| width-equal split/flat combinations | 227; full leaf agreement **2**, partial 1, refuted 224 |
| bound-header coverage if the span reading were admitted | **76.8%** — refused |
| span reading vs the one observable flat header | **9 of 10** indices agree; fails at the tenth |
| leaf vs the same flat header | **10 of 10** — but on ONE table |

**Replay, baseline against repaired (`BIND_COMPARE.json`).** Predictions 113 → 113; monetary operands
566 → 566; bindings 166 → 166; **supported bindings 0 → 0; findings 0 → 0**; primary causes, match
statuses and resolution states identical; same-message postcondition column UNKNOWN 166 → UNKNOWN 166.
Leaf-bearing operands 0 → **43**; predictions whose cell carries a leaf 0 → **12**. Classification: 4
records recovered a leaf with nothing else changed, 20 identical with no leaf.

**The decisive negative result (packet §6a).** The recorded leaves are **correct on UID0019** (`528,979
→ Purchased (1)`, `533,325 → Sold (2)`) and **wrong or spurious on UID0026, UID0163 and UID0014**. Two
different leaves at the same column index in UID0026 prove two renderings with different offsets. So the
10-of-10 agreement did not generalise and the answer to the headline question is **no**.

**Corrections to prior diagnoses (packet §4).** UID0019 column selection, not a unit error — the right
columns give `1169.41` exactly. UID0028 an intended two-hop question. UID0044 a single cited value with
the Canada column read instead of Latin America in 6 of 8 reads — the right column gives `1,461` exactly.
UID0026 a corrected/replaced attempt. Three CONTRADICTED, one PARTLY CONTRADICTED.

**CPU reconciliation (`OFFICEQA_CPU_LEDGER.json`, 0 problems).** Inside the 44-hour ceiling: requested
**66.483**, elapsed **17.833**, core-hours **35.649**, over 105 started jobs with 5 never-started
excluded. Campaign phase alone: 53.583 requested against a 40-hour authorization; 27.684 core-hours
against the recorded 27.61. Grants 3, 5, 6, 7 agree exactly; Grant 4's recorded 6.88 included 1.17 h of
never-started jobs that the stated rule excludes. 41.69 rebuilds to 40.585 (mixed units); 79.65 and 43.92
were over-counts including the pre-campaign phase. **Convention unresolved, deliberately.**

**Verification.** 16 new focused checks: supported alignment, bare-ordinal ambiguity, conflicting and
repeated headers, duplicate values, previously working flat headers, preservation of unknowns and source
pointers, the frozen-module boundary (`oqa_lc_cells` contains neither new identifier), that a leaf cannot
satisfy `_well_bound`, that `index_alignment` is still not in force, that both fields reach both witness
projections, and that the identifier appears in no condition or verdict. **Lean not compiled and not
claimed:** B-4 changes no encoded decision rule, so there is no changed fact to verify; no Python mirror
was substituted.

**Deviations.** (1) Job 66414832 exited 1 on the semantic-bundle guard — correct behaviour; the directory
holds a v6 bundle from run 1 beside run-2 analysis artifacts, and the honest label for the run-2 state is
v7, which needs an allocation this task exhausted. (2) The first replay measured
`operands_whose_cell_has_a_leaf: 0` because two witness projections dropped the new fields; fixed and
pinned by two tests. (3) The leaf's reliability was asserted from n=1 in the amendment and refuted by the
replay; the amendment's own prediction of zero effect on bindings was correct.

**What was NOT done.** No vocabulary expansion, no same-message postcondition change, no table-unit
scope-rule change, no learned-model change, no skill edit, no learner call, no validation or held-out
evaluation, no performance campaign. The baseline was digest-verified unchanged in both jobs.

## OQA-BASE-1 — two-baseline OfficeQA campaign, 2026-11-21

**Question.** How do Original SkillOpt and Trace2Skill compare on OfficeQA, measured with the corrected
actor client against the unchanged starting skill S0?

**Setup, frozen before submission.** Rule OQA-SEL-4, `OFFICEQA_FROZEN_SETS_V4.json`, digest
`b0dbbd4be97fc245`: train 12, val 12 (reused for every selection gate), held-out 30 at two attempts per
endpoint. Disjoint; strata from released metadata; 6 of 30 held-out UNTESTED for availability; one
document shared between train and held-out.

Config `configs/research/officeqa_base2.yaml` (`_base_: officeqa_api.yaml`). Resolved and asserted:
num_epochs 4, batch_size 40 (one step per epoch over 12), use_slow_update true,
slow_update_gate_with_selection true, use_meta_skill true, slow_update_samples **6**,
longitudinal_pair_policy mixed, use_gate true, sel_env_num 0, eval_test false, analyst_workers 2.

Effective outbound settings, read from the live backend config after `configure_openai_compatible` and
cross-checked against 285 transmitted requests: `qwen3-5-4b` on `https://api.empiriolabs.ai/v1`,
temperature 0.7, max_tokens **8,000**, `extra_body {"enable_thinking": false}` (effective — 0
alternate-reasoning characters), narrow `committed_answer` recovery, 24 tool turns, 4 queries per turn,
offline local tools, 4 workers. Starting skill S0 887 bytes `9d377d1c2906`. Tool schemas `7e4fe0e714eb`.

**Arms.**
* *Original SkillOpt framework baseline* — `scripts/train.py` with `OFFICEQA_STAGE_REPORT_DIR` unset,
  so the analyst prompt is byte-identical to upstream's. Epoch 1 consumes the injected s0 pass-1
  rollout and validation baseline.
* *Trace2Skill* — upstream pinned at `3d0b52a140f002a512930252b613c49048f7d5ac`; analysis per
  trajectory, MAP per record, hierarchical REDUCE, apply, verify, over 48 fixed-S0 trajectories. No
  actor key is loaded in that stage, which proves it rolls out nothing.

**Jobs.** 66552338 prep FAILED (free; its own preflight defect). 66552420 prep COMPLETED — 2,428 tests
passed, split id-matched, ledger self-test held on NFS, preflight READY 39 pass / 0 warn / 0 fail.
66552421 s0 RUNNING, gated first-3 checkpoint passed. 66552422-425 Original SkillOpt epochs 1-4.
66552508 Trace2Skill. Bundles `oqa_base_20261121b` (a8f80fa) and `oqa_base_20261121c` (3765139).

**Allowance.** Separate phase ledger starting at zero in
`runs/20261121-officeqa-two-baselines/ledger`. 50 requested CPU job-hours, 0 GPUs, 20,000 actor
attempts and $15, 400 DeepSeek calls and $20. Reserved 10.58 CPU hours; projection 396 episodes, 5,544
attempts, $3.49, 117 optimizer calls.

**Status.** Running. Held-out scores sealed pending contract-arm development.

### OQA-BASE-1, Original SkillOpt — gate decisions through epoch 3 (epoch 4 still running)

**S0's own scores, measured by the s0 stage (job 66552421):**

| | score |
|---|---|
| S0 on the 12 **validation** questions (the gate's baseline) | **4/12** |
| S0 on the 12 **training** questions, four independent passes | **5, 4, 4, 5 = 18/48** |

**Arm A's gate record** (`original_skillopt/summary.json`, `history.json`, `slow_update/*/slow_result.json`):

| epoch | ordinary step | gated slow update |
|---|---|---|
| 1 | **reject** | `inject_placeholder` — by design, no optimizer call at epoch 1 |
| 2 | **reject** | **`accept_new_best`** |
| 3 | **reject** | **reject** |
| 4 | running | running |

`total_accepts: 0, total_rejects: 3` for ordinary steps. `best_step: 2`,
`best_origin: slow_update_epoch_02`, `best_selection_hard: 0.4167`.

**Skill lineage, by digest:** S0 `9d377d1c2906` (887 B) → v1 `5bd17ea109bc` (940 B) → v2
`e35673d87cb7` (4,518 B); **v3 is byte-identical to v2**, so epoch 3 changed nothing. `best_skill.md`
is v2.

**The result so far, stated carefully.** Every *ordinary* bounded-edit step was rejected by the
validation gate. The only change that survived came from the **gated slow update at epoch 2**, and it
moved validation from **4/12 to 5/12 — one question**. Against that, S0's own spread across four
independent passes of the training set is 4 to 5 of 12, i.e. **±1 question from sampling alone**. So the
accepted improvement is inside the band that the actor's own sampling produces, and the acceptance rests
on a one-question margin. This repeats the pattern recorded for campaign AC-1 ("1-task gate margins")
and is a property of the setup's size, not a defect in the framework: 12 validation questions cannot
resolve differences finer than 1/12.

Upstream's strict-improvement rule was preserved, so this is the framework's own behaviour and not an
artefact of a weakened gate. The held-out measurement will be the test of whether that one-question
validation gain transfers.

---

## OQA-LN-1 — the learned source-to-calculation contract (2026-11-21)

**Authorization.** 8 requested CPU job-hours, 24 DeepSeek attempts AND $3, 0 actor calls, 0 GPUs, as
sublimits inside the phase ceilings of 50 h / 400 calls / $20. **Consumed: CPU 4.50 requested job-hours
(0.23 elapsed) over 14 jobs; DeepSeek 6 attempts / $0.029476; actor 0; GPUs 0.**

**Deployment.** Bundles `oqa_ln_20261121` … `j`; the final verification ran from `j`, commit
`abe2984`. Body `research/cluster/oqa_ln_body.sh`, phases `ledger|freeze|learn|restage|lean|rehearse`.
Output root `runs/20261121-officeqa-learner/{v1_freeze,v2_learn,v2b_restage,v3_lean,v4_rehearse}`,
pulled to `~/dev/lean_skillopt/runs/20261121-officeqa-learner/` (26 files).

**Evidence set, frozen before any learner call.** The 48 fixed-S0 trajectories of job 66552421 over
the twelve frozen OQA-SEL-4 TRAINING questions. 676 binary calculations, 1,352 operands, 82 chained
statements out of scope. Task-separated 6/6 by `sha256("OQA-LN-1|"+uid)`: learn UID0028/0026/0079/
0014/0034/0033 (1,214 operands, 28 sourced), check UID0085/0031/0063/0065/0017/0030 (138 operands, 26
sourced). Three recovery-shaped attempts found by trajectory SHAPE, not by score: p2/UID0026 (learn),
p4/UID0063 and p4/UID0085 (check, therefore prospective). Labelled REUSED DEVELOPMENT RECORDS.
Not read: `ground_truth`, `soft`, the provider's alternate reasoning field, `hard`.

**Vocabulary.** Amendment D-1, bundle label v10, digest `464b1cae6c65aa36`, in its own namespace so
`oqa.cc.model.v1` is byte-identical in behaviour. Two entities added (`operand`, `answer`), thirteen
predicates, one obligation (`source_traced`). Exactly one learnable conclusion: `operand_sourced`.
Model bank declared empty at its initial state.

**Learner result (job 66581071, 4 paid calls).** Both repetitions proposed
`operand_positions(operand,1) AND operand_source_tool(operand,grep) ⇒ operand_sourced`, from the
identical prompt digest `47064dac` with different replies (812 and 783 chars). Models `1231f2817c476aae`
and `d81e2383f0a502f2`.

| set | operands | applies | confirmations | counterexamples | silent | precision |
|---|---|---|---|---|---|---|
| learning (6 questions) | 1,214 | 28 | 27 | 1 | 1,186 | 0.964 |
| checking (6 unseen) | 138 | 26 | 26 | **0** | 112 | **1.000** |

Predictions were written and digested before any verdict was read (content digests, clock excluded).
The one counterexample (`14` in `14 * 1935.5 = 27,097`, p1/UID0014) is in-sample. **Both repetitions
declined to repair**, correctly: its feature vector is identical to four operands the resolver called
sourced. No condition was invented.

**Stage obligations over 25 checked attempts (after the restage correction).**

| obligation | DISCHARGED | OUTSTANDING | VIOLATED | UNKNOWN |
|---|---|---|---|---|
| lookup_observed | 16 | — | — | 9 |
| answer_traced | 9 | — | 7 | 9 |
| arith_recorded | 9 | — | — | 16 |
| source_traced | 3 | 6 | — | 16 |

**Lean.** 4.20.0, source `a1cc5340807f652b` (17,777 bytes), **rc 0, 0 errors, 0 warnings, 86 examples**
(36 operand + 50 obligation), job 66588304. The FIRST compile (66587986) returned rc 1 and refuted the
Python side; following it withdrew two false accusations.

**Paired rehearsal (job 66588323, 2 paid calls).** p1/UID0063, skill S0 `9d377d1c2906981b`, edit budget
4, through `run_error_analyst_minibatch`. Trajectory text 16,179 vs 19,338 chars — the difference is the
3,129-byte report plus its 30-char heading exactly. Report `afd740186c0395e0` ← model `d81e2383f0a502f2`
← Lean `a1cc5340807f652b`. Two edits each. **Neither applied; no validation; held-out sealed.**

**Verification.** Job 66588746 against the finished code: suite **2,557 passed / 0 failed**, 9 skipped,
368 subtests; ledger all five checks PASS, 12/12 races at exactly 23, over-grant interleaving refused.

---

## OQA-D2 — 2026-11-22. Amendments D-2..D-5 replayed over the frozen learned-contract evidence

**Authorization.** Offline analysis, bounded internal corrections, regression checks and Lean
compilation within the verified remainder of the eight-hour follow-up CPU allowance. **Zero new
provider, learner, optimizer or actor requests. Zero GPUs.** The 18 remaining teacher attempts were
not authorized and were not spent.

**Design.** The same 48 S0 training trajectories of job 66552421, the same 6/6 learn/check question
partition, the same two frozen learned models (r1 `1231f2817c476aae`, r2 `d81e2383f0a502f2`), the
same contract `operand.sourced@v1`, the same vocabulary bundle v10 `464b1cae6c65aa36`. **Nothing was
retrained.** All 48 trace digests re-verified against the freeze before any replay.

**Freeze witness, reconciled.** The digest `ba3ab77882193c84` that every downstream artifact cites is
clock-dependent: the content-digest fix landed at commit 857ede6 (21:17 EST 2026-11-21) and the
artifact was written by commit 4580459 at 00:32:48Z. The reproducible content digest of the identical
records is `f9c774bc6d3da46f`, now recorded beside it in `v5_d2/D2_FREEZE.json`.

### D-2, the extraction/label correction

| field class | occurrence-producing matches |
|---|---|
| a numeric cell (kept) | 2,002 |
| label text (dropped) | 1,464 |
| several numerals, not a value plus footnotes (dropped) | 48 |
| a second numeral inside a kept cell (dropped) | 34 |
| **before / after** | **3,548 / 2,002, −43.6%** |

Operand verdicts changed: **11 of 1,352**, all on UID0014. Supported bindings **54 → 49**: exactly
the five measured label matches, with **zero gained**. Parse unchanged — identical operand counts,
statements and arithmetic verdicts, asserted per role by a test.

Both frozen models rescored, no retraining: learning records 28 → 23 applications, 27 → 23
confirmations, **1 → 0 counterexamples**, precision 0.9643 → 1.0000, questions applied
{UID0014, UID0079} → {UID0079}. Checking records unchanged at 26/26. **This is an extraction
correction, not learner repair:** the counterexample's operand (`14` matching `Unnamed: 14_level_2`)
lost its only position, so the rule became silent on it.

### D-3/D-4/D-5, and ten withdrawn accusations

38 of 676 statements are fragments of a larger expression and account for **38 of the 50**
arithmetically inconsistent ones. Eight of 48 attempts commit an answer that is not one number. All
four UID0065 attempts write their arithmetic with absolute-value bars.

| attempt | obligation | was → now | cause |
|---|---|---|---|
| p1/p3/p4 UID0017 | answer_traced | VIOLATED → UNKNOWN | D-5a, a two-element list answer |
| p1..p4 UID0065 | answer_traced | VIOLATED → UNKNOWN | D-5b, `\|2.0 − 0.2\| = 1.8` |
| p1/p2/p3 UID0063 | source_traced | OUTSTANDING → UNKNOWN | D-3, ambiguous not absent |
| p4/UID0063 | source_traced | **DISCHARGED → UNKNOWN** | D-3, an identity restatement |
| p4/UID0030 | lookup_observed | DISCHARGED → UNKNOWN | D-2, its observations were labels |

**12 obligations move; 10 accusations withdrawn by a change of state, 0 added.** Pooled over the 25
attempts, from the reports' own `diagnosis_counts`:

| code and extraction | confirmations | execution diagnoses | unresolved provenance | unknowns |
|---|---|---|---|---|
| superseded (`v2b_restage`) | 37 | **13** | — | 50 |
| D-3/D-4/D-5, superseded extraction | 36 | **0** | 7 | 57 |
| D-3/D-4/D-5, D-2 extraction | 35 | **0** | 7 | 58 |

The thirteen are 7 `answer_traced` VIOLATED and 6 `source_traced` OUTSTANDING. Ten change state; the
other three stay OUTSTANDING and stop being counted as an actor error.

### The 2×2: does the learned rule add anything?

Conditions: extraction (superseded | D-2) × learned rule (applied | absent). Rule-absent returns
"not evaluable", never false, so nothing the observations establish is removed. **All four cells give
identical obligation states, identical operand classes and identical diagnosis counts**, for both
models. Attempts where removing the rule changed any state: **0 of 25**. 50 of 50 attempt-level
effects classify ALREADY_SUPPLIED. The only textual difference is the block naming the rule. The
rehearsal's unresolved-operand warning is byte-identical with the rule removed.

**Verdict: NO DEMONSTRATED LEARNED INCREMENT.**

### Ancestry and recovery

| attempt | abandoned | consumed ancestors | same result | replacement demonstrated |
|---|---|---|---|---|
| p2/UID0026 (learn) | **16** | 0 | 17 | yes |
| p4/UID0063 (check) | 0 | 2 | 0 | **no** |
| p4/UID0085 (check) | 0 | 2 | 4 | **no** |
| p1/UID0063 (rehearsal item) | 0 | 0 | 1 | **no** |

p4/UID0063's `source_traced DISCHARGED` rested on `86,000 - 0 = 86,000`. p4/UID0085's two ancestors
are D-4 fragments whose arithmetic is out of scope, and both of its supporting operands have two
candidate producers each — an ambiguity created in part by the `max(0.5, …)` tolerance, which is
flagged and not changed.

### Lean, and the suite

Job 66599377 (bundle `oqa_ln_20261122c`, commit `90ef7cf`, phase `d2`, free, every API key dropped
before the phase began), COMPLETED in **00:04:03 of 0:25 requested on one core**.

| | |
|---|---|
| compiler | Lean (version 4.20.0, x86_64-unknown-linux-gnu, commit 77cfc4d1a4f6, Release) |
| source | **`ec5e8ee905465b76`**, 39,984 bytes |
| return code | **0**, 0 errors, 0 warnings, all statements accepted |
| examples | **272** = 72 operand (36 classifications + 36 settled-ness) + 200 obligation; 0 skipped |
| suite | **2,615 passed, 0 failed**, 9 skipped, 368 subtests |

New definitions encode the corrections: `sourceTraced` gains `nAmbiguous` and can never be VIOLATED;
`answerTraced` gains `needsArithmetic` and `nUnrepresented`; `arithRecorded` gains `fragment`;
`classify` gains `restatementOnly` and `severalPossibleOrigins`; `replacementDemonstrated` requires
an abandoned alternative. Unlike the 2026-11-21 compile, this one agreed at the first attempt — a
weaker cross-check than a disagreement, and it is recorded as such.

**The cluster reproduced the whole replay from the FROZEN cluster records**, not from the local
mirror, and agrees on every headline figure: 3,548 → 2,002 occurrence matches, 54 → 49 supported
bindings, 11 changed bindings, 0 paid calls.

### Resources

CPU **0.42 of the 3.50 remaining requested job-hours** (one 0:25 job on one core; two earlier
submissions, 66598457 and 66599047, were cancelled while PENDING and were never allocated, so they
cost nothing). **Zero provider, learner, optimizer and actor requests. Zero GPUs.** The 18 remaining
teacher attempts were not authorized and were not spent. Remaining after this task: **3.08**
requested CPU job-hours.

**Artifacts.** `runs/20261121-officeqa-learner/v5_d2/` — `D2_FREEZE.json`, `D2_CELLS.json`,
`D2_RULE_r1.json`, `D2_RULE_r2.json`, `D2_ANCESTRY.json`, `D2_STAGE_MOVES.json`,
`D2_OPTIMIZER_EVIDENCE.json`, `r1/r2_stage_reports.json`, `reports_regenerated/`, `D2_RUN.json`;
and `v5_lean/` for the compile. The superseded `v2_learn`, `v2b_restage`, `v3_lean` and `v4_rehearse`
artifacts are preserved unmodified.

---

## PA-1 — implementation-parity audit, 2026-11-22 01:40-02:05 EST (offline, zero jobs)

**Scope.** OfficeQA `officeqa-contracts` @ `39a7e33` against the accepted design v0.1 + A1-A5 and the ALFWorld
reference worktree `contract/learned-contract` @ `4a9914a` (all 70 contract files untracked; digests recorded in
the audit §2.2). Read-only on the ALFWorld side; nothing there was written or executed.

**Measurements.**

| probe | artifact | result |
|---|---|---|
| nine `transferred/` reference copies vs live ALFWorld files | audit §1.1 | all **byte-identical**; reference unmoved since 2026-11-16 |
| call path of the learned rule over all 48 frozen attempts, rule applied vs absent | `PA1_CALLPATH.json` `55a71d3140fe9d99` | 18 `applies` calls, 36 `classify_operand` calls, **0** of which could see the contract; 48/48 obligation states and operand classes identical; 4 annotation fields differ |
| is the target entailed by its antecedent through supplied code | `PA2_TARGET.json` `c471e078923342ab` | grep-sourced unique positions: **39/39** fired `QUERY`, **39/39** `SUPPORTED`; read-sourced: 6/19; P(SUPPORTED \| QUERY) = **1.000** |
| base rates for two candidate targets, 536 tool steps | `PA3_TARGET_BASIS.json` `c8450584d0f4e323` | grep 261 (99 empty, 45 header, **0 scale**); read 192 (0 empty, 50 header, 36 scale, 21 both); glob 76 (61 empty) |
| `scale_binds` yield, re-read from the saved CC check | `CC_CHECK_descriptive.json` | 113 operands, 50 bindings, **50 UNKNOWN / 0 HOLDS / 0 VIOLATED**; 814 of 814 data cells with no bound header |
| LC campaign model drift | arm B / arm C epochs 2-4 | arm C identical at all three epochs (`epoch: 1`); arm B retired one contract at epoch 3 then froze |

**Code.** `research/officeqa/oqa_pa_parity.py` (new, additive, read-only on existing artifacts),
`tests/test_officeqa_pa.py` (7 passed; with `test_officeqa_d2.py`, **63 passed / 2 skipped** locally). Amendment
**D-6** records the module. No existing behaviour, vocabulary, extraction rule, Lean definition or report text
changed; no Lean recompile was needed.

**Resources.** 0.00 of the 2.00 authorized CPU job-hours (no job submitted); 0 provider / learner / optimizer /
actor calls; 0 GPUs; held-out sealed; 18 teacher attempts untouched. OfficeQA CPU remainder unchanged at **3.08**.

**Conclusion.** The generic machinery is substantially equivalent and the OfficeQA LN configuration is
redundant because of its **target**, not because of a porting bug. The one target-independent code gap is the
append-only repair interface. Full report: `docs/research/OFFICEQA_PARITY_AUDIT.md`.
