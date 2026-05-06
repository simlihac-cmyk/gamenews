#!/usr/bin/env bash
set -euo pipefail

REMOTE="${1:-deploy}"
BRANCH="${2:-main}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not inside a git repository." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes. Commit before pushing to deployment repo." >&2
  exit 1
fi

git push "${REMOTE}" "HEAD:${BRANCH}"

