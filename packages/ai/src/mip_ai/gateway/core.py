"""Shared infrastructure gateway for LiteLLM execution."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx
import litellm

from mip_ai.gateway.errors import (
    GatewayConfigurationError,
    GatewayPermanentError,
    GatewayRateLimitError,
    GatewayTransientError,
)

if TYPE_CHECKING:
    from mip_ai.gateway.llm_config import LLMConfig

logger = logging.getLogger(__name__)


class BaseLiteLLMGateway:
    """Infrastructure-only gateway for executing LiteLLM requests safely."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._model = self._build_litellm_model_string(config)

    @property
    def model_id(self) -> str:
        """Return the qualified model string."""
        return self._model

    def _build_litellm_model_string(self, config: LLMConfig) -> str:
        """Deterministically qualify the provider and model for LiteLLM."""
        provider_name = config.provider.value.lower()
        model_name = config.model

        if provider_name == "openai":
            if model_name.startswith("openai/"):
                return model_name
            return model_name

        prefix = f"{provider_name}/"
        if model_name.startswith(prefix):
            return model_name
        return f"{prefix}{model_name}"

    async def execute_structured_request(
        self,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        """Execute request and return (content, latency_ms)."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.0,
            "timeout": self.config.timeout_seconds,
        }

        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
            if self.config.provider == "ollama" and not self.config.api_key:
                kwargs["api_key"] = "ollama"

        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key.get_secret_value()

        if json_schema:
            if self.config.require_structured_output:
                supported_params = litellm.get_supported_openai_params(model=self._model) or []
                if "response_format" not in supported_params and self.config.provider != "ollama":
                    msg = (
                        f"Model {self._model} does not reliably support "
                        "structured output ('response_format'). "
                        "Failing closed due to require_structured_output=True."
                    )
                    raise GatewayConfigurationError(msg)

            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": json_schema,
            }

        start_time = time.monotonic()
        try:
            response = await litellm.acompletion(**kwargs, drop_params=False)
        except litellm.exceptions.RateLimitError as e:
            raise GatewayRateLimitError(f"LiteLLM rate limit: {e}") from e
        except (
            litellm.exceptions.Timeout,
            litellm.exceptions.ServiceUnavailableError,
            httpx.TimeoutException,
        ) as e:
            raise GatewayTransientError(f"Gateway transient failure: {e}") from e
        except litellm.exceptions.AuthenticationError as e:
            raise GatewayConfigurationError(f"Gateway authentication failure: {e}") from e
        except litellm.exceptions.UnsupportedParamsError as e:
            raise GatewayConfigurationError(f"Unsupported structured output param: {e}") from e
        except litellm.exceptions.APIError as e:
            if getattr(e, "status_code", 500) >= 500:
                raise GatewayTransientError(f"Gateway transient API failure: {e}") from e
            raise GatewayPermanentError(f"Gateway permanent API failure: {e}") from e
        except (
            litellm.exceptions.ContextWindowExceededError,
            litellm.exceptions.BadRequestError,
        ) as e:
            raise GatewayPermanentError(f"Bad provider request: {e}") from e
        except Exception as e:
            logger.error("Unknown LiteLLM execution failure: %s", type(e).__name__)
            raise GatewayTransientError("Unknown gateway transient error.") from e

        latency_ms = (time.monotonic() - start_time) * 1000.0

        choices = response.get("choices", [])
        if not choices:
            raise GatewayPermanentError("Gateway returned no choices.")

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise GatewayPermanentError("Gateway returned empty content.")

        return content, latency_ms
