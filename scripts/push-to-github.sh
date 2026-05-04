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

# Convert SSH remote (git@github.com:owner/repo.git) to HTTPS so the PAT can be used
if echo "$REMOTE_URL" | grep -q "^git@"; then
    REMOTE_URL=$(echo "$REMOTE_URL" | sed 's|git@github.com:|https://github.com/|')
fi

# Reject non-HTTPS remotes we cannot handle
if ! echo "$REMOTE_URL" | grep -q "^https://"; then
    echo "❌ Unsupported remote URL scheme: $REMOTE_URL — cannot inject PAT. Push skipped."
    exit 1
fi

# Strip any existing credentials from the URL and inject the token
CLEAN_URL=$(echo "$REMOTE_URL" | sed 's|https://[^@]*@|https://|')
AUTH_URL=$(echo "$CLEAN_URL" | sed "s|https://|https://x-access-token:${GITHUB_PERSONAL_ACCESS_TOKEN}@|")

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "main")

if [ "$BRANCH" != "main" ]; then
    echo "ℹ️  On branch '$BRANCH' (not main) — skipping auto-push."
    exit 0
fi

echo "🔄 Auto-pushing branch '$BRANCH' to GitHub..."
git push "$AUTH_URL" "HEAD:refs/heads/$BRANCH" --quiet && echo "✅ Pushed to GitHub successfully." || echo "❌ Push to GitHub failed."
