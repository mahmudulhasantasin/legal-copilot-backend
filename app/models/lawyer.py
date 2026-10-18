from __future__ import annotations
import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from sqlalchemy import (
    String, Integer, Enum, ForeignKey, DateTime,
    func, Numeric, Text, JSON, Boolean, Float
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User, WithdrawalRequest
    from app.models.memo import ContractMemo


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class LawyerStatus(str, enum.Enum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class LawyerAvailability(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"
    ON_LEAVE = "ON_LEAVE"


# ──────────────────────────────────────────────────────────
# LAWYER PROFILE
# ──────────────────────────────────────────────────────────

class LawyerProfile(Base):
    __tablename__ = "lawyer_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # ── Professional Credentials ──
    bar_council_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    experience_years: Mapped[int] = mapped_column(Integer, default=0)
    specializations: Mapped[list[str]] = mapped_column(JSON, default=list)

    # ── Verification Documents (Supabase paths) ──
    bar_certificate_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    profile_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    signature_img_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Status & Approval ──
    status: Mapped[LawyerStatus] = mapped_column(
        Enum(LawyerStatus), default=LawyerStatus.PENDING_VERIFICATION,
        index=True, nullable=False
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # ── Availability ──
    availability: Mapped[LawyerAvailability] = mapped_column(
        Enum(LawyerAvailability), default=LawyerAvailability.AVAILABLE,
        index=True, nullable=False
    )
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=5)
    active_task_count: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # ── Reputation ──
    rating_average: Mapped[float] = mapped_column(Float, default=4.5)
    total_ratings_received: Mapped[int] = mapped_column(Integer, default=0)
    completed_reviews_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    disputed_reviews_count: Mapped[int] = mapped_column(Integer, default=0)

    # ── Category Experience (for ranking diversity boost) ──
    handled_categories: Mapped[dict] = mapped_column(JSON, default=dict)

    # ── Financial (BDT) ──
    wallet_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_earned: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_withdrawn: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))

    # ── Bio ──
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(10), default="bn")
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Dhaka")

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──
    user: Mapped["User"] = relationship(back_populates="lawyer_profile", foreign_keys=[user_id])
    reviews: Mapped[list["ContractMemo"]] = relationship(
        back_populates="assigned_lawyer", foreign_keys="ContractMemo.lawyer_id"
    )
    withdrawals: Mapped[list["WithdrawalRequest"]] = relationship(
        back_populates="lawyer", cascade="all, delete-orphan"
    )

    # ── Helper Properties ──
    @property
    def is_eligible_for_work(self) -> bool:
        return (
            self.status == LawyerStatus.APPROVED
            and self.availability == LawyerAvailability.AVAILABLE
            and self.active_task_count < self.max_concurrent_tasks
        )

    @property
    def is_newcomer(self) -> bool:
        return self.completed_reviews_count < 5

    @property
    def remaining_task_slots(self) -> int:
        return max(0, self.max_concurrent_tasks - self.active_task_count)

    @property
    def load_ratio(self) -> float:
        return min(1.0, self.active_task_count / max(self.max_concurrent_tasks, 1))