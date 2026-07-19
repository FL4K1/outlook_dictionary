"""MIP Email Parser — MIME email parsing and text extraction.

Provides structured parsing of raw .eml files (RFC 5322 MIME messages)
into a clean ParsedEmail data model suitable for indexing and search.

This package has no dependency on any mail provider — it operates
purely on raw MIME bytes.
"""

from mip_email_parser.models import ParsedAttachment, ParsedEmail
from mip_email_parser.parser import parse_eml

__all__ = [
    "ParsedAttachment",
    "ParsedEmail",
    "parse_eml",
]
