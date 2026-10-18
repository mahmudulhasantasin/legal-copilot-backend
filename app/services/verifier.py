import re
from typing import Dict, Any, List


class CitationVerifier:
    def __init__(self, store=None):
        self.store = store

    def verify_citations(self, text: str, context_docs: List[str]) -> Dict[str, Any]:
        """
        Extracts statutory citations and cross-checks against retrieved context documents.
        """
        # Match patterns like: Section 73 of the Contract Act, Sec. 23 Penal Code
        pattern = r"(?:Section|Sec\.)\s+(\d+[A-Za-z]?)\s+(?:of\s+the\s+)?([A-Za-z\s]+Act)"
        matches = re.findall(pattern, text, re.IGNORECASE)

        if not matches:
            return {
                "citation_verified": True,
                "details": "No explicit statutory citations found to verify."
            }

        verified_citations = []
        all_matched = True

        for sec, act in matches:
            # Check if section number exists in any of the retrieved docs
            matched = any(sec in doc for doc in context_docs)
            verified_citations.append({
                "act": act.strip(),
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