import os
import re
import logging
import asyncio
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
from qdrant_client import models
from tavily import TavilyClient

from app.services.vector_store import vector_manager
from app.config import settings
from app.services.legal_normalizer import resolve_act_robust, get_section_variants

logger = logging.getLogger("legal_mcp_server")

# FastMCP Server Instance initialize
mcp = FastMCP("Bangladesh-Legal-MCP-Server")

# ==========================================
# TAVILY SETUP & DOMAIN ALLOWLIST
# ==========================================
TAVILY_API_KEY = getattr(settings, "TAVILY_API_KEY", os.getenv("TAVILY_API_KEY"))
_tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

# Tier 1: Government & Official Sources
TIER1_DOMAINS = [
    "supremecourt.gov.bd",
    "bdlaws.minlaw.gov.bd",
    "minlaw.gov.bd",
    "lawjustic.gov.bd",
    "barcouncil.gov.bd",
    "dpp.gov.bd",
    "nhrc.org.bd",
]

# Tier 2: Reputable Legal News (Only used if Tier 1 is empty)
TIER2_DOMAINS = [
    "thedailystar.net",
    "dhakatribune.com",
    "tbsnews.net",
    "newagebd.net",
]

ALLOWED_DOMAINS = set(TIER1_DOMAINS + TIER2_DOMAINS)

# Legal relevance signals to filter out non-legal news (sports, politics, entertainment)
LEGAL_SIGNALS = (
    "court", "verdict", "judgment", "judgement", "ruling", "appeal",
    "petition", "writ", "high court", "supreme court", "appellate",
    "section", "act", "statute", "precedent", "plaintiff", "defendant",
    "criminal", "civil", "tribunal", "decree", "ordinance", "law",
    "আদালত", "রায়", "মামলা", "আপিল", "ধারা", "আইন", "বিচার", "আদেশ", "আইনি"
)

def _is_allowed(href: str) -> bool:
    """Checks if the URL belongs to our allowed domain list."""
    try:
        host = (urlparse(href).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def _looks_legal(*parts: str) -> bool:
    """Checks if the title/snippet contains legal keywords."""
    text = " ".join(p for p in parts if p).lower()
    return any(k in text for k in LEGAL_SIGNALS)


# ==========================================
# SEARCH HELPERS (TAVILY & DDGS)
# ==========================================
def _tavily_search_sync(query: str, domains: List[str], n: int) -> List[Dict[str, Any]]:
    if not _tavily_client:
        return []
    try:
        # Tavily Search API call
        resp = _tavily_client.search(
            query=query,
            include_domains=domains,
            search_depth="basic",  # 1 credit per search
            max_results=n,
        )
        return resp.get("results", [])
    except Exception as exc:
        logger.warning(f"[Tavily] Search failed: {exc}")
        return []

def _ddg_one_site_sync(query: str, site: str, n: int) -> List[Dict[str, Any]]:
    """Fallback: DuckDuckGo search for a single site."""
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(f"{query} site:{site}", max_results=n, region="bd-en"))
    except Exception as exc:
        logger.warning(f"[DDGS] Failed for {site}: {exc}")
        return []


# ==========================================
# TOOL 1: Live Precedent & Legal News Search
# ==========================================
@mcp.tool()
async def search_live_bd_judgements(query: str, max_results: int = 4) -> List[Dict[str, Any]]:
    """
    Searches for recent Bangladesh High Court / Supreme Court verdicts.
    Uses Tavily API primarily (Tier 1 Gov -> Tier 2 News).
    Falls back to DuckDuckGo only if Tavily fails or returns empty.
    """
    if not query or not query.strip():
        return []

    clean_query = f"Bangladesh Supreme Court verdict {query}".strip()
    seen = set()
    merged = []

    def _add_results(results, tier, source_name):
        for r in results:
            if len(merged) >= max_results:
                return
            
            # Tavily returns 'url', DDGS returns 'href'
            url = r.get("url") or r.get("href") or ""
            title = r.get("title") or "Legal Update"
            snippet = r.get("content") or r.get("body") or ""

            if not url or url in seen or not _is_allowed(url):
                continue
            if not _looks_legal(title, snippet):
                continue

            seen.add(url)
            merged.append({
                "title": title,
                "snippet": snippet,
                "reference_url": url,
                "source": f"{source_name} (Tier {tier})",
                "tier": tier
            })

    # --- Strategy 1: Tavily (Tier 1 Gov) ---
    if _tavily_client:
        t1_res = await asyncio.to_thread(_tavily_search_sync, clean_query, TIER1_DOMAINS, max_results)
        _add_results(t1_res, tier=1, source_name="Tavily/Gov")

    # --- Strategy 2: Tavily (Tier 2 News) ---
    if _tavily_client and len(merged) < max_results:
        t2_res = await asyncio.to_thread(_tavily_search_sync, clean_query, TIER2_DOMAINS, max_results)
        _add_results(t2_res, tier=2, source_name="Tavily/News")

    # --- Strategy 3: DDGS Fallback (If Tavily fails or key missing) ---
    if not merged:
        logger.info("[MCP] Tavily returned 0 results. Falling back to DDGS...")
        for site in TIER1_DOMAINS[:4]:  # Limit to top 4 gov sites to avoid timeout
            if len(merged) >= max_results:
                break
            d_res = await asyncio.to_thread(_ddg_one_site_sync, clean_query, site, 2)
            _add_results(d_res, tier=1, source_name="DDGS/Gov")
            
        for site in TIER2_DOMAINS[:2]:  # Limit to top 2 news sites
            if len(merged) >= max_results:
                break
            d_res = await asyncio.to_thread(_ddg_one_site_sync, clean_query, site, 2)
            _add_results(d_res, tier=2, source_name="DDGS/News")

    logger.info(f"[MCP] search_live_bd_judgements: {len(merged)} clean results for '{query[:40]}'")
    return merged[:max_results]


# ==========================================
# TOOL 2: Exact Statutory Section Ground-Truth
# ==========================================
@mcp.tool()
def get_statute_by_section(act_name: str, section_number: str) -> Dict[str, Any]:
    """
    Directly retrieves the verbatim text of an exact statutory section from Qdrant Cloud.
    Uses robust act resolution and section variants to prevent cross-act collisions.
    """
    # 1. Resolve canonical enactment title across aliases (Bengali or English)
    act_stored = resolve_act_robust(act_name) 
    
    # 2. Compute statutory section variants (e.g., "ধারা ২৬ক" -> ["26A", "26a"])
    sec_variants = get_section_variants(section_number)
    if not sec_variants:
        sec_variants = [section_number.strip()]

    must_conditions = [
        models.FieldCondition(
            key="metadata.section_norm",
            match=models.MatchAny(any=sec_variants),
        )
    ]
    
    if act_stored:
        must_conditions.insert(0, models.FieldCondition(
            key="metadata.act_norm",
            match=models.MatchValue(value=act_stored),
        ))
    else:
        logger.warning(f"[MCP] Act '{act_name}' not resolved — section-only lookup (risk of collision).")

    try:
        client = vector_manager.client
        scroll_result, _ = client.scroll(
            collection_name=settings.QDRANT_COLLECTION_NAME,
            scroll_filter=models.Filter(must=must_conditions),
            limit=5,
            with_payload=True,
            with_vectors=False
        )

        for point in scroll_result:
            payload = point.payload or {}
            metadata = payload.get("metadata", {})
            
            point_act = metadata.get("act", "")
            
            # Fallback guard: match substring if alias resolution returns empty
            if not act_stored and act_name:
                if act_name.lower() not in point_act.lower():
                    continue

            return {
                "found": True,
                "act": point_act,
                "act_norm": metadata.get("act_norm", ""),
                "section": metadata.get("section", f"Section {section_number}"),
                "section_norm": metadata.get("section_norm", ""),
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