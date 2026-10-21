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

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, cast

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
# The QA variant is a slimmer local-family shape: six resident services, one
# one-shot seed container.
_QA_RESIDENT_SERVICES = (
    "postgres",
    "redis",
    "mock-idp",
    "backend",
    "frontend",
    "reverse-proxy",
)
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
    """The QA variant keeps the same resident restart policy on its six services."""
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


@pytest.mark.parametrize("service", _RESIDENT_SERVICES)
def test_local_stack_resident_service_declares_a_healthcheck(service: str) -> None:
    """Every resident service must keep a healthcheck, or compose can never
    report it healthy and the readiness baseline silently degrades."""
    resolved = _compose_json(_COMPOSE_LOCAL, _ENV_TEMPLATE)["services"]
    assert "healthcheck" in resolved[service], (
        service,
        "resident service must declare a healthcheck",
    )


def test_local_stack_one_shot_containers_exit_without_restart() -> None:
    """The three one-shot containers must run once and exit, not be supervised."""
    resolved = _compose_json(_COMPOSE_LOCAL, _ENV_TEMPLATE)["services"]
    for service in _ONE_SHOT_SERVICES:
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
