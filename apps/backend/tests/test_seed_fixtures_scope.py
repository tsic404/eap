"""Scope of the e2e fixture seed: which journeys get rows and which get none.

The same script serves the whole-suite harness (no spec: every fixture) and the
containerised runner (one spec: only that journey's fixtures). The unknown-spec
branch is the one that protects a long-lived stack from an unrelated run, so it
is pinned here rather than only by a manual run. Loaded by path because the
script lives outside the backend package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SEED_SCRIPT = Path(__file__).resolve().parents[3] / "deploy/e2e/seed_fixtures.py"


@pytest.fixture(scope="module")
def seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_seed_fixtures", SEED_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_spec_seeds_every_fixture(seed: ModuleType) -> None:
    """The whole suite runs without a spec: no journey may find its rows missing."""
    assert seed.wanted_fixtures(None) == seed.ALL_FIXTURES
    assert seed.wanted_fixtures("") == seed.ALL_FIXTURES


def test_dashboard_journey_seeds_only_its_own_rows(seed: ModuleType) -> None:
    spec = "e2e/journeys/tc-ops-01-dashboard.spec.ts"
    assert seed.wanted_fixtures(spec) == frozenset({"metrics"})


def test_unrelated_journey_seeds_nothing(seed: ModuleType) -> None:
    """Any other spec must neither seed nor delete: the stack keeps its data."""
    assert seed.wanted_fixtures("e2e/journeys/tc-auth-08-local-login.spec.ts") == frozenset()
