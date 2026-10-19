"""Run acceptance cases by their design identifier.

Run from ``apps/backend`` (the module needs the backend root on ``sys.path``):

    uv run python -m tests.acceptance                  # every backend TC-tech-* case
    uv run python -m tests.acceptance TC-tech-kb-01    # one case, by identifier
    uv run python -m tests.acceptance --list           # identifiers available here

The cases drive the full ASGI stack against containerised PostgreSQL and Redis,
so Docker must be reachable (shared fixtures live in ``tests/conftest.py``).
"""

from __future__ import annotations

import argparse
import sys

import pytest

from .registry import TECH_TC_IDS, module_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.acceptance",
        description="Execute design acceptance cases (TC-tech-*) by identifier.",
    )
    parser.add_argument("tc_ids", nargs="*", metavar="TC-ID", help="case identifiers")
    parser.add_argument("--list", action="store_true", help="list available identifiers")
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(TECH_TC_IDS))
        return 0

    unknown = [tc_id for tc_id in args.tc_ids if tc_id not in TECH_TC_IDS]
    if unknown:
        parser.error(f"unknown acceptance case(s): {', '.join(unknown)}")

    targets = args.tc_ids or list(TECH_TC_IDS)
    return int(pytest.main([*(str(module_path(tc_id)) for tc_id in targets), "-q"]))


if __name__ == "__main__":
    sys.exit(main())
