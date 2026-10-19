import os
import requests
from typing import Any, Dict, List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from app.config import settings

class QdrantVectorManager:
    """Manages Qdrant vector operations using Jina AI API for embeddings."""

    def __init__(self):
        self.qdrant_url = getattr(settings, "QDRANT_URL", None)
        self.qdrant_api_key = getattr(settings, "QDRANT_API_KEY", None)
        
        # ✅ ক্লাউড কানেকশন চেক (লোকাল ফলব্যাক বাদ দেওয়া হলো)
        if not self.qdrant_url or not self.qdrant_api_key:
            raise ValueError(
                "[CRITICAL ERROR] QDRANT_URL or QDRANT_API_KEY is missing! "
                "Please check your Render Environment Variables."
            )
            
        print("Connecting to Qdrant Cloud...")
        self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60.0)

        # ✅ আপনার পুরনো ১০২৪ ডাইমেনশনের কালেকশনের নাম (bd_laws_v2) এখানে দিন
        self.collection_name = getattr(settings, "QDRANT_COLLECTION_NAME", "bd_laws_v2")
        
        # ✅ Jina AI API Config
        self.jina_api_key = os.getenv("JINA_API_KEY", "")
        if not self.jina_api_key:
            print("[WARNING] JINA_API_KEY is missing! Chat queries will fail.")
            
        self.jina_api_url = "https://api.jina.ai/v1/embeddings"
        
        # ✅ Jina v3 মডেল ১০২৪ ডাইমেনশন দেয়
        self.dimension = 1024 

        # Verify collection exists
        try:
            collections = [c.name for c in self.client.get_collections().collections]
            if self.collection_name not in collections:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.dimension, distance=Distance.COSINE
                    ),
                )
                print(f"Created new collection '{self.collection_name}' with dimension {self.dimension}")
        except Exception as err:
            print(f"[WARN] Failed to verify/create Qdrant collection: {err}")

    def _get_embedding_from_api(self, text: str, task: str = "retrieval.query") -> List[float]:
        """Jina AI API থেকে ভেক্টর নিয়ে আসে।"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.jina_api_key}"
        }
        
        payload = {
            "model": "jina-embeddings-v3",
            "input": [text],
            "task": task,
            "dimensions": self.dimension
        }
        
        try:
            response = requests.post(self.jina_api_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]
        except Exception as e:
            print(f"[ERROR] Jina API embedding failed: {e}")
            return [0.0] * self.dimension 

    def embed_single(self, text: str) -> List[float]:
        return self._get_embedding_from_api(text, task="retrieval.query")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self._get_embedding_from_api(t, task="retrieval.passage") for t in texts]

    def similarity_search(self, query: str, limit: int = 5, **kwargs) -> List[Dict[str, Any]]:
        search_limit = kwargs.get("top_k", limit)
        query_vector = self.embed_single(query)

        if hasattr(self.client, "query_points"):
            query_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=search_limit,
                with_payload=True,
            )
            search_result = query_response.points
        else:
            search_result = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=search_limit,
                with_payload=True,
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

# Global singleton instance
vector_manager = QdrantVectorManager()