"""Database migration runner for the packaging RFI schema."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.app.config.settings import AppSecrets

logger = logging.getLogger(__name__)


def run_migrations(secrets: AppSecrets) -> bool:
    """Execute SQL migrations against the configured PostgreSQL database and schema.

    Substitutes 'ktq' in the migration files with the configured DB_SCHEMA.
    Returns True if migrations succeeded, False otherwise.
    """
    if not secrets.db_configured:
        logger.warning("Database is not configured. Skipping migrations.")
        return False

    try:
        import psycopg
    except ImportError:
        logger.warning("psycopg is not installed. Skipping migrations.")
        return False

    schema = secrets.DB_SCHEMA or "ktq"
    # Validate schema name to prevent SQL injection
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", schema):
        raise ValueError(f"Invalid schema name: {schema}")

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

    migrations_dir = Path(__file__).resolve().parents[3] / "migrations"
    if not migrations_dir.exists():
        logger.warning("Migrations directory not found at %s", migrations_dir)
        return False

    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        return True

    try:
        with psycopg.connect(conninfo, autocommit=True, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                for sql_file in sql_files:
                    logger.info("Executing migration: %s for schema: %s", sql_file.name, schema)
                    content = sql_file.read_text(encoding="utf-8")
                    # Replace default ktq references with configured schema if different
                    if schema != "ktq":
                        content = re.sub(r"\bktq\b", schema, content)
                    cur.execute(content)
        return True
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        return False
