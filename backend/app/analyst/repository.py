"""Database repository for analyst traces, feedback, award scenarios, and audit logs."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from psycopg.rows import dict_row

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection
from backend.app.analyst.models import (
    AnalystFeedbackRequest,
    AwardConstraints,
    AwardOptimizationResult,
    FeedbackRecord,
)

logger = logging.getLogger(__name__)


# In-memory stores for fast response and artifact caching per RFX ID
_RESPONSE_CACHE: dict[str, dict[str, Any]] = {}
_RFX_ARTIFACTS_CACHE: dict[int, list[dict[str, Any]]] = {}


class AnalystRepository:
    """Manages PostgreSQL persistence and token-optimizing caching for Step 3 Decision Analyst."""

    def __init__(self, secrets: AppSecrets | None = None):
        self._secrets = secrets or AppSecrets()

    def get_cached_response(self, rfx_id: int, question: str) -> dict[str, Any] | None:
        """Retrieve cached analysis response for an RFX ID and question to optimize token usage."""
        cache_key = f"{rfx_id}:{question.strip().lower()}"
        if cache_key in _RESPONSE_CACHE:
            return _RESPONSE_CACHE[cache_key]

        if not self._secrets.db_configured:
            return None

        query = """
            SELECT final_response
            FROM ktq.analyst_traces
            WHERE rfx_id = %s AND LOWER(TRIM(question)) = LOWER(TRIM(%s)) AND error IS NULL
            ORDER BY created_at DESC
            LIMIT 1;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(query, (rfx_id, question.strip()))
                    row = cur.fetchone()
                    if row and row.get("final_response"):
                        resp = row["final_response"]
                        _RESPONSE_CACHE[cache_key] = resp
                        return resp
        except Exception as e:
            logger.debug("Cache lookup failed: %s", e)
        return None

    def save_cached_response(self, rfx_id: int, question: str, response_data: dict[str, Any]) -> None:
        """Cache an analysis response in memory."""
        cache_key = f"{rfx_id}:{question.strip().lower()}"
        _RESPONSE_CACHE[cache_key] = response_data

    def store_rfx_artifact(self, rfx_id: int, artifact_type: str, title: str, payload: dict[str, Any]) -> None:
        """Store a generated chart/report artifact for an RFX ID."""
        if rfx_id not in _RFX_ARTIFACTS_CACHE:
            _RFX_ARTIFACTS_CACHE[rfx_id] = []
        
        # Deduplicate by title + type
        _RFX_ARTIFACTS_CACHE[rfx_id] = [
            a for a in _RFX_ARTIFACTS_CACHE[rfx_id] if not (a.get("title") == title and a.get("type") == artifact_type)
        ]
        _RFX_ARTIFACTS_CACHE[rfx_id].append({
            "rfx_id": rfx_id,
            "type": artifact_type,
            "title": title,
            "data": payload,
            "created_at": datetime.now().isoformat(),
        })

    def get_rfx_artifacts(self, rfx_id: int) -> list[dict[str, Any]]:
        """Fetch all stored graphs, charts, and reports for a given RFX ID."""
        in_memory = list(_RFX_ARTIFACTS_CACHE.get(rfx_id, []))
        if in_memory:
            return in_memory

        if not self._secrets.db_configured:
            return []

        # Extract previously generated charts and reports from traces
        query = """
            SELECT final_response, created_at
            FROM ktq.analyst_traces
            WHERE rfx_id = %s AND error IS NULL
            ORDER BY created_at DESC
            LIMIT 20;
        """
        results: list[dict[str, Any]] = []
        seen_titles = set()
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(query, (rfx_id,))
                    rows = cur.fetchall()
                    for r in rows:
                        resp = r.get("final_response") or {}
                        # Charts
                        for chart in resp.get("charts", []):
                            title = chart.get("title", "Chart")
                            if title not in seen_titles:
                                seen_titles.add(title)
                                results.append({
                                    "rfx_id": rfx_id,
                                    "type": "chart",
                                    "title": title,
                                    "data": chart,
                                    "created_at": str(r.get("created_at")),
                                })
                        # Tables / Reports
                        for tbl in resp.get("tables", []):
                            title = tbl.get("title", "Table Report")
                            if title not in seen_titles:
                                seen_titles.add(title)
                                results.append({
                                    "rfx_id": rfx_id,
                                    "type": "table",
                                    "title": title,
                                    "data": tbl,
                                    "created_at": str(r.get("created_at")),
                                })
            _RFX_ARTIFACTS_CACHE[rfx_id] = results
            return results
        except Exception as e:
            logger.debug("Failed to retrieve RFX artifacts from DB: %s", e)
            return []

    def record_trace(
        self,
        trace_id: str,
        rfx_id: int | None,
        session_id: str,
        question: str,
        tool_steps: list[dict[str, Any]],
        sql_executed: list[str],
        path_used: str,
        latencies: dict[str, Any],
        tokens: dict[str, Any],
        final_response: dict[str, Any],
        confidence: str,
        confidence_reason: str,
        prompt_version: str = "1.0.0",
        semantic_model_version: str = "1.0.0",
        error: str | None = None,
    ) -> None:
        """Persist a full trace of an analyst query turn."""
        if not self._secrets.db_configured:
            return

        query = """
            INSERT INTO ktq.analyst_traces (
                trace_id, rfx_id, session_id, question, tool_steps, sql_executed,
                path_used, latencies, tokens, final_response, confidence,
                confidence_reason, prompt_version, semantic_model_version, error, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (trace_id) DO UPDATE SET
                final_response = EXCLUDED.final_response,
                confidence = EXCLUDED.confidence,
                confidence_reason = EXCLUDED.confidence_reason,
                error = EXCLUDED.error;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            trace_id,
                            rfx_id,
                            session_id,
                            question,
                            json.dumps(tool_steps),
                            json.dumps(sql_executed),
                            path_used,
                            json.dumps(latencies),
                            json.dumps(tokens),
                            json.dumps(final_response),
                            confidence,
                            confidence_reason,
                            prompt_version,
                            semantic_model_version,
                            error,
                        ),
                    )
                conn.commit()
        except Exception as e:
            logger.error("Failed to record analyst trace: %s", e, exc_info=True)

    def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieve historical interaction turns for a given session."""
        if not self._secrets.db_configured:
            return []

        query = """
            SELECT trace_id, rfx_id, session_id, question, tool_steps, sql_executed,
                   path_used, latencies, final_response, confidence, confidence_reason,
                   prompt_version, error, created_at
            FROM ktq.analyst_traces
            WHERE session_id = %s
            ORDER BY created_at ASC;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(query, (session_id,))
                    rows = cur.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to get session history: %s", e, exc_info=True)
            return []

    def record_feedback(self, feedback: AnalystFeedbackRequest) -> int | None:
        """Insert user feedback for a given trace."""
        if not self._secrets.db_configured:
            return None

        query = """
            INSERT INTO ktq.analyst_feedback (
                trace_id, rating, comment, corrected_sql, corrected_answer, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, NOW()
            )
            RETURNING id;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            feedback.trace_id,
                            feedback.rating,
                            feedback.comment,
                            feedback.corrected_sql,
                            feedback.corrected_answer,
                        ),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return row[0] if row else None
        except Exception as e:
            logger.error("Failed to record feedback: %s", e, exc_info=True)
            return None

    def get_all_feedback(self) -> list[FeedbackRecord]:
        """Fetch all feedback records for learning and export."""
        if not self._secrets.db_configured:
            return []

        query = """
            SELECT id, trace_id, rating, comment, corrected_sql, corrected_answer, created_at
            FROM ktq.analyst_feedback
            ORDER BY created_at DESC;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(query)
                    rows = cur.fetchall()
                    return [FeedbackRecord(**row) for row in rows]
        except Exception as e:
            logger.error("Failed to fetch feedback: %s", e, exc_info=True)
            return []

    def create_scenario(
        self,
        rfx_id: int,
        title: str,
        constraints: AwardConstraints,
        result: AwardOptimizationResult,
    ) -> int | None:
        """Persist an award optimization scenario."""
        if not self._secrets.db_configured:
            return None

        query = """
            INSERT INTO ktq.award_scenarios (
                rfx_id, title, constraints, allocation, totals, status, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'draft', NOW()
            )
            RETURNING id;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            rfx_id,
                            title,
                            json.dumps(constraints.model_dump()),
                            json.dumps([a.model_dump() for a in result.allocations]),
                            json.dumps({
                                "vendor_totals": [v.model_dump() for v in result.vendor_totals],
                                "total_project_spend_inr": result.total_project_spend_inr,
                                "total_savings_vs_prior_year_inr": result.total_savings_vs_prior_year_inr,
                                "excluded_vendors": result.excluded_vendors,
                                "caveats": result.caveats,
                                "has_unresolved_review_items": result.has_unresolved_review_items,
                                "review_items_requiring_acceptance": result.review_items_requiring_acceptance,
                            }),
                        ),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return row[0] if row else None
        except Exception as e:
            logger.error("Failed to create award scenario: %s", e, exc_info=True)
            return None

    def get_scenario(self, scenario_id: int) -> dict[str, Any] | None:
        """Retrieve a specific scenario by ID."""
        if not self._secrets.db_configured:
            return None

        query = """
            SELECT id, rfx_id, title, constraints, allocation, totals, status,
                   accepted_flags, created_at, finalized_at, finalized_by
            FROM ktq.award_scenarios
            WHERE id = %s;
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(query, (scenario_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("Failed to get scenario: %s", e, exc_info=True)
            return None

    def finalize_scenario(
        self,
        scenario_id: int,
        accepted_flags: list[dict[str, Any]],
        buyer_name: str = "buyer",
    ) -> bool:
        """Finalize an award scenario and record in audit log."""
        if not self._secrets.db_configured:
            return False

        scenario = self.get_scenario(scenario_id)
        if not scenario:
            return False

        update_query = """
            UPDATE ktq.award_scenarios
            SET status = 'finalized',
                accepted_flags = %s,
                finalized_at = NOW(),
                finalized_by = %s
            WHERE id = %s;
        """
        audit_query = """
            INSERT INTO ktq.analyst_audit_log (
                rfx_id, scenario_id, action, actor, detail, created_at
            ) VALUES (
                %s, %s, 'finalize_award_scenario', %s, %s, NOW()
            );
        """
        try:
            with get_db_connection(self._secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        update_query,
                        (json.dumps(accepted_flags), buyer_name, scenario_id),
                    )
                    cur.execute(
                        audit_query,
                        (
                            scenario["rfx_id"],
                            scenario_id,
                            buyer_name,
                            json.dumps({
                                "scenario_title": scenario["title"],
                                "accepted_flags": accepted_flags,
                                "totals": scenario.get("totals", {}),
                            }),
                        ),
                    )
                conn.commit()
                return True
        except Exception as e:
            logger.error("Failed to finalize scenario: %s", e, exc_info=True)
            return False
