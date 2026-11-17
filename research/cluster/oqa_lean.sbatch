#!/bin/bash
# Compile the OfficeQA learned-contract Lean file. CPU only: no GPU, no model, no API key, no network.
#   research/submit.sh oqa_lean <commit> -- --time=00:15:00
#
# What it does, in order:
#   1. verifies the deployed snapshot is clean (require_clean_snapshot);
#   2. verifies research/officeqa/lean/MANIFEST.sha256, so the file compiled is byte-identical to the one the
#      commit records - "compile that exact file" has to be checkable from the job's own output, not asserted;
#   3. copies the file into the run directory and compiles it there with `lean`, under a per-file timeout;
#   4. saves the compiler's version, return code, wall time and complete output, plus a per-section statement
#      count, so the verdict can be parsed locally by research/officeqa/oqa_lc_lean.py:parse_results.
#
# The Lean binary comes from OQA_LEAN_BIN in the private site settings that load_site sources (it is a cluster
# path and must not enter a tracked file or a log). Everything printed about it is its --version string.
#
# Exit: 0 compiled with rc 0 | 3 manifest or configuration | 33 the compiler reported a failure | other = setup.
#SBATCH --job-name=oqa-lean
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out

source research/cluster/lib.sh
load_site
init_run oqa_lean
require_clean_snapshot
start_job_log
write_manifest stage=oqa_lean

LEAN_DIR="research/officeqa/lean"
SRC="$LEAN_DIR/OqaContracts.lean"
[ -f "$SRC" ] || die "missing $SRC in the deployed snapshot"
[ -f "$LEAN_DIR/MANIFEST.sha256" ] || die "missing $LEAN_DIR/MANIFEST.sha256"
[ -n "${OQA_LEAN_BIN:-}" ] || die "OQA_LEAN_BIN is not set in the private site settings"
[ -x "$OQA_LEAN_BIN" ] || die "OQA_LEAN_BIN does not point at an executable"

# 2. the file is the one the commit records
( cd "$LEAN_DIR" && sha256sum --strict -c MANIFEST.sha256 ) > "$RUN_DIR/manifest_check.txt" 2>&1 \
    || { cat "$RUN_DIR/manifest_check.txt"; die "MANIFEST.sha256 does not match the deployed file"; }
cat "$RUN_DIR/manifest_check.txt"
DIGEST=$(sha256sum "$SRC" | cut -d' ' -f1)
log "compiling OqaContracts.lean sha256=$DIGEST"

# 3. compile a copy inside the run directory
"$OQA_LEAN_BIN" --version > "$RUN_DIR/lean_version.txt" 2>&1
log "lean version: $(cat "$RUN_DIR/lean_version.txt")"
mkdir -p "$RUN_DIR/lean"
cp "$SRC" "$RUN_DIR/lean/OqaContracts.lean"
T0=$(date +%s.%N)
( cd "$RUN_DIR/lean" && timeout "${OQA_LEAN_TIMEOUT:-600}" "$OQA_LEAN_BIN" OqaContracts.lean ) \
    > "$RUN_DIR/lean/OqaContracts.out" 2>&1
RC=$?
T1=$(date +%s.%N)
SECONDS_ELAPSED=$(awk "BEGIN{printf \"%.2f\", $T1 - $T0}")

# 4. the record a local parse needs
ERRORS=$(grep -c -i 'error' "$RUN_DIR/lean/OqaContracts.out" || true)
WARNINGS=$(grep -c -i 'warning' "$RUN_DIR/lean/OqaContracts.out" || true)
GENERAL=$(awk '/^section General/,/^end General/' "$SRC" | grep -c '^example\|^theorem' || true)
BRIDGE=$(grep -c '^theorem acceptable_factors\|^theorem acceptable_after_drop_factors' "$SRC" || true)
EVIDENCE=$(awk '/^section Evidence/,/^end Evidence/' "$SRC" | grep -c '^example' || true)
{
  printf 'file\tsha256\trc\tseconds\terror_lines\twarning_lines\tgeneral\tbridge\tevidence\n'
  printf 'OqaContracts.lean\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$DIGEST" "$RC" "$SECONDS_ELAPSED" "$ERRORS" "$WARNINGS" "$GENERAL" "$BRIDGE" "$EVIDENCE"
} > "$RUN_DIR/results.tsv"
cat "$RUN_DIR/results.tsv"
log "compiler output (first 60 lines):"
head -60 "$RUN_DIR/lean/OqaContracts.out" || true

assert_run_dir_clean
[ "$RC" = 0 ] || { log "lean reported a failure (rc=$RC); the file and its output are preserved in the run dir"; exit 33; }
log "all $((GENERAL + BRIDGE + EVIDENCE)) statements compiled"
exit 0
