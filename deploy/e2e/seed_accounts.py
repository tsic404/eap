"""Seed the local accounts the browser journeys sign in with.

Invoked inside the running backend container, where the application's own
password module and database settings are importable:

    docker compose -f docker-compose.qa.yml exec -T backend \
        python - < deploy/e2e/seed_accounts.py

The rows mirror ``apps/frontend/e2e/helpers/accounts.cjs``; should the two
drift, the journeys fail to sign in rather than passing on stale fixtures.
Re-running is a no-op: an existing row keeps its password, so a session opened
by an earlier run never sees its credential rotate underneath it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, text

from app.auth.password import hash_password
from app.core.folding import fold
from app.db import async_session_factory
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, LOCAL_SUBJECT_PREFIX, User

PASSWORD = "Local-Account-1"
TENANT_DOMAIN = "acme.com"

# (key, email, name, role, must_change_password) — mirrors accounts.cjs.
ACCOUNTS = (
    ("admin", "carol@acme.com", "Carol", "agent_admin", False),
    ("employee", "dave@acme.com", "Dave", "employee", False),
    ("platformAdmin", "root@acme.com", "Root", "platform_admin", False),
    ("resetTarget", "frank@acme.com", "Frank", "employee", False),
    ("pending", "erin@acme.com", "Erin", "employee", True),
)


async def main() -> None:
    password_hash = hash_password(PASSWORD)
    async with async_session_factory() as session:
        async with session.begin():
            # Same advisory key as the stack's bootstrap seed: a concurrent
            # bring-up and this seed serialize, so the tenant is either the
            # bootstrap's row or plainly absent, never half-created.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('eap:seed:mock-idp'))")
            )
            tenant = await session.scalar(
                select(Tenant).where(Tenant.sso_domain_folded == fold(TENANT_DOMAIN))
            )
            if tenant is None:
                raise SystemExit(
                    f"tenant {TENANT_DOMAIN} is missing; wait for the stack's bootstrap seed"
                )
            for key, email, name, role, must_change in ACCOUNTS:
                exists = await session.scalar(
                    select(User.id).where(
                        User.tenant_id == tenant.id,
                        User.email_folded == fold(email),
                    )
                )
                if exists is not None:
                    continue
                session.add(
                    User(
                        tenant_id=tenant.id,
                        sso_sub=f"{LOCAL_SUBJECT_PREFIX}{key}",
                        email=email,
                        email_folded=fold(email),
                        name=name,
                        role=role,
                        status="active",
                        account_source=ACCOUNT_SOURCE_LOCAL,
                        password_hash=password_hash,
                        must_change_password=must_change,
                    )
                )
    print(f"e2e: local accounts ready on {TENANT_DOMAIN}")


if __name__ == "__main__":
    asyncio.run(main())
