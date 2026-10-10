from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class ContractMemo(Base):
    __tablename__ = "contract_memos"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    filename = Column(String(255), nullable=False)
    overall_risk_score = Column(String(50), default="MODERATE")
    memo_summary = Column(Text, nullable=True)
    risks_json = Column(Text, nullable=True)
    status = Column(String(50), default="PENDING_REVIEW")  # PENDING_REVIEW, APPROVED, REJECTED

    # Lawyer Human-in-the-Loop (HITL) tracking
    lawyer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    lawyer_notes = Column(Text, nullable=True)
    digital_signature = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    lawyer = relationship("User", back_populates="reviewed_memos", foreign_keys=[lawyer_id])