"""Trust & Risk calculation tool auditing vendor reliability, coverage, price confidence, and open flags."""

from __future__ import annotations

import logging
from typing import Any
from psycopg.rows import dict_row

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection
from backend.app.analyst.models import (
    TrustReportResult,
    VendorTrustSummary,
)

logger = logging.getLogger(__name__)


def compute_trust_report(rfx_id: int, secrets: AppSecrets | None = None) -> TrustReportResult:
    """Compute comprehensive trust and risk metrics for all vendors participating in an RFx."""
    secrets = secrets or AppSecrets()
    if not secrets.db_configured:
        return TrustReportResult(
            rfx_id=rfx_id,
            rfx_title="RFx Project",
            overall_confidence="low",
            vendors=[],
            critical_open_flags=[],
            recommendations=["Database connection is not configured."]
        )

    # 1. Fetch RFx info
    rfx_title = f"RFx #{rfx_id}"
    with get_db_connection(secrets) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, title FROM ktq.rfx WHERE id = %s", (rfx_id,))
            rfx_row = cur.fetchone()
            if rfx_row:
                rfx_title = rfx_row["title"]

            # 2. Fetch vendor coverage rows
            cur.execute(
                """
                SELECT vendor_id, vendor_name, total_rfx_items, matched_items_count, coverage_pct,
                       confident_items_count, review_items_count, missing_items_count,
                       min_normalized_price_inr, max_normalized_price_inr, avg_normalized_price_inr
                FROM ktq.v_vendor_coverage
                WHERE rfx_id = %s
                ORDER BY coverage_pct DESC, vendor_name ASC
                """,
                (rfx_id,),
            )
            coverage_rows = cur.fetchall()

            # 3. Fetch open flags
            cur.execute(
                """
                SELECT flag_id, vendor_name, item_id, code, severity, message
                FROM ktq.v_open_flags
                WHERE rfx_id = %s
                ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END
                """,
                (rfx_id,),
            )
            open_flags = cur.fetchall()

            # 4. Fetch questionnaire knockout statuses
            cur.execute(
                """
                SELECT vr.vendor_id,
                       COUNT(ra.id) FILTER (WHERE rq.is_knockout = TRUE AND ra.pass_fail = 'fail') as knockout_fails,
                       COUNT(ra.id) FILTER (WHERE rq.is_knockout = TRUE AND ra.pass_fail IN ('unanswered', 'needs_review')) as knockout_unresolved,
                       COUNT(ra.id) FILTER (WHERE rq.is_knockout = TRUE) as total_knockouts
                FROM ktq.vendor_responses vr
                LEFT JOIN ktq.response_answers ra ON ra.response_id = vr.id
                LEFT JOIN ktq.rfx_questions rq ON ra.rfx_question_id = rq.id
                WHERE vr.rfx_id = %s AND vr.is_current = TRUE
                GROUP BY vr.vendor_id
                """,
                (rfx_id,),
            )
            knockout_rows = {row["vendor_id"]: row for row in cur.fetchall()}

            # 5. Fetch money at risk (sum of effective_price * qty for REVIEW state lines)
            cur.execute(
                """
                SELECT vendor_id,
                       SUM(rfx_quantity * effective_price_inr) as money_at_risk
                FROM ktq.v_rfx_comparison
                WHERE rfx_id = %s AND state = 'REVIEW' AND effective_price_inr IS NOT NULL
                GROUP BY vendor_id
                """,
                (rfx_id,),
            )
            risk_rows = {row["vendor_id"]: float(row["money_at_risk"] or 0.0) for row in cur.fetchall()}

            # 6. Fetch total quoted spend per vendor
            cur.execute(
                """
                SELECT vendor_id,
                       SUM(rfx_quantity * effective_price_inr) as total_spend
                FROM ktq.v_rfx_comparison
                WHERE rfx_id = %s AND effective_price_inr IS NOT NULL
                GROUP BY vendor_id
                """,
                (rfx_id,),
            )
            spend_rows = {row["vendor_id"]: float(row["total_spend"] or 0.0) for row in cur.fetchall()}

    # Aggregate vendor trust summaries
    vendor_summaries: list[VendorTrustSummary] = []
    has_critical = False

    for cov in coverage_rows:
        vid = cov["vendor_id"]
        vname = cov["vendor_name"]
        cov_pct = float(cov["coverage_pct"] or 0.0)
        tot_items = int(cov["total_rfx_items"] or 0)
        matched = int(cov["matched_items_count"] or 0)
        conf_count = int(cov["confident_items_count"] or 0)
        rev_count = int(cov["review_items_count"] or 0)
        miss_count = int(cov["missing_items_count"] or 0)

        # Flags for this vendor
        v_flags = [f for f in open_flags if f.get("vendor_name") == vname]
        crit_count = sum(1 for f in v_flags if f.get("severity") == "critical")
        warn_count = sum(1 for f in v_flags if f.get("severity") == "warning")
        if crit_count > 0:
            has_critical = True

        # Knockout status
        ko_data = knockout_rows.get(vid, {})
        if ko_data.get("total_knockouts", 0) == 0:
            ko_status = "no_questions"
        elif ko_data.get("knockout_fails", 0) > 0:
            ko_status = "failed"
        elif ko_data.get("knockout_unresolved", 0) > 0:
            ko_status = "unresolved"
        else:
            ko_status = "passed"

        # Trust score calculation: base 100, penalized by missing coverage, review prices, flags
        # Base coverage factor: up to 40 pts
        coverage_score = (min(cov_pct, 100.0) / 100.0) * 40.0
        # Price quality factor: up to 30 pts based on % confident vs review
        price_score = 30.0 * (conf_count / max(matched, 1)) if matched > 0 else 0.0
        # Flags deduction: up to 20 pts deducted
        flag_penalty = min(crit_count * 10.0 + warn_count * 3.0, 20.0)
        # Knockout factor: 10 pts
        ko_score = 10.0 if ko_status in ("passed", "no_questions") else (5.0 if ko_status == "unresolved" else 0.0)

        trust_score = max(0.0, min(100.0, coverage_score + price_score + ko_score - flag_penalty))
        money_risk = risk_rows.get(vid, 0.0)
        total_spend = spend_rows.get(vid, 0.0)

        # Risk level classification
        if trust_score >= 80.0:
            risk_level = "Low Risk"
        elif trust_score >= 50.0:
            risk_level = "Medium Risk"
        else:
            risk_level = "High Risk"

        # Structured reasoning narrative
        reasoning_parts = [
            f"Coverage: {cov_pct:.0f}% ({matched}/{tot_items} items, +{coverage_score:.1f} pts)",
        ]
        if matched > 0:
            conf_pct = (conf_count / matched) * 100.0
            reasoning_parts.append(f"Pricing: {conf_count} confident / {rev_count} review ({conf_pct:.0f}%, +{price_score:.1f} pts)")
        else:
            reasoning_parts.append("Pricing: 0 items quoted (+0 pts)")

        if ko_status in ("passed", "no_questions"):
            reasoning_parts.append("Knockouts: Cleared (+10 pts)")
        elif ko_status == "unresolved":
            reasoning_parts.append("Knockouts: Unresolved (+5 pts)")
        else:
            reasoning_parts.append("Knockouts: Failed criteria (0 pts)")

        if flag_penalty > 0:
            reasoning_parts.append(f"Penalties: -{flag_penalty:.0f} pts ({crit_count} critical, {warn_count} warning flags)")
        else:
            reasoning_parts.append("Penalties: 0 deductions (Clean)")

        score_reasoning = " • ".join(reasoning_parts)

        vendor_summaries.append(
            VendorTrustSummary(
                vendor_id=vid,
                vendor_name=vname,
                coverage_pct=cov_pct,
                total_items=tot_items,
                matched_items=matched,
                confident_items=conf_count,
                review_items=rev_count,
                missing_items=miss_count,
                critical_flags_count=crit_count,
                warning_flags_count=warn_count,
                knockouts_status=ko_status,
                trust_score=round(trust_score, 1),
                score_reasoning=score_reasoning,
                risk_level=risk_level,
                total_quoted_spend_inr=round(total_spend, 2),
                money_at_risk_inr=money_risk,
                flags_list=[f"{f.get('code')}: {f.get('message')}" for f in v_flags],
            )
        )

    # Sort vendors by money at risk descending, then trust score ascending
    vendor_summaries.sort(key=lambda x: (x.money_at_risk_inr, -x.trust_score), reverse=True)

    recommendations = []
    if has_critical:
        recommendations.append("Resolve open critical flags before making final award decisions.")
    if any(v.review_items > 0 for v in vendor_summaries):
        recommendations.append("Review flagged quotation items under the 'Review' tab to verify OCR extraction confidence.")
    if any(v.knockouts_status == "unresolved" for v in vendor_summaries):
        recommendations.append("Follow up on unresolved knockout questionnaire responses to ensure regulatory/quality compliance.")
    if not recommendations:
        recommendations.append("All quotations verified with high confidence. Proceed with award optimization.")

    overall_conf = "low" if has_critical else ("medium" if any(v.review_items > 0 for v in vendor_summaries) else "high")

    return TrustReportResult(
        rfx_id=rfx_id,
        rfx_title=rfx_title,
        overall_confidence=overall_conf,
        vendors=vendor_summaries,
        critical_open_flags=[dict(f) for f in open_flags if f.get("severity") == "critical"],
        recommendations=recommendations,
    )
