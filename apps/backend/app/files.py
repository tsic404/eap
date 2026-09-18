"""Uploaded-document file validation: size cap, extension whitelist, magic bytes.

The extension whitelist is the cheap first gate; magic bytes stop content-type
spoofing for the binary formats that have a stable signature. Text formats
(TXT/MD/CSV/JSON/HTML) carry no reliable leading signature, so they are gated
by extension alone.
"""

from __future__ import annotations

from pathlib import Path

from app.errors import AppError

# 15 MB upload cap — the single source of truth. The batch-3 upload endpoint
# imports this constant (via ``validate_document_file``) instead of declaring a
# second limit, so the two can never drift.
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024

_ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "xlsx", "txt", "md", "csv", "json", "html"})

# DOCX/XLSX are ZIP archives: the ``PK\x03\x04`` local-file-header signature
# sits at byte 0 with no permitted preamble.
_ZIP_MAGIC = b"PK\x03\x04"

# ISO 32000-1 §7.5.5 allows the ``%PDF-`` header to appear anywhere within the
# first 1024 bytes, and a leading UTF-8 BOM before it is permitted. PDF is
# therefore detected by "contains" over the header window, not byte-0 prefix.
_PDF_MAGIC = b"%PDF-"
_PDF_HEADER_WINDOW_BYTES = 1024


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def validate_document_file(filename: str, content: bytes) -> None:
    """Raise ``AppError`` unless ``content`` is an allowed, well-formed document.

    Order matters for the acceptance criteria: an empty upload reports
    ``EMPTY_FILE``, an oversized file ``FILE_TOO_LARGE``, and an
    unknown/mismatched format ``UNSUPPORTED_FORMAT``.
    """
    if len(content) == 0:
        raise AppError(422, "EMPTY_FILE", "File is empty")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise AppError(422, "FILE_TOO_LARGE", "File exceeds the 15 MB limit")

    ext = _extension(filename)
    if ext not in _ALLOWED_EXTENSIONS:
        raise AppError(422, "UNSUPPORTED_FORMAT", f"Unsupported file type: .{ext}")

    if ext == "pdf":
        if _PDF_MAGIC not in content[:_PDF_HEADER_WINDOW_BYTES]:
            raise AppError(422, "UNSUPPORTED_FORMAT", "File content does not match .pdf format")
    elif ext in {"docx", "xlsx"}:
        if not content.startswith(_ZIP_MAGIC):
            raise AppError(422, "UNSUPPORTED_FORMAT", f"File content does not match .{ext} format")
