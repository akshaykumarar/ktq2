"""Vendor Specialist Agent implementation using PydanticAI."""

from typing import Any
import logging
import re
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from backend.app.tools.vendor import search_vendors, get_vendor_status

logger = logging.getLogger(__name__)


def create_vendor_agent(model: Model, instructions: str) -> Agent:
    """Create and configure the Vendor Specialist Agent.

    Args:
        model: PydanticAI model instance.
        instructions: System prompt / instructions for the Vendor agent.

    Returns:
        Configured PydanticAI Agent with Vendor tools registered.
    """
    agent = Agent(
        model,
        system_prompt=instructions,
    )

    @agent.tool_plain
    def tool_search_vendors(query: str, category: str = "") -> list[dict[str, Any]]:
        """Search registered suppliers by product category or name.

        Args:
            query: Keyword query (e.g. 'laptops', 'hardware', 'Dell').
            category: Optional category filter.

        Returns:
            List of matching supplier records.
        """
        return search_vendors(query=query, category=category)

    @agent.tool_plain
    def tool_get_vendor_status(vendor_id: str) -> dict[str, Any]:
        """Check vendor onboarding compliance and status.

        Args:
            vendor_id: Supplier ID or name (e.g. 'VEND-101' or 'Dell').

        Returns:
            Status summary and rating.
        """
        return get_vendor_status(vendor_id=vendor_id)

    return agent


def _fallback_vendor_execution(query: str) -> str:
    """Deterministic fallback using mock vendor tools when offline or API call fails."""
    q_lower = query.lower()
    if "status" in q_lower:
        id_match = re.search(r"(vend-\d+|dell|lenovo|hp|officecore)", q_lower)
        target = id_match.group(1) if id_match else "Dell Global Solutions"
        res = get_vendor_status(target)
        return (
            f"**Vendor Status Report**:\n\n"
            f"- **Vendor**: {res.get('name', target)}\n"
            f"- **Status**: {res.get('compliance_status')}\n"
            f"- **Rating**: {res.get('rating')}/5.0\n"
            f"- **Summary**: {res.get('message')}\n\n"
            f"*(Processed by Vendor Specialist via mock `get_vendor_status()`)*"
        )

    vendors = search_vendors(query)
    lines = [f"- **{v['name']}** ({v['vendor_id']}) — Category: {v['category']}, Rating: {v['rating']}/5.0, Status: {v['compliance_status']}" for v in vendors]
    return (
        f"Here are the matching vendors from our supplier directory:\n\n"
        + "\n".join(lines)
        + "\n\n*(Processed by Vendor Specialist via mock `search_vendors()`)*"
    )


async def execute_vendor_task(agent: Agent, query: str) -> str:
    """Execute a vendor task through the Vendor agent with graceful mock handling.

    Args:
        agent: The Vendor specialist agent.
        query: User message or delegated query.

    Returns:
        Agent response string.
    """
    if isinstance(agent.model, TestModel):
        return _fallback_vendor_execution(query)

    try:
        run_result = await agent.run(query)
        return str(run_result.output)
    except Exception as exc:
        logger.warning("Vendor agent LLM call encountered error (%s); falling back to mock tools.", exc)
        return _fallback_vendor_execution(query)
