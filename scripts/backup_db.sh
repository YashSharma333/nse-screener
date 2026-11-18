#!/usr/bin/env bash
# Makes a compressed backup of the nse_screener database OUTSIDE the repository.
#
# Why: CLAUDE.md section 7a requires a backup before the first data write.
# It only needs SELECT rights, so it uses the read-only user. That user has no
# RELOAD or LOCK TABLES right, so --single-transaction and table locks cannot be
# used; run this when nothing is writing (no pipeline run in progress).
# A message about 'column_masking_policy' may print: newer mysqldump tries to read a
# system table the read-only user cannot see. It is harmless; the data is still dumped.
# Usage:  bash scripts/backup_db.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${MYSQL_READONLY_USER:?Set MYSQL_READONLY_USER in .env}"
: "${MYSQL_READONLY_PASSWORD:?Set MYSQL_READONLY_PASSWORD in .env}"

BACKUP_DIR="${BACKUP_DIR:-$HOME/Documents/backups}"
DB_NAME="${MYSQL_DB:-nse_screener}"
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/${DB_NAME}_$(date +%Y-%m-%d_%H%M%S).sql.gz"

# The password goes through an environment variable, so it is not shown in `ps`.
MYSQL_PWD="$MYSQL_READONLY_PASSWORD" mysqldump \
  -h "${MYSQL_HOST:-localhost}" -P "${MYSQL_PORT:-3306}" \
  -u "$MYSQL_READONLY_USER" \
  --no-tablespaces --skip-lock-tables --set-gtid-purged=OFF \
  "$DB_NAME" 2> >(grep -v "^Warning" >&2) | gzip > "$OUT" \
  || { rm -f "$OUT"; echo "Backup FAILED" >&2; exit 1; }

echo "Backup written to: $OUT"
ls -lh "$OUT"
