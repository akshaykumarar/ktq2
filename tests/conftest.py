"""Shared pytest configuration for the tests/ suite.

Provides the ``requires_db`` marker.  Tests decorated with
``@pytest.mark.requires_db`` are automatically skipped when the database is
not configured (i.e. when running offline or in CI without a live PostgreSQL
instance).
"""

import pytest
from backend.app.config.settings import AppSecrets


def _db_reachable() -> bool:
    """Return True when the environment has a reachable PostgreSQL database."""
    try:
        secrets = AppSecrets()
        if not secrets.db_configured:
            return False
        import psycopg
        from backend.app.db.connection import get_connection_info
        conninfo = get_connection_info(secrets)
        with psycopg.connect(conninfo, connect_timeout=1):
            pass
        return True
    except Exception:
        return False


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``requires_db`` marker so pytest recognises it."""
    config.addinivalue_line(
        "markers",
        "requires_db: skip when PostgreSQL is not reachable in this environment",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-skip any test marked ``requires_db`` when DB is unavailable."""
    if _db_reachable():
        return  # DB is present and reachable — run everything normally

    skip = pytest.mark.skip(
        reason="PostgreSQL not configured or unreachable in this environment"
    )
    for item in items:
        if item.get_closest_marker("requires_db"):
            item.add_marker(skip)
