#!/bin/sh
set -eu

: "${DB_HOST:?DB_HOST is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${BEREV_APP_USER:?BEREV_APP_USER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
DB_PORT=${DB_PORT:-5432}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}
BACKUP_INTERVAL_SECONDS=${BACKUP_INTERVAL_SECONDS:-86400}

case "$BACKUP_RETENTION_DAYS" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS must be a positive integer" >&2; exit 2 ;;
esac
case "$BACKUP_INTERVAL_SECONDS" in
  ''|*[!0-9]*) echo "BACKUP_INTERVAL_SECONDS must be a positive integer" >&2; exit 2 ;;
esac
if [ "$BACKUP_RETENTION_DAYS" -lt 1 ] || [ "$BACKUP_INTERVAL_SECONDS" -lt 60 ]; then
  echo "Backup retention must be at least one day and interval at least 60 seconds" >&2
  exit 2
fi

umask 077
mkdir -p /backups

while :; do
  timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
  basename="berev-${timestamp}-$$"
  dump_final="/backups/${basename}.dump"
  dump_temporary="/backups/.${basename}.dump.tmp"
  receipts_final="/backups/${basename}.receipts.tar.gz"
  receipts_temporary="/backups/.${basename}.receipts.tar.gz.tmp"
  complete_final="/backups/${basename}.complete"
  if pg_dump \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --username "$BEREV_APP_USER" \
    --dbname "$POSTGRES_DB" \
    --format custom \
    --no-owner \
    --file "$dump_temporary"; then
    if [ ! -s "$dump_temporary" ]; then
      echo "Database backup is empty: $dump_temporary" >&2
      rm -f "$dump_temporary"
      exit 1
    fi
    if ! tar -czf "$receipts_temporary" -C /receipts .; then
      rm -f "$dump_temporary" "$receipts_temporary"
      echo "Expense receipt backup failed" >&2
      exit 1
    fi
    mv "$dump_temporary" "$dump_final"
    mv "$receipts_temporary" "$receipts_final"
    : > "${complete_final}.tmp"
    mv "${complete_final}.tmp" "$complete_final"
    echo "Database and receipt backup completed: $basename"

    if [ -n "${BACKUP_OFFSITE_TARGET:-}" ]; then
      offsite_method="${BACKUP_OFFSITE_METHOD:-rclone}"
      echo "Starting off-machine backup transfer to $BACKUP_OFFSITE_TARGET via $offsite_method..."
      case "$offsite_method" in
        rclone)
          if command -v rclone >/dev/null 2>&1; then
            rclone copy "$dump_final" "$BACKUP_OFFSITE_TARGET" ${BACKUP_OFFSITE_OPTIONS:-}
            rclone copy "$receipts_final" "$BACKUP_OFFSITE_TARGET" ${BACKUP_OFFSITE_OPTIONS:-}
            rclone copy "$complete_final" "$BACKUP_OFFSITE_TARGET" ${BACKUP_OFFSITE_OPTIONS:-}
            echo "Off-machine backup transfer completed via rclone."
          else
            echo "Warning: BACKUP_OFFSITE_TARGET is set but rclone is not installed in the container; skipping off-machine copy." >&2
          fi
          ;;
        scp)
          if command -v scp >/dev/null 2>&1; then
            scp ${BACKUP_OFFSITE_OPTIONS:-} "$dump_final" "$receipts_final" "$complete_final" "$BACKUP_OFFSITE_TARGET"
            echo "Off-machine backup transfer completed via scp."
          else
            echo "Warning: BACKUP_OFFSITE_TARGET is set but scp is not installed in the container; skipping off-machine copy." >&2
          fi
          ;;
        *)
          echo "Unknown BACKUP_OFFSITE_METHOD: $offsite_method (supported: rclone, scp)" >&2
          ;;
      esac
    fi

    find /backups -type f -name 'berev-*' -mtime "+$BACKUP_RETENTION_DAYS" -exec rm -f {} +
  else
    status=$?
    rm -f "$dump_temporary" "$receipts_temporary"
    echo "Database backup failed with status $status" >&2
    exit "$status"
  fi
  sleep "$BACKUP_INTERVAL_SECONDS"
done
