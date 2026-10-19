import asyncio
import logging
import inspect
from typing import Any, Dict, List, Optional
from qdrant_client import models
from app.config import settings
from app.services.vector_store import vector_manager
from app.services.query_analyzer import analyze_query_dynamically
from app.services.legal_normalizer import normalize_section, normalize_act, resolve_act

logger = logging.getLogger(__name__)

MIN_DENSE_SCORE = 0.38
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

    # ---------------- Path A ----------------
    def _targeted_lookup(self, act: Optional[str], sections: List[str], limit: int = 12,
                         score: float = PATH_A_SCORE) -> List[tuple]:
        act_norm = resolve_act(act)
        secs = []
        for s in sections[:6]:
            n = normalize_section(s)
            if n and n not in secs:
                secs.append(n)

        if not secs:
            return []
        if not act_norm:
            logger.warning(f"[targeted_lookup] Unknown act '{act}', skipping Path A")
            return []

        try:
            points, _ = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(must=[
                    models.FieldCondition(key="metadata.act_norm", match=models.MatchValue(value=act_norm)),
                    models.FieldCondition(key="metadata.section_norm", match=models.MatchAny(any=secs)),
                ]),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            logger.info(f"[targeted_lookup] act={act_norm} sections={secs} -> {len(points)} hits")
            
            # ✅ FIX: Path A থেকেও Path B-এর মতো স্ট্যান্ডার্ড ডিকশনারি রিটার্ন করা হচ্ছে
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

    # ---------------- Path B ----------------
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

    def _dense_semantic_search(self, search_text: str, act: Optional[str], limit: int = 6) -> List[tuple]:
        act_norm = resolve_act(act)
        q_filter = None
        if act_norm:
            q_filter = models.Filter(must=[
                models.FieldCondition(key="metadata.act_norm", match=models.MatchValue(value=act_norm))
            ])

        try:
            hits = self._run_dense(search_text, limit, q_filter, act_norm)
            # Safety: if an Act is identified, do not mix in results from other Acts
            # merely because the Act-filtered search returns fewer hits.
            if act_norm and len(hits) < MIN_FILTERED_HITS:
                logger.info("[dense] only %s hits under act=%s; keeping filtered results", len(hits), act_norm)
        except Exception as e:
            logger.error(f"[dense_semantic_search] failed: {e}")
            return []

        results = []
        for h in hits:
            score = h.get("score") if isinstance(h, dict) else getattr(h, "score", None)
            score = 0.8 if score is None else float(score)
            if score >= MIN_DENSE_SCORE:
                results.append((h, score))
        return results

    # ---------------- Merge ----------------
    def retrieve(self, query: str, history: list = None, llm_instance=None, top_k: int = 5) -> List[Dict[str, Any]]:
        analysis = analyze_query_dynamically(query, history=history, llm_instance=llm_instance)
        explicit = analysis.explicit_sections or []
        explicit_norm = {normalize_section(s) for s in explicit}
        inferred = [s for s in (analysis.inferred_sections or []) if normalize_section(s) not in explicit_norm]

        acts_to_search = getattr(analysis, "detected_acts", [])
        if not acts_to_search:
            acts_to_search = [None]

        merged: Dict[str, Dict[str, Any]] = {}
        
        for act in acts_to_search:
            path_a = (self._targeted_lookup(act, explicit, score=PATH_A_SCORE)
                      + self._targeted_lookup(act, inferred, score=INFERRED_SCORE))
            path_b = self._dense_semantic_search(analysis.optimized_query, act, limit=top_k + 2)

            for source, results in (("A", path_a), ("B", path_b)):
                for item, score in results:
                    payload = _payload_of(item)
                    d_act, d_sec, title, text = _extract_nested_fields(payload)
                    if not text:
                        continue
                    key = _doc_key(payload, d_act, d_sec)
                    if key in merged:
                        merged[key]["score"] += score
                        if source not in merged[key]["paths"]:
                            merged[key]["paths"].append(source)
                    else:
                        merged[key] = {
                            "id": key, "act": d_act, "section": d_sec, "title": title,
                            "text": text, "content": text, "metadata": payload,
                            "score": score, "paths": [source],
                        }

        return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    async def aretrieve(self, query: str, history: list = None, llm_instance=None, top_k: int = 5):
        return await asyncio.to_thread(self.retrieve, query, history, llm_instance, top_k)

hybrid_retriever = ProductionHybridRetriever()