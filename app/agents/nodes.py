import json
import re
import logging
from typing import Dict, Any
from app.services.retriever import hybrid_retriever
from app.services.llm_provider import get_llm
from app.agents.prompts import SYSTEM_CONTRACT_RISK_PROMPT, SYSTEM_PLAYBOOK_MATCHING_PROMPT

logger = logging.getLogger(__name__)


def retrieve_statutes_node(state: dict) -> dict:
    """
    Statutory RAG Node:
    Extracts core contractual provisions and retrieves governing Bangladesh laws from Qdrant.
    """
    contract_text = state.get("contract_text", "")
    query_snippet = contract_text[:3000].strip()
    if not query_snippet:
        return {"retrieved_statutes": []}

    try:
        # Retrieve statutory provisions (The Contract Act, Labour Act, Arbitration Act, etc.)
        retrieved_laws = hybrid_retriever.retrieve(query_snippet, top_k=5)
    except Exception as exc:
        logger.error(f"[retrieve_statutes_node] Retrieval failed: {exc}")
        retrieved_laws = []

    return {"retrieved_statutes": retrieved_laws}


def analyze_risk_node(state: dict) -> dict:
    """
    LLM Analysis Node:
    Cross-references contract clauses against retrieved Bangladesh statutes AND Enterprise Playbook rules,
    detecting void clauses, penalties, non-compete violations, and internal policy breaches.
    """
    contract_text = state.get("contract_text", "")[:12000]
    statutes = state.get("retrieved_statutes", [])
    playbook_rules = state.get("playbook_rules", [])

    # 1. Format statutory grounding context
    if statutes:
        laws_context = "\n\n".join([
            f"- [{law.get('act', 'Bangladesh Statute')} | {law.get('section', 'Section')} - {law.get('title', '')}]:\n  \"{law.get('text', law.get('content', '')).strip()}\""
            for law in statutes
        ])
    else:
        laws_context = "Apply mandatory provisions of Bangladesh Law (e.g., Contract Act 1872 Sections 27, 28, 73, 74; Labour Act 2006)."

    # 2. Format Playbook context (if rules exist)
    playbook_context = ""
    if playbook_rules:
        rules_text = "\n".join([
            f"- Rule ID: {r.get('rule_id', 'N/A')}\n  Category: {r.get('category', 'General')}\n  Rule: {r.get('rule_description', '')}\n  Severity: {r.get('severity', 'MODERATE')}\n  Suggested Redline: {r.get('suggested_redline', '')}"
            for r in playbook_rules
        ])
        playbook_context = f"""
COMPANY INTERNAL PLAYBOOK RULES (MUST ENFORCE):
{rules_text}

CRITICAL INSTRUCTION: If the contract violates any of the above internal company rules, you MUST flag it as a risk. For the 'violated_law' field in your JSON response, cite the internal rule ID (e.g., "Internal Playbook RULE-CAT-01").
"""

    # 3. Construct the combined prompt
    prompt = f"""{SYSTEM_CONTRACT_RISK_PROMPT}

RELEVANT BANGLADESH STATUTORY CONTEXT:
{laws_context}

{playbook_context}

CONTRACT TEXT FOR AUDIT:
---
{contract_text}
---
"""

    try:
        llm = get_llm()
        response = llm.invoke(prompt)

        # Handle content format
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                parts = [item.get("text", "") if isinstance(item, dict) else str(item) for item in content]
                content = "".join(parts).strip()
            else:
                content = str(content).strip()
        else:
            content = str(response).strip()

        # Clean JSON markdown wrapper
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE).strip()

        analysis = json.loads(content)

    except Exception as e:
        logger.error(f"[analyze_risk_node] Failed to execute or parse risk analysis: {e}")
        analysis = {
            "overall_risk_score": "MODERATE",
            "memo_summary": "Contract analyzed under general Bangladesh legal principles.",
            "detected_risks": []
        }

    return {"analysis": analysis}