"""Acceptance case for the deployment shapes: secret tri-state, production hard
failure, the stub's carrier range, and the same-origin API base.

Every orchestration is exercised through the compose CLI with an explicitly
controlled environment and a temporary copy of the committed environment
template, so the developer's own environment file is never read or written and
nothing outside ``tmp_path`` is touched. The production required keys are derived
from the orchestration file itself rather than from a list kept here.

The last clause (a browser issuing same-origin API requests) is a deployment-layer
fact from this suite's side: the entry proxy routes the API and the frontend under
one origin, the backend is never published on its own port, and no absolute
backend origin is baked into the frontend's public API base or its image build.
That a running browser really issues the relative path is pinned by the frontend
suite.
"""

import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path

import pytest

TC_ID = "TC-tech-ops-02"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_TEMPLATE = _REPO_ROOT / ".env.example"
_LOCAL_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_QA_COMPOSE = _REPO_ROOT / "docker-compose.qa.yml"
_PROD_COMPOSE = _REPO_ROOT / "docker-compose.prod.yml"
# The local/development family: the local main stack and its QA variant. Every
# other orchestration in the repository is a non-local shape.
_LOCAL_FAMILY = frozenset({_LOCAL_COMPOSE.name, _QA_COMPOSE.name})
_BACKEND_CONTAINER_PORT = 3001

_REQUIRED_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?")
_INLINE_DEFAULT_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-")
# A required declaration that also carries the diagnostic the operator reads.
_DECLARED_REQUIRED_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?([^}]*)\}")
_ENV_LINE = re.compile(r"^[ \t]*([A-Z][A-Z0-9_]*)[ \t]*=(.*)$")
# The bootstrap command is the Makefile target, which runs the environment
# bootstrap and the JWT keypair generation beside it.
_BOOTSTRAP_COMMAND = ("make", "setup-env")
_BOOTSTRAP_SCRIPTS = ("setup-env.sh", "gen-jwt-keys.sh")
_BOOTSTRAP_INPUTS = ("Makefile", ".env.example")


def _uncommented(path: Path) -> str:
    """The orchestration file's text without its full-line comments."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _required_keys(compose: Path) -> tuple[str, ...]:
    """Every key the orchestration declares fail-if-missing (``${KEY:?}``)."""
    return tuple(sorted(set(_REQUIRED_VAR.findall(_uncommented(compose)))))


def _inline_defaults(compose: Path) -> frozenset[str]:
    """Every key the orchestration gives a fallback value (``${KEY:-...}``)."""
    return frozenset(_INLINE_DEFAULT_VAR.findall(_uncommented(compose)))


def _template_keys(path: Path = _ENV_TEMPLATE) -> frozenset[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line)
        if match:
            keys.add(match.group(1))
    return frozenset(keys)


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _compose_env() -> dict[str, str]:
    """The environment handed to the compose CLI.

    The caller's environment minus every variable the committed template defines —
    shell exports take precedence over an env file, so leaving one in place would
    hide exactly the missing/empty states this case asserts — and minus the
    ``COMPOSE_*`` overrides that would silently swap the files under test.
    """
    template_keys = _template_keys()
    return {
        key: value
        for key, value in os.environ.items()
        if key not in template_keys and not key.startswith("COMPOSE_")
    }


def _template_copy(tmp_path: Path, name: str = "template.env") -> Path:
    """A verbatim copy of the committed template: placeholders and dev defaults intact."""
    path = tmp_path / name
    path.write_text(_ENV_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _template_placeholders(path: Path = _ENV_TEMPLATE) -> dict[str, str]:
    """Template entries holding a placeholder rather than a usable value.

    The template documents its own convention: a value carrying ``replace-me`` is
    one nobody can authenticate with — a literal ``replace-me-*``, or the reserved
    non-resolving IdP host — as opposed to the dev defaults it deliberately keeps
    for local development (see the template header).
    """
    return {key: value for key, value in _env_values(path).items() if "replace-me" in value}


def _bootstrap_root(tmp_path: Path) -> Path:
    """A throwaway repository root carrying the committed bootstrap command.

    The command writes its environment file and its JWT keypair beside the files
    it runs, so it is executed in a copy: the developer's own ``.env`` and
    ``deploy/keys`` are never read or written.
    """
    root = tmp_path / "bootstrap"
    (root / "scripts").mkdir(parents=True)
    for name in _BOOTSTRAP_INPUTS:
        (root / name).write_text((_REPO_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    for name in _BOOTSTRAP_SCRIPTS:
        script = root / "scripts" / name
        script.write_text(
            (_REPO_ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
        script.chmod(0o755)
    return root


def _run_bootstrap(
    root: Path,
    env_file: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the bootstrap command itself against ``env_file``, inside ``root``."""
    # The make flags a caller may have exported describe the outer build, not this
    # one; leaving them in place makes the sub-make warn about them.
    env = {
        key: value
        for key, value in _compose_env().items()
        if key not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")
    }
    return subprocess.run(
        list(_BOOTSTRAP_COMMAND),
        cwd=root,
        env={
            **env,
            **(extra_env or {}),
            "ENV_FILE": str(env_file),
            "ENV_EXAMPLE": str(root / ".env.example"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _env_with(tmp_path: Path, key: str, value: str | None, name: str) -> Path:
    """A template copy where ``key`` is set to ``value``, or dropped when ``None``."""
    lines: list[str] = []
    replaced = False
    for line in _ENV_TEMPLATE.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line)
        if match and match.group(1) == key:
            replaced = True
            if value is not None:
                lines.append(f"{key}={value}")
            continue
        lines.append(line)
    assert replaced, f"{key} is not part of the environment template"
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _compose_config(
    compose: Path, env_file: Path, *, as_json: bool = False
) -> subprocess.CompletedProcess[str]:
    """Resolve one orchestration with ``env_file`` as the only variable source."""
    command = ["docker", "compose", "--env-file", str(env_file), "-f", str(compose), "config"]
    command += ["--format", "json"] if as_json else ["-q"]
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env=_compose_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _resolved(compose: Path, env_file: Path) -> dict:
    """The compose CLI's fully interpolated view of one orchestration."""
    result = _compose_config(compose, env_file, as_json=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _orchestrations() -> tuple[Path, ...]:
    return tuple(sorted(_REPO_ROOT.glob("docker-compose*.yml")))


def _non_local_orchestrations() -> tuple[Path, ...]:
    return tuple(path for path in _orchestrations() if path.name not in _LOCAL_FAMILY)


_PROD_REQUIRED_KEYS = _required_keys(_PROD_COMPOSE)


@pytest.mark.parametrize(
    "compose",
    [_LOCAL_COMPOSE, _QA_COMPOSE, _PROD_COMPOSE],
    ids=["local", "qa", "production"],
)
def test_template_placeholder_values_resolve_in_every_shape(tmp_path, compose: Path) -> None:
    env_file = _template_copy(tmp_path)

    result = _compose_config(compose, env_file)

    # The committed template satisfies every `${KEY:?}` it ships: placeholders pass.
    assert result.returncode == 0, result.stderr


def test_production_shape_rejects_a_missing_environment_file(tmp_path) -> None:
    absent = tmp_path / "absent.env"

    result = _compose_config(_PROD_COMPOSE, absent)

    assert result.returncode != 0
    # Nothing can be resolved from an environment file that is not there.
    assert str(absent) in result.stderr + result.stdout


@pytest.mark.parametrize("key", _PROD_REQUIRED_KEYS)
def test_production_shape_rejects_a_missing_required_key_naming_it(tmp_path, key: str) -> None:
    env_file = _env_with(tmp_path, key, None, f"missing-{key}.env")

    result = _compose_config(_PROD_COMPOSE, env_file)

    assert result.returncode != 0
    assert key in result.stderr


@pytest.mark.parametrize("key", _PROD_REQUIRED_KEYS)
def test_production_shape_rejects_an_empty_required_key_naming_it(tmp_path, key: str) -> None:
    env_file = _env_with(tmp_path, key, "", f"empty-{key}.env")

    result = _compose_config(_PROD_COMPOSE, env_file)

    # An empty value is as unusable as an absent one: the same key fails the shape.
    assert result.returncode != 0
    assert key in result.stderr


def test_production_required_key_set_matches_the_contracted_size() -> None:
    # C-93 fixes the size of the production required set.
    assert len(_PROD_REQUIRED_KEYS) == 14
    # Each of them is declared fail-if-missing *with* the diagnostic an operator
    # reads, rather than beside a fallback value.
    declared = _DECLARED_REQUIRED_VAR.findall(_uncommented(_PROD_COMPOSE))
    assert set(_PROD_REQUIRED_KEYS) <= {key for key, _ in declared}
    unnamed = sorted({key for key, message in declared if key not in message})
    assert not unnamed, unnamed


def test_production_shape_gives_no_required_key_an_inline_default() -> None:
    # A fallback value would ship a usable-looking secret the template never provided.
    assert _inline_defaults(_PROD_COMPOSE).isdisjoint(_PROD_REQUIRED_KEYS)


# The stub family a non-local shape must never carry: the fixed fixture service
# names, plus the whole `MOCK_` configuration prefix, so a new stub key (the
# model stub's `MOCK_MODEL_*`) cannot slip past a single literal.
_STUB_SERVICE_NAMES = ("mock-idp", "mock-model")
_STUB_CONFIG_PREFIX = "MOCK_"


def test_non_local_shape_carries_no_stub_service_or_stub_config(tmp_path) -> None:
    env_file = _template_copy(tmp_path)
    non_local = _non_local_orchestrations()
    assert non_local, "the production shape must exist as an orchestration"

    # Control: the QA variant carries both stub families (identity and model),
    # so the scan below has teeth for service names and for the config prefix.
    qa_text = _QA_COMPOSE.read_text(encoding="utf-8")
    for marker in (
        *_STUB_SERVICE_NAMES,
        f"{_STUB_CONFIG_PREFIX}IDP",
        f"{_STUB_CONFIG_PREFIX}MODEL_",
    ):
        assert marker in qa_text, marker

    for compose in non_local:
        text = compose.read_text(encoding="utf-8")
        for name in _STUB_SERVICE_NAMES:
            assert name not in text, (compose.name, name)
        assert _STUB_CONFIG_PREFIX not in text, compose.name

        resolved = _resolved(compose, env_file)
        assert not [name for name in resolved["services"] if "mock" in name.lower()], compose.name
        dumped = json.dumps(resolved)
        for name in _STUB_SERVICE_NAMES:
            assert name not in dumped, (compose.name, name)
        assert _STUB_CONFIG_PREFIX not in dumped, compose.name


def test_non_local_shape_never_points_its_token_keys_at_a_repo_directory(tmp_path) -> None:
    env_file = _template_copy(tmp_path)

    for compose in _non_local_orchestrations():
        text = compose.read_text(encoding="utf-8")
        assert "_KEY_FILE" not in text, compose.name
        assert "deploy/keys" not in text, compose.name
        assert "deploy/qa/keys" not in text, compose.name

        resolved = _resolved(compose, env_file)
        assert not resolved.get("secrets"), compose.name
        for service in resolved["services"].values():
            for key, value in (service.get("environment") or {}).items():
                assert not str(key).endswith("_KEY_FILE"), (compose.name, key)
                assert "/deploy/keys" not in str(value), (compose.name, key)


def test_bootstrap_command_fills_the_local_required_secrets(tmp_path) -> None:
    root = _bootstrap_root(tmp_path)
    env_file = _template_copy(tmp_path, "bootstrapped.env")

    result = _run_bootstrap(root, env_file)

    assert result.returncode == 0, result.stderr
    # The command is the whole target: the environment bootstrap and the keypair
    # generation the local stack mounts beside it.
    keys = root / "deploy" / "keys"
    assert "BEGIN PRIVATE KEY" in (keys / "jwt_private.pem").read_text(encoding="utf-8")
    assert "PUBLIC KEY" in (keys / "jwt_public.pem").read_text(encoding="utf-8")

    values = _env_values(env_file)
    for key in _required_keys(_LOCAL_COMPOSE):
        value = values.get(key, "")
        assert value, key
        assert not value.startswith("replace-me"), (key, value)
    # The replaced values keep the production shape resolvable as well.
    assert _compose_config(_PROD_COMPOSE, env_file).returncode == 0


@pytest.mark.parametrize(
    ("key", "exported"),
    [
        pytest.param(
            "OIDC_ISSUER",
            _template_placeholders()["OIDC_ISSUER"],
            id="template-placeholder",
        ),
        pytest.param("WEAVIATE_API_KEY", "", id="empty"),
    ],
)
def test_bootstrap_command_refuses_an_exported_unusable_value(
    tmp_path, key: str, exported: str
) -> None:
    # A shell export outranks .env at compose time, so the bootstrap command must
    # not report success while the exported value — empty, or the template's own
    # placeholder — would still reach the running stack.
    root = _bootstrap_root(tmp_path)
    env_file = _template_copy(tmp_path, "bootstrapped.env")

    result = _run_bootstrap(root, env_file, extra_env={key: exported})

    assert result.returncode != 0
    assert key in result.stderr
    # The command fails before replacing this key: the file keeps the template
    # placeholder rather than gaining a value the export would shadow.
    assert _env_values(env_file)[key] == _template_placeholders()[key]


def test_bootstrap_command_replaces_every_template_placeholder(tmp_path) -> None:
    root = _bootstrap_root(tmp_path)
    env_file = _template_copy(tmp_path, "bootstrapped.env")

    result = _run_bootstrap(root, env_file)

    assert result.returncode == 0, result.stderr
    # C-92 ③: no template placeholder survives the bootstrap command — whatever
    # the template shipped as unusable is either replaced or gone afterwards. (The
    # dev defaults the same command deliberately keeps are not placeholders.)
    values = _env_values(env_file)
    survivors = {
        key: value
        for key, value in _template_placeholders().items()
        if values.get(key, "") == value
    }
    assert not survivors, survivors


def test_bootstrap_command_clears_the_placeholder_under_an_exported_value(tmp_path) -> None:
    # A shell export overrides .env at compose time, but C-92 ③ judges the file:
    # the template placeholder must still be gone, and the caller's value stays
    # in the environment rather than being copied to disk.
    root = _bootstrap_root(tmp_path)
    env_file = _template_copy(tmp_path, "bootstrapped.env")
    key = "WEAVIATE_API_KEY"
    exported = "exported-real-key"

    result = _run_bootstrap(root, env_file, extra_env={key: exported})

    assert result.returncode == 0, result.stderr
    value = _env_values(env_file)[key]
    assert value and value != _template_placeholders()[key], value
    assert value != exported


def test_entry_proxy_serves_the_api_and_the_frontend_under_one_origin(tmp_path) -> None:
    env_file = _template_copy(tmp_path)

    resolved = _resolved(_PROD_COMPOSE, env_file)
    entry = resolved["services"]["nginx"]

    # One published origin, and the backend is never published beside it.
    assert len(entry["ports"]) == 1
    assert "ports" not in resolved["services"]["backend"]
    assert resolved["services"]["backend"]["expose"] == [str(_BACKEND_CONTAINER_PORT)]

    conf_path = Path(entry["volumes"][0]["source"])
    assert conf_path.name == "nginx.conf"
    conf = conf_path.read_text(encoding="utf-8")
    assert conf.count("listen ") == 1
    assert re.search(r"location\s+/api/\s*\{[^}]*proxy_pass\s+\$backend_upstream", conf)
    assert re.search(r"location\s+/\s*\{[^}]*proxy_pass\s+http://frontend:3000", conf)


def test_frontend_public_api_base_is_a_same_origin_relative_path(tmp_path) -> None:
    env_file = _template_copy(tmp_path)

    for compose in _orchestrations():
        resolved = _resolved(compose, env_file)
        base = resolved["services"]["frontend"]["environment"]["NEXT_PUBLIC_API_BASE_URL"]
        split = urllib.parse.urlsplit(base)

        # A same-origin relative path carries neither a scheme nor a host: any
        # absolute base — the entry's own host included — is derived from the
        # published origin, which C-16 rules out.
        assert (split.scheme, split.netloc) == ("", ""), (compose.name, base)
        assert split.path == "/api", (compose.name, base)


def test_frontend_public_api_base_keeps_the_same_origin_api_path(tmp_path) -> None:
    env_file = _template_copy(tmp_path)

    for compose in _orchestrations():
        resolved = _resolved(compose, env_file)
        base = resolved["services"]["frontend"]["environment"]["NEXT_PUBLIC_API_BASE_URL"]
        split = urllib.parse.urlsplit(base)

        assert split.path == "/api", (compose.name, base)
        # Never a distinct backend origin: the API rides the entry's own origin.
        # (The relative-shape clause has its own case above.)
        assert split.port != _BACKEND_CONTAINER_PORT, (compose.name, base)
        assert "backend" not in (split.hostname or ""), (compose.name, base)


def test_frontend_image_bakes_no_backend_origin_into_its_public_api_base(tmp_path) -> None:
    env_file = _template_copy(tmp_path)
    dockerfile = (_REPO_ROOT / "apps/frontend/Dockerfile").read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_API_BASE_URL" not in dockerfile

    for compose in _orchestrations():
        resolved = _resolved(compose, env_file)
        build_args = (resolved["services"]["frontend"].get("build") or {}).get("args") or {}
        # Nothing is handed to the image build that could be baked into it.
        assert "NEXT_PUBLIC_API_BASE_URL" not in build_args, compose.name

    # `.dockerignore` keeps the template's environment file out of the build
    # context, so that one cannot be copied in either. (The Next.js environment
    # files a developer keeps locally — `.env.local` and friends — are not
    # dockerignored; this case claims nothing about them.)
    ignored = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignored
