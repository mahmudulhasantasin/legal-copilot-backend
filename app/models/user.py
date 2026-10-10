import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey, Float
from sqlalchemy.orm import relationship
from app.core.database import Base


class UserRole(str, enum.Enum):
    GENERAL = "general"     # Client / Individual
    ADMIN = "admin"         # System Admin / Reviewer
    LAWYER = "lawyer"       # Verified Legal Advocate
    COMPANY = "company"     # Enterprise / Organization


class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    phone_number = Column(String(20), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.GENERAL, nullable=False)
    
    # Subscription & Credits (Ready for Payment Integration)
    subscription_tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.FREE, nullable=False)
    credits_remaining = Column(Integer, default=3)  # e.g., 3 free analyses on signup
    subscription_expires_at = Column(DateTime, nullable=True)

    # Lawyer specific fields
    bar_council_id = Column(String(100), nullable=True)
    specialization = Column(String(255), nullable=True)
    is_verified_lawyer = Column(Boolean, default=False)

    # Company specific fields
    company_name = Column(String(255), nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    playbooks = relationship(
        "CompanyPlaybook",
        back_populates="company",
        foreign_keys="CompanyPlaybook.company_id"
    )
    reviewed_memos = relationship(
        "ContractMemo",
        back_populates="lawyer",
        foreign_keys="ContractMemo.lawyer_id"
    )
    transactions = relationship(
        "Transaction",
        back_populates="user",
        foreign_keys="Transaction.user_id"
    )


class CompanyPlaybook(Base):
    __tablename__ = "company_playbooks"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    rules_json = Column(String, nullable=False)  # Custom guidelines
    company_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("User", back_populates="playbooks", foreign_keys=[company_id])


class Transaction(Base):
    """Placeholder table for teammate handling payments"""
    __tablename__ = "transactions"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="BDT")
    provider = Column(String(50), nullable=True)  # BKASH, NAGAD, SSLCOMMERZ
    transaction_id = Column(String(100), unique=True, index=True, nullable=True)
    status = Column(String(50), default="PENDING")  # PENDING, SUCCESS, FAILED
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="transactions", foreign_keys=[user_id])