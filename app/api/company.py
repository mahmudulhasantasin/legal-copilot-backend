import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.user import (
    User, UserRole, CompanyRole, MemberApprovalStatus,
    CreditTransaction, CreditTxType,
)
from app.models.playbook import Enterprise, EnterpriseStatus, CompanyPlaybook
from app.models.memo import Contract, ContractMemo
from app.models.audit import AuditLog, AuditAction, AuditSeverity
from app.services.playbook_parser import extract_text_from_file, parse_playbook_to_rules
from app.services.email_service import send_member_approved_email, send_member_rejected_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/company", tags=["Enterprise Admin"])


# ──────────────────────────────────────────────────────────
# GUARDS & HELPERS
# ──────────────────────────────────────────────────────────

async def get_admin_enterprise(
    current_user: User = Depends(require_roles([UserRole.ENTERPRISE_ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> Enterprise:
    """Ensures the admin has a linked, active enterprise."""
    if not current_user.enterprise_id:
        raise HTTPException(400, "No enterprise linked to this account.")
    ent = await db.scalar(
        select(Enterprise).where(Enterprise.id == current_user.enterprise_id)
    )
    if not ent:
        raise HTTPException(404, "Enterprise not found.")
    if ent.status != EnterpriseStatus.ACTIVE:
        raise HTTPException(403, f"Enterprise is not active ({ent.status.value}).")
    return ent


# ✅ NEW: Guard to allow Enterprise Members to access read-only company data
async def get_enterprise_user(
    current_user: User = Depends(require_roles([UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER])),
    db: AsyncSession = Depends(get_db),
) -> Enterprise:
    """Allows both Enterprise Admins and Members to read company data."""
    if not current_user.enterprise_id:
        raise HTTPException(400, "No enterprise linked to this account.")
    ent = await db.scalar(
        select(Enterprise).where(Enterprise.id == current_user.enterprise_id)
    )
    if not ent:
        raise HTTPException(404, "Enterprise not found.")
    if ent.status != EnterpriseStatus.ACTIVE:
        raise HTTPException(403, f"Enterprise is not active ({ent.status.value}).")
    return ent


async def _get_member_or_404(
    db: AsyncSession, enterprise_id: int, user_id: int
) -> User:
    """Fetch a member that belongs to this enterprise. 404 otherwise."""
    member = await db.scalar(
        select(User).where(
            User.id == user_id,
            User.enterprise_id == enterprise_id,
            User.is_deleted == False,
        )
    )
    if not member:
        raise HTTPException(404, "Member not found in your company.")
    return member


async def _log_company_action(
    db: AsyncSession,
    actor: User,
    action: AuditAction,
    *,
    target_entity: Optional[str] = None,
    target_id: Optional[int] = None,
    description: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    severity: AuditSeverity = AuditSeverity.INFO,
) -> None:
    db.add(AuditLog(
        actor_id=actor.id,
        actor_role=actor.role.value,
        actor_email=actor.email,
        action=action,
        severity=severity,
        target_entity=target_entity,
        target_id=target_id,
        description=description,
        before_state=before,
        after_state=after,
    ))


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class ApproveMemberRequest(BaseModel):
    company_role: CompanyRole = CompanyRole.MEMBER
    department: Optional[str] = Field(default=None, max_length=100)
    initial_quota: int = Field(default=0, ge=0, le=100_000)


class RejectMemberRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class UpdateMemberRequest(BaseModel):
    company_role: Optional[CompanyRole] = None
    department: Optional[str] = Field(default=None, max_length=100)


class AdjustQuotaRequest(BaseModel):
    delta: int = Field(
        description="Signed: +200 grants more, -50 reduces. Cannot go below used."
    )
    reason: Optional[str] = Field(default=None, max_length=255)


# ──────────────────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────────────────

@router.get("/dashboard")
async def company_dashboard(
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """Overview cards for Enterprise Admin home."""
    async def _count(*where):
        return (await db.execute(
            select(func.count(User.id)).where(
                User.enterprise_id == ent.id,
                User.is_deleted == False,
                *where,
            )
        )).scalar_one() or 0

    total_members = await _count()
    active_members = await _count(
        (User.member_approval_status == MemberApprovalStatus.APPROVED) | (User.role == UserRole.ENTERPRISE_ADMIN)
    )
    pending_members = await _count(
        User.member_approval_status == MemberApprovalStatus.PENDING,
        User.role != UserRole.ENTERPRISE_ADMIN,
        User.company_role != CompanyRole.ADMIN,
    )

    # Total quota used this month
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    used_this_month = (await db.execute(
        select(func.coalesce(func.sum(CreditTransaction.amount * -1), 0))
        .where(
            CreditTransaction.enterprise_id == ent.id,
            CreditTransaction.amount < 0,
            CreditTransaction.created_at >= month_start,
        )
    )).scalar_one() or 0

    return {
        "enterprise": {
            "id": ent.id,
            "name": ent.name,
            "referral_code": ent.referral_code,
            "credit_balance": ent.credit_balance,
            "member_limit": ent.member_limit,
        },
        "members": {
            "total": total_members,
            "active": active_members,
            "pending_approval": pending_members,
            "slots_remaining": max(0, ent.member_limit - total_members),
        },
        "credits": {
            "pool_balance": ent.credit_balance,
            "used_this_month": int(used_this_month),
        },
    }


# ──────────────────────────────────────────────────────────
# MEMBER MANAGEMENT
# ──────────────────────────────────────────────────────────

@router.get("/members")
async def list_members(
    status_filter: Optional[str] = Query(None, alias="status"),
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """List all members of this enterprise with filters."""
    stmt = select(User).where(
        User.enterprise_id == ent.id, User.is_deleted == False
    )
    if status_filter:
        stmt = stmt.where(User.member_approval_status == MemberApprovalStatus(status_filter))
    if department:
        stmt = stmt.where(User.department == department)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(
            (func.lower(User.email).like(like)) |
            (func.lower(User.full_name).like(like))
        )
    stmt = stmt.order_by(desc(User.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "user_id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "phone": u.phone,
            "company_role": "ADMIN" if (u.role == UserRole.ENTERPRISE_ADMIN or u.company_role == CompanyRole.ADMIN) else (u.company_role.value if u.company_role else "MEMBER"),
            "department": u.department or ("Executive" if (u.role == UserRole.ENTERPRISE_ADMIN or u.company_role == CompanyRole.ADMIN) else None),
            "approval_status": "APPROVED" if (u.role == UserRole.ENTERPRISE_ADMIN or u.company_role == CompanyRole.ADMIN) else (u.member_approval_status.value if u.member_approval_status else "PENDING"),
            "quota_total": u.member_credit_quota,
            "quota_used": u.member_credit_used,
            "quota_remaining": u.remaining_member_quota,
            "joined_at": u.company_joined_at,
            "last_login_at": u.last_login_at,
            "is_active": u.is_active,
        }
        for u in rows
    ]


@router.get("/members/pending")
async def list_pending_members(
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """Pending membership requests (Image 5 'Pending Requests')."""
    stmt = (
        select(User)
        .where(
            User.enterprise_id == ent.id,
            User.member_approval_status == MemberApprovalStatus.PENDING,
            User.role != UserRole.ENTERPRISE_ADMIN,
            User.company_role != CompanyRole.ADMIN,
            User.is_deleted == False,
        )
        .order_by(User.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "user_id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "phone": u.phone,
            "requested_at": u.created_at,
            "is_verified": u.is_verified,
        }
        for u in rows
    ]


@router.get("/members/{user_id}")
async def get_member_detail(
    user_id: int,
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    member = await _get_member_or_404(db, ent.id, user_id)

    # Recent audit for this member within company
    logs_stmt = (
        select(AuditLog)
        .where(
            AuditLog.actor_id == member.id,
            AuditLog.timestamp >= datetime.now(timezone.utc) - timedelta(days=30),
        )
        .order_by(desc(AuditLog.timestamp))
        .limit(20)
    )
    logs = (await db.execute(logs_stmt)).scalars().all()

    # Contracts uploaded by this member
    contracts_count = (await db.execute(
        select(func.count(Contract.id)).where(
            Contract.user_id == member.id,
            Contract.enterprise_id == ent.id,
        )
    )).scalar_one() or 0

    return {
        "user_id": member.id,
        "email": member.email,
        "full_name": member.full_name,
        "phone": member.phone,
        "company_role": member.company_role.value if member.company_role else None,
        "department": member.department,
        "approval_status": member.member_approval_status.value if member.member_approval_status else None,
        "quota_total": member.member_credit_quota,
        "quota_used": member.member_credit_used,
        "quota_remaining": member.remaining_member_quota,
        "joined_at": member.company_joined_at,
        "last_login_at": member.last_login_at,
        "is_active": member.is_active,
        "contracts_uploaded": contracts_count,
        "recent_activity": [
            {
                "action": l.action.value,
                "description": l.description,
                "timestamp": l.timestamp,
            }
            for l in logs
        ],
    }


@router.post("/members/{user_id}/approve")
async def approve_member(
    user_id: int,
    payload: ApproveMemberRequest,
    background_tasks: BackgroundTasks,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Approves a pending member. Enforces member_limit and pool balance.
    Optionally sets initial quota.
    """
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.member_approval_status != MemberApprovalStatus.PENDING:
        raise HTTPException(
            400,
            f"Cannot approve. Status: {member.member_approval_status.value if member.member_approval_status else 'NONE'}",
        )

    # Enforce member limit
    approved_count = (await db.execute(
        select(func.count(User.id)).where(
            User.enterprise_id == ent.id,
            User.member_approval_status == MemberApprovalStatus.APPROVED,
        )
    )).scalar_one() or 0
    if approved_count >= ent.member_limit:
        raise HTTPException(
            400,
            f"Member limit reached ({ent.member_limit}). Contact System Admin to increase.",
        )

    # Enforce initial quota doesn't exceed pool
    if payload.initial_quota > ent.credit_balance:
        raise HTTPException(
            400,
            f"Initial quota ({payload.initial_quota}) exceeds pool balance ({ent.credit_balance}).",
        )

    before = {
        "status": member.member_approval_status.value,
        "quota": member.member_credit_quota,
    }

    member.member_approval_status = MemberApprovalStatus.APPROVED
    member.company_role = payload.company_role
    member.department = payload.department
    member.member_credit_quota = payload.initial_quota
    member.member_credit_used = 0
    member.company_joined_at = datetime.now(timezone.utc)

    await _log_company_action(
        db, admin, AuditAction.MEMBER_APPROVED,
        target_entity="user", target_id=member.id,
        description=f"Approved {member.email} as {payload.company_role.value}"
                    + (f" (dept: {payload.department})" if payload.department else "")
                    + (f", quota={payload.initial_quota}" if payload.initial_quota else ""),
        before=before,
        after={
            "status": member.member_approval_status.value,
            "quota": member.member_credit_quota,
        },
    )
    background_tasks.add_task(
        send_member_approved_email,
        member.email,
        member.full_name or "Team Member",
        ent.name,
        payload.company_role.value if payload.company_role else "Member",
    )

    await db.commit()
    return {"status": "success", "user_id": member.id}


@router.post("/members/{user_id}/reject")
async def reject_member(
    user_id: int,
    payload: RejectMemberRequest,
    background_tasks: BackgroundTasks,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.member_approval_status != MemberApprovalStatus.PENDING:
        raise HTTPException(400, "Only PENDING members can be rejected.")

    member.member_approval_status = MemberApprovalStatus.REJECTED
    member.enterprise_id = None
    member.company_role = None

    background_tasks.add_task(
        send_member_rejected_email,
        member.email,
        member.full_name or "Team Member",
        ent.name,
        payload.reason,
    )

    await _log_company_action(
        db, admin, AuditAction.MEMBER_REJECTED,
        target_entity="user", target_id=member.id,
        description=f"Rejected {member.email}: {payload.reason}",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


@router.post("/members/{user_id}/suspend")
async def suspend_member(
    user_id: int,
    payload: RejectMemberRequest,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.member_approval_status != MemberApprovalStatus.APPROVED:
        raise HTTPException(400, "Only APPROVED members can be suspended.")

    member.member_approval_status = MemberApprovalStatus.SUSPENDED

    await _log_company_action(
        db, admin, AuditAction.MEMBER_SUSPENDED,
        target_entity="user", target_id=member.id,
        description=f"Suspended {member.email}: {payload.reason}",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


@router.post("/members/{user_id}/reactivate")
async def reactivate_member(
    user_id: int,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.member_approval_status != MemberApprovalStatus.SUSPENDED:
        raise HTTPException(400, "Only SUSPENDED members can be reactivated.")

    member.member_approval_status = MemberApprovalStatus.APPROVED

    await _log_company_action(
        db, admin, AuditAction.MEMBER_APPROVED,
        target_entity="user", target_id=member.id,
        description=f"Reactivated {member.email}",
    )
    await db.commit()
    return {"status": "success"}


@router.post("/members/{user_id}/remove")
async def remove_member(
    user_id: int,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Removes a member. Their used quota stays consumed.
    Remaining quota is released.
    """
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.id == admin.id:
        raise HTTPException(400, "You cannot remove yourself.")

    before = {
        "enterprise_id": member.enterprise_id,
        "quota": member.member_credit_quota,
        "used": member.member_credit_used,
    }

    member.enterprise_id = None
    member.company_role = None
    member.department = None
    member.member_approval_status = None
    member.member_credit_quota = 0
    member.member_credit_used = 0
    member.role = UserRole.GENERAL

    await _log_company_action(
        db, admin, AuditAction.MEMBER_REJECTED,
        target_entity="user", target_id=member.id,
        description=f"Removed {member.email} from company",
        before=before,
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


@router.patch("/members/{user_id}")
async def update_member(
    user_id: int,
    payload: UpdateMemberRequest,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update member's role and/or department."""
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.member_approval_status != MemberApprovalStatus.APPROVED:
        raise HTTPException(400, "Only APPROVED members can be updated.")

    before = {
        "company_role": member.company_role.value if member.company_role else None,
        "department": member.department,
    }

    if payload.company_role is not None:
        member.company_role = payload.company_role
    if payload.department is not None:
        member.department = payload.department.strip() or None

    await _log_company_action(
        db, admin, AuditAction.MEMBER_APPROVED,
        target_entity="user", target_id=member.id,
        description=f"Updated {member.email}",
        before=before,
        after={
            "company_role": member.company_role.value if member.company_role else None,
            "department": member.department,
        },
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# QUOTA ALLOCATION (SOFT CAP)
# ──────────────────────────────────────────────────────────

@router.post("/members/{user_id}/quota")
async def adjust_member_quota(
    user_id: int,
    payload: AdjustQuotaRequest,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Adjusts a member's quota by a signed delta.
    IMPORTANT: Credits stay in the enterprise pool. The quota is a LIMIT.
    """
    member = await _get_member_or_404(db, ent.id, user_id)

    if member.member_approval_status != MemberApprovalStatus.APPROVED:
        raise HTTPException(400, "Can only adjust quota for APPROVED members.")

    if payload.delta == 0:
        raise HTTPException(400, "Delta must be non-zero.")

    ent_locked = await db.scalar(
        select(Enterprise).where(Enterprise.id == ent.id).with_for_update()
    )
    member_locked = await db.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )

    new_quota = member_locked.member_credit_quota + payload.delta

    if new_quota < member_locked.member_credit_used:
        raise HTTPException(
            400,
            f"New quota ({new_quota}) would be below used ({member_locked.member_credit_used}). "
            "Used credits cannot be revoked.",
        )

    before_quota = member_locked.member_credit_quota
    member_locked.member_credit_quota = new_quota

    db.add(CreditTransaction(
        enterprise_id=ent.id,
        user_id=member_locked.id,
        amount=0,
        balance_after=ent_locked.credit_balance,
        tx_type=CreditTxType.ENTERPRISE_ALLOCATION if payload.delta > 0 else CreditTxType.ENTERPRISE_REVOKE,
        description=(
            f"Quota {'+' if payload.delta > 0 else ''}{payload.delta} for {member_locked.email}"
            + (f": {payload.reason}" if payload.reason else "")
        ),
    ))

    await _log_company_action(
        db, admin,
        AuditAction.ENTERPRISE_QUOTA_ALLOCATED if payload.delta > 0
        else AuditAction.ENTERPRISE_QUOTA_REVOKED,
        target_entity="user", target_id=member_locked.id,
        description=f"Quota {before_quota} → {new_quota} for {member_locked.email}",
        before={"quota": before_quota},
        after={"quota": new_quota},
    )
    await db.commit()
    return {
        "status": "success",
        "user_id": member_locked.id,
        "quota_total": new_quota,
        "quota_used": member_locked.member_credit_used,
        "quota_remaining": max(0, new_quota - member_locked.member_credit_used),
    }


@router.get("/quota/summary")
async def quota_summary(
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """Total allocated vs pool balance."""
    row = (await db.execute(
        select(
            func.coalesce(func.sum(User.member_credit_quota), 0).label("total_quota"),
            func.coalesce(func.sum(User.member_credit_used), 0).label("total_used"),
        ).where(
            User.enterprise_id == ent.id,
            User.member_approval_status == MemberApprovalStatus.APPROVED,
        )
    )).first()

    total_quota = int(row.total_quota or 0)
    total_used = int(row.total_used or 0)

    return {
        "pool_balance": ent.credit_balance,
        "total_quota_allocated": total_quota,
        "total_quota_used": total_used,
        "total_quota_remaining": max(0, total_quota - total_used),
        "unallocated_pool": max(0, ent.credit_balance - (total_quota - total_used)),
    }


# ──────────────────────────────────────────────────────────
# PLAYBOOK MANAGEMENT
# ──────────────────────────────────────────────────────────

@router.post("/playbook/upload")
async def upload_company_playbook(
    title: str = Form(...),
    file: UploadFile = File(...),
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Uploads policy doc, parses into rules via LLM."""
    file_bytes = await file.read()
    raw_text = await extract_text_from_file(file.filename, file_bytes)

    if not raw_text.strip():
        raise HTTPException(400, "Failed to extract text from the document.")

    rules = await parse_playbook_to_rules(raw_text)

    old_active = (await db.execute(
        select(CompanyPlaybook).where(
            CompanyPlaybook.enterprise_id == ent.id,
            CompanyPlaybook.is_active == True,
        )
    )).scalars().all()
    for old in old_active:
        old.is_active = False

    playbook = CompanyPlaybook(
        enterprise_id=ent.id,
        title=title.strip(),
        rules_json=rules,
        is_active=True,
    )
    db.add(playbook)
    await db.flush()

    await _log_company_action(
        db, admin, AuditAction.ENTERPRISE_APPROVED,
        target_entity="company_playbook", target_id=playbook.id,
        description=f"Uploaded playbook '{title}' with {len(rules)} rules",
    )
    await db.commit()
    await db.refresh(playbook)

    return {
        "status": "success",
        "playbook_id": playbook.id,
        "title": playbook.title,
        "rules_extracted_count": len(rules),
        "rules": rules,
    }


@router.get("/playbooks")
async def list_playbooks(
    ent: Enterprise = Depends(get_enterprise_user), # ✅ Changed from get_admin_enterprise
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(CompanyPlaybook)
        .where(CompanyPlaybook.enterprise_id == ent.id)
        .order_by(desc(CompanyPlaybook.created_at))
    )).scalars().all()
    return [
        {
            "id": p.id,
            "title": p.title,
            "is_active": p.is_active,
            "rules_count": len(p.rules_json or []),
            "created_at": p.created_at,
        }
        for p in rows
    ]


@router.post("/playbooks/{playbook_id}/activate")
async def activate_playbook(
    playbook_id: int,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pb = await db.scalar(
        select(CompanyPlaybook).where(
            CompanyPlaybook.id == playbook_id,
            CompanyPlaybook.enterprise_id == ent.id,
        )
    )
    if not pb:
        raise HTTPException(404, "Playbook not found.")

    others = (await db.execute(
        select(CompanyPlaybook).where(
            CompanyPlaybook.enterprise_id == ent.id,
            CompanyPlaybook.is_active == True,
            CompanyPlaybook.id != playbook_id,
        )
    )).scalars().all()
    for o in others:
        o.is_active = False

    pb.is_active = True

    await _log_company_action(
        db, admin, AuditAction.ENTERPRISE_APPROVED,
        target_entity="company_playbook", target_id=pb.id,
        description=f"Activated playbook '{pb.title}'",
    )
    await db.commit()
    return {"status": "success"}


@router.delete("/playbooks/{playbook_id}")
async def delete_playbook(
    playbook_id: int,
    ent: Enterprise = Depends(get_admin_enterprise),
    admin: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pb = await db.scalar(
        select(CompanyPlaybook).where(
            CompanyPlaybook.id == playbook_id,
            CompanyPlaybook.enterprise_id == ent.id,
        )
    )
    if not pb:
        raise HTTPException(404, "Playbook not found.")

    await db.delete(pb)
    await _log_company_action(
        db, admin, AuditAction.ENTERPRISE_REJECTED,
        target_entity="company_playbook", target_id=playbook_id,
        description=f"Deleted playbook '{pb.title}'",
        severity=AuditSeverity.WARNING,
    )
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# ANALYTICS
# ──────────────────────────────────────────────────────────

@router.get("/analytics/usage")
async def company_usage_analytics(
    days: int = Query(30, ge=1, le=365),
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate credit spend over the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (await db.execute(
        select(
            func.date(CreditTransaction.created_at).label("day"),
            func.sum(CreditTransaction.amount * -1).label("credits_spent"),
            func.count(CreditTransaction.id).label("tx_count"),
        )
        .where(
            CreditTransaction.enterprise_id == ent.id,
            CreditTransaction.amount < 0,
            CreditTransaction.created_at >= since,
        )
        .group_by(func.date(CreditTransaction.created_at))
        .order_by(func.date(CreditTransaction.created_at).asc())
    )).all()

    return {
        "days": days,
        "series": [
            {
                "date": r.day.isoformat() if hasattr(r.day, "isoformat") else str(r.day),
                "credits_spent": int(r.credits_spent or 0),
                "transactions": int(r.tx_count or 0),
            }
            for r in rows
        ],
    }


@router.get("/analytics/members")
async def member_usage_analytics(
    days: int = Query(30, ge=1, le=365),
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """Per-member credit usage ranking."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (await db.execute(
        select(
            User.id, User.email, User.full_name,
            func.coalesce(func.sum(CreditTransaction.amount * -1), 0).label("credits_spent"),
        )
        .join(CreditTransaction, CreditTransaction.user_id == User.id)
        .where(
            User.enterprise_id == ent.id,
            CreditTransaction.created_at >= since,
            CreditTransaction.amount < 0,
        )
        .group_by(User.id, User.email, User.full_name)
        .order_by(desc(func.sum(CreditTransaction.amount * -1)))
        .limit(50)
    )).all()

    return [
        {
            "user_id": r.id,
            "email": r.email,
            "full_name": r.full_name,
            "credits_spent": int(r.credits_spent or 0),
        }
        for r in rows
    ]

# ──────────────────────────────────────────────────────────
# AUDIT LOGS & SHARED CONTRACTS (NEW)
# ──────────────────────────────────────────────────────────

@router.get("/audit-logs")
async def get_company_audit_logs(
    limit: int = 20,
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """Enterprise Admin der jonno privacy-safe audit log."""
    stmt = (
        select(CreditTransaction, User)
        .join(User, CreditTransaction.user_id == User.id)
        .where(
            CreditTransaction.enterprise_id == ent.id,
            CreditTransaction.amount < 0, # Only spends
        )
        .order_by(desc(CreditTransaction.created_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    return [
        {
            "id": tx.id,
            "timestamp": tx.created_at.isoformat() if tx.created_at else None,
            "member_name": user.full_name,
            "department": user.department or "Operations",
            "module": tx.tx_type.value.replace("_", " ").title(),
            "category": tx.description or "Enterprise Usage",
            "credits_consumed": abs(tx.amount),
        }
        for tx, user in rows
    ]


@router.get("/shared-contracts")
async def get_shared_contracts(
    ent: Enterprise = Depends(get_admin_enterprise),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch ALL contracts audited by members of this enterprise.
    (No longer filtering by is_workspace_shared since all member audits are visible to admin).
    """
    stmt = (
        select(Contract, ContractMemo, User)
        .join(ContractMemo, Contract.id == ContractMemo.contract_id)
        .join(User, Contract.user_id == User.id)
        .where(
            Contract.enterprise_id == ent.id,
            # Contract.is_workspace_shared == True,  <-- REMOVED
            Contract.deleted_at == None,
        )
        .order_by(desc(Contract.created_at))
    )
    rows = (await db.execute(stmt)).all()

    return [
        {
            "id": c.id,
            "contract_id": c.id,
            "title": c.title,
            "category": m.clauses_analysis[0].get("clause_title", "General") if m.clauses_analysis else "General",
            "submitted_by": u.full_name,
            "submitted_at": c.created_at.isoformat() if c.created_at else None,
            "risk_score": m.risk_score,
            "status": "approved" if m.hitl_status.value == "COMPLETED" else "pending",
            "advocate_verified": m.hitl_status.value == "COMPLETED",
        }
        for c, m, u in rows
    ]