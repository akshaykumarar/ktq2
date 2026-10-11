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


def test_extract_rfx_id_helper() -> None:
    """Test extracting integer RFx ID from various formats."""
    from backend.app.vendor.service import extract_rfx_id

    assert extract_rfx_id("26") == 26
    assert extract_rfx_id("RFI-26") == 26
    assert extract_rfx_id("rfx-26") == 26
    assert extract_rfx_id("RFX #26") == 26
    assert extract_rfx_id("RFQ 105") == 105
    assert extract_rfx_id("") is None
    assert extract_rfx_id(None) is None


def test_api_submit_vendor_response_with_rfx_id_form(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test POST /api/vendor-responses accepts rfx_id form field and passes it to service."""
    from backend.app.vendor.models import VendorResponseSubmitPreview, ResponseStatus
    from backend.app.vendor.service import VendorResponseService

    captured_rfx_ref = None

    def mock_submit(self, *args, **kwargs):
        nonlocal captured_rfx_ref
        captured_rfx_ref = kwargs.get("rfx_ref")
        return VendorResponseSubmitPreview(
            response_id=48,
            status=ResponseStatus.RECEIVED,
            message="Quotation received.",
            is_duplicate=False,
            rfx_resolution_preview={"rfx_id": int(captured_rfx_ref) if captured_rfx_ref else None, "version": 1},
        )

    async def mock_pipeline(self, response_id: int):
        pass

    monkeypatch.setattr(VendorResponseService, "submit_vendor_response", mock_submit)
    monkeypatch.setattr(VendorResponseService, "run_pipeline", mock_pipeline)

    # Test submitting with rfx_id="26" (as in user's curl)
    resp = test_client.post(
        "/api/vendor-responses",
        data={
            "body_text": "Quotation for Corrugated Boxes: Rs 40 per piece.",
            "rfx_id": "26",
        },
    )
    assert resp.status_code == 202
    assert captured_rfx_ref == "26"
    assert resp.json()["rfx_resolution_preview"]["rfx_id"] == 26


def test_service_cross_rfx_not_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that identical file hash for a DIFFERENT RFx is NOT treated as duplicate."""
    import hashlib
    from backend.app.config.settings import load_config
    from backend.app.vendor.service import VendorResponseService

    cfg = load_config()
    svc = VendorResponseService(cfg)

    file_bytes = b"Sample quotation PDF content"
    expected_hash = hashlib.sha256(file_bytes).hexdigest()

    # Simulate repo where file_hash exists for RFX 25, but we are querying for RFX 26
    def mock_check_dup(file_hashes, sender_email, rfx_id):
        # Only return duplicate if querying for RFX 25
        if rfx_id == 25 and expected_hash in file_hashes:
            return {"id": 47, "status": "done", "rfx_id": 25, "vendor_id": 2, "version": 1}
        return None

    def mock_create(*args, **kwargs):
        return 48

    monkeypatch.setattr(svc.repo, "check_duplicate_response", mock_check_dup)
    monkeypatch.setattr(svc.repo, "find_vendor_by_email_or_name", lambda *a, **kw: None)
    monkeypatch.setattr(svc.repo, "get_latest_response_for_vendor_rfx", lambda *a, **kw: None)
    monkeypatch.setattr(svc.repo, "create_vendor_response", mock_create)
    monkeypatch.setattr(svc.repo, "add_response_document", lambda *a, **kw: None)

    # Submit for RFX 26 with identical file bytes
    res_26 = svc.submit_vendor_response(
        files=[("quote.pdf", file_bytes, "application/pdf")],
        rfx_ref="26",
    )
    assert res_26.is_duplicate is False
    assert res_26.response_id == 48

    # Submit for RFX 25 with identical file bytes -> should be duplicate
    res_25 = svc.submit_vendor_response(
        files=[("quote.pdf", file_bytes, "application/pdf")],
        rfx_ref="25",
    )
    assert res_25.is_duplicate is True
    assert res_25.response_id == 47
    assert res_25.rfx_resolution_preview["rfx_id"] == 25


def test_resolution_prefixed_rfx_ref(mock_repo: MagicMock) -> None:
    """Test explicit rfx_ref with prefix like 'RFI-1' or 'RFX-1' resolves to RFx #1."""
    resolver = ResolutionEngine(mock_repo)
    res = resolver.resolve(
        rfx_ref="RFI-1",
        vendor_name=None,
        sender_email=None,
        sender_name=None,
        subject=None,
        body_text=None,
        combined_doc_text="",
    )
    assert res.rfx_id == 1
    assert res.signal_used == "explicit_rfx_ref_id"


def test_deterministic_text_extractor_volumetric_discount_brackets() -> None:
    """Test extraction of multi-tiered volumetric discount brackets and MOQ in text."""
    from backend.app.vendor.extract import deterministic_text_extractor

    raw_text = """
    From: sales@boxmakers.in
    Quotation for RFX #1
    1. 5-Ply Corrugated Master Box: 100-500 pcs @ ₹45, 501-2000 pcs @ ₹40, 2001+ pcs @ ₹36 | MOQ: 100
    2. Stretch Film 500mm: 50-200 rolls @ ₹320, 201-1000 rolls @ ₹290 | MOQ: 50
    Payment Terms: Net 30 days
    """
    res = deterministic_text_extractor(raw_text, "quote_ocr_scan.txt")
    assert len(res.line_items) >= 5  # 3 tiers for box + 2 tiers for film
    box_tiers = [it for it in res.line_items if "Corrugated" in it.raw_description or "Box" in it.raw_description]
    assert len(box_tiers) == 3
    assert box_tiers[0].raw_price == 45.0
    assert box_tiers[0].moq == 100.0
    assert box_tiers[0].discount.get("type") == "volumetric"
    assert box_tiers[0].discount.get("min_qty") == 100.0
    assert box_tiers[0].discount.get("max_qty") == 500.0

    assert box_tiers[1].raw_price == 40.0
    assert box_tiers[1].discount.get("min_qty") == 501.0
    assert box_tiers[1].discount.get("max_qty") == 2000.0

    assert box_tiers[2].raw_price == 36.0
    assert box_tiers[2].discount.get("min_qty") == 2001.0
    assert box_tiers[2].discount.get("max_qty") is None





