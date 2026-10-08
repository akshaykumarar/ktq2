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

        # Check for direct example exact match
        for ex in self._examples:
            if ex.get("question", "").strip().lower() == question.strip().lower():
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
            if "coverage" in q_low or "quote count" in q_low:
                return (
                    "SELECT vendor_name, total_rfx_items, matched_items_count, coverage_pct, "
                    "confident_items_count, review_items_count, missing_items_count "
                    "FROM ktq.v_vendor_coverage WHERE rfx_id = :rfx_id ORDER BY coverage_pct DESC",
                    path_used
                )
            elif "flag" in q_low or "warning" in q_low or "risk" in q_low:
                return (
                    "SELECT flag_id, vendor_name, code, severity, message, item_id "
                    "FROM ktq.v_open_flags WHERE rfx_id = :rfx_id ORDER BY flag_id ASC",
                    path_used
                )
            elif "cheapest" in q_low or "l1" in q_low:
                return (
                    "SELECT item_number, rfx_description, vendor_name, effective_price_inr, state "
                    "FROM (SELECT item_number, rfx_description, vendor_name, effective_price_inr, state, "
                    "ROW_NUMBER() OVER (PARTITION BY rfx_item_id ORDER BY effective_price_inr ASC) as rank "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id AND effective_price_inr IS NOT NULL) sub "
                    "WHERE rank = 1 ORDER BY item_number",
                    path_used
                )
            else:
                return (
                    "SELECT item_number, rfx_description, vendor_name, effective_price_inr, lead_time_days, state "
                    "FROM ktq.v_rfx_comparison WHERE rfx_id = :rfx_id ORDER BY item_number, effective_price_inr ASC",
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
