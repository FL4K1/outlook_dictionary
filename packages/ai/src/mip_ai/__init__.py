"""MIP AI — LLM and embedding provider abstractions.

This package defines provider-agnostic interfaces for:
- Large Language Model (LLM) completions and streaming
- Embedding generation for semantic search

No concrete implementations are included in Milestone 0.
Implementations (Azure OpenAI, OpenAI, Anthropic, etc.) will be added
in Milestones 4-5.
"""

from mip_ai.embeddings.base import EmbeddingProvider, EmbeddingResult
from mip_ai.llm.base import LLMProvider, LLMResponse, Message, MessageRole

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "MessageRole",
]
