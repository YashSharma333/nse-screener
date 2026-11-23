#!/usr/bin/env bash
# Makes a compressed backup of the nse_screener database OUTSIDE the repository.
#
# Why: CLAUDE.md ("Working on the real database") requires a backup before the first data write.
# It only needs SELECT rights, so it uses the read-only user. That user has no
# RELOAD or LOCK TABLES right, so --single-transaction and table locks cannot be
# used; run this when nothing is writing (no pipeline run in progress).
# A message about 'column_masking_policy' may print: newer mysqldump tries to read a
# system table the read-only user cannot see. It is harmless; the data is still dumped.
# Backups live in ONE place, ~/nse-backups (outside the repo and outside iCloud-synced folders
# such as Documents or Desktop). Set BACKUP_DIR to use another folder.
# After a good dump the script checks the file and keeps only the newest KEEP_BACKUPS (default 5)
# dumps of this database; older ones in that folder are removed.
# Pressing Ctrl+C removes the half-written file, so a cut-off dump can never be mistaken for a
# real backup.
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

BACKUP_DIR="${BACKUP_DIR:-$HOME/nse-backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"
case "$KEEP_BACKUPS" in
  '' | *[!0-9]* | 0) echo "KEEP_BACKUPS must be a whole number of 1 or more" >&2; exit 1 ;;
esac
DB_NAME="${MYSQL_DB:-nse_screener}"
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/${DB_NAME}_$(date +%Y-%m-%d_%H%M%S).sql.gz"

# Remove the half-written file if the run is interrupted (Ctrl+C) or killed.
trap 'rm -f "$OUT"; echo "Backup interrupted, partial file removed" >&2; exit 1' INT TERM

# The password goes through an environment variable, so it is not shown in `ps`.
MYSQL_PWD="$MYSQL_READONLY_PASSWORD" mysqldump \
  -h "${MYSQL_HOST:-localhost}" -P "${MYSQL_PORT:-3306}" \
  -u "$MYSQL_READONLY_USER" \
  --no-tablespaces --skip-lock-tables --set-gtid-purged=OFF \
  "$DB_NAME" 2> >(grep -v "^Warning" >&2) | gzip > "$OUT" \
  || { rm -f "$OUT"; echo "Backup FAILED" >&2; exit 1; }

# A dump that is not a complete gzip file is not a backup.
gzip -t "$OUT" || { rm -f "$OUT"; echo "Backup FAILED: file is not a valid gzip" >&2; exit 1; }
trap - INT TERM

echo "Backup written to: $OUT"
ls -lh "$OUT"

# Keep only the newest KEEP_BACKUPS dumps of this database (newest first by modified time).
ls -1t "$BACKUP_DIR/${DB_NAME}"_*.sql.gz | tail -n +$((KEEP_BACKUPS + 1)) | while IFS= read -r old; do
  rm -f -- "$old"
  echo "Removed old backup: $old"
done
