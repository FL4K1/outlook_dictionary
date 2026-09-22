"""LLM Provider deployment-level configuration schema."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    MOCK = "mock"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"


class LLMConfig(BaseModel):
    """Deployment-level LLM configuration.

    This Configuration represents the single active LLM instance
    used uniformly across the application and all tenants.
    """

    model_config = ConfigDict(extra="forbid")

    provider: LLMProvider = Field(
        default=LLMProvider.MOCK,
        description="The backend provider to use for natural language understanding.",
    )
    model: str = Field(
        default="deterministic",
        description="The provider-specific model name (e.g., 'gpt-4o-mini', 'llama3').",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "API key for cloud providers. Wrapped in SecretStr to prevent accidental logging."
        ),
    )
    base_url: str | None = Field(
        default=None,
        description="Optional custom base URL for the API (e.g., local Ollama instance).",
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="Maximum time to wait for the LLM to complete a response.",
    )
    require_structured_output: bool = Field(
        default=True,
        description=(
            "If True, strictly require JSON Schema structured output support from the provider."
        ),
    )
