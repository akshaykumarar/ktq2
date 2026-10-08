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
    extract_commercial_terms,
    find_non_packaging_terms,
    identify_missing_fields,
    is_commercial_term_or_header,
    is_conversational_or_boilerplate,
    is_packaging_related,
    is_separator_line,
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
7. Strictly EXCLUDE conversational greetings/closings (e.g. "Hi team", "Regards"), general paragraphs, commercial terms, notes, and terms and conditions (e.g. MOQ requirements, freight/shipping terms, warranty/SLA, volume discounts, payment terms, currency specifications), section headers, and horizontal divider/separator lines from line items. Notes, greetings, and commercial terms are NOT packaging line items.
8. Return an IntakeExtractionResult instance.
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
    # Strip leading list markers (e.g. "1. ", "2) ", "[3] ", "- ", "* ")
    clean_clause = re.sub(r"^\s*(?:\d+[\.\)]|\[\d+\]|[-*•–])\s+", "", clean_clause).strip(" ,.")

    # 1. Delivery date specific to clause or default
    delivery_date = default_delivery_date
    date_match = re.search(
        r"(?:delivery\s+(?:required\s+)?by|by|before|required\s+date\s*:?)\s+([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|[0-9]{4}-[0-9]{2}-[0-9]{2}|[A-Za-z]+\s+[0-9]{1,2}(?:,\s*[0-9]{4})?)",
        clean_clause,
        re.I,
    )
    if date_match:
        delivery_date = date_match.group(1).strip()

    text_working = clean_clause
    if date_match:
        text_working = text_working[:date_match.start()] + text_working[date_match.end():]
    text_working = re.sub(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
        " ",
        text_working,
        flags=re.I,
    )

    # 2. Dimensions & Special Shapes
    dimensions: dict[str, Any] = {}
    dim_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|inch|inches|in|m)?",
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

    # Cube box dimensions (e.g. 50inch cube boxes 300, 30cm cube carton)
    cube_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(inch|inches|in|\"|mm|cm|m)?\s*cube\b",
        clean_clause,
        re.I,
    )
    if cube_match and not dimensions:
        cube_val = float(cube_match.group(1))
        cube_u = (cube_match.group(2) or "inch").replace('"', 'inch').lower()
        dimensions = {
            "length": cube_val,
            "width": cube_val,
            "height": cube_val,
            "unit": cube_u,
        }

    # Extract film specs like "5m", "50 micron", "23 gauge" from clause
    film_spec_str: str | None = None
    film_spec_match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(m|meter|meters|mtr|micron|microns|gauge)\s+(stretch\s+film[s]?|shrink\s+film[s]?|film[s]?)\b",
        text_working,
        re.I,
    )
    if film_spec_match:
        film_spec_str = f"{film_spec_match.group(1)}{film_spec_match.group(2)}"

    # 3. Quantity & unit extraction
    quantity: float | None = None
    unit = "pcs"
    unit_patterns = r"boxes|cartons|pcs|pieces|rolls|bags|pouches|sheets|pallets|units|mailers|kg|kgs|kilograms?|packs?|mtr|meters?"

    text_for_qty = text_working
    # If cube dimension matched, mask it out of text_for_qty so it doesn't get confused as quantity
    if cube_match:
        text_for_qty = text_for_qty[:cube_match.start()] + " cube " + text_for_qty[cube_match.end():]

    # If film spec matched, mask it out of text_for_qty
    if film_spec_match:
        text_for_qty = text_for_qty[:film_spec_match.start()] + " " + film_spec_match.group(3) + " " + text_for_qty[film_spec_match.end():]

    qty_match = re.search(
        rf"(?:need|require|order|buy|procure|want)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*({unit_patterns})?(?:\s+of)?",
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

    # Trailing quantity check (e.g. "50inch cube boxes 300", "bubble wrap 50kg")
    trailing_qty = re.search(rf"\b(?:({unit_patterns})\s+)?(\d+(?:,\d+)*(?:\.\d+)?)\s*({unit_patterns})?\s*$", text_for_qty, re.I)
    if trailing_qty:
        t_val = trailing_qty.group(2).replace(",", "")
        t_unit = trailing_qty.group(1) or trailing_qty.group(3)
        # If no quantity found, or previous quantity was overridden by spec
        if quantity is None or (cube_match and quantity == float(cube_match.group(1))):
            try:
                quantity = float(t_val)
                if t_unit:
                    unit = t_unit.lower()
            except ValueError:
                pass
        elif t_unit and unit == "pcs":
            unit = t_unit.lower()

    # 4. Ply
    ply: str | None = None
    ply_match = re.search(r"(\d+)\s*[-]?\s*ply", clean_clause, re.I)
    if ply_match:
        ply = f"{ply_match.group(1)}-ply"

    # 5. Category
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

    # 6. Material
    material: str | None = None
    if "bubble wrap" in lowered or "bubblewrap" in lowered:
        material = "Bubble Wrap"
    elif "stretch film" in lowered or "stretchfilm" in lowered:
        material = "Stretch Film"
    elif "transparent tape" in lowered or "clear tape" in lowered:
        material = "Transparent BOPP Tape"
    elif "brown tape" in lowered:
        material = "Brown BOPP Tape"
    else:
        for mat_pattern in [
            r"(virgin\s+kraft|kraft\s*finish|kraft\s*paper|kraft)",
            r"(polyethylene|bopp|pvc|duplex|shrink\s*film|lldpe)",
            r"(brown\s*tape|packing\s*tape|adhesive\s*tape)",
            r"(corrugated)",
        ]:
            mat_match = re.search(mat_pattern, clean_clause, re.I)
            if mat_match:
                material = mat_match.group(1).strip()
                break

    # 7. Target price
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

    # 8. Description
    cleaned_desc = text_for_qty
    # Remove leading desire verbs
    cleaned_desc = re.sub(
        r"^(?:i\s+want\s+(?:a\s+)?|we\s+need\s+|need\s+|require\s+|order\s+|buy\s+|procure\s+)+",
        "",
        cleaned_desc.strip(),
        flags=re.I,
    ).strip()

    # Strip dimension pattern if present
    if dim_match:
        cleaned_desc = re.sub(r"\s*,?\s*\d+\s*[xX*×]\s*\d+\s*[xX*×]\s*\d+\s*(?:mm|cm|inch|inches|in|m)?", "", cleaned_desc, flags=re.I).strip(" ,.")

    # Remove trailing/leading quantity from description
    cleaned_desc = re.sub(rf"\s+\d+(?:,\d+)*(?:\.\d+)?\s*(?:{unit_patterns})?\s*$", "", cleaned_desc, flags=re.I).strip()
    cleaned_desc = re.sub(rf"^\d+(?:,\d+)*(?:\.\d+)?\s*(?:{unit_patterns})?\s*(?:of\s+)?", "", cleaned_desc, flags=re.I).strip()
    cleaned_desc = re.sub(rf"\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:{unit_patterns})\b", "", cleaned_desc, flags=re.I).strip()

    # Reconstruct canonical descriptor
    if film_spec_str and film_spec_str.lower() not in cleaned_desc.lower():
        cleaned_desc = f"{film_spec_str} {cleaned_desc}".strip()
    if cube_match and "cube" not in cleaned_desc.lower():
        cleaned_desc = f"Cube {cleaned_desc}".strip()

    # Canonical singularization for display
    cleaned_desc = re.sub(r"\btapes\b", "tape", cleaned_desc, flags=re.I)
    cleaned_desc = re.sub(r"\bfilms\b", "film", cleaned_desc, flags=re.I)
    item_description = re.sub(r"\s+", " ", cleaned_desc).strip(" ,.")

    if not item_description:
        item_description = clean_clause[:60].strip()

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
    Filters out separator lines, commercial terms headers, and commercial condition clauses.
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

    # 1. Extract commercial terms and global metadata
    comm_terms = extract_commercial_terms(clean_text)

    # Global delivery date
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
    currency = comm_terms.get("currency") or "INR"
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

    # 3. Split on multi-item delimiters: ';', newlines, ', and ', ' and ', or commas preceding new packaging items / quantities
    clauses = re.split(
        r"(?:;\s*|\n+|\s*,\s*and\s+|\s+and\s+|\s*,\s*(?=(?:\d+\s*(?:inch|inches|in|m|mm|cm|\")|\d+\s*(?:boxes|cartons|rolls|pcs|pieces|pouches|sheets|pallets|units|bags|mailers|kg|kgs)?\s*)?[a-zA-Z\s]*(?:boxes?|cartons?|tape|tapes|film|films|bubble\s*wrap|wrap|mailer|pouches?|pallet|bag|bags)))",
        items_text,
        flags=re.I,
    )
    clauses = [c.strip(" ,.") for c in clauses if c.strip(" ,.")]
    if not clauses:
        clauses = [clean_text]

    requirements: list[PackagingRequirement] = []
    item_counter = 1
    for clause in clauses:
        # Strictly ignore separator lines and commercial terms clauses
        if is_separator_line(clause) or is_commercial_term_or_header(clause):
            continue

        req = _parse_single_packaging_clause(
            clause=clause,
            item_num=item_counter,
            default_delivery_date=delivery_date,
            default_currency=currency,
            default_target_price=target_price,
        )

        # Skip if the description is pure punctuation or still matches a terms header
        if is_separator_line(req.item_description) or is_commercial_term_or_header(req.item_description):
            continue

        missing = identify_missing_fields(req)
        req.missing_fields = missing
        apply_traceable_defaults(req)
        requirements.append(req)
        item_counter += 1

    # Check RFI readiness
    is_ready, _ = check_rfi_readiness(requirements)

    # Categories
    categories = list(dict.fromkeys(r.category for r in requirements))
    if len(categories) == 1:
        detected_category = categories[0]
    elif len(categories) == 2:
        detected_category = f"{categories[0]} & {categories[1]}"
    elif categories:
        detected_category = "Packaging & Dispatch Supplies"
    else:
        detected_category = "Corrugated packaging"

    # Title
    if len(requirements) == 1:
        qty_val = f"{int(requirements[0].quantity)} " if requirements[0].quantity else ""
        title = f"{qty_val}{requirements[0].item_description}".strip().capitalize()
    elif len(requirements) > 1:
        qty_val = f"{int(requirements[0].quantity)} " if requirements[0].quantity else ""
        title = f"RFI for {qty_val}{requirements[0].item_description} and {len(requirements) - 1} other item{'s' if len(requirements) > 2 else ''}".strip()
    else:
        title = "Packaging Procurement RFI"

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
                # Filter out any separator or commercial term items from LLM output
                filtered_reqs: list[PackagingRequirement] = []
                for req in output.requirements:
                    if is_separator_line(req.item_description) or is_commercial_term_or_header(req.item_description):
                        continue
                    req.item_number = len(filtered_reqs) + 1
                    req.missing_fields = identify_missing_fields(req)
                    defaults = apply_traceable_defaults(req)
                    output.defaults_applied.update(defaults)
                    filtered_reqs.append(req)

                output.requirements = filtered_reqs
                is_ready, _ = check_rfi_readiness(output.requirements)
                output.ready_for_rfi = is_ready
                return output
        except Exception as exc:
            logger.info("AI extraction agent ran into issue or mock mode (%s), using deterministic parser.", exc)

    return deterministic_extract_packaging(text)
