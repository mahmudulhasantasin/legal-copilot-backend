import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import attributes
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lawyer import LawyerProfile, LawyerStatus, LawyerAvailability
from app.models.memo import ContractMemo, Contract, HITLStatus, OfferStatus, RiskLevel
from app.models.user import User, UserRole
from app.core.security import refund_credits
from app.services.email_service import send_lawyer_offer_email

logger = logging.getLogger(__name__)

W_SPEC = 0.40
W_LOAD = 0.35
W_REP = 0.15
B_NEW = 0.10
B_RISK = 0.15

OFFER_WINDOW_HOURS = 3

def _calculate_match_score(lawyer: LawyerProfile, contract_risk: RiskLevel, active_tasks_count: int) -> float:
    spec_score = 0.5
    load_score = max(0.0, 1.0 - (active_tasks_count / max(lawyer.max_concurrent_tasks, 1)))
    rep_score = lawyer.rating_average / 5.0
    newcomer_boost = B_NEW if lawyer.completed_reviews_count < 5 else 0.0
    risk_boost = B_RISK if (contract_risk in [RiskLevel.HIGH, RiskLevel.CRITICAL] and lawyer.experience_years >= 3) else 0.0

    final_score = (
        (W_SPEC * spec_score) + (W_LOAD * load_score) + 
        (W_REP * rep_score) + newcomer_boost + risk_boost
    )
    return round(final_score, 4)

async def rank_lawyers_for_task(db: AsyncSession, memo: ContractMemo, limit: int = 5) -> List[Dict[str, Any]]:
    stmt = select(LawyerProfile).where(
        LawyerProfile.status == LawyerStatus.APPROVED,
        LawyerProfile.availability != LawyerAvailability.ON_LEAVE,
    )
    lawyers = (await db.execute(stmt)).scalars().all()

    scored_lawyers = []
    for lawyer in lawyers:
        if lawyer.active_task_count >= lawyer.max_concurrent_tasks:
            continue
        if memo.overall_risk in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            if lawyer.experience_years < 2 and lawyer.completed_reviews_count < 3:
                continue

        score = _calculate_match_score(lawyer, memo.overall_risk, lawyer.active_task_count)
        scored_lawyers.append({
            "lawyer_id": lawyer.id, "score": score, "status": OfferStatus.PENDING.value,
            "offered_at": None, "expires_at": None
        })

    scored_lawyers.sort(key=lambda x: x["score"], reverse=True)
    return scored_lawyers[:limit]

async def generate_initial_offer(db: AsyncSession, memo: ContractMemo) -> bool:
    ranked_list = await rank_lawyers_for_task(db, memo)
    if not ranked_list:
        return False
    memo.hitl_ranked_lawyers = ranked_list
    await db.flush()
    return await assign_next_offer(db, memo)

async def assign_next_offer(db: AsyncSession, memo: ContractMemo) -> bool:
    now = datetime.now(timezone.utc)
    ranked_list = memo.hitl_ranked_lawyers or []
    
    next_lawyer = None
    for item in ranked_list:
        if item["status"] in [OfferStatus.PENDING.value, OfferStatus.EXPIRED.value, OfferStatus.DECLINED.value]:
            next_lawyer = item
            break

    if not next_lawyer:
        logger.info(f"[HITL] Ranked list exhausted for Memo #{memo.id}. Moving to broadcast queue.")
        memo.hitl_status = HITLStatus.QUEUED_FOR_LAWYER
        memo.hitl_current_offer_id = None
        memo.hitl_offer_expires_at = None
        await db.commit()
        return False

    next_lawyer["status"] = OfferStatus.ACTIVE.value
    next_lawyer["offered_at"] = now.isoformat()
    next_lawyer["expires_at"] = (now + timedelta(hours=OFFER_WINDOW_HOURS)).isoformat()

    memo.hitl_status = HITLStatus.OFFERED
    memo.hitl_current_offer_id = next_lawyer["lawyer_id"]
    memo.hitl_offer_expires_at = now + timedelta(hours=OFFER_WINDOW_HOURS)
    
    attributes.flag_modified(memo, "hitl_ranked_lawyers")
    await db.commit()

    # ✅ FETCH LAWYER & SEND EMAIL
    lawyer_profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == next_lawyer["lawyer_id"]))
    if lawyer_profile:
        lawyer_user = await db.scalar(select(User).where(User.id == lawyer_profile.user_id))
        contract = await db.scalar(select(Contract).where(Contract.id == memo.contract_id))
        
        if lawyer_user and contract:
            await send_lawyer_offer_email(
                lawyer_user.email, contract.title, float(memo.lawyer_payout_bdt)
            )

    logger.info(f"[HITL] Offered Memo #{memo.id} to Lawyer #{next_lawyer['lawyer_id']}")
    return True

async def process_expired_offers(db: AsyncSession):
    now = datetime.now(timezone.utc)
    stmt = select(ContractMemo).where(
        ContractMemo.hitl_status == HITLStatus.OFFERED,
        ContractMemo.hitl_offer_expires_at < now
    ).with_for_update(skip_locked=True)
    expired_memos = (await db.execute(stmt)).scalars().all()
    for memo in expired_memos:
        ranked_list = memo.hitl_ranked_lawyers or []
        for item in ranked_list:
            if item["lawyer_id"] == memo.hitl_current_offer_id and item["status"] == OfferStatus.ACTIVE.value:
                item["status"] = OfferStatus.EXPIRED.value
        attributes.flag_modified(memo, "hitl_ranked_lawyers")
        await assign_next_offer(db, memo)

async def process_sla_breaches(db: AsyncSession):
    now = datetime.now(timezone.utc)
    stmt = select(ContractMemo).where(
        ContractMemo.hitl_status.in_([HITLStatus.QUEUED_FOR_LAWYER, HITLStatus.OFFERED, HITLStatus.ASSIGNED, HITLStatus.IN_REVIEW]),
        ContractMemo.hitl_global_deadline < now
    ).with_for_update(skip_locked=True)
    breached_memos = (await db.execute(stmt)).scalars().all()
    for memo in breached_memos:
        logger.warning(f"[HITL] SLA Breached for Memo #{memo.id}. Refunding client.")
        contract = await db.scalar(select(Contract).where(Contract.id == memo.contract_id))
        if contract:
            client_user = await db.scalar(select(User).where(User.id == contract.user_id))
            if client_user:
                await refund_credits(
                    db=db, user=client_user, amount=memo.hitl_credits_paid,
                    reason=f"Auto-refund: SLA breached for Memo #{memo.id}", reference_id=str(memo.id),
                    enterprise_id=contract.enterprise_id if client_user.is_enterprise_user else None,
                )
        memo.hitl_status = HITLStatus.REFUNDED
        memo.refunded_at = now
        memo.refund_reason = "SLA deadline (3 days) breached."
        if memo.lawyer_id:
            lawyer_prof = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == memo.lawyer_id).with_for_update())
            if lawyer_prof:
                lawyer_prof.active_task_count = max(0, lawyer_prof.active_task_count - 1)
                lawyer_prof.availability = LawyerAvailability.AVAILABLE
        await db.commit()

async def process_lawyer_submit_breaches(db: AsyncSession):
    now = datetime.now(timezone.utc)
    stmt = select(ContractMemo).where(
        ContractMemo.hitl_status.in_([HITLStatus.ASSIGNED, HITLStatus.IN_REVIEW]),
        ContractMemo.submit_deadline < now
    ).with_for_update(skip_locked=True)
    breached_memos = (await db.execute(stmt)).scalars().all()
    for memo in breached_memos:
        logger.warning(f"[HITL] Lawyer missed 3h submit deadline for Memo #{memo.id}.")
        if memo.lawyer_id:
            lawyer_prof = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == memo.lawyer_id).with_for_update())
            if lawyer_prof:
                lawyer_prof.active_task_count = max(0, lawyer_prof.active_task_count - 1)
                lawyer_prof.availability = LawyerAvailability.AVAILABLE
        memo.lawyer_id = None
        memo.hitl_status = HITLStatus.QUEUED_FOR_LAWYER
        await assign_next_offer(db, memo)