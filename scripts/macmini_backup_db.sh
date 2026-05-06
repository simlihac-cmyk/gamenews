#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT_FILE="$BACKUP_DIR/nintendowatch-$TIMESTAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

cd "$ROOT_DIR"
docker compose -f docker-compose.prod.yml exec -T db pg_dump \
  -U nintendowatch \
  -d nintendowatch \
  | gzip > "$OUTPUT_FILE"

find "$BACKUP_DIR" -name 'nintendowatch-*.sql.gz' -type f -mtime +"$RETENTION_DAYS" -delete

echo "[gamenews] database backup written: $OUTPUT_FILE"
