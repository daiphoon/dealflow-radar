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

backup_dir="${BACKUP_DIR:-/backups}"
backup_path="${backup_dir}/${BACKUP_FILE}"
checksum_path="${backup_path}.sha256"
test -f "${backup_path}"
test -f "${checksum_path}"
restore_path="${backup_path}"
temporary=""
encrypted=false

cleanup() {
  if [ -n "${temporary}" ]; then
    rm -f "${temporary}"
  fi
}
trap cleanup EXIT HUP INT TERM

case "${BACKUP_FILE}" in
  *.dump.age)
    encrypted=true
    : "${BACKUP_AGE_IDENTITY_FILE:?BACKUP_AGE_IDENTITY_FILE is required for encrypted backup}"
    case "${BACKUP_AGE_IDENTITY_FILE}" in
      /run/secrets/*) ;;
      *)
        echo "BACKUP_AGE_IDENTITY_FILE must be mounted below /run/secrets" >&2
        exit 2
        ;;
    esac
    test -r "${BACKUP_AGE_IDENTITY_FILE}"
    test -f "${backup_path}.plain.sha256"
    command -v age >/dev/null 2>&1 || {
      echo "age is required to restore an encrypted backup" >&2
      exit 2
    }
    expected_plain_sha256="$(cat "${backup_path}.plain.sha256")"
    if [ "${#expected_plain_sha256}" -ne 64 ]; then
      echo "invalid plaintext SHA-256 metadata" >&2
      exit 2
    fi
    case "${expected_plain_sha256}" in
      *[!0-9a-f]*)
        echo "invalid plaintext SHA-256 metadata" >&2
        exit 2
        ;;
    esac
    temporary="/tmp/${BACKUP_FILE%.age}.$$"
    umask 077
    age --decrypt --identity "${BACKUP_AGE_IDENTITY_FILE}" \
      --output "${temporary}" "${backup_path}"
    actual_plain_sha256="$(sha256sum "${temporary}" | awk '{print $1}')"
    if [ "${actual_plain_sha256}" != "${expected_plain_sha256}" ]; then
      echo "decrypted backup SHA-256 does not match metadata" >&2
      exit 2
    fi
    restore_path="${temporary}"
    ;;
  *.dump) ;;
  *)
    echo "BACKUP_FILE must end with .dump or .dump.age" >&2
    exit 2
    ;;
esac

database_name="$(psql "${RESTORE_TEST_DATABASE_URL}" -Atqc 'SELECT current_database()')"
case "${database_name}" in
  *_restore_test) ;;
  *)
    echo "restore target database name must end with _restore_test" >&2
    exit 2
    ;;
esac

(
  cd "${backup_dir}"
  sha256sum --check "${BACKUP_FILE}.sha256"
)
pg_restore --list "${restore_path}" >/dev/null
psql "${RESTORE_TEST_DATABASE_URL}" -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA IF EXISTS public CASCADE' \
  -c 'CREATE SCHEMA public'
pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --dbname="${RESTORE_TEST_DATABASE_URL}" \
  "${restore_path}"

alembic_version="$(psql "${RESTORE_TEST_DATABASE_URL}" -Atqc 'SELECT version_num FROM alembic_version')"
printf '{"status":"restored","encrypted":%s,"database":"%s","alembic_version":"%s"}\n' \
  "${encrypted}" "${database_name}" "${alembic_version}"
