"""MIP Models — Data models, schemas, and database utilities.

This package provides:
- SQLAlchemy ORM base classes and tenant isolation mixins
- Async database session management
- Pydantic schemas for API request/response validation
- The canonical Tenant model (foundation entity)
"""

from mip_models.audit import AuditLog
from mip_models.auth import (
    ApiKey,
    Permission,
    Role,
    RolePermission,
    ServiceAccount,
    Session,
)
from mip_models.base import Base, TenantMixin, TimestampMixin
from mip_models.database import AsyncSessionFactory, get_async_engine
from mip_models.mail import MailAccount, ProviderCredential
from mip_models.organization import Organization
from mip_models.tenant import Tenant
from mip_models.user import Identity, Membership, User

__all__ = [
    "ApiKey",
    "AsyncSessionFactory",
    "AuditLog",
    "Base",
    "Identity",
    "MailAccount",
    "Membership",
    "Organization",
    "Permission",
    "ProviderCredential",
    "Role",
    "RolePermission",
    "ServiceAccount",
    "Session",
    "Tenant",
    "TenantMixin",
    "TimestampMixin",
    "User",
    "get_async_engine",
]
