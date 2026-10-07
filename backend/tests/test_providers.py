"""Tests for provider abstraction and model factory."""

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel

from backend.app.config.settings import LLMConfig, AppSecrets
from backend.app.providers.factory import create_model


def test_create_model_fallback_to_mock() -> None:
    """Test that missing API keys gracefully fall back to TestModel when configured."""
    secrets = AppSecrets(OPENAI_API_KEY=None, ANTHROPIC_API_KEY=None, GOOGLE_API_KEY=None)
    llm_conf = LLMConfig(provider="openai", model="gpt-5")
    model = create_model(llm_conf, secrets, fallback_to_mock=True)
    assert isinstance(model, TestModel)


def test_create_model_explicit_test_provider() -> None:
    """Test explicit test/mock provider creation."""
    secrets = AppSecrets()
    llm_conf = LLMConfig(provider="test", model="mock-model")
    model = create_model(llm_conf, secrets)
    assert isinstance(model, TestModel)


def test_create_model_with_api_keys() -> None:
    """Test model creation with provided API keys."""
    secrets = AppSecrets(
        OPENAI_API_KEY="sk-test-openai",
        ANTHROPIC_API_KEY="sk-ant-test",
        GOOGLE_API_KEY="test-google-key",
    )

    m_openai = create_model(LLMConfig(provider="openai", model="gpt-4o"), secrets)
    assert isinstance(m_openai, OpenAIChatModel)

    m_anthropic = create_model(LLMConfig(provider="anthropic", model="claude-3-5-sonnet-latest"), secrets)
    assert isinstance(m_anthropic, AnthropicModel)

    m_google = create_model(LLMConfig(provider="google", model="gemini-1.5-flash"), secrets)
    assert isinstance(m_google, GoogleModel)


def test_create_model_ollama() -> None:
    """Test Ollama provider model creation."""
    secrets = AppSecrets(OLLAMA_BASE_URL="http://localhost:11434")
    m_ollama = create_model(LLMConfig(provider="ollama", model="llama3"), secrets)
    assert isinstance(m_ollama, OllamaModel)


def test_unsupported_provider_raises_value_error() -> None:
    """Test that unsupported provider raises ValueError."""
    secrets = AppSecrets()
    with pytest.raises(ValueError, match="Unsupported provider"):
        create_model(LLMConfig(provider="unknown_provider", model="custom"), secrets)
