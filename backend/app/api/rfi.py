"""Packaging RFI API endpoints for intake, lifecycle management, and querying."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status

from backend.app.config.settings import load_config
from backend.app.intake.models import (
    ExcelIntakeResponse,
    IntakeExtractionResult,
    IntakeResponse,
    RFICreateRequest,
    RFIDetailResponse,
    RFITriggerResponse,
    RFIUpdateRequest,
    TextIntakeRequest,
)
from backend.app.intake.service import (
    create_rfi,
    get_rfi,
    handle_packaging_intake,
    process_excel_intake,
    process_text_intake,
    trigger_rfi,
    update_rfi,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rfi", tags=["packaging-rfi"])


def _get_app_context(request: Request) -> tuple[Any, Any]:
    """Retrieve secrets and optional agent registry from application state."""
    config = getattr(request.app.state, "config", None) or load_config()
    agents = getattr(request.app.state, "agents", None)
    parser_agent = agents.get_specialist("rfi_parser") if agents else None
    return config.secrets, parser_agent


@router.post(
    "/intake",
    response_model=IntakeExtractionResult,
    summary="Text requirement intake",
    description="Receive plain text packaging requirements, extract structured line items via PydanticAI / rules, apply defaults, and optionally create an RFI.",
)
async def intake_text_requirements(
    payload: TextIntakeRequest,
    request: Request,
) -> IntakeExtractionResult:
    """Parse text requirements into structured packaging line items."""
    secrets, parser_agent = _get_app_context(request)
    return await process_text_intake(payload, secrets, agent=parser_agent)


@router.post(
    "/intake/excel",
    response_model=ExcelIntakeResponse,
    summary="Excel requirement intake",
    description="Upload an Excel (.xlsx) or CSV spreadsheet to extract packaging line items with row-level error reporting.",
)
async def intake_excel_file(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel (.xlsx) or CSV file")],
    create_rfi_flag: Annotated[bool, Query(alias="create_rfi", description="Automatically create RFI if valid")] = False,
    title: Annotated[str | None, Query(description="Optional custom title for the created RFI")] = None,
) -> ExcelIntakeResponse:
    """Parse uploaded spreadsheet file into canonical packaging requirements."""
    secrets, _ = _get_app_context(request)
    content = await file.read()
    filename = file.filename or "upload.xlsx"

    return process_excel_intake(
        file_name=filename,
        content=content,
        content_type=file.content_type,
        secrets=secrets,
        create_rfi=create_rfi_flag,
        title=title,
    )


@router.post(
    "",
    response_model=RFIDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create packaging RFI",
    description="Create an RFI directly with structured line items and commercial terms.",
)
async def create_new_rfi(
    payload: RFICreateRequest,
    request: Request,
) -> RFIDetailResponse:
    """Create a new RFI record in the database."""
    secrets, _ = _get_app_context(request)
    record = create_rfi(payload, secrets)
    return RFIDetailResponse(**record)


@router.get(
    "/{rfi_id}",
    response_model=RFIDetailResponse,
    summary="Get RFI details",
    description="Retrieve an existing RFI along with its line items, terms, and current status.",
)
async def get_rfi_by_id(
    rfi_id: int,
    request: Request,
) -> RFIDetailResponse:
    """Fetch an RFI by integer ID."""
    secrets, _ = _get_app_context(request)
    record = get_rfi(rfi_id, secrets)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RFI with ID {rfi_id} was not found.",
        )
    return RFIDetailResponse(**record)


@router.patch(
    "/{rfi_id}",
    response_model=RFIDetailResponse,
    summary="Update RFI",
    description="Update editable fields of an existing RFI (e.g. title, terms, deadline, status).",
)
async def update_rfi_by_id(
    rfi_id: int,
    payload: RFIUpdateRequest,
    request: Request,
) -> RFIDetailResponse:
    """Safely update permitted columns of an RFI."""
    secrets, _ = _get_app_context(request)
    record = update_rfi(rfi_id, payload, secrets)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RFI with ID {rfi_id} was not found.",
        )
    return RFIDetailResponse(**record)


@router.post(
    "/{rfi_id}/trigger",
    response_model=RFITriggerResponse,
    summary="Trigger RFI",
    description="Validate that the RFI is ready, transition status to TRIGGERED, and record trigger timestamp.",
)
async def trigger_rfi_by_id(
    rfi_id: int,
    request: Request,
) -> RFITriggerResponse:
    """Trigger an RFI to transition into the TRIGGERED state."""
    secrets, _ = _get_app_context(request)
    try:
        updated = trigger_rfi(rfi_id, secrets)
        return RFITriggerResponse(
            id=updated["id"],
            title=updated["title"],
            status=updated["status"],
            triggered_at=updated.get("triggered_at") or "",
            message=f"RFI #{rfi_id} successfully triggered.",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post(
    "/intake/conversational",
    response_model=IntakeResponse,
    summary="Conversational intake session",
    description="Multi-turn conversational packaging intake used by chat assistants.",
)
async def conversational_rfi_intake(
    message: Annotated[str, Form()] = "",
    conversation_id: Annotated[str | None, Form()] = None,
) -> IntakeResponse:
    """Interactive multi-turn session intake endpoint."""
    conv_id = conversation_id or "session-default"
    return handle_packaging_intake(
        conversation_id=conv_id,
        message=message,
    )
