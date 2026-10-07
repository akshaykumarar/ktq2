"""Tests for agent initialization and orchestration."""

import asyncio
import pytest
from pydantic_ai.models.test import TestModel

from backend.app.config.settings import load_config
from backend.app.agents.factory import AgentFactory
from backend.app.agents.master import run_master_orchestration
from backend.app.agents.rfx import create_rfx_agent, execute_rfx_task


def test_agent_factory_creates_all_agents() -> None:
    """Test that AgentFactory instantiates Master and Specialist agents."""
    config = load_config()
    registry = AgentFactory.create_registry(config)

    assert registry.master is not None
    assert "rfx" in registry.specialists
    assert "vendor" in registry.specialists
    assert "status" in registry.specialists


def test_rfx_agent_execution() -> None:
    """Test direct execution of RFX specialist agent."""
    async def _test() -> None:
        test_model = TestModel()
        agent = create_rfx_agent(test_model, instructions="You are an RFX specialist.")
        response = await execute_rfx_task(agent, "Create an RFX for 200 laptops")

        assert "RFX ID" in response
        assert "200" in response
        assert "Draft" in response

    asyncio.run(_test())


def test_master_agent_delegation_to_rfx() -> None:
    """Test Master Agent delegating procurement creation query to RFX agent."""
    async def _test() -> None:
        config = load_config()
        registry = AgentFactory.create_registry(config)

        # Force test model for predictable offline orchestration test
        registry.master.model = TestModel()
        for s in registry.specialists.values():
            s.model = TestModel()

        message, agent_name = await run_master_orchestration(
            registry.master,
            registry.specialists,
            "Create an RFX for 200 laptops",
        )

        assert agent_name == "rfx"
        assert "RFX ID" in message
        assert "200" in message

    asyncio.run(_test())
