#!/bin/bash
# Submit the held-out evaluation, once per DISTINCT skill, PRIMARY skills first. Run on the login node.
#
#   OQA_BUNDLE=<bundle> bash oqa_heldout_submit.sh [--dry-run] [--primary-only] [--max-skills N]
#
# WHAT IS PRIMARY AND WHAT IS EXPLORATORY. The user's instruction: "Keep the validation-selected skills as the
# primary comparison and label any last-skill evaluations exploratory." So:
#
#   PRIMARY      S0, and each arm's `best_skill.md` - the skill its own validation gate selected.
#   EXPLORATORY  each arm's last skill (`skills/skill_v<step>.md`), which the gate may well have REJECTED. A
#                number from one of these is not a comparison result; it is a look at what the arm ended up
#                holding. The job label carries `exploratory` so the output directory says so too.
#
# Skills are deduplicated BY DIGEST before anything is submitted, because the held-out split must be scored once
# per distinct skill and never twice. In the A40 two-arm run every candidate was rejected and both arms' final
# skills were byte-identical to S0, which collapsed five requested skills to one.
#
# THE SPEND CEILING. At the campaign's measured $0.00656 per question-rollout a 60-question held-out pass costs
# $0.39, and the training chain has already spent about $6.22 of the authorized $8.00. So FOUR distinct skills
# fit ($7.79) and five do not ($8.18). `--primary-only` submits just the primary set, which is at most four
# skills including S0 and therefore always fits. Without it the script REFUSES to submit a set it computes to
# cost more than the ceiling, and prints what to ask for.
set -euo pipefail
B="${OQA_BUNDLE:?set OQA_BUNDLE to the bundle directory}"
DRY=0; PRIMARY_ONLY=0; MAX_SKILLS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --primary-only) PRIMARY_ONLY=1 ;;
        --max-skills) MAX_SKILLS="${2:?--max-skills needs a number}"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

set -a; . "$B/settings.env"; set +a
: "${OQA_CAMPAIGN_ROOT:?}" ; : "${OQA_PY:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
ACCOUNT="${OQA_ACCOUNT:-csriram0}"
PARTITION="${OQA_PARTITION:-standard}"
BODY="$B/oqa_campaign_body.sh"
USD_PER_SKILL=0.394          # 60 questions x $0.00656, measured on this campaign (job 66272737)
USD_ALREADY=6.22             # the training chain's projected spend
USD_CAP="${OQA_CAP_ACTOR_USD:-8.00}"

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$W"
# oqa_heldout.py --out names a DIRECTORY and writes HELDOUT_PLAN.json inside it.
PLAN_DIR="$W/plan"
PLAN="$PLAN_DIR/HELDOUT_PLAN.json"
( cd "$W" && "$OQA_PY" -I research/officeqa/oqa_heldout.py --plan \
    --arm "A=$CAMP/arm_A" --arm "B=$CAMP/arm_B" --arm "C=$CAMP/arm_C" \
    --usd-per-question 0.00656 --requests-per-question 10.32 --out "$PLAN_DIR" ) > /dev/null
[ -f "$PLAN" ] || { echo "the held-out planner wrote no plan at $PLAN" >&2; exit 2; }

# One line per skill to score: <role> <arm> <digest> <path>, primary first, deduplicated by digest.
"$OQA_PY" -I -c "
import json, sys
plan = json.load(open('$PLAN'))
seen, rows = set(), []
for want in ('S0', 'validation_selected', 'last'):
    for e in plan['entries']:
        if e.get('role') != want or not e.get('digest') or e['digest'] in seen:
            continue
        seen.add(e['digest'])
        rows.append((e['role'], e.get('arm') or '-', e['digest'][:12], e['path']))
for r in rows:
    print('\t'.join(r))
" > "$W/skills.tsv"

echo "campaign root: $CAMP"
echo "bundle:        $B  (commit $(cat "$B/COMMIT"))"
"$OQA_PY" -I -c "
import json
p = json.load(open('$PLAN'))
print('skills requested %d, distinct %d' % (p['skills_requested'], p['skills_distinct']))
for d, who in sorted(p['distinct_skills'].items()):
    print('  %s  %s' % (d[:12], ', '.join(who)))
if p['problems']:
    print('PROBLEMS:', p['problems'])
"
echo

N_PRIMARY=$(awk -F'\t' '$1=="S0" || $1=="validation_selected"' "$W/skills.tsv" | wc -l | tr -d ' ')
N_ALL=$(wc -l < "$W/skills.tsv" | tr -d ' ')
N_WANT="$N_ALL"
[ "$PRIMARY_ONLY" = "1" ] && N_WANT="$N_PRIMARY"
[ "$MAX_SKILLS" != "0" ] && [ "$MAX_SKILLS" -lt "$N_WANT" ] && N_WANT="$MAX_SKILLS"
PROJECTED=$("$OQA_PY" -I -c "print('%.2f' % ($USD_ALREADY + $N_WANT * $USD_PER_SKILL))")
echo "distinct skills: $N_ALL total, $N_PRIMARY primary; submitting $N_WANT"
echo "projected actor charge including the training chain: \$$PROJECTED of \$$USD_CAP authorized"
if [ "$("$OQA_PY" -I -c "print(1 if $PROJECTED > $USD_CAP else 0)")" = "1" ]; then
    echo
    echo "REFUSING: that would exceed the authorized actor charge. Options:" >&2
    echo "  --primary-only        score S0 and the three validation-selected skills only (the primary" >&2
    echo "                        comparison, at most 4 distinct skills, always inside the ceiling)" >&2
    echo "  --max-skills N        score the first N, primary first" >&2
    echo "  ask for a higher ceiling (about \$10 covers every distinct skill)" >&2
    exit 3
fi
echo

i=0
PREV=""
while IFS=$'\t' read -r ROLE ARM DIGEST PATH_; do
    i=$((i + 1))
    [ "$i" -le "$N_WANT" ] || break
    case "$ROLE" in
        S0)                  LABEL="S0" ;;
        validation_selected) LABEL="primary_${ARM}_selected" ;;
        last)                LABEL="exploratory_${ARM}_last" ;;
        *)                   LABEL="${ROLE}_${ARM}" ;;
    esac
    DEPFLAG=()
    [ -n "$PREV" ] && DEPFLAG=(--dependency="afterok:$PREV" --kill-on-invalid-dep=yes)
    if [ "$DRY" = "1" ]; then
        printf '  %-26s %s  %s\n' "$LABEL" "$DIGEST" "$PATH_"
        continue
    fi
    JOB=$(sbatch --parsable --job-name="oqa-ho-$LABEL" --account="$ACCOUNT" --partition="$PARTITION" \
        --cpus-per-task=2 --mem=8G --time=00:60:00 --no-requeue \
        --output="$B/oqa-ho-$LABEL-%j.out" --chdir="$B" "${DEPFLAG[@]}" \
        --export="ALL,OQA_BUNDLE=$B,STAGE=heldout,OQA_SKILL=$PATH_,OQA_LABEL=$LABEL,OQA_JOB_CAP_ACTOR_ATTEMPTS=900,OQA_JOB_CAP_ACTOR_USD=0.60" \
        --wrap="bash $BODY")
    printf '  %-26s %s  job %s  after %s\n' "$LABEL" "$DIGEST" "$JOB" "${PREV:-none}"
    PREV="$JOB"
done < "$W/skills.tsv"
echo
echo "PRIMARY comparison = S0 and the three *_selected skills. Any *exploratory* number is NOT a comparison"
echo "result: it is what the arm ended up holding, and its gate may well have rejected it."
