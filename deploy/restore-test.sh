#!/bin/sh
set -eu

: "${RESTORE_TEST_DATABASE_URL:?RESTORE_TEST_DATABASE_URL is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"

case "${BACKUP_FILE}" in
  */* | .* | *..*)
    echo "BACKUP_FILE must be a plain file name inside /backups" >&2
    exit 2
    ;;
esac

backup_path="/backups/${BACKUP_FILE}"
checksum_path="${backup_path}.sha256"
test -f "${backup_path}"
test -f "${checksum_path}"

database_name="$(psql "${RESTORE_TEST_DATABASE_URL}" -Atqc 'SELECT current_database()')"
case "${database_name}" in
  *_restore_test) ;;
  *)
    echo "restore target database name must end with _restore_test" >&2
    exit 2
    ;;
esac

(
  cd /backups
  sha256sum --check "${BACKUP_FILE}.sha256"
)
pg_restore --list "${backup_path}" >/dev/null
psql "${RESTORE_TEST_DATABASE_URL}" -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA IF EXISTS public CASCADE' \
  -c 'CREATE SCHEMA public'
pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --dbname="${RESTORE_TEST_DATABASE_URL}" \
  "${backup_path}"

alembic_version="$(psql "${RESTORE_TEST_DATABASE_URL}" -Atqc 'SELECT version_num FROM alembic_version')"
printf '{"status":"restored","database":"%s","alembic_version":"%s"}\n' \
  "${database_name}" "${alembic_version}"
