"""Mock RFX tools for procurement management.

NOTE: These are mock implementations designed to be replaced with real
APIs, database adapters, or Make.com webhooks in subsequent iterations.
"""

from typing import Any
import uuid
from datetime import datetime, timezone


def create_rfx(
    title: str,
    quantity: int = 1,
    budget: float | None = None,
    delivery_date: str | None = None,
    specifications: str = "",
) -> dict[str, Any]:
    """Create a new Request For Quote / Proposal (RFX).

    [MOCK IMPLEMENTATION]
    Simulates RFX creation in the procurement system.

    Args:
        title: Title or summary of the procurement requirement (e.g. '200 laptops').
        quantity: Number of units or scope volume.
        budget: Estimated maximum budget in USD, if specified.
        delivery_date: Target delivery date string, if specified.
        specifications: Key technical requirements or notes.

    Returns:
        Dictionary containing created RFX details and confirmation.
    """
    mock_id = f"RFX-{uuid.uuid4().hex[:6].upper()}"
    return {
        "status": "success",
        "mock": True,
        "rfx_id": mock_id,
        "title": title,
        "quantity": quantity,
        "budget": budget or 50000.0,
        "delivery_date": delivery_date or "2026-11-15",
        "specifications": specifications or "Standard corporate tier specifications",
        "lifecycle_state": "Draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Successfully created RFX '{mock_id}' for {quantity} units of '{title}' in Draft status.",
    }


def get_rfx(rfx_id: str) -> dict[str, Any]:
    """Retrieve details and line items for an existing RFX.

    [MOCK IMPLEMENTATION]
    Simulates fetching RFX data by identifier.

    Args:
        rfx_id: Unique RFX identifier (e.g. 'RFX-101' or 'RFX-A9B2C3').

    Returns:
        Dictionary containing RFX metadata, status, and line items.
    """
    clean_id = rfx_id.strip().upper()
    return {
        "status": "success",
        "mock": True,
        "rfx_id": clean_id,
        "title": f"Procurement Request {clean_id}",
        "lifecycle_state": "Open for Bidding",
        "total_invited_vendors": 4,
        "bids_received": 2,
        "deadline": "2026-10-31T18:00:00Z",
        "owner": "procurement-team@company.com",
        "message": f"RFX '{clean_id}' is currently Open for Bidding with 2 vendor proposals submitted.",
    }
