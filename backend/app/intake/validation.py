"""Packaging-domain validation, defaults, and readiness checking."""

from __future__ import annotations

from typing import Any, Union

from backend.app.intake.models import PackagingLineItem, PackagingRequirement

import re

PACKAGING_KEYWORDS = {
    "box",
    "boxes",
    "carton",
    "cartons",
    "corrugated",
    "shipper",
    "mailer",
    "mailers",
    "pouch",
    "pouches",
    "bag",
    "bags",
    "tape",
    "tapes",
    "label",
    "labels",
    "stretch film",
    "shrink film",
    "bubble wrap",
    "foam",
    "void fill",
    "filler",
    "peanuts",
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

# Regex patterns for pure separator / divider lines
SEPARATOR_PATTERN = re.compile(r"^[-=_*~—\s]{3,}$")

# Regex patterns for commercial terms / notes headers
HEADER_TERMS_PATTERN = re.compile(
    r"^(?:mandatory\s+)?(?:sourcing\s*(?:and|&)\s*commercial\s*instructions?|commercial\s+terms|terms\s*(?:and|&)\s*conditions|commercial\s*conditions|commercial\s*requirements|general\s*terms|terms\s*to\s*include|payment\s*terms|delivery\s*terms|scope\s*of\s*work|special\s*instructions|submission\s*guidelines|notes?|instructions?|general\s*notes?|important\s*notes?|remarks?)(?:\s*:|\s*$)",
    re.I,
)

# Document header metadata patterns (e.g. Enterprise RFI headers)
DOCUMENT_HEADER_PATTERN = re.compile(
    r"^(?:enterprise\s+strategic\s+sourcing|request\s+for\s+information|request\s+for\s+quotation|rfi\s*/\s*rfq\b|rfi\s+reference\s*:|scope\s*:|date\s+of\s+issue\s*:|baseline\s+currency\s*:|order\s+volume\s+scope\s*:)",
    re.I,
)

# Conversational greetings, email boilerplate, and sign-offs
CONVERSATIONAL_BOILERPLATE_PATTERN = re.compile(
    r"^(?:hi|hello|dear)\s+(?:team|all|vendor|supplier|sir|madam|everyone|buyer|procurement)|"
    r"^(?:thanks|thank\s+you|regards|best\s+regards|warm\s+regards|kind\s+regards|sincerely|cheers)|"
    r"^(?:bidding\s+vendors?\s+(?:are\s+)?(?:requested|invited|asked)\s+to|"
    r"vendors?\s+(?:are\s+)?(?:requested|invited|asked)\s+to|"
    r"please\s+provide\s+itemized\s+rates|"
    r"provide\s+itemized\s+rates\s+for|"
    r"the\s+following\s+packaging\s+(?:consumables|materials|items)\s+are\s+required|"
    r"request\s+for\s+quotation\s+for\s+the\s+following)|"
    r"^(?:looking\s+forward|please\s+find|please\s+review|let\s+me\s+know|hope\s+this\s+helps|sent\s+from\s+my|supply\s+chain\s+team)",
    re.I,
)

# Table header pattern (e.g. "# Description Quantity Dimensions Material / Specs" or "BOQ Item # SKU Code Item Description...")
TABLE_HEADER_PATTERN = re.compile(
    r"^(?:#|sl\.?\s*no\.?|sr\.?\s*no\.?|line\s*#?|boq\s*(?:item\s*)?#?|item\s*#?|sku\s*code)\s*[\t\|,\s]+.*(?:description|sku|uom|quantity|qty|category|baseline|lead\s*time)",
    re.I,
)

# Regex patterns for commercial terms / condition clauses / procurement instructions
COMMERCIAL_CLAUSE_PATTERN = re.compile(
    r"^(?:[-*•–]|\d+[\.\)]|\[\d+\])?\s*(?:bidders?\s+must\s+quote|all\s+quotes\s+must|clear\s+indication\s+of\s+moq|moq\b|minimum\s+order\s+quantity|lot\s+size\s+restrictions?|freight|shipping\s+cost|ex-works|delivery\s+included|warranty|sla\b|transit\s+damage|replacement\s+guarantee|volume\s+discounts?|thresholds?\s+and\s+cash\s+settlement|cash\s+settlement|settlement\s+terms|payment\s+terms?|currency\s*\(|net\s+\d+\s+days?|advance\s+payment|validity\s+of\s+quote|target\s+validity|quote\s+validity|valid\s+for\s+\d+\s+days|lead\s+time|taxes?\s*(?:extra|included)|gst\b|sample\s+submission|samples?\s+required|inspection\s+at\s+warehouse|transit\s+insurance|food\s+grade|fsc\s+certified|test\s+certificate|certificate\s+of\s+analysis|coa\b|credit\s+period|payment\s+against\s+delivery|payment\s+within\s+\d+\s+days|rates?\s+must\s+include|rates?\s+should\s+be|pricing\s+should\s+be|prices?\s+must\s+be)",
    re.I,
)

# User conversational commands, edits, and updates (must never be parsed as product items)
COMMAND_INSTRUCTION_PATTERN = re.compile(
    r"^(?:please\s+)?\b(?:update|change|modify|set|adjust|edit|delete|remove|drop|clear)\b|"
    r"^(?:and\s+)?(?:\d+\s+)?to\s+\d+|"
    r"^to\b|"
    r"^(?:with\s+)?(?:baseline|target\s+price|price|rate)\s+(?:of|to|is)\b",
    re.I,
)


def is_separator_line(text: str) -> bool:
    """Return True if text is a horizontal divider or ascii separator line."""
    clean = text.strip()
    if not clean:
        return True
    return bool(SEPARATOR_PATTERN.match(clean))


def is_conversational_or_boilerplate(text: str) -> bool:
    """Return True if text is an email greeting, closing, or casual remark without line items."""
    clean = text.strip()
    if not clean:
        return True
    if CONVERSATIONAL_BOILERPLATE_PATTERN.search(clean):
        return True
    return False


def is_commercial_term_or_header(text: str) -> bool:
    """Return True if text represents a document header, commercial terms header, table header, note, command, or terms condition bullet."""
    clean = text.strip()
    if not clean or is_separator_line(clean):
        return True
    if DOCUMENT_HEADER_PATTERN.search(clean):
        return True
    if HEADER_TERMS_PATTERN.search(clean):
        return True
    if TABLE_HEADER_PATTERN.search(clean):
        return True
    if COMMERCIAL_CLAUSE_PATTERN.search(clean):
        return True
    if COMMAND_INSTRUCTION_PATTERN.search(clean):
        return True
    if is_conversational_or_boilerplate(clean):
        return True

    lowered = clean.lower()
    # Check if text is a pure instruction or note sentence lacking packaging product nouns
    has_packaging_noun = _text_has_any(clean, PACKAGING_KEYWORDS)
    if not has_packaging_noun:
        # Check for instruction or terms indicators
        instruction_indicators = [
            "please quote",
            "please ensure",
            "please deliver",
            "delivery to",
            "deliver to",
            "payment",
            "credit",
            "net ",
            "valid for",
            "validity",
            "freight",
            "shipping",
            "gst",
            "taxes",
            "samples",
            "inspection",
            "warranty",
            "guarantee",
            "quote",
            "rates",
            "pricing",
            "budget",
            "po ",
            "purchase order",
            "facility",
            "plant",
            "warehouse",
            "dispatch",
            "inr",
            "usd",
            "eur",
            "food grade",
            "fsc",
            "moq",
            "ex-works",
            "ddp",
            "fob",
            "cif",
        ]
        if any(ind in lowered for ind in instruction_indicators):
            return True

    terms_indicators = [
        "moq (minimum order quantity)",
        "minimum order quantity",
        "freight / shipping cost",
        "warranty sla",
        "replacement guarantee",
        "volume discounts",
        "payment terms",
        "currency (inr / usd)",
        "currency (inr/usd)",
        "quote validity",
        "mandatory commercial terms",
        "mandatory sourcing",
    ]
    if any(ind in lowered for ind in terms_indicators):
        return True
    return False


def extract_commercial_terms(text: str) -> dict[str, Any]:
    """Extract structured commercial terms (payment, delivery, validity, currency, reference, scope, notes) from text."""
    terms: dict[str, Any] = {
        "title": None,
        "reference": None,
        "scope": None,
        "payment_terms": None,
        "delivery_terms": None,
        "validity_days": None,
        "currency": None,
        "notes": [],
    }

    # Extract global key-value items from document headers if present
    ref_match = re.search(r"RFI\s+Reference\s*:\s*([a-zA-Z0-9_-]+)", text, re.I)
    if ref_match:
        terms["reference"] = ref_match.group(1).strip()

    scope_match = re.search(r"\bScope\s*:\s*([^\n\r\t]+?)(?=\s*(?:Baseline|Date|Target|Order|$|\t))", text, re.I)
    if scope_match:
        terms["scope"] = scope_match.group(1).strip()

    validity_match = re.search(r"(?:Target\s+Validity|Quote\s+Validity|valid\s+for|validity)\s*:\s*(\d+)\s*days?", text, re.I)
    if validity_match:
        terms["validity_days"] = int(validity_match.group(1))

    curr_match = re.search(r"(?:Baseline\s+Currency|Currency)\s*:\s*([a-zA-Z]+)", text, re.I)
    if curr_match:
        terms["currency"] = curr_match.group(1).upper()

    order_vol_m = re.search(r"Order\s+Volume\s+Scope\s*:\s*([^\n\r\t]+)", text, re.I)
    if order_vol_m:
        vol_text = order_vol_m.group(1).strip()
        if vol_text:
            terms["notes"].append(f"Order Volume Scope: {vol_text}")

    # Split text into logical sentences or lines
    raw_lines = re.split(r"[\n\r]+|[.!?](?=\s+[A-Z0-9]|$)", text)
    lines = [line.strip() for line in raw_lines if line.strip()]

    for line in lines:
        if is_separator_line(line) or HEADER_TERMS_PATTERN.search(line) or TABLE_HEADER_PATTERN.search(line) or DOCUMENT_HEADER_PATTERN.search(line):
            continue

        lowered = line.lower()

        # 1. Payment Terms
        if any(k in lowered for k in ["payment", "net ", "credit", "pdc", "advance", "against delivery"]):
            net_match = re.search(r"(\b\d+\s+days?\s+net\b|\bnet\s+\d+\s+days?\b)", line, re.I)
            credit_match = re.search(r"(\b\d+\s+days?\s+credit(?:\s+period)?\b)", line, re.I)
            adv_match = re.search(r"(\b\d+%\s+advance(?:\s+payment)?\b)", line, re.I)
            after_match = re.search(r"(\b\d+\s+days?\s+(?:after|from)\s+(?:invoice|delivery|grn|receipt)\b)", line, re.I)
            pay_match = re.search(r"(?:payment\s+terms?\s*:?\s*)([a-zA-Z0-9\s%]+)", line, re.I)

            if net_match:
                terms["payment_terms"] = net_match.group(1).title()
            elif credit_match:
                terms["payment_terms"] = credit_match.group(1).title()
            elif adv_match:
                terms["payment_terms"] = adv_match.group(1).title()
            elif after_match:
                terms["payment_terms"] = after_match.group(1).title()
            elif pay_match:
                val = pay_match.group(1).strip()
                if len(val) >= 3 and not any(stop in val.lower() for stop in ["will be", "is to be", "should be"]):
                    terms["payment_terms"] = val.title()

        # 2. Delivery Terms & Destinations
        if any(k in lowered for k in ["delivery", "delivered to", "deliver to", "incoterms", "shipping terms", "ex-works", "ddp", "fob", "cif", "freight"]):
            deliv_match = re.search(r"(?:delivery\s+terms?|incoterms?|shipping\s+terms?)\s*:?\s*([a-zA-Z0-9\s\(\)/,%-]+)", line, re.I)
            if deliv_match:
                terms["delivery_terms"] = deliv_match.group(1).strip()
            elif "deliver" in lowered:
                dest_match = re.search(r"(?:deliver(?:y|ed)?\s+(?:is\s+)?(?:to|at)\s+([a-zA-Z0-9\s\(\)/,%-]+?(?:plant|warehouse|facility|hub|bangalore|pune|mumbai|delhi|chennai|hyderabad|kolkata|ahmedabad|gurgaon|noida|india)(?:\s*\([a-zA-Z]+\))?))", line, re.I)
                if dest_match:
                    terms["delivery_terms"] = dest_match.group(1).strip().capitalize()
                else:
                    time_match = re.search(r"(?:delivery\s+(?:within|completed\s+within)\s+([a-zA-Z0-9\s]+?(?:days|weeks|months|po)))", line, re.I)
                    if time_match and not terms["delivery_terms"]:
                        terms["delivery_terms"] = f"Within {time_match.group(1).strip()}"

        # 3. Validity
        if ("valid" in lowered or "validity" in lowered) and not terms["validity_days"]:
            val_match = re.search(r"(?:valid\s+(?:for|till|until)?|validity\s*:?)\s*(\d+)\s*days?", line, re.I)
            if val_match:
                terms["validity_days"] = int(val_match.group(1))

        # 4. Currency
        if any(k in lowered for k in ["currency", "in usd", "in inr", "in eur", "in gbp", "rates in", "prices in", "quotes in"]) and not terms["currency"]:
            curr_match = re.search(r"\b(INR|USD|EUR|GBP)\b", line, re.I)
            if curr_match:
                terms["currency"] = curr_match.group(1).upper()
            elif "inr" in lowered and "usd" in lowered:
                terms["currency"] = "INR / USD"

        # 5. Scope Notes & Remarks
        if (
            is_commercial_term_or_header(line)
            and not is_conversational_or_boilerplate(line)
            and not COMMAND_INSTRUCTION_PATTERN.search(line)
        ):
            clean_note = line.lstrip("-*•– 0123456789.)[]").strip()
            if clean_note and len(clean_note) > 4 and clean_note not in terms["notes"]:
                terms["notes"].append(clean_note)

    return terms


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
