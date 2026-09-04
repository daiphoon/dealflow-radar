from __future__ import annotations

import io
import json
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExtractedPdf:
    text: str
    title: str | None
    creation_date: str | None
    page_count: int
    truncated: bool


def _worker_extract(body: bytes, *, max_pages: int, max_text_chars: int) -> ExtractedPdf:
    if not body.startswith(b"%PDF-"):
        raise PdfExtractionError("invalid_pdf_signature", "PDF file signature is missing")
    try:
        reader = PdfReader(io.BytesIO(body), strict=True)
    except Exception as error:
        raise PdfExtractionError("invalid_pdf", "PDF could not be parsed") from error
    if reader.is_encrypted:
        raise PdfExtractionError("encrypted_pdf", "encrypted PDF files are not supported")
    page_count = len(reader.pages)
    if page_count > max_pages:
        raise PdfExtractionError("pdf_page_limit_exceeded", "PDF page limit exceeded")

    parts: list[str] = []
    text_length = 0
    truncated = False
    try:
        for page in reader.pages:
            value = " ".join((page.extract_text() or "").split())
            if not value:
                continue
            remaining = max_text_chars - text_length
            if remaining <= 0:
                truncated = True
                break
            if len(value) > remaining:
                parts.append(value[:remaining])
                truncated = True
                break
            parts.append(value)
            text_length += len(value)
    except Exception as error:
        raise PdfExtractionError(
            "pdf_text_extraction_failed", "PDF text extraction failed"
        ) from error
    text = "\n".join(parts).strip()
    if not text:
        raise PdfExtractionError("pdf_no_extractable_text", "PDF contains no extractable text")

    metadata = reader.metadata
    title = str(metadata.title).strip() if metadata and metadata.title else None
    creation_date = None
    if metadata:
        try:
            value = metadata.creation_date
        except (TypeError, ValueError):
            value = None
        if value is not None:
            creation_date = value.isoformat()
    return ExtractedPdf(
        text=text,
        title=title,
        creation_date=creation_date,
        page_count=page_count,
        truncated=truncated,
    )


def extract_pdf_safely(
    body: bytes,
    *,
    max_pages: int,
    max_text_chars: int,
    timeout_seconds: int,
) -> ExtractedPdf:
    if not body.startswith(b"%PDF-"):
        raise PdfExtractionError("invalid_pdf_signature", "PDF file signature is missing")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        str(max_pages),
        str(max_text_chars),
    ]
    try:
        completed = subprocess.run(
            command,
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise PdfExtractionError("pdf_parse_timeout", "PDF parsing timed out") from error
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfExtractionError(
            "pdf_parser_failed", "PDF parser returned invalid output"
        ) from error
    if completed.returncode != 0:
        raise PdfExtractionError(
            str(payload.get("error_code") or "pdf_parser_failed"),
            "PDF parser rejected the document",
        )
    return ExtractedPdf(
        text=str(payload["text"]),
        title=str(payload["title"]) if payload.get("title") else None,
        creation_date=(str(payload["creation_date"]) if payload.get("creation_date") else None),
        page_count=int(payload["page_count"]),
        truncated=bool(payload["truncated"]),
    )


def _main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        return 2
    try:
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    except (OSError, ValueError):
        # The parent process still enforces byte, page and wall-clock limits.
        pass
    try:
        result = _worker_extract(
            sys.stdin.buffer.read(),
            max_pages=int(sys.argv[2]),
            max_text_chars=int(sys.argv[3]),
        )
    except PdfExtractionError as error:
        sys.stdout.write(json.dumps({"error_code": error.code}))
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "text": result.text,
                "title": result.title,
                "creation_date": result.creation_date,
                "page_count": result.page_count,
                "truncated": result.truncated,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
