"""Dify Console API client (Session Cookie auth) — architecture doc §2.4 + §30.6.

The EAP backend never talks to the Dify Service API (``/v1/*``) for admin
operations; it drives Dify's *Console* API — the same internal API Dify's own
web UI calls — authenticated with a session cookie obtained from an email /
password login. This module owns that session: login, cookie + CSRF handling,
transparent re-login on 401, and a periodic (25 min) refresh so the session
outlives Dify's ``ACCESS_TOKEN_EXPIRE_MINUTES`` window.

Endpoint list is verified against ``langgenius/dify-api:1.17.0`` (the image
pinned in docker-compose.yml); a few paths differ from the architecture doc,
which was drafted against an earlier Dify release — see the inline notes.
"""

import asyncio
import base64
from typing import Any
from urllib.parse import quote

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from app.config import Settings
from app.logging_conf import get_logger

log = get_logger(__name__)

# Dify 1.17.0 console auth: every console API request is rejected with 401
# unless (a) the `access_token` cookie is present (httpx cookie jar handles it)
# and (b) the `csrf_token` cookie value is echoed back in the `X-CSRF-Token`
# header. The CSRF cookie is the only one that is not HttpOnly, which is how
# the browser reads it; we mirror that behaviour here.
_ACCESS_TOKEN_COOKIE = "access_token"
_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRF-Token"

_LOGIN_PATH = "/console/api/login"
# Refresh well inside the session window. Dify's ACCESS_TOKEN_EXPIRE_MINUTES
# defaults to 60 (configurable); refreshing at 25 min leaves margin against
# both that default and any tighter custom expiry, avoiding the 401 re-login
# race at the expiry boundary.
_SESSION_REFRESH_INTERVAL_MINUTES = 25
_HTTP_TIMEOUT = httpx.Timeout(30.0)


class DifyConsoleError(Exception):
    """Raised when the Dify Console API answers with an error status."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Dify Console error {status_code}: {body}")


class CreateAppParams(BaseModel):
    """Payload for ``POST /console/api/apps`` (Dify 1.17.0 ``CreateAppPayload``)."""

    name: str
    mode: str  # chat | agent-chat | advanced-chat | workflow | completion
    description: str | None = None
    icon_type: str | None = None
    icon: str | None = None
    icon_background: str | None = None


class UpdateAppParams(BaseModel):
    """Payload for ``PUT /console/api/apps/<id>`` (Dify 1.17.0 ``UpdateAppPayload``)."""

    name: str
    description: str | None = None
    icon_type: str | None = None
    icon: str | None = None
    icon_background: str | None = None
    use_icon_as_answer_icon: bool | None = None
    max_active_requests: int | None = None


class ModelConfig(BaseModel):
    """Payload for ``POST /console/api/apps/<id>/model-config``.

    Dify 1.17.0 selects the model with flat ``provider``/``model`` strings and
    carries prompt + completion parameters inside ``configs``; the architecture
    doc's ``model: dict`` / ``pre_prompt`` shape predates that change.
    """

    provider: str | None = None
    model: str | None = None
    configs: dict[str, Any] | None = None
    opening_statement: str | None = None
    suggested_questions: list[str] | None = None
    more_like_this: dict[str, Any] | None = None
    speech_to_text: dict[str, Any] | None = None
    text_to_speech: dict[str, Any] | None = None
    retrieval_model: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    dataset_configs: dict[str, Any] | None = None
    agent_mode: dict[str, Any] | None = None


class CreateDatasetParams(BaseModel):
    """Payload for ``POST /console/api/datasets`` (Dify 1.17.0 ``DatasetCreatePayload``)."""

    name: str
    description: str | None = None
    indexing_technique: str | None = None
    permission: str | None = None
    provider: str | None = None


class TestApiToolParams(BaseModel):
    """Payload for ``POST /console/api/workspaces/current/tool-provider/api/test/pre``."""

    tool_name: str
    provider_name: str | None = None
    credentials: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    schema_type: str | None = None
    schema_: str | None = Field(default=None, alias="schema")


def _encode_password(password: str) -> str:
    """Base64-encode the password for Dify's login endpoint.

    Dify's console login decorator ``decrypt_password_field`` Base64-decodes the
    ``password`` field before authenticating; a plaintext password fails with
    ``Invalid encrypted data``. This mirrors the web UI's ``encryptPassword``.
    """
    return base64.b64encode(password.encode("utf-8")).decode("ascii")


def _parse_response(response: httpx.Response) -> Any:
    text = response.text
    if not text:
        return {}
    try:
        return response.json()
    except ValueError:  # non-JSON body (e.g. plain text) — return it verbatim
        return text


class DifyConsoleClient:
    """Session-cookie client for the Dify Console API."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = settings.dify_api_base_url.rstrip("/")
        self._email = settings.dify_console_email
        self._password = settings.dify_console_password
        self._client = client
        self._owns_client = client is None
        self._login_lock = asyncio.Lock()
        self._scheduler: AsyncIOScheduler | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._logged_in = False

    @property
    def is_configured(self) -> bool:
        """Whether console credentials are present (empty → login is skipped)."""
        return bool(self._email and self._password)

    # ────────── lifecycle ──────────

    async def startup(self) -> None:
        """Application startup: open the client, log in, arm the refresh job."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        if not self.is_configured:
            log.warning("dify_console_client_not_configured", base_url=self._base_url)
            return
        try:
            await self.login()
        except DifyConsoleError:
            # Dify may not be ready yet (compose ordering). Don't crash the app;
            # the 25-min refresh job and on-demand 401 re-login will recover.
            log.exception("dify_console_login_failed_at_startup")
        self._start_scheduler()

    async def shutdown(self) -> None:
        """Application shutdown: stop the refresh job and release the client."""
        self._stop_scheduler()
        # Cancel and await any in-flight refresh so it cannot touch the client
        # after it is closed below.
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
        self._logged_in = False

    def _start_scheduler(self) -> None:
        if self._scheduler is not None:
            return
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            self._refresh_session,
            trigger="interval",
            minutes=_SESSION_REFRESH_INTERVAL_MINUTES,
            id="dify-console-session-refresh",
            replace_existing=True,
        )
        scheduler.start()
        self._scheduler = scheduler

    def _stop_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    async def _refresh_session(self) -> None:
        """Scheduled job: re-login to keep the session alive past Dify's expiry."""
        self._refresh_task = asyncio.current_task()
        try:
            await self.login(force=True)
            log.info("dify_console_session_refreshed")
        except (DifyConsoleError, httpx.HTTPError):
            log.exception("dify_console_session_refresh_failed")
        finally:
            if self._refresh_task is asyncio.current_task():
                self._refresh_task = None

    # ────────── session management ──────────

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        return self._client

    async def login(self, *, force: bool = False) -> None:
        """Log in (concurrency-safe: concurrent callers share one login).

        ``force=True`` (scheduler refresh) re-logs in unconditionally. The 401
        retry path instead resets ``_logged_in`` before calling ``login()`` so
        a concurrent re-login storm still collapses to a single login.
        """
        if not self.is_configured:
            raise DifyConsoleError(0, "Dify console credentials are not configured")
        async with self._login_lock:
            if self._logged_in and not force:
                return
            await self._do_login()

    async def _do_login(self) -> None:
        client = await self._ensure_client()
        response = await client.post(
            f"{self._base_url}{_LOGIN_PATH}",
            json={"email": self._email, "password": _encode_password(self._password)},
        )
        if response.status_code != 200:
            self._logged_in = False
            raise DifyConsoleError(response.status_code, response.text)
        # The login response sets access_token / refresh_token / csrf_token
        # cookies; httpx's cookie jar stores them and re-sends them on later
        # requests to this origin.
        if client.cookies.get(_ACCESS_TOKEN_COOKIE) is None:
            self._logged_in = False
            raise DifyConsoleError(
                response.status_code,
                "Dify login response is missing the access_token cookie",
            )
        self._logged_in = True

    async def _ensure_session(self) -> None:
        if not self._logged_in:
            await self.login()

    @staticmethod
    def _csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
        csrf_token = client.cookies.get(_CSRF_COOKIE)
        if csrf_token is None:
            return {}
        return {_CSRF_HEADER: csrf_token}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        client = await self._ensure_client()
        await self._ensure_session()
        response = await client.request(
            method,
            f"{self._base_url}{path}",
            json=json_body,
            params=params,
            headers=self._csrf_headers(client),
        )
        # Expired session → re-login once and retry the original request.
        if response.status_code == 401:
            self._logged_in = False  # force re-login even though the flag is stale
            await self.login()
            response = await client.request(
                method,
                f"{self._base_url}{path}",
                json=json_body,
                params=params,
                headers=self._csrf_headers(client),
            )
        if response.status_code >= 400:
            raise DifyConsoleError(response.status_code, response.text)
        return _parse_response(response)

    async def health_check(self) -> dict[str, str]:
        """Report Dify console reachability. Never raises."""
        try:
            await self._request("GET", "/console/api/apps", params={"limit": 1})
            return {"status": "ok"}
        except (DifyConsoleError, httpx.HTTPError):
            return {"status": "disconnected"}

    # ────────── app management ──────────

    async def create_app(self, params: CreateAppParams) -> Any:
        return await self._request(
            "POST", "/console/api/apps", json_body=params.model_dump(exclude_none=True)
        )

    async def update_app(self, app_id: str, params: UpdateAppParams) -> Any:
        return await self._request(
            "PUT", f"/console/api/apps/{app_id}", json_body=params.model_dump(exclude_none=True)
        )

    async def delete_app(self, app_id: str) -> None:
        await self._request("DELETE", f"/console/api/apps/{app_id}")

    async def configure_model(self, app_id: str, config: ModelConfig) -> Any:
        return await self._request(
            "POST",
            f"/console/api/apps/{app_id}/model-config",
            json_body=config.model_dump(exclude_none=True),
        )

    # ────────── dataset management ──────────

    async def create_dataset(self, params: CreateDatasetParams) -> Any:
        return await self._request(
            "POST", "/console/api/datasets", json_body=params.model_dump(exclude_none=True)
        )

    async def delete_dataset(self, dataset_id: str) -> None:
        await self._request("DELETE", f"/console/api/datasets/{dataset_id}")

    # ────────── API key management ──────────

    async def get_api_keys(self, app_id: str) -> Any:
        return await self._request("GET", f"/console/api/apps/{app_id}/api-keys")

    async def create_api_key(self, app_id: str) -> Any:
        return await self._request("POST", f"/console/api/apps/{app_id}/api-keys")

    # ────────── model management ──────────
    # Dify 1.17.0 moved these under /workspaces/current/... (doc listed the
    # pre-1.x paths /console/api/model-providers and /model-types/<provider>).

    async def get_model_providers(self) -> Any:
        return await self._request("GET", "/console/api/workspaces/current/model-providers")

    async def get_models(self, provider: str) -> Any:
        # provider is a route path component (e.g. "langgenius/openai/openai");
        # quote keeps special characters out of the URL while leaving "/" intact.
        return await self._request(
            "GET", f"/console/api/workspaces/current/model-providers/{quote(provider)}/models"
        )

    # ────────── workflow DSL ──────────

    async def export_dsl(self, app_id: str, *, include_secret: bool = False) -> Any:
        return await self._request(
            "GET",
            f"/console/api/apps/{app_id}/export",
            params={"include_secret": "true" if include_secret else "false"},
        )

    async def import_dsl(
        self,
        yaml_content: str,
        *,
        name: str | None = None,
        app_id: str | None = None,
    ) -> Any:
        # Dify 1.17.0 imports DSL via JSON body on /apps/imports (the doc's
        # multipart /apps/import was removed).
        body: dict[str, Any] = {"mode": "yaml-content", "yaml_content": yaml_content}
        if name is not None:
            body["name"] = name
        if app_id is not None:
            body["app_id"] = app_id
        return await self._request("POST", "/console/api/apps/imports", json_body=body)

    # ────────── tool management ──────────

    async def get_tools(self) -> Any:
        return await self._request("GET", "/console/api/workspaces/current/tool-providers")

    async def test_api_tool(self, params: TestApiToolParams) -> Any:
        return await self._request(
            "POST",
            "/console/api/workspaces/current/tool-provider/api/test/pre",
            json_body=params.model_dump(exclude_none=True, by_alias=True),
        )
