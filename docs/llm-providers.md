# LLM Providers Configuration

The Mail Intelligence Platform (MIP) supports multiple AI providers for its Natural Language Query Understanding layer through a single, unified gateway. This gateway integrates with LiteLLM structurally, ensuring predictable capabilities and security invariants regardless of the model chosen.

## Deployment Scope

LLM configuration is strict and scoped to **one active provider per application instance**, uniformly used across all tenants.
> [!NOTE]
> Per-tenant Bring-Your-Own-Key (BYOK) is strictly out of scope for the current architecture.

## Supported Providers & Setup

Configuration is performed through environment variables mapped to the central `Settings` instance.

### 1. OpenAI (Default / Preserved)
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
```

### 2. Google Gemini
```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-1.5-flash
LLM_API_KEY=AIzaSy...
```

### 3. Groq
```env
LLM_PROVIDER=groq
LLM_MODEL=llama3-70b-8192
LLM_API_KEY=gsk_...
```

### 4. OpenRouter
```env
LLM_PROVIDER=openrouter
LLM_MODEL=meta-llama/llama-3.1-70b-instruct
LLM_API_KEY=sk-or-v1-...
```

### 5. Ollama (Local)
Local environments do not require an API key but require the base URL configuration natively pointing to the local host.
```env
LLM_PROVIDER=ollama
LLM_MODEL=llama3
LLM_BASE_URL=http://localhost:11434
```

### 6. Mock (Tests)
Deterministic bypassing for testing environments without sending egress requests.
```env
LLM_PROVIDER=mock
LLM_MODEL=deterministic
```

## Security Invariants

### 1. Structured Output Enforcement

To prevent arbitrary data injection or non-deterministic schema deviations, the Gateway pipeline uses strict `json_schema` via the LiteLLM `response_format` constraint combined intricately with local `Pydantic` models.

If `LLM_REQUIRE_STRUCTURED_OUTPUT=true`, any provider or model not proven to natively support JSON Schema definitions (where LiteLLM cannot safely translate capabilities) will **Fail Closed**, actively blocking the query understanding execution and propagating a `QueryUnderstandingConfigurationError` during the very first validation tick to prevent silent fallback to `json_object` or plaintext responses.

### 2. Secret Integrity Tracker

All LLM keys are handled internally as `SecretStr`. They are actively intercepted and exclusively unwrapped entirely at the final execution bound inside `litellm.acompletion`. This guarantees keys NEVER:
- Cascade into external debug logs.
- Traverse through native app observability boundaries.
- Materialize generically via `os.environ` manipulation. 

## Capability Exclusions

The current gateway does **not** implement recursive fallback execution topologies (e.g. cascading OpenAI -> Gemini -> Ollama internally). 

To ensure stability in PR-3.1, deployment availability sits exactly bounded to the explicitly active configuration.

