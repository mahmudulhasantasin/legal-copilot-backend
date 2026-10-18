from __future__ import annotations
import enum
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    String, Integer, Enum, ForeignKey, DateTime,
    func, JSON, Boolean, Numeric, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.audit import Payment


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class EnterpriseStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class CreditPackageTier(str, enum.Enum):
    STARTER = "STARTER"
    STANDARD = "STANDARD"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def generate_referral_code(company_name: str) -> str:
    prefix = "".join(filter(str.isalnum, company_name.upper()))[:4] or "COMP"
    suffix = secrets.token_hex(2).upper()
    return f"{prefix}-{suffix}"


# ──────────────────────────────────────────────────────────
# MODELS
# ──────────────────────────────────────────────────────────

class Enterprise(Base):
    __tablename__ = "enterprises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)

    # ── NEW FIELDS (From Diagram) ──
    company_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    registration_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tin_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Status & Approval ──
    status: Mapped[EnterpriseStatus] = mapped_column(
        Enum(EnterpriseStatus), default=EnterpriseStatus.PENDING_APPROVAL, index=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    pending_admin_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    referral_code: Mapped[str] = mapped_column(
        String(12), unique=True, index=True, nullable=False
    )

    credit_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    member_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    contract_quota_monthly: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    billing_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    billing_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ──
    members: Mapped[list["User"]] = relationship(
        back_populates="enterprise", foreign_keys="User.enterprise_id"
    )
    playbooks: Mapped[list["CompanyPlaybook"]] = relationship(
        back_populates="enterprise", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="enterprise", foreign_keys="Payment.enterprise_id"
    )


class CompanyPlaybook(Base):
    __tablename__ = "company_playbooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    enterprise_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    rules_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    enterprise: Mapped["Enterprise"] = relationship(back_populates="playbooks")


class CreditPackage(Base):
    __tablename__ = "credit_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[CreditPackageTier] = mapped_column(Enum(CreditPackageTier), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    price_bdt: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    base_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_credits: Mapped[int] = mapped_column(Integer, default=0)

    applies_to: Mapped[str] = mapped_column(String(30), default="ALL")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    badge_text: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def total_credits(self) -> int:
        return self.base_credits + self.bonus_credits

    @property
    def effective_price_per_credit(self) -> float:
        return round(self.price_bdt / max(self.total_credits, 1), 4)