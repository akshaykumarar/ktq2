"""Unit tests for Analyst feedback and cached trace handling."""

from unittest.mock import MagicMock, patch
import pytest
from psycopg.errors import ForeignKeyViolation

from backend.app.analyst.models import (
    AnalystAskRequest,
    AnalystFeedbackRequest,
    AnalystResponse,
)
from backend.app.analyst.orchestrator import DecisionAnalystOrchestrator
from backend.app.analyst.repository import AnalystRepository


def test_orchestrator_ask_cache_hit_records_trace():
    """Verify that a cache hit in orchestrator.ask() records a trace with path_used='cache'."""
    repo_mock = MagicMock(spec=AnalystRepository)
    cached_payload = {
        "answer_text": "Vendor A is cheapest across all 3 items.",
        "tables": [],
        "charts": [],
        "exports": [],
        "caveats": [],
        "how_i_got_this": {
            "steps": [],
            "sql": ["SELECT * FROM ktq.v_rfx_comparison;"],
            "vendors_included": ["Vendor A"],
            "vendors_excluded_with_reasons": {},
            "items_included": ["Item 1"],
            "flagged_values_used": [],
            "assumptions_used": {},
        },
        "confidence": "high",
        "confidence_reason": "Verified pricing",
        "suggested_followups": [],
        "trace_id": "orig_trace_123",
    }
    repo_mock.get_cached_response.return_value = cached_payload

    orchestrator = DecisionAnalystOrchestrator()
    orchestrator._repo = repo_mock

    req = AnalystAskRequest(
        rfx_id=1,
        session_id="session_test_cache",
        question="Who is cheapest?",
        options={},
    )

    response = orchestrator.ask(req)

    assert response.trace_id.startswith("cached_")
    repo_mock.record_trace.assert_called_once()
    call_kwargs = repo_mock.record_trace.call_args.kwargs
    assert call_kwargs["trace_id"] == response.trace_id
    assert call_kwargs["path_used"] == "cache"
    assert call_kwargs["tokens"] == {"prompt_tokens": 0, "completion_tokens": 0}
    assert call_kwargs["rfx_id"] == 1
    assert call_kwargs["session_id"] == "session_test_cache"
    assert call_kwargs["question"] == "Who is cheapest?"


def test_record_feedback_creates_stub_trace_when_missing():
    """Verify record_feedback creates a stub trace if trace_id is not in analyst_traces."""
    mock_secrets = MagicMock()
    mock_secrets.db_configured = True
    repo = AnalystRepository(secrets=mock_secrets)

    feedback = AnalystFeedbackRequest(
        trace_id="cached_3ecb8532-6de",
        rating=1,
        comment="Great insight!",
        corrected_sql=None,
        corrected_answer=None,
    )

    mock_cursor = MagicMock()
    # 1st execute is "SELECT 1 FROM ktq.analyst_traces WHERE trace_id = %s;"
    mock_cursor.fetchone.side_effect = [None, (42,)]  # First query returns None (not found), insert returns (42,)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("backend.app.analyst.repository.get_db_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        feedback_id = repo.record_feedback(feedback)

    assert feedback_id == 42
    # Verify stub trace was inserted
    assert mock_cursor.execute.call_count == 3
    # Call 1: check trace existence
    assert "SELECT 1 FROM ktq.analyst_traces" in mock_cursor.execute.call_args_list[0][0][0]
    # Call 2: insert stub trace
    assert "INSERT INTO ktq.analyst_traces" in mock_cursor.execute.call_args_list[1][0][0]
    assert mock_cursor.execute.call_args_list[1][0][1][0] == "cached_3ecb8532-6de"
    # Call 3: insert feedback
    assert "INSERT INTO ktq.analyst_feedback" in mock_cursor.execute.call_args_list[2][0][0]


def test_record_feedback_foreign_key_violation_fallback():
    """Verify record_feedback falls back to NULL trace_id if ForeignKeyViolation is raised."""
    mock_secrets = MagicMock()
    mock_secrets.db_configured = True
    repo = AnalystRepository(secrets=mock_secrets)

    feedback = AnalystFeedbackRequest(
        trace_id="orphaned_trace_abc",
        rating=-1,
        comment="Incorrect comparison",
        corrected_sql=None,
        corrected_answer=None,
    )

    mock_cursor1 = MagicMock()
    mock_cursor1.fetchone.return_value = None
    mock_cursor1.execute.side_effect = [None, None, ForeignKeyViolation("violates foreign key constraint")]

    mock_conn1 = MagicMock()
    mock_conn1.cursor.return_value.__enter__.return_value = mock_cursor1

    # Fallback cursor
    mock_cursor2 = MagicMock()
    mock_cursor2.fetchone.return_value = (99,)
    mock_conn2 = MagicMock()
    mock_conn2.cursor.return_value.__enter__.return_value = mock_cursor2

    with patch("backend.app.analyst.repository.get_db_connection", side_effect=[mock_conn1, mock_conn2]):
        mock_conn1.__enter__.return_value = mock_conn1
        mock_conn2.__enter__.return_value = mock_conn2
        feedback_id = repo.record_feedback(feedback)

    assert feedback_id == 99
    # Verify fallback executed insert with NULL trace_id
    fallback_query = mock_cursor2.execute.call_args[0][0]
    assert "NULL" in fallback_query
