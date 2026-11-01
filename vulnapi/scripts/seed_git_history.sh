#!/bin/bash
# Seed fake git history with bug-fix commit clusters on auth.py, middleware.py, api.py
set -e

cd "$(dirname "$0")/.."

git init 2>/dev/null || true
git config user.email "dev@vulnapi.local"
git config user.name "VulnAPI Dev"

# Initial commit
git add -A
git commit -m "Initial vulnapi demo repo" --allow-empty 2>/dev/null || git commit -m "Initial vulnapi demo repo"

FILES=("vulnapi/auth.py" "vulnapi/middleware.py" "vulnapi/api.py")
MESSAGES=("fix: token validation edge case" "fix: auth middleware bypass" "fix: query parameter sanitization" "bug: handle expired tokens" "patch: admin route auth")

for i in $(seq 1 30); do
  DAY=$((90 - i * 3))
  FILE=${FILES[$((i % 3))]}
  MSG=${MESSAGES[$((i % 5))]}
  GIT_AUTHOR_DATE="$(date -v-${DAY}d +%Y-%m-%d)" GIT_COMMITTER_DATE="$(date -v-${DAY}d +%Y-%m-%d)" \
    git commit --allow-empty -m "$MSG ($FILE)" 2>/dev/null || \
    GIT_AUTHOR_DATE="$(date -d "${DAY} days ago" +%Y-%m-%d)" GIT_COMMITTER_DATE="$(date -d "${DAY} days ago" +%Y-%m-%d)" \
    git commit --allow-empty -m "$MSG ($FILE)" 2>/dev/null || true
done

echo "Seeded ~30 commits with bug-fix density on auth/middleware/api"
