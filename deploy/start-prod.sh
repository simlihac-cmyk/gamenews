#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${GAMENEWS_APP_DIR:-/Users/sg_mac/gamenews}"
COMPOSE_FILE="${GAMENEWS_COMPOSE_FILE:-docker-compose.prod.yml}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ ! -d "$APP_DIR" ]]; then
  echo "[gamenews] missing app directory: $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "[gamenews] docker not found in PATH" >&2
  exit 1
fi

exec docker compose -f "$COMPOSE_FILE" up -d
