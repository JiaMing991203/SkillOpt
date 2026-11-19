# The case-selection rule for OFFICEQA_COLLECT_PACKET.md section 5

Recorded 2026-11-18, BEFORE the 24-episode collection finished and before COLLECT_SUMMARY.json was
read, so the selection cannot be fitted to the findings. Deterministic: anyone can re-apply it to
COLLECT_SUMMARY.json and get the same cases.

## Up to six failures

1. Partition the failures (`hard == 0`) by the pair
   (`termination_cause`, whether an answer was emitted).
2. Order the partitions by size descending, then by partition name ascending.
3. Take one case from each partition in that order, choosing the lowest question id within the
   partition. This guarantees every distinct failure KIND is represented before any kind is repeated.
4. If fewer than six have been taken, continue round-robin through the same partition order, each time
   taking the lowest remaining id, until six are taken or the failures run out.

## Two successes, as controls

5. Among the successes (`hard == 1`), take the two lowest question ids whose permitted trace carries
   written arithmetic, because a control is only informative about false alarms if there is something
   for the checker to be wrong about. If fewer than two qualify, fill from the lowest ids among all
   successes and say so.

## What the rule deliberately does not do

* It does not rank by how much the prototype found, which would select the cases that flatter it.
* It does not rank by actor-written length, which would bias toward the verbose.
* It does not exclude a case because the prototype is silent on it; silence on a failure is a finding.
