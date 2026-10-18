import pytest
import pytest_asyncio
import uuid  # <-- নতুন যুক্ত করা হয়েছে (ইউনিক ইমেইলের জন্য)
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.models.user import User, UserRole, SubscriptionTier
from app.core.security import get_current_user

# টেস্টের জন্য ইন-মেমোরি SQLite ডাটাবেস ব্যবহার করা হবে
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    # সব টেবিল তৈরি করা
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # টেস্ট শেষে টেবিল ড্রপ করা
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def db_session():
    async with TestingSessionLocal() as session:
        yield session

@pytest_asyncio.fixture
async def client(db_session):
    # ডাটাবেস ডিপেন্ডেন্সি ওভাররাইড করা
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    
    # টেস্ট ক্লায়েন্ট তৈরি
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
        
    app.dependency_overrides.clear()

@pytest_asyncio.fixture
async def mock_general_user(db_session):
    """একটি ডামি জেনারেল ইউজার তৈরি করে, যাতে টেস্টের সময় বারবার রেজিস্টার করতে না হয়"""
    # ✅ প্রতিবার নতুন ইউনিক ইমেইল তৈরি হবে, যাতে UNIQUE constraint না ভাঙে
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    
    user = User(
        email=unique_email,
        hashed_password="fakehash",
        full_name="Test User",
        role=UserRole.GENERAL,
        subscription_tier=SubscriptionTier.FREE,
        credit_balance=100,
        is_active=True,
        is_verified=True
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user

@pytest_asyncio.fixture
async def authenticated_client(client, mock_general_user):
    """লগইন ছাড়াই সরাসরি টোকেন বাইপাস করে লগইন করা ইউজার তৈরি করে (Mock Auth)"""
    async def override_get_current_user():
        return mock_general_user
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield client
    app.dependency_overrides.pop(get_current_user, None)