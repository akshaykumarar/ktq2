#!/usr/bin/env python3
"""Script to validate and deploy Wren semantic model / MDL definitions."""

import json
from pathlib import Path
import yaml


def deploy_semantic_model(
    model_yaml_path: str = "config/wren_semantic_model.yaml",
    output_mdl_path: str = "config/wren_mdl.json",
) -> dict:
    """Read YAML semantic model definition, validate structure, and generate JSON MDL format."""
    yaml_file = Path(model_yaml_path)
    if not yaml_file.exists():
        raise FileNotFoundError(f"Semantic model YAML not found at: {yaml_file}")

    with open(yaml_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    print(f"Deploying semantic model version: {data.get('version', 'unknown')}")
    models = data.get("models", [])
    print(f"Found {len(models)} models/views:")
    for m in models:
        print(f" - {m['name']} -> {m['table_name']} ({len(m.get('columns', []))} columns)")

    # Build JSON MDL manifest
    mdl = {
        "version": data.get("version", "1.0.0"),
        "models": models,
        "rules": data.get("rules", []),
    }

    out_file = Path(output_mdl_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(mdl, f, indent=2)

    print(f"Generated and deployed MDL manifest to: {out_file}")
    return mdl


if __name__ == "__main__":
    deploy_semantic_model()
