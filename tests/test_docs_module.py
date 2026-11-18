"""
Tests for the live /docs module (docs API + HTML page + export):

- Access gating: ON / OFF / SCHEDULED windows (incl. naive-datetime normalization)
- Admin bypass: admins preview content/team/stats even when the window is closed
- Content defaults: merged_content never returns an empty page
- Team CRUD: add → list → update → delete
- Markdown export: gated + real content
- GET /docs HTML page: 200 when open, 403 "Not Available" when closed,
  admin-token preview when closed

Follows the test_solve_batch.py pattern: in-memory sqlite, direct function
calls, no HTTP layer.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from jose import jwt

import app.models.user  # noqa: F401
from app.api import docs as docs_api
from app.api import docs_page
from app.config import settings
from app.core.database import Base
from app.models.docs_config import DocsConfig, DocsTeamMember, DocsVisibility
from app.models.user import User, UserRole

from app.api.docs import (
    DocsConfigUpdate,
    TeamMemberCreate,
    TeamMemberUpdate,
)


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        DocsConfig.__table__,
        DocsTeamMember.__table__,
        User.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def make_admin(db: AsyncSession, email: str = "admin@advotech.dev") -> User:
    admin = User(
        email=email,
        full_name="Docs Admin",
        hashed_password="x",
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin


def admin_token(user: User) -> str:
    return jwt.encode(
        {"sub": user.email, "tv": int(getattr(user, "token_version", 1) or 1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


# ── Config singleton & visibility ──

@pytest.mark.asyncio
async def test_get_config_creates_singleton_with_defaults(async_db):
    cfg = await docs_api._get_config(async_db)
    await async_db.commit()
    assert cfg.id == 1
    # Public by default — everyone can see /docs at all times; admins get
    # full edit access on top of that.
    assert cfg.visibility == DocsVisibility.ON
    assert cfg.schedule_start is not None and cfg.schedule_end is not None

    # Second call returns the same row (no duplicates)
    again = await docs_api._get_config(async_db)
    assert again.id == cfg.id


@pytest.mark.asyncio
async def test_is_accessible_modes(async_db):
    cfg = await docs_api._get_config(async_db)
    await async_db.commit()

    cfg.visibility = DocsVisibility.ON
    assert docs_api._is_accessible(cfg) is True

    cfg.visibility = DocsVisibility.OFF
    assert docs_api._is_accessible(cfg) is False

    # SCHEDULED: window containing now (naive datetimes must not crash)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cfg.visibility = DocsVisibility.SCHEDULED
    cfg.schedule_start = now - timedelta(hours=1)
    cfg.schedule_end = now + timedelta(hours=1)
    assert docs_api._is_accessible(cfg) is True

    # Past window
    cfg.schedule_start = now - timedelta(days=2)
    cfg.schedule_end = now - timedelta(days=1)
    assert docs_api._is_accessible(cfg) is False


# ── Content gate + defaults ──

@pytest.mark.asyncio
async def test_content_closed_window_rejects_public(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()

    with pytest.raises(HTTPException) as exc:
        await docs_api.get_content(db=async_db, current_user=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_content_admin_bypasses_closed_window(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()
    admin = await make_admin(async_db)

    data = await docs_api.get_content(db=async_db, current_user=admin)
    # Defaults merged in — never an empty page
    assert data["pitch_deck"]["company"]
    assert data["tech_docs"]["feature_matrix"]
    assert data["changelog"]
    assert data["roadmap"]


@pytest.mark.asyncio
async def test_team_and_stats_closed_window_reject_public(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()

    for coro in (
        docs_api.get_team(db=async_db, current_user=None),
        docs_api.get_live_stats(db=async_db, current_user=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await coro
        assert exc.value.status_code == 403


# ── Config update (schedule normalization) ──

@pytest.mark.asyncio
async def test_update_config_visibility_and_naive_schedule(async_db):
    cfg = await docs_api._get_config(async_db)
    await async_db.commit()
    admin = await make_admin(async_db)

    naive_local = "2026-06-10T00:00:00"  # no timezone — must not crash comparison
    resp = await docs_api.update_config(
        DocsConfigUpdate(visibility="ON", schedule_start=naive_local, schedule_end="2026-06-14T23:59:59"),
        db=async_db,
        current_user=admin,
    )
    assert resp["visibility"] == DocsVisibility.ON
    assert resp["is_accessible_now"] is True

    with pytest.raises(HTTPException) as exc:
        await docs_api.update_config(
            DocsConfigUpdate(visibility="BOGUS"), db=async_db, current_user=admin
        )
    assert exc.value.status_code == 400


# ── Team CRUD ──

@pytest.mark.asyncio
async def test_team_crud(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.ON
    await async_db.commit()
    admin = await make_admin(async_db)

    created = await docs_api.add_team_member(
        TeamMemberCreate(full_name="Mahmudul Hasan Tasin", role="Founder & Engineer",
                         email="tasin@advotech.dev", display_order=1),
        db=async_db,
        current_user=admin,
    )
    member_id = created["id"]

    await docs_api.update_team_member(
        member_id, TeamMemberUpdate(role="Founder & Lead Engineer"),
        db=async_db, current_user=admin,
    )

    admin_list = await docs_api.get_admin_team(db=async_db, current_user=admin)
    assert len(admin_list) == 1
    assert admin_list[0]["role"] == "Founder & Lead Engineer"

    public_list = await docs_api.get_team(db=async_db, current_user=None)
    assert public_list[0]["full_name"] == "Mahmudul Hasan Tasin"

    await docs_api.delete_team_member(member_id, db=async_db, current_user=admin)
    admin_list = await docs_api.get_admin_team(db=async_db, current_user=admin)
    assert admin_list == []

    with pytest.raises(HTTPException) as exc:
        await docs_api.delete_team_member(9999, db=async_db, current_user=admin)
    assert exc.value.status_code == 404


# ── Markdown export ──

@pytest.mark.asyncio
async def test_export_markdown_gated_and_real(async_db):
    cfg = await docs_api._get_config(async_db)
    await async_db.commit()

    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()
    with pytest.raises(HTTPException) as exc:
        await docs_api.export_markdown(db=async_db, current_user=None)
    assert exc.value.status_code == 403

    cfg.visibility = DocsVisibility.ON
    await async_db.commit()
    response = await docs_api.export_markdown(db=async_db, current_user=None)
    body = response.body.decode("utf-8")
    assert ("Advotech Copilot" in body or "ContractMind" in body)
    assert "## Team" in body
    assert "Live System Stats" in body


# ── Duration-based publishing ──

@pytest.mark.asyncio
async def test_duration_based_publishing(async_db):
    cfg = await docs_api._get_config(async_db)
    await async_db.commit()
    admin = await make_admin(async_db)

    start = datetime.now(timezone.utc) - timedelta(hours=1)
    resp = await docs_api.update_config(
        DocsConfigUpdate(
            visibility="SCHEDULED",
            schedule_start=start.isoformat(),
            duration_hours=48.0,
        ),
        db=async_db,
        current_user=admin,
    )
    # end = start + 48h (overrides any supplied end)
    assert resp["schedule_end"] is not None
    delta = docs_api._as_utc(datetime.fromisoformat(resp["schedule_end"])) - docs_api._as_utc(start)
    assert abs(delta.total_seconds() - 48 * 3600) < 5
    assert resp["is_accessible_now"] is True

    with pytest.raises(HTTPException) as exc:
        await docs_api.update_config(
            DocsConfigUpdate(duration_hours=-2), db=async_db, current_user=admin
        )
    assert exc.value.status_code == 400


# ── Draft / Publish / Versioning ──

@pytest.mark.asyncio
async def test_draft_publish_and_versions(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.ON
    await async_db.commit()
    admin = await make_admin(async_db)

    # No draft yet → publish refuses
    with pytest.raises(HTTPException) as exc:
        await docs_api.publish_draft(db=async_db, current_user=admin)
    assert exc.value.status_code == 400

    # Save draft → not live yet
    await docs_api.save_draft(
        docs_api.DocsDraftUpdate(pitch_deck={"tagline": "DRAFT TAGLINE"}),
        db=async_db, current_user=admin,
    )
    data = await docs_api.get_content(db=async_db, current_user=admin)
    assert data["pitch_deck"]["tagline"] != "DRAFT TAGLINE"  # defaults still live

    draft_resp = await docs_api.get_draft(db=async_db, current_user=admin)
    assert draft_resp["draft"]["pitch_deck"]["tagline"] == "DRAFT TAGLINE"

    # Publish → live + version recorded
    pub = await docs_api.publish_draft(db=async_db, current_user=admin)
    assert "pitch_deck" in pub["applied"]
    data = await docs_api.get_content(db=async_db, current_user=admin)
    assert data["pitch_deck"]["tagline"] == "DRAFT TAGLINE"

    versions = await docs_api.list_versions(db=async_db, current_user=admin)
    assert len(versions) == 1

    # Restore version 0 → pre-publish state (merged defaults come back)
    await docs_api.restore_version(0, db=async_db, current_user=admin)
    data = await docs_api.get_content(db=async_db, current_user=admin)
    assert data["pitch_deck"]["tagline"] != "DRAFT TAGLINE"
    versions = await docs_api.list_versions(db=async_db, current_user=admin)
    assert len(versions) == 2  # restore itself is recorded

    # Discard draft clears it without touching live content
    await docs_api.discard_draft(db=async_db, current_user=admin)
    draft_resp = await docs_api.get_draft(db=async_db, current_user=admin)
    assert draft_resp["draft"] is None

    with pytest.raises(HTTPException):
        await docs_api.restore_version(99, db=async_db, current_user=admin)


@pytest.mark.asyncio
async def test_version_list_is_capped(async_db):
    cfg = await docs_api._get_config(async_db)
    await async_db.commit()
    admin = await make_admin(async_db)

    for i in range(12):
        await docs_api.save_draft(
            docs_api.DocsDraftUpdate(roadmap={"short_term": [f"item-{i}"]}),
            db=async_db, current_user=admin,
        )
        await docs_api.publish_draft(db=async_db, current_user=admin)

    versions = await docs_api.list_versions(db=async_db, current_user=admin)
    assert len(versions) == 10
    assert versions[0]["index"] == 0

    await docs_api.restore_version(0, db=async_db, current_user=admin)
    data = await docs_api.get_content(db=async_db, current_user=admin)
    # Version 0 = state BEFORE the last publish → item-10 (item-11 was just published)
    assert data["roadmap"]["short_term"] == ["item-10"]


# ── HTML page ──

@pytest.mark.asyncio
async def test_docs_page_open_window(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.ON
    await async_db.commit()

    resp = await docs_page.docs_page(request=None, token=None, db=async_db)
    assert resp.status_code == 200
    assert (b"Advotech Copilot" in resp.body or b"ContractMind" in resp.body)


@pytest.mark.asyncio
async def test_docs_page_closed_window_shows_not_available(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()

    resp = await docs_page.docs_page(request=None, token=None, db=async_db)
    assert resp.status_code == 403
    assert b"Not Available" in resp.body


@pytest.mark.asyncio
async def test_docs_page_admin_token_previews_closed_window(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()
    admin = await make_admin(async_db)

    resp = await docs_page.docs_page(request=None, token=admin_token(admin), db=async_db)
    assert resp.status_code == 200
    assert (b"Advotech Copilot" in resp.body or b"ContractMind" in resp.body)


@pytest.mark.asyncio
async def test_docs_page_rejects_non_admin_token(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.OFF
    await async_db.commit()
    member = User(
        email="gen@advotech.dev",
        full_name="General User",
        hashed_password="x",
        role=UserRole.GENERAL,
        is_active=True,
        is_verified=True,
    )
    async_db.add(member)
    await async_db.commit()

    resp = await docs_page.docs_page(request=None, token=admin_token(member), db=async_db)
    assert resp.status_code == 403


# ── Interactive Architecture Explorer Tests ──

@pytest.mark.asyncio
async def test_architecture_endpoint_all_systems(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.ON
    await async_db.commit()

    resp = await docs_api.get_architecture(system=None, db=async_db, current_user=None)
    assert resp["status"] == "success"
    assert "systems" in resp
    assert set(resp["available_systems"]) == {"chat", "contract", "lawyer", "mcp"}

    # Verify each system contains real nodes, connections, execution steps and metrics
    for sys_id in ("chat", "contract", "lawyer", "mcp"):
        sys_data = resp["systems"][sys_id]
        assert "nodes" in sys_data
        assert "connections" in sys_data
        assert "execution_steps" in sys_data
        assert "metrics" in sys_data
        assert len(sys_data["nodes"]) >= 5
        assert len(sys_data["execution_steps"]) >= 5


@pytest.mark.asyncio
async def test_architecture_endpoint_single_system(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.ON
    await async_db.commit()

    resp = await docs_api.get_architecture(system="contract", db=async_db, current_user=None)
    assert resp["status"] == "success"
    assert resp["system_id"] == "contract"
    assert resp["data"]["system_id"] == "contract"
    assert "Contract Audit Pipeline" in resp["data"]["title"]


@pytest.mark.asyncio
async def test_architecture_endpoint_invalid_system(async_db):
    cfg = await docs_api._get_config(async_db)
    cfg.visibility = DocsVisibility.ON
    await async_db.commit()

    with pytest.raises(HTTPException) as exc:
        await docs_api.get_architecture(system="unknown_subsystem", db=async_db, current_user=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_architecture_merged_content_has_systems(async_db):
    cfg = await docs_api._get_config(async_db)
    content = docs_api.merged_content(cfg)
    assert "architecture_systems" in content
    assert "chat" in content["architecture_systems"]
    assert "contract" in content["architecture_systems"]
    assert "lawyer" in content["architecture_systems"]
    assert "mcp" in content["architecture_systems"]
