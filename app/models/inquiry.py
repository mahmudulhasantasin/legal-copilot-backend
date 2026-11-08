from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    String, Integer, ForeignKey,
    DateTime, func, Text, Boolean
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class FeedbackSuggestion(Base):
    """
    Public user feedback, ideas, or suggestions submitted via the landing page form.
    """
    __tablename__ = "feedback_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    contact: Mapped[str] = mapped_column(String(150), nullable=False)  # Email or phone
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="NEW", index=True)  # NEW, REVIEWED, RESOLVED
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class NewsletterSubscriber(Base):
    """
    Subscribers to statutory amendments, landmark precedent briefings, and platform digests.
    """
    __tablename__ = "newsletter_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(100), default="landing_page")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
