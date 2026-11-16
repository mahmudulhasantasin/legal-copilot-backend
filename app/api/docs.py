# app/api/docs.py
"""
/api/docs — Live Documentation & Pitch Deck module.

Public endpoints (gated by DocsConfig visibility + schedule):
  GET  /api/docs/access       → Is /docs currently viewable?
  GET  /api/docs/content      → Full pitch deck + tech docs payload
  GET  /api/docs/team         → Team members list
  GET  /api/docs/live-stats   → Real-time system KPIs

Admin endpoints (ADMIN role required):
  GET  /api/docs/config       → Current config
  PUT  /api/docs/config       → Update visibility / schedule
  PUT  /api/docs/content      → Update editable content sections
  POST /api/docs/team         → Add team member
  PUT  /api/docs/team/{id}    → Update team member
  DELETE /api/docs/team/{id}  → Remove team member
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sa_func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.docs_config import DocsConfig, DocsTeamMember, DocsVisibility

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/docs", tags=["Documentation & Pitch Deck"])


# ──────────────────────────────────────────────────────────
# HELPER: get-or-create the singleton config row
# ──────────────────────────────────────────────────────────

# Default schedule: June 10 00:00 → June 14 23:59 BST (UTC+6)
_DEFAULT_START = datetime(2027, 6, 10, 0, 0, 0, tzinfo=timezone.utc)
_DEFAULT_END = datetime(2027, 6, 14, 23, 59, 59, tzinfo=timezone.utc)


async def _get_config(db: AsyncSession) -> DocsConfig:
    """Return the singleton DocsConfig (id=1), creating it on first access."""
    result = await db.execute(select(DocsConfig).where(DocsConfig.id == 1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = DocsConfig(
            id=1,
            visibility=DocsVisibility.SCHEDULED,
            schedule_start=_DEFAULT_START,
            schedule_end=_DEFAULT_END,
        )
        db.add(cfg)
        await db.flush()
    return cfg


def _is_accessible(cfg: DocsConfig) -> bool:
    """Determine whether docs are publicly accessible right now."""
    if cfg.visibility == DocsVisibility.ON:
        return True
    if cfg.visibility == DocsVisibility.OFF:
        return False
    # SCHEDULED
    now = datetime.now(timezone.utc)
    start = cfg.schedule_start or _DEFAULT_START
    end = cfg.schedule_end or _DEFAULT_END
    return start <= now <= end


# ──────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ──────────────────────────────────────────────────────────

class DocsAccessResponse(BaseModel):
    accessible: bool
    visibility: str
    schedule_start: Optional[str] = None
    schedule_end: Optional[str] = None
    message: str = ""


class DocsConfigUpdate(BaseModel):
    visibility: Optional[str] = None
    schedule_start: Optional[str] = None
    schedule_end: Optional[str] = None


class DocsContentUpdate(BaseModel):
    pitch_deck: Optional[Dict[str, Any]] = None
    tech_docs: Optional[Dict[str, Any]] = None
    changelog: Optional[List[Dict[str, Any]]] = None
    roadmap: Optional[Dict[str, Any]] = None
    custom_sections: Optional[List[Dict[str, Any]]] = None


class TeamMemberCreate(BaseModel):
    full_name: str
    role: str
    email: Optional[str] = None
    image_url: Optional[str] = None
    bio: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    display_order: int = 0


class TeamMemberUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    image_url: Optional[str] = None
    bio: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


# ──────────────────────────────────────────────────────────
# PUBLIC ENDPOINTS
# ──────────────────────────────────────────────────────────

@router.get("/access")
async def check_access(db: AsyncSession = Depends(get_db)) -> DocsAccessResponse:
    """Check if /docs is currently accessible (no auth required)."""
    cfg = await _get_config(db)
    await db.commit()
    accessible = _is_accessible(cfg)
    return DocsAccessResponse(
        accessible=accessible,
        visibility=cfg.visibility,
        schedule_start=cfg.schedule_start.isoformat() if cfg.schedule_start else None,
        schedule_end=cfg.schedule_end.isoformat() if cfg.schedule_end else None,
        message="Documentation is live." if accessible else "Documentation is currently unavailable.",
    )


@router.get("/content")
async def get_content(db: AsyncSession = Depends(get_db)):
    """
    Return full docs content (pitch deck + tech docs + changelog + roadmap).
    Public but gated by access control.
    """
    cfg = await _get_config(db)
    await db.commit()
    if not _is_accessible(cfg):
        raise HTTPException(status_code=403, detail="Documentation is not currently available.")

    return {
        "pitch_deck": cfg.pitch_deck,
        "tech_docs": cfg.tech_docs,
        "changelog": cfg.changelog,
        "roadmap": cfg.roadmap,
        "custom_sections": cfg.custom_sections,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.get("/team")
async def get_team(db: AsyncSession = Depends(get_db)):
    """Return active team members, ordered by display_order."""
    cfg = await _get_config(db)
    await db.commit()
    if not _is_accessible(cfg):
        raise HTTPException(status_code=403, detail="Documentation is not currently available.")

    result = await db.execute(
        select(DocsTeamMember)
        .where(DocsTeamMember.is_active == True)
        .order_by(DocsTeamMember.display_order, DocsTeamMember.id)
    )
    members = result.scalars().all()
    return [
        {
            "id": m.id,
            "full_name": m.full_name,
            "role": m.role,
            "email": m.email,
            "image_url": m.image_url,
            "bio": m.bio,
            "linkedin_url": m.linkedin_url,
            "github_url": m.github_url,
            "display_order": m.display_order,
        }
        for m in members
    ]


@router.get("/live-stats")
async def get_live_stats(db: AsyncSession = Depends(get_db)):
    """
    Aggregated live KPIs for the pitch deck traction slide.
    Public but gated by access control.
    """
    cfg = await _get_config(db)
    if not _is_accessible(cfg):
        raise HTTPException(status_code=403, detail="Documentation is not currently available.")

    stats: Dict[str, Any] = {
        "total_users": 0,
        "total_audits": 0,
        "total_chat_sessions": 0,
        "total_chat_messages": 0,
        "total_lawyers": 0,
        "total_enterprises": 0,
        "statutes_indexed": 35000,
        "languages_supported": 3,
        "jurisdictions_active": 1,
    }

    try:
        # User counts
        r = await db.execute(text("SELECT COUNT(*) FROM users"))
        stats["total_users"] = r.scalar() or 0

        # Chat sessions
        r = await db.execute(text("SELECT COUNT(*) FROM chat_sessions"))
        stats["total_chat_sessions"] = r.scalar() or 0

        # Chat messages
        r = await db.execute(text("SELECT COUNT(*) FROM chat_messages"))
        stats["total_chat_messages"] = r.scalar() or 0

        # Audits
        r = await db.execute(text("SELECT COUNT(*) FROM contract_memos"))
        stats["total_audits"] = r.scalar() or 0

        # Lawyers
        r = await db.execute(text("SELECT COUNT(*) FROM lawyer_profiles"))
        stats["total_lawyers"] = r.scalar() or 0

        # Enterprises
        r = await db.execute(text("SELECT COUNT(*) FROM enterprises"))
        stats["total_enterprises"] = r.scalar() or 0

    except Exception as e:
        logger.warning(f"[Docs] live-stats query error (non-fatal): {e}")

    await db.commit()
    return stats


# ──────────────────────────────────────────────────────────
# ADMIN ENDPOINTS
# ──────────────────────────────────────────────────────────

@router.get("/config")
async def get_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: get current docs configuration."""
    cfg = await _get_config(db)
    await db.commit()
    return {
        "visibility": cfg.visibility,
        "schedule_start": cfg.schedule_start.isoformat() if cfg.schedule_start else None,
        "schedule_end": cfg.schedule_end.isoformat() if cfg.schedule_end else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        "updated_by": cfg.updated_by,
    }


@router.put("/config")
async def update_config(
    body: DocsConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: update visibility and/or schedule."""
    cfg = await _get_config(db)

    if body.visibility is not None:
        try:
            cfg.visibility = DocsVisibility(body.visibility)
        except ValueError:
            raise HTTPException(400, f"Invalid visibility: {body.visibility}. Use ON, OFF, or SCHEDULED.")

    if body.schedule_start is not None:
        try:
            cfg.schedule_start = datetime.fromisoformat(body.schedule_start)
        except ValueError:
            raise HTTPException(400, "Invalid schedule_start format. Use ISO 8601.")

    if body.schedule_end is not None:
        try:
            cfg.schedule_end = datetime.fromisoformat(body.schedule_end)
        except ValueError:
            raise HTTPException(400, "Invalid schedule_end format. Use ISO 8601.")

    cfg.updated_by = current_user.email or current_user.full_name or str(current_user.id)
    await db.commit()
    await db.refresh(cfg)

    return {
        "message": "Docs configuration updated.",
        "visibility": cfg.visibility,
        "schedule_start": cfg.schedule_start.isoformat() if cfg.schedule_start else None,
        "schedule_end": cfg.schedule_end.isoformat() if cfg.schedule_end else None,
        "is_accessible_now": _is_accessible(cfg),
    }


@router.put("/content")
async def update_content(
    body: DocsContentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: update editable content sections (pitch deck, tech docs, etc.)."""
    cfg = await _get_config(db)

    if body.pitch_deck is not None:
        cfg.pitch_deck = body.pitch_deck
    if body.tech_docs is not None:
        cfg.tech_docs = body.tech_docs
    if body.changelog is not None:
        cfg.changelog = body.changelog
    if body.roadmap is not None:
        cfg.roadmap = body.roadmap
    if body.custom_sections is not None:
        cfg.custom_sections = body.custom_sections

    cfg.updated_by = current_user.email or current_user.full_name or str(current_user.id)
    await db.commit()
    return {"message": "Documentation content updated."}


# ── Team CRUD ──

@router.get("/admin/team")
async def get_admin_team(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: get all team members (regardless of docs visibility)."""
    result = await db.execute(
        select(DocsTeamMember)
        .order_by(DocsTeamMember.display_order, DocsTeamMember.id)
    )
    members = result.scalars().all()
    return [
        {
            "id": m.id,
            "full_name": m.full_name,
            "role": m.role,
            "email": m.email,
            "image_url": m.image_url,
            "bio": m.bio,
            "linkedin_url": m.linkedin_url,
            "github_url": m.github_url,
            "display_order": m.display_order,
            "is_active": m.is_active,
        }
        for m in members
    ]


@router.post("/team", status_code=201)
async def add_team_member(
    body: TeamMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: add a new team member."""
    member = DocsTeamMember(
        full_name=body.full_name,
        role=body.role,
        email=body.email,
        image_url=body.image_url,
        bio=body.bio,
        linkedin_url=body.linkedin_url,
        github_url=body.github_url,
        display_order=body.display_order,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return {"message": "Team member added.", "id": member.id}


@router.put("/team/{member_id}")
async def update_team_member(
    member_id: int,
    body: TeamMemberUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: update an existing team member."""
    result = await db.execute(select(DocsTeamMember).where(DocsTeamMember.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Team member not found.")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(member, field, value)

    await db.commit()
    return {"message": "Team member updated."}


@router.delete("/team/{member_id}")
async def delete_team_member(
    member_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: remove a team member."""
    result = await db.execute(select(DocsTeamMember).where(DocsTeamMember.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Team member not found.")

    await db.delete(member)
    await db.commit()
    return {"message": "Team member removed."}
