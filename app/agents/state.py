from typing import TypedDict, Annotated, List, Optional, Dict, Any
from langgraph.graph.message import add_messages


class QAState(TypedDict):
    """
    State for the General Legal QA Graph.
    Handles standard statutory queries and contract-scoped user queries.
    """
    messages: Annotated[list, add_messages]
    user_id: int
    user_role: str
    query: str
    retrieved_docs: List[Dict[str, Any]]
    final_answer: Optional[str]
    citation_verified: bool
    contract_id: Optional[int]
    contract_context: Optional[str]
    language: Optional[str]
    
    # Query classification and live case law tracking
    query_type: Optional[str]                     # "ANSWERABLE", "NEEDS_CLARIFICATION", "OUT_OF_SCOPE"
    needs_case_law: Optional[bool]                # Flag indicating whether precedent search is required
    cited_cases: Optional[List[Dict[str, Any]]]   # Case law precedents cited by the model
    structured_response: Optional[Dict[str, Any]] # Parsed JSON response payload from the LLM


class ClauseRisk(TypedDict):
    """
    Structure for individual risk assessments inside a contract.
    """
    clause_title: str
    original_text: str
    law_mapping: str
    risk_level: str  # HIGH, MEDIUM, LOW
    risk_reason: str
    suggested_redline: str


class ContractState(TypedDict):
    """
    State for the Contract Audit Graph.
    Handles statutory retrieval and risk analysis against company playbooks.
    """
    user_id: int
    user_role: str
    filename: str
    raw_chunks: List[str]
    extracted_clauses: List[Dict[str, str]]
    risk_assessments: List[ClauseRisk]
    playbook_rules: Optional[List[Dict[str, Any]]]
    memo_summary: Optional[str]
    overall_risk_score: str