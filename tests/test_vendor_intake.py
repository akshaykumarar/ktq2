"""Unit and integration tests for Vendor Intake, Resolution, Normalization, Validation, and APIs."""

from __future__ import annotations

import io
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config.settings import load_config
from backend.app.vendor.models import ItemKind, ItemState, ResponseStatus
from backend.app.vendor.normalize import NormalizationEngine
from backend.app.vendor.preprocess import preprocess_document
from backend.app.vendor.resolve import ResolutionEngine
from backend.app.vendor.validate import ValidationEngine


@pytest.fixture
def test_client() -> TestClient:
    """Create FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture
def mock_repo() -> MagicMock:
    """Mock repository for isolated unit testing."""
    repo = MagicMock()
    repo.get_unit_conversions.return_value = {
        ("per 100", "pcs"): 0.01,
        ("per 1000", "pcs"): 0.001,
        ("g", "kg"): 0.001,
        ("dozen", "pcs"): 0.0833333333,
    }
    repo.get_fx_rate.side_effect = lambda from_c, to_c="INR": (
        (86.50, "2026-10-07") if from_c.upper() == "USD" else ((93.00, "2026-10-07") if from_c.upper() == "EUR" else (1.0, "2026-10-07"))
    )
    repo.get_open_rfx_list.return_value = [
        {
            "id": 1,
            "title": "Corrugated Boxes E2E Procurement",
            "status": "triggered",
            "items": [
                {"id": 101, "item_number": 1, "description": "Corrugated Box 600x400x300 mm", "quantity": 5000, "unit": "pcs", "target_price": 40.0},
                {"id": 102, "item_number": 2, "description": "BOPP Packing Tape", "quantity": 200, "unit": "roll", "target_price": 35.0},
            ],
        },
        {
            "id": 2,
            "title": "Stretch Film & Bubble Wrap Q4",
            "status": "draft",
            "items": [
                {"id": 201, "item_number": 1, "description": "Stretch Film 23 Micron", "quantity": 50, "unit": "roll", "target_price": 450.0},
            ],
        },
    ]
    return repo


# ---------------------------------------------------------------------------
# 1. Normalization Engine Tests
# ---------------------------------------------------------------------------

def test_normalization_inr_direct(mock_repo: MagicMock) -> None:
    """Test direct INR pricing with same unit."""
    normalizer = NormalizationEngine(mock_repo)
    res = normalizer.normalize(raw_price=45.0, raw_unit="pcs", raw_currency="INR", target_rfx_unit="pcs")
    assert res.normalized_price_inr == 45.0
    assert res.unit_factor == 1.0
    assert res.fx_rate == 1.0


def test_normalization_per_100_trap(mock_repo: MagicMock) -> None:
    """Test price quoted per 100 units conversion."""
    normalizer = NormalizationEngine(mock_repo)
    res = normalizer.normalize(raw_price=4200.0, raw_unit="per 100", raw_currency="INR", target_rfx_unit="pcs")
    # 4200 * 0.01 = 42.0 INR per pc
    assert res.normalized_price_inr == 42.0
    assert res.unit_factor == 0.01


def test_normalization_foreign_currency_usd(mock_repo: MagicMock) -> None:
    """Test foreign currency conversion from USD to INR."""
    normalizer = NormalizationEngine(mock_repo)
    # $ 0.50 per piece @ 86.50 rate = 43.25 INR
    res = normalizer.normalize(raw_price=0.50, raw_unit="pcs", raw_currency="USD", target_rfx_unit="pcs")
    assert res.normalized_price_inr == 43.25
    assert res.fx_rate == 86.50


def test_normalization_usd_per_100_combined(mock_repo: MagicMock) -> None:
    """Test combined USD + per-100 conversion."""
    normalizer = NormalizationEngine(mock_repo)
    # $ 50.00 per 100 pcs = 50 * 0.01 * 86.50 = 43.25 INR
    res = normalizer.normalize(raw_price=50.0, raw_unit="per 100", raw_currency="USD", target_rfx_unit="pcs")
    assert res.normalized_price_inr == 43.25
    assert res.unit_factor == 0.01


# ---------------------------------------------------------------------------
# 2. Validation Engine Tests
# ---------------------------------------------------------------------------

def test_validation_clean_confident_item() -> None:
    """Test a fully clean matching item receives CONFIDENT state."""
    validator = ValidationEngine()
    state, flags, missing, why_unsure, how_resolve = validator.evaluate_item(
        kind=ItemKind.MATCHED,
        raw_price=42.0,
        raw_unit="pcs",
        raw_qty=5000,
        raw_currency="INR",
        tax_basis="exclusive",
        discount={},
        unresolved_reference=None,
        match_confidence=0.95,
        extraction_confidence=0.95,
        target_rfx_unit="pcs",
        target_rfx_qty=5000,
        target_rfx_price=40.0,
    )
    assert state == ItemState.CONFIDENT
    assert len(missing) == 0
    assert why_unsure is None


def test_validation_unresolved_reference() -> None:
    """Test unresolved reference e.g. 'same as last year' produces REVIEW state & critical flag."""
    validator = ValidationEngine()
    state, flags, missing, why_unsure, how_resolve = validator.evaluate_item(
        kind=ItemKind.MATCHED,
        raw_price=None,
        raw_unit="pcs",
        raw_qty=5000,
        raw_currency="INR",
        tax_basis="unknown",
        discount={},
        unresolved_reference="same as last year",
        match_confidence=0.90,
        extraction_confidence=0.85,
        target_rfx_unit="pcs",
        target_rfx_qty=5000,
        target_rfx_price=40.0,
    )
    assert state == ItemState.MISSING
    assert "price" in missing
    assert any(f["code"] == "unresolved_reference" for f in flags)
    assert "same as last year" in why_unsure


def test_validation_pricing_trap_per_100() -> None:
    """Test price > 15x benchmark with '100' in description triggers critical per-100 flag."""
    validator = ValidationEngine()
    state, flags, missing, why_unsure, how_resolve = validator.evaluate_item(
        kind=ItemKind.MATCHED,
        raw_price=4500.0,  # Trap: 4500 quoted instead of 45.00
        raw_unit="pcs",
        raw_qty=5000,
        raw_currency="INR",
        tax_basis="inclusive",
        discount={},
        unresolved_reference=None,
        match_confidence=0.90,
        extraction_confidence=0.90,
        target_rfx_unit="pcs",
        target_rfx_qty=5000,
        target_rfx_price=40.0,
        raw_description="Corrugated Box 600x400x300 (per 100 count)",
    )
    assert state == ItemState.REVIEW
    assert any(f["code"] == "possible_per_100_vs_per_unit" for f in flags)


def test_validation_not_quoted_item() -> None:
    """Test not-quoted placeholder item gets MISSING state."""
    validator = ValidationEngine()
    state, flags, missing, why_unsure, how_resolve = validator.evaluate_item(
        kind=ItemKind.NOT_QUOTED,
        raw_price=None,
        raw_unit="pcs",
        raw_qty=100,
        raw_currency="INR",
        tax_basis="unknown",
        discount={},
        unresolved_reference=None,
        match_confidence=1.0,
        extraction_confidence=1.0,
        target_rfx_unit="pcs",
        target_rfx_qty=100,
        target_rfx_price=50.0,
    )
    assert state == ItemState.MISSING
    assert "price" in missing
    assert any(f["code"] == "not_quoted" for f in flags)


# ---------------------------------------------------------------------------
# 3. Resolution Engine Tests
# ---------------------------------------------------------------------------

def test_resolution_explicit_rfx_ref(mock_repo: MagicMock) -> None:
    """Test explicit rfx_ref ID matches RFx #1 with 1.0 confidence."""
    resolver = ResolutionEngine(mock_repo)
    res = resolver.resolve(
        rfx_ref="1",
        vendor_name="Test Vendor",
        sender_email="test@vendor.com",
        sender_name="Test",
        subject="Quotation",
        body_text="Quotation details",
        combined_doc_text="",
    )
    assert res.rfx_id == 1
    assert res.confidence == 1.0
    assert res.signal_used == "explicit_rfx_ref_id"
    assert res.is_ambiguous is False


def test_resolution_text_pattern_reference(mock_repo: MagicMock) -> None:
    """Test regex pattern 'RFX #1' in body matches RFx #1."""
    resolver = ResolutionEngine(mock_repo)
    res = resolver.resolve(
        rfx_ref=None,
        vendor_name="Test Vendor",
        sender_email="test@vendor.com",
        sender_name="Test",
        subject="Price Quote",
        body_text="Here is our price quote for RFX #1 Corrugated requirements.",
        combined_doc_text="",
    )
    assert res.rfx_id == 1
    assert res.confidence >= 0.85
    assert res.signal_used == "text_pattern_reference"


# ---------------------------------------------------------------------------
# 4. Preprocessing Tests
# ---------------------------------------------------------------------------

def test_preprocessing_text_and_email() -> None:
    """Test raw text and email parsing."""
    eml_bytes = b"From: vendor@pack.com\nSubject: Quote for RFX 1\n\nItem 1: Boxes @ 45"
    prep = preprocess_document(eml_bytes, "quote.eml", "message/rfc822")
    assert prep.status == "done"
    assert "vendor@pack.com" in prep.parsed_text


def test_deterministic_text_extractor_email_signature() -> None:
    """Test extracting vendor details and items from email body quotation and signature."""
    from backend.app.vendor.extract import deterministic_text_extractor
    
    body = (
        "Hi,Thank you for your response. In response to your previous quotation, please find our quoted prices below: "
        "1. Carton Box: ₹1,000 for 50 pieces and ₹5,000 for 300 pieces. "
        "2. BOPP Tape: ₹400 per dozen. "
        "3. Stretch Film: 5m stretch film at ₹300 per box (50 pieces), and 10m stretch film at ₹600 per box (35 pieces). "
        "Please let us know if you need any further details or clarification regarding the above quotation. "
        "Best regards, Rajesh Kumar Shree Packaging Solutions Sales Manager +91 98765 43210 sales@shreepackagingsolutions.com Bengaluru, Karnataka"
    )
    res = deterministic_text_extractor(body, "email_body.txt")
    assert res.vendor_info.email == "sales@shreepackagingsolutions.com"
    assert "Shree Packaging Solutions" in (res.vendor_info.name or "")
    assert "+91 98765 43210" in (res.vendor_info.phone or "")
    assert "Bengaluru" in (res.vendor_info.address or "")
    assert len(res.line_items) >= 3


# ---------------------------------------------------------------------------
# 5. API Endpoint Tests
# ---------------------------------------------------------------------------

def test_api_submit_vendor_response_empty_payload(test_client: TestClient) -> None:
    """Test POST /api/vendor-responses returns 422 if no file/body is sent."""
    resp = test_client.post("/api/vendor-responses", data={})
    assert resp.status_code == 422


def test_api_submit_vendor_response_text_body(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test POST /api/vendor-responses accepts body_text and returns 202 Accepted."""
    from backend.app.vendor.models import VendorResponseSubmitPreview, ResponseStatus
    from backend.app.vendor.service import VendorResponseService

    def mock_submit(self, *args, **kwargs):
        return VendorResponseSubmitPreview(
            response_id=1,
            status=ResponseStatus.RECEIVED,
            message="Quotation received.",
            is_duplicate=False,
            rfx_resolution_preview={"rfx_id": 1, "version": 1},
        )

    async def mock_pipeline(self, response_id: int):
        pass

    monkeypatch.setattr(VendorResponseService, "submit_vendor_response", mock_submit)
    monkeypatch.setattr(VendorResponseService, "run_pipeline", mock_pipeline)
    resp = test_client.post(
        "/api/vendor-responses",
        data={
            "body_text": "Quotation for RFX #1: Corrugated Box 600x400x300 mm at Rs 42.50 per piece. Quantity: 5000 pcs.",
            "sender_name": "Fast Packaging Ltd",
            "sender_email": "fast@packaging.in",
            "channel": "email",
            "rfx_ref": "1",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "response_id" in data
    assert data["status"] in ("received", "preprocessing", "done", "needs_review")
    assert data["rfx_resolution_preview"]["version"] >= 1



