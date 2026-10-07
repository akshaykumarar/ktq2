"""Mock Vendor tools for supplier discovery and status checking.

NOTE: These are mock implementations designed to be replaced with real
APIs, database adapters, or Make.com webhooks in subsequent iterations.
"""

from typing import Any


_MOCK_VENDORS = [
    {
        "vendor_id": "VEND-101",
        "name": "Dell Global Solutions",
        "category": "Hardware & IT Equipment",
        "rating": 4.8,
        "compliance_status": "Approved",
        "payment_terms": "Net 45",
        "contact_email": "enterprise@dell.com",
    },
    {
        "vendor_id": "VEND-102",
        "name": "Lenovo Commercial Direct",
        "category": "Hardware & IT Equipment",
        "rating": 4.6,
        "compliance_status": "Approved",
        "payment_terms": "Net 30",
        "contact_email": "sales@lenovo.com",
    },
    {
        "vendor_id": "VEND-103",
        "name": "HP Enterprise Supplies",
        "category": "Hardware & IT Equipment",
        "rating": 4.5,
        "compliance_status": "Under Annual Review",
        "payment_terms": "Net 30",
        "contact_email": "partners@hp.com",
    },
    {
        "vendor_id": "VEND-201",
        "name": "OfficeCore Ergonomics",
        "category": "Furniture & Facilities",
        "rating": 4.7,
        "compliance_status": "Approved",
        "payment_terms": "Net 60",
        "contact_email": "b2b@officecore.com",
    },
]


def search_vendors(query: str, category: str = "") -> list[dict[str, Any]]:
    """Search registered suppliers by name, product capability, or category.

    [MOCK IMPLEMENTATION]
    Simulates searching the corporate vendor directory.

    Args:
        query: Free-text search term (e.g. 'laptops', 'Dell', 'hardware').
        category: Optional category filter (e.g. 'Hardware & IT Equipment').

    Returns:
        List of matching vendor records.
    """
    q = query.lower().strip()
    c = category.lower().strip()

    matches = []
    for v in _MOCK_VENDORS:
        name_match = q in v["name"].lower() or q in v["category"].lower()
        cat_match = not c or c in v["category"].lower()
        if (not q or name_match) and cat_match:
            matches.append({**v, "mock": True})

    # Return default list if query was very broad
    if not matches and not c:
        matches = [{**v, "mock": True} for v in _MOCK_VENDORS[:2]]

    return matches


def get_vendor_status(vendor_id: str) -> dict[str, Any]:
    """Retrieve compliance and onboarding status for a specific vendor.

    [MOCK IMPLEMENTATION]
    Simulates checking vendor risk, onboarding stage, and performance rating.

    Args:
        vendor_id: Identifier or name of the vendor (e.g. 'VEND-101' or 'Dell').

    Returns:
        Dictionary detailing vendor status and risk indicators.
    """
    clean_id = vendor_id.strip()
    for v in _MOCK_VENDORS:
        if clean_id.lower() in v["vendor_id"].lower() or clean_id.lower() in v["name"].lower():
            return {
                "status": "success",
                "mock": True,
                "vendor_id": v["vendor_id"],
                "name": v["name"],
                "compliance_status": v["compliance_status"],
                "rating": v["rating"],
                "active_contracts": 2,
                "message": f"Vendor '{v['name']}' ({v['vendor_id']}) is currently {v['compliance_status']} with rating {v['rating']}/5.0.",
            }

    return {
        "status": "success",
        "mock": True,
        "vendor_id": clean_id,
        "name": clean_id,
        "compliance_status": "Active / Approved",
        "rating": 4.5,
        "active_contracts": 1,
        "message": f"Vendor '{clean_id}' is active and compliant for procurement transactions.",
    }
