import os
import requests
from typing import Any, Dict, List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from app.config import settings

class QdrantVectorManager:
    def __init__(self):
        self.qdrant_url = getattr(settings, "QDRANT_URL", None)
        self.qdrant_api_key = getattr(settings, "QDRANT_API_KEY", None)
        
        # ✅ আপনার e5-small কালেকশনের নাম
        self.collection_name = getattr(settings, "QDRANT_COLLECTION_NAME", "bd_laws_e5_small")
        
        self.hf_token = os.getenv("HF_TOKEN", "")
        # ✅ e5-small মডেলের API লিংক
        self.hf_api_url = "https://router.huggingface.co/hf-inference/models/intfloat/multilingual-e5-small/pipeline/feature-extraction"
        if not self.qdrant_url or not self.qdrant_api_key:
            raise ValueError("[CRITICAL ERROR] QDRANT_URL or QDRANT_API_KEY is missing!")

        self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60.0)
        self.dimension = 384 

    def _get_embedding_from_api(self, text: str) -> List[float]:
        headers = {"Authorization": f"Bearer {self.hf_token}"} if self.hf_token else {}
        payload = {
            "inputs": [text],
            "options": {"wait_for_model": True}
        }
        try:
            response = requests.post(self.hf_api_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()[0]
        except Exception as e:
            print(f"[ERROR] HF API embedding failed: {e}")
            return [0.0] * self.dimension

    def embed_single(self, text: str) -> List[float]:
        return self._get_embedding_from_api(f"query: {text}")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self._get_embedding_from_api(f"passage: {t}") for t in texts]

    def similarity_search(self, query: str, limit: int = 5, **kwargs) -> List[Dict[str, Any]]:
        search_limit = kwargs.get("top_k", limit)
        query_vector = self.embed_single(query)

        if hasattr(self.client, "query_points"):
            query_response = self.client.query_points(
                collection_name=self.collection_name, query=query_vector, limit=search_limit, with_payload=True,
            )
            search_result = query_response.points
        else:
            search_result = self.client.search(
                collection_name=self.collection_name, query_vector=query_vector, limit=search_limit, with_payload=True,
            )

        results = []
        for hit in search_result:
            payload = getattr(hit, "payload", {}) or {}
            metadata = payload.get("metadata", {})
            text_content = payload.get("text") or payload.get("content") or ""
            results.append({
                "id": hit.id, "score": getattr(hit, "score", 0.0),
                "text": text_content, "content": text_content,
                "act": metadata.get("act", ""), "section": metadata.get("section", ""),
                "title": metadata.get("title", ""), "metadata": metadata, "payload": payload,
            })
        return results

vector_manager = QdrantVectorManager()