# Plan: Fix Cross-RFx Vendor Quotation Deduplication & Form Parameter Support

## 1. Problem Statement & Root Cause
When submitting a vendor quotation via `POST /api/vendor-responses` with `--form 'rfx_id="26"'`:
1. **Form Field Mismatch**: The endpoint parameter was named `rfx_ref` instead of `rfx_id`. As a result, `--form 'rfx_id="26"'` was dropped by FastAPI and evaluated to `None`.
2. **Cross-RFx Duplicate Collision**: `VendorRepository.check_duplicate_response` executed:
   ```sql
   SELECT vr.id, vr.status, vr.rfx_id, vr.vendor_id, vr.version
   FROM vendor_responses vr
   JOIN response_documents rd ON vr.id = rd.response_id
   WHERE rd.sha256 = ANY(%s) AND vr.is_current = TRUE
   LIMIT 1
   ```
   The query completely ignored `rfx_id`. Because `vendor2_beta_box_clean_table.pdf` was previously uploaded for RFX 25 (creating response #47), the SHA-256 matched response #47.
3. **Execution Dropped**: Because `is_duplicate` was returned as `True`, the pipeline skipped background execution and returned response #47 (RFX 25), leaving neither RFX 26 nor RFX 25 updated.

## 2. Proposed Changes

### Phase 1: API Endpoint Parameter Alignment (`backend/app/api/vendor.py`)
- Add `rfx_id: Annotated[str | None, Form(description="Optional RFx identifier")] = None` alongside `rfx_ref`.
- Compute `target_rfx_ref = (rfx_id.strip() if rfx_id and rfx_id.strip() else None) or (rfx_ref.strip() if rfx_ref and rfx_ref.strip() else None)`.
- Pass `target_rfx_ref` to `svc.submit_vendor_response()`.

### Phase 2: Deduplication & Versioning Scoping (`backend/app/vendor/repository.py` & `backend/app/vendor/service.py`)
- Add helper `_extract_rfx_id(ref: str | None) -> int | None` to handle direct numbers (`"26"`) and standard prefixes (`"RFI-26"`, `"RFX #26"`).
- Update `VendorRepository.check_duplicate_response(file_hashes, sender_email, rfx_id)`:
  - When `rfx_id is not None`: Query with `AND vr.rfx_id = %s`. Identical files submitted for a different RFx are NOT duplicates.
  - When `rfx_id is None`: Query with `AND vr.rfx_id IS NULL` so unassigned uploads do not hijack existing assigned RFx responses.
- Add `get_rfx_by_id(rfx_id: int)` in `VendorRepository` to safely retrieve direct RFx records if not in `open_rfx_list`.
- In `update_response_status`, support updating `version` and `supersedes_response_id`.

### Phase 3: Resolution Engine Robustness (`backend/app/vendor/resolve.py`)
- Update `ResolutionEngine.resolve()` so explicit `rfx_ref` cleans both digits and prefixed strings (`"RFI-26"`, `"RFX-26"`), querying `open_rfx_list` first and `repo.get_rfx_by_id()` as fallback.

### Phase 4: Verification & Automated Tests (`tests/test_vendor_intake.py`)
- Test submitting with `rfx_id` form field.
- Test submitting identical file to a different RFx (verifying it is NOT flagged as duplicate).
- Test submitting identical file to the same RFx (verifying it IS flagged as duplicate).
- Test prefixed references (`"RFI-26"`, `"RFX-26"`).
- Run full pytest test suite and log output to `artifacts/logs/test_vendor_duplicate_fix.log`.

### Phase 5: Documentation Updates
- Update `README.md`, `architecture.md`, and `AI_context.md`.
