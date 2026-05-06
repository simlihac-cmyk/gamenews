#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STALE_DAYS="${STALE_DAYS:-30}"
ARCHIVE_DAYS="${ARCHIVE_DAYS:-60}"
ARCHIVE_MAX_IMPORTANCE="${ARCHIVE_MAX_IMPORTANCE:-25}"

cd "${ROOT_DIR}"
docker compose -f docker-compose.prod.yml exec -T web python manage.py mark_stale_issues --days "${STALE_DAYS}"
docker compose -f docker-compose.prod.yml exec -T web python manage.py archive_low_value_items \
  --days "${ARCHIVE_DAYS}" \
  --max-importance "${ARCHIVE_MAX_IMPORTANCE}"
