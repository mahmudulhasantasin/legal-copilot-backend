import logging
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.pricing import (
    ALLOW_CUSTOM_TOPUP,
    CUSTOM_TOPUP_MAX_BDT,
    CUSTOM_TOPUP_MIN_BDT,
    calculate_custom_topup_credits,
)
from app.core.security import get_current_user
from app.models.audit import (
    AuditAction,
    AuditLog,
    AuditSeverity,
    Payment,
    PaymentMethod,
    PaymentPurpose,
    PaymentStatus,
)
from app.models.playbook import CreditPackage, Enterprise, EnterpriseStatus
from app.models.user import User, UserRole
from app.services.payment_fulfillment import (
    mark_payment_terminal_state,
    verify_sslcommerz_and_fulfill,
)
from app.services.sslcommerz import (
    SSLCommerzConfigError,
    SSLCommerzGatewayError,
    sslcommerz_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["Payments"])


# ──────────────────────────────────────────────────────────
# CONSTANTS & HELPERS
# ──────────────────────────────────────────────────────────

PAYMENT_EXPIRY_HOURS = 24
_BD_PHONE_RE = re.compile(r"^01[3-9]\d{8}$")


def _generate_reference_code() -> str:
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(8))
    return f"PAY-{suffix}"


def _normalize_bd_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = raw.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("+880"):
        cleaned = "0" + cleaned[4:]
    elif cleaned.startswith("880"):
        cleaned = "0" + cleaned[3:]
    return cleaned or None


def _is_valid_bd_phone(phone: Optional[str]) -> bool:
    return bool(phone and _BD_PHONE_RE.fullmatch(phone))


async def _extract_callback_params(request: Request) -> Dict[str, Any]:
    """Read SSLCommerz callback fields from POST form-data (or query params fallback)."""
    data: Dict[str, Any] = dict(request.query_params)
    if request.method.upper() == "POST":
        try:
            form = await request.form()
            for k, v in form.items():
                data[k] = v
        except Exception:
            pass
    return data


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
    db.add(
        AuditLog(
            actor_id=actor.id,
            actor_role=actor.role.value,
            actor_email=actor.email,
            action=action,
            severity=severity,
            target_entity="payment",
            target_id=payment_id,
            description=description[:500],
            before_state=before,
            after_state=after,
        )
    )


def _payment_to_view(p: Payment, package_name: Optional[str] = None) -> "PaymentView":
    return PaymentView(
        id=p.id,
        reference_code=p.reference_code,
        package_name=package_name,
        amount_bdt=float(p.amount_bdt),
        store_amount_bdt=float(p.store_amount_bdt) if p.store_amount_bdt is not None else None,
        credits_to_grant=p.credits_to_grant,
        bonus_credits=p.bonus_credits,
        total_credits=p.total_credits,
        purpose=p.purpose.value,
        status=p.status.value,
        method=p.method.value,
        trx_id=p.trx_id,
        sender_number=p.sender_number,
        val_id=p.val_id,
        bank_tran_id=p.bank_tran_id,
        card_type=p.card_type,
        card_issuer=p.card_issuer,
        card_brand=p.card_brand,
        gateway_status=p.gateway_status,
        admin_note=p.admin_note,
        created_at=p.created_at,
        trx_submitted_at=p.trx_submitted_at,
        gateway_validated_at=p.gateway_validated_at,
        reviewed_at=p.reviewed_at,
        expires_at=p.expires_at,
    )


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class InitiatePaymentRequest(BaseModel):
    """Package-based SSLCommerz checkout initiation."""
    package_id: Optional[int] = Field(
        default=None,
        description="ID of the credit package to purchase.",
    )
    customer_phone: Optional[str] = Field(
        default=None,
        max_length=25,
        description="Bangladeshi mobile number (01XXXXXXXXX) required if not saved on user profile.",
    )
    custom_amount_bdt: Optional[Decimal] = Field(
        default=None,
        description="[DISABLED] Custom top-up amount in BDT.",
    )

    @field_validator("custom_amount_bdt")
    @classmethod
    def validate_amount(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Amount must be positive.")
        return v


class PaymentView(BaseModel):
    id: int
    reference_code: str
    package_name: Optional[str] = None
    amount_bdt: float
    store_amount_bdt: Optional[float] = None
    credits_to_grant: int
    bonus_credits: int
    total_credits: int
    purpose: str
    status: str
    method: str
    trx_id: Optional[str] = None
    sender_number: Optional[str] = None
    val_id: Optional[str] = None
    bank_tran_id: Optional[str] = None
    card_type: Optional[str] = None
    card_issuer: Optional[str] = None
    card_brand: Optional[str] = None
    gateway_status: Optional[str] = None
    admin_note: Optional[str] = None
    created_at: datetime
    trx_submitted_at: Optional[datetime] = None
    gateway_validated_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PackageView(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price_bdt: float
    total_credits: int
    badge_text: Optional[str]
    display_order: int

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────
# CONFIG & PACKAGES
# ──────────────────────────────────────────────────────────

@router.get("/config")
async def get_payment_gateway_config():
    """Public gateway capability check (never exposes credentials)."""
    return {
        "gateway": "SSLCOMMERZ",
        "sslcommerz_configured": sslcommerz_service.is_configured(),
        "is_sandbox": sslcommerz_service.is_sandbox,
        "supported_channels": [
            "VISA",
            "MasterCard",
            "bKash",
            "Nagad",
            "Rocket",
            "NetBanking",
        ],
    }


@router.get("/packages", response_model=List[PackageView])
async def list_available_packages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns credit packages visible to the current user.
    Enterprise users see ENTERPRISE + ALL; others see GENERAL + ALL.
    """
    if current_user.role in (UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER):
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
# INITIATE SSLCOMMERZ CHECKOUT
# ──────────────────────────────────────────────────────────

@router.post("/initiate", status_code=status.HTTP_201_CREATED)
async def initiate_payment(
    payload: InitiatePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a PENDING Payment and opens an SSLCommerz checkout session.
    Returns GatewayPageURL for browser redirection.
    """
    if current_user.role == UserRole.ENTERPRISE_MEMBER:
        raise HTTPException(
            403,
            "Enterprise members share their company credit pool. Please ask your Enterprise Admin to top up.",
        )

    if current_user.role == UserRole.LAWYER:
        raise HTTPException(403, "Lawyer accounts cannot purchase client credit packages.")

    if not payload.package_id and not ALLOW_CUSTOM_TOPUP:
        raise HTTPException(400, "Custom top-up is disabled. Please select a package.")

    if payload.package_id and payload.custom_amount_bdt is not None:
        raise HTTPException(400, "Provide package_id OR custom_amount_bdt, not both.")

    if not payload.package_id and payload.custom_amount_bdt is None:
        raise HTTPException(400, "Select a package or provide a custom amount.")

    # ── Verify customer phone before opening gateway session ──
    provided_phone = _normalize_bd_phone(payload.customer_phone)
    profile_phone = _normalize_bd_phone(current_user.phone)

    if provided_phone:
        if not _is_valid_bd_phone(provided_phone):
            raise HTTPException(
                400,
                "Please enter a valid Bangladeshi mobile number (e.g. 017XXXXXXXX).",
            )
        resolved_phone = provided_phone
    elif profile_phone:
        resolved_phone = profile_phone
    else:
        raise HTTPException(
            400,
            "Phone number is required for payment checkout. Please enter your mobile number.",
        )

    if not sslcommerz_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "SSLCommerz gateway credentials are not configured yet. "
                "Set SSLCOMMERZ_STORE_ID and SSLCOMMERZ_STORE_PASSWD in backend .env."
            ),
        )

    # ── Resolve amount + credits from authoritative DB package ──
    pkg_name = "Credit Top-Up"
    if payload.package_id:
        pkg = await db.scalar(
            select(CreditPackage).where(
                CreditPackage.id == payload.package_id,
                CreditPackage.is_active == True,
            )
        )
        if not pkg:
            raise HTTPException(404, "Package not found or inactive.")

        is_enterprise = current_user.role == UserRole.ENTERPRISE_ADMIN
        if pkg.applies_to == "ENTERPRISE" and not is_enterprise:
            raise HTTPException(400, "This package is not available for your account.")
        if pkg.applies_to == "GENERAL" and is_enterprise:
            raise HTTPException(400, "This package is not available for enterprises.")

        amount_bdt = Decimal(pkg.price_bdt)
        credits_to_grant = pkg.base_credits
        bonus_credits = pkg.bonus_credits
        pkg_name = pkg.name
    else:
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

    # ── Decide destination wallet ──
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
        user_id = current_user.id
    else:
        purpose = PaymentPurpose.CREDIT_TOPUP
        enterprise_id = None
        user_id = current_user.id

    # ── Save phone to user profile if empty & unique ──
    if not current_user.phone and resolved_phone and _is_valid_bd_phone(resolved_phone):
        taken = await db.scalar(
            select(User.id).where(
                User.phone == resolved_phone,
                User.id != current_user.id,
            )
        )
        if not taken:
            current_user.phone = resolved_phone

    # ── Generate unique tran_id (reference_code) ──
    reference_code = None
    for _ in range(5):
        code = _generate_reference_code()
        if not await db.scalar(select(Payment).where(Payment.reference_code == code)):
            reference_code = code
            break
    if not reference_code:
        raise HTTPException(500, "Could not generate payment reference code. Try again.")

    expires_at = datetime.now(timezone.utc) + timedelta(hours=PAYMENT_EXPIRY_HOURS)

    payment = Payment(
        user_id=user_id,
        enterprise_id=enterprise_id,
        amount_bdt=amount_bdt,
        credits_to_grant=credits_to_grant,
        bonus_credits=bonus_credits,
        package_id=payload.package_id,
        method=PaymentMethod.SSLCOMMERZ,
        purpose=purpose,
        reference_code=reference_code,
        sender_number=resolved_phone,
        status=PaymentStatus.PENDING,
        expires_at=expires_at,
    )
    db.add(payment)
    await db.flush()

    backend_base = settings.BACKEND_PUBLIC_URL.rstrip("/")
    try:
        session_resp = await sslcommerz_service.create_session(
            tran_id=reference_code,
            amount_bdt=amount_bdt,
            success_url=f"{backend_base}/api/payments/sslcommerz/success",
            fail_url=f"{backend_base}/api/payments/sslcommerz/fail",
            cancel_url=f"{backend_base}/api/payments/sslcommerz/cancel",
            ipn_url=f"{backend_base}/api/payments/sslcommerz/ipn",
            customer_name=current_user.full_name,
            customer_email=current_user.email,
            customer_phone=resolved_phone,
            product_name=pkg_name,
            value_a=str(payment.id),
            value_b=str(current_user.id),
            value_c=purpose.value,
        )
    except SSLCommerzConfigError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))
    except SSLCommerzGatewayError as exc:
        payment.status = PaymentStatus.FAILED
        payment.gateway_status = "SESSION_ERROR"
        payment.admin_note = str(exc)[:500]
        await _log_payment_action(
            db,
            current_user,
            AuditAction.PAYMENT_FAILED,
            payment_id=payment.id,
            description=f"SSLCommerz session error for {reference_code}: {exc}",
            severity=AuditSeverity.WARNING,
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc))

    payment.gateway_session_key = session_resp.get("session_key") or None
    payment.gateway_status = "SESSION_CREATED"

    await _log_payment_action(
        db,
        current_user,
        AuditAction.PAYMENT_INITIATED,
        payment_id=payment.id,
        description=(
            f"Initiated SSLCommerz {reference_code} for ৳{amount_bdt} "
            f"({credits_to_grant + bonus_credits} credits)"
        ),
    )
    await db.commit()
    await db.refresh(payment)

    return {
        "payment_id": payment.id,
        "reference_code": payment.reference_code,
        "amount_bdt": float(payment.amount_bdt),
        "total_credits": payment.total_credits,
        "package_name": pkg_name,
        "expires_at": payment.expires_at,
        "method": payment.method.value,
        "gateway_url": session_resp["gateway_url"],
        "session_key": payment.gateway_session_key,
        "is_sandbox": sslcommerz_service.is_sandbox,
    }


# ──────────────────────────────────────────────────────────
# SSLCOMMERZ CALLBACKS & IPN
# ──────────────────────────────────────────────────────────

@router.api_route("/sslcommerz/success", methods=["POST", "GET"])
async def sslcommerz_success_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Browser redirect callback from SSLCommerz after payment.
    NEVER trusts POST body alone; validates against SSLCommerz Order Validation API.
    """
    data = await _extract_callback_params(request)
    tran_id = str(data.get("tran_id") or "").strip()
    val_id = str(data.get("val_id") or "").strip()
    frontend_base = settings.FRONTEND_URL.rstrip("/")

    success, payment, code = await verify_sslcommerz_and_fulfill(
        db,
        tran_id=tran_id,
        val_id=val_id,
        raw_callback=data,
        verify_signature=True,
    )

    if success and payment:
        redirect_url = (
            f"{frontend_base}/pricing?payment=success"
            f"&ref={quote(payment.reference_code)}"
            f"&credits={payment.total_credits}"
        )
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)

    logger.warning(f"[SSLCommerz] Success callback failed validation: tran_id={tran_id}, code={code}")
    redirect_url = (
        f"{frontend_base}/pricing?payment=failed"
        f"&ref={quote(tran_id)}"
        f"&reason={quote(code)}"
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.api_route("/sslcommerz/fail", methods=["POST", "GET"])
async def sslcommerz_fail_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Browser redirect callback when gateway payment fails."""
    data = await _extract_callback_params(request)
    tran_id = str(data.get("tran_id") or "").strip()
    reason = str(data.get("error") or data.get("failedreason") or "Payment failed at gateway").strip()
    gateway_status = str(data.get("status") or "FAILED").strip()

    if tran_id:
        await mark_payment_terminal_state(
            db,
            reference_code=tran_id,
            target_status=PaymentStatus.FAILED,
            reason=reason,
            gateway_status=gateway_status,
            raw_payload=data,
        )
        await db.commit()

    frontend_base = settings.FRONTEND_URL.rstrip("/")
    redirect_url = f"{frontend_base}/pricing?payment=failed&ref={quote(tran_id)}&reason=gateway_failed"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.api_route("/sslcommerz/cancel", methods=["POST", "GET"])
async def sslcommerz_cancel_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Browser redirect callback when user cancels payment on SSLCommerz."""
    data = await _extract_callback_params(request)
    tran_id = str(data.get("tran_id") or "").strip()

    if tran_id:
        await mark_payment_terminal_state(
            db,
            reference_code=tran_id,
            target_status=PaymentStatus.CANCELLED,
            reason="Cancelled by user on SSLCommerz checkout",
            gateway_status=str(data.get("status") or "CANCELLED"),
            raw_payload=data,
        )
        await db.commit()

    frontend_base = settings.FRONTEND_URL.rstrip("/")
    redirect_url = f"{frontend_base}/pricing?payment=cancelled&ref={quote(tran_id)}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sslcommerz/ipn")
async def sslcommerz_ipn_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Server-to-server Instant Payment Notification (IPN) listener.
    Requires valid MD5 verify_sign signature AND passes Order Validation API.
    """
    data = await _extract_callback_params(request)
    tran_id = str(data.get("tran_id") or "").strip()
    val_id = str(data.get("val_id") or "").strip()
    gw_status = str(data.get("status") or "").strip().upper()

    if not tran_id:
        raise HTTPException(400, "Missing tran_id in IPN payload.")

    if not sslcommerz_service.verify_ipn_signature(data):
        logger.warning(f"[SSLCommerz IPN] Rejected invalid signature for tran_id={tran_id}")
        raise HTTPException(400, "Invalid IPN signature.")

    if gw_status in ("VALID", "VALIDATED"):
        success, _, code = await verify_sslcommerz_and_fulfill(
            db,
            tran_id=tran_id,
            val_id=val_id,
            raw_callback=data,
            verify_signature=False,  # Already verified above
        )
        if not success:
            raise HTTPException(400, f"Validation failed: {code}")
        return {"status": "ok", "result": code}

    if gw_status in ("FAILED", "CANCELLED"):
        target = PaymentStatus.FAILED if gw_status == "FAILED" else PaymentStatus.CANCELLED
        await mark_payment_terminal_state(
            db,
            reference_code=tran_id,
            target_status=target,
            reason=str(data.get("error") or data.get("failedreason") or f"IPN {gw_status}"),
            gateway_status=gw_status,
            raw_payload=data,
        )
        await db.commit()
        return {"status": "ok", "result": target.value.lower()}

    return {"status": "ignored", "gateway_status": gw_status}


# ──────────────────────────────────────────────────────────
# SELF-VERIFY / CANCEL / HISTORY / SUMMARY
# ──────────────────────────────────────────────────────────

@router.post("/{payment_id}/verify")
async def verify_my_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Allows the payment owner to reconcile a PENDING payment directly with SSLCommerz
    if they closed the browser before redirect completed.
    """
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

    if payment.status == PaymentStatus.APPROVED:
        return {
            "status": "APPROVED",
            "credits_granted": payment.total_credits,
            "message": "Payment is already approved and credited.",
        }

    if not sslcommerz_service.is_configured():
        raise HTTPException(503, "SSLCommerz gateway is not configured on the server.")

    try:
        success, updated, code = await verify_sslcommerz_and_fulfill(
            db,
            tran_id=payment.reference_code,
            val_id=payment.val_id,
            verify_signature=False,
        )
    except SSLCommerzGatewayError as exc:
        raise HTTPException(502, str(exc))

    if success and updated:
        return {
            "status": updated.status.value,
            "credits_granted": updated.total_credits,
            "message": "Payment verified with SSLCommerz and credits added.",
        }

    return {
        "status": updated.status.value if updated else payment.status.value,
        "credits_granted": 0,
        "code": code,
        "message": "Payment is not completed on SSLCommerz yet.",
    }


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

    payment.status = PaymentStatus.CANCELLED
    payment.gateway_status = "CANCELLED_BY_USER"
    await _log_payment_action(
        db,
        current_user,
        AuditAction.PAYMENT_CANCELLED,
        payment_id=payment.id,
        description=f"User cancelled {payment.reference_code}",
    )
    await db.commit()
    return {"status": "success"}


@router.get("/my", response_model=List[PaymentView])
async def list_my_payments(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Payment, CreditPackage.name).outerjoin(
        CreditPackage, Payment.package_id == CreditPackage.id
    )

    if current_user.role == UserRole.ENTERPRISE_ADMIN and current_user.enterprise_id:
        stmt = stmt.where(
            (Payment.user_id == current_user.id)
            | (Payment.enterprise_id == current_user.enterprise_id)
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
    rows = (await db.execute(stmt)).all()

    return [_payment_to_view(p, pkg_name) for p, pkg_name in rows]


@router.get("/summary/me")
async def my_payment_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == UserRole.ENTERPRISE_ADMIN and current_user.enterprise_id:
        base_filter = Payment.enterprise_id == current_user.enterprise_id
    else:
        base_filter = Payment.user_id == current_user.id

    async def _count(status_value: PaymentStatus) -> int:
        return (
            await db.execute(
                select(func.count(Payment.id)).where(
                    base_filter,
                    Payment.status == status_value,
                )
            )
        ).scalar_one() or 0

    async def _sum_approved() -> Decimal:
        return (
            await db.execute(
                select(func.coalesce(func.sum(Payment.amount_bdt), 0)).where(
                    base_filter,
                    Payment.status == PaymentStatus.APPROVED,
                )
            )
        ).scalar_one() or Decimal("0.00")

    pending_cnt = await _count(PaymentStatus.PENDING)
    awaiting_cnt = await _count(PaymentStatus.PENDING_VERIFICATION)
    approved_cnt = await _count(PaymentStatus.APPROVED)
    rejected_cnt = await _count(PaymentStatus.REJECTED)
    failed_cnt = await _count(PaymentStatus.FAILED)
    cancelled_cnt = await _count(PaymentStatus.CANCELLED)

    return {
        "pending_submission": pending_cnt,
        "awaiting_admin": awaiting_cnt,
        "approved": approved_cnt,
        "rejected": rejected_cnt,
        "failed": failed_cnt,
        "cancelled": cancelled_cnt,
        "total_topup_bdt": float(await _sum_approved()),
    }


@router.get("/{payment_id}", response_model=PaymentView)
async def get_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(Payment, CreditPackage.name)
            .outerjoin(CreditPackage, Payment.package_id == CreditPackage.id)
            .where(Payment.id == payment_id)
        )
    ).first()
    if not row:
        raise HTTPException(404, "Payment not found.")

    payment, pkg_name = row
    is_owner = payment.user_id == current_user.id
    is_enterprise_owner = (
        payment.enterprise_id
        and payment.enterprise_id == current_user.enterprise_id
        and current_user.role == UserRole.ENTERPRISE_ADMIN
    )
    if not (is_owner or is_enterprise_owner):
        raise HTTPException(403, "Access denied.")

    return _payment_to_view(payment, pkg_name)
