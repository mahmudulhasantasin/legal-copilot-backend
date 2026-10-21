import logging
import json
import re
from typing import Dict, Any
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from app.agents.prompts import SYSTEM_QA_PROMPT
from app.agents.state import QAState
from app.services.llm_provider import get_llm
from app.services.mcp_server import search_live_bd_judgements
from app.services.retriever import hybrid_retriever

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = (
    "দুঃখিত, আপনার প্রশ্নের সাথে সরাসরি সম্পর্কিত কোনো আইনের ধারা এই মুহূর্তে আমাদের ডাটাবেসে খুঁজে পাওয়া যায়নি। "
    "অনুগ্রহ করে আপনার পরিস্থিতি বা ঘটনাটি আরও সুনির্দিষ্টভাবে লিখুন।"
)

# ✅ আপডেটেড রাউটার প্রম্পট: সাধারণ আইনি প্রশ্নে আর ক্লারিফিকেশন চাইবে না
QUERY_ROUTER_PROMPT = """
You are the intelligent query router for Advotech Copilot, a Bangladesh-law assistant.
Classify the CURRENT request as exactly one of:
1. "OUT_OF_SCOPE": Clearly unrelated to law, coding, weather, recipes, etc.
2. "INSUFFICIENT_EVIDENCE": Explicitly asks for another country's jurisdiction rather than Bangladesh law.
3. "NEEDS_CLARIFICATION": ONLY use this when the user is describing a personal incident/event but missing crucial facts (e.g., "I got fired", "My landlord evicted me"). DO NOT use this if the user is asking for a general statutory lookup (e.g., "Which article has the right to equality?", "What is Section 138?").
4. "ANSWERABLE": Specific legal question, statutory lookup, or has enough context to provide a detailed answer.

ALSO, determine if this query requires searching for live case law/precedents (e.g., asks for recent verdicts, supreme court rulings, or is a complex real-world dispute).
Return ONLY JSON: {
  "status":"ANSWERABLE|NEEDS_CLARIFICATION|OUT_OF_SCOPE|INSUFFICIENT_EVIDENCE",
  "needs_case_law": true|false,
  "response":"short polite response if OUT_OF_SCOPE/INSUFFICIENT_EVIDENCE; OR 2-3 clarifying questions if NEEDS_CLARIFICATION; otherwise empty"
}
Match language: Bengali/Banglish -> formal Bengali script; English -> English.
"""


def _content_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in value).strip()
    return str(value or "").strip()


def _json_object(value):
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.I)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", value, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                pass
    return None


def _fallback_route(query, contract_context):
    q = (query or "").strip().lower()
    if contract_context:
        return "ANSWERABLE", False, ""
    off_topic = (
        "weather", "recipe", "football score", "cricket score", "debug my code",
        "python", "javascript", "react", "fastapi", "machine learning", "kaggle", "datathon",
        "আবহাওয়া", "রেসিপি", "ক্রিকেটের স্কোর", "পাইথন কোড", "জাভাস্ক্রিপ্ট"
    )
    if any(term in q for term in off_topic):
        return "OUT_OF_SCOPE", False, "আমি বাংলাদেশি আইন, আইনি প্রক্রিয়া ও চুক্তি-সংক্রান্ত প্রশ্নে সহায়তা করি।"
    
    # ✅ FIX: যদি প্রশ্নে Section বা Article থাকে, তবে সরাসরি Answerable
    if any(k in q for k in ["section", "article", "ধারা", "অনুচ্ছেদ"]):
        return "ANSWERABLE", True, ""

    if len(q.split()) <= 2:
        return "NEEDS_CLARIFICATION", False, "অনুগ্রহ করে আপনার পরিস্থিতি বা আইনি সমস্যাটি আরও বিস্তারিতভাবে লিখুন, যাতে আমি সঠিক আইন ও ধারা বলতে পারি।"
        
    return "ANSWERABLE", True, ""


async def classify_query_node(state: QAState) -> dict:
    query = (state.get("query") or "").strip()
    contract_context = state.get("contract_context", "") or ""
    history = state.get("messages", []) or []
    
    if not query:
        return {"final_answer": "অনুগ্রহ করে আপনার আইনি প্রশ্নটি লিখুন।", "retrieved_docs": [], "citation_verified": False}

    # ✅ FIX: যদি ইউজার সরাসরি কোনো ধারা/অনুচ্ছেদ জিজ্ঞেস করে, তবে LLM রাউটার বাইপাস করে সরাসরি সার্চে যাবে
    if re.search(r"(section|article|ধারা|অনুচ্ছেদ)\s*\d+", query, re.IGNORECASE):
        logger.info("[classify_query_node] Bypassing LLM router (Explicit Section/Article detected).")
        return {"final_answer": None, "query_type": "ANSWERABLE", "needs_case_law": False, "retrieved_docs": [], "citation_verified": False}
        
    try:
        llm = get_llm()
        history_text = "\n".join(
            f"{getattr(m, 'type', 'message')}: {_content_text(getattr(m, 'content', ''))}" for m in history[-6:]
        )
        prompt = (
            f"Contract context available: {'yes' if contract_context else 'no'}\n"
            f"Recent conversation:\n{history_text or '(none)'}\n\n"
            f"Current request:\n{query}"
        )
        result = await llm.ainvoke([SystemMessage(content=QUERY_ROUTER_PROMPT), HumanMessage(content=prompt)])
        parsed = _json_object(_content_text(getattr(result, 'content', result))) or {}
        
        status = str(parsed.get("status", "")).strip().upper()
        response = str(parsed.get("response", "")).strip()
        needs_case_law = bool(parsed.get("needs_case_law", False))

        if status not in {"ANSWERABLE", "NEEDS_CLARIFICATION", "OUT_OF_SCOPE", "INSUFFICIENT_EVIDENCE"}:
            status, needs_case_law, response = _fallback_route(query, contract_context)

        if status == "NEEDS_CLARIFICATION" and not contract_context:
            return {"final_answer": response, "query_type": status, "retrieved_docs": [], "citation_verified": False}

        if status in {"OUT_OF_SCOPE", "INSUFFICIENT_EVIDENCE"}:
            return {"final_answer": response, "query_type": status, "retrieved_docs": [], "citation_verified": False}

        return {"final_answer": None, "query_type": "ANSWERABLE", "needs_case_law": needs_case_law, "retrieved_docs": [], "citation_verified": False}

    except Exception:
        logger.exception("[classify_query_node] failed; using fallback router")
        status, needs_case_law, response = _fallback_route(query, contract_context)
        if status in {"NEEDS_CLARIFICATION", "OUT_OF_SCOPE", "INSUFFICIENT_EVIDENCE"}:
            return {"final_answer": response, "query_type": status, "retrieved_docs": [], "citation_verified": False}
        return {"final_answer": None, "query_type": "ANSWERABLE", "needs_case_law": needs_case_law, "retrieved_docs": [], "citation_verified": False}


def route_after_classification(state: QAState) -> str:
    return "stop" if state.get("final_answer") else "retrieve"


async def retrieve_node(state: QAState) -> dict:
    """Qdrant থেকে আইনের ধারা আনে। MCP দিয়ে লাইভ ওয়েব সার্চ হয় যদি needs_case_law True হয়।"""
    query = state.get("query", "")
    history_messages = state.get("messages", [])
    needs_case_law = state.get("needs_case_law", False)
    context_docs = []

    # 1. Primary Statutory Retrieval (Qdrant)
    try:
        llm = get_llm()
        results = await hybrid_retriever.aretrieve(query, history=history_messages, llm_instance=llm, top_k=8)
        for r in results:
            content_text = r.get("text") or r.get("content", "")
            if content_text and content_text.strip():
                context_docs.append({
                    "id": r.get("id", ""),
                    "act": r.get("act", "Bangladesh Law"),
                    "section": r.get("section", ""),
                    "title": r.get("title", ""),
                    "content": content_text.strip(),
                    "source_type": "statute",
                    "paths": r.get("paths", []),
                    "metadata": r.get("metadata", {}),
                })
        logger.info(f"[retrieve_node] {len(context_docs)} statute docs found.")
    except Exception as e:
        logger.exception(f"[retrieve_node] Hybrid Retriever fetch error: {e}")

    # 2. Live Precedent Search (MCP) - only if router flagged it
    if needs_case_law:
        try:
            logger.info(f"[retrieve_node] Triggering MCP live case law search for: {query}")
            live_results = await search_live_bd_judgements(query=query, max_results=3)
            
            for hit in live_results:
                snippet = hit.get("snippet", "").strip()
                if snippet:
                    context_docs.append({
                        "id": hit.get("reference_url", "live_search"),
                        "act": hit.get("source", "Supreme Court / Legal News"),
                        "section": hit.get("title", "Case Precedent"),
                        "title": hit.get("title", ""),
                        "content": f"{snippet}\nReference Source: {hit.get('reference_url')}",
                        "source_type": "precedent",
                        "reference_url": hit.get("reference_url", ""),
                        "metadata": {
                            "url": hit.get("reference_url"),
                            "tier": hit.get("tier", 2) 
                        },
                    })
        except Exception as e:
            logger.exception(f"[retrieve_node] MCP live judgment error: {e}")
    else:
        logger.info("[retrieve_node] Skipped MCP web search.")

    return {"retrieved_docs": context_docs}


async def generate_answer_node(state: QAState) -> dict:
    """রিট্রিভ হওয়া ধারার ওপর ভিত্তি করে কাঠামোগত JSON উত্তর তৈরি করে।"""
    query = state.get("query", "")
    raw_docs = state.get("retrieved_docs", []) or []
    history_messages = state.get("messages", [])
    contract_context = state.get("contract_context", "")

    statutory_blocks, precedent_blocks = [], []

    for d in raw_docs:
        if not isinstance(d, dict): continue
        text = d.get("text") or d.get("content", "")
        act = d.get("act", "Bangladesh Statute")
        sec = d.get("section", "")
        title = d.get("title", "")

        if d.get("source_type") == "precedent":
            # ✅ FIX: কেস ল'র সাথে সোর্স লিংক এবং টাইটেল স্পষ্টভাবে LLM-কে দেখানো হচ্ছে
            ref_url = d.get("reference_url", "") or d.get("metadata", {}).get("url", "")
            precedent_blocks.append(
                f"• [LIVE WEB RESULT]: {title}\n"
                f"  Snippet: {text}\n"
                f"  Source URL: {ref_url}"
            )
        else:
            header_info = f"{act} | Section: {sec}".strip(" |")
            if title:
                header_info += f" ({title})"
            statutory_blocks.append(f"=== STATUTE: {header_info} ===\n{text}")

    # কনটেক্সট না থাকলে
    if not statutory_blocks and not precedent_blocks and not contract_context:
        logger.warning(f"[generate_answer_node] no context for query: {query[:80]!r}")
        return {
            "final_answer": NO_CONTEXT_ANSWER,
            "retrieved_docs": raw_docs,
            "citation_verified": False,
            "structured_response": None
        }

    context_sections = []
    if contract_context:
        context_sections.append(f"=== CURRENT CONTRACT CONTEXT ===\n{contract_context}\n================================")
    if statutory_blocks:
        context_sections.append("VERIFIED_STATUTORY_CONTEXT:\n" + "\n\n".join(statutory_blocks))
    if precedent_blocks:
        # ✅ FIX: LLM-কে বলা হচ্ছে এটি লাইভ ওয়েব সার্চের রেজাল্ট
        context_sections.append("VERIFIED_CASE_CONTEXT (Live Web Search Results - MUST BE CITED IN DETAILED ANSWER & JSON):\n" + "\n\n".join(precedent_blocks))
        
    context_text = "\n\n---\n\n".join(context_sections)

    user_prompt = (
        f"{context_text}\n\n"
        f"================================\n"
        f"Client Legal Query:\n{query}\n\n"
        f"Instructions: Provide a well-structured, clear, and accurate response based strictly on the verified context above. Return strictly a JSON object."
    )

    try:
        llm = get_llm()
        messages = [SystemMessage(content=SYSTEM_QA_PROMPT)]
        if history_messages:
            messages.extend(history_messages[-6:])
        messages.append(HumanMessage(content=user_prompt))

        response = await llm.ainvoke(messages)
        raw_answer = _content_text(getattr(response, "content", response))
        
        parsed_json = _json_object(raw_answer)
        
        if parsed_json and "detailed_answer" in parsed_json:
            final_text = parsed_json.get("detailed_answer", "")
        else:
            final_text = raw_answer
            parsed_json = {
                "detailed_answer": final_text,
                "cited_sections": [],
                "cited_cases": [],
                "suggested_next_steps": []
            }

        if not final_text:
            raise RuntimeError("LLM returned an empty answer")

    except Exception as exc:
        logger.error(f"[generate_answer_node] Error invoking LLM: {exc}")
        raise exc

    return {
        "final_answer": final_text,
        "structured_response": parsed_json,
        "retrieved_docs": raw_docs,
        "citation_verified": True,
    }


# Build LangGraph workflow
workflow = StateGraph(QAState)
workflow.add_node("classify_query", classify_query_node)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate_answer", generate_answer_node)

workflow.set_entry_point("classify_query")
workflow.add_conditional_edges(
    "classify_query",
    route_after_classification,
    {"stop": END, "retrieve": "retrieve"},
)
workflow.add_edge("retrieve", "generate_answer")
workflow.add_edge("generate_answer", END)

qa_graph = workflow.compile()