"""Integration tests for Step 3 Decision Analyst REST APIs (CP4 & CP5).

Note: All tests in this file require a live PostgreSQL connection.
They are automatically skipped when the database is unavailable (see conftest.py).
"""

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


@pytest.mark.requires_db
def test_analyst_ask_cheapest_query():
    """Verify /api/analyst/ask returns valid JSON response contract for pricing query."""
    payload = {
        "rfx_id": 6,
        "session_id": "test_session_api_1",
        "question": "Who is cheapest vendor for each item?",
        "options": {"debug": True},
    }
    resp = client.post("/api/analyst/ask", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert "answer_text" in data
    assert "tables" in data
    assert "how_i_got_this" in data
    assert "confidence" in data
    assert "trace_id" in data
    assert len(data["tables"]) > 0
    assert len(data["how_i_got_this"]["steps"]) > 0


@pytest.mark.requires_db
def test_analyst_ask_award_split():
    """Verify /api/analyst/ask returns allocation tables, charts, and exports for award query."""
    payload = {
        "rfx_id": 6,
        "session_id": "test_session_api_2",
        "question": "Split the award: cheapest per line",
    }
    resp = client.post("/api/analyst/ask", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["tables"]) >= 2  # Line allocation + Vendor summary
    assert len(data["exports"]) >= 1  # Award pack export generated
    assert data["exports"][0]["url"].startswith("/api/exports/")


@pytest.mark.requires_db
def test_analyst_session_history_and_feedback():
    """Verify session history retrieval and feedback submission."""
    sess_id = "test_sess_hist_1"
    # Ask a question
    client.post("/api/analyst/ask", json={"rfx_id": 6, "session_id": sess_id, "question": "Show quote coverage summary"})

    # Get history
    hist_resp = client.get(f"/api/analyst/sessions/{sess_id}")
    assert hist_resp.status_code == 200
    history = hist_resp.json()
    assert len(history) >= 1
    trace_id = history[0]["trace_id"]

    # Submit feedback
    fb_payload = {
        "trace_id": trace_id,
        "rating": 1,
        "comment": "Accurate coverage metrics!",
        "corrected_sql": None,
        "corrected_answer": None,
    }
    fb_resp = client.post("/api/analyst/feedback", json=fb_payload)
    assert fb_resp.status_code == 200
    assert fb_resp.json()["status"] == "ok"


@pytest.mark.requires_db
def test_comparison_and_trust_endpoints():
    """Verify /api/rfx/{id}/comparison and /api/rfx/{id}/trust endpoints."""
    comp_resp = client.get("/api/rfx/6/comparison")
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert "grid" in comp_data
    assert "totals_with_coverage" in comp_data

    trust_resp = client.get("/api/rfx/6/trust")
    assert trust_resp.status_code == 200
    trust_data = trust_resp.json()
    assert "vendors" in trust_data
    assert "overall_confidence" in trust_data


@pytest.mark.requires_db
def test_award_scenario_lifecycle_and_finalization():
    """Verify creating, fetching, and finalizing award scenario with review flag governance."""
    # 1. Create scenario
    create_payload = {
        "title": "Test Split Scenario",
        "constraints": {
            "strategy": "cheapest_per_line",
            "require_knockout_pass": True,
            "include_review_prices": True,
        },
    }
    create_resp = client.post("/api/rfx/6/award/scenarios", json=create_payload)
    assert create_resp.status_code == 200
    scen_data = create_resp.json()
    scen_id = scen_data["scenario_id"]

    # 2. Fetch scenario
    fetch_resp = client.get(f"/api/rfx/6/award/scenarios/{scen_id}")
    assert fetch_resp.status_code == 200
    assert fetch_resp.json()["status"] == "draft"

    # 3. Finalize scenario (pass accepted review flags if any)
    result = scen_data["result"]
    req_items = result.get("review_items_requiring_acceptance", [])
    accepted_flags = [{"vendor_id": r["vendor_id"], "item_id": r["item_id"], "reason": "Buyer verified"} for r in req_items]

    fin_resp = client.post(
        f"/api/rfx/6/award/scenarios/{scen_id}/finalize",
        json={"accepted_review_flags": accepted_flags, "buyer_name": "lead_buyer@company.com"},
    )
    assert fin_resp.status_code == 200
    assert fin_resp.json()["status"] == "finalized"


@pytest.mark.requires_db
def test_export_file_download():
    """Verify downloading an exported XLSX workbook."""
    # Trigger an award query that generates an export
    resp = client.post("/api/analyst/ask", json={"rfx_id": 6, "session_id": "test_exp", "question": "Split the award: cheapest per line"})
    exports = resp.json().get("exports", [])
    assert len(exports) > 0

    export_url = exports[0]["url"]
    export_id = export_url.split("/")[-1]

    dl_resp = client.get(f"/api/exports/{export_id}")
    assert dl_resp.status_code == 200
    assert len(dl_resp.content) > 1000  # Non-empty valid Excel file
