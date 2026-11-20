"""
Central pricing engine — SINGLE SOURCE OF TRUTH.

⚠️ CRITICAL RULES:
  1. Users must NEVER be able to reverse-engineer ৳ ↔ credit.
     → Every package has a different effective rate.
     → Service costs are set independently of package rates.
     → `internal_bdt_value` is ADMIN-ONLY and NEVER returned to frontend.

  2. Lawyer payouts are FIXED in BDT (predictable for lawyers).
     → User pays in credits (opaque).
     → Lawyer sees BDT in earnings dashboard.
     → Never show both on the same screen.

  3. Custom top-up amounts are DISABLED by default.
     → Enable only via ALLOW_CUSTOM_TOPUP flag once business decides.
"""

from decimal import Decimal
from typing import Dict, Any, List, Optional
from app.models.user import UserRole


# ──────────────────────────────────────────────────────────
# FEATURE FLAGS
# ──────────────────────────────────────────────────────────

ALLOW_CUSTOM_TOPUP: bool = False
# Set to True later to enable "buy N credits for BDT X" flow.
# When True, custom rate MUST be WORSE than the Standard package
# to prevent users from discovering a fixed rate.

CUSTOM_TOPUP_MIN_BDT: Decimal = Decimal("100.00")
CUSTOM_TOPUP_MAX_BDT: Decimal = Decimal("50000.00")
CUSTOM_TOPUP_RATE: Decimal = Decimal("1.20")
# Deliberately worse than any package rate (range is ~0.87–1.14).
# This ensures anyone using custom top-up pays premium → incentivizes packages.


# ──────────────────────────────────────────────────────────
# SERVICE PRICING (what users pay in CREDITS)
# ──────────────────────────────────────────────────────────

SERVICE_PRICING: Dict[str, Dict[str, Any]] = {

    "CHAT_QUERY": {
        "display_name": "Legal Chat Query",
        "display_name_bn": "আইনি চ্যাট প্রশ্ন",
        "cost_credits": 1,
        "internal_bdt_value": Decimal("1.10"),   # ADMIN ONLY
        "applies_to": [
            UserRole.GENERAL,
            UserRole.ENTERPRISE_MEMBER,
            UserRole.ENTERPRISE_ADMIN,
        ],
    },

    "CONTRACT_AUDIT_B2C": {
        "display_name": "Contract Audit (Personal)",
        "display_name_bn": "চুক্তি অডিট (ব্যক্তিগত)",
        "cost_credits": 25,
        "internal_bdt_value": Decimal("28.00"),  # ADMIN ONLY
        "applies_to": [UserRole.GENERAL],
    },

    "CONTRACT_AUDIT_B2B": {
        "display_name": "Contract Audit (Enterprise)",
        "display_name_bn": "চুক্তি অডিট (এন্টারপ্রাইজ)",
        "cost_credits": 50,
        "internal_bdt_value": Decimal("56.00"),  # ADMIN ONLY
        "applies_to": [UserRole.ENTERPRISE_MEMBER, UserRole.ENTERPRISE_ADMIN],
    },

    "PDF_EXPORT": {
        "display_name": "Export Formatted PDF",
        "display_name_bn": "PDF ডাউনলোড",
        "cost_credits": 5,
        "internal_bdt_value": Decimal("5.50"),   # ADMIN ONLY
        "applies_to": [
            UserRole.GENERAL,
            UserRole.ENTERPRISE_MEMBER,
            UserRole.ENTERPRISE_ADMIN,
        ],
    },

    "LAWYER_VERIFICATION": {
        "display_name": "Lawyer Verification",
        "display_name_bn": "লইয়ার ভেরিফিকেশন",
        "cost_credits": 1500,
        # ── Payout Breakdown (ALL PRIVATE) ──
        "lawyer_payout_bdt": Decimal("1000.00"),    # Fixed BDT to lawyer
        "platform_fee_bdt": Decimal("500.00"),      # Platform margin
        "internal_bdt_value": Decimal("1500.00"),   # Sanity check
        "applies_to": [
            UserRole.GENERAL,
            UserRole.ENTERPRISE_MEMBER,
            UserRole.ENTERPRISE_ADMIN,
        ],
    },
}


# ──────────────────────────────────────────────────────────
# PUBLIC HELPERS (Safe for API responses)
# ──────────────────────────────────────────────────────────

def get_service_cost(service_key: str) -> int:
    """
    Returns the CREDIT cost for a service.
    Safe to expose to frontend.
    """
    if service_key not in SERVICE_PRICING:
        raise KeyError(f"Unknown service_key: {service_key}")
    return SERVICE_PRICING[service_key]["cost_credits"]


def get_audit_cost(user_role: UserRole) -> int:
    """Auto-picks B2C vs B2B contract audit cost based on user role."""
    if user_role in (UserRole.ENTERPRISE_MEMBER, UserRole.ENTERPRISE_ADMIN):
        return SERVICE_PRICING["CONTRACT_AUDIT_B2B"]["cost_credits"]
    return SERVICE_PRICING["CONTRACT_AUDIT_B2C"]["cost_credits"]


def get_public_service_costs() -> Dict[str, Any]:
    """
    Returns ONLY credit costs + display names for the frontend pricing page.
    Never includes BDT internal values.
    """
    return {
        "chat_query": {
            "cost": SERVICE_PRICING["CHAT_QUERY"]["cost_credits"],
            "name": SERVICE_PRICING["CHAT_QUERY"]["display_name"],
            "name_bn": SERVICE_PRICING["CHAT_QUERY"]["display_name_bn"],
        },
        "contract_audit_personal": {
            "cost": SERVICE_PRICING["CONTRACT_AUDIT_B2C"]["cost_credits"],
            "name": SERVICE_PRICING["CONTRACT_AUDIT_B2C"]["display_name"],
            "name_bn": SERVICE_PRICING["CONTRACT_AUDIT_B2C"]["display_name_bn"],
        },
        "contract_audit_enterprise": {
            "cost": SERVICE_PRICING["CONTRACT_AUDIT_B2B"]["cost_credits"],
            "name": SERVICE_PRICING["CONTRACT_AUDIT_B2B"]["display_name"],
            "name_bn": SERVICE_PRICING["CONTRACT_AUDIT_B2B"]["display_name_bn"],
        },
        "pdf_export": {
            "cost": SERVICE_PRICING["PDF_EXPORT"]["cost_credits"],
            "name": SERVICE_PRICING["PDF_EXPORT"]["display_name"],
            "name_bn": SERVICE_PRICING["PDF_EXPORT"]["display_name_bn"],
        },
        "lawyer_verification": {
            "cost": SERVICE_PRICING["LAWYER_VERIFICATION"]["cost_credits"],
            "name": SERVICE_PRICING["LAWYER_VERIFICATION"]["display_name"],
            "name_bn": SERVICE_PRICING["LAWYER_VERIFICATION"]["display_name_bn"],
        },
    }


def is_service_available_to_role(service_key: str, user_role: UserRole) -> bool:
    """Check if a service is available to a user role."""
    if service_key not in SERVICE_PRICING:
        return False
    return user_role in SERVICE_PRICING[service_key]["applies_to"]


# ──────────────────────────────────────────────────────────
# PRIVATE HELPERS (ADMIN ONLY — never expose to frontend)
# ──────────────────────────────────────────────────────────

def get_internal_bdt_value(service_key: str) -> Decimal:
    """ADMIN ONLY. For financial reconciliation."""
    return SERVICE_PRICING[service_key]["internal_bdt_value"]


def get_lawyer_verification_split() -> Dict[str, Decimal]:
    """
    ADMIN ONLY. Returns the BDT split for lawyer verification.
    Called when user requests HITL verification to set fee_bdt & payout.
    """
    p = SERVICE_PRICING["LAWYER_VERIFICATION"]
    return {
        "credits_to_deduct": Decimal(p["cost_credits"]),
        "lawyer_payout_bdt": p["lawyer_payout_bdt"],
        "platform_fee_bdt": p["platform_fee_bdt"],
        "internal_bdt_value": p["internal_bdt_value"],
    }


def calculate_custom_topup_credits(amount_bdt: Decimal) -> int:
    """
    ADMIN ONLY. Used only if ALLOW_CUSTOM_TOPUP is True.
    Deliberately uses a WORSE rate to discourage custom top-ups.
    """
    if not ALLOW_CUSTOM_TOPUP:
        raise ValueError("Custom top-up is disabled.")
    if amount_bdt < CUSTOM_TOPUP_MIN_BDT or amount_bdt > CUSTOM_TOPUP_MAX_BDT:
        raise ValueError(
            f"Amount must be between ৳{CUSTOM_TOPUP_MIN_BDT} and ৳{CUSTOM_TOPUP_MAX_BDT}."
        )
    return int(amount_bdt / CUSTOM_TOPUP_RATE)


# ──────────────────────────────────────────────────────────
# DEFAULT PACKAGE SEED (used by seed script)
# ──────────────────────────────────────────────────────────

DEFAULT_PACKAGES: List[Dict[str, Any]] = [
    {
        "name": "Starter Pack",
        "tier": "STARTER",
        "description": "Perfect for trying the platform.",
        "price_bdt": Decimal("500.00"),
        "base_credits": 400,
        "bonus_credits": 40,        # User sees 440 total
        "applies_to": "ALL",
        "display_order": 1,
        "badge_text": None,
    },
    {
        "name": "Standard Pack",
        "tier": "STANDARD",
        "description": "Best for regular legal questions.",
        "price_bdt": Decimal("1000.00"),
        "base_credits": 800,
        "bonus_credits": 150,       # User sees 950 total
        "applies_to": "ALL",
        "display_order": 2,
        "badge_text": "Most Popular",
    },
    {
        "name": "Pro Pack",
        "tier": "PRO",
        "description": "For power users and small teams.",
        "price_bdt": Decimal("2500.00"),
        "base_credits": 2000,
        "bonus_credits": 600,       # User sees 2600 total
        "applies_to": "ALL",
        "display_order": 3,
        "badge_text": "Best Value",
    },
    {
        "name": "Enterprise Growth",
        "tier": "ENTERPRISE",
        "description": "For growing legal teams (up to 5 members).",
        "price_bdt": Decimal("5000.00"),
        "base_credits": 5000,
        "bonus_credits": 500,       # User sees 5500 total
        "applies_to": "ENTERPRISE",
        "display_order": 4,
        "badge_text": "Team Choice",
    },
    {
        "name": "Enterprise Scale",
        "tier": "ENTERPRISE",
        "description": "For corporate legal departments (5-10 members).",
        "price_bdt": Decimal("10000.00"),
        "base_credits": 8000,
        "bonus_credits": 3500,      # User sees 11500 total
        "applies_to": "ENTERPRISE",
        "display_order": 5,
        "badge_text": "Most Popular",
    },
]