import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import (
    String, Integer, Boolean, Enum, ForeignKey,
    DateTime, func, Numeric, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.lawyer import LawyerProfile
    from app.models.audit import Payment
    from app.models.chat import ChatSession
    from app.models.memo import Contract
    from app.models.playbook import Enterprise


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    GENERAL = "GENERAL"
    LAWYER = "LAWYER"
    ENTERPRISE_ADMIN = "ENTERPRISE_ADMIN"
    ENTERPRISE_MEMBER = "ENTERPRISE_MEMBER"
    ADMIN = "ADMIN"


class SubscriptionTier(str, enum.Enum):
    FREE = "FREE"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


class CreditTxType(str, enum.Enum):
    # ── Earns ──
    SIGNUP_BONUS = "SIGNUP_BONUS"
    TOPUP_MANUAL = "TOPUP_MANUAL"
    TOPUP_SSLCOMMERZ = "TOPUP_SSLCOMMERZ"
    ADMIN_ADJUST = "ADMIN_ADJUST"
    REFUND = "REFUND"
    ENTERPRISE_ALLOCATION = "ENTERPRISE_ALLOCATION"
    ENTERPRISE_REVOKE = "ENTERPRISE_REVOKE"

    # ── Spends ──
    CHAT_QUERY = "CHAT_QUERY"
    CONTRACT_AUDIT = "CONTRACT_AUDIT"
    PDF_EXPORT = "PDF_EXPORT"
    LAWYER_REVIEW_FEE = "LAWYER_REVIEW_FEE"
    LAWYER_EXTRA_CHAT = "LAWYER_EXTRA_CHAT"


class CompanyRole(str, enum.Enum):
    """Sub-role within an enterprise."""
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    MEMBER = "MEMBER"


class MemberApprovalStatus(str, enum.Enum):
    """Enterprise member onboarding status."""
    PENDING = "PENDING"        # Awaiting company admin approval
    APPROVED = "APPROVED"      # Active member
    REJECTED = "REJECTED"      # Company admin declined
    SUSPENDED = "SUSPENDED"    # Temporarily disabled


class WithdrawalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PAID = "PAID"
    REJECTED = "REJECTED"


class PayoutMethod(str, enum.Enum):
    BKASH = "BKASH"
    NAGAD = "NAGAD"
    ROCKET = "ROCKET"
    BANK = "BANK"


# ──────────────────────────────────────────────────────────
# USER MODEL
# ──────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), default=UserRole.GENERAL, nullable=False, index=True
    )

    # ── Personal Info ──
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    profile_photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── OTP / Email Verification ──
    otp_code: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    otp_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # SEC-AUTH-01: brute-force protection — failed attempts + lockout window
    otp_failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    otp_locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # SEC-AUTH-03: JWT revocation — bump to instantly invalidate every issued token
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # ── Subscription ──
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        Enum(SubscriptionTier), default=SubscriptionTier.FREE, nullable=False
    )

    # ── Personal Credits ──
    credit_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Lawyer Monthly Free Chat Pool ──
    monthly_free_chat_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monthly_free_chat_reset_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Status Flags ──
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Enterprise Association ──
    enterprise_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("enterprises.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    company_role: Mapped[Optional[CompanyRole]] = mapped_column(
        Enum(CompanyRole), nullable=True
    )
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    member_approval_status: Mapped[Optional[MemberApprovalStatus]] = mapped_column(
        Enum(MemberApprovalStatus), nullable=True, index=True
    )
    company_joined_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Member Credit Quota (SOFT CAP) ──
    member_credit_quota: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    member_credit_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──
    credit_transactions: Mapped[List["CreditTransaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        foreign_keys="CreditTransaction.user_id"
    )
    lawyer_profile: Mapped[Optional["LawyerProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan",
        foreign_keys="LawyerProfile.user_id"
    )
    payments: Mapped[List["Payment"]] = relationship(
        back_populates="user", foreign_keys="Payment.user_id"
    )
    chat_sessions: Mapped[List["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    contracts: Mapped[List["Contract"]] = relationship(
        back_populates="user", foreign_keys="Contract.user_id"
    )

    # Enterprise organization relationship
    enterprise: Mapped[Optional["Enterprise"]] = relationship(  # type: ignore
        back_populates="members", foreign_keys=[enterprise_id]
    )

    # ── Helper Properties ──
    @property
    def remaining_member_quota(self) -> int:
        return max(0, self.member_credit_quota - self.member_credit_used)

    @property
    def monthly_free_chat_remaining(self) -> int:
        from app.core.tier_limits import LAWYER_MONTHLY_FREE_CHAT_CREDITS
        return max(0, LAWYER_MONTHLY_FREE_CHAT_CREDITS - self.monthly_free_chat_used)

    @property
    def is_enterprise_user(self) -> bool:
        return self.role in (UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER)


# ──────────────────────────────────────────────────────────
# CREDIT TRANSACTION LEDGER
# ──────────────────────────────────────────────────────────

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True, index=True
    )
    enterprise_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("enterprises.id", ondelete="CASCADE"),
        nullable=True, index=True
    )

    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # signed (+/-)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_type: Mapped[CreditTxType] = mapped_column(Enum(CreditTxType), nullable=False, index=True)

    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    performed_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[Optional["User"]] = relationship(
        back_populates="credit_transactions", foreign_keys=[user_id]
    )


# ──────────────────────────────────────────────────────────
# WITHDRAWAL REQUEST (Lawyer Payouts)
# ──────────────────────────────────────────────────────────

class WithdrawalRequest(Base):
    """
    Lawyer submits a withdrawal request (bKash, Nagad, Rocket, or Bank Transfer).
    Admin approves & pays manually and marks as PAID with payout reference/TrxID.
    """
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    lawyer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lawyer_profiles.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    amount_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    bkash_number: Mapped[str] = mapped_column(String(60), nullable=False)
    payout_method: Mapped[str] = mapped_column(String(30), default="BKASH", nullable=False)
    account_number: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    branch_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    account_holder_name: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING,
        index=True, nullable=False
    )
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    processed_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    payout_trx_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lawyer: Mapped["LawyerProfile"] = relationship(back_populates="withdrawals")
    processed_by: Mapped[Optional["User"]] = relationship(foreign_keys=[processed_by_id])

    __table_args__ = (
        UniqueConstraint("lawyer_id", "requested_at", name="uq_lawyer_request_time"),
    )
