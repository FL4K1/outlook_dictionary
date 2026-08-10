"""Seed database with default system roles and permissions."""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import get_settings
from mip_models.auth import Permission, Role, RolePermission
from mip_models.base import SystemRole
from mip_models.database import AsyncSessionFactory, get_async_engine

# Define core system permissions
SYSTEM_PERMISSIONS = [
    {"codename": "tenant.read", "resource_type": "tenant", "description": "Read tenant data"},
    {"codename": "tenant.update", "resource_type": "tenant", "description": "Update tenant data"},
    {"codename": "user.read", "resource_type": "user", "description": "Read user data"},
    {"codename": "user.invite", "resource_type": "user", "description": "Invite new users"},
    {"codename": "user.remove", "resource_type": "user", "description": "Remove users"},
    {"codename": "role.read", "resource_type": "role", "description": "Read roles"},
    {"codename": "role.manage", "resource_type": "role", "description": "Manage roles"},
    {
        "codename": "mail_account.read",
        "resource_type": "mail_account",
        "description": "Read mail accounts",
    },
    {
        "codename": "mail_account.connect",
        "resource_type": "mail_account",
        "description": "Connect mail accounts",
    },
    {
        "codename": "mail_account.manage",
        "resource_type": "mail_account",
        "description": "Manage mail accounts",
    },
]

# Map system roles to their permissions
ROLE_PERMISSIONS = {
    SystemRole.PLATFORM_ADMIN: [p["codename"] for p in SYSTEM_PERMISSIONS],
    SystemRole.ORG_OWNER: [p["codename"] for p in SYSTEM_PERMISSIONS],
    SystemRole.TENANT_ADMIN: [
        "tenant.read",
        "tenant.update",
        "user.read",
        "user.invite",
        "user.remove",
        "role.read",
        "role.manage",
        "mail_account.read",
        "mail_account.connect",
        "mail_account.manage",
    ],
    SystemRole.MEMBER: [
        "tenant.read",
        "user.read",
        "mail_account.read",
        "mail_account.connect",
    ],
    SystemRole.VIEWER: [
        "tenant.read",
        "user.read",
        "mail_account.read",
    ],
}


async def seed_permissions(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Seed permissions and return mapping of codename -> ID."""
    import sys

    sys.stdout.write("Seeding permissions...\n")
    perm_map = {}
    for perm_data in SYSTEM_PERMISSIONS:
        result = await session.execute(
            select(Permission).where(Permission.codename == perm_data["codename"])
        )
        perm = result.scalars().first()
        if not perm:
            perm = Permission(**perm_data)
            session.add(perm)
            await session.flush()
        perm_map[perm.codename] = perm.id

    await session.commit()
    return perm_map


async def seed_roles(session: AsyncSession, perm_map: dict[str, uuid.UUID]) -> None:
    """Seed system roles and map permissions to them."""
    import sys

    sys.stdout.write("Seeding roles...\n")
    for role_name in SystemRole:
        # Create role if missing
        result = await session.execute(
            select(Role).where(Role.name == role_name.value, Role.is_system)
        )
        role = result.scalars().first()
        if not role:
            role = Role(
                name=role_name.value,
                display_name=role_name.name.replace("_", " ").title(),
                is_system=True,
                tenant_id=None,
            )
            session.add(role)
            await session.flush()

        # Link permissions
        current_perms_result = await session.execute(
            select(RolePermission).where(RolePermission.role_id == role.id)
        )
        current_perm_ids = {rp.permission_id for rp in current_perms_result.scalars().all()}

        desired_perm_codenames = ROLE_PERMISSIONS.get(role_name, [])
        for codename in desired_perm_codenames:
            perm_id = perm_map[codename]
            if perm_id not in current_perm_ids:
                rp = RolePermission(role_id=role.id, permission_id=perm_id)
                session.add(rp)

    await session.commit()
    import sys

    sys.stdout.write("Seed complete.\n")


async def run_seed() -> None:
    settings = get_settings()
    engine = get_async_engine(settings.database_url)
    factory = AsyncSessionFactory(engine)

    # We create a single session for the seed process
    session_gen = factory.get_session()
    session = await anext(session_gen)
    try:
        perm_map = await seed_permissions(session)
        await seed_roles(session, perm_map)
    except Exception as e:
        import sys

        sys.stdout.write(f"Error seeding: {e}\n")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_seed())
