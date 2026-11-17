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
| EmpirioLabs, outside the ledger | 0 | $0.0000 | |
| **EmpirioLabs total** | **8,394 of 30,000** | **$5.6241 of $16.00** | 28% / 35% |
| DeepSeek, original chain | 66 | $2.1913 | |
| DeepSeek, recovery chain | 36 | $1.2692 | |
| DeepSeek, size probe (job 66271290) | 1 | $0.0411 | |
| **DeepSeek total** | **103 of 260** | **$3.5016 of $8.00** | 40% / 44% |

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

| reading | spent (32 OfficeQA jobs) | of 40 |
|---|---|---|
| wall-clock hours summed over jobs (`Elapsed`) | ~11.7 | 29% |
| CPU-core-hours (`CPUTimeRAW`) | **23.47** | 59% |

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
