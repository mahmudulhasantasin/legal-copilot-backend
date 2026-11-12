"""Evaluators for Advotech / ContractMind legal benchmark suites."""

from scripts.benchmark.evaluators.rag_evaluator import evaluate_rag_suite, evaluate_rag_case
from scripts.benchmark.evaluators.contract_evaluator import evaluate_contract_suite, evaluate_contract_case
from scripts.benchmark.evaluators.hallucination_evaluator import (
    evaluate_hallucination_suite,
    evaluate_hallucination_case,
)
from scripts.benchmark.evaluators.pdf_evaluator import evaluate_ocr_suite, evaluate_ocr_case
from scripts.benchmark.evaluators.performance_evaluater import (
    summarize_performance,
    push_benchmark_events_to_db,
)

__all__ = [
    "evaluate_rag_suite",
    "evaluate_rag_case",
    "evaluate_contract_suite",
    "evaluate_contract_case",
    "evaluate_hallucination_suite",
    "evaluate_hallucination_case",
    "evaluate_ocr_suite",
    "evaluate_ocr_case",
    "summarize_performance",
    "push_benchmark_events_to_db",
]
