from __future__ import annotations

import subprocess

import pytest

from backend.app.pdf_extractor import PdfExtractionError, extract_pdf_safely


def test_pdf_signature_is_checked_before_starting_parser() -> None:
    with pytest.raises(PdfExtractionError) as caught:
        extract_pdf_safely(
            b"not-a-pdf",
            max_pages=5,
            max_text_chars=1_000,
            timeout_seconds=1,
        )
    assert caught.value.code == "invalid_pdf_signature"


def test_pdf_parser_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="pdf-parser", timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(PdfExtractionError) as caught:
        extract_pdf_safely(
            b"%PDF-1.7\n",
            max_pages=5,
            max_text_chars=1_000,
            timeout_seconds=1,
        )
    assert caught.value.code == "pdf_parse_timeout"
