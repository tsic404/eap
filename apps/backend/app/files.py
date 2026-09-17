"""Uploaded-document file validation: size cap, extension whitelist, magic bytes.

The extension whitelist is the cheap first gate; magic bytes stop content-type
spoofing for the binary formats that have a stable signature. Text formats
(TXT/MD/CSV/JSON/HTML) carry no reliable leading signature, so they are gated
by extension alone.
"""

from __future__ import annotations

from pathlib import Path

from app.errors import AppError

# 15 MB hard cap, mirroring the multipart limit declared on the upload endpoint.
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024

_ALLOWED_EXTENSIONS = frozenset(
    {"pdf", "docx", "xlsx", "txt", "md", "csv", "json", "html"}
)

# Leading magic bytes per extension. DOCX/XLSX are ZIP archives (``PK\x03\x04``).
_MAGIC_SIGNATURES: dict[str, bytes] = {
    "pdf": b"%PDF-",
    "docx": b"PK\x03\x04",
    "xlsx": b"PK\x03\x04",
}


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def validate_document_file(filename: str, content: bytes) -> None:
    """Raise ``AppError`` unless ``content`` is an allowed, well-formed document.

    Order matters for the acceptance criteria: an oversized file reports
    ``FILE_TOO_LARGE``, an unknown/mismatched format reports ``UNSUPPORTED_FORMAT``.
    """
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise AppError(422, "FILE_TOO_LARGE", "File exceeds the 15 MB limit")

    ext = _extension(filename)
    if ext not in _ALLOWED_EXTENSIONS:
        raise AppError(422, "UNSUPPORTED_FORMAT", f"Unsupported file type: .{ext}")

    signature = _MAGIC_SIGNATURES.get(ext)
    if signature is not None and not content.startswith(signature):
        raise AppError(422, "UNSUPPORTED_FORMAT", f"File content does not match .{ext} format")
