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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sa_func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, get_current_user_optional, require_roles
from app.models.user import User, UserRole
from app.models.docs_config import DocsConfig, DocsTeamMember, DocsVisibility
from app.services.docs_content import merged_content, build_markdown
from app.services.architecture_data import get_architecture_systems

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/docs", tags=["Documentation & Pitch Deck"])


# ──────────────────────────────────────────────────────────
# HELPER: get-or-create the singleton config row
# ──────────────────────────────────────────────────────────

# Default schedule kept as metadata; visibility defaults to ON (public 24/7).
_DEFAULT_START = datetime(2027, 6, 10, 0, 0, 0, tzinfo=timezone.utc)
_DEFAULT_END = datetime(2027, 6, 14, 23, 59, 59, tzinfo=timezone.utc)


async def _get_config(db: AsyncSession) -> DocsConfig:
    """Return the singleton DocsConfig (id=1), creating it on first access."""
    result = await db.execute(select(DocsConfig).where(DocsConfig.id == 1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = DocsConfig(
            id=1,
            visibility=DocsVisibility.ON,
            schedule_start=_DEFAULT_START,
            schedule_end=_DEFAULT_END,
        )
        db.add(cfg)
        await db.flush()
    else:
        dirty = False
        if cfg.pitch_deck and "ContractMind" in str(cfg.pitch_deck.get("company", "")):
            p = dict(cfg.pitch_deck)
            p["company"] = "Advotech Copilot"
            cfg.pitch_deck = p
            dirty = True
        if cfg.draft and isinstance(cfg.draft, dict) and "ContractMind" in str(cfg.draft.get("pitch_deck", {}).get("company", "")):
            d = dict(cfg.draft)
            dp = dict(d.get("pitch_deck") or {})
            dp["company"] = "Advotech Copilot"
            d["pitch_deck"] = dp
            cfg.draft = d
            dirty = True
        if dirty:
            await db.flush()
    return cfg


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize naive datetimes (client-supplied ISO without timezone) to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_accessible(cfg: DocsConfig) -> bool:
    """Determine whether docs are publicly accessible right now."""
    if cfg.visibility == DocsVisibility.ON:
        return True
    if cfg.visibility == DocsVisibility.OFF:
        return False
    # SCHEDULED
    now = datetime.now(timezone.utc)
    start = _as_utc(cfg.schedule_start) or _DEFAULT_START
    end = _as_utc(cfg.schedule_end) or _DEFAULT_END
    return start <= now <= end


def _is_admin(user: Optional[User]) -> bool:
    """Admins can preview /docs even when the public window is closed."""
    return user is not None and user.role == UserRole.ADMIN


def _gate(cfg: DocsConfig, user: Optional[User]) -> None:
    """Raise 403 unless the docs are public right now or the viewer is admin."""
    if not _is_accessible(cfg) and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Documentation is not currently available.")


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
    # Duration-based publishing: give start + duration and the end is computed.
    duration_hours: Optional[float] = None


class DocsDraftUpdate(BaseModel):
    """Saved-but-not-live content overrides (see /draft and /publish)."""
    pitch_deck: Optional[Dict[str, Any]] = None
    tech_docs: Optional[Dict[str, Any]] = None
    changelog: Optional[List[Dict[str, Any]]] = None
    roadmap: Optional[Dict[str, Any]] = None
    custom_sections: Optional[List[Dict[str, Any]]] = None


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
async def get_content(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Return full docs content (pitch deck + tech docs + changelog + roadmap).
    Public but gated by access control; admins bypass the window.
    Null sections are filled from real defaults (never an empty page).
    """
    cfg = await _get_config(db)
    await db.commit()
    _gate(cfg, current_user)

    data = merged_content(cfg)
    data["updated_at"] = cfg.updated_at.isoformat() if cfg.updated_at else None
    return data


@router.get("/team")
async def get_team(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Return active team members, ordered by display_order. Admin bypasses the window."""
    cfg = await _get_config(db)
    await db.commit()
    _gate(cfg, current_user)

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
async def get_live_stats(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Aggregated live KPIs for the pitch deck traction slide.
    Public but gated by access control; admins bypass the window.
    """
    cfg = await _get_config(db)
    _gate(cfg, current_user)

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


@router.get("/architecture")
async def get_architecture(
    system: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Public interactive enterprise architecture explorer endpoint.
    Returns typed architecture topologies, nodes, connections, and execution steps
    grounded directly in the backend implementation.
    Pass ?system=chat|contract|lawyer|mcp for a specific subsystem.
    """
    cfg = await _get_config(db)
    await db.commit()
    _gate(cfg, current_user)

    systems = get_architecture_systems()
    if system:
        if system not in systems:
            raise HTTPException(
                status_code=404,
                detail=f"Subsystem '{system}' not found. Available systems: {list(systems.keys())}",
            )
        return {
            "status": "success",
            "version": "1.0.0",
            "system_id": system,
            "data": systems[system],
        }

    return {
        "status": "success",
        "version": "1.0.0",
        "available_systems": list(systems.keys()),
        "systems": systems,
    }


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
            cfg.schedule_start = _as_utc(datetime.fromisoformat(body.schedule_start))
        except ValueError:
            raise HTTPException(400, "Invalid schedule_start format. Use ISO 8601.")

    if body.schedule_end is not None:
        try:
            cfg.schedule_end = _as_utc(datetime.fromisoformat(body.schedule_end))
        except ValueError:
            raise HTTPException(400, "Invalid schedule_end format. Use ISO 8601.")

    # Duration-based publishing: end = start + duration (overrides a supplied end)
    if body.duration_hours is not None:
        if body.duration_hours <= 0:
            raise HTTPException(400, "duration_hours must be positive.")
        start = _as_utc(cfg.schedule_start)
        if start is None:
            raise HTTPException(400, "duration_hours requires schedule_start to be set.")
        cfg.schedule_end = start + timedelta(hours=body.duration_hours)

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

    # Version snapshot captures the state BEFORE the change (rollback semantics)
    _append_version(cfg, current_user)

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
    return {"message": "Documentation content updated.", "versions": len(cfg.content_versions or [])}


# ── Draft / Publish / Versioning ──

_LIVE_CONTENT_KEYS = ("pitch_deck", "tech_docs", "roadmap", "changelog", "custom_sections")
_MAX_VERSIONS = 10


def _snapshot_content(cfg: DocsConfig) -> Dict[str, Any]:
    return {key: getattr(cfg, key) for key in _LIVE_CONTENT_KEYS}


def _append_version(cfg: DocsConfig, user: Optional[User]) -> None:
    """Record a rolling snapshot (newest first, capped at _MAX_VERSIONS)."""
    versions = list(cfg.content_versions or [])
    versions.insert(0, {
        "at": datetime.now(timezone.utc).isoformat(),
        "by": (user.email if user and user.email else None)
              or (user.full_name if user else None),
        "snapshot": _snapshot_content(cfg),
    })
    cfg.content_versions = versions[:_MAX_VERSIONS]


@router.get("/draft")
async def get_draft(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: get the saved (not yet published) draft."""
    cfg = await _get_config(db)
    await db.commit()
    return {"draft": cfg.draft}


@router.put("/draft")
async def save_draft(
    body: DocsDraftUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: save content edits as a draft. Nothing goes live until /publish."""
    cfg = await _get_config(db)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()
    payload["saved_by"] = current_user.email or current_user.full_name or str(current_user.id)
    cfg.draft = payload
    await db.commit()
    return {"message": "Draft saved. Publish to make it live.", "saved_at": payload["saved_at"]}


@router.delete("/draft")
async def discard_draft(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: discard the saved draft (live content is untouched)."""
    cfg = await _get_config(db)
    cfg.draft = None
    await db.commit()
    return {"message": "Draft discarded."}


@router.post("/publish")
async def publish_draft(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: apply the saved draft to the live page and record a version."""
    cfg = await _get_config(db)
    draft = cfg.draft or {}
    if not any(draft.get(key) is not None for key in _LIVE_CONTENT_KEYS):
        raise HTTPException(400, "No draft saved. Save a draft first (PUT /api/docs/draft).")

    # Version snapshot captures the state BEFORE publishing (rollback semantics)
    _append_version(cfg, current_user)

    applied = []
    for key in _LIVE_CONTENT_KEYS:
        value = draft.get(key)
        if value is not None:
            setattr(cfg, key, value)
            applied.append(key)

    cfg.updated_by = current_user.email or current_user.full_name or str(current_user.id)
    version_count = len(cfg.content_versions or [])
    await db.commit()
    return {"message": "Draft published.", "applied": applied, "versions": version_count}


@router.get("/versions")
async def list_versions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: list content version snapshots (newest first)."""
    cfg = await _get_config(db)
    await db.commit()
    return [
        {"index": i, "at": v.get("at"), "by": v.get("by")}
        for i, v in enumerate(cfg.content_versions or [])
    ]


@router.post("/versions/{version_index}/restore")
async def restore_version(
    version_index: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Admin: roll the live content back to a stored version snapshot."""
    cfg = await _get_config(db)
    versions = cfg.content_versions or []
    if version_index < 0 or version_index >= len(versions):
        raise HTTPException(404, "Version not found.")

    snapshot = versions[version_index].get("snapshot") or {}

    # Version snapshot captures the state BEFORE restoring (rollback semantics).
    # Keys present in the snapshot are restored verbatim — including None —
    # so an override can be rolled back to "default content".
    _append_version(cfg, current_user)

    restored = []
    for key in _LIVE_CONTENT_KEYS:
        if key in snapshot:
            setattr(cfg, key, snapshot.get(key))
            restored.append(key)

    cfg.updated_by = current_user.email or current_user.full_name or str(current_user.id)
    await db.commit()
    return {"message": f"Restored version {version_index}.", "restored": restored}


# ──────────────────────────────────────────────────────────
# MARKDOWN EXPORT
# ──────────────────────────────────────────────────────────

@router.get("/export.md")
async def export_markdown(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Full /docs page as Markdown (same access gate as /content).
    Content-Type text/markdown so browsers download or render it directly.
    """
    from fastapi import Response

    cfg = await _get_config(db)
    await db.commit()
    _gate(cfg, current_user)

    content = merged_content(cfg)
    team_rows = await db.execute(
        select(DocsTeamMember)
        .where(DocsTeamMember.is_active == True)
        .order_by(DocsTeamMember.display_order, DocsTeamMember.id)
    )
    team = [
        {"full_name": m.full_name, "role": m.role, "email": m.email}
        for m in team_rows.scalars().all()
    ]
    stats = await get_live_stats(db=db, current_user=current_user)
    md = build_markdown(
        content,
        team,
        stats,
        {
            "visibility": cfg.visibility.value if hasattr(cfg.visibility, "value") else str(cfg.visibility),
            "schedule_start": cfg.schedule_start.isoformat() if cfg.schedule_start else None,
            "schedule_end": cfg.schedule_end.isoformat() if cfg.schedule_end else None,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
    )
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": "inline; filename=advotech-copilot-docs.md"},
    )


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
