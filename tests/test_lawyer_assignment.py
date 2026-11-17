"""
Regression tests for the ForeignKeyViolationError on contract_memos.hitl_current_offer_id.

Root cause: assign_next_offer() set hitl_current_offer_id from the cached JSON
ranked list without checking that the lawyer still exists / is APPROVED. If the
lawyer had been deleted or unapproved after ranking, the UPDATE violated the FK
to lawyer_profiles.

Fix: candidates are validated against live lawyer_profiles rows (APPROVED,
not ON_LEAVE) before being offered; stale entries are marked SKIPPED.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import attributes

import app.models.lawyer  # noqa: F401
import app.models.memo  # noqa: F401
import app.models.user  # noqa: F401

from app.core.database import Base
from app.models.lawyer import LawyerProfile, LawyerStatus, LawyerAvailability
from app.models.memo import Contract, ContractMemo, OfferStatus
from app.models.user import User, UserRole
import app.services.lawyer_assignment as la


@pytest.fixture
async def async_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        User.__table__,
        LawyerProfile.__table__,
        Contract.__table__,
        ContractMemo.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture(autouse=True)
def _mute_email(monkeypatch):
    """assign_next_offer sends an offer email after commit — keep tests offline."""
    async def _noop(email, title, payout):
        return None

    monkeypatch.setattr(la, "send_lawyer_offer_email", _noop)


async def _seed(db: AsyncSession) -> tuple[ContractMemo, int]:
    """Create one client, one live APPROVED lawyer, one contract + memo."""
    client = User(
        full_name="Client", email="client@example.com", phone="01711111111",
        hashed_password="x", role=UserRole.GENERAL, is_active=True, is_verified=True,
    )
    db.add(client)
    await db.flush()

    lawyer_user = User(
        full_name="Live Lawyer", email="live@example.com", phone="01811111111",
        hashed_password="x", role=UserRole.LAWYER, is_active=True, is_verified=True,
    )
    db.add(lawyer_user)
    await db.flush()

    live_lawyer = LawyerProfile(
        user_id=lawyer_user.id,
        bar_council_id="BC-LIVE-1",
        status=LawyerStatus.APPROVED,
        availability=LawyerAvailability.AVAILABLE,
    )
    db.add(live_lawyer)
    await db.flush()

    contract = Contract(
        user_id=client.id, title="NDA Review", file_path="contracts/nda.pdf",
    )
    db.add(contract)
    await db.flush()

    memo = ContractMemo(contract_id=contract.id)
    db.add(memo)
    await db.flush()
    return memo, live_lawyer.id


async def test_stale_lawyer_is_skipped_next_eligible_offered(async_db):
    memo, live_id = await _seed(async_db)
    # Ghost lawyer id 9999 was ranked first but no longer exists in lawyer_profiles.
    memo.hitl_ranked_lawyers = [
        {"lawyer_id": 9999, "score": 0.95, "status": OfferStatus.PENDING.value,
         "offered_at": None, "expires_at": None},
        {"lawyer_id": live_id, "score": 0.80, "status": OfferStatus.PENDING.value,
         "offered_at": None, "expires_at": None},
    ]

    success = await la.assign_next_offer(async_db, memo)

    assert success is True
    assert memo.hitl_current_offer_id == live_id
    assert memo.hitl_status.value == "OFFERED"
    by_id = {item["lawyer_id"]: item for item in memo.hitl_ranked_lawyers}
    assert by_id[9999]["status"] == OfferStatus.SKIPPED.value
    assert by_id[live_id]["status"] == OfferStatus.ACTIVE.value
    assert by_id[live_id]["offered_at"] is not None
    assert by_id[live_id]["expires_at"] is not None


async def test_unapproved_lawyer_is_skipped(async_db):
    memo, live_id = await _seed(async_db)
    # Suspend the live lawyer so the only other ranked candidate is ineligible.
    lawyer_profile = await async_db.get(LawyerProfile, live_id)
    lawyer_profile.status = LawyerStatus.SUSPENDED
    await async_db.flush()

    memo.hitl_ranked_lawyers = [
        {"lawyer_id": live_id, "score": 0.90, "status": OfferStatus.PENDING.value,
         "offered_at": None, "expires_at": None},
    ]

    success = await la.assign_next_offer(async_db, memo)

    assert success is False
    assert memo.hitl_current_offer_id is None
    assert memo.hitl_status.value == "QUEUED_FOR_LAWYER"
    assert memo.hitl_ranked_lawyers[0]["status"] == OfferStatus.SKIPPED.value


async def test_on_leave_lawyer_is_skipped(async_db):
    memo, live_id = await _seed(async_db)
    lawyer_profile = await async_db.get(LawyerProfile, live_id)
    lawyer_profile.availability = LawyerAvailability.ON_LEAVE
    await async_db.flush()

    memo.hitl_ranked_lawyers = [
        {"lawyer_id": live_id, "score": 0.90, "status": OfferStatus.PENDING.value,
         "offered_at": None, "expires_at": None},
    ]

    success = await la.assign_next_offer(async_db, memo)

    assert success is False
    assert memo.hitl_current_offer_id is None
    assert memo.hitl_status.value == "QUEUED_FOR_LAWYER"


async def test_expired_offer_to_live_lawyer_is_reoffered(async_db):
    memo, live_id = await _seed(async_db)
    now = datetime.now(timezone.utc)
    memo.hitl_ranked_lawyers = [
        {"lawyer_id": live_id, "score": 0.90, "status": OfferStatus.EXPIRED.value,
         "offered_at": now.isoformat(), "expires_at": now.isoformat()},
    ]
    memo.hitl_status = "OFFERED"
    memo.hitl_current_offer_id = live_id
    await async_db.flush()

    success = await la.assign_next_offer(async_db, memo)

    assert success is True
    assert memo.hitl_current_offer_id == live_id
    assert memo.hitl_ranked_lawyers[0]["status"] == OfferStatus.ACTIVE.value


async def test_active_entry_needs_no_revalidation(async_db):
    """The ACTIVE entry (current offer) is never re-picked — only pickable statuses cycle."""
    memo, live_id = await _seed(async_db)
    memo.hitl_ranked_lawyers = [
        {"lawyer_id": live_id, "score": 0.90, "status": OfferStatus.ACTIVE.value,
         "offered_at": datetime.now(timezone.utc).isoformat(), "expires_at": None},
    ]

    success = await la.assign_next_offer(async_db, memo)

    assert success is False
    assert memo.hitl_status.value == "QUEUED_FOR_LAWYER"
    assert memo.hitl_current_offer_id is None
    assert memo.hitl_ranked_lawyers[0]["status"] == OfferStatus.ACTIVE.value
