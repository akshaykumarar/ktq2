"""Generate 5 realistic messy vendor quotation inputs + ground truth."""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import docx
import openpyxl
import pypdf

OUTPUT_DIR = Path(__file__).resolve().parent / "samples"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def generate_excel_sample() -> Path:
    """Shape 1: Restructured Excel with merged header, notes, and cell coordinates."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Quotation Sheet"

    ws["A1"] = "SUPPLIER QUOTE - PACKAGING SOLUTIONS PVT LTD"
    ws["A2"] = "Ref: RFX-1 Corrugated Boxes Requirement | Date: 07-Oct-2026"

    # Headers
    headers = ["Item #", "Item Specification", "Quantity", "UOM", "Unit Rate (INR)", "Remarks"]
    ws.append([])
    ws.append(headers)

    # Rows
    ws.append(["1", "Corrugated Box 600x400x300 mm (5-ply heavy duty)", 5000, "pcs", 42.50, "Ex-factory rate"])
    ws.append(["2", "BOPP Self-Adhesive Brown Tape 2 inch x 65m", 300, "roll", 38.00, "Per roll price"])
    ws.append(["3", "Eco-friendly wooden pallet base 1200x1000 mm", 50, "pcs", 550.00, "Extra item offered"])

    # Cell note on cell E4
    comment = openpyxl.comments.Comment("5% volume discount applicable if total qty exceeds 10,000", "Sales")
    ws["E4"].comment = comment

    file_path = OUTPUT_DIR / "sample_1_restructured_excel.xlsx"
    wb.save(file_path)
    print(f"Generated: {file_path}")
    return file_path


def generate_pdf_sample() -> Path:
    """Shape 2: PDF quotation on letterhead with conditional discount in footnote."""
    file_path = OUTPUT_DIR / "sample_2_letterhead_quote.pdf"
    
    pdf_content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length 560 >>
stream
BT
/F1 14 Tf
50 720 Td (PREMIER CARTONS & PACKAGING LTD) Tj
/F1 10 Tf
0 -20 Td (GSTIN: 27AABCP1234F1Z5 | Email: sales@premiercartons.in) Tj
0 -30 Td (QUOTATION FOR RFX #1) Tj
0 -20 Td (Date: 07-Oct-2026 | Quote No: PC/2026/089) Tj
0 -30 Td (Line 1: Corrugated Box 600x400x300 mm - 5 Ply) Tj
0 -20 Td (Quantity: 5,000 pcs) Tj
0 -20 Td (Unit Price: Rs. 44.00 per piece) Tj
0 -30 Td (Commercial Terms:) Tj
0 -20 Td (- GST: 18% extra as applicable) Tj
0 -20 Td (- Payment: 30 days credit from invoice date) Tj
0 -20 Td (- Delivery: Within 7 working days from PO) Tj
0 -20 Td (- Footnote: 2% cash discount if payment made within 10 days; freight extra at actuals.) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000244 00000 n 
0000000318 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
930
%%EOF
"""
    file_path.write_bytes(pdf_content)
    print(f"Generated: {file_path}")
    return file_path


def generate_word_sample() -> Path:
    """Shape 3: Word doc (.docx) with commercials in paragraph and partial quote + alternate spec."""
    doc = docx.Document()
    doc.add_heading("Apex Packaging Corporation - Formal Quotation", level=1)
    doc.add_paragraph("Reference: Quote for RFX #1 Corrugated Packaging Requirement")
    doc.add_paragraph("Dear Procurement Team,\nThank you for inviting us to quote. We are pleased to submit our commercial proposal:")

    table = doc.add_table(rows=1, cols=4)
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Item Description"
    hdr_cells[1].text = "Qty"
    hdr_cells[2].text = "Unit Price (INR)"
    hdr_cells[3].text = "Lead Time"

    # Line with alternate spec (4-ply instead of 5-ply)
    row_cells = table.add_row().cells
    row_cells[0].text = "Corrugated Box 600x400x300 mm (4-Ply lightweight alternate)"
    row_cells[1].text = "5,000"
    row_cells[2].text = "39.50"
    row_cells[3].text = "5 days"

    doc.add_paragraph("\nCommercial Terms:")
    doc.add_paragraph("1. Validity: Proposal is valid for 45 days.\n2. Delivery terms: Delivered to buyer warehouse dock.\n3. Freight is included in the unit price.")

    file_path = OUTPUT_DIR / "sample_3_word_partial_alternate.docx"
    doc.save(file_path)
    print(f"Generated: {file_path}")
    return file_path


def generate_phone_photo_rate_card() -> Path:
    """Shape 4: Phone-photo style image of printed rate card with USD and per-100 pricing trap."""
    img = Image.new("RGB", (750, 600), color=(245, 242, 235))
    draw = ImageDraw.Draw(img)

    draw.text((50, 40), "GLOBAL PACK CORP - EXPORT RATE CARD", fill=(20, 20, 20))
    draw.text((50, 65), "Ref: RFX-1 | Currency: USD", fill=(80, 80, 80))
    draw.line((50, 90, 700, 90), fill=(100, 100, 100), width=1)

    # Pricing Trap: Quoted in USD per 100 units
    draw.text((50, 120), "Item: Corrugated Boxes 600x400x300 mm 5-ply", fill=(0, 0, 0))
    draw.text((50, 145), "Rate: $ 52.00 per 100 pcs", fill=(0, 50, 150))
    draw.text((50, 170), "MOQ: 5,000 units | Lead Time: 12 days", fill=(60, 60, 60))

    draw.text((50, 230), "Item: Heavy Duty Stretch Film 23 Micron", fill=(0, 0, 0))
    draw.text((50, 255), "Rate: $ 4.80 per roll", fill=(0, 50, 150))

    # Add simulated slight rotation / background noise
    rotated = img.rotate(1.5, expand=True, fillcolor=(230, 230, 230))
    file_path = OUTPUT_DIR / "sample_4_phone_photo_usd_ratecard.png"

    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text(
        "ocr_text",
        "GLOBAL PACK CORP - EXPORT RATE CARD\nRef: RFX-1 | Currency: USD\nItem: Corrugated Boxes 600x400x300 mm 5-ply\nRate: $ 52.00 per 100 pcs\nMOQ: 5,000 units | Lead Time: 12 days\nItem: Heavy Duty Stretch Film 23 Micron\nRate: $ 4.80 per roll",
    )
    rotated.save(file_path, "PNG", pnginfo=info)
    print(f"Generated: {file_path}")
    return file_path


def generate_email_eml_sample() -> Path:
    """Shape 5: Email quotation with partial prices and 'rest same as last year, freight extra'."""
    eml_text = """From: "Rajesh Sharma" <rajesh@sharmapackaging.in>
To: "Procurement Manager" <buyer@acme-corp.com>
Subject: Re: RFX #1 Corrugated Packaging Quotation
Date: Wed, 07 Oct 2026 14:20:00 +0530
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"

Hi Team,

Please find our quotation for RFX #1:

1. Corrugated Box 600x400x300 mm: Rs 43.20 per piece (Qty: 5000 pcs)
2. Transparent BOPP Tape: same as last year rate
3. Stretch Film 5m: rates unchanged from previous order

Terms:
- Freight extra at actuals
- Payment: 30 days
- Validity: 15 days

Thanks & Regards,
Rajesh Sharma
Sharma Packaging & Co.
GSTIN: 07AAECP4567M1Z2
"""
    file_path = OUTPUT_DIR / "sample_5_email_unresolved_ref.eml"
    file_path.write_text(eml_text, encoding="utf-8")
    print(f"Generated: {file_path}")
    return file_path


def generate_ground_truth() -> Path:
    """Generate ground truth specification for automated pipeline evaluation."""
    truth = {
        "sample_1_restructured_excel": {
            "expected_rfx_id": 1,
            "expected_vendor_name": "Packaging Solutions Pvt Ltd",
            "expected_items": [
                {
                    "description_substring": "Corrugated Box",
                    "expected_kind": "MATCHED",
                    "expected_raw_price": 42.50,
                    "expected_unit": "pcs",
                    "expected_currency": "INR",
                    "expected_state": "CONFIDENT",
                },
                {
                    "description_substring": "Brown Tape",
                    "expected_kind": "EXTRA",
                    "expected_raw_price": 38.00,
                    "expected_unit": "roll",
                    "expected_state": "REVIEW",  # Extra items require review
                },
                {
                    "description_substring": "pallet base",
                    "expected_kind": "EXTRA",
                    "expected_raw_price": 550.00,
                    "expected_state": "REVIEW",
                },
            ],
            "expected_flags": ["extra_item", "conditional_discount_present"],
        },
        "sample_2_letterhead_quote": {
            "expected_rfx_id": 1,
            "expected_vendor_name": "Premier Cartons & Packaging Ltd",
            "expected_items": [
                {
                    "description_substring": "Corrugated Box",
                    "expected_kind": "MATCHED",
                    "expected_raw_price": 44.00,
                    "expected_unit": "pcs",
                    "expected_state": "CONFIDENT",
                }
            ],
            "expected_terms": ["freight", "payment_terms", "gst"],
        },
        "sample_3_word_partial_alternate": {
            "expected_rfx_id": 1,
            "expected_vendor_name": "Apex Packaging Corporation",
            "expected_items": [
                {
                    "description_substring": "Corrugated Box",
                    "expected_kind": "ALTERNATE",
                    "expected_raw_price": 39.50,
                    "expected_state": "REVIEW",  # Alternate requires review
                }
            ],
            "expected_flags": ["alternate_item"],
        },
        "sample_4_phone_photo_usd_ratecard": {
            "expected_rfx_id": 1,
            "expected_vendor_name": "Global Pack Corp",
            "expected_items": [
                {
                    "description_substring": "Corrugated Boxes",
                    "expected_kind": "MATCHED",
                    "expected_raw_price": 52.00,
                    "expected_unit": "per 100",
                    "expected_currency": "USD",
                    # Normalized price = 52.00 * 0.01 * 86.50 = 44.98 INR
                    "expected_normalized_price_inr_approx": 44.98,
                }
            ],
            "expected_flags": ["currency_not_inr", "fx_rate_assumed"],
        },
        "sample_5_email_unresolved_ref": {
            "expected_rfx_id": 1,
            "expected_vendor_name": "Sharma Packaging & Co.",
            "expected_items": [
                {
                    "description_substring": "Corrugated Box",
                    "expected_kind": "MATCHED",
                    "expected_raw_price": 43.20,
                    "expected_state": "CONFIDENT",
                },
                {
                    "description_substring": "BOPP Tape",
                    "expected_kind": "EXTRA",
                    "expected_state": "REVIEW",
                    "has_unresolved_reference": True,
                },
            ],
            "expected_flags": ["unresolved_reference"],
        },
    }

    truth_path = Path(__file__).resolve().parent / "ground_truth.json"
    truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"Generated Ground Truth: {truth_path}")
    return truth_path


if __name__ == "__main__":
    generate_excel_sample()
    generate_pdf_sample()
    generate_word_sample()
    generate_phone_photo_rate_card()
    generate_email_eml_sample()
    generate_ground_truth()
