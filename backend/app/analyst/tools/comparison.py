"""Side-by-side RFx comparison grid builder."""

from __future__ import annotations

import logging
from typing import Any
import psycopg
from psycopg.rows import dict_row

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def get_comparison_grid(rfx_id: int, secrets: AppSecrets | None = None) -> dict[str, Any]:
    """Build side-by-side comparison matrix of items x vendors with prices, states, crops, and totals."""
    secrets = secrets or AppSecrets()
    if not secrets.db_configured:
        return {"error": "Database not configured", "rfx_id": rfx_id}

    with get_db_connection(secrets) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. RFx header
            cur.execute("SELECT id, title, status, currency, response_deadline FROM ktq.rfx WHERE id = %s", (rfx_id,))
            rfx_row = cur.fetchone()
            if not rfx_row:
                return {"error": f"RFx #{rfx_id} not found", "rfx_id": rfx_id}

            # 2. RFx line items
            cur.execute(
                """
                SELECT id, item_number, description, quantity, unit, target_price, specifications
                FROM ktq.rfx_items
                WHERE rfx_id = %s
                ORDER BY item_number ASC
                """,
                (rfx_id,),
            )
            items = [dict(r) for r in cur.fetchall()]

            # 3. Vendor responses & coverage
            cur.execute(
                """
                SELECT vendor_id, vendor_name, response_id, coverage_pct, matched_items_count,
                       total_rfx_items, confident_items_count, review_items_count, missing_items_count
                FROM ktq.v_vendor_coverage
                WHERE rfx_id = %s
                ORDER BY coverage_pct DESC, vendor_name ASC
                """,
                (rfx_id,),
            )
            vendors = [dict(r) for r in cur.fetchall()]

            # 4. Detailed comparison rows
            cur.execute(
                """
                SELECT rfx_item_id, item_number, vendor_id, vendor_name, response_id, response_item_id,
                       kind, raw_price, raw_unit, raw_currency, normalized_price_inr, effective_price_inr,
                       lead_time_days, state, review_status
                FROM ktq.v_rfx_comparison
                WHERE rfx_id = %s
                """,
                (rfx_id,),
            )
            comp_rows = cur.fetchall()

            # 5. Crop links
            cur.execute(
                """
                SELECT ic.item_id, ic.id as crop_id
                FROM ktq.item_crops ic
                JOIN ktq.response_items ri ON ic.item_id = ri.id
                JOIN ktq.vendor_responses vr ON ri.response_id = vr.id
                WHERE vr.rfx_id = %s
                """,
                (rfx_id,),
            )
            crop_map = {row["item_id"]: f"/api/response-items/{row['item_id']}/crop" for row in cur.fetchall()}

            # 6. Open flags map
            cur.execute(
                """
                SELECT item_id, code, severity, message
                FROM ktq.v_open_flags
                WHERE rfx_id = %s AND item_id IS NOT NULL
                """,
                (rfx_id,),
            )
            flags_map: dict[int, list[str]] = {}
            for f in cur.fetchall():
                iid = f["item_id"]
                flags_map.setdefault(iid, []).append(f"{f['code']}: {f['message']}")

            # 7. Questionnaire answers
            cur.execute(
                """
                SELECT vr.vendor_id, rq.question_text, rq.is_knockout, ra.pass_fail, ra.reason
                FROM ktq.response_answers ra
                JOIN ktq.vendor_responses vr ON ra.response_id = vr.id
                JOIN ktq.rfx_questions rq ON ra.rfx_question_id = rq.id
                WHERE vr.rfx_id = %s AND vr.is_current = TRUE
                ORDER BY vr.vendor_id, rq.id
                """,
                (rfx_id,),
            )
            questionnaire_by_vendor: dict[int, list[dict[str, Any]]] = {}
            for q in cur.fetchall():
                vid = q["vendor_id"]
                questionnaire_by_vendor.setdefault(vid, []).append({
                    "question_text": q["question_text"],
                    "is_knockout": q["is_knockout"],
                    "pass_fail": q["pass_fail"],
                    "reason": q["reason"],
                })

            # 8. Document list
            cur.execute(
                """
                SELECT rd.id, rd.response_id, COALESCE(v.name, vr.sender_name) as vendor_name,
                       rd.filename, rd.mime, rd.size_bytes, rd.created_at
                FROM ktq.response_documents rd
                JOIN ktq.vendor_responses vr ON rd.response_id = vr.id
                LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
                WHERE vr.rfx_id = %s AND vr.is_current = TRUE
                ORDER BY rd.created_at DESC
                """,
                (rfx_id,),
            )
            docs = [
                {
                    "id": d["id"],
                    "response_id": d["response_id"],
                    "vendor_name": d["vendor_name"],
                    "filename": d["filename"],
                    "mime": d["mime"],
                    "size_bytes": d["size_bytes"],
                    "download_url": f"/api/vendor-responses/{d['response_id']}/documents/{d['id']}/raw",
                }
                for d in cur.fetchall()
            ]

    # Find minimum price per line item (L1)
    min_price_by_item: dict[int, float] = {}
    for r in comp_rows:
        item_id = r["rfx_item_id"]
        eff_price = float(r["effective_price_inr"]) if r["effective_price_inr"] is not None else None
        if eff_price is not None and eff_price > 0:
            if item_id not in min_price_by_item or eff_price < min_price_by_item[item_id]:
                min_price_by_item[item_id] = eff_price

    # Construct the 2D grid: item_id -> vendor_id -> cell_data
    grid: dict[int, dict[int, dict[str, Any]]] = {}
    vendor_spend_totals: dict[int, float] = {}
    vendor_quoted_counts: dict[int, int] = {}

    for r in comp_rows:
        item_id = r["rfx_item_id"]
        vid = r["vendor_id"]
        eff_price = float(r["effective_price_inr"]) if r["effective_price_inr"] is not None else None
        resp_item_id = r["response_item_id"]

        is_l1 = False
        if eff_price is not None and item_id in min_price_by_item:
            is_l1 = abs(eff_price - min_price_by_item[item_id]) < 1e-4

        # Track total spend for this vendor if quoted
        if eff_price is not None:
            # Find item quantity
            qty = 1.0
            for it in items:
                if it["id"] == item_id:
                    qty = float(it["quantity"] or 1.0)
                    break
            vendor_spend_totals[vid] = vendor_spend_totals.get(vid, 0.0) + (eff_price * qty)
            vendor_quoted_counts[vid] = vendor_quoted_counts.get(vid, 0) + 1

        cell = {
            "response_item_id": resp_item_id,
            "kind": r["kind"],
            "raw_price": float(r["raw_price"]) if r["raw_price"] is not None else None,
            "raw_unit": r["raw_unit"],
            "raw_currency": r["raw_currency"],
            "normalized_price_inr": float(r["normalized_price_inr"]) if r["normalized_price_inr"] is not None else None,
            "effective_price_inr": eff_price,
            "lead_time_days": r["lead_time_days"],
            "state": r["state"],
            "review_status": r["review_status"],
            "is_l1": is_l1,
            "crop_url": crop_map.get(resp_item_id),
            "flags": flags_map.get(resp_item_id, []),
        }

        grid.setdefault(item_id, {})[vid] = cell

    # Build totals with explicit coverage labels
    total_rfx_items_count = len(items)
    totals_with_coverage = []
    for v in vendors:
        vid = v["vendor_id"]
        vname = v["vendor_name"]
        cov_pct = float(v.get("coverage_pct") or 0.0)
        quoted_cnt = vendor_quoted_counts.get(vid, int(v.get("matched_items_count") or 0))
        spend = vendor_spend_totals.get(vid, 0.0)
        
        cov_label = "Full Coverage (100%)" if cov_pct >= 100.0 else f"Partial Coverage ({quoted_cnt}/{total_rfx_items_count} items - {cov_pct:.0f}%)"
        totals_with_coverage.append({
            "vendor_id": vid,
            "vendor_name": vname,
            "total_spend_inr": round(spend, 2),
            "items_quoted": quoted_cnt,
            "total_rfx_items": total_rfx_items_count,
            "coverage_pct": cov_pct,
            "coverage_label": cov_label,
            "is_comparable": cov_pct >= 100.0,
        })

    return {
        "rfx": dict(rfx_row),
        "items": items,
        "vendors": vendors,
        "grid": grid,
        "totals_with_coverage": totals_with_coverage,
        "questionnaire_summary": questionnaire_by_vendor,
        "documents": docs,
    }
