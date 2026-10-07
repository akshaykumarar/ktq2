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
    # ── Ready-Made Comparison Widgets (Pure SQL / Deterministic - No AI) ─────
    # Widget 1: L1 Best Price Summary & Savings
    l1_items_widget = []
    total_target_spend = 0.0
    total_optimal_spend = 0.0
    vendor_wins_count: dict[str, int] = {}
    vendor_wins_spend: dict[str, float] = {}

    for it in items:
        iid = it["id"]
        qty = float(it.get("quantity") or 1.0)
        target = float(it["target_price"]) if it.get("target_price") is not None else None
        if target is not None:
            total_target_spend += (target * qty)

        # Find L1 vendor for this item
        l1_price = min_price_by_item.get(iid)
        l1_vendor_name = None
        if l1_price is not None:
            for vid, cell in grid.get(iid, {}).items():
                if cell.get("is_l1"):
                    # Find vendor name
                    for v in vendors:
                        if v["vendor_id"] == vid:
                            l1_vendor_name = v["vendor_name"]
                            break
                    break

        item_optimal_spend = (l1_price * qty) if l1_price is not None else 0.0
        total_optimal_spend += item_optimal_spend

        unit_savings = (target - l1_price) if (target is not None and l1_price is not None) else None
        total_savings = (unit_savings * qty) if unit_savings is not None else None
        savings_pct = round((unit_savings / target * 100.0), 1) if (unit_savings is not None and target and target > 0) else None

        if l1_vendor_name:
            vendor_wins_count[l1_vendor_name] = vendor_wins_count.get(l1_vendor_name, 0) + 1
            vendor_wins_spend[l1_vendor_name] = vendor_wins_spend.get(l1_vendor_name, 0.0) + item_optimal_spend

        l1_items_widget.append({
            "item_id": iid,
            "item_number": it["item_number"],
            "description": it["description"],
            "quantity": qty,
            "unit": it.get("unit", "pcs"),
            "target_price": target,
            "l1_vendor_name": l1_vendor_name or "Not Quoted",
            "l1_unit_price": l1_price,
            "total_l1_spend": round(item_optimal_spend, 2) if l1_price is not None else None,
            "unit_savings": round(unit_savings, 2) if unit_savings is not None else None,
            "total_savings": round(total_savings, 2) if total_savings is not None else None,
            "savings_pct": savings_pct,
        })

    # Widget 2: Price Spread & Bidder Variance per Line Item
    price_spread_widget = []
    for it in items:
        iid = it["id"]
        prices = [
            float(c["effective_price_inr"])
            for c in grid.get(iid, {}).values()
            if c.get("effective_price_inr") is not None and c.get("effective_price_inr") > 0
        ]
        bidders = len(prices)
        p_min = min(prices) if prices else None
        p_max = max(prices) if prices else None
        spread = (p_max - p_min) if (p_min is not None and p_max is not None) else None
        spread_pct = round((spread / p_min * 100.0), 1) if (spread is not None and p_min and p_min > 0) else 0.0

        price_spread_widget.append({
            "item_id": iid,
            "item_number": it["item_number"],
            "description": it["description"],
            "bidders_count": bidders,
            "min_price": p_min,
            "max_price": p_max,
            "spread_inr": round(spread, 2) if spread is not None else None,
            "spread_pct": spread_pct,
        })

    # Widget 3: Vendor Win Count (L1 Leaderboard)
    leaderboard_widget = []
    for v in vendors:
        vname = v["vendor_name"]
        wins = vendor_wins_count.get(vname, 0)
        spend_share = vendor_wins_spend.get(vname, 0.0)
        win_pct = round(wins / total_rfx_items_count * 100.0, 1) if total_rfx_items_count > 0 else 0.0
        leaderboard_widget.append({
            "vendor_id": v["vendor_id"],
            "vendor_name": vname,
            "items_won": wins,
            "total_items": total_rfx_items_count,
            "win_pct": win_pct,
            "l1_spend_share_inr": round(spend_share, 2),
        })
    leaderboard_widget.sort(key=lambda x: x["items_won"], reverse=True)

    # Widget 4: Overall Basket Spend Summary
    potential_savings_val = (total_target_spend - total_optimal_spend) if total_target_spend > 0 else 0.0
    potential_savings_pct = round((potential_savings_val / total_target_spend * 100.0), 1) if total_target_spend > 0 else 0.0

    basket_summary_widget = {
        "total_target_spend_inr": round(total_target_spend, 2),
        "optimal_basket_spend_inr": round(total_optimal_spend, 2),
        "potential_savings_inr": round(potential_savings_val, 2),
        "potential_savings_pct": potential_savings_pct,
        "total_line_items": total_rfx_items_count,
        "total_vendors_participating": len(vendors),
    }

    # Standard SQL scripts documentation
    standard_sql_scripts = {
        "l1_summary_sql": "SELECT ri.item_number, ri.description, ri.quantity, ri.target_price, c.vendor_name as l1_vendor, MIN(c.effective_price_inr) as l1_price FROM ktq.rfx_items ri JOIN ktq.v_rfx_comparison c ON ri.id = c.rfx_item_id WHERE c.rfx_id = :rfx_id GROUP BY ri.id, ri.item_number, ri.description, ri.quantity, ri.target_price, c.vendor_name",
        "price_spread_sql": "SELECT item_number, description, COUNT(DISTINCT vendor_id) as bidder_count, MIN(effective_price_inr) as min_price, MAX(effective_price_inr) as max_price, (MAX(effective_price_inr) - MIN(effective_price_inr)) as price_spread FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL GROUP BY item_number, description ORDER BY item_number",
        "vendor_coverage_sql": "SELECT vendor_name, coverage_pct, matched_items_count, total_rfx_items, confident_items_count, review_items_count FROM ktq.v_vendor_coverage WHERE rfx_id = :rfx_id ORDER BY coverage_pct DESC",
    }

    widgets = {
        "basket_summary": basket_summary_widget,
        "l1_items": l1_items_widget,
        "price_spread": price_spread_widget,
        "vendor_leaderboard": leaderboard_widget,
        "sql_scripts": standard_sql_scripts,
    }

    return {
        "rfx": dict(rfx_row),
        "items": items,
        "vendors": vendors,
        "grid": grid,
        "totals_with_coverage": totals_with_coverage,
        "questionnaire_summary": questionnaire_by_vendor,
        "documents": docs,
        "widgets": widgets,
    }
