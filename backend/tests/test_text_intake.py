"""Tests for natural language and text requirement intake."""

import pytest
from fastapi.testclient import TestClient

from backend.app.intake.agent import deterministic_extract_packaging, extract_requirements
from backend.app.intake.models import TextIntakeRequest
from backend.app.main import app

client = TestClient(app)


@pytest.mark.anyio
async def test_text_requirement_extraction_exact_prompt() -> None:
    """Test standard procurement text matching prompt specifications:

    'Need 5000 boxes, 600x400x300 mm, 5 ply kraft, delivery by 30 Nov.'
    """
    text = "Need 5000 boxes, 600x400x300 mm, 5 ply kraft, delivery by 30 Nov."
    result = await extract_requirements(text)

    assert result.is_packaging is True
    assert len(result.requirements) == 1

    item = result.requirements[0]
    assert item.quantity == 5000.0
    assert item.unit in ("boxes", "pcs")
    assert item.dimensions == {"length": 600.0, "width": 400.0, "height": 300.0, "unit": "mm"}
    assert item.ply == "5-ply"
    assert "kraft" in (item.material or "").lower()
    assert "30 nov" in (item.delivery_date or "").lower()
    assert item.category == "Corrugated packaging"


def test_text_intake_api_endpoint() -> None:
    """Test POST /api/rfi/intake with plain text payload."""
    payload = {
        "text": "We need 5000 corrugated boxes, 600x400x300 mm, 5-ply, kraft finish, delivery required by 30 November.",
        "title": "Warehouse Carton Procurement Q4",
        "create_rfi": False,
    }
    response = client.post("/api/rfi/intake", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["is_packaging"] is True
    assert data["title"] == "Warehouse Carton Procurement Q4"
    assert len(data["requirements"]) == 1

    req = data["requirements"][0]
    assert req["quantity"] == 5000.0
    assert req["dimensions"]["length"] == 600.0
    assert req["dimensions"]["width"] == 400.0
    assert req["dimensions"]["height"] == 300.0
    assert req["dimensions"]["unit"] == "mm"
    assert req["ply"] == "5-ply"
    assert "kraft" in (req["material"] or "").lower()
    assert "30 november" in (req["delivery_date"] or "").lower()


def test_text_intake_rejection_of_non_packaging() -> None:
    """Test that requests for non-packaging items (laptops, chairs) are rejected."""
    payload = {
        "text": "We need 20 laptops and 10 office chairs for the dispatch department.",
    }
    response = client.post("/api/rfi/intake", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["is_packaging"] is False
    assert len(data["requirements"]) == 0
    assert "laptops" in data["message"].lower() or "unsupported" in data["message"].lower()


def test_text_intake_missing_fields_identified() -> None:
    """Test that missing quantity or dimensions are flagged without hallucination."""
    text = "We need corrugated boxes kraft finish delivery by 30 November."
    result = deterministic_extract_packaging(text)

    req = result.requirements[0]
    assert req.quantity is None
    assert "quantity" in req.missing_fields
    assert "dimensions" in req.missing_fields
    assert result.ready_for_rfi is False
