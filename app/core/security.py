from datetime import datetime, timedelta, timezone
from typing import List, Optional, TYPE_CHECKING
import re
import logging

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.tier_limits import (
    get_tier_key,
    get_session_limit,
    LAWYER_MONTHLY_FREE_CHAT_CREDITS,
)

# ⚠️ সার্কুলার ইমপোর্ট এড়াতে TYPE_CHECKING ব্যবহার করা হলো
if TYPE_CHECKING:
    from app.models.user import User, Enterprise, UserRole, SubscriptionTier, CreditTransaction, CreditTxType, MemberApprovalStatus
    from app.models.playbook import Enterprise

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer()


# ──────────────────────────────────────────────────────────
# PASSWORD HASHING
# ──────────────────────────────────────────────────────────

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        plain_bytes = plain_password.encode("utf-8")[:72]
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


hash_password = get_password_hash


# ──────────────────────────────────────────────────────────
# JWT
# ──────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now_utc = datetime.now(timezone.utc)
    expire = now_utc + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "iat": now_utc})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def build_token_payload(user: "User") -> dict:
    """Centralized JWT payload builder."""
    return {
        "sub": user.email,
        "id": user.id,
        "role": user.role.value,
        "tier": user.subscription_tier.value,
        "ent_id": user.enterprise_id,
        "ver": user.is_verified,
    }


# ──────────────────────────────────────────────────────────
# CURRENT USER & RBAC
# ──────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> "User":
    from app.models.user import User  # <-- লোকাল ইমপোর্ট

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    stmt = select(User).where(User.email == email, User.is_deleted == False)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user account.")

    return user


def require_roles(allowed_roles: list):
    """RBAC dependency factory."""
    async def role_checker(current_user: "User" = Depends(get_current_user)) -> "User":
        user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
        allowed = [r.value if hasattr(r, "value") else str(r) for r in allowed_roles]
        if user_role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role(s): {allowed}",
            )
        return current_user
    return role_checker


def require_verified_enterprise_member():
    """Ensures the enterprise member is APPROVED by their company admin."""
    async def checker(current_user: "User" = Depends(get_current_user)) -> "User":
        from app.models.user import UserRole, MemberApprovalStatus # <-- লোকাল ইমপোর্ট
        
        if current_user.role == UserRole.ENTERPRISE_MEMBER:
            if current_user.member_approval_status != MemberApprovalStatus.APPROVED:
                raise HTTPException(
                    status_code=403,
                    detail=f"Membership status: {current_user.member_approval_status.value if current_user.member_approval_status else 'PENDING'}. Awaiting company admin approval.",
                )
        return current_user
    return checker


# ──────────────────────────────────────────────────────────
# HELPERS: ROLE CHECKS
# ──────────────────────────────────────────────────────────

def is_admin(user: "User") -> bool:
    from app.models.user import UserRole
    return user.role == UserRole.ADMIN

def is_lawyer(user: "User") -> bool:
    from app.models.user import UserRole
    return user.role == UserRole.LAWYER

def is_enterprise_user(user: "User") -> bool:
    from app.models.user import UserRole
    return user.role in (UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER)

def is_enterprise_admin(user: "User") -> bool:
    from app.models.user import UserRole
    return user.role == UserRole.ENTERPRISE_ADMIN


# ──────────────────────────────────────────────────────────
# LANGUAGE DETECTION
# ──────────────────────────────────────────────────────────

_BENGALI_RANGE = re.compile(r"[\u0980-\u09FF]")

_BANGLISH_MARKERS = {
    "ami", "tumi", "she", "se", "apni", "chakri", "taka", "kaj",
    "bhai", "ki", "keno", "kothay", "korbo", "hobe", "diye",
    "theke", "kotha", "bolo", "bolun", "hoy", "hoyni", "korle",
    "korte", "kore", "kora", "chai", "lage", "lagbe", "dorkar",
}


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"

    bengali_chars = len(_BENGALI_RANGE.findall(text))
    total_chars = len(text)

    if bengali_chars > total_chars * 0.3:
        return "bn"
    if bengali_chars > 0:
        return "bn"

    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return "en"
    hits = sum(1 for w in words if w in _BANGLISH_MARKERS)
    if hits / len(words) >= 0.2:
        return "banglish"

    return "en"


def should_reply_in_bengali(detected_lang: str) -> bool:
    return detected_lang in ("bn", "banglish")


# ──────────────────────────────────────────────────────────
# CREDIT DEDUCTION (ROW-LOCK SAFE)
# ──────────────────────────────────────────────────────────

class CreditError(HTTPException):
    def __init__(self, detail: str, code: int = 402):
        super().__init__(status_code=code, detail=detail)


async def _lock_user(db: AsyncSession, user_id: int) -> "User":
    from app.models.user import User
    stmt = select(User).where(User.id == user_id).with_for_update()
    return (await db.execute(stmt)).scalar_one()


async def _lock_enterprise(db: AsyncSession, enterprise_id: int) -> "Enterprise":
    from app.models.playbook import Enterprise
    stmt = select(Enterprise).where(Enterprise.id == enterprise_id).with_for_update()
    return (await db.execute(stmt)).scalar_one()


def _reset_lawyer_monthly_pool_if_needed(user: "User") -> None:
    now = datetime.now(timezone.utc)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if (
        user.monthly_free_chat_reset_at is None
        or user.monthly_free_chat_reset_at < first_of_month
    ):
        user.monthly_free_chat_used = 0
        user.monthly_free_chat_reset_at = first_of_month


async def check_and_deduct_credits(
    user: "User",
    db: AsyncSession,
    cost: int = 1,
    tx_type: str = "CHAT_QUERY",
    description: str = "Query consumption",
    reference_id: Optional[str] = None,
    *,
    chat_mode: Optional[str] = None,
    commit: bool = True,
) -> dict:
    from app.models.user import UserRole, CreditTransaction, CreditTxType
    from app.models.lawyer import LawyerProfile

    if cost <= 0:
        return {"source": "free", "deducted": 0, "remaining": 0}

    if user.role == UserRole.ADMIN:
        return {"source": "free", "deducted": 0, "remaining": -1}

    if user.role == UserRole.LAWYER and chat_mode == "CONTRACT_REVIEW":
        return {"source": "free", "deducted": 0, "remaining": -1}

    if user.role == UserRole.LAWYER:
        locked_user = await _lock_user(db, user.id)
        _reset_lawyer_monthly_pool_if_needed(locked_user)

        remaining_pool = LAWYER_MONTHLY_FREE_CHAT_CREDITS - locked_user.monthly_free_chat_used
        if remaining_pool >= cost:
            locked_user.monthly_free_chat_used += cost
            if commit:
                await db.commit()
            return {"source": "monthly_pool", "deducted": cost, "remaining": remaining_pool - cost}

        from_pool = remaining_pool
        from_wallet = cost - from_pool
        locked_user.monthly_free_chat_used += from_pool

        prof_stmt = select(LawyerProfile).where(LawyerProfile.user_id == user.id).with_for_update()
        profile = (await db.execute(prof_stmt)).scalar_one_or_none()
        if not profile:
            raise CreditError("Lawyer profile missing.")

        wallet_credits = int(profile.wallet_balance)
        if wallet_credits < from_wallet:
            raise CreditError(f"Insufficient credits. Need {cost}, have {remaining_pool} free + {wallet_credits} wallet.")
        profile.wallet_balance -= from_wallet

        db.add(CreditTransaction(
            user_id=user.id, amount=-cost, balance_after=wallet_credits - from_wallet,
            tx_type=CreditTxType(tx_type), description=description or "Lawyer chat beyond free pool", reference_id=reference_id,
        ))
        if commit:
            await db.commit()
        return {"source": "monthly_pool+wallet", "deducted": cost, "remaining": 0}

    if user.role in (UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER):
        if not user.enterprise_id:
            raise CreditError("User has no enterprise association.")

        enterprise = await _lock_enterprise(db, user.enterprise_id)
        if enterprise.status.value != "ACTIVE":
            raise CreditError("Enterprise is not active.")

        if enterprise.credit_balance < cost:
            raise CreditError(f"Company credit pool exhausted. Balance: {enterprise.credit_balance}.")

        if user.role == UserRole.ENTERPRISE_MEMBER:
            locked_user = await _lock_user(db, user.id)
            used = locked_user.member_credit_used or 0
            quota = locked_user.member_credit_quota or 0
            if quota > 0 and (used + cost) > quota:
                raise CreditError(f"Your allocated quota is exhausted ({used}/{quota}). Ask your admin for more.")
            locked_user.member_credit_used = used + cost

        enterprise.credit_balance -= cost
        db.add(CreditTransaction(
            enterprise_id=enterprise.id, user_id=user.id, amount=-cost, balance_after=enterprise.credit_balance,
            tx_type=CreditTxType(tx_type), description=description, reference_id=reference_id,
        ))
        if commit:
            await db.commit()
        return {"source": "enterprise_pool", "deducted": cost, "remaining": enterprise.credit_balance}

    locked_user = await _lock_user(db, user.id)
    if locked_user.credit_balance < cost:
        raise CreditError(f"Insufficient credits. Need {cost}, have {locked_user.credit_balance}.")
    locked_user.credit_balance -= cost
    db.add(CreditTransaction(
        user_id=user.id, amount=-cost, balance_after=locked_user.credit_balance,
        tx_type=CreditTxType(tx_type), description=description, reference_id=reference_id,
    ))
    if commit:
        await db.commit()

    # ✅ FIX: await db.refresh(user) লাইনটি মুছে ফেলা হয়েছে যাতে মেমোরির ডাটা ডাটাবেসের পুরনো ডাটা দ্বারা ওভাররাইট না হয়।
    return {"source": "wallet", "deducted": cost, "remaining": locked_user.credit_balance}


# ──────────────────────────────────────────────────────────
# CREDIT REFUND
# ──────────────────────────────────────────────────────────

async def refund_credits(
    db: AsyncSession,
    user: "User",
    amount: int,
    reason: str,
    reference_id: Optional[str] = None,
    *,
    enterprise_id: Optional[int] = None,
) -> int:
    from app.models.user import CreditTransaction, CreditTxType

    if amount <= 0:
        return 0

    if enterprise_id:
        ent = await _lock_enterprise(db, enterprise_id)
        ent.credit_balance += amount
        db.add(CreditTransaction(
            enterprise_id=ent.id, user_id=user.id if user else None, amount=+amount, balance_after=ent.credit_balance,
            tx_type=CreditTxType.REFUND, description=reason, reference_id=reference_id,
        ))
        await db.commit()
        return ent.credit_balance

    locked_user = await _lock_user(db, user.id)
    locked_user.credit_balance += amount
    db.add(CreditTransaction(
        user_id=user.id, amount=+amount, balance_after=locked_user.credit_balance,
        tx_type=CreditTxType.REFUND, description=reason, reference_id=reference_id,
    ))
    await db.commit()
    return locked_user.credit_balance


# ──────────────────────────────────────────────────────────
# LOGIN HELPERS
# ──────────────────────────────────────────────────────────

async def update_last_login(db: AsyncSession, user: "User") -> None:
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()


async def validate_enterprise_membership(db: AsyncSession, user: "User") -> Optional["Enterprise"]:
    from app.models.playbook import Enterprise
    if not is_enterprise_user(user) or not user.enterprise_id:
        return None
    stmt = select(Enterprise).where(Enterprise.id == user.enterprise_id)
    return (await db.execute(stmt)).scalar_one_or_none()