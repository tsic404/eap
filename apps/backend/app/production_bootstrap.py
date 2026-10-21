"""Production-shape deployment bootstrap: tenant/domain + first local admin (C-119 ~ C-121).

The production shape seeds nothing by itself: a standard deployment that never
runs this action holds no tenant (domained or not) and no platform admin of any
source (C-119). The deployment operator executes this module once per tenant at
deployment time; it is not a resident service, not a runtime endpoint, not a
scheduled job and not part of the C-95 one-shot container set.

Run with ``python -m app.production_bootstrap`` after ``alembic upgrade head``
with the tenant name/domain (and, only when the first admin must be created,
the admin email/name/initial password) provided as arguments.

A run answers exactly one of four results (C-121). Tenant/domain writes, the
admin write and the system audit records commit together — the C-81/C-106
exception — so an unreadable audit record fails the whole run and leaves no
write of this run behind.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TextIO, cast

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import format_error, hash_password
from app.auth.tenants import resolve_tenant_by_email_domain
from app.config import get_settings
from app.core.exceptions import AuthError
from app.db import async_session_factory
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, LOCAL_SUBJECT_PREFIX, User

log = structlog.get_logger(__name__)

PLATFORM_ADMIN_ROLE = "platform_admin"
# Action codes are dot-named (C-81); the audit user is empty because the
# platform has no authenticated actor during deployment (C-119 / C-120).
ACTION_TENANT_CREATED = "bootstrap.tenant_created"
ACTION_ADMIN_CREATED = "bootstrap.platform_admin_created"
RESOURCE_TENANT = "tenant"
RESOURCE_USER = "user"

# One lock name for the whole bootstrap surface; serializes concurrent runs so
# two of them can never create two case-fold-equal tenant domains (C-119).
_ADVISORY_LOCK_KEY = "eap:production-bootstrap"


class BootstrapResult(StrEnum):
    """The four distinguishable outcomes of one bootstrap run (C-121)."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True)
class Outcome:
    """The observable result of a run and the operator-facing reason, if any."""

    result: BootstrapResult
    reason: str = ""


class BootstrapError(Exception):
    """A run-level failure with a stable, operator-visible reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.production_bootstrap",
        description=(
            "Deployment-time bootstrap of one tenant/domain and, when needed, "
            "the first local platform administrator (C-119 ~ C-121)."
        ),
    )
    parser.add_argument("tenant_name", help="non-empty display name of the tenant")
    parser.add_argument("tenant_domain", help="non-empty email domain the login paths resolve")
    parser.add_argument("--admin-email", help="admin's email, inside the tenant domain")
    parser.add_argument("--admin-name", help="admin's non-empty display name")
    parser.add_argument(
        "--admin-password",
        help=(
            "admin's initial password given on the command line (appears in shell "
            "history and process listings; prefer --admin-password-env or "
            "--admin-password-stdin)"
        ),
    )
    parser.add_argument(
        "--admin-password-env",
        metavar="VAR",
        help="read the initial password from environment variable VAR",
    )
    parser.add_argument(
        "--admin-password-stdin",
        action="store_true",
        help="read the initial password from the first line of stdin",
    )
    return parser


def resolve_admin_password(
    *,
    inline: str | None,
    env_var: str | None,
    use_stdin: bool,
    environ: Mapping[str, str],
    stdin: TextIO,
) -> str | None:
    """Resolve the initial password from exactly one of the three channels.

    ``--admin-password`` is the least private channel: the value shows up in
    the deployment host's shell history and in ``ps`` / ``/proc/<pid>/cmdline``.
    The environment and stdin channels keep it out of argv; the deployment
    checklist uses stdin.
    """
    chosen = sum((inline is not None, env_var is not None, use_stdin))
    if chosen > 1:
        raise ValueError(
            "use only one of --admin-password, --admin-password-env, --admin-password-stdin"
        )
    if env_var is not None:
        if env_var not in environ:
            raise ValueError(f"environment variable {env_var} is not set")
        return environ[env_var]
    if use_stdin:
        line = stdin.readline()
        if line == "":
            raise ValueError("no admin password was provided on stdin")
        return line.rstrip("\r\n")
    return inline


async def bootstrap(
    session: AsyncSession,
    *,
    tenant_name: object,
    tenant_domain: object,
    admin_email: object = None,
    admin_name: object = None,
    admin_password: object = None,
    local_login_enabled: bool | None = None,
) -> Outcome:
    """Run one bootstrap action; see the module docstring for the contract.

    ``local_login_enabled`` overrides the environment capability for callers
    (tests); None reads the deployment setting (C-110).
    """
    if clean := _validate_inputs(tenant_name, tenant_domain):
        return clean

    enabled = (
        get_settings().local_login_enabled if local_login_enabled is None else local_login_enabled
    )

    # Serialize concurrent runs: overlapping bring-ups see each other's rows
    # instead of racing them (same idiom as the local-family seed).
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _ADVISORY_LOCK_KEY}
    )
    try:
        return await _bootstrap_locked(
            session,
            tenant_name=cast(str, tenant_name),
            tenant_domain=cast(str, tenant_domain),
            admin_email=cast(str, admin_email),
            admin_name=cast(str, admin_name),
            admin_password=cast(str, admin_password),
            local_login_enabled=enabled,
        )
    except BootstrapError as exc:
        return Outcome(BootstrapResult.FAILED, exc.reason)
    except Exception:
        # Any unexpected failure (audit insert, constraint, connection) rolls
        # the whole run back and fails it — no partial write survives (C-121).
        log.exception("production_bootstrap_failed")
        return Outcome(BootstrapResult.FAILED, "bootstrap failed")
    finally:
        # Releases the advisory lock and discards anything a failed/aborted
        # run left uncommitted; a completed run has nothing left to discard.
        await session.rollback()


def _validate_inputs(tenant_name: object, tenant_domain: object) -> Outcome | None:
    """Reject unusable tenant input with a failed result before any write (C-119)."""
    if not isinstance(tenant_name, str) or not tenant_name.strip():
        return Outcome(BootstrapResult.FAILED, "tenant name must be a non-empty string")
    if not isinstance(tenant_domain, str) or not tenant_domain.strip():
        return Outcome(BootstrapResult.FAILED, "tenant domain must be a non-empty string")
    return None


def _validate_admin_inputs(
    admin_email: object, admin_name: object, admin_password: object
) -> Outcome | None:
    """Reject unusable admin input; judged only when the admin must be created (C-120 ④)."""
    if not isinstance(admin_email, str) or not admin_email.strip():
        return Outcome(BootstrapResult.FAILED, "admin email must be a non-empty string")
    if not isinstance(admin_name, str) or not admin_name.strip():
        return Outcome(BootstrapResult.FAILED, "admin name must be a non-empty string")
    if not isinstance(admin_password, str) or not admin_password:
        return Outcome(BootstrapResult.FAILED, "admin password must be a non-empty string")
    if format_error(admin_password) is not None:
        return Outcome(BootstrapResult.FAILED, format_error(admin_password) or "")
    return None


async def _bootstrap_locked(
    session: AsyncSession,
    *,
    tenant_name: str,
    tenant_domain: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    local_login_enabled: bool,
) -> Outcome:
    # C-119: the tenant domain is the email domain the login paths resolve.
    # A domain whose local-login fold differs from the stored fold can never be
    # reached by that path, so binding it would create a tenant nobody can sign
    # in to — fail before any write, with its own judgement item.
    await _ensure_local_login_resolves(session, tenant_domain)

    hits = await _matching_tenants(session, tenant_domain)
    if len(hits) > 1:
        # Only pre-existing data outside this action can produce this (C-119).
        raise BootstrapError("TENANT_AMBIGUOUS: multiple tenants share this email domain")

    created_tenant = False
    if hits:
        tenant = hits[0]  # name / domain / status stay untouched (C-119)
    else:
        tenant = Tenant(
            name=tenant_name.strip(),
            slug=await _free_slug(session, tenant_domain),
            sso_domain=tenant_domain.strip(),
            sso_provider="local",
            status="active",
        )
        session.add(tenant)
        await session.flush()
        created_tenant = True

    created_admin: User | None = None
    early: Outcome | None = None
    if await _platform_admin_exists(session):
        early = Outcome(BootstrapResult.SKIPPED, "a platform administrator already exists")
    elif not local_login_enabled:
        early = Outcome(BootstrapResult.INCOMPLETE, "local login capability is off")
    elif tenant.status != "active":
        raise BootstrapError("target tenant is not active")
    else:
        invalid = _validate_admin_inputs(admin_email, admin_name, admin_password)
        if invalid is not None:
            return invalid
        created_admin = await _create_admin(
            session,
            tenant,
            admin_email,
            admin_name,
            admin_password,
        )

    # The strong audit pair is part of the same transaction as the writes; any
    # audit failure rolls everything back (C-81 / C-106 exception).
    if created_tenant:
        _add_audit(
            session,
            tenant,
            ACTION_TENANT_CREATED,
            RESOURCE_TENANT,
            resource_id=str(tenant.id),
            details={"name": tenant.name, "domain": tenant.sso_domain},
        )
    if created_admin is not None:
        _add_audit(
            session,
            tenant,
            ACTION_ADMIN_CREATED,
            RESOURCE_USER,
            resource_id=str(created_admin.id),
            details={"email": created_admin.email},
        )
    await session.commit()

    if early is not None:
        return early
    return Outcome(BootstrapResult.SUCCESS)


async def _ensure_local_login_resolves(session: AsyncSession, tenant_domain: str) -> None:
    """Refuse a domain the local login path can never resolve (C-119/C-121).

    ``LocalLoginService`` casefolds the whole address before
    ``resolve_tenant_by_email_domain``; the resolver then lowercases the domain
    and compares it with SQL ``lower(sso_domain)``. Where those folds disagree
    (ß expands to 'ss' in casefold; İ maps to a plain 'i' in the database) the
    bound domain is unreachable by local login. The run fails before any write
    with its own judgement item instead of creating such a tenant.
    """
    raw = tenant_domain.strip()
    login_domain = raw.casefold().rsplit("@", 1)[-1].lower()
    stored_domain = await session.scalar(text("SELECT lower(:value)"), {"value": raw})
    if login_domain != stored_domain:
        raise BootstrapError(
            "DOMAIN_FOLD_INCONSISTENT: local login cannot resolve this tenant domain"
        )


async def _matching_tenants(session: AsyncSession, tenant_domain: str) -> list[Tenant]:
    """Tenants whose domain folds onto the input; domain-less tenants never match.

    The fold is the login paths' (C-23 / C-111), reproduced from
    ``app/auth/tenants.py``: ``str.lower()`` on the input and SQL ``lower()`` on
    the stored value. Python ``casefold()`` folds characters the login path does
    not (ß → ss), so a casefold on only one side silently diverges from the
    tenant the login paths would resolve.
    """
    domain = tenant_domain.strip().lower()
    rows = (
        (
            await session.execute(
                select(Tenant)
                .where(Tenant.sso_domain.is_not(None), func.lower(Tenant.sso_domain) == domain)
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _platform_admin_exists(session: AsyncSession) -> bool:
    """Whether the platform holds any platform-admin account, active or not (C-120 ①)."""
    return (
        await session.scalar(select(User.id).where(User.role == PLATFORM_ADMIN_ROLE).limit(1))
    ) is not None


async def _create_admin(
    session: AsyncSession,
    tenant: Tenant,
    email: str,
    name: str,
    password: str,
) -> User:
    """Create the first local platform admin; any refusal is a failed run (C-114/C-120).

    The address must resolve back to this tenant through the local login chain
    (C-111): the login service casefolds the address and hands it to
    ``resolve_tenant_by_email_domain``. Reusing that resolver here is the only
    check that guarantees "accepted by the bootstrap ⇒ can actually sign in";
    a locally recomputed fold would accept addresses the resolver rejects
    (ß/İ), creating an administrator that can never log in.
    """
    address = email.strip()
    try:
        resolved = await resolve_tenant_by_email_domain(session, address.casefold())
    except AuthError:
        raise BootstrapError("admin email is outside the tenant domain") from None
    if resolved.id != tenant.id:
        raise BootstrapError("admin email is outside the tenant domain")
    if await _email_taken(session, tenant, address.casefold()):
        raise BootstrapError("admin email is already used by another account")
    admin = User(
        tenant_id=tenant.id,
        sso_sub=f"{LOCAL_SUBJECT_PREFIX}{uuid.uuid4()}",
        email=email.strip(),
        name=name.strip(),
        role=PLATFORM_ADMIN_ROLE,
        status="active",
        account_source=ACCOUNT_SOURCE_LOCAL,
        password_hash=hash_password(password),
        must_change_password=True,
    )
    session.add(admin)
    await session.flush()
    return admin


async def _email_taken(session: AsyncSession, tenant: Tenant, folded_email: str) -> bool:
    """Whether the tenant holds the folded email on any account, either source (C-114)."""
    return (
        await session.scalar(
            select(User.id)
            .where(User.tenant_id == tenant.id, func.lower(User.email) == folded_email)
            .limit(1)
        )
    ) is not None


async def _free_slug(session: AsyncSession, tenant_domain: str) -> str:
    """Build a slug no existing tenant uses, suffixing until free."""
    base = "".join(ch for ch in tenant_domain.strip().lower() if ch.isalnum())[:40] or "tenant"
    while True:
        candidate = f"{base}-{uuid.uuid4().hex[:8]}"
        exists = await session.scalar(select(Tenant.id).where(Tenant.slug == candidate).limit(1))
        if exists is None:
            return candidate


def _add_audit(
    session: AsyncSession,
    tenant: Tenant,
    action: str,
    resource: str,
    *,
    resource_id: str,
    details: dict[str, Any],
) -> None:
    """Add a system-source audit record for a bootstrap write (user empty, C-81)."""
    session.add(
        AuditLog(
            tenant_id=tenant.id,
            user_id=None,
            action=action,
            resource=resource,
            resource_id=resource_id,
            details=details,
        )
    )


def exit_status(result: BootstrapResult) -> int:
    """The process exit status of a run: success/skip end 0, the others do not (C-121)."""
    return 0 if result in (BootstrapResult.SUCCESS, BootstrapResult.SKIPPED) else 1


async def run() -> int:
    """Parse argv, run the bootstrap once and return the process exit status."""
    args = build_parser().parse_args()
    try:
        admin_password = resolve_admin_password(
            inline=args.admin_password,
            env_var=args.admin_password_env,
            use_stdin=args.admin_password_stdin,
            environ=os.environ,
            stdin=sys.stdin,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    async with async_session_factory() as session:
        outcome = await bootstrap(
            session,
            tenant_name=args.tenant_name,
            tenant_domain=args.tenant_domain,
            admin_email=args.admin_email,
            admin_name=args.admin_name,
            admin_password=admin_password,
        )
    # The reason reaches the operator on stderr; result name and exit status
    # carry the four-way distinction (C-121).
    if outcome.reason:
        print(outcome.reason, file=sys.stderr)
    print(outcome.result.value)
    return exit_status(outcome.result)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
