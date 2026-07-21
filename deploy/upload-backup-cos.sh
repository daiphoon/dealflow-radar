#!/bin/sh
set -eu

: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${COSCLI_CONFIG_PATH:?COSCLI_CONFIG_PATH is required}"
: "${COS_BUCKET_ALIAS:?COS_BUCKET_ALIAS is required}"

backup_dir="${BACKUP_DIR:-./backups/deployment}"
backup_prefix="${COS_BACKUP_PREFIX:-dealflow-radar/postgres}"
coscli_bin="${COSCLI_BIN:-coscli}"

case "${BACKUP_FILE}" in
  *.dump.age) ;;
  *)
    echo "BACKUP_FILE must be a plain .dump.age file name" >&2
    exit 2
    ;;
esac
case "${BACKUP_FILE}" in
  */* | .* | *..*)
    echo "BACKUP_FILE must be a plain .dump.age file name" >&2
    exit 2
    ;;
esac
case "${COS_BUCKET_ALIAS}" in
  "" | *[!A-Za-z0-9_-]*)
    echo "COS_BUCKET_ALIAS contains unsupported characters" >&2
    exit 2
    ;;
esac
case "${backup_prefix}" in
  "" | /* | */ | *..* | *[!A-Za-z0-9/_-]*)
    echo "COS_BACKUP_PREFIX must be a safe relative object prefix" >&2
    exit 2
    ;;
esac

backup_path="${backup_dir}/${BACKUP_FILE}"
test -f "${backup_path}"
test -f "${backup_path}.sha256"
test -f "${backup_path}.plain.sha256"
test -f "${COSCLI_CONFIG_PATH}"
for protected_file in \
  "${backup_path}" \
  "${backup_path}.sha256" \
  "${backup_path}.plain.sha256" \
  "${COSCLI_CONFIG_PATH}"; do
  if [ -L "${protected_file}" ]; then
    echo "backup artifacts and COSCLI config must not be symbolic links" >&2
    exit 2
  fi
done

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

config_mode=""
if config_mode="$(stat -c '%a' "${COSCLI_CONFIG_PATH}" 2>/dev/null)"; then
  :
elif config_mode="$(stat -f '%Lp' "${COSCLI_CONFIG_PATH}" 2>/dev/null)"; then
  :
else
  echo "cannot inspect COSCLI_CONFIG_PATH permissions" >&2
  exit 2
fi
case "${config_mode}" in
  "" | *[!0-7]*)
    echo "cannot inspect COSCLI_CONFIG_PATH permissions" >&2
    exit 2
    ;;
esac
if [ $((0${config_mode} & 077)) -ne 0 ]; then
  echo "COSCLI_CONFIG_PATH must not be readable or writable by group/other" >&2
  exit 2
fi

command -v "${coscli_bin}" >/dev/null 2>&1 || {
  echo "COSCLI is not installed" >&2
  exit 2
}
(
  cd "${backup_dir}"
  sha256sum --check "${BACKUP_FILE}.sha256"
)

remote_base="cos://${COS_BUCKET_ALIAS}/${backup_prefix}"
for suffix in "" ".sha256" ".plain.sha256"; do
  "${coscli_bin}" cp \
    "${backup_path}${suffix}" \
    "${remote_base}/${BACKUP_FILE}${suffix}" \
    --config-path "${COSCLI_CONFIG_PATH}"
done

printf '{"status":"uploaded","encrypted":true,"file":"%s","destination":"%s/%s"}\n' \
  "${BACKUP_FILE}" "${remote_base}" "${BACKUP_FILE}"
