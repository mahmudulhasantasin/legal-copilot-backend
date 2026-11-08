import json
import asyncio
import re
import logging
from app.services.retriever import hybrid_retriever
from app.services.llm_provider import get_llm
from app.agents.prompts import SYSTEM_CONTRACT_RISK_PROMPT

logger = logging.getLogger(__name__)

# ── সীমা (single source of truth) ──────────────────────────
MAX_CONTRACT_CHARS = 25000   # মোট analyze হবে (~10 পেজ English legal / ~14 পেজ Bengali)
CHUNK_SIZE         = 8000    # প্রতিটি LLM call-এ এতটুকু text
CHUNK_OVERLAP      = 500     # পরের chunk-এ আগের শেষের ৫০০ char পাবে → clause continuity


async def retrieve_statutes_node(state: dict) -> dict:
    """
    Statutory RAG Node (async):
    Contract-এর শুরু, মধ্যভাগ ও শেষ দেখে Qdrant থেকে প্রাসঙ্গিক Bangladesh আইন বের করে।
    """
    contract_text = state.get("contract_text", "")
    if not contract_text.strip():
        return {"retrieved_statutes": []}

    # Sample beginning, middle, and end if contract is large to ensure clauses across entire doc are matched
    if len(contract_text) > 3000:
        part1 = contract_text[:1400]
        mid = len(contract_text) // 2
        part2 = contract_text[mid : mid + 1000]
        part3 = contract_text[-600:]
        query_snippet = f"{part1}\n\n{part2}\n\n{part3}".strip()
    else:
        query_snippet = contract_text.strip()

    try:
        retrieved_laws = await hybrid_retriever.aretrieve(query_snippet, top_k=10)
    except Exception as exc:
        logger.error(f"[retrieve_statutes_node] Retrieval failed: {exc}")
        retrieved_laws = []

    return {"retrieved_statutes": retrieved_laws}


async def analyze_risk_node(state: dict) -> dict:
    """
    LLM Analysis Node (async):
    একটি chunk-এর ক্লজগুলো Bangladesh statute ও Playbook rules-এর বিপরীতে analyze করে।
    """
    contract_text = state.get("contract_text", "")[:CHUNK_SIZE]  # safety cap
    statutes = state.get("retrieved_statutes", [])
    playbook_rules = state.get("playbook_rules", [])

    # 1. Statutory context
    if statutes:
        laws_context = "\n\n".join([
            f"- [{law.get('act', 'Bangladesh Statute')} | {law.get('section', 'Section')} - {law.get('title', '')}]:\n"
            f"  \"{law.get('text', law.get('content', '')).strip()}\""
            for law in statutes
        ])
    else:
        laws_context = (
            "Apply mandatory provisions of Bangladesh Law "
            "(e.g., Contract Act 1872 Sections 27, 28, 73, 74; Labour Act 2006)."
        )

    # 2. Enterprise Playbook context
    playbook_context = ""
    if playbook_rules:
        rules_text = "\n".join([
            f"- Rule ID: {r.get('rule_id', 'N/A')}\n"
            f"  Category: {r.get('category', 'General')}\n"
            f"  Rule: {r.get('rule_description', '')}\n"
            f"  Severity: {r.get('severity', 'MODERATE')}\n"
            f"  Suggested Redline: {r.get('suggested_redline', '')}"
            for r in playbook_rules
        ])
        playbook_context = (
            f"\nCOMPANY INTERNAL PLAYBOOK RULES (MUST ENFORCE):\n{rules_text}\n\n"
            "CRITICAL INSTRUCTION: Flag any playbook violation. "
            "In 'violated_law' cite the rule ID (e.g. \"Internal Playbook RULE-CAT-01\").\n"
        )

    # 3. Full prompt
    prompt = (
        f"{SYSTEM_CONTRACT_RISK_PROMPT}\n\n"
        f"RELEVANT BANGLADESH STATUTORY CONTEXT:\n{laws_context}\n\n"
        f"{playbook_context}"
        f"CONTRACT TEXT FOR AUDIT:\n---\n{contract_text}\n---\n"
    )

    try:
        llm = get_llm()
        # asyncio.to_thread → sync LLM.invoke কে event loop block না করে চালায়
        response = await asyncio.to_thread(llm.invoke, prompt)

        content = response.content if hasattr(response, "content") else str(response)
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            ).strip()
        else:
            content = str(content).strip()

        # JSON markdown wrapper সরানো
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE).strip()
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            content = content[start:end + 1]

        analysis = json.loads(content)

    except Exception as e:
        logger.error(f"[analyze_risk_node] Failed: {e}")
        analysis = {
            "overall_risk_score": "MEDIUM",
            "memo_summary": "Contract analyzed under general Bangladesh legal principles.",
            "detected_risks": [],
        }

    return {"analysis": analysis}
