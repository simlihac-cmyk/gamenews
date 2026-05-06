#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ROOT_DIR}/backups"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "${BACKUP_DIR}"
cd "${ROOT_DIR}"

docker compose -f docker-compose.prod.yml exec -T db pg_dump -U nintendowatch nintendowatch \
  > "${BACKUP_DIR}/nintendowatch_${STAMP}.sql"

echo "Backup written to ${BACKUP_DIR}/nintendowatch_${STAMP}.sql"

