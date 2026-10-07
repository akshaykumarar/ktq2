"""Tests for database health check and dynamic schema configuration."""

from unittest.mock import MagicMock, patch
import pytest

from backend.app.config.settings import AppSecrets
from backend.app.db.health import check_database_connection
from backend.app.db.connection import validate_schema_name, get_connection_info


def test_schema_name_validation() -> None:
    """Validate that safe schema names pass and injection strings are rejected."""
    assert validate_schema_name("ktq") == "ktq"
    assert validate_schema_name("custom_schema_1") == "custom_schema_1"

    with pytest.raises(ValueError):
        validate_schema_name("ktq; DROP TABLE students;")

    with pytest.raises(ValueError):
        validate_schema_name("ktq--")


def test_db_health_unconfigured() -> None:
    """Unconfigured database secrets return unconfigured status without throwing errors."""
    secrets = AppSecrets(
        DB_HOST=None,
        DB_NAME=None,
        DB_USER=None,
        DB_PASSWORD=None,
        DATABASE_URL=None,
        DB_SCHEMA="ktq",
    )
    result = check_database_connection(secrets)
    assert result["ok"] is False
    assert result["status"] == "unconfigured"
    assert result["schema"] == "ktq"
    assert "incomplete" in result["error"].lower()


def test_db_health_invalid_schema() -> None:
    """Invalid schema characters return invalid_schema error without connecting."""
    secrets = AppSecrets(
        DB_HOST="localhost",
        DB_NAME="testdb",
        DB_USER="user",
        DB_PASSWORD="password",
        DB_SCHEMA="bad schema; drop table",
    )
    result = check_database_connection(secrets)
    assert result["ok"] is False
    assert result["status"] == "invalid_schema"


def test_db_health_valid_connection_mocked() -> None:
    """Valid database connection verifies query execution and schema existence."""
    secrets = AppSecrets(
        DB_HOST="localhost",
        DB_PORT=5432,
        DB_NAME="testdb",
        DB_USER="testuser",
        DB_PASSWORD="testpassword",
        DB_SCHEMA="custom_schema",
    )

    mock_cursor = MagicMock()
    # First fetchone is for "SELECT 1;", second is for schema exists check
    mock_cursor.fetchone.side_effect = [(1,), (True,)]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        result = check_database_connection(secrets)
        assert result["ok"] is True
        assert result["status"] == "connected"
        assert result["schema"] == "custom_schema"
        assert result["query_ok"] is True
        assert result["schema_exists"] is True
        assert result["error"] is None


def test_db_health_schema_missing_mocked() -> None:
    """When schema does not exist in PostgreSQL, return clear diagnostic without credential leak."""
    secrets = AppSecrets(
        DB_HOST="localhost",
        DB_PORT=5432,
        DB_NAME="testdb",
        DB_USER="testuser",
        DB_PASSWORD="secret_password_123",
        DB_SCHEMA="missing_schema",
    )

    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [(1,), (False,)]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        result = check_database_connection(secrets)
        assert result["ok"] is False
        assert result["status"] == "schema_missing"
        assert result["schema"] == "missing_schema"
        assert result["query_ok"] is True
        assert result["schema_exists"] is False
        assert "does not exist" in result["error"]
        # Password must not be leaked
        assert "secret_password_123" not in str(result)
