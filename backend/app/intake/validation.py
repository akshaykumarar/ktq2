"""Packaging-domain validation, defaults, and readiness checking."""

from __future__ import annotations

from typing import Any, Union

from backend.app.intake.models import PackagingLineItem, PackagingRequirement

PACKAGING_KEYWORDS = {
    "box",
    "boxes",
    "carton",
    "cartons",
    "corrugated",
    "shipper",
    "mailer",
    "pouch",
    "pouches",
    "bag",
    "bags",
    "tape",
    "label",
    "labels",
    "stretch film",
    "shrink film",
    "bubble wrap",
    "foam",
    "void fill",
    "filler",
    "pallet",
    "pallets",
    "strapping",
    "edge protector",
    "packing",
    "packaging",
}

NON_PACKAGING_HINTS = {
    "laptop",
    "laptops",
    "monitor",
    "monitors",
    "phone",
    "phones",
    "server",
    "servers",
    "chair",
    "chairs",
    "desk",
    "desks",
    "printer",
    "printers",
    "software",
    "vehicle",
    "vehicles",
    "food",
    "raw material",
}


def _text_has_any(text: str, vocabulary: set[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in vocabulary)


def find_non_packaging_terms(text: str) -> list[str]:
    """Return non-packaging procurement terms detected in user text."""
    lowered = text.lower()
    return sorted(term for term in NON_PACKAGING_HINTS if term in lowered)


def is_packaging_related(
    text: str = "",
    line_items: list[Union[PackagingLineItem, PackagingRequirement]] | None = None,
) -> bool:
    """Check whether the intake content belongs to warehouse packaging procurement."""
    if _text_has_any(text, PACKAGING_KEYWORDS):
        return True

    for item in line_items or []:
        if isinstance(item, PackagingRequirement):
            haystack = f"{item.item_description} {item.category} {item.material or ''} {item.specification or ''}"
        else:
            haystack = " ".join(
                part
                for part in [
                    item.description,
                    item.packaging_category or "",
                    item.material or "",
                    item.specifications or "",
                    item.usage_context or "",
                ]
                if part
            )
        if _text_has_any(haystack, PACKAGING_KEYWORDS):
            return True

    return False


def identify_missing_fields(req: PackagingRequirement) -> list[str]:
    """Identify missing critical procurement fields without hallucinating them."""
    missing = []
    if req.quantity is None or req.quantity <= 0:
        missing.append("quantity")
    if not req.item_description:
        missing.append("item_description")
    if not req.dimensions or not any(req.dimensions.values()):
        missing.append("dimensions")
    if not req.material and not req.ply:
        missing.append("material_or_ply")
    if not req.delivery_date:
        missing.append("delivery_date")
    return missing


def apply_traceable_defaults(req: PackagingRequirement) -> dict[str, Any]:
    """Apply traceable defaults for non-critical fields according to product rules.

    Does NOT invent critical quantities, dimensions, prices, or materials.
    Returns a dictionary of applied defaults for traceability.
    """
    applied = {}
    if not req.unit:
        req.unit = "pcs"
        applied["unit"] = "pcs (default warehouse counting unit)"
    if not req.currency:
        req.currency = "INR"
        applied["currency"] = "INR (default currency)"
    if not req.category:
        req.category = "Corrugated packaging"
        applied["category"] = "Corrugated packaging (standard category)"
    return applied


def check_rfi_readiness(requirements: list[PackagingRequirement]) -> tuple[bool, list[str]]:
    """Determine whether a set of requirements is ready to form an active RFI.

    Returns (is_ready, list_of_blocking_reasons).
    """
    if not requirements:
        return False, ["No packaging line items specified."]

    reasons = []
    for req in requirements:
        missing = identify_missing_fields(req)
        # Quantity and description are hard blockers for readiness
        if "quantity" in missing:
            reasons.append(f"Item #{req.item_number} '{req.item_description}': missing quantity")
        if "item_description" in missing:
            reasons.append(f"Item #{req.item_number}: missing item description")

    return len(reasons) == 0, reasons
