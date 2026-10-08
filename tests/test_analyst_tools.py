"""Unit tests for Step 3 Decision Analyst core tools (CP2).

Tests that query the database (sql_runner, trust, comparison) are marked
``requires_db`` and are skipped when no live PostgreSQL connection is present.
"""

import pytest
from backend.app.analyst.tools.sql_runner import run_sql, validate_sql_query, SQLSecurityError
from backend.app.analyst.tools.trust import compute_trust_report
from backend.app.analyst.tools.comparison import get_comparison_grid
from backend.app.analyst.tools.assumptions import get_assumptions, set_assumption, reset_assumptions


@pytest.mark.requires_db
def test_sql_runner_valid_query():
    """Verify safe SELECT queries are parsed and executed properly."""
    sql = "SELECT item_number, rfx_description, vendor_name, effective_price_inr FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id"
    res = run_sql(sql, rfx_id=6)
    assert res["error"] is None
    assert "columns" in res
    assert "rows" in res
    assert res["row_count"] >= 0


def test_sql_runner_blocks_destructive_commands():
    """Verify security guardrails block DROP, UPDATE, DELETE, and system tables."""
    destructive_queries = [
        "DROP TABLE ktq.rfx",
        "DELETE FROM ktq.vendors WHERE id = 1",
        "UPDATE ktq.rfx SET status = 'cancelled'",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM ktq.v_rfx_comparison; DROP TABLE ktq.vendors;",
    ]
    for bad_sql in destructive_queries:
        with pytest.raises(SQLSecurityError):
            validate_sql_query(bad_sql)


@pytest.mark.requires_db
def test_trust_report_computation():
    """Verify trust and risk profiling computes scores, money at risk, and flags."""
    trust = compute_trust_report(rfx_id=6)
    assert trust.rfx_id == 6
    assert trust.overall_confidence in ("high", "medium", "low")
    assert isinstance(trust.vendors, list)
    for v in trust.vendors:
        assert 0.0 <= v.trust_score <= 100.0
        assert v.money_at_risk_inr >= 0.0
        assert v.knockouts_status in ("passed", "failed", "unresolved", "no_questions")


@pytest.mark.requires_db
def test_comparison_grid_matrix():
    """Verify side-by-side grid returns items, vendors, and cell mappings."""
    grid_data = get_comparison_grid(rfx_id=6)
    assert "error" not in grid_data
    assert "items" in grid_data
    assert "vendors" in grid_data
    assert "grid" in grid_data
    assert "totals_with_coverage" in grid_data
    for tot in grid_data["totals_with_coverage"]:
        assert "coverage_label" in tot
        assert "total_spend_inr" in tot


def test_session_assumptions_management():
    """Verify session what-if assumption isolation and mutations."""
    sess_id = "test_sess_123"
    reset_assumptions(sess_id)
    assump = get_assumptions(sess_id)
    assert assump["fx_rates"]["USD"] == 84.0

    # Mutate USD rate
    set_assumption(sess_id, "fx_rates.USD", 88.5)
    updated = get_assumptions(sess_id)
    assert updated["fx_rates"]["USD"] == 88.5

    # Verify another session is isolated
    sess_other = "test_sess_456"
    assert get_assumptions(sess_other)["fx_rates"]["USD"] == 84.0

    # Reset
    reset_assumptions(sess_id)
    assert get_assumptions(sess_id)["fx_rates"]["USD"] == 84.0


def test_analyst_repository_artifact_caching():
    """Verify storing and retrieving charts/reports per rfx_id without database dependency."""
    from backend.app.analyst.repository import AnalystRepository

    repo = AnalystRepository()
    rfx_id = 9999
    
    # Store chart artifact
    chart_payload = {
        "title": "Vendor Spend Breakdown",
        "type": "pie",
        "spec": {"data": [100, 200]}
    }
    repo.store_rfx_artifact(rfx_id, "chart", "Vendor Spend Breakdown", chart_payload)
    
    # Store table artifact
    table_payload = {
        "title": "L1 Savings Summary",
        "columns": ["Item", "L1 Vendor"],
        "rows": [["Box 1", "Vendor A"]]
    }
    repo.store_rfx_artifact(rfx_id, "table", "L1 Savings Summary", table_payload)

    # Retrieve artifacts
    artifacts = repo.get_rfx_artifacts(rfx_id)
    assert len(artifacts) == 2
    types = {a["type"] for a in artifacts}
    assert "chart" in types
    assert "table" in types

    # Test response caching
    question = "Who is the cheapest vendor for Box 1?"
    mock_resp = {"answer_text": "Vendor A is cheapest at 10 INR", "confidence": "high"}
    repo.save_cached_response(rfx_id, question, mock_resp)

    cached = repo.get_cached_response(rfx_id, question)
    assert cached is not None
    assert cached["answer_text"] == "Vendor A is cheapest at 10 INR"


def test_vendor_trust_summary_model_and_reasoning():
    """Verify VendorTrustSummary Pydantic model validates score_reasoning and risk_level."""
    from backend.app.analyst.models import VendorTrustSummary

    summary = VendorTrustSummary(
        vendor_id=1,
        vendor_name="Apex Packaging",
        coverage_pct=100.0,
        total_items=4,
        matched_items=4,
        confident_items=4,
        review_items=0,
        missing_items=0,
        critical_flags_count=0,
        warning_flags_count=0,
        knockouts_status="passed",
        trust_score=95.0,
        score_reasoning="Coverage: 100% (+40 pts) • Pricing: 4 confident / 0 review (+30 pts) • Knockouts: Cleared (+10 pts) • Penalties: 0 deductions",
        risk_level="Low Risk",
        total_quoted_spend_inr=125000.0,
        money_at_risk_inr=0.0,
        flags_list=[],
    )
    assert summary.vendor_id == 1
    assert summary.risk_level == "Low Risk"
    assert summary.trust_score == 95.0
    assert "Coverage: 100%" in summary.score_reasoning
    assert summary.total_quoted_spend_inr == 125000.0


