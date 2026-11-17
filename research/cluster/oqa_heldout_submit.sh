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
# The amended ceiling of 2026-11-17: $16 and 30,000 attempts in total for the campaign.
# docs/research/OFFICEQA_AUTHORIZATION.md.
USD_CAP="${OQA_CAP_ACTOR_USD:-16.00}"

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$W"

# WHAT HAS ALREADY BEEN SPENT, read from the ledger rather than carried as a constant. The earlier version of
# this script hardcoded the training chain's PROJECTED $6.22; the chain actually charged $4.94, and after the
# recovery it will be a third number. A projection that is checked against the wrong baseline is worse than no
# check, so the baseline is measured here every time.
# oqa_ledger_read.py prints its report as JSON on stdout and exits non-zero when the ledger's own verdict is
# not PASS, which is a verdict about the CAPS and not about this figure - so the exit code is tolerated here
# and the caps are checked separately, by running that tool on its own.
USD_ALREADY="$( ( cd "$W" && "$OQA_PY" -I research/officeqa/oqa_ledger_read.py "$CAMP/ledger" 2>/dev/null || true ) \
    | "$OQA_PY" -I -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('%.4f' % float(d['observed']['target']['usd']))
except Exception:
    print('')
" )"
if [ -z "$USD_ALREADY" ]; then
    echo "could not read the actor charge from $CAMP/ledger - refusing to project against a guess" >&2
    exit 2
fi
echo "actor charged so far, from the ledger: \$$USD_ALREADY of \$$USD_CAP authorized"
# S0 MUST BE A DURABLE PATH. The arms' skills live in $CAMP and survive, but the starting skill lives in the
# source tree - and this script extracts that tree into a temp directory it deletes on exit. Submitting
# OQA_SKILL=<that temp path> gives a job a file that no longer exists by the time it runs; the body would die
# on "no such skill file", after the earlier jobs in the chain had already been submitted. So S0 is copied
# into the campaign root once and referenced there, which also makes the exact S0 that was scored an artifact
# of the campaign rather than a path into a tarball.
S0="$CAMP/heldout/S0.md"
mkdir -p "$CAMP/heldout"
if [ ! -f "$S0" ]; then
    cp "$W/skillopt/envs/officeqa/skills/initial.md" "$S0"
    echo "S0 copied into the campaign root: $S0 ($(wc -c < "$S0" | tr -d ' ') bytes)"
else
    LIVE="$(sha256sum "$W/skillopt/envs/officeqa/skills/initial.md" | cut -d" " -f1)"
    HAVE="$(sha256sum "$S0" | cut -d" " -f1)"
    [ "$LIVE" = "$HAVE" ] || { echo "REFUSING: $S0 differs from this bundle's initial skill. The starting
skill of the comparison cannot change between the chain and the held-out evaluation." >&2; exit 3; }
fi

# oqa_heldout.py --out names a DIRECTORY and writes HELDOUT_PLAN.json inside it.
PLAN_DIR="$W/plan"
PLAN="$PLAN_DIR/HELDOUT_PLAN.json"
( cd "$W" && "$OQA_PY" -I research/officeqa/oqa_heldout.py --plan \
    --arm "A=$CAMP/arm_A" --arm "B=$CAMP/arm_B" --arm "C=$CAMP/arm_C" \
    --skill-init "$S0" \
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

# ALREADY EVALUATED, by digest AND settings. The held-out stage writes a PROVENANCE.json beside each result
# recording the skill digest, the split, the config digest and the prompt-shaping settings fingerprint. A skill
# whose digest and settings both match a completed evaluation is reused, not re-scored: 60 question-rollouts
# and $0.39 each time.
DONE="$W/done.txt"
: > "$DONE"
for PV in "$CAMP"/heldout/*/PROVENANCE.json; do
    [ -f "$PV" ] || continue
    "$OQA_PY" -I -c "
import json, sys
r = json.load(open('$PV'))
print('%s\t%s\t%s\t%s' % (r.get('skill_sha256', ''), r.get('settings_fingerprint', ''),
                            r.get('split', ''), r.get('label', '')))
" >> "$DONE" 2>/dev/null || true
done
[ -s "$DONE" ] && echo "already evaluated: $(wc -l < "$DONE" | tr -d ' ') skill(s) with recorded provenance"

# FROZEN BY DIGEST. The held-out split is the pre-registered primary result and it is read once; that only
# means something if what gets scored is the skill that was frozen. The recovery of the starved arm-epochs
# rewrote best_skill.md and skills/skill_v*.md on purpose, so these files are not immutable by nature - they
# are made immutable by being checked. A mismatch stops the submission rather than quietly changing what is
# measured.
FROZEN="$CAMP/heldout/SKILLS_FROZEN.json"
if [ ! -f "$FROZEN" ]; then
    ( cd "$W" && "$OQA_PY" -I research/officeqa/oqa_freeze_skills.py freeze --campaign "$CAMP" \
        --skill-init "$S0" --out "$FROZEN" ) || {
        echo "could not freeze the evaluation set" >&2; exit 2; }
else
    echo "evaluation set already frozen at $FROZEN"
fi
( cd "$W" && "$OQA_PY" -I research/officeqa/oqa_freeze_skills.py verify --frozen "$FROZEN" ) || {
    echo "REFUSING: a skill in the frozen evaluation set has changed. Either a job rewrote it, in which" >&2
    echo "case the recovery is not finished, or the freeze is stale and should be redone deliberately." >&2
    exit 3; }

i=0
PREV=""
while IFS=$'\t' read -r ROLE ARM DIGEST PATH_; do
    i=$((i + 1))
    [ "$i" -le "$N_WANT" ] || break
    FULL="$(sha256sum "$PATH_" | cut -d" " -f1)"
    if PRIOR="$(awk -F'\t' -v d="$FULL" -v s="valid_unseen" '$1==d && $3==s {print $4; exit}' "$DONE")" \
       && [ -n "$PRIOR" ]; then
        printf '  %-26s %s  REUSED from %s (identical digest and settings)\n' \
            "$ROLE/${ARM}" "$DIGEST" "$PRIOR"
        continue
    fi
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
