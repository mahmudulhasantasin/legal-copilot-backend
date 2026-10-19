import re
from typing import Dict, Any, List

BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


class CitationVerifier:
    def __init__(self, store=None):
        self.store = store

    def verify_citations(self, text: str, context_docs: List[str]) -> Dict[str, Any]:
        """
        Extracts statutory citations (both English and Bengali formats)
        and cross-checks against retrieved context documents.
        """
        if not text:
            return {"citation_verified": False, "details": "Empty answer text."}

        normalized_text = text.translate(BN_TO_EN_DIGITS)
        normalized_docs = [doc.translate(BN_TO_EN_DIGITS) for doc in (context_docs or [])]

        # Match patterns like: "Section 73", "Sec. 23", "ধারা 73", "ধারা-73"
        prefix_pattern = r"(?:Section|Sec\.|ধারা)\s*[:\-]?\s*(\d+[A-Za-z]?)"
        # Match patterns like: "73 ধারা", "73 নং ধারা"
        postfix_pattern = r"(\d+[A-Za-z]?)\s*(?:নং\s*)?ধারা"

        found_sections = set(
            re.findall(prefix_pattern, normalized_text, re.IGNORECASE)
            + re.findall(postfix_pattern, normalized_text, re.IGNORECASE)
        )

        # Filter out 4-digit years (e.g., 1872, 2006) mistakenly matched near the word ধারা
        found_sections = {
            sec for sec in found_sections
            if not (sec.isdigit() and len(sec) == 4 and 1700 <= int(sec) <= 2100)
        }

        if not found_sections:
            return {
                "citation_verified": True,
                "details": "No explicit statutory section numbers found to verify."
            }

        verified_citations = []
        all_matched = True

        for sec in sorted(found_sections):
            matched = any(
                re.search(rf"\b{re.escape(sec)}\b", doc, re.IGNORECASE)
                for doc in normalized_docs
            )
            verified_citations.append({
                "section": sec,
                "verified": matched
            })
            if not matched:
                all_matched = False

        return {
            "citation_verified": all_matched,
            "citations": verified_citations
        }


# Global singleton instance for agents
verifier = CitationVerifier()