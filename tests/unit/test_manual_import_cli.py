from __future__ import annotations

from uuid import uuid4

import pytest

from scripts.import_research_json import _import_user_id, _required_env


def test_manual_import_cli_requires_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_IMPORT_FILE", raising=False)

    with pytest.raises(RuntimeError, match="RESEARCH_IMPORT_FILE is required"):
        _required_env("RESEARCH_IMPORT_FILE")


def test_manual_import_cli_validates_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORT_USER_ID", "not-a-uuid")

    with pytest.raises(RuntimeError, match="IMPORT_USER_ID must be a UUID"):
        _import_user_id()


def test_manual_import_cli_accepts_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid4()
    monkeypatch.setenv("IMPORT_USER_ID", str(user_id))

    assert _import_user_id() == user_id
