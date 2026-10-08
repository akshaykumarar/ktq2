"""Pydantic data models for the Step 3 Decision Analyst, Scenarios, and Observability."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


# ── Response Contract Models ─────────────────────────────────────────────────

class TableData(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[Any]]


class ChartSpec(BaseModel):
    title: str
    type: Literal["bar", "grouped_bar", "line", "scatter", "stacked_bar", "pie"]
    spec: dict[str, Any]  # Chart.js spec or renderable config


class ExportFileRef(BaseModel):
    filename: str
    url: str
    kind: str = "xlsx"  # xlsx, csv, pdf


class Caveat(BaseModel):
    severity: Literal["info", "warning", "critical"]
    text: str
    refs: list[str] = Field(default_factory=list)


class FlaggedValueRef(BaseModel):
    vendor: str
    item: str
    state: str
    reason: str | None = None
    evidence_url: str | None = None


class ToolStepRecord(BaseModel):
    tool: str
    input: dict[str, Any] | str
    output_summary: str
    latency_ms: int = 0
    error: str | None = None


class HowIGotThis(BaseModel):
    steps: list[ToolStepRecord] = Field(default_factory=list)
    sql: list[str] = Field(default_factory=list)
    vendors_included: list[str] = Field(default_factory=list)
    vendors_excluded_with_reasons: dict[str, str] = Field(default_factory=dict)
    items_included: list[str] = Field(default_factory=list)
    flagged_values_used: list[FlaggedValueRef] = Field(default_factory=list)
    assumptions_used: dict[str, Any] = Field(default_factory=dict)


class AnalystAskRequest(BaseModel):
    rfx_id: int
    session_id: str
    question: str
    options: dict[str, Any] = Field(default_factory=dict)


class AnalystResponse(BaseModel):
    answer_text: str
    tables: list[TableData] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    exports: list[ExportFileRef] = Field(default_factory=list)
    caveats: list[Caveat] = Field(default_factory=list)
    how_i_got_this: HowIGotThis = Field(default_factory=HowIGotThis)
    confidence: Literal["high", "medium", "low"] = "high"
    confidence_reason: str = "All numbers verified against database tools."
    suggested_followups: list[str] = Field(default_factory=list)
    trace_id: str
    debug: dict[str, Any] | None = None


# ── Feedback Models ──────────────────────────────────────────────────────────

class AnalystFeedbackRequest(BaseModel):
    trace_id: str
    rating: int = Field(description="1 for upvote, -1 for downvote, or 1 to 5")
    comment: str | None = None
    corrected_sql: str | None = None
    corrected_answer: str | None = None


class FeedbackRecord(BaseModel):
    id: int
    trace_id: str
    rating: int
    comment: str | None
    corrected_sql: str | None
    corrected_answer: str | None
    created_at: datetime


# ── Optimizer & Scenario Models ──────────────────────────────────────────────

class AwardConstraints(BaseModel):
    strategy: Literal["cheapest_per_line", "single_vendor", "multi_vendor_split"] = "cheapest_per_line"
    max_share_per_vendor_pct: float | None = Field(default=None, description="Maximum allocation percentage to any single vendor (e.g. 60.0)")
    require_knockout_pass: bool = Field(default=True, description="Only allocate to vendors who cleared all knockout questions")
    include_review_prices: bool = Field(default=True, description="Allow REVIEW-state prices to be awarded")
    excluded_vendor_ids: list[int] = Field(default_factory=list)
    price_overrides: dict[str, float] = Field(default_factory=dict, description="vendor_id:item_number -> new unit price")
    freight_override_per_unit: float | None = None
    fx_rate_overrides: dict[str, float] = Field(default_factory=dict, description="Currency -> INR rate")


class LineAllocation(BaseModel):
    item_id: int
    item_number: int
    description: str
    quantity: float
    unit: str
    awarded_vendor_id: int
    awarded_vendor_name: str
    unit_price_inr: float
    total_spend_inr: float
    state: str
    flags: list[str] = Field(default_factory=list)
    is_l1: bool = True
    l1_unit_price_inr: float | None = None
    prior_year_unit_price_inr: float | None = None
    savings_vs_prior_year_inr: float | None = None
    savings_vs_l1_inr: float | None = None


class VendorAllocationTotal(BaseModel):
    vendor_id: int
    vendor_name: str
    allocated_items_count: int
    total_spend_inr: float
    share_of_spend_pct: float
    cleared_knockouts: bool
    review_items_awarded: int = 0


class AwardOptimizationResult(BaseModel):
    allocations: list[LineAllocation]
    vendor_totals: list[VendorAllocationTotal]
    total_project_spend_inr: float
    total_savings_vs_prior_year_inr: float | None = None
    excluded_vendors: dict[str, str] = Field(default_factory=dict, description="Vendor name -> reason")
    caveats: list[str] = Field(default_factory=list)
    has_unresolved_review_items: bool = False
    review_items_requiring_acceptance: list[dict[str, Any]] = Field(default_factory=list)


class CreateScenarioRequest(BaseModel):
    title: str = "Award Scenario"
    constraints: AwardConstraints = Field(default_factory=AwardConstraints)


class FinalizeScenarioRequest(BaseModel):
    accepted_review_flags: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Explicit acceptance of review-state prices: [{vendor_id, item_id, reason}]"
    )
    buyer_name: str = "buyer"


# ── Trust & Comparison Models ────────────────────────────────────────────────

class VendorTrustSummary(BaseModel):
    vendor_id: int
    vendor_name: str
    coverage_pct: float
    total_items: int
    matched_items: int
    confident_items: int
    review_items: int
    missing_items: int
    critical_flags_count: int
    warning_flags_count: int
    knockouts_status: Literal["passed", "failed", "unresolved", "no_questions"]
    trust_score: float = Field(description="Computed reliability score 0-100")
    score_reasoning: str = Field(default="", description="Detailed human-readable explanation of how the score was calculated")
    risk_level: str = Field(default="Low Risk", description="Risk classification: Low Risk, Medium Risk, High Risk")
    total_quoted_spend_inr: float = Field(default=0.0, description="Total quoted spend in INR")
    money_at_risk_inr: float = Field(default=0.0, description="Estimated spend tied to REVIEW/flagged lines")
    flags_list: list[str] = Field(default_factory=list)


class TrustReportResult(BaseModel):
    rfx_id: int
    rfx_title: str
    overall_confidence: str
    vendors: list[VendorTrustSummary]
    critical_open_flags: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
