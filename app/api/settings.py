import logging
import os
import uuid
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import create_client, Client

from app.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, verify_password, get_password_hash
from app.core.tier_limits import LAWYER_MONTHLY_FREE_CHAT_CREDITS
from app.models.user import User, UserRole, CompanyRole, MemberApprovalStatus
from app.models.lawyer import LawyerProfile, LawyerAvailability
from app.models.playbook import Enterprise

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["Settings & Profile"])

# ──────────────────────────────────────────────────────────
# SUPABASE SETUP FOR AVATARS
# ──────────────────────────────────────────────────────────
SUPABASE_URL = getattr(settings, "SUPABASE_URL", "")
SUPABASE_KEY = getattr(settings, "SUPABASE_SERVICE_KEY", "")
BUCKET_NAME = "contracts"  # আমরা একই বাকেটে avatars/ ফোল্ডার বানাবো


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class EnterpriseSettingsInfo(BaseModel):
    enterprise_id: int
    name: str
    company_role: Optional[str]
    approval_status: Optional[str]
    quota_total: int
    quota_used: int
    quota_remaining: int

class LawyerSettingsInfo(BaseModel):
    bar_council_id: str
    specializations: List[str]
    availability: str
    wallet_balance_bdt: float
    completed_reviews: int
    rating: float
    monthly_free_chat_used: int
    monthly_free_chat_remaining: int

class UserSettingsResponse(BaseModel):
    id: int
    email: str
    full_name: str
    phone: Optional[str]
    role: str
    subscription_tier: str
    credit_balance: int
    is_verified: bool
    last_login_at: Optional[str]
    profile_photo_url: Optional[str] = None  # ✅ নতুন যুক্ত হলো
    
    # Role-specific contexts
    enterprise: Optional[EnterpriseSettingsInfo] = None
    lawyer: Optional[LawyerSettingsInfo] = None

class ProfileUpdatePayload(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=20)

class PasswordChangePayload(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)

class LawyerPreferencesPayload(BaseModel):
    specializations: Optional[List[str]] = None
    availability: Optional[LawyerAvailability] = None
    bio: Optional[str] = Field(default=None, max_length=1000)


# ──────────────────────────────────────────────────────────
# GET MY SETTINGS
# ──────────────────────────────────────────────────────────

@router.get("/me", response_model=UserSettingsResponse)
async def get_my_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns full profile details, credit balances, and role-specific context.
    """
    # ── Base User Data ──
    response = UserSettingsResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        role=current_user.role.value,
        subscription_tier=current_user.subscription_tier.value,
        credit_balance=current_user.credit_balance,
        is_verified=current_user.is_verified,
        last_login_at=current_user.last_login_at.isoformat() if current_user.last_login_at else None,
        profile_photo_url=current_user.profile_photo_url, # ✅ নতুন যুক্ত হলো
    )

    # ── Enterprise Context ──
    if current_user.is_enterprise_user and current_user.enterprise_id:
        ent = await db.scalar(select(Enterprise).where(Enterprise.id == current_user.enterprise_id))
        if ent:
            response.enterprise = EnterpriseSettingsInfo(
                enterprise_id=ent.id,
                name=ent.name,
                company_role=current_user.company_role.value if current_user.company_role else None,
                approval_status=current_user.member_approval_status.value if current_user.member_approval_status else None,
                quota_total=current_user.member_credit_quota,
                quota_used=current_user.member_credit_used,
                quota_remaining=current_user.remaining_member_quota
            )

    # ── Lawyer Context ──
    if current_user.role == UserRole.LAWYER:
        profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.user_id == current_user.id))
        if profile:
            response.lawyer = LawyerSettingsInfo(
                bar_council_id=profile.bar_council_id,
                specializations=profile.specializations,
                availability=profile.availability.value,
                wallet_balance_bdt=float(profile.wallet_balance),
                completed_reviews=profile.completed_reviews_count,
                rating=profile.rating_average,
                monthly_free_chat_used=current_user.monthly_free_chat_used,
                monthly_free_chat_remaining=max(0, LAWYER_MONTHLY_FREE_CHAT_CREDITS - current_user.monthly_free_chat_used)
            )

    return response


# ──────────────────────────────────────────────────────────
# UPDATE PROFILE
# ──────────────────────────────────────────────────────────

@router.patch("/profile")
async def update_profile(
    payload: ProfileUpdatePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update basic info (Name, Phone)."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()
    if payload.phone is not None:
        current_user.phone = payload.phone.strip()

    await db.commit()
    await db.refresh(current_user)
    return {"status": "success", "message": "Profile updated."}


# ──────────────────────────────────────────────────────────
# UPLOAD PROFILE PHOTO
# ──────────────────────────────────────────────────────────

@router.post("/upload-photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Uploads a profile photo to Supabase and updates the user's URL."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(500, "Storage service not configured")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(400, "Only image files are allowed (jpg, png, webp)")

    file_bytes = await file.read()
    safe_name = f"avatars/{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"

    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        supabase.storage.from_(BUCKET_NAME).upload(
            path=safe_name,
            file=file_bytes,
            file_options={"content-type": file.content_type}
        )
        
        # ডাটাবেসে আপডেট
        current_user.profile_photo_url = safe_name
        await db.commit()
        
        return {"status": "success", "profile_photo_url": safe_name}
    except Exception as e:
        logger.error(f"[Supabase] Avatar upload failed: {e}")
        raise HTTPException(500, "Failed to upload profile photo")


# ──────────────────────────────────────────────────────────
# CHANGE PASSWORD
# ──────────────────────────────────────────────────────────

@router.post("/change-password")
async def change_password(
    payload: PasswordChangePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Verify old password and set new one."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(400, "Current password is incorrect.")

    current_user.hashed_password = get_password_hash(payload.new_password)
    await db.commit()
    return {"status": "success", "message": "Password changed successfully."}


# ──────────────────────────────────────────────────────────
# LAWYER SETTINGS
# ──────────────────────────────────────────────────────────

@router.patch("/lawyer")
async def update_lawyer_settings(
    payload: LawyerPreferencesPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Allows lawyers to update their specializations, bio, and toggle availability.
    """
    if current_user.role != UserRole.LAWYER:
        raise HTTPException(403, "Only lawyers can update these settings.")

    profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.user_id == current_user.id))
    if not profile:
        raise HTTPException(404, "Lawyer profile not found.")

    if payload.specializations is not None:
        profile.specializations = payload.specializations
    if payload.bio is not None:
        profile.bio = payload.bio.strip()
    if payload.availability is not None:
        # Ensure they aren't trying to set it to BUSY manually
        if payload.availability == LawyerAvailability.BUSY:
            raise HTTPException(400, "BUSY status is managed automatically by the system.")
        profile.availability = payload.availability

    await db.commit()
    return {"status": "success", "message": "Lawyer preferences updated."}