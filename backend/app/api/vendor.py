"""FastAPI router for Vendor Quotation Intake, Document OCR/Extraction, Verification, and Corrections."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Path,
    Request,
    Response,
    UploadFile,
    status,
)

from backend.app.config.settings import load_config
from backend.app.vendor.models import (
    ItemCorrectionRequest,
    RFXResponsesCoverageSummary,
    ResponseItemDetail,
    ResponseStatus,
    VendorResponseDetail,
    VendorResponseSubmitPreview,
)
from backend.app.vendor.service import VendorResponseService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vendor-responses"])


def _get_service(request: Request) -> VendorResponseService:
    """Instantiate VendorResponseService with application configuration."""
    config = getattr(request.app.state, "config", None) or load_config()
    return VendorResponseService(config)


# ---------------------------------------------------------------------------
# 1. Vendor Quotation Submission (202 Accepted Fast Path)
# ---------------------------------------------------------------------------

@router.post(
    "/api/vendor-responses",
    response_model=VendorResponseSubmitPreview,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit vendor quotation",
    description="Accepts vendor quotations in ANY format (files, email body, JSON payload), immediately persists raw bytes for auditability, and processes OCR extraction & RFx matching asynchronously.",
)
async def submit_vendor_response(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(default=[]),
    body_text: Annotated[str | None, Form(description="Email body or pasted quotation text")] = None,
    body_json: Annotated[str | None, Form(description="Raw JSON quotation payload (as JSON string)")] = None,
    subject: Annotated[str | None, Form(description="Email subject line")] = None,
    sender_email: Annotated[str | None, Form(description="Sender email address")] = None,
    sender_name: Annotated[str | None, Form(description="Sender contact / company name")] = None,
    channel: Annotated[str, Form(description="Intake channel: email | upload | api | simulated")] = "upload",
    received_at: Annotated[str | None, Form(description="ISO timestamp of receipt")] = None,
    rfx_ref: Annotated[str | None, Form(description="Optional RFx identifier or reference code")] = None,
    vendor_name: Annotated[str | None, Form(description="Optional vendor name override")] = None,
) -> VendorResponseSubmitPreview:
    """Store raw inputs and trigger background extraction pipeline."""
    # Validation: at least one input medium required
    if not files and not body_text and not body_json:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of 'files', 'body_text', or 'body_json' must be provided.",
        )

    # Read all uploaded file bytes
    parsed_files: list[tuple[str, bytes, str | None]] = []
    for f in files:
        if f.filename:
            content = await f.read()
            parsed_files.append((f.filename, content, f.content_type))

    # Parse JSON if provided as string
    parsed_json = None
    if body_json:
        try:
            parsed_json = json.loads(body_json)
        except Exception:
            parsed_json = {"raw": body_json}

    rcv_date = None
    if received_at:
        try:
            rcv_date = datetime.fromisoformat(received_at)
        except Exception:
            rcv_date = datetime.utcnow()

    svc = _get_service(request)
    preview = svc.submit_vendor_response(
        files=parsed_files,
        body_text=body_text,
        body_json=parsed_json,
        subject=subject,
        sender_email=sender_email,
        sender_name=sender_name,
        channel=channel,
        received_at=rcv_date,
        rfx_ref=rfx_ref,
        vendor_name=vendor_name,
    )

    # Schedule background state machine if not a duplicate
    if not preview.is_duplicate:
        background_tasks.add_task(svc.run_pipeline, preview.response_id)

    return preview


# ---------------------------------------------------------------------------
# 2. Get Response Status & Header
# ---------------------------------------------------------------------------

@router.get(
    "/api/vendor-responses/{response_id}",
    response_model=VendorResponseDetail,
    summary="Get vendor response details",
    description="Retrieve processing status, resolution findings, document progress, and summary counts.",
)
async def get_vendor_response(
    response_id: Annotated[int, Path(description="Integer ID of vendor response")],
    request: Request,
) -> VendorResponseDetail:
    """Retrieve full status of a vendor response submission."""
    svc = _get_service(request)
    res = svc.repo.get_response_by_id(response_id)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor response #{response_id} was not found.",
        )
    return VendorResponseDetail(**res)


# ---------------------------------------------------------------------------
# 3. Get Extracted Response Items
# ---------------------------------------------------------------------------

@router.get(
    "/api/vendor-responses/{response_id}/items",
    response_model=list[ResponseItemDetail],
    summary="Get extracted line items",
    description="Retrieve all extracted line items with match results, confidence, normalized price in INR, validation flags, and uncertainty reasons.",
)
async def get_response_items(
    response_id: Annotated[int, Path(description="Integer ID of vendor response")],
    request: Request,
) -> list[ResponseItemDetail]:
    """Retrieve all line items for a vendor response."""
    svc = _get_service(request)
    items = svc.repo.get_response_items(response_id)
    return [ResponseItemDetail(**itm) for itm in items]


# ---------------------------------------------------------------------------
# 4. Download Raw Original Document
# ---------------------------------------------------------------------------

@router.get(
    "/api/vendor-responses/{response_id}/documents/{doc_id}/raw",
    summary="Download original raw document",
    description="Fetch the original raw document bytes exactly as received from the vendor.",
)
async def get_raw_document(
    response_id: int,
    doc_id: int,
    request: Request,
) -> Response:
    """Stream raw document bytes."""
    svc = _get_service(request)
    doc_tuple = svc.repo.get_document_raw(doc_id)
    if not doc_tuple:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document #{doc_id} was not found.",
        )
    raw_bytes, filename, mime = doc_tuple
    return Response(
        content=raw_bytes,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# 5. Visual Evidence Crop Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/api/response-items/{item_id}/crop",
    summary="Get visual evidence crop",
    description="Returns cropped image region of the original source document containing this line item.",
)
async def get_item_visual_crop(
    item_id: Annotated[int, Path(description="Integer ID of response item")],
    request: Request,
) -> Response:
    """Stream visual evidence PNG image."""
    svc = _get_service(request)
    crop_tuple = svc.repo.get_item_crop(item_id)
    if not crop_tuple:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No visual evidence crop exists for item #{item_id}.",
        )
    crop_bytes, mime = crop_tuple
    return Response(content=crop_bytes, media_type=mime)


# ---------------------------------------------------------------------------
# 6. RFx All Responses & Coverage Summary
# ---------------------------------------------------------------------------

@router.get(
    "/api/rfx/{rfx_id}/responses",
    response_model=RFXResponsesCoverageSummary,
    summary="Get all vendor responses for an RFx",
    description="Retrieve all vendor responses submitted for an RFx with coverage matrix and comparison metrics.",
)
async def get_rfx_responses_coverage(
    rfx_id: Annotated[int, Path(description="Integer ID of RFx")],
    request: Request,
) -> RFXResponsesCoverageSummary:
    """Retrieve all responses linked to an RFx."""
    svc = _get_service(request)
    rfx_items = svc.repo.get_rfx_items_for_rfx(rfx_id)
    responses_raw = svc.repo.get_responses_for_rfx(rfx_id)
    responses = [VendorResponseDetail(**r) for r in responses_raw]

    # Query view for coverage matrix
    coverage_matrix: list[dict[str, Any]] = []
    with svc.repo.secrets.db_configured and svc.repo:
        try:
            from backend.app.db.connection import get_db_connection
            with get_db_connection(svc.repo.secrets) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT vendor_name, response_id, coverage_pct, matched_items_count, extra_items_count, avg_normalized_price_inr FROM v_vendor_coverage WHERE rfx_id = %s",
                        (rfx_id,),
                    )
                    rows = cur.fetchall()
                    for row in rows:
                        coverage_matrix.append({
                            "vendor_name": row[0],
                            "response_id": row[1],
                            "coverage_pct": float(row[2]) if row[2] else 0.0,
                            "matched_items": row[3],
                            "extra_items": row[4],
                            "avg_price_inr": float(row[5]) if row[5] else None,
                        })
        except Exception as exc:
            logger.warning("Could not query v_vendor_coverage view: %s", exc)

    rfx_title = responses[0].rfx_title if responses else None
    return RFXResponsesCoverageSummary(
        rfx_id=rfx_id,
        rfx_title=rfx_title,
        total_rfx_items=len(rfx_items),
        responses_count=len(responses),
        responses=responses,
        coverage_matrix=coverage_matrix,
    )


# ---------------------------------------------------------------------------
# 7. Reprocess Pipeline Run
# ---------------------------------------------------------------------------

@router.post(
    "/api/vendor-responses/{response_id}/reprocess",
    response_model=VendorResponseDetail,
    summary="Reprocess vendor response",
    description="Re-runs the extraction, resolution, and matching pipeline on original documents, creating a new version run.",
)
async def reprocess_vendor_response(
    response_id: Annotated[int, Path(description="Integer ID of vendor response")],
    request: Request,
) -> VendorResponseDetail:
    """Trigger full re-run of pipeline."""
    svc = _get_service(request)
    try:
        new_resp = await svc.reprocess_response(response_id)
        return VendorResponseDetail(**new_resp)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reprocess failed: {exc}",
        )


# ---------------------------------------------------------------------------
# 8. Buyer Correction
# ---------------------------------------------------------------------------

@router.patch(
    "/api/response-items/{item_id}",
    response_model=ResponseItemDetail,
    summary="Buyer correction on item",
    description="Apply human buyer correction (price, quantity, unit, RFx item mapping), preserving before/after audit history.",
)
async def correct_response_item(
    item_id: Annotated[int, Path(description="Integer ID of response item")],
    payload: ItemCorrectionRequest,
    request: Request,
) -> ResponseItemDetail:
    """Update line item with buyer corrections."""
    svc = _get_service(request)
    try:
        updated = svc.apply_buyer_correction(item_id, payload.model_dump(exclude_unset=True))
        return ResponseItemDetail(**updated)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item correction failed: {exc}",
        )
