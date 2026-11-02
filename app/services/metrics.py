# app/services/metrics.py
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, AsyncGenerator

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metrics import MetricEvent

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# 1. TRACK EVENT (Write Direction)
# ──────────────────────────────────────────────────────────

async def track_event(
    db: AsyncSession,
    event_type: str,
    *,
    user_id: Optional[int] = None,
    entity_id: Optional[int] = None,
    success: bool = True,
    latency_ms: Optional[int] = None,
    event_data: Optional[dict] = None,
    commit: bool = True,
) -> None:
    """
    Append a single metric event to the database.
    ⚠️ Never raises — if metrics fail, the main application flow MUST continue.
    """
    try:
        db.add(MetricEvent(
            event_type=event_type,
            user_id=user_id,
            entity_id=entity_id,
            success=success,
            latency_ms=latency_ms,
            event_data=event_data,
        ))
        if commit:
            await db.commit()
    except Exception as e:
        logger.warning(f"[metrics] track_event failed (event={event_type}): {e}")
        if commit:
            try:
                await db.rollback()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────
# 2. TIMED EVENT (Context Manager)
# ──────────────────────────────────────────────────────────

@asynccontextmanager
async def timed_event(
    db: AsyncSession,
    event_type: str,
    *,
    user_id: Optional[int] = None,
    entity_id: Optional[int] = None,
    event_data: Optional[dict] = None,
    commit: bool = True,
) -> AsyncGenerator[None, None]:
    """
    Context manager that auto-times a block and records success/failure.
    Usage:
        async with timed_event(db, "audit_completed", user_id=user.id):
            result = await analyze_contract_risk_async(...)
    """
    start = time.perf_counter()
    success = True
    try:
        yield
    except Exception:
        success = False
        raise
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await track_event(
            db,
            event_type,
            user_id=user_id,
            entity_id=entity_id,
            success=success,
            latency_ms=latency_ms,
            event_data=event_data,
            commit=commit,
        )


# ──────────────────────────────────────────────────────────
# 3. AGGREGATE METRICS (Read Direction)
# ──────────────────────────────────────────────────────────

async def get_metrics_summary(
    db: AsyncSession,
    days: int = 30,
) -> dict:
    """
    Returns a KPI snapshot for the last N days.
    Used by /api/platform/metrics and /api/admin/system-health.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Helper: Count events ──
    async def _count(event_type: str, success: Optional[bool] = None) -> int:
        q = select(func.count(MetricEvent.id)).where(
            MetricEvent.event_type == event_type,
            MetricEvent.created_at >= since,
        )
        if success is not None:
            q = q.where(MetricEvent.success == success)
        return (await db.execute(q)).scalar_one() or 0

    # ── Helper: Percentile Latency (Postgres) ──
    async def _percentile(event_type: str, p: float) -> Optional[int]:
        try:
            # Postgres percentile_cont (very fast)
            q = select(
                func.percentile_cont(p).within_group(MetricEvent.latency_ms.asc())
            ).where(
                MetricEvent.event_type == event_type,
                MetricEvent.latency_ms.isnot(None),
                MetricEvent.created_at >= since,
            )
            result = (await db.execute(q)).scalar()
            return int(result) if result is not None else None
        except Exception as e:
            # SQLite fallback (for local dev only)
            logger.debug(f"[metrics] percentile_cont failed for {event_type}: {e}")
            return None

    # ── Helper: Latest Benchmark ──
    async def _latest_benchmark(event_type: str) -> Optional[dict]:
        q = (
            select(MetricEvent)
            .where(
                MetricEvent.event_type == event_type,
                MetricEvent.created_at >= since,
            )
            .order_by(MetricEvent.created_at.desc())
            .limit(1)
        )
        row = (await db.execute(q)).scalar_one_or_none()
        return row.event_data if row else None

    # ── Calculate all KPIs ──
    total_citations = await _count("citation_checked")
    verified_citations = await _count("citation_checked", success=True)
    
    total_llm_calls = await _count("llm_call")
    fallback_calls = await _count("llm_fallback")

    chat_queries = await _count("chat_query")
    audits = await _count("audit_completed")
    hitl_requests = await _count("hitl_requested")
    hitl_completed = await _count("hitl_completed")

    audit_accuracy = await _latest_benchmark("audit_accuracy")
    rag_hit_rate = await _latest_benchmark("rag_hit_rate")

    # ── Assemble & Return ──
    return {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ai_quality": {
            "citation_verify_rate": round(
                verified_citations / max(total_citations, 1), 3
            ),
            "citation_total": total_citations,
            "citation_verified": verified_citations,
            "llm_fallback_rate": round(
                fallback_calls / max(total_llm_calls, 1), 3
            ),
            "llm_total_calls": total_llm_calls,
            "audit_accuracy_benchmark": audit_accuracy,
            "rag_hit_rate_benchmark": rag_hit_rate,
        },
        "performance": {
            "chat_p50_ms": await _percentile("chat_query", 0.5),
            "chat_p95_ms": await _percentile("chat_query", 0.95),
            "audit_p50_ms": await _percentile("audit_completed", 0.5),
            "audit_p95_ms": await _percentile("audit_completed", 0.95),
        },
        "business": {
            "chat_queries": chat_queries,
            "audits": audits,
            "hitl_requests": hitl_requests,
            "hitl_completed": hitl_completed,
            "hitl_completion_rate": round(
                hitl_completed / max(hitl_requests, 1), 3
            ),
        },
    }