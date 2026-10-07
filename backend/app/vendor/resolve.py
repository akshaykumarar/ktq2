"""Scored RFx and Vendor resolution engine."""

from __future__ import annotations

import re
import difflib
import logging
from typing import Any

from backend.app.vendor.models import ExtractedVendorInfo
from backend.app.vendor.repository import VendorRepository

logger = logging.getLogger(__name__)


def calculate_text_similarity(a: str, b: str) -> float:
    """Compute token/sequence similarity between two strings."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


class ResolutionResult:
    """Result of RFx and Vendor resolution."""

    def __init__(
        self,
        rfx_id: int | None,
        rfx_title: str | None,
        signal_used: str,
        confidence: float,
        reasoning: str,
        alternatives: list[dict[str, Any]],
        is_ambiguous: bool,
        vendor_id: int | None,
        vendor_name: str,
        vendor_is_new: bool,
        vendor_confidence: float,
        vendor_reasoning: str,
    ) -> None:
        self.rfx_id = rfx_id
        self.rfx_title = rfx_title
        self.signal_used = signal_used
        self.confidence = confidence
        self.reasoning = reasoning
        self.alternatives = alternatives
        self.is_ambiguous = is_ambiguous
        self.vendor_id = vendor_id
        self.vendor_name = vendor_name
        self.vendor_is_new = vendor_is_new
        self.vendor_confidence = vendor_confidence
        self.vendor_reasoning = vendor_reasoning

    def to_rfx_dict(self) -> dict[str, Any]:
        """Convert RFx resolution to dictionary."""
        return {
            "rfx_id": self.rfx_id,
            "rfx_title": self.rfx_title,
            "signal": self.signal_used,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "is_ambiguous": self.is_ambiguous,
            "alternatives": self.alternatives,
        }

    def to_vendor_dict(self) -> dict[str, Any]:
        """Convert vendor resolution to dictionary."""
        return {
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "is_new": self.vendor_is_new,
            "confidence": round(self.vendor_confidence, 3),
            "reasoning": self.vendor_reasoning,
        }


class ResolutionEngine:
    """Resolves which RFx and which Vendor an incoming quote belongs to."""

    def __init__(self, repo: VendorRepository) -> None:
        self.repo = repo

    def resolve(
        self,
        rfx_ref: str | None,
        vendor_name: str | None,
        sender_email: str | None,
        sender_name: str | None,
        subject: str | None,
        body_text: str | None,
        combined_doc_text: str,
        extracted_vendor: ExtractedVendorInfo | None = None,
        extracted_item_descs: list[str] | None = None,
    ) -> ResolutionResult:
        """Execute 6-tier scored resolution algorithm."""
        open_rfx_list = self.repo.get_open_rfx_list()
        all_text = f"{subject or ''} {body_text or ''} {combined_doc_text}".lower()

        # -------------------------------------------------------------------
        # 1. Resolve RFx
        # -------------------------------------------------------------------
        scored_candidates: list[dict[str, Any]] = []

        # Signal 1: Explicit rfx_ref provided
        if rfx_ref and rfx_ref.strip():
            ref_clean = rfx_ref.strip()
            # Match by integer ID
            if ref_clean.isdigit():
                target_id = int(ref_clean)
                matched = next((r for r in open_rfx_list if r["id"] == target_id), None)
                if matched:
                    scored_candidates.append({
                        "rfx_id": matched["id"],
                        "title": matched["title"],
                        "score": 1.0,
                        "signal": "explicit_rfx_ref_id",
                        "reasoning": f"Exact match on explicit rfx_ref ID #{target_id}.",
                    })
            # Match by title or reference code
            for r in open_rfx_list:
                sim = calculate_text_similarity(ref_clean, r["title"])
                if sim > 0.6 or ref_clean.lower() in r["title"].lower():
                    scored_candidates.append({
                        "rfx_id": r["id"],
                        "title": r["title"],
                        "score": 0.95,
                        "signal": "explicit_rfx_ref_text",
                        "reasoning": f"Explicit rfx_ref '{ref_clean}' matched RFx #{r['id']} title '{r['title']}'.",
                    })

        # Signal 2: Reference regex in text (e.g. RFQ-123, RFX #2, Ref: 2, RFI-2)
        if not scored_candidates:
            # Match pattern like (rfx|rfi|rfq)[\s#:-]*(\d+)
            matches = re.findall(r"(?:rfx|rfi|rfq|ref|quote for)[\s#:-]*(\d+)", all_text, re.IGNORECASE)
            for m_id in matches:
                target_id = int(m_id)
                matched = next((r for r in open_rfx_list if r["id"] == target_id), None)
                if matched:
                    scored_candidates.append({
                        "rfx_id": matched["id"],
                        "title": matched["title"],
                        "score": 0.90,
                        "signal": "text_pattern_reference",
                        "reasoning": f"Found reference pattern pointing to RFx #{target_id} in message/document text.",
                    })

        # Signal 3: Reply thread hints / Quoted title in subject
        if not scored_candidates and subject:
            sub_clean = re.sub(r"^(re:|fwd:)\s*", "", subject, flags=re.IGNORECASE).strip()
            for r in open_rfx_list:
                sim = calculate_text_similarity(sub_clean, r["title"])
                if sim > 0.5:
                    scored_candidates.append({
                        "rfx_id": r["id"],
                        "title": r["title"],
                        "score": 0.80,
                        "signal": "reply_subject_match",
                        "reasoning": f"Email subject '{subject}' corresponds closely to RFx title '{r['title']}'.",
                    })

        # Signal 4: Content similarity with RFx line items
        if extracted_item_descs:
            for r in open_rfx_list:
                rfx_item_descs = [it["description"].lower() for it in r.get("items", [])]
                if not rfx_item_descs:
                    continue
                overlap_scores = []
                for quoted_desc in extracted_item_descs:
                    best_match_for_item = max(
                        (calculate_text_similarity(quoted_desc, target) for target in rfx_item_descs),
                        default=0.0,
                    )
                    overlap_scores.append(best_match_for_item)
                avg_overlap = sum(overlap_scores) / len(overlap_scores) if overlap_scores else 0.0
                if avg_overlap > 0.4:
                    scored_candidates.append({
                        "rfx_id": r["id"],
                        "title": r["title"],
                        "score": round(0.50 + (avg_overlap * 0.35), 3),
                        "signal": "content_line_item_similarity",
                        "reasoning": f"Extracted line items have {round(avg_overlap*100, 1)}% semantic overlap with items in RFx #{r['id']}.",
                    })

        # Signal 5: Fallback to most recent open RFx if nothing matched
        if not scored_candidates and open_rfx_list:
            top_open = open_rfx_list[0]
            scored_candidates.append({
                "rfx_id": top_open["id"],
                "title": top_open["title"],
                "score": 0.30,
                "signal": "fallback_recent_rfx",
                "reasoning": f"No definitive RFx reference detected. Defaulting with LOW confidence to most recent open RFx #{top_open['id']}.",
            })

        # Sort candidates by score descending
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        if not scored_candidates:
            # No RFx available in database
            chosen_rfx_id = None
            chosen_title = None
            signal_used = "none"
            confidence = 0.0
            reasoning = "No open RFx records exist in the system."
            is_ambiguous = True
            alternatives = []
        else:
            winner = scored_candidates[0]
            chosen_rfx_id = winner["rfx_id"]
            chosen_title = winner["title"]
            signal_used = winner["signal"]
            confidence = winner["score"]
            reasoning = winner["reasoning"]

            # Ambiguity detection: check if second candidate is close
            is_ambiguous = False
            alternatives = scored_candidates[1:4]
            if len(scored_candidates) > 1:
                runner_up = scored_candidates[1]
                if runner_up["score"] >= 0.6 and (winner["score"] - runner_up["score"]) < 0.15:
                    is_ambiguous = True
                    reasoning += f" (AMBIGUOUS: Close runner-up RFx #{runner_up['rfx_id']} with score {runner_up['score']})"

        # -------------------------------------------------------------------
        # 2. Resolve Vendor
        # -------------------------------------------------------------------
        target_name = (vendor_name or (extracted_vendor.name if extracted_vendor else None) or sender_name or "").strip()
        target_email = (sender_email or (extracted_vendor.email if extracted_vendor else None) or "").strip()
        target_gstin = (extracted_vendor.gstin if extracted_vendor else None) or ""

        existing_vendor = self.repo.find_vendor_by_email_or_name(target_email if target_email else None, target_name if target_name else None)

        if existing_vendor:
            vendor_id = existing_vendor["id"]
            resolved_vendor_name = existing_vendor["name"]
            vendor_is_new = False
            vendor_confidence = 0.95
            vendor_reasoning = f"Matched existing vendor record #{vendor_id} ('{resolved_vendor_name}')."
        else:
            final_name = target_name or (target_email.split("@")[0].capitalize() if target_email else "Unknown Vendor")
            new_v = self.repo.create_vendor(
                name=final_name,
                email=target_email if target_email else None,
                phone=extracted_vendor.phone if extracted_vendor else None,
                gstin=target_gstin if target_gstin else None,
                address=extracted_vendor.address if extracted_vendor else None,
            )
            vendor_id = new_v["id"]
            resolved_vendor_name = new_v["name"]
            vendor_is_new = True
            vendor_confidence = 0.80 if target_name else 0.50
            vendor_reasoning = f"Created new vendor profile #{vendor_id} ('{resolved_vendor_name}') from quotation details."

        return ResolutionResult(
            rfx_id=chosen_rfx_id,
            rfx_title=chosen_title,
            signal_used=signal_used,
            confidence=confidence,
            reasoning=reasoning,
            alternatives=alternatives,
            is_ambiguous=is_ambiguous,
            vendor_id=vendor_id,
            vendor_name=resolved_vendor_name,
            vendor_is_new=vendor_is_new,
            vendor_confidence=vendor_confidence,
            vendor_reasoning=vendor_reasoning,
        )
