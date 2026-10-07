"""Repository layer for RFI and packaging line items in PostgreSQL with fallback."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection

logger = logging.getLogger(__name__)

# Fallback store used when database is unconfigured or offline
_MEMORY_RFIS: dict[int, dict[str, Any]] = {}
_MEMORY_COUNTER = 1000


def _serialize_row(row_dict: dict[str, Any]) -> dict[str, Any]:
    """Ensure dates and datetimes are ISO strings for API serialization."""
    out = {}
    for k, v in row_dict.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


class RFIRepository:
    """PostgreSQL repository for managing RFI records and packaging items."""

    def __init__(self, secrets: AppSecrets) -> None:
        self.secrets = secrets

    def create(self, rfi_data: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a new RFI record and associated line items in a transaction."""
        now = datetime.now(timezone.utc)
        title = rfi_data.get("title") or "Packaging RFI"
        category = rfi_data.get("category") or "Corrugated packaging"
        scope = rfi_data.get("scope")
        currency = rfi_data.get("currency") or "INR"
        payment_terms = rfi_data.get("payment_terms") or "Net 30"
        delivery_terms = rfi_data.get("delivery_terms") or "Delivered to warehouse dock"
        validity_days = rfi_data.get("validity_days", 30)
        response_deadline = rfi_data.get("response_deadline")
        status = rfi_data.get("status", "draft")
        source = rfi_data.get("source", "text_intake")

        if self.secrets.db_configured:
            try:
                with get_db_connection(self.secrets) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO rfx (
                                title, category, scope, currency, payment_terms,
                                delivery_terms, validity_days, response_deadline,
                                status, source, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id, title, category, scope, currency, payment_terms,
                                      delivery_terms, validity_days, response_deadline,
                                      status, source, triggered_at, created_at, updated_at;
                            """,
                            (
                                title,
                                category,
                                scope,
                                currency,
                                payment_terms,
                                delivery_terms,
                                validity_days,
                                response_deadline,
                                status,
                                source,
                                now,
                                now,
                            ),
                        )
                        rfx_row = cur.fetchone()
                        rfx_id = rfx_row[0]

                        # Insert line items
                        inserted_items = []
                        for idx, item in enumerate(items, start=1):
                            item_num = item.get("item_number", idx)
                            item_code = item.get("item_code")
                            desc = item.get("description") or f"Item {item_num}"
                            qty = item.get("quantity") or 1
                            unit = item.get("unit") or "pcs"
                            material = item.get("material")
                            dimensions = json.dumps(item.get("dimensions") or {})
                            specs = item.get("specifications")
                            target_price = item.get("target_price")
                            item_curr = item.get("currency") or currency
                            req_date = item.get("required_date")

                            cur.execute(
                                """
                                INSERT INTO rfx_items (
                                    rfx_id, item_number, item_code, description,
                                    quantity, unit, material, dimensions,
                                    specifications, target_price, currency,
                                    required_date, created_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id, item_number, item_code, description,
                                          quantity, unit, material, dimensions,
                                          specifications, target_price, currency,
                                          required_date, created_at;
                                """,
                                (
                                    rfx_id,
                                    item_num,
                                    item_code,
                                    desc,
                                    qty,
                                    unit,
                                    material,
                                    dimensions,
                                    specs,
                                    target_price,
                                    item_curr,
                                    req_date,
                                    now,
                                ),
                            )
                            item_row = cur.fetchone()
                            inserted_items.append(
                                {
                                    "id": item_row[0],
                                    "rfx_id": rfx_id,
                                    "item_number": item_row[1],
                                    "item_code": item_row[2],
                                    "description": item_row[3],
                                    "quantity": float(item_row[4]) if item_row[4] is not None else None,
                                    "unit": item_row[5],
                                    "material": item_row[6],
                                    "dimensions": item_row[7] if isinstance(item_row[7], dict) else json.loads(item_row[7] or "{}"),
                                    "specifications": item_row[8],
                                    "target_price": float(item_row[9]) if item_row[9] is not None else None,
                                    "currency": item_row[10],
                                    "required_date": item_row[11].isoformat() if item_row[11] else None,
                                    "created_at": item_row[12].isoformat() if item_row[12] else None,
                                }
                            )

                        # Insert activity log
                        cur.execute(
                            """
                            INSERT INTO rfx_activity (rfx_id, activity_type, description, performed_by, created_at)
                            VALUES (%s, %s, %s, %s, %s);
                            """,
                            (rfx_id, "rfi_created", f"Created packaging RFI with {len(items)} items", "system", now),
                        )
                        conn.commit()

                        return {
                            "id": rfx_id,
                            "title": rfx_row[1],
                            "category": rfx_row[2],
                            "scope": rfx_row[3],
                            "currency": rfx_row[4],
                            "payment_terms": rfx_row[5],
                            "delivery_terms": rfx_row[6],
                            "validity_days": rfx_row[7],
                            "response_deadline": rfx_row[8].isoformat() if rfx_row[8] else None,
                            "status": rfx_row[9],
                            "source": rfx_row[10],
                            "triggered_at": rfx_row[11].isoformat() if rfx_row[11] else None,
                            "created_at": rfx_row[12].isoformat() if rfx_row[12] else None,
                            "updated_at": rfx_row[13].isoformat() if rfx_row[13] else None,
                            "items": inserted_items,
                        }
            except Exception as exc:
                logger.warning("Database write failed, falling back to memory store: %s", exc)

        # In-memory fallback
        global _MEMORY_COUNTER
        _MEMORY_COUNTER += 1
        mem_id = _MEMORY_COUNTER
        mem_items = []
        for idx, it in enumerate(items, start=1):
            mem_items.append(
                {
                    "id": idx,
                    "rfx_id": mem_id,
                    "item_number": it.get("item_number", idx),
                    "item_code": it.get("item_code"),
                    "description": it.get("description", f"Item {idx}"),
                    "quantity": it.get("quantity", 1),
                    "unit": it.get("unit", "pcs"),
                    "material": it.get("material"),
                    "dimensions": it.get("dimensions") or {},
                    "specifications": it.get("specifications"),
                    "target_price": it.get("target_price"),
                    "currency": it.get("currency", currency),
                    "required_date": str(it.get("required_date")) if it.get("required_date") else None,
                    "created_at": now.isoformat(),
                }
            )

        record = {
            "id": mem_id,
            "title": title,
            "category": category,
            "scope": scope,
            "currency": currency,
            "payment_terms": payment_terms,
            "delivery_terms": delivery_terms,
            "validity_days": validity_days,
            "response_deadline": str(response_deadline) if response_deadline else None,
            "status": status,
            "source": source,
            "triggered_at": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "items": mem_items,
        }
        _MEMORY_RFIS[mem_id] = record
        return record

    def get_by_id(self, rfi_id: int) -> dict[str, Any] | None:
        """Fetch RFI with line items by integer ID."""
        if self.secrets.db_configured:
            try:
                with get_db_connection(self.secrets) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id, title, category, scope, currency, payment_terms,
                                   delivery_terms, validity_days, response_deadline,
                                   status, source, triggered_at, created_at, updated_at
                            FROM rfx
                            WHERE id = %s;
                            """,
                            (rfi_id,),
                        )
                        rfx_row = cur.fetchone()
                        if not rfx_row:
                            return _MEMORY_RFIS.get(rfi_id)

                        cur.execute(
                            """
                            SELECT id, item_number, item_code, description,
                                   quantity, unit, material, dimensions,
                                   specifications, target_price, currency,
                                   required_date, created_at
                            FROM rfx_items
                            WHERE rfx_id = %s
                            ORDER BY item_number ASC;
                            """,
                            (rfi_id,),
                        )
                        items = []
                        for item_row in cur.fetchall():
                            dims = item_row[7]
                            if isinstance(dims, str):
                                try:
                                    dims = json.loads(dims)
                                except Exception:
                                    dims = {}
                            items.append(
                                {
                                    "id": item_row[0],
                                    "rfx_id": rfi_id,
                                    "item_number": item_row[1],
                                    "item_code": item_row[2],
                                    "description": item_row[3],
                                    "quantity": float(item_row[4]) if item_row[4] is not None else None,
                                    "unit": item_row[5],
                                    "material": item_row[6],
                                    "dimensions": dims or {},
                                    "specifications": item_row[8],
                                    "target_price": float(item_row[9]) if item_row[9] is not None else None,
                                    "currency": item_row[10],
                                    "required_date": item_row[11].isoformat() if item_row[11] else None,
                                    "created_at": item_row[12].isoformat() if item_row[12] else None,
                                }
                            )

                        return {
                            "id": rfx_row[0],
                            "title": rfx_row[1],
                            "category": rfx_row[2],
                            "scope": rfx_row[3],
                            "currency": rfx_row[4],
                            "payment_terms": rfx_row[5],
                            "delivery_terms": rfx_row[6],
                            "validity_days": rfx_row[7],
                            "response_deadline": rfx_row[8].isoformat() if rfx_row[8] else None,
                            "status": rfx_row[9],
                            "source": rfx_row[10],
                            "triggered_at": rfx_row[11].isoformat() if rfx_row[11] else None,
                            "created_at": rfx_row[12].isoformat() if rfx_row[12] else None,
                            "updated_at": rfx_row[13].isoformat() if rfx_row[13] else None,
                            "items": items,
                        }
            except Exception as exc:
                logger.warning("Database query failed: %s", exc)

        return _MEMORY_RFIS.get(rfi_id)

    def update(self, rfi_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update allowed fields of an RFI."""
        allowed_fields = {
            "title",
            "category",
            "scope",
            "currency",
            "payment_terms",
            "delivery_terms",
            "validity_days",
            "response_deadline",
            "status",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered:
            return self.get_by_id(rfi_id)

        now = datetime.now(timezone.utc)
        filtered["updated_at"] = now

        if self.secrets.db_configured:
            try:
                with get_db_connection(self.secrets) as conn:
                    with conn.cursor() as cur:
                        set_clauses = [f"{k} = %s" for k in filtered.keys()]
                        sql = f"UPDATE rfx SET {', '.join(set_clauses)} WHERE id = %s RETURNING id;"
                        params = list(filtered.values()) + [rfi_id]
                        cur.execute(sql, params)
                        if cur.fetchone():
                            cur.execute(
                                """
                                INSERT INTO rfx_activity (rfx_id, activity_type, description, performed_by, created_at)
                                VALUES (%s, %s, %s, %s, %s);
                                """,
                                (rfi_id, "rfi_updated", f"Updated fields: {list(filtered.keys())}", "system", now),
                            )
                            conn.commit()
                            return self.get_by_id(rfi_id)
            except Exception as exc:
                logger.warning("Database update failed, using memory fallback: %s", exc)

        if rfi_id in _MEMORY_RFIS:
            record = _MEMORY_RFIS[rfi_id]
            for k, v in filtered.items():
                record[k] = v.isoformat() if isinstance(v, (datetime, date)) else v
            return record
        return None

    def trigger(self, rfi_id: int) -> dict[str, Any]:
        """Trigger an RFI, transitioning status to 'triggered' and setting timestamp."""
        existing = self.get_by_id(rfi_id)
        if not existing:
            raise ValueError(f"RFI {rfi_id} not found.")

        current_status = existing.get("status", "").lower()
        if current_status not in {"draft", "ready"}:
            raise ValueError(
                f"Cannot trigger RFI with status '{existing.get('status')}'. Must be 'draft' or 'ready'."
            )

        now = datetime.now(timezone.utc)
        if self.secrets.db_configured:
            try:
                with get_db_connection(self.secrets) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE rfx
                            SET status = 'triggered', triggered_at = %s, updated_at = %s
                            WHERE id = %s
                            RETURNING id;
                            """,
                            (now, now, rfi_id),
                        )
                        if cur.fetchone():
                            cur.execute(
                                """
                                INSERT INTO rfx_activity (rfx_id, activity_type, description, performed_by, created_at)
                                VALUES (%s, %s, %s, %s, %s);
                                """,
                                (rfi_id, "rfi_triggered", "RFI triggered to suppliers", "system", now),
                            )
                            conn.commit()
                            return self.get_by_id(rfi_id) or existing
            except Exception as exc:
                logger.warning("Database trigger failed, using memory fallback: %s", exc)

        if rfi_id in _MEMORY_RFIS:
            _MEMORY_RFIS[rfi_id]["status"] = "triggered"
            _MEMORY_RFIS[rfi_id]["triggered_at"] = now.isoformat()
            _MEMORY_RFIS[rfi_id]["updated_at"] = now.isoformat()
            return _MEMORY_RFIS[rfi_id]

        raise ValueError(f"RFI {rfi_id} not found.")
