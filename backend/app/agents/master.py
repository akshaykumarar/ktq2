"""Master Agent implementation for intent understanding and delegation."""

from typing import Any
import logging
import re
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from backend.app.agents.rfx import execute_rfx_task
from backend.app.agents.vendor import execute_vendor_task
from backend.app.agents.status import execute_status_task

logger = logging.getLogger(__name__)


def determine_specialist_fallback(query: str) -> str:
    """Determine target specialist agent using intent heuristics for offline/mock mode.

    Args:
        query: Incoming user message text.

    Returns:
        Agent identifier: 'rfx', 'vendor', 'status', or 'master'.
    """
    q = query.lower()

    # Priority 1: Status checks
    if any(k in q for k in ["check vendor response", "vendor response", "vendor status", "rfx status", "status of", "check status", "track rfx", "track order"]):
        return "status"

    # Priority 2: RFX creation / management
    if any(k in q for k in ["rfx", "rfq", "rfp", "create", "tender", "procure", "requisition", "laptop", "monitor", "purchase"]):
        return "rfx"

    # Priority 3: Vendor search / discovery
    if any(k in q for k in ["vendor", "supplier", "search vendor", "find vendor", "who sells", "directory", "distributor"]):
        return "vendor"

    # Default to Master if conversational / greeting
    return "master"


def create_master_agent(
    model: Model,
    instructions: str,
    specialist_agents: dict[str, Agent],
) -> Agent:
    """Create and configure the Master Orchestrator Agent.

    Args:
        model: PydanticAI model instance.
        instructions: System instructions for the Master agent.
        specialist_agents: Dictionary mapping agent names ('rfx', 'vendor', 'status') to Agents.

    Returns:
        Configured Master Agent with delegation tools.
    """
    agent = Agent(
        model,
        system_prompt=instructions,
    )

    rfx_agent = specialist_agents.get("rfx")
    vendor_agent = specialist_agents.get("vendor")
    status_agent = specialist_agents.get("status")

    if rfx_agent:
        @agent.tool_plain
        async def delegate_to_rfx(requirement: str) -> str:
            """Delegate RFX creation, drafting, or RFX management to the RFX specialist.

            Args:
                requirement: Procurement requirement details.

            Returns:
                Response from the RFX Specialist Agent.
            """
            return await execute_rfx_task(rfx_agent, requirement)

    if vendor_agent:
        @agent.tool_plain
        async def delegate_to_vendor(query: str) -> str:
            """Delegate vendor search and supplier evaluation to the Vendor specialist.

            Args:
                query: Supplier search criteria or vendor name.

            Returns:
                Response from the Vendor Specialist Agent.
            """
            return await execute_vendor_task(vendor_agent, query)

    if status_agent:
        @agent.tool_plain
        async def delegate_to_status(target: str) -> str:
            """Delegate status checks for RFXs or vendors to the Status specialist.

            Args:
                target: RFX or vendor identifier to inspect.

            Returns:
                Response from the Status Specialist Agent.
            """
            return await execute_status_task(status_agent, target)

    return agent


async def run_master_orchestration(
    master_agent: Agent,
    specialists: dict[str, Agent],
    user_message: str,
) -> tuple[str, str]:
    """Execute orchestration through the Master Agent to appropriate specialist.

    Args:
        master_agent: Configured Master agent.
        specialists: Dictionary of specialist agents.
        user_message: Incoming user prompt.

    Returns:
        Tuple of (response_message, responding_agent_name).
    """
    # If offline, TestModel, or fallback routing
    if isinstance(master_agent.model, TestModel):
        target = determine_specialist_fallback(user_message)
        if target == "rfx" and "rfx" in specialists:
            resp = await execute_rfx_task(specialists["rfx"], user_message)
            return resp, "rfx"
        elif target == "vendor" and "vendor" in specialists:
            resp = await execute_vendor_task(specialists["vendor"], user_message)
            return resp, "vendor"
        elif target == "status" and "status" in specialists:
            resp = await execute_status_task(specialists["status"], user_message)
            return resp, "status"
        else:
            return (
                "Hello! I am your Procurement Assistant. I can help you with:\n\n"
                "- **Creating and managing RFXs** (e.g. *'Create an RFX for 200 laptops'*)\n"
                "- **Searching vendors** (e.g. *'Find IT equipment suppliers'*)\n"
                "- **Checking status** (e.g. *'Check status for RFX-101'* or *'Check vendor response'*)\n\n"
                "How would you like to proceed?",
                "master",
            )

    # Live model execution: Let Master Agent delegate
    try:
        run_res = await master_agent.run(user_message)
        output_str = str(run_res.output)
        target = determine_specialist_fallback(user_message)
        agent_name = target if target in specialists else "master"
        return output_str, agent_name
    except Exception as exc:
        logger.warning("Master agent LLM run failed (%s); delegating via intent heuristics.", exc)
        target = determine_specialist_fallback(user_message)
        if target in specialists:
            if target == "rfx":
                resp = await execute_rfx_task(specialists["rfx"], user_message)
            elif target == "vendor":
                resp = await execute_vendor_task(specialists["vendor"], user_message)
            else:
                resp = await execute_status_task(specialists["status"], user_message)
            return resp, target
        return (
            "Hello! I am your Procurement Assistant. I can help you create RFXs, search vendors, or check status.",
            "master",
        )
