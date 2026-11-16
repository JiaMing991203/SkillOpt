# OfficeQA feasibility study: setup, baseline check, and development pilot

Recorded: 2026-11-15, 00:43 EST (from `date`), America/New_York
Worktree: `SkillOpt-officeqa`, branch `officeqa-contracts`, base commit `4a9914a`
Status: **local preparation complete; nothing has run on the cluster and nothing is authorized.**
Design reference: `SkillOpt-learned-contract/docs/LEARNED_CONTRACT_DESIGN.md` v0.1 (sha256 `b115079e…64dc602`)
and its amendment A3 sections 3.2–3.6. Transferred files and their provenance:
`OFFICEQA_TRANSFER_MANIFEST.json`.

Labels used below, as in `CLAUDE.md` section 6: **Verified** (checked in code, on the cluster, or by a test the
reader can repeat), **Interpretation**, **Proposal**, **Unknown**.

---

## 1. Why OfficeQA, and what this study is for

The research question is whether independently specified execution contracts give more useful failure localization
than ordinary reflection. The ALFWorld work ran into a measurement problem that is recorded in the earlier studies:
the actor rarely fails in a way the contracts can anticipate, so predictive coverage was near zero, and the
connected (stage) reports added about one new diagnosis over ten traces.

OfficeQA is attractive because its failures are **typed and dense**. An answer is a number with a period, a unit
and an arithmetic provenance, and all four can be wrong independently. That gives a contract something concrete to
check that does not depend on guessing the environment's hidden mechanics.

**Interpretation, not yet evidence:** this is the reason to try OfficeQA, not a result. Whether its failures really
are denser and more checkable is exactly what the three-question check and the twelve-question pilot are for.

---

## 2. The released OfficeQA setup, as verified in the code

All paths are in this repository at commit `4a9914a`; the adapter is upstream and unmodified.

| Component | Location | Note |
|---|---|---|
| Adapter | `skillopt/envs/officeqa/adapter.py` | registered as `officeqa` in `scripts/train.py` and `scripts/eval_only.py` |
| Rollout | `skillopt/envs/officeqa/rollout.py` | four modes: `offline` (local tools), `custom_search`, `azure_search`, and offline-without-tools |
| Document tools | `skillopt/envs/officeqa/tool_runtime.py` | `glob`, `read`, `grep`, plus the supplied-page builder |
| Native scorer | `skillopt/envs/officeqa/evaluator.py` | `hard` = exact match after normalization, `soft` = token F1 |
| Starting skill | `skillopt/envs/officeqa/skills/initial.md` | 15 lines: retrieval, evidence and final-answer discipline |
| Analyst prompts | `skillopt/envs/officeqa/prompts/analyst_{error,success}.md` | failure types: `retrieval_miss`, `evidence_miss`, `operand_error`, `calculation_error`, `answer_format`, `other` |
| Config | `configs/officeqa/default.yaml` | `search_mode: offline`, `max_tool_turns: 24`, `max_completion_tokens: 16384`, `workers: 4` |
| Split manifest | `data/officeqa_id_split/` | OfficeQA Full: train 50 / val 24 / test 172 |

### 2a. What the actor receives (Verified, `rollout._build_user` and `process_one`)

In `offline` mode the first user message contains, in this order:

1. `## Question` — the question text.
2. `## Oracle Parsed Pages` — **the parsed text of the gold source page(s)**, built by
   `tool_runtime.build_oracle_parsed_pages_context` from the item's `source_files` and the `page=` number in each
   `source_docs` URL. Capped at 24,000 characters per page and 80,000 in total.
3. `## Candidate Files` — the files resolved from the item's `source_files`, i.e. the gold documents.
4. `## Source Hints` — the item's `source_docs` URLs, each of which names the month, the year and the page.

The three document tools are then restricted to those same files: `run_tool` is called with
`allowed_files = [basename(p) for p in candidate_files]`, and `_is_allowed` refuses any other path, while `glob`
filters its matches by the same list.

**This is the single most important finding for the study.** In the released offline setup the actor is handed the
gold pages before it acts and cannot read anything else. Retrieval is therefore not the actor's problem, and
"source selection" can only mean *choosing among the pages it was given* — which matters for the multi-source
items, where several periods are supplied at once. Any claim about retrieval or source-selection failures must say
which of these two things it means. The study's stage model reflects this: it reports `select_source` as
**discharged by the harness** rather than crediting or blaming the actor for it.

*Caveat (Verified):* when an item's `source_files` is empty, `resolve_candidate_files` returns every file in the
corpus and `allowed_files` becomes empty, which disables the restriction. Every item in the train split has
`source_files`, so this does not arise here.

### 2b. The answer key and evaluator-only fields (Verified)

The actor never receives the gold answer: the user message has no answer field, and `evaluate()` runs after the
rollout. But the rollout record's `fail_reason` is written as

```
predicted '<the model's answer>' but expected '<the gold answer>'
```

and `skillopt/gradient/reflect.py::fmt_minibatch_trajectories` copies `fail_reason` into the analyst prompt. So
**SkillOpt's own optimizer view contains the answer key for every failed item.** That is upstream behaviour and
this study leaves it alone; it only means the contract input must be built separately and audited. It is:
`research/officeqa/oqa_trace.py::build_packet` reads only `target_user_prompt.txt` and `conversation.json`, and
`audit_no_gold` checks that no evaluator-only field and no `fail_reason` text entered the packet. The audit
deliberately does **not** fail when the gold *value* appears in page text: the documents are what the question is
asked about, so that proves nothing.

### 2c. The native scoring path (Verified)

`evaluate()` → `{"hard": EM, "soft": token F1}` → the rollout record → `skillopt/engine/trainer.py` buckets →
`skillopt/evaluation/gate.py`, which compares on `hard` by default. Nothing in this study changes the scorer, the
gate, the analyst prompts or the skill-update rules.

One property of the normalizer is worth recording because it interacts with the unit contracts
(`evaluator.normalize_answer`): it strips commas and the words `million`, `billion`, `dollars` and `nominal`. So a
missing or extra *unit word* is forgiven, while a wrong *scale* still fails, because the digits differ
(`1.5` ≠ `1500`). A unit contract can therefore be correct and actionable about something the scorer is indifferent
to in wording but strict about in magnitude.

### 2d. The trajectory record (Verified)

Per item: `target_system_prompt.txt`, `target_user_prompt.txt`, `conversation.json`. The conversation holds
`{"type": "message", "content": …}` and `{"type": "tool_call", "cmd": …, "obs": …}` entries. So tool requests and
the evidence each returned are recorded in full, and `_clip_text` in `reflect.py` does not truncate them.

**Unknown / limitation:** the offline tool loop records no turn index and no separate reasoning field, so any chain
of thought outside the message content is not in the record. Intermediate calculations are therefore available
*only when the model writes them in its visible message*. The three-question check measures how often that happens.

---

## 3. Data access: what is available and what blocks a run

### 3a. Hugging Face (Verified 2026-11-15 from the cluster login node)

`databricks/officeqa` is `private: false`, **`gated: "auto"`**, licensed CC-BY-SA-4.0 / Apache-2.0. Metadata and
the file tree are public; the content is not:

```
GET /api/datasets/databricks/officeqa                     -> 200
GET /api/datasets/databricks/officeqa/revision/8ecbf18d…  -> 200   (the revision our manifest pins)
GET /datasets/databricks/officeqa/resolve/main/officeqa_full.csv
    -> 401  "Access to dataset databricks/officeqa is restricted. You must have access to it and be
             authenticated to access it. Please log in."
```

No Hugging Face token exists on the cluster: `~/.cache/huggingface/token` is absent, `HF_TOKEN` and
`HUGGING_FACE_HUB_TOKEN` are unset, and the secrets file holds only `DEEPSEEK_API_KEY`. The Qwen weights are
cached because that model is ungated.

**This is blocker 1.** It needs a person, not a script: accept the dataset conditions on the dataset page with a
Hugging Face account, create a read token, and place it in a `600` file outside every repository.

### 3b. Document availability (Verified, file tree)

| Path in the dataset | Files | Bytes |
|---|---:|---:|
| `officeqa_full.csv` | 1 | 155,772 |
| `officeqa_pro.csv` | 1 | 87,909 |
| `treasury_bulletins_parsed/jsons/` | 700 | 766.4 MB |
| `treasury_bulletins_parsed/transformed/` | 698 | 480.4 MB |
| `treasury_bulletin_pdfs/` | 697 | (not needed) |

The layout matches what the tool runtime expects: `resolve_docs_roots` prefers a `transformed/` subdirectory for
the `.txt` files, and `_locate_parsed_json` looks for `<root>/jsons/<stem>.json` for the supplied pages. Upstream
already anticipates a partial corpus — `~/officeqa-sparse/treasury_bulletins_parsed` is one of its default search
paths.

**Proposal: fetch a sparse corpus.** The 15 questions of this study reference 26 distinct bulletins, so 52 files
(~46 MB) instead of 1.25 GB. For items whose `source_files` are present this is *behaviourally identical* to the
full corpus, because `allowed_files` already restricts every tool to those files (section 2a). It must be recorded
as a deviation before any claim about retrieval breadth.
`research/officeqa/oqa_materialize.py --print-docs` prints the exact list.

### 3c. Questions and answers are not in the released manifest (Verified)

`data/officeqa_id_split/train/items.json` holds `id`, `uid`, `category`, `source_files`, `source_docs`,
`source_split` — and no question and no answer. `data/README.md` says materialization is the user's job and ships
`scripts/materialize_searchqa.py` but no OfficeQA equivalent. `research/officeqa/oqa_materialize.py` is that
equivalent: it joins the manifest on `uid` with a CSV that is already on disk, writes the `train/`, `val/`, `test/`
tree the loader requires (`val/` and `test/` deliberately empty), and records both CSV digests. It never takes a
token and never downloads anything, so the credential stays with whoever fetches the file.

---

## 4. The frozen question selection (rule OQA-SEL-1)

Fixed on 2026-11-15 **before any question or answer was visible**, which is the only way to pre-register a
selection over gated data. Digest `5d1e27cd32e46902bd5954d373878f9327619380bfb17c5952930b5d692d856d`, checked at
materialization time (`oqa_materialize.selection_digest`).

Rule: take the **train** split (development questions only); order by `id`; drop items whose `source_files` and
`source_docs` counts differ, because the supplied-page builder cannot align their references (this drops `UID0019`
alone, 1 of 50); stratify by `category` × (single-source, multi-source).

* **B3, the baseline check (3):** the first item of (easy, single), (hard, single), (hard, multi) —
  `UID0002` (1944-01), `UID0007` (1950-02), `UID0018` (1985-03 … 1988-03, four sources).
* **P12, the development pilot (12):** the next three of each of the four strata, disjoint from B3 —
  `UID0033`, `UID0034`, `UID0046` (easy single); `UID0017`, `UID0030`, `UID0031` (hard single);
  `UID0014`, `UID0026`, `UID0079` (easy multi); `UID0028`, `UID0065`, `UID0085` (hard multi).
  Balanced 6 easy / 6 hard and 6 single-source / 6 multi-source, spanning 1942–2019.

B3 and P12 are disjoint so the mechanics check cannot tune the pilot. The validation and test splits are not
touched by anything in this study.

**Why the strata:** multi-source items supply several periods at once, which is where a date or source-selection
error can occur at all (section 2a); the easy/hard split is the benchmark's own label and is the cheapest proxy for
arithmetic difficulty. Nothing about the *failures* was selected: which failures occur is discovered by the run,
and the report comparison runs on whatever those are.

---

## 5. The two runs

### 5a. Three-question baseline check (mechanics)

**Purpose:** show the released adapter runs end to end against the frozen target, and record what a trace contains.
Three questions support no comparison; this is not a measurement.

**What it captures**, per question (`research/officeqa/oqa_inventory.py`):
tool requests with their arguments; the evidence each returned, in characters and lines, with error and
no-match flags; what the harness supplied before the actor acted (pages, candidate files, hints, and their sizes);
the intermediate values the model wrote down, each marked grounded or not against the evidence available at that
step; the arithmetic steps it stated; the native `hard` / `soft` score copied verbatim; the size of the first user
message and of the trajectory, which is what an optimizer prompt would cost; and the information-boundary audit.

**Config:** `configs/research/officeqa_check3.yaml` (→ `officeqa_qwen35.yaml` → upstream
`configs/officeqa/default.yaml`). **Job:** `STAGE=check3 research/submit.sh officeqa`.

### 5b. Twelve-question development pilot (the comparison)

**One rollout, three reports, identical traces.** The actor is never re-run per condition, so no condition can be
credited with a different trajectory. The reports are deterministic and CPU-only; they make no model calls.

The three conditions, following amendment A3 section 3.4 and re-implemented over documents in
`research/officeqa/oqa_check.py`:

* **P — a strong plain progress report.** The obligations read from the question (periods, scale, operation,
  answer kind, each `STATED` with its matched text or `UNKNOWN`); per-step checks of the selected tool call (a path
  outside the candidate list, a `grep` that hit its 20-line cap so no absence claim is supported, a no-match
  event); and whether the final answer is **grounded** in the returned evidence when no arithmetic was requested,
  or **derivable** from some pair of available numbers when it was; plus whether each asked period appears in the
  evidence at all. P is order-free: it never binds an operand to a source, never says which stage is open, and
  never carries an obligation across steps. Following A3 section 3.3 it is assessed at PRE of the selected step,
  with the same opportunity as S — the one-step lag that produced false alarms in the earlier v3 report is not
  reproduced here.
* **S — P plus connected stage feedback.** The ordered procedure `select_source → locate_span →
  extract_operands → compute → format_answer`, with per-operand source bindings and handoff checks: an operand
  that no returned evidence supports (`locate_span` never produced it); an operand whose source period is not an
  asked period; operands that do not cover the periods the question varies over (`unsupported_operands`); and
  `format_answer` reached while `compute` or `extract_operands` is still open.
* **C — S plus action contracts.** Four contracts over the tools with their assumptions stated
  (`oqa_check.CONTRACTS`): `grep` returns only lines containing the pattern; `grep` licenses an absence conclusion
  only when its cap was not reached; a `read` observation is the contiguous window it asked for; and — the one
  empirical hypothesis, flagged as such — a scale stated in a table heading applies to the rows below it
  (assumption `table_scale_applies_to_rows`). That last contract is what lets C type an operand's unit and so check
  a scale obligation that S must leave `UNKNOWN`.

**Scoring, frozen before any real trace is seen** (carried from A3 section 3.6 and `score_v4.py`):

* The unit is the **first correct and appropriately timed warning per problem**, where a problem is a thing gone
  wrong (`answer_grounding`, `operand_period_binding`, `scale`, `stage_handoff`, `tool_path:<file>`, …) — *not* a
  category. Saying something new about a problem the base condition already warns about is `EARLIER` /
  `MORE_SPECIFIC` / `MORE_ACTIONABLE`, never `ADDITIONAL`.
* `NOTE` and `EVENT` are neutral information and are never scored as advice (A3 section 3.2).
* An extended condition must **contain** its base with identical first prefixes; `oqa_check.structural_check`
  raises if it does not, and the pilot run checks it per item.
* Eventual success never makes an earlier warning false, and the absence of a later contradiction never makes one
  useful.

**Failure families to report first**, as asked: source selection, dates, units, unsupported operands. Note what
section 2a means for the first of these: with the gold pages supplied and the tools restricted, a source-selection
failure can only be choosing the wrong one of the supplied pages, which is why the pilot is half multi-source.

---

## 6. Deviations from the released defaults, and why

| Setting | Upstream | Here | Reason |
|---|---|---|---|
| `env.max_tool_turns` | 24 | 8 | the released default assumes a hosted frontier model; 8 turns is enough to see whether the tool loop works, at a quarter of the worst-case GPU time |
| `env.max_completion_tokens` | 16384 | 2048 | the supplied pages already consume much of the context; see below |
| `env.workers` | 4 | 1 (check) / 2 (pilot) | one A40 and a trace-quality goal, not throughput |
| `--max-model-len` | 32768 (ALFWorld's frozen value) | 65536 for OfficeQA jobs | one user message carries up to 80,000 characters of supplied pages (~20k tokens) and every observation is appended to the same conversation |
| `vllm serve` flags | none beyond the ALFWorld set | `--enable-auto-tool-choice --tool-call-parser hermes` | see section 7 |
| Document corpus | full 1.25 GB | sparse, 26 bulletins | section 3b |

`research/cluster/experiment.env` is **unchanged**, so ALFWorld's frozen serving settings stay in git; the two
OfficeQA deviations are set in `research/cluster/officeqa.sbatch` after `load_site` and are recorded in the run
manifest. The one change to shared code is additive and opt-in: `start_vllm` in `research/cluster/lib.sh` now
appends `$LSO_VLLM_EXTRA_ARGS` when it is non-empty, which it is not for any ALFWorld run.

---

## 7. Blocker 2: tool calling is silently disabled on the current server

**Verified from the installed vLLM 0.28.0 source**, not from a run. In
`entrypoints/openai/chat_completion/serving.py` the branch

```python
if (not self.enable_auto_tools or not tool_parser_cls) and (
        not is_named_tool_choice and not is_required_tool_choice):
    message = self._create_chat_message(role=role, reasoning=reasoning, content=content)
```

builds the reply from content alone. `enable_auto_tools` comes from `--enable-auto-tool-choice`, which our
`start_vllm` does not pass, and there is no request-level validation that rejects `tool_choice="auto"` without it.

**Interpretation of the consequence:** the OfficeQA offline loop sends `tools=_TOOL_SCHEMAS, tool_choice="auto"`
and reads `message.tool_calls`. With the current flags that list is always empty, so the loop would record
`"Model neither produced a tool request nor a final answer"` and stop after one turn, with the model's tool syntax
sitting in the message text. The run would "succeed" and produce traces with no tool use at all.

**Fix, and how it is checked:** `STAGE=tools` starts vLLM with `--enable-auto-tool-choice --tool-call-parser
<parser>` and sends one OfficeQA-shaped request through SkillOpt's own backend
(`research/officeqa/oqa_tool_probe.py`). It distinguishes the outcomes — a structured call that executes; tool
syntax left in the content (parser or flags wrong); neither (template or model) — and exits non-zero unless the
call comes back *and* executes, so no question is spent on a broken loop.

**Which parser: corrected by measurement, 2026-11-15.** This section previously asserted that `hermes` was the
right choice "because Qwen3 chat templates emit `<tool_call>{…}</tool_call>`", and that the `qwen3_*` parsers were
only for Qwen3-Coder structural tags. **That was wrong for Qwen3.5-4B, and job 66190733 disproved it.** The model
emits Qwen3-Coder XML, not JSON:

```
<tool_call>
<function=grep>
<parameter=pattern>interest-bearing debt</parameter>
<parameter=path>treasury_bulletin_1977_04.txt</parameter>
</function>
</tool_call>
```

`hermes` expects JSON inside `<tool_call>`, so it extracted nothing, vLLM returned the text as content with an
empty `tool_calls` list, and the OfficeQA loop would have stopped after one turn. Two further facts from that
run's own artifacts: this build offers `hermes`, `qwen3_coder` and `qwen3_xml`, and the latter two resolve to the
**same class** (`vllm/tool_parsers/__init__.py` → `Qwen3EngineToolParser`), so they are one parser under two names
rather than two candidates. The default is now `qwen3_coder`.

**Verified offline before re-running (no GPU):** `research/officeqa/oqa_parser_replay.py` replays that job's exact
reply through `qwen3_coder` in the pinned vLLM environment and recovers `grep` with both original arguments, then
executes it through the adapter's own `run_tool` against a small allowed fixture and checks the returned line.

**Still unknown** after a passing tool check: whether a 4B model uses these tools *competently* across real
questions. The check settles one easy prompt; only the three-question baseline begins to answer the rest.

**Fallback if the probe fails:** `env.use_local_tools: false` runs the adapter's single-shot path, which still
yields the supplied pages, the final answer and the native score — but no tool requests, so it cannot support the
part of the pilot that checks evidence provenance. It is a fallback for scoring mechanics, not for this study.

---

## 8. What has been checked locally

25 Stage A software checks pass (`pytest tests/test_officeqa_contracts.py`): obligation reading including
`UNKNOWN` handling, the evidence ledger's scale and period context, prefix-only availability, error observations
contributing no evidence, the P ⊆ S ⊆ C containment invariant on four constructed traces, `NOTE` never scored as
advice, the expected diagnosis per constructed case, packet extraction from adapter-format records, and the
information-boundary audit (pass, fail and unknown paths).

The full offline pipeline was also run end to end on a synthetic rollout directory built from the constructed
cases: packet → ledger → stages → P/S/C → containment check → comparison → inventory, 4 items, no errors, boundary
audit `PASS` on all four while every record's `fail_reason` contained the answer key.

These are **constructed inputs**. They establish what the software does, and nothing about the actor, the benchmark
or the hypothesis. `research/officeqa/oqa_cases.py` says so in its own docstring, and no OfficeQA question, answer
or document text appears in it — the gated data had not been materialized when it was written.

---

## 9. The demonstration asked for: does the connected report do better?

Run `pytest tests/test_officeqa_contracts.py -k wrong_period or scale or hallucinated` to reproduce. All three are
constructed traces.

**Case A2 — units. The plain report is silent; C makes the correction.** Question: the gross Federal debt at the
end of September 1988, *in billions of dollars*. The agent greps the right file, copies `2,602,338` from the right
row of the supplied page, and answers it.

* **P:** no findings. The value *is* in the evidence, the asked period *is* present, the path *is* allowed, and no
  arithmetic was requested — every check P can make passes.
* **S:** no findings either. The operand is grounded and the arity is 1, so no handoff is open.
* **C:** `[ADVICE] scale_conversion_missing: step 2: the value 2,602,338 sits under a heading that states
  'millions' (contract table.scale.1, assumption table_scale_applies_to_rows), and the question asks for
  'billions'. No conversion step appears: divide by 1,000 before answering.`

The native scorer marks this wrong (the digits differ), so the correction is actionable, and only the condition
that can **type the operand's unit** states it. It depends on one empirical assumption, which the message names.

**Case A1 — dates and source selection. The plain report is silent; S makes the correction.** Question: the change
between June 1970 and December 1964. Both gold pages are supplied. The agent greps the 1970 page, takes both
operands from it (the second from the neighbouring row), and answers the difference.

* **P:** no findings. The question asks for a difference, and a pair of available numbers *does* reproduce the
  answer — P cannot tell whether it is the right pair. Both asked periods appear in the evidence, because the
  harness supplied both pages, so P's period check passes too.
* **S:** `[ADVICE] unsupported_operands: the question varies the period over 1970, 1964, but every bound operand
  traces to 1970; 1964 is never read. A difference over these operands answers a different question. Read the page
  for 1964 and take the second operand from it.`
* Comparison: `ADDITIONAL` and `MORE_ACTIONABLE` on problem `operand_period_binding`; C adds nothing (`TIE`).

**Case A3 — the honest negative. The plain report already catches it.** The agent states a figure that appears in
no returned evidence and answers it.

* **P:** `[WARNING] ungrounded_answer_value` at the answer step.
* **S:** `[WARNING] operand_provenance_missing` at the earlier step where the figure was written down.
* Comparison: `EARLIER` and `MORE_SPECIFIC` on problem `answer_grounding`, **not** `ADDITIONAL`. The connected
  report localizes and anticipates; it discovers nothing P does not say. Reporting this as a new finding would be
  exactly the over-counting that scoring per problem rather than per category is there to prevent.

**What this is and is not.** It establishes that the connected conditions *can* produce a correction the plain
report does not, in two of the four named families, and that the scoring does not over-credit them in the third.
It does **not** establish that these failures occur in real OfficeQA traces, at what rate, or that the corrections
would change a skill edit. The earlier ALFWorld studies are a warning here: richer reports there barely changed
the optimizer's edits. That is what the pilot measures.

---

## 10. Resources: what is free, and the request

**Verified at 2026-11-15 00:19–00:43 EST** (`squeue`, `contract_work/jobs.tsv`, `coordination/reservations.jsonl`):
the API campaign owns the cluster right now. Two of its jobs are RUNNING (`ac-pfx` 66155259, `ac-ev` 66155713), four
are held or waiting on dependencies, and six Experiment 3 GPU jobs sit held on `spgpu`. Its approved ceilings are
161 CPU job-hours with 146.5 committed and a 14.5-hour reserve earmarked for recovery (10.5 if the shared-prefix
rerun fires), DeepSeek 750 calls / \$20 with a 682 / \$15.62 worst case already committed, and an EmpirioLabs actor
cap of \$14.50. **No free allocation exists for OfficeQA, and nothing here may draw on the campaign's reserve.**
GPUs are idle but unauthorized.

A coordination record (`type: ANNOUNCE`, `id: OQA-FEAS-0`, `from: officeqa`) states that this session is local-only
and consumes nothing.

### Request OQA-1 — ready to run, pending the user's approval and the campaign owner's ACK

| Item | Value |
|---|---|
| Jobs | three, sequential, on the A40 GPU partition: `STAGE=tools` (0:30), `STAGE=check3` (1:00), then — only on the user's go-ahead after the check is read — `STAGE=pilot12` (1:30) |
| Shape per job | 1 × A40, 4 CPUs, 48 GB (one spgpu billing unit: billing is the max of weighted resources) |
| Billable total | ≤ 3.0 GPU-equivalent hours ≈ **\$0.33** at the \$0.109/GPU-h observed in the earlier runs |
| Model | target `Qwen/Qwen3.5-4B` at revision `851bf6e8…`, served by the existing vLLM 0.28.0 environment, thinking disabled, temperature 0.7 — identical to the ALFWorld arm |
| Paid API | **none.** No DeepSeek call, no EmpirioLabs call. The optimizer is not involved in either run |
| Spending ceiling | **\$0.50 of GPU billing and \$0.00 of API spend.** Stop at either |
| Data | train split only, selection OQA-SEL-1; no validation or test item is read |
| Prerequisite | blocker 1 (section 3a): a Hugging Face token with the dataset conditions accepted, in a `600` file outside every repository, plus the sparse corpus fetched with `oqa_materialize.py --print-docs` |
| Not included | any optimizer comparison. The analyst prompt for OfficeQA is dominated by the supplied pages (up to 80,000 characters per item, un-truncated by `reflect.py`), so its cost must be **measured** by the check before a budget for it can be proposed |

**Proposal for the order of work:** `tools` first and alone. It needs no OfficeQA data, so it can run before
blocker 1 is cleared, and it settles the one question that would otherwise waste the whole study.

---

## 11. Open uncertainties

1. **Unknown:** whether Qwen3.5-4B calls these tools competently at all. A 4B model may ignore them, or loop on
   `glob`. The probe answers the mechanical half; the three-question check answers the behavioural half.
2. **Unknown:** how often the model writes its arithmetic into visible message text. The record has no separate
   reasoning field, so if it does not, the `compute` stage is unobservable and `extract_operands → compute`
   handoff checks lose their input.
3. **Interpretation:** because the harness supplies the gold pages, OfficeQA as released tests evidence selection
   *within* given pages, not retrieval. That is a narrower task than the benchmark's name suggests, and it is the
   task our contracts would be checking.
4. **Unknown:** the obligation reader is regexes over English. A missed cue yields `UNKNOWN` and loses a check
   (safe); a mis-parsed cue yields a wrong obligation (unsafe). Every finding that rests on a `STATED` obligation
   quotes the matched question text so a reader can reject it, but the error rate on real questions is unmeasured.
5. **Open research question, unchanged from the ALFWorld track:** whether a Lean checker adds anything over the
   Python checker here. Nothing in this study is Lean yet, deliberately — the design says to inspect real traces
   first, and `C vs D may be indistinguishable by construction` is still unresolved.
