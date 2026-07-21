from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPOSITORY_ROOT / "deploy" / "backup.sh"
RESTORE_SCRIPT = REPOSITORY_ROOT / "deploy" / "restore-test.sh"
UPLOAD_SCRIPT = REPOSITORY_ROOT / "deploy" / "upload-backup-cos.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _backup_environment(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "pg_dump",
        """#!/bin/sh
set -eu
for argument in "$@"; do
  case "$argument" in
    --file=*) destination="${argument#--file=}" ;;
  esac
done
printf 'deterministic postgres dump' >"${destination:?}"
""",
    )
    _write_executable(bin_dir / "pg_restore", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "age",
        """#!/bin/sh
set -eu
output=""
input=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --encrypt) shift ;;
    --recipient) shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) input="$1"; shift ;;
  esac
done
printf 'age-encrypted:' >"${output:?}"
cat "${input:?}" >>"${output}"
""",
    )
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BACKUP_DATABASE_URL": "postgresql://owner:secret@database/equity_radar",
        "BACKUP_DIR": str(tmp_path / "backups"),
        "BACKUP_REQUIRE_ENCRYPTION": "true",
        "BACKUP_AGE_RECIPIENT": "age1testrecipient",
    }


def test_backup_script_emits_only_encrypted_artifacts_when_required(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(BACKUP_SCRIPT)],
        env=_backup_environment(tmp_path),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    backup_dir = tmp_path / "backups"
    encrypted_file = backup_dir / payload["file"]

    assert payload["encrypted"] is True
    assert encrypted_file.suffix == ".age"
    assert encrypted_file.read_bytes().startswith(b"age-encrypted:")
    assert (backup_dir / payload["sha256_file"]).is_file()
    assert (backup_dir / payload["plain_sha256_file"]).is_file()
    assert not list(backup_dir.glob("*.dump"))
    assert not list(backup_dir.glob(".*.tmp"))


def test_backup_script_fails_before_dump_when_encryption_recipient_is_missing(
    tmp_path: Path,
) -> None:
    environment = _backup_environment(tmp_path)
    environment.pop("BACKUP_AGE_RECIPIENT")

    result = subprocess.run(
        ["sh", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "BACKUP_AGE_RECIPIENT" in result.stderr
    assert not (tmp_path / "backups").exists()


def test_backup_script_removes_plaintext_when_encryption_fails(tmp_path: Path) -> None:
    environment = _backup_environment(tmp_path)
    _write_executable(tmp_path / "bin" / "age", "#!/bin/sh\nexit 7\n")

    result = subprocess.run(
        ["sh", str(BACKUP_SCRIPT)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 7
    assert not list((tmp_path / "backups").iterdir())


def _create_encrypted_artifacts(backup_dir: Path) -> str:
    backup_dir.mkdir()
    filename = "dealflow-radar-20260721T000000Z.dump.age"
    payload = b"encrypted-backup"
    digest = hashlib.sha256(payload).hexdigest()
    (backup_dir / filename).write_bytes(payload)
    (backup_dir / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")
    (backup_dir / f"{filename}.plain.sha256").write_text("a" * 64 + "\n", encoding="utf-8")
    return filename


def test_cos_upload_accepts_only_encrypted_backup_and_uses_protected_config(
    tmp_path: Path,
) -> None:
    backup_dir = tmp_path / "backups"
    filename = _create_encrypted_artifacts(backup_dir)
    config_path = tmp_path / "coscli.secret"
    config_path.write_text("encrypted-config", encoding="utf-8")
    config_path.chmod(0o600)
    invocation_log = tmp_path / "coscli.log"
    fake_coscli = tmp_path / "coscli"
    _write_executable(
        fake_coscli,
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >>'{invocation_log}'\n",
    )
    environment = {
        **os.environ,
        "BACKUP_DIR": str(backup_dir),
        "BACKUP_FILE": filename,
        "COSCLI_BIN": str(fake_coscli),
        "COSCLI_CONFIG_PATH": str(config_path),
        "COS_BUCKET_ALIAS": "private-backups",
        "COS_BACKUP_PREFIX": "dealflow-radar/postgres",
    }

    result = subprocess.run(
        ["sh", str(UPLOAD_SCRIPT)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.splitlines()[-1])
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert payload["encrypted"] is True
    assert len(invocations) == 3
    assert all("cos://private-backups/dealflow-radar/postgres/" in line for line in invocations)
    assert all(str(config_path) in line for line in invocations)


def test_cos_upload_rejects_plaintext_backup_name(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(UPLOAD_SCRIPT)],
        env={
            **os.environ,
            "BACKUP_FILE": "unsafe.dump",
            "COSCLI_CONFIG_PATH": str(tmp_path / "coscli.secret"),
            "COS_BUCKET_ALIAS": "private-backups",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert ".dump.age" in result.stderr


def test_cos_upload_rejects_config_readable_by_group(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    filename = _create_encrypted_artifacts(backup_dir)
    config_path = tmp_path / "coscli.secret"
    config_path.write_text("unsafe-config", encoding="utf-8")
    config_path.chmod(0o640)

    result = subprocess.run(
        ["sh", str(UPLOAD_SCRIPT)],
        env={
            **os.environ,
            "BACKUP_DIR": str(backup_dir),
            "BACKUP_FILE": filename,
            "COSCLI_CONFIG_PATH": str(config_path),
            "COS_BUCKET_ALIAS": "private-backups",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "group/other" in result.stderr


def test_encrypted_restore_requires_identity_below_run_secrets(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    filename = _create_encrypted_artifacts(backup_dir)

    result = subprocess.run(
        ["sh", str(RESTORE_SCRIPT)],
        env={
            **os.environ,
            "BACKUP_FILE": filename,
            "BACKUP_DIR": str(backup_dir),
            "BACKUP_AGE_IDENTITY_FILE": str(tmp_path / "identity.key"),
            "RESTORE_TEST_DATABASE_URL": "postgresql://restore:secret@restore-db/test_restore_test",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "/run/secrets" in result.stderr
