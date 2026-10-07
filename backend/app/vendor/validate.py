"""Pure Python validation rules, flags, and state computation."""

from __future__ import annotations

import logging
from typing import Any

from backend.app.vendor.models import FlagSeverity, ItemKind, ItemState

logger = logging.getLogger(__name__)


class ValidationEngine:
    """Evaluates business rules, generates traceable flags, and computes item/response state."""

    def evaluate_item(
        self,
        kind: ItemKind,
        raw_price: float | None,
        raw_unit: str | None,
        raw_qty: float | None,
        raw_currency: str | None,
        tax_basis: str | None,
        discount: dict[str, Any],
        unresolved_reference: str | None,
        match_confidence: float,
        extraction_confidence: float,
        target_rfx_unit: str | None,
        target_rfx_qty: float | None,
        target_rfx_price: float | None,
        raw_description: str = "",
        is_rfx_ambiguous: bool = False,
    ) -> tuple[ItemState, list[dict[str, Any]], list[str], str | None, str | None]:
        """Validate an item and produce state, flags, missing_fields, why_unsure, and how_to_resolve."""
        flags: list[dict[str, Any]] = []
        missing_fields: list[str] = []
        why_unsure_parts: list[str] = []
        how_to_resolve_parts: list[str] = []

        # 1. Check NOT_QUOTED
        if kind == ItemKind.NOT_QUOTED:
            missing_fields.append("price")
            return (
                ItemState.MISSING,
                [{"code": "not_quoted", "severity": FlagSeverity.INFO, "message": "Item was not quoted by the vendor."}],
                missing_fields,
                "Vendor did not provide a quotation for this RFx item.",
                "Request quotation for this specific item from vendor.",
            )

        # 2. Check EXTRA / ALTERNATE items
        if kind == ItemKind.EXTRA:
            flags.append({
                "code": "extra_item",
                "severity": FlagSeverity.INFO,
                "message": "Vendor offered an additional item not specified in the RFx.",
            })
            why_unsure_parts.append("Item is not part of the active RFx requirement.")
            how_to_resolve_parts.append("Confirm if the additional item is needed by the procurement team.")

        elif kind == ItemKind.ALTERNATE:
            flags.append({
                "code": "alternate_item",
                "severity": FlagSeverity.WARNING,
                "message": "Vendor offered an alternate specification (e.g. ply / material deviation).",
            })
            why_unsure_parts.append("Specification deviates from the RFx baseline specification.")
            how_to_resolve_parts.append("Review technical compliance of the alternate specification.")

        # 3. Check Price & Unresolved references
        if unresolved_reference:
            flags.append({
                "code": "unresolved_reference",
                "severity": FlagSeverity.CRITICAL,
                "message": f"Reference pricing detected ('{unresolved_reference}') without explicit numerical rate.",
            })
            missing_fields.append("price")
            why_unsure_parts.append(f"Quotation states '{unresolved_reference}' rather than an exact price.")
            how_to_resolve_parts.append("Clarify with vendor or lookup prior year contract price and enter buyer correction.")

        elif raw_price is None:
            flags.append({
                "code": "price_missing",
                "severity": FlagSeverity.CRITICAL,
                "message": "No price was provided for this quoted line.",
            })
            missing_fields.append("price")
            why_unsure_parts.append("Price field is empty.")
            how_to_resolve_parts.append("Obtain price quote from vendor.")

        # 4. Check Unit of Measure & Pricing Traps
        if not raw_unit or raw_unit.strip() == "":
            flags.append({
                "code": "unit_missing",
                "severity": FlagSeverity.WARNING,
                "message": "Unit of measure is missing from quotation.",
            })
            missing_fields.append("unit")
            why_unsure_parts.append("Unit of measurement is not stated.")
            how_to_resolve_parts.append("Confirm unit of measure with vendor (e.g. pcs, box, roll).")
        elif target_rfx_unit and raw_unit.lower().strip() != target_rfx_unit.lower().strip():
            flags.append({
                "code": "unit_differs_from_rfx",
                "severity": FlagSeverity.INFO,
                "message": f"Quoted unit '{raw_unit}' differs from RFx baseline unit '{target_rfx_unit}'.",
            })

        # Check per-100 / per-1000 pricing trap
        # If unit is pcs but description has '100' or price is > 10x target price
        if raw_price and target_rfx_price and target_rfx_price > 0:
            if raw_price > (target_rfx_price * 15) and "100" in raw_description:
                flags.append({
                    "code": "possible_per_100_vs_per_unit",
                    "severity": FlagSeverity.CRITICAL,
                    "message": "Quoted price appears to be for 100/1000 units rather than single piece.",
                })
                why_unsure_parts.append(f"Price ({raw_price}) is >15x benchmark ({target_rfx_price}) and description contains '100'.")
                how_to_resolve_parts.append("Verify if price is per 100 pieces and apply unit conversion factor 0.01.")

        # 5. Check Currency & FX Rate
        if raw_currency and raw_currency.upper() != "INR":
            flags.append({
                "code": "currency_not_inr",
                "severity": FlagSeverity.INFO,
                "message": f"Quoted in foreign currency ({raw_currency.upper()}). Converted to INR.",
            })
            flags.append({
                "code": "fx_rate_assumed",
                "severity": FlagSeverity.INFO,
                "message": "Applied prevailing exchange rate conversion to INR.",
            })

        # 6. Check Quantity Mismatch
        if raw_qty is not None and target_rfx_qty is not None and raw_qty != target_rfx_qty:
            flags.append({
                "code": "qty_mismatch",
                "severity": FlagSeverity.WARNING,
                "message": f"Quoted quantity ({raw_qty}) differs from requested RFx quantity ({target_rfx_qty}).",
            })
            why_unsure_parts.append(f"Quoted quantity {raw_qty} does not match requested quantity {target_rfx_qty}.")
            how_to_resolve_parts.append("Check if vendor quoted for minimum batch size or partial quantity.")

        # 7. Tax Basis & Discounts
        if tax_basis == "unknown":
            flags.append({
                "code": "tax_basis_unknown",
                "severity": FlagSeverity.INFO,
                "message": "Tax basis (GST inclusive/exclusive) is not explicitly clarified for this item.",
            })

        if discount and bool(discount):
            flags.append({
                "code": "conditional_discount_present",
                "severity": FlagSeverity.WARNING,
                "message": "Conditional discounts or volume slabs mentioned in notes.",
            })
            why_unsure_parts.append("Conditional discount present in quotation terms.")
            how_to_resolve_parts.append("Review discount threshold conditions during PO creation.")

        # 8. Confidence Check
        if match_confidence < 0.70:
            flags.append({
                "code": "low_match_confidence",
                "severity": FlagSeverity.WARNING,
                "message": f"RFx item matching confidence is low ({round(match_confidence, 2)}).",
            })
            why_unsure_parts.append(f"Match confidence is {round(match_confidence*100, 1)}%.")
            how_to_resolve_parts.append("Verify if item was correctly matched to the intended RFx line.")

        if is_rfx_ambiguous:
            flags.append({
                "code": "rfx_ambiguous",
                "severity": FlagSeverity.WARNING,
                "message": "The RFx assignment for this response is ambiguous with close alternative RFxs.",
            })
            why_unsure_parts.append("Response matched multiple open RFxs.")
            how_to_resolve_parts.append("Confirm RFx assignment in response header.")

        # Compute State
        has_critical = any(f["severity"] == FlagSeverity.CRITICAL for f in flags)
        has_warning = any(f["severity"] == FlagSeverity.WARNING for f in flags)

        if raw_price is None:
            state = ItemState.MISSING
        elif has_critical or has_warning or kind in (ItemKind.EXTRA, ItemKind.ALTERNATE) or is_rfx_ambiguous:
            state = ItemState.REVIEW
        else:
            state = ItemState.CONFIDENT

        why_unsure = "; ".join(why_unsure_parts) if why_unsure_parts else None
        how_to_resolve = "; ".join(how_to_resolve_parts) if how_to_resolve_parts else None

        return state, flags, missing_fields, why_unsure, how_to_resolve

    def compute_summary(
        self,
        total_rfx_items: int,
        items: list[dict[str, Any]],
        all_flags: list[dict[str, Any]],
        is_rfx_ambiguous: bool = False,
    ) -> dict[str, Any]:
        """Compute coverage, counts, and readiness status for the overall response."""
        matched_count = sum(1 for it in items if it.get("kind") == ItemKind.MATCHED)
        alternate_count = sum(1 for it in items if it.get("kind") == ItemKind.ALTERNATE)
        extra_count = sum(1 for it in items if it.get("kind") == ItemKind.EXTRA)
        not_quoted_count = sum(1 for it in items if it.get("kind") == ItemKind.NOT_QUOTED)

        confident_count = sum(1 for it in items if it.get("state") == ItemState.CONFIDENT)
        review_count = sum(1 for it in items if it.get("state") == ItemState.REVIEW)
        missing_count = sum(1 for it in items if it.get("state") == ItemState.MISSING)

        items_quoted = matched_count + alternate_count
        coverage_pct = round((items_quoted / max(total_rfx_items, 1)) * 100, 1)

        critical_flags = sum(1 for f in all_flags if f.get("severity") == FlagSeverity.CRITICAL)
        warning_flags = sum(1 for f in all_flags if f.get("severity") == FlagSeverity.WARNING)

        if critical_flags > 0 or is_rfx_ambiguous or review_count > 0:
            readiness = "needs_review"
        else:
            readiness = "ready"

        return {
            "total_rfx_items": total_rfx_items,
            "items_quoted": items_quoted,
            "matched_items": matched_count,
            "alternate_items": alternate_count,
            "extra_items": extra_count,
            "not_quoted_items": not_quoted_count,
            "coverage_pct": coverage_pct,
            "confident_count": confident_count,
            "review_count": review_count,
            "missing_count": missing_count,
            "critical_flags_count": critical_flags,
            "warning_flags_count": warning_flags,
            "readiness": readiness,
        }
