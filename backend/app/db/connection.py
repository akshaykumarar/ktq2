"""PostgreSQL database connection management respecting dynamic DB_SCHEMA."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Generator

from backend.app.config.settings import AppSecrets

logger = logging.getLogger(__name__)


def validate_schema_name(schema: str) -> str:
    """Validate that the schema name contains only safe alphanumeric/underscore characters."""
    clean = schema.strip()
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", clean):
        raise ValueError(f"Invalid schema name: {schema}")
    return clean


def get_connection_info(secrets: AppSecrets) -> str:
    """Build a sanitized psycopg connection string from secrets."""
    if secrets.DATABASE_URL:
        return secrets.DATABASE_URL

    conninfo = (
        f"host={secrets.DB_HOST} "
        f"port={secrets.DB_PORT} "
        f"dbname={secrets.DB_NAME} "
        f"user={secrets.DB_USER} "
        f"password={secrets.DB_PASSWORD} "
        f"sslmode={secrets.DB_SSL_MODE}"
    )
    if secrets.DB_CHANNEL_BINDING:
        conninfo += f" channel_binding={secrets.DB_CHANNEL_BINDING}"
    return conninfo


@contextmanager
def get_db_connection(secrets: AppSecrets) -> Generator[Any, None, None]:
    """Yield a connected PostgreSQL connection with search_path set to the configured schema.

    Raises RuntimeError or psycopg exceptions if database is not reachable.
    """
    if not secrets.db_configured:
        raise RuntimeError("Database environment variables are incomplete.")

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is not installed. Install psycopg[binary] to enable DB connectivity."
        ) from exc

    schema = validate_schema_name(secrets.DB_SCHEMA or "ktq")
    conninfo = get_connection_info(secrets)

    with psycopg.connect(conninfo, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            # Set search_path dynamically to the validated schema
            cur.execute(f'SET search_path TO "{schema}", public')
        yield conn
