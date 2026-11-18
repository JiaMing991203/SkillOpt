#!/bin/bash
# THE OFFLINE AUDIT of the latest OfficeQA recovery report. CPU only, ZERO API calls, zero GPUs.
#
#   sbatch --wrap="bash <bundle>/oqa_aud_body.sh" --chdir=<bundle> \
#          --export=ALL,OQA_BUNDLE=<bundle>,PHASE=<phase>
#
#   PHASE=audit   free. Workstream A (one manifest per intended episode, recomputed summaries, the
#                 discrepancy table, the input match) and Workstream B (the header-binding causes, the
#                 interpretation checks), then the repaired connected contract re-frozen as v4 and
#                 re-run beside v3, the comparison, and Lean.
#   PHASE=tests   free. The whole test suite.
#
# EVERY API KEY IS DROPPED BEFORE PYTHON STARTS, in both phases. This job cannot spend a cent: there is
# no key in the environment and no request path is exercised. The spend ledger is READ, never written.
#
# WHAT IS PRESERVED. Nothing under followup_20261118/ or followup_20261119/ is written. The audit's own
# outputs go to $CAMP/audit_20261118/, and the re-frozen bundle and re-run checks go to
# $CAMP/audit_20261118/connected_contract/ - the v3 artifacts stay where they are, byte for byte.
set -uo pipefail
B="${OQA_BUNDLE:?OQA_BUNDLE must be set}"
PHASE="${PHASE:?set PHASE=audit|tests}"
case "$PHASE" in audit|tests) ;; *) echo "unknown PHASE=$PHASE" >&2; exit 2 ;; esac

if [ "${SLURM_RESTART_COUNT:-0}" != "0" ]; then
    echo "REFUSING: SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT}" >&2
    exit 6
fi

WORK="/tmp/oqa_aud_${SLURM_JOB_ID:-$$}"
OUTDIR="$B/run_${SLURM_JOB_ID:-nojob}_${PHASE}"
mkdir -p "$WORK" "$OUTDIR" || exit 3
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$OUTDIR/job.log"; }
die() { log "FATAL: $*"; exit 1; }
log "start job=${SLURM_JOB_ID:-none} phase=$PHASE host=$(hostname -s)"

set -a; . "$B/settings.env"; set +a
: "${OQA_PY:?settings.env must define OQA_PY}"
: "${OQA_CAMPAIGN_ROOT:?}"
CAMP="$OQA_CAMPAIGN_ROOT"
FU19="$CAMP/followup_20261119"
FU18="$CAMP/followup_20261118"
AUD="$CAMP/audit_20261118"                # a separately labelled directory; nothing else is written
mkdir -p "$AUD/connected_contract" || exit 3

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
export OQA_TREE="$WORK"

if [ "$PHASE" = "tests" ]; then
    "$OQA_PY" -m pytest -q tests/ 2>&1 | tee "$OUTDIR/pytest.txt" | tail -5
    status=${PIPESTATUS[0]}
    log "phase tests finished with exit $status"
    rm -rf "$WORK"
    exit "$status"
fi

S0R="$FU19/s0_repeat"
[ -d "$S0R" ] || die "no S0 passes at $S0R"
# The campaign's own call log, for the generation payload AS SENT. The gate's val_baseline stage (job
# 66272737) has NO call log - the variable was introduced in the second chain - so the comparison uses a
# later job of the SAME config and code path, and the report says so.
PROJ="$(dirname "$(dirname "$CAMP")")"
CAMP_CALLS="$(ls -d "$PROJ"/oqa_campaign_20261117b/run_*arm_A_e2 2>/dev/null | head -1)/actor_calls.jsonl"
log "campaign call log for the payload comparison: $CAMP_CALLS"

# ───────────────── Workstream A ─────────────────
log "A 1/4 one manifest per intended (pass, question)"
PASSARGS=()
for i in 1 2 3 4 5 6 7 8; do
    [ -f "$S0R/p$i/results.jsonl" ] && PASSARGS+=(--pass "p$i=$S0R/p$i")
done
# The job windows and the passes each job declares, transcribed from the jobs' own logs. The two S0 jobs
# ran CONCURRENTLY, so the clock alone cannot attribute a pass; the declaration does, and the clock then
# separates the two jobs that shared pass 8.
"$OQA_PY" -I research/officeqa/oqa_audit_s0.py manifest "${PASSARGS[@]}" \
    --intended docs/research/OFFICEQA_FROZEN_SETS_V3.json --split val \
    --job '66346539=2026-11-17T22:52:35-05:00,2026-11-18T00:02:19-05:00,p1|p2|p3|p4' \
    --job '66346540=2026-11-17T22:52:35-05:00,2026-11-18T00:12:35-05:00,p5|p6|p7|p8' \
    --job '66351468=2026-11-18T00:14:04-05:00,2026-11-18T00:32:32-05:00,p8' \
    --ledger "$CAMP/ledger" --out "$AUD" 2>&1 | tee "$OUTDIR/a1_manifest.txt" || status=$?

log "A 2/4 every summary recomputed from that manifest, with the assertions"
"$OQA_PY" -I research/officeqa/oqa_audit_s0.py summaries \
    --manifest "$AUD/S0_AUDIT_MANIFEST.json" --out "$AUD" 2>&1 \
    | tee "$OUTDIR/a2_summaries.txt" || status=$?

log "A 3/4 the discrepancy table, and the truncated-source demonstration"
"$OQA_PY" -I research/officeqa/oqa_audit_s0.py discrepancies \
    --manifest "$AUD/S0_AUDIT_MANIFEST.json" --summary "$AUD/S0_AUDIT_SUMMARY.json" \
    --truncate p8=11 --out "$AUD" 2>&1 | tee "$OUTDIR/a3_discrepancies.txt" || status=$?

log "A 4/4 the input match: the rendered prompts, not the file digests"
"$OQA_PY" -I research/officeqa/oqa_audit_s0.py inputs "${PASSARGS[@]}" \
    --baseline "$CAMP/shared/val_baseline/results.jsonl" \
    --skill "heldout_S0_file=$CAMP/heldout/S0.md" \
    --skill "arm_A_skill_v0000=$CAMP/arm_A/skills/skill_v0000.md" \
    --calls "new_p1=$S0R/p1/actor_calls.jsonl" \
    --calls "new_p8=$S0R/p8/actor_calls.jsonl" \
    --calls "campaign_arm_A_e2=$CAMP_CALLS" \
    --out "$AUD" 2>&1 | tee "$OUTDIR/a4_inputs.txt" || status=$?

# ───────────────── Workstream B ─────────────────
CC18="$FU18/connected_contract"
CC="$AUD/connected_contract"
log "B 1/6 the header-binding causes over the reserved records and the new episodes"
RECARGS=(--records "checking=$CC18/CC_RECORDS_v2.json" --records "learning=$CC18/CC_RECORDS_v2.json")
[ -f "$FU19/connected_contract/CC_RECORDS_episodes.json" ] \
    && RECARGS+=(--records "descriptive=$FU19/connected_contract/CC_RECORDS_episodes.json")
"$OQA_PY" -I research/officeqa/oqa_audit_bind.py audit \
    --model "$CC18/CC_MODEL_v1.json" "${RECARGS[@]}" --out "$AUD" 2>&1 \
    | tee "$OUTDIR/b1_bind_audit.txt" || status=$?

log "B 2/6 the interpretation checks"
"$OQA_PY" -I research/officeqa/oqa_audit_bind.py interpret \
    --audit "$AUD/BIND_AUDIT.json" --out "$AUD" 2>&1 \
    | tee "$OUTDIR/b2_interpret.txt" || status=$?

# THE REPAIRED SEMANTICS ARE FROZEN UNDER THEIR OWN LABEL BEFORE ANY RECORD IS RE-READ.
log "B 3/6 freezing the repaired semantics as v4; v3 stays where it is"
"$OQA_PY" -I research/officeqa/oqa_cc_bundle.py freeze \
    --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
    --prompt "$CC18/CC_PROMPT.txt" --out "$CC" --label v4 \
    2>&1 | tee "$OUTDIR/b3_bundle_v4.txt" || status=$?
"$OQA_PY" -I research/officeqa/oqa_cc_bundle.py verify \
    --bundle "$FU19/connected_contract/CC_BUNDLE_v3.json" 2>&1 \
    | tee "$OUTDIR/b3_verify_v3.txt" || log "NOTE: v3 reports changed components (expected: B-3)"

log "B 4/6 re-scoring and prefix-only re-checking under the repaired extraction"
for WHICH in learning checking; do
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
        --model "$CC18/CC_MODEL_v1.json" --records "$CC18/CC_RECORDS_v2.json" \
        --which "$WHICH" --out "$CC" 2>&1 | tee "$OUTDIR/b4_cc_$WHICH.txt" || status=$?
done
if [ -f "$FU19/connected_contract/CC_RECORDS_episodes.json" ]; then
    "$OQA_PY" -I research/officeqa/oqa_cc_run.py check \
        --model "$CC18/CC_MODEL_v1.json" \
        --records "$FU19/connected_contract/CC_RECORDS_episodes.json" \
        --which descriptive --out "$CC" 2>&1 | tee "$OUTDIR/b4_cc_descriptive.txt" || status=$?
fi

log "B 5/6 the comparison against each record's own arm, with the witnesses"
"$OQA_PY" -I research/officeqa/oqa_cc_run.py compare \
    --campaign "$CAMP" --check "$CC/CC_CHECK_checking.json" \
    --records "$CC18/CC_RECORDS_v2.json" --out "$CC" 2>&1 \
    | tee "$OUTDIR/b5_cc_compare.txt" || status=$?
"$OQA_PY" -I research/officeqa/oqa_cc_run.py repair \
    --model "$CC18/CC_MODEL_v1.json" --check "$CC/CC_CHECK_checking.json" \
    --records "$CC18/CC_RECORDS_v2.json" --out "$CC" 2>&1 \
    | tee "$OUTDIR/b5_cc_repair.txt" || status=$?

log "B 6/6 Lean over the changed consequences"
LEAN_BIN="${OQA_LEAN_BIN:-/nfs/turbo/umms-csriram/yaojm/lean/elan/toolchains/leanprover--lean4---v4.20.0/bin/lean}"
"$OQA_PY" -I research/officeqa/oqa_cc_lean.py --check "$CC/CC_CHECK_checking.json" \
    --out "$CC" --lean-bin "$LEAN_BIN" 2>&1 | tee "$OUTDIR/b6_cc_lean.txt" || status=$?

log "the audit wrote:"
ls -l "$AUD" "$CC" 2>&1 | tee -a "$OUTDIR/job.log" >/dev/null
for f in "$AUD"/*.json "$CC"/*.json; do [ -f "$f" ] && log "  $(stat -c%s "$f") $f"; done

rm -rf "$WORK"
log "phase $PHASE finished with exit $status; artifacts in $OUTDIR and $AUD"
exit "$status"
