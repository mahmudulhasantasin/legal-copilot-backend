import asyncio
import logging
import inspect
from typing import Any, Dict, List, Optional
from qdrant_client import models
from app.config import settings
from app.services.vector_store import vector_manager
from app.services.query_analyzer import analyze_query_dynamically
# ✅ FIX: resolve_act_robust এবং get_section_variants ইমপোর্ট করা হলো
from app.services.legal_normalizer import (
    normalize_section, 
    normalize_act, 
    resolve_act_robust, 
    get_section_variants
)

logger = logging.getLogger(__name__)

MIN_DENSE_SCORE = 0.36
UNFILTERED_FALLBACK_SCORE = 0.42
PATH_A_SCORE = 2.0
INFERRED_SCORE = 1.0
MIN_FILTERED_HITS = 2


def _extract_nested_fields(payload: Dict[str, Any]) -> tuple:
    meta = payload.get("metadata") or {}
    act = meta.get("act") or payload.get("act") or "Bangladesh Statute"
    sec = meta.get("section") or payload.get("section") or ""
    title = meta.get("title") or payload.get("title") or ""
    text = payload.get("text") or payload.get("content") or meta.get("text") or ""
    return str(act).strip(), str(sec).strip(), str(title).strip(), str(text).strip()


def _doc_key(payload: Dict[str, Any], act: str, sec: str) -> str:
    meta = payload.get("metadata") or {}
    act_key = meta.get("act_norm") or normalize_act(act) or act.lower()
    sec_key = meta.get("section_norm") or normalize_section(sec) or sec.lower()
    return f"{act_key}::{sec_key}"


def _payload_of(item) -> Dict[str, Any]:
    if hasattr(item, "payload"):
        return item.payload or {}
    if hasattr(item, "model_dump"):
        return item.model_dump().get("payload") or {}
    if isinstance(item, dict):
        return item.get("payload") or item.get("metadata") or item
    return {}


class ProductionHybridRetriever:
    def __init__(self):
        self.collection_name = settings.QDRANT_COLLECTION_NAME
        self.client = vector_manager.client

    # ---------------- Path A: Exact Section Lookup ----------------
    def _targeted_lookup(self, act: Optional[str], sections: List[str], limit: int = 12,
                         score: float = PATH_A_SCORE) -> List[tuple]:
        # ✅ FIX: resolve_act_robust ব্যবহার করা হলো যাতে ৩৫২৫টি অ্যালিয়াস কাজে লাগে
        act_norm = resolve_act_robust(act)
        
        secs = []
        for s in sections[:6]:
            # ✅ FIX: get_section_variants ব্যবহার করে বাংলা ও ইংরেজি ভ্যারিয়েন্ট বের করা হলো
            variants = get_section_variants(s)
            if variants:
                for v in variants:
                    if v and v not in secs:
                        secs.append(v)
            else:
                # Fallback যদি ভ্যারিয়েন্ট না পাওয়া যায়
                n = normalize_section(s)
                if n and n not in secs:
                    secs.append(n)

        if not secs:
            return []

        must_conditions = [
            models.FieldCondition(key="metadata.section_norm", match=models.MatchAny(any=secs))
        ]
        
        if act_norm:
            must_conditions.insert(
                0, models.FieldCondition(key="metadata.act_norm", match=models.MatchValue(value=act_norm))
            )
        else:
            logger.info(f"[targeted_lookup] Act unknown ('{act}'), searching sections={secs} across all acts")

        try:
            points, _ = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(must=must_conditions),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            logger.info(f"[targeted_lookup] act={act_norm} sections={secs} -> {len(points)} hits")
            
            results = []
            for p in points:
                payload = p.payload or {}
                text_content = payload.get("text") or payload.get("content") or ""
                metadata = payload.get("metadata", {})
                
                hit_dict = {
                    "id": p.id,
                    "score": score,
                    "text": text_content,
                    "content": text_content,
                    "act": metadata.get("act", ""),
                    "section": metadata.get("section", ""),
                    "title": metadata.get("title", ""),
                    "metadata": metadata,
                    "payload": payload
                }
                results.append((hit_dict, score))
            return results
            
        except Exception as e:
            logger.error(f"[targeted_lookup] failed: {e}")
            return []

    # ---------------- Path B: Dense Semantic Search ----------------
    def _run_dense(self, search_text: str, limit: int, q_filter, act_norm: Optional[str]):
        sig = inspect.signature(vector_manager.similarity_search)
        if q_filter is not None and "query_filter" in sig.parameters:
            return vector_manager.similarity_search(search_text, limit=limit, query_filter=q_filter)
        if q_filter is not None and "filter" in sig.parameters:
            return vector_manager.similarity_search(search_text, limit=limit, filter=q_filter)
        hits = vector_manager.similarity_search(search_text, limit=limit)
        if act_norm:
            hits = [
                h for h in hits
                if (_payload_of(h).get("metadata") or {}).get("act_norm") == act_norm
            ]
        return hits

    def _dense_semantic_search(self, search_text: str, act: Optional[str], limit: int = 8) -> List[tuple]:
        # ✅ FIX: resolve_act_robust ব্যবহার করা হলো
        act_norm = resolve_act_robust(act)
        q_filter = None
        if act_norm:
            q_filter = models.Filter(must=[
                models.FieldCondition(key="metadata.act_norm", match=models.MatchValue(value=act_norm))
            ])

        results = []
        try:
            hits = self._run_dense(search_text, limit, q_filter, act_norm)
            for h in hits:
                score = h.get("score") if isinstance(h, dict) else getattr(h, "score", None)
                score = 0.8 if score is None else float(score)
                if score >= MIN_DENSE_SCORE:
                    results.append((h, score))

            # High-precision fallback if filtered search yields fewer than MIN_FILTERED_HITS
            if act_norm and len(results) < MIN_FILTERED_HITS:
                logger.info(
                    f"[dense] Only {len(results)} hits under act={act_norm}; running high-precision unfiltered fallback."
                )
                unfiltered_hits = self._run_dense(search_text, limit, None, None)
                for uh in unfiltered_hits:
                    u_score = uh.get("score") if isinstance(uh, dict) else getattr(uh, "score", None)
                    u_score = 0.8 if u_score is None else float(u_score)
                    if u_score >= UNFILTERED_FALLBACK_SCORE:
                        # Slightly penalize unfiltered fallback so exact act matches stay on top
                        results.append((uh, u_score * 0.9))

        except Exception as e:
            logger.error(f"[dense_semantic_search] failed: {e}")
            return []

        return results

    # ---------------- Merge & Rank ----------------
    def retrieve(self, query: str, history: list = None, llm_instance=None, top_k: int = 8) -> List[Dict[str, Any]]:
        analysis = analyze_query_dynamically(query, history=history, llm_instance=llm_instance)
        explicit = analysis.explicit_sections or []
        explicit_norm = {normalize_section(s) for s in explicit}
        inferred = [s for s in (analysis.inferred_sections or []) if normalize_section(s) not in explicit_norm]

        acts_to_search = getattr(analysis, "detected_acts", [])
        if not acts_to_search:
            acts_to_search = [None]

        merged: Dict[str, Dict[str, Any]] = {}
        
        for act in acts_to_search:
            path_a = (
                self._targeted_lookup(act, explicit, score=PATH_A_SCORE)
                + self._targeted_lookup(act, inferred, score=INFERRED_SCORE)
            )
            path_b = self._dense_semantic_search(analysis.optimized_query, act, limit=top_k + 2)

            for source, results in (("A", path_a), ("B", path_b)):
                for item, score in results:
                    payload = _payload_of(item)
                    d_act, d_sec, title, text = _extract_nested_fields(payload)
                    if not text:
                        continue
                    key = _doc_key(payload, d_act, d_sec)
                    if key in merged:
                        merged[key]["score"] = max(merged[key]["score"], score) + (0.2 if source not in merged[key]["paths"] else 0.0)
                        if source not in merged[key]["paths"]:
                            merged[key]["paths"].append(source)
                    else:
                        merged[key] = {
                            "id": key,
                            "act": d_act,
                            "section": d_sec,
                            "title": title,
                            "text": text,
                            "content": text,
                            "metadata": payload,
                            "score": score,
                            "paths": [source],
                        }

        return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    async def aretrieve(self, query: str, history: list = None, llm_instance=None, top_k: int = 8):
        return await asyncio.to_thread(self.retrieve, query, history, llm_instance, top_k)


hybrid_retriever = ProductionHybridRetriever()