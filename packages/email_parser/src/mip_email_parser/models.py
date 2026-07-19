"""Data models for parsed email content."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ParsedAttachment:
    """A single attachment extracted from a MIME message."""

    filename: str
    content_type: str
    size_bytes: int
    is_inline: bool
    content: bytes
    content_id: str | None = None


@dataclass(frozen=True)
class ParsedEmail:
    """Structured representation of a parsed MIME email.

    This is the output of parse_eml() and contains all information
    extracted from a raw .eml file. The index worker uses this to
    build Elasticsearch documents and update PostgreSQL records.
    """

    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    subject: str
    sender_email: str
    sender_name: str
    recipients_to: list[tuple[str, str]]  # (email, name)
    recipients_cc: list[tuple[str, str]]
    recipients_bcc: list[tuple[str, str]]
    date: datetime | None
    body_plain: str
    body_html: str
    body_text_from_html: str  # Clean text extracted from HTML
    attachments: list[ParsedAttachment] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
