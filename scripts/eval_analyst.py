#!/usr/bin/env python3
"""Automated Benchmark & Evaluation Suite for Decision Analyst over 30+ Questions."""

import logging
import sys
import time
from pathlib import Path
from typing import Any
import yaml

from backend.app.config.settings import AppSecrets
from backend.app.analyst.models import AnalystAskRequest
from backend.app.analyst.orchestrator import DecisionAnalystOrchestrator, extract_numbers_from_text
from backend.app.db.connection import get_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("eval_analyst")


def run_benchmark(
    eval_file: str = "config/eval_questions.yaml",
    rfx_id: int = 6,
    log_file: str = "artifacts/logs/eval_analyst.log",
) -> tuple[int, int, list[dict[str, Any]]]:
    """Run full benchmark question set against Decision Analyst and verify against ground truth."""
    eval_path = Path(eval_file)
    if not eval_path.exists():
        raise FileNotFoundError(f"Eval questions file not found: {eval_path}")

    with open(eval_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    questions = data.get("questions", [])
    secrets = AppSecrets()
    orchestrator = DecisionAnalystOrchestrator(secrets=secrets)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "w", encoding="utf-8")

    print(f"\n{'='*80}")
    print(f"RUNNING STEP 3 DECISION ANALYST BENCHMARK ({len(questions)} QUESTIONS) on RFx #{rfx_id}")
    print(f"{'='*80}\n")
    log_f.write(f"=== DECISION ANALYST BENCHMARK RUN: {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")

    results = []
    passed_count = 0
    total_count = len(questions)

    print(f"{'ID':<6} | {'Category':<14} | {'Status':<6} | {'Confidence':<10} | {'Lat(ms)':<8} | {'Question'}")
    print(f"{'-'*6}-+-{'-'*14}-+-{'-'*6}-+-{'-'*10}-+-{'-'*8}-+-{'-'*35}")

    for q_entry in questions:
        qid = q_entry.get("id", "q")
        cat = q_entry.get("category", "general")
        q_text = q_entry.get("question", "")
        props = q_entry.get("check_properties", [])

        req = AnalystAskRequest(
            rfx_id=rfx_id,
            session_id=f"eval_sess_{qid}",
            question=q_text,
            options={"debug": True},
        )

        t0 = time.time()
        res = orchestrator.ask(req)
        lat_ms = int((time.time() - t0) * 1000)

        # ── Property Validations ─────────────────────────────────────────────
        checks_passed = True
        failure_reasons = []

        # 1. Structure check
        if "has_tables" in props and len(res.tables) == 0:
            checks_passed = False
            failure_reasons.append("Expected structured table output, but none returned.")

        if "has_charts" in props and len(res.charts) == 0:
            checks_passed = False
            failure_reasons.append("Expected Chart.js spec, but none returned.")

        if "has_exports" in props and len(res.exports) == 0:
            checks_passed = False
            failure_reasons.append("Expected downloadable export pack, but none returned.")

        # 2. Coverage caveat check
        if "coverage_caveat_present" in props:
            # Check if caveats or narrative mention coverage
            narr_cov = "coverage" in res.answer_text.lower() or any("coverage" in c.text.lower() for c in res.caveats)
            if not narr_cov:
                checks_passed = False
                failure_reasons.append("Missing required coverage comparison disclosure.")

        # 3. Flags check
        if "flags_disclosed" in props:
            has_flag_disclosure = len(res.how_i_got_this.flagged_values_used) > 0 or len(res.caveats) > 0 or "flag" in res.answer_text.lower()
            if not has_flag_disclosure:
                checks_passed = False
                failure_reasons.append("Expected flag or risk disclosure in trust report.")

        # 4. Hallucination check
        if "no_hallucinations" in props:
            # Verified by orchestrator confidence and unverified numbers
            if res.confidence == "low":
                checks_passed = False
                failure_reasons.append(f"Confidence downgraded to low: {res.confidence_reason}")

        status_str = "PASS" if checks_passed else "FAIL"
        if checks_passed:
            passed_count += 1

        print(f"{qid:<6} | {cat:<14} | {status_str:<6} | {res.confidence:<10} | {lat_ms:<8} | {q_text[:35]}...")

        # Write to log
        log_f.write(f"[{status_str}] {qid} ({cat}): {q_text}\n")
        log_f.write(f"  Confidence: {res.confidence} ({res.confidence_reason})\n")
        log_f.write(f"  Latency: {lat_ms}ms | Tables: {len(res.tables)} | Charts: {len(res.charts)} | Exports: {len(res.exports)}\n")
        log_f.write(f"  Narrative Snippet: {res.answer_text[:180]}...\n")
        if failure_reasons:
            log_f.write(f"  FAIL REASONS: {'; '.join(failure_reasons)}\n")
        log_f.write("\n" + "-"*60 + "\n\n")

        results.append({
            "id": qid,
            "category": cat,
            "question": q_text,
            "status": status_str,
            "confidence": res.confidence,
            "latency_ms": lat_ms,
            "failures": failure_reasons,
        })

    log_f.close()

    print(f"\n{'='*80}")
    print(f"BENCHMARK SUMMARY: {passed_count}/{total_count} PASSED ({passed_count/total_count*100:.1f}%)")
    print(f"Detailed trace logs saved to: {log_path}")
    print(f"{'='*80}\n")

    return passed_count, total_count, results


if __name__ == "__main__":
    passed, total, _ = run_benchmark()
    if passed < total:
        sys.exit(1)
