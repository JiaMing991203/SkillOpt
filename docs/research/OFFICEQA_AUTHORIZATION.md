# OfficeQA three-arm campaign: the authorization, and what has been spent against it

This is the authoritative record of what the user has authorized for the 2026-11-17 three-arm OfficeQA
campaign, how it was amended, and what each amendment leaves available. **A prior approval applies only to its
original scope; compaction never authorizes anything.** Figures labelled *measured* come from the spend
ledger's own records (`<campaign>/ledger`) or from `sacct`, never from an estimate.

## 1. The grants, in order

### Grant 1 — 2026-11-17, the original campaign (user message, verbatim figures)

| resource | ceiling |
|---|---|
| EmpirioLabs (actor, `qwen3-5-4b`) | **15,000 HTTP attempts and $8** |
| DeepSeek (optimizer, `deepseek-flash`) | **130 HTTP calls and $4** |
| CPU | **20 job-hours** |
| GPUs | **zero** |

Also in force: stay inside the shared account rate limit; report a specific gap rather than exceed a ceiling.

### Grant 2 — 2026-11-17, the recovery amendment (user message)

> "Proceed autonomously within these expanded **total** campaign ceilings, **including everything already
> spent**: EmpirioLabs — $16 and 30,000 HTTP attempts; DeepSeek — $8 and 260 HTTP attempts; CPU — 40
> job-hours; GPUs — zero."

| resource | ceiling | was |
|---|---|---|
| EmpirioLabs | **$16 and 30,000 HTTP attempts** | $8 / 15,000 |
| DeepSeek | **$8 and 260 HTTP attempts** | $4 / 130 |
| CPU | **40 job-hours** | 20 |
| GPUs | **zero** | zero |

These are **totals for the whole campaign**, not fresh allowances. Everything in section 3 counts against them.
The same message authorizes the recovery and the planned evaluations without further approval, and still
requires the shared account rate limit to be respected and a specific resource gap to be reported rather than
crossed.

### Grant 3 — 2026-11-18, the OQA-FOR-1 follow-up allocation (user message)

A SEPARATE allocation for two bounded investigations, **added to** the cumulative ceilings of Grant 2
without resetting any previous usage:

| resource | follow-up allowance | cumulative ceiling after it |
|---|---|---|
| EmpirioLabs (actor) | **24 HTTP attempts and $1.00** | 30,024 attempts / $17.00 |
| DeepSeek (optimizer) | **6 HTTP attempts and $2.00** | 266 attempts / $10.00 |
| CPU | **4 requested job-hours, at most 2 CPUs per job** | 44 requested job-hours |
| GPUs | **zero** | zero |

Retries and preflight requests count against the HTTP allowances. Existing endpoint guards and provider
concurrency limits stay in force. Scope: (A) repair observability offline and inspect the actual HTTP
response for the empty replies; (B) prototype ONE richer connected action-to-stage contract from saved
training traces. **Not** authorized by it: new gameplay, full validation repeats, skill evolution, or
another campaign.

Held-out questions, answers and trajectories are out of scope for this follow-up; the existing held-out
work continues under its own authorization (Grant 2, released 2026-11-17).

Spent against Grant 3 — measured, updated as the work proceeds:

| | attempts | charged | of the follow-up allowance |
|---|---|---|---|
| EmpirioLabs, HTTP response probe (rule OQA-HTTP-1) | 23 | $0.004679 provider-reported | 23 of 24 / $1.00 |
| DeepSeek, hypothesis proposals (rule OQA-CC-1) | 3 | $0.008363 | 3 of 6 / $2.00 |
| CPU requested | 4.00 h over 17 jobs | — | 4.00 of 4 job-hours — EXHAUSTED |

**The 23 probe attempts are NOT in the spend ledger**, and that is a deviation worth knowing about: the
probe calls the OpenAI SDK directly, which it must in order to capture the raw HTTP body from the same
exchange the SDK parsed, so it bypasses the backend where the ledger lives. The allowance was enforced
instead by an in-process send cap, a per-job `--max-sends`, and a selection frozen before the first
request; the charge above is the provider's own `cost_usd` from each saved exchange, and at this project's
price table the same calls would be $0.007213. The 3 DeepSeek attempts DID go through the ledger. Two of
them were spent on my own errors and are counted: a 400 from an unset model name, and a completion cap
spent entirely on reasoning.

Cumulative after Grant 3, measured: EmpirioLabs **10,181 of 30,024 attempts** (10,158 ledger + 23 probe)
and **$6.8601 of $17.00**; DeepSeek **106 of 266 attempts** and **$3.5100 of $10.00**; CPU **31.6 of 44**
requested job-hours; GPUs 0.

### Grant 4 — 2026-11-18, the answer-recovery follow-up (user message)

A **SUBALLOCATION drawn from the remaining approved cumulative ceilings, not a reset and not an
increase** — the user's own words. Verified against the ledger before anything was submitted:

| resource | cumulative ceiling | measured spend before Grant 4 | headroom | this suballocation | fits |
|---|---|---|---|---|---|
| EmpirioLabs attempts | 30,024 | 10,181 | 19,843 | **5,000** | yes |
| EmpirioLabs charge | $17.00 | $6.8627 | $10.1373 | **$4.00** | yes |
| DeepSeek attempts | 266 | 106 | 160 | **4** (only if a new proposal is needed) | yes |
| DeepSeek charge | $10.00 | $3.5100 | $6.4900 | **$1.00** | yes |
| CPU requested job-hours | 44 | 31.6 | 12.4 | **8**, ≤2 CPUs per job, ≤2 concurrent | yes |
| GPUs | 0 | 0 | — | **0** | — |

The spend column is the ledger's own reading of 2026-11-18 (target 10,158 attempts / $6.855447,
optimizer 105 / $3.468882) plus the 23 probe sends and the one pre-ledger DeepSeek probe.

**The 23 probe sends are now IN the ledger.** The previous follow-up accounted them by hand because the
probe calls the SDK directly and bypasses the backend. `research/officeqa/oqa_ledger_backfill.py` replays
their recorded usage through the real `reserve` / `record_usage` under stage `oqa_http_probe_backfill`,
and **refuses to run twice** — it counts the usage records already present under that stage first, so the
same sends cannot be charged again. Applied in job 66346233: **23 attempts, $0.007213**.

That charge is the LARGER of two defensible figures, on purpose. The provider reports 126,720 of those
164,662 prompt tokens as cache reads and charged $0.004679; the backend computes its own charge with
`cached_tokens = prompt_cache_hit_tokens`, which is **DeepSeek's** field name, so for this provider it
sees zero cache and bills every prompt token. The backfill charges the same way, so a back-filled attempt
and a live one are charged identically and the cumulative figure stays an upper bound. **This is also the
explanation of the campaign's 38% conservatism** ($5.3159 charged against $3.2768 billed on the actor
side): the same class of defect as the `reasoning` field — a field name read for the wrong provider. The
guard is deliberately left as it is, because correcting it would lower the computed charge.

The caps the jobs enforce are therefore ABSOLUTE and were computed once, never per job (a cap derived
from "what has been spent so far" would grant a fresh allowance to every job):

```
target     15,181 attempts   $10.8626      = 10,158 + 23 + 5,000   and   6.855447 + 0.007213 + 4.00
optimizer     109 attempts   $ 4.4688      =    105 + 4            and   3.468882 + 1.00
```

Scope: (A) define and test a narrow answer-recovery policy and run twelve development episodes;
(B) repair the connected contract's extraction and re-check it; (C) **conditionally**, eight passes of S0
on the 24-question validation set through the corrected client. Not authorized by it: skill evolution,
a change to the gate or the scorer, or another three-arm campaign.

**Spent against Grant 4 — measured, final.**

| | used | of the suballocation |
|---|---|---|
| EmpirioLabs attempts | **1,598** (23 back-filled + 95 episodes + 1,480 S0) | 1,598 of 5,000 |
| EmpirioLabs charge, our price table | **$0.9789** | of $4.00 |
| EmpirioLabs charge, provider-reported | $0.6257 | — |
| DeepSeek | **0 attempts, $0.00** — no new proposal was necessary | of 4 / $1.00 |
| CPU requested | **6.88 job-hours** over 14 jobs (6.02 core-hours actual) | of 8 |
| GPUs | **0** | of 0 |

1.17 of those requested job-hours went to three jobs that never started and are counted anyway, because
requested time is what the allowance counts. The ledger's reading after this follow-up: target **11,756
attempts / $7.834371**, optimizer **105 / $3.468882**, verdict **PASS**, zero refusals on the actor side.
Four target attempts are recorded as unresolved: they were in flight when job 66346540 was killed at its
wall limit, and their reserve is deliberately not released.

**Cumulative after Grant 4:** EmpirioLabs **11,756 of 30,024 attempts** and **$7.8344 of $17.00**;
DeepSeek **106 of 266** and **$3.5100 of $10.00**; CPU **38.5 of 44** requested job-hours — **88%, now
the binding constraint**; GPUs 0.

### Grant 5 — 2026-11-18, the offline audit (user-authorized, drawn from the remaining allowance)

Up to **2 requested CPU job-hours**, ≤2 CPUs per job, ≤2 concurrent jobs, **zero API calls, zero GPUs,
zero new gameplay**, saved artifacts only. **Used: 0.87 requested job-hours over 3 jobs (66356410 0:20,
66356411 0:12, 66356437 0:20), 0 attempts, $0.00, 0 GPUs.** Job 66356410 wrote every artifact and then
exited 1 on a `NameError` in the audit tool's own interpretation step; 66356437 redid the phase in 7 s
after the fix, and its requested time is counted anyway.

**Cumulative after Grant 5:** EmpirioLabs **11,756 of 30,024 attempts** and **$7.8344 of $17.00**;
DeepSeek **106 of 266** and **$3.5100 of $10.00**; CPU **39.37 of 44** requested job-hours — **89%, and
the binding constraint on everything that remains**; GPUs 0. The held-out re-measurement that is still
on the table needs about 1.5 of the 4.63 job-hours left.

### Grant 6 — 2026-11-18, the status-and-evidence packet (user-authorized, drawn from the remaining allowance)

Up to **1 requested CPU job-hour**, ≤2 CPUs per job, **zero model API calls, zero provider probes, zero
GPUs, zero new gameplay, training or evaluation**, saved artifacts only. Scope: establish what has
happened since answer recovery, inventory the usable training/development traces, assemble three worked
examples and trace one feedback path. **Not** authorized by it: implementing the pending checker changes,
the proposed held-out comparison, or opening a sealed score.

**Used: 0.90 requested job-hours over 4 jobs, 0 attempts, $0.00, 0 GPUs.**

| job | phase | requested | elapsed | commit | result |
|---|---|---|---|---|---|
| 66395263 | status | 0:15 | 0:33 | `d777d2d` | all six statements HOLD |
| 66395275 | tests | 0:12 | 0:59 | `d777d2d` | 2,366 passed |
| 66395597 | status | 0:15 | 0:04 | `855612c` | **authoritative**; the trace-size fix in force |
| 66395598 | tests | 0:12 | 0:57 | `855612c` | **2,367 passed**, 9 skipped, 368 subtests |

The second pair re-ran the same phases after a counting defect was found in the packet's own new tool:
`actor_written` measured the actor-visible trace by joining the conversation's entries, so the harness's
separators between empty turns were counted as actor text. Both runs' requested time is counted, which is
why a two-line fix cost 0.45 job-hours.

**Cumulative after Grant 6:** EmpirioLabs **11,756 of 30,024 attempts** and **$7.8344 of $17.00**;
DeepSeek **106 of 266** and **$3.5100 of $10.00**; CPU **40.27 of 44** requested job-hours — **92%, and
still the binding constraint on everything that remains**; GPUs 0. The held-out re-measurement that is
still on the table needs about 1.5 of the **3.73** job-hours left.

### Grant 7 — 2026-11-18, the corrected-client training collection (user-authorized)

Within the existing cumulative ceilings: **1.5 requested CPU job-hours**, **800 actor attempts and
$0.75 whichever is reached first**, **zero GPUs**, **zero DeepSeek/optimizer calls**. The caps include
preflight, retries, failures, collection and offline analysis. Own submissions and bounded recovery
permitted without routine approval; completed episodes to be reused after an interruption rather than
recollected.

**Used: 286 actor attempts, $0.180329 (ledger) / $0.104993 (provider-reported), 0 optimizer calls,
1.418 requested CPU job-hours over 5 jobs, 0 GPUs.** 36% of the attempt cap, 24% of the dollar cap, 95%
of the CPU cap. Zero refusals and zero non-ok provider calls in 285.

The absolute caps were computed ONCE from the live ledger's claims net of releases (target 11,795
attempts / $7.897264): target **12,595 attempts / $8.6473**, optimizer pinned AT its current level
(105 / $3.768882) so no optimizer call could be reserved at all, and the DeepSeek key was never loaded
in any phase.

| job | phase | requested | elapsed | commit | result |
|---|---|---|---|---|---|
| 66407516 | freeze | 0:10 | 0:05 | `6a3d01b` | freeze `2083b4587253`, bundle v5 `162b580923e2e0b9` |
| 66407607 | collect | 0:30 | 0:30 | `6a3d01b` | **TIMEOUT** at 16 of 24; rows written incrementally |
| 66410230 | collect | 0:25 | 0:15:49 | `455e267` | resumed the 16, collected 8 more, 24 of 24 |
| 66412182 | analyze | 0:10 | 0:08 | `455e267` | 50 predictions all UNKNOWN; 13 optimizer logs found |
| 66412183 | tests | 0:10 | 0:56 | `455e267` | **2,390 passed**, 9 skipped, 368 subtests |

**Cumulative after Grant 7:** EmpirioLabs **12,042 of 30,024 attempts** and **$8.0147 of $17.00**;
DeepSeek **105 of 266** and **$3.4689 of $10.00**; CPU **41.69 of 44** requested job-hours — **95%**;
GPUs 0. **2.31 requested CPU job-hours remain.**

**A CPU ACCOUNTING DISCREPANCY, raised for the user's decision rather than resolved silently.** The
running total above mixes two conventions: the campaign phase was counted in CPU-core-hours (27.61,
section 3b) and every grant since in requested walltime (4.00 + 6.88 + 0.87 + 0.90 + 1.42). Computed
uniformly over all 122 OfficeQA jobs, `sacct` gives **requested walltime 79.65 h**, **elapsed 19.90 h**,
**core-hours 43.92 h**. Under a uniform requested-walltime reading the 44-hour ceiling is already
exceeded; under a uniform core-hour reading it stands at 99.8%. Which convention governs is the user's
call; the mixed figure is what has been reported since Grant 3.

## 2. What the ledger enforces, and why it is slightly *below* the grant

`research/cluster/oqa_campaign_body.sh` sets the caps the spend ledger enforces. They are not the grant's
numbers, because the grant covers the whole campaign while the ledger only ever saw what passed through it:

| role | grant | enforced cap | the difference |
|---|---|---|---|
| target (EmpirioLabs) | 30,000 / $16.00 | **30,000 / $16.00** | nothing ran outside the ledger |
| optimizer (DeepSeek) | 260 / $8.00 | **259 / $7.95** | the slow-update size probe, job 66271290 |

The probe (job 66271290) sent **1 DeepSeek request for $0.0411** on 2026-11-17 to settle whether a ten-pair
slow update would be accepted at all — 243,986 prompt tokens, 7,562 completion tokens, `finish_reason: stop`.
It ran before the campaign ledger existed, so subtracting it from the cap is what makes the cap an enforcement
of the **total** rather than of the ledger's own subtotal.

There is no equivalent subtraction on the actor side. The preflight work that ran outside the ledger made no
paid actor call: `research/officeqa/oqa_avail3.py` is key-free by construction (it asserts against its own
source at import that it cannot read the answer columns), and `research/officeqa/oqa_ledger_check.py` contains
no HTTP client at all.

## 3. Spent against the total — measured

Figures below are read from the campaign ledger after reconciliation and from `sacct`. They include the
original chain, the four recovery jobs, and the test jobs.

### 3a. API, from the ledger's own per-process records

| | attempts | charged | of the amended ceiling |
|---|---|---|---|
| EmpirioLabs, original chain | 7,313 | $4.9441 | |
| EmpirioLabs, recovery chain | 1,081 | $0.6800 | |
| EmpirioLabs, held-out chain (66332133/4/5) | 1,764 | $1.2314 | |
| EmpirioLabs, outside the ledger | 0 | $0.0000 | |
| **EmpirioLabs total** | **10,158 of 30,000** | **$6.8554 of $16.00** | 34% / 43% |
| DeepSeek, original chain | 66 | $2.1913 | |
| DeepSeek, recovery chain | 36 | $1.2692 | |
| DeepSeek, size probe (job 66271290) | 1 | $0.0411 | |
| **DeepSeek total** | **103 of 260** | **$3.5016 of $8.00** | 40% / 44% |

Read from the ledger on 2026-11-17 after the held-out chain finished: target 10,158 attempts /
$6.855447, optimizer 102 / $3.460519 in the ledger plus the probe, **zero unresolved requests, zero
reserve breaches, verdict PASS.** The held-out line is the arithmetic difference between that reading and
the post-recovery one; and after the `oqa_ledger_reconcile.py` defect found and fixed in this
session (it took the spend to be the larger of two roundings, which exceeds the net claim once claims
have been reconciled down to equal the spend, so it refused to release anything), the reconciliation ran
clean: **net claims equal recorded spend exactly in both roles** — optimizer 102 attempts / $3.460519,
target 10,158 / $6.855447 — and there was nothing left to give back, 0 attempts and $0.000002 released.
The figures above are **recorded spend**, not reservations.

**A conservatism worth knowing about, from the provider's own accounting.** The raw actor call logs carry
the provider's `cost_usd` per attempt. Over the 16 jobs that have a log, the provider charged **$3.2768**
where the ledger charged **$5.3159** for exactly the same 7,909 attempts (the two agree exactly on the
attempt count and on 8,503,592 completion tokens). The difference is cache reads: the provider bills
`prompt_tokens - cached_tokens` and the ledger's price table bills all prompt tokens. **The ledger is
therefore conservative by roughly 38% on the actor side**, which is the safe direction for a cap. The
ceilings above are not adjusted for it; doing so would need the user's decision, and the measured figures
stay as the ledger reports them.

DeepSeek is charged at **peak** prices in the ledger on purpose, so its dollar figure is an upper bound on the
real invoice; off-peak is exactly half.

**The ledger's own verdict is PASS.** After reconciliation, net claims equal recorded spend exactly in both
roles — optimizer $3.460519 against 102 attempts, target $5.624077 against 8,394 — with **zero unresolved
requests** and **zero reserve breaches**. Released back to the authorization across the campaign: optimizer
46 attempts / $2.2395, target 456 attempts / $2.2259. The four recovery jobs released their own unspent claims
on clean exit, which is the fix working in production rather than in a test.

The 24 refusal records remain in the ledger. They are history: every stage they refused has since been
recovered, and `oqa_recover.py audit` reports those stages as `repaired`, not `starved`.

### 3b. CPU, from `sacct`

"CPU job-hours" reads two ways and the two differ by a factor of two here, because every job asked for 2 CPUs.
Both are recorded; **planning uses the larger, conservative reading**:

| reading | spent (57 OfficeQA jobs) | of 40 |
|---|---|---|
| wall-clock hours summed over jobs (`Elapsed`) | ~13.8 | 35% |
| CPU-core-hours (`CPUTimeRAW`) | **27.61** | 69% |

By phase: the three arms 18.80 core-hours, the recovery 3.52, the held-out chain 3.84, the shared
epoch-1 job 1.08, and everything else — tests, the diagnostic read, the forensics audit, the preflights
and the probes — 0.37 between them.

The recovery chain itself was cheap: 26:20 + 20:14 + 24:52 + 27:01 of wall time, **3.28 core-hours** in total.

GPUs: **zero**, as required. No OfficeQA job has ever requested one.

## 4. What the planned work needs

Costs below use the campaign's own **measured** constants, not the pre-launch estimates:
$0.00656 and 10.32 attempts per question-rollout (`research/officeqa/oqa_budget.py`).

| work | question-rollouts | actor | optimizer |
|---|---|---|---|
| recover 4 starved arm-epochs, certain part | 192 | $1.26 / 1,981 attempts | ~36 calls, ~$1.44 |
| the same, worst case (every conditional rollout stale) | 266 | $1.75 / 2,745 attempts | ~36 calls, ~$1.44 |
| held-out, per distinct skill (60 questions) | 60 | $0.39 / 619 attempts | 0 |
| held-out, primary (S0 + each distinct selected skill, at most 4) | 240 | $1.57 | 0 |
| held-out, exploratory last skills (at most 3 more) | 180 | $1.18 | 0 |

**Worst case for everything planned, against the amended totals:**

| | spent | planned | total | ceiling | headroom |
|---|---|---|---|---|---|
| EmpirioLabs attempts | 7,313 | 5,580 | 12,893 | 30,000 | 17,107 |
| EmpirioLabs charge | $4.9441 | $4.50 | $9.44 | $16.00 | $6.56 |
| DeepSeek calls | 67 | 36 | 103 | 260 | 157 |
| DeepSeek charge | $2.2324 | $1.44 | $3.67 | $8.00 | $4.33 |
| CPU core-hours | 19.95 | ~13 | ~33 | 40 | ~7 |
| GPUs | 0 | 0 | 0 | 0 | — |

**Nothing planned reaches a ceiling.** The tightest is CPU on the conservative core-hour reading, at about 83%.

## 5. Per-job ceilings, which are separate

Beside the account caps, each job carries its own ceiling so one runaway arm-epoch cannot consume what the
other arms still need (`SKILLOPT_LEDGER_JOB_MAX_*`). Measured, a full later-epoch arm job spends $0.28–$0.36 of
optimizer over 7–9 calls and under $0.70 of actor, against per-job ceilings of $0.70 / 16 calls and
$1.20 / 1,600 attempts.

## 6. What is still NOT authorized

* **Pushing** this branch to a remote. Deployment is by pinned `git archive` bundle with an in-job manifest
  check (`research/cluster/oqa_bundle.sh`).
* **GPUs**, in any quantity.
* **Weakening the validation gate.** Accepting a candidate on a tie would change the experiment's semantics and
  needs explicit approval; `docs/research/OFFICEQA_GATE_RESOLUTION.md` sets out the options.
* **Unsealing** the held-out outcome of job 66234927, which remains deliberately unread.
* The one remaining paid DeepSeek attempt from the original probe grant stays unspent.
