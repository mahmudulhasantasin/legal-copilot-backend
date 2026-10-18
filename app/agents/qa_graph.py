import logging
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from app.agents.prompts import SYSTEM_QA_PROMPT
from app.agents.state import QAState
from app.services.llm_provider import get_llm
from app.services.mcp_server import search_live_bd_judgements
from app.services.retriever import hybrid_retriever
from app.services.verifier import verifier

logger = logging.getLogger(__name__)

# Specific precedent triggers only (generic terms like 'আদালত' or 'বিচার' বাদ, ধীর ওয়েব সার্চ এড়াতে)
PRECEDENT_TRIGGERS = [
    "verdict", "judgment", "judgement", "precedent", "ruling", "case law",
    "supreme court verdict", "high court verdict",
    "রায়", "মামলার রায়", "নজির", "সুপ্রিম কোর্টের রায়", "হাইকোর্টের রায়",
]

NO_CONTEXT_ANSWER = (
    "দুঃখিত, আপনার প্রশ্নের সাথে সম্পর্কিত কোনো আইনের ধারা আমাদের ডাটাবেসে খুঁজে পাওয়া যায়নি। "
    "অনুগ্রহ করে পরিস্থিতিটি আরও সুনির্দিষ্টভাবে লিখুন (যেমন কোন ধরনের বিষয় বা কোন আইন সম্পর্কিত)।"
)


async def retrieve_node(state: QAState) -> dict:
    """Qdrant থেকে আইনের ধারা আনে। লাইভ ওয়েব সার্চ শুধু স্পষ্টভাবে রায়/নজির চাইলে।"""
    query = state.get("query", "")
    history_messages = state.get("messages", [])
    context_docs = []

    # 1. Primary Statutory Retrieval (Async + HyDE History)
    try:
        llm = get_llm()
        results = await hybrid_retriever.aretrieve(query, history=history_messages, llm_instance=llm, top_k=5)
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
        logger.info(f"[retrieve_node] {len(context_docs)} statute docs: "
                    f"{[(d.get('act'), d.get('section'), d.get('paths')) for d in context_docs]}")
    except Exception as e:
        logger.exception(f"[retrieve_node] Hybrid Retriever fetch error: {e}")

    # 2. Live precedent search: শুধু স্পষ্ট কিওয়ার্ড থাকলে
    normalized_q = query.lower()
    if any(term in normalized_q for term in PRECEDENT_TRIGGERS):
        try:
            logger.info(f"[retrieve_node] Triggering live precedent search for: {query}")
            for hit in search_live_bd_judgements(query=query, max_results=2):
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
                        "metadata": {"url": hit.get("reference_url")},
                    })
        except Exception as e:
            logger.exception(f"[retrieve_node] FastMCP live judgment error: {e}")
    else:
        logger.info("[retrieve_node] Skipped external web search to optimize latency.")

    return {"retrieved_docs": context_docs}


async def generate_answer_node(state: QAState) -> dict:
    """রিট্রিভ হওয়া ধারার ওপর ভিত্তি করে উত্তর তৈরি করে।
    সাইটেশন তৈরির দায়িত্ব chat.py-র (সেখানে retrieved_docs-এর বিপরীতে যাচাই হয়)।"""
    query = state.get("query", "")
    raw_docs = state.get("retrieved_docs", []) or []
    history_messages = state.get("messages", [])
    contract_context = state.get("contract_context", "")

    statutory_blocks, precedent_blocks, verification_texts = [], [], []

    for d in raw_docs:
        if not isinstance(d, dict):
            continue
        text = d.get("text") or d.get("content", "")
        act = d.get("act", "Bangladesh Statute")
        sec = d.get("section", "")
        title = d.get("title", "")

        if text:
            verification_texts.append(text)

        if d.get("source_type") == "precedent":
            precedent_blocks.append(f"• [Case Precedent]: {title}\n  Summary: {text}")
        else:
            header_info = f"{act} | {sec}".strip(" |")
            if title:
                header_info += f" ({title})"
            statutory_blocks.append(f"=== STATUTE: {header_info} ===\nText: {text}")

    # কনটেক্সট না থাকলে এলএলএম কল নয়: মেমোরি থেকে মনগড়া উত্তর আসার ঝুঁকি বন্ধ
    if not statutory_blocks and not precedent_blocks and not contract_context:
        logger.warning(f"[generate_answer_node] no context for query: {query[:80]!r}")
        return {
            "final_answer": NO_CONTEXT_ANSWER,
            "retrieved_docs": raw_docs,
            "citation_verified": False,
        }

    context_sections = []
    
    # নতুন: কন্ট্রাক্ট কনটেক্সট থাকলে সেটি সবার আগে পুশ করা হবে
    if contract_context:
        context_sections.append(f"=== CURRENT CONTRACT ANALYSIS (CRITICAL CONTEXT) ===\n{contract_context}\n===================================================")

    if statutory_blocks:
        context_sections.append("RELEVANT BANGLADESH STATUTES:\n" + "\n\n".join(statutory_blocks))
    if precedent_blocks:
        context_sections.append("RECENT COURT PRECEDENTS & NEWS REFERENCES:\n" + "\n\n".join(precedent_blocks))
        
    context_text = "\n\n---\n\n".join(context_sections)

    user_prompt = f"Verified Context:\n{context_text}\n\nClient Legal Query:\n{query}"

    try:
        llm = get_llm()
        messages = [SystemMessage(content=SYSTEM_QA_PROMPT)]
        if history_messages:
            messages.extend(history_messages)
        messages.append(HumanMessage(content=user_prompt))

        # NEW: ainvoke ব্যবহার করা হচ্ছে (Fast API-তে নন-ব্লকিং কলের জন্য)
        response = await llm.ainvoke(messages)
        
        answer_text = ""
        content = getattr(response, "content", response)
        if isinstance(content, list):
            answer_text = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item) for item in content
            ).strip()
        elif isinstance(content, str):
            answer_text = content.strip()

        if not answer_text:
            raise RuntimeError("LLM returned an empty answer")

    except Exception as exc:
        logger.error(f"[generate_answer_node] Error invoking LLM: {exc}")
        raise exc  # Exception থ্রো করা হচ্ছে যাতে chat.py 500 দেয় এবং ক্রেডিট না কাটে

    # Citation verifier
    is_verified = False
    try:
        result = verifier.verify_citations(answer_text, verification_texts)
        is_verified = bool(result.get("citation_verified", False))
    except Exception as e:
        logger.error(f"[generate_answer_node] verifier error: {e}")

    return {
        "final_answer": answer_text,
        "retrieved_docs": raw_docs,
        "citation_verified": is_verified,
    }


# Build LangGraph workflow
workflow = StateGraph(QAState)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate_answer", generate_answer_node)

workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "generate_answer")
workflow.add_edge("generate_answer", END)

qa_graph = workflow.compile()