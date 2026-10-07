#!/usr/bin/env python3
"""Export rated & corrected analyst feedback records into few-shot Wren example pairs."""

from pathlib import Path
from typing import Any
import yaml
import psycopg
from psycopg.rows import dict_row

from backend.app.config.settings import AppSecrets
from backend.app.db.connection import get_db_connection


def export_feedback_to_examples(
    examples_file: str = "config/wren_examples.yaml",
    secrets: AppSecrets | None = None,
) -> int:
    """Fetch user corrections from PostgreSQL and sync into wren_examples.yaml."""
    secrets = secrets or AppSecrets()
    if not secrets.db_configured:
        print("Database not configured. Cannot fetch feedback.")
        return 0

    query = """
        SELECT af.id, at.question, af.corrected_sql, af.corrected_answer, af.rating
        FROM ktq.analyst_feedback af
        JOIN ktq.analyst_traces at ON af.trace_id = at.trace_id
        WHERE af.corrected_sql IS NOT NULL AND TRIM(af.corrected_sql) != '';
    """

    with get_db_connection(secrets) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            feedback_rows = cur.fetchall()

    if not feedback_rows:
        print("No feedback corrections found with valid SQL.")
        return 0

    ex_path = Path(examples_file)
    existing_data: dict[str, Any] = {"examples": []}
    if ex_path.exists():
        with open(ex_path, "r", encoding="utf-8") as f:
            existing_data = yaml.safe_load(f) or {"examples": []}

    examples_list = existing_data.get("examples", [])
    existing_questions = {e.get("question", "").strip().lower() for e in examples_list}

    added_count = 0
    for row in feedback_rows:
        q = row["question"].strip()
        if q.lower() not in existing_questions:
            new_ex = {
                "id": f"feedback_{row['id']}",
                "question": q,
                "sql": row["corrected_sql"].strip(),
            }
            examples_list.append(new_ex)
            existing_questions.add(q.lower())
            added_count += 1

    existing_data["examples"] = examples_list
    with open(ex_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing_data, f, sort_keys=False)

    print(f"Exported {added_count} new corrected question-SQL pairs to {ex_path}.")
    return added_count


if __name__ == "__main__":
    export_feedback_to_examples()
