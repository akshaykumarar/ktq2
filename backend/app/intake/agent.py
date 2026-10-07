"""PydanticAI agent and deterministic extraction for packaging requirements."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

from backend.app.intake.models import (
    IntakeExtractionResult,
    PackagingRequirement,
)
from backend.app.intake.validation import (
    apply_traceable_defaults,
    check_rfi_readiness,
    find_non_packaging_terms,
    identify_missing_fields,
    is_packaging_related,
)

logger = logging.getLogger(__name__)

DEFAULT_PARSER_INSTRUCTIONS = """
You are an expert Packaging Requirement Extraction Agent for warehouse dispatch and logistics.
Your task is to parse unstructured text from procurement officers and warehouse managers,
and extract strictly typed PackagingRequirement objects.

Rules:
1. Extract line items with item_description, quantity, unit, dimensions, material, ply, gsm, bf,
   specification, delivery_date, and target_price.
2. Structure dimensions into a dictionary: {"length": ..., "width": ..., "height": ..., "unit": ...}.
3. Normalize category to packaging categories (e.g. "Corrugated packaging", "Protective packaging", "Adhesive tape", "Labels").
4. Identify missing critical fields (e.g. quantity, dimensions, delivery date).
5. Identify ambiguous fields if multiple interpretations exist.
6. NEVER invent, hallucinate, or assume critical values (quantity, dimensions, material, target price).
   If not specified in the input text, leave them as None and add to missing_fields.
7. Return an IntakeExtractionResult instance.
"""


def create_rfi_parser_agent(
    model: Model,
    instructions: str | None = None,
) -> Agent[None, IntakeExtractionResult]:
    """Instantiate a PydanticAI Agent configured to produce IntakeExtractionResult."""
    return Agent(
        model=model,
        output_type=IntakeExtractionResult,
        system_prompt=instructions or DEFAULT_PARSER_INSTRUCTIONS,
    )


def deterministic_extract_packaging(text: str) -> IntakeExtractionResult:
    """Deterministic, rule-based extraction for packaging text.

    Acts as a reliable, offline-compatible engine and fallback for the AI agent.
    Never hallucinates missing fields; marks them explicitly.
    """
    clean_text = text.strip()
    non_packaging = find_non_packaging_terms(clean_text)
    is_packaging = is_packaging_related(clean_text)

    if non_packaging and not is_packaging:
        return IntakeExtractionResult(
            is_packaging=False,
            title="Non-packaging Request",
            requirements=[],
            detected_category="Non-packaging",
            missing_critical_fields=["packaging_category"],
            ready_for_rfi=False,
            message=(
                f"Unsupported items detected: {', '.join(non_packaging)}. "
                "This workflow is strictly for warehouse packaging procurement."
            ),
        )

    # 1. Parse delivery date first so dates are not mistaken for quantities
    delivery_date: str | None = None
    date_match = re.search(
        r"(?:delivery\s+(?:required\s+)?by|by|before|required\s+date\s*:?)\s+([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|[0-9]{4}-[0-9]{2}-[0-9]{2}|[A-Za-z]+\s+[0-9]{1,2}(?:,\s*[0-9]{4})?)",
        clean_text,
        re.I,
    )
    if date_match:
        delivery_date = date_match.group(1).strip()

    # Mask date portion when looking for quantity
    text_for_qty = clean_text
    if date_match:
        text_for_qty = text_for_qty[:date_match.start()] + text_for_qty[date_match.end():]

    # Also mask any standalone date-like patterns (e.g. '30 Nov', '30 November')
    text_for_qty = re.sub(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
        " ",
        text_for_qty,
        flags=re.I,
    )

    # 2. Parse quantity and unit
    quantity: float | None = None
    unit = "pcs"
    qty_match = re.search(
        r"(?:need|require|order|buy|procure|want)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(boxes|cartons|pcs|pieces|rolls|bags|pouches|sheets|pallets|units)?",
        text_for_qty,
        re.I,
    )
    if qty_match:
        # Verify it's not preceded by dimensions like 600x400x300
        val_str = qty_match.group(1).replace(",", "")
        # Check that this number isn't part of dimensions or ply
        if not re.search(rf"\b{val_str}\s*(?:x|×|\*|-?\s*ply)", text_for_qty, re.I):
            try:
                quantity = float(val_str)
                if qty_match.group(2):
                    unit = qty_match.group(2).lower()
            except ValueError:
                pass

    # 3. Parse dimensions (e.g. 600x400x300 mm, 600 x 400 x 300mm)
    dimensions: dict[str, Any] = {}
    dim_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|inch|inches|m)?",
        clean_text,
        re.I,
    )
    if dim_match:
        dim_unit = dim_match.group(4) or "mm"
        dimensions = {
            "length": float(dim_match.group(1)),
            "width": float(dim_match.group(2)),
            "height": float(dim_match.group(3)),
            "unit": dim_unit.lower(),
        }

    # 4. Parse ply (e.g. 3-ply, 5-ply, 7 ply)
    ply: str | None = None
    ply_match = re.search(r"(\d+)\s*[-]?\s*ply", clean_text, re.I)
    if ply_match:
        ply = f"{ply_match.group(1)}-ply"

    # 5. Parse material (prioritize specific substrates/finishes over generic 'corrugated')
    material: str | None = None
    for mat_pattern in [
        r"(virgin\s+kraft|kraft\s*finish|kraft\s*paper|kraft)",
        r"(polyethylene|bubble\s*wrap|bopb|pvc|duplex|shrink\s*film|stretch\s*film|lldpe)",
        r"(corrugated)",
    ]:
        mat_match = re.search(mat_pattern, clean_text, re.I)
        if mat_match:
            material = mat_match.group(1).strip()
            break

    # 6. Parse target price (e.g. target price 45 INR, budget 50000, 45/box)
    target_price: float | None = None
    currency = "INR"
    price_match = re.search(
        r"(?:target\s+price|budget|price|cost|rate)\s*(?:of|:)?\s*(?:INR|USD|Rs\.?|₹|\$)?\s*(\d+(?:,\d+)*(?:\.\d+)?)",
        clean_text,
        re.I,
    )
    if price_match:
        try:
            target_price = float(price_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # 7. Category and Item Description
    category = "Corrugated packaging"
    lowered = clean_text.lower()
    if any(k in lowered for k in ["tape", "adhesive"]):
        category = "Adhesive tape"
    elif any(k in lowered for k in ["label", "barcode"]):
        category = "Labels & stickers"
    elif any(k in lowered for k in ["bubble", "foam", "void fill"]):
        category = "Protective packaging"
    elif any(k in lowered for k in ["film", "stretch", "shrink"]):
        category = "Wrapping film"
    elif any(k in lowered for k in ["pouch", "bag"]):
        category = "Flexible pouches"
    elif any(k in lowered for k in ["pallet"]):
        category = "Pallets"

    # Build description
    desc_match = re.search(
        r"(?:need|require|procure|buy|want)?\s*(?:\d+[\s,]*)?([a-zA-Z\s]+(?:boxes|cartons|tape|film|rolls|pouches|pallets|mailers|wrap))",
        clean_text,
        re.I,
    )
    item_description = desc_match.group(1).strip() if desc_match else clean_text[:60].strip()

    requirement = PackagingRequirement(
        item_number=1,
        category=category,
        item_description=item_description,
        quantity=quantity,
        unit=unit,
        dimensions=dimensions,
        material=material,
        ply=ply,
        specification=clean_text,
        delivery_date=delivery_date,
        target_price=target_price,
        currency=currency,
    )

    # Missing & ambiguous identification
    missing = identify_missing_fields(requirement)
    requirement.missing_fields = missing

    # Apply non-hallucinatory defaults
    applied_defaults = apply_traceable_defaults(requirement)

    # Check RFI readiness
    is_ready, reasons = check_rfi_readiness([requirement])

    # Title
    title = f"{quantity or ''} {item_description}".strip().capitalize()
    if not title:
        title = "Packaging RFI Requirement"

    msg = f"Extracted requirement for '{item_description}'"
    if missing:
        msg += f" (missing: {', '.join(missing)})"

    return IntakeExtractionResult(
        is_packaging=True,
        title=title,
        requirements=[requirement],
        detected_category=category,
        missing_critical_fields=missing,
        defaults_applied=applied_defaults,
        ready_for_rfi=is_ready,
        message=msg,
    )


async def extract_requirements(
    text: str,
    agent: Agent[None, IntakeExtractionResult] | None = None,
) -> IntakeExtractionResult:
    """Extract packaging requirements from text using PydanticAI agent with deterministic fallback."""
    if agent:
        try:
            result = await agent.run(text)
            output = result.output
            if isinstance(output, IntakeExtractionResult) and output.requirements:
                # Apply validation & traceable defaults to AI output
                for req in output.requirements:
                    req.missing_fields = identify_missing_fields(req)
                    defaults = apply_traceable_defaults(req)
                    output.defaults_applied.update(defaults)
                is_ready, _ = check_rfi_readiness(output.requirements)
                output.ready_for_rfi = is_ready
                return output
        except Exception as exc:
            logger.info("AI extraction agent ran into issue or mock mode (%s), using deterministic parser.", exc)

    return deterministic_extract_packaging(text)
