#!/bin/bash
set -e

cd "$(dirname "$0")/.."

pip install --quiet -r bot/requirements.txt 2>&1 | tail -5

# Re-install git hooks from scripts/hooks/ so they survive environment resets
bash scripts/install-hooks.sh

echo "post-merge setup done ✅"
