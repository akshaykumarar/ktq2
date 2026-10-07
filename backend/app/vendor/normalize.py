"""Pure Python deterministic unit and currency normalization engine."""

from __future__ import annotations

import logging
from typing import Any

from backend.app.vendor.repository import VendorRepository

logger = logging.getLogger(__name__)


class NormalizationResult:
    """Calculated normalization metrics for a single line item."""

    def __init__(
        self,
        unit_factor: float,
        fx_rate: float,
        fx_rate_date: str | None,
        normalized_price_inr: float | None,
        conversion_notes: list[str],
    ) -> None:
        self.unit_factor = unit_factor
        self.fx_rate = fx_rate
        self.fx_rate_date = fx_rate_date
        self.normalized_price_inr = normalized_price_inr
        self.conversion_notes = conversion_notes


class NormalizationEngine:
    """Applies deterministic conversions from unit conversion tables and FX rates."""

    def __init__(self, repo: VendorRepository) -> None:
        self.repo = repo
        self._db_conversions = self.repo.get_unit_conversions()

    def normalize(
        self,
        raw_price: float | None,
        raw_unit: str | None,
        raw_currency: str | None,
        target_rfx_unit: str | None = None,
    ) -> NormalizationResult:
        """Convert raw price to single-unit INR price."""
        if raw_price is None:
            return NormalizationResult(
                unit_factor=1.0,
                fx_rate=1.0,
                fx_rate_date=None,
                normalized_price_inr=None,
                conversion_notes=["Price is missing"],
            )

        unit_str = (raw_unit or "pcs").lower().strip()
        target_unit_str = (target_rfx_unit or "pcs").lower().strip()
        notes: list[str] = []

        # 1. Calculate Unit Multiplier
        unit_factor = 1.0
        # Check DB conversion table first
        if (unit_str, target_unit_str) in self._db_conversions:
            unit_factor = self._db_conversions[(unit_str, target_unit_str)]
            notes.append(f"Applied DB unit conversion {unit_str} -> {target_unit_str} (x{unit_factor})")
        elif "per 1000" in unit_str or "/1000" in unit_str:
            unit_factor = 0.001
            notes.append("Converted price per 1000 units to single unit price (x0.001)")
        elif "per 100" in unit_str or "/100" in unit_str:
            unit_factor = 0.01
            notes.append("Converted price per 100 units to single unit price (x0.01)")
        elif "dozen" in unit_str:
            unit_factor = 1.0 / 12.0
            notes.append("Converted dozen to single unit price (x0.0833)")
        elif unit_str in ("g", "gm", "grams") and target_unit_str == "kg":
            unit_factor = 0.001
            notes.append("Converted grams to kilograms (x0.001)")
        elif unit_str == "sqft" and target_unit_str == "sqm":
            unit_factor = 0.092903
            notes.append("Converted sqft to sqm (x0.0929)")

        # 2. Calculate FX Currency Rate
        curr_str = (raw_currency or "INR").upper().strip()
        fx_rate, fx_date = self.repo.get_fx_rate(curr_str, "INR")
        if curr_str != "INR":
            notes.append(f"Converted currency {curr_str} to INR @ {fx_rate} (date: {fx_date})")

        # 3. Compute normalized price
        normalized = round(raw_price * unit_factor * fx_rate, 4)

        return NormalizationResult(
            unit_factor=round(unit_factor, 6),
            fx_rate=round(fx_rate, 4),
            fx_rate_date=fx_date,
            normalized_price_inr=normalized,
            conversion_notes=notes,
        )
