"""
System configuration model — platform-wide flags, instant judge mode, and demo settings.

Table:
  system_configs – singleton row (id=1) controlling instant judge mode and fixed lawyer routing.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # ── Instant Judge Mode (Hackathon Evaluation / Demo Routing) ──
    instant_judge_mode: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    fixed_judge_lawyer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("lawyer_profiles.id", ondelete="SET NULL"), nullable=True
    )

    # ── Meta ──
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Relationship to LawyerProfile (optional)
    fixed_lawyer = relationship("LawyerProfile", foreign_keys=[fixed_judge_lawyer_id])
