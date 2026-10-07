"""Database connectivity checks for the configured Postgres schema."""

from __future__ import annotations

import re
from typing import Any

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_connection_info, validate_schema_name


def _sanitize_error_message(error: str) -> str:
    """Strip potential passwords or credentials from error strings."""
    # Hide password=... in any error text
    sanitized = re.sub(r"password=[^\s]+", "password=***", error)
    # Hide postgresql://user:password@...
    sanitized = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", sanitized)
    return sanitized


def check_database_connection(secrets: AppSecrets) -> dict[str, Any]:
    """Check whether Postgres is reachable, the configured schema exists, and a simple query succeeds.

    Returns diagnostic status without leaking database passwords or tokens.
    """
    raw_schema = secrets.DB_SCHEMA or "ktq"
    try:
        schema = validate_schema_name(raw_schema)
    except ValueError as exc:
        return {
            "ok": False,
            "status": "invalid_schema",
            "schema": raw_schema,
            "connection_established": False,
            "schema_exists": False,
            "query_ok": False,
            "error": str(exc),
        }

    if not secrets.db_configured:
        return {
            "ok": False,
            "status": "unconfigured",
            "schema": schema,
            "connection_established": False,
            "schema_exists": False,
            "query_ok": False,
            "error": "Database environment variables are incomplete (host, dbname, user, or password missing).",
        }

    try:
        import psycopg
    except ImportError:
        return {
            "ok": False,
            "status": "driver_missing",
            "schema": schema,
            "connection_established": False,
            "schema_exists": False,
            "query_ok": False,
            "error": "psycopg is not installed. Install psycopg[binary] to enable DB connectivity.",
        }

    conninfo = get_connection_info(secrets)

    try:
        with psycopg.connect(conninfo, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # 1. Simple query verification
                cur.execute("SELECT 1;")
                query_ok = bool(cur.fetchone()[0] == 1)

                # 2. Check if configured schema exists
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = %s);",
                    (schema,),
                )
                schema_exists = bool(cur.fetchone()[0])
    except Exception as exc:
        sanitized_err = _sanitize_error_message(str(exc))
        return {
            "ok": False,
            "status": "connection_error",
            "schema": schema,
            "connection_established": False,
            "schema_exists": False,
            "query_ok": False,
            "error": sanitized_err,
        }

    all_ok = schema_exists and query_ok
    return {
        "ok": all_ok,
        "status": "connected" if all_ok else ("schema_missing" if not schema_exists else "query_failed"),
        "schema": schema,
        "connection_established": True,
        "schema_exists": schema_exists,
        "query_ok": query_ok,
        "error": None if schema_exists else f"Configured schema '{schema}' does not exist in PostgreSQL.",
    }
