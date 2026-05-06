#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi
EXTRA_MESSAGE="${*:-}"

DEPLOY_REMOTE="${DEPLOY_REMOTE:-deploy}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_APP_DIR="${DEPLOY_APP_DIR:-/Users/sg_mac/gamenews}"
DEPLOY_PULL_REMOTE="${DEPLOY_PULL_REMOTE:-origin}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
RELEASE_RUN_TESTS="${RELEASE_RUN_TESTS:-1}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

log() {
  printf '[release] %s\n' "$*"
}

die() {
  printf '[release] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/release_to_deploy.sh v1.0.0 [optional commit message]

Environment overrides:
  DEPLOY_REMOTE=deploy
  DEPLOY_BRANCH=main
  DEPLOY_APP_DIR=/Users/sg_mac/gamenews
  DEPLOY_PULL_REMOTE=origin
  COMPOSE_FILE=docker-compose.prod.yml
  RELEASE_RUN_TESTS=1
USAGE
}

if [[ -z "$VERSION" || "$VERSION" == "-h" || "$VERSION" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]]; then
  die "version must look like v1.0.0, v1.0.0-rc.1, or v1.0.0+build.1"
fi

command -v git >/dev/null 2>&1 || die "git not found"
command -v docker >/dev/null 2>&1 || die "docker not found"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "run this from the development git repository"
fi

DEV_ROOT="$(git rev-parse --show-toplevel)"
cd "$DEV_ROOT"

git remote get-url "$DEPLOY_REMOTE" >/dev/null 2>&1 || die "missing git remote: $DEPLOY_REMOTE"

log "fetching deployment remote $DEPLOY_REMOTE/$DEPLOY_BRANCH"
git fetch "$DEPLOY_REMOTE" "+refs/heads/$DEPLOY_BRANCH:refs/remotes/$DEPLOY_REMOTE/$DEPLOY_BRANCH" --tags

if git rev-parse --verify "refs/tags/$VERSION" >/dev/null 2>&1; then
  die "local tag already exists: $VERSION"
fi

if git ls-remote --exit-code --tags "$DEPLOY_REMOTE" "refs/tags/$VERSION" >/dev/null 2>&1; then
  die "remote tag already exists on $DEPLOY_REMOTE: $VERSION"
fi

if git rev-parse --verify "$DEPLOY_REMOTE/$DEPLOY_BRANCH" >/dev/null 2>&1; then
  if ! git merge-base --is-ancestor "$DEPLOY_REMOTE/$DEPLOY_BRANCH" HEAD; then
    die "local HEAD does not include $DEPLOY_REMOTE/$DEPLOY_BRANCH; pull/rebase before releasing"
  fi
fi

if [[ "$RELEASE_RUN_TESTS" == "1" ]]; then
  WEB_CONTAINER="$(docker compose ps -q web 2>/dev/null || true)"
  if [[ -n "$WEB_CONTAINER" ]]; then
    log "running Django tests"
    docker compose exec -T web python manage.py test
  else
    log "dev web container is not running; skipping tests. Set RELEASE_RUN_TESTS=0 to silence this path."
  fi
fi

log "staging current development changes"
git add -A

if ! git diff --cached --quiet; then
  COMMIT_MESSAGE="${EXTRA_MESSAGE:-Release $VERSION}"
  log "creating release commit: $COMMIT_MESSAGE"
  git commit -m "$COMMIT_MESSAGE"
else
  log "no local changes to commit; tagging current HEAD"
fi

log "creating git tag $VERSION"
git tag -a "$VERSION" -m "Nintendo Watch $VERSION"

log "pushing branch and tag to $DEPLOY_REMOTE"
git push "$DEPLOY_REMOTE" "HEAD:$DEPLOY_BRANCH"
git push "$DEPLOY_REMOTE" "$VERSION"

[[ -d "$DEPLOY_APP_DIR/.git" ]] || die "deployment app directory is not a git repo: $DEPLOY_APP_DIR"

log "updating deployment working tree: $DEPLOY_APP_DIR"
cd "$DEPLOY_APP_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  die "deployment working tree has uncommitted changes: $DEPLOY_APP_DIR"
fi

git fetch "$DEPLOY_PULL_REMOTE" "+refs/heads/$DEPLOY_BRANCH:refs/remotes/$DEPLOY_PULL_REMOTE/$DEPLOY_BRANCH" --tags
if [[ "$(git branch --show-current)" != "$DEPLOY_BRANCH" ]]; then
  git checkout "$DEPLOY_BRANCH"
fi
git pull --ff-only "$DEPLOY_PULL_REMOTE" "$DEPLOY_BRANCH"

log "rebuilding and starting production containers"
docker compose -f "$COMPOSE_FILE" up -d --build

log "running production migrations"
docker compose -f "$COMPOSE_FILE" exec -T web python manage.py migrate --noinput

log "collecting static files"
docker compose -f "$COMPOSE_FILE" exec -T web python manage.py collectstatic --noinput

log "release complete: $VERSION"
