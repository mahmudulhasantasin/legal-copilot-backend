"""
Docs module models — visibility config, content storage, and team members.

Tables:
  docs_config        – singleton row controlling visibility, schedule, and editable content JSON.
  docs_team_members   – ordered list of team member cards shown on the /docs page.
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, Integer, JSON, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# ──────────────────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────────────────

class DocsVisibility(str, enum.Enum):
    """Controls whether /docs is publicly reachable."""
    ON = "ON"            # Always publicly accessible
    OFF = "OFF"          # Never accessible (returns 403)
    SCHEDULED = "SCHEDULED"  # Accessible only within schedule_start → schedule_end


# ──────────────────────────────────────────────────────────
# DOCS CONFIG (singleton — id=1)
# ──────────────────────────────────────────────────────────

class DocsConfig(Base):
    __tablename__ = "docs_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # ── Access Control ──
    visibility: Mapped[str] = mapped_column(
        Enum(DocsVisibility, name="docsvisibility", create_type=True),
        default=DocsVisibility.SCHEDULED,
        nullable=False,
    )
    schedule_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    schedule_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Editable Content (JSON blobs) ──
    pitch_deck: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tech_docs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    changelog: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    roadmap: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    custom_sections: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # ── Meta ──
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)


# ──────────────────────────────────────────────────────────
# TEAM MEMBERS
# ──────────────────────────────────────────────────────────

class DocsTeamMember(Base):
    __tablename__ = "docs_team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
