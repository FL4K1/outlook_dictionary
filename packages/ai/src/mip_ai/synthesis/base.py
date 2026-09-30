"""Base Protocol for Search Synthesis."""

from typing import Any, Protocol

from mip_models.synthesis import SearchSynthesis


class SynthesisHitProtocol(Protocol):
    """Minimal protocol for hits passed to the synthesis provider."""

    id: str
    subject: str | None
    body: str | None
    sender: Any | None


class SearchSynthesisProvider(Protocol):
    """Protocol for providers that can synthesize natural language answers from search hits."""

    @property
    def model_id(self) -> str:
        """Return the identifier of the specific model in use."""
        ...

    async def synthesize(self, query: str, hits: list[SynthesisHitProtocol]) -> SearchSynthesis:
        """Synthesize a natural language answer based on the retrieved hits.

        Args:
            query: The user's natural language question.
            hits: The list of deterministic search results already filtered for the tenant.

        Returns:
            The synthesized answer with citations.

        Raises:
            SynthesisTransientError: On network / timeout.
            SynthesisMalformedOutputError: If structural validation or bounds fail.
        """
        ...
