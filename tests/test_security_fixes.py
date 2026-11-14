"""
Security fix regression tests (SEC-AUTH-01, SEC-AUTH-04, SEC-DOS-01, SEC-INJ-01).

Follows the test_payments_roles.py pattern: in-memory sqlite, direct endpoint
function calls, no HTTP layer.
"""

import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.background import BackgroundTasks
from starlette.datastructures import UploadFile

import app.models.audit  # noqa: F401
import app.models.chat  # noqa: F401
import app.models.lawyer  # noqa: F401
import app.models.memo  # noqa: F401
import app.models.playbook  # noqa: F401
import app.models.user  # noqa: F401

from app.api.auth import _check_otp_with_lockout, _send_and_store_otp
from app.api.lawyer import upload_lawyer_proof
from app.config import settings
from app.core.database import Base
from app.core.rate_limit import check_rate_limit
from app.core.tier_limits import get_tier_key
from app.core.uploads import read_upload_limited, verify_magic_bytes
from app.models.user import User, UserRole, SubscriptionTier


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [User.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def _make_user(**overrides) -> User:
    defaults = dict(
        email="sec-test@example.com",
        hashed_password="x",
        full_name="Sec Test",
        role=UserRole.GENERAL,
        subscription_tier=SubscriptionTier.FREE,
        is_active=True,
        is_verified=False,
    )
    defaults.update(overrides)
    return User(**defaults)


async def _seed_otp_user(db: AsyncSession, otp="123456") -> User:
    user = _make_user()
    user.otp_code = otp
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ── SEC-AUTH-01: OTP brute-force lockout ────────────────────────────────────

async def test_otp_lockout_after_max_attempts(async_db):
    user = await _seed_otp_user(async_db)
    max_attempts = settings.OTP_MAX_ATTEMPTS

    for attempt in range(1, max_attempts):
        with pytest.raises(HTTPException) as exc:
            await _check_otp_with_lockout(async_db, user, "000000")
        assert exc.value.status_code == 400
        assert "remaining" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        await _check_otp_with_lockout(async_db, user, "000000")
    assert exc.value.status_code == 423

    await async_db.refresh(user)
    assert user.otp_code is None
    assert user.otp_expires_at is None
    assert user.otp_locked_until is not None
    locked_until = user.otp_locked_until
    if locked_until.tzinfo is None:  # sqlite drops tzinfo; app code normalizes the same way
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    assert locked_until > datetime.now(timezone.utc)


async def test_otp_lockout_blocks_correct_otp(async_db):
    user = await _seed_otp_user(async_db, otp="654321")
    user.otp_failed_attempts = settings.OTP_MAX_ATTEMPTS - 1

    # Final wrong attempt triggers the lock
    with pytest.raises(HTTPException) as exc:
        await _check_otp_with_lockout(async_db, user, "000000")
    assert exc.value.status_code == 423

    # Even the CORRECT code is rejected while locked
    with pytest.raises(HTTPException) as exc:
        await _check_otp_with_lockout(async_db, user, "654321")
    assert exc.value.status_code == 423


async def test_correct_otp_resets_attempt_counter(async_db):
    user = await _seed_otp_user(async_db)
    user.otp_failed_attempts = 3
    await async_db.commit()

    await _check_otp_with_lockout(async_db, user, "123456")

    assert user.otp_failed_attempts == 0
    assert user.otp_locked_until is None


async def test_new_otp_send_resets_counter_and_lock(async_db):
    user = await _seed_otp_user(async_db)
    user.otp_failed_attempts = 4
    user.otp_locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    await async_db.commit()

    tasks = BackgroundTasks()
    await _send_and_store_otp(async_db, user, tasks)

    assert user.otp_failed_attempts == 0
    assert user.otp_locked_until is None
    assert user.otp_code is not None and len(user.otp_code) == 6


# ── SEC-AUTH-01: in-memory rate limiter ─────────────────────────────────────

def test_rate_limiter_blocks_after_max_requests():
    key = "unittest-window-a"
    check_rate_limit(key, max_requests=3, window_seconds=60)
    check_rate_limit(key, max_requests=3, window_seconds=60)
    check_rate_limit(key, max_requests=3, window_seconds=60)
    with pytest.raises(HTTPException) as exc:
        check_rate_limit(key, max_requests=3, window_seconds=60)
    assert exc.value.status_code == 429


def test_rate_limiter_keys_are_independent():
    check_rate_limit("unittest-window-b", max_requests=1, window_seconds=60)
    # Different key is not affected
    check_rate_limit("unittest-window-c", max_requests=1, window_seconds=60)


# ── SEC-DOS-01: bounded upload reads ────────────────────────────────────────

class _FakeUpload:
    """Minimal UploadFile stand-in with chunked async read."""

    def __init__(self, data: bytes, chunk: int = 1024 * 1024):
        self._data = data
        self._chunk = chunk
        self.size = len(data)
        self.filename = "fake.bin"

    async def read(self, n: int = -1) -> bytes:
        if n == -1 or n >= len(self._data):
            out, self._data = self._data, b""
            return out
        out, self._data = self._data[:n], self._data[n:]
        return out


async def test_read_upload_limited_rejects_oversize():
    big = _FakeUpload(b"x" * (1 * 1024 * 1024 + 10), chunk=1024 * 1024)
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(big, 1 * 1024 * 1024)
    assert exc.value.status_code == 413


async def test_read_upload_limited_rejects_declared_oversize_without_reading():
    huge = _FakeUpload(b"", chunk=1024)  # payload never read
    huge.size = 999 * 1024 * 1024
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(huge, 1 * 1024 * 1024)
    assert exc.value.status_code == 413


async def test_read_upload_limited_accepts_under_cap():
    small = _FakeUpload(b"hello contract", chunk=1024 * 1024)
    out = await read_upload_limited(small, 1 * 1024 * 1024)
    assert out == b"hello contract"


# ── SEC-INJ-01: magic bytes ─────────────────────────────────────────────────

def test_verify_magic_bytes_accepts_real_signatures():
    assert verify_magic_bytes(b"%PDF-1.7 fake pdf", ".pdf")
    assert verify_magic_bytes(b"\xFF\xD8\xFF\xE0 jpeg", ".jpg")
    assert verify_magic_bytes(b"\x89PNG\r\n\x1a\n rest", ".png")
    assert verify_magic_bytes(b"PK\x03\x04 docx zip", ".docx")
    assert verify_magic_bytes(b"plain text is fine", ".txt")  # no signature ext


def test_verify_magic_bytes_rejects_spoofed_files():
    assert not verify_magic_bytes(b"MZ\x90\x00 exe disguised as pdf", ".pdf")
    assert not verify_magic_bytes(b"#!/bin/sh\nrm -rf", ".png")
    assert not verify_magic_bytes(b"<html>not an image</html>", ".jpg")


# ── SEC-INJ-01: lawyer proof endpoint validation ────────────────────────────

class _FakeLawyer:
    id = 123


async def test_lawyer_proof_rejects_disallowed_extension():
    fake = UploadFile(filename="proof.exe", file=io.BytesIO(b"MZ\x90\x00"))
    with pytest.raises(HTTPException) as exc:
        await upload_lawyer_proof(file=fake, lawyer=_FakeLawyer())
    assert exc.value.status_code == 400
    assert "Unsupported proof format" in exc.value.detail


async def test_lawyer_proof_rejects_extension_spoofing():
    # .pdf name but exe content — must be rejected before any storage call
    fake = UploadFile(filename="proof.pdf", file=io.BytesIO(b"MZ\x90\x00 not a pdf"))
    with pytest.raises(HTTPException) as exc:
        await upload_lawyer_proof(file=fake, lawyer=_FakeLawyer())
    assert exc.value.status_code == 400
    assert "does not match" in exc.value.detail


# ── SEC-AUTH-04: hardcoded email backdoor removed ───────────────────────────

def test_demo_email_backdoor_removed():
    impostor = _make_user(email="mowesin210@backyr.com", role=UserRole.GENERAL)
    impostor.subscription_tier = SubscriptionTier.FREE
    assert get_tier_key(impostor) == "FREE"  # NOT "ADMIN"
