"""RFX Specialist Agent implementation using PydanticAI."""

from typing import Any
import logging
import re
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from backend.app.tools.rfx import create_rfx, get_rfx

logger = logging.getLogger(__name__)


def create_rfx_agent(model: Model, instructions: str) -> Agent:
    """Create and configure the RFX Specialist Agent.

    Args:
        model: PydanticAI model instance.
        instructions: System prompt / instructions for the RFX agent.

    Returns:
        Configured PydanticAI Agent with RFX tools registered.
    """
    agent = Agent(
        model,
        system_prompt=instructions,
    )

    @agent.tool_plain
    def tool_create_rfx(
        title: str,
        quantity: int = 1,
        budget: float | None = None,
        delivery_date: str | None = None,
        specifications: str = "",
    ) -> dict[str, Any]:
        """Create a new RFX (Request for Quote/Proposal).

        Args:
            title: Title or summary of requirement (e.g. '200 laptops').
            quantity: Number of units required.
            budget: Estimated budget.
            delivery_date: Target delivery date.
            specifications: Specifications or technical requirements.

        Returns:
            Dictionary with creation status and RFX ID.
        """
        return create_rfx(
            title=title,
            quantity=quantity,
            budget=budget,
            delivery_date=delivery_date,
            specifications=specifications,
        )

    @agent.tool_plain
    def tool_get_rfx(rfx_id: str) -> dict[str, Any]:
        """Retrieve details and bids for an existing RFX.

        Args:
            rfx_id: RFX identifier such as 'RFX-101'.

        Returns:
            Dictionary with RFX details and vendor bidding activity.
        """
        return get_rfx(rfx_id=rfx_id)

    return agent


def _fallback_rfx_execution(query: str) -> str:
    """Deterministic fallback using mock tools when offline or API call fails."""
    q_lower = query.strip().lower()
    if q_lower in ["create an rfx", "create an rfi", "rfi", "create rfi", "create rfx"]:
        return (
            "I will assist you in creating your new **Packaging RFI (Request for Information)**.\n\n"
            "Please provide your packaging requirement details to proceed:\n"
            "- **Item & Material**: e.g., Corrugated boxes, BOPP packing tape, stretch film\n"
            "- **Dimensions / Specifications**: e.g., 300 x 200 x 150 mm (L x W x H), 3-ply/5-ply, GSM\n"
            "- **Quantity Needed**: e.g., 10 units, 500 boxes\n"
            "- **Delivery Destination & Date**: e.g., Bangalore by November 15, 2026\n"
            "- **Target Price / Budget** *(optional)*\n\n"
            "You can enter your specifications in natural language or paste your line item details."
        )

    if "create" in q_lower or "new" in q_lower or "rfx for" in q_lower or "laptop" in q_lower or "box" in q_lower:
        qty_match = re.search(r"(\d+)", query)
        qty = int(qty_match.group(1)) if qty_match else 1
        result = create_rfx(title=query, quantity=qty)
        return (
            f"I have created a new RFX for you:\n\n"
            f"- **RFX ID**: `{result['rfx_id']}`\n"
            f"- **Title**: {result['title']}\n"
            f"- **Quantity**: {result['quantity']}\n"
            f"- **Status**: {result['lifecycle_state']}\n"
            f"- **Target Delivery**: {result['delivery_date']}\n\n"
            f"*(Processed by RFX Specialist via mock `create_rfx()`)*"
        )

    id_match = re.search(r"(rfx-[a-z0-9]+)", q_lower)
    rfx_id = id_match.group(1).upper() if id_match else "RFX-101"
    res = get_rfx(rfx_id)
    return (
        f"Here are the details for **{res['rfx_id']}**:\n\n"
        f"- **Status**: {res['lifecycle_state']}\n"
        f"- **Invited Vendors**: {res['total_invited_vendors']}\n"
        f"- **Bids Received**: {res['bids_received']}\n"
        f"- **Deadline**: {res['deadline']}\n\n"
        f"*(Processed by RFX Specialist via mock `get_rfx()`)*"
    )


async def execute_rfx_task(agent: Agent, query: str) -> str:
    """Execute an RFX task through the RFX agent with graceful mock handling.

    Args:
        agent: The RFX specialist agent.
        query: User message or delegated query.

    Returns:
        Agent response string.
    """
    if isinstance(agent.model, TestModel):
        return _fallback_rfx_execution(query)

    try:
        run_result = await agent.run(query)
        return str(run_result.output)
    except Exception as exc:
        logger.warning("RFX agent LLM call encountered error (%s); falling back to mock tools.", exc)
        return _fallback_rfx_execution(query)
