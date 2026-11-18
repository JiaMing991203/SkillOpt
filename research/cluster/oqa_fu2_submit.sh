#!/bin/bash
# Submit one phase of the second OfficeQA follow-up. Run on the login node.
#
#   OQA_BUNDLE=<bundle> bash oqa_fu2_submit.sh <phase> [--dry-run]
#
#     tests      0:12  free
#     prep       0:12  free        (add OQA_BACKFILL_APPLY=1 to write the ledger backfill)
#     episodes   0:45  PAID        all twelve episodes in one job (A1, A2, B1, B2)
#     cc         0:20  free        (OQA_CC_PROPOSE=1 allows at most 2 DeepSeek attempts)
#     s0         1:20  PAID        two concurrent jobs of four passes each
#
# WHY THE TIME LIMITS ARE SMALL. The CPU allowance for this follow-up is 8 REQUESTED job-hours, and a
# requested hour is spent whether or not the job uses it. Every --time below is sized from a measured
# precedent: the campaign ran 24 questions in about 15 minutes with 4 workers, the whole test suite in
# about 1 minute, and the offline tools in seconds.
#
# AT MOST TWO JOBS RUN AT ONCE, which is the other limit of this allocation.
set -euo pipefail
PHASE="${1:?usage: oqa_fu2_submit.sh tests|prep|episodes|cc|s0 [--dry-run]}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?}"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_fu2_body.sh"
[ -f "$BODY" ] || { echo "no body at $BODY" >&2; exit 2; }

# The ABSOLUTE caps of this suballocation, computed once on 2026-11-18 and recorded in
# docs/research/OFFICEQA_AUTHORIZATION.md. They are passed to every paid job unchanged.
CAPS="OQA_FU2_CAP_ACTOR_ATTEMPTS=15181,OQA_FU2_CAP_ACTOR_USD=10.8626,OQA_FU2_CAP_OPT_CALLS=109,OQA_FU2_CAP_OPT_USD=4.4688"

sub() {                       # sub <name> <time> <extra-exports> [dependency]
    local name="$1" time="$2" extra="$3" dep="${4:-}"
    local depflag=()
    [ -n "$dep" ] && depflag=(--dependency="afterok:$dep" --kill-on-invalid-dep=yes)
    if [ "$DRY" = "1" ]; then
        printf '  %-18s %s  %s\n' "$name" "$time" "$extra"
        echo "dry"
        return
    fi
    sbatch --parsable --job-name="$name" --account="$ACCOUNT" --partition="$PARTITION" \
        --cpus-per-task=2 --mem=8G --time="$time" --no-requeue \
        --output="$B/$name-%j.out" --chdir="$B" "${depflag[@]}" \
        --export="ALL,OQA_BUNDLE=$B,$CAPS,$extra" --wrap="bash $BODY"
}

case "$PHASE" in
tests)
    J=$(sub oqa-fu2-tests 00:12:00 "PHASE=tests")
    echo "tests      $J"
    ;;
prep)
    J=$(sub oqa-fu2-prep 00:12:00 "PHASE=prep${OQA_BACKFILL_APPLY:+,OQA_BACKFILL_APPLY=1}")
    echo "prep       $J  (backfill apply: ${OQA_BACKFILL_APPLY:-0})"
    ;;
episodes)
    # ONE job for all twelve episodes: ~25 minutes of work, and the allowance is counted in REQUESTED
    # job-hours. 400 attempts is ten times the measured need for 12 episodes and still a hard stop.
    J=$(sub oqa-fu2-episodes 00:45:00 \
        "PHASE=episodes,OQA_JOB_CAP_ACTOR_ATTEMPTS=400,OQA_JOB_CAP_ACTOR_USD=0.40")
    echo "episodes   $J  (A1, A2, B1, B2 in one job)"
    ;;
cc)
    J=$(sub oqa-fu2-cc 00:20:00 "PHASE=cc${OQA_CC_PROPOSE:+,OQA_CC_PROPOSE=1},OQA_JOB_CAP_OPT_CALLS=2,OQA_JOB_CAP_OPT_USD=0.60")
    echo "cc         $J  (new proposal allowed: ${OQA_CC_PROPOSE:-0})"
    ;;
s0)
    # 8 passes of 24 questions. Two concurrent jobs of four passes each: at the campaign's measured
    # 10.32 attempts per question-rollout that is about 990 attempts and ~1 hour per job.
    SJ="OQA_JOB_CAP_ACTOR_ATTEMPTS=1600,OQA_JOB_CAP_ACTOR_USD=1.20"
    P1=$(sub oqa-fu2-s0-a 01:20:00 "PHASE=s0,FIRST=1,PASSES=4,$SJ")
    P2=$(sub oqa-fu2-s0-b 01:20:00 "PHASE=s0,FIRST=5,PASSES=4,$SJ")
    echo "s0 passes 1-4  $P1"
    echo "s0 passes 5-8  $P2"
    ;;
*)
    echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
