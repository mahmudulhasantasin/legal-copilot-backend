from typing import TypedDict, Annotated, List, Optional, Dict, Any
from langgraph.graph.message import add_messages

class QAState(TypedDict):
    messages: Annotated[list, add_messages]
    user_id: int
    user_role: str
    query: str
    retrieved_docs: List[Dict[str, Any]]
    final_answer: Optional[str]
    citation_verified: bool

class ClauseRisk(TypedDict):
    clause_title: str
    original_text: str
    law_mapping: str
    risk_level: str  # HIGH, MEDIUM, LOW
    risk_reason: str
    suggested_redline: str

class ContractState(TypedDict):
    user_id: int
    user_role: str
    filename: str
    raw_chunks: List[str]
    extracted_clauses: List[Dict[str, str]]
    risk_assessments: List[ClauseRisk]
    playbook_rules: Optional[List[Dict[str, Any]]]
    memo_summary: Optional[str]
    overall_risk_score: str