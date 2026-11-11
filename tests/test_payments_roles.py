from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure all SQLAlchemy relationships resolve cleanly
import app.models.audit  # noqa: F401
import app.models.chat  # noqa: F401
import app.models.lawyer  # noqa: F401
import app.models.memo  # noqa: F401
import app.models.playbook  # noqa: F401
import app.models.user  # noqa: F401

from app.api.payments import (
    InitiatePaymentRequest,
    _is_valid_bd_phone,
    _normalize_bd_phone,
    initiate_payment,
    list_available_packages,
)
from app.core.database import Base
from app.core.tier_limits import LAWYER_MONTHLY_FREE_CHAT_CREDITS
from app.core.security import CreditError, check_and_deduct_credits
from app.models.audit import AuditLog, Payment, PaymentPurpose
from app.models.lawyer import LawyerProfile, LawyerStatus
from app.models.playbook import (
    CreditPackage,
    CreditPackageTier,
    Enterprise,
    EnterpriseStatus,
)
from app.models.user import CreditTransaction, User, UserRole
from app.services.sslcommerz import (
    _sanitize_ssl_email,
    _sanitize_ssl_phone,
    _sanitize_ssl_text,
    sslcommerz_service,
)


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    tables = [
        Enterprise.__table__,
        User.__table__,
        CreditTransaction.__table__,
        LawyerProfile.__table__,
        CreditPackage.__table__,
        Payment.__table__,
        AuditLog.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def test_bd_phone_normalization_and_validation():
    assert _normalize_bd_phone("+8801712345678") == "01712345678"
    assert _normalize_bd_phone("00880 1812-345678") == "01812345678"
    assert _normalize_bd_phone("880 (19) 1234-5678") == "01912345678"
    assert _normalize_bd_phone("1712345678") == "01712345678"
    assert _normalize_bd_phone("  ") is None

    assert _is_valid_bd_phone(_normalize_bd_phone("+8801712345678")) is True
    assert _is_valid_bd_phone(_normalize_bd_phone("1812345678")) is True
    assert _is_valid_bd_phone("01212345678") is False
    assert _is_valid_bd_phone("12345") is False


def test_sslcommerz_payload_sanitizers():
    # Non-ASCII names fall back cleanly without sending garbled bytes
    assert _sanitize_ssl_text("রহিম উদ্দিন", "Customer", 50) == "Customer"
    assert _sanitize_ssl_text("  A.   Karim (Adv.)  ", "Customer", 50) == "A. Karim (Adv.)"
    assert len(_sanitize_ssl_text("X" * 80, "Customer", 50)) == 50

    # Long email (>50 chars) does not slice mid-domain; falls back cleanly
    long_email = "very.long.legal.department.username.exceeding.limit@organization.example.com"
    assert len(long_email) > 50
    assert _sanitize_ssl_email(long_email) == "billing@contractmind.ai"
    assert _sanitize_ssl_email("user@example.com") == "user@example.com"
    assert _sanitize_ssl_email("not-an-email") == "billing@contractmind.ai"

    # Phone normalizes country prefixes and rejects malformed short strings
    assert _sanitize_ssl_phone("+880 1712-345678") == "01712345678"
    assert _sanitize_ssl_phone("008801912345678") == "01912345678"
    assert _sanitize_ssl_phone("8801812345678") == "01812345678"
    assert _sanitize_ssl_phone("abc") == "01700000000"


async def test_initiate_payment_roles_and_packages(async_db: AsyncSession, monkeypatch):
    ent = Enterprise(
        name="Acme Legal Ltd",
        status=EnterpriseStatus.ACTIVE,
        referral_code="ACME-1234",
        credit_balance=100,
    )
    async_db.add(ent)
    await async_db.flush()

    general_user = User(
        full_name="General User",
        email="general@example.com",
        phone="01711111111",
        hashed_password="x",
        role=UserRole.GENERAL,
        is_active=True,
        is_verified=True,
    )
    lawyer_user = User(
        full_name="Verified Lawyer",
        email="lawyer@example.com",
        phone="+8801811111111",
        hashed_password="x",
        role=UserRole.LAWYER,
        is_active=True,
        is_verified=True,
    )
    ent_admin = User(
        full_name="Enterprise Admin",
        email="admin@acme.example.com",
        phone="01911111111",
        hashed_password="x",
        role=UserRole.ENTERPRISE_ADMIN,
        enterprise_id=ent.id,
        is_active=True,
        is_verified=True,
    )
    ent_member = User(
        full_name="Enterprise Member",
        email="member@acme.example.com",
        phone="01611111111",
        hashed_password="x",
        role=UserRole.ENTERPRISE_MEMBER,
        enterprise_id=ent.id,
        is_active=True,
        is_verified=True,
    )
    async_db.add_all([general_user, lawyer_user, ent_admin, ent_member])

    pkg_general = CreditPackage(
        name="Starter Pack",
        tier=CreditPackageTier.STARTER,
        price_bdt=299.00,
        base_credits=100,
        bonus_credits=10,
        applies_to="ALL",
        is_active=True,
    )
    pkg_general_only = CreditPackage(
        name="Personal Plus",
        tier=CreditPackageTier.STANDARD,
        price_bdt=499.00,
        base_credits=200,
        bonus_credits=20,
        applies_to="GENERAL",
        is_active=True,
    )
    pkg_enterprise = CreditPackage(
        name="Corporate Pack",
        tier=CreditPackageTier.ENTERPRISE,
        price_bdt=1999.00,
        base_credits=1000,
        bonus_credits=100,
        applies_to="ENTERPRISE",
        is_active=True,
    )
    async_db.add_all([pkg_general, pkg_general_only, pkg_enterprise])
    await async_db.commit()

    captured_calls = []

    async def fake_create_session(**kwargs):
        captured_calls.append(kwargs)
        return {
            "gateway_url": f"https://sandbox.sslcommerz.com/EasyCheckOut/{kwargs['tran_id']}",
            "session_key": f"SESS_{kwargs['tran_id']}",
        }

    monkeypatch.setattr(sslcommerz_service, "is_configured", lambda: True)
    monkeypatch.setattr(sslcommerz_service, "create_session", fake_create_session)

    # GENERAL sees ALL + GENERAL and can initiate ALL checkout
    gen_pkgs = await list_available_packages(current_user=general_user, db=async_db)
    assert {p.name for p in gen_pkgs} == {"Starter Pack", "Personal Plus"}
    gen_res = await initiate_payment(
        InitiatePaymentRequest(package_id=pkg_general.id),
        current_user=general_user,
        db=async_db,
    )
    assert gen_res["gateway_url"].startswith("https://sandbox.sslcommerz.com/")
    assert captured_calls[-1]["value_c"] == PaymentPurpose.CREDIT_TOPUP.value

    # LAWYER sees ALL + GENERAL and can initiate personal CREDIT_TOPUP
    law_pkgs = await list_available_packages(current_user=lawyer_user, db=async_db)
    assert {p.name for p in law_pkgs} == {"Starter Pack", "Personal Plus"}
    law_res = await initiate_payment(
        InitiatePaymentRequest(package_id=pkg_general_only.id),
        current_user=lawyer_user,
        db=async_db,
    )
    assert law_res["gateway_url"].startswith("https://sandbox.sslcommerz.com/")
    assert captured_calls[-1]["customer_phone"] == "01811111111"
    assert captured_calls[-1]["value_c"] == PaymentPurpose.CREDIT_TOPUP.value

    # ENTERPRISE_ADMIN sees ALL + ENTERPRISE and initiates ENTERPRISE_TOPUP
    adm_pkgs = await list_available_packages(current_user=ent_admin, db=async_db)
    assert {p.name for p in adm_pkgs} == {"Starter Pack", "Corporate Pack"}
    adm_res = await initiate_payment(
        InitiatePaymentRequest(package_id=pkg_enterprise.id),
        current_user=ent_admin,
        db=async_db,
    )
    assert adm_res["gateway_url"].startswith("https://sandbox.sslcommerz.com/")
    assert captured_calls[-1]["value_c"] == PaymentPurpose.ENTERPRISE_TOPUP.value

    # ENTERPRISE_MEMBER cannot purchase credits
    with pytest.raises(HTTPException) as member_exc:
        await initiate_payment(
            InitiatePaymentRequest(package_id=pkg_general.id),
            current_user=ent_member,
            db=async_db,
        )
    assert member_exc.value.status_code == 403

    # GENERAL and LAWYER cannot purchase ENTERPRISE-only package
    for user in (general_user, lawyer_user):
        with pytest.raises(HTTPException) as pkg_exc:
            await initiate_payment(
                InitiatePaymentRequest(package_id=pkg_enterprise.id),
                current_user=user,
                db=async_db,
            )
        assert pkg_exc.value.status_code == 400


async def test_lawyer_credit_deduction_priority(async_db: AsyncSession):
    now = datetime.now(timezone.utc)
    lawyer = User(
        full_name="Priority Lawyer",
        email="priority.lawyer@example.com",
        phone="01722222222",
        hashed_password="x",
        role=UserRole.LAWYER,
        credit_balance=3,
        monthly_free_chat_used=LAWYER_MONTHLY_FREE_CHAT_CREDITS - 2,
        monthly_free_chat_reset_at=now,
        is_active=True,
        is_verified=True,
    )
    async_db.add(lawyer)
    await async_db.flush()

    profile = LawyerProfile(
        user_id=lawyer.id,
        bar_council_id="BAR-TEST-001",
        status=LawyerStatus.APPROVED,
        wallet_balance=Decimal("10.00"),
    )
    async_db.add(profile)
    await async_db.commit()

    # 1. Free monthly pool covers cost=2
    r1 = await check_and_deduct_credits(lawyer, async_db, cost=2, tx_type="CHAT_QUERY")
    assert r1["source"] == "monthly_pool"
    await async_db.refresh(lawyer)
    assert lawyer.monthly_free_chat_used == LAWYER_MONTHLY_FREE_CHAT_CREDITS
    assert lawyer.credit_balance == 3

    # 2. Monthly pool exhausted -> deducts purchased credit_balance before wallet_balance
    r2 = await check_and_deduct_credits(lawyer, async_db, cost=2, tx_type="CHAT_QUERY")
    assert r2["source"] == "credit_balance"
    await async_db.refresh(lawyer)
    await async_db.refresh(profile)
    assert lawyer.credit_balance == 1
    assert profile.wallet_balance == Decimal("10.00")

    # 3. Remaining credit_balance (1) + wallet_balance (10) covers cost=4
    r3 = await check_and_deduct_credits(lawyer, async_db, cost=4, tx_type="CHAT_QUERY")
    assert r3["source"] == "monthly_pool+wallet"
    await async_db.refresh(lawyer)
    await async_db.refresh(profile)
    assert lawyer.credit_balance == 0
    assert profile.wallet_balance == Decimal("7.00")

    # 4. Over total available -> CreditError
    with pytest.raises(CreditError):
        await check_and_deduct_credits(lawyer, async_db, cost=10, tx_type="CHAT_QUERY")
