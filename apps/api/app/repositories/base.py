"""Base repository pattern implementation.

Provides common CRUD operations using async SQLAlchemy.
All specific repositories should inherit from BaseRepository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import delete, select, update

from mip_models.base import Base

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository[ModelType]:
    """Base generic repository for SQLAlchemy models."""

    def __init__(self, model: type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get(self, id_val: uuid.UUID | str) -> ModelType | None:
        """Get a single record by primary key."""
        result = await self.session.execute(
            select(self.model).where(self.model.id == id_val)  # type: ignore[attr-defined]
        )
        return result.scalars().first()

    async def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[ModelType]:
        """Get a list of records with pagination."""
        result = await self.session.execute(select(self.model).offset(skip).limit(limit))
        return result.scalars().all()

    async def create(self, **kwargs: Any) -> ModelType:
        """Create a new record and flush to generate ID."""
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def update(self, id_val: uuid.UUID | str, **kwargs: Any) -> ModelType | None:
        """Update an existing record by ID."""
        stmt = (
            update(self.model)
            .where(self.model.id == id_val)  # type: ignore[attr-defined]
            .values(**kwargs)
            .returning(self.model)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalars().first()

    async def delete(self, id_val: uuid.UUID | str) -> bool:
        """Hard delete a record by ID."""
        stmt = delete(self.model).where(self.model.id == id_val)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0  # type: ignore[attr-defined]
