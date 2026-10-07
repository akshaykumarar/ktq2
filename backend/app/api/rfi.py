"""Packaging RFI intake API."""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from backend.app.intake.excel_parser import parse_spreadsheet_bytes
from backend.app.intake.models import IntakeResponse
from backend.app.intake.service import handle_packaging_intake

router = APIRouter(prefix="/api/rfi", tags=["packaging-rfi"])


@router.post("/intake", response_model=IntakeResponse)
async def packaging_rfi_intake(
    message: Annotated[str, Form()] = "",
    conversation_id: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> IntakeResponse:
    """Collect packaging RFI details from text plus optional CSV/XLSX attachments."""
    conv_id = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
    attachments = []
    for uploaded in files or []:
        content = await uploaded.read()
        attachments.append(
            parse_spreadsheet_bytes(
                file_name=uploaded.filename or "attachment",
                content=content,
                content_type=uploaded.content_type,
            )
        )

    return handle_packaging_intake(
        conversation_id=conv_id,
        message=message,
        attachments=attachments,
    )
