"""Specific repository implementations for core entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.repositories.base import BaseRepository
from mip_models.auth import Role
from mip_models.organization import Organization
from mip_models.tenant import Tenant
from mip_models.user import Membership, User

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class OrganizationRepository(BaseRepository[Organization]):
    """Repository for Organization entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Organization, session)

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(select(Organization).where(Organization.slug == slug))
        return result.scalars().first()


class TenantRepository(BaseRepository[Tenant]):
    """Repository for Tenant entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Tenant, session)

    async def get_by_slug(self, organization_id: uuid.UUID, slug: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.organization_id == organization_id,
                Tenant.slug == slug,
            )
        )
        return result.scalars().first()


class UserRepository(BaseRepository[User]):
    """Repository for User entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalars().first()


class RoleRepository(BaseRepository[Role]):
    """Repository for Role entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Role, session)

    async def get_system_role(self, name: str) -> Role | None:
        result = await self.session.execute(
            select(Role).where(Role.name == name, Role.is_system == True)  # noqa: E712
        )
        return result.scalars().first()


class MembershipRepository(BaseRepository[Membership]):
    """Repository for Membership entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Membership, session)

    async def get_by_user_and_tenant(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Membership | None:
        """Find an active membership for a user in a tenant."""
        result = await self.session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.tenant_id == tenant_id,
                Membership.is_active == True,  # noqa: E712
            )
        )
        return result.scalars().first()
