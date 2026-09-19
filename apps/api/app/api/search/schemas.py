"""Pydantic schemas for Mail Search API."""

from __future__ import annotations

import datetime  # noqa: TC003 — Pydantic requires runtime access with PEP 563
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MailSearchRequest(BaseModel):
    """Request payload for searching canonical mail messages."""

    query: str | None = Field(
        default=None,
        description="Free-text search query across subject, body, and sender.",
        max_length=200,
    )
    folder_ids: list[str] | None = Field(
        default=None,
        description="Filter by one or more folder IDs. Max 20.",
        max_length=20,
    )
    account_ids: list[str] | None = Field(
        default=None,
        description="Filter by specific mail account IDs belonging to the tenant. Max 10.",
        max_length=10,
    )
    is_read: bool | None = Field(
        default=None,
        description="Filter by read status.",
    )
    has_attachments: bool | None = Field(
        default=None,
        description="Filter by presence of attachments.",
    )
    from_date: datetime.datetime | None = Field(
        default=None,
        description="Filter messages received on or after this ISO8601 timestamp.",
    )
    to_date: datetime.datetime | None = Field(
        default=None,
        description="Filter messages received on or before this ISO8601 timestamp.",
    )
    search_after: list[Any] | None = Field(
        default=None,
        description="Opaque pagination cursor from a previous response.",
        max_length=5,
    )
    page_size: int = Field(
        default=25,
        ge=1,
        le=100,
        description="Number of results to return per page.",
    )
    search_mode: Literal["lexical", "semantic", "hybrid"] = Field(
        default="hybrid",
        description="Retrieval mode. Semantic/hybrid perform vector search fallback on error.",
    )


class SearchParticipant(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str

    model_config = ConfigDict(extra="ignore")


class SearchHit(BaseModel):
    id: str
    mail_account_id: str
    subject: str | None = None
    sender: str | None = None
    participants: list[SearchParticipant] = Field(default_factory=list)
    received_date_time: datetime.datetime | None = None
    folder_ids: list[str] = Field(default_factory=list)
    is_read: bool
    has_attachments: bool

    model_config = ConfigDict(extra="ignore")


class MailSearchResponse(BaseModel):
    """Response payload for Mail Search API."""

    items: list[SearchHit]
    next_page_cursor: list[Any] | None = Field(
        default=None,
        description="Opaque cursor to use in search_after for the next page.",
    )

    model_config = ConfigDict(extra="ignore")
