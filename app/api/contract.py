import os
import re
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import create_client, Client

from app.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, check_and_deduct_credits, refund_credits
from app.core.pricing import get_audit_cost, get_service_cost, get_lawyer_verification_split
from app.models.user import User, UserRole, CreditTxType
from app.models.lawyer import LawyerProfile           # ✅ NEW — for GET /{id}
from app.models.memo import Contract, ContractMemo, RiskLevel, HITLStatus
from app.models.playbook import CompanyPlaybook
from app.services.ingestion import extract_text_from_file
from app.agents.contract_graph import analyze_contract_risk_async
from app.services.lawyer_assignment import generate_initial_offer
from app.services.metrics import track_event, timed_event # ✅ নতুন ইমপোর্ট

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/contracts", tags=["Contract Audit & HITL"])

# ──────────────────────────────────────────────────────────
# SUPABASE SETUP
# ──────────────────────────────────────────────────────────
SUPABASE_URL = getattr(settings, "SUPABASE_URL", "")
SUPABASE_KEY = getattr(settings, "SUPABASE_SERVICE_KEY", "")
BUCKET_NAME = "contracts"


def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase credentials missing. File uploads will fail.")
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class ClauseAnalysisItem(BaseModel):
    clause_number: str
    clause_title: str
    risk_level: str
    original_text: str
    statute_reference: Optional[str] = None
    legal_explanation: Optional[str] = None
    proposed_safe_redline: Optional[str] = None
    is_compliant: bool = False
    statutory_status: Optional[str] = None
    is_statute_verified: Optional[bool] = False
    commercial_risk: Optional[str] = None
    negotiation_guidance: Optional[str] = None


class ContractMemoResponse(BaseModel):
    memo_id: int
    contract_id: int
    title: str
    overall_risk: RiskLevel
    risk_grade: str = "MODERATE"
    risk_score: int
    executive_summary: str
    counts: Dict[str, int]
    clauses: List[ClauseAnalysisItem]
    missing_provisions: Optional[List[Dict[str, Any]]] = None
    audited_for: Optional[str] = "CLIENT"
    stamp_duty_advisory: Optional[str] = None
    hitl_status: HITLStatus
    lawyer_name: Optional[str] = None
    lawyer_notes: Optional[str] = None
    lawyer_signature: Optional[str] = None
    completed_at: Optional[datetime] = None


class RequestVerificationResponse(BaseModel):
    status: str
    memo_id: int
    message: str
    credits_deducted: int


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def _calculate_metrics(clauses: List[dict], missing_provisions: Optional[List[dict]] = None) -> tuple[Dict[str, int], int, RiskLevel, str]:
    high = sum(1 for c in clauses if str(c.get("risk_level", "")).lower() in ["high risk", "high", "critical"])
    review = sum(1 for c in clauses if str(c.get("risk_level", "")).lower() in ["review needed", "moderate", "medium"])
    compliant = sum(1 for c in clauses if c.get("is_compliant") or str(c.get("risk_level", "")).lower() in ["compliant", "low"])
    missing_count = len(missing_provisions or [])

    # Transparent, bounded, monotonic risk score (0-100)
    raw_score = high * 30 + missing_count * 15 + review * 10
    score = min(100, max(10 if (high > 0 or review > 0 or missing_count > 0) else 0, raw_score))

    if high > 0 or score >= 60:
        overall = RiskLevel.HIGH
        risk_grade = "HIGH"
    elif review > 0 or missing_count > 0 or score >= 35:
        overall = RiskLevel.MODERATE
        risk_grade = "MODERATE"
    else:
        overall = RiskLevel.LOW
        risk_grade = "LOW"

    effective_compliant = max(compliant, len(clauses) - high - review)
    return {"high_risk": high, "review_needed": review, "compliant": effective_compliant, "missing_count": missing_count}, score, overall, risk_grade


async def _upload_to_supabase(file_bytes: bytes, filename: str, user_id: int, content_type: str) -> str:
    """Uploads file to Supabase and returns the storage path."""
    supabase = get_supabase()
    if not supabase:
        raise HTTPException(500, "Storage service is not configured.")

    safe_filename = re.sub(r'[^a-zA-Z0-9.\-]', '_', filename)
    safe_name = f"u_{user_id}_{uuid.uuid4().hex[:8]}_{safe_filename}"
    file_path = safe_name

    try:
        res = supabase.storage.from_(BUCKET_NAME).upload(
            path=file_path,
            file=file_bytes,
            file_options={"content-type": content_type or "application/octet-stream"}
        )
        return file_path
    except Exception as e:
        logger.error(f"[Supabase] Upload failed: {e}")
        raise HTTPException(500, f"Failed to upload document to secure storage: {str(e)}")


# ──────────────────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────────────────

@router.post("/upload", response_model=ContractMemoResponse)
async def upload_and_audit(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    audited_for: Optional[str] = Form("CLIENT"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    allowed_types = [".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".txt"]
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_types:
        raise HTTPException(400, f"Unsupported file format. Allowed: {', '.join(allowed_types)}")

    MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

    # ফাইল সাইস চেক — credit deduct হওয়ার আগে
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max 20MB allowed. Your file: {round(len(file_bytes)/(1024*1024),1)}MB."
        )

    cost = get_audit_cost(current_user.role)
    deduction = await check_and_deduct_credits(
        user=current_user,
        db=db,
        cost=cost,
        tx_type=CreditTxType.CONTRACT_AUDIT,
        description=f"AI Audit: {file.filename[:30]}",
        commit=False
    )

    storage_path = await _upload_to_supabase(
        file_bytes, file.filename, current_user.id, file.content_type
    )

    try:
        raw_text = await extract_text_from_file(file.filename, file_bytes)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        logger.error(f"[Ingestion] Extraction failed: {e}")
        raise HTTPException(400, "Could not read text from the uploaded file.")

    if not raw_text.strip():
        raise HTTPException(400, "The uploaded document contains no readable text.")

    playbook_rules = []
    if current_user.is_enterprise_user and current_user.enterprise_id:
        pb = await db.scalar(
            select(CompanyPlaybook).where(
                CompanyPlaybook.enterprise_id == current_user.enterprise_id,
                CompanyPlaybook.is_active == True
            )
        )
        if pb and pb.rules_json:
            playbook_rules = pb.rules_json

    # ✅ METRICS: Track Audit Latency
    async with timed_event(
        db, "audit_completed",
        user_id=current_user.id,
        event_data={"file_ext": ext, "has_playbook": bool(playbook_rules), "audited_for": audited_for},
        commit=False
    ):
        agent_output = await analyze_contract_risk_async(
            contract_text=raw_text,
            playbook_rules=playbook_rules,
            audited_for=audited_for or "CLIENT"
        )

    detected_risks = agent_output.get("detected_risks", [])
    missing_provisions = agent_output.get("missing_provisions", [])
    stamp_duty_advisory = agent_output.get("stamp_duty_advisory", "")
    memo_summary = agent_output.get("memo_summary", "Contract analyzed successfully.")

    ui_clauses = []
    for idx, risk in enumerate(detected_risks, start=1):
        severity = str(risk.get("severity", "MODERATE")).upper()
        is_comp = severity in ["LOW", "COMPLIANT"] or risk.get("is_compliant", False)

        # ✅ CANONICAL RISK LABELS — must match frontend `normalizeRiskLevel()`
        if severity in ("HIGH", "CRITICAL"):
            risk_label = "HIGH"
        elif severity in ("MODERATE", "MEDIUM"):
            risk_label = "MODERATE"
        else:
            risk_label = "LOW"

        # Preserve original source clause number from the parsed AST
        raw_c_num = str(risk.get("source_clause_number") or risk.get("clause_number") or idx).strip()
        if not raw_c_num.startswith("§") and not raw_c_num.lower().startswith("clause"):
            clause_num_str = f"Clause {raw_c_num}"
        else:
            clause_num_str = raw_c_num

        ui_clauses.append({
            "clause_number": clause_num_str,
            "clause_title": risk.get("clause_title", f"Clause {idx}"),
            "risk_level": risk_label,
            "original_text": risk.get("flagged_text", ""),
            "statute_reference": risk.get("violated_law", "Commercial Standard / General Contract Principle"),
            "legal_explanation": risk.get("explanation", "Review required."),
            "proposed_safe_redline": risk.get("recommendation"),
            "is_compliant": is_comp,
            "statutory_status": risk.get("statutory_status", "COMMERCIAL_ISSUE" if not is_comp else "FULLY_COMPLIANT"),
            "is_statute_verified": risk.get("is_statute_verified", False),
            "commercial_risk": risk_label,
            "negotiation_guidance": risk.get("negotiation_guidance", "")
        })

    if not ui_clauses:
        ui_clauses.append({
            "clause_number": "Clause 1",
            "clause_title": "General Compliance",
            "risk_level": "LOW",
            "original_text": "Document scanned.",
            "statute_reference": "N/A",
            "legal_explanation": "No material risks detected.",
            "is_compliant": True,
            "statutory_status": "FULLY_COMPLIANT",
            "is_statute_verified": False,
            "commercial_risk": "LOW",
            "negotiation_guidance": ""
        })

    counts, risk_score, risk_enum, risk_grade_str = _calculate_metrics(ui_clauses, missing_provisions)

    contract = Contract(
        user_id=current_user.id,
        enterprise_id=current_user.enterprise_id if current_user.is_enterprise_user else None,
        title=title or file.filename,
        original_filename=file.filename,
        file_path=storage_path,
        file_size_mb=round(len(file_bytes) / (1024 * 1024), 2),
        file_mime_type=file.content_type,
        # ✅ Only auto-share to company vault for enterprise users
        is_workspace_shared=bool(
            current_user.is_enterprise_user and current_user.enterprise_id
        )
    )
    db.add(contract)
    await db.flush()

    memo = ContractMemo(
        contract_id=contract.id,
        overall_risk=risk_enum,
        risk_score=risk_score,
        executive_summary=memo_summary,
        clauses_analysis=ui_clauses,
        high_risk_count=counts["high_risk"],
        moderate_risk_count=counts["review_needed"],
        compliant_count=counts["compliant"],
        hitl_status=HITLStatus.NOT_REQUESTED
    )
    db.add(memo)

    # ✅ METRICS: Track Audit Quality (Risk Score & Counts)
    await track_event(
        db, "audit_quality",
        user_id=current_user.id,
        event_data={
            "risk_score": risk_score,
            "high_risk_count": counts["high_risk"],
            "overall_risk": risk_enum.value
        },
        commit=False
    )

    await db.commit()
    await db.refresh(contract)
    await db.refresh(memo)

    return ContractMemoResponse(
        memo_id=memo.id,
        contract_id=contract.id,
        title=contract.title,
        overall_risk=memo.overall_risk,
        risk_grade=risk_grade_str,
        risk_score=memo.risk_score,
        executive_summary=memo.executive_summary,
        counts=counts,
        clauses=[ClauseAnalysisItem(**c) for c in memo.clauses_analysis],
        missing_provisions=missing_provisions,
        audited_for=audited_for or "CLIENT",
        stamp_duty_advisory=stamp_duty_advisory,
        hitl_status=memo.hitl_status
    )


@router.post("/{contract_id}/request-verification", response_model=RequestVerificationResponse)
async def request_lawyer_verification(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    memo = await db.scalar(
        select(ContractMemo)
        .join(Contract, ContractMemo.contract_id == Contract.id)
        .where(
            Contract.id == contract_id,
            Contract.user_id == current_user.id,
            Contract.deleted_at == None
        )
    )
    if not memo:
        raise HTTPException(404, "Contract analysis not found.")
    if memo.hitl_status != HITLStatus.NOT_REQUESTED:
        raise HTTPException(400, f"Verification already requested. Status: {memo.hitl_status.value}")

    split = get_lawyer_verification_split()
    cost_credits = int(split["credits_to_deduct"])

    await check_and_deduct_credits(
        user=current_user,
        db=db,
        cost=cost_credits,
        tx_type=CreditTxType.LAWYER_REVIEW_FEE,
        description=f"Lawyer Verification for Contract #{contract_id}",
        reference_id=str(memo.id),
        commit=False
    )

    now = datetime.now(timezone.utc)
    memo.hitl_status = HITLStatus.QUEUED_FOR_LAWYER
    memo.hitl_requested_at = now
    memo.hitl_global_deadline = now + timedelta(hours=72)
    memo.hitl_credits_paid = cost_credits
    memo.fee_bdt = split["internal_bdt_value"]
    memo.lawyer_payout_bdt = split["lawyer_payout_bdt"]
    memo.platform_fee_bdt = split["platform_fee_bdt"]

    await db.flush()
    success = await generate_initial_offer(db, memo)

    if not success:
        # ✅ Refund to the SAME pool that was charged (enterprise pool or personal wallet)
        refund_enterprise_id = (
            current_user.enterprise_id if current_user.is_enterprise_user else None
        )
        await refund_credits(
            db=db,
            user=current_user,
            amount=cost_credits,
            reason="No eligible lawyers available at this time.",
            reference_id=str(memo.id),
            enterprise_id=refund_enterprise_id,
        )

        memo.hitl_status = HITLStatus.REFUNDED
        memo.refunded_at = now
        memo.refund_reason = "No eligible lawyers available at this time."
        await db.commit()
        raise HTTPException(503, "No lawyers are currently available. Your credits have been refunded.")

    # ✅ METRICS: Track HITL Request
    await track_event(
        db, "hitl_requested",
        user_id=current_user.id,
        entity_id=memo.id,
        event_data={"credits_paid": cost_credits},
        commit=False
    )

    await db.commit()

    return RequestVerificationResponse(
        status="success",
        memo_id=memo.id,
        message="Verification requested. A lawyer has been notified.",
        credits_deducted=cost_credits
    )


@router.get("/history", response_model=List[dict])
async def get_my_contracts(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Contract, ContractMemo).join(ContractMemo).where(
        Contract.user_id == current_user.id,
        Contract.deleted_at == None
    )

    if current_user.is_enterprise_user and current_user.enterprise_id:
        stmt = select(Contract, ContractMemo).join(ContractMemo).where(
            or_(
                Contract.user_id == current_user.id,
                Contract.enterprise_id == current_user.enterprise_id
            ),
            Contract.deleted_at == None
        )

    stmt = stmt.order_by(desc(Contract.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()

    return [
        {
            "contract_id": c.id,
            "memo_id": m.id,
            "title": c.title,
            "overall_risk": m.overall_risk.value,
            "risk_score": m.risk_score,
            "hitl_status": m.hitl_status.value,
            "high_risk_count": m.high_risk_count,
            "moderate_risk_count": m.moderate_risk_count,
            "medium_risk_count": m.moderate_risk_count,
            "compliant_count": m.compliant_count,
            "executive_summary": m.executive_summary,
            "summary": m.executive_summary,
            "created_at": c.created_at,
            "is_workspace_shared": c.is_workspace_shared  # ✅ Returns True only for enterprise audits
        }
        for c, m in rows
    ]


@router.delete("/{contract_id}")
async def delete_contract(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Soft deletes a contract so it disappears from the user's archive.
    """
    contract = await db.scalar(
        select(Contract).where(
            Contract.id == contract_id,
            Contract.user_id == current_user.id,
            Contract.deleted_at == None
        )
    )
    if not contract:
        raise HTTPException(404, "Contract not found or already deleted.")

    contract.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "success", "message": "Contract deleted successfully."}


@router.get("/{contract_id}/download")
async def get_download_url(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(Contract, ContractMemo)
        .outerjoin(ContractMemo, Contract.id == ContractMemo.contract_id)
        .where(
            Contract.id == contract_id,
            Contract.user_id == current_user.id,
            Contract.deleted_at == None
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Contract not found.")

    contract, memo = row

    file_to_download = contract.file_path
    if memo and memo.signed_contract_path:
        file_to_download = memo.signed_contract_path

    supabase = get_supabase()
    try:
        res = supabase.storage.from_(BUCKET_NAME).create_signed_url(file_to_download, 3600)
        return {
            "download_url": res.get("signedURL") or res.get("signedUrl"),
            "is_lawyer_verified": bool(memo and memo.signed_contract_path)
        }
    except Exception as e:
        logger.error(f"[Supabase] Signed URL failed: {e}")
        raise HTTPException(500, "Could not generate download link.")


# ──────────────────────────────────────────────────────────
# NEW: GET single contract + memo (archive / detail view)
# ⚠️  MUST be registered AFTER `/history` — otherwise Starlette
#     would try to parse "history" as `contract_id: int`.
# ──────────────────────────────────────────────────────────

@router.get("/{contract_id}", response_model=ContractMemoResponse)
async def get_contract_detail(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch a single contract + memo for the archive / audit detail view.
    Accessible to the owner OR any user in the same enterprise.
    """
    result = await db.execute(
        select(Contract, ContractMemo)
        .join(ContractMemo, Contract.id == ContractMemo.contract_id)
        .where(
            Contract.id == contract_id,
            Contract.deleted_at == None,
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Contract not found.")

    contract, memo = row

    # ── Access check: owner OR same-enterprise user ──
    is_owner = contract.user_id == current_user.id
    is_same_enterprise = (
        current_user.enterprise_id is not None
        and contract.enterprise_id == current_user.enterprise_id
        and current_user.role in (UserRole.ENTERPRISE_ADMIN, UserRole.ENTERPRISE_MEMBER)
    )
    if not (is_owner or is_same_enterprise):
        raise HTTPException(403, "Access denied.")

    # ── Lawyer name (if HITL was assigned/completed) ──
    lawyer_name: Optional[str] = None
    if memo.lawyer_id:
        lawyer_profile = await db.scalar(
            select(LawyerProfile).where(LawyerProfile.id == memo.lawyer_id)
        )
        if lawyer_profile:
            lawyer_user = await db.scalar(
                select(User).where(User.id == lawyer_profile.user_id)
            )
            if lawyer_user:
                lawyer_name = lawyer_user.full_name

    return ContractMemoResponse(
        memo_id=memo.id,
        contract_id=contract.id,
        title=contract.title,
        overall_risk=memo.overall_risk,
        risk_grade=memo.overall_risk.value,
        risk_score=memo.risk_score,
        executive_summary=memo.executive_summary or "",
        counts={
            "high_risk": memo.high_risk_count,
            "review_needed": memo.moderate_risk_count,
            "compliant": memo.compliant_count,
        },
        clauses=[ClauseAnalysisItem(**c) for c in (memo.clauses_analysis or [])],
        hitl_status=memo.hitl_status,
        lawyer_name=lawyer_name,
        lawyer_notes=memo.lawyer_notes,
        lawyer_signature=memo.lawyer_signature,
        completed_at=memo.completed_at,
    )