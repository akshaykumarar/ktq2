"""Database repository for Vendor Responses, Documents, Items, Crops, and Audit Events."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def _json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _dump_json(data: Any) -> str:
    """Safely dump data to JSON string."""
    return json.dumps(data, default=_json_serial)


class VendorRepository:
    """Data access layer for vendor responses and related entities."""

    def __init__(self, secrets: AppSecrets) -> None:
        self.secrets = secrets

    # -----------------------------------------------------------------------
    # Vendor Management
    # -----------------------------------------------------------------------

    def get_vendor_by_id(self, vendor_id: int) -> dict[str, Any] | None:
        """Fetch vendor record by ID."""
        query = "SELECT id, name, email, phone, gstin, address, created_at FROM vendors WHERE id = %s"
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (vendor_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "phone": row[3],
                    "gstin": row[4],
                    "address": row[5],
                    "created_at": row[6],
                }

    def find_vendor_by_email_or_name(
        self, email: str | None, name: str | None
    ) -> dict[str, Any] | None:
        """Find vendor matching email or case-insensitive name."""
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                if email:
                    cur.execute(
                        "SELECT id, name, email, phone, gstin, address, created_at FROM vendors WHERE LOWER(email) = LOWER(%s) LIMIT 1",
                        (email.strip(),),
                    )
                    row = cur.fetchone()
                    if row:
                        return {"id": row[0], "name": row[1], "email": row[2], "phone": row[3], "gstin": row[4], "address": row[5], "created_at": row[6]}

                if name:
                    cur.execute(
                        "SELECT id, name, email, phone, gstin, address, created_at FROM vendors WHERE LOWER(name) = LOWER(%s) LIMIT 1",
                        (name.strip(),),
                    )
                    row = cur.fetchone()
                    if row:
                        return {"id": row[0], "name": row[1], "email": row[2], "phone": row[3], "gstin": row[4], "address": row[5], "created_at": row[6]}
        return None

    def create_vendor(
        self, name: str, email: str | None = None, phone: str | None = None, gstin: str | None = None, address: str | None = None
    ) -> dict[str, Any]:
        """Create a new vendor."""
        query = """
            INSERT INTO vendors (name, email, phone, gstin, address, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id, name, email, phone, gstin, address, created_at;
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (name.strip(), email.strip() if email else None, phone, gstin, address))
                row = cur.fetchone()
                conn.commit()
                return {"id": row[0], "name": row[1], "email": row[2], "phone": row[3], "gstin": row[4], "address": row[5], "created_at": row[6]}

    # -----------------------------------------------------------------------
    # Vendor Response Lifecycle & Idempotency
    # -----------------------------------------------------------------------

    def check_duplicate_response(self, file_hashes: list[str], sender_email: str | None, rfx_id: int | None) -> dict[str, Any] | None:
        """Find if a previous response with identical file hashes was submitted for the same vendor/rfx."""
        if not file_hashes:
            return None
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT vr.id, vr.status, vr.rfx_id, vr.vendor_id, vr.version
                    FROM vendor_responses vr
                    JOIN response_documents rd ON vr.id = rd.response_id
                    WHERE rd.sha256 = ANY(%s) AND vr.is_current = TRUE
                    LIMIT 1
                    """,
                    (file_hashes,),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "id": row[0],
                        "status": row[1],
                        "rfx_id": row[2],
                        "vendor_id": row[3],
                        "version": row[4],
                    }
        return None

    def get_latest_response_for_vendor_rfx(self, vendor_id: int | None, rfx_id: int | None) -> dict[str, Any] | None:
        """Get the active response for a specific vendor and RFx to handle versioning."""
        if not vendor_id or not rfx_id:
            return None
        query = """
            SELECT id, version FROM vendor_responses
            WHERE vendor_id = %s AND rfx_id = %s AND is_current = TRUE
            ORDER BY version DESC LIMIT 1
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (vendor_id, rfx_id))
                row = cur.fetchone()
                if row:
                    return {"id": row[0], "version": row[1]}
        return None

    def supersede_prior_response(self, old_response_id: int) -> None:
        """Mark a previous response as superseded (is_current = false)."""
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE vendor_responses SET is_current = FALSE, updated_at = NOW() WHERE id = %s",
                    (old_response_id,),
                )
                cur.execute(
                    "UPDATE response_items SET is_current = FALSE WHERE response_id = %s",
                    (old_response_id,),
                )
                conn.commit()

    def create_vendor_response(
        self,
        rfx_id: int | None,
        vendor_id: int | None,
        channel: str,
        subject: str | None,
        sender_name: str | None,
        sender_email: str | None,
        body_text: str | None,
        body_json: dict[str, Any] | None,
        received_at: datetime | None,
        version: int = 1,
        supersedes_response_id: int | None = None,
        rfx_resolution: dict[str, Any] | None = None,
        vendor_resolution: dict[str, Any] | None = None,
    ) -> int:
        """Persist a new vendor response header in RECEIVED state."""
        query = """
            INSERT INTO vendor_responses (
                rfx_id, vendor_id, version, supersedes_response_id, channel,
                subject, sender_name, sender_email, body_text, body_json,
                received_at, status, rfx_resolution, vendor_resolution, summary, is_current,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                COALESCE(%s, NOW()), 'received', %s, %s, '{}'::jsonb, TRUE,
                NOW(), NOW()
            ) RETURNING id;
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        rfx_id,
                        vendor_id,
                        version,
                        supersedes_response_id,
                        channel,
                        subject,
                        sender_name,
                        sender_email,
                        body_text,
                        _dump_json(body_json) if body_json else None,
                        received_at,
                        _dump_json(rfx_resolution or {}),
                        _dump_json(vendor_resolution or {}),
                    ),
                )
                row = cur.fetchone()
                conn.commit()
                return int(row[0])

    def add_response_document(
        self,
        response_id: int,
        filename: str,
        mime: str | None,
        size_bytes: int,
        sha256: str,
        raw_bytes: bytes | None,
        doc_role: str = "quotation",
    ) -> int:
        """Store original raw document bytes and metadata."""
        query = """
            INSERT INTO response_documents (
                response_id, filename, mime, size_bytes, sha256, raw_bytes, doc_role, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'received', NOW())
            RETURNING id;
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (response_id, filename, mime, size_bytes, sha256, raw_bytes, doc_role),
                )
                row = cur.fetchone()
                conn.commit()
                return int(row[0])

    def update_document_preprocessing(
        self,
        doc_id: int,
        parsed_text: str | None,
        page_count: int,
        enhanced_bytes: bytes | None,
        quality_notes: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update parsed content and image enhancement of document."""
        query = """
            UPDATE response_documents
            SET parsed_text = %s,
                page_count = %s,
                enhanced_bytes = %s,
                quality_notes = %s,
                status = %s,
                error = %s
            WHERE id = %s
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (parsed_text, page_count, enhanced_bytes, quality_notes, status, error, doc_id),
                )
                conn.commit()

    def update_response_status(
        self,
        response_id: int,
        status: str,
        error: str | None = None,
        rfx_resolution: dict[str, Any] | None = None,
        vendor_resolution: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        rfx_id: int | None = None,
        vendor_id: int | None = None,
    ) -> None:
        """Update overall response state, resolution findings, and summary."""
        clauses = ["status = %s", "updated_at = NOW()"]
        params: list[Any] = [status]

        if error is not None:
            clauses.append("error = %s")
            params.append(error)
        if rfx_resolution is not None:
            clauses.append("rfx_resolution = %s")
            params.append(_dump_json(rfx_resolution))
        if vendor_resolution is not None:
            clauses.append("vendor_resolution = %s")
            params.append(_dump_json(vendor_resolution))
        if summary is not None:
            clauses.append("summary = %s")
            params.append(_dump_json(summary))
        if rfx_id is not None:
            clauses.append("rfx_id = %s")
            params.append(rfx_id)
        if vendor_id is not None:
            clauses.append("vendor_id = %s")
            params.append(vendor_id)

        params.append(response_id)
        query = f"UPDATE vendor_responses SET {', '.join(clauses)} WHERE id = %s"

        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                conn.commit()

    # -----------------------------------------------------------------------
    # Response Items, Terms, Answers, Flags, Crops
    # -----------------------------------------------------------------------

    def save_response_items(self, items: list[dict[str, Any]]) -> list[int]:
        """Bulk insert evaluated response items and return generated IDs."""
        if not items:
            return []
        query = """
            INSERT INTO response_items (
                response_id, rfx_item_id, kind, vendor_line_no, raw_description,
                raw_specs, raw_qty, raw_price, raw_unit, raw_currency, tax_basis,
                discount, moq, lead_time_days, remarks, source_document_id,
                source_snippet, source_location, page, bbox, extraction_confidence,
                extraction_reason, match_candidates, match_confidence, match_reason,
                unit_factor, fx_rate, fx_rate_date, normalized_price_inr,
                missing_fields, state, flags, why_unsure, how_to_resolve,
                effective_price_inr, review_status, is_current, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, TRUE, NOW()
            ) RETURNING id;
        """
        ids = []
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                for item in items:
                    cur.execute(
                        query,
                        (
                            item["response_id"],
                            item.get("rfx_item_id"),
                            item.get("kind", "MATCHED"),
                            item.get("vendor_line_no"),
                            item["raw_description"],
                            _dump_json(item.get("raw_specs", {})),
                            item.get("raw_qty"),
                            item.get("raw_price"),
                            item.get("raw_unit"),
                            item.get("raw_currency", "INR"),
                            item.get("tax_basis", "unknown"),
                            _dump_json(item.get("discount", {})),
                            item.get("moq"),
                            item.get("lead_time_days"),
                            item.get("remarks"),
                            item.get("source_document_id"),
                            item.get("source_snippet"),
                            item.get("source_location"),
                            item.get("page", 1),
                            _dump_json(item.get("bbox", {})),
                            item.get("extraction_confidence", 1.0),
                            item.get("extraction_reason"),
                            _dump_json(item.get("match_candidates", [])),
                            item.get("match_confidence", 1.0),
                            item.get("match_reason"),
                            item.get("unit_factor", 1.0),
                            item.get("fx_rate", 1.0),
                            item.get("fx_rate_date"),
                            item.get("normalized_price_inr"),
                            _dump_json(item.get("missing_fields", [])),
                            item.get("state", "CONFIDENT"),
                            _dump_json(item.get("flags", [])),
                            item.get("why_unsure"),
                            item.get("how_to_resolve"),
                            item.get("effective_price_inr") or item.get("normalized_price_inr"),
                            item.get("review_status", "pending"),
                        ),
                    )
                    row = cur.fetchone()
                    ids.append(int(row[0]))
                conn.commit()
        return ids

    def save_item_crop(
        self,
        item_id: int,
        document_id: int | None,
        page: int,
        bbox: dict[str, Any],
        image_bytes: bytes,
        mime: str = "image/png",
    ) -> int:
        """Store image evidence crop."""
        query = """
            INSERT INTO item_crops (
                item_id, document_id, page, bbox, image_bytes, mime, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
            RETURNING id;
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (item_id, document_id, page, _dump_json(bbox), image_bytes, mime),
                )
                row = cur.fetchone()
                conn.commit()
                return int(row[0])

    def get_item_crop(self, item_id: int) -> tuple[bytes, str] | None:
        """Retrieve crop image bytes and mime type for an item."""
        query = "SELECT image_bytes, mime FROM item_crops WHERE item_id = %s ORDER BY id DESC LIMIT 1"
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (item_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return bytes(row[0]), str(row[1])

    def save_response_terms(self, response_id: int, terms: list[dict[str, Any]]) -> None:
        """Insert commercial terms."""
        if not terms:
            return
        query = """
            INSERT INTO response_terms (
                response_id, term_type, value_text, value_num, condition_text, source_snippet, confidence, state, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                for t in terms:
                    cur.execute(
                        query,
                        (
                            response_id,
                            t.get("term_type", "other"),
                            t.get("value_text"),
                            t.get("value_num"),
                            t.get("condition_text"),
                            t.get("source_snippet"),
                            t.get("confidence", 1.0),
                            t.get("state", "CONFIDENT"),
                        ),
                    )
                conn.commit()

    def save_response_answers(self, response_id: int, answers: list[dict[str, Any]]) -> None:
        """Insert questionnaire answers."""
        if not answers:
            return
        query = """
            INSERT INTO response_answers (
                response_id, rfx_question_id, raw_question, answer_text, pass_fail, reason, source_snippet, confidence, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                for a in answers:
                    cur.execute(
                        query,
                        (
                            response_id,
                            a.get("rfx_question_id"),
                            a.get("raw_question", ""),
                            a.get("answer_text", ""),
                            a.get("pass_fail", "unanswered"),
                            a.get("reason"),
                            a.get("source_snippet"),
                            a.get("confidence", 1.0),
                        ),
                    )
                conn.commit()

    def save_response_flags(self, response_id: int, flags: list[dict[str, Any]]) -> None:
        """Insert response-level and item-level validation flags."""
        if not flags:
            return
        query = """
            INSERT INTO response_flags (
                response_id, item_id, code, severity, message, created_at, resolved
            ) VALUES (%s, %s, %s, %s, %s, NOW(), FALSE)
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                for f in flags:
                    cur.execute(
                        query,
                        (
                            response_id,
                            f.get("item_id"),
                            f["code"],
                            f.get("severity", "warning"),
                            f["message"],
                        ),
                    )
                conn.commit()

    # -----------------------------------------------------------------------
    # Traceability & Observability (Events & LLM Calls)
    # -----------------------------------------------------------------------

    def log_pipeline_event(
        self,
        response_id: int,
        stage: str,
        status: str,
        duration_ms: int = 0,
        document_id: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Log pipeline execution step for auditing."""
        query = """
            INSERT INTO pipeline_events (
                response_id, document_id, stage, status, started_at, duration_ms, detail
            ) VALUES (%s, %s, %s, %s, NOW(), %s, %s)
        """
        try:
            with get_db_connection(self.secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (response_id, document_id, stage, status, duration_ms, _dump_json(detail or {})),
                    )
                    conn.commit()
        except Exception as exc:
            logger.error("Failed to log pipeline event: %s", exc)

    def log_llm_call(
        self,
        response_id: int,
        stage: str,
        provider: str,
        model: str,
        prompt_version: str = "v1",
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        validation_ok: bool = True,
        retries: int = 0,
    ) -> None:
        """Log individual LLM invocation metrics."""
        query = """
            INSERT INTO llm_calls (
                response_id, stage, provider, model, prompt_version,
                tokens_in, tokens_out, latency_ms, validation_ok, retries, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        try:
            with get_db_connection(self.secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            response_id,
                            stage,
                            provider,
                            model,
                            prompt_version,
                            tokens_in,
                            tokens_out,
                            latency_ms,
                            validation_ok,
                            retries,
                        ),
                    )
                    conn.commit()
        except Exception as exc:
            logger.error("Failed to log LLM call: %s", exc)

    # -----------------------------------------------------------------------
    # Retrieval Queries & View Access
    # -----------------------------------------------------------------------

    def get_response_by_id(self, response_id: int) -> dict[str, Any] | None:
        """Retrieve complete response header with vendor, RFx title, and summary."""
        query = """
            SELECT 
                vr.id, vr.rfx_id, r.title AS rfx_title, vr.vendor_id,
                COALESCE(v.name, vr.sender_name) AS vendor_name,
                vr.version, vr.supersedes_response_id, vr.channel,
                vr.subject, vr.sender_name, vr.sender_email,
                vr.body_text, vr.body_json,
                vr.received_at, vr.status, vr.rfx_resolution,
                vr.vendor_resolution, vr.summary, vr.error,
                vr.is_current, vr.created_at, vr.updated_at
            FROM vendor_responses vr
            LEFT JOIN rfx r ON vr.rfx_id = r.id
            LEFT JOIN vendors v ON vr.vendor_id = v.id
            WHERE vr.id = %s
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (response_id,))
                row = cur.fetchone()
                if not row:
                    return None
                
                # Fetch documents
                cur.execute(
                    "SELECT id, filename, mime, size_bytes, sha256, doc_role, page_count, status, error, quality_notes FROM response_documents WHERE response_id = %s ORDER BY id ASC",
                    (response_id,),
                )
                docs = [
                    {
                        "id": d[0],
                        "filename": d[1],
                        "mime": d[2],
                        "size_bytes": d[3],
                        "sha256": d[4],
                        "doc_role": d[5],
                        "page_count": d[6],
                        "status": d[7],
                        "error": d[8],
                        "quality_notes": d[9],
                    }
                    for d in cur.fetchall()
                ]

                # Fetch counts
                cur.execute("SELECT COUNT(*) FROM response_items WHERE response_id = %s AND is_current = TRUE", (response_id,))
                items_count = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM response_flags WHERE response_id = %s", (response_id,))
                flags_count = int(cur.fetchone()[0])

                return {
                    "id": row[0],
                    "rfx_id": row[1],
                    "rfx_title": row[2],
                    "vendor_id": row[3],
                    "vendor_name": row[4],
                    "version": row[5],
                    "supersedes_response_id": row[6],
                    "channel": row[7],
                    "subject": row[8],
                    "sender_name": row[9],
                    "sender_email": row[10],
                    "body_text": row[11],
                    "body_json": row[12] if isinstance(row[12], dict) else (json.loads(row[12]) if row[12] else None),
                    "received_at": row[13],
                    "status": row[14],
                    "rfx_resolution": row[15] if isinstance(row[15], dict) else json.loads(row[15] or "{}"),
                    "vendor_resolution": row[16] if isinstance(row[16], dict) else json.loads(row[16] or "{}"),
                    "summary": row[17] if isinstance(row[17], dict) else json.loads(row[17] or "{}"),
                    "error": row[18],
                    "is_current": row[19],
                    "created_at": row[20],
                    "updated_at": row[21],
                    "documents": docs,
                    "items_count": items_count,
                    "flags_count": flags_count,
                }

    def get_response_items(self, response_id: int) -> list[dict[str, Any]]:
        """Retrieve items for a response joined with crops and RFx info."""
        query = """
            SELECT 
                ri.id, ri.response_id, ri.rfx_item_id, rxi.item_number, rxi.description AS rfx_desc,
                ri.kind, ri.vendor_line_no, ri.raw_description, ri.raw_specs, ri.raw_qty,
                ri.raw_price, ri.raw_unit, ri.raw_currency, ri.tax_basis, ri.discount,
                ri.moq, ri.lead_time_days, ri.remarks, ri.source_document_id,
                ri.source_snippet, ri.source_location, ri.page, ri.bbox,
                ri.extraction_confidence, ri.extraction_reason, ri.match_candidates,
                ri.match_confidence, ri.match_reason, ri.unit_factor, ri.fx_rate,
                ri.fx_rate_date, ri.normalized_price_inr, ri.effective_price_inr,
                ri.missing_fields, ri.state, ri.flags, ri.why_unsure, ri.how_to_resolve,
                ri.buyer_override, ri.review_status, ri.is_current, ri.created_at,
                (SELECT COUNT(*) FROM item_crops ic WHERE ic.item_id = ri.id) > 0 AS has_crop
            FROM response_items ri
            LEFT JOIN rfx_items rxi ON ri.rfx_item_id = rxi.id
            WHERE ri.response_id = %s AND ri.is_current = TRUE
            ORDER BY ri.id ASC
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (response_id,))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "id": r[0],
                        "response_id": r[1],
                        "rfx_item_id": r[2],
                        "rfx_item_number": r[3],
                        "rfx_item_description": r[4],
                        "kind": r[5],
                        "vendor_line_no": r[6],
                        "raw_description": r[7],
                        "raw_specs": r[8] if isinstance(r[8], dict) else json.loads(r[8] or "{}"),
                        "raw_qty": float(r[9]) if r[9] is not None else None,
                        "raw_price": float(r[10]) if r[10] is not None else None,
                        "raw_unit": r[11],
                        "raw_currency": r[12],
                        "tax_basis": r[13],
                        "discount": r[14] if isinstance(r[14], dict) else json.loads(r[14] or "{}"),
                        "moq": float(r[15]) if r[15] is not None else None,
                        "lead_time_days": r[16],
                        "remarks": r[17],
                        "source_document_id": r[18],
                        "source_snippet": r[19],
                        "source_location": r[20],
                        "page": r[21],
                        "bbox": r[22] if isinstance(r[22], dict) else json.loads(r[22] or "{}"),
                        "extraction_confidence": float(r[23]) if r[23] is not None else 1.0,
                        "extraction_reason": r[24],
                        "match_candidates": r[25] if isinstance(r[25], list) else json.loads(r[25] or "[]"),
                        "match_confidence": float(r[26]) if r[26] is not None else 1.0,
                        "match_reason": r[27],
                        "unit_factor": float(r[28]) if r[28] is not None else 1.0,
                        "fx_rate": float(r[29]) if r[29] is not None else 1.0,
                        "fx_rate_date": str(r[30]) if r[30] else None,
                        "normalized_price_inr": float(r[31]) if r[31] is not None else None,
                        "effective_price_inr": float(r[32]) if r[32] is not None else (float(r[31]) if r[31] is not None else None),
                        "missing_fields": r[33] if isinstance(r[33], list) else json.loads(r[33] or "[]"),
                        "state": r[34],
                        "flags": r[35] if isinstance(r[35], list) else json.loads(r[35] or "[]"),
                        "why_unsure": r[36],
                        "how_to_resolve": r[37],
                        "buyer_override": r[38] if isinstance(r[38], dict) else json.loads(r[38] or "{}"),
                        "review_status": r[39],
                        "is_current": r[40],
                        "created_at": r[41],
                        "has_crop": bool(r[42]),
                    })
                return results

    def get_document_raw(self, doc_id: int) -> tuple[bytes, str, str] | None:
        """Fetch raw bytes, filename, mime for a document."""
        query = "SELECT raw_bytes, filename, mime FROM response_documents WHERE id = %s"
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (doc_id,))
                row = cur.fetchone()
                if not row or not row[0]:
                    return None
                return bytes(row[0]), row[1], row[2] or "application/octet-stream"

    def update_item_buyer_correction(
        self,
        item_id: int,
        correction: dict[str, Any],
        reviewed_by: str = "buyer",
    ) -> dict[str, Any] | None:
        """Apply buyer correction to an item and record audit history."""
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, response_id, normalized_price_inr, raw_qty, raw_unit, rfx_item_id, kind, buyer_override FROM response_items WHERE id = %s",
                    (item_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                response_id = row[1]
                old_override = row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}")
                history = old_override.get("history", [])
                history.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "by": reviewed_by,
                    "previous_values": {
                        "normalized_price_inr": float(row[2]) if row[2] else None,
                        "raw_qty": float(row[3]) if row[3] else None,
                        "raw_unit": row[4],
                        "rfx_item_id": row[5],
                        "kind": row[6],
                    },
                    "applied": correction,
                })

                new_override = {
                    **correction,
                    "history": history,
                    "last_updated_at": datetime.utcnow().isoformat(),
                    "reviewed_by": reviewed_by,
                }

                effective_price = correction.get("corrected_price_inr") or (float(row[2]) if row[2] else None)
                kind = correction.get("kind") or row[6]
                rfx_item_id = correction.get("rfx_item_id") if "rfx_item_id" in correction else row[5]

                cur.execute(
                    """
                    UPDATE response_items
                    SET buyer_override = %s,
                        effective_price_inr = %s,
                        kind = %s,
                        rfx_item_id = %s,
                        review_status = 'corrected',
                        reviewed_by = %s,
                        reviewed_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (_dump_json(new_override), effective_price, kind, rfx_item_id, reviewed_by, item_id),
                )
                conn.commit()

        return self.get_item_by_id(item_id)

    def get_item_by_id(self, item_id: int) -> dict[str, Any] | None:
        """Fetch single item by ID."""
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT response_id FROM response_items WHERE id = %s", (item_id,))
                row = cur.fetchone()
                if not row:
                    return None
                items = self.get_response_items(row[0])
                for itm in items:
                    if itm["id"] == item_id:
                        return itm
        return None

    def get_responses_for_rfx(self, rfx_id: int) -> list[dict[str, Any]]:
        """Retrieve all active responses for an RFx."""
        query = "SELECT id FROM vendor_responses WHERE rfx_id = %s AND is_current = TRUE ORDER BY id ASC"
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (rfx_id,))
                ids = [r[0] for r in cur.fetchall()]
        return [self.get_response_by_id(rid) for rid in ids if rid is not None]

    def get_rfx_items_for_rfx(self, rfx_id: int) -> list[dict[str, Any]]:
        """Fetch RFx line items."""
        query = """
            SELECT id, rfx_id, item_number, item_code, description, quantity, unit, material, dimensions, specifications, target_price, currency
            FROM rfx_items WHERE rfx_id = %s ORDER BY item_number ASC
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (rfx_id,))
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "rfx_id": r[1],
                        "item_number": r[2],
                        "item_code": r[3],
                        "description": r[4],
                        "quantity": float(r[5]) if r[5] is not None else 1.0,
                        "unit": r[6] or "pcs",
                        "material": r[7],
                        "dimensions": r[8] if isinstance(r[8], dict) else json.loads(r[8] or "{}"),
                        "specifications": r[9],
                        "target_price": float(r[10]) if r[10] is not None else None,
                        "currency": r[11] or "INR",
                    }
                    for r in rows
                ]

    def get_open_rfx_list(self) -> list[dict[str, Any]]:
        """Get all open/active RFxs for resolution matching."""
        query = """
            SELECT id, title, category, scope, status, currency, payment_terms, validity_days, response_deadline, released_at, created_at
            FROM rfx
            WHERE status NOT IN ('closed', 'completed')
            ORDER BY id DESC
        """
        with get_db_connection(self.secrets) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
                results = []
                for r in rows:
                    items = self.get_rfx_items_for_rfx(r[0])
                    results.append({
                        "id": r[0],
                        "title": r[1],
                        "category": r[2],
                        "scope": r[3],
                        "status": r[4],
                        "currency": r[5],
                        "payment_terms": r[6],
                        "validity_days": r[7],
                        "response_deadline": r[8],
                        "released_at": r[9],
                        "created_at": r[10],
                        "items": items,
                    })
                return results

    def get_unit_conversions(self) -> dict[tuple[str, str], float]:
        """Fetch configured unit conversion multipliers."""
        query = "SELECT LOWER(from_unit), LOWER(to_unit), multiplier FROM unit_conversions"
        try:
            with get_db_connection(self.secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    return {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}
        except Exception:
            return {}

    def get_fx_rate(self, from_currency: str, to_currency: str = "INR") -> tuple[float, str]:
        """Get latest FX rate to INR."""
        if from_currency.upper() == to_currency.upper():
            return 1.0, str(date.today())
        query = """
            SELECT rate, rate_date FROM fx_rates
            WHERE UPPER(from_currency) = UPPER(%s) AND UPPER(to_currency) = UPPER(%s)
            ORDER BY rate_date DESC LIMIT 1
        """
        try:
            with get_db_connection(self.secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (from_currency, to_currency))
                    row = cur.fetchone()
                    if row:
                        return float(row[0]), str(row[1])
        except Exception:
            pass
        # Fallback defaults
        defaults = {"USD": 86.50, "EUR": 93.0, "GBP": 110.0, "AED": 23.55, "SGD": 64.20}
        return defaults.get(from_currency.upper(), 1.0), str(date.today())
