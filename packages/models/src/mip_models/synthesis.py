"""Models for Retrieval-Augmented Generation / Search Synthesis."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SynthesisCitation(BaseModel):
    """Citation reference to a retrieved message."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(description="The ID of the message being referenced as evidence.")


class SearchSynthesis(BaseModel):
    """The synthesized natural-language answer to a search query."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        description="The natural-language factual answer to the query.", max_length=2000
    )
    citations: list[SynthesisCitation] = Field(
        default_factory=list,
        description="List of messages cited in the generated answer.",
        max_length=15,
    )
    insufficient_evidence: bool = Field(
        default=False,
        description="True if evidence is insufficient to answer the query.",
    )
