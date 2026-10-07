"""Tests for Excel spreadsheet intake (.xlsx) with header aliases and row error reporting."""

import io
import openpyxl
from fastapi.testclient import TestClient

from backend.app.intake.excel_parser import parse_spreadsheet_bytes, rows_to_packaging_requirements
from backend.app.main import app

client = TestClient(app)


def _create_test_xlsx(headers: list[str], rows: list[list[any]]) -> bytes:
    """Generate an in-memory XLSX workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PackagingItems"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_valid_excel_workbook() -> None:
    """Test parsing a well-structured Excel workbook with multiple packaging items."""
    content = _create_test_xlsx(
        headers=["Item Description", "Quantity", "Unit", "Dimensions", "Material", "Target Price"],
        rows=[
            ["Corrugated Carton A", 2500, "pcs", "500x300x200 mm", "Virgin Kraft", 35.5],
            ["Heavy Duty Master Carton", 1000, "boxes", "800x600x400 mm", "7-ply kraft", 120.0],
        ],
    )
    extraction = parse_spreadsheet_bytes(file_name="order.xlsx", content=content)
    assert len(extraction.rows) == 2

    reqs, errors = rows_to_packaging_requirements(extraction.rows)
    assert len(reqs) == 2
    assert reqs[0].item_description == "Corrugated Carton A"
    assert reqs[0].quantity == 2500.0
    assert reqs[0].dimensions["length"] == 500.0
    assert reqs[0].dimensions["width"] == 300.0
    assert reqs[0].dimensions["height"] == 200.0
    assert reqs[0].material == "Virgin Kraft"
    assert reqs[0].target_price == 35.5

    assert reqs[1].item_description == "Heavy Duty Master Carton"
    assert reqs[1].quantity == 1000.0


def test_alternative_column_names() -> None:
    """Test header tolerance for common column aliases (e.g. 'Product', 'Volume', 'Rate', 'Specs')."""
    content = _create_test_xlsx(
        headers=["Product", "Volume", "UOM", "Length", "Width", "Height", "Substrate", "Rate"],
        rows=[
            ["Stretch Film Rolls", 500, "rolls", 50, 10, 10, "LLDPE", 450],
        ],
    )
    extraction = parse_spreadsheet_bytes(file_name="custom_headers.xlsx", content=content)
    assert len(extraction.rows) == 1

    reqs, errors = rows_to_packaging_requirements(extraction.rows)
    assert len(reqs) == 1
    req = reqs[0]
    assert req.item_description == "Stretch Film Rolls"
    assert req.quantity == 500.0
    assert req.unit == "rolls"
    assert req.dimensions == {"length": 50.0, "width": 10.0, "height": 10.0, "unit": "mm"}
    assert req.material == "LLDPE"
    assert req.target_price == 450.0


def test_missing_required_values_row_level_reporting() -> None:
    """Test that missing quantity or description reports row-level issues without crashing the file."""
    content = _create_test_xlsx(
        headers=["Item", "Quantity"],
        rows=[
            ["Good Box", 1000],
            ["", 500],             # Missing description -> should be skipped
            ["Box Without Qty", None], # Missing quantity -> captured as issue
        ],
    )
    extraction = parse_spreadsheet_bytes(file_name="mixed.xlsx", content=content)
    reqs, issues = rows_to_packaging_requirements(extraction.rows)

    assert len(reqs) == 2
    assert reqs[0].item_description == "Good Box"
    assert reqs[0].quantity == 1000.0

    assert reqs[1].item_description == "Box Without Qty"
    assert reqs[1].quantity is None

    # Verify row-level issues captured
    assert any("missing item description" in issue.lower() for issue in issues)
    assert any("missing quantity" in issue.lower() for issue in issues)


def test_excel_intake_api_endpoint() -> None:
    """Test POST /api/rfi/intake/excel multipart upload."""
    content = _create_test_xlsx(
        headers=["Item Description", "Quantity", "Unit"],
        rows=[
            ["Self-Adhesive Packing Tape", 1200, "rolls"],
            ["Thermal Barcode Labels", 50000, "labels"],
        ],
    )
    response = client.post(
        "/api/rfi/intake/excel",
        files={"file": ("packaging_order.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rows_parsed"] == 2
    assert len(data["requirements"]) == 2
    assert data["requirements"][0]["item_description"] == "Self-Adhesive Packing Tape"
    assert data["requirements"][1]["quantity"] == 50000.0
