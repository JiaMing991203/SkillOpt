#!/usr/bin/env bash
# Deploy an exact, already-pushed commit to the cluster as a read-only snapshot.
#
#   research/deploy.sh [--check] [<commit-ish>]      (default: HEAD)
#
#   --check  read-only readiness check: local preconditions, SSH, whether the
#            cluster can reach the fork, and what would be created. Changes nothing.
#
# Deploy steps (non-destructive):
#   1. refuse if the local tree has uncommitted changes to tracked files
#   2. refuse if the commit is not on origin (the research fork) yet
#   3. upload the private research/cluster/site.env to $CLUSTER_SITE_ENV
#   4. on the cluster: clone once, `git fetch`, add a detached worktree
#      $SNAPSHOT_ROOT/<sha12> pinned to that commit (reused if present)
# Never pushes, never edits tracked files on the cluster.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
SITE_ENV="$HERE/cluster/site.env"
[ -f "$SITE_ENV" ] || { echo "ERROR: $SITE_ENV missing; copy site.env.example and fill it in." >&2; exit 1; }
# shellcheck disable=SC1090
source "$SITE_ENV"

CHECK=0
if [ "${1:-}" = "--check" ]; then CHECK=1; shift; fi

cd "$REPO_ROOT"
REV="${1:-HEAD}"
SHA=$(git rev-parse --verify "$REV^{commit}")
SHORT=${SHA:0:12}
fail=0

if [ "$REV" = "HEAD" ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "FAIL local tree has uncommitted changes to tracked files (the cluster only sees commits)" >&2
    fail=1
else
    echo "ok   local tree clean for tracked files"
fi

git fetch --quiet origin
if git branch -r --contains "$SHA" | grep -q "origin/"; then
    echo "ok   $SHORT is on origin"
else
    echo "FAIL $SHORT is not on origin; push it (with approval) first" >&2
    fail=1
fi

# The fork is public; clone over HTTPS so the cluster needs no GitHub key.
ORIGIN_URL=$(git remote get-url origin)
CLONE_URL=$(printf '%s' "$ORIGIN_URL" | sed -E 's#^git@github.com:#https://github.com/#')

if [ "$CHECK" = 1 ]; then
    # shellcheck disable=SC2087
    ssh "$SSH_HOST" bash -s <<EOF || fail=1
set -u
ok()   { echo "ok   \$*"; }
bad()  { echo "FAIL \$*"; rc=1; }
info() { echo "info \$*"; }
rc=0
if refs=\$(git ls-remote "$CLONE_URL" 2>/dev/null); then
    ok "cluster can read $CLONE_URL without credentials"
    echo "\$refs" | grep -q "^$SHA" && ok "$SHORT is a branch tip on the fork" || info "$SHORT is not a branch tip (fine if it is an ancestor)"
else
    bad "cluster cannot read $CLONE_URL"
fi
[ -d "$REPO_DIR/.git" ] && info "deployment clone exists: $REPO_DIR" || info "would clone into $REPO_DIR"
[ -d "$SNAPSHOT_ROOT/$SHORT" ] && info "snapshot exists: $SNAPSHOT_ROOT/$SHORT" || info "would create snapshot $SNAPSHOT_ROOT/$SHORT"
[ -f "$CLUSTER_SITE_ENV" ] && info "cluster site.env present (would be replaced by local copy)" || info "would upload site.env to $CLUSTER_SITE_ENV"
[ -f "$SECRETS_FILE" ] && ok "secrets file present (perms \$(stat -c %a "$SECRETS_FILE"))" || info "secrets file not created yet: $SECRETS_FILE"
[ -d "$(dirname "$PROJECT_ROOT")" ] && [ -w "$(dirname "$PROJECT_ROOT")" ] && ok "project parent writable: $(dirname "$PROJECT_ROOT")" || bad "cannot write $(dirname "$PROJECT_ROOT")"
[ -x "$CONDA_BASE/bin/conda" ] && ok "conda found: $CONDA_BASE" || bad "conda not found at $CONDA_BASE"
[ -e "$CTL_ENV" ] && info "controller env exists (setup_env will not modify it): $CTL_ENV" || info "controller env not created yet: $CTL_ENV"
[ -e "$VLLM_ENV" ] && info "vLLM env exists (setup_env will not modify it): $VLLM_ENV" || info "vLLM env not created yet: $VLLM_ENV"
sinfo -h -p "$GPU_PARTITION" -o %P >/dev/null 2>&1 && ok "GPU partition $GPU_PARTITION visible" || bad "GPU partition $GPU_PARTITION not found"
sinfo -h -p "$CPU_PARTITION" -o %P >/dev/null 2>&1 && ok "CPU partition $CPU_PARTITION visible" || bad "CPU partition $CPU_PARTITION not found"
sacctmgr -nP show assoc user=\$USER format=account | grep -qx "$SLURM_ACCOUNT" && ok "account $SLURM_ACCOUNT associated" || bad "account $SLURM_ACCOUNT not associated"
exit \$rc
EOF
    [ "$fail" = 0 ] && echo "READY to deploy $SHORT" || echo "NOT READY (see FAIL lines)"
    exit "$fail"
fi

[ "$fail" = 0 ] || exit 1

echo "Uploading private site.env to $SSH_HOST:$CLUSTER_SITE_ENV"
ssh "$SSH_HOST" "mkdir -p '$(dirname "$CLUSTER_SITE_ENV")' && chmod 700 '$(dirname "$CLUSTER_SITE_ENV")' && umask 077 && cat > '$CLUSTER_SITE_ENV'" < "$SITE_ENV"

echo "Deploying $SHORT to $SSH_HOST:$SNAPSHOT_ROOT/$SHORT"
# shellcheck disable=SC2087
ssh "$SSH_HOST" bash -s <<EOF
set -euo pipefail
mkdir -p "$PROJECT_ROOT" "$SNAPSHOT_ROOT" "$RUN_ROOT" "$PROJECT_ROOT/slurm_logs"
if [ ! -d "$REPO_DIR/.git" ]; then
    git clone --no-checkout "$CLONE_URL" "$REPO_DIR"
fi
git -C "$REPO_DIR" fetch --quiet origin
git -C "$REPO_DIR" cat-file -e "$SHA^{commit}"
if [ -d "$SNAPSHOT_ROOT/$SHORT" ]; then
    echo "snapshot exists: $SNAPSHOT_ROOT/$SHORT"
else
    git -C "$REPO_DIR" worktree add --detach "$SNAPSHOT_ROOT/$SHORT" "$SHA"
fi
test "\$(git -C "$SNAPSHOT_ROOT/$SHORT" rev-parse HEAD)" = "$SHA"
test -z "\$(git -C "$SNAPSHOT_ROOT/$SHORT" status --porcelain --untracked-files=no)"
echo "OK $SNAPSHOT_ROOT/$SHORT @ $SHA"
EOF
