"""Semantic Text-to-SQL Engine with schema-aware prompt engineering, few-shot retrieval, and retry loop."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
import yaml

from pydantic_ai import Agent
from backend.app.config.settings import AppSecrets, find_config_dir, load_config
from backend.app.providers.factory import create_model
from backend.app.analyst.tools.sql_runner import run_sql, validate_sql_query

logger = logging.getLogger(__name__)


class SemanticDataEngine:
    """Orchestrates natural language to validated SQL conversion using semantic MDL models and examples."""

    def __init__(self, config_dir: Path | str | None = None, secrets: AppSecrets | None = None):
        self._config_dir = Path(config_dir) if config_dir else find_config_dir()
        self._secrets = secrets or AppSecrets()
        self._model_yaml = self._config_dir / "wren_semantic_model.yaml"
        self._examples_yaml = self._config_dir / "wren_examples.yaml"
        self._semantic_context = self._load_semantic_context()
        self._examples = self._load_examples()

    def _load_semantic_context(self) -> str:
        """Load semantic model documentation and business rules into prompt context."""
        if not self._model_yaml.exists():
            return "Tables: ktq.v_rfx_comparison, ktq.v_vendor_coverage, ktq.v_open_flags, ktq.response_answers"

        with open(self._model_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        models = data.get("models", [])
        rules = data.get("rules", [])

        lines = ["=== POSTGRESQL SCHEMA & SEMANTIC MODELS ==="]
        for m in models:
            lines.append(f"\nModel/View: {m['table_name']}")
            lines.append(f"Description: {m.get('description', '').strip()}")
            lines.append("Columns:")
            for col in m.get("columns", []):
                lines.append(f"  - {col['name']} ({col['type']}): {col.get('description', '').strip()}")

        lines.append("\n=== BUSINESS RULES ===")
        for r in rules:
            lines.append(f"- {r}")

        return "\n".join(lines)

    def _load_examples(self) -> list[dict[str, Any]]:
        """Load few-shot examples from YAML."""
        if not self._examples_yaml.exists():
            return []
        with open(self._examples_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("examples", [])

    def _select_relevant_examples(self, question: str, max_examples: int = 4) -> list[dict[str, Any]]:
        """Select top few-shot examples matching keywords in the user question."""
        q_lower = question.lower()
        scored = []
        for ex in self._examples:
            score = 0
            ex_q = ex.get("question", "").lower()
            tokens = re.findall(r"\w+", ex_q)
            for tok in tokens:
                if len(tok) > 3 and tok in q_lower:
                    score += 1
            scored.append((score, ex))

        # Sort descending by match score
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:max_examples]]

    def generate_sql(self, question: str, rfx_id: int | None = None) -> tuple[str, str]:
        """Convert a user question into validated PostgreSQL query.

        Returns:
            (sql_string, path_used)
        """
        # 1. Check if Wren native engine is accessible
        try:
            import wren
            # If wren is installed and compiled, could route here
            wren_available = False
        except ImportError:
            wren_available = False

        path_used = "wren" if wren_available else "fallback"

        # Normalize question for matching
        def _normalize_q(s: str) -> str:
            clean = re.sub(r"[^\w\s]", " ", s.lower())
            words = [w for w in clean.split() if w not in {"the", "a", "an", "for", "please", "all", "me", "show"}]
            return " ".join(words)

        q_norm = _normalize_q(question)
        # Check for direct example match (exact or normalized)
        for ex in self._examples:
            ex_q = ex.get("question", "").strip()
            if ex_q.lower() == question.strip().lower() or _normalize_q(ex_q) == q_norm:
                raw_sql = ex.get("sql", "").strip()
                return raw_sql, path_used

        # 2. Build prompt with schema and few-shot examples
        relevant_ex = self._select_relevant_examples(question)
        examples_str = "\n".join([
            f"Question: {e['question']}\nSQL:\n{e['sql'].strip()}\n"
            for e in relevant_ex
        ])

        system_prompt = (
            "You are a PostgreSQL Text-to-SQL expert generating queries for an RFx procurement analysis system.\n"
            "Generate ONLY a valid, single PostgreSQL SELECT query without markdown fences or commentary.\n"
            "Always include 'WHERE rfx_id = :rfx_id' when filtering on project scope.\n"
            "Always use 'effective_price_inr' for prices and 'state' alongside prices.\n\n"
            f"{self._semantic_context}\n\n"
            "=== FEW-SHOT EXAMPLES ===\n"
            f"{examples_str}\n"
        )

        user_prompt = f"User Question: {question}\nGenerate PostgreSQL SELECT query:"

        try:
            app_cfg = load_config(self._config_dir)
            # Use configured analyst or default llm
            llm_key = "analyst" if "analyst" in app_cfg.llms else "default"
            llm_cfg = app_cfg.llms.get(llm_key, app_cfg.llms[list(app_cfg.llms.keys())[0]])
            model = create_model(llm_cfg, secrets=self._secrets, fallback_to_mock=True)

            agent = Agent(model, system_prompt=system_prompt)
            result = agent.run_sync(user_prompt)
            generated_text = getattr(result, "output", getattr(result, "data", str(result)))
            
            # Clean markdown codeblocks if model included them
            clean_sql = re.sub(r"^```(?:sql)?\n?", "", str(generated_text).strip(), flags=re.IGNORECASE)
            clean_sql = re.sub(r"\n?```$", "", clean_sql.strip())
            clean_sql = clean_sql.strip().rstrip(";")
            return clean_sql, path_used
        except Exception as e:
            logger.warning("LLM SQL generation error: %s. Using deterministic query builder fallback.", e)
            # Fallback to smart query based on intent
            q_low = question.lower()
            item_match = re.search(r"\bitem\s+(?:number\s+|#\s*)?(\d+)\b", q_low)
            if item_match:
                item_num = int(item_match.group(1))
                return (
                    f"SELECT item_number, rfx_description, vendor_name, effective_price_inr, lead_time_days, state "
                    f"FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND item_number = {item_num} AND effective_price_inr IS NOT NULL "
                    f"ORDER BY effective_price_inr ASC",
                    path_used
                )
            elif any(w in q_low for w in ["saving", "target", "budget", "variance", "delta"]):
                return (
                    "SELECT item_number, rfx_description, rfx_quantity, rfx_target_price, vendor_name, "
                    "effective_price_inr, (rfx_target_price - effective_price_inr) as savings_per_unit_inr, "
                    "((rfx_target_price - effective_price_inr) * rfx_quantity) as total_savings_inr "
                    "FROM (SELECT item_number, rfx_description, rfx_quantity, rfx_target_price, vendor_name, effective_price_inr, "
                    "ROW_NUMBER() OVER (PARTITION BY rfx_item_id ORDER BY effective_price_inr ASC) as rank "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL AND kind = 'MATCHED') sub "
                    "WHERE rank = 1 AND rfx_target_price IS NOT NULL ORDER BY total_savings_inr DESC",
                    path_used
                )
            elif any(w in q_low for w in ["lead time", "delivery", "fastest", "turnaround", "timeline"]):
                return (
                    "SELECT item_number, rfx_description, vendor_name, lead_time_days, effective_price_inr "
                    "FROM (SELECT item_number, rfx_description, vendor_name, lead_time_days, effective_price_inr, "
                    "ROW_NUMBER() OVER (PARTITION BY rfx_item_id ORDER BY lead_time_days ASC NULLS LAST) as rank "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND lead_time_days IS NOT NULL) ranked "
                    "WHERE rank = 1 ORDER BY item_number",
                    path_used
                )
            elif any(w in q_low for w in ["spend", "total spend", "spend per vendor"]):
                return (
                    "SELECT vendor_name, COUNT(rfx_item_id) as items_quoted, "
                    "ROUND(SUM(rfx_quantity * effective_price_inr), 2) as total_spend_inr "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL AND kind = 'MATCHED' "
                    "GROUP BY vendor_name ORDER BY total_spend_inr ASC",
                    path_used
                )
            elif any(w in q_low for w in ["single bidder", "one bidder", "only one vendor"]):
                return (
                    "SELECT item_number, rfx_description, COUNT(DISTINCT vendor_id) as bidder_count "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL AND kind = 'MATCHED' "
                    "GROUP BY rfx_item_id, item_number, rfx_description HAVING COUNT(DISTINCT vendor_id) = 1 ORDER BY item_number",
                    path_used
                )
            elif any(w in q_low for w in ["compliance", "response status", "status", "qualification"]):
                return (
                    "SELECT vendor_name, response_status, total_rfx_items, matched_items_count, coverage_pct, "
                    "confident_items_count, review_items_count, missing_items_count "
                    "FROM ktq.v_vendor_coverage WHERE rfx_id = :rfx_id ORDER BY coverage_pct DESC, vendor_name ASC",
                    path_used
                )
            elif any(w in q_low for w in ["coverage", "quote count", "quotation summary", "summary for all vendors", "overview"]):
                return (
                    "SELECT vendor_name, total_rfx_items, matched_items_count, coverage_pct, "
                    "confident_items_count, review_items_count, missing_items_count "
                    "FROM ktq.v_vendor_coverage WHERE rfx_id = :rfx_id ORDER BY coverage_pct DESC, vendor_name ASC",
                    path_used
                )
            elif any(w in q_low for w in ["flag", "warning", "risk", "anomaly", "critical"]):
                return (
                    "SELECT flag_id, vendor_name, code, severity, message, item_id "
                    "FROM ktq.v_open_flags WHERE rfx_id = :rfx_id ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, flag_id ASC",
                    path_used
                )
            elif any(w in q_low for w in ["terms", "payment terms", "commercial terms", "freight"]):
                return (
                    "SELECT COALESCE(v.name, vr.sender_name) as vendor_name, rt.term_type, rt.value_text, rt.value_num "
                    "FROM ktq.response_terms rt JOIN ktq.vendor_responses vr ON rt.response_id = vr.id "
                    "LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id WHERE vr.rfx_id = :rfx_id AND vr.is_current = TRUE "
                    "ORDER BY vendor_name, rt.term_type",
                    path_used
                )
            elif any(w in q_low for w in ["cheapest", "l1", "lowest price", "best price"]):
                return (
                    "SELECT item_number, rfx_description, vendor_name, effective_price_inr, state "
                    "FROM (SELECT item_number, rfx_description, vendor_name, effective_price_inr, state, "
                    "ROW_NUMBER() OVER (PARTITION BY rfx_item_id ORDER BY effective_price_inr ASC) as rank "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL AND kind = 'MATCHED') sub "
                    "WHERE rank = 1 ORDER BY item_number",
                    path_used
                )
            else:
                return (
                    "SELECT item_number, rfx_description, vendor_name, effective_price_inr, lead_time_days, state "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL ORDER BY item_number, effective_price_inr ASC",
                    path_used
                )

    def ask_data(self, question: str, rfx_id: int | None = None) -> dict[str, Any]:
        """Execute semantic data query with single self-correction retry on error.

        Returns:
            {
                "sql": executed_sql,
                "columns": [col1, col2, ...],
                "rows": [[val1, val2, ...], ...],
                "row_count": count,
                "path_used": "wren" | "fallback",
                "error": error_string or None
            }
        """
        sql, path_used = self.generate_sql(question, rfx_id=rfx_id)
        result = run_sql(sql, rfx_id=rfx_id, secrets=self._secrets)

        # Retry once if execution failed with a SQL syntax or column error
        if result.get("error"):
            logger.info("Query failed with error: %s. Retrying with error feedback.", result["error"])
            retry_prompt = (
                f"The previous SQL query failed:\n{sql}\n\n"
                f"Error message from PostgreSQL:\n{result['error']}\n\n"
                f"Original Question: {question}\n"
                "Please output ONLY the corrected single PostgreSQL SELECT query:"
            )
            try:
                app_cfg = load_config(self._config_dir)
                llm_key = "analyst" if "analyst" in app_cfg.llms else "default"
                llm_cfg = app_cfg.llms.get(llm_key, app_cfg.llms[list(app_cfg.llms.keys())[0]])
                model = create_model(llm_cfg, secrets=self._secrets, fallback_to_mock=True)
                agent = Agent(model, system_prompt="Correct the SQL query to fix the PostgreSQL error. Return ONLY valid SELECT.")
                retry_res = agent.run_sync(retry_prompt)
                retry_out = getattr(retry_res, "output", getattr(retry_res, "data", str(retry_res)))
                corrected_sql = re.sub(r"^```(?:sql)?\n?", "", str(retry_out).strip(), flags=re.IGNORECASE)
                corrected_sql = re.sub(r"\n?```$", "", corrected_sql.strip()).strip().rstrip(";")
                
                retry_result = run_sql(corrected_sql, rfx_id=rfx_id, secrets=self._secrets)
                if not retry_result.get("error"):
                    retry_result["path_used"] = path_used
                    return retry_result
            except Exception as e:
                logger.warning("Retry attempt failed: %s", e)

        result["path_used"] = path_used
        return result
