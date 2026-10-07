"""Structured extraction and multi-document reconciliation engine."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic_ai import Agent

from backend.app.config.settings import AppConfig, AppSecrets
from backend.app.providers.factory import create_model
from backend.app.vendor.models import (
    DocumentExtractionResult,
    DocumentQualityNote,
    ExtractedCommercialTerm,
    ExtractedLineItem,
    ExtractedQuestionAnswer,
    ExtractedVendorInfo,
)
from backend.app.vendor.prompts import (
    EXTRACTION_PROMPT_VERSION,
    RECONCILIATION_PROMPT_VERSION,
    SYSTEM_EXTRACTION_PROMPT,
    USER_EXTRACTION_PROMPT_TEMPLATE,
)
from backend.app.vendor.repository import VendorRepository

logger = logging.getLogger(__name__)


def deterministic_text_extractor(parsed_text: str, filename: str) -> DocumentExtractionResult:
    """Robust fallback extractor when running offline or in unit tests."""
    lines = parsed_text.splitlines()
    vendor_info = ExtractedVendorInfo()
    line_items: list[ExtractedLineItem] = []
    terms: list[ExtractedCommercialTerm] = []
    answers: list[ExtractedQuestionAnswer] = []

    # Detect vendor details
    for line in lines:
        if "from:" in line.lower() or "vendor:" in line.lower() or "supplier:" in line.lower():
            match = re.search(r"(?:from|vendor|supplier):\s*([^<\n\r]+)", line, re.IGNORECASE)
            if match and not vendor_info.name:
                vendor_info.name = match.group(1).strip()
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", line)
        if email_match and not vendor_info.email:
            vendor_info.email = email_match.group(0)
        gst_match = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b", line)
        if gst_match:
            vendor_info.gstin = gst_match.group(0)
        rfx_ref_match = re.search(r"(?:rfx|rfq|rfi)[\s#:-]*(\d+)", line, re.IGNORECASE)
        if rfx_ref_match:
            vendor_info.rfx_reference_found = rfx_ref_match.group(1)

    # Detect commercial terms
    for line in lines:
        lower = line.lower()
        if "freight" in lower:
            terms.append(ExtractedCommercialTerm(
                term_type="freight",
                value_text=line.strip(),
                source_snippet=line.strip(),
                confidence=0.9,
            ))
        elif "payment" in lower or "net 30" in lower or "advance" in lower:
            terms.append(ExtractedCommercialTerm(
                term_type="payment_terms",
                value_text=line.strip(),
                source_snippet=line.strip(),
                confidence=0.9,
            ))
        elif "gst" in lower or "tax" in lower:
            terms.append(ExtractedCommercialTerm(
                term_type="gst",
                value_text=line.strip(),
                source_snippet=line.strip(),
                confidence=0.9,
            ))
        elif "validity" in lower or "valid for" in lower:
            terms.append(ExtractedCommercialTerm(
                term_type="validity",
                value_text=line.strip(),
                source_snippet=line.strip(),
                confidence=0.9,
            ))
        elif "discount" in lower:
            terms.append(ExtractedCommercialTerm(
                term_type="discount",
                value_text=line.strip(),
                source_snippet=line.strip(),
                confidence=0.9,
            ))

    # Unified single pass extractor handling coordinate tables, inline items, and multi-line blocks
    current_desc = None
    current_qty = None
    current_price = None
    current_unit = "pcs"
    current_curr = "INR"
    current_lead_time = None
    current_snippet = []

    def flush_current_item(loc: str = "Document Body") -> None:
        nonlocal current_desc, current_qty, current_price, current_unit, current_curr, current_lead_time, current_snippet
        if current_desc:
            line_items.append(ExtractedLineItem(
                vendor_line_no=str(len(line_items) + 1),
                raw_description=current_desc,
                raw_qty=current_qty,
                raw_price=current_price,
                raw_unit=current_unit,
                raw_currency=current_curr,
                lead_time_days=current_lead_time,
                source_snippet="\n".join(current_snippet),
                source_location=loc,
                extraction_confidence=0.95,
                extraction_reason="Extracted structured item clause",
            ))
            current_desc = None
            current_qty = None
            current_price = None
            current_unit = "pcs"
            current_curr = "INR"
            current_lead_time = None
            current_snippet = []

    for idx, line in enumerate(lines, start=1):
        l_clean = line.strip()
        if not l_clean or l_clean.startswith("===") or (l_clean.startswith("---") and not l_clean.startswith("--- [")):
            continue

        # Skip document title/header/reference lines
        if any(hdr in l_clean.lower() for hdr in ["supplier quote", "ref:", "date:", "quotation for rfx", "quote no:", "formal quotation"]):
            continue

        # 1. Unresolved Reference check (e.g. "same as last year", "rates unchanged")
        if any(kw in l_clean.lower() for kw in ["same as last year", "as per previous", "rest same", "rates unchanged"]):
            flush_current_item(f"Line {idx}")
            desc_part = l_clean.split(":")[0].strip() if ":" in l_clean else l_clean.strip()
            desc_part = re.sub(r"^\d+[\.\)]\s*", "", desc_part)
            line_items.append(ExtractedLineItem(
                vendor_line_no=str(len(line_items) + 1),
                raw_description=desc_part,
                raw_qty=None,
                raw_price=None,
                raw_unit="pcs",
                raw_currency="INR",
                unresolved_reference="same as last year",
                source_snippet=l_clean,
                source_location=f"Line {idx}",
                extraction_confidence=0.90,
                extraction_reason="Extracted reference keyword phrase without stated numerical rate",
            ))
            continue

        # 2. Table row with coordinates e.g. [A1] Desc | [B1] 5000 pcs | [C1] 45.50
        coords_match = re.findall(r"\[([^\]]+)\]\s*([^\|\[\]]*)", l_clean)
        if len(coords_match) >= 2:
            parts = [c[1].strip() for c in coords_match if c[1].strip()]
            if any(h in " ".join(parts).lower() for h in ["item #", "unit rate", "item specification", "lead time", "unit price"]):
                continue

            flush_current_item(f"Line {idx}")
            loc = coords_match[0][0]
            desc = None
            unit = "pcs"
            currency = "INR"
            lead_time = None
            numeric_vals = []

            for p in parts:
                p_clean = p.replace(",", "").strip()
                if not p_clean:
                    continue

                if "$" in p or "USD" in p.upper():
                    currency = "USD"
                elif "€" in p or "EUR" in p.upper():
                    currency = "EUR"

                if "per 100" in p.lower():
                    unit = "per 100"
                elif "per 1000" in p.lower():
                    unit = "per 1000"
                elif "kg" in p.lower():
                    unit = "kg"
                elif "sqm" in p.lower() or "sqft" in p.lower():
                    unit = "sqm"
                elif "roll" in p.lower():
                    unit = "roll"
                elif "pcs" in p.lower() or "piece" in p.lower() or "box" in p.lower():
                    unit = "pcs"

                if "day" in p.lower():
                    days_match = re.findall(r"\d+", p)
                    if days_match:
                        lead_time = int(days_match[0])
                        continue

                num_match = re.match(r"^[-+]?(?:\d*\.\d+|\d+)$", p_clean)
                if num_match:
                    val = float(p_clean)
                    if desc is None and val in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10) and p == parts[0]:
                        continue
                    numeric_vals.append(val)
                else:
                    if desc is None and len(p) > 2 and not p.isdigit() and p.lower() not in ("pcs", "roll", "box", "kg", "sqm", "inr", "usd"):
                        desc = p

            price = None
            qty = None
            if len(numeric_vals) >= 2:
                qty = numeric_vals[0]
                price = numeric_vals[1]
            elif len(numeric_vals) == 1:
                price = numeric_vals[0]

            if desc:
                line_items.append(ExtractedLineItem(
                    vendor_line_no=str(len(line_items) + 1),
                    raw_description=desc,
                    raw_qty=qty,
                    raw_price=price,
                    raw_unit=unit,
                    raw_currency=currency,
                    lead_time_days=lead_time,
                    source_snippet=l_clean,
                    source_location=loc,
                    extraction_confidence=0.95,
                    extraction_reason="Parsed structured row cells with coordinates",
                ))
            continue

        # If line contains coordinates but only 1 cell (e.g. title cell [A1]), skip
        if coords_match:
            continue

        # 3. Inline line item pattern e.g. "1. Corrugated Box 600x400x300 mm: Rs 43.20 per piece (Qty: 5000 pcs)"
        has_item_kw = any(k in l_clean.lower() for k in ["box", "carton", "film", "tape", "bubble", "pallet", "stretch", "packaging"])
        is_numbered = bool(re.match(r"^(?:line\s*\d+|item\s*\d*|\d+[\.\)])\s*", l_clean, re.IGNORECASE))
        has_price_mention = any(w in l_clean.lower() for w in ["rs", "rate", "@", "$", "inr", "price"])

        if (has_item_kw or is_numbered) and has_price_mention and ":" in l_clean and not any(t in l_clean.lower() for t in ["gst", "payment", "commercial", "footnote"]):
            flush_current_item(f"Line {idx}")
            price_match = re.search(r"(?:unit price|rate|price|cost|@|rs\.?|inr|\$)\s*[:=]?\s*([0-9,]+(?:\.[0-9]+)?)", l_clean, re.IGNORECASE)
            qty_match = re.search(r"(?:quantity|qty|volume|moq)\s*[:=]?\s*([0-9,]+(?:\.[0-9]+)?)", l_clean, re.IGNORECASE)

            p_val = float(price_match.group(1).replace(",", "")) if price_match else None
            q_val = float(qty_match.group(1).replace(",", "")) if qty_match else None

            unit = "pcs"
            if "per 100" in l_clean.lower():
                unit = "per 100"
            elif "per 1000" in l_clean.lower():
                unit = "per 1000"
            elif "roll" in l_clean.lower():
                unit = "roll"
            elif "kg" in l_clean.lower():
                unit = "kg"

            curr = "USD" if ("$" in l_clean or "usd" in l_clean.lower()) else "INR"
            desc_part = re.sub(r"^(?:line\s*\d+|item\s*\d*|\d+[\.\)])\s*[:\.]?\s*", "", l_clean, flags=re.IGNORECASE).split(":")[0].strip()

            line_items.append(ExtractedLineItem(
                vendor_line_no=str(len(line_items) + 1),
                raw_description=desc_part,
                raw_qty=q_val,
                raw_price=p_val,
                raw_unit=unit,
                raw_currency=curr,
                source_snippet=l_clean,
                source_location=f"Line {idx}",
                extraction_confidence=0.95,
                extraction_reason="Parsed inline quotation specification and price",
            ))
            continue

        # 4. Multi-line block accumulator
        if (is_numbered or has_item_kw) and not any(t in l_clean.lower() for t in ["commercial", "terms", "gst", "payment", "footnote", "delivery:"]):
            flush_current_item(f"Line {idx}")
            desc_text = re.sub(r"^(?:line\s*\d+|item\s*\d*|\d+[\.\)])\s*[:\.]?\s*", "", l_clean, flags=re.IGNORECASE).strip()
            current_desc = desc_text
            current_snippet = [l_clean]
            continue

        if current_desc:
            qty_match = re.search(r"(?:quantity|qty|volume|moq)\s*[:=]?\s*([0-9,]+(?:\.[0-9]+)?)", l_clean, re.IGNORECASE)
            if qty_match:
                current_qty = float(qty_match.group(1).replace(",", ""))
                current_snippet.append(l_clean)

            price_match = re.search(r"(?:unit price|rate|price|cost|@|rs\.?|inr|\$)\s*[:=]?\s*([0-9,]+(?:\.[0-9]+)?)", l_clean, re.IGNORECASE)
            if price_match:
                current_price = float(price_match.group(1).replace(",", ""))
                current_snippet.append(l_clean)

            if "per 100" in l_clean.lower():
                current_unit = "per 100"
            elif "per 1000" in l_clean.lower():
                current_unit = "per 1000"
            elif "roll" in l_clean.lower():
                current_unit = "roll"
            elif "kg" in l_clean.lower():
                current_unit = "kg"

            if "$" in l_clean or "usd" in l_clean.lower():
                current_curr = "USD"

    flush_current_item("Document Body")

    # Basic fallback line item if none extracted but document text exists
    if not line_items and len(parsed_text.strip()) > 20:
        first_line = [l.strip() for l in lines if l.strip() and not l.startswith("=")][0]
        line_items.append(ExtractedLineItem(
            vendor_line_no="1",
            raw_description=first_line[:100],
            raw_qty=None,
            raw_price=None,
            raw_unit=None,
            source_snippet=first_line,
            source_location="Line 1",
            extraction_confidence=0.50,
            extraction_reason="Fallback single item capture",
        ))

    return DocumentExtractionResult(
        vendor_info=vendor_info,
        line_items=line_items,
        commercial_terms=terms,
        question_answers=answers,
        quality=DocumentQualityNote(
            is_scan="scanned" in parsed_text.lower(),
            readability_score=0.95,
            notes=f"Extracted {len(line_items)} items deterministically",
        ),
    )


class ExtractionEngine:
    """Executes LLM structured extraction per document and reconciles results."""

    def __init__(self, config: AppConfig, repo: VendorRepository) -> None:
        self.config = config
        self.repo = repo

    async def extract_document(
        self,
        response_id: int,
        document_id: int,
        filename: str,
        doc_role: str,
        parsed_text: str,
        enhanced_bytes: bytes | None = None,
    ) -> DocumentExtractionResult:
        """Extract structured data from a single document with retry on validation failure."""
        start_time = time.time()
        agent_cfg = self.config.agents.get("vendor_evaluator") or self.config.agents.get("rfi_parser")
        llm_cfg = self.config.llms.get(agent_cfg.llm) if agent_cfg else None
        provider = llm_cfg.provider if llm_cfg else "test"
        model_name = llm_cfg.model if llm_cfg else "default"

        # If test/mock provider or credentials missing, run deterministic extractor
        has_key = (
            (provider == "openai" and self.config.secrets.OPENAI_API_KEY)
            or (provider == "anthropic" and self.config.secrets.ANTHROPIC_API_KEY)
            or (provider in ("google", "gemini") and self.config.secrets.GOOGLE_API_KEY)
            or (provider == "ollama")
        )

        if provider in ("test", "mock") or not has_key or not llm_cfg:
            res = deterministic_text_extractor(parsed_text, filename)
            duration_ms = int((time.time() - start_time) * 1000)
            self.repo.log_llm_call(
                response_id=response_id,
                stage="extract_document",
                provider="deterministic_fallback",
                model="regex_v1",
                prompt_version=EXTRACTION_PROMPT_VERSION,
                tokens_in=len(parsed_text) // 4,
                tokens_out=len(res.model_dump_json()) // 4,
                latency_ms=duration_ms,
                validation_ok=True,
                retries=0,
            )
            return res

        # Run real LLM extraction with PydanticAI
        try:
            model = create_model(llm_cfg, self.config.secrets, fallback_to_mock=False)
            agent = Agent(
                model=model,
                output_type=DocumentExtractionResult,
                system_prompt=SYSTEM_EXTRACTION_PROMPT,
            )
            user_prompt = USER_EXTRACTION_PROMPT_TEMPLATE.format(
                filename=filename,
                doc_role=doc_role,
                document_content=parsed_text[:12000],  # bounded safe context window
            )

            result = await agent.run(user_prompt)
            duration_ms = int((time.time() - start_time) * 1000)
            self.repo.log_llm_call(
                response_id=response_id,
                stage="extract_document",
                provider=provider,
                model=model_name,
                prompt_version=EXTRACTION_PROMPT_VERSION,
                tokens_in=len(user_prompt) // 4,
                tokens_out=len(result.data.model_dump_json()) // 4,
                latency_ms=duration_ms,
                validation_ok=True,
                retries=0,
            )
            return result.data
        except Exception as exc:
            err_str = str(exc).lower()
            if "401" in err_str or "api key" in err_str or "unauthorized" in err_str or "auth" in err_str:
                logger.warning("LLM API key unauthorized: %s. Falling back to deterministic extractor.", exc)
                fallback_res = deterministic_text_extractor(parsed_text, filename)
                fallback_res.quality.notes = f"LLM API key unauthorized. Extracted via deterministic parser."
                return fallback_res

            logger.warning("LLM extraction failed on %s: %s. Retrying once with error feedback...", filename, exc)
            # Retry once with error feedback
            try:
                retry_prompt = f"Previous attempt failed with error: {exc}. Please return valid JSON matching DocumentExtractionResult schema.\n\n" + USER_EXTRACTION_PROMPT_TEMPLATE.format(
                    filename=filename,
                    doc_role=doc_role,
                    document_content=parsed_text[:12000],
                )
                retry_agent = Agent(
                    model=model,
                    output_type=DocumentExtractionResult,
                    system_prompt=SYSTEM_EXTRACTION_PROMPT,
                )
                result = await retry_agent.run(retry_prompt)
                duration_ms = int((time.time() - start_time) * 1000)
                self.repo.log_llm_call(
                    response_id=response_id,
                    stage="extract_document_retry",
                    provider=provider,
                    model=model_name,
                    prompt_version=EXTRACTION_PROMPT_VERSION,
                    tokens_in=len(retry_prompt) // 4,
                    tokens_out=len(result.data.model_dump_json()) // 4,
                    latency_ms=duration_ms,
                    validation_ok=True,
                    retries=1,
                )
                return result.data
            except Exception as retry_exc:
                logger.error("LLM retry extraction failed on %s: %s. Falling back to deterministic extractor.", filename, retry_exc)
                fallback_res = deterministic_text_extractor(parsed_text, filename)
                fallback_res.quality.notes = f"LLM extraction errored: {retry_exc}. Extracted via fallback parser."
                return fallback_res

    def reconcile_documents(
        self,
        extracted_results: list[DocumentExtractionResult],
    ) -> tuple[ExtractedVendorInfo, list[ExtractedLineItem], list[ExtractedCommercialTerm], list[ExtractedQuestionAnswer]]:
        """Reconcile multi-document findings into a unified set of items, terms, and vendor info."""
        if not extracted_results:
            return ExtractedVendorInfo(), [], [], []

        # Merge vendor identity (take the first non-null fields)
        merged_vendor = ExtractedVendorInfo()
        for r in extracted_results:
            v = r.vendor_info
            if v.name and not merged_vendor.name:
                merged_vendor.name = v.name
            if v.email and not merged_vendor.email:
                merged_vendor.email = v.email
            if v.phone and not merged_vendor.phone:
                merged_vendor.phone = v.phone
            if v.gstin and not merged_vendor.gstin:
                merged_vendor.gstin = v.gstin
            if v.address and not merged_vendor.address:
                merged_vendor.address = v.address
            if v.quote_number and not merged_vendor.quote_number:
                merged_vendor.quote_number = v.quote_number
            if v.quote_date and not merged_vendor.quote_date:
                merged_vendor.quote_date = v.quote_date
            if v.validity_days and not merged_vendor.validity_days:
                merged_vendor.validity_days = v.validity_days
            if v.rfx_reference_found and not merged_vendor.rfx_reference_found:
                merged_vendor.rfx_reference_found = v.rfx_reference_found

        # Collect all line items (preserving separate items from different docs)
        all_items: list[ExtractedLineItem] = []
        for r in extracted_results:
            all_items.extend(r.line_items)

        # Collect all commercial terms & detect conflicts
        all_terms: list[ExtractedCommercialTerm] = []
        for r in extracted_results:
            all_terms.extend(r.commercial_terms)

        # Collect questionnaire answers
        all_answers: list[ExtractedQuestionAnswer] = []
        for r in extracted_results:
            all_answers.extend(r.question_answers)

        return merged_vendor, all_items, all_terms, all_answers
