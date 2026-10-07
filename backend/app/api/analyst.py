"""FastAPI endpoints for Decision Support, Natural Language Analysis, Scenarios, and Feedback."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from backend.app.config.settings import AppSecrets
from backend.app.analyst.models import (
    AnalystAskRequest,
    AnalystFeedbackRequest,
    AnalystResponse,
    CreateScenarioRequest,
    FinalizeScenarioRequest,
    TrustReportResult,
)
from backend.app.analyst.orchestrator import DecisionAnalystOrchestrator
from backend.app.analyst.repository import AnalystRepository
from backend.app.analyst.optimizer import optimize_award
from backend.app.analyst.tools.comparison import get_comparison_grid
from backend.app.analyst.tools.trust import compute_trust_report
from backend.app.analyst.exports import get_export_file_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["decision-analyst"])

# Lazily initialized singletons
_orchestrator: DecisionAnalystOrchestrator | None = None
_repository: AnalystRepository | None = None


def get_orchestrator() -> DecisionAnalystOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = DecisionAnalystOrchestrator()
    return _orchestrator


def get_repository() -> AnalystRepository:
    global _repository
    if _repository is None:
        _repository = AnalystRepository()
    return _repository


# ── Analyst Chat & Q&A Endpoints ─────────────────────────────────────────────

@router.post("/api/analyst/ask", response_model=AnalystResponse)
def ask_analyst(req: AnalystAskRequest) -> AnalystResponse:
    """Ask natural language questions over RFx vendor quotations and get narrated analysis + artifacts."""
    orchestrator = get_orchestrator()
    return orchestrator.ask(req)


@router.get("/api/analyst/sessions/{id}")
def get_session_history(id: str) -> list[dict[str, Any]]:
    """Retrieve full conversation turn history for a session."""
    repo = get_repository()
    return repo.get_session_history(id)


@router.post("/api/analyst/feedback")
def submit_feedback(req: AnalystFeedbackRequest) -> dict[str, Any]:
    """Submit rating and optional SQL/narrative corrections for a specific trace."""
    repo = get_repository()
    feedback_id = repo.record_feedback(req)
    if feedback_id is None:
        raise HTTPException(status_code=500, detail="Failed to save feedback.")
    return {"status": "ok", "feedback_id": feedback_id}


# ── Grid & Trust Endpoints ───────────────────────────────────────────────────

@router.get("/api/rfx/{id}/comparison")
def get_rfx_comparison(id: int) -> dict[str, Any]:
    """Retrieve side-by-side items x vendors comparison grid with evidence crops and totals."""
    grid = get_comparison_grid(id)
    if "error" in grid and grid["error"]:
        raise HTTPException(status_code=404, detail=grid["error"])
    return grid


@router.get("/api/rfx/{id}/trust", response_model=TrustReportResult)
def get_rfx_trust_report(id: int) -> TrustReportResult:
    """Retrieve comprehensive vendor trust scores, coverage %, and money at risk."""
    return compute_trust_report(id)


@router.get("/api/rfx/{id}/artifacts")
def get_rfx_artifacts(id: int) -> dict[str, Any]:
    """Retrieve all stored and cached graphs, charts, and reports generated for a given RFX ID."""
    repo = get_repository()
    artifacts = repo.get_rfx_artifacts(id)
    return {
        "rfx_id": id,
        "count": len(artifacts),
        "artifacts": artifacts,
    }


# ── Award Scenarios & Finalization ───────────────────────────────────────────

@router.post("/api/rfx/{id}/award/scenarios")
def create_award_scenario(id: int, req: CreateScenarioRequest) -> dict[str, Any]:
    """Calculate and store a deterministic award allocation scenario under custom constraints."""
    repo = get_repository()
    result = optimize_award(id, constraints=req.constraints)
    scenario_id = repo.create_scenario(id, req.title, req.constraints, result)
    if scenario_id is None:
        raise HTTPException(status_code=500, detail="Failed to persist scenario.")
    return {
        "scenario_id": scenario_id,
        "title": req.title,
        "constraints": req.constraints.model_dump(),
        "result": result.model_dump(),
    }


@router.get("/api/rfx/{id}/award/scenarios/{sid}")
def get_award_scenario(id: int, sid: int) -> dict[str, Any]:
    """Fetch stored award scenario by ID."""
    repo = get_repository()
    scenario = repo.get_scenario(sid)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return scenario


@router.post("/api/rfx/{id}/award/scenarios/{sid}/finalize")
def finalize_award_scenario(id: int, sid: int, req: FinalizeScenarioRequest) -> dict[str, Any]:
    """Finalize award scenario.

    Blocks if unresolved REVIEW items affect the award unless buyer explicitly submits accepted flags.
    """
    repo = get_repository()
    scenario = repo.get_scenario(sid)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found.")

    totals = scenario.get("totals", {})
    has_review = totals.get("has_unresolved_review_items", False)
    req_items = totals.get("review_items_requiring_acceptance", [])

    if has_review and req_items:
        # Check if all required review items have explicit acceptance
        accepted_item_ids = {str(f.get("item_id")) for f in req.accepted_review_flags}
        missing_acceptances = [
            r for r in req_items if str(r.get("item_id")) not in accepted_item_ids
        ]
        if missing_acceptances:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Cannot finalize scenario: unresolved REVIEW-state prices affect this award.",
                    "unresolved_items": missing_acceptances,
                    "resolution": "Submit accepted_review_flags with explicit buyer sign-off for each flagged item.",
                },
            )

    success = repo.finalize_scenario(sid, req.accepted_review_flags, buyer_name=req.buyer_name)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to finalize award scenario.")

    return {
        "status": "finalized",
        "scenario_id": sid,
        "accepted_flags_count": len(req.accepted_review_flags),
        "finalized_by": req.buyer_name,
    }


# ── File Download Endpoint ───────────────────────────────────────────────────

@router.get("/api/exports/{id}")
def download_export_file(id: str) -> FileResponse:
    """Download exported XLSX / CSV file by unique export ID."""
    file_path = get_export_file_path(id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found or expired.")
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
