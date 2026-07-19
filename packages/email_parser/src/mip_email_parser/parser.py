"""MIME email parser — extracts structured data from raw .eml bytes.

Uses Python's stdlib email module for MIME parsing and BeautifulSoup
for HTML-to-text conversion.

This is the foundational interface. The full implementation with
charset handling, CID resolution, and edge-case coverage will be
completed in Milestone 2 when the sync engine produces real .eml files.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from mip_email_parser.models import ParsedAttachment, ParsedEmail


def parse_eml(raw: bytes) -> ParsedEmail:
    """Parse raw MIME bytes into a structured ParsedEmail.

    Args:
        raw: Raw MIME content (the contents of a .eml file).

    Returns:
        A ParsedEmail with all extracted fields populated.
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    # Extract headers
    message_id = msg.get("Message-ID")
    in_reply_to = msg.get("In-Reply-To")
    references_raw = msg.get("References", "")
    references = references_raw.split() if references_raw else []
    subject = str(msg.get("Subject", ""))

    # Parse sender
    sender_name, sender_email_addr = _parse_address(msg.get("From", ""))

    # Parse recipients
    recipients_to = _parse_address_list(msg.get_all("To"))
    recipients_cc = _parse_address_list(msg.get_all("Cc"))
    recipients_bcc = _parse_address_list(msg.get_all("Bcc"))

    # Parse date
    date = _parse_date(msg.get("Date"))

    # Extract body parts and attachments
    body_plain = ""
    body_html = ""
    attachments: list[ParsedAttachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                attachments.append(_extract_attachment(part))
            elif content_type == "text/plain" and not body_plain:
                body_plain = _get_text_payload(part)
            elif content_type == "text/html" and not body_html:
                body_html = _get_text_payload(part)
    else:
        content_type = msg.get_content_type()
        if content_type == "text/html":
            body_html = _get_text_payload(msg)
        else:
            body_plain = _get_text_payload(msg)

    # Convert HTML to clean text
    body_text_from_html = _html_to_text(body_html) if body_html else ""

    # Collect all headers
    headers = {key: str(value) for key, value in msg.items()}

    return ParsedEmail(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        subject=subject,
        sender_email=sender_email_addr,
        sender_name=sender_name,
        recipients_to=recipients_to,
        recipients_cc=recipients_cc,
        recipients_bcc=recipients_bcc,
        date=date,
        body_plain=body_plain,
        body_html=body_html,
        body_text_from_html=body_text_from_html,
        attachments=attachments,
        headers=headers,
    )


def _parse_address(raw: str) -> tuple[str, str]:
    """Parse a single email address into (name, email)."""
    name, addr = email.utils.parseaddr(raw)
    return (name, addr)


def _parse_address_list(
    raw_list: list[str] | None,
) -> list[tuple[str, str]]:
    """Parse a list of email addresses into [(name, email), ...]."""
    if not raw_list:
        return []
    results: list[tuple[str, str]] = []
    for raw in raw_list:
        for name, addr in email.utils.getaddresses([raw]):
            if addr:
                results.append((name, addr))
    return results


def _parse_date(raw: str | None) -> datetime | None:
    """Parse an RFC 2822 date string into a timezone-aware datetime."""
    if not raw:
        return None
    parsed = email.utils.parsedate_to_datetime(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _get_text_payload(part: email.message.Message) -> str:  # type: ignore[type-arg]
    """Safely extract text content from a MIME part."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    if not isinstance(payload, bytes):
        return str(payload)
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _extract_attachment(part: email.message.Message) -> ParsedAttachment:  # type: ignore[type-arg]
    """Extract an attachment from a MIME part."""
    filename = part.get_filename() or "unnamed_attachment"
    content_type = part.get_content_type()
    payload = part.get_payload(decode=True)
    content = payload if isinstance(payload, bytes) else b""
    content_id = part.get("Content-ID")
    is_inline = "inline" in str(part.get("Content-Disposition", ""))

    return ParsedAttachment(
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        is_inline=is_inline,
        content=content,
        content_id=content_id,
    )


def _html_to_text(html: str) -> str:
    """Convert HTML email body to clean, searchable plain text.

    Uses BeautifulSoup to strip tags and collapse whitespace.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for element in soup(["script", "style", "head"]):
        element.decompose()

    text = soup.get_text(separator=" ", strip=True)

    # Collapse multiple whitespace into single spaces
    lines = (line.strip() for line in text.splitlines())
    return " ".join(chunk for chunk in lines if chunk)
