"""Deterministic Award Optimization and Allocation Engine in pure Python."""

from __future__ import annotations

import logging
from typing import Any
from psycopg.rows import dict_row

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection
from backend.app.analyst.models import (
    AwardConstraints,
    AwardOptimizationResult,
    LineAllocation,
    VendorAllocationTotal,
)

logger = logging.getLogger(__name__)


def optimize_award(
    rfx_id: int,
    constraints: AwardConstraints | None = None,
    secrets: AppSecrets | None = None,
) -> AwardOptimizationResult:
    """Compute deterministic award allocation based on multi-criteria procurement constraints."""
    constraints = constraints or AwardConstraints()
    secrets = secrets or AppSecrets()

    if not secrets.db_configured:
        return AwardOptimizationResult(
            allocations=[],
            vendor_totals=[],
            total_project_spend_inr=0.0,
            excluded_vendors={},
            caveats=["Database connection not configured."],
        )

    with get_db_connection(secrets) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Fetch line items
            cur.execute(
                """
                SELECT id, item_number, description, quantity, unit, target_price
                FROM ktq.rfx_items
                WHERE rfx_id = %s
                ORDER BY item_number ASC
                """,
                (rfx_id,),
            )
            rfx_items = cur.fetchall()

            # 2. Fetch vendor responses
            cur.execute(
                """
                SELECT vr.id as response_id, vr.vendor_id, COALESCE(v.name, vr.sender_name) as vendor_name
                FROM ktq.vendor_responses vr
                LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
                WHERE vr.rfx_id = %s AND vr.is_current = TRUE
                """,
                (rfx_id,),
            )
            vendors = cur.fetchall()
            vendor_name_map = {v["vendor_id"]: v["vendor_name"] for v in vendors}

            # 3. Fetch knockout questionnaire answers
            cur.execute(
                """
                SELECT vr.vendor_id, rq.question_text, ra.pass_fail
                FROM ktq.response_answers ra
                JOIN ktq.vendor_responses vr ON ra.response_id = vr.id
                JOIN ktq.rfx_questions rq ON ra.rfx_question_id = rq.id
                WHERE vr.rfx_id = %s AND vr.is_current = TRUE AND rq.is_knockout = TRUE
                """,
                (rfx_id,),
            )
            ko_rows = cur.fetchall()

            # 4. Fetch all quotation comparison lines
            cur.execute(
                """
                SELECT rxi.id as rfx_item_id, rxi.item_number, rxi.quantity, rxi.unit, rxi.description,
                       rxi.target_price, vr.vendor_id, COALESCE(v.name, vr.sender_name) as vendor_name,
                       ri.id as response_item_id, ri.raw_price, ri.raw_currency, ri.unit_factor,
                       ri.normalized_price_inr,
                       COALESCE((ri.buyer_override->>'corrected_price_inr')::numeric, ri.normalized_price_inr) as effective_price_inr,
                       ri.state, ri.flags
                FROM ktq.rfx_items rxi
                JOIN ktq.response_items ri ON ri.rfx_item_id = rxi.id AND ri.is_current = TRUE
                JOIN ktq.vendor_responses vr ON ri.response_id = vr.id AND vr.is_current = TRUE
                LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
                WHERE rxi.rfx_id = %s AND ri.kind = 'MATCHED'
                """,
                (rfx_id,),
            )
            quote_lines = cur.fetchall()

    # Determine vendor exclusions
    excluded_vendors: dict[str, str] = {}
    eligible_vendor_ids: set[int] = {v["vendor_id"] for v in vendors}

    # Exclusion 1: Explicit buyer excluded vendors
    for ex_id in constraints.excluded_vendor_ids:
        if ex_id in eligible_vendor_ids:
            eligible_vendor_ids.remove(ex_id)
            vname = vendor_name_map.get(ex_id, f"Vendor #{ex_id}")
            excluded_vendors[vname] = "Explicitly excluded by buyer constraint"

    # Exclusion 2: Knockout question failures
    vendor_ko_fails: dict[int, list[str]] = {}
    vendor_ko_unresolved: dict[int, list[str]] = {}
    for ko in ko_rows:
        vid = ko["vendor_id"]
        pf = (ko["pass_fail"] or "").lower()
        if pf == "fail":
            vendor_ko_fails.setdefault(vid, []).append(ko["question_text"])
        elif pf in ("unanswered", "needs_review"):
            vendor_ko_unresolved.setdefault(vid, []).append(ko["question_text"])

    if constraints.require_knockout_pass:
        for vid, fails in vendor_ko_fails.items():
            if vid in eligible_vendor_ids:
                eligible_vendor_ids.remove(vid)
                vname = vendor_name_map.get(vid, f"Vendor #{vid}")
                excluded_vendors[vname] = f"Failed knockout question(s): {'; '.join(fails)}"

    caveats: list[str] = []
    for vid, unres in vendor_ko_unresolved.items():
        vname = vendor_name_map.get(vid, f"Vendor #{vid}")
        if vid in eligible_vendor_ids:
            caveats.append(f"Vendor '{vname}' has unresolved knockout questions ({len(unres)} pending review).")

    # Organize quotes by item_id -> list of quotes
    quotes_by_item: dict[int, list[dict[str, Any]]] = {}
    for q in quote_lines:
        vid = q["vendor_id"]
        item_id = q["rfx_item_id"]
        item_num = q["item_number"]

        # Apply price overrides if specified
        override_key_num = f"{vid}:{item_num}"
        override_key_id = f"{vid}:{item_id}"
        eff_price = float(q["effective_price_inr"]) if q["effective_price_inr"] is not None else None

        if override_key_num in constraints.price_overrides:
            eff_price = float(constraints.price_overrides[override_key_num])
        elif override_key_id in constraints.price_overrides:
            eff_price = float(constraints.price_overrides[override_key_id])

        # Apply FX rate override if foreign currency
        curr = q["raw_currency"]
        if curr and curr != "INR" and curr in constraints.fx_rate_overrides:
            new_rate = constraints.fx_rate_overrides[curr]
            raw_p = float(q["raw_price"] or 0.0)
            u_factor = float(q["unit_factor"] or 1.0)
            eff_price = raw_p * new_rate * u_factor

        # Apply freight override if specified
        if constraints.freight_override_per_unit is not None and eff_price is not None:
            eff_price += float(constraints.freight_override_per_unit)

        q_copy = dict(q)
        q_copy["effective_price_inr"] = eff_price
        quotes_by_item.setdefault(item_id, []).append(q_copy)

    # Filter quotes by eligibility & review state
    allocations: list[LineAllocation] = []
    total_spend = 0.0
    total_prior_year_spend = 0.0
    review_items_awarded: list[dict[str, Any]] = []

    # Strategy 1: Single Vendor
    if constraints.strategy == "single_vendor":
        best_vendor_id: int | None = None
        best_vendor_spend = float("inf")
        best_vendor_quotes: dict[int, dict[str, Any]] = {}

        for vid in eligible_vendor_ids:
            v_quotes: dict[int, dict[str, Any]] = {}
            can_quote_all = True
            v_spend = 0.0

            for it in rfx_items:
                iid = it["id"]
                qty = float(it["quantity"] or 1.0)
                matching = [
                    q for q in quotes_by_item.get(iid, [])
                    if q["vendor_id"] == vid and q["effective_price_inr"] is not None
                ]
                if not constraints.include_review_prices:
                    matching = [q for q in matching if q["state"] == "CONFIDENT"]

                if matching:
                    best_q = min(matching, key=lambda x: x["effective_price_inr"])
                    v_quotes[iid] = best_q
                    v_spend += best_q["effective_price_inr"] * qty
                else:
                    can_quote_all = False
                    break

            if can_quote_all and v_spend < best_vendor_spend:
                best_vendor_spend = v_spend
                best_vendor_id = vid
                best_vendor_quotes = v_quotes

        if best_vendor_id is None:
            caveats.append("No single eligible vendor quoted all items under the given constraints. Falling back to cheapest per line.")
            constraints.strategy = "cheapest_per_line"
        else:
            for it in rfx_items:
                iid = it["id"]
                qty = float(it["quantity"] or 1.0)
                q = best_vendor_quotes[iid]
                unit_p = q["effective_price_inr"]
                line_tot = unit_p * qty
                total_spend += line_tot

                # Prior year target benchmark
                target_p = float(it["target_price"]) if it["target_price"] else None
                savings_vs_target = (target_p - unit_p) * qty if target_p else None

                # Find absolute L1 for this line
                all_line_quotes = [x for x in quotes_by_item.get(iid, []) if x["effective_price_inr"] is not None]
                l1_p = min((x["effective_price_inr"] for x in all_line_quotes), default=unit_p)
                savings_vs_l1 = (l1_p - unit_p) * qty

                if q["state"] == "REVIEW":
                    review_items_awarded.append({
                        "vendor_id": q["vendor_id"],
                        "vendor_name": q["vendor_name"],
                        "item_id": iid,
                        "item_number": it["item_number"],
                        "price": unit_p,
                        "reason": "Quoted price is in REVIEW state"
                    })

                allocations.append(
                    LineAllocation(
                        item_id=iid,
                        item_number=it["item_number"],
                        description=it["description"] or "",
                        quantity=qty,
                        unit=it["unit"] or "pcs",
                        awarded_vendor_id=q["vendor_id"],
                        awarded_vendor_name=q["vendor_name"],
                        unit_price_inr=unit_p,
                        total_spend_inr=line_tot,
                        state=q["state"],
                        flags=[f["code"] for f in q.get("flags", [])] if isinstance(q.get("flags"), list) else [],
                        is_l1=abs(unit_p - l1_p) < 1e-4,
                        l1_unit_price_inr=l1_p,
                        prior_year_unit_price_inr=target_p,
                        savings_vs_prior_year_inr=savings_vs_target,
                        savings_vs_l1_inr=savings_vs_l1,
                    )
                )

    # Strategy 2: Cheapest per line or Multi-Vendor Split
    if constraints.strategy in ("cheapest_per_line", "multi_vendor_split"):
        # First compute baseline unconstrained cheapest per line
        temp_allocations: list[dict[str, Any]] = []
        vendor_accumulated_spend: dict[int, float] = {}

        for it in rfx_items:
            iid = it["id"]
            qty = float(it["quantity"] or 1.0)
            eligible_quotes = [
                q for q in quotes_by_item.get(iid, [])
                if q["vendor_id"] in eligible_vendor_ids and q["effective_price_inr"] is not None
            ]
            if not constraints.include_review_prices:
                eligible_quotes = [q for q in eligible_quotes if q["state"] == "CONFIDENT"]

            if not eligible_quotes:
                # Include vendor regardless of review state if no confident exists
                eligible_quotes = [
                    q for q in quotes_by_item.get(iid, [])
                    if q["vendor_id"] in eligible_vendor_ids and q["effective_price_inr"] is not None
                ]

            if eligible_quotes:
                # Sort ascending by price
                eligible_quotes.sort(key=lambda x: x["effective_price_inr"])
                temp_allocations.append({
                    "item": it,
                    "quotes": eligible_quotes,
                })
            else:
                caveats.append(f"No valid quotes found for item #{it['item_number']} ({it['description']}).")

        # Estimate unconstrained total spend
        unconstrained_total = sum(t["quotes"][0]["effective_price_inr"] * float(t["item"]["quantity"] or 1.0) for t in temp_allocations)
        max_spend_cap = float("inf")
        if constraints.max_share_per_vendor_pct and constraints.max_share_per_vendor_pct > 0:
            max_spend_cap = unconstrained_total * (constraints.max_share_per_vendor_pct / 100.0)

        # Allocate items considering cap
        for item_entry in temp_allocations:
            it = item_entry["item"]
            quotes = item_entry["quotes"]
            iid = it["id"]
            qty = float(it["quantity"] or 1.0)

            # Pick the lowest priced quote that doesn't breach the vendor spend cap (if alternative exists)
            selected_quote = quotes[0]
            for q in quotes:
                vid = q["vendor_id"]
                current_v_spend = vendor_accumulated_spend.get(vid, 0.0)
                line_val = q["effective_price_inr"] * qty
                if current_v_spend + line_val <= max_spend_cap or q == quotes[-1]:
                    selected_quote = q
                    break

            vid = selected_quote["vendor_id"]
            unit_p = selected_quote["effective_price_inr"]
            line_tot = unit_p * qty
            vendor_accumulated_spend[vid] = vendor_accumulated_spend.get(vid, 0.0) + line_tot
            total_spend += line_tot

            target_p = float(it["target_price"]) if it["target_price"] else None
            savings_vs_target = (target_p - unit_p) * qty if target_p else None
            l1_p = quotes[0]["effective_price_inr"]
            savings_vs_l1 = (l1_p - unit_p) * qty

            if selected_quote["state"] == "REVIEW":
                review_items_awarded.append({
                    "vendor_id": vid,
                    "vendor_name": selected_quote["vendor_name"],
                    "item_id": iid,
                    "item_number": it["item_number"],
                    "price": unit_p,
                    "reason": "Quoted price is in REVIEW state"
                })

            allocations.append(
                LineAllocation(
                    item_id=iid,
                    item_number=it["item_number"],
                    description=it["description"] or "",
                    quantity=qty,
                    unit=it["unit"] or "pcs",
                    awarded_vendor_id=vid,
                    awarded_vendor_name=selected_quote["vendor_name"],
                    unit_price_inr=unit_p,
                    total_spend_inr=line_tot,
                    state=selected_quote["state"],
                    flags=[f["code"] for f in selected_quote.get("flags", [])] if isinstance(selected_quote.get("flags"), list) else [],
                    is_l1=abs(unit_p - l1_p) < 1e-4,
                    l1_unit_price_inr=l1_p,
                    prior_year_unit_price_inr=target_p,
                    savings_vs_prior_year_inr=savings_vs_target,
                    savings_vs_l1_inr=savings_vs_l1,
                )
            )

    # Compute vendor totals and percentages
    vendor_totals: list[VendorAllocationTotal] = []
    for vid, vname in vendor_name_map.items():
        v_lines = [a for a in allocations if a.awarded_vendor_id == vid]
        if v_lines:
            v_spend = sum(a.total_spend_inr for a in v_lines)
            v_share = (v_spend / total_spend * 100.0) if total_spend > 0 else 0.0
            rev_cnt = sum(1 for a in v_lines if a.state == "REVIEW")
            vendor_totals.append(
                VendorAllocationTotal(
                    vendor_id=vid,
                    vendor_name=vname,
                    allocated_items_count=len(v_lines),
                    total_spend_inr=round(v_spend, 2),
                    share_of_spend_pct=round(v_share, 2),
                    cleared_knockouts=vid not in vendor_ko_fails,
                    review_items_awarded=rev_cnt,
                )
            )

    vendor_totals.sort(key=lambda x: x.total_spend_inr, reverse=True)

    # Calculate aggregate savings vs prior year
    tot_savings_prior = sum(a.savings_vs_prior_year_inr for a in allocations if a.savings_vs_prior_year_inr is not None)

    return AwardOptimizationResult(
        allocations=allocations,
        vendor_totals=vendor_totals,
        total_project_spend_inr=round(total_spend, 2),
        total_savings_vs_prior_year_inr=round(tot_savings_prior, 2) if tot_savings_prior != 0 else None,
        excluded_vendors=excluded_vendors,
        caveats=caveats,
        has_unresolved_review_items=len(review_items_awarded) > 0,
        review_items_requiring_acceptance=review_items_awarded,
    )
