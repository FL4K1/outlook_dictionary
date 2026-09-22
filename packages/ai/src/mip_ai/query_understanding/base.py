"""Query Understanding Provider Protocol interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mip_models.search import MailQueryPlan


@dataclass(frozen=True)
class QueryUnderstandingResult:
    """Result from a query understanding LLM invocation."""

    query_plan: MailQueryPlan
    model_id: str
    latency_ms: float
    provider: str
    raw_response: str | None = None


@runtime_checkable
class QueryUnderstandingProvider(Protocol):
    """Interface for natural language query understanding providers."""

    async def understand_query(self, query: str) -> QueryUnderstandingResult:
        """Parse natural language query into a typed MailQueryPlan."""
        ...

    @property
    def model_id(self) -> str:
        """Unique identifier for this model version."""
        ...
