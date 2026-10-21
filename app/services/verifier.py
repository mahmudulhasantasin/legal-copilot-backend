import re
from typing import Dict, Any, List

BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


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

    def verify_structured_response(self, parsed_json: dict, retrieved_docs: List[dict]) -> dict:
        """
        [NEW STRICT VERIFIER] Verifies the LLM's JSON output against retrieved docs.
        Checks if the cited sections and case laws actually exist in the Qdrant/MCP context.
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

        # 2. Verify Statutory Sections
        for sec in cited_sections:
            act_name = str(sec.get("act", "")).translate(BN_TO_EN_DIGITS).lower()
            sec_num = str(sec.get("section", "")).translate(BN_TO_EN_DIGITS).lower()

            # Clean up the section number (remove 'section', 'sec', 'ধারা', brackets etc.)
            sec_num_clean = re.sub(r'(section|sec\.?|ধারা|[\{\}\[\]\(\)])', '', sec_num).strip()

            if not sec_num_clean:
                continue

            # Check if the section number exists in the statute context as a whole word
            # \b is used to prevent matching "7" when the context has "73"
            is_valid = bool(re.search(rf"\b{re.escape(sec_num_clean)}\b", statute_context))
            
            # Edge case: If act name is provided, ensure it roughly matches the context
            if is_valid and act_name and len(act_name) > 5:
                # Just check if the first 5 chars of the act name exist to avoid mismatches like "Penal Code" vs "Contract Act"
                act_prefix = act_name[:5]
                if act_prefix not in statute_context:
                    is_valid = False

            verified_sections.append({
                "act": sec.get("act"),
                "section": sec.get("section"),
                "valid": is_valid,
                "status": "Verified" if is_valid else "Unverified (Hallucination suspected)"
            })
            if not is_valid:
                all_sections_valid = False

        # 3. Verify Case Laws
        verified_cases = []
        all_cases_valid = True

        for case in cited_cases:
            case_name = str(case.get("case_name", "")).lower().strip()

            if not case_name or len(case_name) < 5:
                continue

            # Case names are usually unique enough for a substring match.
            # e.g., "Abdul Kadir vs State"
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