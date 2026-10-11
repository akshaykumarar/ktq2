"""Unit tests for Analyst chat history clearing and session deletion."""

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.analyst.repository import AnalystRepository

client = TestClient(app)


def test_delete_session_history_repository():
    """Verify delete_session_history executes DELETE query on analyst_traces."""
    mock_secrets = MagicMock()
    mock_secrets.db_configured = True
    repo = AnalystRepository(secrets=mock_secrets)

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 3
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("backend.app.analyst.repository.get_db_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        deleted = repo.delete_session_history("test_session_xyz")

    assert deleted == 3
    assert "DELETE FROM ktq.analyst_traces WHERE session_id = %s;" in mock_cursor.execute.call_args[0][0]
    assert mock_cursor.execute.call_args[0][1] == ("test_session_xyz",)


def test_clear_rfx_history_repository():
    """Verify clear_rfx_history purges caches and deletes traces by rfx_id."""
    mock_secrets = MagicMock()
    mock_secrets.db_configured = True
    repo = AnalystRepository(secrets=mock_secrets)

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 5
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("backend.app.analyst.repository.get_db_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        deleted = repo.clear_rfx_history(25)

    assert deleted == 5
    assert "DELETE FROM ktq.analyst_traces WHERE rfx_id = %s;" in mock_cursor.execute.call_args[0][0]
    assert mock_cursor.execute.call_args[0][1] == (25,)


def test_delete_session_api_endpoint():
    """Verify DELETE /api/analyst/sessions/{id} returns ok status."""
    with patch("backend.app.api.analyst.get_repository") as mock_get_repo:
        mock_repo = MagicMock()
        mock_repo.delete_session_history.return_value = 2
        mock_get_repo.return_value = mock_repo

        resp = client.delete("/api/analyst/sessions/sess_123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["deleted_count"] == 2
        mock_repo.delete_session_history.assert_called_once_with("sess_123")


def test_delete_rfx_history_api_endpoint():
    """Verify DELETE /api/analyst/history/{rfx_id} returns ok status."""
    with patch("backend.app.api.analyst.get_repository") as mock_get_repo:
        mock_repo = MagicMock()
        mock_repo.clear_rfx_history.return_value = 8
        mock_get_repo.return_value = mock_repo

        resp = client.delete("/api/analyst/history/25")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["deleted_count"] == 8
        mock_repo.clear_rfx_history.assert_called_once_with(25)
