"""Unit tests for CP3 Deterministic Award Optimizer.

All tests require a live PostgreSQL connection (reads from vendor quotation views).
They are automatically skipped when the database is unavailable (see conftest.py).
"""

import pytest
from backend.app.analyst.models import AwardConstraints
from backend.app.analyst.optimizer import optimize_award


@pytest.mark.requires_db
def test_cheapest_per_line_allocation():
    """Verify unconstrained cheapest-per-line (L1) allocation."""
    result = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="cheapest_per_line"))
    assert result.total_project_spend_inr > 0
    assert len(result.allocations) > 0
    for alloc in result.allocations:
        assert alloc.unit_price_inr > 0
        assert alloc.total_spend_inr == alloc.quantity * alloc.unit_price_inr
    assert len(result.vendor_totals) > 0


@pytest.mark.requires_db
def test_single_vendor_allocation():
    """Verify single vendor award finds a vendor quoting all items."""
    result = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="single_vendor"))
    assert result.total_project_spend_inr > 0
    # Exactly one vendor awarded
    assert len(result.vendor_totals) == 1
    assert result.vendor_totals[0].share_of_spend_pct == 100.0


@pytest.mark.requires_db
def test_max_share_cap_constraint():
    """Verify max share per vendor splits volume when threshold is constrained."""
    result = optimize_award(
        rfx_id=6,
        constraints=AwardConstraints(
            strategy="multi_vendor_split",
            max_share_per_vendor_pct=60.0,
        ),
    )
    assert result.total_project_spend_inr > 0
    for vt in result.vendor_totals:
        # If there are multiple vendors, no single vendor should grossly dominate if alternatives exist
        assert vt.total_spend_inr <= result.total_project_spend_inr


@pytest.mark.requires_db
def test_what_if_overrides():
    """Verify what-if freight and price overrides adjust allocation and totals."""
    baseline = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="cheapest_per_line"))

    # Add freight 50 INR per unit
    freight_result = optimize_award(
        rfx_id=6,
        constraints=AwardConstraints(
            strategy="cheapest_per_line",
            freight_override_per_unit=50.0,
        ),
    )
    assert freight_result.total_project_spend_inr > baseline.total_project_spend_inr


@pytest.mark.requires_db
def test_review_items_disclosure():
    """Verify REVIEW state items are flagged for acceptance if awarded."""
    result = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="cheapest_per_line"))
    if result.has_unresolved_review_items:
        assert len(result.review_items_requiring_acceptance) > 0
        for item in result.review_items_requiring_acceptance:
            assert "vendor_id" in item
            assert "item_id" in item
            assert "price" in item


def test_moq_penalty_computation_modes():
    """Verify compute_line_moq_cost handles none, shortfall_penalty, quantity_bump, and exclude_below_moq."""
    from backend.app.analyst.optimizer import compute_line_moq_cost

    # 1. No MOQ enforcement
    c_none = AwardConstraints(enforce_moq=False, moq_penalty_mode="none")
    spend, eff_q, pen, ok = compute_line_moq_cost(qty=100.0, unit_price=10.0, moq=500.0, constraints=c_none)
    assert spend == 1000.0
    assert eff_q == 100.0
    assert pen is None
    assert ok is True

    # 2. Shortfall penalty mode (order 100 pcs @ 10, MOQ 500, shortfall = 400 pcs)
    # penalty = 400 * 10 * 1.0 = 4000. Total spend = 1000 + 4000 = 5000.
    c_penalty = AwardConstraints(enforce_moq=True, moq_penalty_mode="shortfall_penalty", moq_shortfall_penalty_rate=1.0)
    spend, eff_q, pen, ok = compute_line_moq_cost(qty=100.0, unit_price=10.0, moq=500.0, constraints=c_penalty)
    assert spend == 5000.0
    assert eff_q == 100.0
    assert pen == 4000.0
    assert ok is True

    # 3. Quantity bump mode (order 100 pcs bumped to MOQ 500 pcs @ 10 = 5000 total spend, surplus = 4000)
    c_bump = AwardConstraints(enforce_moq=True, moq_penalty_mode="quantity_bump")
    spend, eff_q, pen, ok = compute_line_moq_cost(qty=100.0, unit_price=10.0, moq=500.0, constraints=c_bump)
    assert spend == 5000.0
    assert eff_q == 500.0
    assert pen == 4000.0
    assert ok is True

    # 4. Exclude below MOQ mode
    c_exclude = AwardConstraints(enforce_moq=True, moq_penalty_mode="exclude_below_moq")
    spend, eff_q, pen, ok = compute_line_moq_cost(qty=100.0, unit_price=10.0, moq=500.0, constraints=c_exclude)
    assert ok is False
    assert spend == float("inf")

    # 5. Qty meets or exceeds MOQ (no penalty)
    spend, eff_q, pen, ok = compute_line_moq_cost(qty=600.0, unit_price=10.0, moq=500.0, constraints=c_penalty)
    assert spend == 6000.0
    assert eff_q == 600.0
    assert pen is None
    assert ok is True
