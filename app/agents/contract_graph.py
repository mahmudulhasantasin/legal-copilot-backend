import logging
from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END
from app.agents.nodes import retrieve_statutes_node, analyze_risk_node

logger = logging.getLogger(__name__)


class ContractState(TypedDict):
    contract_text: str
    playbook_rules: List[Dict[str, Any]] # <-- নতুন যুক্ত হলো
    retrieved_statutes: List[Dict[str, Any]]
    analysis: Dict[str, Any]


# 1. Workflow Build
workflow = StateGraph(ContractState)

workflow.add_node("retrieve_statutes", retrieve_statutes_node)
workflow.add_node("analyze_risks", analyze_risk_node)

workflow.set_entry_point("retrieve_statutes")
workflow.add_edge("retrieve_statutes", "analyze_risks")
workflow.add_edge("analyze_risks", END)

# 2. Compile Graph
contract_graph = workflow.compile()


async def analyze_contract_risk_async(contract_text: str, playbook_rules: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Non-blocking async runner for FastAPI endpoints.
    Executes: PDF text -> Qdrant Statutory RAG -> LLM Legal Risk Analysis -> Structured Output.
    """
    initial_state = {
        "contract_text": contract_text,
        "playbook_rules": playbook_rules or [],  # <-- পাস করা হলো
        "retrieved_statutes": [],
        "analysis": {}
    }
    try:
        result = await contract_graph.ainvoke(initial_state)
        return result.get("analysis", {})
    except Exception as exc:
        logger.error(f"[contract_graph] Async execution error: {exc}")
        return {
            "overall_risk_score": "MODERATE",
            "memo_summary": f"Automated statutory audit encountered an error: {str(exc)}",
            "detected_risks": []
        }


def analyze_contract_risk(contract_text: str, playbook_rules: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Synchronous fallback runner."""
    initial_state = {
        "contract_text": contract_text,
        "playbook_rules": playbook_rules or [],
        "retrieved_statutes": [],
        "analysis": {}
    }
    try:
        result = contract_graph.invoke(initial_state)
        return result.get("analysis", {})
    except Exception as exc:
        logger.error(f"[contract_graph] Sync execution error: {exc}")
        return {
            "overall_risk_score": "MODERATE",
            "memo_summary": f"Execution error: {str(exc)}",
            "detected_risks": []
        }