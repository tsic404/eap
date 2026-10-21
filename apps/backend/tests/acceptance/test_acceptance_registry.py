"""Traceability check for the acceptance suite itself.

Every design case identifier in :mod:`registry` must resolve to exactly one
module declaring that identifier, and no module may exist unregistered.
"""

from __future__ import annotations

import ast
import importlib.util
import types
from pathlib import Path

_MODULE_PREFIX = "test_tc_"


def _registry() -> types.ModuleType:
    """Load ``registry`` by path; the acceptance directory is not a package."""
    path = Path(__file__).with_name("registry.py")
    spec = importlib.util.spec_from_file_location("_acceptance_registry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared_tc_id(module_path: Path) -> str | None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TC_ID" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def _test_functions(module_path: Path) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


def test_registry_resolves_to_declared_modules() -> None:
    registry = _registry()
    assert registry.TECH_TC_IDS, "the registry must list the design's backend cases"

    for tc_id in registry.TECH_TC_IDS:
        path = registry.module_path(tc_id)
        assert path.exists(), f"{tc_id} has no module at {path.name}"
        assert _declared_tc_id(path) == tc_id, f"{path.name} does not declare {tc_id}"
        assert _test_functions(path), f"{path.name} declares no test function"


def test_no_unregistered_case_modules() -> None:
    registry = _registry()
    unknown = []
    for path in sorted(registry.ACCEPTANCE_DIR.glob(f"{_MODULE_PREFIX}*.py")):
        declared = _declared_tc_id(path)
        registered = declared in registry.TECH_TC_IDS
        if not registered or registry.module_path(declared) != path:
            unknown.append(f"{path.name} (declares {declared!r})")
    assert not unknown, f"case modules missing from the registry: {unknown}"
