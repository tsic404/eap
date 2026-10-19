"""Executable acceptance cases for TC-tech-kb-02 (upload boundary values).

Empty payloads, the exact 15 MiB limit and its first rejected byte, the extension
whitelist, magic-byte checking, the binary default for an absent part content type,
size accounting without a content length, and the document count the KB carries.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.events.bus import bus
from app.events.knowledge import DOCUMENT_UPLOADED_EVENT

TC_ID = "TC-tech-kb-02"

_MB = 1024 * 1024
_FIFTEEN_MIB = 15 * _MB
_KB_URL = "/api/knowledge-bases"

_BOUNDARY = "Tckb02Boundary"

# Small payloads that satisfy each whitelisted extension's magic-byte gate.
_VALID_UPLOADS: dict[str, bytes] = {
    "pdf": b"%PDF-1.7\n%acceptance payload",
    "docx": b"PK\x03\x04docx payload",
    "xlsx": b"PK\x03\x04xlsx payload",
    "txt": b"plain text payload",
    "md": b"# markdown payload",
    "csv": b"column,value\n1,2\n",
    "json": b'{"key": "value"}',
    "html": b"<html><body>payload</body></html>",
}


def _multipart_body(filename: str, payload: bytes, *, content_type: str | None) -> bytes:
    """Build a one-part multipart body; ``content_type=None`` omits the part header."""
    head = (
        f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
    )
    if content_type is not None:
        head += f"Content-Type: {content_type}\r\n"
    return head.encode() + b"\r\n" + payload + f"\r\n--{_BOUNDARY}--\r\n".encode()


async def _stream(body: bytes) -> AsyncIterator[bytes]:
    """Yield ``body`` in 1 MiB chunks so the client cannot set a content length."""
    for start in range(0, len(body), _MB):
        yield body[start : start + _MB]


@contextmanager
def _collect(event: str) -> Iterator[list[dict[str, Any]]]:
    """Record every payload emitted on ``event`` for the duration of the block."""
    seen: list[dict[str, Any]] = []

    async def _handler(_name: str, **payload: Any) -> None:
        seen.append(payload)

    bus.subscribe(event, _handler)
    try:
        yield seen
    finally:
        bus.unsubscribe(event, _handler)


async def _upload(
    api: Any, headers: dict[str, str], kb_id: str, filename: str, payload: bytes, content_type: str
) -> Any:
    return await api.client.post(
        f"{_KB_URL}/{kb_id}/documents",
        headers=headers,
        files={"file": (filename, payload, content_type)},
    )


@pytest.mark.asyncio
async def test_empty_payload_returns_422_empty_file(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    resp = await _upload(api, headers, "kb-1", "notes.txt", b"", "text/plain")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EMPTY_FILE"
    detail = await api.client.get(f"{_KB_URL}/kb-1", headers=headers)
    assert detail.json()["data"]["doc_count"] == 0


@pytest.mark.asyncio
async def test_payload_of_exactly_fifteen_mib_is_accepted(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    resp = await _upload(api, headers, "kb-1", "big.txt", b"A" * _FIFTEEN_MIB, "text/plain")

    assert resp.status_code == 201
    assert resp.json()["data"] == {"id": "doc-1", "name": "big.txt", "status": "indexing"}


@pytest.mark.asyncio
async def test_payload_one_byte_over_fifteen_mib_returns_422_file_too_large(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    resp = await _upload(
        api, headers, "kb-1", "bigger.txt", b"A" * (_FIFTEEN_MIB + 1), "text/plain"
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_every_whitelisted_extension_is_accepted(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    accepted: dict[str, int] = {}
    reported: dict[str, str | None] = {}
    for extension, payload in _VALID_UPLOADS.items():
        resp = await _upload(
            api, headers, "kb-1", f"document.{extension}", payload, "application/octet-stream"
        )
        accepted[extension] = resp.status_code
        reported[extension] = resp.json().get("data", {}).get("status")

    assert accepted == dict.fromkeys(_VALID_UPLOADS, 201)
    assert reported == dict.fromkeys(_VALID_UPLOADS, "indexing")


@pytest.mark.asyncio
async def test_extension_outside_the_whitelist_returns_422_unsupported_format(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    rejected: dict[str, int] = {}
    codes: dict[str, str] = {}
    # ``.zip`` carries the same magic bytes as DOCX/XLSX, so it isolates the
    # extension gate from the magic-byte gate.
    for filename, payload in (("malware.exe", b"MZ\x90\x00"), ("archive.zip", b"PK\x03\x04zip")):
        resp = await _upload(api, headers, "kb-1", filename, payload, "application/octet-stream")
        rejected[filename] = resp.status_code
        codes[filename] = resp.json()["error"]["code"]

    assert rejected == {"malware.exe": 422, "archive.zip": 422}
    assert codes == {"malware.exe": "UNSUPPORTED_FORMAT", "archive.zip": "UNSUPPORTED_FORMAT"}


@pytest.mark.asyncio
async def test_magic_byte_mismatch_returns_422_unsupported_format(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    mismatched: dict[str, int] = {}
    for filename, upload_type in (
        ("report.pdf", "application/pdf"),
        ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ):
        resp = await _upload(api, headers, "kb-1", filename, b"plain bytes", upload_type)
        mismatched[filename] = resp.status_code
        assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"

    assert mismatched == {"report.pdf": 422, "report.docx": 422, "report.xlsx": 422}


@pytest.mark.asyncio
async def test_absent_part_content_type_is_sent_upstream_as_binary(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    body = _multipart_body("notes.txt", b"payload without a part content type", content_type=None)

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers={**headers, "content-type": f"multipart/form-data; boundary={_BOUNDARY}"},
        content=body,
    )

    assert resp.status_code == 201
    assert api.console.upload_file.await_args.kwargs["mimetype"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_size_limit_is_judged_from_received_bytes_without_a_content_length(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    async def _send(filename: str, payload: bytes) -> Any:
        body = _multipart_body(filename, payload, content_type="text/plain")
        request = api.client.build_request(
            "POST",
            f"{_KB_URL}/kb-1/documents",
            headers={
                **headers,
                "content-type": f"multipart/form-data; boundary={_BOUNDARY}",
            },
            content=_stream(body),
        )
        # Precondition of the clause: the request carries no content length, so
        # only the bytes actually received can decide the size gate.
        assert "content-length" not in request.headers
        return await api.client.send(request)

    accepted = await _send("streamed.txt", b"A" * _FIFTEEN_MIB)
    rejected = await _send("streamed-bigger.txt", b"A" * (_FIFTEEN_MIB + 1))

    assert accepted.status_code == 201
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_upload_counts_one_document_recorded_as_indexing(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    # Dify has not finished: the document must stay in the indexing state.
    api.console.get_document_indexing_status.return_value = {"indexing_status": "indexing"}

    uploaded = await _upload(api, headers, "kb-1", "notes.txt", b"payload", "text/plain")
    assert uploaded.status_code == 201
    assert uploaded.json()["data"]["status"] == "indexing"

    status = await api.client.get(f"{_KB_URL}/kb-1/documents/doc-1/status", headers=headers)
    detail = await api.client.get(f"{_KB_URL}/kb-1", headers=headers)

    assert status.json()["data"]["status"] == "indexing"
    assert detail.json()["data"]["doc_count"] == 1
    assert detail.json()["data"]["chunk_count"] == 0


@pytest.mark.asyncio
async def test_upload_creates_the_general_document_form(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    resp = await _upload(api, headers, "kb-1", "notes.txt", b"payload", "text/plain")

    assert resp.status_code == 201
    # Dify's general-document form; the API exposes no other document type.
    assert api.console.create_document.await_args.args[1].doc_form == "text_model"


@pytest.mark.asyncio
async def test_upload_failing_mid_way_writes_no_document_record_and_no_count(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    api.console.create_document.return_value = {"documents": []}

    with _collect(DOCUMENT_UPLOADED_EVENT) as events:
        resp = await _upload(api, headers, "kb-1", "notes.txt", b"payload", "text/plain")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_UPLOAD_FAILED"
    detail = await api.client.get(f"{_KB_URL}/kb-1", headers=headers)
    assert detail.json()["data"]["doc_count"] == 0
    assert [event for event in events if event.get("kb_id") == "kb-1"] == []


@pytest.mark.asyncio
async def test_delete_document_decrements_the_document_count(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    # The document is still indexing upstream, so it owns no segments yet.
    api.console.get_document_indexing_status.return_value = {"indexing_status": "indexing"}
    uploaded = await _upload(api, headers, "kb-1", "notes.txt", b"payload", "text/plain")
    assert uploaded.status_code == 201
    counted = await api.client.get(f"{_KB_URL}/kb-1", headers=headers)
    assert counted.json()["data"]["doc_count"] == 1

    resp = await api.client.delete(f"{_KB_URL}/kb-1/documents/doc-1", headers=headers)

    assert resp.status_code == 204
    detail = await api.client.get(f"{_KB_URL}/kb-1", headers=headers)
    assert detail.json()["data"]["doc_count"] == 0
    assert detail.json()["data"]["chunk_count"] == 0
