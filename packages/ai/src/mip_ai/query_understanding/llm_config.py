"""LLM Provider deployment-level configuration schema.

Re-exports the core configuration from mip_ai.gateway.llm_config
for backward compatibility in PR-3.1 boundaries.
"""

from mip_ai.gateway.llm_config import LLMConfig, LLMProvider

__all__ = ["LLMConfig", "LLMProvider"]
