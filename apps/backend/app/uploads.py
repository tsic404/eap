"""Multipart upload reading for the endpoints that accept a document file.

The framework's form parser rolls a part into the temp directory once it passes
1 MiB; when that write fails, the error surfaces as a generic 400 before the
size gate in ``app.files`` runs, so a legitimate upload is rejected by the
parser rather than judged by the limit. Uploads are parsed here instead: the
part stays in memory up to the file cap, the whole body is bounded by a budget
derived from that cap, and a body the parser cannot read keeps the generic
answer the framework gave for it.

The budget is also the memory bound: ``UploadSlots`` caps the uploads in flight
at ``MAX_CONCURRENT_UPLOADS`` (parse plus the handler that consumes the file),
gives each user at most ``MAX_UPLOADS_PER_USER`` of them so one account cannot
take them all, and answers a wait longer than ``UPLOAD_SLOT_WAIT_S`` with the
upload rate limit's own code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartParser
from starlette.requests import Request
from starlette.types import Message, Receive

from app.dependencies import get_current_user
from app.errors import AppError, missing_field, validation_error
from app.files import FILE_TOO_LARGE_MESSAGE, MAX_FILE_SIZE_BYTES, MAX_UPLOAD_REQUEST_BYTES
from app.models.user import User

_UPLOAD_FIELD = "file"
_MULTIPART_CONTENT_TYPE = "multipart/form-data"

# One in-flight upload holds the parsed part and the whole-file bytes its
# handler read: at four uploads the path stays near 4 × 2 × the file cap.
MAX_CONCURRENT_UPLOADS = 4

# Two slots per account keep one user from taking every process slot while
# still letting a client upload twice without waiting on itself.
MAX_UPLOADS_PER_USER = 2

# A queued upload must be answered before the proxy's read timeout becomes the
# answer, and with a code the contract already has (the upload rate limit's).
UPLOAD_SLOT_WAIT_S = 15.0
_BUSY_MESSAGE = "Too many uploads in flight"

# The generic answer the framework gave when it still parsed the body.
_PARSE_FAILURE_MESSAGE = "There was an error parsing the body"

# Replaces the request body ``File`` would declare; a dependency-only parameter
# does not reach the OpenAPI schema, and the documented upload shape must stay.
OPENAPI_FILE_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [_UPLOAD_FIELD],
                    "properties": {_UPLOAD_FIELD: {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


@dataclass
class _UserSlot:
    """One user's upload slots plus how many requests hold or await them."""

    semaphore: asyncio.Semaphore
    requests: int = 0


class UploadSlots:
    """In-flight upload slots: a process-wide cap and a per-user cap.

    The process cap bounds the memory the upload path holds; the per-user cap
    keeps one account from taking every slot. A slot covers the parse and the
    handler that reads the file, so both buffers are inside the bound. The
    limits are constructor arguments so the guards can drive them directly.
    """

    def __init__(
        self,
        max_slots: int = MAX_CONCURRENT_UPLOADS,
        per_user_slots: int = MAX_UPLOADS_PER_USER,
        wait_s: float = UPLOAD_SLOT_WAIT_S,
    ) -> None:
        self._free = asyncio.Semaphore(max_slots)
        self._per_user_slots = per_user_slots
        self._wait_s = wait_s
        self._users: dict[Any, _UserSlot] = {}

    @asynccontextmanager
    async def hold(self, user_id: Any) -> AsyncIterator[_Lease]:
        try:
            async with asyncio.timeout(self._wait_s):
                await self._acquire(user_id)
        except TimeoutError as exc:
            # _acquire rolls back whatever it took before the deadline hit.
            raise AppError(429, "RATE_LIMITED", _BUSY_MESSAGE) from exc
        lease = _Lease(self, user_id)
        try:
            yield lease
        finally:
            lease.release()

    async def _acquire(self, user_id: Any) -> None:
        slot = self._users.get(user_id)
        if slot is None:
            slot = _UserSlot(asyncio.Semaphore(self._per_user_slots))
            self._users[user_id] = slot
        slot.requests += 1
        try:
            await slot.semaphore.acquire()
        except BaseException:
            self._leave(user_id)
            raise
        try:
            await self._free.acquire()
        except BaseException:
            slot.semaphore.release()
            self._leave(user_id)
            raise

    def _release(self, user_id: Any) -> None:
        self._free.release()
        self._users[user_id].semaphore.release()
        self._leave(user_id)

    def _leave(self, user_id: Any) -> None:
        slot = self._users[user_id]
        slot.requests -= 1
        if slot.requests == 0:
            del self._users[user_id]


class _Lease:
    """One held slot; whoever ends the request first releases it exactly once."""

    def __init__(self, slots: UploadSlots, user_id: Any) -> None:
        self._slots = slots
        self._user_id = user_id
        self._is_released = False

    def release(self) -> None:
        if self._is_released:
            return
        self._is_released = True
        self._slots._release(self._user_id)


def create_upload_slots() -> UploadSlots:
    """Create the in-flight limit for uploads; one instance per app."""
    return UploadSlots()


async def document_upload(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> AsyncIterator[UploadFile]:
    """Resolve the single ``file`` part of an upload request.

    The identity gate resolves before this dependency, so an unauthenticated or
    unauthorised caller is answered before its body is read. The declared length
    is checked first, then the bytes actually received, so a missing content
    length cannot lift the budget. The part is released with the request, and a
    client that goes away ends the slot at once: the framework does not cancel
    the request on a lost connection, so waiting for the handler would keep the
    slot until the upstream call returns.
    """
    slots: UploadSlots = request.app.state.upload_slots
    async with slots.hold(user.id) as lease:
        form = await _read_form(request)
        try:
            file = form.get(_UPLOAD_FIELD)
            if not isinstance(file, UploadFile):
                raise validation_error(missing_field(_UPLOAD_FIELD))
            watcher = asyncio.create_task(_release_on_disconnect(request, lease))
            try:
                yield file
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
        finally:
            await form.close()


async def _release_on_disconnect(request: Request, lease: _Lease) -> None:
    """Release the slot when the client goes away.

    A lost connection never cancels the request task; it arrives only on the
    receive channel. The body is already parsed when this starts, so the watcher
    owns the remaining receives. The upstream call keeps running; only the
    client-visible upload ends here.
    """
    while True:
        try:
            message = await request.receive()
        except Exception:
            # A failed receive cannot report a disconnect, and only receive
            # errors are swallowed: a release that fails must stay visible.
            return
        if message["type"] == "http.disconnect":
            lease.release()
            return


async def _read_form(request: Request) -> FormData:
    declared = _declared_length(request)
    if declared is not None and declared > MAX_UPLOAD_REQUEST_BYTES:
        raise AppError(422, "FILE_TOO_LARGE", FILE_TOO_LARGE_MESSAGE)
    if _media_type(request.headers.get("content-type", "")) != _MULTIPART_CONTENT_TYPE:
        return FormData()

    # The parser instance carries the spool threshold: as a class default it
    # would impose this memory policy on every form endpoint in the process.
    parser = MultiPartParser(
        request.headers,
        Request(request.scope, _budgeted_receive(request)).stream(),
        max_part_size=MAX_UPLOAD_REQUEST_BYTES,
    )
    parser.spool_max_size = MAX_FILE_SIZE_BYTES + 1
    try:
        return await parser.parse()
    except (AppError, HTTPException):
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_PARSE_FAILURE_MESSAGE) from exc


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _declared_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _budgeted_receive(request: Request) -> Receive:
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_UPLOAD_REQUEST_BYTES:
                raise AppError(422, "FILE_TOO_LARGE", FILE_TOO_LARGE_MESSAGE)
        return message

    return receive
