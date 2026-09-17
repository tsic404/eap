"""File validation tests: size cap, extension whitelist, magic bytes."""

import pytest

from app.errors import AppError
from app.files import MAX_FILE_SIZE_BYTES, validate_document_file


def test_valid_pdf_passes() -> None:
    validate_document_file("report.pdf", b"%PDF-1.7\n1 0 obj\n%%EOF")


def test_valid_docx_passes() -> None:
    validate_document_file("notes.docx", b"PK\x03\x04zip-local-header")


def test_valid_text_passes() -> None:
    validate_document_file("notes.md", b"# Title\n\nbody")


def test_unknown_extension_rejected() -> None:
    with pytest.raises(AppError) as exc:
        validate_document_file("malware.exe", b"MZ\x90\x00")
    assert exc.value.status_code == 422
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_magic_bytes_mismatch_rejected() -> None:
    with pytest.raises(AppError) as exc:
        validate_document_file("fake.pdf", b"MZ\x90\x00not-a-pdf")
    assert exc.value.status_code == 422
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_oversized_file_rejected() -> None:
    with pytest.raises(AppError) as exc:
        validate_document_file("big.txt", b"a" * (MAX_FILE_SIZE_BYTES + 1))
    assert exc.value.status_code == 422
    assert exc.value.code == "FILE_TOO_LARGE"


def test_file_at_exact_size_limit_passes() -> None:
    validate_document_file("limit.txt", b"a" * MAX_FILE_SIZE_BYTES)
