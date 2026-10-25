"""Acceptance-case registry: design identifiers and their executable modules.

``docs/03-acceptance.md`` §用例（TC）fixes the identifier of every case; this
repo carries its executable form under ``tests/acceptance/`` as one module per
identifier (``TC-tech-kb-01`` → ``test_tc_tech_kb_01.py``), each declaring the
same ``TC_ID``. ``TC-tech-ui-01`` is a browser-side case and lives in the
frontend acceptance suite.
"""

from __future__ import annotations

from pathlib import Path

ACCEPTANCE_DIR = Path(__file__).resolve().parent

TECH_TC_IDS: tuple[str, ...] = (
    # The deploy-level case (AC-41 / TC-ops-05) rides this backend suite too.
    "TC-tech-auth-01",
    "TC-tech-auth-02",
    "TC-tech-auth-03",
    "TC-tech-auth-04",
    "TC-tech-auth-05",
    "TC-tech-auth-06",
    "TC-tech-auth-07",
    "TC-tech-auth-08",
    "TC-tech-auth-09",
    "TC-tech-auth-10",
    "TC-tech-auth-12",
    "TC-tech-agent-01",
    "TC-tech-agent-02",
    "TC-tech-kb-01",
    "TC-tech-kb-02",
    "TC-tech-kb-03",
    "TC-tech-conv-01",
    "TC-tech-conv-02",
    "TC-tech-conv-03",
    "TC-tech-tools-01",
    "TC-tech-tools-02",
    "TC-tech-ops-01",
    "TC-tech-ops-02",
    "TC-tech-ops-03",
    "TC-tech-ops-04",
    "TC-tech-ops-08",
    "TC-tech-ops-09",
    "TC-tech-ops-10",
    "TC-tech-ops-11",
    "TC-tech-ops-12",
    "TC-ops-05",
)


def module_name(tc_id: str) -> str:
    """The module filename carrying ``tc_id``."""
    return f"test_{tc_id.lower().replace('-', '_')}.py"


def module_path(tc_id: str) -> Path:
    return ACCEPTANCE_DIR / module_name(tc_id)
