"""Excel (.xlsx) and CSV export file generation with mandatory Assumptions & Caveats sheet."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from backend.app.analyst.models import ExportFileRef

logger = logging.getLogger(__name__)

EXPORTS_DIR = Path("artifacts/exports")
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory registry for export ID -> filepath mapping
_EXPORT_REGISTRY: dict[str, Path] = {}


def generate_export_file(
    kind: str,
    rfx_title: str,
    headers: list[str],
    rows: list[list[Any]],
    assumptions: dict[str, Any] | None = None,
    caveats: list[str] | None = None,
    review_items: list[dict[str, Any]] | None = None,
    excluded_vendors: dict[str, str] | None = None,
) -> ExportFileRef:
    """Create a formatted multi-sheet Excel workbook with a mandatory Assumptions and Caveats sheet."""
    export_id = str(uuid.uuid4())[:8]
    filename = f"{kind.lower()}_{export_id}.xlsx"
    file_path = EXPORTS_DIR / filename

    wb = openpyxl.Workbook()
    
    # ── Sheet 1: Main Data Sheet ─────────────────────────────────────────────
    ws_main = wb.active
    ws_main.title = "Summary & Data"
    ws_main.views.sheetView[0].showGridLines = True

    # Styling constants
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=14, bold=True, color="1F497D")
    border_thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    # Project Title Header
    ws_main.cell(row=1, column=1, value=f"{rfx_title} - {kind.upper()}").font = title_font
    ws_main.row_dimensions[1].height = 25

    # Table headers at Row 3
    start_row = 3
    for col_idx, h in enumerate(headers, 1):
        cell = ws_main.cell(row=start_row, column=col_idx, value=str(h))
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border_thin
    ws_main.row_dimensions[start_row].height = 20

    # Table rows
    for r_idx, row_data in enumerate(rows, start=start_row + 1):
        ws_main.row_dimensions[r_idx].height = 18
        for c_idx, val in enumerate(row_data, 1):
            cell = ws_main.cell(row=r_idx, column=c_idx, value=val)
            cell.border = border_thin
            if isinstance(val, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if isinstance(val, float):
                    cell.number_format = "#,##0.00"
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Auto-adjust column widths
    for col in ws_main.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws_main.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # ── Sheet 2: Assumptions & Caveats Sheet (MANDATORY) ─────────────────────
    ws_caveats = wb.create_sheet(title="Assumptions & Caveats")
    ws_caveats.views.sheetView[0].showGridLines = True

    section_header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    section_header_font = Font(name="Calibri", size=11, bold=True, color="1F497D")

    ws_caveats.cell(row=1, column=1, value="Procurement Assumptions & Risk Disclosures").font = title_font
    ws_caveats.row_dimensions[1].height = 24

    c_row = 3

    # 1. Macro Assumptions (FX, Freight, Taxes)
    ws_caveats.cell(row=c_row, column=1, value="1. Financial & Operational Assumptions").font = section_header_font
    ws_caveats.cell(row=c_row, column=1).fill = section_header_fill
    c_row += 1

    assump_data = assumptions or {}
    fx_map = assump_data.get("fx_rates", {"USD": 84.0, "EUR": 91.5, "INR": 1.0})
    ws_caveats.cell(row=c_row, column=1, value="Foreign Exchange Rates:")
    ws_caveats.cell(row=c_row, column=2, value=", ".join(f"{k}: {v} INR" for k, v in fx_map.items()))
    c_row += 1

    freight_info = assump_data.get("freight", {})
    ws_caveats.cell(row=c_row, column=1, value="Freight Treatment:")
    ws_caveats.cell(row=c_row, column=2, value="Included by default in comparisons unless explicitly overridden.")
    c_row += 1

    gst_info = assump_data.get("gst", {})
    ws_caveats.cell(row=c_row, column=1, value="GST / Tax Treatment:")
    ws_caveats.cell(row=c_row, column=2, value=f"Standard 18% GST (Benchmark GST excluded from base price comparisons).")
    c_row += 2

    # 2. Excluded Vendors & Rationale
    ws_caveats.cell(row=c_row, column=1, value="2. Excluded Vendors & Rationale").font = section_header_font
    ws_caveats.cell(row=c_row, column=1).fill = section_header_fill
    c_row += 1

    if excluded_vendors:
        for vname, reason in excluded_vendors.items():
            ws_caveats.cell(row=c_row, column=1, value=vname).font = Font(bold=True)
            ws_caveats.cell(row=c_row, column=2, value=reason)
            c_row += 1
    else:
        ws_caveats.cell(row=c_row, column=1, value="None - All quoting vendors were eligible.")
        c_row += 1
    c_row += 1

    # 3. REVIEW-State Prices Used
    ws_caveats.cell(row=c_row, column=1, value="3. Flagged / REVIEW State Prices Utilized").font = section_header_font
    ws_caveats.cell(row=c_row, column=1).fill = section_header_fill
    c_row += 1

    if review_items:
        for rev in review_items:
            v_label = rev.get("vendor_name", f"Vendor #{rev.get('vendor_id')}")
            ws_caveats.cell(row=c_row, column=1, value=f"{v_label} (Item #{rev.get('item_number', rev.get('item_id'))})")
            ws_caveats.cell(row=c_row, column=2, value=f"Price: {rev.get('price')} INR - {rev.get('reason', 'Unverified extraction')}")
            c_row += 1
    else:
        ws_caveats.cell(row=c_row, column=1, value="None - All utilized prices were CONFIDENT.")
        c_row += 1
    c_row += 1

    # 4. General Caveats
    ws_caveats.cell(row=c_row, column=1, value="4. Specific Decision Caveats").font = section_header_font
    ws_caveats.cell(row=c_row, column=1).fill = section_header_fill
    c_row += 1

    if caveats:
        for cav in caveats:
            ws_caveats.cell(row=c_row, column=1, value="•")
            ws_caveats.cell(row=c_row, column=2, value=str(cav))
            c_row += 1
    else:
        ws_caveats.cell(row=c_row, column=1, value="No special caveats recorded.")

    for col in ws_caveats.columns:
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws_caveats.column_dimensions[col_letter].width = 35

    # Save to disk
    wb.save(str(file_path))
    _EXPORT_REGISTRY[export_id] = file_path

    return ExportFileRef(
        filename=filename,
        url=f"/api/exports/{export_id}",
        kind="xlsx",
    )


def get_export_file_path(export_id: str) -> Path | None:
    """Retrieve the physical path of an exported file."""
    if export_id in _EXPORT_REGISTRY:
        return _EXPORT_REGISTRY[export_id]
    # Check if file exists matching pattern in exports directory
    matching = list(EXPORTS_DIR.glob(f"*_{export_id}.*"))
    if matching:
        return matching[0]
    return None
