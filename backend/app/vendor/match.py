"""Semantic RFx Item Matcher with candidate scoring, extras, alternates, and not-quoted detection."""

from __future__ import annotations

import difflib
import logging
from typing import Any

from backend.app.vendor.models import ExtractedLineItem, ItemKind
from backend.app.vendor.repository import VendorRepository

logger = logging.getLogger(__name__)


def score_item_similarity(
    vendor_desc: str,
    vendor_specs: dict[str, Any],
    rfx_item: dict[str, Any],
) -> tuple[float, str]:
    """Score similarity between a quoted line and an RFx line item."""
    rfx_desc = rfx_item.get("description", "").lower()
    v_desc = vendor_desc.lower()

    # Exact string match
    if v_desc == rfx_desc:
        return 1.0, f"Exact description match with RFx Item #{rfx_item.get('item_number')}."

    # Substring match
    if rfx_desc in v_desc or v_desc in rfx_desc:
        return 0.90, f"Strong substring match on '{rfx_desc}'."

    # Sequence matcher token similarity
    ratio = difflib.SequenceMatcher(None, v_desc, rfx_desc).ratio()

    # Keyword overlap (e.g. "box", "film", "tape", "bubble", "5-ply")
    v_tokens = set(v_desc.replace("-", " ").replace("/", " ").split())
    r_tokens = set(rfx_desc.replace("-", " ").replace("/", " ").split())
    overlap = v_tokens.intersection(r_tokens)
    overlap_ratio = len(overlap) / max(len(r_tokens), 1)

    combined_score = max(ratio, overlap_ratio)

    # Check for alternate ply/dimension differences or explicitly mentioned alternate
    is_alternate = False
    if ("4-ply" in v_desc and "5-ply" in rfx_desc) or ("3-ply" in v_desc and "5-ply" in rfx_desc) or "alternate" in v_desc or "substitute" in v_desc:
        is_alternate = True

    reason = f"Semantic similarity score {round(combined_score, 2)} with RFx Item #{rfx_item.get('item_number')} ('{rfx_item.get('description')}')."
    if is_alternate:
        reason += " (Detected alternate specification deviation)"

    return combined_score, reason


class MatchResultItem:
    """Evaluated match between extracted item and RFx item."""

    def __init__(
        self,
        extracted_item: ExtractedLineItem,
        rfx_item_id: int | None,
        rfx_item_number: int | None,
        rfx_item_description: str | None,
        kind: ItemKind,
        match_candidates: list[dict[str, Any]],
        match_confidence: float,
        match_reason: str,
    ) -> None:
        self.extracted_item = extracted_item
        self.rfx_item_id = rfx_item_id
        self.rfx_item_number = rfx_item_number
        self.rfx_item_description = rfx_item_description
        self.kind = kind
        self.match_candidates = match_candidates
        self.match_confidence = match_confidence
        self.match_reason = match_reason


class ItemMatcher:
    """Matches a list of extracted lines against RFx line items."""

    def __init__(self, repo: VendorRepository) -> None:
        self.repo = repo

    def match_items(
        self,
        rfx_id: int | None,
        extracted_items: list[ExtractedLineItem],
    ) -> list[MatchResultItem]:
        """Perform candidate scoring and return evaluated matched items + NOT_QUOTED items."""
        if not rfx_id:
            # If no RFx is linked, mark all lines as EXTRA
            return [
                MatchResultItem(
                    extracted_item=item,
                    rfx_item_id=None,
                    rfx_item_number=None,
                    rfx_item_description=None,
                    kind=ItemKind.EXTRA,
                    match_candidates=[],
                    match_confidence=0.0,
                    match_reason="No RFx linked to response. Categorized as unlinked EXTRA item.",
                )
                for item in extracted_items
            ]

        rfx_items = self.repo.get_rfx_items_for_rfx(rfx_id)
        if not rfx_items:
            return [
                MatchResultItem(
                    extracted_item=item,
                    rfx_item_id=None,
                    rfx_item_number=None,
                    rfx_item_description=None,
                    kind=ItemKind.EXTRA,
                    match_candidates=[],
                    match_confidence=0.0,
                    match_reason="RFx has no configured line items. Categorized as EXTRA.",
                )
                for item in extracted_items
            ]

        matched_results: list[MatchResultItem] = []
        quoted_rfx_ids: set[int] = set()

        for item in extracted_items:
            # Score against all RFx items
            candidate_scores: list[dict[str, Any]] = []
            for rxi in rfx_items:
                score, reason = score_item_similarity(
                    vendor_desc=item.raw_description,
                    vendor_specs=item.raw_specs,
                    rfx_item=rxi,
                )
                candidate_scores.append({
                    "rfx_item_id": rxi["id"],
                    "rfx_item_number": rxi["item_number"],
                    "description": rxi["description"],
                    "score": round(score, 3),
                    "reason": reason,
                })

            # Sort candidate scores descending
            candidate_scores.sort(key=lambda x: x["score"], reverse=True)
            top_candidate = candidate_scores[0] if candidate_scores else None

            if top_candidate and top_candidate["score"] >= 0.55:
                # Strong or good match
                is_alternate = (
                    "alternate" in top_candidate["reason"].lower()
                    or "alternate" in item.raw_description.lower()
                    or "substitute" in item.raw_description.lower()
                    or "4-ply" in item.raw_description.lower()
                )
                kind = ItemKind.ALTERNATE if is_alternate else ItemKind.MATCHED
                target_rfx_id = top_candidate["rfx_item_id"]
                quoted_rfx_ids.add(target_rfx_id)

                matched_results.append(MatchResultItem(
                    extracted_item=item,
                    rfx_item_id=target_rfx_id,
                    rfx_item_number=top_candidate["rfx_item_number"],
                    rfx_item_description=top_candidate["description"],
                    kind=kind,
                    match_candidates=candidate_scores[:3],
                    match_confidence=top_candidate["score"],
                    match_reason=top_candidate["reason"],
                ))
            else:
                # Below threshold -> EXTRA item offered by vendor
                matched_results.append(MatchResultItem(
                    extracted_item=item,
                    rfx_item_id=None,
                    rfx_item_number=None,
                    rfx_item_description=None,
                    kind=ItemKind.EXTRA,
                    match_candidates=candidate_scores[:3],
                    match_confidence=top_candidate["score"] if top_candidate else 0.0,
                    match_reason="Low similarity to RFx items. Categorized as EXTRA line item.",
                ))

        # Check for RFx items not quoted by vendor -> Add explicit NOT_QUOTED rows
        for rxi in rfx_items:
            if rxi["id"] not in quoted_rfx_ids:
                not_quoted_item = ExtractedLineItem(
                    vendor_line_no=None,
                    raw_description=f"[Unquoted] {rxi['description']}",
                    raw_qty=rxi.get("quantity"),
                    raw_price=None,
                    raw_unit=rxi.get("unit"),
                    raw_currency="INR",
                    source_snippet=None,
                    source_location=None,
                    extraction_confidence=1.0,
                    extraction_reason="Generated unquoted placeholder for RFx coverage tracking",
                )
                matched_results.append(MatchResultItem(
                    extracted_item=not_quoted_item,
                    rfx_item_id=rxi["id"],
                    rfx_item_number=rxi["item_number"],
                    rfx_item_description=rxi["description"],
                    kind=ItemKind.NOT_QUOTED,
                    match_candidates=[],
                    match_confidence=1.0,
                    match_reason=f"RFx Item #{rxi['item_number']} was not quoted in vendor response.",
                ))

        return matched_results
