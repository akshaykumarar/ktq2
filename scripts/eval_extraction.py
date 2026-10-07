"""Evaluation script: runs sample quotations through the pipeline and verifies against ground truth."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root in sys.path
root_dir = Path(__file__).resolve().parents[1]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.config.settings import load_config
from backend.app.vendor.service import VendorResponseService

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
GROUND_TRUTH_FILE = Path(__file__).resolve().parent / "ground_truth.json"


async def evaluate_all() -> None:
    """Run evaluation on all generated sample files."""
    config = load_config()
    service = VendorResponseService(config)

    ground_truth = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))

    print("=" * 80)
    print("STARTING END-TO-END VENDOR EXTRACTION & VALIDATION PIPELINE EVALUATION")
    print("=" * 80)

    total_samples = len(ground_truth)
    total_items_evaluated = 0
    total_items_correct = 0
    total_correctly_flagged_for_review = 0
    total_silently_wrong = 0
    total_crops_generated = 0

    results_table = []

    for sample_key, expected in ground_truth.items():
        print(f"\n--> Evaluating: {sample_key}")
        # Find sample file
        files = list(SAMPLES_DIR.glob(f"{sample_key}.*"))
        if not files:
            print(f"Sample file for {sample_key} not found!")
            continue

        sample_path = files[0]
        content = sample_path.read_bytes()

        # Submit response
        preview = service.submit_vendor_response(
            files=[(sample_path.name, content, None)],
            channel="simulated",
            rfx_ref=str(expected.get("expected_rfx_id", "")),
        )

        if preview.is_duplicate:
            response_detail = await service.reprocess_response(preview.response_id)
            active_resp_id = response_detail["id"]
        else:
            await service.run_pipeline(preview.response_id)
            response_detail = service.repo.get_response_by_id(preview.response_id)
            active_resp_id = preview.response_id

        # Retrieve processed details
        items = service.repo.get_response_items(active_resp_id)

        # Evaluate against ground truth
        matched_expected = expected.get("expected_items", [])
        sample_correct = 0
        sample_flagged_review = 0
        sample_silent_errors = 0
        sample_crops = 0

        for exp_item in matched_expected:
            total_items_evaluated += 1
            # Find matching extracted item
            sub = exp_item["description_substring"].lower()
            matching_actual = next((it for it in items if sub in it["raw_description"].lower()), None)

            if not matching_actual:
                print(f"   [MISSING EXTRACTION] Expected item containing '{sub}' was not found!")
                total_silently_wrong += 1
                sample_silent_errors += 1
                continue

            # Check price / state / flags
            is_correct = True

            if "expected_raw_price" in exp_item:
                if matching_actual.get("raw_price") != exp_item["expected_raw_price"]:
                    print(f"   [PRICE MISMATCH] For '{sub}': expected {exp_item['expected_raw_price']}, got {matching_actual.get('raw_price')}")
                    is_correct = False

            if "expected_kind" in exp_item:
                if matching_actual.get("kind") != exp_item["expected_kind"]:
                    print(f"   [KIND MISMATCH] For '{sub}': expected {exp_item['expected_kind']}, got {matching_actual.get('kind')}")
                    is_correct = False

            if "expected_normalized_price_inr_approx" in exp_item:
                actual_norm = matching_actual.get("normalized_price_inr")
                exp_norm = exp_item["expected_normalized_price_inr_approx"]
                if actual_norm is None or abs(actual_norm - exp_norm) > 1.0:
                    print(f"   [NORMALIZATION MISMATCH] For '{sub}': expected ~{exp_norm}, got {actual_norm}")
                    is_correct = False

            # Check review state
            if matching_actual.get("state") == "REVIEW":
                sample_flagged_review += 1
                total_correctly_flagged_for_review += 1

            if matching_actual.get("has_crop"):
                sample_crops += 1
                total_crops_generated += 1

            if is_correct:
                sample_correct += 1
                total_items_correct += 1
            else:
                sample_silent_errors += 1
                total_silently_wrong += 1

        results_table.append({
            "sample": sample_key,
            "response_id": preview.response_id,
            "status": response_detail["status"],
            "items_extracted": len(items),
            "items_correct": sample_correct,
            "review_flagged": sample_flagged_review,
            "crops_count": sample_crops,
            "silent_errors": sample_silent_errors,
        })

    # Print summary evaluation table
    print("\n" + "=" * 80)
    print("PIPELINE EVALUATION SUMMARY REPORT")
    print("=" * 80)
    print(f"{'Sample Name':<35} | {'Resp ID':<8} | {'Status':<12} | {'Extracted':<10} | {'Correct':<8} | {'Silent Errors':<14}")
    print("-" * 95)
    for row in results_table:
        print(f"{row['sample']:<35} | {row['response_id']:<8} | {row['status']:<12} | {row['items_extracted']:<10} | {row['items_correct']:<8} | {row['silent_errors']:<14}")

    print("\n" + "=" * 80)
    print(f"TOTAL ITEMS EVALUATED:          {total_items_evaluated}")
    print(f"ITEMS EXTRACTED CORRECTLY:      {total_items_correct} / {total_items_evaluated} ({round(total_items_correct/max(total_items_evaluated,1)*100, 1)}%)")
    print(f"ITEMS FLAGGED FOR REVIEW:       {total_correctly_flagged_for_review}")
    print(f"CROPS GENERATED FOR EVIDENCE:   {total_crops_generated}")
    print(f"ITEMS SILENTLY WRONG:           {total_silently_wrong} (TARGET: ZERO)")
    print("=" * 80)

    if total_silently_wrong == 0:
        print(">>> EVALUATION PASSED: Zero silent extraction errors! <<<")
    else:
        print(">>> EVALUATION WARNING: Silent errors detected! <<<")


if __name__ == "__main__":
    asyncio.run(evaluate_all())
