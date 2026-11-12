"""PDF & OCR ingestion evaluator against scripts/benchmark/datasets/ocr_cases.json."""

from __future__ import annotations

import logging
import pathlib
import re
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from app.services.clause_segmenter import extract_clauses_from_text
from app.services.ingestion import extract_text_from_file
from app.services.legal_normalizer import BN_TO_EN

logger = logging.getLogger(__name__)

_DATASETS_DIR = pathlib.Path(__file__).resolve().parents[1] / "datasets"


_PUNCT_TRANSLATE = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
})


def _normalize_ws(text: str) -> str:
    cleaned = unicodedata.normalize("NFC", text or "").translate(_PUNCT_TRANSLATE)
    return re.sub(r"\s+", " ", cleaned.strip())


def _char_edit_distance(a: str, b: str) -> int:
    """Standard Levenshtein distance on bounded line/window strings."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j + 1] + 1, curr[-1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _best_anchor_cer(ground_truth: str, extracted: str) -> float:
    """Computes CER per ground-truth line against best-matching window in extracted text."""
    gt_lines = [_normalize_ws(ln) for ln in ground_truth.splitlines() if ln.strip()]
    ext_norm = _normalize_ws(extracted)
    if not gt_lines:
        return 0.0
    if not ext_norm:
        return 1.0

    total_chars = 0
    total_edits = 0
    for line in gt_lines:
        total_chars += len(line)
        if line in ext_norm:
            continue
        # Slide window over extracted text with SequenceMatcher to find closest region
        win = len(line)
        if len(ext_norm) <= win:
            total_edits += _char_edit_distance(line, ext_norm)
            continue
        best_ratio = 0.0
        best_slice = ext_norm[:win]
        step = max(1, min(12, win // 6))
        for start in range(0, len(ext_norm) - win + 1, step):
            candidate = ext_norm[start:start + win]
            ratio = SequenceMatcher(None, line, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_slice = candidate
                if ratio > 0.96:
                    break
        total_edits += _char_edit_distance(line, best_slice)

    return min(1.0, total_edits / max(1, total_chars))


def _entity_recall(entities: List[str], extracted: str) -> float:
    if not entities:
        return 1.0
    ext_norm = _normalize_ws(extracted).translate(BN_TO_EN).lower()
    hits = 0
    for ent in entities:
        e_norm = _normalize_ws(ent).translate(BN_TO_EN).lower()
        if e_norm in ext_norm:
            hits += 1
    return hits / len(entities)


async def evaluate_ocr_case(
    case: Dict[str, Any],
    datasets_dir: pathlib.Path = _DATASETS_DIR,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Runs PDF/DOCX/OCR extraction + AST segmentation on a fixture file."""
    case_id = case["id"]
    fixture_rel = case["fixture_path"]
    fixture_file = datasets_dir / fixture_rel

    if not fixture_file.exists():
        return {
            "id": case_id,
            "suite": "ocr",
            "doc_type": case.get("doc_type"),
            "language": case.get("language"),
            "status": "SKIPPED_MISSING_FIXTURE",
            "passed": None,
            "latency_ms": 0,
            "fixture_path": str(fixture_rel),
        }

    t0 = time.perf_counter()
    try:
        raw_bytes = fixture_file.read_bytes()
        if dry_run:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return {
                "id": case_id,
                "suite": "ocr",
                "mode": "dry_run",
                "doc_type": case.get("doc_type"),
                "language": case.get("language"),
                "status": "PASSED",
                "passed": len(raw_bytes) > 0,
                "latency_ms": latency_ms,
                "fixture_bytes": len(raw_bytes),
            }

        extracted_text = await extract_text_from_file(fixture_file.name, raw_bytes)
        clauses = extract_clauses_from_text(extracted_text)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        cer = _best_anchor_cer(case.get("ground_truth_text", ""), extracted_text)
        ent_recall = _entity_recall(case.get("required_entities", []), extracted_text)
        min_clauses = int(case.get("expected_min_clauses", 1))
        clause_count = len(clauses)
        max_cer = float(case.get("max_cer_threshold", 0.15))

        passed = cer <= max_cer and ent_recall >= 0.8 and clause_count >= min_clauses

        return {
            "id": case_id,
            "suite": "ocr",
            "doc_type": case.get("doc_type"),
            "language": case.get("language"),
            "status": "PASSED" if passed else "FAILED",
            "passed": passed,
            "latency_ms": latency_ms,
            "extracted_chars": len(extracted_text),
            "cer": round(cer, 4),
            "max_cer_threshold": max_cer,
            "entity_recall": round(ent_recall, 4),
            "parsed_clauses": clause_count,
            "expected_min_clauses": min_clauses,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.exception(f"[pdf_evaluator] Case {case_id} crashed: {exc}")
        return {
            "id": case_id,
            "suite": "ocr",
            "status": "ERROR",
            "passed": False,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


async def evaluate_ocr_suite(
    cases: List[Dict[str, Any]],
    datasets_dir: pathlib.Path = _DATASETS_DIR,
    dry_run: bool = False,
    on_result=None,
    resume: Optional[Dict[str, Dict[str, Any]]] = None,
    max_concurrency: int = 2,
) -> Dict[str, Any]:
    """Evaluates OCR cases with bounded concurrency; streams each result via on_result."""
    import asyncio

    resume = resume or {}
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(case: Dict[str, Any]) -> Dict[str, Any]:
        if case["id"] in resume:
            return resume[case["id"]]
        async with sem:
            result = await evaluate_ocr_case(case, datasets_dir=datasets_dir, dry_run=dry_run)
        if on_result:
            try:
                on_result(result)
            except Exception:
                logger.debug("on_result callback failed", exc_info=True)
        return result

    results = await asyncio.gather(*(_one(case) for case in cases))

    ran = [r for r in results if r.get("status") not in ("SKIPPED_MISSING_FIXTURE", "ERROR")]
    skipped = [r for r in results if r.get("status") == "SKIPPED_MISSING_FIXTURE"]
    en_ran = [r for r in ran if r.get("language") == "en"]
    bn_ran = [r for r in ran if "bn" in str(r.get("language", ""))]

    summary = {
        "total": len(results),
        "executed": len(ran),
        "skipped_missing_fixtures": len(skipped),
        "passed": sum(1 for r in ran if r.get("passed")),
        "errors": sum(1 for r in results if r.get("status") == "ERROR"),
        "avg_cer_en": _avg([r.get("cer", 0.0) for r in en_ran]),
        "avg_cer_bn": _avg([r.get("cer", 0.0) for r in bn_ran]),
        "entity_recall": _avg([r.get("entity_recall", 0.0) for r in ran]),
        "clause_count_pass_rate": _avg(
            [1.0 if r.get("parsed_clauses", 0) >= r.get("expected_min_clauses", 1) else 0.0 for r in ran]
        ),
    }
    return {"suite": "ocr", "summary": summary, "cases": results}


def _avg(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0
