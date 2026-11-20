import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import check_rate_limit, client_ip
from app.core.security import get_current_user_optional, get_current_user
from app.models.user import User, UserRole
from app.models.inquiry import FeedbackSuggestion, NewsletterSubscriber

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inquiries", tags=["Inquiries & Feedback"])


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class FeedbackCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    contact: str = Field(min_length=5, max_length=150)
    suggestion: str = Field(min_length=5, max_length=3000)

    @field_validator("name", "contact", "suggestion")
    @classmethod
    def clean_text(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Field cannot be empty or only spaces.")
        return trimmed


class NewsletterSubscribeRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_contact(cls, v: str) -> str:
        clean = v.strip().lower()
        if len(clean) < 5:
            raise ValueError("Email address or contact is too short.")
        return clean


class FeedbackStatusUpdateRequest(BaseModel):
    status: str = Field(pattern="^(NEW|REVIEWED|RESOLVED|ARCHIVED)$")


# ──────────────────────────────────────────────────────────
# PUBLIC ROUTES
# ──────────────────────────────────────────────────────────

@router.post("/feedback")
async def submit_feedback(
    payload: FeedbackCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Public endpoint: Anyone can submit a suggestion or feedback.
    If authenticated, their user_id is automatically associated.
    """
    check_rate_limit(
        f"inquiries:feedback:{client_ip(request)}",
        max_requests=10,
        window_seconds=60,
        detail="Too many feedback submissions. Please wait a minute.",
    )
    feedback = FeedbackSuggestion(
        name=payload.name,
        contact=payload.contact,
        suggestion=payload.suggestion,
        user_id=current_user.id if current_user else None,
        status="NEW"
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    logger.info(f"Feedback #{feedback.id} received from '{feedback.name}' ({feedback.contact})")

    return {
        "status": "success",
        "message": "Thank you! Your feedback has been received.",
        "feedback_id": feedback.id
    }


@router.post("/newsletter")
async def subscribe_newsletter(
    payload: NewsletterSubscribeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Public endpoint: Subscribe to statutory updates, high court precedents, and weekly insights.
    """
    check_rate_limit(
        f"inquiries:newsletter:{client_ip(request)}",
        max_requests=10,
        window_seconds=60,
        detail="Too many subscription attempts. Please wait a minute.",
    )
    email_clean = payload.email.strip().lower()

    existing = await db.scalar(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == email_clean)
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            await db.commit()
            return {
                "status": "success",
                "message": "Welcome back! Your newsletter subscription is reactivated.",
                "is_subscribed": True
            }
        return {
            "status": "success",
            "message": "You are already subscribed to our newsletter updates.",
            "is_subscribed": True
        }

    subscriber = NewsletterSubscriber(
        email=email_clean,
        is_active=True,
        source="landing_page"
    )
    db.add(subscriber)
    await db.commit()
    await db.refresh(subscriber)
    logger.info(f"New newsletter subscriber: {subscriber.email}")

    return {
        "status": "success",
        "message": "Successfully subscribed to our legal insights newsletter!",
        "is_subscribed": True
    }


# ──────────────────────────────────────────────────────────
# ADMIN ONLY ROUTES
# ──────────────────────────────────────────────────────────

@router.get("/feedback", response_model=List[dict])
async def list_feedback_items(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin only: View submitted suggestions and feedback."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access restricted to administrators.")

    stmt = select(FeedbackSuggestion).order_by(desc(FeedbackSuggestion.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": f.id,
            "name": f.name,
            "contact": f.contact,
            "suggestion": f.suggestion,
            "user_id": f.user_id,
            "status": f.status,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in rows
    ]


@router.patch("/feedback/{feedback_id}/status")
async def update_feedback_status(
    feedback_id: int,
    payload: FeedbackStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin only: Update status of a feedback item (e.g. REVIEWED, RESOLVED)."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access restricted to administrators.")

    feedback = await db.scalar(
        select(FeedbackSuggestion).where(FeedbackSuggestion.id == feedback_id)
    )
    if not feedback:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feedback item not found.")

    feedback.status = payload.status
    await db.commit()
    return {"status": "success", "message": f"Status updated to {payload.status}."}


@router.get("/subscribers", response_model=List[dict])
async def list_subscribers(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin only: View list of newsletter subscribers."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access restricted to administrators.")

    stmt = select(NewsletterSubscriber).order_by(desc(NewsletterSubscriber.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": s.id,
            "email": s.email,
            "is_active": s.is_active,
            "source": s.source,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in rows
    ]
