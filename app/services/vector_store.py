import os
import requests
from typing import Any, Dict, List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from app.config import settings

class QdrantVectorManager:
    """Manages Qdrant vector operations using Hugging Face API for embeddings."""

    def __init__(self):
        self.qdrant_url = getattr(settings, "QDRANT_URL", None)
        self.qdrant_api_key = getattr(settings, "QDRANT_API_KEY", None)
        self.collection_name = getattr(settings, "QDRANT_COLLECTION_NAME", "bd_laws_e5_small")
        
        # ✅ Hugging Face API টোকেন (Render-এর Environment-এ HF_TOKEN হিসেবে সেট করতে হবে)
        self.hf_token = os.getenv("HF_TOKEN", "")
        # ✅ আপনার লোকাল স্ক্রিপ্ট যে মডেল ব্যবহার করছে, ঠিক সেই মডেলটিই এখানে ব্যবহার হচ্ছে
        self.hf_api_url = "https://router.huggingface.co/hf-inference/models/intfloat/multilingual-e5-small/pipeline/feature-extraction"
        if self.qdrant_url and self.qdrant_api_key:
            print("Connecting to Qdrant Cloud via API...")
            self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60.0)
        else:
            print("Using Local Qdrant Database (./local_qdrant_data)...")
            self.client = QdrantClient(path="./local_qdrant_data")

        self.dimension = 384 

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

    def _get_embedding_from_api(self, text: str) -> List[float]:
        """HF API থেকে ভেক্টর নিয়ে আসে (রেন্ডারে কোনো মেমরি খরচ করে না)।"""
        headers = {}
        if self.hf_token:
            headers["Authorization"] = f"Bearer {self.hf_token}"
            
        payload = {
            "inputs": [text],
            "options": {"wait_for_model": True}
        }
        
        try:
            response = requests.post(self.hf_api_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            # HF API সাধারণত [[0.1, 0.2, ...]] এই ফরম্যাটে রেসপন্স দেয়
            return response.json()[0]
        except Exception as e:
            print(f"[ERROR] HF API embedding failed: {e}")
            # ফলব্যাক: যাতে অ্যাপ ক্র্যাশ না করে
            return [0.0] * self.dimension 

    def embed_single(self, text: str) -> List[float]:
        # E5 মডেলের জন্য 'query: ' প্রিফিক্স
        prefixed_text = f"query: {text}"
        return self._get_embedding_from_api(prefixed_text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        prefixed_texts = [f"passage: {t}" for t in texts]
        return [self._get_embedding_from_api(t) for t in prefixed_texts]

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
            score = getattr(hit, "score", 0.0)
            text_content = payload.get("text") or payload.get("content") or ""
            metadata = payload.get("metadata", {})
            
            results.append({
                "id": hit.id,
                "score": score,
                "text": text_content,
                "content": text_content,
                "act": metadata.get("act", ""),
                "section": metadata.get("section", ""),
                "title": metadata.get("title", ""),
                "metadata": metadata,
                "payload": payload,
            })
        return results

# Global singleton instance
vector_manager = QdrantVectorManager()