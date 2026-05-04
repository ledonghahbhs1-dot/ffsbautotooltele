#!/bin/bash
set -e

cd "$(dirname "$0")/.."

pip install --quiet -r bot/requirements.txt 2>&1 | tail -5
echo "post-merge setup done ✅"
