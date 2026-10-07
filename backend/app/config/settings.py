"""Configuration models and loader for multi-agent system."""

from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    """Configuration for an LLM provider and model parameters."""

    provider: str = Field(default="openai", description="Model provider: openai, anthropic, gemini/google, ollama, test")
    model: str = Field(default="gpt-5", description="Model identifier string")
    temperature: float = Field(default=0.2, description="Sampling temperature")
    max_tokens: int = Field(default=2500, description="Maximum tokens for completion")


class AgentConfig(BaseModel):
    """Configuration for an individual agent."""

    llm: str = Field(description="Key referencing the LLM configuration in llms.yaml")
    instructions: str = Field(description="System instructions for the agent")


class AppSecrets(BaseSettings):
    """Environment secrets loaded from .env."""

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class AppConfig(BaseModel):
    """Aggregate configuration combining LLMs, agents, and secrets."""

    llms: dict[str, LLMConfig]
    agents: dict[str, AgentConfig]
    secrets: AppSecrets


def find_config_dir() -> Path:
    """Locate the configuration directory containing llms.yaml and agents.yaml.

    Returns:
        Path to the config directory.
    """
    candidate_paths = [
        Path.cwd() / "config",
        Path(__file__).resolve().parent.parent.parent.parent / "config",
    ]
    for path in candidate_paths:
        if path.exists() and (path / "llms.yaml").exists():
            return path
    # Default fallback
    return Path.cwd() / "config"


def load_config(config_dir: Path | str | None = None) -> AppConfig:
    """Load LLM and agent configurations from YAML files and secrets from .env.

    Args:
        config_dir: Optional path to the configuration directory.

    Returns:
        AppConfig populated with loaded configurations.
    """
    base_dir = Path(config_dir) if config_dir else find_config_dir()
    llms_file = base_dir / "llms.yaml"
    agents_file = base_dir / "agents.yaml"

    if not llms_file.exists():
        raise FileNotFoundError(f"LLM config not found at: {llms_file}")
    if not agents_file.exists():
        raise FileNotFoundError(f"Agents config not found at: {agents_file}")

    with open(llms_file, "r", encoding="utf-8") as f:
        llms_raw: dict[str, Any] = yaml.safe_load(f) or {}

    with open(agents_file, "r", encoding="utf-8") as f:
        agents_raw: dict[str, Any] = yaml.safe_load(f) or {}

    llm_map = {
        name: LLMConfig(**data)
        for name, data in llms_raw.get("llms", {}).items()
    }
    agent_map = {
        name: AgentConfig(**data)
        for name, data in agents_raw.get("agents", {}).items()
    }

    secrets = AppSecrets()
    return AppConfig(llms=llm_map, agents=agent_map, secrets=secrets)
