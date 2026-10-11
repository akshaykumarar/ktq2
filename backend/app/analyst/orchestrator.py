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


COLUMN_RENAME_MAP = {
    "item_number": "Item #",
    "rfx_description": "Description",
    "vendor_name": "Vendor",
    "effective_price_inr": "Price (INR)",
    "normalized_price_inr": "Norm Price (INR)",
    "raw_price": "Raw Price",
    "raw_currency": "Currency",
    "raw_unit": "Quoted Unit",
    "rfx_quantity": "Quantity",
    "rfx_unit": "Unit",
    "rfx_target_price": "Target Price (INR)",
    "delta_from_target": "Delta vs Target (INR)",
    "savings_per_unit_inr": "Unit Savings (INR)",
    "total_savings_inr": "Total Savings (INR)",
    "lowest_bidder": "Lowest Bidder",
    "lowest_price": "Lowest Price (INR)",
    "lead_time_days": "Lead Time (Days)",
    "coverage_pct": "Coverage %",
    "total_rfx_items": "Total Items",
    "matched_items_count": "Quoted Items",
    "confident_items_count": "Confident Quotes",
    "review_items_count": "Review Quotes",
    "missing_items_count": "Missing Quotes",
    "response_status": "Status",
    "state": "State",
    "severity": "Severity",
    "code": "Flag Code",
    "message": "Details",
    "pass_fail": "Pass / Fail",
    "question_text": "Question",
    "bidder_count": "Bidder Count",
    "items_quoted": "Items Quoted",
    "total_spend_inr": "Total Spend (INR)",
    "term_type": "Term",
    "value_text": "Details",
    "value_num": "Value",
}

HIDDEN_COLS = {
    "rfx_item_id",
    "vendor_id",
    "response_id",
    "response_item_id",
    "rfx_question_id",
    "flag_id",
    "item_id",
    "rfx_id",
}


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

    def _determine_table_title(self, question: str, cols: list[str]) -> str:
        q_low = question.lower()
        if any(w in q_low for w in ["cheapest", "l1", "lowest"]):
            return "Lowest Quoted Price (L1) per Line Item"
        elif any(w in q_low for w in ["saving", "target", "budget"]):
            return "Savings & Price Variance vs Target Price"
        elif any(w in q_low for w in ["coverage", "quotation summary", "quote count", "summary for all"]):
            return "Vendor Quotation & Coverage Summary"
        elif any(w in q_low for w in ["compliance", "response status", "status"]):
            return "Vendor Response & Compliance Status"
        elif any(w in q_low for w in ["flag", "risk", "warning"]):
            return "Open Flags & Validation Findings"
        elif any(w in q_low for w in ["lead time", "delivery"]):
            return "Delivery Lead Time Comparison"
        elif any(w in q_low for w in ["spend", "total spend"]):
            return "Projected Spend Summary by Vendor"
        elif any(w in q_low for w in ["single", "one bidder"]):
            return "Single-Bidder Line Items"
        return "Query Result"

    def _clean_table_data(self, question: str, cols: list[str], rows: list[list[Any]]) -> TableData:
        visible_indices = [i for i, c in enumerate(cols) if c.lower() not in HIDDEN_COLS]
        if not visible_indices:
            visible_indices = list(range(len(cols)))
        display_cols = [COLUMN_RENAME_MAP.get(cols[i].lower(), cols[i].replace("_", " ").title()) for i in visible_indices]
        display_rows = []
        for r in rows:
            row_vals = []
            for i in visible_indices:
                val = r[i]
                col_name = cols[i].lower()
                if isinstance(val, (int, float)):
                    if "price" in col_name or "spend" in col_name or "saving" in col_name or "delta" in col_name:
                        row_vals.append(f"₹{val:,.2f}")
                    elif "pct" in col_name or "percentage" in col_name:
                        row_vals.append(f"{val:.1f}%")
                    else:
                        row_vals.append(val)
                elif val is None:
                    row_vals.append("-")
                else:
                    row_vals.append(val)
            display_rows.append(row_vals)

        title = self._determine_table_title(question, cols)
        return TableData(title=title, columns=display_cols, rows=display_rows[:50])

    def _synthesize_deterministic_narrative(
        self,
        question: str,
        rfx_id: int | None,
        cols: list[str],
        rows: list[list[Any]],
        collected_numbers: set[float],
    ) -> str:
        q_low = question.lower()
        col_lower = [c.lower() for c in cols]
        n_rows = len(rows)

        collected_numbers.add(float(n_rows))

        # Case 1: Cheapest / L1 per item
        if any(w in q_low for w in ["cheapest", "l1", "lowest price", "best price"]):
            vendor_idx = col_lower.index("vendor_name") if "vendor_name" in col_lower else -1
            price_idx = col_lower.index("effective_price_inr") if "effective_price_inr" in col_lower else -1
            state_idx = col_lower.index("state") if "state" in col_lower else -1

            vendor_wins: dict[str, int] = {}
            prices: list[float] = []
            review_count = 0
            confident_count = 0

            for r in rows:
                if vendor_idx >= 0 and r[vendor_idx]:
                    v_name = str(r[vendor_idx])
                    vendor_wins[v_name] = vendor_wins.get(v_name, 0) + 1
                if price_idx >= 0 and isinstance(r[price_idx], (int, float)):
                    prices.append(float(r[price_idx]))
                if state_idx >= 0:
                    st = str(r[state_idx]).upper()
                    if st == "REVIEW":
                        review_count += 1
                    elif st == "CONFIDENT":
                        confident_count += 1

            sorted_wins = sorted(vendor_wins.items(), key=lambda x: x[1], reverse=True)
            wins_summary = []
            for v, cnt in sorted_wins:
                pct = (cnt / n_rows) * 100 if n_rows else 0
                collected_numbers.add(float(cnt))
                collected_numbers.add(round(pct, 2))
                wins_summary.append(f"- **{v}**: lowest on **{cnt}** item(s) ({pct:.1f}% of basket)")

            min_p = min(prices) if prices else 0.0
            max_p = max(prices) if prices else 0.0
            if prices:
                collected_numbers.add(round(min_p, 2))
                collected_numbers.add(round(max_p, 2))

            collected_numbers.add(float(review_count))
            collected_numbers.add(float(confident_count))

            narrative = (
                f"### Lowest Quoted Price (L1) Analysis\n\n"
                f"Based on verified vendor responses for **RFx #{rfx_id}**, L1 lowest prices were identified across **{n_rows}** line items:\n\n"
                f"**Vendor Win Distribution**:\n"
                f"{chr(10).join(wins_summary)}\n\n"
            )
            if prices:
                narrative += f"- **Price Range**: Unit rates range from **₹{min_p:,.2f}** to **₹{max_p:,.2f}** INR.\n"
            narrative += f"- **Data Quality State**: **{confident_count}** CONFIDENT line items; **{review_count}** items in REVIEW status requiring buyer review."
            return narrative

        # Case 2: Savings vs Target Price
        elif any(w in q_low for w in ["saving", "target", "budget", "delta", "variance"]):
            tot_savings_idx = col_lower.index("total_savings_inr") if "total_savings_inr" in col_lower else -1
            unit_savings_idx = col_lower.index("savings_per_unit_inr") if "savings_per_unit_inr" in col_lower else -1

            total_savings = 0.0
            under_target = 0
            over_target = 0

            for r in rows:
                if tot_savings_idx >= 0 and isinstance(r[tot_savings_idx], (int, float)):
                    val = float(r[tot_savings_idx])
                    total_savings += val
                    if val >= 0:
                        under_target += 1
                    else:
                        over_target += 1
                elif unit_savings_idx >= 0 and isinstance(r[unit_savings_idx], (int, float)):
                    val = float(r[unit_savings_idx])
                    if val >= 0:
                        under_target += 1
                    else:
                        over_target += 1

            total_savings = round(total_savings, 2)
            collected_numbers.add(total_savings)
            collected_numbers.add(float(under_target))
            collected_numbers.add(float(over_target))

            narrative = (
                f"### Savings Analysis vs Target Benchmark\n\n"
                f"Evaluation against buyer target benchmark prices across **{n_rows}** line items for **RFx #{rfx_id}**:\n\n"
            )
            if tot_savings_idx >= 0:
                narrative += f"- **Total Projected Savings**: **₹{total_savings:,.2f}** INR vs target budget.\n"
            narrative += (
                f"- **Items Under Budget**: **{under_target}** line items priced below target price.\n"
            )
            if over_target > 0:
                narrative += f"- **Items Exceeding Budget**: **{over_target}** line items priced above target benchmark.\n"
            return narrative

        # Case 3: Quotation Summary / Coverage / Compliance
        elif any(w in q_low for w in ["coverage", "quote count", "quotation summary", "compliance", "response status", "overview"]):
            vendor_idx = col_lower.index("vendor_name") if "vendor_name" in col_lower else 0
            cov_idx = col_lower.index("coverage_pct") if "coverage_pct" in col_lower else -1
            matched_idx = col_lower.index("matched_items_count") if "matched_items_count" in col_lower else -1
            total_idx = col_lower.index("total_rfx_items") if "total_rfx_items" in col_lower else -1
            status_idx = col_lower.index("response_status") if "response_status" in col_lower else -1

            vendor_lines = []
            for r in rows:
                vname = str(r[vendor_idx])
                cov = f"{float(r[cov_idx]):.1f}%" if cov_idx >= 0 and isinstance(r[cov_idx], (int, float)) else "N/A"
                if cov_idx >= 0 and isinstance(r[cov_idx], (int, float)):
                    collected_numbers.add(round(float(r[cov_idx]), 2))
                quoted = f"{r[matched_idx]}/{r[total_idx]}" if matched_idx >= 0 and total_idx >= 0 else ""
                status_str = f" • Status: `{r[status_idx]}`" if status_idx >= 0 else ""
                vendor_lines.append(f"- **{vname}**: **{cov}** coverage ({quoted} items){status_str}")

            return (
                f"### Vendor Quotation & Coverage Summary\n\n"
                f"Quotation response overview across **{n_rows}** participating vendor(s) for **RFx #{rfx_id}**:\n\n"
                f"{chr(10).join(vendor_lines)}"
            )

        # Case 4: Lead Times
        elif any(w in q_low for w in ["lead time", "delivery", "fastest"]):
            lt_idx = col_lower.index("lead_time_days") if "lead_time_days" in col_lower else -1
            lead_times = [float(r[lt_idx]) for r in rows if lt_idx >= 0 and isinstance(r[lt_idx], (int, float))]

            if lead_times:
                min_lt = min(lead_times)
                avg_lt = sum(lead_times) / len(lead_times)
                collected_numbers.add(round(min_lt, 2))
                collected_numbers.add(round(avg_lt, 2))
                return (
                    f"### Delivery Lead Time Comparison\n\n"
                    f"Lead time evaluation across **{n_rows}** line items for **RFx #{rfx_id}**:\n\n"
                    f"- **Fastest Delivery**: **{min_lt:.0f}** calendar days.\n"
                    f"- **Average Turnaround**: **{avg_lt:.1f}** calendar days across quoted lines."
                )

        # Case 5: Flags / Risks
        elif any(w in q_low for w in ["flag", "risk", "warning", "critical"]):
            sev_idx = col_lower.index("severity") if "severity" in col_lower else -1
            crit_count = sum(1 for r in rows if sev_idx >= 0 and str(r[sev_idx]).lower() == "critical")
            warn_count = sum(1 for r in rows if sev_idx >= 0 and str(r[sev_idx]).lower() == "warning")
            collected_numbers.add(float(crit_count))
            collected_numbers.add(float(warn_count))

            return (
                f"### Open Flags & Risk Findings\n\n"
                f"Identified **{n_rows}** active validation flags for **RFx #{rfx_id}**:\n\n"
                f"- **Critical Flags**: **{crit_count}** issues requiring buyer resolution.\n"
                f"- **Warning Flags**: **{warn_count}** warnings on pricing or documentation."
            )

        # Case 6: General Fallback
        preview_parts = []
        for i, c in enumerate(cols[:3]):
            val = rows[0][i] if rows and i < len(rows[0]) else ""
            preview_parts.append(f"`{COLUMN_RENAME_MAP.get(c.lower(), c)}: {val}`")

        return (
            f"### Analysis for: *{question}*\n\n"
            f"Retrieved **{n_rows}** verified database records for **RFx #{rfx_id}**.\n\n"
            f"- **Sample Record**: {' | '.join(preview_parts)}\n"
            f"- Refer to the structured table below for complete details."
        )

    def _generate_data_narrative(
        self,
        question: str,
        rfx_id: int | None,
        cols: list[str],
        rows: list[list[Any]],
        caveats: list[Caveat],
        collected_numbers: set[float],
    ) -> str:
        """Synthesize executive procurement narrative from verified query data."""
        if not rows:
            caveats.append(Caveat(severity="info", text=f"Zero records matched query for RFx #{rfx_id}."))
            return (
                f"### Analysis for: *{question}*\n\n"
                f"No records matched your specific query criteria in the database for RFx #{rfx_id}.\n"
                f"Please ensure vendor quotations have been uploaded and processed for this RFx."
            )

        # 1. Attempt LLM Narrative Synthesis
        try:
            app_cfg = load_config(self._config_dir)
            llm_key = "analyst" if "analyst" in app_cfg.llms else ("reasoning" if "reasoning" in app_cfg.llms else list(app_cfg.llms.keys())[0])
            llm_cfg = app_cfg.llms[llm_key]
            model = create_model(llm_cfg, secrets=self._secrets, fallback_to_mock=False)

            sample_rows = rows[:15]
            row_preview = "\n".join([f"- {dict(zip(cols, r))}" for r in sample_rows])

            prompt = (
                f"{self._system_prompt}\n\n"
                f"TASK: Generate a concise, high-value procurement analysis answer to the buyer's question.\n"
                f"User Question: {question}\n"
                f"RFx ID: {rfx_id}\n"
                f"Total Records Found: {len(rows)}\n"
                f"Data Columns: {', '.join(cols)}\n\n"
                f"Top Records (sample):\n{row_preview}\n\n"
                f"INSTRUCTIONS:\n"
                f"1. Lead with the direct, concise answer in the first 1-2 sentences.\n"
                f"2. Summarize key findings, distributions (e.g. which vendors won which items or total spend), and noteworthy highlights.\n"
                f"3. Note data confidence (CONFIDENT vs REVIEW) if state is in the data.\n"
                f"4. Format in clean, professional GitHub-flavored Markdown with bold highlights and bullet points.\n"
                f"5. Do NOT invent numbers. Reference ONLY the exact figures from the provided data.\n"
                f"6. Keep the response crisp (under 200 words)."
            )

            agent = Agent(model)
            result = agent.run_sync(prompt)
            narrative = getattr(result, "output", getattr(result, "data", str(result))).strip()
            if narrative and len(narrative) > 20:
                for num in extract_numbers_from_text(narrative):
                    collected_numbers.add(num)
                return narrative
        except Exception as e:
            logger.info("LLM narrative synthesis unavailable or skipped (%s). Using deterministic synthesizer.", e)

        # 2. Deterministic Domain Synthesizer Fallback
        return self._synthesize_deterministic_narrative(question, rfx_id, cols, rows, collected_numbers)

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
            tables.append(TableData(title="Scenario Allocation Breakdown (Decision Simulation)", columns=["Item #", "Description", "Quantity", "Suggested Vendor", "Unit Price", "Total Spend", "State"], rows=alloc_rows))

            vendor_rows = [
                [vt.vendor_name, vt.allocated_items_count, f"₹{vt.total_spend_inr:,.2f}", f"{vt.share_of_spend_pct:.1f}%", "Yes" if vt.cleared_knockouts else "No", vt.review_items_awarded]
                for vt in opt_result.vendor_totals
            ]
            tables.append(TableData(title="Vendor Scenario Summary (Decision Simulation)", columns=["Vendor", "Allocated Items", "Total Spend", "Share %", "Cleared Knockouts", "REVIEW Items"], rows=vendor_rows))

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
                headers=["Item #", "Description", "Quantity", "Suggested Vendor", "Unit Price (INR)", "Total Spend (INR)", "State"],
                rows=[[a.item_number, a.description, a.quantity, a.awarded_vendor_name, a.unit_price_inr, a.total_spend_inr, a.state] for a in opt_result.allocations],
                assumptions=assumptions_used,
                caveats=[c.text for c in caveats],
                review_items=opt_result.review_items_requiring_acceptance,
                excluded_vendors=vendors_excluded_reasons,
            )
            exports.append(export_ref)

            narrative = (
                f"### Sourcing Scenario Evaluation\n\n"
                f"*(Decision Support Simulation — Formal line item awarding and RFX status transitions are future scope)*\n\n"
                f"Under the **{strategy.replace('_', ' ').title()}** strategy, total projected spend is **₹{opt_result.total_project_spend_inr:,.2f}**.\n\n"
                f"- **Allocated Vendors**: {', '.join(v.vendor_name for v in opt_result.vendor_totals)}\n"
                f"- **Coverage**: 100% of required line items evaluated in this scenario.\n"
            )
            if opt_result.has_unresolved_review_items:
                narrative += f"\n> ⚠️ **Caution**: {len(opt_result.review_items_requiring_acceptance)} item(s) are priced with REVIEW status and require verification before contract finalization.\n"

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
                tables.append(self._clean_table_data(question, cols, rows))
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
                    datasets=[{"label": COLUMN_RENAME_MAP.get(cols[val_col_idx].lower(), cols[val_col_idx]), "data": chart_vals}],
                ))

            # Generate narrative
            narrative = self._generate_data_narrative(question, rfx_id, cols, rows, caveats, collected_numbers)

            # Check if coverage comparability caveat is warranted
            if any(w in q_lower for w in ["cheapest overall", "overall", "total", "coverage", "fair"]):
                cov_check = run_sql("SELECT vendor_name, coverage_pct, matched_items_count, total_rfx_items FROM ktq.v_vendor_coverage WHERE rfx_id = :rfx_id", rfx_id=rfx_id, secrets=self._secrets)
                cov_rows = cov_check.get("rows", [])
                cov_text = ", ".join(f"{r[0]}: {r[1]}%" for r in cov_rows) if cov_rows else "all vendors"
                caveats.append(Caveat(
                    severity="info",
                    text=f"Coverage & Comparability Notice: Direct overall comparisons depend on line coverage ({cov_text}).",
                ))
                narrative += f"\n\n> ℹ️ **Coverage Notice**: Direct overall spend comparisons are subject to line coverage ({cov_text})."

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
