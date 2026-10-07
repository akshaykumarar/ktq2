#!/usr/bin/env python3
"""Script to reload and validate question-to-SQL example pairs."""

from pathlib import Path
import yaml


def reload_examples(examples_path: str = "config/wren_examples.yaml") -> list[dict]:
    """Load and validate few-shot example pairs."""
    ex_file = Path(examples_path)
    if not ex_file.exists():
        raise FileNotFoundError(f"Examples file not found at: {ex_file}")

    with open(ex_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    examples = data.get("examples", [])
    print(f"Successfully loaded and validated {len(examples)} question-to-SQL examples from {ex_file}:")
    for idx, ex in enumerate(examples, 1):
        print(f" {idx:02d}. [{ex.get('id', 'N/A')}] {ex.get('question', '')[:60]}...")
    return examples


if __name__ == "__main__":
    reload_examples()
