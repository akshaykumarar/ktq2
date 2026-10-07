"""Agent Factory for dynamically building PydanticAI agents from configuration."""

from typing import Any
from pydantic import BaseModel
from pydantic_ai import Agent

from backend.app.config.settings import AppConfig, AgentConfig
from backend.app.providers.factory import create_model
from backend.app.agents.rfx import create_rfx_agent
from backend.app.agents.vendor import create_vendor_agent
from backend.app.agents.status import create_status_agent
from backend.app.agents.master import create_master_agent


class AgentRegistry:
    """Registry maintaining active agent instances and configurations."""

    def __init__(
        self,
        master: Agent,
        specialists: dict[str, Agent],
        config: AppConfig,
    ) -> None:
        self.master = master
        self.specialists = specialists
        self.config = config

    def get_specialist(self, name: str) -> Agent | None:
        """Retrieve specialist agent by name.

        Args:
            name: Agent identifier (e.g. 'rfx', 'vendor', 'status').

        Returns:
            Matching Agent or None.
        """
        return self.specialists.get(name)


class AgentFactory:
    """Factory responsible for instantiating independent agents from YAML configuration."""

    @staticmethod
    def create_registry(config: AppConfig) -> AgentRegistry:
        """Dynamically load and configure all agents defined in config.

        Args:
            config: Aggregated application configuration.

        Returns:
            Configured AgentRegistry with Master and Specialist agents.
        """
        specialists: dict[str, Agent] = {}

        # 1. Instantiate specialist agents first
        for name, agent_cfg in config.agents.items():
            if name == "master":
                continue

            llm_key = agent_cfg.llm
            if llm_key not in config.llms:
                raise KeyError(
                    f"Agent '{name}' references undefined LLM preset '{llm_key}' in llms.yaml"
                )

            llm_conf = config.llms[llm_key]
            model = create_model(llm_conf, config.secrets)

            if name == "rfx":
                specialists["rfx"] = create_rfx_agent(model, agent_cfg.instructions)
            elif name == "vendor":
                specialists["vendor"] = create_vendor_agent(model, agent_cfg.instructions)
            elif name == "status":
                specialists["status"] = create_status_agent(model, agent_cfg.instructions)
            else:
                # Generic specialist fallback allowing easy extension
                specialists[name] = Agent(model, system_prompt=agent_cfg.instructions)

        # 2. Instantiate Master orchestrator agent
        master_cfg = config.agents.get("master")
        if not master_cfg:
            raise KeyError("Configuration missing required 'master' agent definition in agents.yaml")

        master_llm_conf = config.llms[master_cfg.llm]
        master_model = create_model(master_llm_conf, config.secrets)
        master_agent = create_master_agent(
            model=master_model,
            instructions=master_cfg.instructions,
            specialist_agents=specialists,
        )

        return AgentRegistry(
            master=master_agent,
            specialists=specialists,
            config=config,
        )
