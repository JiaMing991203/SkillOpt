#!/bin/bash
# THE OFFICEQA STATUS-AND-EVIDENCE PACKET: the cluster half. CPU only, ZERO API calls, zero GPUs,
# zero gameplay, nothing re-run and nothing re-measured.
#
#   sbatch --wrap="bash <bundle>/oqa_st_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>
#
#   PHASE=status  free. The corrected-client trace inventory, the six status statements decided against
#                 the records, and the UID0014 feedback chain with the analyst view reconstructed from
#                 SkillOpt's own formatter.
#   PHASE=tests   free. The whole test suite, including the 13 new OQA-ST-1 reading-rule tests.
#
# EVERY API KEY IS DROPPED BEFORE PYTHON STARTS. This job cannot spend: no key is in the environment and
# no request path is exercised. The spend ledger is READ, never written.
#
# WHAT IS PRESERVED. Nothing under followup_20261118/, followup_20261119/, audit_20261118/, heldout/ or
# any arm_*/ directory is written. Every output goes to $CAMP/status_20261118/. The checker proposals
# P-1 and P-2 are NOT applied; `oqa_status.py statements` only reads the deployed source to confirm that
# they are still unapplied.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:?set PHASE=status|tests}"
case "$PHASE" in status|tests) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}" >&2
    exit 6
fi

WORK="/tmp/oqa_st_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
AUD="$CAMP/audit_20261118"
ST="$CAMP/status_20261118"                # a separately labelled directory; nothing else is written
mkdir -p "$ST" || exit 3

( cd "$B" && sha256sum --strict -c MANIFEST.sha256 ) > "$OUTDIR/manifest_check.txt" 2>&1 \
    || { log "ERROR: bundle manifest mismatch"; cat "$OUTDIR/manifest_check.txt" >> "$OUTDIR/job.log"; exit 3; }
COMMIT="$(cat "$B/COMMIT")"
export OQA_COMMIT="$COMMIT"
log "manifest ok; commit $COMMIT"
tar -xzf "$B"/oqa_tree_*.tar.gz -C "$WORK" || die "extract failed"
log "extracted $(find "$WORK" -type f | wc -l) files to node-local storage"

# NO KEY REACHES PYTHON IN EITHER PHASE.
unset TARGET_OPENAI_COMPATIBLE_API_KEY OPTIMIZER_OPENAI_COMPATIBLE_API_KEY \
      EMPIRIOLABS_API_KEY DEEPSEEK_API_KEY OPENAI_API_KEY AZURE_OPENAI_API_KEY \
      HF_TOKEN HUGGING_FACE_HUB_TOKEN
log "every API key dropped: this job cannot spend"

status=0
cd "$WORK" || exit 1
export PYTHONPATH="$WORK"

if [ "$PHASE" = "tests" ]; then
    "$OQA_PY" -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -5
    status=${PIPESTATUS[0]}
    log "phase tests finished with exit $status"
    rm -rf "$WORK"
    exit "$status"
fi

FROZEN="$WORK/docs/research/OFFICEQA_FROZEN_SETS_V3.json"
[ -f "$FROZEN" ] || die "no frozen split record at $FROZEN"

log "1/3 the corrected-client trace inventory, verified from each run's own call log"
BINDARG=()
[ -f "$AUD/BIND_AUDIT.json" ] && BINDARG+=(--bind-audit "$AUD/BIND_AUDIT.json")
MANARG=()
[ -f "$AUD/S0_AUDIT_MANIFEST.json" ] && MANARG+=(--s0-manifest "$AUD/S0_AUDIT_MANIFEST.json")
"$OQA_PY" -I research/officeqa/oqa_status.py inventory \
    --campaign "$CAMP" --frozen-sets "$FROZEN" "${BINDARG[@]}" "${MANARG[@]}" \
    --out "$ST" 2>&1 | tee "$OUTDIR/s1_inventory.txt" || status=$?

log "2/3 the six status statements, decided against the records"
"$OQA_PY" -I research/officeqa/oqa_status.py statements \
    --campaign "$CAMP" --inventory "$ST/STATUS_INVENTORY.json" \
    --out "$ST" 2>&1 | tee "$OUTDIR/s2_statements.txt" || status=$?

log "3/3 the feedback chain for the historical unit-conversion error (arm C, epoch 3)"
# `feedback` imports skillopt.gradient.reflect to re-render the analyst view. That is a RECONSTRUCTION:
# the campaign saved no analyst prompt and no minibatch composition, and the tool labels it so.
"$OQA_PY" -I research/officeqa/oqa_status.py feedback \
    --campaign "$CAMP" --item UID0014 --arm C --epoch 3 \
    --out "$ST" 2>&1 | tee "$OUTDIR/s3_feedback.txt" || status=$?

log "the status phase wrote:"
for f in "$ST"/*.json "$ST"/*.txt; do [ -f "$f" ] && log "  $(stat -c%s "$f") $f"; done

rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR and $ST"
exit "$status"
