#!/bin/sh
set -eu

deploy_directory="${DEALFLOW_RADAR_DEPLOY_DIRECTORY:-/opt/dealflow-radar/current}"
environment_file="${DEALFLOW_RADAR_ENV_FILE:-/opt/dealflow-radar/shared/single-host.env}"
docker_bin="${DOCKER_BIN:-docker}"
curl_bin="${CURL_BIN:-curl}"
df_bin="${DF_BIN:-df}"

test -f "${deploy_directory}/compose.production.yml" || {
  echo "production compose file is missing" >&2
  exit 1
}
test -f "${deploy_directory}/deploy/compose.single-host.yml" || {
  echo "single-host compose file is missing" >&2
  exit 1
}
test -f "${environment_file}" || {
  echo "single-host environment file is missing" >&2
  exit 1
}

read_environment_value() {
  key="$1"
  awk -F= -v key="${key}" '
    $1 == key {
      sub(/^[^=]*=/, "")
      value = $0
    }
    END { print value }
  ' "${environment_file}"
}

app_public_origin="$(read_environment_value APP_PUBLIC_ORIGIN)"
on_demand_research_enabled="$(read_environment_value ON_DEMAND_RESEARCH_ENABLED)"
: "${app_public_origin:?APP_PUBLIC_ORIGIN is required}"
on_demand_research_enabled="${on_demand_research_enabled:-false}"
case "${on_demand_research_enabled}" in
  true | false) ;;
  *)
    echo "ON_DEMAND_RESEARCH_ENABLED must be true or false" >&2
    exit 1
    ;;
esac

compose() {
  "${docker_bin}" compose \
    --profile research \
    --env-file "${environment_file}" \
    -f "${deploy_directory}/compose.production.yml" \
    -f "${deploy_directory}/deploy/compose.single-host.yml" \
    "$@"
}

running_services="$(compose ps --status running --services)"
required_services="database api frontend proxy"
if [ "${on_demand_research_enabled}" = "true" ]; then
  required_services="${required_services} on-demand-research-worker"
fi

for service in ${required_services}; do
  printf '%s\n' "${running_services}" | grep -Fx "${service}" >/dev/null || {
    echo "required service is not running: ${service}" >&2
    exit 1
  }
  container_id="$(compose ps -q "${service}")"
  test -n "${container_id}" || {
    echo "container id is missing: ${service}" >&2
    exit 1
  }
  container_state="$(
    "${docker_bin}" inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "${container_id}"
  )"
  case "${container_state}" in
    healthy | running) ;;
    *)
      echo "service is not healthy: ${service}" >&2
      exit 1
      ;;
  esac
done

"${curl_bin}" \
  --fail \
  --silent \
  --show-error \
  --connect-timeout 5 \
  --max-time 12 \
  --output /dev/null \
  "${app_public_origin%/}/login"

disk_percent="$(${df_bin} -P "${deploy_directory}" | awk 'NR == 2 {gsub(/%/, "", $5); print $5}')"
case "${disk_percent}" in
  "" | *[!0-9]*)
    echo "unable to read deployment disk usage" >&2
    exit 1
    ;;
esac
if [ "${disk_percent}" -ge 85 ]; then
  echo "deployment disk usage reached ${disk_percent}%" >&2
  exit 1
fi

printf 'status=healthy research_queue=%s disk_percent=%s\n' \
  "${on_demand_research_enabled}" "${disk_percent}"
