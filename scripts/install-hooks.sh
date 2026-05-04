#!/bin/bash
# Installs git hooks from scripts/hooks/ into .git/hooks/
# Run this once after cloning, or it is called automatically by scripts/post-merge.sh

set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOOKS_SRC="$REPO_ROOT/scripts/hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

for hook in "$HOOKS_SRC"/*; do
    name=$(basename "$hook")
    cp "$hook" "$HOOKS_DST/$name"
    chmod +x "$HOOKS_DST/$name"
    echo "Installed git hook: $name"
done

echo "All hooks installed ✅"
