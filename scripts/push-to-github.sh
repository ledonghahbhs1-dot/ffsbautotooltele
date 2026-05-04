#!/bin/bash
# Auto-push to GitHub after every commit.
# Requires GITHUB_PERSONAL_ACCESS_TOKEN to be set in the environment.
# The token is injected into the remote URL at push time and never written to disk.

set -e

if [ -z "$GITHUB_PERSONAL_ACCESS_TOKEN" ]; then
    echo "⚠️  GITHUB_PERSONAL_ACCESS_TOKEN is not set — skipping auto-push to GitHub."
    exit 0
fi

REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)

if [ -z "$REMOTE_URL" ]; then
    echo "⚠️  No 'origin' remote configured — skipping auto-push."
    exit 0
fi

# Strip any existing credentials from the URL and inject the token
CLEAN_URL=$(echo "$REMOTE_URL" | sed 's|https://[^@]*@|https://|')
AUTH_URL=$(echo "$CLEAN_URL" | sed "s|https://|https://x-access-token:${GITHUB_PERSONAL_ACCESS_TOKEN}@|")

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "main")

echo "🔄 Auto-pushing branch '$BRANCH' to GitHub..."
git push "$AUTH_URL" "HEAD:refs/heads/$BRANCH" --quiet && echo "✅ Pushed to GitHub successfully." || echo "❌ Push to GitHub failed."
