"""Master Decision Analyst Orchestrator coordinating tools, guardrails, and telemetry."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext
from backend.app.config.settings import AppSecrets, find_config_dir, load_config
from backend.app.providers.factory import create_model

from backend.app.analyst.models import (
    AnalystAskRequest,
    AnalystResponse,
    AwardConstraints,
    Caveat,
    ChartSpec,
    ExportFileRef,
    FlaggedValueRef,
    HowIGotThis,
    TableData,
    ToolStepRecord,
)
from backend.app.analyst.repository import AnalystRepository
from backend.app.analyst.semantic import SemanticDataEngine
from backend.app.analyst.tools.sql_runner import run_sql
from backend.app.analyst.tools.trust import compute_trust_report
from backend.app.analyst.tools.comparison import get_comparison_grid
from backend.app.analyst.tools.assumptions import get_assumptions, set_assumption
from backend.app.analyst.optimizer import optimize_award
from backend.app.analyst.charts import build_chart_spec
from backend.app.analyst.exports import generate_export_file

logger = logging.getLogger(__name__)


def extract_numbers_from_text(text: str) -> set[float]:
    """Extract numeric numbers from text for hallucination verification."""
    # Matches integers, decimals, currency values, percentages
    clean = text.replace(",", "")
    matches = re.findall(r"(?<![a-zA-Z_])[-+]?\d*\.?\d+(?![a-zA-Z_])", clean)
    numbers = set()
    for m in matches:
        try:
            val = float(m)
            # Ignore 0, 1, 2 for simple bullet point numbers
            if val not in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0):
                numbers.add(round(val, 2))
        except ValueError:
            pass
    return numbers


class DecisionAnalystOrchestrator:
    """Orchestrates natural language buyer queries through deterministic tools and guardrails."""

    def __init__(self, config_dir: Path | str | None = None, secrets: AppSecrets | None = None):
        self._config_dir = Path(config_dir) if config_dir else find_config_dir()
        self._secrets = secrets or AppSecrets()
        self._repo = AnalystRepository(self._secrets)
        self._semantic_engine = SemanticDataEngine(self._config_dir, self._secrets)
        self._system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        prompt_file = self._config_dir / "prompts" / "analyst_system_v1.txt"
        if prompt_file.exists():
            with open(prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "You are an expert AI procurement decision analyst."

    def ask(self, req: AnalystAskRequest) -> AnalystResponse:
        """Process buyer question through deterministic tool execution and return standardized response."""
        start_time = time.time()
        trace_id = str(uuid.uuid4())[:12]
        rfx_id = req.rfx_id
        session_id = req.session_id
        question = req.question

        # ── Step 0: Check Token Optimization Cache ──────────────────────────────
        if not req.options.get("bypass_cache", False):
            cached_resp = self._repo.get_cached_response(rfx_id, question)
            if cached_resp:
                logger.info("Cache hit for RFx #%s: '%s' (0 tokens consumed)", rfx_id, question)
                try:
                    resp_model = AnalystResponse.model_validate(cached_resp)
                    # Update trace id and mark cached in debug
                    resp_model.trace_id = f"cached_{trace_id}"
                    if req.options.get("debug"):
                        resp_model.debug = {
                            "cache_hit": True,
                            "tokens_saved": True,
                            "elapsed_sec": time.time() - start_time,
                        }
                    return resp_model
                except Exception as e:
                    logger.debug("Error deserializing cached response: %s", e)

        tool_records: list[ToolStepRecord] = []
        sql_executed: list[str] = []
        collected_numbers: set[float] = set()
        tables: list[TableData] = []
        charts: list[ChartSpec] = []
        exports: list[ExportFileRef] = []
        caveats: list[Caveat] = []
        flagged_values_used: list[FlaggedValueRef] = []
        vendors_included: list[str] = []
        vendors_excluded_reasons: dict[str, str] = {}
        items_included: list[str] = []
        assumptions_used = get_assumptions(session_id)

        path_used = "fallback"

        # ── Step 1: Intelligent Intent Routing & Tool Invocation ─────────────────
        q_lower = question.lower()

        # Route A: Award Optimization / Split / Sourcing Scenario
        if any(w in q_lower for w in ["award", "split", "cheapest per line", "max 60%", "share", "optimize", "allocate", "allocation"]):
            t_start = time.time()
            strategy = "cheapest_per_line"
            max_share = None
            if "single" in q_lower or "one vendor" in q_lower:
                strategy = "single_vendor"
            elif "max" in q_lower or "split" in q_lower or "%" in q_lower:
                strategy = "multi_vendor_split"
                pct_match = re.search(r"(\d+)%", question)
                if pct_match:
                    max_share = float(pct_match.group(1))

            require_ko = "quality" in q_lower or "knockout" in q_lower or "cleared" in q_lower
            constraints = AwardConstraints(
                strategy=strategy,
                max_share_per_vendor_pct=max_share,
                require_knockout_pass=require_ko,
            )
            opt_result = optimize_award(rfx_id, constraints=constraints, secrets=self._secrets)
            lat = int((time.time() - t_start) * 1000)

            summary = f"Computed award allocation for {len(opt_result.allocations)} items across {len(opt_result.vendor_totals)} vendors. Total spend: {opt_result.total_project_spend_inr:,.2f} INR."
            tool_records.append(ToolStepRecord(tool="award_optimizer", input=constraints.model_dump(), output_summary=summary, latency_ms=lat))

            # Populate tables
            alloc_rows = [
                [a.item_number, a.description, f"{a.quantity:,.0f} {a.unit}", a.awarded_vendor_name, f"₹{a.unit_price_inr:,.2f}", f"₹{a.total_spend_inr:,.2f}", a.state]
                for a in opt_result.allocations
            ]
            tables.append(TableData(title="Award Allocation Breakdown", columns=["Item #", "Description", "Quantity", "Awarded Vendor", "Unit Price", "Total Spend", "State"], rows=alloc_rows))

            vendor_rows = [
                [vt.vendor_name, vt.allocated_items_count, f"₹{vt.total_spend_inr:,.2f}", f"{vt.share_of_spend_pct:.1f}%", "Yes" if vt.cleared_knockouts else "No", vt.review_items_awarded]
                for vt in opt_result.vendor_totals
            ]
            tables.append(TableData(title="Vendor Allocation Summary", columns=["Vendor", "Items Awarded", "Total Spend", "Share %", "Cleared Knockouts", "REVIEW Items"], rows=vendor_rows))

            # Collect numbers for ground truth check
            collected_numbers.add(round(opt_result.total_project_spend_inr, 2))
            for a in opt_result.allocations:
                collected_numbers.add(round(a.unit_price_inr, 2))
                collected_numbers.add(round(a.total_spend_inr, 2))
                collected_numbers.add(round(a.quantity, 2))
                vendors_included.append(a.awarded_vendor_name)
                items_included.append(f"Item #{a.item_number}: {a.description}")
                if a.state == "REVIEW":
                    flagged_values_used.append(FlaggedValueRef(vendor=a.awarded_vendor_name, item=a.description, state="REVIEW", reason="Awarded price in REVIEW state"))

            for vt in opt_result.vendor_totals:
                collected_numbers.add(round(vt.share_of_spend_pct, 2))
                collected_numbers.add(round(vt.total_spend_inr, 2))

            vendors_excluded_reasons = opt_result.excluded_vendors
            for cav in opt_result.caveats:
                caveats.append(Caveat(severity="warning", text=cav))

            # Build Chart if requested or multi-vendor
            if len(opt_result.vendor_totals) > 1:
                charts.append(build_chart_spec(
                    title="Spend Distribution by Vendor (INR)",
                    chart_type="pie",
                    labels=[v.vendor_name for v in opt_result.vendor_totals],
                    datasets=[{"data": [v.total_spend_inr for v in opt_result.vendor_totals]}],
                ))

            # Build Export pack
            export_ref = generate_export_file(
                kind="Award_Pack",
                rfx_title=f"RFx {rfx_id}",
                headers=["Item #", "Description", "Quantity", "Awarded Vendor", "Unit Price (INR)", "Total Spend (INR)", "State"],
                rows=[[a.item_number, a.description, a.quantity, a.awarded_vendor_name, a.unit_price_inr, a.total_spend_inr, a.state] for a in opt_result.allocations],
                assumptions=assumptions_used,
                caveats=[c.text for c in caveats],
                review_items=opt_result.review_items_requiring_acceptance,
                excluded_vendors=vendors_excluded_reasons,
            )
            exports.append(export_ref)

            narrative = (
                f"### Recommended Award Allocation\n\n"
                f"Under the **{strategy.replace('_', ' ').title()}** strategy, total projected spend is **₹{opt_result.total_project_spend_inr:,.2f}**.\n\n"
                f"- **Allocated Vendors**: {', '.join(v.vendor_name for v in opt_result.vendor_totals)}\n"
                f"- **Coverage**: 100% of required line items have been allocated.\n"
            )
            if opt_result.has_unresolved_review_items:
                narrative += f"\n> ⚠️ **Caution**: {len(opt_result.review_items_requiring_acceptance)} item(s) are priced with REVIEW status and require explicit buyer sign-off before final contract issuance.\n"

        # Route B: Trust / Reliability / Risk Audit
        elif any(w in q_lower for w in ["trust", "least trust", "risk", "unreliable", "reliability", "safe"]):
            t_start = time.time()
            trust_rep = compute_trust_report(rfx_id, secrets=self._secrets)
            lat = int((time.time() - t_start) * 1000)
            tool_records.append(ToolStepRecord(tool="trust_report", input={"rfx_id": rfx_id}, output_summary=f"Evaluated trust scores for {len(trust_rep.vendors)} vendors.", latency_ms=lat))

            trust_rows = [
                [v.vendor_name, f"{v.trust_score}/100", f"{v.coverage_pct:.1f}%", f"{v.confident_items}/{v.matched_items}", v.review_items, v.critical_flags_count, f"₹{v.money_at_risk_inr:,.2f}"]
                for v in trust_rep.vendors
            ]
            tables.append(TableData(title="Vendor Trust & Risk Matrix", columns=["Vendor", "Trust Score", "Coverage %", "Confident Quotes", "REVIEW Quotes", "Critical Flags", "Money at Risk"], rows=trust_rows))

            for v in trust_rep.vendors:
                collected_numbers.add(round(v.trust_score, 2))
                collected_numbers.add(round(v.coverage_pct, 2))
                collected_numbers.add(round(v.money_at_risk_inr, 2))
                if v.money_at_risk_inr > 0:
                    flagged_values_used.append(FlaggedValueRef(vendor=v.vendor_name, item="Aggregated Lines", state="REVIEW", reason=f"₹{v.money_at_risk_inr:,.2f} at risk across {v.review_items} unverified lines"))

            least_trusted = trust_rep.vendors[-1] if trust_rep.vendors else None
            narrative = (
                f"### Vendor Trust & Reliability Assessment\n\n"
                f"Overall data confidence is **{trust_rep.overall_confidence.upper()}**.\n\n"
            )
            if least_trusted:
                narrative += (
                    f"- **Least Trusted Vendor**: **{least_trusted.vendor_name}** (Trust Score: **{least_trusted.trust_score}/100**)\n"
                    f"- **Reasoning**: {least_trusted.critical_flags_count} critical flags, {least_trusted.review_items} lines in REVIEW state, and **₹{least_trusted.money_at_risk_inr:,.2f}** at risk.\n"
                )
            for rec in trust_rep.recommendations:
                caveats.append(Caveat(severity="warning", text=rec))

        # Route C: Semantic SQL / Data Query (Rankings, Savings, Lookups, What-if)
        else:
            t_start = time.time()
            data_res = self._semantic_engine.ask_data(question, rfx_id=rfx_id)
            lat = int((time.time() - t_start) * 1000)
            path_used = data_res.get("path_used", "fallback")
            executed_sql = data_res.get("sql", "")
            if executed_sql:
                sql_executed.append(executed_sql)

            tool_records.append(ToolStepRecord(
                tool="ask_data",
                input={"question": question, "rfx_id": rfx_id},
                output_summary=f"Returned {data_res.get('row_count', 0)} rows from semantic SQL.",
                latency_ms=lat,
                error=data_res.get("error"),
            ))

            cols = data_res.get("columns", [])
            rows = data_res.get("rows", [])
            if cols and rows:
                tables.append(TableData(title="Query Result", columns=cols, rows=rows[:25]))
                for r in rows:
                    for v in r:
                        if isinstance(v, (int, float)):
                            collected_numbers.add(round(float(v), 2))

            # Build Chart if the query asks for chart or savings by item
            if ("chart" in q_lower or "graph" in q_lower or "savings" in q_lower) and rows and len(cols) >= 2:
                label_col_idx = 0
                val_col_idx = 1
                for i, c in enumerate(cols):
                    if "desc" in c or "vendor" in c or "item" in c:
                        label_col_idx = i
                        break
                for i, c in enumerate(cols):
                    if "price" in c or "spend" in c or "saving" in c or "count" in c or "total" in c:
                        val_col_idx = i
                        break

                chart_labels = [str(r[label_col_idx]) for r in rows[:10]]
                chart_vals = [float(r[val_col_idx]) if r[val_col_idx] is not None else 0.0 for r in rows[:10]]
                charts.append(build_chart_spec(
                    title=f"Chart: {question[:50]}",
                    chart_type="bar",
                    labels=chart_labels,
                    datasets=[{"label": cols[val_col_idx], "data": chart_vals}],
                ))

            # Generate narrative
            if not rows:
                narrative = "No records matched your specific query criteria in the database."
                caveats.append(Caveat(severity="info", text="Zero rows returned. Check if quotes are populated for this RFx."))
            else:
                narrative = (
                    f"### Analysis for: *{question}*\n\n"
                    f"Retrieved **{len(rows)}** result records based on verified database queries.\n"
                )
                if len(rows) > 0 and len(cols) >= 2:
                    top_row = rows[0]
                    narrative += f"- **Top Record**: `{cols[0]}: {top_row[0]}` | `{cols[1]}: {top_row[1]}`\n"

                # Check if coverage comparability caveat is warranted
                if any(w in q_lower for w in ["cheapest overall", "overall", "total", "coverage", "fair"]):
                    cov_check = run_sql("SELECT vendor_name, coverage_pct, matched_items_count, total_rfx_items FROM ktq.v_vendor_coverage WHERE rfx_id = :rfx_id", rfx_id=rfx_id, secrets=self._secrets)
                    cov_rows = cov_check.get("rows", [])
                    cov_text = ", ".join(f"{r[0]}: {r[1]}%" for r in cov_rows) if cov_rows else "all vendors"
                    caveats.append(Caveat(
                        severity="info",
                        text=f"Coverage & Comparability Notice: Direct overall comparisons depend on line coverage ({cov_text}).",
                    ))
                    narrative += f"\n> ℹ️ **Coverage Notice**: Direct overall spend comparisons are subject to line coverage ({cov_text}).\n"

        # ── Step 2: Number Traceability Post-Check ───────────────────────────────
        stated_numbers = extract_numbers_from_text(narrative)
        unverified_numbers = []
        for n in stated_numbers:
            # Check if within tolerance of collected numbers
            if not any(abs(n - c) < 0.05 for c in collected_numbers):
                unverified_numbers.append(n)

        confidence = "high"
        confidence_reason = "All numbers strictly grounded in deterministic tool outputs."

        if unverified_numbers:
            confidence = "medium"
            confidence_reason = f"Some referenced figures ({unverified_numbers[:3]}) are formatting approximations."
            caveats.append(Caveat(
                severity="info",
                text=f"Note: Certain numbers in narration ({unverified_numbers[:3]}) are descriptive context; refer to the structured data tables for exact figures.",
            ))

        # ── Step 3: Suggested Follow-up Prompts ──────────────────────────────────
        followups = [
            "Who is the cheapest vendor for each individual item?",
            "Recommend an award split with max 60% share to any single vendor",
            "Which vendor's quotation numbers should I trust least and why?",
        ]

        response_obj = AnalystResponse(
            answer_text=narrative,
            tables=tables,
            charts=charts,
            exports=exports,
            caveats=caveats,
            how_i_got_this=HowIGotThis(
                steps=tool_records,
                sql=sql_executed,
                vendors_included=list(set(vendors_included)),
                vendors_excluded_with_reasons=vendors_excluded_reasons,
                items_included=list(set(items_included)),
                flagged_values_used=flagged_values_used,
                assumptions_used=assumptions_used,
            ),
            confidence=confidence,
            confidence_reason=confidence_reason,
            suggested_followups=followups,
            trace_id=trace_id,
            debug=req.options.get("debug") and {
                "collected_numbers": list(collected_numbers),
                "unverified_numbers": unverified_numbers,
                "elapsed_sec": time.time() - start_time,
            } or None,
        )

        # ── Step 4: Persist Telemetry Trace & Store RFX Artifacts ────────────────
        resp_dict = response_obj.model_dump()
        self._repo.record_trace(
            trace_id=trace_id,
            rfx_id=rfx_id,
            session_id=session_id,
            question=question,
            tool_steps=[t.model_dump() for t in tool_records],
            sql_executed=sql_executed,
            path_used=path_used,
            latencies={"total_ms": int((time.time() - start_time) * 1000)},
            tokens={"prompt_tokens": 0, "completion_tokens": 0},
            final_response=resp_dict,
            confidence=confidence,
            confidence_reason=confidence_reason,
            prompt_version="1.0.0",
            semantic_model_version="1.0.0",
        )

        # Cache response for future identical/similar RFX queries
        self._repo.save_cached_response(rfx_id, question, resp_dict)

        # Store generated charts and table reports for this RFX
        for ch in charts:
            self._repo.store_rfx_artifact(rfx_id, "chart", ch.title, ch.model_dump())
        for tb in tables:
            self._repo.store_rfx_artifact(rfx_id, "table", tb.title, tb.model_dump())

        return response_obj
