from typing import Any, Dict, List
from qdrant_client import QdrantClient, models
from app.config import settings

class QdrantVectorManager:
    def __init__(self):
        self.qdrant_url = getattr(settings, "QDRANT_URL", None)
        self.qdrant_api_key = getattr(settings, "QDRANT_API_KEY", None)
        
        self.collection_name = getattr(settings, "QDRANT_COLLECTION_NAME", "bd_laws_e5_small")
        
        if not self.qdrant_url or not self.qdrant_api_key:
            raise ValueError("[CRITICAL ERROR] QDRANT_URL or QDRANT_API_KEY is missing!")

        # ✅ cloud_inference=True যুক্ত করা হলো
        self.client = QdrantClient(
            url=self.qdrant_url, 
            api_key=self.qdrant_api_key, 
            cloud_inference=True
        )

    def similarity_search(self, query: str, limit: int = 5, **kwargs) -> List[Dict[str, Any]]:
        search_limit = kwargs.get("top_k", limit)

        # Qdrant-এর সার্ভারেই এমবেডিং তৈরি হবে
        query_response = self.client.query_points(
            collection_name=self.collection_name,
            query=models.Document(
                text=query,
                model="intfloat/multilingual-e5-small"
            ),
            limit=search_limit,
            with_payload=True,
        )
        
        search_result = query_response.points

        results = []
        for hit in search_result:
            payload = getattr(hit, "payload", {}) or {}
            metadata = payload.get("metadata", {})
            text_content = payload.get("text") or payload.get("content") or ""
            
            results.append({
                "id": hit.id, 
                "score": getattr(hit, "score", 0.0),
                "text": text_content, 
                "content": text_content,
                "act": metadata.get("act", ""), 
                "section": metadata.get("section", ""),
                "title": metadata.get("title", ""), 
                "metadata": metadata, 
                "payload": payload,
            })
        return results

    def embed_single(self, text: str) -> List[float]:
        # Qdrant নিজেই এমবেডিং করছে, তাই এই মেথডটি ফাঁকা
        return []

vector_manager = QdrantVectorManager()