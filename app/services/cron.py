import logging
import asyncio
from datetime import datetime, timezone
from typing import Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit import Payment, PaymentStatus
from app.services.lawyer_assignment import (
    process_expired_offers,
    process_sla_breaches,
    process_lawyer_submit_breaches,
)

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler()


# ──────────────────────────────────────────────────────────
# PAYMENT EXPIRY JOB (New addition for the cron)
# ──────────────────────────────────────────────────────────
async def process_expired_payments(db):
    """
    Finds PENDING payments that weren't paid within 24 hours
    and marks them as EXPIRED.
    """
    now = datetime.now(timezone.utc)
    
    stmt = select(Payment).where(
        Payment.status == PaymentStatus.PENDING,
        Payment.expires_at < now
    ).with_for_update(skip_locked=True)
    
    expired_payments = (await db.execute(stmt)).scalars().all()
    
    for p in expired_payments:
        p.status = PaymentStatus.EXPIRED
        logger.info(f"[Cron] Payment {p.reference_code} auto-expired.")
        
    if expired_payments:
        await db.commit()


# ──────────────────────────────────────────────────────────
# JOB RUNNER HELPER
# ──────────────────────────────────────────────────────────
async def _run_job(job_name: str, job_func: Callable, db):
    """Wrapper to catch and log exceptions so one failure doesn't crash the scheduler."""
    try:
        await job_func(db)
    except Exception as e:
        logger.error(f"[Cron Error] Job '{job_name}' failed: {e}", exc_info=True)
        await db.rollback()


# ──────────────────────────────────────────────────────────
# SCHEDULER TASKS
# ──────────────────────────────────────────────────────────
async def cron_15_minutes():
    """
    Runs every 15 minutes:
    1. Rotate expired 3-hour offers to the next lawyer.
    2. Re-queue tasks where lawyers missed the 3-hour submit deadline.
    """
    async with AsyncSessionLocal() as db:
        logger.info("[Cron] Running 15-minute HITL checks...")
        await _run_job("process_expired_offers", process_expired_offers, db)
        await _run_job("process_lawyer_submit_breaches", process_lawyer_submit_breaches, db)


async def cron_1_hour():
    """
    Runs every 1 hour:
    1. Refund clients who breached the 3-day global SLA.
    2. Expire abandoned SSLCommerz pending payments.
    """
    async with AsyncSessionLocal() as db:
        logger.info("[Cron] Running hourly SLA & Payment checks...")
        await _run_job("process_sla_breaches", process_sla_breaches, db)
        await _run_job("process_expired_payments", process_expired_payments, db)


# ──────────────────────────────────────────────────────────
# LIFECYCLE MANAGEMENT (Called from main.py)
# ──────────────────────────────────────────────────────────
def start_scheduler():
    """Attaches jobs and starts the scheduler."""
    if scheduler.running:
        logger.warning("[Cron] Scheduler is already running.")
        return

    # Add 15-minute jobs
    scheduler.add_job(
        cron_15_minutes,
        trigger=IntervalTrigger(minutes=15),
        id="hitl_15_min",
        name="HITL Offer & Submit Checks",
        replace_existing=True,
    )

    # Add 1-hour jobs
    scheduler.add_job(
        cron_1_hour,
        trigger=IntervalTrigger(hours=1),
        id="sla_1_hour",
        name="SLA Breach & Payment Expiry",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("✅ Background Cron Scheduler started successfully.")


def shutdown_scheduler():
    """Gracefully stops the scheduler on server shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 Background Cron Scheduler stopped.")