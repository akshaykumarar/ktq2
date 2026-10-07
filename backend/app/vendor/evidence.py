"""Visual evidence cropping engine."""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image

from backend.app.vendor.repository import VendorRepository

logger = logging.getLogger(__name__)


def crop_image_region(
    image: Image.Image,
    bbox: dict[str, Any] | None = None,
    padding_pct: float = 0.10,
) -> bytes:
    """Crop region from an image using normalized or absolute coordinates with padding."""
    width, height = image.size

    if not bbox or not isinstance(bbox, dict) or not all(k in bbox for k in ("ymin", "xmin", "ymax", "xmax")):
        # Fallback: Crop upper middle region (where quotation table usually lives)
        crop_box = (0, int(height * 0.1), width, int(height * 0.6))
    else:
        # Check if coordinates are normalized (0.0 - 1.0 or 0 - 1000)
        ymin = float(bbox["ymin"])
        xmin = float(bbox["xmin"])
        ymax = float(bbox["ymax"])
        xmax = float(bbox["xmax"])

        if ymax <= 1.0 and xmax <= 1.0:
            ymin *= height
            ymax *= height
            xmin *= width
            xmax *= width
        elif ymax <= 1000 and xmax <= 1000 and (ymax > 1.0 or xmax > 1.0):
            ymin = (ymin / 1000.0) * height
            ymax = (ymax / 1000.0) * height
            xmin = (xmin / 1000.0) * width
            xmax = (xmax / 1000.0) * width

        # Apply padding
        pad_y = (ymax - ymin) * padding_pct
        pad_x = (xmax - xmin) * padding_pct

        left = max(0, int(xmin - pad_x))
        top = max(0, int(ymin - pad_y))
        right = min(width, int(xmax + pad_x))
        bottom = min(height, int(ymax + pad_y))

        crop_box = (left, top, right, bottom)

    cropped = image.crop(crop_box)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


class EvidenceEngine:
    """Handles generation and storage of visual evidence crops."""

    def __init__(self, repo: VendorRepository) -> None:
        self.repo = repo

    def generate_and_save_crops(
        self,
        item_id: int,
        document_id: int | None,
        page: int,
        bbox: dict[str, Any],
        doc_images: dict[int, Image.Image],
    ) -> int | None:
        """Create crop for an item and save to DB."""
        img = doc_images.get(page) or doc_images.get(1)
        if not img:
            return None

        try:
            crop_bytes = crop_image_region(img, bbox)
            crop_id = self.repo.save_item_crop(
                item_id=item_id,
                document_id=document_id,
                page=page,
                bbox=bbox,
                image_bytes=crop_bytes,
                mime="image/png",
            )
            return crop_id
        except Exception as exc:
            logger.error("Failed to generate evidence crop for item %s: %s", item_id, exc)
            return None
