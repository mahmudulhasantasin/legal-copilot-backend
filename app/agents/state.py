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
    
    # ✅ নতুন ফিল্ডসমূহ (Smart Router এবং MCP Case Law-এর জন্য)
    query_type: Optional[str]            # "ANSWERABLE", "NEEDS_CLARIFICATION", "OUT_OF_SCOPE"
    needs_case_law: Optional[bool]       # মামলার রায় খোঁজার প্রয়োজন আছে কি না
    cited_cases: Optional[List[Dict[str, Any]]] # LLM যে মামলার রায় উল্লেখ করেছে
    structured_response: Optional[Dict[str, Any]] # LLM-এর JSON আউটপুট (Parse করার পর)


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