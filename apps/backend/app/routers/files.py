"""FileController: raw file upload for chat attachments (§32.4.2).

Chat messages carry Dify upload-file ids (``MessageFileDto.id``), which only
the Console API's ``/console/api/files/upload`` produces. The knowledge-base
upload endpoint returns a *document* id instead, so attachments need this
standalone upload path — it mirrors ``DifyConsoleClient.upload_file`` and
returns the upload-file id the frontend later echoes back on send.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.dependencies import get_current_user
from app.dify_console import DifyConsoleClient, DifyConsoleError
from app.errors import AppError
from app.files import MAX_FILE_SIZE_BYTES, validate_document_file
from app.models.user import User
from app.schemas.conversation import UploadedFileDto

router = APIRouter(prefix="/api/files", tags=["files"])

CurrentUser = Annotated[User, Depends(get_current_user)]


def get_dify_console(request: Request) -> DifyConsoleClient:
    """Resolve the app-scoped Dify console client (wired in ``create_app`` lifespan)."""
    return cast(DifyConsoleClient, request.app.state.dify_console)


DifyConsole = Annotated[DifyConsoleClient, Depends(get_dify_console)]


@router.post("/upload", response_model=UploadedFileDto, status_code=201)
async def upload_file(
    dify_console: DifyConsole,
    _: CurrentUser,
    file: Annotated[UploadFile, File(...)],
) -> UploadedFileDto:
    """Upload a document for chat attachment, returning its Dify upload-file id."""
    filename = file.filename or ""
    # Pre-check Content-Length so an oversized upload is rejected before it is
    # buffered into memory; ``validate_document_file`` re-checks the actual
    # byte count as the authoritative guard.
    if file.size is not None and file.size > MAX_FILE_SIZE_BYTES:
        raise AppError(422, "FILE_TOO_LARGE", "File exceeds the 15 MB limit")
    content = await file.read()
    validate_document_file(filename, content)

    try:
        record = await dify_console.upload_file(
            filename,
            content,
            mimetype=file.content_type or "application/octet-stream",
        )
    except DifyConsoleError as exc:
        # A failed Console call is a Dify-side failure, distinct from the 500
        # the global handler would emit for an uncaught exception — the
        # frontend can surface "try again" instead of "internal error".
        raise AppError(502, "DIFY_UPLOAD_FAILED", "Dify file upload failed") from exc
    return UploadedFileDto(id=record["id"], name=filename, type="document")
