"""Pydantic domain models and API contracts for vendor response intake and processing."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ResponseStatus(str, Enum):
    """Pipeline state machine statuses."""
    RECEIVED = "received"
    PREPROCESSING = "preprocessing"
    EXTRACTING = "extracting"
    RESOLVING = "resolving"
    MATCHING = "matching"
    NORMALIZING = "normalizing"
    VALIDATING = "validating"
    DONE = "done"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class ItemKind(str, Enum):
    """Line item relationship to RFx."""
    MATCHED = "MATCHED"
    EXTRA = "EXTRA"
    ALTERNATE = "ALTERNATE"
    NOT_QUOTED = "NOT_QUOTED"


class ItemState(str, Enum):
    """Confidence status of an extracted line item."""
    CONFIDENT = "CONFIDENT"
    REVIEW = "REVIEW"
    MISSING = "MISSING"


class FlagSeverity(str, Enum):
    """Severity level of validation flags."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Extraction Models (Used by LLM Prompts & Parsing)
# ---------------------------------------------------------------------------

class ExtractedVendorInfo(BaseModel):
    """Vendor identity clues discovered in documents."""
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    gstin: str | None = None
    address: str | None = None
    quote_number: str | None = None
    quote_date: str | None = None
    validity_days: int | None = None
    rfx_reference_found: str | None = None


class ExtractedLineItem(BaseModel):
    """Structured line item extracted from quotation document."""
    vendor_line_no: str | None = None
    raw_description: str
    raw_specs: dict[str, Any] = Field(default_factory=dict)
    raw_qty: float | None = None
    raw_price: float | None = None
    raw_unit: str | None = None
    raw_currency: str | None = "INR"
    tax_basis: str | None = "unknown"  # inclusive | exclusive | unknown
    discount: dict[str, Any] = Field(default_factory=dict)
    moq: float | None = None
    lead_time_days: int | None = None
    remarks: str | None = None
    source_snippet: str | None = None
    source_location: str | None = None  # e.g., Sheet1!A4:D4, Page 1 Paragraph 3
    page: int | None = 1
    bbox: dict[str, Any] = Field(default_factory=dict)  # {ymin, xmin, ymax, xmax} or pixel bounds
    extraction_confidence: float = 1.0
    extraction_reason: str | None = None
    unresolved_reference: str | None = None  # e.g. "same as last year"


class ExtractedCommercialTerm(BaseModel):
    """Commercial terms (freight, payment, warranty, GST, discounts)."""
    term_type: str  # freight | gst | payment_terms | delivery | warranty | discount | other
    value_text: str | None = None
    value_num: float | None = None
    condition_text: str | None = None
    source_snippet: str | None = None
    confidence: float = 1.0


class ExtractedQuestionAnswer(BaseModel):
    """Answer to RFx question found in document."""
    raw_question: str
    answer_text: str
    source_snippet: str | None = None
    confidence: float = 1.0


class DocumentQualityNote(BaseModel):
    """Assessment of document readability and scan quality."""
    is_scan: bool = False
    readability_score: float = 1.0  # 0.0 (unreadable) to 1.0 (clean)
    language: str = "en"
    has_handwriting: bool = False
    illegible_regions: list[str] = Field(default_factory=list)
    notes: str | None = None


class DocumentExtractionResult(BaseModel):
    """Full extraction result for a single document."""
    vendor_info: ExtractedVendorInfo = Field(default_factory=ExtractedVendorInfo)
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    commercial_terms: list[ExtractedCommercialTerm] = Field(default_factory=list)
    question_answers: list[ExtractedQuestionAnswer] = Field(default_factory=list)
    quality: DocumentQualityNote = Field(default_factory=DocumentQualityNote)


# ---------------------------------------------------------------------------
# API Request & Response Contracts
# ---------------------------------------------------------------------------

class VendorResponseSubmitPreview(BaseModel):
    """Immediate response on submission (202 Accepted)."""
    response_id: int
    status: ResponseStatus
    message: str
    is_duplicate: bool = False
    superseded_response_id: int | None = None
    rfx_resolution_preview: dict[str, Any] = Field(default_factory=dict)


class ValidationFlagItem(BaseModel):
    """Validation flag detail."""
    id: int | None = None
    item_id: int | None = None
    code: str
    severity: FlagSeverity
    message: str
    resolved: bool = False


class ResponseItemDetail(BaseModel):
    """Full details of an evaluated response line item."""
    id: int
    response_id: int
    rfx_item_id: int | None = None
    rfx_item_number: int | None = None
    rfx_item_description: str | None = None
    kind: ItemKind
    vendor_line_no: str | None = None
    raw_description: str
    raw_specs: dict[str, Any] = Field(default_factory=dict)
    raw_qty: float | None = None
    raw_price: float | None = None
    raw_unit: str | None = None
    raw_currency: str | None = "INR"
    tax_basis: str | None = "unknown"
    discount: dict[str, Any] = Field(default_factory=dict)
    moq: float | None = None
    lead_time_days: int | None = None
    remarks: str | None = None
    source_document_id: int | None = None
    source_snippet: str | None = None
    source_location: str | None = None
    page: int | None = 1
    bbox: dict[str, Any] = Field(default_factory=dict)
    has_crop: bool = False
    extraction_confidence: float = 1.0
    extraction_reason: str | None = None
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_confidence: float = 1.0
    match_reason: str | None = None
    unit_factor: float = 1.0
    fx_rate: float = 1.0
    fx_rate_date: str | None = None
    normalized_price_inr: float | None = None
    effective_price_inr: float | None = None
    missing_fields: list[str] = Field(default_factory=list)
    state: ItemState
    flags: list[dict[str, Any]] = Field(default_factory=list)
    why_unsure: str | None = None
    how_to_resolve: str | None = None
    buyer_override: dict[str, Any] = Field(default_factory=dict)
    review_status: str = "pending"
    is_current: bool = True
    created_at: datetime | str | None = None


class ResponseDocumentSummary(BaseModel):
    """Summary of an attached document and its processing progress."""
    id: int
    filename: str
    mime: str | None = None
    size_bytes: int = 0
    sha256: str
    doc_role: str = "quotation"
    page_count: int = 1
    status: str
    error: str | None = None
    quality_notes: str | None = None


class VendorResponseDetail(BaseModel):
    """Comprehensive response detail."""
    id: int
    rfx_id: int | None = None
    rfx_title: str | None = None
    vendor_id: int | None = None
    vendor_name: str | None = None
    version: int = 1
    supersedes_response_id: int | None = None
    channel: str = "upload"
    subject: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    received_at: datetime | str
    status: ResponseStatus
    rfx_resolution: dict[str, Any] = Field(default_factory=dict)
    vendor_resolution: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    documents: list[ResponseDocumentSummary] = Field(default_factory=list)
    items_count: int = 0
    flags_count: int = 0
    error: str | None = None
    is_current: bool = True
    created_at: datetime | str
    updated_at: datetime | str


class ItemCorrectionRequest(BaseModel):
    """Buyer correction payload for an item."""
    corrected_price_inr: float | None = None
    corrected_qty: float | None = None
    corrected_unit: str | None = None
    rfx_item_id: int | None = None
    kind: ItemKind | None = None
    review_status: str = "corrected"
    reviewed_by: str = "buyer"
    notes: str | None = None


class RFXResponsesCoverageSummary(BaseModel):
    """Coverage summary across all responses for an RFx."""
    rfx_id: int
    rfx_title: str | None = None
    total_rfx_items: int = 0
    responses_count: int = 0
    responses: list[VendorResponseDetail] = Field(default_factory=list)
    coverage_matrix: list[dict[str, Any]] = Field(default_factory=list)
