"""Procurement Status Specialist Agent implementation using PydanticAI."""

from typing import Any
import logging
import re
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from backend.app.tools.status import get_rfx_status, get_vendor_status

logger = logging.getLogger(__name__)


def create_status_agent(model: Model, instructions: str) -> Agent:
    """Create and configure the Status Specialist Agent.

    Args:
        model: PydanticAI model instance.
        instructions: System prompt / instructions for the Status agent.

    Returns:
        Configured PydanticAI Agent with Status tools registered.
    """
    agent = Agent(
        model,
        system_prompt=instructions,
    )

    @agent.tool_plain
    def tool_get_rfx_status(rfx_id: str) -> dict[str, Any]:
        """Check status and bidding progress of an RFX.

        Args:
            rfx_id: RFX identifier such as 'RFX-101'.

        Returns:
            Current stage, progress percentage, and next steps.
        """
        return get_rfx_status(rfx_id=rfx_id)

    @agent.tool_plain
    def tool_get_vendor_status(vendor_id: str) -> dict[str, Any]:
        """Check compliance and activity status of a vendor.

        Args:
            vendor_id: Vendor identifier or name.

        Returns:
            Vendor status summary.
        """
        return get_vendor_status(vendor_id=vendor_id)

    return agent


def _fallback_status_execution(query: str) -> str:
    """Deterministic fallback using mock status tools when offline or API call fails."""
    q_lower = query.lower()
    if "vendor" in q_lower or "vend-" in q_lower:
        id_match = re.search(r"(vend-\d+|dell|lenovo|hp)", q_lower)
        target = id_match.group(1) if id_match else "VEND-101"
        res = get_vendor_status(target)
        return (
            f"**Vendor Status**:\n\n"
            f"- **Vendor**: {res.get('name', target)}\n"
            f"- **Compliance**: {res.get('compliance_status')}\n"
            f"- **Rating**: {res.get('rating')}/5.0\n\n"
            f"*(Processed by Status Specialist via mock `get_vendor_status()`)*"
        )

    id_match = re.search(r"(rfx-[a-z0-9]+)", q_lower)
    rfx_id = id_match.group(1).upper() if id_match else "RFX-101"
    res = get_rfx_status(rfx_id)
    return (
        f"**RFX Status Report ({res['rfx_id']})**:\n\n"
        f"- **Stage**: {res['stage']}\n"
        f"- **Progress**: {res['progress_percentage']}%\n"
        f"- **Submitted Quotes**: {res['submitted_quotes']}\n"
        f"- **Next Action**: {res['next_action']}\n"
        f"- **Estimated Closure**: {res['estimated_closure']}\n\n"
        f"*(Processed by Status Specialist via mock `get_rfx_status()`)*"
    )


async def execute_status_task(agent: Agent, query: str) -> str:
    """Execute a status inspection task through the Status agent with graceful mock handling.

    Args:
        agent: The Status specialist agent.
        query: User message or delegated query.

    Returns:
        Agent response string.
    """
    if isinstance(agent.model, TestModel):
        return _fallback_status_execution(query)

    try:
        run_result = await agent.run(query)
        return str(run_result.output)
    except Exception as exc:
        logger.warning("Status agent LLM call encountered error (%s); falling back to mock tools.", exc)
        return _fallback_status_execution(query)
