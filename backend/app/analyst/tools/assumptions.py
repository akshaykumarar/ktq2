"""Session-scoped what-if assumptions manager for foreign exchange, freight, taxes, and thresholds."""

from __future__ import annotations

import copy
from typing import Any
from pathlib import Path
import yaml

from backend.app.config.settings import find_config_dir

DEFAULT_ASSUMPTIONS: dict[str, Any] = {
    "fx_rates": {
        "USD": 84.0,
        "EUR": 91.5,
        "GBP": 108.0,
        "INR": 1.0,
    },
    "freight": {
        "included_by_default": True,
        "rate_per_kg_inr": 15.0,
        "rate_per_trip_inr": 2500.0,
    },
    "gst": {
        "standard_rate": 0.18,
        "included_in_benchmark": False,
    },
    "thresholds": {
        "outlier_price_ratio": 2.5,
        "moq_risk_multiplier": 1.5,
    },
    "quality_gate": {
        "strict_knockout_enforcement": True,
    }
}

# Session in-memory registry
_SESSION_ASSUMPTIONS: dict[str, dict[str, Any]] = {}


def get_assumptions(session_id: str) -> dict[str, Any]:
    """Retrieve the current what-if assumptions dictionary for a session."""
    if session_id not in _SESSION_ASSUMPTIONS:
        _SESSION_ASSUMPTIONS[session_id] = copy.deepcopy(DEFAULT_ASSUMPTIONS)
    return _SESSION_ASSUMPTIONS[session_id]


def set_assumption(session_id: str, key_path: str, value: Any) -> dict[str, Any]:
    """Update a specific nested assumption key (e.g. 'fx_rates.USD', 'freight.rate_per_kg_inr')."""
    assumptions = get_assumptions(session_id)
    parts = key_path.split(".")

    curr = assumptions
    for p in parts[:-1]:
        if p not in curr or not isinstance(curr[p], dict):
            curr[p] = {}
        curr = curr[p]

    curr[parts[-1]] = value
    return assumptions


def reset_assumptions(session_id: str) -> dict[str, Any]:
    """Reset session assumptions to default."""
    _SESSION_ASSUMPTIONS[session_id] = copy.deepcopy(DEFAULT_ASSUMPTIONS)
    return _SESSION_ASSUMPTIONS[session_id]
