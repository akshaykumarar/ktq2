"""Test script verifying CP1: Semantic Layer & Text-to-SQL Fallback against live PostgreSQL data."""

import json
from backend.app.analyst.semantic import SemanticDataEngine

def test_10_basic_questions():
    engine = SemanticDataEngine()
    
    questions = [
        "Who is the cheapest vendor for each item?",
        "Show total coverage percentage and quote counts for all vendors",
        "Which items have only one valid bidder?",
        "List all items with REVIEW state prices or flags",
        "What are the active critical and warning flags for this RFx?",
        "Show questionnaire pass/fail results for each vendor",
        "Which vendors cleared all knockout quality questions?",
        "Compare vendors on price for item number 1",
        "What are the commercial terms and freight conditions quoted by each vendor?",
        "Show price comparison versus target price for all items"
    ]
    
    print("=== CP1: TESTING 10 BASIC QUESTIONS AGAINST SEMANTIC ENGINE ===")
    success_count = 0
    for idx, q in enumerate(questions, 1):
        res = engine.ask_data(q, rfx_id=6)
        has_error = bool(res.get("error"))
        row_count = res.get("row_count", 0)
        status = "FAIL" if has_error else "PASS"
        if not has_error:
            success_count += 1
        print(f"\n[{idx:02d}] {q}")
        print(f"     Status: {status} | Rows: {row_count} | Path: {res.get('path_used')}")
        print(f"     SQL: {res.get('sql', '')[:120]}...")
        if has_error:
            print(f"     Error: {res.get('error')}")
        elif row_count > 0:
            print(f"     Columns: {res.get('columns')}")
            print(f"     Sample Row 1: {res.get('rows', [])[0] if res.get('rows') else 'None'}")

    print(f"\nCP1 Summary: {success_count}/{len(questions)} queries executed successfully.")
    assert success_count == len(questions), f"Expected all 10 queries to pass, got {success_count}"

if __name__ == "__main__":
    test_10_basic_questions()
