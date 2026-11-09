#!/bin/sh
set -eu

: "${DB_HOST:?DB_HOST is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_ADMIN_USER:?POSTGRES_ADMIN_USER is required}"
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"
: "${BEREV_APP_USER:?BEREV_APP_USER is required}"
: "${BEREV_APP_PASSWORD:?BEREV_APP_PASSWORD is required}"
DB_PORT=${DB_PORT:-5432}

latest_marker=$(printf '%s\n' /backups/berev-*.complete | sort | tail -n 1)
if [ ! -f "$latest_marker" ]; then
  echo "No complete BEREV backup set found in /backups" >&2
  exit 1
fi
backup_base=${latest_marker%.complete}
database_backup="${backup_base}.dump"
receipts_backup="${backup_base}.receipts.tar.gz"
if [ ! -s "$database_backup" ] || [ ! -s "$receipts_backup" ]; then
  echo "The latest complete backup marker has a missing or empty data file" >&2
  exit 1
fi

case "$POSTGRES_DB" in
  ''|*[!a-zA-Z0-9_]*) echo "POSTGRES_DB must contain only letters, digits, or underscores" >&2; exit 2 ;;
esac

export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
printf 'Restoring %s into isolated database %s\n' "$database_backup" "$POSTGRES_DB"
tar -tzf "$receipts_backup" > /dev/null
dropdb \
  --host "$DB_HOST" \
  --port "$DB_PORT" \
  --username "$POSTGRES_ADMIN_USER" \
  --maintenance-db postgres \
  --if-exists \
  "$POSTGRES_DB"
createdb \
  --host "$DB_HOST" \
  --port "$DB_PORT" \
  --username "$POSTGRES_ADMIN_USER" \
  --maintenance-db postgres \
  --owner "$BEREV_APP_USER" \
  --template template0 \
  "$POSTGRES_DB"
pg_restore \
  --host "$DB_HOST" \
  --port "$DB_PORT" \
  --username "$POSTGRES_ADMIN_USER" \
  --dbname "$POSTGRES_DB" \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  --exit-on-error \
  "$database_backup"

psql \
  --host "$DB_HOST" \
  --port "$DB_PORT" \
  --username "$POSTGRES_ADMIN_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=app_user="$BEREV_APP_USER" <<'SQL'
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
SQL

export PGPASSWORD="$BEREV_APP_PASSWORD"
table_count=$(psql \
  --host "$DB_HOST" \
  --port "$DB_PORT" \
  --username "$BEREV_APP_USER" \
  --dbname "$POSTGRES_DB" \
  --no-align \
  --tuples-only \
  --command "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
case "$table_count" in
  ''|*[!0-9]*) echo "Could not verify restored table count: $table_count" >&2; exit 1 ;;
esac
if [ "$table_count" -lt 1 ]; then
  echo "Restore completed but no public tables were found" >&2
  exit 1
fi

version_count=$(psql \
  --host "$DB_HOST" \
  --port "$DB_PORT" \
  --username "$BEREV_APP_USER" \
  --dbname "$POSTGRES_DB" \
  --no-align \
  --tuples-only \
  --command "SELECT count(*) FROM alembic_version")
case "$version_count" in
  ''|*[!0-9]*) echo "Could not verify restored Alembic version: $version_count" >&2; exit 1 ;;
esac
if [ "$version_count" -lt 1 ]; then
  echo "Restore completed but no Alembic migration version was found" >&2
  exit 1
fi

echo "Database restore passed: $table_count public tables and $version_count migration version row(s)"
echo "Run the restore-check service to verify application preflight and accounting integrity."
