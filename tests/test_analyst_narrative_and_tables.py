"""Unit tests for analyst question answering, narrative synthesis, and table formatting."""

import pytest
from backend.app.analyst.orchestrator import DecisionAnalystOrchestrator, extract_numbers_from_text
from backend.app.analyst.semantic import SemanticDataEngine
from backend.app.analyst.models import Caveat


def test_clean_table_data_hides_internal_ids_and_humanizes_columns():
    """Verify internal technical IDs are excluded and column headers are humanized."""
    orchestrator = DecisionAnalystOrchestrator()
    cols = ["rfx_item_id", "item_number", "rfx_description", "vendor_name", "effective_price_inr", "state"]
    rows = [
        [184, 1, "3-Ply Corrugated Box", "Vendor Alpha", 19.57, "CONFIDENT"],
        [185, 2, "Stretch Film 5m", "Vendor Beta", 450.00, "REVIEW"],
    ]

    tbl = orchestrator._clean_table_data("Who is cheapest vendor for each item?", cols, rows)

    assert "rfx_item_id" not in [c.lower() for c in tbl.columns]
    assert tbl.columns == ["Item #", "Item Description", "Vendor", "Price (INR)", "State"]
    assert tbl.rows[0] == [1, "3-Ply Corrugated Box", "Vendor Alpha", "₹19.57", "CONFIDENT"]
    assert tbl.rows[1] == [2, "Stretch Film 5m", "Vendor Beta", "₹450.00", "REVIEW"]
    assert tbl.title == "Lowest Quoted Price (L1) per Line Item"


def test_clean_table_data_savings_title_and_percentages():
    """Verify savings query table formatting and percentage humanization."""
    orchestrator = DecisionAnalystOrchestrator()
    cols = ["vendor_name", "coverage_pct", "matched_items_count", "total_rfx_items", "response_status"]
    rows = [
        ["Vendor Alpha", 100.0, 30, 30, "done"],
        ["Vendor Beta", 83.33, 25, 30, "needs_review"],
    ]

    tbl = orchestrator._clean_table_data("Show quotation summary for all vendors", cols, rows)
    assert tbl.columns == ["Vendor", "Coverage %", "Quoted Items", "Total Items", "Status"]
    assert tbl.rows[0][1] == "100.0%"
    assert tbl.rows[1][1] == "83.3%"
    assert tbl.title == "Vendor Quotation & Coverage Summary"


def test_synthesize_deterministic_narrative_cheapest_vendor():
    """Verify cheapest vendor analysis synthesizes vendor win distribution and data confidence."""
    orchestrator = DecisionAnalystOrchestrator()
    cols = ["item_number", "rfx_description", "vendor_name", "effective_price_inr", "state"]
    rows = [
        [1, "Carton Box", "Vendor Alpha", 20.0, "CONFIDENT"],
        [2, "Brown Tape", "Vendor Alpha", 15.0, "CONFIDENT"],
        [3, "Bubble Wrap", "Vendor Beta", 120.0, "REVIEW"],
    ]
    collected_numbers = set()
    narrative = orchestrator._synthesize_deterministic_narrative(
        "Who is cheapest vendor for each item?",
        rfx_id=25,
        cols=cols,
        rows=rows,
        collected_numbers=collected_numbers,
    )

    assert "Lowest Quoted Price (L1) Analysis" in narrative
    assert "Vendor Alpha" in narrative
    assert "Vendor Beta" in narrative
    assert "CONFIDENT" in narrative
    assert "REVIEW" in narrative
    assert "Carton Box" in narrative
    assert "rfx_item_id" not in narrative
    assert 20.0 in collected_numbers or 15.0 in collected_numbers or 3.0 in collected_numbers


def test_synthesize_deterministic_narrative_savings():
    """Verify savings analysis calculates total savings and budget comparison."""
    orchestrator = DecisionAnalystOrchestrator()
    cols = ["item_number", "rfx_description", "rfx_quantity", "rfx_target_price", "vendor_name", "effective_price_inr", "savings_per_unit_inr", "total_savings_inr"]
    rows = [
        [1, "Carton Box", 1000, 25.0, "Vendor Alpha", 20.0, 5.0, 5000.0],
        [2, "Brown Tape", 500, 20.0, "Vendor Beta", 15.0, 5.0, 2500.0],
    ]
    collected_numbers = set()
    narrative = orchestrator._synthesize_deterministic_narrative(
        "Show savings vs target price",
        rfx_id=25,
        cols=cols,
        rows=rows,
        collected_numbers=collected_numbers,
    )

    assert "Savings Analysis vs Target Benchmark" in narrative
    assert "₹7,500.00" in narrative
    assert 7500.0 in collected_numbers


def test_semantic_sql_normalized_matching():
    """Verify semantic SQL engine matches questions regardless of stop words or casing."""
    engine = SemanticDataEngine()

    sql1, _ = engine.generate_sql("Who is the cheapest vendor for each item?", rfx_id=25)
    sql2, _ = engine.generate_sql("Who is cheapest vendor for each item?", rfx_id=25)
    assert sql1.strip() == sql2.strip()
    assert "ktq.v_rfx_comparison" in sql1

    sql_summary, _ = engine.generate_sql("Show quotation summary for all vendors", rfx_id=25)
    assert "ktq.v_vendor_coverage" in sql_summary

    sql_savings, _ = engine.generate_sql("Show savings vs target price", rfx_id=25)
    assert "savings_per_unit_inr" in sql_savings or "rfx_target_price" in sql_savings


def test_semantic_sql_intent_fallbacks_for_new_questions():
    """Verify fallback query generator produces proper SQL for free-form user questions."""
    engine = SemanticDataEngine()

    # Lead times
    sql_lt, _ = engine.generate_sql("Who has the fastest delivery lead time?", rfx_id=25)
    assert "lead_time_days" in sql_lt

    # Spend per vendor
    sql_spend, _ = engine.generate_sql("What is total spend per vendor?", rfx_id=25)
    assert "total_spend_inr" in sql_spend

    # Single bidder
    sql_single, _ = engine.generate_sql("Which items have only one vendor?", rfx_id=25)
    assert "bidder_count" in sql_single

    # Specific item
    sql_item, _ = engine.generate_sql("Compare quotes for item 1", rfx_id=25)
    assert "item_number = 1" in sql_item
