import secrets

import logging

from datetime import datetime, timedelta, timezone

from typing import Optional, List



from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks

from sqlalchemy import select, func

from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, EmailStr, Field, field_validator, ValidationInfo, ConfigDict

from google.oauth2 import id_token

from google.auth.transport import requests as google_requests



from app.core.database import get_db

from app.core.security import (

    verify_password, get_password_hash, create_access_token,

    build_token_payload, get_current_user, update_last_login,

)

from app.config import settings

from app.models.user import (

    User, UserRole, SubscriptionTier, CreditTransaction, 

    CreditTxType, MemberApprovalStatus, CompanyRole,

)

from app.models.playbook import Enterprise, EnterpriseStatus, generate_referral_code

from app.models.lawyer import LawyerProfile, LawyerStatus

from app.services.email_service import send_otp_email, send_password_reset_email  # ✅ UPDATED



logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])



SIGNUP_BONUS_CREDITS = 100

OTP_EXPIRY_MINUTES = 5



# ✅ BUG B FIX: backend LawyerStatus → frontend verification_status

_LAWYER_STATUS_MAP = {

    "PENDING_VERIFICATION": "pending",

    "APPROVED": "verified",

    "REJECTED": "rejected",

    "SUSPENDED": "suspended",

    "INACTIVE": "suspended",

}



# ──────────────────────────────────────────────────────────

# SCHEMAS

# ──────────────────────────────────────────────────────────

class EmailRegisterRequest(BaseModel):

    email: EmailStr

    password: str = Field(min_length=6, max_length=128)

    confirm_password: str

    full_name: str = Field(min_length=2, max_length=255)

    phone: Optional[str] = None

    date_of_birth: Optional[datetime] = None

    gender: Optional[str] = None



    @field_validator("confirm_password")

    @classmethod

    def passwords_match(cls, v: str, info: ValidationInfo) -> str:

        if "password" in info.data and v != info.data["password"]:

            raise ValueError("Passwords do not match.")

        return v



class RegisterResponse(BaseModel):

    message: str

    email: str



class EnterpriseMemberRegisterRequest(BaseModel):

    email: EmailStr

    password: str = Field(min_length=6, max_length=128)

    confirm_password: str

    full_name: str = Field(min_length=2, max_length=255)

    phone: Optional[str] = None

    referral_code: str = Field(min_length=4, max_length=20)

    date_of_birth: Optional[datetime] = None

    gender: Optional[str] = None



    @field_validator("confirm_password")

    @classmethod

    def passwords_match(cls, v: str, info: ValidationInfo) -> str:

        if "password" in info.data and v != info.data["password"]:

            raise ValueError("Passwords do not match.")

        return v



class VerifyOtpRequest(BaseModel):

    email: EmailStr

    otp_code: str = Field(min_length=6, max_length=6)



class ResendOtpRequest(BaseModel):

    email: EmailStr



class LoginRequest(BaseModel):

    email: EmailStr

    password: str



class GoogleLoginRequest(BaseModel):

    token: str



class GoogleRegisterRequest(BaseModel):

    token: str

    password: Optional[str] = None

    confirm_password: Optional[str] = None



# ✅ NEW — forgot-password schemas

class ForgotPasswordRequest(BaseModel):

    email: EmailStr



class ResetPasswordRequest(BaseModel):

    email: EmailStr

    otp_code: str = Field(min_length=6, max_length=6)

    new_password: str = Field(min_length=6, max_length=128)



class Token(BaseModel):

    access_token: str

    token_type: str = "bearer"

    role: str

    credit_balance: int

    is_verified: bool
    has_usable_password: bool = True


    # ✅ NEW — must match _issue_token() response (fixes undefined user on frontend)

    user_id: str

    email: str

    full_name: str

    is_phone_verified: bool
    has_usable_password: bool = True




class LawyerInfoResponse(BaseModel):

    bar_council_id: Optional[str] = None

    experience_years: Optional[int] = None

    specializations: List[str] = Field(default_factory=list)

    verification_status: str = "pending"



# ✅ NEW — nested enterprise info for frontend company name display

class EnterpriseProfileResponse(BaseModel):
    company_name: str
    company_type: Optional[str] = None
    registration_no: Optional[str] = None
    tin_no: Optional[str] = None
    refer_code: Optional[str] = None
    member_limit: Optional[int] = None
    verification_status: str = "pending"  # "verified" | "pending" | "rejected"
    is_team_member: bool = False
    member_approval_status: Optional[str] = None



class UserResponse(BaseModel):

    id: int

    email: str

    full_name: str

    phone: Optional[str] = None

    role: UserRole

    subscription_tier: SubscriptionTier

    credit_balance: int

    enterprise_credit_balance: Optional[int] = None 

    

    # ✅ NEW: Member Quota & Approval Details
    member_credit_quota: Optional[int] = None
    member_credit_used: Optional[int] = None
    member_approval_status: Optional[str] = None

    

    is_active: bool

    is_verified: bool
    has_usable_password: bool = True


    enterprise_id: Optional[int] = None

    profile_photo_url: Optional[str] = None

    lawyer_profile: Optional[LawyerInfoResponse] = None

    # ✅ NEW — populated for enterprise users (admin + member)

    enterprise_profile: Optional[EnterpriseProfileResponse] = None



    model_config = ConfigDict(from_attributes=True)



class OnboardLawyerRequest(BaseModel):

    bar_council_id: str = Field(min_length=3, max_length=100)

    experience_years: int = Field(ge=0, le=60)

    specializations: list[str] = []



class OnboardEnterpriseRequest(BaseModel):

    company_name: str = Field(min_length=2, max_length=255)

    company_type: Optional[str] = None

    registration_no: Optional[str] = None

    tin_no: Optional[str] = None

    member_limit: int = Field(default=5, ge=1, le=50)

    billing_email: Optional[EmailStr] = None

    billing_phone: Optional[str] = None





# ──────────────────────────────────────────────────────────

# HELPERS

# ──────────────────────────────────────────────────────────

def _verify_google_token(token: str) -> dict:

    try:

        aud = settings.GOOGLE_CLIENT_ID.strip() if settings.GOOGLE_CLIENT_ID else None

        return id_token.verify_oauth2_token(token, google_requests.Request(), aud)

    except Exception as e:

        logger.warning(f"Google token verification failed: {e}")

        raise HTTPException(401, f"Invalid or expired Google credential: {e}")



def _generate_otp() -> str:

    return f"{secrets.randbelow(1_000_000):06d}"



def _issue_token(user: User) -> dict:

    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    token = create_access_token(data=build_token_payload(user), expires_delta=expires)

    return {

        "access_token": token, "token_type": "bearer", "role": user.role.value,

        "credit_balance": user.credit_balance, "is_verified": user.is_verified,

        # ✅ NEW — required by frontend TokenResponse

        "user_id": str(user.id),

        "email": user.email,

        "full_name": user.full_name,

        "is_phone_verified": user.is_verified,  # no dedicated column yet

    }



async def _get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:

    stmt = select(User).where(User.email == email.lower().strip(), User.is_deleted == False)

    return (await db.execute(stmt)).scalar_one_or_none()



def _check_otp(user: User, otp_code: str) -> None:

    if not user.otp_code or user.otp_code != otp_code.strip():

        raise HTTPException(400, "Invalid OTP code.")

    if not user.otp_expires_at:

        raise HTTPException(400, "OTP expired. Request a new one.")

    now = datetime.now(timezone.utc)

    exp = user.otp_expires_at

    if exp.tzinfo is None:

        exp = exp.replace(tzinfo=timezone.utc)

    if exp < now:

        raise HTTPException(400, "OTP expired. Request a new one.")



async def _send_and_store_otp(db: AsyncSession, user: User, background_tasks: BackgroundTasks) -> None:

    otp = _generate_otp()

    user.otp_code = otp

    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)

    await db.commit()

    background_tasks.add_task(send_otp_email, user.email, otp)





# ──────────────────────────────────────────────────────────

# ENDPOINTS

# ──────────────────────────────────────────────────────────

@router.post("/register/email", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)

async def register_with_email(

    payload: EmailRegisterRequest,

    background_tasks: BackgroundTasks,

    db: AsyncSession = Depends(get_db),

):

    email = payload.email.lower().strip()

    existing = await _get_user_by_email(db, email)

    if existing:

        if not existing.is_verified:

            await _send_and_store_otp(db, existing, background_tasks)

            return RegisterResponse(message="Account exists but not verified. New OTP sent.", email=email)

        raise HTTPException(400, "Email already registered. Please log in.")



    user = User(

        email=email, hashed_password=get_password_hash(payload.password),

        full_name=payload.full_name.strip(), phone=payload.phone,

        date_of_birth=payload.date_of_birth, gender=payload.gender,

        role=UserRole.GENERAL, subscription_tier=SubscriptionTier.FREE,

        credit_balance=0, is_active=True, is_verified=False,

    )

    db.add(user)

        

    try:

        await db.flush()

    except Exception as e:

        await db.rollback()

        if "duplicate key" in str(e).lower():

            raise HTTPException(status_code=400, detail="Email or Phone number already registered.")

        logger.error(f"DB Error on registration: {e}")

        raise HTTPException(status_code=500, detail="Internal server error during registration.")



    await _send_and_store_otp(db, user, background_tasks)

    return RegisterResponse(message="OTP sent to your email.", email=email)



@router.post("/verify-otp", response_model=Token)

async def verify_otp(payload: VerifyOtpRequest, db: AsyncSession = Depends(get_db)):

    user = await _get_user_by_email(db, payload.email)

    if not user:

        raise HTTPException(404, "No account found for this email.")



    _check_otp(user, payload.otp_code)

    user.otp_code = None

    user.otp_expires_at = None



    if not user.is_verified:

        user.is_verified = True

        user.credit_balance += SIGNUP_BONUS_CREDITS

        db.add(CreditTransaction(

            user_id=user.id, amount=SIGNUP_BONUS_CREDITS, balance_after=user.credit_balance,

            tx_type=CreditTxType.SIGNUP_BONUS, description="Email signup welcome bonus",

        ))



    await db.commit()

    await db.refresh(user)

    await update_last_login(db, user)

    return _issue_token(user)



@router.post("/resend-otp")

async def resend_otp(payload: ResendOtpRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):

    user = await _get_user_by_email(db, payload.email)

    if not user:

        raise HTTPException(404, "No account found.")

    if user.is_verified:

        raise HTTPException(400, "Account already verified.")



    await _send_and_store_otp(db, user, background_tasks)

    return {"message": "New OTP sent."}





# ✅ NEW — forgot password endpoints

@router.post("/forgot-password")

async def forgot_password(

    payload: ForgotPasswordRequest,

    background_tasks: BackgroundTasks,

    db: AsyncSession = Depends(get_db),

):

    """

    Sends a password-reset OTP to the user's email.

    Returns 200 even if email doesn't exist (prevents account enumeration).

    """

    user = await _get_user_by_email(db, payload.email)



    # Don't leak whether the email exists

    if not user:

        return {"message": "If this email exists, a reset code has been sent."}



    # Unverified users can't reset — they need to finish signup first

    if not user.is_verified:

        raise HTTPException(

            400,

            "Email not verified. Please complete signup verification first."

        )



    if not user.is_active:

        raise HTTPException(403, "Account disabled.")



    otp = _generate_otp()

    user.otp_code = otp

    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)

    await db.commit()



    background_tasks.add_task(send_password_reset_email, user.email, otp)



    return {"message": "If this email exists, a reset code has been sent."}





@router.post("/reset-password", response_model=Token)

async def reset_password(

    payload: ResetPasswordRequest,

    db: AsyncSession = Depends(get_db),

):

    """

    Verifies the reset OTP and sets a new password.

    Returns a fresh token so the user is logged in immediately.

    """

    user = await _get_user_by_email(db, payload.email)

    if not user:

        raise HTTPException(404, "No account found for this email.")

    if not user.is_verified:

        raise HTTPException(400, "Email not verified. Please complete signup first.")



    _check_otp(user, payload.otp_code)



    user.otp_code = None

    user.otp_expires_at = None

    user.hashed_password = get_password_hash(payload.new_password)



    await db.commit()

    await db.refresh(user)

    await update_last_login(db, user)



    return _issue_token(user)





@router.post("/register/enterprise-member", status_code=status.HTTP_201_CREATED)

async def register_enterprise_member(payload: EnterpriseMemberRegisterRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):

    email = payload.email.lower().strip()

    code = payload.referral_code.strip().upper()



    ent_stmt = select(Enterprise).where(Enterprise.referral_code == code, Enterprise.status == EnterpriseStatus.ACTIVE)

    enterprise = (await db.execute(ent_stmt)).scalar_one_or_none()

    if not enterprise:

        raise HTTPException(400, "Invalid or inactive company referral code.")



    member_count = await db.scalar(

        select(func.count(User.id)).where(User.enterprise_id == enterprise.id, User.role == UserRole.ENTERPRISE_MEMBER)

    )

    if member_count >= enterprise.member_limit:

        raise HTTPException(400, f"Company member limit reached ({enterprise.member_limit}). Contact your admin.")



    existing = await _get_user_by_email(db, email)

    if existing:

        if not existing.is_verified:

            await _send_and_store_otp(db, existing, background_tasks)

            return {"message": "Account exists but not verified. New OTP sent.", "email": email}

        raise HTTPException(400, "Email already registered.")



    user = User(

        email=email, hashed_password=get_password_hash(payload.password),

        full_name=payload.full_name.strip(), phone=payload.phone,

        date_of_birth=payload.date_of_birth, gender=payload.gender,

        role=UserRole.ENTERPRISE_MEMBER, subscription_tier=SubscriptionTier.ENTERPRISE,

        credit_balance=0, is_active=True, is_verified=False,

        enterprise_id=enterprise.id, member_approval_status=MemberApprovalStatus.PENDING,

    )

    db.add(user)

    await db.flush()

    await _send_and_store_otp(db, user, background_tasks)

    return {"message": "OTP sent. After verifying, your company admin must approve your account.", "email": email, "company": enterprise.name}



@router.post("/register/google", response_model=Token, status_code=status.HTTP_201_CREATED)

async def register_with_google(payload: GoogleRegisterRequest, db: AsyncSession = Depends(get_db)):

    return await login_with_google(GoogleLoginRequest(token=payload.token), db)



@router.post("/login", response_model=Token)

async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):

    user = await _get_user_by_email(db, payload.email)

    if not user or not verify_password(payload.password, user.hashed_password):

        raise HTTPException(401, "Incorrect email or password.")

    # ✅ Block unverified users — must finish OTP before issuing a token

    if not user.is_verified:

        raise HTTPException(

            403,

            "Email not verified. Please complete OTP verification before signing in.",

        )

    if not user.is_active:

        raise HTTPException(403, "Account disabled.")

    await update_last_login(db, user)

    return _issue_token(user)



@router.post("/login/google", response_model=Token)

@router.post("/google", response_model=Token)

async def login_with_google(payload: GoogleLoginRequest, db: AsyncSession = Depends(get_db)):

    id_info = _verify_google_token(payload.token)

    email = (id_info.get("email") or "").lower().strip()

    if not email:

        raise HTTPException(400, "Google account has no email.")



    user = await _get_user_by_email(db, email)

    if not user:

        # ✅ Auto-register new Google user (ChatGPT/Gemini style seamless signup)

        full_name = (id_info.get("name") or id_info.get("given_name") or "Google User").strip()

        picture = id_info.get("picture")

        user = User(

            email=email,

            hashed_password=f"GOOGLE_UNSET${get_password_hash(secrets.token_urlsafe(32))}",

            full_name=full_name,

            profile_photo_url=picture,

            role=UserRole.GENERAL,

            subscription_tier=SubscriptionTier.FREE,

            credit_balance=SIGNUP_BONUS_CREDITS,

            is_active=True,

            is_verified=True,

        )

        db.add(user)

        await db.flush()

        db.add(CreditTransaction(

            user_id=user.id,

            amount=SIGNUP_BONUS_CREDITS,

            balance_after=SIGNUP_BONUS_CREDITS,

            tx_type=CreditTxType.SIGNUP_BONUS,

            description="Google signup welcome bonus",

        ))

        await db.commit()

        await db.refresh(user)

        logger.info(f"[AUTH] Auto-registered Google user: {user.email}")

    elif not user.is_active:

        raise HTTPException(403, "Account disabled.")

    else:

        # If user existed but wasn't verified via email OTP, Google confirms their email

        if not user.is_verified:

            user.is_verified = True

            if user.credit_balance == 0:

                user.credit_balance = SIGNUP_BONUS_CREDITS

                db.add(CreditTransaction(

                    user_id=user.id,

                    amount=SIGNUP_BONUS_CREDITS,

                    balance_after=SIGNUP_BONUS_CREDITS,

                    tx_type=CreditTxType.SIGNUP_BONUS,

                    description="Google signup welcome bonus",

                ))

            await db.commit()

            await db.refresh(user)

        # Update picture if available and not set

        picture = id_info.get("picture")

        if not user.profile_photo_url and picture:

            user.profile_photo_url = picture

            await db.commit()

            await db.refresh(user)



    await update_last_login(db, user)

    return _issue_token(user)



@router.post("/onboard/lawyer")

async def onboard_as_lawyer(payload: OnboardLawyerRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):

    if current_user.role != UserRole.GENERAL:

        raise HTTPException(400, "You already have an active role. You cannot apply as a lawyer.")



    pending_ent = await db.scalar(select(Enterprise).where(Enterprise.pending_admin_user_id == current_user.id))

    if pending_ent:

        raise HTTPException(400, "You have a pending enterprise application. You cannot apply as a lawyer.")



    existing_profile = await db.scalar(select(LawyerProfile).where(LawyerProfile.user_id == current_user.id))

    if existing_profile:

        if existing_profile.status == LawyerStatus.APPROVED:

            raise HTTPException(400, "You are already an approved lawyer.")

        if existing_profile.status == LawyerStatus.PENDING_VERIFICATION:

            raise HTTPException(400, "You already have a pending lawyer application.")

        if existing_profile.status == LawyerStatus.REJECTED:

            raise HTTPException(400, "Your lawyer application was rejected. You cannot apply again.")

        raise HTTPException(400, "You already have a lawyer profile.")



    db.add(LawyerProfile(

        user_id=current_user.id, bar_council_id=payload.bar_council_id.strip(),

        experience_years=payload.experience_years, specializations=payload.specializations,

        status=LawyerStatus.PENDING_VERIFICATION,

    ))

    await db.commit()

    return {"status": "success", "message": "Application submitted. Awaiting admin verification."}



@router.post("/onboard/enterprise")

async def onboard_as_enterprise(payload: OnboardEnterpriseRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):

    if current_user.role != UserRole.GENERAL:

        raise HTTPException(400, "You already have an active role. You cannot register an enterprise.")



    existing_lawyer = await db.scalar(select(LawyerProfile).where(LawyerProfile.user_id == current_user.id))

    if existing_lawyer:

        raise HTTPException(400, "You already have a lawyer application. You cannot apply for an enterprise.")



    existing_ent = await db.scalar(select(Enterprise).where(Enterprise.pending_admin_user_id == current_user.id))

    if existing_ent:

        raise HTTPException(400, "You already have a pending enterprise application.")



    if await db.scalar(select(Enterprise).where(Enterprise.name == payload.company_name.strip())):

        raise HTTPException(400, "Company name already registered.")



    for _ in range(5):

        code = generate_referral_code(payload.company_name)

        if not await db.scalar(select(Enterprise).where(Enterprise.referral_code == code)):

            break

    else:

        raise HTTPException(500, "Could not generate unique referral code.")



    enterprise = Enterprise(

        name=payload.company_name.strip(), 

        company_type=payload.company_type,

        registration_no=payload.registration_no,

        tin_no=payload.tin_no,

        referral_code=code,

        status=EnterpriseStatus.PENDING_APPROVAL, 

        member_limit=payload.member_limit,

        credit_balance=0, 

        billing_email=payload.billing_email or current_user.email,

        billing_phone=payload.billing_phone or current_user.phone,

        pending_admin_user_id=current_user.id,

    )

    db.add(enterprise)
    await db.flush()
    current_user.enterprise_id = enterprise.id
    current_user.company_role = CompanyRole.ADMIN
    current_user.member_approval_status = MemberApprovalStatus.PENDING
    await db.commit()
    await db.refresh(enterprise)

    return {"status": "success", "message": "Enterprise registered. Awaiting system admin approval.", "referral_code": enterprise.referral_code, "enterprise_id": enterprise.id}





@router.get("/me", response_model=UserResponse)

async def get_me(

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db),

):

    """

    Returns the authenticated user with nested lawyer_profile (for lawyers),

    enterprise_credit_balance (for enterprise admins/members), and

    enterprise_profile (company name, RJSC, TIN, referral code, etc.).

    """

    response = UserResponse(

        id=current_user.id,

        email=current_user.email,

        full_name=current_user.full_name,

        phone=current_user.phone,

        role=current_user.role,

        subscription_tier=current_user.subscription_tier,

        credit_balance=current_user.credit_balance,

        enterprise_credit_balance=None,

        member_credit_quota=None,

        member_credit_used=None,

        is_active=current_user.is_active,

        is_verified=current_user.is_verified,
        has_usable_password=not (current_user.hashed_password or "").startswith("GOOGLE_UNSET$"),


        enterprise_id=current_user.enterprise_id,

        profile_photo_url=current_user.profile_photo_url,
        lawyer_profile=None,
        enterprise_profile=None,   # ✅ NEW default
        member_approval_status=current_user.member_approval_status.value if current_user.member_approval_status else None,
    )



    # ✅ Fetch Company Pool Balance + Enterprise Profile for Enterprise Roles
    if current_user.role in (UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER) and current_user.enterprise_id:
        enterprise = await db.scalar(
            select(Enterprise).where(Enterprise.id == current_user.enterprise_id)
        )
        if enterprise:
            response.enterprise_credit_balance = enterprise.credit_balance
            # ✅ NEW — populate nested enterprise profile for frontend
            response.enterprise_profile = EnterpriseProfileResponse(
                company_name=enterprise.name,
                company_type=enterprise.company_type,
                registration_no=enterprise.registration_no,
                tin_no=enterprise.tin_no,
                refer_code=enterprise.referral_code,
                member_limit=enterprise.member_limit,
                verification_status="verified" if enterprise.status == EnterpriseStatus.ACTIVE else "pending",
                is_team_member=(current_user.role == UserRole.ENTERPRISE_MEMBER),
                member_approval_status=current_user.member_approval_status.value if current_user.member_approval_status else None,
            )

    # ✅ Check if user has an enterprise application awaiting admin approval
    if not response.enterprise_profile:
        pending_enterprise = await db.scalar(
            select(Enterprise).where(
                (Enterprise.pending_admin_user_id == current_user.id) |
                (Enterprise.id == current_user.enterprise_id)
            ).order_by(Enterprise.id.desc())
        )
        if pending_enterprise:
            status_str = "pending"
            if pending_enterprise.status == EnterpriseStatus.ACTIVE:
                status_str = "verified"
            elif pending_enterprise.status == EnterpriseStatus.SUSPENDED:
                status_str = "rejected"

            response.enterprise_profile = EnterpriseProfileResponse(
                company_name=pending_enterprise.name,
                company_type=pending_enterprise.company_type,
                registration_no=pending_enterprise.registration_no,
                tin_no=pending_enterprise.tin_no,
                refer_code=pending_enterprise.referral_code,
                member_limit=pending_enterprise.member_limit,
                verification_status=status_str,
                is_team_member=False,
            )



    # ✅ NEW: Populate Member Quota Details (Only for ENTERPRISE_MEMBER)

    if current_user.role == UserRole.ENTERPRISE_MEMBER:

        response.member_credit_quota = current_user.member_credit_quota

        response.member_credit_used = current_user.member_credit_used



    # ✅ Fetch Lawyer Profile (for approved lawyers or pending applicants)
    profile = await db.scalar(
        select(LawyerProfile).where(LawyerProfile.user_id == current_user.id)
    )
    if profile:
        response.lawyer_profile = LawyerInfoResponse(
            bar_council_id=profile.bar_council_id,
            experience_years=profile.experience_years,
            specializations=profile.specializations or [],
            verification_status=_LAWYER_STATUS_MAP.get(
                profile.status.value, "pending"
            ),
        )
    elif current_user.role == UserRole.LAWYER:
        logger.warning(
            f"[get_me] LAWYER role user {current_user.id} has no LawyerProfile row"
        )



    return response





@router.delete("/me")

async def delete_my_account(

    db: AsyncSession = Depends(get_db),

    current_user: User = Depends(get_current_user)

):

    """

    Soft deletes the current user's account and anonymizes their PII data.

    """

    if current_user.is_deleted:

        raise HTTPException(400, "Account is already deleted.")



    current_user.is_deleted = True

    current_user.is_active = False

    current_user.full_name = "Deleted User"

    current_user.email = f"deleted_{current_user.id}_{current_user.email}"

    current_user.phone = None

    current_user.profile_photo_url = None

    current_user.hashed_password = "DELETED_ACCOUNT"

    current_user.otp_code = None

    current_user.otp_expires_at = None



    if current_user.member_credit_quota:

        current_user.member_credit_quota = 0

        current_user.member_credit_used = 0



    await db.commit()

    return {"status": "success", "message": "Your account and personal data have been permanently deleted."}