"""Spreadsheet parsing for packaging intake attachments (.xlsx, .csv)."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from backend.app.intake.models import (
    AttachmentExtraction,
    PackagingLineItem,
    PackagingRequirement,
)
from backend.app.intake.validation import apply_traceable_defaults, identify_missing_fields


def _normalize_header(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    return re.sub(r"[\s\-]+", "_", raw).strip("_")


def parse_spreadsheet_bytes(
    *,
    file_name: str,
    content: bytes,
    content_type: str | None = None,
) -> AttachmentExtraction:
    """Parse CSV/XLSX-like bytes into row dictionaries with preserved sheet and row numbers.

    Supports both CSV and XLSX (via openpyxl).
    """
    suffix = Path(file_name).suffix.lower()
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            rows = []
            for row_idx, row in enumerate(reader, start=2):
                clean_row = {
                    _normalize_header(k, f"col_{i}"): v
                    for i, (k, v) in enumerate(row.items())
                    if k is not None
                }
                clean_row["_sheet"] = "Sheet1"
                clean_row["_row"] = row_idx
                rows.append(clean_row)
            return AttachmentExtraction(
                file_name=file_name,
                content_type=content_type,
                rows=rows,
            )
        except Exception as exc:
            return AttachmentExtraction(
                file_name=file_name,
                content_type=content_type,
                extraction_status="error",
                errors=[f"Failed to parse CSV: {exc}"],
            )

    if suffix in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError:
            return AttachmentExtraction(
                file_name=file_name,
                content_type=content_type,
                extraction_status="error",
                errors=["openpyxl is required to parse Excel files."],
            )

        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            rows: list[dict[str, Any]] = []
            for sheet in workbook.worksheets:
                iterator = sheet.iter_rows(values_only=True)
                headers_raw = next(iterator, None)
                if not headers_raw:
                    continue
                headers = [
                    _normalize_header(header, f"column_{index + 1}")
                    for index, header in enumerate(headers_raw)
                ]
                for row_idx, values in enumerate(iterator, start=2):
                    row = {
                        headers[index]: value
                        for index, value in enumerate(values)
                        if index < len(headers) and value is not None
                    }
                    if row:
                        row["_sheet"] = sheet.title
                        row["_row"] = row_idx
                        rows.append(row)

            return AttachmentExtraction(file_name=file_name, content_type=content_type, rows=rows)
        except Exception as exc:
            return AttachmentExtraction(
                file_name=file_name,
                content_type=content_type,
                extraction_status="error",
                errors=[f"Failed to parse Excel workbook: {exc}"],
            )

    return AttachmentExtraction(
        file_name=file_name,
        content_type=content_type,
        extraction_status="unsupported",
        errors=[f"Unsupported file type '{suffix or 'unknown'}'. Upload CSV or XLSX."],
    )


def rows_to_packaging_requirements(
    rows: list[dict[str, Any]],
) -> tuple[list[PackagingRequirement], list[str]]:
    """Convert spreadsheet rows to canonical PackagingRequirement objects.

    Returns:
        (requirements, errors_and_warnings)
    """
    requirements: list[PackagingRequirement] = []
    issues: list[str] = []

    for index, row in enumerate(rows, start=1):
        sheet_info = f"Sheet '{row.get('_sheet', 'Sheet1')}', Row {row.get('_row', index)}"

        # 1. Description
        description = _first_present(
            row,
            [
                "description",
                "item_description",
                "item",
                "item_name",
                "product",
                "material_description",
                "packaging_item",
            ],
        )
        if not description:
            issues.append(f"{sheet_info}: Skipped row missing item description.")
            continue

        # 2. Quantity
        quantity_raw = _first_present(
            row, ["quantity", "qty", "required_quantity", "volume", "count", "units"]
        )
        quantity = _to_float(quantity_raw)
        if quantity is None:
            issues.append(f"{sheet_info}: Missing quantity for '{description}'.")

        # 3. Dimensions
        dimensions: dict[str, Any] = {}
        dim_str = _first_present(row, ["dimensions", "dimension", "size", "dim"])
        if dim_str:
            dim_match = re.search(
                r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|inch|m)?",
                str(dim_str),
                re.I,
            )
            if dim_match:
                dimensions = {
                    "length": float(dim_match.group(1)),
                    "width": float(dim_match.group(2)),
                    "height": float(dim_match.group(3)),
                    "unit": (dim_match.group(4) or "mm").lower(),
                }
        else:
            length = _to_float(_first_present(row, ["length", "len", "l"]))
            width = _to_float(_first_present(row, ["width", "w"]))
            height = _to_float(_first_present(row, ["height", "h", "depth"]))
            if length and width and height:
                dim_u = _first_present(row, ["dimension_unit", "dim_unit", "dim_uom"]) or "mm"
                dimensions = {
                    "length": length,
                    "width": width,
                    "height": height,
                    "unit": str(dim_u).lower(),
                }

        # 4. Specifications and Material
        material = _string_or_none(
            _first_present(row, ["material", "paper_type", "substrate"])
        )
        ply = _string_or_none(_first_present(row, ["ply", "no_of_ply", "plies"]))
        gsm = _string_or_none(_first_present(row, ["gsm"]))
        bf = _string_or_none(_first_present(row, ["bf", "bursting_factor"]))
        specifications = _string_or_none(
            _first_present(
                row,
                [
                    "specifications",
                    "specification",
                    "specs",
                    "notes",
                    "remarks",
                    "details",
                ],
            )
        )

        # 5. Delivery date and Target price
        delivery_date = _string_or_none(
            _first_present(
                row,
                ["delivery_date", "required_date", "needed_by", "deadline", "target_date"],
            )
        )
        target_price = _to_float(
            _first_present(
                row, ["target_price", "price", "unit_price", "budget", "rate"]
            )
        )
        currency = (
            _string_or_none(_first_present(row, ["currency", "curr"])) or "INR"
        )
        category = (
            _string_or_none(
                _first_present(row, ["category", "packaging_category", "type"])
            )
            or "Corrugated packaging"
        )
        unit = (
            _string_or_none(
                _first_present(row, ["unit", "uom", "unit_of_measure"])
            )
            or "pcs"
        )

        req = PackagingRequirement(
            item_number=len(requirements) + 1,
            category=category,
            item_description=str(description),
            quantity=quantity,
            unit=unit,
            dimensions=dimensions,
            material=material,
            ply=ply,
            gsm=gsm,
            bf=bf,
            specification=specifications,
            delivery_date=delivery_date,
            target_price=target_price,
            currency=currency,
        )
        req.missing_fields = identify_missing_fields(req)
        apply_traceable_defaults(req)
        requirements.append(req)

    return requirements, issues


def rows_to_line_items(rows: list[dict[str, Any]]) -> list[PackagingLineItem]:
    """Map spreadsheet rows to packaging line items (legacy compatibility)."""
    reqs, _ = rows_to_packaging_requirements(rows)
    return [
        PackagingLineItem(
            item_number=r.item_number,
            description=r.item_description,
            packaging_category=r.category,
            quantity=r.quantity,
            unit=r.unit,
            material=r.material,
            dimensions=r.dimensions,
            gsm=r.gsm,
            bf=r.bf,
            ply=r.ply,
            specifications=r.specification,
            target_price=r.target_price,
            currency=r.currency,
        )
        for r in reqs
    ]


def _first_present(row: dict[str, Any], keys: list[str]) -> Any:
    normalized = {_normalize_header(key, key): value for key, value in row.items()}
    for key in keys:
        norm_key = _normalize_header(key, key)
        value = normalized.get(norm_key)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()
