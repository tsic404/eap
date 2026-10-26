"""Deployment-topology guard: the entry proxy must not reject a legal upload.

The acceptance cases drive the app through ``httpx.ASGITransport``, which never
touches the nginx entry standing in front of it. These cases send uploads
through the shipped ``deploy/nginx/nginx.conf`` (only the listen port and the
upstream addresses are pointed at the test app) and check that the upload routes
are the only ones opened past nginx's default body ceiling.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx
import pytest
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.types import ASGIApp, Receive, Scope, Send

from app.dependencies import get_current_user
from app.errors import register_exception_handlers
from app.files import MAX_UPLOAD_REQUEST_BYTES, validate_document_file
from app.models.user import User
from app.uploads import create_upload_slots, document_upload

_NEEDS_DOCKER = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is required")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NGINX_CONF = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
# Same image the local and production orchestrations run.
_NGINX_IMAGE = "nginx:1.27-alpine"
_UPLOAD_LOCATION = r"location ~ ^/api/(files/upload|knowledge-bases/[^/]+/documents)$"
# The container reaches the host's loopback through this alias.
_HOST_ALIAS = "host.docker.internal"
_UPLOAD_PATH = "/api/knowledge-bases/kb-1/documents"
_OTHER_API_PATH = "/api/agents"
_UPLOAD_FIELD = "file"
_BOUNDARY = "ProxyBoundary"
_MB = 1024 * 1024
_FIFTEEN_MIB = 15 * _MB
_TWENTY_MIB = 20 * _MB
_READY_TIMEOUT_S = 30.0
_REQUEST_TIMEOUT_S = 180.0


def test_upload_routes_are_the_only_api_routes_opened_past_the_default() -> None:
    """The ceiling must cover the upload budget and stay off the other routes."""
    shipped = _NGINX_CONF.read_text(encoding="utf-8")
    uploads = _location_block(shipped, _UPLOAD_LOCATION)
    other_api = _location_block(shipped, "location /api/")

    assert _body_ceiling_bytes(uploads) > MAX_UPLOAD_REQUEST_BYTES
    assert _body_ceiling_bytes(uploads) > _TWENTY_MIB
    assert "client_max_body_size" not in other_api


@_NEEDS_DOCKER
def test_other_api_routes_keep_the_default_body_ceiling(proxied: _ProxiedApp) -> None:
    """A 2 MiB body outside the upload routes never reaches the app."""
    response = _post(proxied.url + _OTHER_API_PATH, b"A" * (2 * _MB))

    assert response.status_code == 413, response.text


@_NEEDS_DOCKER
def test_payload_of_exactly_fifteen_mib_survives_the_entry_proxy(proxied: _ProxiedApp) -> None:
    """A legal 15 MiB upload must reach the app, not the proxy's 413."""
    response = _post_upload(proxied.url, b"A" * _FIFTEEN_MIB)

    assert response.status_code == 201, response.text


@_NEEDS_DOCKER
def test_twenty_mib_with_a_declared_length_is_judged_by_the_app(
    proxied: _ProxiedApp,
) -> None:
    """The proxy passes 20 MiB so the app's budget answers FILE_TOO_LARGE."""
    response = _post_upload(proxied.url, b"A" * _TWENTY_MIB)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


@_NEEDS_DOCKER
def test_twenty_mib_chunked_upload_is_judged_by_the_declared_length(
    proxied: _ProxiedApp,
) -> None:
    """The proxy buffers a chunked body, so the app's length precheck judges it."""
    proxied.app.state.inbound_content_lengths.clear()
    body = _multipart_body(b"A" * _TWENTY_MIB)

    response = _post_upload(proxied.url, b"A" * _TWENTY_MIB, streamed=True)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert proxied.app.state.inbound_content_lengths == [str(len(body))]


# ── the shipped config, read as text ───────────────────────────────────────


def _location_block(config: str, header: str) -> str:
    """The text of one ``location`` block, up to its closing brace."""
    start = config.index(header + " {")
    depth = 0
    for index in range(start, len(config)):
        if config[index] == "{":
            depth += 1
        elif config[index] == "}":
            depth -= 1
            if depth == 0:
                return config[start : index + 1]
    raise AssertionError(f"unterminated block: {header}")


def _body_ceiling_bytes(block: str) -> int:
    """The ``client_max_body_size`` a block declares, in bytes."""
    for line in block.splitlines():
        tokens = line.strip().split()
        if len(tokens) == 2 and tokens[0] == "client_max_body_size" and tokens[1].endswith("m;"):
            return int(tokens[1][:-2]) * _MB
    raise AssertionError("the block declares no client_max_body_size")


# ── the app and the proxy under test ───────────────────────────────────────


@dataclass
class _ProxiedApp:
    """The proxied upload app plus the base URL the proxy answers on."""

    url: str
    app: FastAPI


@contextmanager
def _proxied_app() -> Iterator[_ProxiedApp]:
    """Run the upload app behind the shipped nginx config."""
    app = _upload_app()
    app_port = _free_port()
    proxy_port = _free_port()
    with _AppServer(app, app_port):
        with _entry_proxy(proxy_port, app_port):
            yield _ProxiedApp(url=f"http://127.0.0.1:{proxy_port}", app=app)


class _RecordContentLength:
    """Record the ``content-length`` each request reaches the app with."""

    def __init__(self, app: ASGIApp, *, sink: list[str | None]) -> None:
        self.app = app
        self._sink = sink

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            raw = headers.get(b"content-length")
            self._sink.append(raw.decode() if raw is not None else None)
        await self.app(scope, receive, send)


def _caller() -> User:
    """The fixed identity these transport cases run as."""
    return User(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        sso_sub="proxy-test",
        email="proxy@example.com",
        name="Proxy Test",
        role="knowledge_admin",
        status="active",
    )


def _upload_app() -> FastAPI:
    """The real upload parser and size gate behind a bare ASGI app."""
    app = FastAPI()
    app.state.upload_slots = create_upload_slots()
    app.state.inbound_content_lengths: list[str | None] = []
    # Applied to every request, including the ones the parser rejects before a
    # handler runs.
    app.add_middleware(_RecordContentLength, sink=app.state.inbound_content_lengths)
    register_exception_handlers(app)
    # Identity is covered by the acceptance cases; these cases exercise the
    # transport and the parser, so they resolve one fixed caller.
    app.dependency_overrides[get_current_user] = _caller

    @app.post(_UPLOAD_PATH, status_code=201)
    async def upload_document(
        file: Annotated[UploadFile, Depends(document_upload)],
    ) -> JSONResponse:
        content = await file.read()
        validate_document_file(file.filename or "", content)
        return JSONResponse({"data": {"name": file.filename}}, status_code=201)

    @app.post(_OTHER_API_PATH, status_code=201)
    async def other_api() -> JSONResponse:
        return JSONResponse({"data": {}}, status_code=201)

    return app


class _AppServer:
    """The test app on a real socket, so nginx can reach it."""

    def __init__(self, app: FastAPI, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", access_log=False)
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> _AppServer:
        self._thread.start()
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while not self._server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("the upload app did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=_READY_TIMEOUT_S)


@contextmanager
def _entry_proxy(proxy_port: int, app_port: int) -> Iterator[None]:
    """Run nginx with the rendered config and wait for its own health probe."""
    name = f"eap-proxy-test-{uuid.uuid4().hex[:8]}"
    shipped = _NGINX_CONF.read_text(encoding="utf-8")
    rendered = _render_config(shipped, listen_port=proxy_port, upstream_port=app_port)
    with tempfile.TemporaryDirectory(prefix="eap-nginx-") as directory:
        conf_path = Path(directory) / "default.conf"
        conf_path.write_text(rendered, encoding="utf-8")
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--publish",
                f"{proxy_port}:{proxy_port}",
                "--add-host",
                f"{_HOST_ALIAS}:host-gateway",
                "--volume",
                f"{conf_path}:/etc/nginx/conf.d/default.conf:ro",
                _NGINX_IMAGE,
            ],
            check=True,
            capture_output=True,
        )
        try:
            _wait_ready(f"http://127.0.0.1:{proxy_port}/healthz")
            yield
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True)


def _render_config(shipped: str, *, listen_port: int, upstream_port: int) -> str:
    """Point the shipped config at the test app, changing topology only.

    The upstreams become literals so nginx resolves them from the container's
    hosts file; every other directive — the body ceiling among them — stays as
    deployed.
    """
    upstream = f"http://{_HOST_ALIAS}:{upstream_port}"
    rendered = shipped.replace("listen 80;", f"listen {listen_port};")
    rendered = rendered.replace(
        "set $backend_upstream http://backend:3001;\n        proxy_pass $backend_upstream;",
        f"proxy_pass {upstream};",
    )
    rendered = rendered.replace("proxy_pass http://frontend:3000;", f"proxy_pass {upstream};")
    assert rendered.count(f"proxy_pass {upstream};") == 3
    return rendered


def _wait_ready(url: str) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT_S
    while True:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"the entry proxy did not answer {url}")
        time.sleep(0.1)


def _post(url: str, body: bytes) -> httpx.Response:
    with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
        return client.post(url, content=body, headers={"content-type": "application/json"})


def _post_upload(proxy_url: str, payload: bytes, *, streamed: bool = False) -> httpx.Response:
    """Send one upload through the proxy; ``streamed`` sends it without a client length."""
    with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
        if not streamed:
            return client.post(
                proxy_url + _UPLOAD_PATH,
                files={_UPLOAD_FIELD: ("big.txt", payload, "text/plain")},
            )
        return client.post(
            proxy_url + _UPLOAD_PATH,
            headers={"content-type": f"multipart/form-data; boundary={_BOUNDARY}"},
            content=_chunks(_multipart_body(payload)),
        )


def _multipart_body(payload: bytes) -> bytes:
    head = (
        f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="{_UPLOAD_FIELD}"; '
        'filename="big.txt"\r\nContent-Type: text/plain\r\n\r\n'
    )
    return head.encode() + payload + f"\r\n--{_BOUNDARY}--\r\n".encode()


def _chunks(body: bytes) -> Iterator[bytes]:
    for start in range(0, len(body), _MB):
        yield body[start : start + _MB]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def proxied() -> Iterator[_ProxiedApp]:
    """One app and one nginx container for every transport case in this module."""
    with _proxied_app() as running:
        yield running
