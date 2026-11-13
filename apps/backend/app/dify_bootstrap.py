"""Configure the local family's inference engine (C-131).

Run by the ``db-seed`` one-shot of both local-family orchestrations, right after
``app.bootstrap``. The engine (api / worker / plugin daemon) is a resident
dependency of that family, and its readiness item only turns normal once the
console account can log in and the workspace holds one usable model provider, so
this bootstrap establishes both from the local family's own values:

1. creates the console admin (``DIFY_CONSOLE_*`` from the env file) and keeps its
   password equal to the file's current value;
2. installs the pinned OpenAI-compatible model plugin from Dify's marketplace;
3. registers the deterministic models the stub container serves — the chat one
   and the embedding one a `high_quality` knowledge base resolves — and makes
   them the workspace defaults.

Every step is state-checked first, so a repeated bring-up leaves the engine
configured instead of duplicating it. Failures exit non-zero with the engine's
answer: the one-shot's exit code is part of the local family's readiness
baseline (C-88), so a broken engine must not masquerade as ready.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import secrets
import sys
import time
from typing import NamedTuple, NoReturn

import asyncpg  # type: ignore[import-untyped]
import httpx

CONSOLE_BASE = os.environ.get("DIFY_API_BASE_URL", "http://dify-api:5001").rstrip("/")
CONSOLE_EMAIL = os.environ.get("DIFY_CONSOLE_EMAIL", "")
CONSOLE_PASSWORD = os.environ.get("DIFY_CONSOLE_PASSWORD", "")
# Cosmetic, and only read when this bootstrap creates the account.
CONSOLE_NAME = "EAP Console"
# The engine's own database. Read only when the console rejects the env value;
# see ``align_console_password``.
DIFY_DB_URL = os.environ.get("DIFY_DB_URL", "")
# Pinned marketplace package: the checksum makes the installed provider byte-stable.
MODEL_PLUGIN = os.environ.get(
    "DIFY_MODEL_PLUGIN",
    "langgenius/openai_api_compatible:0.0.73"
    "@4cf123ffaa4556f079bed6c47f3a462cc825504b859b183239e74eb19136a4b5",
)
MODEL_PLUGIN_ID = os.environ.get("DIFY_MODEL_PLUGIN_ID", "langgenius/openai_api_compatible")
MODEL_PROVIDER = os.environ.get("DIFY_MODEL_PROVIDER", f"{MODEL_PLUGIN_ID}/openai_api_compatible")
MODEL_NAME = os.environ.get("DIFY_MODEL_NAME", "mock-gpt")
# `high_quality` knowledge bases resolve the default embedding model first.
EMBEDDING_NAME = os.environ.get("DIFY_EMBEDDING_NAME", "mock-embedding")
MODEL_BASE_URL = os.environ.get("DIFY_MODEL_BASE_URL", "http://mock-idp:4100/v1")
MODEL_API_KEY = os.environ.get("DIFY_MODEL_API_KEY", "")
# The engine's cold start (migrations + gunicorn) is measured in minutes; the
# deadline covers the whole bootstrap, not one request.
BOOTSTRAP_TIMEOUT_SECONDS = float(os.environ.get("DIFY_BOOTSTRAP_TIMEOUT", "900"))
CONSOLE_REQUEST_TIMEOUT = 30.0
# The marketplace install endpoint downloads the package synchronously.
PLUGIN_INSTALL_TIMEOUT_SECONDS = 300.0
POLL_INTERVAL_SECONDS = 3.0
# Dify stores base64(hexlify(pbkdf2_hmac("sha256", password, salt, 10 000))) and
# the base64 salt beside it; the engine's own login compares against that.
PASSWORD_HASH_ITERATIONS = 10_000
PASSWORD_SALT_BYTES = 16


def log(message: str) -> None:
    print(f"[dify-bootstrap] {message}", flush=True)


def fail(message: str) -> NoReturn:
    raise SystemExit(f"[dify-bootstrap] {message}")


def _body(response: httpx.Response) -> str:
    return response.text.strip()[:500]


class ConsoleCredentialRejected(Exception):
    """The engine answered the console login with a non-200 status."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"console login failed ({status_code}): {body}")


class Console:
    """Session-cookie client for the Dify console API used by this bootstrap."""

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=CONSOLE_BASE, timeout=CONSOLE_REQUEST_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Console:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        token = self._client.cookies.get("csrf_token")
        return {"X-CSRF-Token": token} if token else {}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
        timeout: float = CONSOLE_REQUEST_TIMEOUT,
    ) -> httpx.Response:
        try:
            return self._client.request(
                method,
                path,
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            fail(f"engine is unreachable at {CONSOLE_BASE}: {exc}")

    def login(self) -> None:
        if not CONSOLE_EMAIL or not CONSOLE_PASSWORD:
            fail("DIFY_CONSOLE_EMAIL / DIFY_CONSOLE_PASSWORD are not set")
        response = self._client.post(
            "/console/api/login",
            json={
                "email": CONSOLE_EMAIL,
                # Dify base64-decodes the password field (same encoding the
                # backend's console client and the web UI use).
                "password": base64.b64encode(CONSOLE_PASSWORD.encode("utf-8")).decode("ascii"),
            },
        )
        if response.status_code != 200:
            raise ConsoleCredentialRejected(response.status_code, _body(response))
        # The login response sets access_token / refresh_token / csrf_token
        # cookies; httpx's cookie jar stores them and re-sends them on later
        # requests to this origin.
        if self._client.cookies.get("access_token") is None:
            raise ConsoleCredentialRejected(
                response.status_code, "login response is missing the access_token cookie"
            )
        log(f"console session established as {CONSOLE_EMAIL}")


def wait_for_engine() -> None:
    deadline = time.monotonic() + BOOTSTRAP_TIMEOUT_SECONDS
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{CONSOLE_BASE}/console/api/setup", timeout=5.0)
            if response.status_code == 200:
                log("engine console API is up")
                return
            last_error = f"HTTP {response.status_code}: {_body(response)}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(POLL_INTERVAL_SECONDS)
    fail(
        f"engine console API did not become reachable within {BOOTSTRAP_TIMEOUT_SECONDS}s: "
        f"{last_error}"
    )


def ensure_setup(console: Console) -> None:
    status = console.request("GET", "/console/api/setup")
    if status.status_code != 200:
        fail(f"cannot read setup status ({status.status_code}): {_body(status)}")
    if status.json().get("step") == "finished":
        log("console setup already completed")
        return

    response = console.request(
        "POST",
        "/console/api/setup",
        json_body={
            "email": CONSOLE_EMAIL,
            "name": CONSOLE_NAME,
            "password": CONSOLE_PASSWORD,
        },
    )
    if response.status_code == 201:
        log(f"console admin created for {CONSOLE_EMAIL}")
        return
    # Another bring-up finishing the same step concurrently is the only
    # tolerated non-201: the credentials are then already the generated ones.
    if "already" in _body(response).lower():
        log("console setup was completed concurrently")
        return
    fail(f"console setup failed ({response.status_code}): {_body(response)}")


def _hashed_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS)
    return base64.b64encode(binascii.hexlify(digest)).decode("ascii")


async def _write_console_password() -> str:
    """Rewrite the console account's password row; returns the UPDATE status tag."""
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    connection = await asyncpg.connect(DIFY_DB_URL, timeout=CONSOLE_REQUEST_TIMEOUT)
    try:
        status: str = await connection.execute(
            "UPDATE accounts SET password = $1, password_salt = $2 WHERE email = $3",
            _hashed_password(CONSOLE_PASSWORD, salt),
            base64.b64encode(salt).decode("ascii"),
            CONSOLE_EMAIL,
        )
        return status
    finally:
        await connection.close()


def align_console_password() -> None:
    """Make the console account's password equal the env file's current value.

    The engine only compares a password at login and changes it behind an
    authenticated ``/account/password`` call (which needs the current one); its
    forgot-password path needs a mail server the local family does not run. An
    env file regenerated after the account was created — ``make setup-env``
    rotating the value, or the file restored from elsewhere — is therefore
    aligned on the account row directly, using the construction the engine
    verifies against. Local family only: production never runs this container.
    """
    if not DIFY_DB_URL:
        fail(
            "cannot align the console password: DIFY_DB_URL is not set — reset the engine "
            "volumes or restore the env file the account was created from"
        )
    try:
        status = asyncio.run(_write_console_password())
    except (OSError, ValueError, asyncpg.PostgresError) as exc:
        fail(f"cannot align the console password: {exc}")
    if status != "UPDATE 1":
        fail(
            f"no console account holds {CONSOLE_EMAIL} ({status}) — the engine setup was left "
            "half finished; reset the volume the engine keeps its database on and re-run"
        )
    log(f"console password aligned with the env file for {CONSOLE_EMAIL}")


def ensure_console_session(console: Console) -> None:
    """Log in, aligning an account whose password no longer matches the env file."""
    try:
        console.login()
        return
    except ConsoleCredentialRejected as rejected:
        first = rejected
    log(f"console rejected {CONSOLE_EMAIL}: {first}; aligning the account with the env file")
    align_console_password()
    try:
        console.login()
    except ConsoleCredentialRejected as rejected:
        fail(
            f"{rejected} — the env file's DIFY_CONSOLE_EMAIL / DIFY_CONSOLE_PASSWORD do not "
            "match the console account even after aligning it; reset the engine volumes "
            "(`docker compose down -v`) or restore the env file the account was created from"
        )


def installed_plugin_ids(console: Console) -> set[str]:
    response = console.request(
        "GET",
        "/console/api/workspaces/current/plugin/list",
        params={"page": 1, "page_size": 100},
    )
    if response.status_code != 200:
        fail(f"cannot list installed plugins ({response.status_code}): {_body(response)}")
    return {
        str(plugin.get("plugin_id"))
        for plugin in response.json().get("plugins", [])
        if isinstance(plugin, dict)
    }


def install_task(console: Console, task_id: str) -> dict[str, object] | None:
    response = console.request(
        "GET", f"/console/api/workspaces/current/plugin/tasks/{task_id}"
    )
    if response.status_code != 200:
        return None
    task = response.json().get("task")
    return task if isinstance(task, dict) else None


def ensure_model_plugin(console: Console) -> None:
    if MODEL_PLUGIN_ID in installed_plugin_ids(console):
        log(f"model plugin {MODEL_PLUGIN_ID} already installed")
        return

    log(f"installing model plugin {MODEL_PLUGIN} from the marketplace")
    response = console.request(
        "POST",
        "/console/api/workspaces/current/plugin/install/marketplace",
        json_body={"plugin_unique_identifiers": [MODEL_PLUGIN]},
        # The API downloads the package before answering; a slow marketplace
        # would otherwise look like a dead engine.
        timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS,
    )
    if response.status_code not in (200, 201):
        fail(f"plugin install request failed ({response.status_code}): {_body(response)}")
    started = response.json() if response.content else {}
    task_id = str(started.get("task_id") or "")

    deadline = time.monotonic() + BOOTSTRAP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        task = install_task(console, task_id) if task_id else None
        plugins = task.get("plugins") if task else None
        if isinstance(plugins, list):
            for plugin in plugins:
                if not isinstance(plugin, dict) or plugin.get("plugin_id") != MODEL_PLUGIN_ID:
                    continue
                status = plugin.get("status")
                if status == "failed":
                    fail(f"model plugin install failed: {plugin.get('message')}")
                if status == "success":
                    log(f"model plugin {MODEL_PLUGIN_ID} installed")
                    return
        # The task id is not always echoed back; the installed-plugin listing is
        # the authoritative terminal state either way.
        if MODEL_PLUGIN_ID in installed_plugin_ids(console):
            log(f"model plugin {MODEL_PLUGIN_ID} installed")
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    fail(
        f"model plugin {MODEL_PLUGIN_ID} was not installed within {BOOTSTRAP_TIMEOUT_SECONDS}s"
    )


def provider_entry(console: Console) -> dict[str, object] | None:
    response = console.request("GET", "/console/api/workspaces/current/model-providers")
    if response.status_code != 200:
        fail(f"cannot list model providers ({response.status_code}): {_body(response)}")
    for provider in response.json().get("data", []):
        if isinstance(provider, dict) and provider.get("provider") == MODEL_PROVIDER:
            return provider
    return None


def wait_for_provider(console: Console) -> dict[str, object]:
    """The provider cache is invalidated on install; allow it to refresh."""
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        provider = provider_entry(console)
        if provider is not None:
            return provider
        time.sleep(POLL_INTERVAL_SECONDS)
    fail(f"provider {MODEL_PROVIDER} is absent after installing the plugin")


class ModelSpec(NamedTuple):
    """One custom model the workspace needs registered on the stub provider."""

    model_type: str
    name: str
    credentials: dict[str, str]


MODEL_SPECS = (
    ModelSpec("llm", MODEL_NAME, {"mode": "chat", "context_size": "4096"}),
    # `high_quality` knowledge bases resolve the workspace default embedding
    # model before the engine creates a dataset, and the stub answers
    # /v1/embeddings with deterministic vectors. The plugin's credential
    # validator requires both parameters, as it does for the chat model.
    ModelSpec(
        "text-embedding",
        EMBEDDING_NAME,
        {"context_size": "4096", "max_chunks": "1"},
    ),
)


def registered_models(console: Console) -> set[str]:
    response = console.request(
        "GET",
        f"/console/api/workspaces/current/model-providers/{MODEL_PROVIDER}/models",
    )
    if response.status_code != 200:
        fail(f"cannot list provider models ({response.status_code}): {_body(response)}")
    return {
        str(model.get("model"))
        for model in response.json().get("data", [])
        if isinstance(model, dict)
    }


def ensure_model(console: Console, spec: ModelSpec) -> None:
    """Register one custom model on the stub provider and enable it."""
    if spec.name not in registered_models(console):
        if not MODEL_API_KEY:
            fail("DIFY_MODEL_API_KEY is not set")
        response = console.request(
            "POST",
            f"/console/api/workspaces/current/model-providers/{MODEL_PROVIDER}/models/credentials",
            json_body={
                "model": spec.name,
                "model_type": spec.model_type,
                "credentials": {
                    "endpoint_url": MODEL_BASE_URL,
                    "api_key": MODEL_API_KEY,
                    **spec.credentials,
                },
            },
        )
        if response.status_code not in (200, 201):
            fail(f"adding model {spec.name} failed ({response.status_code}): {_body(response)}")
        log(f"model {spec.name} ({spec.model_type}) registered against {MODEL_BASE_URL}")

    # A freshly added custom model is enabled; a model disabled by a previous
    # run stays disabled, and readiness must not depend on that state.
    response = console.request(
        "PATCH",
        f"/console/api/workspaces/current/model-providers/{MODEL_PROVIDER}/models/enable",
        json_body={"model": spec.name, "model_type": spec.model_type},
    )
    if not response.is_success:
        fail(
            f"enabling model {spec.name} failed ({response.status_code}): {_body(response)}"
        )


def ensure_models(console: Console) -> None:
    wait_for_provider(console)
    for spec in MODEL_SPECS:
        ensure_model(console, spec)
    # Defaults keep app and knowledge-base paths that omit an explicit model
    # working: the app path resolves the default LLM, the dataset path resolves
    # the default embedding model.
    response = console.request(
        "POST",
        "/console/api/workspaces/current/default-model",
        json_body={
            "model_settings": [
                {"model_type": spec.model_type, "provider": MODEL_PROVIDER, "model": spec.name}
                for spec in MODEL_SPECS
            ]
        },
    )
    if not response.is_success:
        fail(f"setting the default models failed ({response.status_code}): {_body(response)}")
    log(f"workspace models under {MODEL_PROVIDER} are ready")


def main() -> None:
    wait_for_engine()
    # Setup is not a session: Dify answers it before an admin exists, and it can
    # leave an account without a workspace when its write fails midway. Configure
    # the engine on a client that logs in afterwards, so no state from the setup
    # call is carried into the plugin and model steps.
    with Console() as setup_console:
        ensure_setup(setup_console)
    with Console() as console:
        ensure_console_session(console)
        ensure_model_plugin(console)
        ensure_models(console)
    log("inference engine is configured")


if __name__ == "__main__":
    main()
    sys.exit(0)
