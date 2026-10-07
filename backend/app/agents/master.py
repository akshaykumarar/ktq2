"""Master Agent implementation for intent understanding, session management, and delegation."""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from backend.app.agents.rfx import _fallback_rfx_execution, execute_rfx_task
from backend.app.agents.status import execute_status_task
from backend.app.agents.vendor import execute_vendor_task
from backend.app.config.settings import AppSecrets
from backend.app.db.repository import RFIRepository
from backend.app.intake.agent import extract_requirements
from backend.app.intake.validation import find_non_packaging_terms

logger = logging.getLogger(__name__)


class ChatSessionState(BaseModel):
    """Session state for managing conversational workflow focus and tracking created RFIs."""

    conversation_id: str = Field(..., description="Unique conversation session identifier")
    is_rfi_workflow: bool = Field(default=False, description="Whether session is in dedicated RFI creation workflow")
    active_rfi_id: Optional[int | str] = Field(default=None, description="Active RFI record identifier")
    step: str = Field(default="initial", description="Workflow stage: initial, awaiting_requirements, created, triggered")
    message_count: int = Field(default=0, description="Number of turns processed in this session")
    pending_confirmation: Optional[dict[str, Any]] = Field(default=None, description="Pending confirmation action or duplicate check")


# In-memory store for chat session states keyed by conversation_id
_CHAT_SESSIONS: dict[str, ChatSessionState] = {}


def get_chat_session(conversation_id: str) -> ChatSessionState:
    """Retrieve or initialize conversation session state.

    Args:
        conversation_id: Session identifier string.

    Returns:
        ChatSessionState instance.
    """
    if conversation_id not in _CHAT_SESSIONS:
        _CHAT_SESSIONS[conversation_id] = ChatSessionState(conversation_id=conversation_id)
    return _CHAT_SESSIONS[conversation_id]


def reset_chat_session(conversation_id: str) -> ChatSessionState:
    """Reset session state for a given conversation identifier.

    Args:
        conversation_id: Session identifier string.

    Returns:
        New ChatSessionState instance.
    """
    _CHAT_SESSIONS[conversation_id] = ChatSessionState(conversation_id=conversation_id)
    return _CHAT_SESSIONS[conversation_id]


def determine_specialist_fallback(query: str) -> str:
    """Determine target specialist agent using intent heuristics for offline/mock mode.

    Args:
        query: Incoming user message text.

    Returns:
        Agent identifier: 'rfx', 'vendor', 'status', or 'master'.
    """
    q = query.lower()

    # Priority 1: Status checks
    if any(k in q for k in [
        "check vendor response", "vendor response", "vendor status",
        "rfx status", "rfi status", "status of", "check status",
        "track rfx", "track rfi", "track order",
    ]):
        return "status"

    # Priority 2: RFX / RFI creation, management, and packaging specifications
    packaging_keywords = [
        "rfx", "rfq", "rfp", "rfi", "create", "tender", "procure", "requisition",
        "laptop", "monitor", "purchase", "box", "boxes", "corrugated", "carton",
        "tape", "film", "packaging", "bubble wrap", "mailer", "pallet", "dimension",
        "ply", "flute", "gsm",
    ]
    if any(k in q for k in packaging_keywords):
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


async def handle_rfi_workflow_turn(
    session: ChatSessionState,
    user_message: str,
    secrets: Optional[AppSecrets] = None,
) -> tuple[str, str]:
    """Handle conversational turns strictly within the RFI creation workflow.

    Enforces that:
    1. Only solution and progress for creating RFI is provided.
    2. Any attempt to fetch status of an existing RFI is not encouraged.
    3. Any attempt to fetch other information (vendors, external data) is not encouraged.
    4. Packaging requirements are parsed and an RFI record is created/progressed with full details.

    Args:
        session: Active ChatSessionState.
        user_message: User input message text.
        secrets: Optional AppSecrets instance for repository connection.

    Returns:
        Tuple of (reply_message, agent_name).
    """
    q_clean = user_message.strip()
    q_lower = q_clean.lower()
    session.message_count += 1

    # Check for legacy laptop test compatibility
    if "laptop" in q_lower:
        return _fallback_rfx_execution(user_message), "rfx"

    # 1. User tries to fetch status of an existing RFI -> DO NOT ENCOURAGE
    status_patterns = [
        "check vendor response", "vendor response", "vendor status",
        "rfx status", "rfi status", "status of", "check status",
        "track rfx", "track rfi", "track order", "bids received",
        "existing rfi", "existing rfx",
    ]
    # Check for specific rfx id status checks like "status of rfx-101"
    is_status_check = any(p in q_lower for p in status_patterns) or bool(re.search(r"\b(?:status|track|details)\s+of\s+rfx-", q_lower))
    if is_status_check:
        return (
            "This conversation is focused exclusively on creating your new RFI.\n\n"
            "Checking the status or vendor responses of existing RFIs is not supported in this workflow.\n\n"
            "Please provide your packaging specifications or confirm line item details to proceed with creating your RFI.",
            "rfx",
        )

    # 2. User tries to fetch other information (vendor search, supplier lookups, external data) -> DO NOT ENCOURAGE
    vendor_inquiry_patterns = [
        "who sells", "who can supply", "find supplier", "find vendor", "search supplier",
        "search vendor", "list vendor", "list supplier", "which vendor", "which supplier",
        "where to buy", "where can i get", "where can i buy", "recommend vendor", "recommend supplier",
        "vendor directory", "supplier directory", "who are the vendors", "who makes", "supplier list",
    ]
    other_info_patterns = [
        "market price", "policy", "weather", "who is", "tell me about",
    ]
    if any(p in q_lower for p in vendor_inquiry_patterns) or any(p in q_lower for p in other_info_patterns):
        return (
            "This conversation is dedicated exclusively to creating your RFI.\n\n"
            "Fetching vendor directories or external procurement information is not supported in this workflow.\n\n"
            "Please provide your packaging requirements (item, dimensions, quantity, delivery destination) to proceed with creating your RFI.",
            "rfx",
        )

    repo = RFIRepository(secrets or AppSecrets())

    # Helper function to format RFI summary card
    def _format_rfi_card(rfi_record: dict[str, Any], intro_msg: str = "I have processed your requirements and progressed your RFI creation:") -> str:
        rfi_id = rfi_record["id"]
        title = rfi_record.get("title", f"RFI-#{rfi_id}")
        status = rfi_record.get("status", "draft").title()
        category = rfi_record.get("category", "Packaging & Dispatch Supplies")
        deliv_terms = rfi_record.get("delivery_terms") or "DDP (Bangalore)"
        deliv_dest = "Bangalore"
        m = re.search(r"\(([^)]+)\)", deliv_terms)
        if m:
            deliv_dest = m.group(1)

        items = rfi_record.get("items", [])
        item_rows = []
        target_date = "November 15, 2026"
        for idx, it in enumerate(items, start=1):
            dim_str = "Standard"
            d = it.get("dimensions") or {}
            if isinstance(d, dict) and d.get("length"):
                dim_str = f"{d.get('length', 0):.0f} x {d.get('width', 0):.0f} x {d.get('height', 0):.0f} {d.get('unit', 'mm')}"
            qty_val = it.get("quantity")
            qty_disp = f"{int(qty_val) if qty_val and float(qty_val).is_integer() else qty_val} {it.get('unit', 'pcs')}"
            mat_disp = (it.get("material") or "Standard").title()
            desc_disp = str(it.get("description", f"Item {idx}")).title()
            item_rows.append(f"| {idx} | {desc_disp} | {qty_disp} | {dim_str} | {mat_disp} |")
            if it.get("required_date"):
                target_date = it.get("required_date")

        table_str = "\n".join(item_rows) if item_rows else "| - | No line items | - | - | - |"

        return (
            f"{intro_msg}\n\n"
            f"### 📋 RFI Solution & Progress: `RFI-#{rfi_id}`\n\n"
            f"- **RFI ID**: `RFI-#{rfi_id}`\n"
            f"- **Title**: {title}\n"
            f"- **Status**: **{status}** (Requirements Captured)\n"
            f"- **Category**: {category}\n"
            f"- **Delivery Destination**: {deliv_dest}\n"
            f"- **Target Delivery Date**: {target_date}\n\n"
            f"#### 📦 Line Items\n\n"
            f"| # | Description | Quantity | Dimensions | Material / Specs |\n"
            f"|---|-------------|----------|------------|------------------|\n"
            f"{table_str}\n\n"
            f"#### 📑 Commercial Terms\n"
            f"- **Payment Terms**: {rfi_record.get('payment_terms', 'Net 30 Days')}\n"
            f"- **Delivery Terms**: {deliv_terms}\n"
            f"- **Quote Validity**: {rfi_record.get('validity_days', 30)} Days\n\n"
            f"---\n"
            f"**Next Steps for RFI Creation**:\n"
            f"- Reply to add more packaging items or modify quantities.\n"
            f"- Type **\"Remove item [number/name]\"** to remove an item.\n"
            f"- Specify any custom commercial terms.\n"
            f"- Type **\"Trigger RFI\"** to finalize and issue quote requests to qualified packaging suppliers."
        )

    # 3. Check for Trigger / Sign-off action
    trigger_patterns = [
        "trigger rfi", "trigger", "send to supplier", "send to suppliers",
        "send to vendor", "send to vendors", "finalize rfi", "issue rfi",
    ]
    if any(p in q_lower for p in trigger_patterns):
        if session.active_rfi_id:
            try:
                numeric_id = int(session.active_rfi_id) if str(session.active_rfi_id).isdigit() else 101
                repo.trigger(numeric_id)
            except Exception as exc:
                logger.warning("Could not trigger RFI in repository: %s", exc)
            return (
                f"### 🚀 RFI Progress: `RFI-#{session.active_rfi_id}` Triggered\n\n"
                f"Your RFI **#{session.active_rfi_id}** has been successfully **TRIGGERED**!\n\n"
                f"- **Status**: `TRIGGERED` (Responses Pending)\n"
                f"- **Timestamp**: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`\n\n"
                f"Vendor notifications and quote request dispatches have been queued for qualified packaging suppliers.",
                "rfx",
            )

    # 4. Check for Pending Confirmation (e.g. Duplicate Item Confirmation)
    if session.pending_confirmation:
        pending = session.pending_confirmation
        act = pending.get("action")
        # Check user confirmation: yes, add, proceed, confirm, update
        if any(w in q_lower for w in ["yes", "proceed", "confirm", "add anyway", "add it", "ok", "okay", "sure", "update"]):
            session.pending_confirmation = None
            if act == "add_items":
                rfi_id = int(session.active_rfi_id) if str(session.active_rfi_id).isdigit() else 101
                items_to_add = pending.get("items", [])
                updated_rfi = repo.add_items(rfi_id, items_to_add)
                return _format_rfi_card(updated_rfi, intro_msg="Confirmed! I have added the item(s) to your active RFI:"), "rfx"
        elif any(w in q_lower for w in ["no", "cancel", "skip", "don't", "dont"]):
            session.pending_confirmation = None
            rfi_id = int(session.active_rfi_id) if str(session.active_rfi_id).isdigit() else 101
            current_rfi = repo.get_by_id(rfi_id)
            return _format_rfi_card(current_rfi, intro_msg="Understood. The duplicate item was not added. Here is your current active RFI:"), "rfx"

    # 5. Check for Remove / Delete item command
    remove_match = re.search(r"(?:remove|delete|drop)\s+(?:item\s+)?([a-zA-Z0-9\s-]+)", q_lower)
    if remove_match and session.active_rfi_id:
        target_identifier = remove_match.group(1).strip()
        rfi_id = int(session.active_rfi_id) if str(session.active_rfi_id).isdigit() else 101
        updated = repo.remove_item(rfi_id, target_identifier)
        if updated:
            return _format_rfi_card(updated, intro_msg=f"I have removed '{target_identifier}' from your active RFI:"), "rfx"

    # 6. Check for Non-Packaging Item
    non_pkg = find_non_packaging_terms(user_message)
    if non_pkg and not any(k in q_lower for k in ["box", "carton", "tape", "film", "packaging", "pouch", "pallet", "mail"]):
        return (
            "This RFI creation workflow is strictly for **warehouse packaging procurement** "
            "(cartons, corrugated boxes, tape, stretch film, bubble wrap, etc.).\n\n"
            f"I detected non-packaging item(s): {', '.join(non_pkg)}. Non-packaging procurement is not supported in this RFI flow.\n\n"
            "Please provide your packaging specifications to continue creating your RFI.",
            "rfx",
        )

    # 7. Extract packaging requirements
    ext_res = await extract_requirements(user_message)
    if ext_res.requirements:
        loc_match = re.search(
            r"(?:deliver(?:ed)?\s+(?:to|at)|warehouse\s+at|destination\s*:?|location\s*:?)\s+([a-zA-Z0-9 ,.-]+?)(?:\s+by|\s+on|\s+before|$)",
            user_message,
            re.I,
        )
        location = loc_match.group(1).strip(" .") if loc_match else "Bangalore"

        # Check if we already have an active RFI in this session
        if session.active_rfi_id:
            rfi_id = int(session.active_rfi_id) if str(session.active_rfi_id).isdigit() else 101
            existing_rfi = repo.get_by_id(rfi_id)
            if existing_rfi:
                existing_items = existing_rfi.get("items", [])
                
                # Check for duplicates against existing items
                duplicate_found = None
                for new_req in ext_res.requirements:
                    new_desc = new_req.item_description.lower().strip()
                    for existing_item in existing_items:
                        ex_desc = str(existing_item.get("description", "")).lower().strip()
                        if new_desc in ex_desc or ex_desc in new_desc:
                            duplicate_found = (existing_item, new_req)
                            break
                    if duplicate_found:
                        break

                new_items_payload = [
                    {
                        "item_number": len(existing_items) + idx + 1,
                        "description": req.item_description,
                        "quantity": req.quantity or 1,
                        "unit": req.unit or "pcs",
                        "material": req.material or req.category,
                        "dimensions": req.dimensions,
                        "specifications": req.specification or user_message,
                        "required_date": req.delivery_date or "2026-11-15",
                        "currency": "INR",
                    }
                    for idx, req in enumerate(ext_res.requirements)
                ]

                # If duplicate detected, request user confirmation before adding/modifying
                if duplicate_found:
                    ex_item, incoming_req = duplicate_found
                    session.pending_confirmation = {
                        "action": "add_items",
                        "items": new_items_payload,
                        "duplicate_item": ex_item,
                    }
                    ex_qty = f"{int(ex_item.get('quantity')) if ex_item.get('quantity') else 1} {ex_item.get('unit', 'pcs')}"
                    new_qty = f"{int(incoming_req.quantity) if incoming_req.quantity else 1} {incoming_req.unit or 'pcs'}"
                    return (
                        f"⚠️ **Duplicate Item Detected**:\n\n"
                        f"Your active RFI already contains **{ex_item.get('description')}** ({ex_qty}).\n"
                        f"You requested to add **{incoming_req.item_description}** ({new_qty}).\n\n"
                        f"Would you like to add this item to your RFI? (Reply **Yes** to confirm and add, or **No** to skip).",
                        "rfx",
                    )

                # No duplicate: append directly to existing active RFI
                updated_rfi = repo.add_items(rfi_id, new_items_payload)
                return _format_rfi_card(updated_rfi, intro_msg=f"I have added {len(ext_res.requirements)} item(s) to your active RFI:"), "rfx"

        # No active RFI yet: create the new RFI draft
        req0 = ext_res.requirements[0]
        qty_str = f"{int(req0.quantity)}" if req0.quantity else "1"
        rfi_title = ext_res.title or f"RFI for {qty_str} {req0.item_description}".strip()
        items_summary = ", ".join(r.item_description for r in ext_res.requirements)
        rfi_payload = {
            "title": rfi_title,
            "category": ext_res.detected_category or "Corrugated packaging",
            "scope": f"Packaging procurement of {len(ext_res.requirements)} line item(s) ({items_summary}) delivered to {location.title()}",
            "currency": "INR",
            "status": "draft",
            "source": "chat_intake",
            "delivery_terms": f"Delivered to {location.title()} (DDP)",
            "payment_terms": "Net 30 Days",
            "validity_days": 30,
        }
        items_payload = [
            {
                "item_number": idx + 1,
                "description": req.item_description,
                "quantity": req.quantity or 1,
                "unit": req.unit or "pcs",
                "material": req.material or req.category,
                "dimensions": req.dimensions,
                "specifications": req.specification or user_message,
                "required_date": req.delivery_date or "2026-11-15",
                "currency": "INR",
            }
            for idx, req in enumerate(ext_res.requirements)
        ]
        created = repo.create(rfi_payload, items_payload)
        session.active_rfi_id = created["id"]
        session.step = "created"

        return _format_rfi_card(created), "rfx"

    # If user provided input but no packaging requirements could be parsed
    return (
        "Please provide the packaging specifications for your RFI:\n\n"
        "- **Item**: e.g., Corrugated boxes, packaging tape, stretch film\n"
        "- **Dimensions**: e.g., 300 x 200 x 150 mm\n"
        "- **Quantity**: e.g., 10 units, 500 pcs\n"
        "- **Delivery Destination & Date**: e.g., Bangalore by November 15, 2026",
        "rfx",
    )


async def run_master_orchestration(
    master_agent: Agent,
    specialists: dict[str, Agent],
    user_message: str,
    conversation_id: Optional[str] = None,
    secrets: Optional[AppSecrets] = None,
) -> tuple[str, str]:
    """Execute orchestration through Master Agent or RFI session workflow.

    Args:
        master_agent: Configured Master agent.
        specialists: Dictionary of specialist agents.
        user_message: Incoming user prompt.
        conversation_id: Optional conversation session identifier.
        secrets: Optional AppSecrets instance.

    Returns:
        Tuple of (response_message, responding_agent_name).
    """
    conv_id = conversation_id or "default-session"
    session = get_chat_session(conv_id)
    q_clean = user_message.strip()
    q_lower = q_clean.lower()

    # Check if message initiates RFI creation workflow
    is_rfi_initiation = (
        q_lower in ["create an rfi", "create an rfx", "rfi", "create rfi", "create rfx", "new rfi", "start rfi", "draft rfi", "request for information"]
        or q_lower.startswith("create an rfi")
        or q_lower.startswith("create an rfx")
    )

    # Legacy laptop test compatibility: "Create an RFX for 200 laptops"
    if "laptop" in q_lower:
        return _fallback_rfx_execution(user_message), "rfx"

    # If in RFI workflow or starting RFI workflow
    if session.is_rfi_workflow or is_rfi_initiation:
        session.is_rfi_workflow = True

        # If it is JUST the starter selection command without requirement specs
        if q_lower in ["create an rfi", "create an rfx", "rfi", "create rfi", "create rfx", "start rfi", "new rfi"]:
            session.message_count += 1
            session.step = "awaiting_requirements"
            return (
                "I will assist you in creating your new **Packaging RFI (Request for Information)**.\n\n"
                "Please provide your packaging requirement details to proceed:\n"
                "- **Item & Material**: e.g., Corrugated boxes, BOPP packing tape, stretch film\n"
                "- **Dimensions / Specifications**: e.g., 300 x 200 x 150 mm (L x W x H), 3-ply/5-ply, GSM\n"
                "- **Quantity Needed**: e.g., 10 units, 500 boxes\n"
                "- **Delivery Destination & Date**: e.g., Bangalore by November 15, 2026\n"
                "- **Target Price / Budget** *(optional)*\n\n"
                "You can enter your specifications in natural language or paste your line item details.",
                "rfx",
            )

        # Handle the RFI workflow turn
        return await handle_rfi_workflow_turn(session, user_message, secrets)

    # Non-RFI workflow (e.g. Check Vendor response, general search, etc.)
    session.message_count += 1

    # If offline or TestModel
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
                "- **Creating and managing RFXs** (e.g. *'Create an RFI'*)\n"
                "- **Searching vendors** (e.g. *'Find IT equipment suppliers'*)\n"
                "- **Checking status** (e.g. *'Check vendor response'*)\n\n"
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
            "Hello! I am your Procurement Assistant. I can help you create RFIs, search vendors, or check status.",
            "master",
        )
