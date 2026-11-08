import logging
from typing import TypedDict, List, Dict, Any, Optional

from langgraph.graph import StateGraph, END
from app.agents.nodes import (
    retrieve_statutes_node,
    analyze_risk_node,
    MAX_CONTRACT_CHARS,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)

logger = logging.getLogger(__name__)

CONTRACT_TEXT_LIMIT = MAX_CONTRACT_CHARS


# ── LangGraph State (single-chunk গ্রাফের জন্য, fallback হিসেবে রাখা হলো) ──
class ContractState(TypedDict):
    contract_text: str
    playbook_rules: List[Dict[str, Any]]
    retrieved_statutes: List[Dict[str, Any]]
    analysis: Dict[str, Any]


workflow = StateGraph(ContractState)
workflow.add_node("retrieve_statutes", retrieve_statutes_node)
workflow.add_node("analyze_risks", analyze_risk_node)
workflow.set_entry_point("retrieve_statutes")
workflow.add_edge("retrieve_statutes", "analyze_risks")
workflow.add_edge("analyze_risks", END)
contract_graph = workflow.compile()


# ── Chunking helpers ────────────────────────────────────────

def _split_into_chunks(text: str) -> List[str]:
    """
    Contract text-কে CHUNK_SIZE-এর টুকরোয় ভাগ করে।
    প্রতিটি chunk-এ আগের chunk-এর শেষ CHUNK_OVERLAP char থাকে,
    যাতে clause boundary-তে context না কাটে।
    """
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


_RISK_PRIORITY = {"LOW": 1, "MEDIUM": 2, "MODERATE": 2, "HIGH": 3, "CRITICAL": 3}


def _merge_chunk_results(chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    একাধিক chunk-এর analysis result মার্জ করে একটি final output তৈরি করে।
    - detected_risks: সব chunk মিলিয়ে, clause_title দিয়ে dedup
    - overall_risk_score: সর্বোচ্চ risk টি নেওয়া হয়
    - memo_summary: সব chunk-এর summary যোগ করা হয়
    """
    all_risks: List[dict] = []
    summaries: List[str] = []
    best_score = "LOW"

    for result in chunk_results:
        risks = result.get("detected_risks", [])
        all_risks.extend(risks)

        summary = result.get("memo_summary", "").strip()
        if summary and summary not in summaries:
            summaries.append(summary)

        score = str(result.get("overall_risk_score", "LOW")).upper()
        if _RISK_PRIORITY.get(score, 0) > _RISK_PRIORITY.get(best_score, 0):
            best_score = score

    # clause_title দিয়ে dedup (case-insensitive)
    seen: set = set()
    unique_risks: List[dict] = []
    for risk in all_risks:
        key = risk.get("clause_title", "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique_risks.append(risk)
        elif not key:
            unique_risks.append(risk)  # title নেই → রাখা হলো

    combined_summary = " ".join(summaries) if summaries else "Contract analyzed successfully."

    total_clauses_estimate = sum(
        int(r.get("estimated_total_clauses") or 0) for r in chunk_results
    )

    return {
        "overall_risk_score": best_score,
        "memo_summary": combined_summary,
        "detected_risks": unique_risks,
        "estimated_total_clauses": total_clauses_estimate,
    }


# ── Main async entry point ──────────────────────────────────

async def analyze_contract_risk_async(
    contract_text: str,
    playbook_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Chunked async contract analysis:
      1. পুরো text-কে MAX_CONTRACT_CHARS-এ ছেঁটে নাও (~10 পেজ)
      2. Statutes একবার retrieve করো (contract শুরু দেখে)
      3. প্রতিটি chunk-এ LLM analysis চালাও (concurrently)
      4. সব chunk-এর result merge করো
    """
    rules = playbook_rules or []
    text = contract_text[:MAX_CONTRACT_CHARS]

    # Step 1: Retrieve statutes once for the whole contract
    try:
        statute_state = await retrieve_statutes_node(
            {"contract_text": text, "playbook_rules": rules}
        )
        retrieved_statutes = statute_state.get("retrieved_statutes", [])
    except Exception as exc:
        logger.error(f"[contract_graph] Statute retrieval failed: {exc}")
        retrieved_statutes = []

    # Step 2: Split into chunks and analyze concurrently
    chunks = _split_into_chunks(text)
    logger.info(f"[contract_graph] Analyzing {len(chunks)} chunk(s) ({len(text)} chars total)")

    async def _analyze_chunk(chunk: str, idx: int) -> Dict[str, Any]:
        try:
            result = await analyze_risk_node({
                "contract_text": chunk,
                "retrieved_statutes": retrieved_statutes,
                "playbook_rules": rules,
            })
            return result.get("analysis", {})
        except Exception as exc:
            logger.error(f"[contract_graph] Chunk {idx} analysis failed: {exc}")
            return {"overall_risk_score": "MEDIUM", "memo_summary": "", "detected_risks": []}

    chunk_results = await asyncio.gather(
        *[_analyze_chunk(chunk, i) for i, chunk in enumerate(chunks)]
    )

    # Step 3: Merge
    return _merge_chunk_results(list(chunk_results))


def analyze_contract_risk(
    contract_text: str,
    playbook_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Synchronous fallback (scripts / testing use only)."""
    import asyncio as _asyncio
    return _asyncio.run(
        analyze_contract_risk_async(contract_text, playbook_rules)
    )
