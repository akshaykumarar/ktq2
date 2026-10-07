"""Multi-format document preprocessor and image enhancer."""

from __future__ import annotations

import csv
import email
from email import policy
import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

logger = logging.getLogger(__name__)


class PreprocessingResult:
    """Encapsulates the preprocessed document text, enhanced visual bytes, and metadata."""

    def __init__(
        self,
        parsed_text: str,
        page_count: int = 1,
        enhanced_bytes: bytes | None = None,
        is_scan: bool = False,
        quality_notes: str | None = None,
        images_by_page: dict[int, Image.Image] | None = None,
        status: str = "done",
        error: str | None = None,
    ) -> None:
        self.parsed_text = parsed_text
        self.page_count = page_count
        self.enhanced_bytes = enhanced_bytes
        self.is_scan = is_scan
        self.quality_notes = quality_notes
        self.images_by_page = images_by_page or {}
        self.status = status
        self.error = error


def enhance_image(image: Image.Image) -> tuple[Image.Image, bytes]:
    """Auto-orient, contrast-enhance, sharpen, and normalize image."""
    try:
        # Auto-rotate according to EXIF tag
        img = ImageOps.exif_transpose(image)
    except Exception:
        img = image

    # Convert to RGB if needed
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Mild contrast and sharpness enhancement for OCR/Vision
    try:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.25)
        sharpener = ImageEnhance.Sharpness(img)
        img = sharpener.enhance(1.2)
    except Exception as exc:
        logger.warning("Image enhancement filter failed: %s", exc)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return img, buf.getvalue()


def preprocess_image_file(raw_bytes: bytes, filename: str) -> PreprocessingResult:
    """Preprocess image files (.png, .jpg, .jpeg, .webp, .heic)."""
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        enhanced_img, enhanced_bytes = enhance_image(img)
        # Check if image has embedded OCR text metadata
        ocr_text = img.text.get("ocr_text") if hasattr(img, "text") and img.text else None
        parsed_text = (
            f"=== IMAGE OCR TEXT: {filename} ===\n{ocr_text}"
            if ocr_text
            else f"[Image File: {filename}, Dimensions: {img.width}x{img.height}]"
        )
        quality_notes = f"Resolution: {img.width}x{img.height}, format: {img.format or 'image'}"
        return PreprocessingResult(
            parsed_text=parsed_text,
            page_count=1,
            enhanced_bytes=enhanced_bytes,
            is_scan=True,
            quality_notes=quality_notes,
            images_by_page={1: enhanced_img},
            status="done",
        )
    except Exception as exc:
        logger.error("Failed to preprocess image %s: %s", filename, exc)
        return PreprocessingResult(
            parsed_text="",
            page_count=1,
            status="failed",
            error=f"Image preprocessing failed: {exc}",
        )


def preprocess_excel_file(raw_bytes: bytes, filename: str) -> PreprocessingResult:
    """Preprocess Excel spreadsheets (.xlsx, .xls) using openpyxl."""
    try:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=False)
        lines = [f"=== EXCEL WORKBOOK: {filename} ==="]
        total_cells = 0

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            lines.append(f"\n--- Sheet: {sheet_name} (Hidden: {sheet.sheet_state != 'visible'}) ---")
            for row_idx, row in enumerate(sheet.iter_rows(values_only=False), start=1):
                row_vals = []
                for col_idx, cell in enumerate(row, start=1):
                    val = cell.value
                    coord = cell.coordinate
                    if val is not None and str(val).strip():
                        comment_text = f" [Note: {cell.comment.text}]" if cell.comment else ""
                        row_vals.append(f"[{coord}] {str(val).strip()}{comment_text}")
                        total_cells += 1
                if row_vals:
                    lines.append(f"Row {row_idx}: " + " | ".join(row_vals))

        text = "\n".join(lines)
        return PreprocessingResult(
            parsed_text=text,
            page_count=len(wb.sheetnames),
            is_scan=False,
            quality_notes=f"Total cells extracted: {total_cells}, sheets: {len(wb.sheetnames)}",
            status="done",
        )
    except Exception as exc:
        logger.error("Failed to parse Excel file %s: %s", filename, exc)
        return PreprocessingResult(
            parsed_text="",
            page_count=1,
            status="failed",
            error=f"Excel preprocessing failed: {exc}",
        )


def preprocess_csv_file(raw_bytes: bytes, filename: str) -> PreprocessingResult:
    """Preprocess CSV files."""
    try:
        text_content = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text_content))
        lines = [f"=== CSV DOCUMENT: {filename} ==="]
        row_count = 0
        for row_idx, row in enumerate(reader, start=1):
            cleaned = [f"[{chr(65+c)}{row_idx}] {col.strip()}" for c, col in enumerate(row) if col.strip()]
            if cleaned:
                lines.append(f"Row {row_idx}: " + " | ".join(cleaned))
                row_count += 1

        return PreprocessingResult(
            parsed_text="\n".join(lines),
            page_count=1,
            is_scan=False,
            quality_notes=f"Rows extracted: {row_count}",
            status="done",
        )
    except Exception as exc:
        logger.error("Failed to parse CSV file %s: %s", filename, exc)
        return PreprocessingResult(
            parsed_text="",
            page_count=1,
            status="failed",
            error=f"CSV preprocessing failed: {exc}",
        )


def preprocess_pdf_file(raw_bytes: bytes, filename: str) -> PreprocessingResult:
    """Preprocess PDF documents extracting per-page text and rasterizing when needed."""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        num_pages = len(reader.pages)
        lines = [f"=== PDF DOCUMENT: {filename} ({num_pages} pages) ==="]
        images_by_page: dict[int, Image.Image] = {}
        total_extracted_chars = 0

        for page_idx, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            total_extracted_chars += len(page_text.strip())
            lines.append(f"\n--- [Page {page_idx}/{num_pages}] ---")
            lines.append(page_text.strip() if page_text.strip() else "[No selectable text - scanned page]")

            # Extract embedded images if available
            for img_file in page.images:
                try:
                    pil_img = Image.open(io.BytesIO(img_file.data))
                    if page_idx not in images_by_page:
                        enhanced_img, _ = enhance_image(pil_img)
                        images_by_page[page_idx] = enhanced_img
                except Exception:
                    pass

        is_scan = total_extracted_chars < 50
        quality = "Scanned PDF" if is_scan else f"Digital PDF with {total_extracted_chars} characters"

        return PreprocessingResult(
            parsed_text="\n".join(lines),
            page_count=num_pages,
            is_scan=is_scan,
            quality_notes=quality,
            images_by_page=images_by_page,
            status="done",
        )
    except Exception as exc:
        logger.error("Failed to parse PDF file %s: %s", filename, exc)
        return PreprocessingResult(
            parsed_text="",
            page_count=1,
            status="failed",
            error=f"PDF preprocessing failed: {exc}",
        )


def preprocess_docx_file(raw_bytes: bytes, filename: str) -> PreprocessingResult:
    """Preprocess Word documents (.docx)."""
    try:
        import docx

        doc = docx.Document(io.BytesIO(raw_bytes))
        lines = [f"=== WORD DOCUMENT: {filename} ==="]
        
        # Read paragraphs
        for p_idx, p in enumerate(doc.paragraphs, start=1):
            txt = p.text.strip()
            if txt:
                lines.append(f"[Paragraph {p_idx}] {txt}")

        # Read tables
        for t_idx, table in enumerate(doc.tables, start=1):
            lines.append(f"\n--- [Table {t_idx}] ---")
            for r_idx, row in enumerate(table.rows, start=1):
                cell_texts = [f"[C{c_idx}] {cell.text.strip()}" for c_idx, cell in enumerate(row.cells, start=1) if cell.text.strip()]
                if cell_texts:
                    lines.append(f"Row {r_idx}: " + " | ".join(cell_texts))

        return PreprocessingResult(
            parsed_text="\n".join(lines),
            page_count=1,
            is_scan=False,
            quality_notes=f"Paragraphs: {len(doc.paragraphs)}, Tables: {len(doc.tables)}",
            status="done",
        )
    except Exception as exc:
        logger.error("Failed to parse Word document %s: %s", filename, exc)
        return PreprocessingResult(
            parsed_text="",
            page_count=1,
            status="failed",
            error=f"DOCX preprocessing failed: {exc}",
        )


def preprocess_email_file(raw_bytes: bytes, filename: str) -> PreprocessingResult:
    """Preprocess .eml email file."""
    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
        headers = [
            f"Subject: {msg.get('Subject', '')}",
            f"From: {msg.get('From', '')}",
            f"To: {msg.get('To', '')}",
            f"Date: {msg.get('Date', '')}",
        ]
        body = msg.get_body(preferencelist=('plain', 'html'))
        body_text = body.get_content() if body else ""

        full_text = f"=== EMAIL MESSAGE: {filename} ===\n" + "\n".join(headers) + "\n\n" + body_text
        return PreprocessingResult(
            parsed_text=full_text,
            page_count=1,
            is_scan=False,
            quality_notes=f"Email headers and body parsed ({len(body_text)} chars)",
            status="done",
        )
    except Exception as exc:
        logger.error("Failed to parse EML file %s: %s", filename, exc)
        return PreprocessingResult(
            parsed_text="",
            page_count=1,
            status="failed",
            error=f"EML preprocessing failed: {exc}",
        )


def preprocess_document(raw_bytes: bytes, filename: str, mime: str | None = None) -> PreprocessingResult:
    """Route document to the appropriate parser based on extension and mime type."""
    ext = Path(filename).suffix.lower()

    if ext in (".xlsx", ".xls") or (mime and "spreadsheet" in mime):
        return preprocess_excel_file(raw_bytes, filename)
    elif ext == ".csv" or (mime and "csv" in mime):
        return preprocess_csv_file(raw_bytes, filename)
    elif ext == ".pdf" or (mime and "pdf" in mime):
        return preprocess_pdf_file(raw_bytes, filename)
    elif ext in (".docx", ".doc") or (mime and "word" in mime):
        return preprocess_docx_file(raw_bytes, filename)
    elif ext == ".eml" or (mime and "message/rfc822" in mime):
        return preprocess_email_file(raw_bytes, filename)
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".heic", ".bmp", ".tiff") or (mime and "image" in mime):
        return preprocess_image_file(raw_bytes, filename)
    else:
        # Plain text fallback
        try:
            txt = raw_bytes.decode("utf-8", errors="replace")
            return PreprocessingResult(
                parsed_text=txt,
                page_count=1,
                is_scan=False,
                quality_notes="Raw text document",
                status="done",
            )
        except Exception as exc:
            return PreprocessingResult(
                parsed_text="",
                page_count=1,
                status="failed",
                error=f"Unsupported file format and text decoding failed: {exc}",
            )
