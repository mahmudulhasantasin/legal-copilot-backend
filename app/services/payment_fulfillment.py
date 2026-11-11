"""
Shared atomic payment fulfillment and SSLCommerz reconciliation helpers.

Ensures idempotent credit granting with PostgreSQL row-level locking
so concurrent success redirect + IPN webhook calls can never double-credit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import (
    AuditAction,
    AuditLog,
    AuditSeverity,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.playbook import Enterprise
from app.models.user import CreditTransaction, CreditTxType, User
from app.services.sslcommerz import SSLCommerzGatewayError, sslcommerz_service

logger = logging.getLogger(__name__)


async def _write_payment_audit(
    db: AsyncSession,
    *,
    action: AuditAction,
    payment: Payment,
    description: str,
    actor: Optional[User] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    severity: AuditSeverity = AuditSeverity.INFO,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id if actor else payment.user_id,
            actor_role=actor.role.value if actor else "SYSTEM_GATEWAY",
            actor_email=actor.email if actor else "gateway@sslcommerz",
            action=action,
            severity=severity,
            target_entity="payment",
            target_id=payment.id,
            description=description[:500],
            before_state=before,
            after_state=after,
        )
    )


async def fulfill_approved_payment(
    db: AsyncSession,
    payment_id: int,
    *,
    actor: Optional[User] = None,
    admin_note: Optional[str] = None,
    gateway_telemetry: Optional[Dict[str, Any]] = None,
    allowed_statuses: Tuple[PaymentStatus, ...] = (
        PaymentStatus.PENDING,
        PaymentStatus.PENDING_VERIFICATION,
        PaymentStatus.EXPIRED,
    ),
) -> Dict[str, Any]:
    """
    Atomically approves a Payment and credits either the Enterprise pool or User wallet.
    Caller is responsible for `await db.commit()`.
    """
    p = await db.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if not p:
        raise HTTPException(404, "Payment not found.")

    if p.status == PaymentStatus.APPROVED:
        return {
            "already_approved": True,
            "credits_granted": p.total_credits,
            "payment": p,
        }

    if p.status not in allowed_statuses:
        raise HTTPException(400, f"Cannot approve payment in status: {p.status.value}")

    # Ensure val_id is not reused across separate Payment rows
    if gateway_telemetry and gateway_telemetry.get("val_id"):
        existing_val = await db.scalar(
            select(Payment).where(
                Payment.val_id == gateway_telemetry["val_id"],
                Payment.id != p.id,
            )
        )
        if existing_val:
            raise HTTPException(409, "Gateway val_id is already linked to another payment.")

    credits = p.total_credits
    before = {"status": p.status.value, "credits_granted": 0}
    is_ssl = p.method == PaymentMethod.SSLCOMMERZ
    tx_type = CreditTxType.TOPUP_SSLCOMMERZ if is_ssl else CreditTxType.TOPUP_MANUAL
    method_label = "SSLCommerz" if is_ssl else "Manual bKash"

    # ── Grant to enterprise pool ──
    if p.enterprise_id:
        ent = await db.scalar(
            select(Enterprise).where(Enterprise.id == p.enterprise_id).with_for_update()
        )
        if not ent:
            raise HTTPException(404, "Enterprise not found.")
        ent.credit_balance += credits
        if credits >= 11500:
            ent.member_limit = max(ent.member_limit, 10)
        elif credits >= 5500:
            ent.member_limit = max(ent.member_limit, 5)

        db.add(
            CreditTransaction(
                enterprise_id=ent.id,
                amount=+credits,
                balance_after=ent.credit_balance,
                tx_type=tx_type,
                description=f"{method_label} topup approved (Ref: {p.reference_code})",
                reference_id=p.reference_code,
                performed_by_id=actor.id if actor else p.user_id,
            )
        )
    # ── Grant to personal wallet ──
    elif p.user_id:
        u = await db.scalar(
            select(User).where(User.id == p.user_id).with_for_update()
        )
        if not u:
            raise HTTPException(404, "User not found.")
        u.credit_balance += credits

        db.add(
            CreditTransaction(
                user_id=u.id,
                amount=+credits,
                balance_after=u.credit_balance,
                tx_type=tx_type,
                description=f"{method_label} topup approved (Ref: {p.reference_code})",
                reference_id=p.reference_code,
                performed_by_id=actor.id if actor else u.id,
            )
        )
    else:
        raise HTTPException(400, "Payment has no payer assigned.")

    now = datetime.now(timezone.utc)
    p.status = PaymentStatus.APPROVED
    p.reviewed_at = now
    if actor:
        p.reviewed_by_id = actor.id
    if admin_note:
        p.admin_note = admin_note

    if gateway_telemetry:
        p.val_id = gateway_telemetry.get("val_id") or p.val_id
        p.bank_tran_id = gateway_telemetry.get("bank_tran_id") or p.bank_tran_id
        p.card_type = gateway_telemetry.get("card_type") or p.card_type
        p.card_issuer = gateway_telemetry.get("card_issuer") or p.card_issuer
        p.card_brand = gateway_telemetry.get("card_brand") or p.card_brand
        if gateway_telemetry.get("store_amount_bdt") is not None:
            p.store_amount_bdt = gateway_telemetry["store_amount_bdt"]
        p.gateway_status = gateway_telemetry.get("gateway_status") or p.gateway_status
        p.gateway_validated_at = gateway_telemetry.get("gateway_validated_at") or now
        if gateway_telemetry.get("gateway_raw_response") is not None:
            p.gateway_raw_response = gateway_telemetry["gateway_raw_response"]

    await _write_payment_audit(
        db,
        action=AuditAction.PAYMENT_APPROVED,
        payment=p,
        actor=actor,
        description=f"Approved {p.reference_code} via {method_label} (+{credits} credits)",
        before=before,
        after={
            "status": p.status.value,
            "credits_granted": credits,
            "val_id": p.val_id,
            "bank_tran_id": p.bank_tran_id,
        },
    )

    return {
        "already_approved": False,
        "credits_granted": credits,
        "payment": p,
    }


async def mark_payment_terminal_state(
    db: AsyncSession,
    *,
    reference_code: str,
    target_status: PaymentStatus,
    reason: Optional[str] = None,
    gateway_status: Optional[str] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Payment]:
    """
    Safely transitions a PENDING payment to FAILED or CANCELLED.
    Never overwrites an APPROVED payment.
    """
    if target_status not in (PaymentStatus.FAILED, PaymentStatus.CANCELLED):
        raise ValueError("target_status must be FAILED or CANCELLED")

    p = await db.scalar(
        select(Payment).where(Payment.reference_code == reference_code).with_for_update()
    )
    if not p:
        return None

    if p.status != PaymentStatus.PENDING:
        return p

    before = {"status": p.status.value}
    p.status = target_status
    p.gateway_status = (gateway_status or target_status.value)[:40]
    if reason:
        p.admin_note = reason[:500]
    if raw_payload:
        p.gateway_raw_response = sslcommerz_service.sanitize_gateway_payload(raw_payload)

    action = (
        AuditAction.PAYMENT_FAILED
        if target_status == PaymentStatus.FAILED
        else AuditAction.PAYMENT_CANCELLED
    )
    severity = AuditSeverity.WARNING if target_status == PaymentStatus.FAILED else AuditSeverity.INFO

    await _write_payment_audit(
        db,
        action=action,
        payment=p,
        description=f"{target_status.value} {p.reference_code}: {reason or 'Gateway callback'}",
        before=before,
        after={"status": p.status.value},
        severity=severity,
    )
    return p


async def verify_sslcommerz_and_fulfill(
    db: AsyncSession,
    *,
    tran_id: str,
    val_id: Optional[str] = None,
    raw_callback: Optional[Dict[str, Any]] = None,
    verify_signature: bool = True,
) -> Tuple[bool, Optional[Payment], str]:
    """
    Verifies a payment directly against SSLCommerz servers and fulfills credits atomically.
    Returns (success, payment, code).
    """
    clean_tran_id = (tran_id or "").strip()
    if not clean_tran_id:
        return False, None, "missing_tran_id"

    payment = await db.scalar(
        select(Payment).where(Payment.reference_code == clean_tran_id)
    )
    if not payment:
        return False, None, "payment_not_found"

    if payment.status == PaymentStatus.APPROVED:
        return True, payment, "already_approved"

    if verify_signature and raw_callback and raw_callback.get("verify_sign"):
        if not sslcommerz_service.verify_ipn_signature(raw_callback):
            await _write_payment_audit(
                db,
                action=AuditAction.PAYMENT_FAILED,
                payment=payment,
                description=f"Invalid SSLCommerz signature on callback for {payment.reference_code}",
                severity=AuditSeverity.CRITICAL,
            )
            await db.commit()
            return False, payment, "invalid_signature"

    resolved_val_id = (val_id or "").strip()
    if not resolved_val_id:
        queried = await sslcommerz_service.query_by_tran_id(payment.reference_code)
        if queried:
            q_status = str(queried.get("status", "")).upper()
            if q_status in ("VALID", "VALIDATED") and queried.get("val_id"):
                resolved_val_id = str(queried["val_id"]).strip()
            else:
                return False, payment, f"gateway_status_{(q_status or 'unknown').lower()}"
        else:
            return False, payment, "missing_val_id"

    try:
        validated = await sslcommerz_service.validate_order(resolved_val_id)
    except SSLCommerzGatewayError as exc:
        logger.error(f"[SSLCommerz] Validation call failed for {clean_tran_id}: {exc}")
        return False, payment, "validation_unreachable"

    v_status = str(validated.get("status", "")).upper()
    v_tran_id = str(validated.get("tran_id", "")).strip()
    v_currency = str(validated.get("currency_type") or validated.get("currency") or "BDT").upper()
    v_amount = sslcommerz_service.parse_decimal(validated.get("amount"))
    expected_amount = Decimal(payment.amount_bdt).quantize(Decimal("0.01"))

    if v_status not in ("VALID", "VALIDATED"):
        await _write_payment_audit(
            db,
            action=AuditAction.PAYMENT_FAILED,
            payment=payment,
            description=f"Validation status {v_status} rejected for {payment.reference_code}",
            severity=AuditSeverity.WARNING,
        )
        await db.commit()
        return False, payment, "invalid_gateway_status"

    if v_tran_id != payment.reference_code or v_currency != "BDT" or v_amount != expected_amount:
        await _write_payment_audit(
            db,
            action=AuditAction.PAYMENT_FAILED,
            payment=payment,
            description=(
                f"Amount/TranID mismatch for {payment.reference_code}: "
                f"got tran_id={v_tran_id}, amount={v_amount} {v_currency}, "
                f"expected={expected_amount} BDT"
            ),
            severity=AuditSeverity.CRITICAL,
        )
        await db.commit()
        return False, payment, "amount_or_tran_mismatch"

    telemetry = sslcommerz_service.extract_telemetry(validated)
    result = await fulfill_approved_payment(
        db,
        payment.id,
        admin_note="Auto-approved via SSLCommerz Validation API",
        gateway_telemetry=telemetry,
        allowed_statuses=(
            PaymentStatus.PENDING,
            PaymentStatus.PENDING_VERIFICATION,
            PaymentStatus.FAILED,
            PaymentStatus.EXPIRED,
        ),
    )
    await db.commit()
    return True, result["payment"], "already_approved" if result["already_approved"] else "approved"
