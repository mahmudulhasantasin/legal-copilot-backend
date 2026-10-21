import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import (
    String, Integer, ForeignKey, DateTime, func, Text, JSON, Boolean, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.memo import Contract


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class ChatMode(str, enum.Enum):
    """
    GENERAL:          Normal users asking legal questions (1 credit/query)
    LAWYER_RESEARCH:  Lawyers doing legal research (100 free/month, then wallet)
    CONTRACT_REVIEW:  Lawyers reviewing a claimed contract (free, session-capped)
    USER_CONTRACT:    General users chatting about their uploaded contract (1 credit/query)
    """
    GENERAL = "GENERAL"
    LAWYER_RESEARCH = "LAWYER_RESEARCH"
    CONTRACT_REVIEW = "CONTRACT_REVIEW"
    USER_CONTRACT = "USER_CONTRACT"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ──────────────────────────────────────────────────────────
# CHAT SESSION
# ──────────────────────────────────────────────────────────

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(255), default="New Legal Query")

    # ── Mode & Scoping ──
    session_mode: Mapped[str] = mapped_column(
        String(30), default=ChatMode.GENERAL.value, index=True
    )
    scoped_contract_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contracts.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    # Only set when session_mode == CONTRACT_REVIEW or USER_CONTRACT

    # ── Tier Snapshot (frozen at creation) ──
    tier_at_creation: Mapped[str] = mapped_column(String(20), default="FREE")
    session_limit: Mapped[int] = mapped_column(Integer, default=5)
    # Snapshot from tier_limits.py — doesn't change if user upgrades mid-session

    # ── Usage Tracking ──
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_consumed: Mapped[int] = mapped_column(Integer, default=0)

    # ── Rolling Summary (for long sessions) ──
    rolling_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_summarized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summarized_up_to_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    # ── Lifecycle ──
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_close_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Dormant sessions auto-close after 7 days (set at last message time)

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──
    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[List["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at"
    )
    scoped_contract: Mapped[Optional["Contract"]] = relationship(
        foreign_keys=[scoped_contract_id]
    )

    # ── Helper Properties ──
    @property
    def is_full(self) -> bool:
        return self.message_count >= self.session_limit

    @property
    def remaining_messages(self) -> int:
        return max(0, self.session_limit - self.message_count)

    @property
    def is_contract_review(self) -> bool:
        return self.session_mode == ChatMode.CONTRACT_REVIEW.value

    # ── Composite Indexes ──
    __table_args__ = (
        Index("ix_chat_user_mode", "user_id", "session_mode", "created_at"),
        Index("ix_chat_scoped_contract", "scoped_contract_id", "user_id"),
    )


# ──────────────────────────────────────────────────────────
# CHAT MESSAGE
# ──────────────────────────────────────────────────────────

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Citations & Sources ──
    citations: Mapped[Optional[list[dict]]] = mapped_column(JSON, nullable=True)
    
    # ✅ নতুন যোগ করা ৩টি কলাম
    cited_cases: Mapped[Optional[list[dict]]] = mapped_column(JSON, nullable=True)
    suggested_next_steps: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    needs_clarification: Mapped[bool] = mapped_column(Boolean, default=False)
    
    retrieved_docs_snapshot: Mapped[Optional[list[dict]]] = mapped_column(JSON, nullable=True)
    # Snapshot of RAG docs used — for debugging/audit

    # ── Metadata ──
    credit_deducted: Mapped[int] = mapped_column(Integer, default=0)
    language_detected: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # "bn" | "en" | "banglish"
    model_used: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # "gemini-flash" | "groq-llama"
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Error Handling ──
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Timestamp ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # ── Relationship ──
    session: Mapped["ChatSession"] = relationship(back_populates="messages")