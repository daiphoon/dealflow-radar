from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALERT_SCRIPT = REPOSITORY_ROOT / "deploy" / "notify-feishu.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_feishu_alert_dry_run_needs_no_webhook() -> None:
    result = subprocess.run(
        ["sh", str(ALERT_SCRIPT), "dealflow-radar-health-check.service"],
        env={**os.environ, "OPS_ALERT_DRY_RUN": "true"},
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "dry_run"
    assert payload["unit"] == "dealflow-radar-health-check.service"
    assert "webhook" not in result.stdout.lower()


def test_feishu_alert_rejects_non_official_webhook(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(ALERT_SCRIPT), "dealflow-radar-backup.service"],
        env={**os.environ, "FEISHU_WEBHOOK_URL": "http://127.0.0.1/not-allowed"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "official HTTPS" in result.stderr


def test_feishu_alert_rejects_curl_config_injection() -> None:
    result = subprocess.run(
        ["sh", str(ALERT_SCRIPT), "dealflow-radar-backup.service"],
        env={
            **os.environ,
            "FEISHU_WEBHOOK_URL": (
                'https://open.feishu.cn/open-apis/bot/v2/hook/safe"\noutput = "/tmp/leak"'
            ),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "unsupported characters" in result.stderr


def test_feishu_alert_keeps_webhook_out_of_curl_arguments(tmp_path: Path) -> None:
    argument_log = tmp_path / "arguments.log"
    config_log = tmp_path / "config.log"
    fake_curl = tmp_path / "curl"
    _write_executable(
        fake_curl,
        """#!/bin/sh
set -eu
: "${FAKE_CURL_ARGUMENT_LOG:?}"
: "${FAKE_CURL_CONFIG_LOG:?}"
output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output)
      output="$2"
      printf '%s\n' "$1" "$2" >>"${FAKE_CURL_ARGUMENT_LOG}"
      shift 2
      ;;
    --config)
      test "$2" = "-"
      cat >"${FAKE_CURL_CONFIG_LOG}"
      shift 2
      ;;
    *)
      printf '%s\n' "$1" >>"${FAKE_CURL_ARGUMENT_LOG}"
      shift
      ;;
  esac
done
printf '{"code":0,"msg":"success"}' >"${output:?}"
""",
    )
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/test-secret"

    result = subprocess.run(
        ["sh", str(ALERT_SCRIPT), "dealflow-radar-backup.service"],
        env={
            **os.environ,
            "FEISHU_WEBHOOK_URL": webhook,
            "CURL_BIN": str(fake_curl),
            "FAKE_CURL_ARGUMENT_LOG": str(argument_log),
            "FAKE_CURL_CONFIG_LOG": str(config_log),
        },
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout)["status"] == "sent"
    assert webhook not in argument_log.read_text(encoding="utf-8")
    assert webhook in config_log.read_text(encoding="utf-8")
    assert webhook not in result.stdout
