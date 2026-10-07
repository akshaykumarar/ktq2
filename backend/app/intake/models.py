"""Data models for the packaging RFI intake workflow and API contracts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RFIStatus(str, Enum):
    """Lifecycle status states for an RFI."""

    DRAFT = "draft"
    READY = "ready"
    TRIGGERED = "triggered"
    RESPONSES_PENDING = "responses_pending"
    COMPLETED = "completed"


IntakeStatus = Literal[
    "collecting",
    "needs_domain_confirmation",
    "awaiting_confirmation",
    "approved",
    "submitted",
]


class PackagingRequirement(BaseModel):
    """Canonical packaging requirement extracted from text, Excel, or structured input."""

    item_number: int = 1
    category: str = "Corrugated packaging"
    item_description: str
    quantity: float | None = None
    unit: str = "pcs"
    dimensions: dict[str, Any] = Field(
        default_factory=dict,
        description="Dimensions such as length, width, height, unit (e.g. {'length': 600, 'width': 400, 'height': 300, 'unit': 'mm'})",
    )
    material: str | None = None
    ply: str | None = None
    gsm: str | None = None
    bf: str | None = None
    specification: str | None = None
    delivery_date: str | None = None
    target_price: float | None = None
    currency: str = "INR"
    notes: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)


class PackagingLineItem(BaseModel):
    """Legacy-compatible packaging line item representation."""

    item_number: int
    description: str
    packaging_category: str | None = None
    quantity: float | None = None
    unit: str | None = None
    material: str | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    gsm: str | None = None
    bf: str | None = None
    ply: str | None = None
    flute_type: str | None = None
    color: str | None = None
    print_required: bool | None = None
    printing_details: str | None = None
    usage_context: str | None = None
    specifications: str | None = None
    required_date: date | None = None
    target_price: float | None = None
    currency: str = "INR"

    def to_requirement(self) -> PackagingRequirement:
        """Convert line item to canonical PackagingRequirement."""
        return PackagingRequirement(
            item_number=self.item_number,
            category=self.packaging_category or "Corrugated packaging",
            item_description=self.description,
            quantity=self.quantity,
            unit=self.unit or "pcs",
            dimensions=self.dimensions,
            material=self.material,
            ply=self.ply,
            gsm=self.gsm,
            bf=self.bf,
            specification=self.specifications,
            delivery_date=self.required_date.isoformat() if self.required_date else None,
            target_price=self.target_price,
            currency=self.currency,
        )


class PackagingTerms(BaseModel):
    """Commercial and fulfillment terms shown before final sign-off."""

    terms_type: Literal["standard", "custom", "combo"] = "standard"
    payment_terms: str = "Net 30"
    delivery_terms: str = "Delivered to warehouse dock"
    quote_validity_days: int = 30
    response_deadline: date | None = None
    inspection_terms: str = "Warehouse team will inspect packaging quality and quantity at receipt."
    custom_terms: list[str] = Field(default_factory=list)


class AttachmentExtraction(BaseModel):
    """Parsed file content retained for traceability."""

    file_name: str
    content_type: str | None = None
    extraction_status: Literal["parsed", "unsupported", "error"] = "parsed"
    rows: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PackagingIntakeState(BaseModel):
    """Conversation-scoped packaging procurement intake state."""

    conversation_id: str
    status: IntakeStatus = "collecting"
    title: str | None = None
    category: str = "Packaging"
    scope: str | None = None
    delivery_location: str | None = None
    line_items: list[PackagingLineItem] = Field(default_factory=list)
    terms: PackagingTerms = Field(default_factory=PackagingTerms)
    selected_supplier_ids: list[int] = Field(default_factory=list)
    vendor_selection_rule: str | None = "Use active packaging suppliers that match item category and location."
    missing_fields: list[str] = Field(default_factory=list)
    current_question: str | None = None
    current_default: dict[str, Any] | None = None
    raw_user_text: list[str] = Field(default_factory=list)
    attachments: list[AttachmentExtraction] = Field(default_factory=list)
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IntakeResponse(BaseModel):
    """Response sent by the conversational packaging intake API."""

    message: str
    agent: str = "packaging_intake"
    conversation_id: str
    status: IntakeStatus
    state: PackagingIntakeState


class TextIntakeRequest(BaseModel):
    """Payload for text requirement intake API."""

    text: str = Field(description="Plain text packaging requirements.")
    title: str | None = Field(default=None, description="Optional title for the RFI.")
    create_rfi: bool = Field(default=False, description="Whether to automatically create an RFI from the parsed result.")


class IntakeExtractionResult(BaseModel):
    """Structured extraction result returned by the intake parser and AI agent."""

    is_packaging: bool = True
    title: str = "Packaging RFI"
    requirements: list[PackagingRequirement] = Field(default_factory=list)
    detected_category: str = "Corrugated packaging"
    missing_critical_fields: list[str] = Field(default_factory=list)
    defaults_applied: dict[str, Any] = Field(default_factory=dict)
    ready_for_rfi: bool = False
    rfi_id: int | None = None
    message: str = ""


class ExcelIntakeResponse(BaseModel):
    """Result of parsing and processing an uploaded Excel spreadsheet."""

    file_name: str
    total_sheets: int
    rows_parsed: int
    errors: list[str] = Field(default_factory=list)
    requirements: list[PackagingRequirement] = Field(default_factory=list)
    ready_for_rfi: bool = False
    rfi_id: int | None = None


class RFICreateRequest(BaseModel):
    """Direct RFI creation request payload."""

    title: str
    category: str = "Corrugated packaging"
    scope: str | None = None
    currency: str = "INR"
    payment_terms: str = "Net 30"
    delivery_terms: str = "Delivered to warehouse dock"
    validity_days: int = 30
    response_deadline: date | None = None
    status: RFIStatus = RFIStatus.DRAFT
    source: str = "api"
    requirements: list[PackagingRequirement] = Field(default_factory=list)


class RFIUpdateRequest(BaseModel):
    """Update payload for editable RFI fields."""

    title: str | None = None
    category: str | None = None
    scope: str | None = None
    currency: str | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    validity_days: int | None = None
    response_deadline: date | None = None
    status: RFIStatus | None = None


class RFIDetailResponse(BaseModel):
    """Full detail view of an RFI including items and status."""

    id: int
    title: str
    category: str
    scope: str | None = None
    currency: str
    payment_terms: str | None = None
    delivery_terms: str | None = None
    validity_days: int | None = None
    response_deadline: str | None = None
    status: str
    source: str | None = None
    triggered_at: str | None = None
    created_at: str
    updated_at: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class RFITriggerResponse(BaseModel):
    """Result of triggering an RFI."""

    id: int
    title: str
    status: str
    triggered_at: str
    message: str
