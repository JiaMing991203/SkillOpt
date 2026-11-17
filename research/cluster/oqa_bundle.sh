#!/bin/bash
# Build and upload a staging bundle for an OfficeQA cluster job, from a committed revision.
#
#   research/cluster/oqa_bundle.sh <bundle-name> <body-script> [commit]
#
# WHY A BUNDLE AND NOT research/deploy.sh. deploy.sh refuses a commit that is not on `origin`, and pushing this
# branch has not been authorized. A bundle carries the exact tree of a local commit instead, with a sha256
# manifest the job verifies before it runs, so the deployed revision is still pinned and checkable.
#
# What it writes into $PROJECT_ROOT/<bundle-name>/ on the cluster:
#   oqa_tree_<sha>.tar.gz   `git archive` of the commit - tracked files only, so no run output and no secret
#   COMMIT                  the full sha
#   MANIFEST.sha256         covers the tarball and the body script; settings.env is deliberately NOT listed,
#                           because it is written separately and would make the manifest fail
#   <body-script>           readable before extraction, which is why it is uploaded on its own
#
# settings.env is NOT written here. It holds machine paths and is private to the cluster; write it there once.
set -euo pipefail
NAME="${1:?usage: oqa_bundle.sh <bundle-name> <body-script> [commit]}"
BODY="${2:?usage: oqa_bundle.sh <bundle-name> <body-script> [commit]}"
COMMIT="${3:-HEAD}"
[ -f "$BODY" ] || { echo "body script not found: $BODY" >&2; exit 2; }

SHA="$(git rev-parse "$COMMIT")"
SHORT="$(git rev-parse --short "$COMMIT")"
if [ -n "$(git status --porcelain)" ]; then
    echo "refusing: the working tree is dirty, so the bundle would not match any commit" >&2
    exit 2
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
TAR="oqa_tree_${SHORT}.tar.gz"
git archive --format=tar.gz -o "$STAGE/$TAR" "$SHA"
cp "$BODY" "$STAGE/$(basename "$BODY")"
printf '%s\n' "$SHA" > "$STAGE/COMMIT"
( cd "$STAGE" && shasum -a 256 "$TAR" "$(basename "$BODY")" > MANIFEST.sha256 )

DEST="$(ssh gl 'source ~/.config/lean_skillopt/site.env; printf "%s" "$PROJECT_ROOT"')/$NAME"
ssh gl "mkdir -p '$DEST'"
# Remove any earlier tree from this bundle dir. The job body extracts oqa_tree_*.tar.gz by glob, so two of them
# would make it extract the wrong revision or fail outright - and the manifest would not catch it, because a
# manifest only checks the files it lists.
ssh gl "rm -f '$DEST'/oqa_tree_*.tar.gz"
scp -q "$STAGE/$TAR" "$STAGE/$(basename "$BODY")" "$STAGE/COMMIT" "$STAGE/MANIFEST.sha256" "gl:$DEST/"
echo "bundle   $DEST"
echo "commit   $SHA"
echo "tarball  $TAR  ($(wc -c < "$STAGE/$TAR" | tr -d ' ') bytes)"
ssh gl "cd '$DEST' && sha256sum --strict -c MANIFEST.sha256"
