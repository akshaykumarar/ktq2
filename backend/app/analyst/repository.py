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


class AnalystRepository:
    """Manages PostgreSQL persistence for Step 3 Decision Analyst."""

    def __init__(self, secrets: AppSecrets | None = None):
        self._secrets = secrets or AppSecrets()

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
