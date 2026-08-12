#!/bin/sh
set -eu

unit_name="${1:-}"
environment_name="${ALERT_ENVIRONMENT:-香港邀请测试}"
host_name="${ALERT_HOSTNAME:-$(hostname)}"
curl_bin="${CURL_BIN:-curl}"
python_bin="${PYTHON_BIN:-python3}"

case "${unit_name}" in
  "" | *[!A-Za-z0-9@_.:-]*)
    echo "alert unit name contains unsupported characters" >&2
    exit 2
    ;;
esac

case "${environment_name}${host_name}" in
  *'
'*)
    echo "alert labels must be single-line values" >&2
    exit 2
    ;;
esac

if [ "${#environment_name}" -gt 80 ] || [ "${#host_name}" -gt 255 ]; then
  echo "alert labels exceed the supported length" >&2
  exit 2
fi

occurred_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
message="Dealflow Radar 运维告警
环境：${environment_name}
主机：${host_name}
失败单元：${unit_name}
时间（UTC）：${occurred_at}
请检查 systemctl status 和 journalctl；通知不包含业务数据。"

command -v "${python_bin}" >/dev/null 2>&1 || {
  echo "python3 is required for the Feishu alert" >&2
  exit 2
}

payload="$(${python_bin} - "${message}" <<'PY'
import json
import sys

print(json.dumps({"msg_type": "text", "content": {"text": sys.argv[1]}}, ensure_ascii=False))
PY
)"

if [ "${OPS_ALERT_DRY_RUN:-false}" = "true" ]; then
  "${python_bin}" - "${unit_name}" "${occurred_at}" <<'PY'
import json
import sys

print(json.dumps({"status": "dry_run", "unit": sys.argv[1], "occurred_at": sys.argv[2]}))
PY
  exit 0
fi

: "${FEISHU_WEBHOOK_URL:?FEISHU_WEBHOOK_URL is required}"
case "${FEISHU_WEBHOOK_URL}" in
  https://open.feishu.cn/open-apis/bot/v2/hook/*)
    webhook_token="${FEISHU_WEBHOOK_URL#https://open.feishu.cn/open-apis/bot/v2/hook/}"
    ;;
  https://open.larksuite.com/open-apis/bot/v2/hook/*)
    webhook_token="${FEISHU_WEBHOOK_URL#https://open.larksuite.com/open-apis/bot/v2/hook/}"
    ;;
  *)
    echo "FEISHU_WEBHOOK_URL must be an official HTTPS custom-bot webhook" >&2
    exit 2
    ;;
esac
case "${webhook_token}" in
  "" | *[!A-Za-z0-9_-]*)
    echo "FEISHU_WEBHOOK_URL contains unsupported characters" >&2
    exit 2
    ;;
esac

command -v "${curl_bin}" >/dev/null 2>&1 || {
  echo "curl is required for the Feishu alert" >&2
  exit 2
}
response_file="$(mktemp)"
trap 'rm -f "${response_file}"' EXIT HUP INT TERM

"${curl_bin}" \
  --fail \
  --silent \
  --show-error \
  --proto '=https' \
  --tlsv1.2 \
  --connect-timeout 5 \
  --max-time 12 \
  --retry 1 \
  --retry-delay 2 \
  --request POST \
  --header 'Content-Type: application/json; charset=utf-8' \
  --data-binary "${payload}" \
  --output "${response_file}" \
  --config - <<EOF
url = "${FEISHU_WEBHOOK_URL}"
EOF

"${python_bin}" - "${response_file}" <<'PY'
import json
import pathlib
import sys

response = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
code = response.get("code", response.get("StatusCode"))
if code not in (0, "0"):
    raise SystemExit("Feishu webhook rejected the alert")
PY

"${python_bin}" - "${unit_name}" "${occurred_at}" <<'PY'
import json
import sys

print(json.dumps({"status": "sent", "unit": sys.argv[1], "occurred_at": sys.argv[2]}))
PY
