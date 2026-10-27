"""Acceptance case for the deployment verification: container baseline, probes and
production hard failure.

AC-41 / TC-ops-05 — the local/development family must keep its 10 resident and 3
one-shot containers, the liveness and entry health endpoints answer 200 with a
normal status, both orchestration shapes validate, and the production shape exits
non-zero when any required key is missing, naming the key. The container-level
health facts are asserted statically from the committed orchestrations (same
evidence the QA stack reads through ``docker compose ps`` / ``docker inspect``),
the probes are exercised against the real ASGI app, and the hard failure is
executed with the compose CLI itself.

The probe clauses are the deployment-facing half of C-17 / C-18 (the functional
detail, including per-dependency flagging and the time caps, is TC-tech-ops-01).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

TC_ID = "TC-ops-05"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE_LOCAL = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_QA = _REPO_ROOT / "docker-compose.qa.yml"
_COMPOSE_PROD = _REPO_ROOT / "docker-compose.prod.yml"
_ENV_TEMPLATE = _REPO_ROOT / ".env.example"
# The local/development family is exactly the local main stack and its QA
# variant; no other commit has a resident/one-shot baseline of its own.
_RESIDENT_SERVICES = (
    "backend",
    "dify-api",
    "dify-worker",
    "frontend",
    "mock-idp",
    "nginx",
    "plugin-daemon",
    "postgres",
    "redis",
    "weaviate",
)
_ONE_SHOT_SERVICES = ("db-seed", "dify-db-init", "init_permissions")
# The QA variant is the same local-family shape as the main stack (C-88): the
# same ten resident services, with the reverse proxy standing in for nginx.
_QA_RESIDENT_SERVICES = (
    "backend",
    "dify-api",
    "dify-worker",
    "frontend",
    "mock-idp",
    "plugin-daemon",
    "postgres",
    "redis",
    "reverse-proxy",
    "weaviate",
)
_QA_ONE_SHOT_SERVICES = ("db-seed", "dify-db-init", "init_permissions")
_BACKEND_CONTAINER_PORT = 3001

# C-93 fixes the size of the production required set; TC-tech-ops-02 pins the
# count, this case only checks the fail-if-missing mechanism stays loud.
_COMPOSE_ENV_VARS = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?")


def _compose_env() -> dict[str, str]:
    """The environment handed to the compose CLI.

    Shell exports take precedence over an env file, so the caller's environment
    minus every variable the committed template defines (same rule as
    TC-tech-ops-02): a leftover export would hide the missing states under test.
    """
    template_keys = {
        line.split("=", 1)[0].strip()
        for line in _ENV_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[ \t]*[A-Z][A-Z0-9_]*[ \t]*=", line)
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key not in template_keys and not key.startswith("COMPOSE_")
    }


def _compose_json(compose: Path, env_file: Path) -> dict[str, Any]:
    """The compose CLI's fully interpolated view of one orchestration."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        ],
        cwd=_REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def _required_keys(compose: Path) -> tuple[str, ...]:
    """Every key the orchestration declares fail-if-missing (``${KEY:?}``)."""
    lines = compose.read_text(encoding="utf-8").splitlines()
    uncommented = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    return tuple(sorted(set(_COMPOSE_ENV_VARS.findall(uncommented))))


def _env_missing(tmp_path: Path, key: str) -> Path:
    """A copy of the committed template with ``key`` dropped.

    Every other declared variable stays (placeholder values included), so the
    compose run can only trip on ``key`` — whichever missing variable compose
    happens to report first, it has to be this one.
    """
    lines = _ENV_TEMPLATE.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not re.match(rf"^[ \t]*{re.escape(key)}[ \t]*=", line)]
    path = tmp_path / f"missing-{key}.env"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return path


# ── ① compose container baseline (resident 10, one-shot 3) ──────────────────


@pytest.mark.parametrize("service", _RESIDENT_SERVICES)
def test_local_stack_resident_service_stays_long_lived(service: str) -> None:
    """Every resident service must keep a supervising restart policy."""
    text = _COMPOSE_LOCAL.read_text(encoding="utf-8")
    match = re.search(
        rf"^  {service}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_.-]+:|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"service {service} missing from docker-compose.yml"
    assert re.search(r"^ {4}restart: unless-stopped\s*$", match.group("body"), re.MULTILINE), (
        service,
        "resident service must restart on failure",
    )


def test_qa_stack_resident_services_stay_long_lived() -> None:
    """The QA variant keeps the local-family resident restart policy."""
    text = _COMPOSE_QA.read_text(encoding="utf-8")
    for service in _QA_RESIDENT_SERVICES:
        match = re.search(
            rf"^  {service}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_.-]+:|\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        assert match is not None, f"service {service} missing from docker-compose.qa.yml"
        assert re.search(r"^ {4}restart: unless-stopped\s*$", match.group("body"), re.MULTILINE), (
            service,
            "resident service must restart on failure",
        )


def test_local_stack_resident_container_count_stays_ten() -> None:
    """Resolved state must keep 10 resident services, one per committed name."""
    services = _compose_json(_COMPOSE_LOCAL, _ENV_TEMPLATE)["services"]
    assert set(services) == set(_RESIDENT_SERVICES) | set(_ONE_SHOT_SERVICES)
    assert len(services) == 13


def test_qa_stack_resident_container_count_stays_ten() -> None:
    """The QA variant carries the same ten resident and three one-shot services
    as the local main stack (C-88): the engine is part of its readiness baseline."""
    services = _compose_json(_COMPOSE_QA, _ENV_TEMPLATE)["services"]
    assert set(services) == set(_QA_RESIDENT_SERVICES) | set(_QA_ONE_SHOT_SERVICES)
    assert len(services) == 13


@pytest.mark.parametrize("service", _RESIDENT_SERVICES)
def test_local_stack_resident_service_declares_a_healthcheck(service: str) -> None:
    """Every resident service must keep a healthcheck, or compose can never
    report it healthy and the readiness baseline silently degrades."""
    resolved = _compose_json(_COMPOSE_LOCAL, _ENV_TEMPLATE)["services"]
    assert "healthcheck" in resolved[service], (
        service,
        "resident service must declare a healthcheck",
    )


@pytest.mark.parametrize("service", _QA_RESIDENT_SERVICES)
def test_qa_stack_resident_service_declares_a_healthcheck(service: str) -> None:
    """The QA variant's residents must stay observable as healthy too."""
    resolved = _compose_json(_COMPOSE_QA, _ENV_TEMPLATE)["services"]
    assert "healthcheck" in resolved[service], (
        service,
        "resident service must declare a healthcheck",
    )


def test_local_stack_one_shot_containers_exit_without_restart() -> None:
    """The three one-shot containers must run once and exit, not be supervised."""
    resolved = _compose_json(_COMPOSE_LOCAL, _ENV_TEMPLATE)["services"]
    for service in _ONE_SHOT_SERVICES:
        assert resolved[service].get("restart") == "no", service


def test_qa_stack_one_shot_containers_exit_without_restart() -> None:
    """The QA variant's bootstraps stay one-shot: their exit codes are readiness."""
    resolved = _compose_json(_COMPOSE_QA, _ENV_TEMPLATE)["services"]
    for service in _QA_ONE_SHOT_SERVICES:
        assert resolved[service].get("restart") == "no", service


# ── ② probes ────────────────────────────────────────────────────────────────


def _refused_endpoint() -> str:
    """A local ``host:port`` with nothing listening — connects are refused at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


def test_backend_container_probe_checks_its_liveness_endpoint() -> None:
    """The backend healthcheck must hit the liveness endpoint from inside."""
    text = _COMPOSE_LOCAL.read_text(encoding="utf-8")
    match = re.search(
        r"^  backend:\n(?P<body>.*?)(?=^  [A-Za-z0-9_.-]+:|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    body = match.group("body")
    assert re.search(r"api/health/live", body), "backend healthcheck must probe /api/health/live"
    assert str(_BACKEND_CONTAINER_PORT) in body


@pytest.mark.asyncio
async def test_liveness_probe_is_raw_200_with_status_ok(api) -> None:  # type: ignore[no-untyped-def]
    """Liveness answers 200 with a normal status while the process is up,
    no matter what state the dependencies are in."""
    resp = await api.client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_entry_health_endpoint_is_raw_200(api) -> None:  # type: ignore[no-untyped-def]
    """The entry (/api/health) answers 200 with a normal status."""
    resp = await api.client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"
    assert "data" not in body


@pytest.mark.asyncio
async def test_readiness_probe_answers_503_before_dependencies_are_ready(api) -> None:  # type: ignore[no-untyped-def]
    """Readiness stays 503 (degraded) while a dependency is not ready, keeping
    the live-entry distinction observable."""
    # The engine target is refused deterministically: the fixture's default
    # settings would otherwise depend on whether the host answers on :5001.
    api.app.state.settings = api.settings.model_copy(
        update={"dify_api_base_url": f"http://{_refused_endpoint()}"}
    )
    resp = await api.client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert set(body["checks"]) == {"database", "redis", "dify"}
    assert body["checks"]["dify"] == "error"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


# ── ③ production hard failure ───────────────────────────────────────────────


@pytest.mark.parametrize("key", _required_keys(_COMPOSE_PROD))
def test_production_shape_fails_missing_required_key_by_name(tmp_path: Path, key: str) -> None:
    """The production orchestration fails loudly when any required key is
    missing, naming the key it missed.

    Only the key under test is dropped, so whichever missing variable compose
    reports first has to be that one — this stays true across compose versions
    that stop at the first interpolation error.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(_env_missing(tmp_path, key)),
            "-f",
            str(_COMPOSE_PROD),
            "config",
            "-q",
        ],
        cwd=_REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "production config must not resolve with keys missing"
    assert key in result.stderr


def test_production_shape_rejects_an_empty_env_file() -> None:
    """Any required key missing must exit non-zero, not fall back to a default."""
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f", str(_COMPOSE_PROD), "config", "-q"],
        cwd=_REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stderr


# ── ④ QA bring-up bootstrap scripts ─────────────────────────────────────────

_QA_SECRETS_SCRIPT = _REPO_ROOT / "scripts" / "gen-qa-secrets.sh"
_QA_DIFY_BOOTSTRAP = _REPO_ROOT / "deploy" / "qa" / "dify_bootstrap.py"
_ENV_ASSIGNMENT = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$")


def _run_qa_secrets(output: Path) -> subprocess.CompletedProcess[str]:
    """Run the QA secret generator into ``output``, as `make qa-secrets` does."""
    return subprocess.run(
        [str(_QA_SECRETS_SCRIPT), str(output)],
        cwd=_REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _qa_secrets_env(tmp_path: Path) -> Path:
    output = tmp_path / "qa.env"
    result = _run_qa_secrets(output)
    assert result.returncode == 0, result.stderr
    return output


def _env_assignments(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if match:
            entries[match.group(1)] = match.group(2)
    return entries


def _qa_compose_config(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f", str(_COMPOSE_QA), "config", "-q"],
        cwd=_REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_qa_secret_generator_satisfies_every_required_qa_key(tmp_path: Path) -> None:
    """The generated file is the QA stack's only value source, so it must make
    the orchestration resolve on its own — one key short fails the bring-up at
    configuration time. The file stays owner-only: it holds the live secrets."""
    env_file = _qa_secrets_env(tmp_path)

    assert (env_file.stat().st_mode & 0o777) == 0o600
    values = _env_assignments(env_file)
    for key in _required_keys(_COMPOSE_QA):
        assert values.get(key), key
    result = _qa_compose_config(env_file)
    assert result.returncode == 0, result.stderr


def test_qa_secret_generator_is_idempotent(tmp_path: Path) -> None:
    """A restart reuses the generated values: re-running must not rotate the
    engine session key or the console password out from under existing volumes."""
    env_file = _qa_secrets_env(tmp_path)
    first = _env_assignments(env_file)

    result = _run_qa_secrets(env_file)

    assert result.returncode == 0, result.stderr
    assert _env_assignments(env_file) == first


def test_qa_compose_rejects_a_secret_file_missing_a_required_key(tmp_path: Path) -> None:
    """`--env-file` replaces `.env`, so a dropped key cannot be rescued from a
    checkout-level file: the stack must fail by name instead of starting with a
    placeholder."""
    env_file = _qa_secrets_env(tmp_path)
    kept = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if not line.startswith("QA_MODEL_API_KEY=")
    ]
    env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")

    result = _qa_compose_config(env_file)

    assert result.returncode != 0
    assert "QA_MODEL_API_KEY" in result.stderr


class _EngineConsole:
    """The console calls ``ensure_model`` makes, answering per step."""

    def __init__(
        self, *, provider: str, model: str, enable_status: int, default_status: int
    ) -> None:
        self._provider = provider
        self._model = model
        self._enable_status = enable_status
        self._default_status = default_status

    def request(self, method: str, path: str, **_kwargs: object) -> httpx.Response:
        if path.endswith("/models/enable"):
            return httpx.Response(self._enable_status, text="enable rejected")
        if path.endswith("/default-model"):
            return httpx.Response(self._default_status, text="default rejected")
        if path.endswith("/model-providers"):
            return httpx.Response(200, json={"data": [{"provider": self._provider}]})
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"model": self._model}]})
        raise AssertionError(f"unexpected console call: {method} {path}")


def _load_dify_bootstrap() -> Any:
    spec = importlib.util.spec_from_file_location("qa_dify_bootstrap", _QA_DIFY_BOOTSTRAP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("enable_status", "default_status", "expected"),
    [
        pytest.param(200, 200, "", id="both-accepted"),
        pytest.param(500, 200, "enabling model", id="enable-rejected"),
        pytest.param(200, 500, "default model", id="default-rejected"),
    ],
)
def test_qa_engine_bootstrap_exits_non_zero_when_a_model_step_is_rejected(
    enable_status: int, default_status: int, expected: str
) -> None:
    """The one-shot's exit code is part of the readiness baseline (C-88), so a
    rejected enable / default-model step must fail the bootstrap. Both are
    configuration later requests depend on: finishing 0 would publish a stack
    that only fails at the first knowledge-base or app call."""
    module = _load_dify_bootstrap()
    console = _EngineConsole(
        provider=str(module.MODEL_PROVIDER),
        model=str(module.MODEL_NAME),
        enable_status=enable_status,
        default_status=default_status,
    )

    if not expected:
        module.ensure_model(console)
        return

    with pytest.raises(SystemExit) as caught:
        module.ensure_model(console)
    assert caught.value.code not in (None, 0)
    assert expected in str(caught.value.code)
