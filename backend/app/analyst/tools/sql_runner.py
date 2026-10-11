"""Deterministic read-only SQL execution tool with strict guardrails and validation."""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection

logger = logging.getLogger(__name__)

# Disallowed keywords that indicate non-read-only or dangerous SQL
FORBIDDEN_KEYWORDS = [
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bDELETE\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bTRUNCATE\b",
    r"\bCREATE\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bVACUUM\b",
    r"\bCOPY\b",
    r"\bpg_catalog\b",
    r"\binformation_schema\b",
    r"\bpg_tables\b",
    r"\bpg_user\b",
    r"\bpg_shadow\b",
]

ALLOWED_TABLES_AND_VIEWS = {
    "ktq.v_rfx_comparison",
    "ktq.v_vendor_coverage",
    "ktq.v_open_flags",
    "ktq.v_response_items_current",
    "ktq.rfx",
    "ktq.rfx_items",
    "ktq.rfx_questions",
    "ktq.response_answers",
    "ktq.response_terms",
    "ktq.vendors",
    "ktq.vendor_responses",
    "ktq.item_crops",
    "v_rfx_comparison",
    "v_vendor_coverage",
    "v_open_flags",
    "v_response_items_current",
    "rfx",
    "rfx_items",
    "rfx_questions",
    "response_answers",
    "response_terms",
    "vendors",
    "vendor_responses",
    "item_crops",
}


import sqlglot
from sqlglot import exp


class SQLSecurityError(ValueError):
    """Raised when a SQL query violates security or policy guardrails."""
    pass


def validate_sql_query(sql: str, default_limit: int = 100, max_limit: int = 500) -> str:
    """Validate that SQL is a single read-only SELECT statement against allowed entities using an AST parser."""
    clean_sql = sql.strip().rstrip(";")

    # 1. Multi-statement check
    if ";" in clean_sql:
        raise SQLSecurityError("Multiple SQL statements in a single execution are forbidden.")

    # 2. Must begin with SELECT or WITH
    upper_sql = clean_sql.upper()
    if not (upper_sql.startswith("SELECT") or upper_sql.startswith("WITH")):
        raise SQLSecurityError("Only SELECT or WITH ... SELECT queries are permitted.")

    # 3. Forbidden keywords check
    for pattern in FORBIDDEN_KEYWORDS:
        if re.search(pattern, clean_sql, re.IGNORECASE):
            raise SQLSecurityError(f"SQL contains forbidden keyword/pattern: {pattern}")

    # 4. AST Parse & Table Allowlist Enforcement (including CTEs, JOINs, Subqueries)
    try:
        parsed = sqlglot.parse_one(clean_sql, read="postgres")
    except Exception as parse_err:
        raise SQLSecurityError(f"SQL Syntax / Parsing Error: {parse_err}") from parse_err

    if not isinstance(parsed, (exp.Select, exp.Union)):
        raise SQLSecurityError("Only SELECT or query expressions are permitted.")

    cte_names = {c.alias_or_name.lower() for c in parsed.find_all(exp.CTE)}
    allowed_normalized = {t.lower() for t in ALLOWED_TABLES_AND_VIEWS}

    for tbl_node in parsed.find_all(exp.Table):
        t_name = tbl_node.name.lower()
        schema_name = tbl_node.db.lower() if tbl_node.db else None
        full_name = f"{schema_name}.{t_name}" if schema_name else t_name

        # Skip internal CTE aliases defined in the query
        if t_name in cte_names or full_name in cte_names:
            continue

        # Strictly check against table/view allowlist
        if full_name not in allowed_normalized and t_name not in allowed_normalized:
            raise SQLSecurityError(f"Access to table/view '{full_name}' is not permitted by SQL security policy.")

    # 5. Enforce or clamp LIMIT
    limit_match = re.search(r"\bLIMIT\s+(\d+)", clean_sql, re.IGNORECASE)
    if limit_match:
        val = int(limit_match.group(1))
        if val > max_limit:
            clean_sql = re.sub(r"\bLIMIT\s+\d+", f"LIMIT {max_limit}", clean_sql, flags=re.IGNORECASE)
    else:
        clean_sql = f"{clean_sql} LIMIT {default_limit}"

    return clean_sql


def run_sql(
    sql: str,
    rfx_id: int | None = None,
    secrets: AppSecrets | None = None,
    limit: int = 100,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Execute a guarded read-only SELECT query against allowed database views.

    Returns:
        {
            "sql": executed_sql,
            "columns": list of column names,
            "rows": list of row tuples/lists,
            "row_count": number of rows returned,
            "error": error string if execution failed, None otherwise
        }
    """
    secrets = secrets or AppSecrets()
    if not secrets.db_configured:
        return {
            "sql": sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": "Database not configured.",
        }

    try:
        validated_sql = validate_sql_query(sql, default_limit=limit)
    except SQLSecurityError as sec_err:
        return {
            "sql": sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": f"SQL Policy Rejection: {sec_err}",
        }

    # Replace :rfx_id placeholder safely if present
    if ":rfx_id" in validated_sql:
        if rfx_id is None:
            return {
                "sql": validated_sql,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "error": "Query references :rfx_id parameter but no rfx_id was supplied.",
            }
        validated_sql = re.sub(r":rfx_id\b", str(int(rfx_id)), validated_sql)

    try:
        with get_db_connection(secrets) as conn:
            with conn.cursor() as cur:
                # Set statement timeout for safety
                cur.execute(f"SET statement_timeout = {int(timeout_ms)};")
                cur.execute(validated_sql)
                
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    # Convert row tuples to JSON-friendly lists
                    serialized_rows = []
                    for row in rows:
                        serialized_rows.append([
                            float(v) if hasattr(v, "as_tuple") else (str(v) if hasattr(v, "isoformat") else v)
                            for v in row
                        ])
                    return {
                        "sql": validated_sql,
                        "columns": columns,
                        "rows": serialized_rows,
                        "row_count": len(serialized_rows),
                        "error": None,
                    }
                else:
                    return {
                        "sql": validated_sql,
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                        "error": None,
                    }
    except Exception as e:
        logger.warning("SQL execution error for query [%s]: %s", validated_sql, e)
        return {
            "sql": validated_sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": str(e),
        }
