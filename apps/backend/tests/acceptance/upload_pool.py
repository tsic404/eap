"""Shared plumbing for the upload in-flight acceptance cases.

``TC-tech-upload-01`` (AC-T22) and ``TC-tech-upload-02`` (AC-T23) judge one
in-flight pool from two sides — its rejection semantics and the fact that its
capacity belongs to a service process. Both need four things the shared harness
does not provide:

* request sessions on connections the case owns: the harness binds every request
  to one connection inside one outer transaction, where two requests can never
  be in flight at once;
* a Dify boundary that holds an upload open — the only hop these cases mock, and
  the place where "in flight" is counted;
* request exchanges the case owns end to end, including the client disconnect
  (httpx cannot deliver ``http.disconnect`` mid-request);
* a second *service process*: the pool belongs to one process, so the boundary
  case starts a real uvicorn subprocess against the same PostgreSQL/Redis and
  the same Dify stub.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.datastructures import UploadFile

from app.db import get_session
from app.dify_console import DifyConsoleError
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.models.user import User
from tests.acceptance.pool_probe import PROBE_PATH
from tests.conftest import seed_kb, seed_tenant_user, seed_user, unique_client_ip

ATTACHMENT_PATH = "/api/files/upload"
DOCUMENTS_PATH = "/api/knowledge-bases/{kb_id}/documents"

# A payload both gates accept, so a case that queues on the pool observes the
# pool and nothing else.
SMALL_FILE = ("notes.txt", b"payload", "text/plain")
# ``.exe`` is outside the extension whitelist and its magic bytes are irrelevant.
UNSUPPORTED_FILE = ("malware.exe", b"MZ\x90\x00", "application/octet-stream")

# Build on demand: this module is imported during collection, and 20 MiB held for
# the whole session would be memory the timing cases never asked for.
_OVERSIZED_BYTES = 20 * 1024 * 1024

# The subprocess is a real app boot; the stub and container bring-up are fast,
# but a cold CI runner is not.
_SERVICE_START_TIMEOUT_S = 60.0
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def oversized_file() -> tuple[str, bytes, str]:
    """A payload past the single-source upload cap (``app.files.MAX_FILE_SIZE_BYTES``)."""
    return ("huge.txt", b"A" * _OVERSIZED_BYTES, "text/plain")


def fresh_kb_id(worker_index: str) -> str:
    """A knowledge-base id unique to this worker and run.

    The knowledge-base id is a global primary key, and the world commits its
    rows into the shared database. A fixed id would collide with a parallel
    worker and with anything a killed earlier run left behind.
    """
    return f"kb-{worker_index}-{uuid.uuid4().hex[:8]}"


class UploadGate:
    """The Dify upload boundary, held open so in-flight uploads can be counted.

    Every entry is one upload that reached its handler, i.e. one upload holding
    an in-flight slot: ``live``/``peak`` are the observable pool occupancy. While
    ``hold`` is in effect an entry waits for ``release``; ``fail`` makes the
    upstream reject instead, which is a business failure after the slot is taken.
    """

    def __init__(self) -> None:
        self.live = 0
        self.peak = 0
        self.entries = 0
        self.fail = False
        self._open = asyncio.Event()
        self._ids = 0

    def hold(self) -> None:
        self._open.clear()

    def release(self) -> None:
        self._open.set()

    def install(self, console: Any) -> None:
        """Route the app-scoped console mock through this gate."""
        console.upload_file.side_effect = self.upload_file
        console.create_document.side_effect = self.create_document

    async def upload_file(self, filename: str, content: bytes, *, mimetype: str) -> dict[str, str]:
        self.live += 1
        self.entries += 1
        self.peak = max(self.peak, self.live)
        try:
            if self.fail:
                raise DifyConsoleError(500, "upload failed")
            await self._open.wait()
            self._ids += 1
            return {"id": f"upload-{self._ids}"}
        finally:
            self.live -= 1

    async def create_document(self, dataset_id: str, params: Any) -> dict[str, Any]:
        self._ids += 1
        return {"documents": [{"id": f"doc-{self._ids}", "indexing_status": "indexing"}]}


class World:
    """Committed seed data plus the request sessions a case owns.

    Everything the fixtures seed lives in one transaction that is rolled back,
    so a request on a connection of its own could not see it. This world commits
    the tenant, its members and its knowledge bases on its own engine, and drops
    them again when the case ends. The same committed rows are what a service
    subprocess reads.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self._tenant_ids: list[uuid.UUID] = []
        self._restore: list[tuple[Any, Any]] = []

    def bind(self, app: Any) -> None:
        """Route one app's request sessions through this world's connections."""

        async def _session() -> AsyncIterator[AsyncSession]:
            async with self.factory() as session:
                yield session

        self._restore.append((app, app.dependency_overrides.get(get_session)))
        app.dependency_overrides[get_session] = _session

    async def seed_tenant(
        self,
        api: Any,
        *,
        roles: Sequence[str] = ("knowledge_admin",),
        kb_ids: Sequence[str] = (),
    ) -> tuple[Tenant, list[User]]:
        """Commit a tenant, one member per role, and the requested knowledge bases."""
        async with self.factory() as session:
            tenant, first = await seed_tenant_user(
                session, role=roles[0], worker_index=api.worker_index
            )
            members = [first]
            for role in roles[1:]:
                members.append(await seed_user(session, tenant, role=role))
            for kb_id in kb_ids:
                await seed_kb(session, tenant, kb_id=kb_id)
        self._tenant_ids.append(tenant.id)
        return tenant, members

    async def close(self) -> None:
        for app, previous in self._restore:
            if previous is None:
                app.dependency_overrides.pop(get_session, None)
            else:
                app.dependency_overrides[get_session] = previous
        self._restore.clear()
        if not self._tenant_ids:
            return
        async with self.factory() as session:
            for tenant_id in self._tenant_ids:
                # Knowledge documents go with their knowledge base (FK cascade).
                await session.execute(
                    delete(KnowledgeBaseRegistry).where(
                        KnowledgeBaseRegistry.tenant_id == tenant_id
                    )
                )
                await session.execute(delete(User).where(User.tenant_id == tenant_id))
                await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.commit()
        self._tenant_ids.clear()


@asynccontextmanager
async def owned_connections(api: Any) -> AsyncIterator[World]:
    """A world whose requests each get a connection of their own."""
    engine = create_async_engine(api.settings.database_url)
    world = World(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield world
    finally:
        await world.close()
        await engine.dispose()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def dify_stub(gate: UploadGate) -> AsyncIterator[str]:
    """Run the Dify Console API the service subprocesses call.

    It answers the console login and holds every file upload on ``gate``: the
    upload is counted and parked in the *parent* process, so the case observes
    what both service processes have in flight at the boundary.
    """
    stub = FastAPI()

    @stub.post("/console/api/login")
    async def login(response: Response) -> dict[str, str]:
        response.set_cookie("access_token", "stub-access-token")
        response.set_cookie("csrf_token", "stub-csrf-token")
        return {"result": "success"}

    @stub.post("/console/api/files/upload")
    async def upload(request: Request) -> dict[str, str]:
        form = await request.form()
        uploaded = form["file"]
        if not isinstance(uploaded, UploadFile):
            raise TypeError("the stub expected a file part")
        return await gate.upload_file(
            uploaded.filename or "",
            await uploaded.read(),
            mimetype=uploaded.content_type or "application/octet-stream",
        )

    port = _free_port()
    server = _StubServer(uvicorn.Config(stub, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        await until(lambda: server.started, what="the Dify stub is serving", timeout=30.0)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10.0)


class _StubServer(uvicorn.Server):
    """A server awaited inside the test loop; the process keeps its signals."""

    def install_signal_handlers(self) -> None:
        pass


@dataclass
class ServiceProcess:
    """One real service process: an OS process serving the shipped app."""

    client: httpx.AsyncClient
    process: asyncio.subprocess.Process

    async def pool(self) -> dict[str, int]:
        response = await self.client.get(PROBE_PATH)
        response.raise_for_status()
        # The API envelope wraps every route body (C-5).
        return dict(response.json()["data"])


def _service_env(api: Any, *, dify_url: str, port: int, rate_limited: bool) -> dict[str, str]:
    env = {**os.environ}
    env.update(
        APP_ENV="test",
        DATABASE_URL=api.settings.database_url,
        REDIS_URL=api.settings.redis_url,
        JWT_PUBLIC_KEY=api.settings.jwt_public_key,
        DIFY_API_BASE_URL=dify_url,
        DIFY_CONSOLE_EMAIL="acceptance@example.com",
        DIFY_CONSOLE_PASSWORD="acceptance",
        LOG_LEVEL="WARNING",
        RATE_LIMIT_ENABLED="true" if rate_limited else "false",
        RATE_LIMIT_REQUESTS="100000",
        RATE_LIMIT_UPLOAD_PER_MINUTE="10",
        UPLOAD_SERVICE_PORT=str(port),
    )
    return env


@asynccontextmanager
async def service_process(
    api: Any, *, dify_url: str, rate_limited: bool = False
) -> AsyncIterator[ServiceProcess]:
    """Start a real service process sharing the deployment's PostgreSQL/Redis."""
    port = _free_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.acceptance.upload_service",
        cwd=str(_BACKEND_ROOT),
        env=_service_env(api, dify_url=dify_url, port=port, rate_limited=rate_limited),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(30.0))
    try:
        await _await_live(client, process)
        yield ServiceProcess(client=client, process=process)
    finally:
        await client.aclose()
        if process.returncode is None:
            process.terminate()
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=10.0)
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _await_live(client: httpx.AsyncClient, process: asyncio.subprocess.Process) -> None:
    deadline = time.monotonic() + _SERVICE_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.returncode is not None:
            output = await process.communicate()
            raise AssertionError(
                f"service process exited with {process.returncode}: "
                f"{output[0].decode(errors='replace')[-2000:]}"
            )
        try:
            response = await client.get("/api/health/live")
        except httpx.HTTPError:
            pass
        else:
            if response.status_code == 200:
                return
        await asyncio.sleep(0.05)
    raise AssertionError("service process never became live")


# An upload the pool admitted still parses its body before it reaches the
# upstream gate, so the gate's own count lags the lease count. A queued upload
# would cover that same stretch if the cap were wrong: once the gate has counted
# every free slot, wait this long before judging the one still queued.
GATE_SETTLE_S = 0.5


async def until(predicate: Callable[[], bool], *, what: str, timeout: float = 10.0) -> None:
    """Wait until ``predicate`` holds, or fail saying what was never reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never observed: {what}")


async def until_async(
    predicate: Callable[[], Awaitable[bool]], *, what: str, timeout: float = 10.0
) -> None:
    """Wait until an async ``predicate`` holds, or fail saying what was never reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never observed: {what}")


async def timed(request: Any) -> tuple[httpx.Response, float]:
    """Await ``request`` and report how long it took."""
    started = time.monotonic()
    response = await request
    return response, time.monotonic() - started


def upload_limit_headers(response: httpx.Response) -> list[str]:
    """The rate-limit response headers a limited answer must not carry."""
    return [
        name
        for name in response.headers
        if name.lower() == "retry-after"
        or "ratelimit" in name.lower()
        or "rate-limit" in name.lower()
    ]


async def post_attachment(
    client: httpx.AsyncClient, headers: dict[str, str], file: tuple[str, bytes, str] = SMALL_FILE
) -> httpx.Response:
    return await client.post(ATTACHMENT_PATH, headers=headers, files={"file": file})


async def post_document(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    kb_id: str,
    file: tuple[str, bytes, str] = SMALL_FILE,
) -> httpx.Response:
    return await client.post(
        DOCUMENTS_PATH.format(kb_id=kb_id), headers=headers, files={"file": file}
    )


def multipart_body(
    file: tuple[str, bytes, str], *, boundary: str = "acceptance-boundary"
) -> tuple[bytes, str]:
    """A single-part multipart body and the content type that declares it."""
    filename, content, content_type = file
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    return head + content + f"\r\n--{boundary}--\r\n".encode(), (
        f"multipart/form-data; boundary={boundary}"
    )


@dataclass
class RawResponse:
    """What one raw ASGI exchange sent back, after the app finished."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


class RawRequest:
    """One upload the case can disconnect for real.

    httpx's ASGI transport cannot make the client go away mid-request, and
    cancelling the task is a different signal: uvicorn reports a lost connection
    as ``http.disconnect`` on ``receive`` while the ASGI task keeps running.
    This drives the app callable with exactly that receive.
    """

    def __init__(
        self, app: Any, *, path: str, headers: dict[str, str], body: bytes, client_ip: str
    ) -> None:
        self._app = app
        self._body = body
        self._is_body_sent = False
        self._disconnected = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.response: RawResponse | None = None
        self._scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in headers.items()
            ],
            "server": ("test", 80),
            "client": (client_ip, 12345),
            "state": {},
        }

    def start(self) -> None:
        self._task = asyncio.create_task(self._call())

    def disconnect(self) -> None:
        self._disconnected.set()

    async def wait(self, *, timeout: float = 30.0) -> RawResponse:
        if self._task is None:
            raise AssertionError("the request was never started")
        await asyncio.wait_for(self._task, timeout=timeout)
        if self.response is None:
            raise AssertionError("the app never answered")
        return self.response

    def abort(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _call(self) -> None:
        status = 0
        response_headers: list[tuple[str, str]] = []
        body = bytearray()

        async def receive() -> dict[str, Any]:
            if not self._is_body_sent:
                self._is_body_sent = True
                return {"type": "http.request", "body": self._body, "more_body": False}
            await self._disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status, response_headers
            if message["type"] == "http.response.start":
                status = int(message["status"])
                response_headers = [
                    (name.decode("latin-1"), value.decode("latin-1"))
                    for name, value in message.get("headers", [])
                ]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        await self._app(self._scope, receive, send)
        self.response = RawResponse(status=status, headers=response_headers, body=bytes(body))


def raw_upload(
    app: Any, headers: dict[str, str], file: tuple[str, bytes, str] = SMALL_FILE
) -> RawRequest:
    """An attachment upload over a connection the case owns."""
    body, content_type = multipart_body(file)
    return RawRequest(
        app,
        path=ATTACHMENT_PATH,
        headers={**headers, "content-type": content_type, "content-length": str(len(body))},
        body=body,
        client_ip=unique_client_ip(),
    )
