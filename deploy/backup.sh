#!/bin/sh
set -eu

: "${BACKUP_DATABASE_URL:?BACKUP_DATABASE_URL is required}"
backup_dir="${BACKUP_DIR:-/backups}"
require_encryption="${BACKUP_REQUIRE_ENCRYPTION:-false}"
age_recipient="${BACKUP_AGE_RECIPIENT:-}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
filename="dealflow-radar-${timestamp}.dump"
temporary="${backup_dir}/.${filename}.tmp"
destination="${backup_dir}/${filename}"
encrypted_temporary="${backup_dir}/.${filename}.age.tmp"
encrypted_checksum_temporary="${backup_dir}/.${filename}.age.sha256.tmp"
plain_checksum_temporary="${backup_dir}/.${filename}.age.plain.sha256.tmp"

case "${require_encryption}" in
  true | false) ;;
  *)
    echo "BACKUP_REQUIRE_ENCRYPTION must be true or false" >&2
    exit 2
    ;;
esac
if [ "${require_encryption}" = "true" ] && [ -z "${age_recipient}" ]; then
  echo "BACKUP_AGE_RECIPIENT is required when encrypted backup is mandatory" >&2
  exit 2
fi

umask 077
mkdir -p "${backup_dir}"
if [ -e "${destination}" ] || [ -e "${destination}.sha256" ] || \
  [ -e "${destination}.age" ] || [ -e "${destination}.age.sha256" ] || \
  [ -e "${destination}.age.plain.sha256" ]; then
  echo "backup destination already exists: ${filename}" >&2
  exit 2
fi
trap 'rm -f "${temporary}" "${encrypted_temporary}" "${encrypted_checksum_temporary}" "${plain_checksum_temporary}"' EXIT HUP INT TERM

pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="${temporary}" \
  "${BACKUP_DATABASE_URL}"
pg_restore --list "${temporary}" >/dev/null

if [ -n "${age_recipient}" ]; then
  command -v age >/dev/null 2>&1 || {
    echo "age is required for encrypted backups" >&2
    exit 2
  }
  plain_sha256="$(sha256sum "${temporary}" | awk '{print $1}')"
  age --encrypt --recipient "${age_recipient}" \
    --output "${encrypted_temporary}" "${temporary}"
  test -s "${encrypted_temporary}"
  encrypted_sha256="$(sha256sum "${encrypted_temporary}" | awk '{print $1}')"
  printf '%s  %s.age\n' "${encrypted_sha256}" "${filename}" \
    >"${encrypted_checksum_temporary}"
  printf '%s\n' "${plain_sha256}" >"${plain_checksum_temporary}"
  mv "${encrypted_temporary}" "${destination}.age"
  mv "${encrypted_checksum_temporary}" "${destination}.age.sha256"
  mv "${plain_checksum_temporary}" "${destination}.age.plain.sha256"
  rm -f "${temporary}"
  trap - EXIT HUP INT TERM
  printf '{"status":"created","encrypted":true,"file":"%s.age","sha256_file":"%s.age.sha256","plain_sha256_file":"%s.age.plain.sha256"}\n' \
    "${filename}" "${filename}" "${filename}"
  exit 0
fi

mv "${temporary}" "${destination}"
(
  cd "${backup_dir}"
  sha256sum "${filename}" >"${filename}.sha256"
)
trap - EXIT HUP INT TERM

printf '{"status":"created","encrypted":false,"file":"%s","sha256_file":"%s.sha256"}\n' \
  "${filename}" "${filename}"
