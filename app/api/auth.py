from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user
)
from app.models.user import User, UserRole

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# ----------------------------
# Pydantic Schemas
# ----------------------------
class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    role: str = "general"

class UserResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: str
    is_active: bool

    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str

# ----------------------------
# Authentication Endpoints
# ----------------------------
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account"
)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    # Verify email uniqueness
    existing_user = db.query(User).filter(User.email == req.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered"
        )

    # Normalize role input to UserRole Enum
    role_map = {
        "general": UserRole.GENERAL,
        "lawyer": UserRole.LAWYER,
        "company": UserRole.COMPANY,
        "admin": UserRole.ADMIN
    }
    user_role = role_map.get(req.role.lower(), UserRole.GENERAL)

    new_user = User(
        full_name=req.full_name,
        email=req.email,
        hashed_password=hash_password(req.password),
        role=user_role,
        is_active=True
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "full_name": new_user.full_name,
        "email": new_user.email,
        "role": new_user.role.value if hasattr(new_user.role, "value") else str(new_user.role),
        "is_active": new_user.is_active
    }


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="OAuth2 Password Flow Login"
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Standard OAuth2 token endpoint compliant with RFC 6749 and Swagger UI authorization.
    Accepts `username` (user email) and `password`.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
    token_payload = {
        "sub": user.email,
        "role": role_str,
        "user_id": user.id
    }
    
    access_token = create_access_token(data=token_payload)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": role_str
    }


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Fetch current logged-in user details"
)
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        "is_active": current_user.is_active
    }