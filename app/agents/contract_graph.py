from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
from app.agents.nodes import retrieve_statutes_node, analyze_risk_node

class ContractState(TypedDict):
    contract_text: str
    retrieved_statutes: List[Dict[str, Any]]
    analysis: Dict[str, Any]

# স্টেট মেশিন তৈরি
workflow = StateGraph(ContractState)

# নোডগুলো যুক্ত করা
workflow.add_node("retrieve_statutes", retrieve_statutes_node)
workflow.add_node("analyze_risks", analyze_risk_node)

# ফ্লো নির্ধারণ: Entry -> Retrieve -> Analyze -> End
workflow.set_entry_point("retrieve_statutes")
workflow.add_edge("retrieve_statutes", "analyze_risks")
workflow.add_edge("analyze_risks", END)

contract_graph = workflow.compile()

def analyze_contract_text(contract_text: str) -> dict:
    """API-এর জন্য হেল্পার ফাংশন যা গ্রাফ রান করে।"""
    initial_state = {"contract_text": contract_text}
    result = contract_graph.invoke(initial_state)
    return result.get("analysis", {})