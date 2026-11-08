from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User


# ──────────────────────────────────────────────────────────
# CHAT TIER LIMITS
# ──────────────────────────────────────────────────────────

TIER_LIMITS: Dict[str, Dict[str, Any]] = {
    "FREE": {
        "active_sessions": 5,
        "messages_per_session": 10,
        "monthly_total_chats": 50,
        "rolling_summary": False,
    },
    "PRO": {
        "active_sessions": 15,
        "messages_per_session": 25,
        "monthly_total_chats": 150,
        "rolling_summary": True,
    },
    "ENTERPRISE_MEMBER": {
        "active_sessions": 30,
        "messages_per_session": 40,
        "monthly_total_chats": 300,
        "rolling_summary": True,
    },
    "LAWYER": {
        "active_sessions": 20,
        "messages_per_session": 25,
        "monthly_total_chats": 80,
        "rolling_summary": True,
    },
    "ADMIN": {
        "active_sessions": 200000,
        "messages_per_session": 200000,
        "monthly_total_chats": None,
        "rolling_summary": True,
    },
}

CONTRACT_REVIEW_LIMITS: Dict[str, int] = {
    "max_sessions_per_contract": 3,
    "messages_per_session": 15,
    "total_messages_per_contract": 30,
}

LAWYER_MONTHLY_FREE_CHAT_CREDITS = 50


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def get_tier_key(user: "User") -> str:
    from app.models.user import UserRole # <-- লোকাল ইমপোর্ট
    
    # ✅ HACK: আপনার নির্দিষ্ট ডেমো ইউজারের জন্য আনলিমিটেড সেশন ও মেসেজ লিমিট
    if getattr(user, "email", "") == "mowesin210@backyr.com":
        return "ADMIN" 

    if user.role == UserRole.ADMIN:
        return "ADMIN"
    if user.role == UserRole.LAWYER:
        return "LAWYER"
    if user.role == UserRole.ENTERPRISE_MEMBER:
        return "ENTERPRISE_MEMBER"
    if user.role == UserRole.ENTERPRISE_ADMIN:
        return "ENTERPRISE_MEMBER"
    
    return user.subscription_tier.value if user.subscription_tier else "FREE"


def get_chat_limits(user: "User") -> Dict[str, Any]:
    return TIER_LIMITS.get(get_tier_key(user), TIER_LIMITS["FREE"])


def get_session_limit(user: "User") -> int:
    return get_chat_limits(user)["messages_per_session"]


def get_max_active_sessions(user: "User") -> int:
    return get_chat_limits(user)["active_sessions"]


def get_monthly_chat_cap(user: "User") -> int | None:
    return get_chat_limits(user)["monthly_total_chats"]


def should_generate_summary(user: "User") -> bool:
    return get_chat_limits(user)["rolling_summary"]
