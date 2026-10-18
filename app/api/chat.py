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
    should_generate_summary
)
from app.models.user import User, UserRole, CreditTxType
from app.models.chat import ChatSession, ChatMessage, ChatMode
from app.models.lawyer import LawyerProfile
from app.models.memo import ContractMemo, Contract, HITLStatus
from app.agents.qa_graph import qa_graph
from app.services.llm_provider import get_llm
from app.services.legal_normalizer import normalize_act, normalize_section, resolve_act

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Legal Chat"])

# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class CitedSectionDetail(BaseModel):
    act: str
    section_number: str
    title: str
    provision_summary: str
    relevance: str

class StructuredLegalResponse(BaseModel):
    detailed_answer: str
    cited_sections: List[CitedSectionDetail] = []
    suggested_next_steps: List[str] = []

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
    credits_source: str  # e.g., "wallet", "monthly_pool", "free"
    language_detected: str

DEFAULT_NEXT_STEPS = [
    "Review the referenced statutory provisions with an advocate.",
    "Gather any formal contracts, notices, or evidence related to this issue.",
]


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def _format_agent_output(raw_text: str, retrieved_docs: list) -> StructuredLegalResponse:
    """Formats LLM output and validates citations against retrieved docs."""
    valid_docs_map = {}
    by_section = {}
    for doc in retrieved_docs:
        if doc.get("source_type") == "precedent":
            continue
        d_act = doc.get("act", "")
        s_n = normalize_section(str(doc.get("section", "")))
        a_n = resolve_act(d_act) or normalize_act(d_act)
        if not s_n:
            continue
        valid_docs_map[f"{a_n}_{s_n}"] = doc
        by_section.setdefault(s_n, []).append(doc)

    cited_items = []
    seen = set()

    def _doc_citation(doc, relevance):
        content = doc.get("content") or doc.get("text") or ""
        return CitedSectionDetail(
            act=doc.get("act", ""),
            section_number=doc.get("section", ""),
            title=doc.get("title") or "Statutory Provision",
            provision_summary=(content[:200] + "...") if len(content) > 200 else content,
            relevance=relevance or "Primary statutory grounding.",
        )

    def add_citation(act, sec, relevance=""):
        s_n = normalize_section(sec)
        if not s_n: return
        doc = None
        if act:
            a_n = resolve_act(act) or normalize_act(act)
            doc = valid_docs_map.get(f"{a_n}_{s_n}")
        if doc is None:
            candidates = by_section.get(s_n, [])
            if len(candidates) == 1:
                doc = candidates[0]
        if doc is None: return
        
        key = f"{resolve_act(doc.get('act','')) or normalize_act(doc.get('act',''))}_{normalize_section(doc.get('section',''))}"
        if key in seen: return
        seen.add(key)
        cited_items.append(_doc_citation(doc, relevance))

    answer_text = raw_text
    next_steps = None

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "detailed_answer" in parsed:
            answer_text = parsed.get("detailed_answer") or raw_text
            next_steps = parsed.get("suggested_next_steps") or None
            for c in parsed.get("cited_sections", []) or []:
                if isinstance(c, dict):
                    add_citation(c.get("act"), c.get("section_number"), c.get("relevance"))
    except Exception:
        pass

    return StructuredLegalResponse(
        detailed_answer=answer_text,
        cited_sections=cited_items,
        suggested_next_steps=next_steps or DEFAULT_NEXT_STEPS,
    )


async def _generate_rolling_summary(db: AsyncSession, session: ChatSession):
    """Generates a summary of the conversation so far if it exceeds 10 messages."""
    if session.message_count < 10 or session.rolling_summary:
        return

    stmt = select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc())
    messages = (await db.execute(stmt)).scalars().all()
    
    # Format history for LLM
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
    """Creates a new chat session. Snapshots tier limits at creation."""
    
    # 1. Validate mode
    valid_modes = [ChatMode.GENERAL.value, ChatMode.LAWYER_RESEARCH.value, ChatMode.USER_CONTRACT.value]
    if payload.mode not in valid_modes:
        raise HTTPException(400, f"Invalid mode. Use {', '.join(valid_modes)}.")
    
    if payload.mode == ChatMode.LAWYER_RESEARCH.value and current_user.role != UserRole.LAWYER:
        raise HTTPException(403, "Only lawyers can use LAWYER_RESEARCH mode.")

    if payload.mode == ChatMode.USER_CONTRACT.value and not payload.contract_id:
        raise HTTPException(400, "contract_id is required for USER_CONTRACT mode.")

    # 2. Check active session limits
    max_sessions = get_max_active_sessions(current_user)
    if max_sessions != -1:
        active_count = (await db.execute(
            select(func.count(ChatSession.id)).where(
                ChatSession.user_id == current_user.id,
                ChatSession.is_closed == False,
                ChatSession.session_mode != ChatMode.CONTRACT_REVIEW.value # Contract reviews have their own limits
            )
        )).scalar_one()
        
        if active_count >= max_sessions:
            raise HTTPException(403, f"You have reached the maximum of {max_sessions} active sessions.")

    # 3. Verify ownership if USER_CONTRACT
    scoped_contract_id = None
    if payload.mode == ChatMode.USER_CONTRACT.value:
        contract = await db.scalar(select(Contract).where(Contract.id == payload.contract_id, Contract.user_id == current_user.id))
        if not contract:
            raise HTTPException(404, "Contract not found or you don't have access.")
        scoped_contract_id = contract.id

    # 4. Create session with snapshotted limits
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
    """Lists user's chat sessions (excluding contract reviews, which show in lawyer panel)."""
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

    # 2. Context-specific validation (Contract Review)
    contract_context = ""
    if session.session_mode == ChatMode.CONTRACT_REVIEW.value:
        # Verify lawyer is assigned to this contract
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
        # Fetch the AI Memo for this user's contract
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

    # 4. Credit Deduction (Security service handles free modes & monthly pools)
    deduction = await check_and_deduct_credits(
        user=current_user,
        db=db,
        cost=1, # Actual cost logic is handled inside based on role and chat_mode
        tx_type=CreditTxType.CHAT_QUERY,
        description=f"Chat query: {payload.query[:30]}...",
        chat_mode=session.session_mode,
        commit=False # We commit at the end with the messages
    )

    # 5. Fetch History (Limit to last 6 messages for context)
    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(6)
    )
    raw_history = (await db.execute(history_stmt)).scalars().all()
    raw_history.reverse() # Put back in chronological order

    formatted_chat_history = []
    for msg in raw_history:
        if msg.role == "user":
            formatted_chat_history.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            formatted_chat_history.append(AIMessage(content=msg.content))

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
        "language": lang
    }

    try:
        graph_output = await qa_graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception(f"Error processing query: {exc}")
        raise HTTPException(500, "An error occurred while generating the response.")

    # 7. Format Output
    raw_answer = graph_output.get("final_answer") or "No legal answer generated."
    retrieved_docs = graph_output.get("retrieved_docs") or []
    structured_res = _format_agent_output(raw_answer, retrieved_docs)

    # 8. Save Messages & Update Session
    user_msg = ChatMessage(
        session_id=session.id, role="user", content=payload.query,
        credit_deducted=deduction["deducted"], language_detected=lang
    )
    assistant_msg = ChatMessage(
        session_id=session.id, role="assistant", content=structured_res.detailed_answer,
        citations=[c.model_dump() for c in structured_res.cited_sections],
        language_detected=lang
    )
    
    db.add(user_msg)
    db.add(assistant_msg)
    
    session.message_count += 2
    session.last_message_at = datetime.now(timezone.utc)
    session.auto_close_at = datetime.now(timezone.utc) + timedelta(days=7)
    
    await db.commit()

    # 9. Generate Rolling Summary (if applicable)
    if should_generate_summary(current_user) and session.message_count >= 10:
        # We do this asynchronously or inline (inline for simplicity here)
        await _generate_rolling_summary(db, session)

    return ChatResponse(
        session_id=session.id,
        user_query=payload.query,
        response=structured_res,
        credits_remaining=deduction.get("remaining", 0),
        credits_source=deduction.get("source", "wallet"),
        language_detected=lang
    )