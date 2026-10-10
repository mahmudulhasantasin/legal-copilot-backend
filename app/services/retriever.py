import re
from typing import List, Dict, Any
from rank_bm25 import BM25Okapi
from app.services.vector_store import vector_manager

# বাংলাদেশের মৌলিক সংবিধিবদ্ধ আইনের প্রাথমিক কর্পাস
DEFAULT_BANGLADESH_LAWS = [
    {
        "id": "contract_act_sec_27",
        "act": "The Contract Act, 1872",
        "section": "Section 27",
        "title": "Agreement in restraint of trade, void",
        "text": "Every agreement by which any one is restrained from exercising a lawful profession, trade or business of any kind, is to that extent void. Exception allows restriction only on sale of goodwill."
    },
    {
        "id": "contract_act_sec_28",
        "act": "The Contract Act, 1872",
        "section": "Section 28",
        "title": "Agreements in restraint of legal proceedings, void",
        "text": "Every agreement, by which any party thereto is restricted absolutely from enforcing his rights under or in respect of any contract, by the usual legal proceedings in the ordinary tribunals, is void."
    },
    {
        "id": "contract_act_sec_74",
        "act": "The Contract Act, 1872",
        "section": "Section 74",
        "title": "Compensation for breach of contract where penalty stipulated for",
        "text": "When a contract has been broken, if a sum is named in the contract as penalty, the party complaining is entitled to receive reasonable compensation not exceeding the penalty amount."
    },
    {
        "id": "labour_act_sec_26",
        "act": "The Bangladesh Labour Act, 2006",
        "section": "Section 26",
        "title": "Termination of employment of permanent workers by notice",
        "text": "Employment of a permanent worker may be terminated by 120 days notice for monthly rated workers or wages in lieu thereof, plus statutory compensation equal to 30 days wages per completed year of service."
    },
    {
        "id": "labour_act_sec_100",
        "act": "The Bangladesh Labour Act, 2006",
        "section": "Section 100",
        "title": "Daily working hours and overtime limit",
        "text": "No adult worker shall ordinarily be required or allowed to work in an establishment for more than eight hours in any day, subject to a maximum of ten hours including overtime."
    }
]

class HybridRetriever:
    def __init__(self, corpus: List[Dict[str, Any]] = None):
        self.corpus = corpus or DEFAULT_BANGLADESH_LAWS
        
        # BM25 ইনডেক্সিং
        self.tokenized_corpus = [
            f"{doc['act']} {doc['section']} {doc['title']} {doc['text']}".lower().split()
            for doc in self.corpus
        ]
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        
        # ChromaDB ইনিশিয়ালাইজেশন
        try:
            if vector_manager.collection.count() == 0:
                vector_manager.add_statutes(self.corpus)
        except Exception:
            pass

    def exact_section_lookup(self, query: str) -> List[Dict[str, Any]]:
        """যদি কুয়েরিতে সরাসরি 'Section 27' বা 'Labour Act' থাকে তবে সরাসরি এক্সাক্ট ম্যাচ রিট্রিভ করে।"""
        matches = []
        for doc in self.corpus:
            sec_num = re.search(r"section\s*(\d+)", doc["section"], re.IGNORECASE)
            if sec_num and re.search(rf"\bsection\s*{sec_num.group(1)}\b", query, re.IGNORECASE):
                matches.append(doc)
        return matches

    def retrieve(self, query: str, top_k: int = 3, rrf_k: int = 60) -> List[Dict[str, Any]]:
        """RRF (Reciprocal Rank Fusion) অ্যালগরিদম ব্যবহার করে BM25 + Vector সার্চ একত্রিত করে।"""
        # ১. Exact Lookup
        exact_matches = self.exact_section_lookup(query)
        if exact_matches:
            return exact_matches[:top_k]

        # ২. BM25 Search
        query_tokens = query.lower().split()
        bm25_scores = self.bm25.get_scores(query_tokens)
        bm25_ranked_indices = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)

        # ৩. Vector Search
        vector_results = vector_manager.similarity_search(query, top_k=top_k)

        # ৪. RRF স্কোরিং
        doc_scores = {}
        for rank, idx in enumerate(bm25_ranked_indices[:5]):
            doc_id = self.corpus[idx]["id"]
            doc_scores[doc_id] = doc_scores.get(doc_id, 0) + (1.0 / (rrf_k + rank + 1))

        for rank, v_doc in enumerate(vector_results):
            doc_id = v_doc["id"]
            doc_scores[doc_id] = doc_scores.get(doc_id, 0) + (1.0 / (rrf_k + rank + 1))

        # সর্বোচ্চ স্কোরের ভিত্তিতে সাজানো
        sorted_doc_ids = sorted(doc_scores.keys(), key=lambda d_id: doc_scores[d_id], reverse=True)

        final_docs = []
        for d_id in sorted_doc_ids[:top_k]:
            match = next((d for d in self.corpus if d["id"] == d_id), None)
            if match:
                final_docs.append(match)

        return final_docs or self.corpus[:top_k]

hybrid_retriever = HybridRetriever()