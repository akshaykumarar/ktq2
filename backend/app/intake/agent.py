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


def _parse_single_packaging_clause(
    clause: str,
    item_num: int,
    default_delivery_date: str | None = None,
    default_currency: str = "INR",
    default_target_price: float | None = None,
) -> PackagingRequirement:
    """Parse an individual packaging requirement clause into a typed PackagingRequirement."""
    clean_clause = clause.strip(" ,.")

    # 1. Delivery date specific to clause or default
    delivery_date = default_delivery_date
    date_match = re.search(
        r"(?:delivery\s+(?:required\s+)?by|by|before|required\s+date\s*:?)\s+([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|[0-9]{4}-[0-9]{2}-[0-9]{2}|[A-Za-z]+\s+[0-9]{1,2}(?:,\s*[0-9]{4})?)",
        clean_clause,
        re.I,
    )
    if date_match:
        delivery_date = date_match.group(1).strip()

    text_for_qty = clean_clause
    if date_match:
        text_for_qty = text_for_qty[:date_match.start()] + text_for_qty[date_match.end():]
    text_for_qty = re.sub(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
        " ",
        text_for_qty,
        flags=re.I,
    )

    # 2. Quantity & unit
    quantity: float | None = None
    unit = "pcs"
    qty_match = re.search(
        r"(?:need|require|order|buy|procure|want)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(boxes|cartons|pcs|pieces|rolls|bags|pouches|sheets|pallets|units|mailers)?(?:\s+of)?",
        text_for_qty,
        re.I,
    )
    if qty_match:
        val_str = qty_match.group(1).replace(",", "")
        if not re.search(rf"\b{val_str}\s*(?:x|×|\*|-?\s*ply)", text_for_qty, re.I):
            try:
                quantity = float(val_str)
                if qty_match.group(2):
                    unit = qty_match.group(2).lower()
            except ValueError:
                pass

    # 3. Dimensions
    dimensions: dict[str, Any] = {}
    dim_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|inch|inches|m)?",
        clean_clause,
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

    # 4. Ply
    ply: str | None = None
    ply_match = re.search(r"(\d+)\s*[-]?\s*ply", clean_clause, re.I)
    if ply_match:
        ply = f"{ply_match.group(1)}-ply"

    # 5. Material
    material: str | None = None
    for mat_pattern in [
        r"(virgin\s+kraft|kraft\s*finish|kraft\s*paper|kraft)",
        r"(polyethylene|bubble\s*wrap|bopp|pvc|duplex|shrink\s*film|stretch\s*film|lldpe)",
        r"(brown\s*tape|packing\s*tape|adhesive\s*tape)",
        r"(corrugated)",
    ]:
        mat_match = re.search(mat_pattern, clean_clause, re.I)
        if mat_match:
            material = mat_match.group(1).strip()
            break

    # 6. Target price
    target_price = default_target_price
    currency = default_currency
    price_match = re.search(
        r"(?:target\s+price|budget|price|cost|rate)\s*(?:of|:)?\s*(?:INR|USD|Rs\.?|₹|\$)?\s*(\d+(?:,\d+)*(?:\.\d+)?)",
        clean_clause,
        re.I,
    )
    if price_match:
        try:
            target_price = float(price_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # 7. Category
    category = "Corrugated packaging"
    lowered = clean_clause.lower()
    if any(k in lowered for k in ["tape", "adhesive", "bopp"]):
        category = "Adhesive tape"
    elif any(k in lowered for k in ["label", "barcode", "sticker"]):
        category = "Labels & stickers"
    elif any(k in lowered for k in ["bubble", "foam", "void fill", "air pillow"]):
        category = "Protective packaging"
    elif any(k in lowered for k in ["film", "stretch", "shrink"]):
        category = "Wrapping film"
    elif any(k in lowered for k in ["pouch", "bag", "mailer"]):
        category = "Flexible pouches"
    elif any(k in lowered for k in ["pallet"]):
        category = "Pallets"

    # 8. Description
    desc_match = re.search(
        r"(?:need|require|procure|buy|want)?\s*(?:\d+[\s,]*)?(?:(?:boxes|cartons|pcs|pieces|rolls|bags|pouches|sheets|pallets|units|mailers)\s+of\s+)?([a-zA-Z\s]+(?:boxes|cartons|tape|film|rolls|pouches|pallets|mailers|wrap))",
        clean_clause,
        re.I,
    )
    if desc_match:
        item_description = desc_match.group(1).strip()
    else:
        item_description = re.sub(r"^(?:need|require|order|buy|procure|want|\d+|\s|,)+", "", clean_clause, flags=re.I).strip()
        if not item_description:
            item_description = clean_clause[:60].strip()

    # Clean description from leading unit prefix
    item_description = re.sub(r"^(?:boxes|cartons|pcs|pieces|rolls|bags|pouches|sheets|pallets|units|mailers)\s+of\s+", "", item_description, flags=re.I).strip()

    # Strip dimension pattern from description if accidentally matched
    if dim_match:
        item_description = re.sub(r"\s*,?\s*\d+\s*[xX*×]\s*\d+\s*[xX*×]\s*\d+\s*(?:mm|cm|inch|m)?", "", item_description, flags=re.I).strip(" ,.")

    if not material:
        material = "Corrugated" if category == "Corrugated packaging" else (item_description.title() if "tape" in item_description.lower() else category)

    return PackagingRequirement(
        item_number=item_num,
        category=category,
        item_description=item_description,
        quantity=quantity,
        unit=unit,
        dimensions=dimensions,
        material=material,
        ply=ply,
        specification=clean_clause,
        delivery_date=delivery_date,
        target_price=target_price,
        currency=currency,
    )


def deterministic_extract_packaging(text: str) -> IntakeExtractionResult:
    """Deterministic, rule-based extraction for packaging text supporting single and multi-line items.

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

    # 1. Global delivery date
    delivery_date: str | None = None
    date_match = re.search(
        r"(?:delivery\s+(?:required\s+)?by|by|before|required\s+date\s*:?)\s+([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|[0-9]{4}-[0-9]{2}-[0-9]{2}|[A-Za-z]+\s+[0-9]{1,2}(?:,\s*[0-9]{4})?)",
        clean_text,
        re.I,
    )
    if date_match:
        delivery_date = date_match.group(1).strip()

    # Global target price and currency
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

    # 2. Extract item text by trimming destination / trailing delivery phrases
    items_text = clean_text
    deliv_kw = re.search(r"\b(?:deliver(?:ed)?\s+to|warehouse\s+at|destination\s*:?)\b", items_text, re.I)
    if deliv_kw:
        items_text = items_text[:deliv_kw.start()].strip(" ,.")
    elif date_match:
        date_kw = re.search(r"\b(?:by|before)\s+[A-Za-z0-9]", items_text, re.I)
        if date_kw:
            items_text = items_text[:date_kw.start()].strip(" ,.")

    # 3. Split on multi-item delimiters: ';', newlines, ', and ', ' and ', or commas preceding new quantities
    clauses = re.split(
        r"(?:;\s*|\n+|\s*,\s*and\s+|\s+and\s+|\s*,\s*(?=\d+\s*(?:boxes|cartons|rolls|pcs|pieces|pouches|sheets|pallets|units|bags|mailers|[a-zA-Z]+\s+tape|[a-zA-Z]+\s+film|[a-zA-Z]+\s+boxes)))",
        items_text,
        flags=re.I,
    )
    clauses = [c.strip(" ,.") for c in clauses if c.strip(" ,.")]
    if not clauses:
        clauses = [clean_text]

    requirements: list[PackagingRequirement] = []
    for idx, clause in enumerate(clauses, start=1):
        req = _parse_single_packaging_clause(
            clause=clause,
            item_num=idx,
            default_delivery_date=delivery_date,
            default_currency=currency,
            default_target_price=target_price,
        )
        missing = identify_missing_fields(req)
        req.missing_fields = missing
        apply_traceable_defaults(req)
        requirements.append(req)

    # Check RFI readiness
    is_ready, _ = check_rfi_readiness(requirements)

    # Categories
    categories = list(dict.fromkeys(r.category for r in requirements))
    if len(categories) == 1:
        detected_category = categories[0]
    elif len(categories) == 2:
        detected_category = f"{categories[0]} & {categories[1]}"
    else:
        detected_category = "Packaging & Dispatch Supplies"

    # Title
    if len(requirements) == 1:
        qty_val = f"{int(requirements[0].quantity)} " if requirements[0].quantity else ""
        title = f"{qty_val}{requirements[0].item_description}".strip().capitalize()
    else:
        qty_val = f"{int(requirements[0].quantity)} " if requirements[0].quantity else ""
        title = f"RFI for {qty_val}{requirements[0].item_description} and {len(requirements) - 1} other item{'s' if len(requirements) > 2 else ''}".strip()

    all_missing = list(dict.fromkeys(m for r in requirements for m in r.missing_fields))
    all_defaults = {}
    for r in requirements:
        all_defaults.update(apply_traceable_defaults(r))

    return IntakeExtractionResult(
        is_packaging=True,
        title=title,
        requirements=requirements,
        detected_category=detected_category,
        missing_critical_fields=all_missing,
        defaults_applied=all_defaults,
        ready_for_rfi=is_ready,
        message=f"Extracted {len(requirements)} packaging requirement line item(s)",
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
