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
