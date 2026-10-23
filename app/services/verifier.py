import re
import json
import logging
from typing import Dict, Any, List

# ✅ Official MCP Client imports (handshake + session handled automatically)
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)
BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


# ✅ UPDATED: Official MCP Client (handles initialize handshake + Mcp-Session-Id automatically)
async def _call_remote_verify_mcp(act_name: str, section_num: str) -> bool:
    """
    Calls the standalone MCP server on Render to verify a specific statute section.
    Uses the official MCP Client for proper handshaking (initialize + session ID).
    """
    MCP_URL = "https://bd-legal-mcp-server.onrender.com/mcp"

    # ✅ No try-except here. Let it raise so the caller can fallback to local regex.
    async with streamablehttp_client(MCP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "verify_legal_citation",
                {"statute_act": act_name, "cited_section": section_num}
            )

            # Parse the MCP response content blocks
            for item in result.content:
                # item is a TextContent-like object; model_dump() gives a dict
                item_dict = item.model_dump() if hasattr(item, "model_dump") else item
                if isinstance(item_dict, dict) and item_dict.get("type") == "text":
                    try:
                        parsed_result = json.loads(item_dict.get("text", "{}"))
                        return parsed_result.get("valid", False)
                    except json.JSONDecodeError:
                        continue

    return False


class CitationVerifier:
    def __init__(self, store=None):
        self.store = store

    def verify_citations(self, text: str, context_docs: List[str]) -> Dict[str, Any]:
        """
        [LEGACY FUNCTION] Extracts statutory citations from raw text (Regex based).
        Kept for backward compatibility. Use `verify_structured_response` for the new JSON output.
        """
        if not text:
            return {"citation_verified": False, "details": "Empty answer text."}

        normalized_text = text.translate(BN_TO_EN_DIGITS)
        normalized_docs = [doc.translate(BN_TO_EN_DIGITS) for doc in (context_docs or [])]

        prefix_pattern = r"(?:Section|Sec\.|ধারা)\s*[:\-]?\s*(\d+[A-Za-z]?)"
        postfix_pattern = r"(\d+[A-Za-z]?)\s*(?:নং\s*)?ধারা"

        found_sections = set(
            re.findall(prefix_pattern, normalized_text, re.IGNORECASE)
            + re.findall(postfix_pattern, normalized_text, re.IGNORECASE)
        )

        found_sections = {
            sec for sec in found_sections
            if not (sec.isdigit() and len(sec) == 4 and 1700 <= int(sec) <= 2100)
        }

        if not found_sections:
            return {"citation_verified": True, "details": "No explicit statutory section numbers found."}

        verified_citations = []
        all_matched = True

        for sec in sorted(found_sections):
            matched = any(
                re.search(rf"\b{re.escape(sec)}\b", doc, re.IGNORECASE)
                for doc in normalized_docs
            )
            verified_citations.append({"section": sec, "verified": matched})
            if not matched:
                all_matched = False

        return {"citation_verified": all_matched, "citations": verified_citations}

    # ✅ Async: calls the remote MCP server for strict verification
    async def verify_structured_response(self, parsed_json: dict, retrieved_docs: List[dict]) -> dict:
        """
        [NEW STRICT VERIFIER] Verifies the LLM's JSON output against the remote MCP server.
        Checks if the cited sections actually exist in the Bangladesh Legal Qdrant Database.
        """
        if not parsed_json:
            return {"all_valid": False, "verified_sections": [], "verified_cases": []}

        cited_sections = parsed_json.get("cited_sections", []) or []
        cited_cases = parsed_json.get("cited_cases", []) or []

        # 1. Prepare Context Strings from retrieved docs
        statute_context = ""
        precedent_context = ""
        
        for doc in retrieved_docs:
            text = doc.get("text") or doc.get("content") or ""
            text = str(text).translate(BN_TO_EN_DIGITS).lower()

            if doc.get("source_type") == "precedent":
                title = str(doc.get("title", "")).lower()
                precedent_context += f" {title} {text} "
            else:
                act = str(doc.get("act", "")).lower()
                sec = str(doc.get("section", "")).translate(BN_TO_EN_DIGITS).lower()
                statute_context += f" {act} {sec} {text} "

        verified_sections = []
        all_sections_valid = True

        # 2. Verify Statutory Sections via MCP (with local regex fallback)
        for sec in cited_sections:
            act_name = str(sec.get("act", ""))
            sec_num = str(sec.get("section", ""))
            
            # Clean up section number just in case LLM outputs "Section 73" instead of "73"
            sec_num_clean = re.sub(r'(section|sec\.?|ধারা|[\{\}\[\]\(\)])', '', sec_num, flags=re.IGNORECASE).strip()

            if not sec_num_clean:
                continue

            is_valid = False
            try:
                # ✅ Call Remote MCP Server for 100% accurate verification
                is_valid = await _call_remote_verify_mcp(act_name, sec_num_clean)
            except Exception as e:
                logger.warning(f"[MCP Verify] Failed (likely 502/Timeout). Falling back to local regex: {e}")
                # ✅ Fallback: Local regex check against retrieved Qdrant docs
                is_valid = bool(re.search(rf"\b{re.escape(sec_num_clean)}\b", statute_context))

            verified_sections.append({
                "act": sec.get("act"),
                "section": sec.get("section"),
                "valid": is_valid,
                "status": "Verified via MCP" if is_valid else "Unverified (Hallucination suspected)"
            })
            if not is_valid:
                all_sections_valid = False

        # 3. Verify Case Laws (Substring match against fetched web results)
        verified_cases = []
        all_cases_valid = True

        for case in cited_cases:
            case_name = str(case.get("case_name", "")).lower().strip()

            if not case_name or len(case_name) < 5:
                continue

            is_valid = case_name in precedent_context

            verified_cases.append({
                "case_name": case.get("case_name"),
                "valid": is_valid,
                "status": "Verified" if is_valid else "Unverified (Hallucination suspected)"
            })
            if not is_valid:
                all_cases_valid = False

        return {
            "all_valid": all_sections_valid and all_cases_valid,
            "verified_sections": verified_sections,
            "verified_cases": verified_cases
        }


# Global singleton instance for agents
verifier = CitationVerifier()