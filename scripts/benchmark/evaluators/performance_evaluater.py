"""Performance telemetry aggregator and optional DB metric pusher."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _percentile(sorted_vals: List[int], p: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil(p * len(sorted_vals))) - 1))
    return int(sorted_vals[idx])


def summarize_performance(suite_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes p50/p95/mean/max latency, pass rates, and JSON compliance per suite and overall."""
    all_latencies: List[int] = []
    per_suite: Dict[str, Any] = {}
    total_executed = 0
    total_passed = 0
    total_errors = 0

    for suite_out in suite_outputs:
        name = suite_out.get("suite", "unknown")
        cases = suite_out.get("cases", [])
        executed = [c for c in cases if c.get("status") != "SKIPPED_MISSING_FIXTURE"]
        latencies = sorted(int(c.get("latency_ms", 0)) for c in executed if c.get("latency_ms") is not None)

        passed = sum(1 for c in executed if c.get("passed") is True)
        errors = sum(1 for c in executed if c.get("status") == "ERROR")

        total_executed += len(executed)
        total_passed += passed
        total_errors += errors
        all_latencies.extend(latencies)

        per_suite[name] = {
            "executed": len(executed),
            "passed": passed,
            "errors": errors,
            "pass_rate": round(passed / len(executed), 4) if executed else 0.0,
            "p50_ms": _percentile(latencies, 0.50),
            "p95_ms": _percentile(latencies, 0.95),
            "mean_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "max_ms": max(latencies) if latencies else 0,
        }

    all_latencies.sort()
    return {
        "overall": {
            "executed": total_executed,
            "passed": total_passed,
            "errors": total_errors,
            "pass_rate": round(total_passed / total_executed, 4) if total_executed else 0.0,
            "p50_ms": _percentile(all_latencies, 0.50),
            "p95_ms": _percentile(all_latencies, 0.95),
            "mean_ms": int(sum(all_latencies) / len(all_latencies)) if all_latencies else 0,
            "max_ms": max(all_latencies) if all_latencies else 0,
        },
        "by_suite": per_suite,
    }


async def push_benchmark_events_to_db(model_profile: str, suites: List[Dict[str, Any]], perf: Dict[str, Any]) -> bool:
    """Pushes benchmark suite summary metrics into metric_events via track_event."""
    try:
        from app.config import settings
        if not getattr(settings, "DATABASE_URL", ""):
            logger.warning("[performance_evaluater] DATABASE_URL not set; skipping DB metric push.")
            return False

        from app.core.database import AsyncSessionLocal
        from app.services.metrics import track_event

        async with AsyncSessionLocal() as db:
            overall = perf.get("overall", {})
            await track_event(
                db,
                event_type="benchmark_run_summary",
                success=(overall.get("errors", 0) == 0),
                latency_ms=overall.get("p50_ms"),
                event_data={
                    "model_profile": model_profile,
                    "overall": overall,
                    "by_suite": perf.get("by_suite", {}),
                },
            )
            for suite_out in suites:
                suite_name = suite_out.get("suite")
                suite_perf = perf.get("by_suite", {}).get(suite_name, {})
                await track_event(
                    db,
                    event_type=f"benchmark_suite_{suite_name}",
                    success=(suite_perf.get("errors", 0) == 0),
                    latency_ms=suite_perf.get("p50_ms"),
                    event_data={
                        "model_profile": model_profile,
                        "summary": suite_out.get("summary", {}),
                        "perf": suite_perf,
                    },
                )
        return True
    except Exception as exc:
        logger.warning(f"[performance_evaluater] DB push failed cleanly: {exc}")
        return False
