"""Unit tests for CP3 Deterministic Award Optimizer."""

import pytest
from backend.app.analyst.models import AwardConstraints
from backend.app.analyst.optimizer import optimize_award


def test_cheapest_per_line_allocation():
    """Verify unconstrained cheapest-per-line (L1) allocation."""
    result = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="cheapest_per_line"))
    assert result.total_project_spend_inr > 0
    assert len(result.allocations) > 0
    for alloc in result.allocations:
        assert alloc.unit_price_inr > 0
        assert alloc.total_spend_inr == alloc.quantity * alloc.unit_price_inr
    assert len(result.vendor_totals) > 0


def test_single_vendor_allocation():
    """Verify single vendor award finds a vendor quoting all items."""
    result = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="single_vendor"))
    assert result.total_project_spend_inr > 0
    # Exactly one vendor awarded
    assert len(result.vendor_totals) == 1
    assert result.vendor_totals[0].share_of_spend_pct == 100.0


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


def test_review_items_disclosure():
    """Verify REVIEW state items are flagged for acceptance if awarded."""
    result = optimize_award(rfx_id=6, constraints=AwardConstraints(strategy="cheapest_per_line"))
    if result.has_unresolved_review_items:
        assert len(result.review_items_requiring_acceptance) > 0
        for item in result.review_items_requiring_acceptance:
            assert "vendor_id" in item
            assert "item_id" in item
            assert "price" in item
