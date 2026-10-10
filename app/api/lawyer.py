from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole
from app.models.memo import ContractMemo

router = APIRouter(prefix="/api/lawyer", tags=["Lawyer Portal"])


# --- Schemas ---
class MemoReviewRequest(BaseModel):
    lawyer_notes: str
    status: str  # "APPROVED" or "REJECTED"


class MemoSignRequest(BaseModel):
    digital_signature: str  # e.g., "Advocate Mahmudul Hasan (Bar Council No: 12345)"


# --- Endpoints ---

@router.get("/pending-memos")
def get_pending_memos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """আইনজীবী বা অ্যাডমিন পেন্ডিং পর্যালোচনার তালিকা দেখতে পারবেন।"""
    if current_user.role not in [UserRole.LAWYER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to verified advocates and admins."
        )

    return db.query(ContractMemo).filter(ContractMemo.status == "PENDING_REVIEW").all()


@router.post("/memo/{memo_id}/review")
def review_memo(
    memo_id: int,
    payload: MemoReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """আইনি নোট ও সিদ্ধান্ত (APPROVED / REJECTED) রেকর্ড করা।"""
    if current_user.role not in [UserRole.LAWYER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to verified advocates."
        )

    memo = db.query(ContractMemo).filter(ContractMemo.id == memo_id).first()
    if not memo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract memo not found."
        )

    if payload.status not in ["APPROVED", "REJECTED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid status. Must be APPROVED or REJECTED."
        )

    memo.lawyer_id = current_user.id
    memo.lawyer_notes = payload.lawyer_notes
    memo.status = payload.status
    memo.reviewed_at = datetime.utcnow()

    db.commit()
    db.refresh(memo)

    return {
        "message": f"Memo successfully marked as {memo.status}",
        "memo_id": memo.id,
        "reviewed_by": current_user.full_name,
        "status": memo.status
    }


@router.post("/memo/{memo_id}/sign")
def sign_memo(
    memo_id: int,
    payload: MemoSignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """আইনজীবী মেমোতে ডিজিটাল স্বাক্ষর প্রদান করবেন।"""
    if current_user.role not in [UserRole.LAWYER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to verified advocates."
        )

    memo = db.query(ContractMemo).filter(ContractMemo.id == memo_id).first()
    if not memo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract memo not found."
        )

    if not memo.lawyer_notes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must review and provide legal notes before affixing signature."
        )

    memo.digital_signature = payload.digital_signature
    memo.reviewed_at = datetime.utcnow()

    db.commit()
    db.refresh(memo)

    return {
        "message": "Digital signature attached successfully.",
        "memo_id": memo.id,
        "digital_signature": memo.digital_signature,
        "signed_by": current_user.full_name,
        "signed_at": memo.reviewed_at
    }