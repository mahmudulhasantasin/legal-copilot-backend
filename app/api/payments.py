import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
# ✅ Pydantic V2 ইমপোর্ট
from pydantic import BaseModel, Field, field_validator, ConfigDict
from sqlalchemy import select, desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.pricing import (
    ALLOW_CUSTOM_TOPUP,
    CUSTOM_TOPUP_MIN_BDT,
    CUSTOM_TOPUP_MAX_BDT,
    calculate_custom_topup_credits,
)
from app.models.user import User, UserRole
from app.models.playbook import Enterprise, EnterpriseStatus, CreditPackage
from app.models.audit import (
    Payment, PaymentMethod, PaymentPurpose, PaymentStatus,
    AuditLog, AuditAction, AuditSeverity,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["Payments"])


# ──────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────

PAYMENT_EXPIRY_HOURS = 24


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def _generate_reference_code() -> str:
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"PAY-{suffix}"


async def _log_payment_action(
    db: AsyncSession,
    actor: User,
    action: AuditAction,
    *,
    payment_id: int,
    description: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    severity: AuditSeverity = AuditSeverity.INFO,
) -> None:
    db.add(AuditLog(
        actor_id=actor.id,
        actor_role=actor.role.value,
        actor_email=actor.email,
        action=action,
        severity=severity,
        target_entity="payment",
        target_id=payment_id,
        description=description,
        before_state=before,
        after_state=after,
    ))


def _bkash_config() -> dict:
    return {
        "bkash_number": settings.BKASH_MERCHANT_NUMBER,
        "bkash_type": "Personal",
        "instructions_en": (
            "1. Open bKash app.\n"
            "2. Choose 'Send Money'.\n"
            f"3. Send to {settings.BKASH_MERCHANT_NUMBER}.\n"
            "4. Enter the exact amount shown.\n"
            "5. Add the reference code in the 'Reference' field.\n"
            "6. Copy the TrxID from the bKash confirmation SMS."
        ),
        "instructions_bn": (
            "১. বিকাশ অ্যাপ খুলুন।\n"
            "২. 'Send Money' নির্বাচন করুন।\n"
            f"৩. {settings.BKASH_MERCHANT_NUMBER} নম্বরে পাঠান।\n"
            "৪. দেখানো পরিমাণ হুবহু লিখুন।\n"
            "৫. 'Reference' ঘরে কোডটি লিখুন।\n"
            "৬. বিকাশের কনফার্মেশন SMS থেকে TrxID কপি করুন।"
        ),
    }


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class InitiatePaymentRequest(BaseModel):
    """
    Package-based payment ONLY. Custom amounts are disabled by default
    (see ALLOW_CUSTOM_TOPUP in app/core/pricing.py).
    """
    package_id: Optional[int] = Field(
        default=None,
        description="ID of the credit package to purchase.",
    )
    custom_amount_bdt: Optional[Decimal] = Field(
        default=None,
        description="[DISABLED] Custom top-up amount in BDT.",
    )

    # ✅ Pydantic V2 Validator
    @field_validator("custom_amount_bdt")
    @classmethod
    def validate_amount(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Amount must be positive.")
        return v


class SubmitTrxRequest(BaseModel):
    trx_id: str = Field(min_length=4, max_length=100)
    sender_number: str = Field(min_length=11, max_length=20)

    # ✅ Pydantic V2 Validator
    @field_validator("trx_id")
    @classmethod
    def clean_trx(cls, v):
        return v.strip().upper()

    # ✅ Pydantic V2 Validator
    @field_validator("sender_number")
    @classmethod
    def clean_number(cls, v):
        return v.strip().replace(" ", "").replace("-", "")


class PaymentView(BaseModel):
    id: int
    reference_code: str
    amount_bdt: float
    credits_to_grant: int
    bonus_credits: int
    total_credits: int
    purpose: str
    status: str
    method: str
    trx_id: Optional[str]
    sender_number: Optional[str]
    admin_note: Optional[str]
    created_at: datetime
    trx_submitted_at: Optional[datetime]
    reviewed_at: Optional[datetime]
    expires_at: Optional[datetime]

    # ✅ Pydantic V2 Config
    model_config = ConfigDict(from_attributes=True)


class PackageView(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price_bdt: float
    total_credits: int
    badge_text: Optional[str]
    display_order: int

    # ✅ Pydantic V2 Config
    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────
# PACKAGES
# ──────────────────────────────────────────────────────────

@router.get("/packages", response_model=List[PackageView])
async def list_available_packages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns credit packages visible to the current user.
    Enterprise users see ENTERPRISE + ALL; others see GENERAL + ALL.
    """
    if current_user.role == UserRole.ENTERPRISE_ADMIN:
        audience = ["ALL", "ENTERPRISE"]
    else:
        audience = ["ALL", "GENERAL"]

    stmt = (
        select(CreditPackage)
        .where(
            CreditPackage.is_active == True,
            CreditPackage.applies_to.in_(audience),
        )
        .order_by(CreditPackage.display_order.asc(), CreditPackage.price_bdt.asc())
    )
    packages = (await db.execute(stmt)).scalars().all()

    return [
        PackageView(
            id=p.id,
            name=p.name,
            description=p.description,
            price_bdt=float(p.price_bdt),
            total_credits=p.total_credits,
            badge_text=p.badge_text,
            display_order=p.display_order,
        )
        for p in packages
    ]


# ──────────────────────────────────────────────────────────
# INITIATE PAYMENT
# ──────────────────────────────────────────────────────────

@router.post("/initiate", status_code=status.HTTP_201_CREATED)
async def initiate_payment(
    payload: InitiatePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a PENDING payment. Users must select a package unless
    ALLOW_CUSTOM_TOPUP is enabled in app/core/pricing.py.
    """
    # ── Validate input ──
    if not payload.package_id and not ALLOW_CUSTOM_TOPUP:
        raise HTTPException(400, "Custom top-up is disabled. Please select a package.")

    if payload.package_id and payload.custom_amount_bdt is not None:
        raise HTTPException(400, "Provide package_id OR custom_amount_bdt, not both.")

    if not payload.package_id and payload.custom_amount_bdt is None:
        raise HTTPException(400, "Select a package or provide a custom amount.")

    # ── Resolve amount + credits ──
    if payload.package_id:
        pkg = await db.scalar(
            select(CreditPackage).where(
                CreditPackage.id == payload.package_id,
                CreditPackage.is_active == True,
            )
        )
        if not pkg:
            raise HTTPException(404, "Package not found or inactive.")

        # Audience check
        is_enterprise = current_user.role == UserRole.ENTERPRISE_ADMIN
        if pkg.applies_to == "ENTERPRISE" and not is_enterprise:
            raise HTTPException(400, "This package is not available for your account.")
        if pkg.applies_to == "GENERAL" and is_enterprise:
            raise HTTPException(400, "This package is not available for enterprises.")

        amount_bdt = Decimal(pkg.price_bdt)
        credits_to_grant = pkg.base_credits
        bonus_credits = pkg.bonus_credits
    else:
        # ── Custom top-up (only reached if ALLOW_CUSTOM_TOPUP=True) ──
        if payload.custom_amount_bdt < CUSTOM_TOPUP_MIN_BDT:
            raise HTTPException(400, f"Minimum top-up is ৳{CUSTOM_TOPUP_MIN_BDT}.")
        if payload.custom_amount_bdt > CUSTOM_TOPUP_MAX_BDT:
            raise HTTPException(400, f"Maximum top-up is ৳{CUSTOM_TOPUP_MAX_BDT}.")

        try:
            credits_to_grant = calculate_custom_topup_credits(payload.custom_amount_bdt)
        except ValueError as e:
            raise HTTPException(400, str(e))

        amount_bdt = payload.custom_amount_bdt
        bonus_credits = 0

    # ── Decide destination ──
    if current_user.role == UserRole.ENTERPRISE_ADMIN:
        if not current_user.enterprise_id:
            raise HTTPException(400, "Enterprise not linked to your account.")
        ent = await db.scalar(
            select(Enterprise).where(Enterprise.id == current_user.enterprise_id)
        )
        if not ent or ent.status != EnterpriseStatus.ACTIVE:
            raise HTTPException(403, "Enterprise is not active.")
        purpose = PaymentPurpose.ENTERPRISE_TOPUP
        enterprise_id = ent.id
        user_id = None
    else:
        purpose = PaymentPurpose.CREDIT_TOPUP
        enterprise_id = None
        user_id = current_user.id

    # ── Generate unique reference code ──
    reference_code = None
    for _ in range(5):
        code = _generate_reference_code()
        if not await db.scalar(select(Payment).where(Payment.reference_code == code)):
            reference_code = code
            break
    if not reference_code:
        raise HTTPException(500, "Could not generate reference code. Try again.")

    # ── Create Payment ──
    expires_at = datetime.now(timezone.utc) + timedelta(hours=PAYMENT_EXPIRY_HOURS)

    payment = Payment(
        user_id=user_id,
        enterprise_id=enterprise_id,
        amount_bdt=amount_bdt,
        credits_to_grant=credits_to_grant,
        bonus_credits=bonus_credits,
        package_id=payload.package_id,
        method=PaymentMethod.BKASH,
        purpose=purpose,
        reference_code=reference_code,
        status=PaymentStatus.PENDING,
        expires_at=expires_at,
    )
    db.add(payment)
    await db.flush()

    await _log_payment_action(
        db, current_user, AuditAction.PAYMENT_INITIATED,
        payment_id=payment.id,
        description=f"Initiated {reference_code} for ৳{amount_bdt} → {credits_to_grant + bonus_credits} credits",
    )
    await db.commit()
    await db.refresh(payment)

    return {
        "payment_id": payment.id,
        "reference_code": payment.reference_code,
        "amount_bdt": float(payment.amount_bdt),
        "total_credits": payment.total_credits,
        "expires_at": payment.expires_at,
        "bkash": _bkash_config(),
        "next_step": "Send bKash, then POST /api/payments/{payment_id}/submit-trx with TrxID.",
    }


# ──────────────────────────────────────────────────────────
# SUBMIT TRX ID
# ──────────────────────────────────────────────────────────

@router.post("/{payment_id}/submit-trx")
async def submit_trx(
    payment_id: int,
    payload: SubmitTrxRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payment = await db.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if not payment:
        raise HTTPException(404, "Payment not found.")

    if payment.user_id and payment.user_id != current_user.id:
        raise HTTPException(403, "Not your payment.")
    if payment.enterprise_id and payment.enterprise_id != current_user.enterprise_id:
        raise HTTPException(403, "Not your enterprise payment.")

    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(400, f"Cannot submit TrxID. Status: {payment.status.value}")

    now = datetime.now(timezone.utc)
    if payment.expires_at:
        exp = payment.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            payment.status = PaymentStatus.EXPIRED
            await db.commit()
            raise HTTPException(400, "Payment expired. Please initiate a new one.")

    existing = await db.scalar(
        select(Payment).where(
            Payment.trx_id == payload.trx_id,
            Payment.id != payment.id,
        )
    )
    if existing:
        raise HTTPException(400, "This TrxID has already been submitted.")

    before = {"status": payment.status.value, "trx_id": payment.trx_id}
    payment.trx_id = payload.trx_id
    payment.sender_number = payload.sender_number
    payment.trx_submitted_at = now
    payment.status = PaymentStatus.PENDING_VERIFICATION

    await _log_payment_action(
        db, current_user, AuditAction.PAYMENT_TRX_SUBMITTED,
        payment_id=payment.id,
        description=f"TrxID {payload.trx_id} submitted for {payment.reference_code}",
        before=before,
        after={"status": payment.status.value, "trx_id": payment.trx_id},
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "This TrxID has already been submitted.")

    return {
        "status": "success",
        "payment_id": payment.id,
        "message": "TrxID submitted. Admin will verify within 24 hours.",
        "current_status": payment.status.value,
    }


# ──────────────────────────────────────────────────────────
# CANCEL
# ──────────────────────────────────────────────────────────

@router.post("/{payment_id}/cancel")
async def cancel_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payment = await db.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if not payment:
        raise HTTPException(404, "Payment not found.")

    if payment.user_id and payment.user_id != current_user.id:
        raise HTTPException(403, "Not your payment.")
    if payment.enterprise_id and payment.enterprise_id != current_user.enterprise_id:
        raise HTTPException(403, "Not your enterprise payment.")

    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(400, "Only PENDING payments can be cancelled.")

    payment.status = PaymentStatus.EXPIRED
    await _log_payment_action(
        db, current_user, AuditAction.PAYMENT_REJECTED,
        payment_id=payment.id,
        description=f"User cancelled {payment.reference_code}",
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# LIST / GET / SUMMARY
# ──────────────────────────────────────────────────────────

@router.get("/my", response_model=List[PaymentView])
async def list_my_payments(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Payment)

    if current_user.role == UserRole.ENTERPRISE_ADMIN and current_user.enterprise_id:
        stmt = stmt.where(
            (Payment.user_id == current_user.id) |
            (Payment.enterprise_id == current_user.enterprise_id)
        )
    else:
        stmt = stmt.where(Payment.user_id == current_user.id)

    if status_filter:
        try:
            st = PaymentStatus(status_filter)
            stmt = stmt.where(Payment.status == st)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status_filter}")

    stmt = stmt.order_by(desc(Payment.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        PaymentView(
            id=p.id, reference_code=p.reference_code,
            amount_bdt=float(p.amount_bdt),
            credits_to_grant=p.credits_to_grant,
            bonus_credits=p.bonus_credits,
            total_credits=p.total_credits,
            purpose=p.purpose.value, status=p.status.value,
            method=p.method.value,
            trx_id=p.trx_id, sender_number=p.sender_number,
            admin_note=p.admin_note,
            created_at=p.created_at,
            trx_submitted_at=p.trx_submitted_at,
            reviewed_at=p.reviewed_at, expires_at=p.expires_at,
        )
        for p in rows
    ]


@router.get("/{payment_id}", response_model=PaymentView)
async def get_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise HTTPException(404, "Payment not found.")

    is_owner = payment.user_id == current_user.id
    is_enterprise_owner = (
        payment.enterprise_id
        and payment.enterprise_id == current_user.enterprise_id
        and current_user.role == UserRole.ENTERPRISE_ADMIN
    )
    if not (is_owner or is_enterprise_owner):
        raise HTTPException(403, "Access denied.")

    return PaymentView(
        id=payment.id, reference_code=payment.reference_code,
        amount_bdt=float(payment.amount_bdt),
        credits_to_grant=payment.credits_to_grant,
        bonus_credits=payment.bonus_credits,
        total_credits=payment.total_credits,
        purpose=payment.purpose.value, status=payment.status.value,
        method=payment.method.value,
        trx_id=payment.trx_id, sender_number=payment.sender_number,
        admin_note=payment.admin_note,
        created_at=payment.created_at,
        trx_submitted_at=payment.trx_submitted_at,
        reviewed_at=payment.reviewed_at, expires_at=payment.expires_at,
    )


@router.get("/summary/me")
async def my_payment_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == UserRole.ENTERPRISE_ADMIN and current_user.enterprise_id:
        base_filter = (Payment.enterprise_id == current_user.enterprise_id)
    else:
        base_filter = (Payment.user_id == current_user.id)

    async def _count(status_value):
        return (await db.execute(
            select(func.count(Payment.id)).where(
                base_filter, Payment.status == status_value,
            )
        )).scalar_one() or 0

    async def _sum_approved():
        return (await db.execute(
            select(func.coalesce(func.sum(Payment.amount_bdt), 0)).where(
                base_filter, Payment.status == PaymentStatus.APPROVED,
            )
        )).scalar_one() or Decimal("0.00")

    return {
        "pending_submission": await _count(PaymentStatus.PENDING),
        "awaiting_admin": await _count(PaymentStatus.PENDING_VERIFICATION),
        "approved": await _count(PaymentStatus.APPROVED),
        "rejected": await _count(PaymentStatus.REJECTED),
        "total_topup_bdt": float(await _sum_approved()),
    }