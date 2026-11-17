import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List

# ✅ FIX: BackgroundTasks ইমপোর্ট করা হলো
from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    require_roles,
    refund_credits,
)
from app.core.pricing import (
    SERVICE_PRICING,
    get_lawyer_verification_split,
)
from app.models.user import (
    User, UserRole, CompanyRole, MemberApprovalStatus,
    CreditTransaction, CreditTxType,
    WithdrawalRequest, WithdrawalStatus,
)
from app.models.lawyer import LawyerProfile, LawyerStatus
from app.models.system_config import SystemConfig
from app.models.playbook import Enterprise, EnterpriseStatus, CreditPackage
from app.models.audit import (
    Payment, PaymentMethod, PaymentStatus,
    AuditLog, AuditAction, AuditSeverity,
)
from app.services.payment_fulfillment import (
    fulfill_approved_payment,
    verify_sslcommerz_and_fulfill,
)
from app.services.sslcommerz import SSLCommerzGatewayError, sslcommerz_service

# ✅ FIX: ইমেইল সার্ভিস ইমপোর্ট করা হলো
from app.services.email_service import (
    send_lawyer_approved_email,
    send_lawyer_rejected_email,
    send_enterprise_approved_email,
    send_enterprise_rejected_email,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["System Admin"])


# ──────────────────────────────────────────────────────────
# GUARD
# ──────────────────────────────────────────────────────────

def check_is_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(403, "System Admin privileges required.")
    return current_user


# ──────────────────────────────────────────────────────────
# AUDIT LOG HELPER
# ──────────────────────────────────────────────────────────

async def log_action(
    db: AsyncSession,
    actor: User,
    action: AuditAction,
    *,
    target_entity: Optional[str] = None,
    target_id: Optional[int] = None,
    description: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    severity: AuditSeverity = AuditSeverity.INFO,
) -> None:
    """Write an immutable audit entry. Never commit here — caller commits."""
    db.add(AuditLog(
        actor_id=actor.id,
        actor_role=actor.role.value,
        actor_email=actor.email,
        action=action,
        severity=severity,
        target_entity=target_entity,
        target_id=target_id,
        description=description,
        before_state=before,
        after_state=after,
    ))


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdjustCreditsRequest(BaseModel):
    amount: int = Field(description="Signed: +50 grants, -20 deducts")
    reason: str = Field(min_length=3, max_length=255)


class ApprovePaymentRequest(BaseModel):
    admin_note: Optional[str] = None


class RejectPaymentRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class MarkPaidWithdrawalRequest(BaseModel):
    payout_trx_id: str = Field(min_length=4, max_length=100)
    admin_note: Optional[str] = None


class RejectWithdrawalRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# ──────────────────────────────────────────────────────────
# DASHBOARD STATS
# ──────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard_stats(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Overview stats for System Admin home."""
    async def _count(model, *where):
        stmt = select(func.count()).select_from(model)
        if where:
            stmt = stmt.where(*where)
        return (await db.execute(stmt)).scalar_one() or 0

    return {
        "users": {
            "total": await _count(User, User.is_deleted == False),
            "general": await _count(User, User.role == UserRole.GENERAL),
            "lawyers": await _count(User, User.role == UserRole.LAWYER),
            "enterprise_admins": await _count(User, User.role == UserRole.ENTERPRISE_ADMIN),
            "enterprise_members": await _count(User, User.role == UserRole.ENTERPRISE_MEMBER),
        },
        "pending": {
            "lawyers": await _count(
                LawyerProfile, LawyerProfile.status == LawyerStatus.PENDING_VERIFICATION
            ),
            "enterprises": await _count(
                Enterprise, Enterprise.status == EnterpriseStatus.PENDING_APPROVAL
            ),
            "payments": await _count(
                Payment, Payment.status == PaymentStatus.PENDING_VERIFICATION
            ),
            "withdrawals": await _count(
                WithdrawalRequest, WithdrawalRequest.status == WithdrawalStatus.PENDING
            ),
        },
    }


# ──────────────────────────────────────────────────────────
# LAWYER MANAGEMENT
# ──────────────────────────────────────────────────────────

@router.get("/lawyers/pending")
async def list_pending_lawyers(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(LawyerProfile, User)
        .join(User, LawyerProfile.user_id == User.id)
        .where(LawyerProfile.status == LawyerStatus.PENDING_VERIFICATION)
        .order_by(LawyerProfile.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "lawyer_id": p.id,
            "user_id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "phone": u.phone,
            "bar_council_id": p.bar_council_id,
            "experience_years": p.experience_years,
            "specializations": p.specializations,
            "bar_certificate_url": p.bar_certificate_url,
            "applied_at": p.created_at,
        }
        for p, u in rows
    ]


@router.post("/lawyers/{lawyer_id}/approve")
async def approve_lawyer(
    lawyer_id: int,
    background_tasks: BackgroundTasks,  # ✅ FIX: BackgroundTasks যোগ করা হলো
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approves lawyer AND upgrades the associated user's role."""
    profile = await db.scalar(
        select(LawyerProfile).where(LawyerProfile.id == lawyer_id).with_for_update()
    )
    if not profile:
        raise HTTPException(404, "Lawyer profile not found.")
    if profile.status != LawyerStatus.PENDING_VERIFICATION:
        raise HTTPException(400, f"Cannot approve. Current status: {profile.status.value}")

    user = await db.scalar(select(User).where(User.id == profile.user_id))
    if not user:
        raise HTTPException(404, "Associated user not found.")

    before = {"status": profile.status.value, "user_role": user.role.value}

    profile.status = LawyerStatus.APPROVED
    profile.approved_at = datetime.now(timezone.utc)
    profile.approved_by_id = admin.id
    profile.rejection_reason = None

    # ⚠️ THE critical role upgrade — only here
    user.role = UserRole.LAWYER
    user.is_verified = True

    # ✅ FIX: ইমেইল পাঠানোর জন্য ব্যাকগ্রাউন্ড টাস্কে যোগ করা হলো
    background_tasks.add_task(
        send_lawyer_approved_email,
        user.email,
        user.full_name
    )

    await log_action(
        db, admin, AuditAction.LAWYER_APPROVED,
        target_entity="lawyer_profile", target_id=profile.id,
        description=f"Approved lawyer {user.email}",
        before=before,
        after={"status": profile.status.value, "user_role": user.role.value},
    )
    await db.commit()
    return {"status": "success", "lawyer_id": profile.id}


@router.post("/lawyers/{lawyer_id}/reject")
async def reject_lawyer(
    lawyer_id: int,
    payload: RejectRequest,
    background_tasks: BackgroundTasks,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == lawyer_id))
    if not profile:
        raise HTTPException(404, "Lawyer profile not found.")

    profile.status = LawyerStatus.REJECTED
    profile.rejection_reason = payload.reason

    user = await db.scalar(select(User).where(User.id == profile.user_id))
    if user:
        background_tasks.add_task(
            send_lawyer_rejected_email,
            user.email,
            user.full_name or "Counsel",
            payload.reason
        )

    await log_action(
        db, admin, AuditAction.LAWYER_REJECTED,
        target_entity="lawyer_profile", target_id=profile.id,
        description=f"Rejected: {payload.reason}",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


@router.post("/lawyers/{lawyer_id}/suspend")
async def suspend_lawyer(
    lawyer_id: int,
    payload: RejectRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.id == lawyer_id))
    if not profile:
        raise HTTPException(404, "Not found.")

    profile.status = LawyerStatus.SUSPENDED
    profile.rejection_reason = payload.reason

    await log_action(
        db, admin, AuditAction.LAWYER_REJECTED,
        target_entity="lawyer_profile", target_id=profile.id,
        description=f"Suspended: {payload.reason}",
        severity=AuditSeverity.CRITICAL,
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# ENTERPRISE MANAGEMENT
# ──────────────────────────────────────────────────────────

@router.get("/enterprises/pending")
async def list_pending_enterprises(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Enterprise)
        .where(Enterprise.status == EnterpriseStatus.PENDING_APPROVAL)
        .order_by(Enterprise.created_at.asc())
    )
    enterprises = (await db.execute(stmt)).scalars().all()

    out = []
    for e in enterprises:
        admin_user = None
        if e.pending_admin_user_id:
            u = await db.scalar(select(User).where(User.id == e.pending_admin_user_id))
            if u:
                admin_user = {"id": u.id, "email": u.email, "full_name": u.full_name}
        out.append({
            "enterprise_id": e.id,
            "name": e.name,
            "domain": e.domain,
            "referral_code": e.referral_code,
            "member_limit": e.member_limit,
            "billing_email": e.billing_email,
            "requested_admin": admin_user,
            "created_at": e.created_at,
        })
    return out


@router.post("/enterprises/{enterprise_id}/approve")
async def approve_enterprise(
    enterprise_id: int,
    background_tasks: BackgroundTasks,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Activates enterprise AND upgrades its admin's user role."""
    ent = await db.scalar(
        select(Enterprise).where(Enterprise.id == enterprise_id).with_for_update()
    )
    if not ent:
        raise HTTPException(404, "Enterprise not found.")
    if ent.status != EnterpriseStatus.PENDING_APPROVAL:
        raise HTTPException(400, f"Cannot approve. Status: {ent.status.value}")
    if not ent.pending_admin_user_id:
        raise HTTPException(400, "No pending admin user linked to this enterprise.")

    user = await db.scalar(select(User).where(User.id == ent.pending_admin_user_id))
    if not user:
        raise HTTPException(404, "Pending admin user not found.")

    before = {"status": ent.status.value, "user_role": user.role.value}

    ent.status = EnterpriseStatus.ACTIVE
    ent.approved_at = datetime.now(timezone.utc)
    ent.approved_by_id = admin.id

    # ⚠️ Upgrade user
    user.role = UserRole.ENTERPRISE_ADMIN
    user.enterprise_id = ent.id
    user.company_role = CompanyRole.ADMIN
    user.member_approval_status = MemberApprovalStatus.APPROVED
    user.company_joined_at = datetime.now(timezone.utc)
    user.is_verified = True

    # Send approval email with team referral code
    background_tasks.add_task(
        send_enterprise_approved_email,
        user.email,
        user.full_name or "Admin",
        ent.name,
        ent.referral_code,
    )

    await log_action(
        db, admin, AuditAction.ENTERPRISE_APPROVED,
        target_entity="enterprise", target_id=ent.id,
        description=f"Approved enterprise {ent.name}; admin user={user.email}",
        before=before,
        after={"status": ent.status.value, "user_role": user.role.value},
    )
    await db.commit()
    return {
        "status": "success",
        "enterprise_id": ent.id,
        "referral_code": ent.referral_code,
    }


@router.post("/enterprises/{enterprise_id}/reject")
async def reject_enterprise(
    enterprise_id: int,
    payload: RejectRequest,
    background_tasks: BackgroundTasks,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    ent = await db.scalar(select(Enterprise).where(Enterprise.id == enterprise_id))
    if not ent:
        raise HTTPException(404, "Not found.")

    ent.status = EnterpriseStatus.SUSPENDED
    ent.rejection_reason = payload.reason

    if ent.pending_admin_user_id:
        user = await db.scalar(select(User).where(User.id == ent.pending_admin_user_id))
        if user:
            background_tasks.add_task(
                send_enterprise_rejected_email,
                user.email,
                user.full_name or "Admin",
                ent.name,
                payload.reason,
            )

    await log_action(
        db, admin, AuditAction.ENTERPRISE_REJECTED,
        target_entity="enterprise", target_id=ent.id,
        description=f"Rejected: {payload.reason}",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────
# PAYMENTS & GATEWAY LEDGER (SSLCommerz + Legacy)
# ──────────────────────────────────────────────────────────

@router.get("/payments")
async def list_all_payments(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    limit: int = Query(100, le=300),
    offset: int = Query(0, ge=0),
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Full gateway payment ledger with SSLCommerz telemetry (val_id, bank_tran_id, card_type)."""
    stmt = (
        select(Payment, User, Enterprise, CreditPackage.name)
        .outerjoin(User, Payment.user_id == User.id)
        .outerjoin(Enterprise, Payment.enterprise_id == Enterprise.id)
        .outerjoin(CreditPackage, Payment.package_id == CreditPackage.id)
    )

    if status_filter:
        try:
            st = PaymentStatus(status_filter.upper())
            stmt = stmt.where(Payment.status == st)
        except ValueError:
            raise HTTPException(400, f"Invalid payment status: {status_filter}")

    if search:
        like = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Payment.reference_code).like(like)
            | func.lower(func.coalesce(Payment.val_id, "")).like(like)
            | func.lower(func.coalesce(Payment.bank_tran_id, "")).like(like)
            | func.lower(func.coalesce(Payment.trx_id, "")).like(like)
            | func.lower(func.coalesce(User.email, "")).like(like)
            | func.lower(func.coalesce(User.full_name, "")).like(like)
        )

    stmt = stmt.order_by(desc(Payment.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()

    return [
        {
            "payment_id": p.id,
            "reference_code": p.reference_code,
            "method": p.method.value,
            "purpose": p.purpose.value,
            "status": p.status.value,
            "amount_bdt": float(p.amount_bdt),
            "store_amount_bdt": float(p.store_amount_bdt) if p.store_amount_bdt is not None else None,
            "total_credits": p.total_credits,
            "package_name": pkg_name,
            "payer_type": "enterprise" if p.enterprise_id else "user",
            "user_id": p.user_id,
            "user_name": u.full_name if u else None,
            "user_email": u.email if u else None,
            "enterprise_id": p.enterprise_id,
            "enterprise_name": ent.name if ent else None,
            "sender_number": p.sender_number,
            "trx_id": p.trx_id,
            "gateway_session_key": p.gateway_session_key,
            "val_id": p.val_id,
            "bank_tran_id": p.bank_tran_id,
            "card_type": p.card_type,
            "card_issuer": p.card_issuer,
            "card_brand": p.card_brand,
            "gateway_status": p.gateway_status,
            "admin_note": p.admin_note,
            "created_at": p.created_at,
            "gateway_validated_at": p.gateway_validated_at,
            "reviewed_at": p.reviewed_at,
        }
        for p, u, ent, pkg_name in rows
    ]


@router.post("/payments/{payment_id}/reconcile")
async def reconcile_sslcommerz_payment(
    payment_id: int,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Queries SSLCommerz directly for a pending payment and grants credits if VALID."""
    p = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not p:
        raise HTTPException(404, "Payment not found.")
    if p.status == PaymentStatus.APPROVED:
        return {"status": "APPROVED", "credits_granted": p.total_credits, "message": "Already approved."}
    if not sslcommerz_service.is_configured():
        raise HTTPException(503, "SSLCommerz credentials are not configured on the server.")

    try:
        success, updated, code = await verify_sslcommerz_and_fulfill(
            db,
            tran_id=p.reference_code,
            val_id=p.val_id,
            verify_signature=False,
        )
    except SSLCommerzGatewayError as exc:
        raise HTTPException(502, str(exc))

    if success and updated:
        return {
            "status": "APPROVED",
            "credits_granted": updated.total_credits,
            "message": f"Reconciled via SSLCommerz ({code}).",
        }
    return {
        "status": updated.status.value if updated else p.status.value,
        "credits_granted": 0,
        "code": code,
        "message": f"Payment not validated at SSLCommerz ({code}).",
    }


@router.get("/payments/pending")
async def list_pending_payments(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Payment)
        .where(Payment.status == PaymentStatus.PENDING_VERIFICATION)
        .order_by(Payment.trx_submitted_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "payment_id": p.id,
            "reference_code": p.reference_code,
            "amount_bdt": float(p.amount_bdt),
            "total_credits": p.total_credits,
            "payer_type": "enterprise" if p.enterprise_id else "user",
            "user_id": p.user_id,
            "enterprise_id": p.enterprise_id,
            "trx_id": p.trx_id,
            "sender_number": p.sender_number,
            "trx_submitted_at": p.trx_submitted_at,
        }
        for p in rows
    ]


@router.post("/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: int,
    payload: ApprovePaymentRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Grants credits to user or enterprise wallet (delegates to shared atomic service)."""
    result = await fulfill_approved_payment(
        db,
        payment_id,
        actor=admin,
        admin_note=payload.admin_note,
        allowed_statuses=(PaymentStatus.PENDING_VERIFICATION,),
    )
    await db.commit()
    return {"status": "success", "credits_granted": result["credits_granted"]}


@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int,
    payload: RejectPaymentRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    p = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not p:
        raise HTTPException(404, "Not found.")
    if p.status not in (PaymentStatus.PENDING, PaymentStatus.PENDING_VERIFICATION):
        raise HTTPException(400, f"Cannot reject. Status: {p.status.value}")

    p.status = PaymentStatus.REJECTED
    p.reviewed_at = datetime.now(timezone.utc)
    p.reviewed_by_id = admin.id
    p.admin_note = payload.reason

    await log_action(
        db, admin, AuditAction.PAYMENT_REJECTED,
        target_entity="payment", target_id=p.id,
        description=f"Rejected {p.reference_code}: {payload.reason}",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# WITHDRAWAL PROCESSING (Lawyer Payouts)
# ──────────────────────────────────────────────────────────

@router.get("/withdrawals/pending")
async def list_pending_withdrawals(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(WithdrawalRequest, LawyerProfile, User)
        .join(LawyerProfile, WithdrawalRequest.lawyer_id == LawyerProfile.id)
        .join(User, LawyerProfile.user_id == User.id)
        .where(
            WithdrawalRequest.status.in_(
                [WithdrawalStatus.PENDING, WithdrawalStatus.APPROVED]
            )
        )
        .order_by(WithdrawalRequest.requested_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "withdrawal_id": w.id,
            "lawyer_id": w.lawyer_id,
            "lawyer_name": u.full_name,
            "lawyer_email": u.email,
            "amount_bdt": float(w.amount_bdt),
            "payout_method": w.payout_method or "BKASH",
            "account_number": w.account_number or w.bkash_number,
            "bkash_number": w.bkash_number,
            "bank_name": w.bank_name,
            "branch_name": w.branch_name,
            "account_holder_name": w.account_holder_name,
            "status": w.status.value,
            "requested_at": w.requested_at,
        }
        for w, _, u in rows
    ]


@router.post("/withdrawals/{withdrawal_id}/approve")
async def approve_withdrawal(
    withdrawal_id: int,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Marks withdrawal as APPROVED. Admin then sends bKash manually."""
    w = await db.scalar(
        select(WithdrawalRequest)
        .where(WithdrawalRequest.id == withdrawal_id)
        .with_for_update()
    )
    if not w:
        raise HTTPException(404, "Not found.")
    if w.status != WithdrawalStatus.PENDING:
        raise HTTPException(400, f"Cannot approve. Status: {w.status.value}")

    # Check lawyer still has the balance
    profile = await db.scalar(
        select(LawyerProfile).where(LawyerProfile.id == w.lawyer_id).with_for_update()
    )
    if not profile:
        raise HTTPException(404, "Lawyer not found.")
    if profile.wallet_balance < w.amount_bdt:
        raise HTTPException(400, "Lawyer wallet balance insufficient.")

    # Deduct from wallet immediately (hold)
    profile.wallet_balance -= w.amount_bdt

    w.status = WithdrawalStatus.APPROVED
    w.processed_at = datetime.now(timezone.utc)
    w.processed_by_id = admin.id

    await log_action(
        db, admin, AuditAction.WITHDRAWAL_APPROVED,
        target_entity="withdrawal", target_id=w.id,
        description=f"Approved ৳{w.amount_bdt} for lawyer {profile.id}",
    )
    await db.commit()
    return {"status": "success"}


@router.post("/withdrawals/{withdrawal_id}/mark-paid")
async def mark_withdrawal_paid(
    withdrawal_id: int,
    payload: MarkPaidWithdrawalRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin confirms bKash sent. Records TrxID for audit."""
    w = await db.scalar(
        select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id)
    )
    if not w:
        raise HTTPException(404, "Not found.")
    if w.status != WithdrawalStatus.APPROVED:
        raise HTTPException(400, f"Must be APPROVED first. Current: {w.status.value}")

    w.status = WithdrawalStatus.PAID
    w.payout_trx_id = payload.payout_trx_id
    w.admin_note = payload.admin_note
    w.processed_at = datetime.now(timezone.utc)
    w.processed_by_id = admin.id

    # Track lifetime withdrawn
    profile = await db.scalar(
        select(LawyerProfile).where(LawyerProfile.id == w.lawyer_id)
    )
    if profile:
        profile.total_withdrawn += w.amount_bdt

    await log_action(
        db, admin, AuditAction.WITHDRAWAL_PAID,
        target_entity="withdrawal", target_id=w.id,
        description=f"Paid ৳{w.amount_bdt} via {w.payout_method or 'BKASH'} ({payload.payout_trx_id})",
    )
    await db.commit()
    return {"status": "success", "payout_trx_id": payload.payout_trx_id}


@router.post("/withdrawals/{withdrawal_id}/reject")
async def reject_withdrawal(
    withdrawal_id: int,
    payload: RejectWithdrawalRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    w = await db.scalar(
        select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id)
    )
    if not w:
        raise HTTPException(404, "Not found.")
    if w.status not in (WithdrawalStatus.PENDING, WithdrawalStatus.APPROVED):
        raise HTTPException(400, f"Cannot reject. Status: {w.status.value}")

    # If already approved, refund the held amount back to lawyer wallet
    if w.status == WithdrawalStatus.APPROVED:
        profile = await db.scalar(
            select(LawyerProfile).where(LawyerProfile.id == w.lawyer_id)
        )
        if profile:
            profile.wallet_balance += w.amount_bdt

    w.status = WithdrawalStatus.REJECTED
    w.admin_note = payload.reason
    w.processed_at = datetime.now(timezone.utc)
    w.processed_by_id = admin.id

    await log_action(
        db, admin, AuditAction.WITHDRAWAL_REJECTED,
        target_entity="withdrawal", target_id=w.id,
        description=f"Rejected: {payload.reason}",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# MANUAL CREDIT ADJUSTMENTS
# ──────────────────────────────────────────────────────────

@router.post("/users/{user_id}/adjust-credits")
async def adjust_user_credits(
    user_id: int,
    payload: AdjustCreditsRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if not user:
        raise HTTPException(404, "User not found.")

    before_balance = user.credit_balance
    user.credit_balance += payload.amount
    if user.credit_balance < 0:
        raise HTTPException(400, "Adjustment would make balance negative.")

    db.add(CreditTransaction(
        user_id=user.id,
        amount=payload.amount,
        balance_after=user.credit_balance,
        tx_type=CreditTxType.ADMIN_ADJUST,
        description=f"Admin adjust: {payload.reason}",
        performed_by_id=admin.id,
    ))

    await log_action(
        db, admin, AuditAction.CREDITS_ADJUSTED,
        target_entity="user", target_id=user.id,
        description=f"{payload.amount:+d}: {payload.reason}",
        before={"credit_balance": before_balance},
        after={"credit_balance": user.credit_balance},
    )
    await db.commit()
    return {"status": "success", "new_balance": user.credit_balance}


@router.post("/enterprises/{enterprise_id}/adjust-credits")
async def adjust_enterprise_credits(
    enterprise_id: int,
    payload: AdjustCreditsRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    ent = await db.scalar(
        select(Enterprise).where(Enterprise.id == enterprise_id).with_for_update()
    )
    if not ent:
        raise HTTPException(404, "Enterprise not found.")

    before = ent.credit_balance
    ent.credit_balance += payload.amount
    if ent.credit_balance < 0:
        raise HTTPException(400, "Adjustment would make balance negative.")

    db.add(CreditTransaction(
        enterprise_id=ent.id,
        amount=payload.amount,
        balance_after=ent.credit_balance,
        tx_type=CreditTxType.ADMIN_ADJUST,
        description=f"Admin adjust: {payload.reason}",
        performed_by_id=admin.id,
    ))

    await log_action(
        db, admin, AuditAction.CREDITS_ADJUSTED,
        target_entity="enterprise", target_id=ent.id,
        description=f"{payload.amount:+d}: {payload.reason}",
        before={"credit_balance": before},
        after={"credit_balance": ent.credit_balance},
    )
    await db.commit()
    return {"status": "success", "new_balance": ent.credit_balance}


# ──────────────────────────────────────────────────────────
# USER MANAGEMENT
# ──────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    role: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).where(User.is_deleted == False)
    if role:
        stmt = stmt.where(User.role == UserRole(role))
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(
            (func.lower(User.email).like(like)) |
            (func.lower(User.full_name).like(like))
        )
    stmt = stmt.order_by(desc(User.created_at)).limit(limit).offset(offset)
    users = (await db.execute(stmt)).scalars().all()
    
    # ✅ FIX: profile_photo_url এবং phone যোগ করা হলো
    return [
        {
            "id": u.id, 
            "email": u.email, 
            "full_name": u.full_name,
            "role": u.role.value, 
            "credit_balance": u.credit_balance,
            "is_active": u.is_active, 
            "is_verified": u.is_verified,
            "enterprise_id": u.enterprise_id,
            "created_at": u.created_at,
            "profile_photo_url": u.profile_photo_url,
            "phone": u.phone,
        }
        for u in users
    ]


# ✅ NEW: Get specific user details for Admin Profile Modal
@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: int,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.id == user_id, User.is_deleted == False))
    if not user:
        raise HTTPException(404, "User not found")

    response = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "role": user.role.value,
        "credit_balance": user.credit_balance,
        "member_credit_quota": user.member_credit_quota or 0,
        "member_credit_used": user.member_credit_used or 0,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "created_at": user.created_at,
        "profile_photo_url": user.profile_photo_url,
        "lawyer_profile": None,
        "enterprise_id": user.enterprise_id,
        "enterprise_details": None,
    }

    if user.enterprise_id:
        ent = await db.scalar(select(Enterprise).where(Enterprise.id == user.enterprise_id))
        if ent:
            response["enterprise_details"] = {
                "id": ent.id,
                "name": ent.name,
                "pool_balance": ent.credit_balance,
                "member_limit": ent.member_limit,
            }

    if user.role == UserRole.LAWYER:
        profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.user_id == user.id))
        if profile:
            response["lawyer_profile"] = {
                "bar_council_id": profile.bar_council_id,
                "experience_years": profile.experience_years,
                "specializations": profile.specializations,
                "status": profile.status.value,
                "wallet_balance": float(profile.wallet_balance)
            }
    
    return response


@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: int,
    payload: RejectRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(404, "Not found.")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot suspend yourself.")

    user.is_active = False
    # ✅ SEC-AUTH-03: instantly revoke all sessions of the suspended user
    user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
    await log_action(
        db, admin, AuditAction.MEMBER_SUSPENDED,
        target_entity="user", target_id=user.id,
        description=f"Suspended: {payload.reason}",
        severity=AuditSeverity.CRITICAL,
    )
    await db.commit()
    return {"status": "success"}


@router.post("/users/{user_id}/activate")
async def activate_user(
    user_id: int,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(404, "Not found.")
    user.is_active = True
    await log_action(
        db, admin, AuditAction.CREDITS_ADJUSTED,
        target_entity="user", target_id=user.id,
        description="Reactivated account",
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# AUDIT LOGS
# ──────────────────────────────────────────────────────────

@router.get("/audit-logs")
async def list_audit_logs(
    severity: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target_entity: Optional[str] = Query(None),
    target_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog)
    if severity:
        stmt = stmt.where(AuditLog.severity == AuditSeverity(severity))
    if action:
        stmt = stmt.where(AuditLog.action == AuditAction(action))
    if target_entity:
        stmt = stmt.where(AuditLog.target_entity == target_entity)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)

    stmt = stmt.order_by(desc(AuditLog.timestamp)).limit(limit).offset(offset)
    logs = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": l.id,
            "actor_email": l.actor_email,
            "actor_role": l.actor_role,
            "action": l.action.value,
            "severity": l.severity.value,
            "target_entity": l.target_entity,
            "target_id": l.target_id,
            "description": l.description,
            "before": l.before_state,
            "after": l.after_state,
            "timestamp": l.timestamp,
        }
        for l in logs
    ]


# ──────────────────────────────────────────────────────────
# FINANCIALS (ADMIN ONLY)
# ──────────────────────────────────────────────────────────

@router.get("/financials")
async def financials_dashboard(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    ADMIN ONLY. Real ৳ valuation across the platform.
    NEVER expose this endpoint to non-admin users.
    """
    split = get_lawyer_verification_split()

    # Total approved revenue
    total_revenue_bdt = (await db.execute(
        select(func.coalesce(func.sum(Payment.amount_bdt), 0)).where(
            Payment.status == PaymentStatus.APPROVED,
        )
    )).scalar_one() or Decimal("0.00")

    # Total credits sold
    total_credits_sold = (await db.execute(
        select(func.coalesce(
            func.sum(Payment.credits_to_grant + Payment.bonus_credits), 0
        )).where(
            Payment.status == PaymentStatus.APPROVED,
        )
    )).scalar_one() or 0

    # Pending payouts (approved but not yet paid withdrawals)
    pending_payouts_bdt = (await db.execute(
        select(func.coalesce(func.sum(WithdrawalRequest.amount_bdt), 0)).where(
            WithdrawalRequest.status.in_([
                WithdrawalStatus.PENDING,
                WithdrawalStatus.APPROVED,
            ]),
        )
    )).scalar_one() or Decimal("0.00")

    # Lifetime lawyer payouts already sent
    paid_payouts_bdt = (await db.execute(
        select(func.coalesce(func.sum(WithdrawalRequest.amount_bdt), 0)).where(
            WithdrawalRequest.status == WithdrawalStatus.PAID,
        )
    )).scalar_one() or Decimal("0.00")

    return {
        "internal_service_costs_bdt": {
            key: float(cfg["internal_bdt_value"])
            for key, cfg in SERVICE_PRICING.items()
        },
        "lawyer_verification_split_bdt": {
            "lawyer_payout": float(split["lawyer_payout_bdt"]),
            "platform_fee": float(split["platform_fee_bdt"]),
        },
        "aggregates": {
            "total_revenue_bdt": float(total_revenue_bdt),
            "total_credits_sold": int(total_credits_sold),
            "pending_payouts_bdt": float(pending_payouts_bdt),
            "paid_payouts_bdt": float(paid_payouts_bdt),
            "net_platform_position_bdt": float(
                total_revenue_bdt - paid_payouts_bdt
            ),
        },
    }


# ──────────────────────────────────────────────────────────
# SYSTEM CONFIG (INSTANT JUDGE MODE)
# ──────────────────────────────────────────────────────────

class UpdateSystemConfigRequest(BaseModel):
    instant_judge_mode: Optional[bool] = None
    fixed_judge_lawyer_id: Optional[int] = None


@router.get("/system-config")
async def get_admin_system_config(
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns platform settings including Instant Judge Mode status,
    the currently fixed judge lawyer, and all approved lawyers to choose from.
    """
    sys_cfg = await db.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    if not sys_cfg:
        sys_cfg = SystemConfig(id=1, instant_judge_mode=False)
        db.add(sys_cfg)
        await db.commit()
        await db.refresh(sys_cfg)

    # Fetch all approved lawyers
    stmt = (
        select(LawyerProfile, User)
        .join(User, LawyerProfile.user_id == User.id)
        .where(LawyerProfile.status == LawyerStatus.APPROVED)
        .order_by(User.full_name.asc())
    )
    lawyers_rows = (await db.execute(stmt)).all()
    approved_lawyers = [
        {
            "lawyer_id": lp.id,
            "user_id": u.id,
            "name": u.full_name,
            "email": u.email,
            "bar_council_id": lp.bar_council_id,
            "experience_years": lp.experience_years,
        }
        for lp, u in lawyers_rows
    ]

    fixed_name = None
    fixed_email = None
    if sys_cfg.fixed_judge_lawyer_id:
        fixed_match = next((l for l in approved_lawyers if l["lawyer_id"] == sys_cfg.fixed_judge_lawyer_id), None)
        if fixed_match:
            fixed_name = fixed_match["name"]
            fixed_email = fixed_match["email"]

    return {
        "instant_judge_mode": bool(sys_cfg.instant_judge_mode),
        "fixed_judge_lawyer_id": sys_cfg.fixed_judge_lawyer_id,
        "fixed_judge_lawyer_name": fixed_name,
        "fixed_judge_lawyer_email": fixed_email,
        "updated_at": sys_cfg.updated_at.isoformat() if sys_cfg.updated_at else None,
        "updated_by": sys_cfg.updated_by,
        "approved_lawyers": approved_lawyers,
    }


@router.patch("/system-config")
async def update_admin_system_config(
    payload: UpdateSystemConfigRequest,
    admin: User = Depends(check_is_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Toggles Instant Judge Mode (ON/OFF) and sets the target fixed lawyer.
    """
    sys_cfg = await db.scalar(select(SystemConfig).where(SystemConfig.id == 1))
    if not sys_cfg:
        sys_cfg = SystemConfig(id=1)
        db.add(sys_cfg)

    if payload.instant_judge_mode is not None:
        sys_cfg.instant_judge_mode = payload.instant_judge_mode

    if payload.fixed_judge_lawyer_id is not None:
        if payload.fixed_judge_lawyer_id > 0:
            lp = await db.scalar(
                select(LawyerProfile).where(
                    LawyerProfile.id == payload.fixed_judge_lawyer_id,
                    LawyerProfile.status == LawyerStatus.APPROVED,
                )
            )
            if not lp:
                raise HTTPException(400, "Selected lawyer does not exist or is not approved.")
            sys_cfg.fixed_judge_lawyer_id = payload.fixed_judge_lawyer_id
        else:
            sys_cfg.fixed_judge_lawyer_id = None

    sys_cfg.updated_by = admin.email

    audit = AuditLog(
        actor_id=admin.id,
        action=AuditAction.UPDATE_ENTERPRISE_SETTINGS,
        severity=AuditSeverity.INFO,
        target_entity="system_config",
        target_id=1,
        description=f"Updated Instant Judge Mode: {sys_cfg.instant_judge_mode}, Fixed Lawyer: {sys_cfg.fixed_judge_lawyer_id}",
    )
    db.add(audit)
    await db.commit()
    await db.refresh(sys_cfg)

    return {
        "status": "success",
        "message": f"Instant Judge Mode is now {'ON' if sys_cfg.instant_judge_mode else 'OFF'}",
        "instant_judge_mode": sys_cfg.instant_judge_mode,
        "fixed_judge_lawyer_id": sys_cfg.fixed_judge_lawyer_id,
    }
