from __future__ import annotations
import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from sqlalchemy import (
    String, Integer, Enum, ForeignKey,
    DateTime, func, Numeric, Text, JSON, Boolean
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.lawyer import LawyerProfile
    from app.models.playbook import Enterprise


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class RiskLevel(str, enum.Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HITLStatus(str, enum.Enum):
    """
    Human-in-the-Loop verification lifecycle.
    
    Flow:
      NOT_REQUESTED
        → PENDING_PAYMENT    (user clicked verify, awaiting credit deduction)
        → QUEUED_FOR_LAWYER  (payment confirmed, waiting for offer acceptance)
        → OFFERED            (offered to specific lawyer, 3h window)
        → ASSIGNED           (lawyer accepted, 3h to submit)
        → IN_REVIEW          (lawyer started working)
        → COMPLETED          (verified & delivered)
        → REJECTED_BY_LAWYER (lawyer declined after accepting)
        → EXPIRED            (3-day global SLA breached → auto-refund)
        → REFUNDED           (credits returned to client)
        → DISPUTED           (client rejected lawyer's work)
    """
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    QUEUED_FOR_LAWYER = "QUEUED_FOR_LAWYER"
    OFFERED = "OFFERED"
    ASSIGNED = "ASSIGNED"
    IN_REVIEW = "IN_REVIEW"
    COMPLETED = "COMPLETED"
    REJECTED_BY_LAWYER = "REJECTED_BY_LAWYER"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"


class OfferStatus(str, enum.Enum):
    """Status of an individual offer within a ranked list."""
    PENDING = "PENDING"        # Not yet offered
    ACTIVE = "ACTIVE"          # Currently offered (3h window running)
    ACCEPTED = "ACCEPTED"      # Lawyer accepted → becomes ASSIGNED
    DECLINED = "DECLINED"      # Lawyer explicitly declined
    EXPIRED = "EXPIRED"        # 3h window passed without response
    SKIPPED = "SKIPPED"        # Auto-skipped (e.g., lawyer overloaded)


# ──────────────────────────────────────────────────────────
# CONTRACT (the uploaded file)
# ──────────────────────────────────────────────────────────

class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── Owner ──
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enterprise_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("enterprises.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ── File Metadata ──
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)  # Supabase storage path
    file_size_mb: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    file_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Visibility ──
    is_workspace_shared: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──
    user: Mapped["User"] = relationship(back_populates="contracts", foreign_keys=[user_id])
    memo: Mapped["ContractMemo | None"] = relationship(
        back_populates="contract", uselist=False, cascade="all, delete-orphan"
    )


# ──────────────────────────────────────────────────────────
# CONTRACT MEMO (AI audit + HITL state)
# ──────────────────────────────────────────────────────────

class ContractMemo(Base):
    __tablename__ = "contract_memos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contracts.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # ──────────────────────────────────────────
    # AI AUDIT RESULTS
    # ──────────────────────────────────────────
    overall_risk: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel), default=RiskLevel.MODERATE, index=True
    )
    risk_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    clauses_analysis: Mapped[list[dict]] = mapped_column(JSON, default=list)

    # ── Metrics (denormalized for fast dashboard queries) ──
    high_risk_count: Mapped[int] = mapped_column(Integer, default=0)
    moderate_risk_count: Mapped[int] = mapped_column(Integer, default=0)
    compliant_count: Mapped[int] = mapped_column(Integer, default=0)

    # ──────────────────────────────────────────
    # HITL (Human-in-the-Loop) VERIFICATION
    # ──────────────────────────────────────────
    hitl_status: Mapped[HITLStatus] = mapped_column(
        Enum(HITLStatus), default=HITLStatus.NOT_REQUESTED, index=True
    )

    # ── Client Side ──
    hitl_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hitl_credits_paid: Mapped[int] = mapped_column(Integer, default=0)
    hitl_global_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )  # hitl_requested_at + 72 hours

    # ── Ranked Lawyer Offers ──
    # Structure: [
    #   {"lawyer_id": 5, "score": 0.87, "offered_at": "...", "status": "ACTIVE", "expires_at": "..."},
    #   {"lawyer_id": 8, "score": 0.72, "offered_at": null, "status": "PENDING", "expires_at": null}
    # ]
    hitl_ranked_lawyers: Mapped[list[dict]] = mapped_column(JSON, default=list)
    hitl_current_offer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lawyer_profiles.id", ondelete="SET NULL"), nullable=True
    )
    hitl_offer_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # ── Assignment ──
    lawyer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lawyer_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )  # assigned_at + 3 hours

    # ── Lawyer Deliverables ──
    lawyer_edits_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    lawyer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    lawyer_signature: Mapped[str | None] = mapped_column(String(500), nullable=True)
    signed_contract_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lawyer_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Payment & Payout (INTERNAL ONLY) ──
    fee_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    lawyer_payout_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    platform_fee_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))

    # ── Refund ──
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Dispute ──
    dispute_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispute_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ──────────────────────────────────────────
    # TIMESTAMPS
    # ──────────────────────────────────────────
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ──────────────────────────────────────────
    # RELATIONSHIPS
    # ──────────────────────────────────────────
    contract: Mapped["Contract"] = relationship(back_populates="memo")
    assigned_lawyer: Mapped["LawyerProfile | None"] = relationship(
        back_populates="reviews",
        foreign_keys=[lawyer_id]
    )
    current_offer_lawyer: Mapped["LawyerProfile | None"] = relationship(
        foreign_keys=[hitl_current_offer_id]
    )

    # ──────────────────────────────────────────
    # HELPER PROPERTIES
    # ──────────────────────────────────────────
    @property
    def is_hitl_active(self) -> bool:
        """Whether a HITL task is currently in the pipeline."""
        return self.hitl_status in (
            HITLStatus.QUEUED_FOR_LAWYER,
            HITLStatus.OFFERED,
            HITLStatus.ASSIGNED,
            HITLStatus.IN_REVIEW,
        )

    @property
    def is_hitl_final(self) -> bool:
        """Whether HITL reached a terminal state."""
        return self.hitl_status in (
            HITLStatus.COMPLETED,
            HITLStatus.EXPIRED,
            HITLStatus.REFUNDED,
        )

    @property
    def days_until_deadline(self) -> float | None:
        """Days remaining until global SLA expires (for UI countdown)."""
        if not self.hitl_global_deadline:
            return None
        delta = self.hitl_global_deadline - datetime.now(self.hitl_global_deadline.tzinfo)
        return round(delta.total_seconds() / 86400, 2)

    @property
    def hours_until_submit(self) -> float | None:
        """Hours remaining for assigned lawyer to submit."""
        if not self.submit_deadline:
            return None
        delta = self.submit_deadline - datetime.now(self.submit_deadline.tzinfo)
        return round(delta.total_seconds() / 3600, 2)