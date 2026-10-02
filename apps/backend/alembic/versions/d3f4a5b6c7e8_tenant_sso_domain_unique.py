"""unique tenants.sso_domain

Revision ID: d3f4a5b6c7e8
Revises: f0a1b2c3d4e5
Create Date: 2026-09-29 00:00:00.000000

The backend resolves a user's tenant from the email domain of the IdP identity,
so one SSO domain may map to at most one tenant: a duplicate makes every login
from that domain fail with ``TENANT_AMBIGUOUS``. NULL stays free for tenants that
do not use SSO. The lookup case-folds the domain, so case-variant rows remain
possible and are still rejected at login time.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f4a5b6c7e8"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_tenants_sso_domain", "tenants", ["sso_domain"])


def downgrade() -> None:
    op.drop_constraint("uq_tenants_sso_domain", "tenants", type_="unique")
