"""Provider abstraction for multi-agent LLM initialization."""

import logging
from typing import Union
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider

from backend.app.config.settings import LLMConfig, AppSecrets

logger = logging.getLogger(__name__)


def create_model(
    llm_config: LLMConfig,
    secrets: AppSecrets,
    fallback_to_mock: bool = True,
) -> Model:
    """Create a PydanticAI Model instance configured for the specified provider.

    Supports OpenAI, Anthropic, Gemini/Google, Ollama, and test/mock models.
    Falls back to TestModel when API keys are not supplied and fallback_to_mock is True.

    Args:
        llm_config: LLM configuration containing provider, model name, and parameters.
        secrets: Application secrets containing API keys and base URLs.
        fallback_to_mock: Whether to return TestModel if credentials are absent.

    Returns:
        PydanticAI Model instance.

    Raises:
        ValueError: If provider is unsupported or credentials missing and fallback disabled.
    """
    provider = llm_config.provider.lower().strip()
    model_name = llm_config.model

    if provider == "openai":
        api_key = secrets.OPENAI_API_KEY
        if api_key:
            return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=api_key))
        if fallback_to_mock:
            logger.warning(
                "OPENAI_API_KEY not found in environment; using TestModel fallback for '%s'.",
                model_name,
            )
            return TestModel()
        raise ValueError("OPENAI_API_KEY is required for OpenAI provider.")

    if provider == "anthropic":
        api_key = secrets.ANTHROPIC_API_KEY
        if api_key:
            return AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
        if fallback_to_mock:
            logger.warning(
                "ANTHROPIC_API_KEY not found in environment; using TestModel fallback for '%s'.",
                model_name,
            )
            return TestModel()
        raise ValueError("ANTHROPIC_API_KEY is required for Anthropic provider.")

    if provider in ("google", "gemini"):
        api_key = secrets.GOOGLE_API_KEY
        if api_key:
            return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
        if fallback_to_mock:
            logger.warning(
                "GOOGLE_API_KEY not found in environment; using TestModel fallback for '%s'.",
                model_name,
            )
            return TestModel()
        raise ValueError("GOOGLE_API_KEY is required for Google/Gemini provider.")

    if provider == "ollama":
        base_url = secrets.OLLAMA_BASE_URL.rstrip("/")
        # Connect to Ollama via OpenAI-compatible endpoints
        ollama_provider = OpenAIProvider(base_url=f"{base_url}/v1", api_key="ollama")
        return OllamaModel(model_name, provider=ollama_provider)

    if provider in ("test", "mock"):
        return TestModel()

    raise ValueError(
        f"Unsupported provider: '{provider}'. Supported providers: openai, anthropic, google, gemini, ollama, test."
    )
