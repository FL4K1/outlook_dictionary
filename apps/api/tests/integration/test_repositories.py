"""Tests for database repositories."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.core import OrganizationRepository, UserRepository


@pytest.mark.asyncio
async def test_create_and_get_organization(db_session: AsyncSession) -> None:
    """Test creating and retrieving an organization."""
    repo = OrganizationRepository(db_session)
    org_slug = f"test-org-{uuid.uuid4().hex[:6]}"

    # Create
    org = await repo.create(name="Test Organization", slug=org_slug, plan_tier="free")
    assert org.id is not None
    assert org.slug == org_slug

    # Get by ID
    fetched_org = await repo.get(org.id)
    assert fetched_org is not None
    assert fetched_org.id == org.id

    # Get by slug
    fetched_org_by_slug = await repo.get_by_slug(org_slug)
    assert fetched_org_by_slug is not None
    assert fetched_org_by_slug.id == org.id


@pytest.mark.asyncio
async def test_create_and_get_user(db_session: AsyncSession) -> None:
    """Test creating and retrieving a user."""
    repo = UserRepository(db_session)
    email = f"test-{uuid.uuid4().hex[:6]}@example.com"

    # Create
    user = await repo.create(
        email=email,
        display_name="Test User",
    )
    assert user.id is not None
    assert user.email == email

    # Get by ID
    fetched_user = await repo.get(user.id)
    assert fetched_user is not None
    assert fetched_user.id == user.id

    # Get by email
    fetched_user_by_email = await repo.get_by_email(email)
    assert fetched_user_by_email is not None
    assert fetched_user_by_email.id == user.id


@pytest.mark.asyncio
async def test_update_and_delete(db_session: AsyncSession) -> None:
    """Test updating and deleting records."""
    repo = UserRepository(db_session)
    email = f"test-{uuid.uuid4().hex[:6]}@example.com"

    user = await repo.create(
        email=email,
        display_name="Test User",
    )

    # Update
    updated_user = await repo.update(user.id, display_name="Updated Name")
    assert updated_user is not None
    assert updated_user.display_name == "Updated Name"

    # Delete
    deleted = await repo.delete(user.id)
    assert deleted is True

    # Verify deleted
    fetched_user = await repo.get(user.id)
    assert fetched_user is None
