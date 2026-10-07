"""Tests for mock procurement tools."""

from backend.app.tools.rfx import create_rfx, get_rfx
from backend.app.tools.vendor import search_vendors, get_vendor_status
from backend.app.tools.status import get_rfx_status


def test_create_rfx_tool() -> None:
    """Test mock create_rfx output."""
    res = create_rfx("200 laptops", quantity=200, budget=150000.0)
    assert res["status"] == "success"
    assert res["mock"] is True
    assert res["rfx_id"].startswith("RFX-")
    assert res["quantity"] == 200
    assert res["lifecycle_state"] == "Draft"
    assert "200 laptops" in res["message"]


def test_get_rfx_tool() -> None:
    """Test mock get_rfx output."""
    res = get_rfx("RFX-101")
    assert res["status"] == "success"
    assert res["rfx_id"] == "RFX-101"
    assert res["lifecycle_state"] == "Open for Bidding"
    assert res["bids_received"] == 2


def test_search_vendors_tool() -> None:
    """Test mock search_vendors filtering."""
    res = search_vendors("Dell")
    assert len(res) >= 1
    assert any("Dell" in v["name"] for v in res)


def test_get_vendor_status_tool() -> None:
    """Test mock get_vendor_status output."""
    res = get_vendor_status("VEND-101")
    assert res["status"] == "success"
    assert res["compliance_status"] == "Approved"
    assert res["rating"] == 4.8


def test_get_rfx_status_tool() -> None:
    """Test mock get_rfx_status output."""
    res = get_rfx_status("RFX-101")
    assert res["status"] == "success"
    assert res["rfx_id"] == "RFX-101"
    assert res["progress_percentage"] == 65
    assert res["stage"] == "Vendor Bidding"
