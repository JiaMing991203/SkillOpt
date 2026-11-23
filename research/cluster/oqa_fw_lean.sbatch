#!/bin/bash
# Compile the PORTED FRAMEWORK's Lean file (rule OQA-FW-1). CPU only: no GPU, no model, no API key,
# no network.
#   research/submit.sh oqa_fw_lean <commit> -- --time=00:20:00
#
# Deliberately a separate job from oqa_lean.sbatch, which the frozen D-1 record points at: that script
# and the file it compiles stay byte-identical, so the earlier receipt remains reproducible.
#
# What it does, in order:
#   1. verifies the deployed snapshot is clean;
#   2. verifies research/officeqa/lean/MANIFEST.sha256, so the file compiled is the one this commit
#      records - checkable from the job's own output rather than asserted;
#   3. compiles a copy inside the run directory under a per-file timeout;
#   4. saves the version, return code, wall time, full output and a statement count, and ALSO the
#      counts of the two statement families separately, because they establish different things: the
#      `theorem`s are general properties of the stage algebra, the `example`s are the exported
#      per-column and per-tuple cases.
#
# The binary comes from OQA_LEAN_BIN in the private site settings (a cluster path: never a tracked
# file, never a log).
#
# Exit: 0 compiled with rc 0 | 3 manifest or configuration | 33 the compiler reported a failure.
#SBATCH --job-name=oqa-fw-lean
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:20:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run oqa_fw_lean
require_clean_snapshot
start_job_log
write_manifest stage=oqa_fw_lean

LEAN_DIR="research/officeqa/lean"
SRC="$LEAN_DIR/OqaFramework.lean"
[ -f "$SRC" ] || die "missing $SRC in the deployed snapshot"
[ -f "$LEAN_DIR/MANIFEST.sha256" ] || die "missing $LEAN_DIR/MANIFEST.sha256"
[ -n "${OQA_LEAN_BIN:-}" ] || die "OQA_LEAN_BIN is not set in the private site settings"
[ -x "$OQA_LEAN_BIN" ] || die "OQA_LEAN_BIN does not point at an executable"

( cd "$LEAN_DIR" && sha256sum --strict -c MANIFEST.sha256 ) > "$RUN_DIR/manifest_check.txt" 2>&1 \
    || { cat "$RUN_DIR/manifest_check.txt"; die "MANIFEST.sha256 does not match the deployed files"; }
cat "$RUN_DIR/manifest_check.txt"
DIGEST=$(sha256sum "$SRC" | cut -d' ' -f1)
log "compiling OqaFramework.lean sha256=$DIGEST"

"$OQA_LEAN_BIN" --version > "$RUN_DIR/lean_version.txt" 2>&1
log "lean version: $(cat "$RUN_DIR/lean_version.txt")"
mkdir -p "$RUN_DIR/lean"
cp "$SRC" "$RUN_DIR/lean/OqaFramework.lean"
T0=$(date +%s.%N)
# `set -e` is in force from lib.sh, and a non-zero compiler exit here would end the script BEFORE
# results.tsv is written - which is what job 66666627 did: 24 goals were disproved, the output file
# survived, and the receipt that names the digest and the counts did not. The compiler's own exit
# status is the result of this job, so it is captured rather than allowed to abort it.
set +e
( cd "$RUN_DIR/lean" && timeout "${OQA_LEAN_TIMEOUT:-900}" "$OQA_LEAN_BIN" OqaFramework.lean ) \
    > "$RUN_DIR/lean/OqaFramework.out" 2>&1
RC=$?
set -e
T1=$(date +%s.%N)
SECONDS_ELAPSED=$(awk "BEGIN{printf \"%.2f\", $T1 - $T0}")

ERRORS=$(grep -c ': error' "$RUN_DIR/lean/OqaFramework.out" || true)
WARNINGS=$(grep -c ': warning' "$RUN_DIR/lean/OqaFramework.out" || true)
THEOREMS=$(grep -c '^theorem ' "$SRC" || true)
EXAMPLES=$(grep -c '^example ' "$SRC" || true)
{
  printf 'file\tsha256\trc\tseconds\terror_lines\twarning_lines\ttheorems\texamples\n'
  printf 'OqaFramework.lean\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$DIGEST" "$RC" "$SECONDS_ELAPSED" "$ERRORS" "$WARNINGS" "$THEOREMS" "$EXAMPLES"
} > "$RUN_DIR/results.tsv"
cat "$RUN_DIR/results.tsv"
log "compiler output (first 80 lines):"
head -80 "$RUN_DIR/lean/OqaFramework.out" || true

assert_run_dir_clean
[ "$RC" = 0 ] || { log "lean reported a failure (rc=$RC); the file and its output are preserved"; exit 33; }
log "all $((THEOREMS + EXAMPLES)) statements compiled"
exit 0
