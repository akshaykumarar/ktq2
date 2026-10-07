"""Service orchestrator for Vendor Intake Pipeline (state machine, resolution, extraction, normalization, validation)."""

from __future__ import annotations

import hashlib
import io
import logging
import time
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from backend.app.config.settings import AppConfig, AppSecrets
from backend.app.vendor.evidence import EvidenceEngine
from backend.app.vendor.extract import ExtractionEngine
from backend.app.vendor.match import ItemMatcher
from backend.app.vendor.models import (
    DocumentExtractionResult,
    ItemKind,
    ItemState,
    ResponseStatus,
    VendorResponseSubmitPreview,
)
from backend.app.vendor.normalize import NormalizationEngine
from backend.app.vendor.preprocess import PreprocessingResult, preprocess_document
from backend.app.vendor.repository import VendorRepository
from backend.app.vendor.resolve import ResolutionEngine

logger = logging.getLogger(__name__)


class VendorResponseService:
    """Coordinates intake, document preprocessing, extraction, resolution, validation, and storage."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.repo = VendorRepository(config.secrets)
        self.preprocessor = preprocess_document
        self.resolver = ResolutionEngine(self.repo)
        self.extractor = ExtractionEngine(config, self.repo)
        self.matcher = ItemMatcher(self.repo)
        self.normalizer = NormalizationEngine(self.repo)
        self.validator = None  # instantiated per run
        self.evidence = EvidenceEngine(self.repo)

    # -----------------------------------------------------------------------
    # Step A: Ingest & Store Raw First (202 Fast Path)
    # -----------------------------------------------------------------------

    def submit_vendor_response(
        self,
        files: list[tuple[str, bytes, str | None]],  # [(filename, bytes, mime)]
        body_text: str | None = None,
        body_json: dict[str, Any] | None = None,
        subject: str | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        channel: str = "upload",
        received_at: datetime | None = None,
        rfx_ref: str | None = None,
        vendor_name: str | None = None,
    ) -> VendorResponseSubmitPreview:
        """Persist raw files and metadata, perform idempotency checks, and return 202 preview."""
        file_hashes = [hashlib.sha256(content).hexdigest() for _, content, _ in files if content]

        # 1. Idempotency check: Link to existing if identical payload arrived
        duplicate = self.repo.check_duplicate_response(file_hashes, sender_email, int(rfx_ref) if (rfx_ref and rfx_ref.isdigit()) else None)
        if duplicate and len(file_hashes) > 0:
            logger.info("Duplicate quotation submission detected: links to response #%s", duplicate["id"])
            return VendorResponseSubmitPreview(
                response_id=duplicate["id"],
                status=ResponseStatus(duplicate["status"]),
                message="Identical quotation content already received. Linked to existing response.",
                is_duplicate=True,
                rfx_resolution_preview={"rfx_id": duplicate["rfx_id"], "version": duplicate["version"]},
            )

        # 2. Versioning check: If vendor already has an active quote for this RFx, increment version & supersede
        resolved_rfx_id = int(rfx_ref) if (rfx_ref and rfx_ref.isdigit()) else None
        existing_vendor = self.repo.find_vendor_by_email_or_name(sender_email, vendor_name)
        vendor_id = existing_vendor["id"] if existing_vendor else None

        prior_response = self.repo.get_latest_response_for_vendor_rfx(vendor_id, resolved_rfx_id)
        version = (prior_response["version"] + 1) if prior_response else 1
        superseded_id = prior_response["id"] if prior_response else None

        if superseded_id:
            self.repo.supersede_prior_response(superseded_id)

        # 3. Create initial response header
        response_id = self.repo.create_vendor_response(
            rfx_id=resolved_rfx_id,
            vendor_id=vendor_id,
            channel=channel,
            subject=subject,
            sender_name=sender_name or (existing_vendor["name"] if existing_vendor else None),
            sender_email=sender_email,
            body_text=body_text,
            body_json=body_json,
            received_at=received_at or datetime.now(timezone.utc),
            version=version,
            supersedes_response_id=superseded_id,
        )

        # 4. Store raw document bytes byte-for-byte
        for filename, content, mime in files:
            sha256 = hashlib.sha256(content).hexdigest()
            self.repo.add_response_document(
                response_id=response_id,
                filename=filename,
                mime=mime,
                size_bytes=len(content),
                sha256=sha256,
                raw_bytes=content,
            )

        # Fast preview of RFx resolution
        res_preview = {
            "rfx_ref_provided": rfx_ref,
            "version": version,
            "superseded_response_id": superseded_id,
        }

        return VendorResponseSubmitPreview(
            response_id=response_id,
            status=ResponseStatus.RECEIVED,
            message="Quotation received and raw payload stored. Processing asynchronously.",
            is_duplicate=False,
            superseded_response_id=superseded_id,
            rfx_resolution_preview=res_preview,
        )

    # -----------------------------------------------------------------------
    # Step B - G: Background Processing Pipeline
    # -----------------------------------------------------------------------

    async def run_pipeline(self, response_id: int) -> None:
        """Execute full async state machine: preprocess -> extract -> resolve -> match -> normalize -> validate -> crops."""
        from backend.app.vendor.validate import ValidationEngine

        self.validator = ValidationEngine()
        resp_data = self.repo.get_response_by_id(response_id)
        if not resp_data:
            logger.error("Response #%s not found for pipeline execution.", response_id)
            return

        pipeline_start = time.time()
        self.repo.log_pipeline_event(response_id, "pipeline", "started")

        try:
            # ---------------------------------------------------------------
            # Phase 1: Preprocessing
            # ---------------------------------------------------------------
            self.repo.update_response_status(response_id, status="preprocessing")
            stage_start = time.time()

            doc_results: list[tuple[dict[str, Any], PreprocessingResult]] = []
            combined_texts: list[str] = []
            all_doc_images: dict[int, dict[int, Image.Image]] = {}  # {doc_id: {page: img}}

            if resp_data.get("body_text"):
                combined_texts.append(f"=== EMAIL / SUBMISSION BODY ===\n{resp_data['body_text']}")

            for doc in resp_data["documents"]:
                doc_raw = self.repo.get_document_raw(doc["id"])
                if not doc_raw:
                    continue
                raw_bytes, filename, mime = doc_raw
                prep = preprocess_document(raw_bytes, filename, mime)
                self.repo.update_document_preprocessing(
                    doc_id=doc["id"],
                    parsed_text=prep.parsed_text,
                    page_count=prep.page_count,
                    enhanced_bytes=prep.enhanced_bytes,
                    quality_notes=prep.quality_notes,
                    status=prep.status,
                    error=prep.error,
                )
                if prep.parsed_text:
                    combined_texts.append(prep.parsed_text)
                if prep.images_by_page:
                    all_doc_images[doc["id"]] = prep.images_by_page
                doc_results.append((doc, prep))

            self.repo.log_pipeline_event(
                response_id,
                "preprocessing",
                "completed",
                duration_ms=int((time.time() - stage_start) * 1000),
                detail={"documents_processed": len(doc_results)},
            )

            # ---------------------------------------------------------------
            # Phase 2: Extraction & Reconciliation
            # ---------------------------------------------------------------
            self.repo.update_response_status(response_id, status="extracting")
            stage_start = time.time()

            extracted_doc_results: list[DocumentExtractionResult] = []

            # Extract from body_text if provided
            if resp_data.get("body_text"):
                body_ext = await self.extractor.extract_document(
                    response_id=response_id,
                    document_id=None,
                    filename="email_body.txt",
                    doc_role="email_body",
                    parsed_text=resp_data["body_text"],
                )
                extracted_doc_results.append(body_ext)

            for doc, prep in doc_results:
                if prep.status == "done" and prep.parsed_text:
                    ext_res = await self.extractor.extract_document(
                        response_id=response_id,
                        document_id=doc["id"],
                        filename=doc["filename"],
                        doc_role=doc.get("doc_role", "quotation"),
                        parsed_text=prep.parsed_text,
                        enhanced_bytes=prep.enhanced_bytes,
                    )
                    # Link document ID to each extracted item
                    for itm in ext_res.line_items:
                        itm.page = itm.page or 1
                    extracted_doc_results.append(ext_res)

            # Reconcile across documents
            merged_vendor, merged_items, merged_terms, merged_answers = self.extractor.reconcile_documents(extracted_doc_results)

            self.repo.log_pipeline_event(
                response_id,
                "extracting",
                "completed",
                duration_ms=int((time.time() - stage_start) * 1000),
                detail={"items_extracted": len(merged_items), "terms_extracted": len(merged_terms)},
            )

            # ---------------------------------------------------------------
            # Phase 3: Scored RFx and Vendor Resolution
            # ---------------------------------------------------------------
            self.repo.update_response_status(response_id, status="resolving")
            stage_start = time.time()

            combined_all_text = "\n\n".join(combined_texts)
            item_descriptions = [it.raw_description for it in merged_items]

            resolution = self.resolver.resolve(
                rfx_ref=str(resp_data.get("rfx_id") or merged_vendor.rfx_reference_found or ""),
                vendor_name=resp_data.get("sender_name") or merged_vendor.name,
                sender_email=resp_data.get("sender_email") or merged_vendor.email,
                sender_name=resp_data.get("sender_name"),
                subject=resp_data.get("subject"),
                body_text=resp_data.get("body_text"),
                combined_doc_text=combined_all_text,
                extracted_vendor=merged_vendor,
                extracted_item_descs=item_descriptions,
            )

            self.repo.update_response_status(
                response_id=response_id,
                status="resolving",
                rfx_id=resolution.rfx_id,
                vendor_id=resolution.vendor_id,
                rfx_resolution=resolution.to_rfx_dict(),
                vendor_resolution=resolution.to_vendor_dict(),
            )

            self.repo.log_pipeline_event(
                response_id,
                "resolving",
                "completed",
                duration_ms=int((time.time() - stage_start) * 1000),
                detail=resolution.to_rfx_dict(),
            )

            # ---------------------------------------------------------------
            # Phase 4: Matching to RFx Items
            # ---------------------------------------------------------------
            self.repo.update_response_status(response_id, status="matching")
            stage_start = time.time()

            matched_items = self.matcher.match_items(
                rfx_id=resolution.rfx_id,
                extracted_items=merged_items,
            )

            self.repo.log_pipeline_event(
                response_id,
                "matching",
                "completed",
                duration_ms=int((time.time() - stage_start) * 1000),
                detail={"matched_count": len(matched_items)},
            )

            # ---------------------------------------------------------------
            # Phase 5: Normalization, Validation & Visual Evidence
            # ---------------------------------------------------------------
            self.repo.update_response_status(response_id, status="normalizing")
            stage_start = time.time()

            rfx_items_map = {}
            if resolution.rfx_id:
                for rxi in self.repo.get_rfx_items_for_rfx(resolution.rfx_id):
                    rfx_items_map[rxi["id"]] = rxi

            db_items_to_save: list[dict[str, Any]] = []
            all_response_flags: list[dict[str, Any]] = []

            for match in matched_items:
                ext_item = match.extracted_item
                rxi = rfx_items_map.get(match.rfx_item_id) if match.rfx_item_id else None

                # Normalization
                norm = self.normalizer.normalize(
                    raw_price=ext_item.raw_price,
                    raw_unit=ext_item.raw_unit,
                    raw_currency=ext_item.raw_currency,
                    target_rfx_unit=rxi["unit"] if rxi else None,
                )

                # Validation
                state, flags, missing, why_unsure, how_to_resolve = self.validator.evaluate_item(
                    kind=match.kind,
                    raw_price=ext_item.raw_price,
                    raw_unit=ext_item.raw_unit,
                    raw_qty=ext_item.raw_qty,
                    raw_currency=ext_item.raw_currency,
                    tax_basis=ext_item.tax_basis,
                    discount=ext_item.discount,
                    unresolved_reference=ext_item.unresolved_reference,
                    match_confidence=match.match_confidence,
                    extraction_confidence=ext_item.extraction_confidence,
                    target_rfx_unit=rxi["unit"] if rxi else None,
                    target_rfx_qty=rxi["quantity"] if rxi else None,
                    target_rfx_price=rxi["target_price"] if rxi else None,
                    raw_description=ext_item.raw_description,
                    is_rfx_ambiguous=resolution.is_ambiguous,
                )

                all_response_flags.extend(flags)

                # Pick primary doc ID
                doc_id = resp_data["documents"][0]["id"] if resp_data["documents"] else None

                db_items_to_save.append({
                    "response_id": response_id,
                    "rfx_item_id": match.rfx_item_id,
                    "kind": match.kind.value,
                    "vendor_line_no": ext_item.vendor_line_no,
                    "raw_description": ext_item.raw_description,
                    "raw_specs": ext_item.raw_specs,
                    "raw_qty": ext_item.raw_qty,
                    "raw_price": ext_item.raw_price,
                    "raw_unit": ext_item.raw_unit,
                    "raw_currency": ext_item.raw_currency,
                    "tax_basis": ext_item.tax_basis,
                    "discount": ext_item.discount,
                    "moq": ext_item.moq,
                    "lead_time_days": ext_item.lead_time_days,
                    "remarks": ext_item.remarks,
                    "source_document_id": doc_id,
                    "source_snippet": ext_item.source_snippet,
                    "source_location": ext_item.source_location,
                    "page": ext_item.page or 1,
                    "bbox": ext_item.bbox,
                    "extraction_confidence": ext_item.extraction_confidence,
                    "extraction_reason": ext_item.extraction_reason,
                    "match_candidates": match.match_candidates,
                    "match_confidence": match.match_confidence,
                    "match_reason": match.match_reason,
                    "unit_factor": norm.unit_factor,
                    "fx_rate": norm.fx_rate,
                    "fx_rate_date": norm.fx_rate_date,
                    "normalized_price_inr": norm.normalized_price_inr,
                    "missing_fields": missing,
                    "state": state.value,
                    "flags": flags,
                    "why_unsure": why_unsure,
                    "how_to_resolve": how_to_resolve,
                    "effective_price_inr": norm.normalized_price_inr,
                    "review_status": "pending",
                })

            # Save items to DB
            saved_item_ids = self.repo.save_response_items(db_items_to_save)

            # Generate and save evidence crops
            for idx, item_id in enumerate(saved_item_ids):
                item_spec = db_items_to_save[idx]
                doc_id = item_spec.get("source_document_id")
                if doc_id and doc_id in all_doc_images:
                    self.evidence.generate_and_save_crops(
                        item_id=item_id,
                        document_id=doc_id,
                        page=item_spec.get("page", 1),
                        bbox=item_spec.get("bbox", {}),
                        doc_images=all_doc_images[doc_id],
                    )

            # Save commercial terms and answers
            self.repo.save_response_terms(
                response_id=response_id,
                terms=[t.model_dump() for t in merged_terms],
            )
            self.repo.save_response_answers(
                response_id=response_id,
                answers=[a.model_dump() for a in merged_answers],
            )
            self.repo.save_response_flags(
                response_id=response_id,
                flags=all_response_flags,
            )

            # ---------------------------------------------------------------
            # Phase 6: Response Summary & Final State
            # ---------------------------------------------------------------
            total_rfx_items = len(rfx_items_map) if rfx_items_map else len(db_items_to_save)
            summary = self.validator.compute_summary(
                total_rfx_items=total_rfx_items,
                items=db_items_to_save,
                all_flags=all_response_flags,
                is_rfx_ambiguous=resolution.is_ambiguous,
            )

            final_status = ResponseStatus.NEEDS_REVIEW.value if summary["readiness"] == "needs_review" else ResponseStatus.DONE.value
            self.repo.update_response_status(
                response_id=response_id,
                status=final_status,
                summary=summary,
            )

            self.repo.log_pipeline_event(
                response_id,
                "pipeline",
                "completed",
                duration_ms=int((time.time() - pipeline_start) * 1000),
                detail=summary,
            )
            logger.info("Pipeline completed successfully for response #%s in %sms with status '%s'", response_id, int((time.time() - pipeline_start) * 1000), final_status)

        except Exception as exc:
            logger.exception("Pipeline failed for response #%s: %s", response_id, exc)
            self.repo.update_response_status(response_id, status=ResponseStatus.FAILED.value, error=str(exc))
            self.repo.log_pipeline_event(
                response_id,
                "pipeline",
                "failed",
                duration_ms=int((time.time() - pipeline_start) * 1000),
                detail={"error": str(exc)},
            )

    # -----------------------------------------------------------------------
    # Reprocess & Buyer Correction APIs
    # -----------------------------------------------------------------------

    async def reprocess_response(self, response_id: int) -> dict[str, Any]:
        """Re-run the full extraction and matching pipeline on existing documents as a new run."""
        resp = self.repo.get_response_by_id(response_id)
        if not resp:
            raise ValueError(f"Vendor response #{response_id} does not exist.")

        # Create new version response inheriting the raw documents
        new_version = resp["version"] + 1
        self.repo.supersede_prior_response(response_id)

        new_response_id = self.repo.create_vendor_response(
            rfx_id=resp["rfx_id"],
            vendor_id=resp["vendor_id"],
            channel=resp["channel"],
            subject=resp["subject"],
            sender_name=resp["sender_name"],
            sender_email=resp["sender_email"],
            body_text=resp["body_text"],
            body_json=resp.get("body_json"),
            received_at=resp["received_at"],
            version=new_version,
            supersedes_response_id=response_id,
        )

        # Copy document records
        for doc in resp["documents"]:
            raw_data = self.repo.get_document_raw(doc["id"])
            if raw_data:
                raw_bytes, fname, mime = raw_data
                self.repo.add_response_document(
                    response_id=new_response_id,
                    filename=fname,
                    mime=mime,
                    size_bytes=len(raw_bytes),
                    sha256=doc["sha256"],
                    raw_bytes=raw_bytes,
                )

        # Trigger pipeline
        await self.run_pipeline(new_response_id)
        return self.repo.get_response_by_id(new_response_id)

    def apply_buyer_correction(self, item_id: int, correction_data: dict[str, Any]) -> dict[str, Any]:
        """Apply buyer correction override on a line item."""
        res = self.repo.update_item_buyer_correction(item_id, correction_data, reviewed_by=correction_data.get("reviewed_by", "buyer"))
        if not res:
            raise ValueError(f"Response item #{item_id} not found.")
        return res
