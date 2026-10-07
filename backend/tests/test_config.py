"""Tests for configuration loading and validation."""

from pathlib import Path
import pytest
from backend.app.config.settings import load_config, LLMConfig, AgentConfig


def test_load_config_success() -> None:
    """Test loading valid configuration files from config directory."""
    config = load_config()

    # Validate LLMs
    assert "reasoning" in config.llms
    assert "fast" in config.llms
    assert config.llms["reasoning"].provider == "openai"
    assert config.llms["reasoning"].model == "gpt-5"
    assert config.llms["fast"].model == "gpt-5-mini"

    # Validate Agents
    assert "master" in config.agents
    assert "rfx" in config.agents
    assert "vendor" in config.agents
    assert "status" in config.agents
    assert config.agents["master"].llm == "reasoning"
    assert config.agents["rfx"].llm == "reasoning"
    assert config.agents["vendor"].llm == "fast"
    assert config.agents["status"].llm == "fast"


def test_llm_config_model_validation() -> None:
    """Test LLMConfig Pydantic model validation."""
    conf = LLMConfig(provider="anthropic", model="claude-3-5-sonnet", temperature=0.7, max_tokens=1000)
    assert conf.provider == "anthropic"
    assert conf.temperature == 0.7
    assert conf.max_tokens == 1000


def test_agent_config_model_validation() -> None:
    """Test AgentConfig Pydantic model validation."""
    agent_conf = AgentConfig(llm="reasoning", instructions="Test instructions")
    assert agent_conf.llm == "reasoning"
    assert agent_conf.instructions == "Test instructions"


def test_dynamic_agent_model_reconfiguration(tmp_path: Path) -> None:
    """Test changing an agent's model mapping purely via configuration without python code changes."""
    import yaml
    from backend.app.agents.factory import AgentFactory

    # Create temporary config directory
    llms_data = {
        "llms": {
            "reasoning": {"provider": "test", "model": "reasoning-model"},
            "fast": {"provider": "test", "model": "fast-model"},
        }
    }
    # Initially rfx uses reasoning
    agents_data = {
        "agents": {
            "master": {"llm": "reasoning", "instructions": "Master instructions"},
            "rfx": {"llm": "reasoning", "instructions": "RFX instructions"},
            "vendor": {"llm": "fast", "instructions": "Vendor instructions"},
            "status": {"llm": "fast", "instructions": "Status instructions"},
        }
    }

    (tmp_path / "llms.yaml").write_text(yaml.dump(llms_data), encoding="utf-8")
    (tmp_path / "agents.yaml").write_text(yaml.dump(agents_data), encoding="utf-8")

    cfg1 = load_config(tmp_path)
    reg1 = AgentFactory.create_registry(cfg1)
    assert reg1.config.agents["rfx"].llm == "reasoning"

    # Now change agents.yaml so rfx uses fast
    agents_data["agents"]["rfx"]["llm"] = "fast"
    (tmp_path / "agents.yaml").write_text(yaml.dump(agents_data), encoding="utf-8")

    cfg2 = load_config(tmp_path)
    reg2 = AgentFactory.create_registry(cfg2)
    assert reg2.config.agents["rfx"].llm == "fast"

