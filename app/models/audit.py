from __future__ import annotations
import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from sqlalchemy import (
    String, Integer, Enum, ForeignKey, DateTime,
    func, Numeric, Text, JSON, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.playbook import Enterprise


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class PaymentMethod(str, enum.Enum):
    BKASH = "BKASH"
    NAGAD = "NAGAD"
    BANK = "BANK"


class PaymentPurpose(str, enum.Enum):
    CREDIT_TOPUP = "CREDIT_TOPUP"
    ENTERPRISE_TOPUP = "ENTERPRISE_TOPUP"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REFUNDED = "REFUNDED"
    EXPIRED = "EXPIRED"


class AuditSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AuditAction(str, enum.Enum):
    # ── Authentication ──
    USER_REGISTERED = "USER_REGISTERED"
    USER_LOGIN = "USER_LOGIN"
    USER_LOGOUT = "USER_LOGOUT"
    USER_OTP_VERIFIED = "USER_OTP_VERIFIED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"

    # ── Admin Approvals ──
    LAWYER_APPROVED = "LAWYER_APPROVED"
    LAWYER_REJECTED = "LAWYER_REJECTED"
    ENTERPRISE_APPROVED = "ENTERPRISE_APPROVED"
    ENTERPRISE_REJECTED = "ENTERPRISE_REJECTED"
    MEMBER_APPROVED = "MEMBER_APPROVED"
    MEMBER_REJECTED = "MEMBER_REJECTED"
    MEMBER_SUSPENDED = "MEMBER_SUSPENDED"

    # ── Payments ──
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAYMENT_TRX_SUBMITTED = "PAYMENT_TRX_SUBMITTED"
    PAYMENT_APPROVED = "PAYMENT_APPROVED"
    PAYMENT_REJECTED = "PAYMENT_REJECTED"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"

    # ── Credits ──
    CREDITS_GRANTED = "CREDITS_GRANTED"
    CREDITS_DEDUCTED = "CREDITS_DEDUCTED"
    CREDITS_ADJUSTED = "CREDITS_ADJUSTED"
    ENTERPRISE_QUOTA_ALLOCATED = "ENTERPRISE_QUOTA_ALLOCATED"
    ENTERPRISE_QUOTA_REVOKED = "ENTERPRISE_QUOTA_REVOKED"

    # ── HITL (Lawyer Verification) ──
    HITL_REQUESTED = "HITL_REQUESTED"
    HITL_OFFERED = "HITL_OFFERED"
    HITL_ACCEPTED = "HITL_ACCEPTED"
    HITL_DECLINED = "HITL_DECLINED"
    HITL_SUBMITTED = "HITL_SUBMITTED"
    HITL_EXPIRED = "HITL_EXPIRED"
    HITL_REFUNDED = "HITL_REFUNDED"
    HITL_DISPUTED = "HITL_DISPUTED"
    HITL_DISPUTE_RESOLVED = "HITL_DISPUTE_RESOLVED"

    # ── Contracts ──
    CONTRACT_UPLOADED = "CONTRACT_UPLOADED"
    CONTRACT_ANALYZED = "CONTRACT_ANALYZED"
    CONTRACT_DELETED = "CONTRACT_DELETED"
    CONTRACT_EXPORTED_PDF = "CONTRACT_EXPORTED_PDF"

    # ── Withdrawals ──
    WITHDRAWAL_REQUESTED = "WITHDRAWAL_REQUESTED"
    WITHDRAWAL_APPROVED = "WITHDRAWAL_APPROVED"
    WITHDRAWAL_PAID = "WITHDRAWAL_PAID"
    WITHDRAWAL_REJECTED = "WITHDRAWAL_REJECTED"

    # ── System ──
    SLA_BREACH_AUTO_REFUND = "SLA_BREACH_AUTO_REFUND"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# ──────────────────────────────────────────────────────────
# MODELS
# ──────────────────────────────────────────────────────────

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    enterprise_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("enterprises.id", ondelete="SET NULL"),
        nullable=True, index=True
    )

    amount_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    credits_to_grant: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_credits: Mapped[int] = mapped_column(Integer, default=0)

    package_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("credit_packages.id", ondelete="SET NULL"), nullable=True
    )

    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod), default=PaymentMethod.BKASH, nullable=False
    )
    purpose: Mapped[PaymentPurpose] = mapped_column(Enum(PaymentPurpose), nullable=False)

    reference_code: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )

    trx_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, index=True, nullable=True
    )
    sender_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING, index=True, nullable=False
    )

    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    trx_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User | None"] = relationship(
        back_populates="payments", foreign_keys=[user_id]
    )
    enterprise: Mapped["Enterprise | None"] = relationship(
        back_populates="payments", foreign_keys=[enterprise_id]
    )
    reviewed_by: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by_id])

    __table_args__ = (
        Index("ix_payment_status_created", "status", "created_at"),
    )

    @property
    def total_credits(self) -> int:
        return self.credits_to_grant + self.bonus_credits

    @property
    def is_final(self) -> bool:
        return self.status in (
            PaymentStatus.APPROVED, PaymentStatus.REJECTED,
            PaymentStatus.REFUNDED, PaymentStatus.EXPIRED,
        )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), nullable=False, index=True)
    severity: Mapped[AuditSeverity] = mapped_column(
        Enum(AuditSeverity), default=AuditSeverity.INFO, index=True
    )

    target_entity: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    before_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_audit_actor_action", "actor_id", "action", "timestamp"),
        Index("ix_audit_target", "target_entity", "target_id", "timestamp"),
        Index("ix_audit_severity_time", "severity", "timestamp"),
    )