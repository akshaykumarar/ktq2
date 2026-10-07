"""Tests for end-to-end RFI lifecycle operations: create, retrieve, update, trigger."""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_rfi_crud_and_lifecycle_flow() -> None:
    """Test full RFI lifecycle: create -> retrieve -> update -> trigger -> invalid transition."""
    # 1. Create RFI
    create_payload = {
        "title": "Corrugated Cartons Q4 Procure",
        "category": "Corrugated packaging",
        "scope": "Warehouse dispatch packaging for North hub",
        "payment_terms": "Net 30",
        "delivery_terms": "Delivered to warehouse dock",
        "validity_days": 45,
        "status": "ready",
        "requirements": [
            {
                "item_number": 1,
                "category": "Corrugated packaging",
                "item_description": "Master Shipping Box 5-ply",
                "quantity": 3000,
                "unit": "boxes",
                "dimensions": {"length": 600, "width": 400, "height": 300, "unit": "mm"},
                "material": "Kraft",
                "target_price": 55.0,
            }
        ],
    }
    create_res = client.post("/api/rfi", json=create_payload)
    assert create_res.status_code == 201
    created_rfi = create_res.json()
    rfi_id = created_rfi["id"]
    assert rfi_id > 0
    assert created_rfi["title"] == "Corrugated Cartons Q4 Procure"
    assert created_rfi["status"] == "ready"
    assert len(created_rfi["items"]) == 1
    assert created_rfi["items"][0]["quantity"] == 3000

    # 2. Retrieve RFI
    get_res = client.get(f"/api/rfi/{rfi_id}")
    assert get_res.status_code == 200
    fetched_rfi = get_res.json()
    assert fetched_rfi["id"] == rfi_id
    assert fetched_rfi["title"] == "Corrugated Cartons Q4 Procure"

    # 3. Update RFI (PATCH)
    patch_payload = {
        "title": "Corrugated Cartons Q4 Procure - Revised",
        "validity_days": 60,
        "payment_terms": "Net 45",
    }
    patch_res = client.patch(f"/api/rfi/{rfi_id}", json=patch_payload)
    assert patch_res.status_code == 200
    updated_rfi = patch_res.json()
    assert updated_rfi["title"] == "Corrugated Cartons Q4 Procure - Revised"
    assert updated_rfi["validity_days"] == 60
    assert updated_rfi["payment_terms"] == "Net 45"

    # 4. Trigger RFI
    trigger_res = client.post(f"/api/rfi/{rfi_id}/trigger")
    assert trigger_res.status_code == 200
    triggered_rfi = trigger_res.json()
    assert triggered_rfi["id"] == rfi_id
    assert triggered_rfi["status"] == "triggered"
    assert triggered_rfi["triggered_at"] != ""

    # Verify status changed in GET
    verify_res = client.get(f"/api/rfi/{rfi_id}")
    assert verify_res.json()["status"] == "triggered"

    # 5. Invalid status transition: triggering an already TRIGGERED RFI
    invalid_res = client.post(f"/api/rfi/{rfi_id}/trigger")
    assert invalid_res.status_code == 400
    assert "Cannot trigger RFI with status 'triggered'" in invalid_res.json()["detail"]


def test_rfi_not_found_handling() -> None:
    """Non-existent RFI IDs return 404 cleanly."""
    res_get = client.get("/api/rfi/9999999")
    assert res_get.status_code == 404

    res_patch = client.patch("/api/rfi/9999999", json={"title": "new"})
    assert res_patch.status_code == 404

    res_trigger = client.post("/api/rfi/9999999/trigger")
    assert res_trigger.status_code == 400 or res_trigger.status_code == 404
