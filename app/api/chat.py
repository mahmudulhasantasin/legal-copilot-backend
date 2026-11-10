import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import HumanMessage, AIMessage

from app.core.database import get_db
from app.core.security import get_current_user, check_and_deduct_credits, detect_language
from app.core.tier_limits import (
    get_tier_key, 
    get_session_limit, 
    get_max_active_sessions,
    get_monthly_chat_cap,
    should_generate_summary
)
from app.models.user import User, UserRole, CreditTxType
from app.models.chat import ChatSession, ChatMessage, ChatMode
from app.models.lawyer import LawyerProfile
from app.models.memo import ContractMemo, Contract, HITLStatus
from app.agents.qa_graph import qa_graph
from app.services.llm_provider import get_llm
from app.services.verifier import verifier 
from app.services.metrics import track_event, timed_event # ✅ নতুন ইমপোর্ট

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Legal Chat"])

# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class CitedSectionDetail(BaseModel):
    act: str
    section_number: str
    title: Optional[str] = "Statutory Provision"
    provision_summary: Optional[str] = ""
    relevance: Optional[str] = ""

class CitedCaseDetail(BaseModel):
    case_name: str
    ruling: Optional[str] = ""
    relevance: Optional[str] = ""

class StructuredLegalResponse(BaseModel):
    detailed_answer: str
    cited_sections: List[CitedSectionDetail] = Field(default_factory=list)
    cited_cases: List[CitedCaseDetail] = Field(default_factory=list) 
    suggested_next_steps: List[str] = Field(default_factory=list)
    suggested_follow_ups: List[str] = Field(default_factory=list)
    needs_clarification: bool = False 

class CreateSessionRequest(BaseModel):
    mode: str = Field(default="GENERAL", description="GENERAL, LAWYER_RESEARCH, or USER_CONTRACT")
    title: Optional[str] = None
    contract_id: Optional[int] = None

class QueryRequest(BaseModel):
    session_id: int
    query: str = Field(..., min_length=2, max_length=2000)

class ChatResponse(BaseModel):
    session_id: int
    user_query: str
    response: StructuredLegalResponse
    credits_remaining: int
    credits_source: str
    language_detected: str

# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

# ✅ FIX: Made async to support the new MCP-based verifier
async def _format_agent_output(structured_response: dict, raw_answer: str, retrieved_docs: list) -> StructuredLegalResponse:
    """
    Takes the LLM's JSON output, strictly verifies citations against retrieved docs,
    and returns a safe StructuredLegalResponse.
    """
    if not structured_response:
        return StructuredLegalResponse(
            detailed_answer=raw_answer,
            cited_sections=[],
            cited_cases=[],
            suggested_next_steps=[],
            suggested_follow_ups=[],
            needs_clarification=False
        )

    answer_text = structured_response.get("detailed_answer") or raw_answer
    needs_clarification = structured_response.get("needs_clarification", False)
    next_steps = structured_response.get("suggested_next_steps", [])
    follow_ups = structured_response.get("suggested_follow_ups", [])
    raw_sections = structured_response.get("cited_sections", [])
    raw_cases = structured_response.get("cited_cases", [])

    # 1. STRICT VERIFICATION: Cross-check with retrieved context
    # ✅ FIX: Added await
    verification = await verifier.verify_structured_response(structured_response, retrieved_docs)
    
    # 2. Filter Sections
    final_sections = []
    verified_sec_map = { f"{v.get('act','')}_{v.get('section','')}".lower(): v.get('valid') for v in verification.get("verified_sections", []) }
    
    for s in raw_sections:
        key = f"{s.get('act','')}_{s.get('section','')}".lower()
        if verified_sec_map.get(key, False):
            final_sections.append(CitedSectionDetail(
                act=s.get("act", ""),
                section_number=s.get("section", ""),
                title=s.get("title", "Statutory Provision"),
                provision_summary=s.get("provision_summary", ""),
                relevance=s.get("relevance", "Primary statutory grounding.")
            ))
    
    # 3. Filter Cases (Bypass strict verifier for Live Web Results/URLs)
    final_cases = []
    verified_case_map = { c.get('case_name','').lower(): c.get('valid') for c in verification.get("verified_cases", []) }
    
    for c in raw_cases:
        case_name = c.get("case_name", "")
        ruling = c.get("ruling", "")
        
        # ✅ FIX: If it's a live web result (contains http), we bypass strict verifier
        has_url = "http" in ruling.lower() or "http" in case_name.lower()
        is_verified = verified_case_map.get(case_name.lower(), False)
        
        if is_verified or has_url:
            final_cases.append(CitedCaseDetail(
                case_name=case_name,
                ruling=ruling,
                relevance=c.get("relevance", "Relevant judicial precedent.")
            ))

    return StructuredLegalResponse(
        detailed_answer=answer_text,
        cited_sections=final_sections,
        cited_cases=final_cases,
        suggested_next_steps=next_steps,
        suggested_follow_ups=follow_ups,
        needs_clarification=needs_clarification
    )


async def _generate_rolling_summary(db: AsyncSession, session: ChatSession):
    """Generates a summary of the conversation so far if it exceeds 10 messages."""
    if session.message_count < 10 or session.rolling_summary:
        return

    stmt = select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc())
    messages = (await db.execute(stmt)).scalars().all()
    
    history_text = "\n".join([f"{m.role.capitalize()}: {m.content}" for m in messages])
    
    prompt = f"""Summarize the following legal consultation in 3-4 concise sentences. 
Focus on the core legal issue, the statutes discussed, and the final advice given.
DO NOT include greetings or filler.

Transcript:
{history_text}
"""
    try:
        llm = get_llm()
        response = await llm.ainvoke(prompt)
        session.rolling_summary = response.content.strip()
        session.last_summarized_at = datetime.now(timezone.utc)
        session.summarized_up_to_message_id = messages[-1].id
        await db.commit()
        logger.info(f"[Chat] Generated rolling summary for session {session.id}")
    except Exception as e:
        logger.error(f"[Chat] Failed to generate summary: {e}")


# ──────────────────────────────────────────────────────────
# ENDPOINTS: SESSIONS
# ──────────────────────────────────────────────────────────

@router.post("/sessions", response_model=dict)
async def create_session(
    payload: CreateSessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    valid_modes = [ChatMode.GENERAL.value, ChatMode.LAWYER_RESEARCH.value, ChatMode.USER_CONTRACT.value]
    if payload.mode not in valid_modes:
        raise HTTPException(400, f"Invalid mode. Use {', '.join(valid_modes)}.")
    
    if payload.mode == ChatMode.LAWYER_RESEARCH.value and current_user.role != UserRole.LAWYER:
        raise HTTPException(403, "Only lawyers can use LAWYER_RESEARCH mode.")

    if payload.mode == ChatMode.USER_CONTRACT.value and not payload.contract_id:
        raise HTTPException(400, "contract_id is required for USER_CONTRACT mode.")

    max_sessions = get_max_active_sessions(current_user)
    if max_sessions != -1:
        now = datetime.now(timezone.utc)
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        active_count = (await db.execute(
            select(func.count(ChatSession.id)).where(
                ChatSession.user_id == current_user.id,
                ChatSession.created_at >= first_of_month,
                ChatSession.session_mode != ChatMode.CONTRACT_REVIEW.value
            )
        )).scalar_one()
        
        if active_count >= max_sessions:
            raise HTTPException(
                status_code=403,
                detail=f"You have reached your monthly limit of {max_sessions} sessions. Closing or deleting chats does not restore this quota. Quota resets on the 1st of next month."
            )

    scoped_contract_id = None
    if payload.mode == ChatMode.USER_CONTRACT.value:
        contract = await db.scalar(select(Contract).where(Contract.id == payload.contract_id, Contract.user_id == current_user.id))
        if not contract:
            raise HTTPException(404, "Contract not found or you don't have access.")
        scoped_contract_id = contract.id

    session = ChatSession(
        user_id=current_user.id,
        title=payload.title or "Contract Chat",
        session_mode=payload.mode,
        scoped_contract_id=scoped_contract_id,
        tier_at_creation=get_tier_key(current_user),
        session_limit=get_session_limit(current_user),
        auto_close_at=datetime.now(timezone.utc) + timedelta(days=7)
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return {
        "session_id": session.id,
        "mode": session.session_mode,
        "message_limit": session.session_limit,
        "title": session.title
    }


@router.get("/sessions", response_model=List[dict])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ChatSession)
        .where(
            ChatSession.user_id == current_user.id,
            ChatSession.is_closed == False,
            ChatSession.session_mode != ChatMode.CONTRACT_REVIEW.value
        )
        .order_by(desc(ChatSession.updated_at))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "session_id": s.id,
            "title": s.title,
            "mode": s.session_mode,
            "message_count": s.message_count,
            "message_limit": s.session_limit,
            "updated_at": s.updated_at,
        }
        for s in rows
    ]


@router.get("/sessions/{session_id}", response_model=dict)
async def get_session_history(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id
        )
    )
    if not session:
        raise HTTPException(404, "Session not found.")

    msg_stmt = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc())
    messages = (await db.execute(msg_stmt)).scalars().all()

    return {
        "session_id": session.id,
        "mode": session.session_mode,
        "message_count": session.message_count,
        "message_limit": session.session_limit,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "cited_cases": m.cited_cases,                   
                "suggested_next_steps": m.suggested_next_steps, 
                "suggested_follow_ups": getattr(m, "suggested_follow_ups", None) or [],
                "needs_clarification": m.needs_clarification,   
                "created_at": m.created_at
            }
            for m in messages
        ]
    }


@router.post("/sessions/{session_id}/close")
async def close_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id
        )
    )
    if not session:
        raise HTTPException(404, "Not found.")
    
    session.is_closed = True
    await db.commit()
    return {"status": "success"}


# ──────────────────────────────────────────────────────────
# ENDPOINT: QUERY (Main Chat)
# ──────────────────────────────────────────────────────────

@router.post("/query", response_model=ChatResponse)
async def process_chat_query(
    payload: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Validate Session
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == payload.session_id,
            ChatSession.user_id == current_user.id
        )
    )
    if not session or session.is_closed:
        raise HTTPException(404, "Session not found or closed.")

    if session.is_full:
        raise HTTPException(
            status_code=403,
            detail=f"Session limit reached ({session.session_limit} messages). Please start a new session."
        )

    # 1.1 Check Monthly Chat Cap
    monthly_cap = get_monthly_chat_cap(current_user)
    if monthly_cap is not None:
        now = datetime.now(timezone.utc)
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_queries = (await db.execute(
            select(func.count(ChatMessage.id))
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(
                ChatSession.user_id == current_user.id,
                ChatMessage.role == "user",
                ChatMessage.created_at >= first_of_month,
            )
        )).scalar_one()

        if monthly_queries >= monthly_cap:
            raise HTTPException(
                status_code=403,
                detail=f"Monthly chat limit reached ({monthly_cap} queries). Your quota will reset on the 1st of next month."
            )

    # 2. Context-specific validation
    contract_context = ""
    if session.session_mode == ChatMode.CONTRACT_REVIEW.value:
        memo = await db.scalar(
            select(ContractMemo).where(
                ContractMemo.contract_id == session.scoped_contract_id,
                ContractMemo.lawyer_id == current_user.lawyer_profile.id,
                ContractMemo.hitl_status.in_([HITLStatus.ASSIGNED, HITLStatus.IN_REVIEW])
            )
        )
        if not memo:
            raise HTTPException(403, "You are not assigned to review this contract.")
    
    elif session.session_mode == ChatMode.USER_CONTRACT.value:
        memo = await db.scalar(select(ContractMemo).where(ContractMemo.contract_id == session.scoped_contract_id))
        if memo:
            contract_context = f"""
CONTRACT SUMMARY: {memo.executive_summary}
OVERALL RISK: {memo.overall_risk.value} (Score: {memo.risk_score}/100)
RISK COUNTS: {memo.high_risk_count} High, {memo.moderate_risk_count} Moderate, {memo.compliant_count} Compliant.
DETAILED CLAUSES ANALYSIS:
{json.dumps(memo.clauses_analysis, ensure_ascii=False, indent=2)}
"""

    # 3. Language Detection
    lang = detect_language(payload.query)

    # 4. Credit Deduction
    deduction = await check_and_deduct_credits(
        user=current_user,
        db=db,
        cost=1,
        tx_type=CreditTxType.CHAT_QUERY,
        description=f"Chat query: {payload.query[:30]}...",
        chat_mode=session.session_mode,
        commit=False
    )

    # 5. Fetch History
    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(6)
    )
    raw_history = (await db.execute(history_stmt)).scalars().all()
    raw_history.reverse()

    formatted_chat_history = []
    is_follow_up_to_clarification = False

    for msg in raw_history:
        if msg.role == "user":
            formatted_chat_history.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            formatted_chat_history.append(AIMessage(content=msg.content))
            # ✅ FIX: যদি শেষ মেসেজটি ক্লারিফিকেশন চাওয়া হয়, তবে ফ্ল্যাগ True করবো
            is_follow_up_to_clarification = getattr(msg, "needs_clarification", False)

    # 6. Invoke LangGraph
    initial_state = {
        "messages": formatted_chat_history,
        "user_id": current_user.id,
        "user_role": current_user.role.value,
        "query": payload.query,
        "retrieved_docs": [],
        "final_answer": None,
        "citation_verified": False,
        "contract_id": session.scoped_contract_id,
        "contract_context": contract_context,
        "language": lang,
        "query_type": None,
        "needs_case_law": False,
        "structured_response": None,
        # ✅ FIX: নতুন ফ্ল্যাগ পাস করা হলো
        "force_answerable": is_follow_up_to_clarification 
    }

    try:
        # ✅ METRICS: Track Latency of the entire LangGraph execution
        async with timed_event(
            db, "chat_query",
            user_id=current_user.id,
            entity_id=session.id,
            event_data={"mode": session.session_mode, "lang": lang},
            commit=False,  # Will be committed with messages below
        ):
            graph_output = await qa_graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception(f"Error processing query: {exc}")
        raise HTTPException(500, "An error occurred while generating the response.")

    # 7. Format Output (Strict Verification happens here)
    raw_answer = graph_output.get("final_answer") or "No legal answer generated."
    retrieved_docs = graph_output.get("retrieved_docs") or []
    structured_response_json = graph_output.get("structured_response")
    
    # ✅ FIX: Added await
    structured_res = await _format_agent_output(
        structured_response_json, 
        raw_answer, 
        retrieved_docs
    )

    # ✅ METRICS: Track Citation Verification
    if structured_response_json:
        for sec in (structured_response_json.get("cited_sections") or []):
            # A citation is "valid" if it survived _format_agent_output's filter
            was_verified = any(
                c.act == sec.get("act") and c.section_number == sec.get("section")
                for c in structured_res.cited_sections
            )
            await track_event(
                db, "citation_checked",
                user_id=current_user.id,
                entity_id=session.id,
                success=was_verified,
                commit=False,
            )

    # 8. Save Messages & Update Session
    user_msg = ChatMessage(
        session_id=session.id, role="user", content=payload.query,
        credit_deducted=deduction["deducted"], language_detected=lang
    )
    
    assistant_msg = ChatMessage(
        session_id=session.id, 
        role="assistant", 
        content=structured_res.detailed_answer,
        citations=[c.model_dump() for c in structured_res.cited_sections],
        cited_cases=[c.model_dump() for c in structured_res.cited_cases],             
        suggested_next_steps=structured_res.suggested_next_steps,                   
        suggested_follow_ups=structured_res.suggested_follow_ups,
        needs_clarification=structured_res.needs_clarification,                     
        language_detected=lang
    )
    
    db.add(user_msg)
    db.add(assistant_msg)
    
    session.message_count += 1
    session.last_message_at = datetime.now(timezone.utc)
    session.auto_close_at = datetime.now(timezone.utc) + timedelta(days=7)
    
    await db.commit()

    # 9. Generate Rolling Summary
    if should_generate_summary(current_user) and session.message_count >= 10:
        await _generate_rolling_summary(db, session)

    return ChatResponse(
        session_id=session.id,
        user_query=payload.query,
        response=structured_res,
        credits_remaining=deduction.get("remaining", 0),
        credits_source=deduction.get("source", "wallet"),
        language_detected=lang
    )