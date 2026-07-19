"""LLM Provider Protocol interfaces.

Defines the contract for all LLM provider implementations.
Architecture reference: Blueprint Section 3.10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class MessageRole(StrEnum):
    """Roles for chat completion messages."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class Message:
    """A single message in a chat completion request."""

    role: MessageRole
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM completion request."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    provider: str
    finish_reason: str  # "stop" | "length" | "content_filter"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens


@runtime_checkable
class LLMProvider(Protocol):
    """Interface for Large Language Model providers.

    Implementations must support both blocking and streaming completions.
    """

    async def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Send a completion request and return the full response."""
        ...

    async def complete_stream(
        self,
        messages: list[Message],
        model: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Stream a completion response token by token."""
        ...

    def count_tokens(self, text: str, model: str) -> int:
        """Count tokens for cost estimation before sending."""
        ...
