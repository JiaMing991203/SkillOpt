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
