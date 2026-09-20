"""Semantic text construction for embedding input.

Provides a single deterministic function for building the text payload
sent to embedding providers.  Used by both the live OutboxWorker embed
job and the historical backfill worker.
"""

from __future__ import annotations

import re

# Collapse runs of whitespace (including newlines) to single space
_WHITESPACE_RE = re.compile(r"\s+")


def build_semantic_text(
    subject: str = "",
    sender: str = "",
    body: str = "",
    *,
    max_length: int = 2000,
) -> str:
    """Build a deterministic, truncated, Unicode-safe embedding input.

    Concatenates structured mail fields into a single string suitable
    for an embedding model.  The result is whitespace-normalized and
    truncated to *max_length* characters.

    Never includes secrets, transport headers, or raw MIME.
    """
    raw = f"Subject: {subject}\nFrom: {sender}\n\n{body}"
    normalized = _WHITESPACE_RE.sub(" ", raw).strip()
    return normalized[:max_length]
