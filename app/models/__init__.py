"""
Models package — imports every model so SQLAlchemy resolves all relationships.

⚠️ CRITICAL: This file MUST be imported before any database query.
Add this line in main.py:
    import app.models  # noqa: F401

Without it, SQLAlchemy raises InvalidRequestError on string-based
relationships like "Payment", "Enterprise", "WithdrawalRequest".
"""

from app.core.database import Base

# ──────────────────────────────────────────────────────────
# IMPORT ORDER
# (Roughly dependency order; SQLAlchemy defers circular FK resolution)
# ──────────────────────────────────────────────────────────

# 1. Enterprise / Playbook (referenced by many)
from app.models.playbook import (
    Enterprise,
    EnterpriseStatus,
    CompanyPlaybook,
    CreditPackage,
    CreditPackageTier,
    generate_referral_code,
)

# 2. User (central hub)
from app.models.user import (
    User,
    UserRole,
    SubscriptionTier,
    CreditTransaction,
    CreditTxType,
    CompanyRole,
    MemberApprovalStatus,
    WithdrawalRequest,
    WithdrawalStatus,
    PayoutMethod,
)

# 3. Lawyer (references User)
from app.models.lawyer import (
    LawyerProfile,
    LawyerStatus,
    LawyerAvailability,
)

# 4. Memo / Contract (references User, Enterprise, Lawyer)
from app.models.memo import (
    Contract,
    ContractMemo,
    RiskLevel,
    HITLStatus,
    OfferStatus,
)

# 5. Audit / Payment (references User, Enterprise, CreditPackage)
from app.models.audit import (
    Payment,
    PaymentMethod,
    PaymentPurpose,
    PaymentStatus,
    AuditLog,
    AuditAction,
    AuditSeverity,
)

# 6. Chat (references User, Contract)
from app.models.chat import (
    ChatSession,
    ChatMessage,
    ChatMode,
    MessageRole,
)

# 7. Metrics (independent event log)  <-- ✅ নতুন যোগ করা হলো
from app.models.metrics import MetricEvent

# 8. Inquiries & Feedback (independent public forms)
from app.models.inquiry import (
    FeedbackSuggestion,
    NewsletterSubscriber,
)

# 9. Docs / Pitch Deck Module
from app.models.docs_config import (
    DocsConfig,
    DocsVisibility,
    DocsTeamMember,
)


# ──────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────

__all__ = [
    # ── Base ──
    "Base",

    # ── Playbook / Enterprise ──
    "Enterprise",
    "EnterpriseStatus",
    "CompanyPlaybook",
    "CreditPackage",
    "CreditPackageTier",
    "generate_referral_code",

    # ── User ──
    "User",
    "UserRole",
    "SubscriptionTier",
    "CreditTransaction",
    "CreditTxType",
    "CompanyRole",
    "MemberApprovalStatus",
    "WithdrawalRequest",
    "WithdrawalStatus",
    "PayoutMethod",

    # ── Lawyer ──
    "LawyerProfile",
    "LawyerStatus",
    "LawyerAvailability",

    # ── Memo / Contract ──
    "Contract",
    "ContractMemo",
    "RiskLevel",
    "HITLStatus",
    "OfferStatus",

    # ── Audit / Payment ──
    "Payment",
    "PaymentMethod",
    "PaymentPurpose",
    "PaymentStatus",
    "AuditLog",
    "AuditAction",
    "AuditSeverity",

    # ── Chat ──
    "ChatSession",
    "ChatMessage",
    "ChatMode",
    "MessageRole",

    # ── Metrics ── <-- ✅ নতুন যোগ করা হলো
    "MetricEvent",

    # ── Inquiries ──
    "FeedbackSuggestion",
    "NewsletterSubscriber",

    # -- Docs Module --
    "DocsConfig",
    "DocsVisibility",
    "DocsTeamMember",
]