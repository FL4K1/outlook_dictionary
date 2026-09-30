"""Search Synthesis specific exceptions."""


class SearchSynthesisError(Exception):
    """Base exception for search synthesis errors."""


class SynthesisConfigurationError(SearchSynthesisError):
    """Provider misconfiguration (missing API keys, no capability)."""


class SynthesisTransientError(SearchSynthesisError):
    """Temporary network or rate-limit failure (safe for graceful degradation)."""


class SynthesisPermanentError(SearchSynthesisError):
    """Permanent error, often due to bad request structures."""


class SynthesisMalformedOutputError(SearchSynthesisError):
    """Output did not match the strict schema or contained invalid references."""
