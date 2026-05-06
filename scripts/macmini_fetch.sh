#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FETCH_LIMIT="${FETCH_LIMIT:-30}"
NOTIFY_FLAG="${NOTIFY_FLAG:---notify}"

cd "${ROOT_DIR}"
docker compose -f docker-compose.prod.yml exec -T web python manage.py fetch_news --limit "${FETCH_LIMIT}" ${NOTIFY_FLAG}

