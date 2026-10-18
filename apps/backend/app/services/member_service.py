"""Member management: the platform-administrator surface over accounts (C-28).

Building the roster, creating local accounts, switching a member's status and
resetting a password. Every path is tenant-scoped: another tenant's member
identifier reads as absent (C-29), and the status switch protects the last
platform administrator and refuses self-disable.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.api_body import required_text
from app.auth.password import hash_password
from app.auth.password_change import PASSWORD_RESET_REASON, set_local_password
from app.auth.refresh import RefreshService
from app.config import Settings
from app.errors import AppError, invalid_field, validation_error
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, LOCAL_SUBJECT_PREFIX, User

# The two status values the admin console switches between (C-28/C-29).
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
# The role the last-administrator invariant protects.
PLATFORM_ADMIN_ROLE = "platform_admin"
# Role a created local account starts on; role changes are outside this surface.
DEFAULT_ROLE = "employee"
# Advisory-lock namespace of the tenant's member invariants; the lock key is
# this namespace plus the tenant, so switches in other tenants never wait.
_STATUS_LOCK_NAMESPACE = "eap:member_status"


class MemberService:
    """Member lifecycle for platform administrators, per tenant."""

    def __init__(self, settings: Settings, *, refresh_service: RefreshService) -> None:
        self._settings = settings
        self._refresh_service = refresh_service

    async def list_members(
        self,
        session: AsyncSession,
        tenant: Tenant,
        *,
        page: int,
        page_size: int,
        search: str | None,
        role: str | None,
        status: str | None,
    ) -> tuple[list[User], int]:
        """Return one page of the tenant's members and the tenant's total."""
        filters = [User.tenant_id == tenant.id]
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            filters.append(or_(User.name.ilike(pattern), User.email.ilike(pattern)))
        if role:
            filters.append(User.role == role)
        if status:
            filters.append(User.status == status)

        total = await session.scalar(select(func.count()).select_from(User).where(*filters))
        rows = (
            (
                await session.execute(
                    select(User)
                    .where(*filters)
                    .order_by(User.created_at.desc(), User.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                    # Scalars only: the roster reads no relationship and the
                    # selectin loads would pull every session, task and run.
                    .options(noload("*"))
                )
            )
            .scalars()
            .all()
        )
        return list(rows), int(total or 0)

    async def create_local_account(
        self, session: AsyncSession, tenant: Tenant, *, body: dict[str, Any]
    ) -> User:
        """Create a local account for the tenant and mark it for a first change."""
        # Closed capability: account creation reads as a path that does not
        # exist, exactly like the local login entry (C-110).
        if not self._settings.local_login_enabled:
            raise AppError(404, "NOT_FOUND", "Not Found")

        email, name, password = self._creation_fields(body)
        domain = email.rsplit("@", 1)[-1]
        if tenant.sso_domain is None or domain.casefold() != tenant.sso_domain.casefold():
            raise validation_error(invalid_field("email", "Email must be inside the tenant domain"))

        # One address is one account per tenant whatever its case, and an
        # account of the other source blocks the address just the same (C-114).
        existing = await session.scalar(
            select(User.id)
            .where(User.tenant_id == tenant.id, func.lower(User.email) == email.casefold())
            .limit(1)
        )
        if existing is not None:
            raise AppError(409, "ACCOUNT_EXISTS", "Account already exists")

        user = User(
            tenant_id=tenant.id,
            sso_sub=f"{LOCAL_SUBJECT_PREFIX}{uuid.uuid4()}",
            email=email,
            name=name,
            role=DEFAULT_ROLE,
            status=STATUS_ACTIVE,
            password_hash=hash_password(password),
            must_change_password=True,
            account_source=ACCOUNT_SOURCE_LOCAL,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent create claimed the address between the lookup above
            # and this insert: the constraint answers the same conflict.
            await session.rollback()
            raise AppError(409, "ACCOUNT_EXISTS", "Account already exists") from None
        await session.refresh(user, attribute_names=["created_at"])
        return user

    async def set_status(
        self,
        session: AsyncSession,
        tenant: Tenant,
        actor: User,
        member_id: str,
        *,
        status: str,
    ) -> User:
        """Switch a member's status, enforcing the C-29 invariants on disable."""
        if status == STATUS_DISABLED:
            await self._serialise_status_switch(session, tenant)
        member = await self.get_member(session, tenant, member_id)
        if status == STATUS_DISABLED and member.status != STATUS_DISABLED:
            if member.role == PLATFORM_ADMIN_ROLE and await self._admins_left(session, tenant) <= 1:
                raise AppError(
                    409, "LAST_ADMIN", "The last platform administrator cannot be disabled"
                )
            if member.id == actor.id:
                raise AppError(409, "SELF_DISABLE", "An account cannot disable itself")
        member.status = status
        await session.commit()
        await session.refresh(member, attribute_names=["updated_at"])
        return member

    async def reset_password(
        self, session: AsyncSession, tenant: Tenant, member_id: str, *, body: dict[str, Any]
    ) -> User:
        """Set a member's password and force a change on the next login (C-115)."""
        if not self._settings.local_login_enabled:
            raise AppError(404, "NOT_FOUND", "Not Found")

        member = await self.get_member(session, tenant, member_id)
        if member.account_source != ACCOUNT_SOURCE_LOCAL:
            # An SSO account has no password credential to reset.
            raise AppError(404, "NOT_FOUND", "Not Found")

        password = required_text(body, "password")["password"]

        await set_local_password(
            session,
            member,
            password=password,
            must_change=True,
            reason=PASSWORD_RESET_REASON,
            refresh_service=self._refresh_service,
        )
        await session.commit()
        return member

    async def get_member(self, session: AsyncSession, tenant: Tenant, member_id: str) -> User:
        """Load one member of ``tenant``, or answer the member domain's 404."""
        try:
            member_uuid = uuid.UUID(member_id)
        except (ValueError, TypeError):
            raise AppError(404, "NOT_FOUND", "Member not found") from None
        member = await session.scalar(
            select(User)
            .where(User.id == member_uuid, User.tenant_id == tenant.id)
            .options(noload("*"))
        )
        if member is None:
            raise AppError(404, "NOT_FOUND", "Member not found")
        return member

    @staticmethod
    async def _serialise_status_switch(session: AsyncSession, tenant: Tenant) -> None:
        """Serialise the tenant's status switches for the rest of this unit of work.

        The last-administrator rule reads a count and then writes a status, so
        two switches running at once each count the other's target as active and
        both commit, leaving the tenant without an administrator (C-29). The
        transaction-scoped lock is released by the commit or rollback that ends
        the switch, and the count behind it runs after the lock, so it sees the
        switch that went first.
        """
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext(:namespace), hashtext(CAST(:tenant AS text)))"
            ),
            {"namespace": _STATUS_LOCK_NAMESPACE, "tenant": str(tenant.id)},
        )

    @staticmethod
    async def _admins_left(session: AsyncSession, tenant: Tenant) -> int:
        """How many active platform administrators the tenant still has."""
        count = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == tenant.id,
                User.role == PLATFORM_ADMIN_ROLE,
                User.status == STATUS_ACTIVE,
            )
        )
        return int(count or 0)

    @staticmethod
    def _creation_fields(body: dict[str, Any]) -> tuple[str, str, str]:
        """Read the required creation fields, naming only fields that failed.

        The password never travels back: an unusable body is answered with the
        field names, never with the rejected values (F8).
        """
        unexpected = [name for name in body if name not in ("email", "name", "password")]
        if unexpected:
            raise validation_error(
                *[invalid_field(name, "Field not supported") for name in sorted(unexpected)]
            )
        fields = required_text(body, "email", "name", "password")
        email = fields["email"].strip()
        name = fields["name"].strip()
        errors = []
        if "@" not in email:
            errors.append(invalid_field("email", "A valid email address is required"))
        if not name:
            errors.append(invalid_field("name", "Field must not be blank"))
        if errors:
            raise validation_error(*errors)
        return email, name, fields["password"]
