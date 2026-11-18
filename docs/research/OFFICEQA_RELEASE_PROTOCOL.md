# The procedure for releasing an OfficeQA held-out result

The rules below were already in force — they come from `CLAUDE.md` §5 and from how the earlier runs were
handled — but they were spread across several documents and partly carried in conversation. Written down here
so that "the established procedure" is something a later session can check compliance against instead of
reconstructing. The compliance record for this campaign's release is at the bottom.

## The six rules

**1. The held-out split is read ONCE, at the end, and nothing is tuned on it.**
No skill, contract, configuration or threshold may be chosen or adjusted using a held-out number. In practice
this means the held-out evaluation is a separate job submitted after the training chain has finished, with
`evaluation.eval_test: false` throughout the chain so the trainer never touches it.

**2. The comparison is pre-registered before the numbers exist.**
The **PRIMARY** comparison is S0 against each arm's **validation-selected** skill (`best_skill.md`) — the skill
that arm's own validation gate chose. It is fixed in advance and does not depend on how the numbers come out.

**3. Any other skill's held-out number is EXPLORATORY and must be labelled so.**
Each arm's **last** skill (`skills/skill_v<step>.md`) is what the arm happened to be holding when it stopped,
which its own gate may well have rejected. A number from one of those describes what an arm ended up with; it
is not a comparison result and may not be presented as one. The job label carries `exploratory` so the output
directory says so too.

**4. The evaluation set is frozen by digest before anything is submitted.**
`research/officeqa/oqa_freeze_skills.py freeze` records every skill with its sha256, byte count, role and arm,
plus a digest over the whole record; `verify` re-checks them and `oqa_heldout_submit.sh` refuses to submit on
any mismatch. This is not ceremony: the arms' skill files live in run directories that a resumed trainer job
writes to, and the recovery of the starved arm-epochs rewrote exactly those files.

**5. Each distinct skill is scored once, and an identical evaluation is reused rather than repeated.**
Skills are deduplicated by digest before submission. Each result carries a `PROVENANCE.json` recording the
skill digest, the split, the config digest and the prompt-shaping settings fingerprint; a skill whose digest
**and** settings match a completed evaluation is reused. Digest alone is not enough — the same skill under a
different split or turn budget is a different measurement.

**6. Earlier sealed results stay sealed.**
The held-out outcome of **job 66234927** (the two-arm A40 run) remains deliberately **UNREAD**. It is not
consulted, not compared against, and not used to calibrate anything here.

## How a result is reported

* Give the observed scores, the denominator, and the resolution of the instrument. One question on the
  60-question held-out split is 1.7 percentage points; on the 24-question gate set it is 4.2.
* Separate **verified** measurements from **interpretation**. A held-out difference of one or two questions is
  not a finding.
* Report the primary comparison whatever it shows, including when it shows nothing, and report what the
  design could not have detected.
* Say which skills were distinct **by advice** as well as by digest. Two skills that differ only inside the
  injected `SLOW_UPDATE` / `META_SKILL` / `APPENDIX` marker blocks carry the same advice to the actor, so
  counting them as two different skills would overstate how much the arms diverged.

## Compliance record — the 2026-11-17 three-arm campaign

| rule | how it was met |
|---|---|
| 1. read once, nothing tuned on it | `eval_test: false` in `configs/research/officeqa_api3.yaml` for the whole chain and the whole recovery; the held-out jobs are a separate submission after the last training job |
| 2. pre-registered primary | S0 + each arm's `best_skill.md`, fixed in `oqa_heldout_submit.sh` and in `OFFICEQA_THREE_ARM_LAUNCH.md` before any arm ran |
| 3. exploratory labelled | last-skill jobs are named `oqa-ho-exploratory_<arm>_last` and their output directories carry that name |
| 4. frozen by digest | `heldout/SKILLS_FROZEN.json`, verified by the submitter before submission |
| 5. scored once, reused | deduplicated by digest; `PROVENANCE.json` per result; the submitter skips a matching digest+settings pair |
| 6. sealed stays sealed | job 66234927 unread; `docs/research/OFFICEQA_AUTHORIZATION.md` §6 records it as not authorized to unseal |

---

## THE RESULT, released 2026-11-17 under these six rules

The chain finished: 66332133 (S0) COMPLETED, 66332134 (arm B's selected skill) COMPLETED, 66332135
(arm A's and C's last skill, exploratory) COMPLETED. All three ran at commit
`251e9514beaefb88ed1ded997102c4769a2e23f1`, config `officeqa_api3.yaml` digest `9ff823b2043a`, settings
fingerprint `d58cf3b59bbc2928`, split `valid_unseen` of `split_sel3`, n = 60. Each carries a
`PROVENANCE.json` naming its skill digest.

### Verified — the observed scores

| | skill | digest | bytes | hard | soft | questions |
|---|---|---|---|---|---|---|
| **PRIMARY** | S0 | `9d377d1c2906` | 887 | **0.0500** | 0.0500 | **3 of 60** |
| **PRIMARY** | arm B's validation-selected skill | `95989ce5ebad` | 3,603 | **0.1000** | 0.1000 | **6 of 60** |
| exploratory | arm A's and C's last skill | `5bd17ea109bc` | 940 | **0.1333** | 0.1500 | **8 of 60** |

One question on this split is **1.7 percentage points**. Arms A and C selected S0 itself, so the primary
comparison has two distinct documents, not four; the third row is their *last* skill, which their own
gates rejected, and is labelled exploratory because of that.

**Distinct by advice, not only by digest.** The exploratory skill differs from S0 by exactly two HTML
comment lines — the empty `<!-- SLOW_UPDATE_START -->` / `<!-- SLOW_UPDATE_END -->` placeholder — so its
advice digest (`3e70328ffb65`) is **identical to S0's**. It carries the actor the same advice. Of the
three documents scored here, **two are the same advice** and one (arm B's) differs.

### Verified — the measurement's own resolution, from this same table

Two documents that give the actor **identical advice** scored **3 of 60** and **8 of 60**: a five-question,
8.3-percentage-point spread with no difference in advice at all. That is a held-out measurement of the
same instrument noise the training-split replicates showed (one fixed document, seven draws, 1 to 6 of 24,
sd 1.95 — `OFFICEQA_DIAGNOSTIC_PACKET.md` §3). The actor is sampled at temperature 0.7 with no seed.

### Interpretation, and what this design could not have detected

* Arm B's selected skill scored **three questions above S0** on the primary comparison. Under rule 2 that
  number is reported whatever it shows — and under rule 3 it is **not a finding**: the S0-advice pair in
  the same table differs by five questions, which is larger. Nothing here separates a skill effect from a
  draw.
* The comparison **cannot** separate the arms. Arms A and C selected S0, so there is no arm-A or arm-C
  *selected* skill to score; the only edited document in the primary comparison is arm B's.
* The design had no power to resolve differences of this size. At the measured spread, detecting a
  three-question difference on 60 questions would need many repeated evaluations per document
  (`OFFICEQA_DIAGNOSTIC_PACKET.md` §9 gives the arithmetic on the 24-question set).
* Reported for completeness because rule 2 requires it: the exploratory document scored highest of the
  three. It carries S0's advice, so it is evidence about the instrument, not about any arm's optimizer.

### Compliance

| rule | how it was met here |
|---|---|
| 1. read once, nothing tuned on it | read once, after the chain finished; `eval_test: false` throughout; and the two evidence packets' proposed next tests were written **before** these numbers were opened |
| 2. pre-registered primary | S0 + each arm's `best_skill.md`, fixed before any arm ran; arms A and C selected S0, so the primary set has 2 distinct documents |
| 3. exploratory labelled | job name, output directory and this table all say exploratory for the last skill |
| 4. frozen by digest | `heldout/SKILLS_FROZEN.json`, record `f26ba852b9e59dc4`, verified by the submitter before submission; each result's `PROVENANCE.json` repeats its skill digest |
| 5. scored once, reused | three evaluations for three distinct digests at one settings fingerprint; no digest scored twice |
| 6. sealed stays sealed | job 66234927 remains unread |
