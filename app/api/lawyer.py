import logging
import os        # ✅ NEW
import uuid      # ✅ NEW
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, UploadFile, File # ✅ NEW
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import create_client # ✅ NEW

from app.config import settings # ✅ NEW
from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.core.tier_limits import CONTRACT_REVIEW_LIMITS
from app.models.user import (
    User, UserRole, CreditTxType, CreditTransaction,
    WithdrawalRequest, WithdrawalStatus,
)
from app.models.lawyer import LawyerProfile, LawyerStatus, LawyerAvailability
from app.models.memo import ContractMemo, Contract, HITLStatus
from app.models.chat import ChatSession, ChatMode
from app.services.email_service import (
    send_client_lawyer_assigned_email,
    send_client_verification_complete_email,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lawyer", tags=["Lawyer HITL"])

# ──────────────────────────────────────────────────────────
# GUARDS & HELPERS
# ──────────────────────────────────────────────────────────
async def get_current_lawyer(
    current_user: User = Depends(require_roles([UserRole.LAWYER])),
    db: AsyncSession = Depends(get_db),
) -> LawyerProfile:
    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.user_id == current_user.id))
    if not profile:
        raise HTTPException(404, "Lawyer profile not found.")
    if profile.status != LawyerStatus.APPROVED:
        raise HTTPException(403, f"Lawyer account is {profile.status.value}.")
    return profile

async def _get_assigned_memo(db: AsyncSession, memo_id: int, lawyer_id: int) -> ContractMemo:
    memo = await db.scalar(select(ContractMemo).where(ContractMemo.id == memo_id, ContractMemo.lawyer_id == lawyer_id))
    if not memo:
        raise HTTPException(404, "Assigned contract not found or claimed by another lawyer.")
    return memo

# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────
class DeclineRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=255)

class SubmitReviewRequest(BaseModel):
    updated_clauses: List[dict] = Field(..., description="Modified clauses from AI audit")
    lawyer_notes: str = Field(min_length=5, max_length=5000)
    signature_url: str = Field(min_length=5, max_length=500, description="Supabase path for signature image")
    signed_pdf_url: str = Field(min_length=5, max_length=500, description="Supabase path for final signed PDF")

class WithdrawRequest(BaseModel):
    amount_bdt: Decimal = Field(gt=0, le=100000)
    bkash_number: str = Field(min_length=11, max_length=20)
    account_holder_name: str = Field(min_length=2, max_length=255)

# ──────────────────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────────────────
@router.get("/queue")
async def get_my_active_offers(lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    stmt = select(ContractMemo, Contract).join(Contract, ContractMemo.contract_id == Contract.id).where(
        ContractMemo.hitl_status == HITLStatus.OFFERED,
        ContractMemo.hitl_current_offer_id == lawyer.id,
        ContractMemo.hitl_offer_expires_at > now,
    ).order_by(ContractMemo.hitl_offer_expires_at.asc())
    rows = (await db.execute(stmt)).all()
    return [
        {
            "memo_id": memo.id, "contract_id": memo.contract_id, "contract_title": contract.title,
            "overall_risk": memo.overall_risk.value, "risk_score": memo.risk_score,
            "payout_bdt": float(memo.lawyer_payout_bdt), "offer_expires_at": memo.hitl_offer_expires_at,
            "time_left_seconds": int((memo.hitl_offer_expires_at - now).total_seconds()),
        }
        for memo, contract in rows
    ]

@router.post("/queue/{memo_id}/accept")
async def accept_offer(
    memo_id: int,
    background_tasks: BackgroundTasks,
    lawyer: LawyerProfile = Depends(get_current_lawyer),
    db: AsyncSession = Depends(get_db),
):
    memo = await db.scalar(select(ContractMemo).where(ContractMemo.id == memo_id).with_for_update())
    if not memo:
        raise HTTPException(404, "Contract memo not found.")
    if memo.hitl_current_offer_id != lawyer.id or memo.hitl_status != HITLStatus.OFFERED:
        raise HTTPException(403, "This task is not currently offered to you.")

    now = datetime.now(timezone.utc)
    if memo.hitl_offer_expires_at < now:
        memo.hitl_status = HITLStatus.QUEUED_FOR_LAWYER
        memo.hitl_current_offer_id = None
        await db.commit()
        raise HTTPException(400, "Offer expired. Task has been re-queued.")

    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == lawyer.id).with_for_update())
    if profile.active_task_count >= profile.max_concurrent_tasks:
        raise HTTPException(400, "You have reached your maximum concurrent tasks limit.")

    profile.active_task_count += 1
    if profile.active_task_count >= profile.max_concurrent_tasks:
        profile.availability = LawyerAvailability.BUSY

    memo.lawyer_id = lawyer.id
    memo.hitl_status = HITLStatus.ASSIGNED
    memo.assigned_at = now
    memo.submit_deadline = now + timedelta(hours=3)
    memo.hitl_current_offer_id = None

    await db.commit()

    # ✅ Queue Client Email
    contract = await db.scalar(select(Contract).where(Contract.id == memo.contract_id))
    if contract:
        client = await db.scalar(select(User).where(User.id == contract.user_id))
        lawyer_user = await db.scalar(select(User).where(User.id == lawyer.user_id))
        if client and lawyer_user:
            background_tasks.add_task(
                send_client_lawyer_assigned_email,
                client.email, lawyer_user.full_name, contract.title
            )

    return {"status": "success", "memo_id": memo.id, "submit_deadline": memo.submit_deadline}

@router.post("/queue/{memo_id}/decline")
async def decline_offer(memo_id: int, payload: DeclineRequest, lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    memo = await db.scalar(select(ContractMemo).where(ContractMemo.id == memo_id).with_for_update())
    if not memo or memo.hitl_current_offer_id != lawyer.id or memo.hitl_status != HITLStatus.OFFERED:
        raise HTTPException(403, "This task is not currently offered to you.")
    memo.hitl_status = HITLStatus.QUEUED_FOR_LAWYER
    memo.hitl_current_offer_id = None
    memo.hitl_offer_expires_at = None
    await db.commit()
    return {"status": "success", "message": "Offer declined. Task has been re-queued."}

@router.get("/active")
async def get_my_active_tasks(lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    stmt = select(ContractMemo, Contract).join(Contract, ContractMemo.contract_id == Contract.id).where(
        ContractMemo.lawyer_id == lawyer.id,
        ContractMemo.hitl_status.in_([HITLStatus.ASSIGNED, HITLStatus.IN_REVIEW]),
    ).order_by(ContractMemo.submit_deadline.asc())
    rows = (await db.execute(stmt)).all()
    return [
        {
            "memo_id": memo.id, "contract_id": memo.contract_id, "contract_title": contract.title,
            "hitl_status": memo.hitl_status.value, "payout_bdt": float(memo.lawyer_payout_bdt),
            "submit_deadline": memo.submit_deadline, "hours_left": memo.hours_until_submit,
            "clauses_analysis": memo.clauses_analysis, # ✅ FIX: এআই ক্লজগুলো ফ্রন্টএন্ডে পাঠানোর জন্য
        }
        for memo, contract in rows
    ]

# ✅ NEW: Upload proof endpoint
@router.post("/upload-proof")
async def upload_lawyer_proof(
    file: UploadFile = File(...),
    lawyer: LawyerProfile = Depends(get_current_lawyer),
):
    """
    Uploads a signature image or signed PDF to Supabase.
    Returns the storage path to be used in submit_review.
    """
    SUPABASE_URL = getattr(settings, "SUPABASE_URL", "")
    SUPABASE_KEY = getattr(settings, "SUPABASE_SERVICE_KEY", "")
    BUCKET_NAME = "contracts"

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(500, "Storage not configured")

    ext = os.path.splitext(file.filename)[1].lower()
    file_bytes = await file.read()
    
    # Save as: proof/lawyer_{id}_{uuid}.ext
    safe_name = f"proof/lawyer_{lawyer.id}_{uuid.uuid4().hex[:8]}{ext}"

    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        supabase.storage.from_(BUCKET_NAME).upload(
            path=safe_name,
            file=file_bytes,
            file_options={"content-type": file.content_type}
        )
        return {"status": "success", "file_path": safe_name}
    except Exception as e:
        logger.error(f"[Supabase] Proof upload failed: {e}")
        raise HTTPException(500, "Failed to upload proof file")

@router.post("/active/{memo_id}/submit")
async def submit_review(
    memo_id: int,
    payload: SubmitReviewRequest,
    background_tasks: BackgroundTasks,
    lawyer: LawyerProfile = Depends(get_current_lawyer),
    db: AsyncSession = Depends(get_db),
):
    memo = await db.scalar(select(ContractMemo).where(ContractMemo.id == memo_id).with_for_update())
    if not memo or memo.lawyer_id != lawyer.id:
        raise HTTPException(404, "Not your assigned task.")
    if memo.hitl_status not in [HITLStatus.ASSIGNED, HITLStatus.IN_REVIEW]:
        raise HTTPException(400, f"Cannot submit. Status is {memo.hitl_status.value}.")

    now = datetime.now(timezone.utc)
    if memo.submit_deadline and now > memo.submit_deadline:
        raise HTTPException(400, "Submission deadline has passed. Task is being re-queued.")

    memo.hitl_status = HITLStatus.COMPLETED
    memo.completed_at = now
    memo.clauses_analysis = payload.updated_clauses  # ✅ এডিট করা ক্লজগুলো ডাটাবেসে সেভ হচ্ছে
    memo.lawyer_notes = payload.lawyer_notes
    memo.lawyer_signature = payload.signature_url
    memo.signed_contract_path = payload.signed_pdf_url
    memo.lawyer_verified_at = now

    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == lawyer.id).with_for_update())
    payout = memo.lawyer_payout_bdt
    profile.wallet_balance += payout
    profile.total_earned += payout
    profile.completed_reviews_count += 1
    profile.active_task_count = max(0, profile.active_task_count - 1)
    profile.availability = LawyerAvailability.AVAILABLE
    profile.last_active_at = now

    db.add(CreditTransaction(
        user_id=profile.user_id, amount=0, balance_after=0,
        tx_type=CreditTxType.LAWYER_REVIEW_FEE,
        description=f"Earned ৳{payout} for verifying Memo #{memo.id}", reference_id=str(memo.id),
    ))

    await db.commit()

    # ✅ Queue Client Email
    contract = await db.scalar(select(Contract).where(Contract.id == memo.contract_id))
    if contract:
        client = await db.scalar(select(User).where(User.id == contract.user_id))
        lawyer_user = await db.scalar(select(User).where(User.id == lawyer.user_id))
        if client and lawyer_user:
            background_tasks.add_task(
                send_client_verification_complete_email,
                client.email, lawyer_user.full_name, contract.title
            )

    return {"status": "success", "memo_id": memo.id, "earned_bdt": float(payout), "new_wallet_balance": float(profile.wallet_balance)}

@router.post("/active/{memo_id}/chat-session")
async def start_contract_review_chat(memo_id: int, lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    memo = await db.scalar(select(ContractMemo).where(ContractMemo.id == memo_id, ContractMemo.lawyer_id == lawyer.id, ContractMemo.hitl_status.in_([HITLStatus.ASSIGNED, HITLStatus.IN_REVIEW])))
    if not memo:
        raise HTTPException(404, "Task not found or not assigned to you.")

    existing_count = (await db.execute(select(func.count(ChatSession.id)).where(
        ChatSession.user_id == lawyer.user_id, ChatSession.session_mode == ChatMode.CONTRACT_REVIEW.value,
        ChatSession.scoped_contract_id == memo.contract_id
    ))).scalar_one() or 0

    if existing_count >= CONTRACT_REVIEW_LIMITS["max_sessions_per_contract"]:
        raise HTTPException(400, f"Max {CONTRACT_REVIEW_LIMITS['max_sessions_per_contract']} chat sessions per contract reached.")

    session = ChatSession(
        user_id=lawyer.user_id, title=f"Contract Review: {memo.contract_id}",
        session_mode=ChatMode.CONTRACT_REVIEW.value, scoped_contract_id=memo.contract_id,
        tier_at_creation="LAWYER", session_limit=CONTRACT_REVIEW_LIMITS["messages_per_session"],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"status": "success", "session_id": session.id, "scoped_contract_id": session.scoped_contract_id, "message": f"Chat session started. Limit: {session.session_limit} messages."}

@router.get("/earnings")
async def get_earnings_dashboard(lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    pending_w = (await db.execute(select(func.coalesce(func.sum(WithdrawalRequest.amount_bdt), 0)).where(
        WithdrawalRequest.lawyer_id == lawyer.id, WithdrawalRequest.status == WithdrawalStatus.PENDING
    ))).scalar_one() or Decimal("0.00")
    return {
        "wallet_balance_bdt": float(lawyer.wallet_balance), "total_earned_bdt": float(lawyer.total_earned),
        "total_withdrawn_bdt": float(lawyer.total_withdrawn), "pending_withdrawals_bdt": float(pending_w),
        "completed_reviews": lawyer.completed_reviews_count, "rating": lawyer.rating_average, "active_tasks": lawyer.active_task_count,
    }

@router.post("/withdrawals/request")
async def request_withdrawal(payload: WithdrawRequest, lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == lawyer.id).with_for_update())
    if payload.amount_bdt < Decimal("500.00"):
        raise HTTPException(400, "Minimum withdrawal is ৳500.")
    if profile.wallet_balance < payload.amount_bdt:
        raise HTTPException(400, f"Insufficient balance. Available: ৳{profile.wallet_balance}")

    existing = await db.scalar(select(WithdrawalRequest).where(WithdrawalRequest.lawyer_id == profile.id, WithdrawalRequest.status == WithdrawalStatus.PENDING))
    if existing:
        raise HTTPException(400, "You already have a pending withdrawal request.")

    req = WithdrawalRequest(
        lawyer_id=profile.id, amount_bdt=payload.amount_bdt, bkash_number=payload.bkash_number,
        account_holder_name=payload.account_holder_name, status=WithdrawalStatus.PENDING,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return {"status": "success", "withdrawal_id": req.id, "amount_bdt": float(req.amount_bdt), "message": "Withdrawal requested. Admin will process within 2 business days."}

@router.get("/withdrawals")
async def list_my_withdrawals(lawyer: LawyerProfile = Depends(get_current_lawyer), db: AsyncSession = Depends(get_db)):
    stmt = select(WithdrawalRequest).where(WithdrawalRequest.lawyer_id == lawyer.id).order_by(desc(WithdrawalRequest.requested_at)).limit(20)
    rows = (await db.execute(stmt)).scalars().all()
    
    # ✅ FIX: লিস্ট কম্প্রিহেনশনের বদলে লুপ ব্যবহার করা হলো যাতে কোনো সিনট্যাক্স এরর না আসে
    result = []
    for w in rows:
        result.append({
            "id": w.id, 
            "amount_bdt": float(w.amount_bdt), 
            "status": w.status.value,
            "bkash_number": w.bkash_number, 
            "requested_at": w.requested_at,
            "processed_at": w.processed_at, 
            "admin_note": w.admin_note, 
            "payout_trx_id": w.payout_trx_id,
        })
    return result