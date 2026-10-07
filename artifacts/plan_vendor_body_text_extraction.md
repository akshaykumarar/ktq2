# Implementation Plan: Vendor Body Text & Inline Quotation Extraction

## 1. Problem Statement & Root Cause
When vendor quotations are submitted as direct text (`body_text`) via `POST /api/vendor-responses` (e.g., email quotations, pasted quotes without attached files):
1. In `backend/app/vendor/service.py`, the extraction phase only iterated over `resp_data["documents"]`, completely skipping `body_text`.
2. When no file attachments were uploaded, `doc_results` was empty, causing `extracted_doc_results` to be empty and inserting 0 line items into `ktq.response_items`.
3. In `backend/app/vendor/extract.py`, multi-tier item phrases and currency symbols (e.g., `₹1,000 for 50 pieces`, `₹400 per dozen`) in `deterministic_text_extractor` need robust parsing for rupee symbols (`₹`, `rs`, `inr`) and multi-volume quotes.

## 2. Proposed Changes
1. **`backend/app/vendor/service.py`**:
   - In `run_pipeline`, check if `resp_data.get("body_text")` is present.
   - If present, run `self.extractor.extract_document` with `parsed_text=resp_data["body_text"]`, `filename="email_body.txt"`, `doc_role="email_body"`, and include the result in `extracted_doc_results`.
   - Also reconcile `body_text` extractions alongside any attached document extractions.

2. **`backend/app/vendor/extract.py`**:
   - Enhance `deterministic_text_extractor` to recognize:
     - Currency symbol `₹` (Indian Rupee) alongside `Rs` and `INR`.
     - Quantity expressions with "for X pieces", "per dozen", "at ₹X per box (Y pieces)".
     - Signature block parsing for Vendor Name, Contact Person, Phone, Email, Location.

3. **Reprocess API / Workflow**:
   - Ensure `POST /api/vendor-responses/{id}/reprocess` passes `body_text` from superseded responses so existing submissions like `#37` can be re-extracted with full item rows.

4. **Testing**:
   - Add unit and integration tests for `body_text` extraction.
   - Run pytest and record logs in `artifacts/logs/test_body_text_extraction.log`.
