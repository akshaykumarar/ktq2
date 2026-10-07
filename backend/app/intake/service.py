"""Deterministic packaging RFX intake workflow."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from backend.app.intake.excel_parser import rows_to_line_items
from backend.app.intake.models import (
    AttachmentExtraction,
    IntakeResponse,
    PackagingIntakeState,
    PackagingLineItem,
)
from backend.app.intake.validation import find_non_packaging_terms, is_packaging_related


_SESSIONS: dict[str, PackagingIntakeState] = {}


def get_intake_state(conversation_id: str) -> PackagingIntakeState:
    """Return existing intake state or initialize a new conversation state."""
    if conversation_id not in _SESSIONS:
        _SESSIONS[conversation_id] = PackagingIntakeState(conversation_id=conversation_id)
    return _SESSIONS[conversation_id]


def reset_intake_state(conversation_id: str) -> PackagingIntakeState:
    """Reset one conversation state."""
    _SESSIONS[conversation_id] = PackagingIntakeState(conversation_id=conversation_id)
    return _SESSIONS[conversation_id]


def handle_packaging_intake(
    *,
    conversation_id: str,
    message: str = "",
    attachments: list[AttachmentExtraction] | None = None,
) -> IntakeResponse:
    """Merge text and parsed spreadsheets, ask next question, or request sign-off."""
    state = get_intake_state(conversation_id)
    message = message.strip()
    state.updated_at = datetime.now(timezone.utc)

    if message:
        state.raw_user_text.append(message)

    if attachments:
        state.attachments.extend(attachments)
        parsed_rows = [row for attachment in attachments for row in attachment.rows]
        state.extracted_data.setdefault("rows", []).extend(parsed_rows)
        _merge_line_items(state, rows_to_line_items(parsed_rows))

    if _is_affirmative(message) and state.status == "needs_domain_confirmation":
        state.status = "collecting"
        state.current_question = None
        state.current_default = None
        return _ask_for_packaging_items(state)

    if _is_final_approval(message) and state.status == "awaiting_confirmation":
        state.status = "approved"
        return IntakeResponse(
            message=(
                "Approved. I will create the packaging RFX draft and queue quote requests once the "
                "vendor API adapter is configured.\n\n"
                f"{_format_line_items_and_terms(state)}"
            ),
            conversation_id=conversation_id,
            status=state.status,
            state=state,
        )

    if not message and state.current_default:
        _apply_default(state)
    elif message:
        _merge_text(state, message)

    non_packaging_terms = find_non_packaging_terms(message)
    if non_packaging_terms and not is_packaging_related(message, state.line_items):
        state.status = "needs_domain_confirmation"
        state.current_question = (
            "This demo is only for warehouse packaging procurement. "
            f"I detected non-packaging item(s): {', '.join(non_packaging_terms)}. "
            "Please confirm whether you want to switch this request to packaging items."
        )
        state.current_default = None
        return IntakeResponse(
            message=f"Error: unsupported procurement category.\n\n{state.current_question}",
            conversation_id=conversation_id,
            status=state.status,
            state=state,
        )

    if not is_packaging_related(" ".join(state.raw_user_text), state.line_items):
        return _ask_for_packaging_items(state)

    state.missing_fields = _missing_fields(state)
    if state.missing_fields:
        state.status = "collecting"
        return _ask_next_missing_field(state)

    state.status = "awaiting_confirmation"
    state.current_question = "Please confirm if I should proceed with these packaging line items and terms."
    state.current_default = None
    return IntakeResponse(
        message=(
            "Before I make the final decision, please review the packaging line items and terms:\n\n"
            f"{_format_line_items_and_terms(state)}\n\n"
            "Reply `yes` to approve, or tell me what to change."
        ),
        conversation_id=conversation_id,
        status=state.status,
        state=state,
    )


def _merge_line_items(state: PackagingIntakeState, items: list[PackagingLineItem]) -> None:
    if not items:
        return
    existing_descriptions = {item.description.lower() for item in state.line_items}
    for item in items:
        if item.description.lower() not in existing_descriptions:
            item.item_number = len(state.line_items) + 1
            state.line_items.append(item)
            existing_descriptions.add(item.description.lower())


def _merge_text(state: PackagingIntakeState, message: str) -> None:
    lowered = message.lower()

    if not state.title and any(word in lowered for word in ["rfx", "rfq", "rfi", "quote", "procure", "buy", "need"]):
        state.title = _title_from_message(message)

    if not state.delivery_location:
        location_match = re.search(r"(?:deliver(?:y)?\s+(?:to|at)|warehouse\s+at|location\s*:?)\s+([a-zA-Z0-9 ,.-]+)", message, re.I)
        if location_match:
            state.delivery_location = location_match.group(1).strip(" .")

    if "custom terms" in lowered:
        state.terms.terms_type = "custom"
        state.terms.custom_terms.append(message)
    elif "standard terms" in lowered:
        state.terms.terms_type = "standard"
    elif "combo" in lowered or "combination" in lowered:
        state.terms.terms_type = "combo"
        state.terms.custom_terms.append(message)

    if not state.line_items:
        item = _line_item_from_text(message)
        if item:
            state.line_items.append(item)


def _line_item_from_text(message: str) -> PackagingLineItem | None:
    lowered = message.lower()
    packaging_terms = [
        "carton",
        "box",
        "corrugated",
        "tape",
        "label",
        "stretch film",
        "bubble wrap",
        "pouch",
        "mailer",
        "pallet",
        "void fill",
    ]
    category = next((term for term in packaging_terms if term in lowered), None)
    if not category:
        return None

    qty_match = re.search(r"(\d+(?:,\d+)*(?:\.\d+)?)", message)
    quantity = float(qty_match.group(1).replace(",", "")) if qty_match else None
    unit = "pcs" if quantity else None
    return PackagingLineItem(
        item_number=1,
        description=message,
        packaging_category=category,
        quantity=quantity,
        unit=unit,
        specifications=message,
    )


def _title_from_message(message: str) -> str:
    clean = re.sub(r"\s+", " ", message).strip()
    return clean[:100] if clean else "Packaging RFX"


def _missing_fields(state: PackagingIntakeState) -> list[str]:
    missing: list[str] = []
    if not state.line_items:
        missing.append("line_items")
    if any(item.quantity is None for item in state.line_items):
        missing.append("quantity")
    if not state.delivery_location:
        missing.append("delivery_location")
    if not state.terms.response_deadline:
        missing.append("response_deadline")
    if not state.vendor_selection_rule and not state.selected_supplier_ids:
        missing.append("vendor_selection")
    return missing


def _ask_for_packaging_items(state: PackagingIntakeState) -> IntakeResponse:
    state.status = "collecting"
    state.current_question = (
        "What packaging item do you need for warehouse dispatch? "
        "For example: corrugated cartons, packing tape, labels, stretch film, pouches, or bubble wrap."
    )
    state.current_default = None
    return IntakeResponse(
        message=state.current_question,
        conversation_id=state.conversation_id,
        status=state.status,
        state=state,
    )


def _ask_next_missing_field(state: PackagingIntakeState) -> IntakeResponse:
    field = state.missing_fields[0]
    if field == "quantity":
        state.current_question = "What quantity is required for each packaging line item?"
        state.current_default = None
    elif field == "delivery_location":
        state.current_question = (
            "Delivery location is missing. Default: main warehouse receiving dock. "
            "Press Enter to accept, or type a different warehouse/location."
        )
        state.current_default = {"field": "delivery_location", "value": "Main warehouse receiving dock"}
    elif field == "response_deadline":
        default_deadline = date.today() + timedelta(days=7)
        state.current_question = (
            f"Quote response deadline is missing. Default: {default_deadline.isoformat()} "
            "(7 days from today). Press Enter to accept, or type another date."
        )
        state.current_default = {"field": "response_deadline", "value": default_deadline.isoformat()}
    elif field == "vendor_selection":
        state.current_question = (
            "Vendor selection is missing. Default: invite active packaging suppliers matching the item category "
            "and delivery location. Press Enter to accept, or describe preferred suppliers."
        )
        state.current_default = {
            "field": "vendor_selection_rule",
            "value": "Invite active packaging suppliers matching item category and delivery location.",
        }
    else:
        state.current_question = "Please provide the missing packaging line item details."
        state.current_default = None

    return IntakeResponse(
        message=state.current_question,
        conversation_id=state.conversation_id,
        status=state.status,
        state=state,
    )


def _apply_default(state: PackagingIntakeState) -> None:
    default = state.current_default or {}
    field = default.get("field")
    value = default.get("value")
    if field == "delivery_location":
        state.delivery_location = value
    elif field == "response_deadline":
        state.terms.response_deadline = date.fromisoformat(value)
    elif field == "vendor_selection_rule":
        state.vendor_selection_rule = value
    state.current_default = None
    state.current_question = None


def _is_affirmative(message: str) -> bool:
    return message.strip().lower() in {"yes", "y", "ok", "okay", "confirm", "confirmed", "proceed"}


def _is_final_approval(message: str) -> bool:
    return message.strip().lower() in {"yes", "y", "approve", "approved", "submit", "proceed", "send"}


def _format_line_items_and_terms(state: PackagingIntakeState) -> str:
    line_bits = []
    for item in state.line_items:
        line_bits.append(
            "- "
            f"{item.item_number}. {item.description}"
            f" | category: {item.packaging_category or 'packaging'}"
            f" | qty: {item.quantity or 'TBD'} {item.unit or ''}".rstrip()
            f" | specs: {item.specifications or 'standard warehouse packaging specs'}"
        )

    deadline = state.terms.response_deadline.isoformat() if state.terms.response_deadline else "TBD"
    terms = (
        f"- Terms type: {state.terms.terms_type}\n"
        f"- Payment: {state.terms.payment_terms}\n"
        f"- Delivery: {state.terms.delivery_terms}\n"
        f"- Quote validity: {state.terms.quote_validity_days} days\n"
        f"- Response deadline: {deadline}\n"
        f"- Inspection: {state.terms.inspection_terms}"
    )
    if state.terms.custom_terms:
        terms += "\n- Custom terms: " + "; ".join(state.terms.custom_terms)

    return "Line items:\n" + "\n".join(line_bits) + "\n\nTerms:\n" + terms
