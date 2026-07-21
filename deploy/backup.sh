#!/bin/sh
set -eu

: "${BACKUP_DATABASE_URL:?BACKUP_DATABASE_URL is required}"
backup_dir="${BACKUP_DIR:-/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
filename="dealflow-radar-${timestamp}.dump"
temporary="${backup_dir}/.${filename}.tmp"
destination="${backup_dir}/${filename}"

umask 077
mkdir -p "${backup_dir}"
if [ -e "${destination}" ] || [ -e "${destination}.sha256" ]; then
  echo "backup destination already exists: ${filename}" >&2
  exit 2
fi
trap 'rm -f "${temporary}"' EXIT HUP INT TERM

pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="${temporary}" \
  "${BACKUP_DATABASE_URL}"
pg_restore --list "${temporary}" >/dev/null
mv "${temporary}" "${destination}"
(
  cd "${backup_dir}"
  sha256sum "${filename}" >"${filename}.sha256"
)
trap - EXIT HUP INT TERM

printf '{"status":"created","file":"%s","sha256_file":"%s.sha256"}\n' \
  "${filename}" "${filename}"
