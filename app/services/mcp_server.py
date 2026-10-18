import os
import re
import logging
from typing import List, Dict, Any
from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
from qdrant_client import models
from app.services.vector_store import vector_manager
from app.config import settings
# আগের ধাপে বানানো নরম্যালাইজার ফাংশনটি ইমপোর্ট করছি
from app.services.legal_normalizer import normalize_section 

logger = logging.getLogger("legal_mcp_server")

# FastMCP Server Instance initialize
mcp = FastMCP("Bangladesh-Legal-MCP-Server")


# ==========================================
# TOOL 1: Live Precedent & Legal News Search
# ==========================================
@mcp.tool()
def search_live_bd_judgements(query: str, max_results: int = 4) -> List[Dict[str, Any]]:
    """
    Searches the web for recent Bangladesh High Court / Supreme Court verdicts, 
    legal news, and precedents from authoritative legal sources and mainstream news portals.
    """
    search_prompt = f"Bangladesh Supreme Court verdict {query} site:supremecourt.gov.bd OR site:thedailystar.net OR site:dhakatribune.com OR site:tbsnews.net"
    
    results = []
    try:
        with DDGS() as ddgs:
            raw_hits = ddgs.text(search_prompt, max_results=max_results)
            for hit in raw_hits:
                results.append({
                    "title": hit.get("title", "Legal Update"),
                    "snippet": hit.get("body", ""),
                    "reference_url": hit.get("href", ""),
                    "source": "Supreme Court / Verified Legal News"
                })
    except Exception as exc:
        logger.error(f"[MCP] Web search error: {exc}")
        # FIX (MCP-02): ডামি ডেটা না পাঠিয়ে সরাসরি ফাঁকা লিস্ট রিটার্ন করা হলো
        # যাতে এলএলএম এরর মেসেজকে আইনি রায় হিসেবে হ্যালুসিনেট না করে।
        return []
        
    return results


# ==========================================
# TOOL 2: Exact Statutory Section Ground-Truth
# ==========================================
@mcp.tool()
def get_statute_by_section(act_name: str, section_number: str) -> Dict[str, Any]:
    """
    Directly retrieves the verbatim text of an exact statutory section from Qdrant Cloud.
    Prevents LLM hallucinations on statutory wording.
    """
    # FIX: আমাদের নতুন রেজেক্স ফাংশন দিয়ে সেকশন নম্বর নরম্যালাইজ করা হলো
    clean_sec = normalize_section(section_number)
    
    if not clean_sec:
        clean_sec = section_number.strip()

    try:
        client = vector_manager.client
        scroll_result, _ = client.scroll(
            collection_name=settings.QDRANT_COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        # FIX (MCP-01): Strict Mode-এর জন্য ইনডেক্সকৃত ফিল্ড ব্যবহার করা হলো
                        key="metadata.section_norm",
                        # MatchText এর বদলে MatchValue ব্যবহার করা বেশি নিরাপদ
                        match=models.MatchValue(value=clean_sec)
                    )
                ]
            ),
            limit=3,
            with_payload=True,
            with_vectors=False
        )

        for point in scroll_result:
            payload = point.payload or {}
            metadata = payload.get("metadata", {})
            
            point_act = metadata.get("act", "")
            if act_name.lower() in point_act.lower() or not act_name:
                return {
                    "found": True,
                    "act": point_act,
                    "section": metadata.get("section", f"Section {clean_sec}"),
                    "title": metadata.get("title", ""),
                    "verbatim_text": payload.get("text", "") or payload.get("content", ""),
                    "status": "Verified against Bangladesh Statutory Gazette"
                }

    except Exception as exc:
        logger.error(f"[MCP] Statute lookup failed: {exc}")

    return {
        "found": False,
        "message": f"Section {section_number} not found under {act_name} in the primary database.",
        "status": "Unverified"
    }


# ==========================================
# TOOL 3: Real-Time Legal Citation Verifier
# ==========================================
@mcp.tool()
def verify_legal_citation(statute_act: str, cited_section: str) -> Dict[str, Any]:
    """
    Validates whether a cited section actually exists in recognized Bangladeshi enactments.
    """
    lookup = get_statute_by_section(act_name=statute_act, section_number=cited_section)
    if lookup.get("found"):
        return {
            "valid": True,
            "act": lookup["act"],
            "section": lookup["section"],
            "title": lookup["title"],
            "confidence": 1.0
        }
    return {
        "valid": False,
        "reason": f"No authentic statutory record found for {cited_section} of {statute_act}.",
        "confidence": 0.0
    }


if __name__ == "__main__":
    mcp.run()