from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.core.security import get_current_user
from app.models.user import User
from app.agents.qa_graph import qa_graph

router = APIRouter(prefix="/api/chat", tags=["Legal Q&A"])

class ChatRequest(BaseModel):
    query: str

class ChatResponse(BaseModel):
    query: str
    answer: str
    citation_verified: bool

@router.post(
    "/",
    response_model=ChatResponse,
    summary="Ask legal questions grounded in Bangladesh statutory laws"
)
def legal_qa_chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        initial_state = {
            "query": req.query,
            "retrieved_docs": [],
            "answer": "",
            "final_answer": "",
            "citation_verified": False
        }
        
        result = qa_graph.invoke(initial_state)

        # Fallback to ensure Pydantic never receives None
        answer_text = (
            result.get("final_answer")
            or result.get("answer")
            or "No statutory analysis could be generated."
        )

        citation_status = result.get("citation_verified", True)

        return ChatResponse(
            query=req.query,
            answer=answer_text,
            citation_verified=citation_status
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Legal QA Engine Error: {str(e)}"
        )