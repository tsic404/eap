"""Configure the QA variant's inference engine (C-87 / C-88).

Run by the QA stack's one-shot bootstrap container right after the tenant /
platform-admin seed. It makes the QA variant a working local-family shape:

1. creates the Dify console admin the backend logs in with (``DIFY_CONSOLE_*``
   from the generated env file — no credential lives in the repository);
2. installs the pinned OpenAI-compatible model provider plugin from Dify's
   marketplace;
3. registers the deterministic model the stub container serves and adds it to
   the workspace model list.

Every step is idempotent and state-checked first, so a repeated ``make qa-up``
(or a stack restart) leaves the engine configured instead of duplicating it.
Failures exit non-zero with the engine's answer: the one-shot's exit code is
part of the local family's readiness baseline (C-88), so a broken engine must
not masquerade as ready.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from typing import NoReturn

import httpx

CONSOLE_BASE = os.environ.get("DIFY_API_BASE_URL", "http://dify-api:5001").rstrip("/")
CONSOLE_EMAIL = os.environ.get("DIFY_CONSOLE_EMAIL", "")
CONSOLE_PASSWORD = os.environ.get("DIFY_CONSOLE_PASSWORD", "")
# Pinned marketplace package: the checksum makes the installed provider byte-stable.
MODEL_PLUGIN = os.environ.get(
    "QA_MODEL_PLUGIN",
    "langgenius/openai_api_compatible:0.0.73"
    "@4cf123ffaa4556f079bed6c47f3a462cc825504b859b183239e74eb19136a4b5",
)
MODEL_PLUGIN_ID = os.environ.get(
    "QA_MODEL_PLUGIN_ID", "langgenius/openai_api_compatible"
)
MODEL_PROVIDER = os.environ.get(
    "QA_MODEL_PROVIDER", f"{MODEL_PLUGIN_ID}/openai_api_compatible"
)
MODEL_NAME = os.environ.get("QA_MODEL_NAME", "mock-gpt")
MODEL_BASE_URL = os.environ.get("QA_MODEL_BASE_URL", "http://mock-idp:4100/v1")
MODEL_API_KEY = os.environ.get("QA_MODEL_API_KEY", "")
# The engine's cold start (migrations + gunicorn) is measured in minutes; the
# deadline covers the whole bootstrap, not one request.
BOOTSTRAP_TIMEOUT_SECONDS = float(os.environ.get("QA_DIFY_TIMEOUT", "900"))
CONSOLE_REQUEST_TIMEOUT = 30.0
# The marketplace install endpoint downloads the package synchronously.
PLUGIN_INSTALL_TIMEOUT_SECONDS = 300.0
POLL_INTERVAL_SECONDS = 3.0


def log(message: str) -> None:
    print(f"[qa-dify] {message}", flush=True)


def fail(message: str) -> NoReturn:
    raise SystemExit(f"[qa-dify] {message}")


def _body(response: httpx.Response) -> str:
    return response.text.strip()[:500]


class Console:
    """Session-cookie client for the Dify console API used by this bootstrap."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=CONSOLE_BASE, timeout=CONSOLE_REQUEST_TIMEOUT
        )

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        token = self._client.cookies.get("csrf_token")
        return {"X-CSRF-Token": token} if token else {}

    def request(
        self, method: str, path: str, *, timeout: float | None = None, **kwargs: object
    ) -> httpx.Response:
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            return self._client.request(method, path, headers=self._headers(), **kwargs)
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
                "password": base64.b64encode(CONSOLE_PASSWORD.encode("utf-8")).decode(
                    "ascii"
                ),
            },
        )
        if response.status_code != 200:
            fail(
                "console login failed "
                f"({response.status_code}): {_body(response)} — if the console account "
                "predates the generated env file, reset the QA volumes "
                "(`docker compose --env-file deploy/qa/qa.env -f docker-compose.qa.yml "
                "down -v`) or restore deploy/qa/qa.env"
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
        f"engine console API did not become reachable within {BOOTSTRAP_TIMEOUT_SECONDS}s: {last_error}"
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
        json={
            "email": CONSOLE_EMAIL,
            "name": "QA Console",
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


def installed_plugin_ids(console: Console) -> set[str]:
    response = console.request(
        "GET",
        "/console/api/workspaces/current/plugin/list",
        params={"page": 1, "page_size": 100},
    )
    if response.status_code != 200:
        fail(
            f"cannot list installed plugins ({response.status_code}): {_body(response)}"
        )
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
        json={"plugin_unique_identifiers": [MODEL_PLUGIN]},
        # The API downloads the package before answering; a slow marketplace
        # would otherwise look like a dead engine.
        timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS,
    )
    if response.status_code not in (200, 201):
        fail(
            f"plugin install request failed ({response.status_code}): {_body(response)}"
        )
    started = response.json() if response.content else {}
    task_id = str(started.get("task_id") or "")

    deadline = time.monotonic() + BOOTSTRAP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        task = install_task(console, task_id) if task_id else None
        plugins = task.get("plugins") if task else None
        if isinstance(plugins, list):
            for plugin in plugins:
                if (
                    not isinstance(plugin, dict)
                    or plugin.get("plugin_id") != MODEL_PLUGIN_ID
                ):
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


def ensure_model(console: Console) -> None:
    wait_for_provider(console)
    models_response = console.request(
        "GET",
        f"/console/api/workspaces/current/model-providers/{MODEL_PROVIDER}/models",
    )
    if models_response.status_code != 200:
        fail(
            f"cannot list provider models ({models_response.status_code}): {_body(models_response)}"
        )
    existing = {
        model.get("model")
        for model in models_response.json().get("data", [])
        if isinstance(model, dict)
    }
    if MODEL_NAME not in existing:
        if not MODEL_API_KEY:
            fail("QA_MODEL_API_KEY is not set")
        response = console.request(
            "POST",
            f"/console/api/workspaces/current/model-providers/{MODEL_PROVIDER}/models/credentials",
            json={
                "model": MODEL_NAME,
                "model_type": "llm",
                "credentials": {
                    "endpoint_url": MODEL_BASE_URL,
                    "api_key": MODEL_API_KEY,
                    "mode": "chat",
                    "context_size": "4096",
                },
            },
        )
        if response.status_code not in (200, 201):
            fail(
                f"adding model {MODEL_NAME} failed ({response.status_code}): {_body(response)}"
            )
        log(f"model {MODEL_NAME} registered against {MODEL_BASE_URL}")

    # A freshly added custom model is enabled; a model disabled by a previous
    # run stays disabled, and readiness must not depend on that state.
    response = console.request(
        "PATCH",
        f"/console/api/workspaces/current/model-providers/{MODEL_PROVIDER}/models/enable",
        json={"model": MODEL_NAME, "model_type": "llm"},
    )
    if not response.is_success:
        fail(
            f"enabling model {MODEL_NAME} failed ({response.status_code}): {_body(response)}"
        )
    # Defaults keep knowledge-base / app paths that omit an explicit model working.
    response = console.request(
        "POST",
        "/console/api/workspaces/current/default-model",
        json={
            "model_settings": [
                {"model_type": "llm", "provider": MODEL_PROVIDER, "model": MODEL_NAME}
            ]
        },
    )
    if not response.is_success:
        fail(
            f"setting the default model to {MODEL_NAME} failed "
            f"({response.status_code}): {_body(response)}"
        )
    log(f"workspace model {MODEL_PROVIDER} / {MODEL_NAME} is ready")


def main() -> None:
    wait_for_engine()
    console = Console()
    try:
        ensure_setup(console)
        console.login()
        ensure_model_plugin(console)
        ensure_model(console)
    finally:
        console.close()
    log("inference engine is configured")


if __name__ == "__main__":
    main()
    sys.exit(0)
